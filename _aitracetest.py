# -*- coding: utf-8 -*-
"""AI 活动流自测：AI 干了什么，终端里必须看得见。

四段：
  1. core.aitrace 的拼装（纯函数，不起窗口）
  2. 引擎上报：模型请求 / 响应各一条，失败也有一条
  3. TerminalPane.ai_feed：写进去、能关掉
  4. 真窗口：ai_trace 会自动开终端；_ai_exec 的命令与输出落到终端，
     且不会被 tracer 再喂一遍（重复回显是最容易犯的错）

数据目录在临时目录，--no-probe 保证不跑任何探测。
"""
import json
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
TD = tempfile.mkdtemp(prefix="tbaitrace_")

import app as appmod  # noqa: E402
from core import aitrace, aiops  # noqa: E402

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


def joined(lines):
    return "\n".join(t for t, _k in lines)


# ================================================================== 1. 格式
sec("1. 拼装（纯函数）")

req = aitrace.api_request(
    base="https://api.deepseek.com/v1", model="deepseek-chat",
    messages=[{"role": "system", "content": "你是运维助手"},
              {"role": "user", "content": "看看磁盘\n顺便看看负载"}],
    provider="deepseek", t=0)
j = joined(req)
chk("POST api.deepseek.com/v1/chat/completions" in j, "请求带上完整地址", j[:80])
chk("https://" not in j, "协议头被剥掉，省一行宽度")
chk("deepseek-chat" in j, "请求带模型名")
chk("2 条消息" in j, "请求带消息条数", j[:100])
chk("system" in j and "user" in j, "逐条列出角色")
chk("看看磁盘 ⏎ 顺便看看负载" in j, "多行压成一行，不撑破终端")
chk(all(tag in ("aihead", "aidim") for _t, tag in req), "请求只有两级标签")

long_msg = aitrace.api_request(model="m", messages=[
    {"role": "user", "content": "啊" * 400}], t=0)
body = [t for t, _k in long_msg if "啊" in t][0]
chk(body.rstrip().endswith("…"), "超长消息被截断")
chk(len(body) < aitrace.HEAD_MAX + 20, "截断后长度受控", str(len(body)))

resp = aitrace.api_response(ok=True, ms=1832, tokens={"total": 640},
                            text='{"tool":"exec","args":{"cmd":"df -h"}}', t=0)
jr = joined(resp)
chk("200" in jr, "响应带状态码")
chk("1.8s" in jr, "响应带耗时")
chk("640 tokens" in jr, "响应带 token")
chk('"tool":"exec"' in jr, "响应带上模型实际回了什么")

bad = aitrace.api_response(ok=False, ms=90, err="认证失败（401）：请检查 API Key", t=0)
jb = joined(bad)
chk("失败" in jb and "401" in jb, "失败也有一条，带原因", jb[:80])
chk(all(tag in ("err",) for _t, tag in bad), "失败整段标红")
chk(aitrace.api_response(ok=False, err="")[1][1] == "err", "空原因也不崩")

c = aitrace.cmd(host="web-01", command="df -h", t=0)
chk("AI ▶ web-01 $ df -h" in c[0][0], "命令行带主机与命令", c[0][0])
chk(aitrace.cmd(command="rm -rf /", risk="danger")[0][0].endswith("⚠"),
    "危险命令带警示标记")

r = aitrace.result(rc=0, ms=820, out="a\nb\nc", t=0)
chk("rc=0" in r[0][0] and "820ms" in r[0][0] and "3 行" in r[0][0],
    "收尾行带 rc / 耗时 / 行数", r[0][0])
chk(aitrace.result(rc=2)[0][1] == "err", "非 0 的 rc 标红")

eq(aitrace.human_size(900), "900B", "体积：字节")
eq(aitrace.human_size(2048), "2.0KB", "体积：KB")
eq(aitrace.human_ms(820), "820ms", "耗时：毫秒")
eq(aitrace.human_ms(1832), "1.8s", "耗时：秒")
eq(aitrace.short_host("https://api.deepseek.com/v1/"), "api.deepseek.com/v1",
   "地址剥协议与尾斜杠")
eq(aitrace.short_host(""), "(未配置地址)", "空地址有兜底")
chk("AI ⇢" in aitrace.banner()[1][0] or any("AI ⇢" in t for t, _k in aitrace.banner()),
    "开场说明里解释了三种行")

# ================================================================== 2. 引擎上报
sec("2. 引擎上报（模型请求 / 响应）")

evts: list[tuple[str, dict]] = []
aiops.set_trace(lambda k, i: evts.append((k, i)))
chk(aiops.trace_enabled(), "钩子已挂上")

cfg = aiops.AIConfig(Path(TD) / "ai2.json")
cfg.data["provider"] = "deepseek"
cfg.set_key("sk-test-123")

_orig_post = aiops._post_json
aiops._post_json = lambda *a, **k: {
    "choices": [{"message": {"content": '{"analysis":"ok","tool":"exec"}'}}],
    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
}
try:
    text, usage = aiops.OpenAIEngine(cfg).chat(
        [{"role": "user", "content": "磁盘满了吗"}])
finally:
    aiops._post_json = _orig_post

kinds = [k for k, _i in evts]
eq(kinds, ["req", "resp"], "一次调用正好一进一出")
req_i = evts[0][1]
chk(req_i["messages"][0]["content"] == "磁盘满了吗", "请求里有原始输入")
chk(req_i["model"], "请求里有模型名")
resp_i = evts[1][1]
chk(resp_i["ok"] is True, "响应标记成功")
chk(resp_i["text"].startswith("{\"analysis\""), "响应里有模型原文")
chk(resp_i["tokens"]["total_tokens"] == 120, "响应里带 token")
chk(isinstance(resp_i["ms"], int), "响应里带耗时")


def _boom(*a, **k):
    raise aiops.AIError("认证失败（401）：请检查 API Key")


evts.clear()
aiops._post_json = _boom
try:
    aiops.OpenAIEngine(cfg).chat([{"role": "user", "content": "x"}])
except aiops.AIError:
    pass
finally:
    aiops._post_json = _orig_post
eq([k for k, _i in evts], ["req", "resp"], "失败也是一进一出")
chk(evts[1][1]["ok"] is False and "401" in evts[1][1]["err"],
    "失败原因传到了上报里")

# 上报是旁路：自己抛异常也不能影响模型调用
evts.clear()


def _bad_cb(k, i):
    raise RuntimeError("上报自己坏了")


aiops.set_trace(_bad_cb)
aiops._post_json = lambda *a, **k: {"choices": [{"message": {"content": "hi"}}]}
try:
    txt, _u = aiops.OpenAIEngine(cfg).chat([{"role": "user", "content": "x"}])
    chk(txt == "hi", "上报回调崩了，模型调用照常返回")
except Exception as e:  # noqa: BLE001
    chk(False, "上报回调崩了，模型调用照常返回", str(e))
finally:
    aiops._post_json = _orig_post
aiops.set_trace(None)

# ================================================================== 3. 终端写入
sec("3. TerminalPane.ai_feed")

win = appmod.ToolboxApp()
win.geometry("+30+30")
win.deiconify()
pump(win, 1.0)

from ui.terminal import TerminalPane  # noqa: E402

eq(win.tabbar.count(), 1, "起步只有主机资源一个页签")
pane = TerminalPane(win.ws_hosts, win)
pane.pack(fill="both", expand=True)
pump(win, 0.2)

before = pane.text.get("1.0", "end-1c")
pane.ai_feed(aitrace.cmd(host="h1", command="uptime"))
pump(win, 0.1)
mid = pane.text.get("1.0", "end-1c")
chk("AI ▶ h1 $ uptime" in mid, "命令行写进了正文")
chk(len(mid) > len(before), "确实多出内容")

pane._ai_on.set(False)
pane.ai_feed(aitrace.cmd(host="h2", command="whoami"))
pump(win, 0.1)
chk("whoami" not in pane.text.get("1.0", "end-1c"), "取消勾选后不再回显")
pane._ai_on.set(True)
pane.destroy()

# ================================================================== 4. 真接线
sec("4. ai_trace 自动开终端 + _ai_exec 落字")

eq(win.tabbar.count(), 1, "清场后仍只有一个页签")
win.ai_trace(aitrace.note("你好", tone="ok"), ensure=True)
pump(win, 0.4)
chk("term" in win.tabbar.tabs, "AI 第一次活动就把终端叫出来了")
t = win._term_live()
chk(t is not None, "终端活着")
win.ai_trace(aitrace.note("第二行"))
pump(win, 0.2)
body = t.text.get("1.0", "end-1c")
chk("AI · 你好" in body, "第一行进去了", body[-200:])
chk("第二行" in body, "第二行也进去了")


class FakeSession:
    """假装成一台已经连上的设备：跑什么就回什么，并像真会话那样触发 tracer。"""

    def __init__(self, box):
        self.box = box

    def run(self, cmd, timeout=30):
        rc, out, err = self.box
        # 真会话在 run() 末尾调 tracer，这里必须照做，否则测不出重复回显
        win._trace_cmd(cmd, rc, out, err, 12)
        return rc, out, err


win._ai_targets = lambda: [("web-01", FakeSession((0, " 10:00 up 3 days\n", "")))]
ok, text = win._ai_exec("uptime", "")
pump(win, 0.6)
body = t.text.get("1.0", "end-1c")
chk(ok is True, "_ai_exec 报告成功")
chk("AI ▶ web-01 $ uptime" in body, "命令在执行前就写进了终端", body[-300:])
chk("10:00 up 3 days" in body, "输出也进去了")
chk("rc=0" in body, "收尾行有 rc")
chk(body.count("▸") == 0, "tracer 没有把同一条命令再喂一遍（▸ 是主程序命令的回显符）",
    str(body.count("▸")))
chk("10:00 up 3 days" in text and "rc=0" in text,
    "_ai_exec 的返回值仍是给模型看的原文")

# 失败也要看得见
win._ai_targets = lambda: [("web-02", FakeSession((1, "", "permission denied")))]
win._ai_exec("cat /etc/shadow", "")
pump(win, 0.6)
body = t.text.get("1.0", "end-1c")
chk("permission denied" in body, "stderr 也进了终端")
chk("rc=1" in body, "非 0 的 rc 有记录")

# 用户自己敲的命令仍走原来的 ▸ 回显（别因为 AI 通道把普通命令吞了）
win._ai_targets = lambda: [("web-01", FakeSession((0, "plain\n", "")))]
win._term_feed("ls -la", 0, "plain\n", "", 9)
pump(win, 0.3)
chk("▸ ls -la" in t.text.get("1.0", "end-1c"), "非 AI 的命令照旧回显")

# 关掉 AI 活动只是不回显，不影响 _ai_exec 的执行
t._ai_on.set(False)
win._ai_targets = lambda: [("web-03", FakeSession((0, "hidden\n", "")))]
ok2, _ = win._ai_exec("echo hidden", "")
pump(win, 0.4)
chk(ok2 is True, "关掉回显后命令照样执行")
chk("AI ▶ web-03" not in t.text.get("1.0", "end-1c"), "关掉后不再写入")

# ================================================================== 收尾
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
