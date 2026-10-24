# -*- coding: utf-8 -*-
"""连接页三种形态截图：本机（Windows，只有 UAC 勾选）/ SSH / Telnet。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _shots as SH

OUT = Path(__file__).resolve().parent / "screenshots"


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
    a.geometry("+50+30")
    pump(a, 1.0)
    a.nav.select("conn")
    pump(a, 0.6)

    h = (SH.find_titled() or [0])[0]
    for proto, tag in (("local", "conn_local"), ("ssh", "conn_ssh"),
                       ("telnet", "conn_telnet")):
        a.v_proto.set(proto)
        a._proto_changed()
        pump(a, 0.7)
        vis = {
            "档案": bool(a.row_prof.winfo_manager()),
            "地址": bool(a.row_addr.winfo_manager()),
            "认证": bool(a.row_auth.winfo_manager()),
            "提权区": bool(a.row_elev.winfo_manager()),
        }
        print(f"{proto:7s} 可见：{vis}")
        if h:
            img = SH.capture(h)
            if img:
                img.save(OUT / f"{tag}.png")
                print(f"  {tag}.png OK {img.size}")

    a.destroy()


if __name__ == "__main__":
    main()
