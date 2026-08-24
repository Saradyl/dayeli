#!/usr/bin/env python3
"""
Magic_Hook Stop Hook — 异步评分器。

职责：本轮模型回答完毕后，异步读取本轮上下文与执行信号，
计算本轮得分，更新升级积分；达到阈值则写入"下轮升级"标志。

设计原则（零打扰）：
  - 回答已吐给用户后才异步评分，用户零等待
  - 不打印任何用户可见信息到界面
  - 出错默认回退，不阻塞主流程
  - turn_id 去重 + 文件锁防止并发
"""

import json
import os
import re
import sys
import time

# 解析脚本所在目录（hook 调用 cwd 不稳定）
HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
if HOOK_DIR not in sys.path:
    sys.path.insert(0, HOOK_DIR)

import magic_state
import magic_router


CONFIG_PATH = os.path.join(HOOK_DIR, "magic_router_config.json")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def parse_hook_input(raw):
    """从 Claude Code Stop hook 的 stdin JSON 提取字段。"""
    try:
        d = json.loads(raw) if raw else {}
    except Exception:
        return {}

    user_prompt = ""
    assistant = ""
    turn_id = ""

    # Claude Code Stop hook payload 结构
    if isinstance(d, dict):
        messages = d.get("messages") or d.get("last_messages") or []
        if isinstance(messages, list):
            for m in messages:
                if isinstance(m, dict):
                    role = m.get("role", "")
                    content = m.get("content")
                    if isinstance(content, str):
                        if role == "user" and not user_prompt:
                            user_prompt = content
                    elif isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text" and role == "user":
                                user_prompt += c.get("text", "")
                    if role == "assistant":
                        if isinstance(content, str):
                            assistant += content + "\n"
                        elif isinstance(content, list):
                            for c in content:
                                if isinstance(c, dict) and c.get("type") == "text":
                                    assistant += c.get("text", "") + "\n"

        # 兜底判定器回底 payload：没有 messages 列表，只有 last_assistant_message 字符串
        if not assistant and isinstance(d.get("last_assistant_message"), str):
            assistant += d.get("last_assistant_message", "")

        turn_id = d.get("turn_id") or d.get("hookEvent", {}).get("turnId", "") or d.get("prompt_id", "")
    return {"user_prompt": user_prompt.strip(), "assistant": assistant.strip(), "turn_id": turn_id}


def score_turn(cfg, turn, user_prompt, assistant):
    """规则引擎评分，返回 (score, reasons)。"""
    score = 0
    reasons = []

    signals = cfg.get("signals", {})

    # 零打扰：闲聊直接衰减，不升级
    chitchat = cfg.get("chitchat_patterns", [])
    for pat in chitchat:
        if re.search(pat, user_prompt, re.IGNORECASE):
            score -= 1
            reasons.append("chitchat")
            return max(0, score), reasons

    # A. 工具/代码执行错误信号（高权重）
    error_patterns = [
        r"Traceback", r"SyntaxError", r"ImportError", r"ModuleNotFoundError",
        r"Permission denied", r"command not found", r"No such file or directory",
        r"Failed to", r"Error:", r"Exception",
    ]
    hit = [p for p in error_patterns if re.search(p, assistant, re.IGNORECASE)]
    if hit:
        score += signals.get("tool_error", 5)
        reasons.append("tool_error")

    # A. 严重错误信号
    severe = r"I cannot|I am unable|I don't know how|not enough information|cannot complete|无法|做不到"
    if re.search(severe, assistant, re.IGNORECASE):
        score += signals.get("severe_error", 5)
        reasons.append("severe_error")

    # B. 用户负反馈（下一轮用户输入）
    for pat in cfg.get("negative_feedback_patterns", []):
        if re.search(pat, user_prompt, re.IGNORECASE):
            score += signals.get("user_negative_feedback", 4)
            reasons.append("user_negative_feedback")
            break

    # C. 回答质量弱信号
    for pat in cfg.get("low_confidence_patterns", []):
        if re.search(pat, assistant, re.IGNORECASE):
            score += signals.get("low_confidence_wording", 2)
            reasons.append("low_confidence")
            break

    if not reasons:
        # 默认无信号，+0
        reasons.append("none")

    return score, reasons


def main():
    cfg = load_config()

    # 开关：关闭时立即退出，零影响
    if not cfg.get("_meta", {}).get("enabled", True):
        return

    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
    except Exception:
        raw = sys.stdin.read()

    turn = parse_hook_input(raw)

    # 无 assistant 内容（异常场景），直接退出
    if not turn.get("assistant"):
        return

    state = magic_state.read_state()

    # turn_id 去重：防止同一轮被重复计分
    if turn.get("turn_id") == state.get("last_turn_id"):
        return

    score, reasons = score_turn(cfg, turn, turn.get("user_prompt", ""), turn.get("assistant", ""))

    # 自然衰减
    score -= cfg.get("scoring", {}).get("natural_decay_per_turn", 1)
    if score < 0:
        score = 0

    # 连续成功轮衰减
    if state.get("last_route") == "local" and score <= 0:
        state["consecutive_local_failures"] = max(0, state.get("consecutive_local_failures", 0) - 1)

    # 云端：积分清零，防止重复触发升级
    if state.get("last_route") == "cloud":
        score = 0
        new_score = 0
        state["consecutive_local_failures"] = 0
    else:
        new_score = state.get("score", 0) + score

    # 连续本地失败计数
    if state.get("last_route") == "local" and score >= 1:
        state["consecutive_local_failures"] = state.get("consecutive_local_failures", 0) + 1
    else:
        state["consecutive_local_failures"] = 0

    threshold = cfg.get("scoring", {}).get("upgrade_threshold", 7)
    hard_threshold = cfg.get("scoring", {}).get("hard_upgrade_threshold", 10)

    need_upgrade = (
        new_score >= threshold or
        state["consecutive_local_failures"] >= cfg.get("scoring", {}).get("max_consecutive_local_failures", 2)
    )

    # 判断本轮走的是不是本地
    if magic_router.is_local_provider(state.get("target_provider", "")):
        last_route = "local"
    elif state.get("target_provider", "") == "":
        # 没 target 信息时，用 current 名判断
        from magic_router import current_claude_provider_name
        last_route = "local" if magic_router.is_local_provider(current_claude_provider_name()) else "cloud"
    else:
        last_route = "cloud"

    magic_state.write_state({
        **state,
        "score": new_score,
        "last_turn_id": turn.get("turn_id", ""),
        "last_route": last_route,
        "last_provider": state.get("target_provider", ""),
        "consecutive_local_failures": state["consecutive_local_failures"],
        "upgrade_next_turn": need_upgrade,
        "target_provider": state.get("target_provider", ""),
        "last_updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })

    # 审计日志（静默）
    audit_path = os.path.join(os.path.dirname(HOOK_DIR), "state", "magic_router_audit.jsonl")
    try:
        os.makedirs(os.path.dirname(audit_path), exist_ok=True)
        with open(audit_path, "a") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "turn_id": turn.get("turn_id", ""),
                "route": last_route,
                "score_before": state.get("score", 0),
                "score_delta": score,
                "score_after": new_score,
                "upgrade_next_turn": need_upgrade,
                "reasons": reasons,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()

