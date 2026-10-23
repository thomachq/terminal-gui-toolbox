# -*- coding: utf-8 -*-
"""托盘与退出机制自检。

用户 2026-10-23 报的问题：关掉窗口后进程还在，任务栏右下角也找不到图标。
根因是 `close_to_tray` 默认 True —— 点 × 只是藏进托盘，而 Win11 默认把新
图标塞进「︿」溢出区，用户看不见就只能去任务管理器强杀。

本测试锁死三件事：
  1. 托盘底层（Shell_NotifyIconW）在本机真能起来；
  2. 默认**不**驻留托盘 —— 点 × 直接退；
  3. 真要收进托盘时，会画一个自己的提示窗（不依赖系统气泡）。

跑法：<系统 Python，需带 tkinter> _traytest.py
"""
from __future__ import annotations

import sys
import tkinter as tk
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PASS, FAIL = 0, 0
RESULTS: list[tuple[bool, str, str]] = []


def chk(ok, name: str, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    RESULTS.append((bool(ok), name, str(detail)))
    print(("  [PASS] " if ok else "  [FAIL] ") + name
          + (f"  —— {detail}" if detail and not ok else ""))
    return bool(ok)


def pump(win, seconds: float = 0.2) -> None:
    import time
    end = time.time() + seconds
    while time.time() < end:
        try:
            win.update()
        except tk.TclError:
            return
        time.sleep(0.02)


class FakeTray:
    """假托盘：只提供 app 用到的三个接口，避免测试时真去弹气泡。"""

    def __init__(self, running: bool = True):
        self.running = running
        self.notes: list[tuple[str, str]] = []
        self.stopped = False

    def notify(self, title: str, text: str) -> None:
        self.notes.append((title, text))

    def stop(self) -> None:
        self.stopped = True
        self.running = False


# ============================================================ 1. 托盘底层
def test_tray_low_level() -> None:
    print("\n[1] ui.tray —— Shell_NotifyIconW 这条路")
    from ui.tray import Tray

    if sys.platform != "win32":
        chk(True, "非 Windows 跳过托盘底层检查")
        return
    root = tk.Tk()
    root.withdraw()
    t = Tray(root, "托盘自检", "", menu_factory=lambda: tk.Menu(root, tearoff=0))
    started = t.start()
    chk(started, "Tray.start() 起得来", t.last_error)
    chk(t.running, "running 为 True")
    chk(bool(t._hwnd), "拿到隐藏宿主窗口句柄", t._hwnd)
    t.stop()
    pump(root, 0.1)
    root.destroy()


# ============================================================ 2. 退出语义
def test_quit_semantics() -> None:
    print("\n[2] 退出语义 —— 点 × 该不该退")
    import app as A

    src = (HERE / "app.py").read_text(encoding="utf-8")
    chk('"close_to_tray", False' in src,
        "默认不驻留托盘（源码里的默认值就是 False）")

    a = A.ToolboxApp()
    try:
        pump(a, 0.4)
        # 有的机器上 settings.json 已经存了这个键，按存的走；没存必须是 False
        raw = a.settings.data.get("close_to_tray", None)
        expect = False if raw is None else bool(raw)
        chk(a._close_to_tray == expect,
            "关闭行为按设置生效",
            f"settings={raw!r} 实际={a._close_to_tray}")

        hits: list[str] = []
        real_quit = a._quit_app
        a._quit_app = lambda: hits.append("quit")
        a._tray = FakeTray(True)

        a._close_to_tray = False
        a._on_close()
        chk(hits == ["quit"], "托盘可用但开关关着 —— 点 × 直接退出", hits)

        hits.clear()
        a._close_to_tray = True
        a._on_close()
        chk(hits == [], "开关打开 —— 点 × 不退出（收进托盘）", hits)
        chk(a.state() == "withdrawn", "开关打开时窗口确实收起来了", a.state())

        hits.clear()
        a._close_to_tray = False
        a._tray = FakeTray(False)          # 托盘没起来
        a._on_close()
        chk(hits == ["quit"], "托盘起不来时 —— 点 × 必须退出，不能变幽灵进程", hits)

        a._tray = FakeTray(True)
        a._quit_app = real_quit
    finally:
        try:
            a._hide_tray_hint()
            a.destroy()
        except tk.TclError:
            pass


# ============================================================ 3. 去处提示
def test_tray_hint() -> None:
    print("\n[3] 收进托盘时的自绘提示")
    import app as A

    a = A.ToolboxApp()
    try:
        pump(a, 0.4)
        a._tray = FakeTray(True)
        a._close_to_tray = True
        a._hide_to_tray()
        pump(a, 0.2)

        win = a._tray_hint_win
        chk(win is not None, "画出了提示窗")
        chk(win is not None and bool(win.winfo_exists()), "提示窗真的存在")
        chk(isinstance(a._tray, FakeTray) and a._tray.notes != [],
            "同时发了系统气泡（有就用，没有也不影响）")

        a._hide_tray_hint()
        pump(a, 0.1)
        chk(a._tray_hint_win is None, "点掉后引用清空")
        chk(win is None or not win.winfo_exists(), "提示窗已销毁")

        # 提示窗关掉再收起，不应该残留上一条
        a._hide_to_tray()
        pump(a, 0.1)
        w1 = a._tray_hint_win
        a._hide_to_tray()
        pump(a, 0.1)
        w2 = a._tray_hint_win
        chk(w1 is not w2 and w2 is not None, "重复收起只保留一个提示窗")
        chk(w1 is None or not w1.winfo_exists(), "上一只提示窗已销毁")

        a._show_from_tray()
        pump(a, 0.1)
        chk(a.state() == "normal", "双击托盘能恢复窗口", a.state())
        chk(a._tray_hint_win is None, "恢复时顺手收掉提示窗")
    finally:
        try:
            a._hide_tray_hint()
            a.destroy()
        except tk.TclError:
            pass


def main() -> int:
    print("== 托盘 / 退出机制自检 ==")
    try:
        test_tray_low_level()
        test_quit_semantics()
        test_tray_hint()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2
    print(f"\n合计 {PASS} 项通过，{FAIL} 项失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
