# -*- coding: utf-8 -*-
"""抓「远程管理」四段页面的截图（喂假数据，不连设备）。

    python _shotrmt.py

数据目录用临时目录：跑完不会污染 data/，也不会把窗口状态写回
data/window.json（这个坑 v1.0.25 / v1.0.26 各栽过一次）。
"""
from __future__ import annotations

import ctypes
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

TMP = Path(tempfile.mkdtemp(prefix="tbshot-"))
import os  # noqa: E402

os.environ["TB_DATA_DIR"] = str(TMP)

import app as A  # noqa: E402
import wbtui as U  # noqa: E402
from core import remote as R  # noqa: E402

OUT = HERE / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

a = A.ToolboxApp()
a.geometry("1500x980+40+30")
a.update()
a.nav.select("rmt")


def pump(sec: float) -> None:
    end = time.time() + sec
    while time.time() < end:
        a.update()
        time.sleep(0.02)


pump(1.0)

# ---- 假数据：一条「Ubuntu 上真实会有的」sshd_config
SSHD = """#	$OpenBSD: sshd_config,v 1.103 2018/04/09 20:41:22 tj Exp $
Port 22
#AddressFamily any
ListenAddress 0.0.0.0
HostKey /etc/ssh/ssh_host_rsa_key
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication yes
KbdInteractiveAuthentication no
X11Forwarding no
MaxAuthTries 6
ClientAliveInterval 120
UsePAM yes
Subsystem sftp /usr/lib/openssh/sftp-server
"""
a._rm_path = "/etc/ssh/sshd_config"
a._rm_init = "systemd"
a._rm_cfg = R.parse_sshd(SSHD)
a._rm_eff = R.sshd_effective(a._rm_cfg)
a.rm_ssh_state.set("允许密码登录", "warn")
a._rm_ssh_render("/etc/ssh/sshd_config")
a.rm_probe_lbl.configure(text="Linux · SSH、XRDP · 监听 22、3389")

RDP_OUT = "enabled=1\nnla=1\nport=3389\nfw_rules=2\n"
a._rm_sess = lambda: object()          # 只为让读取流程走得下去
a._rm_run = lambda cmd, timeout=60, admin=True: (0, RDP_OUT, "")
a.rm_rdp_read()
pump(0.6)

XRDP_INI = """[Globals]
ini_version=1
fork=true
port=3389
use_vsock=false
tcp_nodelay=true
crypt_level=high
security_layer=negotiate
max_bpp=32
new_cursors=true
"""
a._rm_xrdp_path = "/etc/xrdp/xrdp.ini"
a._rm_xrdp_cfg = R.parse_ini(XRDP_INI)
_s = R.xrdp_summary(a._rm_xrdp_cfg)
for w in list(a.rm_xrdp_rows.winfo_children()):
    w.destroy()
for _i, (_k, _v) in enumerate([("配置文件", "/etc/xrdp/xrdp.ini"),
                               ("端口", _s["port"]), ("色深", _s["max_bpp"]),
                               ("安全层", _s["security_layer"]),
                               ("加密级别", _s["crypt_level"])]):
    U.wlabel(a.rm_xrdp_rows, _k, "dim", U.FONT_S).grid(
        row=_i, column=0, sticky="w", padx=(0, U.px(14)), pady=U.px(3))
    U.wlabel(a.rm_xrdp_rows, str(_v), "card", U.FONT_S).grid(
        row=_i, column=1, sticky="w", pady=U.px(3))

# ---- 密钥页：真生成一把（ed25519 很快），不联网
a.rm_key_kind.set("ed25519")
a.rm_key_comment.set("toolbox@nas")
a.rm_key_gen()
a.rm_imp.set_text("PuTTY-User-Key-File-3: ssh-rsa\n"
                  "Encryption: none\n"
                  "Comment: alice@win11\n"
                  "Public-Lines: 2\n"
                  "AAAAB3NzaC1yc2EAAAADAQABAAABAQC7\n"
                  "kQ9examplebase64line==\n")
pump(0.4)


def grab(name: str) -> None:
    from PIL import ImageGrab
    a.attributes("-topmost", True)
    a.lift()
    a.focus_force()
    for _ in range(3):
        a.update()
        time.sleep(0.2)
    x, y = a.winfo_rootx(), a.winfo_rooty()
    w, h = a.winfo_width(), a.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    dst = OUT / f"{name}.png"
    img.save(dst)
    print(f"saved {dst}  {img.size}")


try:
    for key, name in (("ssh", "rmt_ssh"), ("key", "rmt_key"),
                      ("rdp", "rmt_rdp"), ("xrdp", "rmt_xrdp")):
        a._rm_switch(key)
        pump(0.5)
        grab(name)
    U.apply_theme("dark")
    a._rm_switch("ssh")
    pump(0.8)
    grab("rmt_ssh_dark")
finally:
    a.destroy()
