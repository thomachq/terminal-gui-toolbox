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

# ================================================================== 导航归属
sec("6b. 左侧导航只属于主机资源页签")
# 结构层面：导航是主机资源容器的孩子，不是窗口层的。这一条是根因级断言 ——
# 只要它还挂在窗口层，切页签就一定露出来。
eq(str(win.nav.master), str(win.ws_hosts), "导航挂在主机资源容器里")


def top_at(w):
    """w（或它的某个祖先）是不是当前抬在最上面的那块工作区。

    原先用 winfo_containing 做像素级命中判断，但 Windows 上它走
    WindowFromPoint：窗口只要被别的程序盖住就返回 None，同一份代码在不同
    桌面状态下结论会变。改成问程序自己记的那份 z 序（app._ws_raise 维护），
    结论只取决于「谁被 tkraise 了」。
    """
    cur = getattr(win, "_ws_top", None)
    chain = []
    node = w
    while node is not None:
        chain.append(node)
        node = getattr(node, "master", None)
    return chain if cur in chain else []


pump(win, 0.3)
chk(win.nav in top_at(win.nav), "主机资源页签：导航在最上层")

win.open_terminal()
pump(win, 0.6)
chk(win.nav not in top_at(win.nav), "终端页签：导航被盖住")
chk(win.ws_hosts not in top_at(win.ws_hosts), "终端页签：主机资源整页被盖住")
win.tabbar.close("term")
pump(win, 0.4)

win.open_settings()
pump(win, 0.6)
chk(win.nav not in top_at(win.nav), "设置页签：导航被盖住")
sc = win._settings_reg().get(win.SETTINGS_TAB)
chk(sc is not None, "设置页签已嵌进工作区")
if sc is not None:
    chk(hasattr(sc, "_nav_btns"), "设置页有自己的分区导航")
    pump(win, 0.2)
    chk(sc._nav_btns["look"] in top_at(sc._nav_btns["look"]),
        "设置页的分区导航在最上层")
win.tabbar.close(win.SETTINGS_TAB)
pump(win, 0.4)
chk(win.nav in top_at(win.nav), "切回主机资源，导航回来了")

# ================================================================== 切页签只留一块
sec("6c. 切页签「只留一块在布局里」")


def ws_visible() -> list:
    """当前真正参与布局的工作区块（其它块必须被 grid_remove）。"""
    out = []
    for w in win.workspace.winfo_children():
        try:
            if w.winfo_manager() == "grid":
                out.append(w)
        except tkinter.TclError:
            pass
    return out


win.open_settings()
pump(win, 0.6)
spane = win._settings_reg().get(win.SETTINGS_TAB)
chk(spane is not None, "设置面板还在")
eq(len(ws_visible()), 1, "设置页签：工作区里只有一块在布局里")
if spane is not None:
    eq(ws_visible()[0], spane.master, "留下的正是设置那一块")

win.tabbar.select("hosts")
pump(win, 0.3)
eq(len(ws_visible()), 1, "主机资源：也只留一块")
chk(ws_visible()[0] is win.ws_hosts, "留下的正是主机资源")

# 以前「点页签没反应」就发生在这里：设置那块已经被摘出布局，再 raise 也抬不起来
if spane is not None:
    t = win.tabbar.tabs[win.SETTINGS_TAB]
    t["label"].event_generate("<Button-1>", x=3, y=3)
    pump(win, 0.3)
    chk(win._ws_top is spane.master, "真点页签文字：能切回设置")
    eq(spane.master.winfo_manager(), "grid", "设置那块重新回到布局里")

    # 顶部那 2px 色条也是页签的一部分，点了也该切
    win.tabbar.select("hosts")
    pump(win, 0.2)
    t["bar"].event_generate("<Button-1>", x=3, y=1)
    pump(win, 0.3)
    chk(win._ws_top is spane.master, "真点页签顶部色条：也能切回设置")

win.tabbar.close(win.SETTINGS_TAB)
pump(win, 0.4)
eq(len(ws_visible()), 1, "关掉设置页签后仍只有一块")

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
