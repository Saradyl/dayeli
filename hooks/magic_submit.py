#!/usr/bin/env python3
"""
Magic_Hook UserPromptSubmit — 路由执行器。

职责：用户提交 prompt 后、模型执行前，读取状态文件，
如果需要升级则切换到云端 provider，否则确保为本地。
"""

import json
import os
import re
import sys
import time

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
if HOOK_DIR not in sys.path:
    sys.path.insert(0, HOOK_DIR)

import magic_state
import magic_session
from magic_router import (
    current_claude_provider_name,
    is_local_provider,
    magic_active,
    write_magic_target,
    read_magic_target,
    current_magic_route,
)

CONFIG_PATH = os.path.join(HOOK_DIR, "magic_router_config.json")
STATE_DIR = os.path.join(HOOK_DIR, "..", "state")
AUDIT_PATH = os.path.join(STATE_DIR, "magic_router_audit.jsonl")

# 用户暗号（看 prompt 最后 30 字符，不区分大小写）
PASSPHRASE_WINDOW = 30
PASSPHRASE_UP = "dylup"
PASSPHRASE_DOWN = "dylgo"
UPGRADE_ON_USER_TRIGGER = 7


def detect_passphrase(prompt):
    """只检测 prompt 最后 30 个字符里的暗号，返回 "up"/"go"/None。

    只看尾部是为了避免 prompt 正文里出现的普通词被误中；
    不区分大小写。
    """
    tail = (prompt or "")[-PASSPHRASE_WINDOW:].lower()
    if PASSPHRASE_UP in tail:
        return "up"
    if PASSPHRASE_DOWN in tail:
        return "go"
    return None


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_audit(turn, action, trigger="", target="", score_after=None):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "turn_id": turn.get("turn_id", ""),
            "action": action,
            "trigger": trigger,
            "target": target,
        }
        if score_after is not None:
            entry["score_after"] = score_after
        with open(AUDIT_PATH, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def parse_hook_input(raw):
    try:
        d = json.loads(raw) if raw else {}
    except Exception:
        return {}
    # Extract user prompt
    text = ""
    # Claude Code UserPromptSubmit 的 payload 结构：prompt 文本可能在顶层
    # "prompt" 字段，也可能在 "messages" 数组里（旧结构）。两种都兼容。
    if isinstance(d.get("prompt"), str) and d.get("prompt"):
        text = d.get("prompt")
    messages = d.get("messages") or []
    if isinstance(messages, list):
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                if isinstance(c, list):
                    for seg in c:
                        if isinstance(seg, dict) and seg.get("type") == "text":
                            text += seg.get("text", "")
                elif isinstance(c, str):
                    text = c
                if text:
                    break
    turn_id = (
        d.get("prompt_id")
        or d.get("turn_id")
        or d.get("hookEvent", {}).get("turnId", "")
        or ""
    )
    # 会话标识（用于多会话隔离），若没有则退回空串
    session_id = d.get("session_id", "") or d.get("conversation_id", "")
    return {"user_prompt": text.strip(), "turn_id": turn_id, "session_id": session_id}


def is_chitchat(cfg, prompt):
    for pat in cfg.get("chitchat_patterns", []):
        if re.search(pat, prompt, re.IGNORECASE):
            return True
    return False


def read_router_state(turn):
    """读路由器 state：有 session_id 走 per-session，否则退化全局。

    返回 (state, session_id)。
    """
    sid = magic_session.resolve_session(turn)
    if sid:
        return magic_state.read_session_state(sid), sid
    return magic_state.read_state(), None


def write_router_state(turn, state, action=None, extra=None):
    """写路由器 state：有 session_id 走 per-session，否则退化全局。
    同时写一条审计日志（全局统一审计，方便排查多会话）。
    """
    sid = magic_session.resolve_session(turn)
    state["last_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if sid:
        magic_state.write_session_state(sid, state)
    else:
        magic_state.write_state(state)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        entry = {"ts": state.get("last_updated_at"), "session_id": sid or "", "action": action}
        if extra:
            entry.update(extra)
        with open(AUDIT_PATH, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    cfg = load_config()
    if not cfg.get("_meta", {}).get("enabled", True):
        return

    # ── 激活门（需求#1）：仅当用户在 cc-switch 选了 magic_hook 专用双copy
    #    provider（指向 15666 代理）才激活。单copy（常驻本地）或其他云模型
    #    一律不干预。 ──
    if not magic_active():
        return

    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
    except Exception:
        raw = sys.stdin.read()

    turn = parse_hook_input(raw)

    # 会话标识（用于多会话隔离）
    session_id = magic_session.resolve_session(turn)
    state, _sid = read_router_state(turn)
    upgrading = state.get("upgrade_next_turn", False)

    # 用户暗号：只看 prompt 最后 30 字符
    phrase = detect_passphrase(turn.get("user_prompt", ""))

    # 云端冷却轮（保留在云端）
    cooldown = state.get("cloud_cooldown_turns", 0)

    # 当前路由看 magic_target（provider 全程是专用双copy，不变）
    current_is_local = (read_magic_target() == "base")

    # ── 用户暗号（prompt 最后 30 字符）：优先级最高，先处理 ──
    if phrase == "go":
        # 用户主动降级：积分清零、关升级标志、清冷却，立即路由回本地。
        # 用户降级意图优先于自动冷却，且不受多会话暂停影响。
        success = write_magic_target("base", reason="user_passphrase_dylgo")
        if success:
            print("[MagicHook] 用户降级暗号命中，已路由回本地模型", flush=True)
        else:
            print("[MagicHook] 用户降级：写路由决策失败", flush=True)

        write_router_state(turn, {
            **state,
            "score": 0,
            "upgrade_next_turn": False,
            "target_provider": "",
            "cloud_cooldown_turns": 0,
            "last_route": "local",
        }, action="user_downgrade", extra={"trigger": "dylgo", "target": "base"})
        return

    if phrase == "up":
        # 用户明确指示升级：强制走云端，与打分阈值无关。
        # 积分 +UPGRADE_ON_USER_TRIGGER，日志标记为 user-triggered。
        # 不受多会话暂停影响。
        new_score = state.get("score", 0) + UPGRADE_ON_USER_TRIGGER
        success = write_magic_target("upgrade", reason="user_passphrase_dylup")

        write_router_state(turn, {
            **state,
            "score": new_score,
            "upgrade_next_turn": True,       # 强制下轮升级
            "target_provider": cfg.get("cloud", {}).get("primary", ""),
            # 与自动升级路径一致：进入冷却
            "cloud_cooldown_turns": cfg.get("scoring", {}).get(
                "cooldown_turns_after_cloud", 0),
            "last_route": "cloud" if success else state.get("last_route", "local"),
        }, action="user_upscale", extra={"trigger": "dylup",
                     "target": "upgrade", "score_after": new_score})
        if not success:
            print("[MagicHook] 用户升级暗号命中，但写路由决策失败", flush=True)
        return

    # ── 自动路由（打分驱动，无暗号）：多会话自动暂停 ──
    active_count = magic_state.count_active_sessions()
    if not magic_session.should_act(session_id, active_count, user_trigger=False):
        # 多会话：暂停自动升降级（不写路由决策），但照常刷新本地状态。
        print(f"[MagicHook] 检测到 {active_count} 个活跃会话，自动路由暂停（仅单会话时恢复）", flush=True)
        new_state = dict(state)
        new_state["last_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        new_state["pause_reason"] = "multi_session"
        if cooldown > 0:
            new_state["cloud_cooldown_turns"] = cooldown - 1
        write_router_state(turn, new_state, action="route_paused",
                           extra={"active_sessions": active_count,
                                  "previous_cooldown": cooldown})
        return

    run_auto_routing(turn, state, cfg, current_is_local)


def run_auto_routing(turn, state, cfg, current_is_local):
    """单会话（或无 session_id）时执行自动路由：写 magic_target.json。"""
    upgrading = state.get("upgrade_next_turn", False)
    cooldown = state.get("cloud_cooldown_turns", 0)

    if upgrading:
        # 升级 → 路由到云端（自动打分驱动）
        success = write_magic_target("upgrade", reason="auto_score_upgrade")
        if success:
            write_router_state(turn, {
                **state,
                "upgrade_next_turn": False,
                "target_provider": cfg.get("cloud", {}).get("primary", ""),
                "cloud_cooldown_turns": cfg.get("scoring", {}).get(
                    "cooldown_turns_after_cloud", 0),
                "last_route": "cloud",
            }, action="escalate_to_cloud", extra={"target": "upgrade"})
        else:
            print("[MagicHook] 自动升级：写路由决策失败，保留本地", flush=True)
    elif cooldown > 0:
        # 冷却轮：继续云端（不改路由，只递减冷却）
        write_router_state(turn, {
            **state,
            "cloud_cooldown_turns": cooldown - 1,
        }, action="cooldown_tick")
    elif not current_is_local:
        # 应该本地，但当前路由是云端 → 路由回本地
        success = write_magic_target("base", reason="auto_drop_back")
        write_router_state(turn, {
            **state,
            "last_route": "local",
            "target_provider": "",
        }, action="drop_back_to_local", extra={"target": "base"})
    # 已经是本地 → 无事可做


if __name__ == "__main__":
    main()