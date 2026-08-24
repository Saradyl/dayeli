#!/usr/bin/env python3
"""
Magic Proxy 管理工具 — 查看/控制路由。
"""

import json
import os
import sys
import time
import tempfile
import subprocess

_TD = tempfile.gettempdir()
TARGET_FILE = os.path.join(_TD, "magic_target.json")
PENDING_FILE = os.path.join(_TD, "magic_pending.json")
AUDIT_FILE = os.path.join(_TD, "magic_proxy_audit.jsonl")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "magic_proxy_config.json")


def status():
    print("=== Magic Proxy Status ===")
    # Proxy process check
    try:
        out = subprocess.run(["netstat", "-an"], capture_output=True, text=True, timeout=5).stdout
        running = "LISTENING" in out and "15666" in out
        print(f"  Port 15666:  {'[running]' if running else '[stopped]'}")
    except:
        print(f"  Port 15666:  ?")

    # Config
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            enabled = cfg.get("_meta", {}).get("enabled", True)
            print(f"  Proxy:     {'[ON]' if enabled else '[OFF]'}")
            patterns = cfg.get("activation", {}).get("model_patterns", [])
            print(f"  Activate:  {patterns}")
        except:
            print(f"  Config:    [parse error]")
    else:
        print(f"  Config:    [not found]")

    # Target
    target = "base (default)"
    if os.path.exists(TARGET_FILE):
        try:
            with open(TARGET_FILE, encoding="utf-8") as f:
                d = json.load(f)
            target = d.get("target", "?")
            reason = d.get("reason", "")
            print(f"  Route:     {'[UPGRADE]' if target == 'upgrade' else '[local]'} {reason}")
        except:
            pass
    else:
        print(f"  Route:     {target}")

    # Score
    if os.path.exists(PENDING_FILE):
        try:
            with open(PENDING_FILE, encoding="utf-8") as f:
                p = json.load(f)
            th = cfg.get("scoring", {}).get("upgrade_threshold", 7)
            print(f"  Score:     {p.get('score','?')}/{th}")
        except:
            pass
    else:
        print(f"  Score:     0")

    # Activate check
    print(f"  Models:    {cfg.get('base',{}).get('primary',{}).get('default_model','?')} (base) -> {cfg.get('upgrade',{}).get('primary',{}).get('default_model','?')} (upgrade)")


def set_target(t):
    if t not in ("base", "upgrade"):
        print(f"Usage: set [base|upgrade]")
        return
    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        json.dump({"target": t, "updated_at": time.time()}, f)
    print(f"  Route -> {t}")


def set_score(score):
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    threshold = cfg.get("scoring", {}).get("upgrade_threshold", 7)
    p = {"score": score, "threshold": threshold, "upgrade": score >= threshold}
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f)
    print(f"  Score -> {score}/{threshold} -> {'[UPGRADE]' if p['upgrade'] else '[local]'}")
    t = "upgrade" if score >= threshold else "base"
    set_target(t)


def on():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg["_meta"] = cfg.get("_meta", {})
    cfg["_meta"]["enabled"] = True
    cfg["_meta"]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print("  Proxy: ON")


def off():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg["_meta"] = cfg.get("_meta", {})
    cfg["_meta"]["enabled"] = False
    cfg["_meta"]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print("  Proxy: OFF")


def audit():
    if not os.path.exists(AUDIT_FILE):
        print("  Audit log empty")
        return
    with open(AUDIT_FILE, encoding="utf-8") as f:
        lines = f.readlines()
    print(f"  Audit ({len(lines)} entries, last 10):")
    for line in lines[-10:]:
        try:
            d = json.loads(line)
            ts = d.get("ts", "")[:19]
            action = d.get("action", d.get("route", "?"))
            print(f"    {ts} {action}")
        except:
            pass


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        status()
    elif cmd == "set":
        set_target(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "score":
        try:
            set_score(int(sys.argv[2]))
        except:
            print("Usage: score <number>")
    elif cmd == "on":
        on()
    elif cmd == "off":
        off()
    elif cmd == "audit":
        audit()
    else:
        print("Usage: magic_proxy_cli.py [status|set base|set upgrade|score N|on|off|audit]")