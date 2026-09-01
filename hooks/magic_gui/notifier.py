#!/usr/bin/env python3
"""
magic_gui notifier — 弹一个 Windows 风格的黄色便签，显示当前模型状态。

由 dylwh/dylup/dylgo 暗号触发，约 5 秒后自动关闭。
"""

import os
import sys
import shutil
import subprocess


def ensure_tkinter():
    """若当前解释器没有 tkinter，用带 tkinter 的 python 重启本脚本并退出。"""
    try:
        import tkinter  # noqa: F401
        return
    except Exception:
        pass

    candidates = [s for s in (sys.executable, "python3", "python") if s]
    for cand in candidates:
        resolved = cand if (os.sep in cand) else shutil.which(cand)
        if not resolved or not os.path.exists(resolved):
            continue
        try:
            subprocess.call([resolved] + sys.argv)
        except Exception:
            continue
        sys.exit(0)
    sys.exit(0)


ensure_tkinter()

from ctypes import windll
try:
    windll.user32.SetCurrentProcessExplicitAppUserModelID("magic.hook.notifier")
except Exception:
    pass

import tkinter as tk
from tkinter import font as tkfont


def build_status_summary():
    """调用 status_lib 拿到摘要；失败则回退一段友好提示。"""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import status_lib
        return status_lib.build_status().get("summary", "无法读取状态。")
    except Exception:
        return "Magic_Hook 状态读取失败，请检查 cc-switch 是否运行。"


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=5000)
    args = parser.parse_args()
    close_ms = args.seconds

    summary = build_status_summary()

    # 配色
    WIN_BG = "#FFFFE5"
    HEADER_BG = "#FFE98A"
    TEXT_FG = "#1A1A1A"
    ACCENT = "#C99700"

    # 固定尺寸，足够显示 6-8 行文本
    win_w = 320
    margin_right, margin_top = 24, 60

    root = tk.Tk()
    root.title("Magic_Hook 状态")
    screen_w = int(root.winfo_screenwidth())

    x = max(0, screen_w - win_w - margin_right)
    y = margin_top
    root.geometry(f"{win_w}x1+{x}+{y}")
    root.resizable(False, False)
    root.configure(bg=WIN_BG)

    # ── 标题条 ──
    bar = tk.Frame(root, bg=HEADER_BG, height=34, cursor="fleur")
    bar.pack(fill="x")
    bar.pack_propagate(False)

    drag = {"x": 0, "y": 0}

    def on_down(e):
        drag["x"], drag["y"] = e.x, e.y

    def on_move(e):
        root.geometry("+%d+%d" % (root.winfo_x() + (e.x - drag["x"]),
                                  root.winfo_y() + (e.y - drag["y"])))

    bar.bind("<Button-1>", on_down)
    bar.bind("<B1-Motion>", on_move)

    tk.Label(bar, text="📝 Magic_Hook 状态", bg=HEADER_BG, fg=ACCENT,
             font=("Segoe UI", 11, "bold"), anchor="w",
             padx=12, relief="flat").pack(side="left", fill="y")

    def close():
        root.destroy()

    tk.Button(bar, text="✕", bg=HEADER_BG, fg=ACCENT, width=2, height=1,
              relief="flat", cursor="hand2", command=close).pack(
        side="right", padx=10, pady=5)

    # ── 正文（Text 控件，自动换行，支持多行） ──
    txt_font = tkfont.Font(family="Consolas", size=11)
    text = tk.Text(root, wrap="char", bg=WIN_BG, fg=TEXT_FG,
                   font=txt_font, relief="flat", padx=12, pady=12,
                   insertbackground=TEXT_FG, borderwidth=0, highlightthickness=0)
    # 先插内容，再设为只读（disabled 状态下 insert 会静默丢失内容）
    text.insert("1.0", summary)
    text.config(state="disabled")
    text.pack(side="top", fill="both", expand=True, padx=0, pady=0)

    # 底部小字
    tk.Label(root, text=f"约 {close_ms//1000} 秒后自动消失 · 也可点 ✕",
             bg=WIN_BG, fg="#888888", font=("Segoe UI", 8)).pack(
        side="bottom", pady=(0, 6))

    # 自动关闭
    def auto_close():
        try:
            root.destroy()
        except Exception:
            pass

    root.attributes("-topmost", True)
    root.after(close_ms, auto_close)
    root.protocol("WM_DELETE_WINDOW", auto_close)

    # 用 update_idletasks 让内容自然撑开窗口高度
    root.deiconify()
    root.update_idletasks()
    # 获取内容实际高度，设置窗口高度
    content_height = text.winfo_reqheight()
    bar_height = 34
    foot_height = 30
    win_h = bar_height + content_height + foot_height + 24
    win_h = min(win_h, 400)  # 最大 400px
    root.geometry(f"{win_w}x{win_h}+{x}+{y}")
    root.mainloop()


if __name__ == "__main__":
    main()