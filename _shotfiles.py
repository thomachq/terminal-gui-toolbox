# -*- coding: utf-8 -*-
"""文件管理页截图（进程内）：用来看双栏到底是左右还是上下。

    python _shotfiles.py both     # 双栏
    python _shotfiles.py remote   # 只有设备
    python _shotfiles.py local    # 只有本机

不连任何设备，只验布局 —— 远端那张卡会显示「还没有连上设备」的空态。
抓完立刻关窗，不留在桌面上。
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import app as A                       # noqa: E402
from _shot_term import capture_screen  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "both"
if mode not in ("both", "remote", "local"):
    raise SystemExit("mode 只能是 both / remote / local")


def pump(a, s: float) -> None:
    t0 = time.time()
    while time.time() - t0 < s:
        a.update()
        time.sleep(0.01)


a = A.ToolboxApp()
pump(a, 1.4)
try:
    if a.state() == "zoomed":
        a.state("normal")
except Exception:  # noqa: BLE001
    pass
pump(a, 0.5)

a.nav.select("files")
pump(a, 0.8)
a.seg_fl.select(mode)
pump(a, 1.6)

try:
    hwnd = ctypes.windll.user32.GetParent(a.winfo_id()) or a.winfo_id()
    img = capture_screen(hwnd)
    if img is None:
        raise RuntimeError("抓屏返回空")
    out = HERE / "screenshots" / f"files_{mode}.png"
    img.save(out)
    print(f"saved {out} ({img.width}x{img.height})")
except Exception as e:  # noqa: BLE001
    print(f"截图失败：{type(e).__name__}: {e}")

# 顺手把两栏的几何打出来，比看图更硬
try:
    for name, card in (("本机", a._fl_card_l), ("设备", a._fl_card_r)):
        gi = card.grid_info() if card.winfo_manager() == "grid" else {}
        print(f"{name}: manager={card.winfo_manager() or '未布局'} "
              f"row={gi.get('row')} col={gi.get('column')} "
              f"w={card.winfo_width()} h={card.winfo_height()} "
              f"x={card.winfo_x()} y={card.winfo_y()}")
    print(f"holder: {a._fl_holder.winfo_width()}x{a._fl_holder.winfo_height()}")
finally:
    a.destroy()
