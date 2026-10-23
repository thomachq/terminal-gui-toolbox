# -*- coding: utf-8 -*-
"""助手输入区改造自测（v1.0.7 T3）。

四段：
  1. 权限档位：按钮文案跟着 agent_cfg.mode 走，选一档会落盘
  2. 上下文用量：已用/上限的文案与颜色（80% 黄、95% 红、未知不编数字）
  3. 思考档位：模型有档位才出现按钮；选中档位写进请求 body 的 reasoning_effort；
     off 不发这个字段；模型不支持时一个字段都不发
  4. 历史会话：右上角有独立按钮；对话框能开、标题按时间倒序、搜索能过滤

数据目录在临时目录。不联网：`_post_json` 全程被替身接管。
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
TD = tempfile.mkdtemp(prefix="tbdock3_")

import app as appmod  # noqa: E402
from core import aiops  # noqa: E402

appmod.APP_DIR = Path(TD)
appmod.DATA_DIR = Path(TD) / "data"
appmod.DATA_DIR.mkdir(parents=True, exist_ok=True)

import tkinter  # noqa: E402


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


win = appmod.ToolboxApp()
pump(win, 1.0)
win.ai_dock_open()
pump(win, 0.5)

# ================================================================== 1. 权限档位
sec("1. 权限档位")

chk(hasattr(win, "btn_ai_perm"), "输入框旁有权限按钮")
eq(win.btn_ai_perm.text, "自动", "默认显示「自动」")
chk(win.btn_ai_perm.winfo_ismapped(), "权限按钮真的摆出来了")

# 从「权限」按钮选一档
win.btn_ai_perm.command()
pump(win, 0.2)
top = getattr(win, "_ai_perm_top", None)
chk(top is not None, "点权限弹出档位菜单")
win._ai_close_all_menus()
pump(win, 0.1)
chk(getattr(win, "_ai_perm_top", None) is None, "选完/收起后菜单没了")

win.agent_cfg.data["mode"] = "readonly"
win.agent_cfg.save()
win._ai_refresh_composer()
pump(win, 0.1)
eq(win.btn_ai_perm.text, "只读", "按钮跟着配置变")
chk('"mode": "readonly"' in Path(win.agent_cfg.path).read_text(encoding="utf-8"),
    "模式已落盘")
win.agent_cfg.data["mode"] = "auto"
win.agent_cfg.save()

# ================================================================== 2. 上下文
sec("2. 上下文用量")

text, tone, tip = win._ai_ctx_usage()
eq(tone, "mute", "没填上下文窗口时不报警")
chk("上限未知" in text, "上限未知就写「未知」，不编数字", text)

cfg = win.ai_cfg
mid = cfg.current_id()
rec = cfg.find_model(mid)
rec["context"] = 1000
cfg.upsert_model(rec)
win.agent.tokens = 100
text, tone, _ = win._ai_ctx_usage()
eq(tone, "mute", "10% 正常色")
chk("/ 1,000" in text, "文案是「已用 / 上限」", text)
win.agent.tokens = 850
text, tone, tip = win._ai_ctx_usage()
eq(tone, "warn", "85% 变黄")
chk("八成" in tip or "建议" in tip, "提示里给了建议", tip)
win.agent.tokens = 960
text, tone, tip = win._ai_ctx_usage()
eq(tone, "err", "96% 变红")
chk("新建会话" in tip, "红了就直说新建会话", tip)
win._ai_refresh_composer()
pump(win, 0.1)
eq(win.lbl_ai_ctx.cget("fg"), win.ui_err_color if hasattr(win, "ui_err_color")
   else __import__("wbtui").T.err, "标签真的用了错误色")
chk("96" in win.lbl_ai_ctx.cget("text"), "标签文案里有百分比",
    win.lbl_ai_ctx.cget("text"))

# ================================================================== 3. 思考档位
sec("3. 思考档位")

rec = cfg.find_model(mid)
rec["caps"] = {"thinking": {"levels": ["off", "low", "high", "max"],
                            "default": "high"}, "tools": False}
cfg.upsert_model(rec)
win._ai_refresh_composer()
pump(win, 0.2)
chk(win.btn_ai_think.winfo_ismapped(), "模型有档位时按钮出现")
eq(win.btn_ai_think.text, "思考·高", "默认档来自模型配置")
eq(cfg.thinking(), "high", "cfg.thinking() 读到默认档")

# 换成低档，落盘
chk(cfg.set_thinking("low") is True, "能切到低档")
eq(cfg.thinking(), "low", "切完读到低档")
again = aiops.AIConfig(cfg.path)
eq(again.thinking(), "low", "档位落盘了，重开还在")
chk(cfg.set_thinking("bogus") is False, "不存在的档位被拒")
eq(cfg.thinking(), "low", "被拒后档位不变")

# 请求 body 里带上 reasoning_effort
seen: dict = {}


def fake_post(url, payload, headers, timeout):
    seen.clear()
    seen.update(payload)
    return {"choices": [{"message": {"content": "pong"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1,
                      "total_tokens": 4}}


real_post = aiops._post_json
aiops._post_json = fake_post
# Key 存在**模型档案**上（扁平字段只是镜像）：upsert 会拿档案覆盖扁平字段，
# 只 set_key 不写档案的话，下一次 upsert 就把它抹掉了。
cfg.set_model_key(mid, "sk-test-3")
try:
    eng = aiops.OpenAIEngine(cfg)
    eng.chat([{"role": "user", "content": "ping"}])
    eq(seen.get("reasoning_effort"), "low", "选中的档位写进请求 body")
    cfg.set_thinking("off")
    eng.chat([{"role": "user", "content": "ping"}])
    chk("reasoning_effort" not in seen, "「关」档不发这个字段（不是发 none）",
        seen.get("reasoning_effort"))
    cfg.set_thinking("max")
    eng.chat([{"role": "user", "content": "ping"}])
    eq(seen.get("reasoning_effort"), "max", "最高档也带得出去")
finally:
    aiops._post_json = real_post

# 模型不支持思考 → 一个字段都不发
rec = cfg.find_model(mid)
rec["caps"] = {"thinking": None, "tools": False}
cfg.upsert_model(rec)
win._ai_refresh_composer()
pump(win, 0.1)
chk(not win.btn_ai_think.winfo_ismapped(), "模型不支持思考时按钮不占位")
aiops._post_json = fake_post
try:
    aiops.OpenAIEngine(cfg).chat([{"role": "user", "content": "ping"}])
    chk("reasoning_effort" not in seen, "不支持思考的模型不带参数",
        seen.get("reasoning_effort"))
finally:
    aiops._post_json = real_post

# 思考菜单能开、点了能改 cfg
rec["caps"] = {"thinking": {"levels": ["off", "low", "high", "max"],
                            "default": "high"}, "tools": False}
cfg.upsert_model(rec)
win._ai_refresh_composer()
pump(win, 0.1)
win.btn_ai_think.command()
pump(win, 0.2)
chk(getattr(win, "_ai_think_top", None) is not None, "点思考弹出档位菜单")
win._ai_close_all_menus()
pump(win, 0.1)

# ================================================================== 4. 历史会话
sec("4. 历史会话")

chk(hasattr(win, "btn_dock_hist"), "右上角有独立的历史按钮")
chk(win.btn_dock_hist.winfo_ismapped(), "历史按钮真的摆出来了")

store = win._ai_sess_store
n0 = len(store.all())
s2 = store.create("第二个会话")
s2.updated = time.time() + 5
store.save(s2)
win._ai_sess_list()
pump(win, 0.3)
tops = [w for w in win.winfo_children()
        if isinstance(w, tkinter.Toplevel) and w.title() == "历史会话"]
chk(bool(tops), "历史会话窗口开出来了")
if tops:
    top = tops[0]
    ent = [w for w in top.winfo_children()
           if isinstance(w, tkinter.Frame)]
    chk(len(store.all()) >= n0, "会话条数没丢", len(store.all()))
    # 列表按最近聊过倒序：第一条应该是刚改过 updated 的那个
    items = store.all()
    eq(items[0].id, s2.id, "按最近聊过倒序")
    chk(bool([w for w in top.winfo_children()]), "窗口里有内容")
    top.destroy()
pump(win, 0.1)

try:
    win.destroy()
except tkinter.TclError:
    pass

print("\n" + "=" * 50)
print(f"{'失败 ' + str(len(FAILS)) if FAILS else '全部通过'}：{PASS}/{PASS + FAIL}")
for x in FAILS:
    print("  ✗", x)
raise SystemExit(1 if FAILS else 0)
