# -*- coding: utf-8 -*-
"""智能运维助手侧栏 + 设置中心的自测。

不连远端、不跑系统命令：
  · 智能体内核（core/agent.py）用固定样本测解析与循环
  · 侧栏与设置中心只建界面、验结构，不触发真实探测（--no-probe）

跑法：
    python _docktest.py
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

RESULTS: list[tuple[bool, str, str]] = []


def chk(ok, name: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, str(detail)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  —— {detail}" if detail else ""))
    return bool(ok)


def pump(win, seconds: float = 0.2) -> None:
    end = time.time() + seconds
    while time.time() < end:
        try:
            win.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.02)


def sec(title: str) -> None:
    print(f"== {title} ==")


def run():
    from core import agent as A

    sec("1. 工具注册表").__str__()
    chk(len(A.TOOL_SPECS) >= 6, "工具数量", len(A.TOOL_SPECS))
    chk(set(A.TOOLS) >= {"exec", "device", "pages", "open_page",
                         "page_action", "page_read", "relay"},
        "关键工具都在", sorted(A.TOOLS))
    chk("device" in A.TOOLS["exec"].opt_keys, "exec 可指定 device")
    cat = A.tool_catalog()
    chk("exec" in cat and "relay" in cat, "目录含 exec 与 relay")

    sec("2. 步骤解析")
    st = A.parse_step(
        '```json\n{"analysis":"先看磁盘","done":false,"calls":'
        '[{"tool":"exec","cmd":"df -h"},{"tool":"open_page","page":"sec"}]}\n```')
    chk(st["analysis"] == "先看磁盘", "analysis 解析", st["analysis"])
    chk([c["tool"] for c in st["calls"]] == ["exec", "open_page"],
        "两个调用都认出来")
    chk(not st["bad"], "没有丢弃项")
    st2 = A.parse_step('{"commands":["df -h"]}')
    chk(st2["calls"] and st2["calls"][0]["tool"] == "exec", "旧 commands 格式兼容")
    st3 = A.parse_step('{"calls":[{"tool":"nope"},{"tool":"exec"}]}')
    chk(len(st3["calls"]) == 0 and len(st3["bad"]) == 2, "坏调用全丢弃",
        st3["bad"])
    st4 = A.parse_step("磁盘满了就删日志")
    chk(st4["calls"] == [] and "磁盘" in st4["analysis"], "无 JSON 当结论")
    st5 = A.parse_step('{"calls":[{"tool":"exec","cmd":"df -h"}]}', ["device"])
    chk(st5["calls"] == [] and "未启用" in (st5["bad"] or [""])[0],
        "未启用的工具拒绝调用", st5["bad"])

    sec("3. 风险判定")
    chk(A.default_risk({"tool": "exec", "args": {"cmd": "df -h"}}) == "read",
        "df 算只读")
    chk(A.default_risk({"tool": "exec",
                        "args": {"cmd": "rm -rf /tmp/x"}}) == "danger",
        "rm -rf 算高危")
    chk(A.default_risk({"tool": "relay", "args": {}}) == "write",
        "relay 算改动")

    sec("4. 智能体配置")
    cfg = A.AgentConfig(HERE / "_test_data" / "agent_probe.json")
    chk(cfg.name == A.AGENT_NAME, "默认名字", cfg.name)
    chk(set(cfg.tools) == set(A.DEFAULT_TOOLS), "默认工具全开")
    chk(cfg.mode == "auto" and cfg.max_steps == 8, "默认模式与步数")
    cfg.data["name"] = "   "
    cfg.data["max_steps"] = 999
    cfg.data["tools"] = ["exec", "不存在"]
    cfg._clean()
    chk(cfg.name == A.AGENT_NAME, "空名字回落到默认")
    chk(cfg.max_steps == 30, "步数被夹到 30", cfg.max_steps)
    chk(cfg.tools == ["exec"], "不存在的工具被剔掉", cfg.tools)
    cfg.data["mode"] = "whatever"
    cfg._clean()
    chk(cfg.mode == "auto", "坏模式回落 auto")

    sec("5. 提示词拼装")
    a = A.Agent(cfg, llm=None)
    sysmsg = a.system_prompt("设备：web-1", "技能正文")
    chk("智能运维助手" in sysmsg, "名字进了提示词")
    chk("{tools}" not in sysmsg and "{context}" not in sysmsg, "占位符都被替换")
    chk("web-1" in sysmsg and "技能正文" in sysmsg, "上下文与技能都拼上")

    # ---- 界面
    import tkinter as tk
    sys.argv = ["app.py", "--no-probe"]
    import app as appmod

    # 打开状态是落盘的，先复位成「关」，否则这台机器上次开着会干扰断言
    import json as _json
    _cfg = appmod.DATA_DIR / "agent.json"
    if _cfg.is_file():
        _d = _json.loads(_cfg.read_text(encoding="utf-8"))
        _d["dock_open"] = False
        _cfg.write_text(_json.dumps(_d, ensure_ascii=False), encoding="utf-8")

    sec("6. 主窗口骨架")
    win = appmod.ToolboxApp()
    win.geometry("+40+40")   # 不 withdraw：按钮坐标要真实布局后才算得出
    pump(win, 0.6)
    # 模型管理在 v1.0.9 并进了「设置中心」的页签，不再是顶层页签，
    # 所以是 15 而不是原来的 16。
    chk(len(win._pages) == 15, "十五个选项卡", len(win._pages))
    chk("ai" not in win._pages, "AI 不再是一个选项卡")
    chk(hasattr(win, "btn_ai_dock"), "顶部有助手按钮")
    chk(hasattr(win, "btn_settings"), "顶部有设置按钮")
    chk(not hasattr(win, "btn_lock"), "独立锁按钮已撤")
    chk(not hasattr(win, "btn_theme"), "独立主题按钮已撤")
    texts = [getattr(w, "text", "") for w in win.header_bar.winfo_children()
             for w in w.winfo_children() if isinstance(w, appmod.U.RoundButton)]
    chk("智能运维助手" in texts and "设置" in texts, "按钮文案", texts)
    # 位置：助手的 x 必须大于终端，设置最大
    def rx(w):
        return w.winfo_rootx()
    chk(rx(win.btn_ai_dock) > rx(win.btn_term), "助手在终端右边",
        f"{rx(win.btn_ai_dock)} > {rx(win.btn_term)}")
    chk(rx(win.btn_settings) >= rx(win.btn_ai_dock), "设置在助手右边")

    sec("7. 侧栏开关")
    chk(win.ai_dock is None, "初始不建侧栏")
    win.ai_dock_toggle()
    pump(win, 0.3)
    chk(win.ai_dock is not None, "点一下建出来")
    chk(win._ai_dock_on, "记录为打开")
    panes = [str(p) for p in win.paned.panes()]
    chk(str(win.ai_dock) in panes, "侧栏挂进分隔器")
    chk(getattr(win.btn_ai_dock, "text", "") == "收起助手", "按钮文案切换",
        getattr(win.btn_ai_dock, "text", ""))
    chk(win.agent_cfg.data["dock_open"] is True, "打开状态落盘")
    win.ai_dock_toggle()
    pump(win, 0.2)
    panes = [str(p) for p in win.paned.panes()]
    chk(str(win.ai_dock) not in panes, "再点一下收起")
    chk(win._ai_dock_on is False, "记录为关闭")

    sec("8. 会话卡片")
    win.ai_dock_open()
    pump(win, 0.3)
    before = len(win._ai_flow.winfo_children())
    win._ai_bubble("把安全体检跑一遍")
    win._ai_reply("先看下现状", 1)
    rec = win._ai_tool_card({"tool": "exec", "risk": "read",
                             "args": {"cmd": "uptime"}, "why": "看负载"})
    win._ai_tool_result(rec, True, "load average: 0.1 0.2 0.3")
    pump(win, 0.2)
    after = len(win._ai_flow.winfo_children())
    chk(after > before, "卡片都加进去了", f"{before} → {after}")
    chk(rec["full"].startswith("load average"), "结果留存可复制")
    win._ai_tool_toggle(rec)
    chk(rec["shown"] is True, "展开状态可切换")

    sec("9. 主机范围")
    chk(win._ai_scope_all is False and win._ai_scope_sids == [],
        "默认不是全部主机")
    targets = win._ai_targets()
    chk(len(targets) == 1, "默认只有一台", [t[0] for t in targets])
    win._ai_scope_all = True
    targets = win._ai_targets()
    chk(len(targets) == 1 and targets[0][0] == "本机", "全部主机含本机",
        [t[0] for t in targets])
    win._ai_scope_all = False
    win._ai_scope_sids = ["no-such-sid"]
    chk(len(win._ai_targets()) == 1, "坏 sid 回落到当前设备")
    win._ai_scope_sids = []

    sec("10. 页面工具")
    txt = win._ai_pages_text()
    chk("sec" in txt and "scan" in txt, "页面清单含动作")
    ok, msg = win._ai_open_page("sec")
    chk(ok and win.nav.current == "sec", "能切页", msg)
    ok2, msg2 = win._ai_open_page("nope")
    chk(not ok2 and "没有这个页面" in msg2, "坏页名有明确提示")
    ok3, msg3 = win._ai_page_action("sec", "nope")
    chk(not ok3 and "没有动作" in msg3, "坏动作有明确提示")
    ok4, msg4 = win._ai_page_read("cron")
    chk(isinstance(ok4, bool) and isinstance(msg4, str), "读页面不炸", msg4[:30])
    ok5, msg5 = win._ai_exec("", "")
    chk(not ok5, "空命令被挡", msg5)
    ok6, msg6 = win._ai_relay("", "relative", "", "/etc/x")
    chk(not ok6 and "绝对路径" in msg6, "非绝对路径被挡", msg6)

    sec("11. 设置中心")
    win.open_settings()
    pump(win, 0.4)
    # 现在默认落在页签里，不是弹窗（弹窗要按页签上的 ⧉ 才出来）
    from pages import settings_center as SC
    pane = win._settings_reg().get(win.SETTINGS_TAB)
    chk(pane is not None and pane.winfo_exists(), "设置嵌成页签")
    chk(getattr(win, "_settings_win", None) is None, "默认不开弹窗")
    for key, label in SC.SECTIONS:
        try:
            pane.show(key)
            pump(win, 0.12)
            ok = True
            err = ""
        except Exception as e:  # noqa: BLE001
            ok, err = False, f"{type(e).__name__}: {e}"
        chk(ok, f"分区「{label}」能渲染", err)
    chk(pane._nav_btns["safe"].cget("text") == "安全", "安全分区不叫「账号」")
    win.settings_popout()
    pump(win, 0.4)
    dlg = win._settings_win
    chk(dlg is not None and dlg.winfo_exists(), "设置能拉成独立窗口")
    win.settings_embed(dlg)
    pump(win, 0.4)
    chk(win._settings_win is None, "设置能嵌回主界面")

    sec("12. 外观切换")
    base = win._accent_hex()
    chk(base != "", "默认配色有值", base)
    win._set_accent("宝塔绿")
    pump(win, 0.2)
    chk(appmod.U.T.brand == "#2E9E5B", "配色生效", appmod.U.T.brand)
    win._set_theme("dark")
    chk(appmod.U.T.name == "dark", "切深色")
    win._set_theme("light")
    chk(appmod.U.T.name == "light", "切浅色")
    win._restore_look()
    chk(appmod.U.T.brand == "#3A66AC", "恢复默认配色", appmod.U.T.brand)

    sec("13. 安全分区复用锁")
    chk(hasattr(win, "lock"), "锁管理器在")
    chk(hasattr(win, "_open_lock_window"), "锁设置入口在")
    dlg.show("safe")
    pump(dlg, 0.15)
    chk(True, "安全页渲染不炸")

    dlg.destroy()
    pump(win, 0.1)
    win.destroy()


def main() -> int:
    try:
        run()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        RESULTS.append((False, "未捕获异常", "见上方堆栈"))
    bad = [r for r in RESULTS if not r[0]]
    print(f"\n合计 {len(RESULTS)} 项：通过 {len(RESULTS) - len(bad)}，失败 {len(bad)}")
    for _, name, detail in bad:
        print(f"  FAIL {name}  {detail}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
