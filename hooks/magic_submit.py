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
from magic_router import set_claude_provider, current_claude_provider_name, is_local_provider

CONFIG_PATH = os.path.join(HOOK_DIR, "magic_router_config.json")
STATE_DIR = os.path.join(HOOK_DIR, "..", "state")
AUDIT_PATH = os.path.join(STATE_DIR, "magic_router_audit.jsonl")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def parse_hook_input(raw):
    try:
        d = json.loads(raw) if raw else {}
    except Exception:
        return {}
    # Extract user prompt
    text = ""
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
    turn_id = d.get("turn_id") or d.get("hookEvent", {}).get("turnId", "")
    return {"user_prompt": text.strip(), "turn_id": turn_id}


def is_chitchat(cfg, prompt):
    for pat in cfg.get("chitchat_patterns", []):
        if re.search(pat, prompt, re.IGNORECASE):
            return True
    return False


def main():
    cfg = load_config()
    if not cfg.get("_meta", {}).get("enabled", True):
        return

    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
    except Exception:
        raw = sys.stdin.read()
    turn = parse_hook_input(raw)

    # 读取状态
    state = magic_state.read_state()
    upgrading = state.get("upgrade_next_turn", False)

    # 云端冷却轮（保留在云端）
    cooldown = state.get("cloud_cooldown_turns", 0)

    current_name = current_claude_provider_name()
    current_is_local = is_local_provider(current_name)

    if upgrading:
        # 升级 → 切到云端
        target_name = state.get("target_provider", "")

        # 如果 target_provider 为空，从配置取云端 primary
        if not target_name:
            target_name = cfg.get("cloud", {}).get("primary", "")

        if target_name:
            success = set_claude_provider(target_name)

            if success:
                # 下轮不再自动升级
                magic_state.write_state({
                    **state,
                    "upgrade_next_turn": False,
                    "target_provider": target_name,
                    "cloud_cooldown_turns": cfg.get("scoring", {}).get("cooldown_turns_after_cloud", 0),

                    "last_route": "cloud",
                })

                # 更新审计
                try:
                    os.makedirs(STATE_DIR, exist_ok=True)
                    with open(AUDIT_PATH, "a") as f:
                        f.write(json.dumps({
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "turn_id": turn.get("turn_id", ""),
                            "action": "escalate_to_cloud",
                            "target": target_name,
                        }, ensure_ascii=False) + "\n")
                except Exception:
                    pass
            else:
                # 升级失败，退回到本地
                print(f"[MagicHook] 云端切换失败，保留本地", flush=True)
    elif cooldown > 0:
        # 冷却轮：继续云端
        magic_state.write_state({
            **state,
            "cloud_cooldown_turns": cooldown - 1,
        })
    elif not current_is_local:
        # 应该本地，但当前是云端 → 切回本地
        target_name = cfg.get("local", {}).get("primary", "")
        if target_name:
            success = set_claude_provider(target_name)
            if not success:
                backup = cfg.get("local", {}).get("backup", "")
                if backup:
                    set_claude_provider(backup)

            magic_state.write_state({
                **state,
                "last_route": "local",
                "target_provider": "",
            })
    # 已经是本地 → 无事可做


if __name__ == "__main__":
    main()