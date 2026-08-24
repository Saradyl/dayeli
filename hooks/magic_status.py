#!/usr/bin/env python3
"""
Magic_Hook 诊断工具 — 查看当前状态、积分、路由方向。
运行：python3 .claude/hooks/magic_status.py
"""

import json
import os
import sys

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HOOK_DIR, "..", "state")
STATE_FILE = os.path.join(STATE_DIR, "magic_router_state.json")
AUDIT_FILE = os.path.join(STATE_DIR, "magic_router_audit.jsonl")
CONFIG_PATH = os.path.join(HOOK_DIR, "magic_router_config.json")

sys.path.insert(0, HOOK_DIR)
from magic_router import current_claude_provider_name, resolve_provider_uuid, CC_SWITCH_SETTINGS

# 1. 当前状态
print("=== Magic_Hook Status ===")
print(f"Config: {os.path.exists(CONFIG_PATH)}")
print(f"State:  {os.path.exists(STATE_FILE)}")
print(f"Audit:  {os.path.exists(AUDIT_FILE)}")
print()

if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    enabled = cfg.get("_meta", {}).get("enabled", False)
    print(f"Enabled:   {enabled}")
    print(f"Threshold: {cfg.get('scoring', {}).get('upgrade_threshold', '?')}")
    print(f"Local:     {cfg.get('local', {}).get('primary', '?')}")
    print(f"Cloud:     {cfg.get('cloud', {}).get('primary', '?')}")
    print()

# 2. 当前 provider
current_name = current_claude_provider_name()
print(f"Current Claude provider: {current_name}")

# 3. 状态文件
if os.path.exists(STATE_FILE):
    with open(STATE_FILE) as f:
        st = json.load(f)
    print(f"Score:      {st.get('score', '?')}")
    print(f"Last route: {st.get('last_route', '?')}")
    print(f"Upgrade next turn: {st.get('upgrade_next_turn', False)}")
    print(f"Target:     {st.get('target_provider', '')}")
    print(f"Consecutive failures: {st.get('consecutive_local_failures', 0)}")
    print()

# 4. 最近审计日志
if os.path.exists(AUDIT_FILE):
    with open(AUDIT_FILE) as f:
        lines = f.readlines()
    print(f"Recent audit ({len(lines)} entries, last 5):")
    for line in lines[-5:]:
        try:
            d = json.loads(line)
            print(f"  {d.get('ts','')[:19]} route={d.get('route','')} score={d.get('score_before','')}->{d.get('score_after','')} upgrade={d.get('upgrade_next_turn','')} reasons={d.get('reasons','')[:30]}")
        except:
            pass