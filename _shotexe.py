# -*- coding: utf-8 -*-
"""冒烟测试打包后的 exe：启动 → 等窗口出现 → PrintWindow 截图 → 结束进程。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    ctypes.windll.user32.SetProcessDPIAware()

HERE = Path(__file__).resolve().parent
EXE = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "VPNCertConsole.exe"
TITLE = "Client Cert Console"
OUT = Path(r"C:\Users\Thomas\AppData\Local\Temp\vpn_exe.png")

u = ctypes.windll.user32
k32 = ctypes.windll.kernel32


def find_titled():
    hits = []

    def cb(h, _l):
        if u.IsWindowVisible(h):
            n = u.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                if TITLE in b.value:
                    hits.append(h)
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return hits


def kill_titled():
    for h in find_titled():
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        k32.TerminateProcess(k32.OpenProcess(1, False, pid.value), 0)


def kill_tree(p):
    """连子进程一起收掉。

    onefile 打包出来是 bootloader + 子进程两条同名进程，窗口在子进程里。
    只 terminate bootloader 的话，子进程会带着窗口继续活着（2026-10-25 实测：
    terminate 之后 20 秒，子进程、窗口、互斥量全都还在）。

    原来这里还有一句 kill_titled()，是按窗口标题扫全桌面的 —— 用户自己开着的
    那个实例标题一模一样，会被一起打死。现在只收本次起的这棵进程树。
    """
    if p is None or p.poll() is not None:
        return
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                       capture_output=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        p.wait(timeout=12)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


def grab(hwnd):
    class R(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                    ("r", ctypes.c_long), ("b", ctypes.c_long)]

    r = R()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.r - r.l, r.b - r.t
    hdc = u.GetWindowDC(hwnd)
    mdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
    bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
    ctypes.windll.gdi32.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, 2)

    class BMI(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]
        _pack_ = 1

    bi = BMI()
    bi.biSize = ctypes.sizeof(BMI)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount = 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    got = ctypes.windll.gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    ctypes.windll.gdi32.DeleteObject(bmp)
    ctypes.windll.gdi32.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    if not ok or got == 0:
        return None, (w, h)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
    return img.convert("RGB"), (w, h)


kill_titled()
time.sleep(0.6)
before = set(find_titled())

t0 = time.time()
proc = subprocess.Popen([str(EXE), "--instance-key", "shot"],
                        cwd=str(EXE.parent))
print(f"launched pid={proc.pid}")

hwnd = None
for _ in range(120):
    time.sleep(0.25)
    fresh = [h for h in find_titled() if h not in before]
    if fresh:
        hwnd = fresh[0]
        break

if not hwnd:
    print("FAIL: 窗口未出现")
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    sys.exit(1)

print(f"window up in {time.time() - t0:.1f}s  hwnd={hwnd}")
time.sleep(1.6)
u.ShowWindow(hwnd, 9)
u.SetForegroundWindow(hwnd)
time.sleep(1.0)

img, size = grab(hwnd)
if img:
    img.save(OUT)
    print(f"saved {OUT}  {size}")
else:
    print(f"FAIL: 抓图失败 size={size}")

data = EXE.parent / "data"
print("data dir:", data, "exists =", data.exists(),
      "children =", [p.name for p in data.iterdir()] if data.exists() else [])

kill_tree(proc)
print("EXIT", proc.poll())
