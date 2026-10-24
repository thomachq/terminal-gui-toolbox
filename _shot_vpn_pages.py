# -*- coding: utf-8 -*-
"""内嵌后的控制台五个子页逐个截图（进程内，抓完即关）。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _shots as SH
from PIL import Image

OUT = Path(__file__).resolve().parent / "screenshots"
u = ctypes.windll.user32
g = ctypes.windll.gdi32


def hwnd_of(win):
    want = win.title().strip()
    hits = []

    def cb(h, _l):
        n = u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if b.value.strip() == want and u.IsWindowVisible(h):
                r = wt.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                area = (r.right - r.left) * (r.bottom - r.top)
                if area > 100000:
                    hits.append((area, h))
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    hits.sort(reverse=True)
    return hits[0][1] if hits else 0


def pump(root, sec):
    end = time.time() + sec
    while time.time() < end:
        try:
            root.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.02)


def main():
    # 备份 + 退出还原：别把用户真实设的锁屏密码当成自测垃圾清掉
    import _lockoff

    _lockoff.unlock(Path("data/settings.json"))

    import app as APP
    a = APP.ToolboxApp()
    a.geometry("+60+40")
    pump(a, 0.8)
    a.nav.select("vpn")
    pump(a, 1.6)

    con = a.vpn_console
    hwnd = hwnd_of(a)
    print("主窗口 hwnd =", hwnd)

    for key, tag in (("cert", "vpn_cert"), ("pack", "vpn_pack"),
                     ("deploy", "vpn_deploy"), ("cons", "vpn_cons")):
        con.nav.select(key)
        pump(a, 0.9)
        # PrintWindow 直接渲染窗口内容，不怕被别的窗口压在上面；
        # 抓主窗口整张，再按控制台的相对偏移裁出来。
        full = SH.capture(hwnd)
        if full is None:
            print(f"  {tag:12s} 抓屏失败")
            continue
        ox = con.winfo_rootx() - a.winfo_rootx()
        oy = con.winfo_rooty() - a.winfo_rooty()
        # 抓的是整窗（含标题栏），Tk 给的是客户区坐标 —— 补上这段偏移
        dx, dy = SH.client_offset(hwnd)
        ox, oy = ox + dx, oy + dy
        w, h = con.winfo_width(), con.winfo_height()
        img = full.crop((ox, oy, min(ox + w, full.width), min(oy + h, full.height)))
        img.save(OUT / f"{tag}.png")
        print(f"  {tag:12s} OK {img.size[0]}x{img.size[1]}  offset {ox},{oy}")

    a.destroy()


if __name__ == "__main__":
    main()
