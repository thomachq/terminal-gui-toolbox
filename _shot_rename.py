# -*- coding: utf-8 -*-
"""改名后的界面核对：主窗口导航 + 证书页两个子页（进程内，抓完即关）。"""
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
    p = Path("data/settings.json")
    d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    d.update({"lock_enabled": False, "lock_hash": "", "lock_timeout": 0,
              "lock_auto_login": False, "lock_pass_enc": ""})
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    import app as APP
    a = APP.ToolboxApp()
    a.geometry("+60+40")
    pump(a, 1.0)

    # 1) 主窗口整窗：看左侧导航末几项有没有被裁字
    hits = SH.find_titled()
    h = hits[0] if hits else 0
    if h:
        img = SH.capture(h)
        if img:
            img.save(OUT / "nav_rename.png")
            print("nav_rename.png OK", img.size)
    item = a.nav.items.get("vpn")
    if item is not None:
        lbl = item.lbl
        print("导航项文本 =", repr(lbl.cget("text")),
              "| 需要宽", lbl.winfo_reqwidth(), "| 实际宽", lbl.winfo_width())

    # 2) 证书页：CA 默认名
    a.nav.select("vpn")
    pump(a, 1.6)
    con = a.vpn_console
    con.nav.select("cert")
    pump(a, 0.9)
    print("CA 默认名 =", repr(con.v_cacn.get()))
    if h:
        full = SH.capture(h)
        if full:
            ox = con.winfo_rootx() - a.winfo_rootx()
            oy = con.winfo_rooty() - a.winfo_rooty()
            dx, dy = SH.client_offset(h)
            ox, oy = ox + dx, oy + dy
            w, hh = con.winfo_width(), con.winfo_height()
            full.crop((ox, oy, min(ox + w, full.width),
                       min(oy + hh, full.height))).save(OUT / "vpn_cert.png")
            print("vpn_cert.png OK")

    # 3) 打包页：导出平台复选框文案
    con.nav.select("pack")
    pump(a, 0.9)
    if h:
        full = SH.capture(h)
        if full:
            ox = con.winfo_rootx() - a.winfo_rootx()
            oy = con.winfo_rooty() - a.winfo_rooty()
            dx, dy = SH.client_offset(h)
            ox, oy = ox + dx, oy + dy
            w, hh = con.winfo_width(), con.winfo_height()
            full.crop((ox, oy, min(ox + w, full.width),
                       min(oy + hh, full.height))).save(OUT / "vpn_pack.png")
            print("vpn_pack.png OK")

    a.destroy()


if __name__ == "__main__":
    main()
