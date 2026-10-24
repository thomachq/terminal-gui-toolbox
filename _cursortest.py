# -*- coding: utf-8 -*-
"""光标守卫：表格上只有列分隔线该显示 ↔，别处一律普通箭头。

用户两轮反馈：
  v1.0.12 后：「鼠标的问题你得解决下」
  v1.0.15 后：「鼠标移动到这里任意区域，还是会变成双箭头，能不能解决」

v1.0.12 用的是 after_idle：idle 要等下一轮事件循环，而鼠标每秒移动几十次，
每次 ttk 先设 ↔、idle 才清，中间的每一帧用户都看得见 —— 看起来就是「移到
哪都是双箭头」。根因是 bindtags 顺序：widget → class → toplevel → all，
ttk::treeview 的 <Motion> 是类绑定，挂在控件上的回调永远跑在它前面。

现在往 bindtags 里插一个 tag（排在 Treeview 之后），同一个事件里先后执行，
屏幕只渲染最终值。

分段：
  1. bindtags 顺序正确（守卫确实在 Treeview 之后）
  2. 各区域实测：表头 / 数据行 / 下方空白 -> arrow；列分隔线 -> ↔
  3. 左键按着时不抢（拖列宽期间 ↔ 该留着）
  4. 离开表格 -> arrow
  5. cursor 显式给 arrow，不留给 ttk 猜
"""
import os
import sys
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import wbtui as U  # noqa: E402
from ui.table import CUR_TAG, Column, DataTable  # noqa: E402

PASS = FAIL = 0
FAILS: list[str] = []


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")
    print(("  ok   " if ok else "  FAIL ") + name
          + (f"  [{extra}]" if extra and not ok else ""))


def sec(t):
    print(f"\n-- {t}")


ARROW = "arrow"
HZ = "sb_h_double_arrow"

root = tk.Tk()
root.geometry("1000x520")
root.configure(bg=U.T.page)

cols = [Column("a", "状态", 90), Column("b", "名称", 170),
        Column("c", "地址", 280), Column("d", "系统", 120)]
tbl = DataTable(root, cols, height=6)
tbl.pack(fill="both", expand=True, padx=10, pady=10)
tbl.set_rows([{"a": "● 可达", "b": "192.168.3.12",
               "c": "cloudadmin@192.168.3.12:30", "d": "Linux"}] * 3)
root.update()
tr = tbl.tree


def cur():
    c = tr.cget("cursor")
    return str(c) if c else "<空>"


def region(x, y):
    return str(tr.identify_region(x, y))


def move(x, y, state=0):
    tr.event_generate("<Motion>", x=x, y=y, state=state)
    root.update()
    return cur()


# ---------------------------------------------------------------- 1
sec("1. bindtags 顺序")
tags = list(tr.bindtags())
chk(CUR_TAG in tags, f"守卫 tag 已装上：{CUR_TAG}")
i_guard = tags.index(CUR_TAG) if CUR_TAG in tags else -1
i_cls = tags.index("Treeview") if "Treeview" in tags else -1
chk(i_guard == i_cls + 1,
    "守卫排在 Treeview 类绑定之后（否则会被 ttk 覆盖）",
    f"guard@{i_guard} class@{i_cls} tags={tags}")
chk(str(tr.cget("cursor")) == ARROW, "初始化就把 cursor 定成 arrow，不留给 ttk 猜",
    cur())


# ---------------------------------------------------------------- 2
sec("2. 各区域实测")
W, H = tr.winfo_width(), tr.winfo_height()

# 找一条真正的列分隔线
sepx = None
for x in range(20, W, 2):
    if region(x, 30) == "separator":
        sepx = x
        break

pts = [
    ("表头中间", W // 2, 12, ARROW),
    ("数据行第一行", W // 3, 60, ARROW),
    ("数据行文字上", 120, 60, ARROW),
    ("数据行空白列", W - 40, 60, ARROW),
    ("表格下方大片空白", W // 2, H - 40, ARROW),
    ("最左下角", 6, H - 6, ARROW),
]
for name, x, y, want in pts:
    got = move(x, y)
    r = region(x, y)
    chk(got == want, f"{name} -> {want}", f"得到 {got}，region={r}")

if sepx:
    got = move(sepx, 30)
    chk(got == HZ, "列分隔线 -> ↔（该有的提示）", f"得到 {got}，x={sepx}")
    # 关键回归：从分隔线移开后必须立刻变回箭头
    got = move(W // 2, 60)
    chk(got == ARROW, "从分隔线移开 -> 立刻变回箭头（同一事件内）", got)
    got = move(sepx, 30)
    got = move(W // 2, H - 40)
    chk(got == ARROW, "分隔线 -> 大片空白 也立刻回箭头", got)
else:
    chk(False, "找到列分隔线用于测试", "没找到")


# ---------------------------------------------------------------- 3
sec("3. 左键按着时不抢")
if sepx:
    tr.event_generate("<Motion>", x=sepx, y=30)
    root.update()
    before = cur()
    got = move(W // 2, 60, state=0x100)     # Button1 按着，指针移到数据区
    chk(got == HZ, "拖动中指针移到数据区：↔ 保持（不干扰拖拽）",
        f"拖前 {before}，拖中 {got}")
    got = move(W // 2, 60, state=0)          # 松手
    chk(got == ARROW, "松手后回到箭头", got)
else:
    chk(False, "找到列分隔线用于测试", "没找到")


# ---------------------------------------------------------------- 4
sec("4. 离开表格")
tr.event_generate("<Leave>")
root.update()
chk(cur() == ARROW, "Leave -> arrow", cur())


# ---------------------------------------------------------------- 5
sec("5. 反复进出不退化")
ok_all = True
for _ in range(6):
    if sepx:
        move(sepx, 30)
    for x, y in ((W // 2, 60), (W // 2, H - 30), (60, 12)):
        if move(x, y) != ARROW:
            ok_all = False
chk(ok_all, "来回扫 6 轮，非分隔线区域始终是箭头")

root.destroy()

print(f"\n{'=' * 52}")
print(f"通过 {PASS}，失败 {FAIL}")
if FAILS:
    print("失败项：")
    for f in FAILS:
        print("  -", f)
sys.exit(1 if FAIL else 0)
