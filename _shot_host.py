# -*- coding: utf-8 -*-
"""抓主机清单页与「添加主机」表单的图，核对状态列 / 编辑入口 / 私钥密码（纯本地核对）。

    python _shot_host.py
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.argv = [sys.argv[0], "--no-probe"]
sys.path.insert(0, str(HERE))

import _shots as SH  # noqa: E402

import app as appmod  # noqa: E402
from core import hosts as H  # noqa: E402
from ui import forms  # noqa: E402
from pages.hosts_page import host_fields  # noqa: E402

# 下面的 DEMO 会 upsert 档案 —— 必须改临时副本，别碰真实 data
SH.isolate("tbshot_host_")


def pump(win, sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        try:
            win.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.01)


win = appmod.ToolboxApp()
win.geometry("1180x760+40+40")
win.deiconify()
pump(win, 1.0)

# 造几条像用户截图里那样的档案
DEMO = [
    ("chromas.synology.me", "chromas.synology.me", "2222", "git", "ssh", "linux"),
    ("DESKTOP-HBM6KCB", "", "", "", "local", "windows"),
    ("gitcode.com", "gitcode.com", "22", "git", "ssh", "linux"),
    ("gitee.com", "gitee.com", "22", "git", "ssh", "linux"),
    ("github.com", "ssh.github.com", "443", "git", "ssh", "linux"),
    ("lab-win", "10.0.0.31", "22", "Administrator", "ssh", "windows"),
]
for name, host, port, user, proto, os in DEMO:
    rec = H.blank_record(proto)
    rec.update({"name": name, "host": host, "port": port or "22",
                "user": user, "ostype": os, "auth": "key",
                "group": "工作" if proto != "local" else ""})
    win.profiles.upsert(rec)

# 给几条假的探测结果，看看状态列的观感
now = int(time.time())
for name in ("chromas.synology.me", "gitee.com"):
    r = win.profiles.get(name)
    if r:
        win._host_probe.setdefault("", None)
        win._host_probe[__import__("pages.hosts_page", fromlist=["x"]).probe_key(r)] = ("ok", now)
for name in ("github.com",):
    r = win.profiles.get(name)
    if r:
        win._host_probe[__import__("pages.hosts_page", fromlist=["x"]).probe_key(r)] = ("bad", now)

win.host_refresh()
pump(win, 0.8)


def shoot(name, title="Terminal GUI Toolbox"):
    SH.TITLE = title
    hits = SH.find_titled()
    if not hits:
        print("没找到窗口", title)
        return
    img = SH.capture(hits[-1])
    if img is None:
        print("capture 返回空")
        return
    out = HERE / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    p = out / name
    img.save(p)
    print("saved", p, img.size)


shoot("hosts_status.png")

# 编辑一台 Linux 主机：看私钥密码 + 保存勾 + sudo 密码
rec = win.profiles.get("gitee.com")
dlg = forms.FormDialog(win, "编辑主机", host_fields(rec), width=580)
pump(dlg, 1.2)
if dlg._checks.get("sudo"):
    dlg._checks["sudo"].set(True)
pump(dlg, 0.4)
shoot("host_form.png", "编辑主机")
try:
    dlg.destroy()
except Exception:  # noqa: BLE001
    pass

# 再抓一张「目标系统 = Windows」的样子
dlg2 = forms.FormDialog(win, "添加主机", host_fields({"proto": "ssh"}), width=580)
pump(dlg2, 0.5)
dlg2._vars["ostype"].set("Windows")
pump(dlg2, 0.4)
shoot("host_form_win.png", "添加主机")
try:
    dlg2.destroy()
except Exception:  # noqa: BLE001
    pass

try:
    win.destroy()
except Exception:  # noqa: BLE001
    pass
