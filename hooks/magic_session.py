#!/usr/bin/env python3
"""
Magic_Hook 会话管理 — 多会话隔离辅助。

职责：
  - 从 hook payload 解析 session_id
  - 活跃会话计数（多会话自动暂停）
  - 决定本次是否应继续自动升降级

原则：
  - 有 session_id 时用 per-session 隔离
  - 无 session_id 时退化为旧的全局行为，不破坏现有功能
"""

import time


def resolve_session(turn):
    """从 turn dict 解析会话标识。

    优先级：session_id > conversation_id > ""（无标识=退化全局）。
    有则去掉非法字符保证是合法文件名。
    """
    sid = turn.get("session_id", "") or turn.get("conversation_id", "")
    if isinstance(sid, str):
        sid = sid.strip()
    if not sid:
        return None
    return sid


def should_act(session_id, active_count, user_trigger):
    """
    是否执行自动升降级（set_claude_provider）。

    规则：
      - 用户暗号永远生效（user_trigger=True）
      - 否则，仅当活跃会话 <= 1 时执行自动路由
      - 活跃会话 > 1 时：自动暂停，原地不动

    无 session_id（旧 payload）时直接放行，行为与无隔离时一致。
    """
    if not session_id:
        # 退化到全局：按老逻辑执行（无隔离概念）
        return True
    if user_trigger:
        return True
    return active_count <= 1
