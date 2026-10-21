# -*- coding: utf-8 -*-
"""截图：文件管理页的「双栏」模式（同进程建窗 + PrintWindow）。

默认那张 files.png 是「设备单栏」，这张补一张双栏的：
不这么拍的话，双栏布局只有测试结论、没有肉眼确认。

    python _shot_files_both.py [--dark]
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time
from pathlib import Path

from PIL import Image

sys.argv = ["x"]
HERE = Path(__file__).resolve().parent

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    ctypes.windll.user32.SetProcessDPIAware()

u = ctypes.windll.user32
g = ctypes.windll.gdi32

import app as A


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BIH), ("bmiColors", wt.DWORD * 3)]


def capture(hwnd):
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    hdc = u.GetDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, 2)
    bi = BINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    if not ok:
        return None
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)


a = A.ToolboxApp()
if "--dark" in sys.argv:
    a.apply_theme("dark")
a.nav.select("files")
for _ in range(60):
    a.update()
    time.sleep(0.02)
a.seg_fl.select("both")
end = time.time() + 3.0
while time.time() < end:
    a.update()
    time.sleep(0.02)

hwnd = int(a.winfo_id())
parent = u.GetParent(hwnd)
img = capture(parent or hwnd)
out = HERE / "screenshots" / "files_both.png"
out.parent.mkdir(parents=True, exist_ok=True)
if img is None:
    print("抓屏失败")
else:
    img.save(out)
    print(f"saved {out}  {img.size}")
a.destroy()
