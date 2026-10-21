# -*- coding: utf-8 -*-
"""脱敏效果截图：终端 + 命令日志，各跑一遍「cat 私钥」。

不是造数据糊一张图 —— 终端那张是**真的**在本地会话里执行
`type <临时文件>`，文件内容是真私钥格式，看它出来是不是打码了。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _shot_term as T
import _shots as SH

OUT = Path(__file__).resolve().parent / "screenshots"

FAKE_KEY = """-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACBq1l9mS3crz0PvXKxHnDk1lPqQ7wZc8nWfYk2tVbY0sQAAAJg7Hk3Z
Ox5N2TseTdk7Hk3ZOx5N2TseTdk7Hk3ZOx5N2TseTdk7Hk3ZOx5N2TseTdk7Hk3ZOx5N
-----END OPENSSH PRIVATE KEY-----"""

FAKE_TA = """-----BEGIN OpenVPN Static key V1-----
6acef03f62675b4b1bbd03e53b187727
423cea742242106cb2916a8a4c829756
-----END OpenVPN Static key V1-----"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    p = Path("data/settings.json")
    d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    d.update({"lock_enabled": False, "lock_hash": "", "lock_timeout": 0,
              "lock_auto_login": False, "lock_pass_enc": ""})
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    key_file = Path(tempfile.gettempdir()) / "demo_id_ed25519"
    key_file.write_text(FAKE_KEY + "\n", encoding="utf-8")
    ta_file = Path(tempfile.gettempdir()) / "demo_ta.key"
    ta_file.write_text(FAKE_TA + "\n", encoding="utf-8")

    import app as APP
    a = APP.ToolboxApp()
    a.geometry("+80+40")
    pump = T.pump
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
    pump(a, 0.4)

    # 真的执行，真的输出私钥正文 —— 走的是 write() 这条唯一出口
    for cmd in (f"type {key_file}", f"type {ta_file}", "Write-Output 上面两段本该是密钥正文"):
        w.v_in.set(cmd)
        w._submit()
        pump(a, 1.3)
    # 主程序旁路（tracer）也来一条，验证 feed() 不漏
    a._term_feed(f"cat /root/ta.key", 0, FAKE_TA, "", 8)
    pump(a, 0.6)
    w.attributes("-topmost", True)
    hwnd = T.find_prefix  # noqa: F841
    h = T.find_prefix("终端 ·")
    h = h[0] if h else 0
    img = T.capture_screen(h) if h else None
    if img:
        img.save(OUT / "redact_terminal.png")
        print("redact_terminal.png OK", img.size)
    else:
        print("终端抓屏失败 hwnd=", h)

    # ---- VPN 命令日志：同样的正文，看控制台那一侧
    a.nav.select("vpn")
    pump(a, 1.5)
    con = a.vpn_console
    con.nav.select("cons")
    pump(a, 0.7)
    con._cons_write("cmd", "$ cat /var/packages/VPNCenter/etc/openvpn/ta.key\n")
    con._cons_write("out", FAKE_TA + "\n")
    con._cons_write("cmd", "$ cat ~/.ssh/id_ed25519\n")
    con._cons_write("out", FAKE_KEY + "\n")
    con._cons_write("meta", "[完成] 两条都执行完了，正文本该在上面对应位置\n")
    pump(a, 0.9)

    hw = T.find_prefix(a.title().strip())
    hw = hw[0] if hw else 0
    full = SH.capture(hw) if hw else None
    if full is not None:
        ox = con.winfo_rootx() - a.winfo_rootx()
        oy = con.winfo_rooty() - a.winfo_rooty()
        dx, dy = SH.client_offset(hw)
        ox, oy = ox + dx, oy + dy
        img = full.crop((ox, oy, min(ox + con.winfo_width(), full.width),
                         min(oy + con.winfo_height(), full.height)))
        img.save(OUT / "vpn_cons.png")
        print("vpn_cons.png OK", img.size)
    else:
        print("命令日志抓屏失败 hwnd=", hw)

    try:
        a._close_to_tray = False
        a.destroy()
    except Exception:  # noqa: BLE001
        pass
    key_file.unlink(missing_ok=True)
    ta_file.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
