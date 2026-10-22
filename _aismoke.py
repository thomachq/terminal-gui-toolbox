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


# ---------------------------------------------------------------- C. 页面
def test_page():
    print("\n[C] AI 运维页")

    import tkinter as tk  # noqa: F401
    import app as APP
    from ui import forms

    a = APP.ToolboxApp()
    try:
        chk(len(a._pages) == 11, "十一个页面都已构建", len(a._pages))
        chk("ai" in a._pages, "AI 运维页已注册", list(a._pages))
        a.nav.select("ai")
        for _ in range(20):
            a.update()
            a.after(10)

        chk(hasattr(a, "tb_ai_chat"), "对话区已建", "")
        chk("缺 API Key" in str(a.badge_ai.text)
            or "已就绪" in str(a.badge_ai.text),
            "状态胶囊有文字", a.badge_ai.text)

        # 空输入不发送
        a.tb_ai_input.clear()
        a.ai_send()
        chk(not a.ai_busy, "空输入不启动", a.ai_busy)

        # 没 Key 不发送
        a.ai_cfg.get_key = lambda: ""  # noqa: SLF001
        a.ai_cfg.data["provider"] = "deepseek"
        a.tb_ai_input.set_text("看看磁盘")
        a.ai_send()
        chk(not a.ai_busy, "没 Key 不启动", a.ai_busy)
        chk(a.tb_ai_input.get("1.0", "end-1c").strip() == "看看磁盘",
            "没 Key 时不清空输入", a.tb_ai_input.get("1.0", "end-1c"))

        # 有 Key + 假引擎：完整跑一轮
        a.ai_cfg.get_key = lambda: "test-key"  # noqa: SLF001
        install([{"analysis": "先看磁盘", "done": False,
                  "commands": [{"cmd": "df -h", "why": "看用量", "risk": "read"}]},
                 {"analysis": "看完了", "done": True, "commands": []}])
        a._ai_clear_log()
        a.tb_ai_input.set_text("看看磁盘")
        a.ai_send()
        for _ in range(120):
            a.update()
            time.sleep(0.02)
            if not a.ai_busy:
                break
        text = a.tb_ai_chat.get("1.0", "end-1c")
        chk("你 > 看看磁盘" in text, "对话区有提问", text[:80])
        chk("df -h" in text, "对话区有命令", text[:200])
        chk("先看磁盘" in text, "对话区有 AI 判断", text[:200])
        chk("rc=0" in text, "对话区有返回码", text[-300:])
        chk(not a.ai_busy, "跑完自动解锁", a.ai_busy)
        chk(a.btn_ai_send.text == "发送", "按钮文字复原", a.btn_ai_send.text)

        # 确认弹窗：别人（工作线程）调，主线程答
        box, ev = {}, threading.Event()
        got = {}

        def fake_confirm(*_a, **_k):
            got["called"] = True
            return True

        was = forms.confirm
        forms.confirm = fake_confirm
        try:
            t = threading.Thread(target=lambda: got.update(
                ok=a._ai_confirm_blocking([{"cmd": "systemctl restart nginx",
                                            "risk": "write", "why": "重启"}])),
                daemon=True)
            t.start()
            for _ in range(60):
                a.update()
                time.sleep(0.01)
                if got:
                    break
            t.join(timeout=2)
        finally:
            forms.confirm = was
        chk(got.get("called"), "确认经主线程弹窗", got)
        chk(got.get("ok") is True, "确认结果回给工作线程", got)
        chk(box == {} and not ev.is_set(), "没有泄漏内部对象", (box, ev.is_set()))

        # 停止按钮
        a.ai_busy = True
        a._ai_stop.clear()
        a.ai_stop()
        chk(a._ai_stop.is_set(), "停止按钮置位", a._ai_stop.is_set())
        a.ai_busy = False

        # 模式下拉存盘
        a.v_ai_mode.set("只读 · 只执行查询，不改任何东西")
        a._ai_mode_changed()
        chk(a.ai_cfg.data.get("exec_mode") == "readonly",
            "切模式会存盘", a.ai_cfg.data.get("exec_mode"))
        a.ai_cfg.data["exec_mode"] = "auto"
        a.ai_cfg.save()
    finally:
        a.destroy()


# ---------------------------------------------------------------- D. 技能
def test_skills():
    print("\n[D] 技能库（宝塔终端 8 个内置）")
    import tkinter as tk  # noqa: F401
    import app as APP
    from core import ai_skills
    from pages.ai_page import AUTO_LABEL, SkillDialog

    a = APP.ToolboxApp()
    try:
        a.nav.select("ai")
        for _ in range(10):
            a.update()

        vals = list(a.cb_ai_skill["values"])
        chk(vals and vals[0] == AUTO_LABEL, "下拉第一项是自动判断", vals[:2])
        chk(len(vals) == 9, "自动判断 + 8 个技能", len(vals))
        chk(any("服务器资源与性能检查" in v for v in vals),
            "能看到内置技能名", vals[:3])

        # 手动选技能 → 存盘 → 再打开还能记住
        store_skills = a.ai_skill_store.all()
        chk(len(store_skills) == 8, "技能库里有 8 个", len(store_skills))
        pick_lab = next(v for v in vals if "数据库诊断" in v)
        a.v_ai_skill.set(pick_lab)
        a._ai_skill_changed()
        chk(a.ai_cfg.data.get("skill") == "database-diagnosis",
            "选技能会存盘", a.ai_cfg.data.get("skill"))
        a._ai_refresh_skills()
        chk(a.v_ai_skill.get() == pick_lab, "刷新后仍选中", a.v_ai_skill.get())

        # 自动判断能按问题命中
        a.v_ai_skill.set(AUTO_LABEL)
        a._ai_skill_changed()
        sk, why = a._ai_pick_skill("磁盘满了，看看哪个目录占空间")
        chk(sk is not None and sk.slug == "server-performance-diagnosis",
            "自动挑对技能", (sk.slug if sk else None, why))
        chk("命中" in why, "给出命中理由", why)

        # 技能库窗口
        dlg = SkillDialog(a, a)
        try:
            a.update()
            chk(dlg.lst.size() == 8, "技能库窗口列出 8 个", dlg.lst.size())
            dlg.lst.selection_clear(0, "end")
            dlg.lst.selection_set(0)
            dlg._preview()
            a.update()
            body = dlg.tb.get("1.0", "end-1c")
            chk("探测" in body and "修复" in body, "正文预览有 SOP", body[:80])
            dlg._use()
            a.update()
            chk(a.ai_cfg.data.get("skill") in
                [s.slug for s in store_skills], "窗口里能选用技能",
                a.ai_cfg.data.get("skill"))
        finally:
            dlg.destroy()

        # 发送时把技能带进对话区
        a.ai_cfg.get_key = lambda: "test-key"  # noqa: SLF001
        a.ai_cfg.data["provider"] = "deepseek"
        a._ai_refresh_skills()
        a.v_ai_skill.set(AUTO_LABEL)
        a._ai_skill_changed()
        install([{"analysis": "先看磁盘", "done": False,
                  "commands": [{"cmd": "df -h", "why": "看用量", "risk": "read"}]},
                 {"analysis": "完", "done": True, "commands": []}])
        a._ai_clear_log()
        a.tb_ai_input.set_text("磁盘满了，帮我看看")
        a.ai_send()
        for _ in range(120):
            a.update()
            time.sleep(0.02)
            if not a.ai_busy:
                break
        text = a.tb_ai_chat.get("1.0", "end-1c")
        chk("· 技能：" in text, "对话区标出用了哪个技能", text[:200])
        chk("服务器资源与性能检查" in text, "技能名进了对话区", text[:200])
        chk("df -h" in text, "照常执行命令", text[:300])
        a.ai_cfg.data["skill"] = ""
        a.ai_cfg.save()
    finally:
        a.destroy()


def main() -> int:
    try:
        test_parse()
        test_loop()
        test_page()
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
