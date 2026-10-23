# -*- coding: utf-8 -*-
"""窗口几何 + 导航滚动条自测。

三件事各自单独验，互不依赖：

  A. 纯函数（不需要 Tk）：工作区读取、尺寸压缩、位置夹取、DPI 换算
  B. 导航滚动条（只建一个小 Tk 窗口）：装不下时出现、装得下时收回、滚轮能动
  C. 真应用：按工作区开的窗口不会超屏、缩矮后滚动条出来、退出前落盘、
     下次按记录恢复（缩到 tmp 的 data 目录里，不碰用户真实配置）

跑法：
    python _winstest.py
    python _winstest.py --dark      # 深色主题下再跑一遍控件部分
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

FAILS: list[str] = []
N = 0


def chk(cond, name: str, info="") -> bool:
    global N
    N += 1
    if cond:
        print(f"  \u2713 {name}")
        return True
    FAILS.append(name)
    print(f"  \u2717 {name}" + (f"   [{info}]" if info else ""))
    return False


def pump(win, sec: float = 0.25) -> None:
    t0 = time.time()
    while time.time() - t0 < sec:
        try:
            win.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.008)


# ---------------------------------------------------------------- A. 纯函数
def test_pure() -> None:
    print("\n[A] 窗口几何纯函数")
    from core import winstate

    area = winstate.work_area((0, 0, 1366, 728))
    chk(area[2] >= 320 and area[3] >= 240, "能读到工作区", area)
    chk(area[3] <= 4000, "工作区高度是合理值（不是 0 也不是天文数字）", area)

    # 1) 首次启动：居中、放得下
    box = winstate.restore_box({}, (0, 0, 1600, 860), 1.25,
                               1700, 1100, 1125, 700)
    chk(box.w <= 1600 - 16 and box.h <= 860 - 16,
        "默认尺寸被压进工作区", f"{box.w}x{box.h}")
    chk(box.x >= 0 and box.x + box.w <= 1600, "水平居中且不出界", box.x)
    chk(box.y >= 0 and box.y + box.h <= 860, "垂直居中且不出界", box.y)
    chk(abs((box.x + box.w // 2) - 800) <= 2, "水平基本居中", box.x)
    chk(not box.zoomed, "首次启动不最大化")

    # 2) 默认尺寸本来就小于工作区：不许被撑大
    small = winstate.restore_box({}, (0, 0, 2560, 1400), 1.0,
                                 1360, 880, 1150, 720)
    chk(small.w == 1360 and small.h == 880, "屏幕够大时用默认尺寸",
        f"{small.w}x{small.h}")

    # 3) 上次停在副屏，这次副屏没了 → 必须夹回主屏
    far = winstate.restore_box({"x": 3200, "y": 200, "w": 1200, "h": 800},
                               (0, 0, 1600, 860), 1.0, 1360, 880, 900, 560)
    chk(far.x + far.w <= 1600 and far.y + far.h <= 860,
        "跑出屏幕的记录被夹回来", f"{far.x},{far.y} {far.w}x{far.h}")
    neg = winstate.restore_box({"x": -900, "y": -500, "w": 1200, "h": 800},
                               (0, 0, 1600, 860), 1.0, 1360, 880, 900, 560)
    chk(neg.x >= 0 and neg.y >= 0, "负坐标也被夹回来", f"{neg.x},{neg.y}")

    # 4) 记录比工作区还大 → 压缩
    big = winstate.restore_box({"x": 0, "y": 0, "w": 3000, "h": 2000},
                               (0, 0, 1600, 860), 1.0, 1360, 880, 900, 560)
    chk(big.w <= 1600 and big.h <= 860, "过大的记录被压缩",
        f"{big.w}x{big.h}")

    # 5) 最大化：恢复时用普通状态那份矩形，别拿最大化时的 (8,8)
    z = winstate.restore_box({"x": -8, "y": -8, "nx": 300, "ny": 180,
                              "nw": 1200, "nh": 760, "zoomed": True},
                             (0, 0, 1600, 1000), 1.0, 1360, 880, 900, 560)
    chk(z.zoomed, "记住最大化状态")
    chk((z.x, z.y) == (300, 180), "还原普通状态的位置", f"{z.x},{z.y}")
    chk((z.w, z.h) == (1200, 760), "还原普通状态的尺寸", f"{z.w}x{z.h}")

    # 6) DPI 变了：150% → 125%，尺寸不能还是按老缩放的物理像素
    dpi = winstate.restore_box({"x": 100, "y": 100, "w": 1500, "h": 900,
                                "scale": 1.5},
                               (0, 0, 2560, 1400), 1.25, 1360, 880, 900, 560)
    chk(abs(dpi.w - 1250) <= 2, "换缩放后按比例换算宽度", dpi.w)
    chk(abs(dpi.h - 750) <= 2, "换缩放后按比例换算高度", dpi.h)

    # 7) 损坏的存盘不炸
    bad = winstate.restore_box({"x": "??", "y": None, "w": -5, "h": "x",
                                "zoomed": "yes", "scale": "abc"},
                               (0, 0, 1500, 800), 1.0, 1360, 880, 900, 560)
    chk(bad.w > 0 and bad.h > 0, "脏数据回落成默认尺寸",
        f"{bad.w}x{bad.h}")

    # 8) minsize 比屏幕还大时，屏幕优先
    tiny = winstate.restore_box({}, (0, 0, 1000, 620), 1.0,
                                1360, 880, 1150, 720)
    chk(tiny.w <= 1000 and tiny.h <= 620, "小屏上不讲 minsize 只讲放得下",
        f"{tiny.w}x{tiny.h}")

    # 9) WinState 存取往返
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "window.json"
        st = winstate.WinState(p)
        chk(st.data == {}, "文件不存在时是空状态")
        st.save({"x": 10, "y": 20, "w": 900, "h": 700, "zoomed": False,
                 "scale": 1.25})
        st2 = winstate.WinState(p)
        chk(st2.data.get("w") == 900 and st2.data.get("x") == 10,
            "存进去能读回来", st2.data)
        chk("saved_at" in st2.data, "带写入时间戳")
        p.write_text("{ 这不是 json", encoding="utf-8")
        st3 = winstate.WinState(p)
        chk(st3.data == {}, "坏文件当没有处理，不抛异常")


# ---------------------------------------------------------------- B. 滚动条
def test_nav_scroll() -> None:
    print("\n[B] 导航滚动条")
    import tkinter as tk

    import wbtui as U
    if "--dark" in sys.argv:
        U.T = U.DARK

    root = tk.Tk()
    root.geometry("900x240")
    try:
        items: list = [("group", "资产", [])]
        items[0][2].extend([(f"p{i}", f"页面 {i}") for i in range(6)])
        items.append(("group", "运维", [(f"q{i}", f"工具 {i}") for i in range(12)]))
        holder = U.wframe(root, "page")
        holder.pack(fill="both", expand=True)
        nav = U.Nav(holder, items, lambda k: None)
        nav.pack(side="left", fill="y")
        pump(root, 0.35)

        chk(len(nav.items) == 18, "条目都建出来了", len(nav.items))
        chk(nav._sb_visible, "装不下时滚动条出现")
        chk(nav._inner.winfo_reqheight() > nav._canvas.winfo_height(),
            "内容确实比可视区高",
            f"{nav._inner.winfo_reqheight()} vs {nav._canvas.winfo_height()}")

        # 滚到底：最后一个条目要能看见
        nav._canvas.yview_moveto(1.0)
        pump(root, 0.15)
        first, last = nav._canvas.yview()
        chk(last > 0.98, "能滚到底", f"{first:.2f}~{last:.2f}")

        # 滚轮：往上滚一格，视口要动
        before = nav._canvas.yview()[0]
        class _E:
            num = 0
            delta = 120
        nav._wheel(_E())
        pump(root, 0.1)
        chk(nav._canvas.yview()[0] < before, "滚轮能往上滚",
            f"{before:.3f} → {nav._canvas.yview()[0]:.3f}")

        # 放大到真能装下（按内容实际高度算，别猜一个数字 —— 缩放 150% 时
        # 900 物理像素根本装不下 18 个条目）
        need_h = nav._inner.winfo_reqheight() + 40
        root.geometry(f"900x{need_h}")
        pump(root, 0.4)
        chk(not nav._sb_visible, "装得下时滚动条自动收起")
        chk(nav._canvas.yview()[0] < 0.02, "收起时视口回到顶部",
            nav._canvas.yview())

        # 再拉矮 → 又出来
        root.geometry("900x200")
        pump(root, 0.35)
        chk(nav._sb_visible, "再拉矮又出滚动条")

        # 选中逻辑没被滚动改造破坏
        nav.select("q3")
        pump(root, 0.1)
        chk(nav.current == "q3" and nav.items["q3"].active, "点选仍然生效")
        nav.select("不存在的页面")
        chk(nav.current == "q3", "不存在的键静默忽略")
    finally:
        root.destroy()


# ---------------------------------------------------------------- C. 真应用
def test_app() -> None:
    print("\n[C] 真应用：自适应 + 记住状态")
    import tkinter as tk

    import wbtui as U
    if "--dark" in sys.argv:
        U.T = U.DARK

    from core import winstate

    argv = [a for a in sys.argv if a not in ("--smoke",)]
    sys.argv = argv + ["--no-probe"]

    import app as APP

    tmp = tempfile.mkdtemp(prefix="wbt_win_")
    real_data = APP.DATA_DIR
    APP.DATA_DIR = Path(tmp)          # 别把测试窗口的尺寸写进用户真实配置
    a = None
    try:
        a = APP.ToolboxApp()
        pump(a, 0.8)

        area = winstate.work_area((0, 0, a.winfo_screenwidth(),
                                   a.winfo_screenheight()))
        chk(a.winfo_height() <= area[3], "窗口高度不超工作区",
            f"{a.winfo_height()} vs {area[3]}")
        chk(a.winfo_width() <= area[2], "窗口宽度不超工作区",
            f"{a.winfo_width()} vs {area[2]}")
        chk(a.winfo_rooty() >= area[1] - 2, "窗口顶部没跑到屏幕外",
            a.winfo_rooty())
        chk(a.winfo_rooty() + a.winfo_height() <= area[1] + area[3] + 2,
            "窗口底部没被任务栏盖住",
            f"{a.winfo_rooty() + a.winfo_height()} vs {area[1] + area[3]}")

        # 缩矮到导航装不下
        a.geometry(f"{U.px(1000)}x{U.px(420)}")
        pump(a, 0.5)
        chk(a.nav._sb_visible, "应用缩矮后导航出滚动条",
            f"{a.nav._inner.winfo_reqheight()} vs {a.nav._canvas.winfo_height()}")
        a.nav._canvas.yview_moveto(1.0)
        pump(a, 0.15)
        chk(a.nav._canvas.yview()[1] > 0.98, "能滚到底看到最后一项")

        # 退出前落盘
        a._win_state_save()
        p = Path(tmp) / "window.json"
        chk(p.is_file(), "窗口状态已落盘", p)
        data = json.loads(p.read_text(encoding="utf-8"))
        chk(data.get("w", 0) > 0 and data.get("h", 0) > 0, "记录了尺寸", data)
        chk(data.get("zoomed") is False, "记录了「非最大化」", data.get("zoomed"))
        chk(abs(float(data.get("scale") or 0) - float(U.S)) < 1e-6,
            "记录了当时的 DPI 缩放", data.get("scale"))

        # 用落盘的数据模拟「下次启动」
        box = winstate.restore_box(data, area, float(U.S),
                                   U.px(1360), U.px(880),
                                   U.px(900), U.px(560))
        chk(abs(box.w - data["w"]) <= 2 and abs(box.h - data["h"]) <= 2,
            "下次启动恢复到同样大小", f"{box.w}x{box.h} vs {box.w}x{box.h}")

        # 最大化状态也要记住（用假数据验恢复路径，不动真实窗口）
        a._win_normal = (120, 90, 1200, 760)
        zoom = winstate.restore_box(
            {"x": -8, "y": -8, "nx": 120, "ny": 90, "nw": 1200, "nh": 760,
             "zoomed": True, "scale": float(U.S)},
            area, float(U.S), U.px(1360), U.px(880), U.px(900), U.px(560))
        chk(zoom.zoomed and (zoom.x, zoom.y) == (120, 90),
            "最大化状态能原样恢复（位置取普通状态那份）",
            f"{zoom.x},{zoom.y} zoomed={zoom.zoomed}")
    finally:
        try:
            if a is not None:
                a.destroy()
        except Exception:  # noqa: BLE001
            pass
        APP.DATA_DIR = real_data


def main() -> int:
    print("=" * 60)
    print("窗口几何 / 导航滚动条自测")
    print("=" * 60)
    test_pure()
    test_nav_scroll()
    test_app()
    print("\n" + "=" * 60)
    if FAILS:
        print(f"失败 {len(FAILS)} / {N}")
        for f in FAILS:
            print("  ✗", f)
        return 1
    print(f"全部通过：{N} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
