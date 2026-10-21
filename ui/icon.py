# -*- coding: utf-8 -*-
"""窗口图标 + 任务栏身份。

两件事常被混在一起：

  `iconbitmap`           决定**窗口左上角**和任务栏缩略图上画什么
  AppUserModelID         决定任务栏把窗口**归到哪个组**、用哪个图标

打包后的 exe 图标由 .spec 的 icon 决定；但开发态（python app.py）没有 exe 图标，
所以要显式 iconbitmap，否则任务栏是个默认的 Tk 羽毛图标。
"""
from __future__ import annotations

import sys
from pathlib import Path


def apply_icon(win, ico: str | Path | None) -> bool:
    """给窗口（含之后创建的 Toplevel）设图标。"""
    if not ico:
        return False
    p = Path(ico)
    if not p.is_file():
        return False
    try:
        win.iconbitmap(default=str(p))
        return True
    except Exception:  # noqa: BLE001
        pass
    try:
        win.iconbitmap(str(p))
        return True
    except Exception:  # noqa: BLE001
        return False


def set_app_id(app_id: str = "TerminalToolbox.App.1") -> None:
    """让任务栏把本程序当成独立应用，而不是「python.exe」。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:  # noqa: BLE001
        pass


def set_window_icon_photo(win, png: str | Path | None) -> None:
    """补充：某些 DPI 下 iconbitmap 不生效，可用 PhotoImage 兜一次。"""
    if not png:
        return
    try:
        import tkinter as tk
        img = tk.PhotoImage(file=str(png))
        win.iconphoto(False, img)
        win._icon_photo = img          # 防被 GC 掉
    except Exception:  # noqa: BLE001
        pass
