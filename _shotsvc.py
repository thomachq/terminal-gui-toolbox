# -*- coding: utf-8 -*-
"""抓「隧道服务管理 · 服务」页的截图（喂假数据，不连设备）。

    python _shotsvc.py
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tkinter as tk  # noqa: E402

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    ctypes.windll.user32.SetProcessDPIAware()

OUT = HERE / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

root = tk.Tk()
root.geometry("1500x980+40+30")
root.title("服务页截图")
root.update()

import vpncert_gui  # noqa: E402
import wbtui as U  # noqa: E402

U.apply_theme("light")
app = vpncert_gui.App(root, embedded=True)
app.pack(fill="both", expand=True)
root.update()

# 喂一组「群晖上真的会出现」的探测结果
app._svc_os = "Linux"
app._svc_init = "syno"
app._svc = [
    {"key": "openvpn", "name": "OpenVPN", "kind": "openvpn", "installed": True,
     "active": True, "ports": [1194], "note": "证书/用户名口令两种认证都支持",
     "confs": ["/var/packages/VPNCenter/target/etc/openvpn/server.conf"]},
    {"key": "strongswan", "name": "IPSec / IKEv2（strongSwan）", "kind": "ipsec",
     "installed": True, "active": True, "ports": [500, 4500], "note": "",
     "confs": ["/etc/ipsec.conf"]},
    {"key": "xl2tpd", "name": "L2TP over IPSec（xl2tpd）", "kind": "l2tp",
     "installed": True, "active": True, "ports": [1701], "note": "",
     "confs": ["/etc/xl2tpd/xl2tpd.conf"]},
    {"key": "pptpd", "name": "PPTP（pptpd）", "kind": "pptp", "installed": True,
     "active": False, "ports": [1723],
     "note": "明文协议，只建议内网用", "confs": ["/etc/pptpd.conf"]},
]
app._svc_render()
app.lbl_svc_sys.configure(
    text="Linux · init=syno · 装了 4 个、在跑 3 个")
for row in (("alice", "203.0.113.5:51234", "10.8.0.2",
             "今天 09:10", "1.2 MB", "3.4 MB", "在线"),
            ("bob", "198.51.100.9:41000", "10.8.0.3",
             "今天 09:30", "300 KB", "600 KB", "在线"),
            ("carol", "192.0.2.77:39811", "10.8.0.4",
             "今天 10:02", "12 KB", "8 KB", "在线")):
    app.tree_cli.insert("", "end", values=row)
app.lbl_cli.configure(text="OpenVPN：3 个连接")
app.v_certmode.set("require —— 必须客户端证书（最安全）")
app.lbl_cert.configure(
    text="/var/packages/VPNCenter/target/etc/openvpn/server.conf · 当前模式 require")

app._show("svc")
for _ in range(4):
    root.update()
    time.sleep(0.25)


def grab(name: str) -> None:
    from PIL import ImageGrab
    # 必须每次重新置顶：切主题后窗口会被别的程序盖住，直接按坐标抓屏
    # 抓到的是别人（实测抓到 IDE 自己的界面）。
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    for _ in range(3):
        root.update()
        time.sleep(0.2)
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    dst = OUT / f"{name}.png"
    img.save(dst)
    print(f"saved {dst}  {img.size}")


try:
    grab("vpn_svc_light")
    U.apply_theme("dark")
    root.update()
    app._svc_render()
    time.sleep(0.4)
    grab("vpn_svc_dark")
finally:
    root.destroy()
