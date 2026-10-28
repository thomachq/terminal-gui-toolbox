# -*- coding: utf-8 -*-
"""复现「连了多台设备，终端开不了 / 只有一个终端页签」。

    python _multitermrepro.py

纯界面层复现：不连真机，往 SessionManager 里塞两个假会话，
然后按用户路径（点设备标签 → 开终端）走一遍，看页签与面板的实际状态。
"""
from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core.detect import DeviceInfo  # noqa: E402


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

    def close(self):
        self.alive = False

    def run(self, cmd, timeout=30, admin=False):
        return 0, "/tmp\n", ""

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
    pump(app, 0.3)

    print("== 连上两台后 ==")
    print("  页签:", list(app.tabbar.tabs))
    print("  终端面板:", list(app._term_panes))

    # 用户路径 1：点第一台设备标签（会顺手开终端）
    print("\n== 点设备标签 Synology_DSM_VM ==")
    app._switch_device(slot1.sid)
    pump(app, 0.4)
    print("  页签:", list(app.tabbar.tabs), "当前:", app.tabbar.current)
    print("  面板数:", len(app._term_panes))
    p1 = app._term_live()

    # 用户路径 2：点第二台设备标签
    print("\n== 点设备标签 fnOS ==")
    app._switch_device(slot2.sid)
    pump(app, 0.4)
    print("  页签:", list(app.tabbar.tabs), "当前:", app.tabbar.current)
    print("  面板数:", len(app._term_panes))
    p2 = app._term_live()
    print("  是否是同一个面板:", p1 is p2)
    print("  面板头部文案:", getattr(p2, "lbl_dev", None) and p2.lbl_dev.cget("text"))
    print("  页签标题:", app.tabbar.label_of(app.TERM_TAB))
    print("  term_describe():", app.term_describe())

    # 用户路径 3：直接点「终端」按钮
    print("\n== 点右上角『终端』按钮 ==")
    n_before = len(app._term_panes)
    app.open_terminal()
    pump(app, 0.3)
    print("  面板数:", n_before, "->", len(app._term_panes))

    # 关掉一台设备，看它的终端页签会不会残留
    print("\n== 断开 fnOS ==")
    app.sessions.close(slot2.sid)
    app._after_switch()
    pump(app, 0.4)
    print("  页签:", list(app.tabbar.tabs))
    print("  面板:", list(app._term_panes))
    print("  当前会话:", app.session and app.session.label)

    try:
        app.destroy()
    except tk.TclError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
