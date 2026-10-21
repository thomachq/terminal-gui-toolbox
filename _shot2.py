# -*- coding: utf-8 -*-
"""截图：起窗 → 置顶 → 按窗口矩形抓屏 → 关窗。

PrintWindow 拿不到 Tk 9 的自绘控件，所以改成「置前 + 按矩形抓屏」。
抓完立刻把窗口关掉，不会留在用户桌面上。

    python _shot2.py conn
    python _shot2.py tools --dark
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
PY = sys.executable
OUT = HERE / "screenshots"
TITLE = "Terminal GUI Toolbox"

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    ctypes.windll.user32.SetProcessDPIAware()

u = ctypes.windll.user32
g = ctypes.windll.gdi32

page = sys.argv[1] if len(sys.argv) > 1 else "conn"
extra = sys.argv[2:]
dark = "--dark" in extra


def find_titled():
    hits = []

    def cb(h, _l):
        if u.IsWindowVisible(h):
            n = u.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                if b.value.strip() == TITLE:
                    hits.append(h)
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return hits


OUT.mkdir(parents=True, exist_ok=True)
before = set(find_titled())

proc = subprocess.Popen([PY, str(HERE / "app.py"), "--page", page, *extra],
                        cwd=str(HERE))

hwnd = None
for _ in range(100):
    time.sleep(0.25)
    fresh = [h for h in find_titled() if h not in before]
    if fresh:
        hwnd = fresh[0]
        break
if not hwnd:
    proc.terminate()
    raise SystemExit(f"窗口没起来（page={page}）")

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
    """PrintWindow 会把标题栏一起画进来，所以位图要按**窗口**矩形取，
    按客户区取会把底部状态栏裁掉。"""
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    hdc = u.GetDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, 2)       # PW_RENDERFULLCONTENT
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


time.sleep(2.4)
try:
    img = capture(hwnd)
    dst = OUT / f"{page}{'_dark' if dark else ''}.png"
    if img is None:
        print("PrintWindow 失败")
    else:
        img.save(dst)
        print(f"saved {dst}  {img.size}")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=6)
    except Exception:  # noqa: BLE001
        proc.kill()
