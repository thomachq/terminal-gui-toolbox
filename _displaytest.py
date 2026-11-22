# -*- coding: utf-8 -*-
"""v1.0.39 专项回归：连接池同宽 + RDP 改分辨率后界面/终端自适应。

离线跑：真实 Tk 控件，假分辨率（只替换 winstate.work_area 的返回值），不联外网、
不动用户 data（TB_DATA_DIR 指到临时目录）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.argv = [sys.argv[0], "--no-probe"]
_TD = tempfile.mkdtemp(prefix="tbdisplay_")
os.environ["TB_DATA_DIR"] = _TD
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = FAIL = 0
BAD: list[str] = []


def chk(ok, name, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok   {name}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL += 1
        BAD.append(name)
        print(f"  FAIL {name}  [{detail}]")


def pump(root, sec=0.2):
    end = time.time() + sec
    while time.time() < end:
        try:
            root.update()
        except tk.TclError:
            return
        time.sleep(0.01)


def card_by_title(page, title: str):
    for w in page.winfo_children():
        lab = getattr(w, "_title", None)
        if lab is not None:
            try:
                if lab.cget("text") == title:
                    return w
            except tk.TclError:
                pass
    return None


def main():
    import app as APP
    from core import winstate as WS

    a = APP.ToolboxApp()
    a.geometry("1380x880+20+20")
    a.deiconify()
    pump(a, 0.5)

    print("\n1. 智能体接入页：连接池同宽")
    a._show("api")
    pump(a, 0.4)
    page = a._pages["api"]
    agent = card_by_title(page, "智能体接入")
    pool = card_by_title(page, "连接池")
    chk(agent is not None and pool is not None, "两张卡片都已构建")
    if agent and pool:
        wa, wp = agent.winfo_width(), pool.winfo_width()
        chk(wa > 100 and abs(wa - wp) <= 2, "连接池与智能体接入同宽",
            f"{wp} vs {wa}")
        xa, xp = agent.winfo_x(), pool.winfo_x()
        chk(abs(xa - xp) <= 2, "两张卡片左边缘对齐", f"{xp} vs {xa}")

    print("\n2. 运行中分辨率变化")
    chk(a._usable_area((0, 0, 1707, 960)) == (0, 0, 1707, 960),
        "SPI 工作区还报旧大屏时按实际屏幕收敛",
        str(a._usable_area((0, 0, 1707, 960))))
    new_area = (0, 0, 1024, 728)
    orig_work_area = WS.work_area
    WS.work_area = lambda *_a, **_kw: new_area
    try:
        a.geometry("1560x960+600+400")
        a.update_idletasks()
        a._on_display_changed(new_area)
        pump(a, 0.4)
        w, h = a.winfo_width(), a.winfo_height()
        x, y = a.winfo_rootx(), a.winfo_rooty()
        chk(w <= new_area[2] and h <= new_area[3], "主窗口收进新工作区",
            f"{w}x{h}")
        chk(x >= 0 and y >= 0, "主窗口左上不再跑出屏幕", f"{x},{y}")
        mw, mh = a.minsize()
        chk(mw <= new_area[2] and mh <= new_area[3],
            "最小尺寸也跟着下调（否则 geometry 会被弹回旧尺寸）",
            f"{mw}x{mh}")
        if getattr(a, "_term_wins", None) is not None:
            chk(True, "拉出去的终端窗口列表已纳入自适应")
    finally:
        WS.work_area = orig_work_area

    print("\n3. 分辨率变化后仍能打开终端")
    if getattr(a, "_log_dock_on", False):
        a.log_dock_toggle(False)
        pump(a, 0.2)
    a.open_terminal()
    pump(a, 0.5)
    key = a._term_cur_key()
    pane = a._term_panes.get(key)
    chk(pane is not None and pane.winfo_exists() == 1, "终端页签已创建", key)
    if pane is not None:
        chk(pane.winfo_ismapped() == 1, "终端页签已经显示")
        chk(pane.winfo_height() > 40, "终端面板高度没有被压成 0",
            str(pane.winfo_height()))
        chk(pane.text.winfo_height() > 0, "终端正文可见",
            str(pane.text.winfo_height()))
    a.open_terminal()
    pump(a, 0.2)
    chk(len(a._term_panes) == 1, "重复点终端不会开出第二个",
        str(len(a._term_panes)))

    print("\n4. 轮询能发现屏幕变化")
    a.geometry("1500x950+50+50")
    a.update_idletasks()
    orig_snap = a._display_snapshot
    a._display_seen = orig_snap()
    a._display_snapshot = lambda: ((0, 0, 900, 700), (0, 0, 900, 700))
    try:
        a._watch_display()
        pump(a, 0.3)
        chk(a.winfo_width() <= 900 and a.winfo_height() <= 700,
            "轮询触发后主窗再次收敛", f"{a.winfo_width()}x{a.winfo_height()}")
    finally:
        a._display_snapshot = orig_snap

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    for b in BAD:
        print("  ·", b)
    try:
        a._close_to_tray = False
        a.destroy()
    except tk.TclError:
        pass
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
