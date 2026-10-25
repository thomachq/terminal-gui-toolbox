# -*- coding: utf-8 -*-
"""KVGrid 列宽自适应自测：不同宽度下都要「值不被裁」。

背景（2026-10-25 用户截图）：智能体接入页在约 860px 宽时仍是 3 列，
令牌那格的值被裁成 `ttb_***********` + 只剩一个「复」字的按钮。

根因是列数判定用了「全表最宽那条 × 列数」的整除估法，漏算了
① 标签列是固定字符宽的 ② 行尾按钮宽度不在值文本里。
"""
import sys
import time
import tkinter as tk

sys.argv = [sys.argv[0] if sys.argv else "kvfit"] + [
    a for a in sys.argv[1:] if a == "--no-probe"]

FAILS = []
N = 0


def chk(cond, name, info=""):
    global N
    N += 1
    if cond:
        print(f"  \u2713 {name}")
    else:
        FAILS.append(name)
        print(f"  \u2717 {name}" + (f"   [{info}]" if info else ""))


def pump(w, s=0.35):
    t0 = time.time()
    while time.time() - t0 < s:
        w.update()
        time.sleep(0.01)


import wbtui as U
from ui.table import KVGrid

# 智能体接入页真实的那 6 条（含带按钮的那条）
ITEMS = [
    ("监听地址", "127.0.0.1:41483", None),
    ("令牌", "ttb_" + "*" * 12, "brand", ("复制", lambda: None)),
    ("保活间隔", "25 秒", None),
    ("连接数", "0", None),
    ("请求数", "0", None),
    ("闲置回收", "不回收", None),
]

root = tk.Tk()
root.withdraw()


def build(width):
    win = tk.Toplevel(root)
    win.geometry(f"{width}x420")
    host = tk.Frame(win)
    host.pack(fill="x")
    g = KVGrid(host, columns=3)
    g.pack(fill="x")
    g.set_items(ITEMS)
    pump(win, 0.45)
    return win, host, g


def overflow(g):
    """返回被裁的格子：值标签的请求宽 > 它所在列的实际可用宽。"""
    bad = []
    for i, (lb, vl) in enumerate(g._cells):
        cell = vl.master
        col = int(cell.grid_info()["column"])
        avail = g.grid_bbox(col, 0)[2] if g.grid_bbox(col, 0) else 0
        need = cell.winfo_reqwidth()
        # grid_bbox 给的是整列宽，减去右侧留白才是值真正能用的
        usable = avail - U.px(24)
        if need > usable + 2:
            bad.append((i, g._items[i][0], need, usable))
    return bad


print("=== 1. 窄（860，用户截图那档）===")
win, host, g = build(860)
chk(g.ncol >= 2, f"860px 至少 2 列（实际 {g.ncol} 列）", g.ncol)
bad = overflow(g)
chk(not bad, "没有值被裁（截图里的病灶）", bad[:2])
chk(all(vl.master.winfo_width() > 5 for _lb, vl in g._cells),
    "每个值格都排出来了（不是 0 宽）")

print("=== 2. 宽（1400，应该回到 3 列）===")
win.destroy()
win, host, g = build(1400)
chk(g.ncol == 3, f"1400px 用满 3 列（实际 {g.ncol} 列）", g.ncol)
bad = overflow(g)
chk(not bad, "宽屏下也不裁", bad[:2])

print("=== 3. 很窄（520，应该降到 1 列）===")
win.destroy()
win, host, g = build(520)
chk(g.ncol == 1, f"520px 降到 1 列（实际 {g.ncol} 列）", g.ncol)

print("=== 4. 从宽到窄：空列不能吃掉宽度 ===")
win.destroy()
win, host, g = build(1400)
chk(g.ncol == 3, "先排成 3 列")
win.geometry("560x420")
pump(win, 0.5)
chk(g.ncol < 3, f"缩窄后真的重排了（{g.ncol} 列）", g.ncol)
# 降列后，最后一个值格子的右边界应该贴着控件右缘（留 padx），
# 不该出现「内容挤左边、右边空一大片」——那说明空列还挂着 weight=1。
lonely = 0
for i in range(g.ncol, g.ncol_max):
    if g.columnconfigure(i * 2 + 1)["weight"]:
        lonely += 1
chk(lonely == 0, "多出来的列 weight 已清零", f"仍有 {lonely} 个")
# 降列后所有格子都得在控件框内（不越界 = 没被挤出去裁掉）
right = max((vl.master.winfo_x() + vl.master.winfo_width())
            for _lb, vl in g._cells)
chk(right <= g.winfo_width(), "内容都在框内，没有越界",
    f"右缘 {right} / 总宽 {g.winfo_width()}")

print("=== 5. 极端：单条超长值 ===")
win.destroy()
win, host, g = build(900)
g.set_items([("路径", "/var/packages/ContainerManager/target/usr/bin/docker", None),
             ("版本", "24.0.2", None)])
pump(win, 0.4)
chk(g.ncol == 2, f"2 条内容不用 3 列（{g.ncol} 列）", g.ncol)
bad = overflow(g)
chk(not bad, "长路径不被裁", bad)

print("=== 6. 没有 items 时不炸 ===")
win.destroy()
win, host, g = build(700)
g.set_items([])
pump(win, 0.3)
chk(g.ncol == 0 or g.ncol >= 1, "空列表不抛异常")
chk(not g._cells, "空列表没有格子")

root.destroy()
print()
if FAILS:
    print(f"失败 {len(FAILS)}/{N}：")
    for f in FAILS:
        print("  -", f)
else:
    print(f"全部通过（{N} 项）")
sys.exit(1 if FAILS else 0)
