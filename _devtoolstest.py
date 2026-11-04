# -*- coding: utf-8 -*-
"""自测：core/devtools.py（ADB / HDC 组件定位、端口占用、设备清单解析）。

这些函数全都「要么不碰系统、要么只读」，所以可以离线跑：
  · 解析用现成的字符串样本（中文 / 英文 netstat、adb / hdc 两种输出）；
  · 定位与端口占用用替身函数，不去起真 adb，也不杀任何进程。

    python _devtoolstest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import devtools  # noqa: E402

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


def main() -> int:
    section("1. 端口解析")
    eq(devtools._port_of("0.0.0.0:5037"), 5037, "IPv4 监听地址取端口")
    eq(devtools._port_of("[::]:8710"), 8710, "IPv6 监听地址取端口")
    eq(devtools._port_of("*:5555"), 5555, "通配地址取端口")
    eq(devtools._port_of("garbage"), 0, "认不出来返回 0")

    section("2. netstat 行解析（英文 / 中文表头）")
    sample_en = (
        "Active Connections\n\n"
        "  Proto  Local Address          Foreign Address        State           PID\n"
        "  TCP    0.0.0.0:5037           0.0.0.0:0              LISTENING       4321\n"
        "  TCP    127.0.0.1:5037         127.0.0.1:52000        ESTABLISHED     4321\n"
        "  TCP    [::]:8710              [::]:0                 LISTENING       9988\n"
        "  TCP    192.168.3.10:22        192.168.3.5:51000      ESTABLISHED     4\n")
    rows = devtools._listen_rows(sample_en)
    eq(len(rows), 2, "只收 LISTENING，ESTABLISHED 不算占用")
    eq(rows[0], ("0.0.0.0:5037", 4321), "解析出地址与 PID")
    sample_cn = (
        "活动连接\r\n\r\n"
        "  协议  本地地址          外部地址        状态            PID\r\n"
        "  TCP    0.0.0.0:8710      0.0.0.0:0       侦听            7788\r\n")
    rows2 = devtools._listen_rows(sample_cn)
    check(rows2 and rows2[0][1] == 7788, "中文「侦听」也能认出来（不看表头）")
    eq(devtools._listen_rows(""), [], "空输入不炸")

    section("3. adb devices 解析")
    adb_txt = (
        "List of devices attached\n"
        "* daemon not running; starting now at tcp:5037\n"
        "R58M123ABC\tdevice product:beyond1 model:SM_G973N device:beyond1 "
        "transport_id:1\n"
        "192.168.1.5:5555\tdevice product:x model:Pixel_7 transport_id:2\n"
        "emulator-5554\toffline\n"
        "ABCDEF\tunauthorized usb:1-1 transport_id:3\n")
    rows = devtools.parse_adb_devices(adb_txt)
    eq(len(rows), 4, "四台设备（含 offline / unauthorized）")
    eq(rows[0]["serial"], "R58M123ABC", "序列号")
    eq(rows[0]["state"], "device", "状态")
    eq(rows[0]["model"], "SM G973N", "型号下划线换成空格")
    check(rows[1]["remote"] is True, "ip:port 判为远程")
    check(rows[2]["remote"] is False, "emulator 不算远程")
    check(all(r["serial"] for r in rows), "没有空行混进来")

    section("4. hdc list targets 解析（带 -v / 不带 -v）")
    hdc_v = (
        "127.0.0.1:5555          TCP     Connected       localhost       HarmonyOS 5.0\n"
        "FMR0223C13000789        USB     Connected       localhost       HarmonyOS 5.0\n"
        "OFFLINE123              USB     Offline         localhost       \n")
    rows = devtools.parse_hdc_targets(hdc_v)
    eq(len(rows), 3, "三行目标")
    eq(rows[0]["serial"], "127.0.0.1:5555", "key 作为序列号")
    eq(rows[0]["state"], "Connected", "状态列")
    check(rows[0]["remote"] is True, "TCP 判为远程")
    check(rows[1]["remote"] is False, "USB 不是远程")
    eq(rows[2]["state"], "Offline", "Offline 保留原样")
    rows2 = devtools.parse_hdc_targets("FMR0223C13000789\n\n")
    eq(len(rows2), 1, "不带 -v 的一行一个 key 也能解析")
    eq(rows2[0]["state"], "Connected", "缺状态时默认 Connected")
    eq(devtools.parse_hdc_targets("Empty"), [], "Empty 当作没有设备")
    # hdc 会把本机所有 COM 口列成 UART/Ready，这些不能当成「插着的设备」
    uart_rows = devtools.parse_hdc_targets(
        "COM1            UART    Ready       unknown         hdc\n"
        "COM3            UART    Ready       unknown         hdc\n")
    got, why = devtools.pick_serial("hdc", uart_rows)
    check(got == "" and "UART" in why, "只有 UART 候选时不自动选")
    eq(devtools.pick_serial("hdc", uart_rows, "COM3")[0], "COM3",
       "用户明确写了 COM3 才走串口")
    mixed = uart_rows + devtools.parse_hdc_targets(
        "FMR0223C13000789        USB     Connected   localhost   HarmonyOS 5.0\n")
    eq(devtools.pick_serial("hdc", mixed)[0], "FMR0223C13000789",
       "有 USB 设备时自动选它，不选 COM")

    section("5. pick_serial：多设备绝不猜")
    one = [{"serial": "S1", "state": "device"}]
    eq(devtools.pick_serial("adb", one)[0], "S1", "只有一台时自动选它")
    two = one + [{"serial": "S2", "state": "device"}]
    got, why = devtools.pick_serial("adb", two)
    eq(got, "", "两台且没指定 → 不给结果")
    check("S1" in why and "S2" in why, "错误里列出可选的序列号")
    eq(devtools.pick_serial("adb", two, "S2")[0], "S2", "指定了就选指定的")
    got, why = devtools.pick_serial("adb", two, "S9")
    check(got == "" and "没找到" in why, "指定了不存在的序列号要报错")
    got, why = devtools.pick_serial(
        "adb", [{"serial": "S1", "state": "unauthorized"}])
    check(got == "" and "允许" in why, "未授权时提示去设备上点允许")
    eq(devtools.pick_serial("adb", [])[0], "", "空清单返回空")

    section("6. 端口占用文案必须点名具体软件")
    owner = {"pid": 4321, "name": "adb.exe", "path": r"C:\Studio\adb.exe"}
    t = devtools.conflict_text("adb", owner)
    for word in ("Android Studio", "5037", "4321", "adb.exe", "投屏"):
        check(word in t, f"adb 文案包含「{word}」")
    t2 = devtools.conflict_text("hdc", {"pid": 7, "name": "hdc.exe"})
    for word in ("DevEco Studio", "鸿蒙", "7"):
        check(word in t2, f"hdc 文案包含「{word}」")
    eq(devtools.conflict_text("adb", None), "", "没有占用者就没有文案")

    section("7. resolve_tool：auto 先环境变量、后 bin")
    orig_env, orig_bin = devtools._env_path, devtools._bundled_path
    real_pref, real_port = devtools.resolve_tool, devtools.port_owner
    try:
        devtools._env_path = lambda bus: ""
        devtools._bundled_path = lambda bus: r"C:\App\bin\adb.exe"
        info = devtools.resolve_tool("adb", "auto")
        check(info["exists"] and info["src_used"] == "bundled",
              "没有环境变量 → 用内置 bin")
        devtools._env_path = lambda bus: r"C:\SDK\adb.exe"
        info = devtools.resolve_tool("adb", "auto")
        check(info["src_used"] == "env", "环境变量优先")
        info = devtools.resolve_tool("adb", "bundled")
        check(info["src_used"] == "bundled", "强制内置时不看环境变量")
        devtools._env_path = lambda bus: ""
        info = devtools.resolve_tool("adb", "env")
        check(not info["exists"] and "PATH" in info["error"],
              "强制环境变量但找不到 → 给出人话指引")
        info = devtools.resolve_tool("nope", "auto")
        check(not info["exists"], "不认识的组件返回不存在")

        section("8. preflight：同路径不冲突，不同路径算冲突")
        devtools._env_path = lambda bus: r"C:\App\bin\adb.exe"
        devtools.port_owner = lambda port: {
            "pid": 11, "name": "adb.exe", "path": r"C:\App\bin\adb.exe"}
        pf = devtools.preflight("adb", "auto", 5037)
        check(pf["same_tool"] and not pf["conflict"], "同一个文件不算冲突")
        devtools.port_owner = lambda port: {
            "pid": 12, "name": "adb.exe", "path": r"D:\Other\adb.exe"}
        pf = devtools.preflight("adb", "auto", 5037)
        check(pf["conflict"] and not pf["ok"], "不同路径算冲突")
        check("5037" in pf["note"], "note 里给了端口")
        devtools.port_owner = lambda port: None
        pf = devtools.preflight("adb", "auto", 5037)
        check(pf["ok"] and not pf["conflict"], "没人占用 → 体检通过")
    finally:
        devtools._env_path, devtools._bundled_path = orig_env, orig_bin
        devtools.resolve_tool, devtools.port_owner = real_pref, real_port

    section("9. 真实 bin 目录（有就打勾，没有只提示）")
    b = devtools.bin_dir()
    print(f"  · bin = {b}")
    for name in ("adb.exe", "hdc.exe"):
        p = b / name
        print(f"  · {name}: {'有' if p.is_file() else '没有（用环境变量或跑 _fetch_bins）'}")
    check(devtools.app_dir() == ROOT, "源码运行时 app_dir 就是项目根")

    print("\n" + "=" * 50)
    print(f"{'全部通过' if not BAD else '有失败'}：{OK}/{OK + BAD}")
    return 0 if not BAD else 1


if __name__ == "__main__":
    raise SystemExit(main())
