# -*- coding: utf-8 -*-
"""模型管理改造自测（v1.0.7 T4）。

四段：
  1. 解析器：6 种常见字段名各给一份假 JSON，能取到值；字段缺失返回 None 而不是 0
  2. 过滤：只留文本模型，embedding / rerank / tts / 图像 / 视频 都被挡掉
  3. 数据模型：kind 固定文本、caps 洗白（默认档必须属于可用档）、三个 token 值
  4. 界面：真开一张表单 —— 类型只读、勾思考才出档位、默认档在档位内、
     模态只有两项、胶囊点了能把值写进输入框

数据目录在临时目录，不污染真实配置。
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
TD = tempfile.mkdtemp(prefix="tbmodel4_")

import app as appmod  # noqa: E402
from core import aiops, modelsync  # noqa: E402

appmod.APP_DIR = Path(TD)
appmod.DATA_DIR = Path(TD) / "data"
appmod.DATA_DIR.mkdir(parents=True, exist_ok=True)

import tkinter  # noqa: E402
from pages import model_page  # noqa: E402
from pages.model_page import ModelDialog, _ModelForm  # noqa: E402


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


# ================================================================== 1. 解析器
sec("1. token 限制解析器")

CASES = [
    ("context_length", {"id": "a", "context_length": 128000},
     {"context": 128000}),
    ("max_context_length", {"id": "b", "max_context_length": 262144},
     {"context": 262144}),
    ("max_model_len（vLLM 写法）", {"id": "c", "max_model_len": 32768},
     {"context": 32768}),
    ("top_provider.context_length（OpenRouter）",
     {"id": "d", "top_provider": {"context_length": 200000}},
     {"context": 200000}),
    ("max_input_tokens + max_output_tokens",
     {"id": "e", "max_input_tokens": 196608, "max_output_tokens": 65536},
     {"max_input": 196608, "max_output": 65536}),
    ("limits 嵌套（LiteLLM）",
     {"id": "f", "limits": {"context_window": 1048576,
                            "max_output_tokens": 32768}},
     {"context": 1048576, "max_output": 32768}),
]
for name, payload, want in CASES:
    got = modelsync.parse_limits(payload)
    ok = all(got[k] == v for k, v in want.items())
    chk(ok, f"认得 {name}", got)

empty = modelsync.parse_limits({"id": "z", "object": "model"})
chk(set(empty) == {"context", "max_input", "max_output"},
    "三个键永远都在", empty)
chk(empty["context"] is None and empty["max_input"] is None
    and empty["max_output"] is None, "字段缺失返回 None（不是 0）", empty)
chk(modelsync.parse_limits("just-a-string")["context"] is None,
    "字符串条目也不炸")
chk(modelsync.parse_limits(None)["context"] is None, "None 也不炸")

eq(modelsync.to_int("128K"), 131072, "128K 认成 131072")
eq(modelsync.to_int("1M"), 1048576, "1M 认成 1048576")
eq(modelsync.to_int("128,000"), 128000, "带千分位也认")
eq(modelsync.to_int("65536"), 65536, "纯数字字符串")
eq(modelsync.to_int("不知道"), None, "认不出来就是 None")
eq(modelsync.to_int(0), None, "0 当未知，不当 0")
eq(modelsync.to_int(None), None, "None 就是 None")

# ================================================================== 2. 过滤
sec("2. 只留文本模型")

KEEP = ["deepseek-chat", "gpt-4o", "qwen3-coder:14b", "claude-sonnet-4",
        "qwen2.5-vl-7b", "glm-4.6", "kimi-k2-0905"]
DROP = ["text-embedding-3-large", "bge-m3", "gte-large-zh",
        "bge-reranker-v2-m3", "gpt-4o-mini-tts", "whisper-1",
        "dall-e-3", "gpt-image-1", "stable-diffusion-xl",
        "wanx2.1-t2v-turbo", "kling-video-v3-omni", "mureka-music-v2.6",
        "cosyvoice-v2", "text-embedding-v4"]

for m in KEEP:
    chk(modelsync.is_text_model(m), f"留下 {m}")
for m in DROP:
    chk(not modelsync.is_text_model(m), f"挡掉 {m}")

got = modelsync.filter_text_models(["deepseek-chat", "text-embedding-3-large",
                                    "deepseek-chat", "", "bge-m3",
                                    "qwen3-coder:14b"])
eq(got, ["deepseek-chat", "qwen3-coder:14b"], "过滤 + 去重 + 保序")

caps_g = modelsync.guess_caps("deepseek-r1")
chk(getattr(caps_g.get("thinking"), "get", lambda *_: None)("levels")
    == ["off", "low", "high", "max"], "推理家族预填四个档位", caps_g)
chk(modelsync.guess_caps("qwen3-coder:14b")["thinking"] is None,
    "coder 系不预填思考")
chk(modelsync.guess_caps("llama-3.1-8b")["thinking"] is None,
    "普通模型不预填思考")

# ================================================================== 3. 数据模型
sec("3. 模型档案结构")

m = aiops.AIConfig._clean_model({
    "id": "t1", "provider": "custom", "model": "deepseek-v4",
    "kind": "image",                       # 外部塞进来的类型要被纠正
    "caps": {"thinking": {"levels": ["high", "乱来", "low"],
                          "default": "max"},
             "tools": 1},
    "modalities": ["vision", "image", "video", "audio"],
    "context": 131072, "max_input": 120000, "max_output": 16384,
})
eq(m["kind"], "text", "类型固定成文本，不给选")
eq(m["caps"]["thinking"]["levels"], ["high", "low"], "非法档位被丢掉并保序")
eq(m["caps"]["thinking"]["default"], "high",
   "默认档不在可用档位里 → 退回第一档")
eq(m["modalities"], ["vision", "audio"], "模态只认视觉/音频（image 归一成 vision）")
eq(m["context"], 131072, "上下文存下来了")
eq(m["max_input"], 120000, "最大输入存下来了")
eq(m["max_output"], 16384, "最大输出存下来了")
eq(m["ctx"], 131072, "ctx 老字段跟 context 走")

m2 = aiops.AIConfig._clean_model({"id": "t2", "provider": "custom",
                                  "model": "x", "ctx": 65536})
eq(m2["context"], 65536, "老配置只有 ctx 时，context 跟着 ctx")
eq(m2["max_input"], 0, "没给的最大输入是 0（未知）")

eq(aiops.caps_label(m), "思考·高 · 工具 · 视觉＋音频", "能力摘要一句话")
eq(aiops.caps_label({"caps": {}}), "—", "什么能力都没有就显示 —")

v = aiops.model_view({"id": "t3", "provider": "custom", "model": "y"}, "t3")
eq(v["ctx"], "未知", "抓不到上下文显示「未知」而不是 0")
eq(v["max_in"], "未知", "最大输入也是未知")
eq(v["kind"], "文本", "展示层也标文本")
eq(v["caps"], "—", "没能力")
v2 = aiops.model_view(m, "")
eq(v2["ctx"], "131,072", "有值就带千分位")
eq(v2["max_out"], "16,384", "输出也带千分位")

# 切当前模型时 caps 必须是副本：改扁平那份不能动到档案
cfg = aiops.AIConfig(Path(TD) / "ai_t4.json")
cfg.upsert_model(m)
cfg.set_active("t1")
cfg.data["caps"]["thinking"]["levels"].append("off")
chk("off" not in cfg.find_model("t1")["caps"]["thinking"]["levels"],
    "扁平镜像的 caps 是副本，改它不影响档案")

# ================================================================== 4. 界面
sec("4. 表单与清单")

win = appmod.ToolboxApp()
win.geometry("+20+20")
win.deiconify()
pump(win, 1.0)

f = _ModelForm(win, {"id": "u1", "provider": "custom",
                     "name": "测试", "base": "http://127.0.0.1:9/v1",
                     "model": "deepseek-v4"}, win.ai_cfg)
pump(win, 0.4)
chk(f.winfo_exists(), "表单起来了")
chk(not hasattr(f, "var_kind") and not hasattr(f, "cmb_kind"),
    "没有「模型类型」选择控件（只读）")
chk(hasattr(f, "chips_context") and len(f.chips_context) == 6,
    "上下文有 6 个预设胶囊")
chk(hasattr(f, "chips_max_output") and len(f.chips_max_output) == 5,
    "最大输出有 5 个预设胶囊")

# 胶囊点了要把值写进输入框
f.chips_context[0].command()
eq(f.ent_context.get(), "131072", "点 128K 胶囊写进输入框")
f.chips_max_output[1].command()
eq(f.ent_max_output.get(), "32768", "点 32K 胶囊写进最大输出")
chk(f.ent_context.winfo_exists() and f.ent_max_output.winfo_exists(),
    "手填入口还在（胶囊之上）")
f.ent_context.delete(0, "end")
f.ent_context.insert(0, "999999")
eq(f.ent_context.get(), "999999", "手填优先，胶囊只是填值")

# 勾「思考」才出现档位
f.var_think.set(False)
f._sync_thinking_area()
pump(win, 0.1)
eq(f._think_cell.winfo_manager(), "", "不勾思考时档位区收起来")
f.var_think.set(True)
f._sync_thinking_area()
pump(win, 0.1)
eq(f._think_cell.winfo_manager(), "grid", "勾上思考档位区出现")

# 默认档只能从已勾档位里选
for k in aiops.THINKING_KEYS:
    f.var_levels[k].set(k in ("low", "high"))
f._sync_default_values()
pump(win, 0.1)
labels = list(f.cmb_default.cget("values"))
eq(labels, ["低", "高"], "默认档下拉只列已勾的档位")
chk(f.var_def.get() in labels, "当前默认值必在候选里", f.var_def.get())
for k in aiops.THINKING_KEYS:
    f.var_levels[k].set(False)
f._sync_default_values()
eq(list(f.cmb_default.cget("values")), [], "一个档都不勾，候选为空")

# 模态只有两项
eq(len(aiops.MODALITIES), 2, "输入模态只有两项")
eq(sorted(f.var_mods), ["audio", "vision"], "就是视觉和音频")
chk("video" not in f.var_mods and "file" not in f.var_mods,
    "没有视频/文件")

# 勾了思考但一个档都不选 → 保存被拦
f.var_think.set(True)
f._sync_thinking_area()
f._ok()
chk(f.result is None, "勾了思考却没选档位，保存被拦下")
chk("档位" in f.lb_err.cget("text"), "错误提示说清了原因", f.lb_err.cget("text"))

# 选上档位再保存 → 成功，且默认档在档位内
for k in aiops.THINKING_KEYS:
    f.var_levels[k].set(k in ("off", "high"))
f._sync_default_values()
f.var_mods["vision"].set(True)
f.ent_context.delete(0, "end")
f.ent_context.insert(0, "200000")
f._ok()
pump(win, 0.2)
res = f.result or {}
eq(res.get("kind"), "text", "保存出来的一定是文本类型")
eq((res.get("caps") or {}).get("thinking", {}).get("levels"),
   ["off", "high"], "档位存下来了")
chk((res.get("caps") or {}).get("thinking", {}).get("default") in ("off", "high"),
    "默认档属于可用档位", res.get("caps"))
eq(res.get("modalities"), ["vision"], "模态存下来了")
eq(res.get("context"), 200000, "上下文存下来了")

# 认不出来的 token 值要报错，不能静默变 0
f2 = _ModelForm(win, {"id": "u2", "provider": "custom", "model": "m",
                      "base": "http://127.0.0.1:9/v1"}, win.ai_cfg)
pump(win, 0.3)
f2.ent_max_output.delete(0, "end")
f2.ent_max_output.insert(0, "很多")
f2._ok()
chk(f2.result is None, "认不出来的 token 值被拦下")
chk("认不出来" in f2.lb_err.cget("text"), "提示说清哪个字段", f2.lb_err.cget("text"))
f2.ent_max_output.delete(0, "end")
f2.ent_max_output.insert(0, "64K")
f2._ok()
pump(win, 0.2)
eq((f2.result or {}).get("max_output"), 65536, "64K 存成 65536")
try:
    f2.destroy()
except tkinter.TclError:
    pass

# 清单：列在、未知显示成「未知」、只显示文本模型
dlg = ModelDialog(win, win)
pump(win, 0.6)
keys = [c.key for c in dlg.tbl_model.columns] if hasattr(
    dlg.tbl_model, "columns") else [c.key for c in model_page.MODEL_COLS]
for k in ("ctx", "max_in", "max_out", "caps"):
    chk(k in keys, f"清单有「{k}」这一列")
chk("kind" not in keys, "清单里没有模型类型列（都是文本，没必要）")
rows = dlg.tbl_model.all_rows()
chk(len(rows) >= 1, "清单有行", len(rows))
chk(all(r.get("kind") == "文本" for r in rows), "每一行都是文本模型")
try:
    dlg._close()
except tkinter.TclError:
    pass

try:
    f.destroy()
except tkinter.TclError:
    pass
win.destroy()

print("\n" + "=" * 50)
print(f"{'失败 ' + str(len(FAILS)) if FAILS else '全部通过'}：{PASS}/{PASS + FAIL}")
for x in FAILS:
    print("  ✗", x)
raise SystemExit(1 if FAILS else 0)
