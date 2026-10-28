# -*- coding: utf-8 -*-
"""安全中心 → 证书管理：**连着真设备**时这一页长什么样（截图）。

    python _shotcertdev.py 192.168.3.12
    python _shotcertdev.py 飞牛OS

数据目录整体复制到临时目录：不碰真实 data/，也不改 window.json。
证书页应当自动带上设备自己的清单（群晖 DSM / 飞牛 fnOS）。
"""
from __future__ import annotations

import ctypes
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SRC_DATA = Path(r"D:\Tools\Terminal-GUI-Toolbox\data")
TMP = Path(tempfile.mkdtemp(prefix="tb_shotcertdev_"))
shutil.copytree(SRC_DATA, TMP / "data", dirs_exist_ok=True)
os.environ["TB_DATA_DIR"] = str(TMP / "data")
WIN_BAK = (TMP / "data" / "window.json")
if WIN_BAK.is_file():
    shutil.copy2(WIN_BAK, TMP / "window.json.bak")

import app as A                                        # noqa: E402
from core.detect import detect                          # noqa: E402
from core.session import ConnSpec, open_session         # noqa: E402
from profiles import ProfileStore                       # noqa: E402

import ctypes.wintypes as wt                             # noqa: E402
from PIL import Image                                    # noqa: E402

_g = ctypes.windll.gdi32
_u = ctypes.windll.user32


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class _BINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BIH), ("bmiColors", wt.DWORD * 3)]


def grab(hwnd):
    """PrintWindow(PW_RENDERFULLCONTENT) —— 锁屏时普通抓屏只能拿到壁纸。"""
    r = wt.RECT()
    _u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    hdc = _u.GetDC(hwnd)
    mdc = _g.CreateCompatibleDC(hdc)
    bmp = _g.CreateCompatibleBitmap(hdc, w, h)
    _g.SelectObject(mdc, bmp)
    ok = _u.PrintWindow(hwnd, mdc, 2)
    bi = _BINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(_BIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    _g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    _g.DeleteObject(bmp)
    _g.DeleteDC(mdc)
    _u.ReleaseDC(hwnd, hdc)
    if not ok:
        return None
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)

WANT = sys.argv[1] if len(sys.argv) > 1 else "192.168.3.12"

import crypto_store                                     # noqa: E402

crypto_store.set_key_dir(TMP / "data")
_pre = ProfileStore(TMP / "data" / "profiles.dat")
print(f"[pre ] 启动前读到 {len(_pre.data.get('profiles') or [])} 条档案")


def pump(a, s: float) -> None:
    t0 = time.time()
    while time.time() - t0 < s:
        a.update()
        time.sleep(0.01)


a = A.ToolboxApp()
pump(a, 1.2)
# vpncert_gui 在 import 时会把它自己的 DATA_DIR（源码目录 data）设成密钥目录，
# 把 TB_DATA_DIR 的重定向顶掉 —— 这里再按临时目录设回来，否则档案解不开。
crypto_store.set_key_dir(TMP / "data")
sess = None
try:
    if a.state() == "zoomed":
        a.state("normal")
    store = ProfileStore(TMP / "data" / "profiles.dat")
    print(f"档案 {len(store.data.get('profiles') or [])} 条；找「{WANT}」")
    h = next((p for p in (store.data.get("profiles") or [])
              if str(p.get("name")) == WANT), None)
    if h is None:
        raise SystemExit(f"档案里没有「{WANT}」")

    spec = ConnSpec(proto="ssh", host=h["host"], port=int(str(h.get("port") or 22)),
                    user=h.get("user") or "", auth=h.get("auth") or "key",
                    key_path=h.get("key_path") or "",
                    key_passphrase=h.get("key_passphrase") or "",
                    sudo_password=h.get("sudo_password") or "",
                    elevate=bool(h.get("elevate")))
    sess = open_session(spec)
    sess.tracer = a._trace_cmd
    dev = detect(sess)
    sess.bind_device(dev)
    print("设备：", dev.os_name or dev.os_family, "| vendor:", dev.vendor)
    if spec.sudo_password:
        sess.sudo_pw = spec.sudo_password
    slot = a.sessions.add(sess, dev, label=str(h.get("name")))
    a.sessions.active_id = slot.sid
    try:
        a._after_switch()
    except Exception as e:  # noqa: BLE001
        print("after_switch:", e)

    a.nav.select("sec")
    pump(a, 1.0)
    a.seg_sec.select("cert")
    # 日志面板默认开着会吃掉下半屏（证书表格只剩表头），收起来才看得到全貌
    try:
        a.log_dock_toggle(False)
    except Exception as e:  # noqa: BLE001
        print("收起日志面板失败：", e)
    pump(a, 8.0)          # 等后台读设备证书

    hwnd = ctypes.windll.user32.GetParent(a.winfo_id()) or a.winfo_id()
    img = grab(hwnd)
    if img is None:
        raise RuntimeError("抓屏返回空")
    out = HERE / "screenshots" / f"sec_cert_dev_{WANT}.png"
    img.save(out)
    print(f"saved {out} ({img.width}x{img.height})")
    print("摘要：" + a.lbl_cert_sum.cget("text"))
    rows = a.tbl_cert.rows()
    print(f"表格行数：{len(rows)}")
    for r in rows:
        print("  ", r["src"], "|", r["name"], "|", r["cn"], "|", r["days"],
              "|", r["key"], "|", r["usage"])
finally:
    try:
        if sess is not None:
            sess.close()
    except Exception:  # noqa: BLE001
        pass
    a.destroy()
