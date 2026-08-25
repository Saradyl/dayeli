"""
Magic_Hook 路由模块 — 通过 cc-switch 配置文件切换 provider

机制：cc-switch 热切换的本质就是改 ~/.cc-switch/settings.json 里的
currentProviderClaude 字段，指向目标 provider 的 UUID。
本模块只读 cc-switch DB（name→UUID 解析），只改 settings.json 的
currentProviderClaude 字段，不触碰任何 provider 配置。
"""

import json
import os
import sqlite3
import time
import tempfile

CC_SWITCH_DIR = os.path.expanduser("~/.cc-switch")
CC_SWITCH_SETTINGS = os.path.join(CC_SWITCH_DIR, "settings.json")
CC_SWITCH_DB = os.path.join(CC_SWITCH_DIR, "cc-switch.db")


def resolve_provider_uuid(name):
    """
    从 cc-switch 数据库按 name 解析 provider UUID。
    返回 UUID 字符串，找不到返回 None。
    注意：这是只读操作。
    """
    if not name:
        return None
    if not os.path.exists(CC_SWITCH_DB):
        print(f"[MagicHook] DB 不存在: {CC_SWITCH_DB}", flush=True)
        return None
    # 只读操作，加 busy_timeout + 重试，容忍 cc-switch 进程间歇性占用 DB
    busy_ms = 3000
    for attempt in range(3):
        try:
            conn = sqlite3.connect(CC_SWITCH_DB, timeout=busy_ms / 1000.0)
            conn.execute("PRAGMA busy_timeout = %d" % busy_ms)
            cur = conn.cursor()
            cur.execute("SELECT id, name FROM providers WHERE name = ?", (name,))
            rows = cur.fetchall()
            conn.close()
            if not rows:
                print(f"[MagicHook] 找不到 provider: {name}", flush=True)
                return None
            return rows[0][0]
        except Exception as e:
            print(f"[MagicHook] name→UUID 解析失败(第{attempt+1}次): {e}", flush=True)
            time.sleep(0.5)
            continue
    return None


def current_claude_provider_name():
    """读当前 settings.json，返回当前 Claude provider 名。"""
    try:
        with open(CC_SWITCH_SETTINGS) as f:
            s = json.load(f)
        uuid = s.get("currentProviderClaude", "")
        if not uuid:
            return ""
        conn = sqlite3.connect(CC_SWITCH_DB)
        cur = conn.cursor()
        cur.execute("SELECT name FROM providers WHERE id = ?", (uuid,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else ""
    except Exception as e:
        print(f"[MagicHook] 读取当前 provider 失败: {e}", flush=True)
        return ""


def set_claude_provider(name):
    """
    热切 Claude provider。先原子备份，再改 settings.json。
    如果 target 解析失败返回 False。
    """
    if not name:
        return False
    uuid = resolve_provider_uuid(name)
    if not uuid:
        return False

    try:
        with open(CC_SWITCH_SETTINGS) as f:
            s = json.load(f)

        # 幂等：如果已经是目标，直接返回成功
        if s.get("currentProviderClaude") == uuid:
            print(f"[MagicHook] 已经是目标 provider: {name}", flush=True)
            return True

        s["currentProviderClaude"] = uuid
        tmp = CC_SWITCH_SETTINGS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CC_SWITCH_SETTINGS)
        print(f"[MagicHook] 热切 Claude provider → {name}", flush=True)
        return True
    except Exception as e:
        print(f"[MagicHook] 热切失败: {e}", flush=True)
        return False


def is_local_provider(name):
    """Heuristic: does this provider name look like a local/low-cost model?

    Adjust the markers to match your own local provider naming.
    """
    if not name:
        return False
    local_markers = ["local", "my-local-model"]
    return any(m in name.lower() for m in local_markers)


# ===================================================================
# Magic_Hook v2 — activation gate + magic_target.json routing
#
# Mechanism: a dedicated cc-switch provider points its base_url at the
# magic proxy (default 127.0.0.1:15666). Up/downgrading does NOT switch
# the cc-switch provider; instead it writes magic_target.json in the temp
# dir, which the proxy hot-reads on every request to pick base/upgrade.
# This keeps the cc-switch provider constant, so the activation gate
# stays consistent.
# ===================================================================

# The dedicated provider that activates Magic_Hook. CHANGE THIS to the name
# of the cc-switch provider whose base_url points at the magic proxy (15666).
MAGIC_DEDICATED_PROVIDER = "my-local-model-dynamic"
# The magic proxy port. The dedicated provider's base_url points at 127.0.0.1:15666.
MAGIC_PROXY_PORT = "15666"
# Routing-decision file shared with magic_proxy.py / magic_proxy_cli.py
MAGIC_TARGET_FILE = os.path.join(tempfile.gettempdir(), "magic_target.json")


def is_magic_dedicated(name):
    """Exact-match the dedicated provider.

    MUST be exact match (not substring). Substring matching would confuse a
    resident-local provider with the dedicated dynamic one.
    """
    return (name or "").strip() == MAGIC_DEDICATED_PROVIDER


def magic_active():
    """Is Magic_Hook active? = has the user selected the dedicated provider?

    IMPORTANT: do NOT judge by ANTHROPIC_BASE_URL. If you use cc-switch's
    local hot-switch routing proxy, the base_url is always that proxy's port
    and never reveals the backend. The reliable signal is the currently
    selected cc-switch provider NAME:
      - == dedicated provider -> traffic goes through the magic proxy -> active
      - any other provider    -> not through the magic proxy           -> inactive
    """
    try:
        return is_magic_dedicated(current_claude_provider_name())
    except Exception:
        return False


def current_base_url():
    """Read ANTHROPIC_BASE_URL from ~/.claude/settings.json env.

    Note: if you run cc-switch's local hot-switch routing proxy, this value
    is always that proxy's port and cannot be used to detect activation;
    it is for diagnostics only.
    """
    try:
        settings_path = os.path.expanduser("~/.claude/settings.json")
        with open(settings_path, encoding="utf-8") as f:
            s = json.load(f)
        return (s.get("env", {}) or {}).get("ANTHROPIC_BASE_URL", "")
    except Exception:
        return ""


def read_magic_target():
    """读 magic_target.json，返回 'base'/'upgrade'；缺失或异常默认 'base'。"""
    try:
        if os.path.exists(MAGIC_TARGET_FILE):
            with open(MAGIC_TARGET_FILE, encoding="utf-8") as f:
                d = json.load(f)
            v = d.get("target", "base")
            if v in ("base", "upgrade"):
                return v
    except Exception:
        pass
    return "base"


def write_magic_target(target, reason=""):
    """写 magic_target.json，让 15666 代理按此路由。成功返回 True。"""
    if target not in ("base", "upgrade"):
        return False
    try:
        with open(MAGIC_TARGET_FILE, "w", encoding="utf-8") as f:
            json.dump({"target": target, "reason": reason,
                       "updated_at": time.time()}, f)
        return True
    except Exception:
        return False


def current_magic_route():
    """当前 magic_target 对应的路由：'local'(base) / 'cloud'(upgrade)。"""
    return "local" if read_magic_target() == "base" else "cloud"
