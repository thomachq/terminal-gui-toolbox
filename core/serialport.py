# -*- coding: utf-8 -*-
"""串口（COM）底层：只做「连上 console 口、收发字节」这一件事。

为什么不用 pyserial：
  · 打包环境（`vpnconsole` 解释器）里没有它，为一个功能加一条新依赖，
    `toolbox.spec` / 依赖清单 / 离线构建全都要跟着改；
  · 我们只需要「打开 / 读写 / 关闭」，Windows 上 kernel32 的五个函数就够，
    POSIX 上 `termios` 是标准库 —— 零依赖，打包零风险。

对外只暴露 `SerialLink`，所有平台差异都关在本文件里。打不开串口一律抛
`SerialError`（人话），绝不让 ctypes 的 `WinError 5` 直接冒到界面上。

Windows 上几个真会踩的点，写在这里免得下次重踩：
  · 端口名必须写成 `\\\\.\\COM10` —— COM10 及以上不这么写一定打不开；
  · `CreateFileW` 的 `restype` 必须是 `c_void_p`，否则 64 位句柄被截断，
    后面 SetCommState 一律返回「句柄无效」；
  · 读取超时靠 `COMMTIMEOUTS`：`ReadIntervalTimeout=50` + `ReadTotalTimeoutConstant`
    按本次调用动态设，`ReadFile` 才会「有就返回、没有就等一小会儿」，
    否则要么一直阻塞，要么读不到正在输出的数据；
  · `PurgeComm` 要清 RX/TX 两侧，不然上一轮残留的提示符会被当成这次的回显。
"""
from __future__ import annotations

import os
import sys
import time

WIN = sys.platform == "win32"

# 常见波特率。超出这个表也允许（驱动支持就行），但界面上只列这些。
BAUDS = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200,
         230400, 460800, 921600, 1500000)
DATABITS = (5, 6, 7, 8)
PARITIES = ("none", "even", "odd", "mark", "space")
STOPBITS = ("1", "1.5", "2")
FLOWS = ("none", "rtscts", "xonxoff")

#: 常用默认：console 口基本都是 115200 8N1 无流控
DEFAULTS = {"baud": 115200, "databits": 8, "parity": "none",
            "stopbits": "1", "flow": "none"}


class SerialError(Exception):
    pass


# ------------------------------------------------------------------ 参数归一
def norm_port(name: str) -> str:
    """`com3` / `\\\\.\\COM3` → `COM3`；POSIX 原样（只去空白）。"""
    s = str(name or "").strip().strip('"').strip("'")
    if not s:
        return ""
    if WIN:
        s = s.replace("\\\\.\\", "")
        if s.lower().startswith("com") and s[3:].isdigit():
            return "COM" + s[3:]
        return s
    return s


def norm_params(baud=None, databits=None, parity=None, stopbits=None,
                flow=None) -> dict:
    """把界面上五花八门的写法归一，非法值抛 SerialError（人话）。

    界面上是下拉框，但档案是手改得到的（data/profiles.dat 虽然加密，
    用户也可能从旧版导入），所以这里必须自己校验一次。
    """
    try:
        b = int(str(baud if baud not in (None, "") else DEFAULTS["baud"]))
    except (TypeError, ValueError):
        raise SerialError(f"波特率不是数字：{baud!r}")
    if not (50 <= b <= 4000000):
        raise SerialError(f"波特率超出范围（50–4000000）：{b}")
    try:
        db = int(str(databits if databits not in (None, "") else DEFAULTS["databits"]))
    except (TypeError, ValueError):
        raise SerialError(f"数据位不是数字：{databits!r}")
    if db not in DATABITS:
        raise SerialError(f"数据位只能是 5/6/7/8：{db}")
    p = str(parity or DEFAULTS["parity"]).strip().lower()
    p = {"n": "none", "e": "even", "o": "odd", "m": "mark", "s": "space"}.get(p, p)
    if p not in PARITIES:
        raise SerialError(f"校验位只能是 {'/'.join(PARITIES)}：{parity!r}")
    sb = str(stopbits if stopbits not in (None, "") else DEFAULTS["stopbits"]).strip()
    if sb not in STOPBITS:
        raise SerialError(f"停止位只能是 1/1.5/2：{stopbits!r}")
    fl = str(flow or DEFAULTS["flow"]).strip().lower()
    fl = {"": "none", "none": "none", "rts": "rtscts", "cts": "rtscts",
          "rts/cts": "rtscts", "xon": "xonxoff", "xoff": "xonxoff"}.get(fl, fl)
    if fl not in FLOWS:
        raise SerialError(f"流控只能是 {'/'.join(FLOWS)}：{flow!r}")
    return {"baud": b, "databits": db, "parity": p,
            "stopbits": sb, "flow": fl}


def params_text(p: dict) -> str:
    """`115200 8N1` 这种给人看的一行。"""
    p = p or {}
    par = {"none": "N", "even": "E", "odd": "O", "mark": "M",
           "space": "S"}.get(str(p.get("parity") or "none"), "N")
    return f"{p.get('baud') or 115200} {p.get('databits') or 8}{par}" \
           f"{p.get('stopbits') or 1}"


# ------------------------------------------------------------------ 端口枚举
def list_ports() -> list[str]:
    """本机现在有哪些串口。读注册表，不打开端口、不影响正在用的设备。"""
    if WIN:
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DEVICEMAP\SERIALCOMM")
        except OSError:
            return []
        out: list[str] = []
        try:
            i = 0
            while True:
                try:
                    _name, value, _kind = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                p = norm_port(value)
                if p and p not in out:
                    out.append(p)
        finally:
            try:
                key.Close()
            except OSError:
                pass
        return sorted(out, key=_port_sort_key)
    import glob
    found = []
    for pat in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyS*",
                "/dev/serial/by-id/*"):
        found.extend(glob.glob(pat))
    return sorted(set(found))


def _port_sort_key(name: str):
    digits = "".join(ch for ch in name if ch.isdigit())
    return (0, int(digits)) if digits else (1, name)


def port_labels() -> dict[str, str]:
    """`{COM3: "USB-SERIAL CH340 (COM3)"}`。取不到就返回空 dict。

    **当前没有任何调用者，别随手又接回界面上**：v1.0.35 把串口做成下拉时
    试过用它在下拉里显示芯片型号，实测这台机器上 PowerShell 起不来，
    8 秒超时返回空 dict —— 接进「切到 COM 就刷新」的路径就是把界面冻住。
    下拉里只要端口名（COM3）就够用了。

    真要复活它，先在后台线程里跑，别放在 UI 线程上。
    """
    if not WIN:
        return {}
    try:
        from core.session import decode_bytes
        import subprocess
        ps = ("Get-CimInstance Win32_SerialPort | "
              "ForEach-Object { \"$($_.DeviceID)|$($_.Caption)\" }")
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out: dict[str, str] = {}
        for line in decode_bytes(p.stdout).splitlines():
            if "|" not in line:
                continue
            dev, cap = line.split("|", 1)
            dev = norm_port(dev)
            if dev:
                out[dev] = cap.strip() or dev
        return out
    except Exception:  # noqa: BLE001  WMI 不可用不是错误，只是没有友好名
        return {}


# ------------------------------------------------------------------ Windows
if WIN:  # pragma: no cover - 平台分支，逻辑靠假链路测
    import ctypes
    import ctypes.wintypes as wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _INVALID = ctypes.c_void_p(-1).value

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    PURGE_ALL = 0x0001 | 0x0002 | 0x0004 | 0x0008
    MAXDWORD = 0xFFFFFFFF

    class _DCB(ctypes.Structure):
        _fields_ = [
            ("DCBlength", wt.DWORD), ("BaudRate", wt.DWORD),
            ("fBinary", wt.DWORD, 1), ("fParity", wt.DWORD, 1),
            ("fOutxCtsFlow", wt.DWORD, 1), ("fOutxDsrFlow", wt.DWORD, 1),
            ("fDtrControl", wt.DWORD, 2), ("fDsrSensitivity", wt.DWORD, 1),
            ("fTXContinueOnXoff", wt.DWORD, 1), ("fOutX", wt.DWORD, 1),
            ("fInX", wt.DWORD, 1), ("fErrorChar", wt.DWORD, 1),
            ("fNull", wt.DWORD, 1), ("fRtsControl", wt.DWORD, 2),
            ("fAbortOnError", wt.DWORD, 1), ("fDummy2", wt.DWORD, 17),
            ("wReserved", wt.WORD), ("XonLim", wt.WORD), ("XoffLim", wt.WORD),
            ("ByteSize", wt.BYTE), ("Parity", wt.BYTE), ("StopBits", wt.BYTE),
            ("XonChar", ctypes.c_char), ("XoffChar", ctypes.c_char),
            ("ErrorChar", ctypes.c_char), ("EofChar", ctypes.c_char),
            ("EvtChar", ctypes.c_char), ("wReserved1", wt.WORD),
        ]

    class _TIMEOUTS(ctypes.Structure):
        _fields_ = [
            ("ReadIntervalTimeout", wt.DWORD),
            ("ReadTotalTimeoutMultiplier", wt.DWORD),
            ("ReadTotalTimeoutConstant", wt.DWORD),
            ("WriteTotalTimeoutMultiplier", wt.DWORD),
            ("WriteTotalTimeoutConstant", wt.DWORD),
        ]

    _k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                 ctypes.c_void_p, wt.DWORD, wt.DWORD,
                                 ctypes.c_void_p]
    _k32.CreateFileW.restype = ctypes.c_void_p
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]
    _k32.CloseHandle.restype = wt.BOOL
    _k32.GetCommState.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DCB)]
    _k32.SetCommState.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DCB)]
    _k32.SetCommTimeouts.argtypes = [ctypes.c_void_p, ctypes.POINTER(_TIMEOUTS)]
    _k32.PurgeComm.argtypes = [ctypes.c_void_p, wt.DWORD]
    _k32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
                              ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    _k32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
                               ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    _k32.EscapeCommFunction.argtypes = [ctypes.c_void_p, wt.DWORD]

    _PARITY = {"none": 0, "odd": 1, "even": 2, "mark": 3, "space": 4}
    _STOPBITS = {"1": 0, "1.5": 1, "2": 2}

    def _why(code: int) -> str:
        """WinError 翻成人话 —— 「拒绝访问」对用户毫无信息量。"""
        return {
            2: "系统里没有这个串口（设备没插好 / 驱动没装）",
            5: "串口被别的程序占用（先关掉串口助手、SecureCRT 之类）",
            31: "这台设备不支持该串口参数",
            1167: "设备没有响应（线没接好或设备没上电）",
            121: "串口读超时（设备一直没回数据）",
        }.get(int(code or 0), f"系统错误 {code}")


class SerialLink:
    """一条打开的串口。线程安全性由上层保证（CLIClient 本身就是串行的）。"""

    def __init__(self, port: str, baud=None, databits=None, parity=None,
                 stopbits=None, flow=None) -> None:
        self.port = norm_port(port)
        if not self.port:
            raise SerialError("没有指定串口（例如 COM3）")
        self.params = norm_params(baud, databits, parity, stopbits, flow)
        self._h = None
        self._fd = None
        self._closed = True

    # ---------------------------------------------------------------- 打开
    def open(self) -> None:
        if self.alive:
            return
        if WIN:
            self._open_win()
        else:
            self._open_posix()
        self._closed = False

    def _open_win(self) -> None:
        path = self.port if self.port.startswith("\\\\.\\") else f"\\\\.\\{self.port}"
        h = _k32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, None,
                             OPEN_EXISTING, 0, None)
        if not h or h == _INVALID:
            code = ctypes.get_last_error()
            raise SerialError(f"打不开 {self.port}：{_why(code)}")
        self._h = h
        try:
            dcb = _DCB()
            dcb.DCBlength = ctypes.sizeof(_DCB)
            if not _k32.GetCommState(h, ctypes.byref(dcb)):
                raise SerialError(f"读不到 {self.port} 的串口参数（{_why(ctypes.get_last_error())}）")
            p = self.params
            dcb.BaudRate = p["baud"]
            dcb.ByteSize = p["databits"]
            dcb.Parity = _PARITY[p["parity"]]
            dcb.StopBits = _STOPBITS[p["stopbits"]]
            dcb.fBinary = 1
            dcb.fParity = 1 if p["parity"] != "none" else 0
            # 流控：硬件用 RTS/CTS 握手，软件用 XON/XOFF；都不用时 RTS 常有效
            dcb.fOutxCtsFlow = 1 if p["flow"] == "rtscts" else 0
            dcb.fRtsControl = 2 if p["flow"] == "rtscts" else 1
            dcb.fOutX = 1 if p["flow"] == "xonxoff" else 0
            dcb.fInX = 1 if p["flow"] == "xonxoff" else 0
            dcb.fDtrControl = 1          # 拉起 DTR：很多 console 线靠它供电/唤醒
            dcb.fAbortOnError = 0
            if not _k32.SetCommState(h, ctypes.byref(dcb)):
                raise SerialError(f"{self.port} 不接受这组参数"
                                  f"（{_why(ctypes.get_last_error())}）")
            to = _TIMEOUTS(50, 0, 200, 0, 2000)
            if not _k32.SetCommTimeouts(h, ctypes.byref(to)):
                raise SerialError(f"设置 {self.port} 超时失败"
                                  f"（{_why(ctypes.get_last_error())}）")
            _k32.PurgeComm(h, PURGE_ALL)
        except Exception:
            self.close()
            raise

    def _open_posix(self) -> None:
        import termios
        try:
            fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as e:
            raise SerialError(f"打不开 {self.port}：{e.strerror or e}")
        try:
            attrs = termios.tcgetattr(fd)
            p = self.params
            speed = getattr(termios, f"B{p['baud']}", None)
            if speed is None:
                raise SerialError(f"系统不支持波特率 {p['baud']}")
            attrs[4] = attrs[5] = speed
            attrs[2] = termios.CLOCAL | termios.CREAD
            attrs[2] &= ~termios.CSTOPB
            if p["stopbits"] == "2":
                attrs[2] |= termios.CSTOPB
            attrs[2] &= ~termios.PARENB
            if p["parity"] != "none":
                attrs[2] |= termios.PARENB
                if p["parity"] == "odd":
                    attrs[2] |= termios.PARODD
            attrs[2] &= ~termios.CSIZE
            attrs[2] |= {5: termios.CS5, 6: termios.CS6,
                         7: termios.CS7, 8: termios.CS8}[p["databits"]]
            if p["flow"] == "rtscts":
                attrs[2] |= termios.CRTSCTS
            attrs[6][termios.VMIN] = 0
            attrs[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            termios.tcflush(fd, termios.TCIOFLUSH)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        self._fd = fd

    # ---------------------------------------------------------------- 属性
    @property
    def alive(self) -> bool:
        if WIN:
            return bool(self._h) and not self._closed
        return self._fd is not None and not self._closed

    # ---------------------------------------------------------------- 收发
    def write(self, data: bytes) -> int:
        if not data:
            return 0
        if not self.alive:
            raise SerialError("串口已关闭")
        if WIN:
            n = wt.DWORD(0)
            buf = ctypes.create_string_buffer(data, len(data))
            if not _k32.WriteFile(self._h, buf, len(data), ctypes.byref(n), None):
                raise SerialError(f"写串口失败（{_why(ctypes.get_last_error())}）")
            return int(n.value)
        try:
            return os.write(self._fd, data)
        except OSError as e:
            raise SerialError(f"写串口失败：{e.strerror or e}")

    def read(self, timeout: float = 0.35) -> bytes:
        """读一次。没数据返回 b""，不抛异常（上层靠「安静」判定结束）。"""
        if not self.alive:
            return b""
        wait_ms = max(10, int(float(timeout) * 1000))
        if WIN:
            # 每次调用按本次等待时间设一遍：这个结构体是「一次读」的语义，
            # 设一次就固定住的话，长命令会在读到一半时被 200ms 截断。
            to = _TIMEOUTS(50, 0, wait_ms, 0, 2000)
            if not _k32.SetCommTimeouts(self._h, ctypes.byref(to)):
                return b""
            buf = ctypes.create_string_buffer(65536)
            n = wt.DWORD(0)
            if not _k32.ReadFile(self._h, buf, 65536, ctypes.byref(n), None):
                code = ctypes.get_last_error()
                if code in (121, 0):        # 超时就是「没数据」
                    return b""
                self._closed = True
                return b""
            return buf.raw[: int(n.value)]
        import select
        try:
            r, _, _ = select.select([self._fd], [], [], max(0.0, float(timeout)))
            if not r:
                return b""
            return os.read(self._fd, 65536)
        except OSError:
            self._closed = True
            return b""

    def close(self) -> None:
        self._closed = True
        if WIN:
            if self._h:
                try:
                    _k32.PurgeComm(self._h, PURGE_ALL)
                    _k32.CloseHandle(self._h)
                except Exception:  # noqa: BLE001
                    pass
            self._h = None
            return
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        self._fd = None


def open_link(port: str, **kw) -> SerialLink:
    """建好并打开一条串口（调用方只需要它）。"""
    link = SerialLink(port, **kw)
    link.open()
    # 有些 console 线插上后要 100ms 才开始出数据；给设备一个醒来的时间，
    # 也让「刚打开就发命令」不会丢首字符。
    time.sleep(0.05)
    return link
