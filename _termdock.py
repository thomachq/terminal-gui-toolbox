# -*- coding: utf-8 -*-
"""终端页 + 底部日志面板：日志升起后输入不许被挤掉。

用户 2026-10-25 报的 bug（截图里输入框整条不见）：
    底部日志面板一升起 → 终端区被压矮 → 输入行整条消失，
    正文却还占着 20 多行的高度。

真因是 pack 顺序：正文 `expand=True`、请求高度是 Text 默认的 24 行，
输入行最后才 pack —— 空间不够时第一个被压成 0 高。`winfo_manager()`
仍然是 "pack"，从返回值完全看不出问题，只有 `winfo_ismapped()` /
几何位置才诚实。

这个脚本在真窗口里量三个状态：
    日志未开 → 日志升起（默认高）→ 日志拖到允许的最高
两种输入形态都量：行内（默认，没有输入行）与输入框（有输入行）。

    python _termdock.py            # 只量数值
    python _termdock.py --shot     # 量完再抓一张图到 screenshots/
"""
from __future__ import annotations

import sys
import time

import wbtui as U

FAILS: list[str] = []
N = 0


def chk(ok, name, info=""):
    global N
    N += 1
    if not ok:
        FAILS.append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{info}]" if info else ""))


def pump(app, sec=0.5):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.update()
        time.sleep(0.01)


def inside(widget, panel_h: int) -> bool:
    """控件的底边是否落在面板内、且自己有高度。"""
    if widget is None:
        return False
    try:
        bottom = widget.winfo_y() + widget.winfo_height()
        return widget.winfo_height() > 1 and bottom <= panel_h + 1
    except Exception:  # noqa: BLE001
        return False


def snap(pane, tag: str) -> dict:
    """量一次并打印。返回量到的数字，供断言用。"""
    ph = pane.winfo_height()
    row, wrap = pane.row, pane.wrap
    row_in = inside(row, ph)
    wrap_in = inside(wrap, ph)
    print(f"    {tag:14} 面板高={ph:4d} | 正文 y={wrap.winfo_y():4d} h={wrap.winfo_height():4d} "
          f"完整={wrap_in} | 输入行 mapped={row.winfo_ismapped()} "
          f"y={row.winfo_y():4d} h={row.winfo_height():3d} 完整={row_in}")
    return {"ph": ph, "row_in": row_in, "wrap_in": wrap_in,
            "row_h": row.winfo_height(), "row_mgr": row.winfo_manager(),
            "wrap_h": wrap.winfo_height()}


def main() -> int:
    import app as A

    app = A.ToolboxApp()
    pump(app, 1.3)
    try:
        if app.state() == "zoomed":
            app.state("normal")
        app.geometry("1400x900")
    except Exception:  # noqa: BLE001
        pass
    pump(app, 0.9)

    pane = app._term_add_tab()
    pump(app, 0.7)
    chk(pane is not None, "终端页签建出来了")
    # 存档里可能记着「日志上次是开着的」，那样开局就已经被压矮过一次，
    # 后面「升起日志」就成了空操作。先归零，量到的差才是日志造成的。
    app.log_dock_toggle(False)
    pump(app, 0.9)
    print(f"\n---- 窗口 {app.winfo_width()}x{app.winfo_height()}")

    # ---------------- 形态一：行内（默认）
    print("\n== 行内（默认形态，没有独立输入行）==")
    chk(pane.mode == "inline", "默认就是行内", pane.mode)
    chk(pane.row.winfo_manager() == "", "不给输入行留高度", repr(pane.row.winfo_manager()))
    a = snap(pane, "日志未开")
    chk(a["wrap_in"], "日志未开时正文完整")

    app.log_dock_open()
    pump(app, 0.9)
    b = snap(pane, "日志升起")
    chk(b["wrap_in"], "日志升起后正文仍完整")
    chk(b["ph"] < a["ph"], "终端区确实被压矮了（这就是触发条件）",
        f"{a['ph']} -> {b['ph']}")

    # 把日志拖到允许的最高：走产品自己的拖动路径，别自己造一个高度
    # （自己 configure(height=...) 会绕过上限逻辑，量出来的不是用户能做到的状态）
    try:
        app._log_drag = (0, app.log_dock_host.winfo_height())
        app._log_drag_move(type("E", (), {"y_root": -100000})())
        app._log_drag = None
        pump(app, 0.8)
    except Exception as e:  # noqa: BLE001
        print(f"    （拖高失败：{e}）")
    c = snap(pane, "日志拖到最高")
    chk(c["ph"] >= U.px(160),
        f"日志拖到最高后主区还剩得下终端（≥{U.px(160)} 物理）", f"{c['ph']}")
    chk(c["wrap_in"], "日志拖到最高时正文仍完整")

    # ---------------- 形态二：输入框
    print("\n== 输入框（有独立输入行，就是用户报的那条）==")
    pane.seg_mode.select("box")
    pump(app, 0.6)
    chk(pane.mode == "box", "切到输入框形态", pane.mode)
    d = snap(pane, "日志仍最高")
    chk(d["row_mgr"] == "pack", "输入行在布局里", repr(d["row_mgr"]))
    chk(d["row_in"], "日志拖到最高时输入行仍完整可见",
        f"h={d['row_h']} 面板={d['ph']}")
    chk(d["row_h"] > U.px(20), "输入行有实际高度，不是被压成一条线",
        f"{d['row_h']} 逻辑 {d['row_h']/max(1,U.S):.0f}")

    app.log_dock_toggle(False)
    pump(app, 0.8)
    e = snap(pane, "日志收起")
    chk(e["row_in"], "日志收起后输入行完整")
    chk(e["ph"] > d["ph"], "收起日志后终端区变高", f"{d['ph']} -> {e['ph']}")

    app.log_dock_open()
    pump(app, 0.8)
    f = snap(pane, "再升起")
    chk(f["row_in"], "再升起后输入行还在（可反复开关）")

    if "--shot" in sys.argv:
        # 直接 ImageGrab 抓屏幕矩形会被别的窗口盖住（实测抓到的是背后的
        # 编辑器），必须先把本窗口抬到最前再 BitBlt。
        try:
            import ctypes
            from _shot_term import capture_screen
            hwnd = ctypes.windll.user32.GetParent(app.winfo_id()) or app.winfo_id()
            for mode, name in (("inline", "term_dock_inline"), ("box", "term_dock_box")):
                pane.seg_mode.select(mode)
                pump(app, 0.6)
                img = capture_screen(hwnd)
                if img is None:
                    raise RuntimeError("抓屏返回空")
                path = f"screenshots/{name}.png"
                img.save(path)
                print(f"\n    截图已存 {path}  ({img.width}x{img.height})")
        except Exception as e:  # noqa: BLE001
            print(f"\n    截图失败：{type(e).__name__}: {e}")

    try:
        app.destroy()
    except Exception:  # noqa: BLE001
        pass
    print(f"\n结果：{N - len(FAILS)} 通过 / {len(FAILS)} 失败")
    for x in FAILS:
        print(f"  · {x}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
