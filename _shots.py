# -*- coding: utf-8 -*-
"""八页 + 锁屏 一次性截图核对。

    python _shots.py            全部
    python _shots.py docker fw  只抓指定页

每页单独起一个进程（--page X），等它把数据拉完再抓，抓完立刻关掉。
锁屏那张会临时往 data\\settings.json 写一个锁，抓完原样还原。
"""
import ctypes
import ctypes.wintypes as wt
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
PY = sys.executable
OUT = HERE / "screenshots"
TITLE = "Terminal GUI Toolbox"
SETTINGS = HERE / "data" / "settings.json"

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    ctypes.windll.user32.SetProcessDPIAware()

u = ctypes.windll.user32
g = ctypes.windll.gdi32

# 页面 → 等待秒数（等懒加载把真实数据拉进表格）
WAIT = {
    "conn": 2.6,
    "tools": 3.2,
    "sys": 3.4,
    "files": 4.0,
    "routes": 5.0,
    "fw": 10.0,
    "docker": 4.5,
    "vpn": 3.0,
    "api": 3.0,
}


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BIH), ("bmiColors", wt.DWORD * 3)]


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


def client_offset(hwnd):
    """窗口矩形左上角 → 客户区左上角 的偏移（标题栏 + 边框）。

    capture() 抓的是 GetWindowRect 那一整块（含标题栏），而 Tk 的
    winfo_rootx/rooty 给的是客户区坐标。直接拿来当裁剪起点会整体
    上移一条标题栏，页面底部就被切掉一行 —— 之前「手动执行」被切
    就是栽在这里。
    """
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    pt = wt.POINT(0, 0)
    u.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x - r.left, pt.y - r.top


def shoot(tag, args, wait):
    before = set(find_titled())
    proc = subprocess.Popen([PY, str(HERE / "app.py"), *args], cwd=str(HERE))
    hwnd = None
    t0 = time.time()
    while time.time() - t0 < 25:
        time.sleep(0.25)
        fresh = [h for h in find_titled() if h not in before]
        if fresh:
            hwnd = fresh[0]
            break
    if not hwnd:
        proc.terminate()
        print(f"  {tag:8s} 窗口没起来")
        return False
    time.sleep(wait)
    try:
        img = capture(hwnd)
        if img is None:
            print(f"  {tag:8s} 抓屏失败")
            return False
        dst = OUT / f"{tag}.png"
        img.save(dst)
        print(f"  {tag:8s} OK  {img.size[0]}x{img.size[1]}  -> {dst.name}")
        return True
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:  # noqa: BLE001
            proc.kill()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    want = [a for a in sys.argv[1:] if not a.startswith("-")]
    pages = want or list(WAIT)

    print("== 页面截图 ==")
    for p in pages:
        if p in WAIT:
            shoot(p, ["--page", p], WAIT[p])

    if want and "lock" not in want:
        return
    if not want:
        pass
    print("== 锁屏 ==")
    backup = None
    if SETTINGS.is_file():
        backup = SETTINGS.with_suffix(".json.shotbak")
        shutil.copy2(SETTINGS, backup)
    try:
        sys.path.insert(0, str(HERE))
        import lockmgr
        st = lockmgr.Settings(SETTINGS)
        lm = lockmgr.LockManager(st)
        ok, msg = lm.set_password("lockdemo123", timeout=0)
        print(f"  临时设锁：{msg}")
        shoot("lock", [], 3.0)
    finally:
        if backup is not None:
            shutil.move(str(backup), str(SETTINGS))
        else:
            SETTINGS.unlink(missing_ok=True)
        print("  settings.json 已还原")


if __name__ == "__main__":
    main()
