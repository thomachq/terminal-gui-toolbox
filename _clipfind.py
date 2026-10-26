# -*- coding: utf-8 -*-
"""找出「真的被切掉」的控件：映射出来、右下角超出画布右侧的叶子控件。

    python _clipfind.py            # 全部页
    python _clipfind.py sec db     # 只看这几页

叶子 = 没有子控件的控件（Label / Entry / Button / ttk 部件）。
它们没法自己压缩，一旦右边缘超出视口就是**真的看不见**，不是被挤压。

Tk 的父控件会裁掉超出自己范围的子控件，所以判断用「相对画布的绝对坐标」：
winfo_rootx 差一点不准（滚动时 y 会变），这里用 winfo_x 逐层累加到画布。
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")

import app as A  # noqa: E402

PAGES = ["hosts", "proxy", "cred", "script", "tools", "sys", "files", "routes",
         "sec", "docker", "web", "db", "cron", "vpn", "api"]


def pump(a, s: float) -> None:
    t0 = time.time()
    while time.time() - t0 < s:
        try:
            a.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.01)


def rel_x(w, stop) -> int:
    """w 相对 stop（画布）的横坐标，逐层累加 winfo_x。"""
    x = 0
    node = w
    while node is not None and node is not stop:
        try:
            x += node.winfo_x()
        except Exception:  # noqa: BLE001
            return -99999
        node = getattr(node, "master", None)
    return x


def walk(w, out: list):
    try:
        kids = w.winfo_children()
    except Exception:  # noqa: BLE001
        return
    if not kids:
        out.append(w)
        return
    for ch in kids:
        walk(ch, out)


def main() -> int:
    wanted = sys.argv[1:] or PAGES
    a = A.ToolboxApp()
    pump(a, 1.5)
    try:
        if a.state() == "zoomed":
            a.state("normal")
    except Exception:  # noqa: BLE001
        pass
    pump(a, 0.8)
    print(f"窗口 {a.winfo_width()}x{a.winfo_height()}")
    total = 0
    for key in wanted:
        if key not in a._pages:
            continue
        a._show(key)
        pump(a, 1.5)
        host = a._page_host.get(key)
        if host is None:
            continue
        cv = host.cv
        cw = cv.winfo_width()
        leaves: list = []
        walk(a._pages[key], leaves)
        cut = []
        for lf in leaves:
            try:
                if not lf.winfo_ismapped():
                    continue
                x = rel_x(lf, cv)
                w = lf.winfo_width()
            except Exception:  # noqa: BLE001
                continue
            if x < -1000 or w <= 1:
                continue
            if x + w > cw + 2:
                cut.append((lf.__class__.__name__, x, w, x + w - cw))
        total += len(cut)
        flag = f"切掉 {len(cut)} 个" if cut else "ok"
        print(f"{key:8} 视口宽 {cw:>5}  {flag}")
        for cls, x, w, over in cut[:6]:
            print(f"        {cls:14} x={x:>5} w={w:>4} 超出 {over:>4}")
    print(f"\n合计被切掉的叶子控件：{total}")
    a.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
