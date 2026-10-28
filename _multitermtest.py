# -*- coding: utf-8 -*-
"""多设备多终端自测：每台设备一个终端页签、命令不串台。

    python _multitermtest.py

为什么不连真机：这条链路的 bug 全在界面层的「键」上（以前所有设备共用
一个 `term` 键），用假会话就能完整验证。真机只影响 `term_run` 发出去的内容，
不影响「哪个面板收命令」。
"""
from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core.detect import DeviceInfo  # noqa: E402

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


class FakeSession:
    def __init__(self, host, user="root", name=""):
        self.host = host
        self.user = user
        self.port = 22
        self.kind = "ssh"
        self.label = name or f"{user}@{host}"
        self.alive = True
        self.host_kind = "unix"
        self.vendor = ""
        self.tracer = None
        self.is_root = True
        self.elevated = False
        self.ran: list[str] = []

    def close(self):
        self.alive = False

    def run(self, cmd, timeout=30, admin=False):
        self.ran.append(cmd)
        if cmd.strip().startswith(("pwd", "cd")):
            return 0, "/root\n", ""
        return 0, f"ok:{cmd}\n", ""

    # 终端真正走的是 term_exec → run_traced（提权了才走 run_admin）。
    # 不补这两个方法，测试里每条命令都会 TypeError，看起来像「命令没发出去」。
    def run_traced(self, cmd, timeout=30):
        return self.run(cmd, timeout=timeout)

    def run_admin(self, cmd, timeout=30):
        return self.run(cmd, timeout=timeout, admin=True)

    def bind_device(self, dev):
        pass


def dev(name):
    d = DeviceInfo()
    d.hostname = name
    d.os_family = "linux"
    d.os_name = "Linux"
    d.kernel = "6.1"
    d.arch = "x86_64"
    return d


def pump(app, sec=0.3):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.update()
        time.sleep(0.01)


def main() -> int:
    import wbtui as U  # noqa: F401
    import app as A

    app = A.ToolboxApp()
    pump(app, 0.8)

    s1 = FakeSession("192.168.3.15", name="Synology_DSM_VM")
    s2 = FakeSession("192.168.3.48", name="fnOS")
    slot1 = app.sessions.add(s1, dev("Synology_DSM_VM"), "Synology_DSM_VM")
    slot2 = app.sessions.add(s2, dev("fnOS"), "fnOS")
    app._after_switch()
    pump(app, 0.2)

    print("\n[A] 两台设备各有自己的终端页签")
    chk(len(app._term_panes) == 0, "刚连上时还没有终端面板", str(app._term_panes))

    app._switch_device(slot1.sid)
    pump(app, 0.4)
    key1 = app._term_key(slot1.sid)
    chk(key1 in app.tabbar.tabs, "点设备标签后建了它自己的页签", key1)
    chk(key1 in app._term_panes, "面板登记在同一把键上", str(list(app._term_panes)))
    pane1 = app._term_panes.get(key1)
    chk(getattr(getattr(pane1, "ctrl", None), "sid", None) == slot1.sid,
        "面板被钉在第一台上", str(getattr(getattr(pane1, "ctrl", None), "sid", None)))

    app._switch_device(slot2.sid)
    pump(app, 0.4)
    key2 = app._term_key(slot2.sid)
    chk(key2 in app._term_panes, "第二台也开了自己的面板", str(list(app._term_panes)))
    chk(key1 != key2, "两把键不同", f"{key1} / {key2}")
    chk(len(app._term_panes) == 2, "同时存在两个终端面板",
        str(len(app._term_panes)))
    pane2 = app._term_panes.get(key2)
    chk(pane1 is not pane2, "不是同一个面板被复用", "")
    chk(getattr(getattr(pane2, "ctrl", None), "sid", None) == slot2.sid,
        "第二个面板钉在第二台上", "")

    print("\n[B] 标题 / 提示符各认自己的设备")
    chk("Synology_DSM_VM" in app.tabbar.label_of(key1),
        "第一把页签标题写第一台", app.tabbar.label_of(key1))
    chk("fnOS" in app.tabbar.label_of(key2),
        "第二把页签标题写第二台", app.tabbar.label_of(key2))
    chk("Synology_DSM_VM" in pane1.ctrl.term_describe(),
        "第一块面板的头部是它自己", pane1.ctrl.term_describe())
    chk("fnOS" in pane2.ctrl.term_describe(),
        "第二块面板的头部是它自己", pane2.ctrl.term_describe())

    print("\n[C] 命令按面板走，不按「当前窗口」走")
    got: list = []
    pane1.ctrl.term_run("whoami", lambda rc, o, e, ms: got.append(("p1", o)))
    pane2.ctrl.term_run("hostname", lambda rc, o, e, ms: got.append(("p2", o)))
    for _ in range(60):
        app.update()
        time.sleep(0.05)
        if len(got) >= 2:
            break
    chk(any("whoami" in c for c in s1.ran), "第一台的会话收到 whoami",
        str(s1.ran[-3:]))
    chk(not any("whoami" in c for c in s2.ran), "whoami 没跑到第二台上",
        str(s2.ran[-3:]))
    chk(any("hostname" in c for c in s2.ran), "第二台的会话收到 hostname",
        str(s2.ran[-3:]))
    chk(not any("hostname" in c for c in s1.ran), "hostname 没跑到第一台上",
        str(s1.ran[-3:]))

    print("\n[D] 当前目录各记各的")
    app._cwd_set(slot1.sid, "/volume1")
    app._cwd_set(slot2.sid, "/root")
    chk(app.term_cwd(slot1.sid) == "/volume1", "第一台目录独立", app.term_cwd(slot1.sid))
    chk(app.term_cwd(slot2.sid) == "/root", "第二台目录独立", app.term_cwd(slot2.sid))

    print("\n[E] 再点一次已开着的设备：只切过去，不重复建")
    n_before = len(app._term_panes)
    app.open_terminal()
    pump(app, 0.2)
    chk(len(app._term_panes) == n_before, "面板数没变", str(len(app._term_panes)))

    print("\n[F] 断开一台：它的终端跟着消失，另一台不受影响")
    old_ask = A.messagebox.askokcancel
    A.messagebox.askokcancel = lambda *a, **k: True
    try:
        app._close_device(slot2.sid)
    finally:
        A.messagebox.askokcancel = old_ask
    pump(app, 0.4)
    chk(key2 not in app._term_panes, "第二台的页签面板已清", str(list(app._term_panes)))
    chk(key2 not in app.tabbar.tabs, "第二台的页签也从页签栏移除",
        str(list(app.tabbar.tabs)))
    chk(key1 in app._term_panes, "第一台的面板还在", str(list(app._term_panes)))
    chk(app.session is s1, "当前会话回到第一台", app.session and app.session.label)

    print("\n[G] 断开后还能再开那台（重新连）")
    s2b = FakeSession("192.168.3.48", name="fnOS")
    slot2b = app.sessions.add(s2b, dev("fnOS"), "fnOS")
    app._switch_device(slot2b.sid)
    pump(app, 0.4)
    chk(app._term_key(slot2b.sid) in app._term_panes, "新会话开了新页签",
        str(list(app._term_panes)))

    print("\n[结果]")
    print(f"  PASS {PASS} / FAIL {FAIL}")
    if FAILED:
        for n in FAILED:
            print("    -", n)

    try:
        app.destroy()
    except tk.TclError:
        pass
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
