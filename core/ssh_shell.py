# -*- coding: utf-8 -*-
"""SSH 的持久 shell 通道（paramiko invoke_shell）。

为什么需要它：SSH 连交换机 / 路由器时，`exec_command` 每次都是**新开一个
通道**，设备上的权限和视图状态不会带过去 —— 所以 `configure terminal` 敲完
下一条命令又回到用户模式，等于没法改配置。这不是「SSH 只能只读」，
而是「exec 通道只能只读」。

走 `invoke_shell()` 拿一条真正的交互式会话，模式就能保持，和 Telnet 一样能
进配置视图。收包、翻页、提示符这些行为与 Telnet 共用 CLIClient，
所以两边的判定标准完全一致。
"""
from __future__ import annotations

import socket

import paramiko

from core.cli_channel import CLIClient, CLIError


class SSHShellClient(CLIClient):
    """在一台已连上的 SSH 上开一条交互式 shell。"""

    # 网络设备的分页是按「屏幕行数」算的，行数给小了会疯狂翻页
    COLS = 512
    ROWS = 1000

    def __init__(self, client: paramiko.SSHClient,
                 cols: int | None = None, rows: int | None = None) -> None:
        super().__init__()
        try:
            self.chan = client.invoke_shell(
                term="vt100", width=int(cols or self.COLS),
                height=int(rows or self.ROWS))
        except Exception as e:  # noqa: BLE001
            raise CLIError(f"开不了交互式 shell：{e}") from e
        self.chan.settimeout(0.35)
        try:
            # 不关掉回显的话，每条命令都会在输出里重复一遍
            self.chan.get_pty(term="vt100", width=int(cols or self.COLS),
                              height=int(rows or self.ROWS))
        except Exception:  # noqa: BLE001
            pass
        try:
            self.chan.setblocking(False)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 收发
    def _send(self, text: str) -> None:
        if not self.alive():
            raise CLIError("shell 已关闭")
        try:
            self.chan.send(text.encode(self.encoding, "replace"))
        except Exception as e:  # noqa: BLE001
            raise CLIError(f"发送失败：{e}") from e

    def _recv_once(self, wait: float = 0.35) -> str:
        try:
            if not self.chan.recv_ready():
                # 没数据时别忙等，让出一点时间给设备
                import time as _t
                _t.sleep(min(wait, 0.2))
                if not self.chan.recv_ready():
                    return ""
            raw = self.chan.recv(65535)
        except socket.timeout:
            return ""
        except Exception:  # noqa: BLE001
            return ""
        if not raw:
            return ""
        return self._decode(raw)

    def alive(self) -> bool:
        ch = getattr(self, "chan", None)
        if ch is None:
            return False
        try:
            return not ch.closed and ch.active
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        ch = getattr(self, "chan", None)
        self.chan = None
        try:
            if ch:
                ch.close()
        except Exception:  # noqa: BLE001
            pass
