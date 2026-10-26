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
import re
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


def load_private_key(path: str, passphrase: str | None = None):
    """读私钥，返回 paramiko.PKey。**口令一律转成 bytes 再交给 paramiko。**

    为什么不用 `SSHClient.connect(key_filename=...)`：paramiko 5 内部走
    `PKey.from_path`，遇到传统 PEM（`-----BEGIN RSA PRIVATE KEY-----`）
    且口令是 str 时会抛 `encountered RSA key, expected OPENSSH key`。
    这条报错完全不提"加密/口令"，看着像文件格式不对，实际把口令转成
    bytes 就能正常解密 —— 群晖那把 `chromas_openssh_ppk` 就是这么被卡住的，
    GUI 里看起来像"连不上群晖"，真因是口令类型。

    错误一律翻成人话，别再让 cryptography 的术语漏到界面上。
    """
    from pathlib import Path

    import paramiko

    p = Path(str(path or "").strip())
    if not str(path or "").strip():
        raise SessionError("没指定私钥文件")
    if not p.exists():
        raise SessionError(f"私钥文件不存在：{p}")
    if not p.is_file():
        raise SessionError(f"私钥路径不是普通文件：{p}")
    try:
        head = p.open("rb").read(120)
    except OSError as e:
        raise SessionError(f"私钥读不出来：{e}") from e
    if b"PuTTY-User-Key-File" in head:
        raise SessionError("这是 PuTTY 的 .ppk 私钥，请先用 PuTTYgen 另存为 "
                           "OpenSSH 或 PEM 格式再选")

    pw = passphrase.encode("utf-8") if isinstance(passphrase, str) else passphrase
    pw = pw or None                      # 空口令 == 没给
    try:
        return paramiko.PKey.from_path(str(p), pw)
    except FileNotFoundError as e:
        raise SessionError(f"私钥文件不存在：{p}") from e
    except Exception as e:  # noqa: BLE001
        # paramiko 只吃 OpenSSH 容器与传统 PEM 两路；PKCS#8
        # （`BEGIN ENCRYPTED PRIVATE KEY`，openssl 默认就是它）它读不了，
        # 但 cryptography 可以 —— 先自己解一遍，能解就包成 paramiko 的 key。
        key, why = _load_with_crypto(p, pw)
        if key is not None:
            return key
        if why:
            raise SessionError(why) from e
        msg = str(e)
        if "encrypt" in msg.lower():
            # paramiko 在"加密但没给口令"时抛的是 TypeError，不是专用异常
            raise SessionError("私钥已加密：请在档案里填「私钥密码」") from e
        if "Incorrect password" in msg or "Bad password" in msg:
            raise SessionError("私钥密码不对（私钥文件本身可能也坏了）") from e
        if "OPENSSH key" in msg:
            raise SessionError("私钥格式 paramiko 读不了，请转成 OpenSSH 格式") from e
        raise SessionError(f"私钥读不出来：{type(e).__name__}: {msg}") from e


def _load_with_crypto(path, password: bytes | None):
    """兜底：用 cryptography 解 PKCS#8 / PKCS#1 / OpenSSH，再包成 paramiko key。

    返回 (key, 人话错误)。key 为 None 且错误为 None 表示"说不清，交给上层"。
    """
    import paramiko
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
    from cryptography.hazmat.primitives.serialization import (
        load_pem_private_key, load_ssh_private_key)

    try:
        data = path.read_bytes()
    except OSError:
        return None, None
    key, why = None, None
    for fn in (load_ssh_private_key, load_pem_private_key):
        try:
            key = fn(data, password)
            break
        except TypeError:                # 加密但没给口令
            return None, "私钥已加密：请在档案里填「私钥密码」"
        except ValueError as e:
            if "Incorrect password" in str(e):
                return None, "私钥密码不对（私钥文件本身可能也坏了）"
            continue
        except Exception:  # noqa: BLE001
            continue
    if key is None:
        return None, why
    # 解出来的对象怎么变回 paramiko key：不要 `Cls(key=obj)` ——
    # paramiko 5 的 Ed25519Key 根本没这个参数（`__init__() got an unexpected
    # keyword argument 'key'`）。统一重序列化成内存里的 OpenSSH 容器再喂回去，
    # RSA / ECDSA / Ed25519 三种都走同一条路，也不会在磁盘上留明文。
    import io

    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat)

    try:
        pem = key.private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption())
    except Exception:  # noqa: BLE001
        return None, None
    for attr in ("Ed25519Key", "ECDSAKey", "RSAKey"):
        cls = getattr(paramiko, attr, None)
        if cls is None:
            continue
        try:
            return cls.from_private_key(io.StringIO(pem.decode("ascii"))), None
        except Exception:  # noqa: BLE001
            continue
    return None, None


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
        # 提权是否**已经生效**（不是「有密码」，是「验过并且真的进去了」）。
        # 终端靠它决定命令走不提权通道还是 root 通道。
        self.elevated = False
        # 提权是怎么生效的：sudo-i（进了 root shell）/ percmd（每条命令 sudo）
        # / enable（交换机特权模式）/ root（本来就是 root）。界面文案要用它，
        # 否则「已提权」三个字看不出到底走哪条路。
        self.elev_method = ""
        # 提权失败的原因原文（给日志用）。空 = 没失败过。
        self.elev_msg = ""

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
        # 走不走提权助手：Windows 没有 sudo，普通权限的进程要跑管理员命令，
        # 只能另起一个管理员进程代跑（见 core/elev.py）。True = 命令交给它。
        self.use_helper = False
        # Windows 本机用哪种外壳："" = 自动（认命令前缀）/ "cmd" / "powershell"
        self.shell = ""
        self.is_root = self.is_admin

    def set_shell(self, shell: str) -> None:
        """指定本机 Windows 的执行外壳。cmd 下不能再按 PowerShell 拼引号。"""
        sh = str(shell or "").strip().lower()
        self.shell = sh if sh in ("cmd", "powershell") else ""
        if self.is_windows:
            self.ps_native = self.shell != "cmd"

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
        if self.is_windows and self.use_helper:
            h = self._helper()
            if h is None:
                # 通道没了（助手被杀 / 崩了）：立刻降级并说明，
                # 否则界面还挂着「已提权」，用户以为命令是管理员跑的。
                self.use_helper = False
                self.elevated = False
                self.elev_method = ""
                self.elev_msg = "提权通道已断开（助手不在了），已退回普通权限"
            else:
                # 勾了「以管理员身份运行」之后，本机所有命令都从这个通道走 ——
                # 终端里手敲的、页面按需跑的，全都真的带管理员权限，而不是
                # 只有 run_admin() 那几条。进程自身权限没变，界面不用重开。
                try:
                    return h.exec(cmd, timeout, self.shell)
                except Exception as e:  # noqa: BLE001
                    self.elev_msg = f"提权通道断开：{e}"
                    self.use_helper = False
                    self.elevated = False
                    self.elev_method = ""
        return self._exec(self.argv_for(cmd), timeout)

    # ---------------- 提权助手（Windows 一次性 UAC，不重启应用）
    def _helper(self):
        """能用的提权助手；没有就 None（调用方据此决定直连还是报错）。"""
        if not (self.is_windows and self.use_helper):
            return None
        from core import elev
        return elev.current()

    def set_helper(self, on: bool = True) -> None:
        """接上 / 断开提权通道。接上即视为「已提权」。"""
        self.use_helper = bool(on)
        if on:
            self.elevated = True
            self.elev_method = "uac"
            self.elev_msg = ""
        else:
            self.elevated = False
            self.elev_method = ""

    def elevate_root(self, pw: str = "") -> bool:
        """Windows 本机：点一次 UAC 拉起提权助手（**不重启程序**）。

        `pw` 在 Windows 上用不到（没有 sudo），保留参数是为了与 SSH 同签名。
        """
        if not self.is_windows:
            return self.sudo_ok(pw)
        self.elev_msg = ""
        if self.is_admin:
            # 进程本来就是管理员，不需要助手
            self.elevated = True
            self.elev_method = "uac"
            return True
        from core import elev
        h = elev.helper()
        if h.alive or h.start():
            self.set_helper(True)
            return True
        self.elev_msg = h.last_error or "UAC 授权没通过"
        self.elevated = False
        return False

    def verify_root(self) -> tuple[bool, str]:
        """连上之后实测一次「命令是不是真的带管理员权限」。

        和 SSH 那条同一条道理：`elevate_root()` 返回 True 只代表 UAC 点了
        「是」，命令实际跑在哪条通道得验 —— 用户看到提示符变成 `#` 了，
        这里就不能再猜。
        """
        if not self.is_windows:
            return bool(self.elevated), (self.elev_method or "本机")
        ps = ("powershell -NoProfile -NonInteractive -Command \""
              "([Security.Principal.WindowsPrincipal]"
              "[Security.Principal.WindowsIdentity]::GetCurrent())"
              ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)\"")
        try:
            rc, out, err = self.run(ps, timeout=30)
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        txt = (out or "").strip().lower()
        if rc == 0 and txt.startswith("true"):
            self.elevated = True
            self.elev_method = self.elev_method or "uac"
            return True, "True"
        if not txt:
            # 回显读不到（编码/被杀软吞了）：按通道在不在判，别把一次怪回显
            # 当成「没提权」，那会把用户已经点过的 UAC 白费掉。
            return bool(self.elevated), "读不到 IsInRole 回显"
        self.elevated = False
        return False, f"IsInRole = {txt[:40]}"

    def argv_for(self, cmd: str) -> list[str]:
        """把一条命令变成 argv。抽出来是为了能单独测（不必真起进程）。"""
        if not self.is_windows:
            return ["/bin/sh", "-c", cmd]
        if self.shell == "cmd":
            return ["cmd", "/c", cmd]
        if self.shell == "powershell":
            return ["powershell", "-NoProfile", "-NonInteractive",
                    "-Command", cmd]
        return windows_argv(cmd)     # 自动：看命令自己有没有 powershell 前缀

    def sudo_ok(self, pw: str) -> bool:
        """在本机类 Unix 上验一次 sudo 密码（sudo -v 只刷时间戳，不执行命令）。

        本机没有「持久 shell」可进（每个命令都是独立的子进程），所以提权
        只体现在 `elevated` 上：终端与页面操作改走 `run_admin()`，
        由它在每条命令前面套 `sudo -S`。
        """
        if self.is_windows:
            return False
        self.sudo_pw = pw
        rc, _, _ = self._exec(["sudo", "-S", "-p", "", "-v"], 20, pw + "\n")
        self.elevated = rc == 0
        self.elev_method = "percmd" if rc == 0 else ""
        self.elev_msg = "" if rc == 0 else "本机 sudo 密码没通过"
        return rc == 0

    def run_admin(self, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
        if self.is_windows:
            # Windows 没有 sudo：要么进程本身是管理员，要么命令交给提权助手。
            # 这里必须先判断，否则报错会伪装成「系统找不到路径」这种误导信息。
            if self._helper() is not None:
                return self.run_traced(cmd, timeout)
            if not self.is_admin:
                raise NeedElevation(
                    "这一步要管理员权限：勾「以管理员身份运行」点一次 UAC 就行，"
                    "程序不用重启。",
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
        if self.is_admin or self._helper() is not None:
            return True, ""
        if self.is_windows:
            return False, "点「以管理员身份运行」弹一次 UAC 即可，不用重启程序"
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
        self._root_shell = None     # sudo -i 之后的 root 持久 shell（见 elevate_root）
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
        # 私钥自己读、自己给：口令类型 / 加密 / 格式 三类错误都能翻成人话，
        # 交给 connect(key_filename=...) 只会得到一句 misleading 的格式报错。
        pkey = load_private_key(key_path, key_passphrase) if key_path else None
        # pkey 已解好就不要再传 passphrase：paramiko 只在按文件名读钥匙时才用它，
        # 同时给两个反而是自找麻烦（顺序一变就得重调）。
        extra: dict = {}
        if pkey is None and key_passphrase:
            extra["passphrase"] = key_passphrase.encode("utf-8")
        try:
            cli.connect(
                hostname=host, port=int(port), username=user,
                password=password or None,
                pkey=pkey,
                timeout=timeout,
                # 指定私钥就只认这把，避免静默落到 ssh-agent 的其它钥匙上
                allow_agent=(not key_path),
                look_for_keys=(not key_path and not password),
                **extra,
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
            if self._root_shell:
                self._root_shell.close()
        except Exception:  # noqa: BLE001
            pass
        self._root_shell = None
        self.elevated = False
        self.elev_method = ""
        self.elev_msg = ""
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
                self.elev_msg = "开不了设备 CLI（连不上或不是网络设备）"
                return False
            ok = bool(cli.enable(pw))
            self.enabled = ok
            if ok:
                self.sudo_pw = pw
                self.is_root = True
                self.elevated = True
                self.elev_method = "enable"
                self.elev_msg = ""
            else:
                self.elev_msg = getattr(cli, "last_error", "") or "enable 没成功"
            return ok
        self.sudo_pw = pw
        try:
            rc, _, _ = self.run("id -u", sudo=True, timeout=20)
            self.elevated = rc == 0
            if rc == 0:
                self.elev_method = "percmd"
                self.elev_msg = ""
            else:
                self.elev_msg = "sudo 逐条执行时返回非 0"
            return rc == 0
        except Exception as e:  # noqa: BLE001
            self.elev_msg = str(e) or "sudo 逐条执行失败"
            return False

    def elevate_root(self, pw: str) -> bool:
        """「连接后自动提权」的真正落地：**进去**，而不是每条命令再 sudo 一次。

        · 交换机 / 路由器 -> `enable`（进特权视图）
        · 类 Unix 服务器 -> 开一条持久 shell 执行 `sudo -i`
        · 登录用户本来就是 root -> 什么都不用做

        为什么必须开持久 shell：`exec_command` 每条都是新通道，`sudo -i`
        进去就退出来了，状态带不过去。而终端里手敲的命令走的正是这条 shell，
        所以进去之后敲 `id` 就是 0，不是每条命令重新过一遍密码。

        `sudo -i` 进不去时**不直接认输**：有些机器（群晖 DSM、部分加固过的
        发行版）把 `sudo -i` 关掉但放行 `sudo <cmd>`。这种情况退回「逐条 sudo」
        —— 终端里敲的命令照样是 root 跑的，只是每条多一次 sudo，慢一点。
        """
        self.elev_msg = ""
        self.elev_method = ""
        if self.host_kind == "network":
            return self.sudo_ok(pw)
        if self.is_root or self.user == "root":
            self.elevated = True
            self.elev_method = "root"
            return True
        if not pw:
            self.elev_msg = "没有可用的 sudo 密码"
            return False
        if self._root_shell is not None and self._root_shell.alive():
            self.elevated = True
            self.elev_method = "sudo-i"
            return True
        why = ""
        try:
            from core.ssh_shell import SSHShellClient
            sh = SSHShellClient(self.client)
        except Exception as e:  # noqa: BLE001
            self.elev_msg = f"开不了交互式 shell：{e}"
            return False
        try:
            ok = bool(sh.sudo_i(pw))
            why = getattr(sh, "last_error", "") or ""
        except Exception as e:  # noqa: BLE001
            ok, why = False, str(e)
        if ok:
            self._root_shell = sh
            self.sudo_pw = pw
            self.elevated = True
            self.elev_method = "sudo-i"
            # 刻意**不动 is_root**：它表示「登录用户本身就是 root」。
            # 这里只是多了一条 root shell，exec 通道还是原用户，
            # 所以 run_admin() 仍然要靠 sudo 而不是直接跑。
            return True
        try:
            sh.close()
        except Exception:  # noqa: BLE001
            pass
        # 退一步：逐条 sudo 还通不通？
        if self.sudo_ok(pw):
            self.elev_method = "percmd"
            self.elev_msg = ""
            return True
        self.elev_msg = why or self.elev_msg or "sudo -i 与逐条 sudo 都没通过"
        return False

    def verify_root(self) -> tuple[bool, str]:
        """连上之后真的跑一次 `id -u` 落锤。

        为什么还要这一步：`elevate_root()` 返回 True 只代表「提权那一步的
        回显看着对」。终端里真正执行的是另一条通道（root shell / run_admin），
        它通不通得实测 —— 用户看到的提示符已经是 `#` 了，这里就不能再猜。
        返回 (是否 root, 说明)。说明是 uid 或失败原因，直接进日志。
        """
        if self.host_kind == "network":
            ok = bool(self.enabled)
            return ok, ("特权模式已进" if ok
                        else (self.elev_msg or "没进特权模式"))
        if self.kind == "local":
            return bool(self.elevated), (self.elev_method or "本机")
        try:
            rc, out, err = term_exec(self, "id -u", timeout=20)
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        text = (out or "") + "\n" + (err or "")
        got = re.findall(r"(?m)^\s*(\d+)\s*$", text)
        if rc == 0 and got and got[-1] == "0":
            return True, "0"
        return False, (f"id -u = {got[-1]}" if got
                       else (text.strip()[-160:] or "读不到 id -u"))

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
        self.elev_msg = ""
        self.elev_method = ""
        if not self._impl:
            self.elev_msg = "Telnet 连接已断开"
            return False
        if self.host_kind == "unix":
            ok = bool(self._impl.sudo_check(pw))
            if ok:
                self.sudo_pw = pw
                self.is_root = True
                self.elevated = True
                self.elev_method = "percmd"
            else:
                self.elev_msg = getattr(self._impl, "last_error", "") or \
                    "sudo 密码没通过"
            return ok
        ok = self._impl.enable(pw)
        self.enabled = bool(ok)
        if ok:
            self.sudo_pw = pw
            self.is_root = True
            self.elevated = True
            self.elev_method = "enable"
        else:
            self.elev_msg = getattr(self._impl, "last_error", "") or "enable 没成功"
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
    # 本机 Windows 的执行外壳："" 自动 / "cmd" / "powershell"
    shell: str = ""


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
        if spec.shell:
            s.set_shell(spec.shell)
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


def term_exec(s: BaseSession, cmd: str,
              timeout: int = 180) -> tuple[int, str, str]:
    """终端里的一条命令怎么跑 —— 提权生效了就走提权通道。

    这是「勾了自动提权、终端里还是普通用户」的修复点。以前终端固定调
    `run_traced()`，那是**不带任何提权**的通道；只有页面上那些显式调
    `run_admin()` 的按钮才带 sudo。于是提示写着「之后的命令都带 root」，
    用户在终端里敲 `id` 看到的还是自己。

    三档优先级：
      1. 有 `sudo -i` 进出来的 root 持久 shell -> 直接发给它
      2. 提权已生效但没有持久 shell（本机类 Unix / 交换机 enable）
         -> 走 `run_admin()`，由它决定是套 sudo 还是发给设备 CLI
      3. 都没提权 -> 普通执行，与以前完全一致
    """
    sh = getattr(s, "_root_shell", None)
    if sh is not None:
        try:
            if sh.alive():
                return 0, sh.command(cmd, timeout=timeout), ""
        except Exception as e:  # noqa: BLE001
            return 1, "", str(e)
    if getattr(s, "elevated", False):
        try:
            return s.run_admin(cmd, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return 1, "", str(e)
    return s.run_traced(cmd, timeout=timeout)
