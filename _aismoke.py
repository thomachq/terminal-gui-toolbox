# -*- coding: utf-8 -*-
"""AI 运维页与自驱循环自测（不联网、不碰真设备）。

    python _aismoke.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import aiops  # noqa: E402

PASS = 0
FAIL = 0
FAILED: list[str] = []


def chk(ok, name, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  [FAIL] {name}  {detail}")


# ---------------------------------------------------------------- 假引擎
class FakeEngine:
    """按脚本逐轮返回，顺手记下每次收到的 messages。"""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.seen: list[list[dict]] = []
        self.i = 0

    def chat(self, messages):
        self.seen.append(messages)
        if self.i < len(self.rounds):
            body = self.rounds[self.i]
        else:
            body = {"analysis": "没有更多了", "done": True, "commands": []}
        self.i += 1
        return json.dumps(body, ensure_ascii=False), {"total": 100}


def install(rounds):
    eng = FakeEngine(rounds)
    aiops.make_engine = lambda cfg: eng  # noqa: SLF001
    return eng


# ---------------------------------------------------------------- A. 解析
def test_parse():
    print("\n[A] 方案解析")

    p = aiops.parse_plan('{"analysis":"看端口","done":true,"commands":[]}')
    chk(p["done"] is True, "done=true 能解出来", p)
    chk(p["commands"] == [], "done 时没有命令", p["commands"])

    p = aiops.parse_plan('{"analysis":"x","commands":[{"cmd":"ls"}]}')
    chk(p["done"] is False, "缺 done 默认 False", p["done"])

    p = aiops.parse_plan("这不是 JSON，就是一句话")
    chk(p["done"] is False and p["commands"] == [],
        "没有 JSON 时不猜命令", p)

    p = aiops.parse_plan(
        '{"analysis":"x","done":false,"commands":'
        '[{"cmd":"rm -rf /tmp/x","why":"清","risk":"read"}]}')
    chk(p["commands"][0]["risk"] == "danger",
        "模型标 read 但规则判 danger → 取 danger", p["commands"][0])

    p = aiops.parse_plan('```json\n{"analysis":"y","done":false,'
                         '"commands":[{"cmd":"uptime","risk":"read"}]}\n```')
    chk(len(p["commands"]) == 1 and p["commands"][0]["cmd"] == "uptime",
        "代码围栏能剥", p)


# ---------------------------------------------------------------- B. 循环
def test_loop():
    print("\n[B] 自驱循环")

    rounds = [
        {"analysis": "先看监听", "done": False,
         "commands": [{"cmd": "ss -lntp", "why": "查端口", "risk": "read"}]},
        {"analysis": "看到 80 了，收工", "done": True, "commands": []},
    ]
    eng = install(rounds)
    events = []
    ran_cmds = []

    def runner(cmd):
        ran_cmds.append(cmd)
        return 0, "LISTEN 0 128 0.0.0.0:80", ""

    asst = aiops.Assistant(_cfg())
    res = asst.run_task("找出 80 端口的服务", runner,
                        device={"name": "nas", "host_kind": "unix"},
                        mode="auto",
                        on_event=lambda k, p: events.append((k, p)))

    chk(res["ok"] and res["steps"] == 2, "两轮结束", res)
    chk(ran_cmds == ["ss -lntp"], "执行了计划里那条", ran_cmds)
    kinds = [k for k, _ in events]
    chk("plan" in kinds and "run" in kinds and "result" in kinds,
        "事件齐全", kinds)
    chk("finish" in kinds, "有结束事件", kinds)

    # 第二轮必须看到第一轮的真实输出
    second = eng.seen[1]
    blob = json.dumps(second, ensure_ascii=False)
    chk("0.0.0.0:80" in blob, "上一轮输出喂回了模型", blob[-200:])
    chk("第 1 步" not in blob, "喂回的是输出而不是废话", "")

    # read 在 auto 下不问
    confirms = []
    install(rounds)
    asst = aiops.Assistant(_cfg())
    asst.run_task("x", lambda c: (0, "ok", ""), mode="confirm",
                  confirm=lambda cmds: confirms.append(cmds) or True,
                  on_event=lambda *a: None)
    chk(confirms == [], "只读命令不弹确认", confirms)

    # write 在 confirm 模式下要问
    install([{"analysis": "重启", "done": False,
              "commands": [{"cmd": "systemctl restart nginx",
                            "why": "重启", "risk": "write"}]},
             {"analysis": "完", "done": True, "commands": []}])
    asks = []
    asst = aiops.Assistant(_cfg())
    ran = []
    asst.run_task("重启 nginx", lambda c: ran.append(c) or (0, "", ""),
                  mode="confirm", confirm=lambda cmds: asks.append(cmds) or True)
    chk(len(asks) == 1 and asks[0][0]["cmd"].startswith("systemctl"),
        "confirm 模式对写操作弹确认", asks)
    chk(ran == ["systemctl restart nginx"], "确认后才执行", ran)

    # 拒绝就不执行
    install([{"analysis": "重启", "done": False,
              "commands": [{"cmd": "systemctl restart nginx", "risk": "write"}]},
             {"analysis": "完", "done": True, "commands": []}])
    ran = []
    res = aiops.Assistant(_cfg()).run_task(
        "x", lambda c: ran.append(c) or (0, "", ""), mode="confirm",
        confirm=lambda cmds: False)
    chk(ran == [], "拒绝了就不执行", ran)
    chk(res["refused"] and "nginx" in res["refused"][0],
        "拒绝记在 refused 里", res["refused"])

    # danger 在任何模式下都要问（auto 也问）
    install([{"analysis": "删", "done": False,
              "commands": [{"cmd": "rm -rf /var/log/x", "risk": "read"}]},
             {"analysis": "完", "done": True, "commands": []}])
    asked = []
    aiops.Assistant(_cfg()).run_task(
        "x", lambda c: (0, "", ""), mode="auto",
        confirm=lambda cmds: asked.append(cmds) or False)
    chk(len(asked) == 1, "auto 模式下 danger 也必问", asked)

    # readonly 直接跳过写操作，不问也不跑
    install([{"analysis": "改", "done": False,
              "commands": [{"cmd": "sed -i 's/a/b/' /etc/x", "risk": "write"},
                           {"cmd": "cat /etc/x", "risk": "read"}]},
             {"analysis": "完", "done": True, "commands": []}])
    ran, asked = [], []
    res = aiops.Assistant(_cfg()).run_task(
        "x", lambda c: ran.append(c) or (0, "", ""), mode="readonly",
        confirm=lambda cmds: asked.append(cmds) or True)
    chk(ran == ["cat /etc/x"], "只读模式只跑只读", ran)
    chk(asked == [], "只读模式不弹确认", asked)
    chk(res["skipped"] and "sed" in res["skipped"][0],
        "跳过记在 skipped 里", res["skipped"])

    # runner 抛异常不许整条链断
    install([{"analysis": "两条", "done": False,
              "commands": [{"cmd": "hostname", "risk": "read"},
                           {"cmd": "uptime", "risk": "read"}]},
             {"analysis": "完", "done": True, "commands": []}])
    got = []

    def bad_runner(cmd):
        got.append(cmd)
        if cmd == "hostname":
            raise RuntimeError("通道断了")
        return 0, "up 3 days", ""

    res = aiops.Assistant(_cfg()).run_task("x", bad_runner, mode="auto")
    chk(got == ["hostname", "uptime"], "前一条炸了后面继续跑", got)
    chk(res["ok"], "异常不影响整体返回", res["ok"])

    # 步数上限
    def loop_round(n):
        return [{"analysis": f"第{n}轮", "done": False,
                 "commands": [{"cmd": f"echo {n}", "risk": "read"}]}] * n

    install(loop_round(20))
    res = aiops.Assistant(_cfg()).run_task(
        "x", lambda c: (0, "ok", ""), mode="auto", max_steps=3)
    chk(res["steps"] == 3, "max_steps 生效", res["steps"])

    # 停止
    install(loop_round(20))
    flag = {"v": False}
    ticks = {"n": 0}

    def runner2(cmd):
        ticks["n"] += 1
        flag["v"] = True
        return 0, "ok", ""

    res = aiops.Assistant(_cfg()).run_task(
        "x", runner2, mode="auto", should_stop=lambda: flag["v"], max_steps=5)
    chk(res.get("stopped") is True, "停止标志能让循环退出", res)
    chk(ticks["n"] == 1, "第一条跑完就停", ticks["n"])

    # 模型给空命令就收工，不空转
    install([{"analysis": "不知道", "done": False, "commands": []}])
    eng = aiops.make_engine(None)
    res = aiops.Assistant(_cfg()).run_task("x", lambda c: (0, "", ""))
    chk(res["steps"] == 1 and eng.i == 1, "空命令不空转", (res["steps"], eng.i))


def _cfg():
    """测试用配置：不落盘、不碰 crypto_store。"""
    c = aiops.AIConfig(Path("./data/_ai_test.json"))
    c.data["provider"] = "deepseek"
    c.get_key = lambda: "test-key"  # noqa: SLF001
    return c


# ---------------------------------------------------------------- C. 侧栏
def test_dock():
    """助手长什么样。

    v1.0.4 起「AI 运维」不再是导航里的一个页签，而是右侧常驻侧栏
    （换页签不打断对话）。所以这里测侧栏，不测那个已经废弃的独立页面。
    """
    print("\n[C] 助手侧栏与多会话")

    import tkinter as tk  # noqa: F401
    import app as APP

    a = APP.ToolboxApp()
    try:
        need = {"hosts", "proxy", "cred", "script", "log", "tools", "sys",
                "files", "routes", "sec", "docker", "web", "db", "cron",
                "vpn", "api"}
        chk(need.issubset(set(a._pages)), "导航页面都在",
            f"{len(a._pages)} 个：{sorted(set(a._pages) - need)} 多出")
        chk("ai" not in a._pages, "AI 不再占一个导航页签",
            list(a._pages))

        a.ai_dock_open()
        for _ in range(30):
            a.update()
            time.sleep(0.01)
        chk(getattr(a, "ai_dock", None) is not None, "侧栏已构建")
        chk(a.ai_dock.winfo_width() > 100, "侧栏有宽度",
            a.ai_dock.winfo_width())
        chk(hasattr(a, "_ai_input") and hasattr(a, "btn_ai_send"),
            "输入区与发送按钮都在")
        chk(abs(a.ai_dock.winfo_width() - APP_DOCK_W(a)) <= APP_DOCK_SLACK(a),
            "侧栏宽度落在设定值附近",
            f"{a.ai_dock.winfo_width()} vs {APP_DOCK_W(a)}")

        # 空输入不启动
        a._ai_input.clear()
        a.ai_dock_send()
        chk(not a.ai_busy, "空输入不启动", a.ai_busy)

        # 多会话页签
        n0 = len(a._ai_sess_order)
        s2 = a._ai_tab_new()
        for _ in range(10):
            a.update()
            time.sleep(0.01)
        chk(len(a._ai_sess_order) == n0 + 1, "＋ 能开新会话页签",
            len(a._ai_sess_order))
        chk(a._ai_sess.id == s2.id, "新页签成为当前会话")
        a._ai_tab_close(s2.id)
        for _ in range(10):
            a.update()
            time.sleep(0.01)
        chk(s2.id not in a._ai_open, "页签能关掉")

        a.ai_dock_close()
        for _ in range(10):
            a.update()
            time.sleep(0.01)
        chk(not getattr(a, "_ai_dock_on", False), "侧栏能收起")
    finally:
        a.destroy()


def APP_DOCK_W(a):
    from pages.ai_dock import DOCK_W_DEFAULT
    import wbtui as U
    want = int(a.agent_cfg.data.get("dock_width") or DOCK_W_DEFAULT)
    return U.px(want)


def APP_DOCK_SLACK(a):
    import wbtui as U
    return U.px(80)


# ---------------------------------------------------------------- D. 技能
def test_skills():
    print("\n[D] 技能库")

    import tkinter as tk  # noqa: F401
    import app as APP
    from core import ai_skills

    a = APP.ToolboxApp()
    try:
        store = a.ai_skill_store.all()
        chk(len(store) == len(ai_skills.BUILTIN) if hasattr(
            ai_skills, "BUILTIN") else len(store) >= 8,
            "技能库里有内置技能", len(store))
        slugs = [s.slug for s in store]
        chk("server-performance-diagnosis" in slugs, "服务器性能技能在", slugs[:3])

        # 自动判断能按问题命中
        a.ai_dock_open()
        for _ in range(20):
            a.update()
            time.sleep(0.01)
        sk, why = a._ai_pick_skill("磁盘满了，看看哪个目录占空间")
        chk(sk is not None and sk.slug == "server-performance-diagnosis",
            "自动挑对技能", (sk.slug if sk else None, why))
        chk("命中" in why, "给出命中理由", why)

        # 手动指定优先于自动
        a._ai_pick_skill_now("database-diagnosis")
        sk2, why2 = a._ai_pick_skill("磁盘满了，看看哪个目录占空间")
        chk(sk2 is not None and sk2.slug == "database-diagnosis",
            "手动选的技能盖过自动判断",
            (sk2.slug if sk2 else None, why2))
        a._ai_pick_skill_now("")
        chk(not a._ai_skill_now, "能切回自动", a._ai_skill_now)

        # 技能浮层能画出来
        top = a._ai_skill_menu()
        chk(top is not None and top.winfo_exists(), "技能浮层能打开")
        top.destroy()
    finally:
        a.destroy()


def main() -> int:
    try:
        test_parse()
        test_loop()
        test_dock()
        test_skills()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2
    print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
    if FAILED:
        print("失败项：" + "、".join(FAILED))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
