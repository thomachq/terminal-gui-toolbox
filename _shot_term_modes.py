# -*- coding: utf-8 -*-
"""终端两种输入形态各拍一张（真连本机、真跑命令）。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _shot_term as T
import _shots as SH

OUT = Path(__file__).resolve().parent / "screenshots"


def snap(win, name: str) -> None:
    # 抓屏前把终端拉到最前：BitBlt 取的是屏幕像素，被别的窗口压住就拍到别人
    try:
        win.deiconify()
        win.lift()
        win.attributes("-topmost", True)
        win.focus_force()
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.6)
    hits = T.find_prefix("终端 ·")
    h = hits[0] if hits else 0
    img = T.capture_screen(h) if h else None
    if img:
        img.save(OUT / f"{name}.png")
        print(f"{name}.png OK {img.size}")
    else:
        print(f"{name}.png 抓屏失败 hwnd={h}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    p = Path("data/settings.json")
    d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    d.update({"lock_enabled": False, "lock_hash": "", "lock_timeout": 0,
              "lock_auto_login": False, "lock_pass_enc": ""})
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    import app as APP
    import ui.terminal as TM

    a = APP.ToolboxApp()
    a.geometry("+80+40")
    a.attributes("-topmost", True)
    T.pump(a, 0.6)

    a.v_proto.set("local")
    a._proto_changed()
    a.do_connect()
    for _ in range(200):
        T.pump(a, 0.05)
        if a.session is not None:
            break
    T.pump(a, 0.8)

    a.open_terminal()
    T.pump(a, 0.8)
    w = a._term
    w.geometry("+120+110")
    w.attributes("-topmost", True)
    T.pump(a, 0.4)

    # ---- 形态一：输入框
    w.seg_mode.select("box")
    T.pump(a, 0.2)
    for cmd in ("echo 输入框形态 · 上面是程序输出", "hostname", "Get-Date -Format HH:mm:ss"):
        w.v_in.set(cmd)
        w._submit()
        T.pump(a, 1.2)
    snap(w, "terminal_box")

    # ---- 形态二：行内
    w.seg_mode.select("inline")
    T.pump(a, 0.3)
    for cmd in ("echo 行内形态 · 提示符直接在正文里", "whoami", "echo 敲到一半时输出也不会冲掉这行"):
        w.text.insert("input_start", cmd)
        w._submit()
        for _ in range(120):
            T.pump(a, 0.05)
            if not w._busy:
                break
    # 留一条敲了一半的内容在里面，展示「输入区」
    w.text.insert("input_start", "cd C:\\")
    T.pump(a, 0.3)
    snap(w, "terminal_inline")

    try:
        a._close_to_tray = False
        a.destroy()
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
