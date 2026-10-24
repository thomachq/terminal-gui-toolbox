# -*- coding: utf-8 -*-
"""模型管理自测：多模型清单 + 状态机 + 试连。

三段：
  1. 配置层：老配置静默升级、增删切、最后一条删不掉、重开还在
  2. 展示层：model_view 的状态与 tone（缺 Key / 缺 Harness / 就绪）
  3. 界面层：真开一个 ModelDialog，行数对得上、切换能生效、删到只剩一条被拒

数据目录在临时目录，--no-probe 保证不跑任何探测。
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
TD = tempfile.mkdtemp(prefix="tbmodel_")

import app as appmod  # noqa: E402
from core import aiops  # noqa: E402

appmod.APP_DIR = Path(TD)
appmod.DATA_DIR = Path(TD) / "data"
appmod.DATA_DIR.mkdir(parents=True, exist_ok=True)

import tkinter  # noqa: E402
from pages.model_page import ModelDialog  # noqa: E402


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
        except tkinter.TclError:
            return
        time.sleep(0.01)


# ================================================================== 1. 配置层
sec("1. 配置层")

p = Path(TD) / "ai_old.json"
# 老版本只有扁平字段，没有 models 数组
p.write_text('{"provider":"deepseek","base":"https://api.deepseek.com",'
             '"model":"deepseek-chat","temperature":0.3,'
             '"key_enc":"x"}', encoding="utf-8")
cfg = aiops.AIConfig(p)
ms = cfg.models()
eq(len(ms), 1, "老配置升级成一条")
eq(ms[0]["name"], "默认模型", "升级出来的那条叫默认模型")
eq(ms[0]["model"], "deepseek-chat", "模型名从扁平字段带过来")
eq(cfg.current_id(), ms[0]["id"], "升级后它就是当前")
chk(cfg.current_id() in p.read_text(encoding="utf-8"), "活跃 id 落盘了")

cfg.set_key("sk-old")
chk(cfg.get_key() == "sk-old", "老 Key 没丢")

m2 = cfg.upsert_model({"provider": "ollama", "model": "qwen2.5-coder:14b",
                       "name": "本机 Ollama", "temperature": 0.1})
eq(len(cfg.models()), 2, "新增第二条")
chk(cfg.current_id() == ms[0]["id"], "新增不会自动抢当前")

chk(cfg.set_active(m2["id"]) is True, "切到第二条成功")
eq(cfg.data["model"], "qwen2.5-coder:14b", "扁平字段跟着切")
eq(cfg.data["provider"], "ollama", "provider 也跟着切")
chk(cfg.current_id() == m2["id"], "活跃 id 已更新")

again = aiops.AIConfig(p)
eq(len(again.models()), 2, "重开配置还有两条")
eq(again.current_id(), m2["id"], "重开后活跃项保持")

ok, why = again.remove_model(ms[0]["id"])
chk(ok is True and why == "", "删掉一条")
eq(len(again.models()), 1, "只剩一条")
ok2, why2 = again.remove_model(m2["id"])
chk(ok2 is False and "至少" in why2, "最后一条删不掉", why2)

# ================================================================== 2. 展示层
sec("2. model_view 状态机")

v = aiops.model_view({"id": "a", "provider": "deepseek", "model": "deepseek-chat",
                      "key_enc": ""}, "b")
eq(v["state"], "缺 Key", "没 Key 的官方线路标缺 Key")
eq(v["tone"], "warn", "缺 Key 用 warn 色")
eq(v["current"], "", "不是当前就别打点")

v2 = aiops.model_view({"id": "b", "provider": "deepseek", "model": "deepseek-chat",
                       "key_enc": "zzz"}, "b")
eq(v2["state"], "就绪", "Key 有了就是就绪")
eq(v2["current"], "● 当前", "当前那条有标记")
eq(v2["key"], "已存", "Key 只显示状态，不回显")

v3 = aiops.model_view({"id": "c", "provider": "ollama", "model": "qwen2.5:7b"},
                      "b")
eq(v3["key"], "不需要", "本地线路不需要 Key")
eq(v3["state"], "就绪", "本地线路直接就绪")

v4 = aiops.model_view({"id": "d", "provider": "dsh"}, "b")
chk(v4["state"] in ("可用", "缺 Harness"), "harness 线路按 dsh 是否可用来判", v4["state"])

v5 = aiops.model_view({"id": "e", "provider": "custom", "ctx": 65536,
                       "temperature": 0.2, "base": "http://10.0.0.9:8000/v1"},
                      "e")
eq(v5["ctx"], "65,536", "上下文带千分位")
eq(v5["temp"], "0.2", "温度去掉多余零")

# ================================================================== 3. 界面层
sec("3. ModelDialog")

win = appmod.ToolboxApp()
win.geometry("+20+20")
win.deiconify()
pump(win, 1.0)

dlg = ModelDialog(win, win)
pump(win, 0.5)
chk(dlg.winfo_exists(), "对话框起来了")
pid = dlg.sel_pid
eq(len(dlg.tbl_model.all_rows()), len(win.ai_cfg.provider_models(pid)),
   "只显示选中供应商下的模型")
chk("共 " in dlg.lbl_sum.cget("text"), "汇总行有内容", dlg.lbl_sum.cget("text"))

# 左侧每一家都能点，切过去表格跟着换（供应商 / 模型 两个维度）
pids = list(dlg.plist._rows.keys())
chk(len(pids) >= 4, "左侧列出多家供应商", pids)
dlg.provider_pick("ollama")
pump(win, 0.2)
eq(len(dlg.tbl_model.all_rows()), len(win.ai_cfg.provider_models("ollama")),
   "切到 Ollama 后只剩它下面的模型")
eq(dlg.lbl_prov.cget("text"), aiops.PROVIDERS["ollama"]["label"],
   "右上供应商标题跟着切换")
eq(dlg.v_base.get(), win.ai_cfg.provider_base("ollama"), "接口地址跟着切换")

# 在这个供应商下新增一条，列表要长一行
n_oll = len(dlg.tbl_model.all_rows())
rec = win.ai_cfg.upsert_model({"provider": "ollama", "name": "第二条",
                               "model": "qwen2.5:14b"})
dlg.model_reload()
eq(len(dlg.tbl_model.all_rows()), n_oll + 1, "新增后列表长一行")

# 选中新增那条 → 设为当前
rows = dlg.tbl_model.all_rows()
idx = [i for i, r in enumerate(rows) if r["_id"] == rec["id"]][0]
dlg.tbl_model.tree.selection_set(dlg.tbl_model.tree.get_children()[idx])
pump(win, 0.1)
dlg.model_activate()
pump(win, 0.2)
eq(win.ai_cfg.current_id(), rec["id"], "点『设为当前』真的切过去了")
cur = [r for r in dlg.tbl_model.all_rows() if r["current"]][0]
eq(cur["_id"], rec["id"], "表格里的当前标记跟着动")

# 删到全局只剩一条时，删除被拒
for m in win.ai_cfg.models()[1:]:
    win.ai_cfg.remove_model(m["id"])
dlg.provider_pick("ollama")
pump(win, 0.1)
ok, why = win.ai_cfg.remove_model(win.ai_cfg.current_id())
chk(ok is False, "最后一条删不掉（删空助手就没模型可用）", why)

# 试连：没 Key 的官方线路要给出人话
bad = win.ai_cfg.upsert_model({"provider": "deepseek", "model": "deepseek-chat",
                               "name": "没填 Key 的", "key_enc": ""})
okp, msgp = aiops.probe_model(bad, timeout=3)
chk(okp is False, "缺 Key 试连失败")
chk("Key" in msgp or "key" in msgp.lower(), "失败原因指向 Key", msgp)

dsh = win.ai_cfg.upsert_model({"provider": "dsh", "name": "harness"})
okd, msgd = aiops.probe_model(dsh, timeout=3)
chk(isinstance(okd, bool) and bool(msgd), "harness 线路试连有明确结论", msgd)

try:
    dlg.destroy()
except tkinter.TclError:
    pass
try:
    win.destroy()
except tkinter.TclError:
    pass
try:
    import shutil
    shutil.rmtree(TD, ignore_errors=True)
except OSError:
    pass

print(f"\n=== {PASS} 通过 / {FAIL} 失败")
for f in FAILS:
    print("  ✗", f)
sys.exit(1 if FAIL else 0)
