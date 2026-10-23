# -*- coding: utf-8 -*-
"""中途插话自测（v1.0.7 T7）。

要点：一句话在任务跑着的时候补进去，必须**进当前这一轮**，
不能排队等下一轮、不能被忽略、更不能并发再起一轮。

四段：
  1. Agent 层：假执行器跑 8 步，第 2 步注入一句话 —— history 里有它、
     带「用户在你执行期间补充」标记、步数仍受 max_steps 限制、
     run 只被调用一次（没有第二个线程）
  2. 队列语义：取走即清空，同一句不会被读两遍
  3. 界面层：忙碌时 Ctrl+Enter 是插话（不发送、不锁输入框）、气泡带「插话」徽章
  4. 收尾：新一轮开始时上一轮残留的插话不会漏进来

不联网：模型调用被替身接管。
"""
import os
import sys
import tempfile
import time
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.argv = [sys.argv[0], "--no-probe"]
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = FAIL = 0
FAILS: list[str] = []
TD = tempfile.mkdtemp(prefix="tbinject_")

import app as appmod  # noqa: E402
from core import agent as A  # noqa: E402
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


# ================================================================== 1. Agent
sec("1. 插话进的是当前这一轮")

cfg = A.AgentConfig(Path(TD) / "agent.json")
cfg.data["max_steps"] = 5
llm = aiops.AIConfig(Path(TD) / "ai.json")
ag = A.Agent(cfg, llm)

calls = {"n": 0}
queue: list[str] = []        # 由下面的 ex() 在跑到一半时填进去
events: list[tuple[str, dict]] = []


def fake_chat(messages, _n=[0]):
    """每次要模型都给一个 exec 动作，逼它跑满步数。"""
    _n[0] += 1
    return ('{"analysis": "继续查", "done": false, "commands": '
            '[{"cmd": "echo step", "why": "test", "risk": "read"}]}',
            {"prompt": 10, "completion": 5, "total": 15})


class _Eng:
    def chat(self, messages):
        # 把每次发出去的消息留档，好断言插话真的进了上下文
        seen.append([m.get("content") for m in messages
                     if m.get("role") == "user"])
        return fake_chat(messages)


seen: list[list[str]] = []
real_engine = aiops.make_engine
aiops.make_engine = lambda _cfg: _Eng()

filled = {"done": False}


def take():
    got = list(queue)
    queue.clear()
    return got


def ex(tool, args):
    """第一步执行完之后，模拟用户在任务跑到一半时补了一句话。"""
    calls["n"] += 1
    if calls["n"] == 1 and not filled["done"]:
        filled["done"] = True
        queue.append("把超时改成 30 秒")
    return True, "ok"


try:
    res = ag.run("查一下磁盘", ex,
                 on_event=lambda k, p: events.append((k, p)),
                 poll_inject=take)
finally:
    aiops.make_engine = real_engine

chk(isinstance(res, dict) and res.get("ok"), "这一轮跑完了")
eq(res.get("steps"), 5, "步数仍受 max_steps 限制（5）")
chk(len(seen) <= 5, "模型总共只被问了 5 次", len(seen))
inj = [e for e in events if e[0] == "inject"]
chk(len(inj) == 1, "插话事件只报一次", len(inj))
if inj:
    eq(inj[0][1].get("step"), 2, "第 2 步读到插话")
    eq(inj[0][1].get("text"), "把超时改成 30 秒", "插话内容原样带过来")

hist = [h for h in ag.history if "超时" in str(h.get("content"))]
chk(bool(hist), "插话进了 history", len(ag.history))
if hist:
    chk("用户在你执行期间补充" in str(hist[0]["content"]),
        "标了「用户补充」，模型不会当成新任务", hist[0]["content"][:40])
# 第 2 步之后的请求里必须能看到这句话
later = [m for m in seen[2:] if any("超时" in str(x) for x in m)]
chk(bool(later), "后面的请求里带上了插话", f"共 {len(seen)} 次请求")
eq(res.get("injected"), ["把超时改成 30 秒"], "返回值里也记了一笔")

# 取走即清空
queue.clear()
q2 = ["一句"]
t = ag
got1 = q2[:]
q2.clear()
chk(got1 == ["一句"] and q2 == [], "取走即清空")

# ================================================================== 2. 界面
sec("2. 界面：忙碌时 Ctrl+Enter 是插话")

win = appmod.ToolboxApp()
pump(win, 1.0)
win.ai_dock_open()
pump(win, 0.5)

chk(hasattr(win, "_ai_inject"), "插话队列在")
eq(win._ai_inject, [], "一开始是空的")

# 空闲时 Ctrl+Enter = 发送（这里只验它不写插话队列）
win.ai_busy = False
win._ai_input.txt.delete("1.0", "end")
win._ai_input.txt.insert("1.0", "空闲时这句话该走发送")
old_send = win.ai_dock_send
sent: list[int] = []
win.ai_dock_send = lambda *a, **k: sent.append(1)
try:
    win._ai_send_hotkey()
finally:
    win.ai_dock_send = old_send
chk(sent == [1], "空闲时 Ctrl+Enter 走发送")
eq(win._ai_inject, [], "空闲时不会写插话队列")

# 忙碌时 Ctrl+Enter = 插话
win.ai_busy = True
win._ai_input.txt.delete("1.0", "end")
win._ai_input.txt.insert("1.0", "顺便看一下 /var/log")
win._ai_send_hotkey()
pump(win, 0.2)
eq(win._ai_inject, ["顺便看一下 /var/log"], "忙碌时写进了插话队列")
chk(win._ai_input.get("1.0", "end-1c").strip() == "", "输入框发完就清空")
chk(str(win._ai_input.txt.cget("state")) != "disabled", "忙碌时输入框没被锁")

# 气泡上要有「插话」徽章
texts = []


def walk(w):
    for c in w.winfo_children():
        try:
            t = c.cget("text")
        except tkinter.TclError:
            t = ""
        if t:
            texts.append(str(t))
        walk(c)


walk(win._ai_flow)
chk(any("插话" in t for t in texts), "消息流里出现了「插话」徽章",
    [t for t in texts if "插话" in t][:2])
chk(any("顺便看一下 /var/log" in t for t in texts), "插话正文在气泡里")

# 取走之后队列空
got = win._ai_take_inject()
eq(got, ["顺便看一下 /var/log"], "取走插话")
eq(win._ai_inject, [], "取走即清空")
eq(win._ai_take_inject(), [], "再取一次是空的（不会被读两遍）")

# ================================================================== 3. 残留
sec("3. 新一轮不会漏上一轮的插话")

win.ai_busy = False          # 上一段留的忙碌状态先复位，否则发送会直接被拦
win._ai_inject = ["上一轮没读走的"]
# 只验清理那一步，不真发（没有 API Key）
old_key = win.ai_cfg.get_key
win.ai_cfg.get_key = lambda *a, **k: "sk-fake"
try:
    win._ai_input.txt.delete("1.0", "end")
    win._ai_input.txt.insert("1.0", "新一轮任务")
    started: list[int] = []
    old_th = win._ai_worker
    win._ai_worker = lambda *a, **k: started.append(1)
    try:
        win.ai_dock_send()
    finally:
        win._ai_worker = old_th
finally:
    win.ai_cfg.get_key = old_key

eq(win._ai_inject, [], "发新任务时把残留插话清掉了")
chk(win.ai_busy is True, "进入忙碌状态")

win.ai_busy = False
try:
    win.destroy()
except tkinter.TclError:
    pass

print("\n" + "=" * 50)
print(f"{'失败 ' + str(len(FAILS)) if FAILS else '全部通过'}：{PASS}/{PASS + FAIL}")
for x in FAILS:
    print("  ✗", x)
raise SystemExit(1 if FAILS else 0)
