# -*- coding: utf-8 -*-
"""截图：安全中心 → 证书管理（确认新按钮没把动作条挤坏）。

    python _shotcertinstall.py
产出：_shots/cert_install.png
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time
from ctypes import byref
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PIL import Image  # noqa: E402

OUT = HERE / "_shots"
OUT.mkdir(exist_ok=True)


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
    a.nav.select("sec")
    pump(a, 0.6)
    a._sec_tab("cert")
    pump(a, 0.8)

    # 自己造一张本机证书，让表格里有行可看
    try:
        from core import servercert as SC
        c, k = SC.make_selfsigned("nas.example.local",
                                  ["nas.example.local", "192.168.3.12"], days=365)
        a.cert_store().add("演示-nas", c, k, overwrite=True)
        a.cert_refresh()
        pump(a, 0.6)
    except Exception as e:  # noqa: BLE001
        print("造样本失败:", e)

    a.attributes("-topmost", True)
    a.lift()
    pump(a, 0.5)
    u = ctypes.windll.user32
    hwnd = u.GetAncestor(a.winfo_id(), 2) or a.winfo_id()
    img = print_window(hwnd)
    dst = OUT / "cert_install.png"
    if img is not None:
        img.save(dst)
        print("saved", dst, img.size)
    try:
        a.destroy()
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
