# -*- coding: utf-8 -*-
"""抓模型管理页的图，核对「按分组折叠」的观感（纯本地核对，不入库）。

    python _shot_model.py
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.argv = [sys.argv[0], "--no-probe"]
sys.path.insert(0, str(HERE))

import _shots as SH  # noqa: E402

import app as appmod  # noqa: E402

# 下面会先删光 ollama 的模型再灌 demo —— 必须改临时副本（这就是 10-24
# 把用户真实模型表覆盖掉的元凶），真实 data 一个字节都不动
SH.isolate("tbshot_model_")


def pump(win, sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        try:
            win.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.01)


win = appmod.ToolboxApp()
win.geometry("1180x760+40+40")
win.deiconify()
pump(win, 1.0)

cfg = win.ai_cfg
pid = "ollama"
for m in list(cfg.provider_models(pid)):
    cfg.remove_model(m["id"])
DEMO = [
    ("qwen3.6-35b", "qwen3.6-35b-a3b", 65536, 65536, 8192, "思考 · 工具"),
    ("qwen3.6-35b", "qwen3.6-35b-q4", 65536, 32768, 8192, "思考"),
    ("qwen3.6-35b", "qwen3.6-35b-iq4xs", 65536, 32768, 8192, "思考"),
    ("deepseek", "deepseek-v4", 262144, 262144, 65536, "思考 · 工具"),
    ("deepseek", "deepseek-coder", 131072, 131072, 32768, "工具"),
    ("qwen2.5-vl", "qwen2.5-vl:7b", 32768, 32768, 8192, "视觉 · 工具"),
]
for grp, model, ctx, mi, mo, caps in DEMO:
    cfg.upsert_model({"provider": pid, "name": model, "model": model,
                      "group": grp, "context": ctx, "max_input": mi,
                      "max_output": mo,
                      "caps": {"tools": "工具" in caps,
                               "thinking": ({"levels": ["off", "high"],
                                             "default": "off"}
                                            if "思考" in caps else None)},
                      "modalities": (["image"] if "视觉" in caps else ["text"])})

from pages.model_page import ModelDialog  # noqa: E402

dlg = ModelDialog(win, win)
pump(dlg, 1.0)
dlg.provider_pick(pid)
try:
    dlg.grab_release()
except Exception:  # noqa: BLE001
    pass
pump(dlg, 1.2)

SH.TITLE = "模型管理 — 智能运维助手"


def shoot(name):
    hits = SH.find_titled()
    if not hits:
        print("没找到窗口")
        return
    img = SH.capture(hits[-1])
    if img is None:
        print("capture 返回空")
        return
    out = HERE / "screenshots"
    out.mkdir(exist_ok=True)
    p = out / name
    img.save(p)
    print("saved", p, img.size)


shoot("model_grouped.png")

rows = dlg.tbl_model.all_rows()
if rows:
    dlg.model_edit(rows[0]["_raw"])
    pump(dlg, 0.8)
    shoot("model_drawer.png")

try:
    dlg.destroy()
except Exception:  # noqa: BLE001
    pass
