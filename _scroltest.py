# -*- coding: utf-8 -*-
"""外层滚动条闸门：每页 × 三档窗口，验四件事。

  1. 内容放得下 → 外层竖条必须**收起**（不许常驻一条摆设）
  2. 内容放不下 → 外层竖条必须出现，而且滚到底真的看得到最后那块内容
  3. 一个都滚不到的内容 = 真裁切（内胆被压到视口高，叶子控件却在视口外）
  4. 滚轮真的能滚（含 Shift+滚轮走横向，只在挂了横条的页上）

判据一律用 `winfo_ismapped()` 与几何坐标，不看代码里自己记的标记 ——
v1.0.22 的教训：`_sb_visible=True` 不代表屏幕上看得见。

    python _scroltest.py
"""
from __future__ import annotations

import sys
import time
import types

sys.path.insert(0, ".")

import app as A  # noqa: E402

PAGES = ["hosts", "proxy", "cred", "script", "tools", "sys", "files", "routes",
         "sec", "docker", "web", "db", "cron", "vpn", "api"]
SIZES = [(1725, 1080), (1366, 900), (1200, 760)]

ok_n = 0
fail_n = 0
FAILS: list[str] = []


def chk(cond, name, detail="") -> None:
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print(f"  \033[32mPASS\033[0m {name}" + (f"  · {detail}" if detail else ""))
    else:
        fail_n += 1
        FAILS.append(name)
        print(f"  \033[31mFAIL\033[0m {name}" + (f"  · {detail}" if detail else ""))


def pump(a, s: float) -> None:
    t0 = time.time()
    while time.time() - t0 < s:
        try:
            a.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.01)


def rel_pos(w, stop) -> tuple[int, int, int, int]:
    """控件相对画布的 (x, y, w, h)。"""
    x = y = 0
    n = w
    while n is not None and n is not stop:
        try:
            x += n.winfo_x()
            y += n.winfo_y()
        except Exception:  # noqa: BLE001
            return -99999, -99999, 0, 0
        n = getattr(n, "master", None)
    return x, y, w.winfo_width(), w.winfo_height()


def scroll_owner(w, stop):
    """沿 master 链找「最近的那个滚动内胆」。找到 stop 为止。"""
    n = w
    while n is not None and n is not stop:
        if getattr(n, "_is_scroll_inner", False):
            return n
        n = getattr(n, "master", None)
    return stop if n is stop else None


def leaves(w, out: list) -> None:
    kids = w.winfo_children()
    if not kids:
        out.append(w)
        return
    for ch in kids:
        leaves(ch, out)


def inner_h(host) -> int:
    try:
        return int(float(host.cv.itemcget(host._win, "height") or 0))
    except Exception:  # noqa: BLE001
        return 0


def main() -> int:
    a = A.ToolboxApp()
    pump(a, 1.5)
    try:
        if a.state() == "zoomed":
            a.state("normal")
    except Exception:  # noqa: BLE001
        pass
    pump(a, 0.6)

    # 主程序的 minsize 是 1150x720 逻辑像素（物理 1725x1080），
    # 不先放开的话 geometry() 会被 WM 直接忽略 —— 三档窗口量出来一模一样，
    # 看着跑了三遍其实只跑了一遍。
    try:
        a.minsize(200, 200)
    except Exception:  # noqa: BLE001
        pass
    for (w, h) in SIZES:
        try:
            a.geometry(f"{w}x{h}")
        except Exception:  # noqa: BLE001
            pass
        pump(a, 0.9)
        print(f"\n===== 窗口 {a.winfo_width()}x{a.winfo_height()} =====")
        for key in PAGES:
            if key not in a._pages:
                continue
            a._show(key)
            pump(a, 1.3)
            host = a._page_host[key]
            cv, ih = host.cv, inner_h(host)
            vh = cv.winfo_height()
            fits = ih <= vh + 1
            shown = bool(host.vs.winfo_ismapped())
            if fits:
                chk(not shown, f"{key} 放得下时竖条收起",
                    f"内胆 {ih} / 视口 {vh}")
            else:
                chk(shown and host.vs.winfo_width() > U_MIN_SB,
                    f"{key} 放不下时竖条出现",
                    f"内胆 {ih} / 视口 {vh} / 条宽 {host.vs.winfo_width()}")

            # 滚到底：最后一块内容必须进得来
            if not fits:
                try:
                    cv.yview_moveto(1.0)
                except Exception:  # noqa: BLE001
                    pass
                pump(a, 0.25)
                bottom = 0
                lv: list = []
                leaves(a._pages[key], lv)
                for lf in lv:
                    try:
                        if not lf.winfo_ismapped():
                            continue
                        _x, y, _w, lh = rel_pos(lf, cv)
                    except Exception:  # noqa: BLE001
                        continue
                    if y < -9999 or lh <= 1:
                        continue
                    bottom = max(bottom, y + lh)
                chk(bottom <= vh + 3, f"{key} 滚到底能看到最后的内容",
                    f"最低叶子底 {bottom} / 视口 {vh}")
                try:
                    cv.yview_moveto(0.0)
                except Exception:  # noqa: BLE001
                    pass
                pump(a, 0.15)

            # 有没有「怎么滚都看不到」的叶子：内胆已经等于视口高时，
            # 视口外的内容就是死的（没有滚动条能带你去）。
            lv = []
            leaves(a._pages[key], lv)
            dead = 0
            for lf in lv:
                try:
                    if not lf.winfo_ismapped():
                        continue
                    # 嵌在别的滚动容器里（控制台子页那种）不归本页管：
                    # 那截内容由内层自己的滚动条负责。
                    if scroll_owner(lf, host.inner) is not host.inner:
                        continue
                    _x, y, _w, lh = rel_pos(lf, cv)
                except Exception:  # noqa: BLE001
                    continue
                if y < -9999 or lh <= 1:
                    continue
                if y + lh > vh + 3 and not shown:
                    dead += 1
            chk(dead == 0, f"{key} 没有被永久切掉的内容", f"{dead} 个叶子在视口外")
    # ---- 滚轮（只测当前页）
    print("\n===== 滚轮 =====")
    a._show("api")
    pump(a, 1.2)
    host = a._page_host["api"]
    before = host.cv.yview()[0]
    host._wheel(types.SimpleNamespace(widget=host.inner, delta=-120, num=None,
                                      state=0))
    pump(a, 0.2)
    chk(host.cv.yview()[0] > before, "滚轮能往下滚",
        f"{before:.2f} → {host.cv.yview()[0]:.2f}")

    # 横条：只有开了 hscroll 的页（隧道）才有，Shift+滚轮要能动
    a._show("vpn")
    pump(a, 1.2)
    vh_host = a._page_host["vpn"]
    if vh_host.hs.winfo_ismapped():
        x0 = vh_host.cv.xview()[0]
        vh_host._wheel(types.SimpleNamespace(widget=vh_host.inner, delta=-120,
                                             num=None, state=0x0001))
        pump(a, 0.2)
        chk(vh_host.cv.xview()[0] > x0, "Shift+滚轮走横向",
            f"{x0:.2f} → {vh_host.cv.xview()[0]:.2f}")
    else:
        chk(True, "隧道页当前不需要横条（内容没超宽）")

    print(f"\n===== {ok_n + fail_n} 项，通过 {ok_n}，失败 {fail_n} =====")
    if FAILS:
        for f in FAILS[:12]:
            print(f"  · {f}")
    a.destroy()
    return 0 if fail_n == 0 else 1


U_MIN_SB = 6        # 滚动条窄于这个宽度就是「看不见的摆设」

if __name__ == "__main__":
    raise SystemExit(main())
