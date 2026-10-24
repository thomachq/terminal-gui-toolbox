# -*- coding: utf-8 -*-
"""终端窗口截图（进程内，抓完即关）。"""
from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _shots as SH          # 复用 capture / find_titled
import ctypes
import ctypes.wintypes as wt

from PIL import Image

OUT = Path(__file__).resolve().parent / "screenshots"
u = ctypes.windll.user32
g = ctypes.windll.gdi32


def capture_screen(hwnd):
    """从屏幕 DC 直接抓窗口矩形。

    PrintWindow 对 Tk 的 Toplevel 经常只画得出标题栏（客户区是画布，
    窗口没完全 exposure 时是空白），所以这里先置前再 BitBlt 屏幕像素。
    """
    u.ShowWindow(hwnd, 9)              # SW_RESTORE
    u.SetForegroundWindow(hwnd)
    u.BringWindowToTop(hwnd)
    for _ in range(12):
        time.sleep(0.05)
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    sdc = u.GetDC(0)
    mdc = g.CreateCompatibleDC(sdc)
    bmp = g.CreateCompatibleBitmap(sdc, w, h)
    g.SelectObject(mdc, bmp)
    g.BitBlt(mdc, 0, 0, w, h, sdc, r.left, r.top, 0x00CC0020)   # SRCCOPY
    bi = SH.BINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(SH.BIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(0, sdc)
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)


def find_prefix(prefix: str):
    hits = []

    def cb(h, _l):
        if u.IsWindowVisible(h):
            n = u.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                if b.value.strip().startswith(prefix):
                    hits.append(h)
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return hits


def pump(root, sec):
    end = time.time() + sec
    while time.time() < end:
        try:
            root.update()
        except tk.TclError:
            return
        time.sleep(0.02)


def main():
    import json
    # 备份 + 退出还原：别把用户真实设的锁屏密码当成自测垃圾清掉
    import _lockoff

    _lockoff.unlock(Path("data/settings.json"))

    import app as APP
    a = APP.ToolboxApp()
    a.geometry("+80+40")
    a.attributes("-topmost", True)
    pump(a, 0.6)

    a.v_proto.set("local")
    a._proto_changed()
    a.do_connect()
    for _ in range(200):
        pump(a, 0.05)
        if a.session is not None:
            break
    pump(a, 1.0)

    a.open_terminal()
    pump(a, 0.8)
    w = a._term
    w.geometry("+120+120")
    w.attributes("-topmost", True)
    pump(a, 0.5)

    # 敲点真命令，让窗口里有内容
    for cmd in ("echo 终端已接通 · hostname 是本机名",
                "hostname",
                "cd C:\\",
                "Get-Location"):
        w.v_in.set(cmd)
        w._submit()
        pump(a, 1.4)
    # 再让主程序跑一条，验证「同步主程序命令」
    a.do_tool("ip")
    pump(a, 4.0)

    OUT.mkdir(parents=True, exist_ok=True)

    def hwnd_of(win):
        """按窗口标题精确找 top-level：Tk 的 winfo_id 给的是内部 child，
        GetAncestor 在这个 DPI 下会摸到一个尺寸不对的宿主窗口。"""
        want = win.title().strip()
        hits = []

        def cb(h, _l):
            n = u.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                if b.value.strip() == want:
                    r = wt.RECT()
                    u.GetWindowRect(h, ctypes.byref(r))
                    area = (r.right - r.left) * (r.bottom - r.top)
                    if area > 100:
                        hits.append((area, h))
            return True

        u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
        hits.sort(reverse=True)
        return hits[0][1] if hits else 0

    hit = hwnd_of(w)
    print("终端 hwnd =", hit, "标题 =", w.title())
    img = capture_screen(hit)
    if img:
        img.save(OUT / "terminal.png")
        print("terminal.png OK", img.size)
    else:
        print("终端抓屏失败")

    # 深色主题再来一张
    a._toggle_theme()
    pump(a, 0.8)
    img = capture_screen(hwnd_of(w))
    if img:
        img.save(OUT / "terminal_dark.png")
        print("terminal_dark.png OK", img.size)
    a._toggle_theme()
    pump(a, 0.4)

    # 主窗口那张顺手也拍了
    hits = [hwnd_of(a)]
    if hits:
        img = capture_screen(hits[0])
        if img:
            img.save(OUT / "main_with_term_btn.png")
            print("main_with_term_btn.png OK", img.size)

    try:
        a._close_to_tray = False
        a.destroy()
    except tk.TclError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
