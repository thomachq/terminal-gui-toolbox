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


class NeedElevation(SessionError):
    """需要更高权限，但手上没有凭据。

    上层（主程序的 bg() 统一出口）会弹一个输入框要 sudo 密码，
    或者提示「以管理员身份重启」。所以这里只描述「缺什么」，不自己弹窗 ——
    run_admin() 可能在后台线程里跑，Tk 窗口只能在主线程开。

    kind:
      sudo   —— 类 Unix，需要 sudo 密码（本机或 SSH 都一样）
      uac    —— Windows 本机，需要整个进程以管理员身份运行
      enable —— 网络设备（Cisco / H3C），需要进特权模式
    """

    def __init__(self, message: str, kind: str = "sudo", target: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.target = target      # 显示给用户看的目标（user@host 或 本机）


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

    # ---------------- 对端到底是什么
    # 协议和它是「什么设备」是两件事：交换机 / 路由器既可能只开 Telnet，
    # 也可能只开 SSH；Linux 服务器同理（老设备只留 telnet 的也不少）。
    # 所以提权方式、能不能改配置，一律看这里的 host_kind，不看 proto。
    host_kind = ""              # unix / network / windows，detect 之后由 bind_device 填

    def bind_device(self, info) -> None:
        fam = (getattr(info, "os_family", "") or "").lower()
        if fam == "network":
            self.host_kind = "network"
        elif fam == "windows":
            self.host_kind = "windows"
        elif fam:
            self.host_kind = "unix"

    @property
    def is_network_host(self) -> bool:
        return self.host_kind == "network"

    @property
    def can_cli(self) -> bool:
        """能不能开一条「模式能保持」的持久 CLI（改配置必须靠它）。"""
        return False

    def cli(self):
        """持久 CLI 通道，没有就返回 None。"""
        return None


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
        self.host_kind = "windows" if self.is_windows else "unix"
        self.ps_native = self.is_windows
        self.platform_hint = {"win32": "windows", "darwin": "darwin"}.get(
            sys.platform, "linux")
        self.sudo_pw: str | None = None
        # 允许外部指定「这次当自己是不是管理员」：自测要模拟普通用户，
        # 界面上「以管理员身份运行」勾选后也可以先假定提权成功再重启。
        self._admin_override: bool | None = None
        self.is_root = self.is_admin

    @property
    def admin_kind(self) -> str:
        """这一端提权靠什么：Windows 是整个进程 UAC，类 Unix 是 sudo。"""
        return "uac" if self.is_windows else "sudo"

    @property
    def is_admin(self) -> bool:
        """当前进程是否已经具备管理员权限。"""
        if self._admin_override is not None:
            return self._admin_override
        if self.is_windows:
            try:
                import ctypes
                return bool(ctypes.windll.shell32.IsUserAnAdmin())
            except Exception:  # noqa: BLE001
                return False
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False

    @is_admin.setter
    def is_admin(self, value) -> None:
        self._admin_override = None if value is None else bool(value)

    @property
    def alive(self) -> bool:
        return True

    def _exec(self, args: list[str], timeout: int,
              stdin_text: str = "") -> tuple[int, str, str]:
        try:
            p = subprocess.run(
                args, capture_output=True, timeout=timeout,
                input=(stdin_text.encode("utf-8") if stdin_text else None),
                creationflags=(subprocess.CREATE_NO_WINDOW
                               if self.is_windows else 0))
            return p.returncode, decode_bytes(p.stdout), decode_bytes(p.stderr)
        except subprocess.TimeoutExpired:
            return 124, "", f"命令超时（{timeout}s）"
        except FileNotFoundError as e:
            return 127, "", str(e)
        except OSError as e:
            return 126, "", str(e)

    def run(self, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        args = windows_argv(cmd) if self.is_windows else ["/bin/sh", "-c", cmd]
        return self._exec(args, timeout)

    def sudo_ok(self, pw: str) -> bool:
        """在本机类 Unix 上验一次 sudo 密码（sudo -v 只刷时间戳，不执行命令）。"""
        if self.is_windows:
            return False
        self.sudo_pw = pw
        rc, _, _ = self._exec(["sudo", "-S", "-p", "", "-v"], 20, pw + "\n")
        return rc == 0

    def run_admin(self, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
        if self.is_windows:
            # Windows 没有 sudo：要么整个进程以管理员启动，要么命令自己失败。
            # 这里必须先判断，否则报错会伪装成「系统找不到路径」这种误导信息。
            if not self.is_admin:
                raise NeedElevation(
                    "这一步要管理员权限：请以管理员身份运行本程序，"
                    "或在「连接与设备」里勾选「以管理员身份运行」。",
                    kind="uac", target="本机")
            return self.run_traced(cmd, timeout)

        if self.is_admin:
            return self.run_traced(cmd, timeout)
        if not self.sudo_pw:
            raise NeedElevation(
                "这一步要 root 权限：请提供 sudo 密码。",
                kind="sudo", target=self.user or "本机")
        # 密码走 stdin，不进命令行 —— 进程列表里查不到，日志里也不会留痕。
        t0 = time.time()
        rc, out, err = self._exec(
            ["sudo", "-S", "-p", "", "-H", "sh", "-c", cmd],
            timeout, self.sudo_pw + "\n")
        self._trace("sudo " + cmd, rc, out, err,
                    int((time.time() - t0) * 1000))
        return rc, out, err

    def can_admin(self) -> tuple[bool, str]:
        if self.is_admin:
            return True, ""
        if self.is_windows:
            return False, "需要以管理员身份运行"
        if self.sudo_pw:
            return True, ""
        return False, "需要 sudo 密码"


# ------------------------------------------------------------------ SSH
class SSHSession(BaseSession):
    kind = "ssh"

    def __init__(self) -> None:
        super().__init__()
        self.client = None
        self.sudo_pw: str | None = None
        self._sftp = None
        self._cli = None            # 交换机 / 路由器用的持久 shell
        self.enabled = False        # 网络设备是否已进 enable
        self.vendor = ""            # detect 之后由 bind_device 补上，关分页要用

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
            if self._cli:
                self._cli.close()
        except Exception:  # noqa: BLE001
            pass
        self._cli = None
        self.enabled = False
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
        if not sudo and self.host_kind == "network":
            # 交换机 / 路由器上 exec 通道每次都是新会话：分页关不掉，
            # 长一点的 show 会卡在 --More--。统一走持久 shell。
            try:
                cli = self.cli()
            except Exception as e:  # noqa: BLE001
                raise SessionError(f"开不了设备 CLI：{e}") from e
            if cli is not None:
                return 0, cli.command(cmd, timeout=timeout), ""
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

    # ---------------- 提权（看设备类型，不看协议）
    # SSH 连服务器是 sudo；SSH 连交换机 / 路由器就是 enable + 配置视图。
    # 后者不能走 exec 通道（每条命令一个新通道，模式带不过去），
    # 所以这里按需开一条 invoke_shell 的持久会话，见 cli()。
    @property
    def can_cli(self) -> bool:
        return self.host_kind == "network" and self.alive

    def cli(self):
        """交换机 / 路由器的持久 CLI；服务器返回 None（用 exec 就够）。"""
        if self.host_kind != "network" or not self.alive:
            return None
        if self._cli is None:
            from core.ssh_shell import SSHShellClient
            self._cli = SSHShellClient(self.client)
            vendor = (self.vendor or "").lower()
            try:
                self._cli.disable_paging(vendor)
            except Exception:  # noqa: BLE001
                pass
        return self._cli

    @property
    def admin_kind(self) -> str:
        return "enable" if self.host_kind == "network" else "sudo"

    def run_admin(self, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
        if self.host_kind == "network":
            if not self.enabled:
                raise NeedElevation(
                    "这一步要进入特权模式：请提供 enable 密码。",
                    kind="enable", target=f"{self.user}@{self.host}")
            t0 = time.time()
            out = self.cli().command(cmd, timeout=timeout)
            self._trace(cmd, 0, out, "", int((time.time() - t0) * 1000))
            return 0, out, ""
        if self.is_root or self.user == "root":
            return self.run_traced(cmd, timeout)
        if not self.sudo_pw:
            raise NeedElevation(
                "这一步要 root 权限：请提供 sudo 密码。",
                kind="sudo", target=f"{self.user}@{self.host}" if self.host
                else self.user)
        t0 = time.time()
        try:
            rc, out, err = self.run(cmd, timeout=timeout, sudo=True)
        except Exception as e:  # noqa: BLE001
            self._trace("sudo " + cmd, 1, "", str(e),
                        int((time.time() - t0) * 1000))
            raise
        self._trace("sudo " + cmd, rc, out, err, int((time.time() - t0) * 1000))
        return rc, out, err

    def sudo_ok(self, pw: str) -> bool:
        """一个密码框，两种含义：交换机是 enable，服务器是 sudo。"""
        if self.host_kind == "network":
            cli = self.cli()
            if cli is None:
                return False
            ok = bool(cli.enable(pw))
            self.enabled = ok
            if ok:
                self.sudo_pw = pw
                self.is_root = True
            return ok
        self.sudo_pw = pw
        try:
            rc, _, _ = self.run("id -u", sudo=True, timeout=20)
            return rc == 0
        except Exception:  # noqa: BLE001
            return False

    def can_admin(self) -> tuple[bool, str]:
        if self.host_kind == "network":
            return (True, "") if self.enabled else (False, "需要 enable 密码")
        if self.is_root or self.user == "root":
            return True, ""
        if self.sudo_pw:
            return True, ""
        return False, "需要 sudo 密码或 root 登录"

    def bind_device(self, info) -> None:
        super().bind_device(info)
        self.vendor = (getattr(info, "vendor", "") or "").lower()

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
        self.sudo_pw: str | None = None
        self.enabled = False

    def connect(self, host: str, port: int = 23, timeout: int = 15,
                user: str = "", password: str = "") -> None:
        from core.telnet_session import TelnetClient  # 延迟导入，避免循环
        self._impl = TelnetClient(host, port, timeout=timeout)
        self._impl.login(user, password)
        self.host, self.port = host, int(port)
        self.user = user
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

    # ---------------- 提权（看设备类型，不看协议）
    # Telnet 连交换机是 enable，Telnet 连 Linux 就是 sudo —— 判断依据是
    # 探测出来的 host_kind。没探测到（空）时按网络设备处理：Telnet 的
    # 典型场景就是老交换机，真连到 Linux 的话 detect 会给它改回来。
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
        """一个密码框，两种含义：网络设备是 enable，Linux 是 sudo。"""
        if not self._impl:
            return False
        if self.host_kind == "unix":
            ok = bool(self._impl.sudo_check(pw))
            if ok:
                self.sudo_pw = pw
                self.is_root = True
            return ok
        ok = self._impl.enable(pw)
        self.enabled = bool(ok)
        if ok:
            self.sudo_pw = pw
            self.is_root = True
        return ok

    def run_admin(self, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
        if not self._impl:
            raise SessionError("尚未连接")
        if self.host_kind == "unix":
            # Linux over telnet：和 SSH 那条路一样走 sudo -S
            if self.is_root:
                return self.run_traced(cmd, timeout)
            if not self.sudo_pw:
                raise NeedElevation("这一步要 root 权限：请提供 sudo 密码。",
                                    kind="sudo", target=self.host)
            ok, out = self._impl.run_sudo(cmd, self.sudo_pw, timeout=timeout)
            self._trace(cmd, 0 if ok else 1, out, "", 0)
            return (0 if ok else 1), out, ""
        if not self.enabled:
            raise NeedElevation(
                "这一步要进入特权模式：请提供 enable 密码。",
                kind="enable", target=self.host)
        return self.run_traced(cmd, timeout)


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
    # 提权：给了密码就连上后自动提权（类 Unix 上是 sudo -i，网络设备是 enable）
    sudo_password: str = ""
    elevate: bool = False


def open_session(spec: ConnSpec) -> BaseSession:
    """连上就好，**不提权**。

    远端提权必须等探测完再决定：同一个密码框，交换机上是 enable、
    服务器上是 sudo。以前在 connect 之后立刻提权，等于先假定对方是
    Linux —— 连交换机时那次 sudo 必然失败，还会把协议当成设备类型。
    调用方拿到会话后：detect() → bind_device() → 需要的话再 sudo_ok()。
    本机没有这个歧义（平台已知），所以照旧就地做完。
    """
    if spec.proto == "local":
        s = LocalSession()
        if spec.elevate and not s.is_windows and spec.sudo_password:
            s.sudo_ok(spec.sudo_password)
        return s
    if spec.proto == "telnet":
        s = TelnetSession()
        s.connect(spec.host, spec.port or 23,
                  user=spec.user, password=spec.password)
        return s
    s = SSHSession()
    s.connect(spec.host, spec.port or 22, spec.user,
              password=spec.password if spec.auth == "password" else None,
              key_path=spec.key_path if spec.auth == "key" else None,
              key_passphrase=spec.key_passphrase or None)
    return s
