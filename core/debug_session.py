# -*- coding: utf-8 -*-
"""ADB / HDC 会话：把「调试桥上的 shell」包装成和 SSH 一样的会话。

两种调试桥的共同点：
  · 本机跑一个客户端（adb.exe / hdc.exe），它自己带一个常驻 server；
  · 设备可能是 USB 直连，也可能是网络上的（`adb connect` / `hdc tconn`）；
  · 真正执行命令是客户端把一行字符串交给设备端的 shell。

两个必须处理的细节：
  1. **退出码**：`adb shell false` 的进程退出码是 0（flase 的 rc 在内层），
     页面会因此把失败当成功。这里统一包一层 `; echo __TTB_RC__$?` 取内层值。
  2. **多设备**：不指定序列号时绝不「随便挑一台」。改错设备的代价远大于报错。
"""
from __future__ import annotations

import time

from core import devtools
from core.session import BaseSession, SessionError

RC_MARK = "__TTB_RC__"


class _DebugSession(BaseSession):
    """ADB / HDC 的公共部分。子类只差「怎么拼一条 shell 命令」。"""

    bus = "adb"
    port_default = 5555

    def __init__(self) -> None:
        super().__init__()
        self.tool_src = devtools.SRC_AUTO
        self.serial = ""
        self.remote = False
        self.host_kind = "unix"
        self.platform_hint = ""       # 让 detect() 正常跑 uname
        self.probe_timeout = 8
        self._alive_at = 0.0
        self._alive_val = False

    # ------------------------------------------------------------ 连接
    def connect(self, device_id: str = "", host: str = "", port=None,
                tool_src: str = devtools.SRC_AUTO) -> None:
        self.close()
        self.tool_src = devtools.norm_src(tool_src)
        host = str(host or "").strip()
        info = devtools.resolve_tool(self.bus, self.tool_src)
        if not info["exists"]:
            raise SessionError(info["error"])
        if host:
            target = host if ":" in host or not port else f"{host}:{int(port)}"
            ok, text = devtools.connect_device(self.bus, target, self.tool_src)
            if not ok:
                raise SessionError(
                    f"连不上 {target}：{text or '没有输出'}\n"
                    f"（远程调试要先在设备上打开网络调试 / 无线调试，"
                    f"并确认端口没被防火墙挡住）")
            self.remote = True
        rows = devtools.list_devices(self.bus, self.tool_src)
        serial, why = devtools.pick_serial(self.bus, rows, device_id)
        if not serial:
            raise SessionError(why)
        self.serial = serial
        self.host = host or serial
        self.port = int(port or self.port_default) if host else 0
        self.user = "shell"
        self.label = f"{self.bus}://{serial}"
        self._alive_at = time.time()
        self._alive_val = True

    @property
    def alive(self) -> bool:
        if not self.serial:
            return False
        now = time.time()
        # 3 秒缓存：alive 会被页面 / 终端 / 采样器高频问，每次都 fork 一次
        # `adb devices` 太贵（实测一次 40–80ms，采样器每 3 秒问一次就够呛）。
        if now - self._alive_at < 3.0:
            return self._alive_val
        self._alive_at = now
        try:
            rows = devtools.list_devices(self.bus, self.tool_src)
        except Exception:  # noqa: BLE001
            self._alive_val = False
            return False
        want = (self.serial or "").lower()
        self._alive_val = any(
            str(r.get("serial") or "").lower() == want
            and str(r.get("state") or "").lower() in
            ("device", "connected", "ready")
            for r in rows)
        return self._alive_val

    def close(self) -> None:
        # 只关「本会话」：设备端的 adbd / hdcd 不动，别人的连接也不动。
        # 用户要的是「断开这条」，不是「把所有调试都踢掉」。
        self.serial = ""
        self._alive_val = False
        self._alive_at = 0.0

    # ------------------------------------------------------------ 执行
    def _shell_args(self, cmd: str) -> list[str]:
        raise NotImplementedError

    def _wrap(self, cmd: str) -> str:
        """给命令补一个内层退出码。已有末尾换行的先去掉，别产生空行。"""
        c = str(cmd or "").rstrip("\r\n")
        return f"{c}; echo {RC_MARK}$?"

    def run(self, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        if not self.serial:
            raise SessionError(f"{self.bus.upper()} 没有连接")
        wrapped = self._wrap(cmd)
        try:
            rc, out, err = devtools.run_tool(
                self.bus, self._shell_args(wrapped), self.tool_src,
                timeout=max(5, int(timeout)))
        except devtools.ToolMissing as e:
            raise SessionError(str(e)) from e
        return _split_rc(rc, out, err)

    # ------------------------------------------------------------ 提权
    # 调试桥上的 shell 通常是设备给的受限 shell（uid 2000）或 root（模拟器）。
    # 没有 sudo 这回事：能不能改看设备本身，不伪装成「已提权」。
    def can_admin(self) -> tuple[bool, str]:
        return True, ""

    def bind_device(self, info) -> None:
        super().bind_device(info)
        # Android / HarmonyOS 都是类 Unix，页面按 unix 走；
        # 显示名在 probe_info() 里覆盖成「Android 14」/「HarmonyOS 5.0」。
        self.host_kind = "unix"
        self.platform_hint = ""

    # ------------------------------------------------------------ 描述
    def probe_info(self) -> dict:
        """设备型号 / 系统版本。取不到就返回空值，不报错。"""
        return {"model": "", "version": ""}


class AdbSession(_DebugSession):
    kind = "adb"
    bus = "adb"
    port_default = 5555

    def _shell_args(self, cmd: str) -> list[str]:
        return ["-s", self.serial, "shell", cmd]

    def probe_info(self) -> dict:
        out = {"model": "", "version": ""}
        try:
            _rc, o, _e = self.run("getprop ro.product.model", timeout=12)
            out["model"] = (o or "").strip().splitlines()[-1].strip() if o.strip() else ""
        except Exception:  # noqa: BLE001
            pass
        try:
            _rc, o, _e = self.run("getprop ro.build.version.release", timeout=12)
            v = (o or "").strip().splitlines()[-1].strip() if o.strip() else ""
            if v:
                out["version"] = f"Android {v}"
        except Exception:  # noqa: BLE001
            pass
        return out


class HdcSession(_DebugSession):
    kind = "hdc"
    bus = "hdc"
    port_default = 8710

    def _shell_args(self, cmd: str) -> list[str]:
        return ["-t", self.serial, "shell", cmd]

    def probe_info(self) -> dict:
        out = {"model": "", "version": ""}
        for key, target in (("model", "const.product.model"),
                            ("version", "const.product.software.version")):
            try:
                _rc, o, _e = self.run(f"param get {target}", timeout=12)
                v = (o or "").strip().splitlines()[-1].strip() if o.strip() else ""
                if v and "fail" not in v.lower():
                    out[key] = (f"HarmonyOS {v}" if key == "version" else v)
            except Exception:  # noqa: BLE001
                pass
        return out


def _split_rc(rc: int, out: str, err: str) -> tuple[int, str, str]:
    """从输出里抠出内层退出码并把它那一行删掉。

    找不到标记（老版本 adb 不支持 `; echo`、或命令自己把 shell 弄死了）
    就退回进程 rc —— 至少不会比现状更差。
    """
    text = out or ""
    inner = None
    lines = text.splitlines(True)
    keep: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith(RC_MARK):
            try:
                inner = int(s[len(RC_MARK):].strip())
            except ValueError:
                inner = None
            continue
        keep.append(line)
    if inner is None:
        return rc, text, err or ""
    return inner, "".join(keep).rstrip("\r\n"), err or ""
