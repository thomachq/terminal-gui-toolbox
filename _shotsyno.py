# -*- coding: utf-8 -*-
"""抓两张图：① 群晖防火墙（配置文件后端）的规则列表 ② 终端顶栏的「同步 API」。

数据是照着真机 192.168.3.12 的 firewall.d/1.json 抄的（12 条代表性规则），
不连设备、不碰任何配置文件。

    python _shotsyno.py
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TMP = Path(tempfile.mkdtemp(prefix="tb_shotsyno_"))
os.environ["TB_DATA_DIR"] = str(TMP)

from core import firewall as fw  # noqa: E402

OUT = HERE / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)


def rule(policy=0, group=1, iplist=("192.168.3.0", "255.255.255.0"),
         itype=0, protocol=1, pgroup=1, plist=("2222",), enable=True,
         name=""):
    return {"adapterDirect": 1, "blLog": False,
            "chainList": ["FORWARD_FIREWALL", "INPUT_FIREWALL"],
            "enable": enable, "ipDirect": 1, "ipGroup": group,
            "ipList": list(iplist), "ipType": itype, "labelList": [],
            "name": name, "policy": policy, "portDirect": 0,
            "portGroup": pgroup, "portList": list(plist),
            "protocol": protocol, "ruleIndex": 0, "table": "filter"}


# 真机 1.json 的节选：自定义网段 / 单机 / 位置 / 内置服务 / 拒绝 / 所有端口
RULES = [
    rule(plist=("2222",), name=""),
    rule(plist=("23333",), iplist=("10.8.0.0", "255.255.255.0")),
    rule(protocol=3, pgroup=3, plist=(), iplist=("172.15.0.0", "255.255.0.0")),
    rule(plist=("39443",), iplist=("27.153.133.133",)),
    rule(protocol=3, pgroup=3, plist=(), iplist=("172.17.0.0", "255.255.0.0")),
    rule(protocol=2, plist=("9876",), iplist=("172.17.0.0", "255.255.0.0"),
         name="CloudDrive2 heartbeat"),
    rule(group=3, iplist=("CN",), itype=2, plist=("32400",)),
    rule(plist=("ReverseProxy_13000", "ReverseProxy_15255"), pgroup=2),
    rule(protocol=3, pgroup=3, plist=(), iplist=("10.2.0.0", "255.255.255.0"),
         name="办公网"),
    rule(protocol=4, pgroup=1, plist=(), iplist=("10.8.0.0", "255.255.255.0")),
    rule(policy=1, group=5, iplist=(), itype=2, protocol=3, pgroup=3, plist=()),
    rule(group=0, iplist=(), plist=("19820",)),
]
TEXT = "\n".join([
    "@@@TB-SETTINGS",
    json.dumps({"profile": "default", "status": True}),
    "@@@TB-FILE 1.json " + "a" * 64,
    json.dumps({"adapterPolicyMap": {"global": 2}, "name": "default",
                "rules": {"global": RULES}}, ensure_ascii=False),
    "@@@TB-FILE 2.json " + "b" * 64,
    json.dumps({"adapterPolicyMap": {"global": 2}, "name": "custom",
                "rules": {"global": [rule(plist=("6000",))]}},
               ensure_ascii=False),
])

BE = fw.SynoBackend()
BE.parse(TEXT)

import app as A                       # noqa: E402
import wbtui as U                     # noqa: E402
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
pump(a, 0.6)


def grab(name: str, win=None) -> None:
    win = win or a
    try:
        win.attributes("-topmost", True)
        win.lift()
        win.focus_force()
    except Exception:  # noqa: BLE001
        pass
    pump(win, 0.5)
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    own = win.winfo_id()
    # GetAncestor(GA_ROOT=2) 比 GetParent 稳：GetParent 在 Tk 换过 wrapper
    # 的情况下会指到一个内部小窗口（这次就抓出 237x39 的标题栏碎片）。
    hwnd = u.GetAncestor(own, 2) or u.GetParent(own) or own
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    print(f"  [grab] {name}: tk={win.winfo_width()}x{win.winfo_height()} "
          f"hwnd={hwnd} rect={r.right - r.left}x{r.bottom - r.top}", flush=True)
    img = print_window(hwnd)
    if img is None or img.size[0] < 400 or img.size[1] < 300:
        img = capture_screen(hwnd) or img
    # 尺寸不合理就是抓错了 —— 宁可报错，也不拿碎片覆盖掉原来那张好图
    if img is None or img.size[0] < 400 or img.size[1] < 300:
        print(f"!! {name} 抓到的是碎片 {getattr(img, 'size', None)}，不覆盖原图")
        return
    dst = OUT / f"{name}.png"
    img.save(dst)
    print(f"saved {dst} {img.size}")


def print_window(hwnd):
    """让窗口自己画到内存 DC。屏幕被锁屏盖住时 BitBlt 只能抓到锁屏，
    这条路不依赖当前前台，锁屏也能抓到窗口内容。"""
    import ctypes.wintypes as wt
    from ctypes import byref
    from PIL import Image
    u = ctypes.windll.user32
    g = ctypes.windll.gdi32
    import struct
    r = wt.RECT()
    u.GetWindowRect(hwnd, byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    sdc = u.GetDC(0)
    mdc = g.CreateCompatibleDC(sdc)
    bmp = g.CreateCompatibleBitmap(sdc, w, h)
    g.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, 2)      # PW_RENDERFULLCONTENT
    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                    ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]
    class BI(ctypes.Structure):
        _fields_ = [("bmiHeader", BIH), ("bmiColors", wt.DWORD * 3)]
    bi = BI()
    bi.bmiHeader.biSize = ctypes.sizeof(BIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, byref(bi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(0, sdc)
    if not ok:
        return None
    raw = buf.raw
    # 全黑 / 全同色 = 没画出来，交给屏幕抓取兜底
    if raw[:4096] == b"\x00" * 4096:
        return None
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)


try:
    # ---------------------------------------------------- ① 终端顶栏
    a.v_proto.set("local")
    a._proto_changed()
    a.do_connect()
    for _ in range(120):
        pump(a, 0.1)
        if a.session is not None:
            break
    a.open_terminal()
    pump(a, 1.2)
    t = a._term_live()
    t._set_mode("box")
    pump(a, 0.4)
    t.v_in.set("sudo /usr/syno/bin/synofirewall --enum IPV4 | head -6")
    pump(a, 0.3)
    grab("term_syncapi")

    # ---------------------------------------------------- ② 群晖防火墙
    rows = BE.parse(TEXT)
    a.nav.select("sec")
    pump(a, 1.2)
    a.seg_sec.select("fw")
    # 进页会跑 fw_probe()（异步），它的 done 回调**无条件**
    # `self.fw_backend = backend` —— 不等它跑完就注入，会被冲成
    # 「未探测 + 空表」。先让它探完（本机那下很快），再覆盖。
    pump(a, 3.0)
    a.fw_backend = BE
    pump(a, 0.3)
    a.badge_fw.set("群晖防火墙（配置文件）", "ok")
    a.lbl_fw.configure(
        text="群晖 DSM 防火墙 · profile default（写在 /usr/syno/etc/firewall.d/1.json，"
             "跟 DSM 界面同一份配置，重启不丢）", fg=U.T.ok)
    a.tbl_fw.set_rows(rows)
    a.tbl_fw.set_empty("规则是空的", "")
    a.kk_fw.set_items([
        ("后端", "群晖防火墙（配置文件）", "brand"),
        ("规则条数", str(len(rows)), None),
        ("允许", str(sum(1 for r in rows if r.get("_tone") == "ok")), "ok"),
        ("拒绝 / 丢弃", str(sum(1 for r in rows
                             if r.get("_tone") in ("err", "warn"))), "warn"),
        ("可写", "是", "ok"),
        ("持久化", BE.persist_note()[:70], None),
    ])
    a._fw_set_writable(True)
    a.status.set_state(f"规则已读取（{len(rows)} 条）", "ok")
    pump(a, 0.8)
    grab("fw_syno_light")

    # 新增规则表单：「位置（国家）」是这一版新加的字段，40 项下拉
    # 会不会把行撑高/溢出只有截图看得见。
    def shot_form(tag: str) -> None:
        from ui import forms as _F
        dlg = _F.FormDialog(
            a, "新增防火墙规则", a._fw_fields(BE, None),
            subtitle="后端：群晖防火墙（配置文件）。保存后会先给你看命令，"
                     "确认了才执行。", submit="确定")
        dlg.deiconify()
        for _ in range(40):
            dlg.update()
            time.sleep(0.01)
        grab(f"fw_syno_form_{tag}", dlg)
        try:
            dlg.destroy()
        except Exception:  # noqa: BLE001
            pass
        pump(a, 0.4)

    shot_form("light")

    U.apply_theme("dark")
    pump(a, 0.8)
    grab("fw_syno_dark")
    try:
        shot_form("dark")
    except Exception as e:  # noqa: BLE001
        # 暗色这张偶尔被窗口管理器提前销毁。产品本身连开三次都正常
        # （840x438），这里是截图脚本在 ShowWindow 之后重开对话框的时序问题，
        # 亮色那张已经足够证明布局，不影响交付。
        print(f"!! 暗色表单没截到：{type(e).__name__}: {e}")
finally:
    try:
        a._close_to_tray = False
        a.destroy()
    except Exception:  # noqa: BLE001
        pass
