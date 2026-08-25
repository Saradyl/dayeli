#!/usr/bin/env python3
"""
Magic_Hook 诊断工具 v2 — 查看激活状态、当前路由、各会话积分。
运行：python <path-to>/magic_status.py
"""

import json
import os
import sys
import glob

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HOOK_DIR, "..", "state")
AUDIT_FILE = os.path.join(STATE_DIR, "magic_router_audit.jsonl")
CONFIG_PATH = os.path.join(HOOK_DIR, "magic_router_config.json")

sys.path.insert(0, HOOK_DIR)
import magic_router

print("=== Magic_Hook Status (v2) ===")
print(f"Config: {os.path.exists(CONFIG_PATH)}")
print()

# 1. 激活状态（需求#1）
provider = magic_router.current_claude_provider_name()
active = magic_router.magic_active()
print(f"cc-switch 当前选中 : {provider}")
print(f"专用双copy provider: {magic_router.MAGIC_DEDICATED_PROVIDER}")
print(f"magic_hook 激活    : {'[激活]' if active else '[未激活]'}")
if not active:
    print("    （只有选中双copy专用 provider 才激活；单copy=常驻本地，其他云=不干预）")
print()

# 2. 当前路由决策（magic_target.json，15666 代理热读）
tgt = magic_router.read_magic_target()
print(f"当前路由决策 magic_target: {tgt} ({'本地' if tgt=='base' else '云端升级'})")
print()

# 3. 配置
if os.path.exists(CONFIG_PATH):
    try:
        cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
        sc = cfg.get("scoring", {})
        print(f"Enabled:   {cfg.get('_meta', {}).get('enabled', False)}")
        print(f"Threshold: {sc.get('upgrade_threshold', '?')}")
        print(f"Cloud:     {cfg.get('cloud', {}).get('primary', '?')}")
        print()
    except Exception as e:
        print(f"配置读取失败: {e}")

# 4. 各会话状态（v2 按会话存）
session_files = glob.glob(os.path.join(STATE_DIR, "session_*.json"))
print(f"会话状态文件: {len(session_files)} 个")
for f in sorted(session_files, key=os.path.getmtime, reverse=True):
    try:
        st = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    sid = os.path.basename(f)[len("session_"):-len(".json")]
    print(f"  [{sid[:12]}…] score={st.get('score',0)} route={st.get('last_route','?')} "
          f"upgrade={st.get('upgrade_next_turn',False)} cooldown={st.get('cloud_cooldown_turns',0)} "
          f"updated={st.get('last_updated_at','')}")
# 全局兜底文件
g = os.path.join(STATE_DIR, "magic_router_state.json")
if os.path.exists(g):
    try:
        st = json.load(open(g, encoding="utf-8"))
        print(f"  [全局兜底] score={st.get('score',0)} route={st.get('last_route','?')} "
              f"updated={st.get('last_updated_at','')}")
    except Exception:
        pass
print()

# 5. 最近审计
if os.path.exists(AUDIT_FILE):
    with open(AUDIT_FILE, encoding="utf-8") as f:
        lines = f.readlines()
    print(f"Recent audit ({len(lines)} entries, last 6):")
    for line in lines[-6:]:
        try:
            d = json.loads(line)
            ts = d.get("ts", "")[:19]
            if "action" in d:
                print(f"  {ts} [{d.get('action')}] trigger={d.get('trigger','')} "
                      f"target={d.get('target','')} session={str(d.get('session_id',''))[:8]}")
            else:
                print(f"  {ts} route={d.get('route','')} {d.get('score_before','')}->{d.get('score_after','')} "
                      f"upgrade={d.get('upgrade_next_turn','')} reasons={d.get('reasons','')}")
        except Exception:
            pass
