# -*- coding: utf-8 -*-
"""抓地址库对话框的截图（三组各一张）。

PrintWindow 拿不到 Tk 的自绘控件，所以还是「置前 + 按窗口矩形抓屏」。
抓完立刻关窗，不在用户桌面上留东西。

    python _shotipick.py
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tkinter as tk  # noqa: E402

import wbtui as U  # noqa: E402

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    ctypes.windll.user32.SetProcessDPIAware()

OUT = HERE / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

root = tk.Tk()
# 父窗口必须先真正映射：dialog 是 transient，父窗口 withdraw 时它也跟着
# 不映射，winfo_width() 会一直是 1，抓出来就是 1x1 的空白图。
root.geometry("320x200+60+60")
U.apply_theme("light")
root.update()

from ui.ipick import IpPickDialog  # noqa: E402

dlg = IpPickDialog(root, title="地址库")
dlg.attributes("-topmost", True)
dlg.update()
dlg.lift()
dlg.focus_force()
for _ in range(3):
    dlg.update()
    time.sleep(0.3)


def grab(name: str) -> None:
    from PIL import ImageGrab
    dlg.update_idletasks()
    dlg.update()
    x, y = dlg.winfo_rootx(), dlg.winfo_rooty()
    w, h = dlg.winfo_width(), dlg.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    dst = OUT / f"{name}.png"
    img.save(dst)
    print(f"saved {dst}  {img.size}")


try:
    # 勾两条，让 ✓ 和底部汇总都出现在图里
    it = dlg.tb.rows()
    if len(it) >= 2:
        dlg._picked[it[0]["_key"]] = it[0]["_item"]
        dlg._picked[it[2]["_key"]] = it[2]["_item"]
        dlg._render()
    grab("ipick_common")

    dlg.seg.select("province")
    dlg._render()
    time.sleep(0.3)
    grab("ipick_province")

    dlg.seg.select("country")
    dlg._render()
    time.sleep(0.3)
    grab("ipick_country")
finally:
    try:
        dlg.grab_release()
    except tk.TclError:
        pass
    dlg.destroy()
    root.destroy()
