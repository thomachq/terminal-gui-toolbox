# -*- coding: utf-8 -*-
"""启动窗口 → 用 PrintWindow 从窗口 DC 取图 → 关闭。

本机桌面被壁纸层覆盖，普通抓屏拿不到窗口，所以走 PrintWindow。

    python _shot.py cert
    python _shot.py conn --dark
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
OUT = Path(r"C:\Users\Thomas\AppData\Local\Temp")
TITLE = "Client Cert Console"

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    ctypes.windll.user32.SetProcessDPIAware()

u = ctypes.windll.user32
g = ctypes.windll.gdi32

page = sys.argv[1] if len(sys.argv) > 1 else "conn"
extra = sys.argv[2:]


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


def kill_titled():
    k = ctypes.windll.kernel32
    for h in find_titled():
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        k.TerminateProcess(k.OpenProcess(1, False, pid.value), 0)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def capture(hwnd):
    r = wt.RECT()
    u.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    hdc = u.GetDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, 2)          # PW_RENDERFULLCONTENT
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h                # 负值 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0            # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    if not ok:
        return None
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)


kill_titled()
time.sleep(0.5)
before = set(find_titled())

proc = subprocess.Popen([PY, str(HERE / "vpncert_gui.py"), "--page", page, *extra],
                        cwd=str(HERE))

hwnd = None
for _ in range(80):
    time.sleep(0.25)
    fresh = [h for h in find_titled() if h not in before]
    if fresh:
        hwnd = fresh[0]
        break
if not hwnd:
    proc.terminate()
    raise SystemExit(f"窗口没起来（page={page}）")

time.sleep(2.0)
img = capture(hwnd)
dst = OUT / f"vpn_{page}{'_dark' if '--dark' in extra else ''}.png"
if img is None:
    print("PrintWindow 失败")
else:
    img.save(dst)
    samples = [img.getpixel((img.width // 2, img.height - 30)),
               img.getpixel((img.width - 20, 10))]
    print(f"saved {dst}  {img.size}  samples={samples}")

proc.terminate()
try:
    proc.wait(timeout=5)
except Exception:  # noqa: BLE001
    proc.kill()
