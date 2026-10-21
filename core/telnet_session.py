# -*- coding: utf-8 -*-
"""Telnet 协议层：只做「运维要用的那一层」。

不是完整的终端仿真器，目标很具体：
  · 能连上交换机 / 路由器 / 老设备
  · 能把一条命令发过去，把这段输出收回来
  · 能处理分页提示（--More--），不让人工一直敲空格
  · 能进 enable 特权模式（Cisco / H3C / 华为都是这个套路）

为什么不用 telnetlib：Python 3.13 起标准库把它删了，而且它不管分页、
不管提示符、不管 IAC 之外的乱码，实际拿来跑 `display current-configuration`
还是会卡住。这里用 socket 自己实现，代码量不大，行为可预期。

协议要点（IAC = 0xFF）：
  IAC IAC     → 数据里真的一个 0xFF
  IAC WILL/WONT/DO/DONT <opt>  → 选项协商
  IAC SB ... IAC SE            → 子协商，整段丢掉

我们对所有选项一律回 DONT / WONT：不需要窗口大小、不要回显协商，
服务端自己会降级成「普通字节流」，这正是我们要的。
"""
from __future__ import annotations

import re
import socket
import time

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240

# 分页提示：Cisco "<--- More --->"，H3C/华为 "--More--"，部分老设备 "---- More ----"
_MORE = re.compile(r"(?:<---\s*More\s*--->|--\s*More\s*--|----\s*More\s*----"
                   r"|--More--|more\s*--\s*|\(q\)uit)", re.I)

# 登录提示
_LOGIN = re.compile(r"(?:login:|login as:|username:|user\s*name:)", re.I)
_PASSWORD = re.compile(r"(?:password:|passwd:)", re.I)
_PROMPT_TAIL = re.compile(r"[#>\]$]\s*$")

DEFAULT_ENCODINGS = ("utf-8", "gbk", "latin-1")


class TelnetError(Exception):
    pass


class TelnetClient:
    """一条 Telnet 连接。command() 是串行的：同一时刻只跑一条命令。"""

    def __init__(self, host: str, port: int = 23, timeout: int = 15) -> None:
        self.host = host
        self.port = int(port or 23)
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.buf = ""
        self.encoding = "utf-8"
        self.last_prompt = ""
        self._closed = False

    # ------------------------------------------------------------ 连接
    def connect(self) -> None:
        if self.sock:
            return
        try:
            self.sock = socket.create_connection((self.host, self.port),
                                                 timeout=self.timeout)
        except OSError as e:
            raise TelnetError(f"连不上 {self.host}:{self.port} —— {e}") from e
        self.sock.settimeout(0.35)
        self._closed = False

    def login(self, user: str = "", password: str = "",
              timeout: int | None = None) -> str:
        """按提示走一遍登录。设备直接给提示符（免登录）也能正常返回。"""
        self.connect()
        limit = time.time() + (timeout or max(self.timeout, 20))
        banner = self._drain(limit)

        if _PASSWORD.search(banner) and not _LOGIN.search(banner):
            # 只要密码的老设备
            self._send(password + "\r\n")
            banner += self._drain(time.time() + 12)
        elif _LOGIN.search(banner):
            if user:
                self._send(user + "\r\n")
            banner += self._drain(time.time() + 12)
            if _PASSWORD.search(banner) and password:
                self._send(password + "\r\n")
                banner += self._drain(time.time() + 12)

        # 登录后可能是 ">"（用户模式）或 "#"（特权模式）
        self._remember_prompt(banner)
        return banner

    def alive(self) -> bool:
        return bool(self.sock and not self._closed)

    def close(self) -> None:
        self._closed = True
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    # ------------------------------------------------------------ 收发
    def _send(self, text: str) -> None:
        if not self.sock:
            raise TelnetError("连接已关闭")
        try:
            self.sock.sendall(text.encode(self.encoding, "replace"))
        except OSError as e:
            self._closed = True
            raise TelnetError(f"发送失败：{e}") from e

    def _recv_once(self, wait: float = 0.35) -> str:
        """收一包（可能为空）。IAC 协商就地处理掉，只把正文交给上层。"""
        if not self.sock:
            return ""
        try:
            self.sock.settimeout(wait)
            raw = self.sock.recv(65535)
        except socket.timeout:
            return ""
        except OSError:
            self._closed = True
            return ""
        if not raw:
            self._closed = True
            return ""
        return self._decode(self._strip_iac(raw))

    def _strip_iac(self, raw: bytes) -> bytes:
        """剥掉 IAC 协商序列，顺带回 WONT/DONT（我们不需要任何选项）。"""
        out = bytearray()
        reply = bytearray()
        i = 0
        n = len(raw)
        while i < n:
            b = raw[i]
            if b != IAC:
                out.append(b)
                i += 1
                continue
            if i + 1 >= n:
                break
            cmd = raw[i + 1]
            if cmd == IAC:                     # 转义的 0xFF
                out.append(IAC)
                i += 2
            elif cmd in (WILL, WONT, DO, DONT):
                if i + 2 >= n:
                    break
                opt = raw[i + 2]
                if cmd == WILL:
                    reply += bytes((IAC, DONT, opt))
                elif cmd == DO:
                    reply += bytes((IAC, WONT, opt))
                i += 3
            elif cmd == SB:                    # 子协商整段跳过
                i += 2
                while i + 1 < n and not (raw[i] == IAC and raw[i + 1] == SE):
                    i += 1
                i += 2
            else:
                i += 2
        if reply and self.sock:
            try:
                self.sock.sendall(bytes(reply))
            except OSError:
                pass
        return bytes(out)

    def _decode(self, raw: bytes) -> str:
        """先按当前编码解，出现替换字符就换一种试（设备常见 UTF-8 / GBK 混用）。"""
        for enc in (self.encoding,) + DEFAULT_ENCODINGS:
            try:
                text = raw.decode(enc)
            except UnicodeDecodeError:
                continue
            if "\ufffd" not in text:
                self.encoding = enc
                return text
        return raw.decode(self.encoding, "replace")

    def _drain(self, deadline: float, quiet: float = 0.6) -> str:
        """收到「安静一段时间」为止，同时自动翻过所有分页提示。"""
        parts: list[str] = []
        last = time.time()
        while time.time() < deadline:
            chunk = self._recv_once(0.35)
            if chunk:
                parts.append(chunk)
                last = time.time()
                if _MORE.search(chunk):
                    self._send(" ")          # 翻页
                    continue
            elif parts and time.time() - last >= quiet:
                break
            elif not parts and time.time() - last >= max(quiet, 2.0):
                break
        return "".join(parts)

    def _remember_prompt(self, text: str) -> None:
        """把最后一行的提示符记下来（"SW1#"、"R1>" 这种）。"""
        for line in reversed((text or "").splitlines()):
            s = line.strip()
            if s and len(s) <= 96 and _PROMPT_TAIL.search(s):
                self.last_prompt = s
                return

    # ------------------------------------------------------------ 命令
    def command(self, cmd: str, timeout: int = 30, quiet: float = 0.6) -> str:
        """发一条命令并收回输出。

        用「安静判定」而不是硬等提示符：网络设备的提示符五花八门
        （带主机名、带模块号、带 `(config)`），靠正则匹配一定会漏。
        安静 0.6 秒且已有内容，就认为这条命令跑完了。
        """
        if not self.alive():
            raise TelnetError("尚未连接")
        self._send(cmd + "\r\n")
        text = self._drain(time.time() + max(timeout, 3), quiet=quiet)
        self._remember_prompt(text)
        return text

    def send_line(self, text: str) -> None:
        """只发不收 —— 给交互式终端用（比如 conf t 之后一条条敲）。"""
        self._send(text + "\r\n")

    # ------------------------------------------------------------ 特权模式
    def enable(self, password: str = "", timeout: int = 15) -> bool:
        """进 enable 模式（Cisco `enable`，H3C/华为 `super`、`system-view` 另说）。

        判定标准是最后的提示符里出现 `#` —— Cisco / H3C / 华为的特权模式
        全都是 `#`，用户模式是 `>`。这比解析回显文字可靠。
        """
        if not self.alive():
            return False
        self._send("enable\r\n")
        out = self._drain(time.time() + 6, quiet=0.5)
        if _PASSWORD.search(out) and password:
            self._send(password + "\r\n")
            out += self._drain(time.time() + 8, quiet=0.6)
        elif password and re.search(r"^\s*password\s*:?\s*$", out, re.I | re.M):
            self._send(password + "\r\n")
            out += self._drain(time.time() + 8, quiet=0.6)
        self._remember_prompt(out)
        return "#" in (self.last_prompt or "")

    def disable_paging(self, vendor: str = "") -> None:
        """关掉分页。不关的话 `display current-configuration` 会一直翻页。"""
        cmds = {
            "cisco": ["terminal length 0"],
            "h3c": ["screen-length disable"],
            "huawei": ["screen-length 0 temporary"],
        }.get(vendor, ["terminal length 0", "screen-length disable",
                       "screen-length 0 temporary"])
        for c in cmds:
            try:
                self.command(c, timeout=8)
            except TelnetError:
                return
