# -*- coding: utf-8 -*-
"""自测：COM（console）串口会话。

用一条**假串口**跑完整个交互：登录 → 提示符 → 命令回显 → 分页 → enable。
这样不需要真交换机，也不会去打开本机串口。

    python _serialtest.py

串口的 ctypes 层（core/serialport.py）只验参数归一与「打不开要报人话」，
真正的 CreateFile/SetCommState 只能上真硬件验 —— 那部分在这里明确跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import serialport as SP           # noqa: E402
from core.console_session import ComClient, ComSession  # noqa: E402
from core.session import NeedElevation, SessionError      # noqa: E402

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


class FakeDev:
    """一条「假交换机」串口：认 admin/pw，有一个 SW1 提示符，支持分页与 enable。"""

    def __init__(self, banner: bytes = b"Welcome to FakeSwitch\r\nlogin: "):
        self.out = banner
        self.closed = False
        self.mode = "login"
        self.pending = ""
        self.sent: list[str] = []
        self.echo = True

    @property
    def alive(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True

    def write(self, data: bytes) -> int:
        text = data.decode("utf-8", "replace")
        self.sent.append(text)
        self.out += self._react(text).encode("utf-8")
        return len(data)

    def read(self, timeout: float = 0.35) -> bytes:
        d, self.out = self.out, b""
        return d

    # -- 设备行为
    def _react(self, text: str) -> str:
        if self.closed:
            return ""
        if self.mode == "login":
            if text.strip() == "admin":
                self.mode = "password"
                return "\r\nPassword: "
            return ""
        if self.mode == "password":
            if text.strip() == "pw":
                self.mode = "user"
                return "\r\nSW1>"
            return "\r\nPassword: "
        # 分页：上一次输出停在了 --More--
        if text == " " and self.pending:
            rest, self.pending = self.pending, ""
            return rest + "\r\nSW1>"
        cmd = text.strip()
        if cmd == "enable":
            self.mode = "enable_pw"
            return "\r\nenable\r\nPassword: "
        if cmd == "show version":
            return ("\r\nshow version\r\n"
                    "Huawei Versatile Routing Platform Software\r\n"
                    "VRP (R) software, Version 8.180 (CE6865 V200R019C10SPC800)\r\n"
                    "SW1>")
        if cmd == "display clock":
            self.pending = "2026-11-04 10:00:00+08:00\r\nSecond line\r\n"
            return ("\r\ndisplay clock\r\n"
                    "2026-11-04 10:00:00+08:00\r\n"
                    "  ---- More ----")
        if self.mode == "enable_pw":
            if text.strip() == "en":
                self.mode = "priv"
                return "\r\nSW1#"
            return "\r\nPassword: "
        if cmd.startswith("reboot"):
            return "\r\n% Not enough privileges\r\nSW1>"
        return f"\r\n{cmd}\r\n% Unrecognized command found at '^' position.\r\nSW1>"


def main() -> int:
    section("1. 串口参数归一")
    eq(SP.norm_port("com3"), "COM3", "小写 com3 → COM3")
    eq(SP.norm_port(r"\\.\COM12"), "COM12", "去掉 \\\\.\\ 前缀")
    eq(SP.norm_port("  "), "", "空串保持空")
    p = SP.norm_params(9600, 8, "N", 1, "none")
    eq(p, {"baud": 9600, "databits": 8, "parity": "none",
           "stopbits": "1", "flow": "none"}, "9600 8N1 归一")
    eq(SP.norm_params()["baud"], 115200, "默认 115200")
    eq(SP.params_text(p), "9600 8N1", "参数显示成 9600 8N1")
    eq(SP.params_text({"baud": 115200, "databits": 8, "parity": "even",
                       "stopbits": "1"}), "115200 8E1", "偶校验显示 E")
    for bad, why in ((0, "波特率 0"), ("abc", "波特率 abc"),
                     (9600, "数据位不是 5/6/7/8")):
        try:
            if why.startswith("数据位"):
                SP.norm_params(9600, 9, "none", "1", "none")
            else:
                SP.norm_params(bad)
        except SP.SerialError as e:
            check(True, f"{why} → 抛 SerialError：{str(e)[:24]}")
        else:
            check(False, f"{why} 应该抛异常")
    try:
        SP.norm_params(9600, 8, "none", "1", "weird")
    except SP.SerialError:
        check(True, "未知流控 → 抛 SerialError")

    section("2. 打不开的串口要说人话")
    link = SP.SerialLink("COM9999")
    try:
        link.open()
    except SP.SerialError as e:
        check("COM9999" in str(e), f"错误里带端口名：{e}")
        check(any(k in str(e) for k in ("没有这个串口", "被别的程序占用",
                                        "系统错误")), "错误是可读的中文")
    except Exception as e:  # noqa: BLE001
        check(False, f"抛了非 SerialError：{type(e).__name__}: {e}")
    else:
        link.close()
        check(True, "本机居然真有 COM9999（跳过）")

    section("3. 假串口：登录")
    dev = FakeDev()
    cli = ComClient("COM9", 115200, 8, "none", "1", "none", link=dev)
    banner = cli.open()
    check("FakeSwitch" in banner, "open() 收到开机 banner")
    text = cli.login("admin", "pw")
    check("Password" in text or "SW1>" in text, "登录过程收到了密码提示")
    eq(cli.last_prompt, "SW1>", "记住了用户模式提示符")

    section("4. 单条命令")
    out = cli.command("show version", timeout=6)
    check("VRP" in out, "拿到命令输出")
    check("show version" in out, "回显也在（console 就是这样）")
    eq(cli.last_prompt, "SW1>", "提示符没变")

    section("5. 分页自动翻")
    out = cli.command("display clock", timeout=8)
    check("Second line" in out, "第二页内容被取回（说明自动敲了空格）")
    check(" " in dev.sent, "确实给设备发过空格")

    section("6. enable 提权")
    ok = cli.enable("en")
    check(ok, "进特权模式成功")
    eq(cli.last_prompt, "SW1#", "提示符变成 #")
    bad = ComClient("COM9", link=FakeDev())
    bad.open()
    bad.login("admin", "pw")
    check(not bad.enable("wrong"), "密码错时 enable 返回 False")
    check(bool(bad.last_error), "并且给出了失败原因")

    section("7. ComSession：对外就是普通会话")
    dev2 = FakeDev()
    s = ComSession()
    s.connect("COM9", 115200, 8, "none", "1", "none",
              user="admin", password="pw", link=dev2)
    eq(s.kind, "com", "kind = com")
    eq(s.host, "COM9", "host = 串口名")
    check("115200 8N1" in s.label, f"label 带线路参数：{s.label}")
    rc, out, err = s.run("show version", timeout=6)
    check(rc == 0 and "VRP" in out, "run() 走 CLI，rc=0")
    try:
        s.run_admin("reboot")
    except NeedElevation as e:
        check(e.kind == "enable", "没提权时 run_admin 抛 enable 提示")
    else:
        check(False, "没提权竟然执行了特权命令")
    check(s.sudo_ok("en"), "sudo_ok 在网络设备上就是 enable")
    check(s.verify_root()[0], "verify_root 认特权模式")
    rc2, out2, _ = s.run_admin("reboot")
    check("% Not enough privileges" in out2, "提权后命令真的发下去了")
    s.close()
    check(not s.alive, "close 之后 alive=False")

    section("8. 编码：GBK 的 console")
    class GbkDev(FakeDev):
        def __init__(self):
            super().__init__(b"\xc4\xe3\xba\xc3\r\nlogin: ")
    g = GbkDev()
    c2 = ComClient("COM9", link=g)
    b = c2.open()
    check("你好" in b, f"GBK banner 被正确解码：{b.strip()[:12]}")
    eq(c2.encoding, "gbk", "编码自动切到 gbk")

    print("\n" + "=" * 50)
    print(f"{'全部通过' if not BAD else '有失败'}：{OK}/{OK + BAD}")
    return 0 if not BAD else 1


if __name__ == "__main__":
    raise SystemExit(main())
