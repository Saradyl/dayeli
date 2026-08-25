"""
Magic_Hook 状态管理 — 线程安全的 JSON 状态读写
跨平台兼容：Windows 没有 fcntl，做降级处理。

支持 per-session 隔离：多会话各自一份 state 文件，
避免多个 Claude Code 会话互相踩踏。旧代码的全局状态文件
（magic_router_state.json）仍被 read_state/write_state 覆盖，
供无 session_id 的旧 payload 兜底使用。
"""

import json
import os
import time
import glob
import re

STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "state")
STATE_FILE = os.path.join(STATE_DIR, "magic_router_state.json")
LOCK_FILE = os.path.join(STATE_DIR, "magic_router.lock")
AUDIT_PATH = os.path.join(STATE_DIR, "magic_router_audit.jsonl")

# 确保目录存在
os.makedirs(STATE_DIR, exist_ok=True)

DEFAULT_STATE = {
    "score": 0,
    "last_route": "local",
    "upgrade_next_turn": False,
    "target_provider": "",
    "cloud_cooldown_turns": 0,
    "consecutive_local_failures": 0,
    "last_turn_id": "",
    "last_cloud_provider": "",
    "turn_counter": 0,
    "last_updated_at": ""
}


def _read_raw():
    if not os.path.exists(STATE_FILE):
        return dict(DEFAULT_STATE)
    with open(STATE_FILE, "r") as f:
        return json.load(f)


def _write_raw(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def read_state():
    """无锁读取（Stop 异步场景下使用）"""
    return _read_raw()


def write_state(state):
    """无锁写入"""
    state["last_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_raw(state)


def locked_update(updater):
    """
    带文件锁的更新操作。
    updater 是一个接收 state dict 并返回修改后 state 的函数。
    用于 UserPromptSubmit（同步场景），确保不会被 Stop 并发覆盖。
    跨平台：Linux/macOS 用 flock；Windows 无 fcntl，退化为无锁（钩子为单线程顺序触发，实际无并发风险）。
    """
    try:
        with open(LOCK_FILE, "w") as lockf:
            import fcntl
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                state = _read_raw()
                state = updater(state)
                state["last_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                _write_raw(state)
                return state
            finally:
                fcntl.flock(lockf, fcntl.LOCK_UN)
    except ImportError:
        # Windows 无 fcntl —— 无锁降级执行（hook 按序触发，无并发）
        state = _read_raw()
        state = updater(state)
        state["last_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_raw(state)
        return state


def reset_after_cloud():
    """云端执行完后归零"""
    locked_update(lambda s: {
        **s,
        "score": 0,
        "upgrade_next_turn": False,
        "target_provider": "",
        "last_route": "cloud",
        "cloud_cooldown_turns": s.get("cloud_cooldown_turns", 0)  # 保持配置
    })


# ===================================================================
# Per-session 状态管理
#
# 每个会话一份 state 文件：state/session_<session_id>.json
# 配套一个轻量 last-write-ts 文件记录每个会话最近活动时刻，
# 用于"活跃会话"判定（多会话自动暂停）。
# ===================================================================

SESSION_SUFFIX = ".json"
SESSION_PREFIX = "session_"
# 会话活动窗口（秒）：超过这个时间没活动的会话视为已退出
SESSION_ACTIVE_WINDOW = 120


def _session_filename(session_id):
    """会话安全文件名（UUID 去掉连字符即可，唯一且合法）。"""
    safe = session_id.replace("-", "")
    if not re.match(r"^[A-Za-z0-9]+$", safe):
        safe = "unknown"
    return os.path.join(STATE_DIR, SESSION_PREFIX + safe + SESSION_SUFFIX)


def session_state_path(session_id):
    """返回某会话的 state 文件路径（不创建）。"""
    return _session_filename(session_id)


def read_session_state(session_id):
    """读取某会话的 state，不存在则返回副本。"""
    path = _session_filename(session_id)
    if not os.path.exists(path):
        return dict(DEFAULT_STATE)
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return dict(DEFAULT_STATE)


def write_session_state(session_id, state):
    """写入某会话的 state（带时间戳）。"""
    state["last_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = _session_filename(session_id)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    # 跨平台原子替换；Windows 用 replace，其它系统先 unlink 再 rename
    try:
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)


def _now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def count_active_sessions():
    """
    统计"活跃会话数"。

    判定标准：所有 session_*.json 文件的 last_updated_at 落在
    SESSION_ACTIVE_WINDOW 秒内的数量。过旧的文件视为该会话已退出。

    无 session_id 时（旧 payload）退化为：只看全局 state 文件是否存在，
    返回 1，行为与无隔离时一致。
    """
    import datetime as _dt

    files = glob.glob(os.path.join(STATE_DIR, SESSION_PREFIX + "*" + SESSION_SUFFIX))
    cutoff = _dt.datetime.utcnow() - _dt.timedelta(seconds=SESSION_ACTIVE_WINDOW)
    active = 0
    for f in files:
        try:
            with open(f, "r") as fh:
                st = json.load(fh)
        except Exception:
            continue
        ts = st.get("last_updated_at", "")
        try:
            # 形如 2026-08-25T03:48:45Z
            d = _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue
        if d >= cutoff:
            active += 1
    # 兜底：一个 session 文件都没有，但全局 state 文件存在且新鲜
    if active == 0 and os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as fh:
                st = json.load(fh)
            ts = st.get("last_updated_at", "")
            d = _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
            if d >= cutoff:
                active = 1
        except Exception:
            pass
    return active


def log_turn_event(session_id, action, extra=None):
    """
    记录一条审计事件（per-session 也可独立存一份，但统一走全局审计日志，
    方便一次性排查多会话）。
    """
    try:
        entry = {"ts": _now_ts(), "session_id": session_id, "action": action}
        if extra:
            entry.update(extra)
        with open(AUDIT_PATH, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
