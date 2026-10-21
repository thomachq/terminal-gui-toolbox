# -*- coding: utf-8 -*-
"""会话抽象：把「在哪台机器上跑命令」这件事统一成一个接口。

  BaseSession.run(cmd) -> (rc, out, err)

三种实现：
  LocalSession   —— 本机（Windows 下走 cmd / powershell）
  SSHSession     —— paramiko，支持密码 / 私钥 / 私钥口令，可 sudo 提权
  TelnetSession  —— 完整交互式（见 telnet_session.py，第二批）

所有会话都带 `tracer` 钩子，命令与输出会进命令日志侧边栏。
"""
from __future__ import annotations

import os
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import dataclass


class SessionError(Exception):
    pass


# ------------------------------------------------------------------ 编码
def _ansi_enc() -> str:
    """Windows 下控制台输出的默认编码（中文系统是 cp936/GBK）。"""
    if sys.platform == "win32":
        try:
            import locale
            enc = locale.getpreferredencoding(False)
            if enc and enc.lower().replace("-", "") not in ("utf8", "cp65001"):
                return enc
        except Exception:  # noqa: BLE001
            pass
        return "mbcs"
    return "utf-8"


def decode_bytes(b: bytes) -> str:
    """子进程输出解码。

    先按 UTF-8 试；失败再按系统 ANSI 码页试。**不要**直接
    `decode("utf-8", "replace")` —— 中文 Windows 上 ipconfig / ping
    的回显是 GBK，强解会把「无法访问目标主机」变成一串 `����`。
    """
    if not b:
        return ""
    if b[:3] == b"\xef\xbb\xbf":
        b = b[3:]
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for enc in (_ansi_enc(), "utf-8"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("utf-8", "replace")


def windows_argv(cmd: str) -> list[str]:
    """把一条要在 Windows 上跑的命令转成 argv。

    已经写成 `powershell ... -Command "脚本"` 的就去掉外壳直接跑脚本 ——
    否则会变成 powershell 套 powershell，多一次进程、多一层引号解析，
    中文输出还可能被解码两遍。
    """
    s = (cmd or "").strip()
    low = s.lower()
    if low.startswith("powershell") or low.startswith("pwsh"):
        head, _, tail = s.partition(" ")
        body = tail.strip()
        changed = True
        while changed:
            changed = False
            for flag in ("-NoProfile", "-NonInteractive", "-NoLogo",
                         "-ExecutionPolicy", "Bypass", "-Command", "-c"):
                if body.lower().startswith(flag.lower()):
                    body = body[len(flag):].strip()
                    changed = True
        # 去掉包住整段脚本的那一对引号
        if len(body) >= 2 and body[0] == '"' and body[-1] == '"':
            body = body[1:-1]
        elif len(body) >= 2 and body[0] == "'" and body[-1] == "'":
            body = body[1:-1]
        return [head, "-NoProfile", "-NonInteractive", "-Command", body]
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", s]



class BaseSession:
    kind = "base"
    platform_hint = ""        # 已知系统族时填上，探测可省掉好几条探针
    ps_native = False         # 命令本来就跑在 PowerShell 里，不必再套一层

    def __init__(self) -> None:
        self.tracer = None          # tracer(display, rc, out, err, ms)
        self.host = ""
        self.port = 0
        self.user = ""
        self.label = ""
        self.is_root = False        # 探测到 root/管理员后由上层写入

    # ---------------- 由子类实现
    def run(self, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        raise NotImplementedError

    def close(self) -> None:
        pass

    @property
    def alive(self) -> bool:
        return False

    # ---------------- 公共
    def _trace(self, display: str, rc: int, out: str, err: str, ms: int) -> None:
        if self.tracer:
            try:
                self.tracer(display, rc, out, err, ms)
            except Exception:  # noqa: BLE001
                pass

    def run_traced(self, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        """执行并记日志。用户要看的「命令 + 输出」都走这里。

        追踪放在 run_traced 而不是 run —— 否则性能采样（每 3 秒一次）
        也会刷进命令日志，把真正有用的记录冲掉。
        """
        t0 = time.time()
        try:
            rc, out, err = self.run(cmd, timeout)
        except Exception as e:  # noqa: BLE001
            self._trace(cmd, 1, "", str(e), int((time.time() - t0) * 1000))
            raise
        self._trace(cmd, rc, out, err, int((time.time() - t0) * 1000))
        return rc, out, err

    # ---------------- 提权
    def run_admin(self, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
        """需要特权的操作（加路由、改防火墙、管 Docker）。

        非 root 时用 sudo 再跑一次，**命令与 sudo 都记进日志**，
        这样用户在侧边栏里能看清到底执行了什么。
        """
        return self.run_traced(cmd, timeout)

    def can_admin(self) -> tuple[bool, str]:
        """(能不能提权, 不能的话给用户看的一句话)"""
        return True, ""


# ------------------------------------------------------------------ 本机
class LocalSession(BaseSession):
    """本机命令。Windows 默认走 PowerShell，Unix 走 sh。"""

    kind = "local"

    def __init__(self) -> None:
        super().__init__()
        self.host = "localhost"
        self.user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        self.label = "本机"
        self.is_windows = sys.platform == "win32"
        self.ps_native = self.is_windows
        self.platform_hint = {"win32": "windows", "darwin": "darwin"}.get(
            sys.platform, "linux")

    @property
    def alive(self) -> bool:
        return True

    def run(self, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        try:
            if self.is_windows:
                args = windows_argv(cmd)
            else:
                args = ["/bin/sh", "-c", cmd]
            p = subprocess.run(args, capture_output=True, timeout=timeout,
                               creationflags=(subprocess.CREATE_NO_WINDOW
                                              if self.is_windows else 0))
            return p.returncode, decode_bytes(p.stdout), decode_bytes(p.stderr)
        except subprocess.TimeoutExpired:
            return 124, "", f"命令超时（{timeout}s）"
        except FileNotFoundError as e:
            return 127, "", str(e)
        except OSError as e:
            return 126, "", str(e)

    def run_admin(self, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
        if self.is_windows:
            # Windows 没有 sudo：要么本程序以管理员启动，要么命令自己失败
            return self.run_traced(cmd, timeout)
        try:
            root = os.geteuid() == 0
        except AttributeError:
            root = False
        if root:
            return self.run_traced(cmd, timeout)
        return self.run_traced("sudo -n " + shlex.quote(cmd), timeout)

    def can_admin(self) -> tuple[bool, str]:
        if self.is_windows:
            return True, "Windows 下需要以管理员身份运行本程序才能改路由 / 防火墙"
        return True, ""


# ------------------------------------------------------------------ SSH
class SSHSession(BaseSession):
    kind = "ssh"

    def __init__(self) -> None:
        super().__init__()
        self.client = None
        self.sudo_pw: str | None = None
        self._sftp = None

    # ---------------- 连接
    def connect(self, host: str, port: int = 22, user: str = "",
                password: str | None = None, key_path: str | None = None,
                key_passphrase: str | None = None,
                timeout: int = 15) -> None:
        import paramiko

        self.close()
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            cli.connect(
                hostname=host, port=int(port), username=user,
                password=password or None,
                key_filename=str(key_path) if key_path else None,
                passphrase=key_passphrase or None,
                timeout=timeout,
                # 指定私钥就只认这把，避免静默落到 ssh-agent 的其它钥匙上
                allow_agent=(not key_path),
                look_for_keys=(not key_path and not password),
            )
        except paramiko.AuthenticationException:
            raise SessionError("认证失败：检查用户名 / 密码 / 私钥（及私钥口令）")
        except (socket.timeout, TimeoutError):
            raise SessionError(f"连接超时：{host}:{port} 不可达或未开 SSH")
        except Exception as e:  # noqa: BLE001
            raise SessionError(f"连接失败：{e}")
        self.client = cli
        self.host, self.port, self.user = host, int(port), user
        self.label = f"{user}@{host}"

    @property
    def alive(self) -> bool:
        if not self.client:
            return False
        tr = self.client.get_transport()
        return bool(tr and tr.is_active())

    def close(self) -> None:
        try:
            if self._sftp:
                self._sftp.close()
        except Exception:  # noqa: BLE001
            pass
        self._sftp = None
        try:
            if self.client:
                self.client.close()
        except Exception:  # noqa: BLE001
            pass
        self.client = None

    # ---------------- 执行
    def run(self, cmd: str, timeout: int = 30, sudo: bool = False
            ) -> tuple[int, str, str]:
        if not self.alive:
            raise SessionError("尚未连接")
        if sudo:
            if not self.sudo_pw:
                raise SessionError("该操作需要 sudo 密码")
            full = f"sudo -S -p '' sh -c {shlex.quote(cmd)}"
        else:
            full = cmd
        stdin, stdout, stderr = self.client.exec_command(full, timeout=timeout)
        if sudo:
            stdin.write(self.sudo_pw + "\n")
            stdin.flush()
            try:
                stdin.channel.shutdown_write()
            except Exception:  # noqa: BLE001
                pass
        out = decode_bytes(stdout.read())
        err = decode_bytes(stderr.read())
        rc = stdout.channel.recv_exit_status()
        return rc, out, err

    def sudo_ok(self, pw: str) -> bool:
        self.sudo_pw = pw
        try:
            rc, _, _ = self.run("id -u", sudo=True, timeout=20)
            return rc == 0
        except Exception:  # noqa: BLE001
            return False

    # ---------------- 提权
    def run_admin(self, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
        if self.is_root or self.user == "root":
            return self.run_traced(cmd, timeout)
        if not self.sudo_pw:
            raise SessionError(
                "该操作需要 root 权限：请在「连接与设备」里填写 sudo 密码，"
                "或改用 root 账户登录")
        t0 = time.time()
        try:
            rc, out, err = self.run(cmd, timeout=timeout, sudo=True)
        except Exception as e:  # noqa: BLE001
            self._trace("sudo " + cmd, 1, "", str(e),
                        int((time.time() - t0) * 1000))
            raise
        self._trace("sudo " + cmd, rc, out, err, int((time.time() - t0) * 1000))
        return rc, out, err

    def can_admin(self) -> tuple[bool, str]:
        if self.is_root or self.user == "root":
            return True, ""
        if self.sudo_pw:
            return True, ""
        return False, "需要 sudo 密码或 root 登录"

    # ---------------- SFTP
    def sftp(self):
        if not self.alive:
            raise SessionError("尚未连接")
        if self._sftp is None:
            self._sftp = self.client.open_sftp()
        return self._sftp


# ------------------------------------------------------------------ Telnet
class TelnetSession(BaseSession):
    """交互式 Telnet。协议层实现在 telnet_session.py（第二批）。

    这里先留接口，等协议层就位后替换实现。
    """

    kind = "telnet"

    def __init__(self) -> None:
        super().__init__()
        self._impl = None

    def connect(self, host: str, port: int = 23, timeout: int = 15) -> None:
        from core.telnet_session import TelnetClient  # 延迟导入，避免循环
        self._impl = TelnetClient(host, port, timeout=timeout)
        self.host, self.port = host, int(port)
        self.user = ""
        self.label = f"telnet://{host}:{port}"

    @property
    def alive(self) -> bool:
        return bool(self._impl and self._impl.alive)

    def close(self) -> None:
        if self._impl:
            self._impl.close()
            self._impl = None

    def run(self, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        if not self._impl:
            raise SessionError("尚未连接")
        out = self._impl.command(cmd, timeout=timeout)
        return 0, out, ""


# ------------------------------------------------------------------ 工厂
@dataclass
class ConnSpec:
    proto: str = "ssh"          # ssh / telnet / local
    host: str = ""
    port: int = 22
    user: str = ""
    auth: str = "key"           # key / password
    key_path: str = ""
    password: str = ""
    key_passphrase: str = ""


def open_session(spec: ConnSpec) -> BaseSession:
    if spec.proto == "local":
        return LocalSession()
    if spec.proto == "telnet":
        s = TelnetSession()
        s.connect(spec.host, spec.port or 23)
        return s
    s = SSHSession()
    s.connect(spec.host, spec.port or 22, spec.user,
              password=spec.password if spec.auth == "password" else None,
              key_path=spec.key_path if spec.auth == "key" else None,
              key_passphrase=spec.key_passphrase or None)
    return s
