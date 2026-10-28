# -*- coding: utf-8 -*-
"""截图：两台设备各一个终端页签（v1.0.31 多终端改造的验收图）。

    python _shotmultiterm.py
产出：_shots/multiterm.png（整个主窗）、_shots/multiterm_tabs.png（页签栏局部）
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import struct
import sys
import time
from ctypes import byref
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PIL import Image  # noqa: E402

from core.detect import DeviceInfo  # noqa: E402

OUT = HERE / "_shots"
OUT.mkdir(exist_ok=True)


class FakeSession:
    def __init__(self, host, name):
        self.host = host
        self.user = "root"
        self.port = 22
        self.kind = "ssh"
        self.label = name
        self.alive = True
        self.host_kind = "unix"
        self.vendor = ""
        self.tracer = None
        self.is_root = True
        self.elevated = False

    def close(self):
        self.alive = False

    def run(self, cmd, timeout=30, admin=False):
        if cmd.strip().startswith(("pwd", "cd")):
            return 0, "/root\n", ""
        return 0, "", ""

    def run_traced(self, cmd, timeout=30):
        return self.run(cmd, timeout)

    def run_admin(self, cmd, timeout=30):
        return self.run(cmd, timeout, admin=True)

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


def pump(a, s):
    t0 = time.time()
    while time.time() - t0 < s:
        a.update()
        time.sleep(0.01)


def print_window(hwnd):
    u = ctypes.windll.user32
    g = ctypes.windll.gdi32
    r = wt.RECT()
    u.GetWindowRect(hwnd, byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    sdc = u.GetDC(0)
    mdc = g.CreateCompatibleDC(sdc)
    bmp = g.CreateCompatibleBitmap(sdc, w, h)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, 2)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                    ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    bih = BIH()
    bih.biSize = ctypes.sizeof(BIH)
    bih.biWidth, bih.biHeight = w, -h
    bih.biPlanes, bih.biBitCount, bih.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, byref(bih), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(0, sdc)
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")


def grab(name, win):
    try:
        win.attributes("-topmost", True)
        win.lift()
    except Exception:  # noqa: BLE001
        pass
    pump(win, 0.5)
    u = ctypes.windll.user32
    own = win.winfo_id()
    hwnd = u.GetAncestor(own, 2) or own
    img = print_window(hwnd)
    dst = OUT / f"{name}.png"
    if img is not None:
        img.save(dst)
        print(f"saved {dst} {img.size}")
    return img


def main() -> int:
    import wbtui as U  # noqa: F401
    import app as A

    a = A.ToolboxApp()
    pump(a, 1.2)
    try:
        if a.state() == "zoomed":
            a.state("normal")
    except Exception:  # noqa: BLE001
        pass
    pump(a, 0.4)

    s1 = FakeSession("192.168.3.15", "Synology_DSM_VM")
    s2 = FakeSession("192.168.3.48", "飞牛OS")
    slot1 = a.sessions.add(s1, dev("Synology_DSM_VM"), "Synology_DSM_VM")
    slot2 = a.sessions.add(s2, dev("飞牛OS"), "飞牛OS")
    a._after_switch()
    pump(a, 0.3)

    a._switch_device(slot1.sid)
    pump(a, 0.5)
    p1 = a._term_live(a._term_key(slot1.sid))
    if p1 is not None:
        p1.write("$ docker ps --format 'table {{.Names}}\\t{{.Status}}'\n", "cmd")
        p1.write("NAMES            STATUS\nvaultwarden      Up 3 days\n"
                 "gitea            Up 3 days\n", "out")

    a._switch_device(slot2.sid)
    pump(a, 0.5)
    p2 = a._term_live(a._term_key(slot2.sid))
    if p2 is not None:
        p2.write("$ nginx -t\n", "cmd")
        p2.write("nginx: configuration file /usr/trim/nginx/conf/nginx.conf "
                 "test is successful\n", "out")

    pump(a, 0.6)
    print("页签:", [a.tabbar.label_of(k) for k in a.tabbar.tabs])
    print("面板:", list(a._term_panes))
    grab("multiterm", a)

    # 页签栏局部（用户报的就是这一条）
    try:
        bar = a.tabbar
        x, y = bar.winfo_rootx(), bar.winfo_rooty()
        w, h = bar.winfo_width(), bar.winfo_height()
        img = Image.open(OUT / "multiterm.png")
        ox = a.winfo_rootx()
        oy = a.winfo_rooty()
        crop = img.crop((x - ox, y - oy, x - ox + w, y - oy + h))
        crop.save(OUT / "multiterm_tabs.png")
        print("saved", OUT / "multiterm_tabs.png", crop.size)
    except Exception as e:  # noqa: BLE001
        print("局部裁剪失败:", e)

    try:
        a.destroy()
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
