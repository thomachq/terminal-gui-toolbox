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

from core.cli_channel import (CLIClient, CLIError, DEFAULT_ENCODINGS,
                              _MORE, _PASSWORD, _PROMPT_TAIL)

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240

# 登录提示（Telnet 才会一上来就问账号）
_LOGIN = re.compile(r"(?:login:|login as:|username:|user\s*name:)", re.I)


class TelnetError(CLIError):
    pass


class TelnetClient(CLIClient):
    """一条 Telnet 连接。command() 是串行的：同一时刻只跑一条命令。

    收包、翻页、提示符、enable 这些行为都在 CLIClient 里，和 SSH 通道共用；
    这里只负责 Telnet 特有的部分：socket 收发、IAC 协商、登录提示。
    """

    def __init__(self, host: str, port: int = 23, timeout: int = 15) -> None:
        super().__init__()
        self.host = host
        self.port = int(port or 23)
        self.timeout = timeout
        self.sock: socket.socket | None = None
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
