#!/usr/bin/env python3
"""
Magic_Hook 配置合并工具 — 幂等地把 hooks/def.json 的 hook 配置
写回用户级 ~/.claude/settings.json，**只补 hooks 段，不删你别的配置**。

用途：如果某次清理/升级又抹掉了 settings.json 里的 hook，
跑一次本脚本就能把它补回来（幂等：已存在则不动，不会重复叠加）。

用法:  python hooks/merge_hooks.py
"""

import json
import os
import sys

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
DEF_FILE = os.path.join(HOOKS_DIR, "def.json")
SETTINGS_FILE = os.path.expanduser("~/.claude/settings.json")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path, data):
    """原子写：先写 .tmp 再 replace。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def main():
    if not os.path.exists(DEF_FILE):
        print(f"[merge_hooks] 找不到配置文件: {DEF_FILE}", file=sys.stderr)
        return 1

    try:
        settings = load_json(SETTINGS_FILE)
    except FileNotFoundError:
        print(f"[merge_hooks] 找不到 settings.json: {SETTINGS_FILE}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[merge_hooks] 读 settings.json 失败: {e}", file=sys.stderr)
        return 1

    with open(DEF_FILE, "r", encoding="utf-8") as f:
        desired = json.load(f)

    if "hooks" not in settings or not isinstance(settings["hooks"], dict):
        settings["hooks"] = {}

    before = json.dumps(settings["hooks"], ensure_ascii=False, sort_keys=True)

    # 清理错误大小写/下划线的重复键（如 user_prompt_submit / stop），
    # 只保留 def.json 里定义的正确键名（UserPromptSubmit / Stop）。
    def norm(k):
        return k.replace("_", "").replace("-", "").lower()
    valid_norm = {norm(vk) for vk in desired.keys()}
    for k in list(settings["hooks"].keys()):
        if norm(k) in valid_norm and k not in desired:
            del settings["hooks"][k]

    # 幂等合并：逐个 hook 类型写入（正确键名）
    for hook_type, value in desired.items():
        settings["hooks"][hook_type] = value

    after = json.dumps(settings["hooks"], ensure_ascii=False, sort_keys=True)

    if before == after:
        print("[merge_hooks] hooks 配置已是目标状态，无需改动")
    else:
        dump_json(SETTINGS_FILE, settings)
        print("[merge_hooks] 已写入 hooks 配置:")
        for hook_type, value in desired.items():
            cmds = [h.get("command", "?") for group in value for h in group.get("hooks", [])]
            print(f"    {hook_type}: {', '.join(cmds)}")

    # 校验：目标 hook 是否都在
    ok = True
    for hook_type, value in desired.items():
        got = settings["hooks"].get(hook_type, [])
        if len(got) != len(value):
            print(f"[merge_hooks] 警告: {hook_type} 钩子数不匹配", file=sys.stderr)
            ok = False
    if ok:
        print("[merge_hooks] 校验通过：所有 hook 已在位")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
