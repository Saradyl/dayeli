"""
Magic_Hook 状态管理 — 线程安全的 JSON 状态读写
跨平台兼容：Windows 没有 fcntl，做降级处理。
"""

import json
import os
import time

STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "state")
STATE_FILE = os.path.join(STATE_DIR, "magic_router_state.json")
LOCK_FILE = os.path.join(STATE_DIR, "magic_router.lock")

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
