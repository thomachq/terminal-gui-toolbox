# -*- coding: utf-8 -*-
"""设置中心「助手」区的四个内嵌页签自测。

以前这里是三个按钮，各弹一个 Toplevel：关掉一个才能看另一个。
现在必须做到：
  · 四个页签（智能体设置 / 模型设置 / 技能库 / MCP 库）都在同一页里；
  · 切换不再新开任何窗口（用 Toplevel 数量断言，别只看「没报错」）；
  · 每个面板都能真的建起来（建不起来会留一块红字）。
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
TD = tempfile.mkdtemp(prefix="tbtabs_")

import app as appmod  # noqa: E402

appmod.APP_DIR = Path(TD)
appmod.DATA_DIR = Path(TD) / "data"
appmod.DATA_DIR.mkdir(parents=True, exist_ok=True)


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(("  [PASS] " if ok else "  [FAIL] ") + name
          + (f"  —— {extra}" if extra else ""))
    return bool(ok)


def eq(a, b, name):
    return chk(a == b, name, f"期望 {b!r} 实得 {a!r}")


def pump(w, t=0.4):
    end = time.time() + t
    while time.time() < end:
        w.update()


def toplevels(w):
    """整个解释器里的 Toplevel 数量（含隐藏的）。"""
    return [c for c in w.winfo_children() if isinstance(c, __import__("tkinter").Toplevel)]


def main() -> int:
    win = appmod.ToolboxApp()
    win.geometry("1180x760+20+20")
    win.deiconify()
    pump(win, 1.0)

    win.open_settings("agent")
    pump(win, 0.8)

    pane = win._settings_reg().get(win.SETTINGS_TAB)
    chk(pane is not None, "设置页嵌进工作区了")
    chk(getattr(pane, "section", "") == "agent", "停在「智能运维助手」这一页",
        getattr(pane, "section", ""))

    base = len(toplevels(win))
    host = getattr(pane, "_agent_host", None)
    chk(host is not None and host.winfo_exists(), "四个页签的宿主建起来了")

    # 1. 首页签是「智能体设置」
    panels = getattr(pane, "_agent_panels", {})
    chk("agent" in panels, "默认建好的是智能体设置")
    chk(panels.get("agent") is not None
        and panels["agent"].winfo_manager() == "pack", "它正显示着")

    # 2. 依次切到另外三个，每个都要真建起来
    for key, cls_name in (("model", "ModelPanel"), ("skill", "SkillPanel"),
                          ("mcp", "MCPPanel")):
        pane._agent_pick(key)
        pump(win, 0.5)
        p = panels.get(key)
        ok = p is not None and p.winfo_manager() == "pack"
        chk(ok, f"切到 {key}：面板建好并显示")
        if ok:
            eq(type(p).__name__, cls_name, f"{key} 用的是 {cls_name}")
        # 上一块必须收起来，不能叠在一起
        others = [k for k, v in panels.items()
                  if k != key and v.winfo_manager() == "pack"]
        eq(others, [], f"切到 {key} 时其它面板都收起来了")

    # 3. 切一圈不能冒出任何新窗口
    for key in ("agent", "model", "skill", "mcp", "agent"):
        pane._agent_pick(key)
        pump(win, 0.3)
    eq(len(toplevels(win)), base, "切四个页签不开新窗口")
    eq(len([p for p in panels.values() if p.winfo_manager() == "pack"]), 1,
       "同时只有一块在显示")

    # 4. 面板里不该有报错红字（_agent_build 建不起来会放一块 pageerr 标签）
    errs = []

    def walk(w):
        for c in w.winfo_children():
            try:
                if c.winfo_class() == "Label" and "没起来" in str(c.cget("text")):
                    errs.append(str(c.cget("text")))
            except Exception:  # noqa: BLE001
                pass
            walk(c)

    for p in panels.values():
        walk(p)
    eq(errs, [], "四块面板都没报「没起来」")

    # 5. MCP 库能登记假服务器并列出工具
    from core import mcp as mcp_mod
    stub = HERE / "_mcpstub.py"
    win.mcp_store.upsert({"sid": "stub", "name": "桩服务器", "transport": "stdio",
                          "command": sys.executable, "args": [str(stub)],
                          "timeout": 10})
    win.mcp_pool.ensure_tools()
    rows = win.mcp_pool.tools()
    chk(len(rows) >= 3, "MCP 池子列出桩服务器的工具", f"{len(rows)} 个")
    eq(sorted(r["name"] for r in rows)[:3], ["add", "boom", "echo"],
       "工具名对得上")

    # 面板重新载入后，左侧清单里能看到它
    mp = panels.get("mcp")
    pane._agent_pick("mcp")
    pump(win, 0.4)
    if mp is not None and hasattr(mp, "reload"):
        mp.reload()
        pump(win, 0.2)
        items = list(mp.lst.get(0, "end"))
        chk(any("桩服务器" in str(i) for i in items), "面板清单里显示这条",
            str(items))

    # 6. 页签上的文字就是用户要的四块
    labels = []
    for w in host.winfo_children():
        if type(w).__name__ == "Segmented":
            pass
    # Segmented 的按钮是自绘的，直接从设置页里找四个标题的文本
    texts = []

    def walk2(w):
        for c in w.winfo_children():
            try:
                if c.winfo_class() == "Label":
                    texts.append(str(c.cget("text")))
            except Exception:  # noqa: BLE001
                pass
            walk2(c)

    walk2(host.master)
    for want in ("智能体设置", "模型设置", "技能库", "MCP 库"):
        chk(want in texts, f"页签上有「{want}」")
    _ = labels

    win.mcp_pool.stop_all()
    win.destroy()
    print(f"\n合计 {PASS} 项通过，{FAIL} 项失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
