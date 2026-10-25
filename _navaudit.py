# -*- coding: utf-8 -*-
"""导航体检：多档窗口高度下，条目文字有没有被裁、滚动条有没有出现。

判据两条：
  · 每条标签的实际宽 >= 请求宽 - 2px（没被 tk 硬切）
  · 内容装不下时滚动条 must be mapped 且宽度 > 6 逻辑像素
"""
from __future__ import annotations

import sys
import time
import traceback

import wbtui as U

RESULTS: list[tuple[bool, str, str]] = []


def chk(ok, name, detail=""):
    RESULTS.append((bool(ok), name, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + detail) if detail else ""))
    return bool(ok)


def pump(app, sec=0.5):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.update()
        time.sleep(0.01)


def main() -> int:
    import app as A
    try:
        app = A.ToolboxApp()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    pump(app, 1.2)

    for w, h in ((1725, 1000), (1200, 900), (950, 760), (900, 620)):
        try:
            app.geometry(f"{w}x{h}")
        except Exception:  # noqa: BLE001
            continue
        pump(app, 0.7)
        nav = app.nav
        cv, sb = nav._canvas, nav._sb
        ih = nav._inner.winfo_reqheight()
        ch = cv.winfo_height()
        need = ih > ch + 1
        print(f"\n---- 窗口 {w}x{h}  内容={ih} 可视={ch} 需要滚动={need}"
              f" 滚动条={sb.winfo_ismapped()}/{sb.winfo_width()}px")
        # 1. 文字不许被裁
        bad = []
        for key, it in nav.items.items():
            lb = it.lbl
            got, want = lb.winfo_width(), lb.winfo_reqwidth()
            if got < want - U.px(2):
                bad.append(f"{key}({got}<{want})")
        chk(not bad, f"{w}x{h} 导航条目文字没被裁", " / ".join(bad[:4]))
        # 2. 需要滚动时滚动条必须真的在、且够宽
        if need:
            chk(sb.winfo_ismapped() and sb.winfo_width() > U.px(6),
                f"{w}x{h} 滚动条可见可点",
                f"w={sb.winfo_width()}")
            # 3. 每个条目都能滚到
            unseen = []
            for key, it in nav.items.items():
                cv.yview_moveto(0.0)
                pump(app, 0.05)
                top = it.winfo_rooty() - cv.winfo_rooty()
                if not (0 <= top and top + it.winfo_height() <= ch + U.px(2)):
                    cv.yview_moveto(1.0)
                    pump(app, 0.05)
                    bot = it.winfo_rooty() - cv.winfo_rooty()
                    if not (0 <= bot and bot + it.winfo_height() <= ch + U.px(2)):
                        unseen.append(key)
            cv.yview_moveto(0.0)
            pump(app, 0.05)
            chk(not unseen, f"{w}x{h} 每个条目都能滚到", " / ".join(unseen[:4]))
        else:
            chk(not sb.winfo_ismapped(), f"{w}x{h} 装得下时滚动条收起")

    app.destroy()
    n = len(RESULTS)
    okn = sum(1 for r in RESULTS if r[0])
    print(f"\n===== {n} 项，通过 {okn}，失败 {n - okn} =====")
    return 0 if okn == n else 1


if __name__ == "__main__":
    sys.exit(main())
