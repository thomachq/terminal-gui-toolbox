# -*- coding: utf-8 -*-
"""窗口几何：按屏幕工作区自适应，并记住上次退出时的尺寸 / 位置 / 最大化。

为什么不用 Tk 的 `winfo_screenwidth/height`：

  那两个给的是**整块屏幕**，包含任务栏。默认窗口是 1360×880 **逻辑**像素，
  在 150% 缩放的笔记本上会被算成 2040×1320 物理像素 —— 比屏幕还高，
  一启动就顶出屏幕外，任务栏盖住底部、标题栏跑到屏幕上方看不到。
  真正该用的是 `SPI_GETWORKAREA`（Windows 的「工作区」，已扣掉任务栏与
  停靠栏）。macOS / Linux 上 SPI 不可用，退回 Tk 的整屏尺寸再手动让出
  一点边距，宁可小一点也不能超出屏幕。

为什么坐标要重新夹一遍：

  上次窗口停在副屏上，这次副屏没插 —— 直接照搬坐标就会把窗口开到
  屏幕外面，用户只能看到任务管理器。所以保存的是绝对坐标，恢复前必须
  按**当前**工作区重新夹一次，夹不下就居中。
"""
from __future__ import annotations

import ctypes
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

SPI_GETWORKAREA = 0x0030
DEFAULT_MARGIN = 16          # 窗口边缘与工作区之间至少留的空隙


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def work_area(fallback: tuple[int, int, int, int] = (0, 0, 1280, 720)
              ) -> tuple[int, int, int, int]:
    """返回主屏工作区 (x, y, 宽, 高)，已扣掉任务栏。

    多屏时 SPI_GETWORKAREA 只给主屏 —— 这里也只要主屏：窗口恢复到一个
    一定存在的地方，比恢复到「上次那块可能已经拔掉的屏」靠谱。
    """
    try:
        r = _RECT()
        ok = ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined]
            SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
        if ok:
            w = int(r.right - r.left)
            h = int(r.bottom - r.top)
            if w >= 320 and h >= 240:
                return (int(r.left), int(r.top), w, h)
    except (AttributeError, OSError, ValueError):
        pass
    return fallback


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """取某个窗口的**外框**屏幕矩形 (x, y, 宽, 高)。

    Tk 的 `geometry()` 说的是客户区尺寸，标题栏（约 30~45 物理像素）和
    边框不算在内。只按客户区算，窗口整体还是会比工作区高出那一截，
    底部正好压到任务栏下面 —— 所以映射完成后要拿外框实测一次。
    """
    try:
        r = _RECT()
        if not ctypes.windll.user32.GetWindowRect(  # type: ignore[attr-defined]
                int(hwnd), ctypes.byref(r)):
            return None
        w = int(r.right - r.left)
        h = int(r.bottom - r.top)
        if w <= 0 or h <= 0:
            return None
        return (int(r.left), int(r.top), w, h)
    except (AttributeError, OSError, ValueError):
        return None


@dataclass
class Box:
    """一个窗口矩形 + 是否最大化。"""
    x: int
    y: int
    w: int
    h: int
    zoomed: bool = False

    def geometry(self) -> str:
        return f"{self.w}x{self.h}+{self.x}+{self.y}"


def _int(v, default: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n


def fit_size(want_w: int, want_h: int, area: tuple[int, int, int, int],
             min_w: int, min_h: int,
             margin: int = DEFAULT_MARGIN) -> tuple[int, int]:
    """把想要的尺寸压进工作区。

    「最小值」也要让位：屏幕本身比 minsize 还小时，先保证放得下，
    否则 Windows 会按 minsize 开一个必然超屏的窗口（就是本轮那个 bug）。
    """
    _ax, _ay, aw, ah = area
    max_w = aw - margin * 2
    max_h = ah - margin * 2
    w = min(want_w, max_w)
    h = min(want_h, max_h)
    w = max(w, min(min_w, max_w))
    h = max(h, min(min_h, max_h))
    return max(240, w), max(160, h)


def restore_box(data: dict | None, area: tuple[int, int, int, int],
                scale: float, default_w: int, default_h: int,
                min_w: int, min_h: int,
                margin: int = DEFAULT_MARGIN) -> Box:
    """算出这次启动该用的窗口矩形。

    `data` 是上次退出时存下的（物理像素）。`scale` 是当前 DPI 缩放，
    存盘时的缩放记在 `data["scale"]`；两者不同就按比例换算 —— 换了显示器
    或改了缩放比例之后，窗口不会突然缩成一小块。
    """
    data = data if isinstance(data, dict) else {}
    ax, ay, aw, ah = area

    saved_scale = 0.0
    try:
        saved_scale = float(data.get("scale") or 0)
    except (TypeError, ValueError):
        saved_scale = 0.0
    f = 1.0
    if saved_scale > 0.2 and scale > 0.2 and abs(saved_scale - scale) > 0.01:
        f = scale / saved_scale

    want_w = _int(data.get("w"), 0)
    want_h = _int(data.get("h"), 0)
    if want_w <= 0 or want_h <= 0:
        want_w, want_h = default_w, default_h
    elif f != 1.0:
        want_w, want_h = int(want_w * f), int(want_h * f)

    w, h = fit_size(want_w, want_h, area, min_w, min_h, margin)

    zoomed = bool(data.get("zoomed"))
    # 最大化状态下存的 x/y 是「最大化时的左上角」，拿来做普通状态的位置
    # 会把窗口顶到 (8,8)；所以最大化时优先用普通状态的那份矩形。
    if zoomed and ("nx" in data or "ny" in data):
        want_x = _int(data.get("nx"), ax)
        want_y = _int(data.get("ny"), ay)
        nw = _int(data.get("nw"), 0)
        nh = _int(data.get("nh"), 0)
        if nw > 0 and nh > 0:
            if f != 1.0:
                nw, nh = int(nw * f), int(nh * f)
            w, h = fit_size(nw, nh, area, min_w, min_h, margin)
        if f != 1.0:
            want_x, want_y = int(want_x * f), int(want_y * f)
    else:
        want_x = _int(data.get("x"), ax)
        want_y = _int(data.get("y"), ay)

    if not data:
        # 第一次启动：居中，别贴着左上角
        x = ax + max(0, (aw - w) // 2)
        y = ay + max(0, (ah - h) // 2)
    else:
        x = min(max(want_x, ax), ax + max(0, aw - w))
        y = min(max(want_y, ay), ay + max(0, ah - h))
    return Box(x=x, y=y, w=w, h=h, zoomed=zoomed)


class WinState:
    """data/window.json —— 只存窗口几何，坏了就当没有，绝不影响启动。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {}
        self.load()

    def load(self) -> None:
        try:
            if self.path.is_file():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.data = raw
        except (OSError, ValueError):
            self.data = {}

    def save(self, box: dict) -> None:
        """原子写；失败只吞掉 —— 记不住窗口位置不算错误。"""
        if not isinstance(box, dict) or not box:
            return
        box = dict(box)
        box["saved_at"] = int(time.time())
        self.data = box
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(box, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass
