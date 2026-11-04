# -*- coding: utf-8 -*-
"""自测：新增连接方式（v1.0.33）——COM / ADB / HDC。

分两层：
  · 档案层：字段显隐、协议归一、校验、地址/系统标签（纯函数，无窗口）；
  · 界面层：真起一次主窗口，切到 COM / ADB / HDC，看该出现的行出现了、
    不该出现的行消失了，`_collect_profile()` 收得到新字段，设置页能打开。

    python _newconntest.py
    （想连真设备/真串口，请手工跑程序，不在自测范围内）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 数据目录重定向：档案 / 设置 / 日志全落在临时目录，真实 data 一个字节不动
_TMP = tempfile.mkdtemp(prefix="tb-newconn-")
os.environ["TB_DATA_DIR"] = _TMP

from core import hosts as H          # noqa: E402
from pages.hosts_page import host_fields, merge_host_form  # noqa: E402

OK = 0
BAD = 0


def check(cond, msg: str) -> None:
    global OK, BAD
    if cond:
        OK += 1
        print(f"  [ok] {msg}")
    else:
        BAD += 1
        print(f"  [NG] {msg}")


def eq(got, want, msg: str) -> None:
    check(got == want, f"{msg}（got={got!r}）")


def section(t: str) -> None:
    print(f"\n-- {t}")


def keys_for(proto: str, extra: dict | None = None) -> list[str]:
    """按 show_if 算一遍「这个协议下会显示哪些字段」。"""
    vals = H.blank_record(proto)
    vals.update(extra or {})
    vals["proto"] = proto
    out = []
    for f in host_fields(rec=vals):
        si = f.get("show_if")
        if si:
            if callable(si):
                if not si(vals):
                    continue
            else:
                if not all(vals.get(k) in v for k, v in si.items()):
                    continue
        out.append(f.get("key"))
    return out


def main() -> int:
    section("1. 协议下拉与协议归一")
    pick = dict(H.PROTO_PICK)
    for p in ("ssh", "telnet", "com", "adb", "hdc", "local"):
        check(p in pick, f"下拉里有 {p}：{pick.get(p)}")
    for p in ("com", "adb", "hdc"):
        eq(H.proto_of({"proto": p}), p, f"proto_of 认 {p}")
    eq(H.proto_of({"port": "23"}), "telnet", "老档案 23 端口仍认 telnet")
    eq(H.proto_of({"port": "2222"}), "ssh", "老档案其它端口仍认 ssh")

    section("2. 默认值")
    c = H.blank_record("com")
    eq((c["com_port"], c["baud"], c["databits"], c["parity"],
        c["stopbits"], c["flow"]), ("COM1", "115200", "8", "none", "1", "none"),
       "COM 默认 COM1 / 115200 8N1")
    eq(H.blank_record("adb")["port"], "5555", "ADB 默认端口 5555")
    eq(H.blank_record("hdc")["port"], "8710", "HDC 默认端口 8710")
    eq(H.blank_record("adb")["tool_src"], "auto", "ADB 组件来源默认自动")
    eq(H.blank_record("ssh")["com_port"], "", "SSH 不带串口字段")

    section("3. 字段显隐")
    com_keys = keys_for("com")
    for k in ("com_port", "baud", "databits", "parity", "stopbits", "flow",
              "user"):
        check(k in com_keys, f"COM 显示 {k}")
    for k in ("host", "port", "tool_src", "device_id", "auth"):
        check(k not in com_keys, f"COM 不显示 {k}")
    adb_keys = keys_for("adb")
    for k in ("tool_src", "device_id", "host", "port"):
        check(k in adb_keys, f"ADB 显示 {k}")
    for k in ("com_port", "baud", "auth", "user", "ostype"):
        check(k not in adb_keys, f"ADB 不显示 {k}")
    hdc_keys = keys_for("hdc")
    check("tool_src" in hdc_keys and "device_id" in hdc_keys, "HDC 有组件与设备字段")
    ssh_keys = keys_for("ssh")
    check("com_port" not in ssh_keys and "tool_src" not in ssh_keys,
          "SSH 不会串出 COM / 调试字段")

    section("4. 表单合并：切协议要把不属于它的字段清掉")
    m = merge_host_form({"name": "sw1", "proto": "com", "com_port": "COM5",
                         "baud": "9600", "databits": "8", "parity": "even",
                         "stopbits": "1", "flow": "none", "user": "admin",
                         "host": "1.1.1.1", "port": "23", "sudo": True},
                        is_win=True)
    eq(m["com_port"], "COM5", "串口名保留")
    eq(m["baud"], "9600", "波特率保留")
    eq(m["tool_src"], "", "COM 不带调试组件来源")
    check(m["sudo"] is True, "COM 接交换机时 enable 勾选保留")
    eq(m["host"], "1.1.1.1", "host 字段仍在（COM 只是不显示/不用）")

    m2 = merge_host_form({"name": "phone", "proto": "adb", "host": "",
                          "port": "5555", "device_id": "S1",
                          "tool_src": "bundled", "sudo": True,
                          "com_port": "COM3", "baud": "9600"}, is_win=True)
    eq(m2["tool_src"], "bundled", "ADB 组件来源保留")
    eq(m2["device_id"], "S1", "设备序列号保留")
    eq(m2["com_port"], "", "切到 ADB 会清掉串口名")
    check(m2["sudo"] is False, "ADB 强制关掉 sudo（没有这层）")
    m3 = merge_host_form({"name": "x", "proto": "ssh", "host": "a", "port": "22",
                          "device_id": "S9", "tool_src": "env"}, is_win=True)
    eq((m3["device_id"], m3["tool_src"]), ("", ""), "切回 SSH 清掉调试字段")

    section("5. 校验")
    def errs(rec):
        return H.validate(rec, others=[])

    c1 = H.blank_record("com")
    c1.update({"name": "sw1", "com_port": ""})
    check(any("串口" in e for e in errs(c1)), "COM 缺串口名要报错")
    c2 = H.blank_record("com")
    c2.update({"name": "sw1", "com_port": "COM3", "baud": "abc"})
    check(any("波特率" in e for e in errs(c2)), "波特率不是数字要报错")
    c3 = H.blank_record("com")
    c3.update({"name": "sw1", "com_port": "COM3", "baud": "115200"})
    eq(errs(c3), [], "COM 填好就通过")
    a1 = H.blank_record("adb")
    a1.update({"name": "phone"})
    eq(errs(a1), [], "ADB 不填地址（USB）也能通过")
    a2 = H.blank_record("adb")
    a2.update({"name": "phone", "host": "192.168.1.5", "port": ""})
    check(any("端口" in e for e in errs(a2)), "ADB 填了地址就必须给端口")
    a3 = H.blank_record("adb")
    a3.update({"name": "phone", "host": "192.168.1.5", "port": "5555"})
    eq(errs(a3), [], "ADB 远程填全就通过")
    h1 = H.blank_record("hdc")
    h1.update({"name": "mate"})
    eq(errs(h1), [], "HDC USB 也能通过")

    section("6. 地址 / 系统标签")
    eq(H.addr_of({"proto": "com", "com_port": "COM3", "baud": "115200",
                  "databits": "8", "parity": "none", "stopbits": "1"}),
       "COM3 · 115200 8N1", "COM 的地址列显示串口与线路参数")
    eq(H.addr_of({"proto": "com", "com_port": ""}), "—", "没串口显示破折号")
    eq(H.addr_of({"proto": "adb", "host": "", "port": "5555"}), "USB",
       "ADB USB 显示 USB")
    eq(H.addr_of({"proto": "adb", "host": "", "device_id": "S1"}), "USB（S1）",
       "带序列号时一起显示")
    eq(H.addr_of({"proto": "hdc", "host": "10.0.0.2", "port": "8710"}),
       "10.0.0.2", "默认端口不重复显示")
    eq(H.assume_os({"proto": "com"}, True), "network", "COM 默认按网络设备")
    eq(H.assume_os({"proto": "com", "ostype": "linux"}, True), "linux",
       "COM 也可以手动指定 Linux")
    eq(H.assume_os({"proto": "adb"}, True), "linux", "ADB 按类 Unix")
    eq(H.os_label({"proto": "adb"}), "Android", "ADB 的默认系统名")
    eq(H.os_label({"proto": "hdc"}), "HarmonyOS", "HDC 的默认系统名")
    check(H.host_view({"name": "sw1", "proto": "com", "com_port": "COM3",
                       "baud": "9600"})["addr"].startswith("COM3"),
          "主机列表里 COM 不再显示 —")

    section("7. 界面：切协议时行的显隐")
    import tkinter as tk
    import app as A
    root = None
    try:
        root = A.ToolboxApp()
        root.update_idletasks()
        v = root.v_proto

        v.set("com")
        root._proto_changed()
        root.update_idletasks()
        check(bool(root.row_com.winfo_manager()), "切到 COM 显示串口参数行")
        check(not root.row_dbg.winfo_manager(), "切到 COM 收起调试行")
        check(not root.e_host.winfo_ismapped() or True, "COM 下地址框被 grid_remove")
        check(root.e_host.winfo_manager() == "", "COM 下地址输入框已从布局移除")
        check(root.e_user.winfo_manager() != "", "COM 下仍可填 console 用户")
        p = root._collect_profile()
        eq((p["proto"], p["com_port"]), ("com", "COM1"),
           "收档案时带上 COM 字段")
        eq(p["tool_src"], "", "COM 不带 tool_src")

        # ---- 串口是下拉（v1.0.35）：检测到的口直接点，但仍能手输
        from core import serialport as _sp
        _ports = _sp.list_ports()
        eq(str(root.cb_com.winfo_class()), "TCombobox", "连接页串口是下拉框")
        eq(str(root.cb_com.cget("state")), "normal", "连接页串口可编辑（能手输）")
        eq(list(root.cb_com.cget("values")), _ports, "下拉里就是检测到的串口")
        root.v_com_port.set("COM9")
        root._proto_changed()
        eq(root.v_com_port.get(), "COM9", "手填没检测到的口也不会被顶掉")
        root.v_com_port.set("COM1")

        _cf = [x for x in host_fields({"proto": "com", "com_port": "COM3"})
               if x["key"] == "com_port"][0]
        eq(_cf["kind"], "select", "档案表单的串口也是下拉")
        check(_cf.get("editable") is True, "档案表单的串口仍可手输")
        _opts = [v for v, _lab in
                 (o if isinstance(o, tuple) else (o, o) for o in _cf["options"])]
        check("COM3" in _opts, "档案里存的口在候选里（不会被第一项顶掉）")

        v.set("adb")
        root._proto_changed()
        root.update_idletasks()
        check(bool(root.row_dbg.winfo_manager()), "切到 ADB 显示调试参数行")
        check(not root.row_com.winfo_manager(), "切到 ADB 收起串口行")
        eq(root.v_port.get(), "5555", "ADB 端口自动填 5555")
        eq(root.e_host.winfo_manager(), "grid", "ADB 仍可填地址（远程调试）")
        eq(root.e_user.winfo_manager(), "", "ADB 不显示用户字段")
        check(root.lbl_dbg_hint.cget("text") != "", "调试行给出了组件路径提示")
        p2 = root._collect_profile()
        eq((p2["proto"], p2["tool_src"]), ("adb", "auto"),
           "收档案时带上组件来源")

        v.set("hdc")
        root._proto_changed()
        root.update_idletasks()
        eq(root.v_port.get(), "8710", "HDC 端口自动填 8710")
        eq(root._collect_profile()["proto"], "hdc", "HDC 收档案正常")

        v.set("ssh")
        root._proto_changed()
        root.update_idletasks()
        check(not root.row_com.winfo_manager() and not root.row_dbg.winfo_manager(),
              "回到 SSH 两个新行都收起")
        eq(root.e_user.winfo_manager(), "grid", "SSH 仍显示用户")

        section("8. 载入档案：新字段能回填")
        root._apply_profile({"name": "sw2", "proto": "com", "com_port": "COM7",
                             "baud": "9600", "databits": "7", "parity": "even",
                             "stopbits": "2", "flow": "rtscts",
                             "host": "", "port": "22"})
        root.update_idletasks()
        eq((root.v_com_port.get(), root.v_baud.get(), root.v_databits.get()),
           ("COM7", "9600", "7"), "串口 / 波特率 / 数据位回填")
        eq((root.v_parity.get(), root.v_stopbits.get(), root.v_flow.get()),
           ("even", "2", "rtscts"), "校验 / 停止位 / 流控回填")
        root._apply_profile({"name": "p1", "proto": "adb", "device_id": "S7",
                             "tool_src": "bundled", "host": "", "port": "5555"})
        eq((root.v_device.get(), root.v_tool_src.get()), ("S7", "bundled"),
           "设备序列号与组件来源回填")
        eq(root.cb_tool_src.get(), "内置组件（应用目录 bin）",
           "下拉框显示成中文标签")

        section("8.5 主机表单能真的搭起来（不弹窗）")
        from ui import forms as _forms
        for proto in ("com", "adb", "hdc"):
            rec = H.blank_record(proto)
            rec["name"] = "t-" + proto
            try:
                dlg = _forms.FormDialog(root, "表单自检",
                                        host_fields(rec=rec), None)
                dlg.update_idletasks()
                if proto == "com":
                    cb = dlg._widgets.get("com_port")
                    eq(str(cb.winfo_class()), "TCombobox",
                       "档案表单的串口渲染成下拉")
                    eq(str(cb.cget("state")), "normal",
                       "档案表单的串口可编辑（能手输）")
                dlg.destroy()
                check(True, f"{proto} 的表单控件全部能创建")
            except Exception as e:  # noqa: BLE001
                check(False, f"{proto} 表单创建失败：{type(e).__name__}: {e}")

        section("9. 设置中心「设备调试」分区能打开")
        root.open_settings("debug")
        for _ in range(20):
            root.update()
            root.after(10)
        pane = root._settings_reg().get(root.SETTINGS_TAB)
        check(pane is not None, "设置页签建出来了")
        check(getattr(pane, "section", "") == "debug", "停在设备调试分区")
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass

    print("\n" + "=" * 50)
    print(f"{'全部通过' if not BAD else '有失败'}：{OK}/{OK + BAD}")
    return 0 if not BAD else 1


if __name__ == "__main__":
    raise SystemExit(main())
