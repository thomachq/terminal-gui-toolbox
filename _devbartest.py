# -*- coding: utf-8 -*-
"""运维工具「操作目标」条 / 表格光标 / 本机连接状态（v1.0.11）。

这一轮用户的三条反馈：

  「鼠标的问题你得解决下」—— 截图里指针停在数据行的文字上也顶着 ↔ 光标。
  「这个选项卡点开应该显示终端才对」—— 点设备标签要开到终端。
  「连接状态依然没有」—— 进页面一片「未检测」，本机连着也只看得到「本机」。
  「运维工具的话，你没有选择具体操作的设备的功能，这个是有问题的」

分段：
  1. 光标：数据区一律箭头，只有表头列分隔线才留 ↔
  2. 「操作目标」条：运维工具组出现、资产组收起，下拉含本机与已连接设备
  3. 在下拉里换目标不打开终端（人还在当前页），点顶部设备标签才开终端
  4. 本机有活会话 → 状态列显示「已连接」；没会话 → 「本机」

数据目录在临时目录，不联网。
"""
import os
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.argv = [sys.argv[0], "--no-probe"]
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = FAIL = 0
FAILS: list[str] = []
TD = tempfile.mkdtemp(prefix="tbdevbar_")

import app as appmod  # noqa: E402
from core import hosts as H  # noqa: E402

appmod.APP_DIR = Path(TD)
appmod.DATA_DIR = Path(TD) / "data"
appmod.DATA_DIR.mkdir(parents=True, exist_ok=True)

import tkinter as tk  # noqa: E402
from pages import hosts_page as HP  # noqa: E402
from ui.table import Column, DataTable  # noqa: E402


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")
    print(("  ok   " if ok else "  FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))


def eq(a, b, name):
    chk(a == b, name, f"期望 {b!r} 实得 {a!r}")


def sec(t):
    print(f"\n-- {t}")


def pump(win, seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        try:
            win.update()
        except tk.TclError:
            return
        time.sleep(0.01)


win = appmod.ToolboxApp()
win.geometry("1200x800+30+30")
win.deiconify()
pump(win, 1.0)


# ================================================================== 1. 光标
sec("1. 表格光标：数据区不该顶着 ↔")

tbl = DataTable(win, [Column("a", "名称", 160),
                      Column("b", "地址", 240, stretch=True)],
                height=6)
tbl.pack(fill="both", expand=True, padx=20, pady=10)
tbl.set_rows([{"a": f"host-{i}", "b": f"10.0.0.{i}"} for i in range(6)])
pump(win, 0.5)

# 假装 ttk 刚把光标设成 ↔，指针落在数据行里
tbl.tree.configure(cursor="sb_h_double_arrow")
tbl._cur_xy = (60, 60)
tbl._settle_cursor()
eq(tbl.tree.cget("cursor"), "", "数据区：↔ 被收掉")

# 指针落在表头的列分隔线上 → 应该留着（那里真能拖列宽）
sep_ok = False
for x in range(20, 400, 2):
    try:
        if str(tbl.tree.identify_region(x, 6)) == "separator":
            tbl.tree.configure(cursor="sb_h_double_arrow")
            tbl._cur_xy = (x, 6)
            tbl._settle_cursor()
            sep_ok = str(tbl.tree.cget("cursor")) == "sb_h_double_arrow"
            break
    except tk.TclError:
        break
chk(sep_ok, "表头分隔线：↔ 保留（那是真能拖的地方）")

# 模拟一次真实 <Motion>：排的是 idle，不该立刻改，跑完事件循环后才纠正
tbl.tree.configure(cursor="sb_h_double_arrow")
tbl._cur_xy = None
tbl._cur_job = None
ev = types.SimpleNamespace(x=40, y=70, state=0)
tbl._maybe_reset_cursor(ev)
chk(tbl._cur_job is not None, "<Motion> 排了 idle 任务（等 ttk 先定完态）")
pump(win, 0.2)
eq(tbl.tree.cget("cursor"), "", "idle 跑完：残留的 ↔ 清掉了")

# 左键按着（正在拖列宽）时不许清
tbl.tree.configure(cursor="sb_h_double_arrow")
tbl._cur_job = None
tbl._maybe_reset_cursor(types.SimpleNamespace(x=40, y=70, state=0x100))
chk(tbl._cur_job is None, "左键按着：不去动光标（正在拖）")
eq(str(tbl.tree.cget("cursor")), "sb_h_double_arrow", "左键按着：↔ 原样留着")
tbl.destroy()
pump(win, 0.2)


# ================================================================== 2. 设备条
sec("2. 运维工具「操作目标」条")

eq(win.dev_strip.winfo_manager(), "", "初始（主机管理页）不占位")
win._show("hosts")
pump(win, 0.3)
eq(win.dev_strip.winfo_manager(), "", "主机管理：仍然收起")

for key, label in (("tools", "网络工具"), ("sys", "系统信息"),
                   ("files", "文件管理"), ("routes", "路由表"),
                   ("sec", "安全中心"), ("docker", "Docker"),
                   ("web", "Web 服务"), ("db", "数据库"),
                   ("cron", "计划任务"), ("vpn", "隧道证书"),
                   ("api", "智能体接入")):
    win._show(key)
    pump(win, 0.15)
    chk(win.dev_strip.winfo_manager() == "pack",
        f"运维工具「{label}」：设备条在")

for key, label in (("hosts", "主机管理"), ("proxy", "代理池"),
                   ("cred", "凭证管理"), ("script", "脚本库")):
    win._show(key)
    pump(win, 0.15)
    eq(win.dev_strip.winfo_manager(), "", f"资产「{label}」：设备条收起")

win._show("sys")
pump(win, 0.3)
vals = list(win.cb_target.cget("values"))
chk(any("本机" in v for v in vals), "下拉里有「本机」", str(vals))
eq(win.cb_target.get(), vals[0], "默认选中的是第一条（本机）")
chk(set(win._target_map.values()) == {""},
    "没连设备时映射只有本机（sid 空）", str(win._target_map))


# ================================================================== 3. 切目标不跳终端
sec("3. 换操作目标 vs 点设备标签")

win._show("sys")
pump(win, 0.3)
n0 = win.tabbar.count()
win._switch_device("", open_term=False)
pump(win, 0.3)
eq(win.tabbar.count(), n0, "下拉换目标：不新开终端页签")

win._switch_device("", open_term=True)
pump(win, 0.5)
chk(win.tabbar.count() > n0, "点设备标签：打开终端页签", f"{n0} → {win.tabbar.count()}")
# 收掉终端，别影响后面的断言
try:
    win._tab_close("term")
except Exception:  # noqa: BLE001
    pass
pump(win, 0.3)


# ================================================================== 4. 本机状态
sec("4. 本机连着时状态列显示「已连接」")

win._show("hosts")
pump(win, 0.6)

rec_local = {"name": "本机", "proto": "local", "host": "", "port": ""}
chk(not win._local_live(), "还没连本机：_local_live 为假")
eq(win._host_status(rec_local)[0], HP.STATUS_TEXT["local"], "没连：显示「本机」")


class _FakeSession:
    kind = "local"
    alive = True
    host = ""


class _PoolLocal:
    """本机连着：active_id 为空（本机不占槽位），session 是本机会话。"""
    active_id = ""
    session = _FakeSession()

    def slots(self):
        return []


class _PoolRemote:
    """活动设备是某台远程：本机即便还活着也不算「正在连这台」。"""
    active_id = "s1"
    session = None

    def slots(self):
        return []


_pool_orig = win.sessions
win.sessions = _PoolLocal()
chk(win._local_live(), "连上本机：_local_live 为真")
eq(win._host_status(rec_local)[0], HP.STATUS_TEXT["live"], "连着：显示「已连接」")

win.sessions = _PoolRemote()
chk(not win._local_live(), "活动设备是远程：本机不算在连")
eq(win._host_status(rec_local)[0], HP.STATUS_TEXT["local"], "那时显示「本机」")
win.sessions = _pool_orig


# ================================================================== 5. 自动探测
sec("5. 自动探测真的会跑（以前被 ismapped 卡死）")

win2 = appmod.ToolboxApp()
win2.geometry("1100x740+40+40")
win2.deiconify()
pump(win2, 0.8)
win2._no_probe = False
hit = []
win2.host_probe = lambda *a, **k: hit.append(1)
# 先让 _show("hosts") 把它自己那轮 host_refresh 跑完（否则会把下面这行覆盖掉）
win2._show("hosts")
pump(win2, 0.5)
win2._host_rows_all = [{"host": "127.0.0.1", "port": "9", "proto": "ssh",
                        "name": "fake"}]
win2._probe_at = 0.0
win2._probe_retry = 0
win2._maybe_auto_probe()
# 别用固定等待：机器忙的时候（整套回归跑着 40 多个窗口）after(350) 可能
# 迟迟排不到，等 3 秒更稳，正常情况下几十毫秒就命中。
_deadline = time.time() + 3.0
while time.time() < _deadline and not hit:
    pump(win2, 0.1)
chk(bool(hit), "映射好了：自动探测被触发", f"hit={len(hit)}")

# 页面没映射时不该放弃，而是排重试
win2._show("proxy")
pump(win2, 0.4)
win2._probe_retry = 0
win2._probe_at = 0.0
hit.clear()
win2._maybe_auto_probe()
chk(win2._probe_retry == 1, "页面没映射：排了一次重试", f"retry={win2._probe_retry}")
_deadline = time.time() + 3.0
while time.time() < _deadline and win2._probe_retry < 2:
    pump(win2, 0.1)
chk(win2._probe_retry >= 2, "重试链继续往下走", f"retry={win2._probe_retry}")
chk(not hit, "没映射时没真去探测（不瞎扫）", f"hit={len(hit)}")
win2.destroy()


# ================================================================== 汇总
print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
if FAILS:
    for f in FAILS:
        print("   ✗", f)
sys.exit(1 if FAIL else 0)
