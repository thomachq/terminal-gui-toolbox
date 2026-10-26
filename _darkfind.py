# -*- coding: utf-8 -*-
"""暗色下扫「远程管理」页：找出仍然亮底的控件（白条根因定位）。"""
from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    ctypes.windll.user32.SetProcessDPIAware()

os.environ["TB_DATA_DIR"] = tempfile.mkdtemp(prefix="tbdark-")

import app as A
import wbtui as U

a = A.ToolboxApp()
a.geometry("1500x980+40+30")
a.update()
a.nav.select("rmt")
for _ in range(40):
    a.update()
    time.sleep(0.03)

U.apply_theme("dark")
for _ in range(40):
    a.update()
    time.sleep(0.03)


def lum(hexcol: str) -> float:
    h = hexcol.lstrip("#")
    if len(h) != 6:
        return -1
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


hits = []


def walk(w):
    try:
        bg = str(w.cget("bg"))
    except Exception:  # noqa: BLE001
        bg = ""
    if bg and lum(bg) > 0.75:
        try:
            hits.append((str(w), bg, w.winfo_width(), w.winfo_height(),
                         w.winfo_rooty()))
        except Exception:  # noqa: BLE001
            pass
    for c in w.winfo_children():
        walk(c)


walk(a)
hits.sort(key=lambda x: -x[2] * x[3])
for cls, bg, w, h, y in hits[:20]:
    print(f"{cls:60s} bg={bg} {w}x{h} y={y}")
print(f"共 {len(hits)} 个亮底控件")
a.destroy()
