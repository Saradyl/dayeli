#!/usr/bin/env python3
"""
magic_gui — dylwh 暗号钩子（UserPromptSubmit 调用）

用法：在提问末尾 30 字符内插入 dylwh（不区分大小写），本钩子会
① 读取当前 magic_hook 状态（当前模型、是否激活），
② 弹一个 Windows 风格的黄色便签（右上角），显示状态信息，
   约 6 秒后自动消失。

设计要点：
- 纯只读，不修改 magic_hook 的任何 state/配置。
- 弹窗口在「独立子进程」里做，本钩子立即退出，绝不阻塞对话。
- 与 magic_hook 激活与否无关：即使当前不在双copy 专用 provider 上，
  也会正常显示"当前是什么模型 / hook 是否激活"。

依赖：需要带 tkinter 的 python。若本脚本运行的解释器没有 tkinter，
内部会自动用带 tkinter 的 python 重启 notifier 子进程。

可选环境变量：
- DYLWH_MINUTES   窗口停留秒数（默认 6000ms）
"""

import json
import os
import re
import shutil
import subprocess
import sys


HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
PASSPHRASE_WINDOW = 30
PASSPHRASE = "dylwh"
NOTIFIER = os.path.join(HOOK_DIR, "notifier.py")


def detect_passphrase(prompt):
    """只看 prompt 最后 30 字符里的暗号。"""
    tail = (prompt or "")[-PASSPHRASE_WINDOW:].lower()
    return PASSPHRASE in tail


def find_interpreter_with_tkinter():
    """返回带 tkinter 的 python 可执行路径，找不到返回 None。

    优先 sys.executable；否则在 PATH 里找 python3 / python。
    """
    if sys.executable:
        try:
            code = 'import tkinter; print("ok")'
            out = subprocess.run([sys.executable, "-c", code],
                                 capture_output=True, text=True, timeout=5).stdout
            if "ok" in out:
                return sys.executable
        except Exception:
            pass
    for name in ("python3", "python"):
        found = shutil.which(name)
        if not found:
            continue
        try:
            out = subprocess.run([found, "-c", "import tkinter; print('ok')"],
                                 capture_output=True, text=True, timeout=5).stdout
            if "ok" in out:
                return found
        except Exception:
            continue
    return None


def notify_status():
    """弹 5 秒状态便签。非阻塞（子进程）。可在任何地方调用。

    设计为 importable 函数，供 magic_submit.py 的暗号处理逻辑调用。
    """
    if not os.path.exists(NOTIFIER):
        return
    py = find_interpreter_with_tkinter()
    if py is None:
        return
    try:
        subprocess.Popen(
            [py, NOTIFIER, "--seconds", "5000"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception:
        pass


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
    except Exception:
        raw = sys.stdin.read()

    try:
        d = json.loads(raw) if raw else {}
    except Exception:
        d = {}

    # 提取用户 prompt（兼容顶层 prompt 字段和 messages 数组）
    prompt = ""
    if isinstance(d.get("prompt"), str):
        prompt = d.get("prompt")
    for m in reversed(d.get("messages", []) or []):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                prompt = c
                break
            if isinstance(c, list):
                for seg in c:
                    if isinstance(seg, dict) and seg.get("type") == "text":
                        prompt += seg.get("text", "")

    if not detect_passphrase(prompt):
        return

    # 触发：弹便签。若找不到带 tkinter 的 python，静默跳过。
    if not os.path.exists(NOTIFIER):
        print(f"[MagicGui] notifier 未找到: {NOTIFIER}", flush=True)
        return

    py = find_interpreter_with_tkinter()
    if py is None:
        print("[MagicGui] 未找到带 tkinter 的 python，跳过弹窗", flush=True)
        return

    print("[MagicGui] dylwh 命中，弹出示状便签…", flush=True)
    try:
        # 子进程方式，非阻塞（调用方不等待）
        subprocess.Popen(
            [py, NOTIFIER],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception as e:
        print(f"[MagicGui] 弹窗启动失败: {e}", flush=True)


if __name__ == "__main__":
    main()
