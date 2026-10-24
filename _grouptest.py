# -*- coding: utf-8 -*-
"""模型管理「对齐 Cherry Studio」那批交互的自测（v1.0.9 F 段）。

为什么单独一个文件：`_modeltest.py` 已经 114 项、跑一轮要开一堆窗口，
这批是「表格控件的新能力 + 供应商卡的新控件」，既要在裸 DataTable 上验
（不掺模型页的业务），又要在真实模型页上验（组名从 group 字段来、
组内排序、✕ 删除、端点切换、Key 掩码）。两边都要，所以独立成文件。

分段：
  1. 裸 DataTable：组头数 / 子行数 / show 切到 tree headings
  2. 折叠状态跨 rebuild 保持（切供应商、增删一条都不能把用户收起的组弹开）
  3. 三个新入口：select_index / column_at / row_at
  4. 模型页：组名取 group、缺省叫「默认」、组内按名称排
  5. 行尾 ✕ 能删掉那一行
  6. set_group(None) 回到平表（别的页面还在用平表）
  7. 接口地址多端点：加 / 切 / 删，以及「改地址不丢端点」
  8. Key 的掩码 ↔ 明文（眼睛），切供应商自动关回去
"""
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.argv = [sys.argv[0], "--no-probe"]
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = FAIL = 0
FAILS: list[str] = []
TD = tempfile.mkdtemp(prefix="tbgrouptest_")

import app as appmod  # noqa: E402

appmod.APP_DIR = Path(TD)
appmod.DATA_DIR = Path(TD) / "data"
appmod.DATA_DIR.mkdir(parents=True, exist_ok=True)

import tkinter as tk  # noqa: E402

from ui import forms  # noqa: E402
from ui.table import Column, DataTable  # noqa: E402


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")


def eq(a, b, name):
    chk(a == b, name, f"期望 {b!r} 实得 {a!r}")


def sec(t):
    print(f"\n-- {t}")


def pump(win, seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        try:
            win.update()
        except tk.TclError:
            return
        time.sleep(0.01)


win = appmod.ToolboxApp()
win.geometry("+20+20")
win.deiconify()
pump(win, 1.0)

# ============================================================ 1. 裸表格分组
sec("1. 裸 DataTable 的分组")

COLS = [
    Column("name", "名称", 140),
    Column("grp", "组", 80),
    Column("del", "", 30, anchor="center", format=lambda v, r: "✕"),
]
ROWS = [
    {"name": "b-model", "grp": "alpha"},
    {"name": "a-model", "grp": "alpha"},
    {"name": "c-model", "grp": "beta"},
    {"name": "d-model", "grp": "beta"},
    {"name": "e-model", "grp": "beta"},
]

top = tk.Toplevel(win)
top.geometry("620x360+60+60")
dt = DataTable(top, COLS, height=6)
dt.pack(fill="both", expand=True)
dt.set_group("grp", empty="默认")
dt.set_rows(list(ROWS))
pump(top, 0.4)

chk(dt.is_grouped(), "分组已开启")
chk("tree" in str(dt.tree.cget("show")), "show 切成 tree headings",
    dt.tree.cget("show"))
heads = dt.tree.get_children("")
eq(len(heads), 2, "两个组 → 两条组头")
eq(sum(len(dt.tree.get_children(g)) for g in heads), len(ROWS),
   "子行总数等于数据行数")
eq(dt.groups(), ["alpha", "beta"], "组顺序取首出现顺序")
chk("alpha" in dt.tree.item(heads[0], "text"), "组头写着组名",
    dt.tree.item(heads[0], "text"))
chk("(2)" in dt.tree.item(heads[0], "text"), "组头带条数",
    dt.tree.item(heads[0], "text"))
chk(all(dt.tree.item(g, "open") for g in heads), "默认全展开")
eq(len(dt.tree.item(heads[0], "values")), len(COLS),
   "组头行的 values 与列数对齐（不然 Treeview 会错位）")

# ============================================================ 2. 折叠状态保持
sec("2. 折叠状态跨 rebuild 保持")

dt.tree.item(heads[0], open=False)
dt._remember_open()
dt.set_rows(list(ROWS))          # 模拟「切了个供应商 / 增删一条」
pump(top, 0.3)
heads = dt.tree.get_children("")
eq(dt.tree.item(heads[0], "open"), 0, "收起的组 rebuild 之后还是收起的")
eq(dt.tree.item(heads[1], "open"), 1, "另一组不受影响")

dt.set_all_open(True)
pump(top, 0.2)
heads = dt.tree.get_children("")
chk(all(dt.tree.item(g, "open") for g in heads), "一键全展开")
dt.set_all_open(False)
pump(top, 0.2)
heads = dt.tree.get_children("")
chk(not any(dt.tree.item(g, "open") for g in heads), "一键全收起")
dt.set_all_open(True)
pump(top, 0.2)

# ============================================================ 3. 新入口
sec("3. select_index / column_at / row_at")

heads = dt.tree.get_children("")
first_row_iid = dt.tree.get_children(heads[0])[0]
got = dt.select_index(0)
pump(top, 0.2)
chk(got is not None, "select_index(0) 返回行")
eq((dt.selected() or {}).get("name"), ROWS[0]["name"],
   "selected() 按新 iid 反查得到第一行")

# 列定位：拿 bbox 量出每个**显示**列的横坐标，再让 column_at 认回来。
# 注意分组列已被 displaycolumns 摘掉，所以 #N 里没有它 —— 这正是要验的：
# column_at 报的 key 必须跟实际显示顺序一致，不能把隐藏列算进去。
keys = [c.key for c in COLS if c.key != "grp"]
ok_map = True
detail = ""
for n, key in enumerate(keys, start=1):
    box = dt.tree.bbox(first_row_iid, "#%d" % n)
    if not box:
        ok_map = False
        detail = f"bbox 拿不到 #{n}"
        break
    x = box[0] + max(1, box[2] // 2)
    if dt.column_at(x) != key:
        ok_map = False
        detail = f"#{n} 期望 {key} 实得 {dt.column_at(x)}"
        break
chk(ok_map, "column_at 能把每一列认回来", detail)

box = dt.tree.bbox(first_row_iid)
row = dt.row_at(box[1] + box[3] // 2) if box else None
eq((row or {}).get("name"), ROWS[0]["name"], "row_at 认得出那一行")
eq(dt.column_at(0), "#0", "最左边的分组列报 #0")
gh = dt.tree.bbox(heads[0])
chk(dt.row_at(gh[1] + gh[3] // 2) is None if gh else True,
    "组头不当作数据行（点它不该触发编辑）")

top.destroy()
pump(win, 0.2)

# ============================================================ 4. 模型页
sec("4. 模型页的分组")

from pages.model_page import ModelDialog  # noqa: E402

cfg = win.ai_cfg
pid = "ollama"
for m in list(cfg.provider_models(pid)):
    cfg.remove_model(m["id"])
cfg.upsert_model({"provider": pid, "name": "z-后加的", "model": "m-z",
                  "group": "qwen3.6-35b"})
cfg.upsert_model({"provider": pid, "name": "a-先写的", "model": "m-a",
                  "group": "qwen3.6-35b"})
cfg.upsert_model({"provider": pid, "name": "deepseek-chat", "model": "m-ds",
                  "group": "deepseek"})
cfg.upsert_model({"provider": pid, "name": "没写分组的", "model": "m-none"})

dlg = ModelDialog(win, win)
pump(win, 0.6)
dlg.provider_pick(pid)
pump(win, 0.4)

tbl = dlg.tbl_model
chk(tbl.is_grouped(), "模型表开着分组")
heads = tbl.tree.get_children("")
eq(len(heads), 3, "三个分组（deepseek / qwen3.6-35b / 默认）")
names = [tbl.tree.item(g, "text") for g in heads]
chk(any("默认" in n for n in names), "没写分组名的归到「默认」", names)
chk(any("qwen3.6-35b" in n for n in names), "组头用 group 字段", names)

# 组内按名称排序：a-先写的 要排在 z-后加的 前面
qg = [g for g in heads if "qwen3.6-35b" in tbl.tree.item(g, "text")][0]
kids = [tbl.tree.item(k, "values") for k in tbl.tree.get_children(qg)]
# values 按 columns 顺序给（current, name, model, …），名称在第 2 格
first_names = [v[1] for v in kids]
eq(first_names, ["a-先写的", "z-后加的"], "组内按名称排序（不是插入顺序）")

# 分组列不再占一列（组名已经在左边树列上）
disp = [c for c in tbl.columns if c.key == "group"]
eq(len(disp), 0, "模型表里没有单独的「分组」列")
chk(all(c.key not in ("key", "base") for c in tbl.columns),
    "不再有「Key」「接口地址」列（都归供应商层）")

# ============================================================ 5. 行尾 ✕
sec("5. 行尾 ✕ 直接删")

tbl.set_all_open(True)
pump(win, 0.3)
target = [r for r in tbl.all_rows() if r.get("model") == "m-ds"][0]
tid = target["_id"]
heads = tbl.tree.get_children("")
# 找到它挂在哪条组头下面
rid = None
for g in heads:
    for k in tbl.tree.get_children(g):
        if (tbl._iid_row.get(k) or {}).get("_id") == tid:
            rid = k
            break
    if rid:
        break
chk(rid is not None, "先在树上定位到那一行")

if rid:
    n_index = [c.key for c in tbl.columns].index("del") + 1
    box = tbl.tree.bbox(rid, "#%d" % n_index)
    chk(bool(box), "拿到 ✕ 那一格的坐标", box)
    if box:
        old_confirm = forms.confirm
        forms.confirm = lambda *a, **k: True
        try:
            tbl.tree.event_generate("<Button-1>",
                                    x=box[0] + box[2] // 2,
                                    y=box[1] + box[3] // 2)
            pump(win, 0.4)
        finally:
            forms.confirm = old_confirm
        left = [m["id"] for m in cfg.provider_models(pid)]
        chk(tid not in left, "点 ✕ 真的删掉了那一条", left)

# 点别处的格子不该删任何东西
before = len(cfg.provider_models(pid))
heads = tbl.tree.get_children("")
k0 = tbl.tree.get_children(heads[0])[0]
box = tbl.tree.bbox(k0, "#1")
if box:
    tbl.tree.event_generate("<Button-1>", x=box[0] + box[2] // 2,
                            y=box[1] + box[3] // 2)
    pump(win, 0.3)
eq(len(cfg.provider_models(pid)), before, "点名称格不会误删")

# ============================================================ 6. 关掉分组
sec("6. set_group(None) 回到平表")

tbl.set_group(None)
pump(win, 0.3)
chk(not tbl.is_grouped(), "分组可关")
show = str(tbl.tree.cget("show"))
chk("headings" in show and "tree" not in show, "show 回到 headings", show)
kids = tbl.tree.get_children("")
eq(len(kids), len(tbl.all_rows()), "平表一行一个节点")
chk(all(k.startswith("0") or k.isdigit() for k in kids), "平表 iid 还是序号",
    kids[:3])
tbl.set_group("group", empty="默认")
pump(win, 0.2)

# ============================================================ 7. 多端点
sec("7. 接口地址可以有多个端点")

cfg = win.ai_cfg
p = "ollama"
cfg.set_provider(p, base="http://10.0.0.1:11434/v1")
chk("http://10.0.0.1:11434/v1" in cfg.provider_endpoints(p),
    "当前地址自动进清单", cfg.provider_endpoints(p))
chk(cfg.add_endpoint(p, "http://gw.local/ollama/v1"), "能加第二个端点")
chk(cfg.add_endpoint(p, "http://gw.local/ollama/v1") is False, "重复地址不加")
eps = cfg.provider_endpoints(p)
eq(eps[0], "http://10.0.0.1:11434/v1", "当前生效的排第一")
eq(len(eps), 2, "清单两条")

cfg.use_endpoint(p, "http://gw.local/ollama/v1")
eq(cfg.provider_base(p), "http://gw.local/ollama/v1", "切端点真的切过去了")
sub = cfg.provider_models(p)
chk(all(str(m.get("base")) == "http://gw.local/ollama/v1" for m in sub),
    "新地址下发到下面每条模型", [(m.get("name"), m.get("base")) for m in sub])

cfg.set_provider(p, base="http://10.0.0.1:11434/v1")
chk("http://gw.local/ollama/v1" in cfg.provider_endpoints(p),
    "改地址不会把别的端点一起丢掉（整体覆盖写的老坑）")

chk(cfg.remove_endpoint(p, "http://gw.local/ollama/v1"), "非当前端点能删")
eq(cfg.provider_base(p), "http://10.0.0.1:11434/v1", "删的不是当前那条，地址不动")
chk(cfg.remove_endpoint(p, "http://10.0.0.1:11434/v1") is False,
    "最后一条端点删不得（删了这家没入口）")

# 界面上的两个按钮真的在
chk(hasattr(dlg, "btn_eps") and dlg.btn_eps.winfo_exists(), "供应商卡上有「端点 ▾」")
chk(hasattr(dlg, "btn_eye") and dlg.btn_eye.winfo_exists(), "Key 行有眼睛")

# ============================================================ 8. 眼睛
sec("8. Key 掩码 / 明文")

show0 = str(dlg.ent_key.cget("show"))
chk("•" in show0, "Key 默认是掩码", show0)
dlg._toggle_key_show()
pump(win, 0.2)
chk(str(dlg.ent_key.cget("show")) == "", "点眼睛变明文",
    dlg.ent_key.cget("show"))
dlg._toggle_key_show()
pump(win, 0.2)
chk("•" in str(dlg.ent_key.cget("show")), "再点回掩码")
dlg._toggle_key_show()
dlg.provider_pick(p)          # 换一家
pump(win, 0.2)
chk("•" in str(dlg.ent_key.cget("show")), "切供应商时自动关回掩码")

try:
    dlg._close()
except tk.TclError:
    pass

# ============================================================ 9. 编辑抽屉
sec("9. 右侧「编辑模型」抽屉")

dlg2 = ModelDialog(win, win)
pump(win, 0.8)
dlg2.provider_pick("ollama")
pump(win, 0.4)

chk(dlg2.drawer is None, "一开始没有抽屉")
dlg2.model_add()
pump(win, 0.4)
d = dlg2.drawer
chk(d is not None, "点「＋ 新增模型」开的是抽屉")
chk(d is not None and str(d.winfo_manager()) == "place",
    "抽屉是浮层（place），不挤窄表格", getattr(d, "winfo_manager", lambda: "?")())
for key in ("model", "name", "group"):
    chk(hasattr(d, "ent_" + key), f"抽屉里有「{key}」输入框")

d.ent_model.insert(0, "qwen3.9-9b")
d.ent_name.insert(0, "九号模型")
d.ent_group.insert(0, "qwen3.9")
n_before = len(cfg.provider_models("ollama"))
d.save()
pump(win, 0.5)
eq(len(cfg.provider_models("ollama")), n_before + 1, "保存后多一条模型")
saved = [m for m in cfg.provider_models("ollama")
         if m.get("model") == "qwen3.9-9b"]
chk(bool(saved), "存进去了")
if saved:
    eq(saved[0].get("group"), "qwen3.9", "分组名记下来了")
    eq(saved[0].get("name"), "九号模型", "名称记下来了")
chk(dlg2.drawer is None, "保存完抽屉自动收起")

# 再点这一行编辑：抽屉要带着原值打开
rec = [m for m in cfg.provider_models("ollama") if m.get("model") == "qwen3.9-9b"]
if rec:
    dlg2.model_edit(rec[0])
    pump(win, 0.4)
    d2 = dlg2.drawer
    chk(d2 is not None, "编辑时开抽屉")
    eq(d2.v_model.get(), "qwen3.9-9b", "抽屉带出模型 ID")
    eq(d2.v_group.get(), "qwen3.9", "抽屉带出分组名")
    d2.ent_group.delete(0, "end")
    d2.ent_group.insert(0, "qwen3.9-pro")
    d2.save()
    pump(win, 0.4)
    after = [m for m in cfg.provider_models("ollama")
             if m.get("model") == "qwen3.9-9b"]
    eq((after[0].get("group") if after else None), "qwen3.9-pro", "改分组生效")

# 空模型 ID 不许保存
dlg2.model_add()
pump(win, 0.3)
d3 = dlg2.drawer
n_now = len(cfg.provider_models("ollama"))
d3.save()
pump(win, 0.3)
eq(len(cfg.provider_models("ollama")), n_now, "模型 ID 空着不保存")
chk("模型 ID" in str(d3.lb_err.cget("text")), "并且告诉用户缺什么",
    d3.lb_err.cget("text"))
eq(dlg2.drawer is not None, True, "报错时抽屉不关（不然提示看不到）")

# 换供应商要把抽屉收掉
dlg2.provider_pick("deepseek")
pump(win, 0.4)
chk(dlg2.drawer is None, "切供应商自动收起抽屉")

try:
    dlg2._close()
except tk.TclError:
    pass
win.destroy()

print("\n" + "=" * 50)
print(f"{'失败 ' + str(len(FAILS)) if FAILS else '全部通过'}：{PASS}/{PASS + FAIL}")
for x in FAILS:
    print("  ✗", x)
raise SystemExit(1 if FAILS else 0)
