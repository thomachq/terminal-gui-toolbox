# -*- coding: utf-8 -*-
"""COM 口（console）会话：把「串口 + 设备 CLI」包装成和 SSH/Telnet 一样的会话。

为什么要包一层 `CLIClient` 而不是自己写收发：
  交换机 / 防火墙 / 路由器 / 安全设备的 console 全是同一类交互 ——
  发一行、等安静、遇 `--More--` 敲空格、`enable` 进特权、提示符可能是
  `SW1#` / `R1>` / `(config)#`。这些 `core.cli_channel.CLIClient` 已经做完了，
  Telnet 用的也是它；这里只补串口特有的两件事：**打开串口** 和 **console 登录**。

和 Telnet 的差别只有一个：console 线是本地物理链路，不存在「网络不可达」，
但存在「设备没上电 / 线松了 / 波特率不对」—— 所以错误文案必须点名这几种，
而不是笼统的「连接失败」。
"""
from __future__ import annotations

import re
import time

from core.cli_channel import CLIError, CLIClient, _PASSWORD
from core import serialport
from core.session import BaseSession, SessionError

#: console 登录提示。网络设备的 console 常见三种：
#:   `Username:` / `Password:`   （华为 / H3C / Cisco 的 AAA）
#:   `login:`                     （老设备 / Linux getty）
#:   `Password:` 单独出现          （只问密码）
_LOGIN = re.compile(r"(?:login:|login as:|username:|user\s*name:)", re.I)


class ComError(CLIError):
    pass


class ComClient(CLIClient):
    """一条串口 CLI。所有平台差异都在 `core.serialport` 里。"""

    quiet_default = 0.6

    def __init__(self, port: str, baud=None, databits=None, parity=None,
                 stopbits=None, flow=None, link=None) -> None:
        super().__init__()
        self.port = serialport.norm_port(port)
        self.params = serialport.norm_params(baud, databits, parity,
                                             stopbits, flow)
        #: 允许注入一条假链路（自测用）；正常连接传 None，自己开真串口
        self._link = link
        self.banner = ""
        self.encoding = "utf-8"

    # ------------------------------------------------------------ 连接
    def open(self, timeout: float = 3.0) -> str:
        if self._link is None:
            self._link = serialport.open_link(self.port, **self.params)
        # console 线插上后设备可能已经在吐启动日志；先读一小段，
        # 既能清掉残留，也能把开机 banner 记下来（排障时很有用）。
        self.banner = self._drain(time.time() + max(0.8, float(timeout)))
        self._remember_prompt(self.banner)
        return self.banner

    def login(self, user: str = "", password: str = "",
              timeout: float = 8.0) -> str:
        """按提示走一遍 console 登录。设备直接给提示符也能正常返回。

        为什么不在 `open()` 里自动登录：有些设备的 console 是
        「免登录直接进 CLI」，多发一次回车会打断它正在输出的启动过程；
        这里只对**明确出现的提示**回应。
        """
        if not self.alive():
            raise ComError("串口没有打开")
        text = self.banner or ""
        if not text:
            text = self._drain(time.time() + 2.0)
        if _PASSWORD.search(text) and not _LOGIN.search(text):
            if password:
                self._send(password + "\r\n")
                text += self._drain(time.time() + float(timeout))
        elif _LOGIN.search(text):
            if user:
                self._send(user + "\r\n")
                text += self._drain(time.time() + float(timeout))
            if _PASSWORD.search(text) and password:
                self._send(password + "\r\n")
                text += self._drain(time.time() + float(timeout))
        self._remember_prompt(text)
        self.banner = text
        return text

    def alive(self) -> bool:
        """注意：`CLIClient` 里这是**方法**（`self.alive()`），
        写成 property 会让父类的 `command()` 里 `self.alive()` 直接崩。"""
        return bool(self._link and self._link.alive)

    def close(self) -> None:
        if self._link is not None:
            try:
                self._link.close()
            except Exception:  # noqa: BLE001
                pass
        self._link = None

    # ------------------------------------------------------------ 收发
    def _send(self, text: str) -> None:
        if not self.alive():
            raise ComError("串口已关闭")
        try:
            self._link.write(text.encode(self.encoding, "replace"))
        except serialport.SerialError as e:
            raise ComError(str(e)) from e

    def _recv_once(self, wait: float = 0.35) -> str:
        if not self.alive():
            return ""
        try:
            raw = self._link.read(wait)
        except serialport.SerialError:
            return ""
        return self._decode(raw) if raw else ""

    # ------------------------------------------------------------ 交互
    def send_break(self) -> None:
        """发一个空行 —— 「按回车唤醒 console」是最常见的操作。"""
        self._send("\r\n")


class ComSession(BaseSession):
    """console 会话。对外接口与 TelnetSession 一致，页面不用知道这是串口。"""

    kind = "com"
    #: COM 设备默认是网络设备（交换机 / 防火墙）；`detect()` 认出 Linux 会改回来
    host_kind = "network"

    def __init__(self) -> None:
        super().__init__()
        self._impl: ComClient | None = None
        self.sudo_pw: str | None = None
        self.enabled = False
        self.vendor = ""
        #: detect() 的探针等不了 12 秒：串口上设备不回就是没上电 / 波特率错，
        #: 等太久用户只会以为卡死。6 秒足够交换机回一行。
        self.probe_timeout = 6

    # ------------------------------------------------------------ 连接
    def connect(self, port: str, baud=None, databits=None, parity=None,
                stopbits=None, flow=None, user: str = "",
                password: str = "", link=None) -> None:
        self.close()
        cli = ComClient(port, baud, databits, parity, stopbits, flow, link=link)
        try:
            cli.open()
            cli.login(user, password)
        except serialport.SerialError as e:
            try:
                cli.close()
            except Exception:  # noqa: BLE001
                pass
            raise SessionError(str(e)) from e
        except ComError:
            try:
                cli.close()
            except Exception:  # noqa: BLE001
                pass
            raise
        self._impl = cli
        self.host = serialport.norm_port(port)
        self.port = 0
        self.user = user
        self.label = f"{self.host} · {serialport.params_text(cli.params)}"

    @property
    def alive(self) -> bool:
        return bool(self._impl and self._impl.alive())

    def close(self) -> None:
        try:
            if self._impl:
                self._impl.close()
        except Exception:  # noqa: BLE001
            pass
        self._impl = None
        self.enabled = False
        self.elevated = False
        self.elev_method = ""
        self.elev_msg = ""

    # ------------------------------------------------------------ 执行
    def run(self, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        if not self._impl:
            raise SessionError("串口没有连接")
        try:
            out = self._impl.command(cmd, timeout=timeout)
        except ComError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SessionError(f"串口执行失败：{e}") from e
        return 0, out, ""

    # ------------------------------------------------------------ 提权
    @property
    def can_cli(self) -> bool:
        return self._impl is not None

    def cli(self):
        return self._impl

    @property
    def admin_kind(self) -> str:
        return "sudo" if self.host_kind == "unix" else "enable"

    def can_admin(self) -> tuple[bool, str]:
        if self.host_kind == "unix":
            if self.is_root or self.sudo_pw:
                return True, ""
            return False, "需要 sudo 密码"
        if self.enabled or self.is_root:
            return True, ""
        return False, "需要 enable 密码"

    def sudo_ok(self, pw: str) -> bool:
        """一个密码框两种含义：网络设备是 enable，Linux 是 sudo。"""
        self.elev_msg = ""
        self.elev_method = ""
        if not self._impl:
            self.elev_msg = "串口连接已断开"
            return False
        if self.host_kind == "unix":
            ok = bool(self._impl.sudo_check(pw))
            if ok:
                self.sudo_pw = pw
                self.is_root = True
                self.elevated = True
                self.elev_method = "percmd"
            else:
                self.elev_msg = (getattr(self._impl, "last_error", "")
                                 or "sudo 密码没通过")
            return ok
        ok = bool(self._impl.enable(pw))
        self.enabled = bool(ok)
        if ok:
            self.sudo_pw = pw
            self.is_root = True
            self.elevated = True
            self.elev_method = "enable"
        else:
            self.elev_msg = (getattr(self._impl, "last_error", "")
                             or "enable 没成功")
        return ok

    def elevate_root(self, pw: str = "") -> bool:
        return self.sudo_ok(pw)

    def verify_root(self) -> tuple[bool, str]:
        if self.host_kind == "network":
            ok = bool(self.enabled)
            return ok, ("特权模式已进" if ok
                        else (self.elev_msg or "没进特权模式"))
        return bool(self.elevated), (self.elev_method or "")

    def run_admin(self, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
        if not self._impl:
            raise SessionError("串口没有连接")
        if self.host_kind == "unix":
            if self.is_root:
                return self.run_traced(cmd, timeout)
            if not self.sudo_pw:
                from core.session import NeedElevation
                raise NeedElevation("这一步要 root 权限：请提供 sudo 密码。",
                                    kind="sudo", target=self.host)
            ok, out = self._impl.run_sudo(cmd, self.sudo_pw, timeout=timeout)
            self._trace(cmd, 0 if ok else 1, out, "", 0)
            return (0 if ok else 1), out, ""
        if not self.enabled:
            from core.session import NeedElevation
            raise NeedElevation("这一步要进入特权模式：请提供 enable 密码。",
                                kind="enable", target=self.host)
        return self.run_traced(cmd, timeout)

    def bind_device(self, info) -> None:
        super().bind_device(info)
        self.vendor = (getattr(info, "vendor", "") or "").lower()
