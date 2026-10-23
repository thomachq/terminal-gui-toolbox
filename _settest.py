# -*- coding: utf-8 -*-
"""设置页的「页签 / 独立窗口 / 嵌回」三态自测。

跟 _tabtest.py 分开写，是因为这条链路的重点不是终端，而是：
  · 设置默认落在页签里，不是弹窗
  · 页签上的 ⧉ 能拉成独立窗口，拉出去时停在哪一页跟着走
  · 独立窗口里的「嵌回主界面」能原样收回
  · 智能运维助手**永远**只做侧边栏，不参与这套机制

真建窗口，数据目录放临时目录，--no-probe 保证不跑任何探测。
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
TD = tempfile.mkdtemp(prefix="tbset_")

import app as appmod  # noqa: E402

appmod.APP_DIR = Path(TD)
appmod.DATA_DIR = Path(TD) / "data"
appmod.DATA_DIR.mkdir(parents=True, exist_ok=True)

import tkinter  # noqa: E402

from pages import settings_center as SC  # noqa: E402


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


def alive(w) -> bool:
    try:
        return bool(w is not None and w.winfo_exists())
    except tkinter.TclError:
        return False


win = appmod.ToolboxApp()
win.geometry("+30+30")
win.deiconify()
pump(win, 1.0)

# ================================================================== 骨架
sec("1. 初始状态")
eq(win.SETTINGS_TAB, "settings", "页签键名固定")
eq(win.tabbar.count(), 1, "初始只有一个页签")
chk("settings" not in win.tabbar.tabs, "设置页签还没建")
chk(win._settings_reg().get("settings") is None, "没有设置 pane")
chk(getattr(win, "_settings_win", None) is None, "也没有独立窗口")

# ================================================================== 页签
sec("2. 点设置 → 嵌成页签，不是弹窗")
win.open_settings()
pump(win, 0.5)
eq(win.tabbar.count(), 2, "多了一个页签")
eq(win.tabbar.current, "settings", "自动切到设置页签")
pane = win._settings_reg().get("settings")
chk(pane is not None, "pane 已登记")
eq(getattr(pane, "__class__", type(None)).__name__, "SettingsPane",
   "是嵌入形态（Frame 宿主）")
chk(isinstance(pane, tkinter.Frame), "真的是 Frame")
chk(not isinstance(pane, tkinter.Toplevel), "不是 Toplevel")
chk(alive(pane), "pane 真的存在")
chk(pane.winfo_ismapped(), "pane 可见")
chk(getattr(win, "_settings_win", None) is None, "没有开弹窗")
eq(win.tabbar.label_of("settings"), "设置", "页签文案")
t = win.tabbar.tabs["settings"]
chk(t["closable"], "设置页签可关")
chk(t["pop"] is not None, "设置页签有 ⧉ 拉出按钮")

sec("3. 重复打开不会开出第二个")
win.open_settings()
pump(win, 0.3)
eq(win.tabbar.count(), 2, "页签数量不变")
chk(win._settings_reg().get("settings") is pane, "还是同一个 pane")

# ================================================================== 分区
sec("4. 六个分区都能渲染")
for key, label in SC.SECTIONS:
    try:
        pane.show(key)
        pump(win, 0.1)
        ok, err = True, ""
    except Exception as e:  # noqa: BLE001
        ok, err = False, f"{type(e).__name__}: {e}"
    chk(ok, f"分区「{label}」能渲染", err)
eq(pane.section, SC.SECTIONS[-1][0], "记住最后停的分区")
eq(len(pane._nav_btns), len(SC.SECTIONS), "左侧分类条目数")
eq(pane._nav_btns["safe"].cget("text"), "安全", "安全分区不叫「账号」")
pane.show("look")
pump(win, 0.2)
chk(len(pane.panel.winfo_children()) > 0, "右侧内容有东西")

sec("5. 带分区名直接打开就落在那页")
win.open_settings("safe")
pump(win, 0.3)
eq(pane.section, "safe", "直接跳到安全分区")

sec("6. 页签形态下 _settings_sync 不炸")
try:
    win._settings_sync()
    pump(win, 0.2)
    chk(True, "_settings_sync 可调用")
except Exception as e:  # noqa: BLE001
    chk(False, "_settings_sync 可调用", f"{type(e).__name__}: {e}")

# ================================================================== 拉出
sec("7. 页签 → 独立窗口")
pane.show("agent")
pump(win, 0.2)
win.settings_popout()
pump(win, 0.6)
eq(win.tabbar.count(), 1, "设置页签已移除")
chk(win._settings_reg().get("settings") is None, "pane 已注销")
chk(not alive(pane), "旧 pane 已销毁")
w = getattr(win, "_settings_win", None)
eq(getattr(w, "__class__", type(None)).__name__, "SettingsWindow",
   "变成了独立窗口")
chk(isinstance(w, tkinter.Toplevel), "真的是 Toplevel")
chk(alive(w), "独立窗口存在")
eq(w.section, "agent", "停在哪页跟到哪页")
try:
    w.withdraw()      # 测试里不抢焦点
except tkinter.TclError:
    pass

sec("8. 窗口形态也能切分区、能同步")
w.show("backup")
pump(win, 0.3)
eq(w.section, "backup", "独立窗口能切分区")
try:
    win._settings_sync()
    pump(win, 0.2)
    chk(True, "窗口形态下 _settings_sync 可调用")
except Exception as e:  # noqa: BLE001
    chk(False, "窗口形态下 _settings_sync 可调用", f"{type(e).__name__}: {e}")
# 已经拉出成窗口时再点「设置」应该抬窗口，而不是再开一个页签
win.open_settings()
pump(win, 0.3)
eq(win.tabbar.count(), 1, "不会同时存在页签和窗口")
chk(win._settings_win is w, "还是同一个窗口")
eq(w.section, "backup", "再点一次设置不会把分区弹回外观页")

# ================================================================== 嵌回
sec("9. 独立窗口 → 嵌回主界面")
win.settings_embed(w)
pump(win, 0.6)
chk(not alive(w), "旧窗口已销毁")
chk(getattr(win, "_settings_win", None) is None, "窗口引用已清")
eq(win.tabbar.count(), 2, "页签回来了")
eq(win.tabbar.current, "settings", "切回设置页签")
pane2 = win._settings_reg().get("settings")
chk(pane2 is not None and alive(pane2), "新 pane 已登记")
eq(pane2.section, "backup", "分区跟着收回")

sec("10. 关独立窗口会清引用")
win.settings_popout()
pump(win, 0.5)
w2 = win._settings_win
chk(alive(w2), "又拉出来了")
w2.close()
pump(win, 0.4)
chk(getattr(win, "_settings_win", None) is None, "close() 清掉了引用")
chk(not alive(w2), "窗口真的没了")

# ================================================================== 关闭页签
sec("11. 关页签后能重新打开")
eq(win.tabbar.count(), 1, "现在只剩主机资源")
win.open_settings()
pump(win, 0.4)
eq(win.tabbar.count(), 2, "能重新开出设置页签")
pane3 = win._settings_reg().get("settings")
chk(alive(pane3), "新 pane 存在")
win.tabbar.close("settings")
pump(win, 0.4)
eq(win.tabbar.count(), 1, "× 能关掉设置页签")
chk(win._settings_reg().get("settings") is None, "登记表清干净了")
chk(not alive(pane3), "pane 已销毁")
eq(win.tabbar.current, "hosts", "关掉当前页签后回到主机资源")

# ================================================================== 助手不进页签
sec("12. 智能运维助手只能当侧边栏")
win.ai_dock_toggle()
pump(win, 0.6)
chk(getattr(win, "ai_dock", None) is not None, "助手侧栏建出来了")
chk("ai" not in win.tabbar.tabs, "助手没有变成页签")
chk("settings" not in win.tabbar.tabs, "此时也没有设置页签")
chk(str(win.ai_dock) in [str(p) for p in win.paned.panes()],
    "助手挂在分栏容器里")
eq(win.tabbar.count(), 1, "页签数量没被助手影响")
win.ai_dock_toggle()
pump(win, 0.3)

# ================================================================== 布局
sec("13. 设置页签里的导航与工作区位置")
win.open_settings("look")
pump(win, 0.5)
p4 = win._settings_reg().get("settings")
eq(win.tabbar.current, "settings", "设置页签是当前页")
chk(p4.winfo_ismapped(), "设置 pane 可见")
chk(win.nav.winfo_rootx() < p4.winfo_rootx(), "左侧导航在设置页左边")


def rx(w):
    try:
        return w.winfo_rootx()
    except tkinter.TclError:
        return -1


chk(rx(win.nav) < rx(win.workspace), "主导航仍在工作区左边")

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
