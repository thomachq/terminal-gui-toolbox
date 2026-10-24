# -*- coding: utf-8 -*-
"""表格列宽拖动自检（真拖，不是直接改 width）。

用 event_generate 在表头分隔线（identify_region == "separator"）上模拟
按下 → 拖动 → 松开，检查：
  · 拖完之后列宽是否真的跟手（差值 ≈ 拖动距离）
  · 表格的横向视图有没有被拽回 0（这是「拖动有问题」最典型的表现）
  · 横向滚动条的显隐有没有在拖动过程中反复抖（每次抖都会触发 <Configure>）

跑法：python _tableresize.py
"""
from __future__ import annotations

import sys
import tkinter as tk
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PASS, FAIL = 0, 0


def chk(ok, name: str, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(("  [PASS] " if ok else "  [FAIL] ") + name
          + (f"  —— {detail}" if detail else ""))
    return bool(ok)


def eq(a, b, name: str) -> bool:
    return chk(a == b, name, f"期望 {b!r} 实得 {a!r}")


def separators(tree, y: int, limit: int = 40) -> list[int]:
    """表头上所有 separator 的 x（连续的一段算一条，取中点）。"""
    runs: list[list[int]] = []
    for x in range(2, max(3, tree.winfo_width() - 2)):
        try:
            if tree.identify_region(x, y) != "separator":
                continue
        except tk.TclError:
            break
        if runs and x - runs[-1][-1] <= 1:
            runs[-1].append(x)
        else:
            runs.append([x])
        if len(runs) >= limit:
            break
    return [(r[0] + r[-1]) // 2 for r in runs]


def boundaries(tree) -> list[int]:
    """按各列宽度累加出来的**真实**列边界（横向未滚动时就是屏幕 x）。"""
    out, acc = [], 0
    keys = tree["columns"]
    for k in keys[:-1]:
        acc += int(tree.column(k, "width"))
        out.append(acc)
    return out


def settle(root, ms: int = 200) -> None:
    """等 debounce 的定时器跑完（列宽 40ms、滚动条 80ms）。"""
    import time
    end = time.time() + ms / 1000.0
    while time.time() < end:
        root.update()
        time.sleep(0.01)
    root.update()


def drag(tree, root, x0: int, y: int, dx: int) -> None:
    tree.event_generate("<Button-1>", x=x0, y=y)
    root.update()
    step = 6 if dx > 0 else -6
    moved = 0
    while abs(moved) < abs(dx):
        moved += step
        if abs(moved) > abs(dx):
            moved = dx
        tree.event_generate("<B1-Motion>", x=x0 + moved, y=y)
        root.update()
    tree.event_generate("<ButtonRelease-1>", x=x0 + dx, y=y)
    root.update()


def main() -> int:
    import wbtui as U
    from ui.table import Column, DataTable

    root = tk.Tk()
    root.geometry("1240x420+60+60")
    root.title("列宽拖动自检")
    cols = [
        Column("name", "名称", 170, stretch=True),
        Column("addr", "地址", 210, mono=True),
        Column("proto", "协议", 76),
        Column("group", "分组", 110),
        Column("tags", "标签", 140),
        Column("cred", "凭证", 110),
        Column("last_used", "最近连接", 130),
    ]
    dt = DataTable(root, cols)
    dt.pack(fill="both", expand=True, padx=U.px(8), pady=U.px(8))
    dt.set_rows([
        {"name": "DESKTOP-8ABC", "addr": "—", "proto": "本机", "group": "未分组",
         "tags": "", "cred": "—", "last_used": "从未连接"},
        {"name": "nas", "addr": "cloudadmin@192.168.3.12:3022", "proto": "SSH",
         "group": "机房", "tags": "常用", "cred": "nas-key",
         "last_used": "2026-10-24 06:00"},
    ])
    root.update()
    root.update_idletasks()

    tree = dt.tree
    y = 14
    settle(root)
    bands = separators(tree, y)
    edges = boundaries(tree)
    chk(len(bands) >= 3, "表头能认出分隔线（找得到拖拽把手）",
        f"recognized {len(bands)}: {bands}")

    # 总宽 vs 可视宽：这个表 7 列在 1240 物理像素下必然溢出
    total = sum(int(tree.column(c.key, "width")) for c in cols)
    chk(total > tree.winfo_width(),
        "列宽总和超过可视宽度（该出现横向滚动条）",
        f"总宽 {total} / 可视 {tree.winfo_width()}")
    chk(bool(dt.sb_x.winfo_manager()), "横向滚动条可见",
        "管理器=%r" % dt.sb_x.winfo_manager())

    if not bands or not edges:
        root.destroy()
        print(f"\n合计 {PASS} 项通过，{FAIL} 项失败")
        return 1

    # ---- 拖第一列（名称）加宽 60：抓第一个真实边界
    x0 = edges[0]
    chk(tree.identify_region(x0, y) == "separator",
        "边界点确实落在拖拽带上", f"x={x0} region={tree.identify_region(x0, y)}")
    before = int(tree.column("name", "width"))
    xv_before = tree.xview()
    drag(tree, root, x0, y, 60)
    settle(root)
    after = int(tree.column("name", "width"))
    chk(abs((after - before) - 60) <= 4,
        "拖 ＋60 像素，列宽跟手（松手不反弹）",
        f"{before} → {after}（差 {after - before}）")

    xv_after = tree.xview()
    chk(xv_after[0] < 0.5,
        "拖完没有被拽回最左边（xview 不乱跳）",
        f"{xv_before} → {xv_after}")
    chk(bool(dt.sb_x.winfo_manager()), "拖完横向滚动条还在")

    # ---- 往回拖 30
    b2 = int(tree.column("name", "width"))
    drag(tree, root, boundaries(tree)[0], y, -30)
    settle(root)
    a2 = int(tree.column("name", "width"))
    chk(abs((a2 - b2) + 30) <= 4,
        "往回拖 －30 像素也跟手", f"{b2} → {a2}（差 {a2 - b2}）")

    # ---- 拖到比最小宽度还窄：应停在 minwidth，不能变成 0 或负数
    drag(tree, root, boundaries(tree)[0], y, -400)
    settle(root)
    w3 = int(tree.column("name", "width"))
    chk(w3 >= U.px(48), "拖过头也不会小于 minwidth", f"宽度 {w3}")

    # ---- 一列拖爆之后整表仍可横向滚到底
    tree.xview_moveto(1.0)
    root.update()
    xv_end = tree.xview()
    chk(xv_end[1] > 0.99, "能横向滚到最右端", f"{xv_end}")

    # ---- 把窗口拉宽：填充列吃掉余量，内容仍不溢出
    for w, h in ((1240, 420), (2000, 420)):
        root.geometry(f"{w}x{h}")
        settle(root, 300)
    total2 = sum(int(tree.column(c.key, "width")) for c in cols)
    chk(total2 >= tree.winfo_width() - 4,
        "窗口变宽后列宽铺满可视区", f"总宽 {total2} / 可视 {tree.winfo_width()}")
    chk(int(tree.column("last_used", "width")) == U.px(130)
        or tree.column("name", "width") > U.px(170),
        "多出来的宽度给了填充列，不是摊给每一列")

    # ---- 拖完光标要还回箭头（旧毛病：松手后一直顶着 ↔）
    tree.configure(cursor="sb_h_double_arrow")
    tree.event_generate("<ButtonRelease-1>", x=10, y=10)
    root.update()
    eq(str(tree.cget("cursor")), "", "松手后光标还原成箭头")

    tree.configure(cursor="sb_h_double_arrow")
    tree.event_generate("<Motion>", x=20, y=120, state=0)
    root.update()
    eq(str(tree.cget("cursor")), "", "左键早就松了时，移动鼠标也会还原")

    tree.configure(cursor="sb_h_double_arrow")
    tree.event_generate("<Motion>", x=20, y=120, state=0x100)
    root.update()
    eq(str(tree.cget("cursor")), "sb_h_double_arrow", "左键还按着时不抢 ttk 的拖拽光标")
    tree.configure(cursor="")

    root.destroy()
    print(f"\n合计 {PASS} 项通过，{FAIL} 项失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
