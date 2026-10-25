# -*- coding: utf-8 -*-
"""页面级滚动体检：每页在多档窗口下都要能滚到最底部，且内容不被横向撑破。

用户 2026-10-25 报的两个症状：
  1. 「很多界面都缺少外层滚动条，导致界面无法显示完全」 —— 安全中心页底部
     （防火墙的「撤销上一步」那一排）根本滚不到。
  2. 同一页右边被裁 —— 根因不是缺横向滚动条，而是 16 个页面 grid 在同一格，
     grid 取「所有子页请求宽度的最大值」当列宽，一张宽表（两张表列宽合计
     1311）就把整个 stack 撑到 1668，每页都跟着变宽然后被外框裁掉。
"""
from __future__ import annotations

import sys
import time
import traceback

import wbtui as U

PAGES = ["hosts", "proxy", "cred", "script", "tools", "sys", "files", "routes",
         "sec", "docker", "web", "db", "cron", "vpn", "api"]

# 主体是表格的页面：表格自己有内滚动，外层只是压缩，不该出滚动条
TABLE_PAGES = {"hosts", "tools", "sys", "files", "routes"}

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


def main() -> int:
    import app as A
    try:
        app = A.ToolboxApp()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    pump(app, 1.3)

    for w, h in ((1725, 1000), (1200, 820), (950, 760)):
        try:
            if app.state() == "zoomed":
                app.state("normal")
            app.geometry(f"{w}x{h}")
        except Exception:  # noqa: BLE001
            continue
        pump(app, 0.9)
        print(f"\n---- 窗口 {app.winfo_width()}x{app.winfo_height()}")
        for key in PAGES:
            app._show(key)
            pump(app, 0.55)
            host = app._page_host.get(key)
            body = app._pages.get(key)
            if host is None or body is None:
                chk(False, f"{key} 有页面宿主")
                continue
            view_h = host.cv.winfo_height()
            view_w = host.cv.winfo_width()
            need = body.winfo_reqheight()
            sb = host.vs

            # 1. 内容不许被横向撑破（撑破就是右边那一截被裁）
            chk(body.winfo_width() <= view_w + U.px(2),
                f"{key} 页面宽度没被内容撑破",
                f"内胆 {body.winfo_width()} / 可视 {view_w}")

            # 2. 需要滚的必须真有滚动条；表格页不许双重滚动
            if key in TABLE_PAGES:
                chk(not sb.winfo_ismapped(),
                    f"{key} 表格页不叠外层滚动条（避免双重滚动）")
                continue
            if need <= view_h + 1:
                chk(not sb.winfo_ismapped(), f"{key} 装得下时不出滚动条")
                continue
            chk(sb.winfo_ismapped() and sb.winfo_width() > U.px(6),
                f"{key} 内容超高时滚动条可见可点", f"w={sb.winfo_width()}")

            # 3. 滚到底必须真到底（不是「滚了但还有一半看不到」）
            host.cv.yview_moveto(1.0)
            pump(app, 0.25)
            first, last = host.cv.yview()
            chk(abs(last - 1.0) < 0.02, f"{key} 能滚到最底部",
                f"yview={first:.2f}~{last:.2f}")
            kids = [c for c in body.winfo_children() if c.winfo_ismapped()]
            if kids:
                low = max(k.winfo_rooty() + k.winfo_height() for k in kids)
                chk(low <= host.cv.winfo_rooty() + view_h + U.px(2),
                    f"{key} 滚到底后最后一块内容在视口内",
                    f"底边 {low - host.cv.winfo_rooty()} / 视口 {view_h}")
            host.cv.yview_moveto(0.0)
            pump(app, 0.1)

    app.destroy()
    print(f"\n===== {N} 项，通过 {N - len(FAILS)}，失败 {len(FAILS)} =====")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
