# -*- coding: utf-8 -*-
"""安全中心 → 证书管理 页截图（进程内，数据目录重定向到临时目录）。

    python _shotcert.py

不碰真实 data/：`TB_DATA_DIR` 指到临时目录，证书库也是临时的那一份。
"""
from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 必须在 import app 之前：DATA_DIR 是模块级算出来的
TMP = Path(tempfile.mkdtemp(prefix="tb_shotcert_"))
os.environ["TB_DATA_DIR"] = str(TMP)

from core import servercert as SC  # noqa: E402

# 先放两张真证书进临时库，图上才有内容可看
st = SC.Store(TMP / "certs")
c1, k1 = SC.make_selfsigned("nas.home.arpa",
                            ["nas.home.arpa", "192.168.3.7"], days=825)
st.add("群晖 DSM", c1, k1)
c2, k2 = SC.make_selfsigned("git.home.arpa",
                            ["git.home.arpa", "192.168.3.9"], days=90)
st.add("gitea", c2, k2)
c3, k3 = SC.make_selfsigned("ldap.internal", ["ldap.internal"], days=3)
st.add("旧测试证书", c3, k3)

import app as A                       # noqa: E402
from _shot_term import capture_screen  # noqa: E402


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

try:
    a.nav.select("sec")
    pump(a, 1.0)
    a.seg_sec.select("cert")
    pump(a, 1.6)
    hwnd = ctypes.windll.user32.GetParent(a.winfo_id()) or a.winfo_id()
    img = capture_screen(hwnd)
    if img is None:
        raise RuntimeError("抓屏返回空")
    out = HERE / "screenshots" / "sec_cert.png"
    img.save(out)
    print(f"saved {out} ({img.width}x{img.height})")
    print("表格行数：" + str(len(a.tbl_cert.rows())))
    for r in a.tbl_cert.rows():
        print("  ", r["name"], "|", r["cn"], "|", r["sans"], "|", r["days"])
finally:
    a.destroy()
