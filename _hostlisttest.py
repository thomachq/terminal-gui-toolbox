# -*- coding: utf-8 -*-
"""主机清单：状态列 / 右键 / 行内编辑 / 连通检测（v1.0.9）。

用户这一轮的原话（三条都在这个文件里验）：
  「也没有连接状态显示」
  「右键编辑按钮不管用」
  「请你在每个连接的头部加个编辑按钮」
  「主机都删不掉」

分段：
  1. 右键：指针下那行先被选中（不再用上一次的选中项）
  2. 右键回调两种写法都调得动（0 参与 1 参）—— 以前只认 1 参，
     0 参的直接 TypeError，被 Tk 吞进 stderr，表现就是「点了没反应」
  3. 右键「删除」「编辑」真的落地
  4. 头部「编辑」按钮 + 行尾 ✎ 都作用于正确那一行
  5. 状态列：本机 / 未检测 / 可达 / 不可达 / 已连接
  6. probe_host：开放的端口算可达，关掉的算不可达（只用回环，不联外网）

数据目录在临时目录，不联网（端口探测只走 127.0.0.1）。
"""
import os
import socket
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
TD = tempfile.mkdtemp(prefix="tbhostlist_")

import app as appmod  # noqa: E402
from core import hosts as H  # noqa: E402

appmod.APP_DIR = Path(TD)
appmod.DATA_DIR = Path(TD) / "data"
appmod.DATA_DIR.mkdir(parents=True, exist_ok=True)

import tkinter as tk  # noqa: E402
from pages import hosts_page as HP  # noqa: E402
from ui.table import DataTable  # noqa: E402


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
        except tk.TclError:
            return
        time.sleep(0.01)


def menu_invoke(tbl: DataTable, label: str) -> bool:
    """按文字找到右键菜单项并把它的 Tcl 命令跑一遍。

    直接 tk_popup 会真的弹菜单、抢 grab，测试里容易卡住；
    这里只把菜单**建好**（_popup 里除 tk_popup 之外的逻辑全跑），
    再照菜单里注册的 Tcl 命令名执行一次 —— 等于用户点了那一项。
    """
    try:
        n = tbl._menu.index("end")
    except tk.TclError:
        return False
    if n is None:
        return False
    for i in range(int(n) + 1):
        try:
            if str(tbl._menu.entrycget(i, "label")) != label:
                continue
            cmd = str(tbl._menu.entrycget(i, "command"))
        except tk.TclError:
            continue
        if not cmd:
            return False
        tbl.tk.eval(cmd)
        return True
    return False


# ================================================================== 准备
win = appmod.ToolboxApp()
win.geometry("+20+20")
win.deiconify()
pump(win, 0.9)

win.profiles.upsert(H.blank_record("local") | {"name": "本机甲"})
win.profiles.upsert({"name": "nas", "host": "192.168.3.7", "port": "22",
                     "user": "root", "proto": "ssh", "auth": "key"})
win.profiles.upsert({"name": "winsrv", "host": "10.0.0.9", "port": "22",
                     "user": "admin", "proto": "ssh", "auth": "password",
                     "ostype": "windows", "os_name": "Windows Server 2022"})
win.host_refresh()
pump(win, 0.4)

tbl = win.tbl_host
eq(len(tbl.rows()), 3, "清单里三条")
# 右键不要再真弹菜单
tbl._menu.tk_popup = lambda *a, **k: None     # type: ignore[assignment]


def row_of(name: str) -> dict:
    for r in tbl.rows():
        if r.get("name") == name:
            return r
    return {}


# ================================================================== 1. 右键选行
sec("1. 右键先选中指针下那行")

w0 = [i for i, r in enumerate(tbl.rows()) if r.get("name") == "winsrv"][0]
tbl.select_index(w0)
pump(win, 0.15)
first = tbl.selected() or {}
idx = [i for i, r in enumerate(tbl.rows()) if r.get("name") == "nas"][0]
y = tbl.tree.bbox(tbl._row_iid[idx])[1] + 3 if tbl._row_iid.get(idx) else 5
ev = types.SimpleNamespace(x=40, y=y, x_root=50, y_root=50)
tbl._popup(ev)
pump(win, 0.15)
sel = tbl.selected() or {}
eq(sel.get("name"), "nas", "右键之后选中的是「nas」，不是上一次选的")
chk(first.get("name") != "nas", "右上角那行确实换过一次（说明不是本来就选着）",
    f"{first.get('name')} → {sel.get('name')}")

# ================================================================== 2. 回调写法
sec("2. 两种回调签名都要调得动")

chk(DataTable._takes_row(lambda: 1) is False, "0 参 lambda 判为「不要行数据」")
chk(DataTable._takes_row(lambda r: 1) is True, "1 参 lambda 判为「要行数据」")
chk(DataTable._takes_row(lambda *a: 1) is True, "*args 判为「要行数据」")


class _Obj:
    def one(self, r):
        return r

    def zero(self):
        return 1


chk(DataTable._takes_row(_Obj().one) is True, "绑定方法带一个参数 → 要行数据")
chk(DataTable._takes_row(_Obj().zero) is False, "绑定方法无参 → 不要行数据")

# ================================================================== 3. 右键动作
sec("3. 右键「编辑」「删除」真的落地")

calls: list[dict] = []
win.host_edit = lambda rec, _c=calls: _c.append(dict(rec or {}))  # type: ignore[assignment]
tbl._popup(ev)
pump(win, 0.1)
ok = menu_invoke(tbl, "编辑…")
pump(win, 0.2)
chk(ok, "右键菜单里有「编辑…」并且命令能执行")
chk(bool(calls) and calls[-1].get("name") == "nas",
    "点「编辑…」拿到的是指针下那台（nas）",
    [c.get("name") for c in calls])

asked: list[str] = []
_orig_ask = HP.messagebox.askyesno


def _fake_ask(title, msg, **kw):
    asked.append(str(msg))
    return True


HP.messagebox.askyesno = _fake_ask
try:
    tbl._popup(ev)
    pump(win, 0.1)
    ok = menu_invoke(tbl, "删除")
    pump(win, 0.3)
finally:
    HP.messagebox.askyesno = _orig_ask

chk(ok, "右键菜单里有「删除」并且命令能执行")
chk(bool(asked), "删除前问了确认")
chk(win.profiles.get("nas") is None, "「nas」真的从档案里没了",
    [p.get("name") for p in win.profiles.profiles])
eq(len(tbl.rows()), 2, "清单跟着少一条")
chk(win.profiles.get("winsrv") is not None, "没误删别的主机")

# 删掉不存在的记录要明说，而不是假装成功
deleted = win.profiles.remove("根本没有这台")
chk(deleted is False, "删不存在的名字返回 False（界面据此提示失败）")

# ================================================================== 4. 编辑入口
sec("4. 头部「编辑」按钮与行首 ✎ / 连接")

chk(hasattr(win, "host_edit_sel"), "头部有「编辑」入口")
win.profiles.upsert({"name": "gitlab", "host": "10.0.0.20", "port": "22",
                     "proto": "ssh", "auth": "key"})
win.host_refresh()
pump(win, 0.3)

calls.clear()
gidx = [i for i, r in enumerate(tbl.rows()) if r.get("name") == "gitlab"][0]
tbl.select_index(gidx)
pump(win, 0.1)
win.host_edit_sel()
pump(win, 0.2)
chk(bool(calls) and calls[-1].get("name") == "gitlab",
    "头部「编辑」作用于选中行", [c.get("name") for c in calls])

calls.clear()
tgt = row_of("winsrv")
tbl.column_at = lambda x: "edit"          # type: ignore[assignment]
tbl.row_at = lambda y: tgt                # type: ignore[assignment]
res = win._host_row_click(types.SimpleNamespace(x=1, y=1))
pump(win, 0.2)
eq(res, "break", "点 ✎ 之后把事件吃掉（不再触发选中/双击连接）")
chk(bool(calls) and calls[-1].get("name") == "winsrv",
    "点行尾 ✎ 编辑的是那一行", [c.get("name") for c in calls])

# 点别的列不能开编辑
calls.clear()
tbl.column_at = lambda x: "name"          # type: ignore[assignment]
res = win._host_row_click(types.SimpleNamespace(x=1, y=1))
chk(res is None and not calls, "点名称列不会弹编辑", f"res={res} calls={calls}")

# ---- 行首「连接」列：点一下就连接这一行
tbl.column_at = lambda x: "act"           # type: ignore[assignment]
tbl.row_at = lambda y: tgt                # type: ignore[assignment]
applied: list[dict] = []
conns: list[int] = []
win._host_apply = lambda rec, _a=applied: _a.append(dict(rec or {}))  # type: ignore[assignment]
win.do_connect = lambda _c=conns: _c.append(1)  # type: ignore[assignment]
res = win._host_row_click(types.SimpleNamespace(x=1, y=1))
pump(win, 0.2)
eq(res, "break", "点「连接」也把事件吃掉（别再顺带开编辑/切选中）")
chk(bool(applied) and applied[-1].get("name") == "winsrv",
    "点「连接」把那台灌进连接表单", [a.get("name") for a in applied])
eq(len(conns), 1, "并且真的发起了连接")

# 列顺序：连接 / ✎ 必须在最左，且都在「状态」之前
keys = [c.key for c in tbl.columns]
eq(keys[:2], ["act", "edit"], "最左两列是「连接」和「✎」")
chk(keys.index("status") > 1, "状态列排在这两个动作列之后", keys)

# 已经连接的那台：连接列文案不变（点它等于重连，别让文案跟着状态跳）
chk("act" in row_of("winsrv"), "行模型里有动作列取值")
eq(row_of("winsrv").get("act"), "连接", "动作列文字是「连接」")

# ================================================================== 5. 状态列
sec("5. 状态列")

chk("status" in [c.key for c in tbl.columns], "表里有「状态」列")
chk("os" in [c.key for c in tbl.columns], "表里有「系统」列")
chk("edit" in [c.key for c in tbl.columns], "表里有编辑列")

win._host_probe = {}
win.host_refresh()
pump(win, 0.3)
eq(row_of("本机甲").get("status"), "● 本机", "本机记录直接显示「本机」")
eq(row_of("winsrv").get("status"), "○ 未检测", "没探过就是「未检测」")
eq(row_of("winsrv").get("os"), "Windows Server 2022",
   "系统列显示探测到的版本名")

rec = win.profiles.get("winsrv") or {}
key = HP.probe_key(rec)
win._host_probe[key] = ("ok", int(time.time()))
win.host_refresh()
pump(win, 0.3)
eq(row_of("winsrv").get("status"), "● 可达", "探通了显示「可达」")

win._host_probe[key] = ("bad", int(time.time()))
win.host_refresh()
pump(win, 0.3)
eq(row_of("winsrv").get("status"), "● 不可达", "探不通显示「不可达」")


class _FakeSession:
    host = "10.0.0.9"
    port = 22
    alive = True


class _FakeSlot:
    session = _FakeSession()
    device = None

    def alive(self):
        return True


win.sessions._slots.append(_FakeSlot())
win.host_refresh()
pump(win, 0.3)
eq(row_of("winsrv").get("status"), "● 已连接", "有活会话时优先显示「已连接」")
win.sessions._slots.pop()

# ================================================================== 6. 端口探测
sec("6. probe_host 只做一次握手")

srv = socket.socket()
srv.bind(("127.0.0.1", 0))
srv.listen(1)
port = srv.getsockname()[1]
state, why = HP.probe_host({"host": "127.0.0.1", "port": str(port), "proto": "ssh"},
                           timeout=1.0)
eq(state, "ok", f"开着端口的本机地址算可达（{port}）")
srv.close()

# 端口关掉之后再探一次：应当不可达（连接被拒）
state2, why2 = HP.probe_host({"host": "127.0.0.1", "port": str(port),
                              "proto": "ssh"}, timeout=1.0)
eq(state2, "bad", f"关掉的端口算不可达：{why2[:40]}")

eq(HP.probe_host({"proto": "local"})[0], "local", "本机不参与探测")
eq(HP.probe_host({"host": "", "proto": "ssh"})[0], "none", "没地址不探测")
eq(HP.probe_key({"proto": "local"}), "", "本机的探测键是空的")
eq(HP.probe_key({"proto": "ssh", "host": "A.B", "port": "22"}),
   "ssh://a.b:22", "探测键按 协议+地址+端口，大小写统一")

res = HP.probe_many([{"proto": "local"},
                     {"host": "127.0.0.1", "port": "1", "proto": "ssh"}],
                    timeout=0.4)
eq(len(res), 1, "并发探测跳过本机，只探能探的")

try:
    win.destroy()
except tk.TclError:
    pass

print("\n" + "=" * 50)
print(f"{'失败 ' + str(len(FAILS)) if FAILS else '全部通过'}：{PASS}/{PASS + FAIL}")
for x in FAILS:
    print("  ✗", x)
raise SystemExit(1 if FAILS else 0)
