# -*- coding: utf-8 -*-
"""顶部页签 + 终端嵌入/拉出的自测。

真建窗口，但数据目录在临时目录，--no-probe 保证不跑任何探测。
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
TD = tempfile.mkdtemp(prefix="tbtab_")

import app as appmod  # noqa: E402

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
win.geometry("+30+30")
# 不 deiconify 的话 winfo_ismapped 恒为 0（窗口没映射），断言就成了看运气
win.deiconify()
pump(win, 1.0)

# ================================================================== 页签栏
sec("1. 页签栏骨架")
chk(hasattr(win, "tabbar"), "有页签栏")
chk(hasattr(win, "workspace"), "有工作区容器")
eq(win.tabbar.count(), 1, "初始一个页签")
chk("hosts" in win.tabbar.tabs, "主机资源页签已注册")
eq(win.tabbar.current, "hosts", "默认选中主机资源")
eq(win.tabbar.label_of("hosts"), "主机资源", "页签文案")
chk(not win.tabbar.tabs["hosts"]["closable"], "主机资源页签不可关")
chk(win.ws_hosts.winfo_ismapped(), "主机资源工作区可见")

# 左侧导航还在
chk(hasattr(win, "nav") and "hosts" in win.nav.items, "导航仍在主机资源页内")

# ================================================================== 打开终端
sec("2. 终端嵌成页签")
win.open_terminal()
pump(win, 0.5)
eq(win.tabbar.count(), 2, "多了一个页签")
eq(win.tabbar.current, "term", "自动切到终端页签")
chk("term" in win._term_panes, "终端 pane 已登记")
pane = win._term_panes.get("term")
chk(pane is not None and pane.__class__.__name__ == "TerminalPane",
    "是嵌入形态（Frame 宿主）")
chk(pane.winfo_ismapped(), "终端 pane 可见")
chk("终端" in win.tabbar.label_of("term"), "页签标题含终端",
    win.tabbar.label_of("term"))
chk(not win.ws_hosts.winfo_ismapped() or win.tabbar.current == "term",
    "切到终端页签")
chk(win._term_live() is pane, "_term_live 认到 pane")

# 再点一次「终端」不该开出第二个
win.open_terminal()
pump(win, 0.3)
eq(win.tabbar.count(), 2, "重复打开不会多出页签")

# ================================================================== 切回
sec("3. 点导航回到主机资源")
win.nav.select("sec")
pump(win, 0.3)
eq(win.tabbar.current, "hosts", "点导航自动切回主机资源页签")
chk(win.ws_hosts.winfo_ismapped(), "工作区切回")
eq(win.nav.current, "sec", "导航停在安全中心")
win.tabbar.select("term")
pump(win, 0.2)
eq(win.tabbar.current, "term", "点页签能切回终端")

# ================================================================== 拉出
sec("4. 拉出为独立窗口")
pane.write("__MARK_POPOUT__\n", "out")
pane._hist = ["df -h", "ls"]
pump(win, 0.2)
win.term_popout()
pump(win, 0.5)
eq(win.tabbar.count(), 1, "终端页签已移除")
chk("term" not in win._term_panes, "pane 已注销")
w = win._term
chk(w is not None and w.__class__.__name__ == "TerminalWindow",
    "变成了独立窗口")
chk(w.winfo_exists(), "独立窗口真的存在")
try:
    w.withdraw()
except tkinter.TclError:
    pass
txt = w.text.get("1.0", "end-1c")
chk("__MARK_POPOUT__" in txt, "正文搬到了独立窗口")
eq(list(w._hist), ["df -h", "ls"], "命令历史一起搬过去")
chk(win._term_live() is w, "_term_live 认到独立窗口")

# ================================================================== 嵌回
sec("5. 嵌回主界面")
win.term_embed(w)
pump(win, 0.5)
eq(win.tabbar.count(), 2, "页签回来了")
chk(win._term is None, "独立窗口已注销")
pane2 = win._term_panes.get("term")
chk(pane2 is not None, "新 pane 已登记")
txt2 = pane2.text.get("1.0", "end-1c")
chk("__MARK_POPOUT__" in txt2, "正文跟着收回")
eq(list(pane2._hist), ["df -h", "ls"], "历史跟着收回")

# ================================================================== 关闭
sec("6. 关闭终端页签")
win.tabbar.close("term")
pump(win, 0.4)
eq(win.tabbar.count(), 1, "只剩主机资源")
eq(win.tabbar.current, "hosts", "关掉当前页签后回到主机资源")
chk("term" not in win._term_panes, "pane 已清理")
chk(win._term_live() is None, "没有活着的终端了")
chk(win.ws_hosts.winfo_ismapped(), "主机资源可见")

# 主机资源页签不可关（closable=False → 没有关闭按钮）
chk(win.tabbar.tabs["hosts"]["close"] is None, "主机资源没有关闭按钮")

# ================================================================== 细节
sec("7. 页签细节")
win.tabbar.add("t2", "测试页", closable=True)
eq(win.tabbar.count(), 2, "能加页签")
win.tabbar.set_label("t2", "改过名")
eq(win.tabbar.label_of("t2"), "改过名", "能改文案")
win.tabbar.remove("t2")
eq(win.tabbar.count(), 1, "能移除")
win.tabbar.select("不存在的")
eq(win.tabbar.current, "hosts", "选不存在的页签不改变当前")
chk(win._tab_new is not None, "+ 按钮有回调")
win._tab_new()
pump(win, 0.4)
eq(win.tabbar.count(), 2, "「+」能开终端")
# 关闭走的是 tabbar.close：它先问业务回调，再移除页签
win.tabbar.close("term")
pump(win, 0.3)
eq(win.tabbar.count(), 1, "关闭回调能移除页签")

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
