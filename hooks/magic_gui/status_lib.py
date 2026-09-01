#!/usr/bin/env python3
"""
magic_gui status_lib — 读取当前 Magic_Hook 路由状态，供弹窗显示。

数据来源：
  - cc-switch 数据库 → provider 名 + 是否双copy
  - magic_proxy_config.json → base/upgrade/backup 的模型名
  - magic_target.json → 当前路由决策（base/upgrade + level）

输出一条给 tkinter 弹窗用的摘要文本。
"""

import json
import os
import sys
import tempfile


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def build_status():
    """返回一个 dict 包含状态摘要文本。"""
    # ── 1. cc-switch 当前 provider ──
    cc_switch_file = os.path.expanduser("~/.cc-switch/settings.json")
    cc = _read_json(cc_switch_file, {})
    provider_name = ""
    if cc:
        uuid = cc.get("currentProviderClaude", "")
        if uuid:
            db = os.path.expanduser("~/.cc-switch/cc-switch.db")
            try:
                import sqlite3
                conn = sqlite3.connect(db, timeout=3.0)
                cur = conn.cursor()
                cur.execute("SELECT name FROM providers WHERE id=?", (uuid,))
                row = cur.fetchone()
                if row:
                    provider_name = row[0]
                conn.close()
            except Exception:
                pass

    is_dedicated = (provider_name or "").strip() == "my-local-model-dynamic"  # 改成你 cc-switch 的双copy provider 名

    # ── 2. magic_target 当前路由 ──
    target_file = os.path.join(tempfile.gettempdir(), "magic_target.json")
    tgt = _read_json(target_file, {})
    target = tgt.get("target", "base") if tgt else "base"
    level = tgt.get("level", 1) if tgt else 1

    # ── 3. proxy 配置 → 模型名 ──
    proxy_config_file = os.path.join(os.path.dirname(__file__), "..", "magic_proxy_config.json")
    proxy = _read_json(proxy_config_file, {})
    base_model = ""
    cloud1_model = ""
    cloud2_model = ""
    if proxy:
        base = proxy.get("base", {}).get("primary", {})
        base_model = base.get("default_model", "") or base.get("name", "")
        up = proxy.get("upgrade", {})
        p = up.get("primary", {})
        cloud1_model = p.get("default_model", "") or p.get("name", "")
        b = up.get("backup", {})
        if b:
            cloud2_model = b.get("default_model", "") or b.get("name", "")

    # ── 4. 拼装摘要 ──
    lines = []
    if is_dedicated:
        lines.append("┌──── Magic_Hook 状态 ────┐")
        if target == "base":
            lines.append(f"│ 当前模型 : {base_model or '本地'}")
            lines.append(f"│ 路由     : 本地 (base)")
        else:
            if level == 1:
                lines.append(f"│ 当前模型 : {cloud1_model or '云1'}")
                lines.append(f"│ 路由     : 云端 (level=1)")
            else:
                lines.append(f"│ 当前模型 : {cloud2_model or '云2'}")
                lines.append(f"│ 路由     : 云端 (level=2)")
        lines.append(f"│ hook     : 已激活")
        lines.append(f"│ provider : {provider_name}")
        lines.append("└──────────────────────────┘")
    else:
        lines.append("┌──── Magic_Hook 状态 ────┐")
        lines.append(f"│ 当前 provider: {provider_name or '(未知)'}")
        lines.append(f"│ hook     : 未激活")
        lines.append(f"│ 暗号仅在双copy下生效")
        lines.append("└──────────────────────────┘")

    summary = "\n".join(lines)
    return {"summary": summary, "provider": provider_name, "target": target, "level": level}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    s = build_status()
    print(s["summary"])
    print()
    print("当前路由:", s["target"], "level:", s.get("level", 1))