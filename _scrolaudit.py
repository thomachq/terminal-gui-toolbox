# -*- coding: utf-8 -*-
"""逐页审计外层滚动：视口 / 内胆 / 内容需求 / 滚动条是否可见。

    python _scrolaudit.py            # 用当前存档尺寸
    python _scrolaudit.py 1725x1080  # 指定物理尺寸

输出每一页一行，字段含义：
  视口      画布实际大小（物理像素）
  内胆      内胆被设定的宽×高
  需求      内胆内容的原始需求宽×高（reqwidth 是**子控件**的最大请求宽）
  竖条      纵向滚动条 winfo_ismapped（代码说显示不算，要看这个）
  横条      横向滚动条同上
  裁切      内容放不下却滚不到（true 就是真 bug）

判断「裁切」的方法：内容需求高 - 内胆高 > 1，且纵向滚动条不可见；
宽同理。
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")

import app as A  # noqa: E402

PAGES = ["hosts", "proxy", "cred", "script", "tools", "rmt", "sys", "files",
         "routes", "sec", "docker", "web", "db", "cron", "vpn", "api"]


def pump(a, s: float) -> None:
    t0 = time.time()
    while time.time() - t0 < s:
        try:
            a.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.01)


def content_req(page) -> tuple[int, int]:
    """页面的内容需求：子控件请求宽/高的最大值（含 padding 判断不了，够用）。"""
    w = h = 0
    for ch in page.winfo_children():
        if not ch.winfo_manager():
            continue
        try:
            w = max(w, ch.winfo_reqwidth())
            h = max(h, ch.winfo_reqheight())
        except Exception:  # noqa: BLE001
            pass
    return w, h


def main() -> int:
    a = A.ToolboxApp()
    pump(a, 1.5)
    try:
        if a.state() == "zoomed":
            a.state("normal")
    except Exception:  # noqa: BLE001
        pass
    if len(sys.argv) > 1 and "x" in sys.argv[1]:
        wh = sys.argv[1]
        try:
            a.geometry(wh)
        except Exception:  # noqa: BLE001
            pass
    pump(a, 0.8)
    print(f"窗口 {a.winfo_width()}x{a.winfo_height()}  日志开出={a._log_dock_on}")
    print(f"{'页面':8} {'视口':>11} {'内胆':>11} {'需求':>11} "
          f"{'竖条':>4} {'横条':>4} {'裁切':>4}")
    bad = 0
    for key in PAGES:
        a._show(key)
        pump(a, 1.4)
        host = a._page_host.get(key)
        page = a._pages.get(key)
        if host is None or page is None:
            print(f"{key:8} （没有这个页）")
            continue
        cv = host.cv
        try:
            iw = int(float(cv.itemcget(host._win, "width") or 0))
            ih = int(float(cv.itemcget(host._win, "height") or 0))
        except Exception:  # noqa: BLE001
            iw = ih = 0
        rw, rh = content_req(page)
        vs = host.vs.winfo_ismapped()
        hs = getattr(host, "hs", None)
        hm = hs.winfo_ismapped() if hs is not None else 0
        vcut = rh - ih > 1 and not vs
        hcut = rw - iw > 1 and not hm
        if vcut or hcut:
            bad += 1
        print(f"{key:8} {cv.winfo_width():>5}x{cv.winfo_height():<5} "
              f"{iw:>5}x{ih:<5} {rw:>5}x{rh:<5} "
              f"{'有' if vs else '—':>4} {'有' if hm else '—':>4} "
              f"{('竖' if vcut else '') + ('横' if hcut else '') or '—':>4}")
    print(f"\n有裁切的页：{bad}")
    a.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
