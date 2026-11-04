# -*- coding: utf-8 -*-
"""自测：ADB / HDC 会话（core/debug_session.py）。

不碰真设备：把 `core.devtools.run_tool / list_devices / connect_device /
resolve_tool` 换成替身，只验「拼出来的命令对不对、退出码取得对不对、
多设备会不会乱挑」。

    python _devsessiontest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import devtools                     # noqa: E402
from core import debug_session as DS          # noqa: E402
from core.session import SessionError         # noqa: E402

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


class Fake:
    """替身集合：调用记录 + 可编排的返回值。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.devices = [{"serial": "S1", "state": "device"}]
        self.replies: dict[str, tuple] = {}
        self.connect_ok = True
        self.exists = True

    def resolve_tool(self, bus, src="auto"):
        return {"bus": bus, "path": f"C:/bin/{bus}.exe", "src_used": "bundled",
                "exists": self.exists, "tried": [],
                "error": "" if self.exists else f"没找到 {bus}"}

    def list_devices(self, bus, src="auto"):
        self.calls.append(("list", bus))
        return list(self.devices)

    def connect_device(self, bus, target, src="auto", timeout=20):
        self.calls.append(("connect", bus, target))
        return self.connect_ok, "connected to x" if self.connect_ok else "failed"

    def run_tool(self, bus, args, src="auto", timeout=25):
        self.calls.append(("run", bus, list(args)))
        key = " ".join(args)
        for pat, reply in self.replies.items():
            if pat in key:
                return reply
        return 0, "", ""


def main() -> int:
    section("0. 内层退出码解析")
    eq(DS._split_rc(0, "abc\n__TTB_RC__0\n", ""), (0, "abc", ""),
       "标记行被删掉，rc 取内层")
    eq(DS._split_rc(0, "abc\n__TTB_RC__3\n", "")[0], 3, "内层 rc=3 要透出来")
    eq(DS._split_rc(7, "abc\n", "")[0], 7, "没有标记就退回进程 rc")
    eq(DS._split_rc(0, "__TTB_RC__x\n", "")[0], 0, "标记值不是数字时退回进程 rc")
    rc, out, _ = DS._split_rc(0, "a\n__TTB_RC__0\nb\n", "")
    eq(out, "a\nb", "标记之后的输出不会被吞")

    fake = Fake()
    orig = (devtools.resolve_tool, devtools.list_devices,
            devtools.connect_device, devtools.run_tool)
    devtools.resolve_tool = fake.resolve_tool
    devtools.list_devices = fake.list_devices
    devtools.connect_device = fake.connect_device
    devtools.run_tool = fake.run_tool
    try:
        section("1. ADB：USB 连接与命令拼装")
        s = DS.AdbSession()
        try:
            s.connect("S1", tool_src="bundled")
        except Exception as e:  # noqa: BLE001
            check(False, f"connect 抛了异常：{e}")
        eq(s.serial, "S1", "序列号记下来了")
        eq(s.host, "S1", "没有地址时 host 用序列号（会话池靠它去重）")
        eq(s.label, "adb://S1", "label 便于在终端里辨认")
        eq(s.tool_src, "bundled", "组件来源透传")

        fake.replies["id -u"] = (0, "0\n__TTB_RC__0\n", "")
        rc, out, err = s.run("id -u")
        eq(rc, 0, "内层 rc=0")
        eq(out, "0", "输出里没有标记行")
        run_call = [c for c in fake.calls if c[0] == "run"][-1]
        eq(run_call[2], ["-s", "S1", "shell", "id -u; echo __TTB_RC__$?"],
           "拼成 adb -s <serial> shell <cmd; echo rc>")

        fake.replies["getprop ro.product.model"] = (0, "Pixel 7\n__TTB_RC__0\n", "")
        fake.replies["ro.build.version.release"] = (0, "14\n__TTB_RC__0\n", "")
        info = s.probe_info()
        eq(info["model"], "Pixel 7", "读到型号")
        eq(info["version"], "Android 14", "版本带 Android 前缀")

        section("2. 多设备：不指定就停手")
        fake.devices = [{"serial": "S1", "state": "device"},
                        {"serial": "S2", "state": "device"}]
        s2 = DS.AdbSession()
        try:
            s2.connect()
        except SessionError as e:
            check("S1" in str(e) and "S2" in str(e),
                  "错误里列出两台设备，让用户自己选")
        else:
            check(False, "多设备竟然自己挑了一台")
        s3 = DS.AdbSession()
        s3.connect("S2")
        eq(s3.serial, "S2", "指定了就按指定的来")

        section("3. 没有设备 / 未授权")
        fake.devices = []
        try:
            DS.AdbSession().connect()
        except SessionError as e:
            check("没有发现设备" in str(e), "空清单给人话提示")
        fake.devices = [{"serial": "S1", "state": "unauthorized"}]
        try:
            DS.AdbSession().connect()
        except SessionError as e:
            check("允许" in str(e), "未授权时提示去设备上点允许")

        section("4. 远程 ADB")
        fake.devices = [{"serial": "192.168.1.5:5555", "state": "device",
                        "remote": True}]
        s4 = DS.AdbSession()
        s4.connect("", host="192.168.1.5", port=5555)
        conn = [c for c in fake.calls if c[0] == "connect"][-1]
        eq(conn[2], "192.168.1.5:5555", "先 adb connect ip:port")
        eq(s4.serial, "192.168.1.5:5555", "再按清单定位设备")
        check(s4.remote, "标记为远程")
        fake.connect_ok = False
        try:
            DS.AdbSession().connect("", host="10.0.0.9", port=5555)
        except SessionError as e:
            check("连不上" in str(e) and "10.0.0.9:5555" in str(e),
                  "远程连不上时给出目标地址")

        section("5. HDC：命令行只差 -t")
        fake.connect_ok = True
        fake.devices = [{"serial": "FMR0223C13000789", "state": "Connected"}]
        h = DS.HdcSession()
        h.connect("FMR0223C13000789")
        eq(h.label, "hdc://FMR0223C13000789", "hdc 的 label")
        fake.replies["uname"] = (0, "Linux\n__TTB_RC__0\n", "")
        h.run("uname -s")
        run_call = [c for c in fake.calls if c[0] == "run"][-1]
        eq(run_call[2], ["-t", "FMR0223C13000789", "shell",
                         "uname -s; echo __TTB_RC__$?"],
           "拼成 hdc -t <key> shell ...")

        section("6. 组件缺失要说清楚")
        fake.exists = False
        try:
            DS.AdbSession().connect("S1", tool_src="bundled")
        except SessionError as e:
            check("没找到 adb" in str(e), "SessionError 里带着组的错误文案")
        else:
            check(False, "组件缺失竟然连上了")
        fake.exists = True

        section("7. alive 有缓存，不会每次都 fork 一次")
        fake.devices = [{"serial": "S1", "state": "device"}]
        fake.calls.clear()
        a = DS.AdbSession()
        a.connect("S1")
        n0 = len([c for c in fake.calls if c[0] == "list"])
        for _ in range(5):
            a.alive
        n1 = len([c for c in fake.calls if c[0] == "list"])
        eq(n1, n0, "3 秒内重复问 alive 不再列设备")
        a._alive_at = 0.0     # 手动把缓存过期
        check(a.alive is True, "过期后重新查得到在线")
        fake.devices = []
        a._alive_at = 0.0
        check(a.alive is False, "设备没了 alive 变 False")

        section("8. close 只断自己，不杀进程")
        before = len([c for c in fake.calls if c[0] == "run"])
        a.close()
        after = len([c for c in fake.calls if c[0] == "run"])
        eq(after, before, "close 不调用 adb/kill-server")
        check(not a.alive, "close 之后 alive=False")
        try:
            a.run("id")
        except SessionError as e:
            check("没有连接" in str(e), "断开后执行命令给出人话错误")
        else:
            check(False, "断开后竟然执行了命令")
    finally:
        (devtools.resolve_tool, devtools.list_devices,
         devtools.connect_device, devtools.run_tool) = orig

    print("\n" + "=" * 50)
    print(f"{'全部通过' if not BAD else '有失败'}：{OK}/{OK + BAD}")
    return 0 if not BAD else 1


if __name__ == "__main__":
    raise SystemExit(main())
