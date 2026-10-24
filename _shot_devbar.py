# -*- coding: utf-8 -*-
"""抓「运维工具目标设备条」与「主机清单状态列」两张图（不入库）。

    python _shot_devbar.py

数据目录走 _shots.isolate() 的临时副本，绝不碰真实 data。
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.argv = [sys.argv[0], "--no-probe"]
sys.path.insert(0, str(HERE))

import _shots as SH  # noqa: E402

# 会调 host_probe 真探端口，先复制一份数据出来改
SH.isolate("tbshot_devbar_")

import app as appmod  # noqa: E402


def pump(win, sec=0.4):
    end = time.time() + sec
    while time.time() < end:
        try:
            win.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.01)


def shoot(name):
    """抓**本进程**这个窗口。

    不能按标题找：用户自己也可能开着一个同名窗口（实测就有两个），
    find_titled() 取到的可能是别人那个，PrintWindow 直接返回空。
    改用 Tk 自己的 wm_frame 拿句柄。
    """
    SH.TITLE = "Terminal GUI Toolbox"
    out = HERE / "screenshots"
    out.mkdir(exist_ok=True)
    try:
        hwnd = int(win.wm_frame(), 16)
    except Exception:  # noqa: BLE001
        hwnd = 0
    for _ in range(4):
        img = SH.capture(hwnd) if hwnd else None
        if img is not None:
            p = out / name
            img.save(p)
            print("saved", p, img.size)
            return
        time.sleep(0.6)
    print("没抓到", name, "hwnd=", hwnd)


win = appmod.ToolboxApp()
win.geometry("1240x820+40+40")
win.deiconify()
win.lift()
try:
    win.attributes("-topmost", True)   # PrintWindow 在主窗口被遮挡时偶尔返回空
except Exception:  # noqa: BLE001
    pass
pump(win, 1.6)

# ---- 图 1：运维工具页 + 目标设备条
win._show("sys")
pump(win, 1.2)
SH.TITLE = "Terminal GUI Toolbox"
shoot("devbar_sys.png")

# ---- 图 2：主机清单状态列
win._show("hosts")
pump(win, 0.6)
win.host_refresh()          # --no-probe 下 _show 不会自己刷，得显式来一次
pump(win, 0.6)
# 塞几条代表性结果，看观感（不真探，免得等外网超时）
from pages.hosts_page import probe_key as _pk  # noqa: E402
now = int(time.time())
for rec in win.profiles.profiles:
    k = _pk(rec)
    if not k:
        continue
    if rec.get("name") in ("chromas.synology.me", "gitee.com", "github.com"):
        win._host_probe[k] = ("ok", now)
    elif rec.get("name") == "gitcode.com":
        win._host_probe[k] = ("bad", now)
win._hosts_apply_filter()
win.host_refresh()   # 状态是 hosts_load 时算的，塞完结果得再刷一遍
pump(win, 0.8)
shoot("hosts_status2.png")

# ---- 图 3：设备标签点开 → 终端
win._switch_device("", open_term=True)
pump(win, 1.4)
shoot("devtab_term.png")

try:
    win.destroy()
except Exception:  # noqa: BLE001
    pass
