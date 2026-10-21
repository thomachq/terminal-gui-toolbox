# -*- coding: utf-8 -*-
"""设备与操作系统探测 + 命令方言。

这个模块解决一个核心问题：**同一个意图，在不同系统上是不同的命令**。
比如看 IP，Linux 用 `ip -4 addr`，macOS/FreeBSD 用 `ifconfig`，
Windows 用 `ipconfig`；ping 的次数参数 Linux 是 `-c`，Windows 是 `-n`。

探测分两步：
  1. `detect(session)` 跑几条最便宜的探针，得出 DeviceInfo；
  2. `dialect_for(info)` 拿到对应的 Dialect，之后所有命令都从这里生成。

探测失败也要能用：一律回落到 POSIX 方言，界面标注「未知系统」。
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

# 组合采集时用的分隔符，尽量不可能出现在正常输出里
SEP = "@@@VPNTOOL@@@"


@dataclass
class DeviceInfo:
    os_family: str = "posix"      # linux / darwin / freebsd / windows / network / posix
    os_name: str = "未知系统"
    kernel: str = ""
    arch: str = ""
    hostname: str = ""
    device_type: str = "server"   # nas / router / switch / server / workstation / container
    shell: str = "sh"
    user: str = ""
    is_root: bool = False
    vendor: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def short(self) -> str:
        return self.os_name or self.os_family

    def summary_line(self) -> str:
        bits = [b for b in (self.os_name, self.arch, self.device_type) if b]
        return " · ".join(bits)


# ------------------------------------------------------------------ 探测
def _run(session, cmd: str, timeout: int = 12, traced: bool = True) -> str:
    """跑一条探针。

    默认记日志 —— 用户能在侧边栏看见「探测到底跑了什么」，
    而不是干瞪眼等结果。性能采样那种高频命令才关掉。
    """
    try:
        if traced and hasattr(session, "run_traced"):
            rc, out, err = session.run_traced(cmd, timeout=timeout)
        else:
            rc, out, err = session.run(cmd, timeout=timeout)
        return out or ""
    except Exception:  # noqa: BLE001
        return ""


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _ps(session, script: str, timeout: int = 25) -> str:
    """在会话上跑一段 PowerShell。

    会话本身就是 PowerShell（本机 Windows）时不要再套一层 powershell，
    省一次进程，也避免引号被解析两遍。
    """
    if getattr(session, "ps_native", False):
        return _run(session, script, timeout)
    return _run(session, 'powershell -NoProfile -Command "' + script + '"', timeout)


def detect(session) -> DeviceInfo:
    """跑最少的探针确定系统类型。失败不抛异常，回落 posix。"""
    info = DeviceInfo()
    info.user = getattr(session, "user", "") or ""
    hint = getattr(session, "platform_hint", "") or ""

    # --- 已知平台就直接走，省掉 uname 那一串探针
    if hint == "windows":
        info.os_family = "windows"
        info.shell = "powershell" if getattr(session, "ps_native", False) else "cmd"
        _detect_windows(session, info)
        if not info.hostname:
            info.hostname = _first_line(_run(session, "hostname", timeout=10)).strip()
        return info

    # --- 第一步：内核族。`uname -s` 是所有类 Unix（含 DSM、OpenWrt）都有的
    uname = _first_line(_run(session, "uname -s 2>/dev/null"))
    kernel_rel = _first_line(_run(session, "uname -r 2>/dev/null"))
    arch = _first_line(_run(session, "uname -m 2>/dev/null"))
    host = _first_line(_run(session, "uname -n 2>/dev/null"))

    if not uname:
        # 没有 uname → 大概是 Windows（远程 OpenSSH 默认 shell 是 cmd）
        ver = _run(session, "ver")
        if not ver.strip():
            ver = _run(session, "cmd /c ver")
        psver = _ps(session, "$PSVersionTable.PSVersion.Major", timeout=15)
        if ver.strip() or psver.strip() or "Windows" in ver:
            info.os_family = "windows"
            info.shell = "cmd"
            info.kernel = _first_line(ver)
            info.hostname = _first_line(_run(session, "hostname"))
            _detect_windows(session, info)
            return info
        info.os_family = "unknown"
        return info

    low = uname.lower()
    if "linux" in low:
        info.os_family = "linux"
    elif "darwin" in low:
        info.os_family = "darwin"
    elif "freebsd" in low:
        info.os_family = "freebsd"
    elif "openbsd" in low or "netbsd" in low:
        info.os_family = "bsd"
    else:
        info.os_family = "posix"

    info.kernel = f"{uname} {kernel_rel}".strip()
    info.arch = arch
    info.hostname = host
    info.shell = _first_line(_run(session, "basename \"$SHELL\" 2>/dev/null")) or "sh"
    uid = _run(session, "id -u 2>/dev/null").strip()
    info.is_root = uid == "0"

    if info.os_family == "linux":
        _detect_linux(session, info)
    elif info.os_family == "darwin":
        _detect_darwin(session, info)
    elif info.os_family == "freebsd":
        _detect_freebsd(session, info)

    if not info.os_name or info.os_name == "未知系统":
        info.os_name = info.kernel or uname
    return info


def _detect_linux(session, info: DeviceInfo) -> None:
    # Synology 的 /etc/VERSION 是自家格式，先看它
    syno = _run(session, "cat /etc/VERSION 2>/dev/null")
    if syno.strip():
        kv = dict(re.findall(r'(\w+)="?([^"\n]+)"?', syno))
        ver = kv.get("productversion") or kv.get("majorversion", "")
        minor = kv.get("minorversion", "")
        if ver:
            info.os_name = f"Synology DSM {ver}" + (f"-{minor}" if minor else "")
        info.device_type = "nas"
        info.vendor = "Synology"
        info.raw["syno_version"] = kv
        return

    # OpenWrt / LEDE
    owrt = _run(session, "cat /etc/openwrt_release 2>/dev/null")
    if owrt.strip():
        m = re.search(r"DISTRIB_DESCRIPTION=['\"]?([^'\"\n]+)", owrt)
        info.os_name = (m.group(1) if m else "OpenWrt").strip()
        info.device_type = "router"
        info.vendor = "OpenWrt"
        return

    rel = _run(session, "cat /etc/os-release 2>/dev/null")
    if rel.strip():
        kv = dict(re.findall(r'^(\w+)="?([^"\n]*)"?', rel, re.M))
        name = kv.get("PRETTY_NAME") or kv.get("NAME") or "Linux"
        info.os_name = name.strip()
        info.raw["os_release"] = kv
    else:
        for f in ("/etc/redhat-release", "/etc/issue", "/etc/debian_version"):
            t = _first_line(_run(session, f"cat {f} 2>/dev/null"))
            if t:
                info.os_name = t
                break

    # 容器
    if _run(session, "test -f /.dockerenv && echo Y").strip() == "Y":
        info.device_type = "container"
    elif _run(session, "grep -qa 'lxc' /proc/1/cgroup 2>/dev/null && echo Y").strip() == "Y":
        info.device_type = "container"


def _detect_darwin(session, info: DeviceInfo) -> None:
    name = _first_line(_run(session, "sw_vers -productName 2>/dev/null")) or "macOS"
    ver = _first_line(_run(session, "sw_vers -productVersion 2>/dev/null"))
    info.os_name = f"{name} {ver}".strip()
    info.vendor = "Apple"
    model = _first_line(_run(session, "sysctl -n hw.model 2>/dev/null"))
    info.raw["model"] = model
    if model and "MacBook" in model:
        info.device_type = "workstation"


def _detect_freebsd(session, info: DeviceInfo) -> None:
    ver = _first_line(_run(session, "freebsd-version 2>/dev/null"))
    info.os_name = f"FreeBSD {ver}".strip() if ver else info.kernel


def _norm_win_arch(s: str) -> str:
    """中文版 Windows 报的是「64 位」「32 位」，统一成 x64 / x86 / ARM64。"""
    t = (s or "").strip()
    low = t.lower().replace(" ", "")
    if "arm" in low:
        return "ARM64"
    if t in ("64 位", "64位") or low in ("64-bit", "x64", "amd64"):
        return "x64"
    if t in ("32 位", "32位") or low in ("32-bit", "x86"):
        return "x86"
    return t


def _detect_windows(session, info: DeviceInfo) -> None:
    # PowerShell 可能被限制，cmd 一定能用，双路尝试
    ps = _ps(
        session,
        "$o=Get-CimInstance Win32_OperatingSystem;"
        "\"$($o.Caption)|$($o.Version)|$($o.OSArchitecture)|$($o.CSName)\"",
        timeout=25)
    line = _first_line(ps)
    if "|" in line:
        parts = [p.strip() for p in line.split("|")]
        info.os_name = parts[0] or "Windows"
        info.raw["version"] = parts[1] if len(parts) > 1 else ""
        info.arch = _norm_win_arch(parts[2]) if len(parts) > 2 else info.arch
        info.hostname = parts[3] if len(parts) > 3 else info.hostname
        info.shell = "powershell"
        # 客户端版 Windows 不是服务器
        name = info.os_name.lower()
        if "server" not in name and ("windows" in name):
            info.device_type = "workstation"
        return
    # 老系统可能没装 wmic / CIM 被限，退回 ver
    ver = _first_line(_run(session, "ver")) or _first_line(_run(session, "cmd /c ver"))
    info.os_name = ver or "Windows"
    info.shell = "cmd"


# ------------------------------------------------------------------ 方言
class Dialect:
    """把「意图」翻译成命令。子类按系统覆盖。"""

    family = "posix"
    # 3 次采样够算 CPU 与网络速率；间隔由 metrics 模块控制
    supports_procfs = False

    # ---------------- 性能采集
    def metrics_cmd(self) -> str:
        """返回一条命令，输出用 SEP 分成若干段。"""
        return ("cat /proc/stat | head -1; echo " + SEP + "; "
                "cat /proc/meminfo | head -8; echo " + SEP + "; "
                "cat /proc/net/dev; echo " + SEP + "; "
                "cat /proc/uptime; echo " + SEP + "; "
                "nproc 2>/dev/null || echo 1")

    def parse_metrics(self, raw: str) -> dict:
        """解析成原始计数，速率换算交给 metrics 模块。"""
        parts = raw.split(SEP)
        out: dict = {}
        if len(parts) < 4:
            return out
        # --- CPU
        m = re.match(r"cpu\s+(.*)", parts[0])
        if m:
            vals = [int(x) for x in m.group(1).split()[:8]]
            if len(vals) >= 4:
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                out["cpu_total"] = sum(vals)
                out["cpu_idle"] = idle
        # --- 内存（kB）
        mem = {}
        for line in parts[1].splitlines():
            mm = re.match(r"(\w+):\s+(\d+)", line)
            if mm:
                mem[mm.group(1)] = int(mm.group(2)) * 1024
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable")
        if avail is None:
            avail = (mem.get("MemFree", 0) + mem.get("Buffers", 0)
                     + mem.get("Cached", 0))
        out["mem_total"] = total
        out["mem_used"] = max(0, total - avail)
        # --- 网络（累计字节）
        rx = tx = 0
        for line in parts[2].splitlines():
            if ":" not in line:
                continue
            iface, rest = line.split(":", 1)
            if iface.strip() == "lo":
                continue
            nums = rest.split()
            if len(nums) >= 9:
                try:
                    rx += int(nums[0])
                    tx += int(nums[8])
                except ValueError:
                    pass
        out["net_rx"] = rx
        out["net_tx"] = tx
        # --- uptime
        try:
            out["uptime"] = float(parts[3].split()[0])
        except (ValueError, IndexError):
            pass
        # --- 核数
        try:
            out["ncpu"] = max(1, int(parts[4].strip()))
        except (ValueError, IndexError):
            out["ncpu"] = 1
        return out

    # ---------------- 常用命令
    def ping_cmd(self, host: str, count: int = 4) -> str:
        return f"ping -c {count} -W 2 {shlex.quote(host)}"

    def traceroute_cmd(self, host: str) -> str:
        return f"traceroute -n -w 2 -q 1 {shlex.quote(host)} 2>/dev/null || tracepath -n {shlex.quote(host)}"

    def ip_cmd(self) -> str:
        return "ip -4 addr show 2>/dev/null || ifconfig"

    def route_cmd(self) -> str:
        return "ip route 2>/dev/null || netstat -rn"

    def os_info_cmd(self) -> str:
        return ("uname -a; echo; cat /etc/os-release 2>/dev/null; "
                "echo; hostname; echo; id")

    def disk_cmd(self) -> str:
        return "df -h"

    def uptime_cmd(self) -> str:
        return "uptime"

    def top_cmd(self, n: int = 10) -> str:
        return f"ps -eo pid,ppid,pcpu,pmem,comm --sort=-pcpu 2>/dev/null | head -{n}"

    def listen_ports_cmd(self) -> str:
        return ("ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null "
                "|| netstat -tln 2>/dev/null")

    def dns_cmd(self) -> str:
        return "cat /etc/resolv.conf 2>/dev/null"


class DarwinDialect(Dialect):
    family = "darwin"

    def metrics_cmd(self) -> str:
        # macOS 没有 /proc：vm_stat + netstat + top 一次采样
        return ("sysctl -n vm.pagesize 2>/dev/null; echo " + SEP + "; "
                "vm_stat 2>/dev/null; echo " + SEP + "; "
                "sysctl -n hw.memsize hw.ncpu 2>/dev/null; echo " + SEP + "; "
                "netstat -ibn 2>/dev/null | grep -E '^[a-z]' ; echo " + SEP + "; "
                "sysctl -n kern.boottime 2>/dev/null")

    def parse_metrics(self, raw: str) -> dict:
        parts = raw.split(SEP)
        out: dict = {}
        if len(parts) < 5:
            return out
        try:
            pagesize = int(_first_line(parts[0]) or 4096)
        except ValueError:
            pagesize = 4096
        free_pages = 0
        for line in parts[1].splitlines():
            if "Pages free" in line or "Pages inactive" in line:
                m = re.search(r"(\d+)\.", line)
                if m:
                    free_pages += int(m.group(1))
        hw = parts[2].split()
        total = int(hw[0]) if hw else 0
        ncpu = int(hw[1]) if len(hw) > 1 else 1
        out["mem_total"] = total
        out["mem_used"] = max(0, total - free_pages * pagesize)
        out["ncpu"] = ncpu
        # 网络累计字节：netstat -ibn 第 7/10 列（不同系统有差异，取不到就 0）
        rx = tx = 0
        for line in parts[3].splitlines():
            cols = line.split()
            if len(cols) >= 10 and cols[0] != "lo0":
                try:
                    rx += int(cols[6])
                    tx += int(cols[9])
                except (ValueError, IndexError):
                    pass
        out["net_rx"], out["net_tx"] = rx, tx
        m = re.search(r"sec\s*=\s*(\d+)", parts[4])
        if m:
            import time
            out["uptime"] = max(0.0, time.time() - int(m.group(1)))
        return out

    def ip_cmd(self) -> str:
        return "ifconfig"

    def route_cmd(self) -> str:
        return "netstat -rn"

    def traceroute_cmd(self, host: str) -> str:
        return f"traceroute -n -w 2 -q 1 {shlex.quote(host)}"

    def top_cmd(self, n: int = 10) -> str:
        return f"ps -Ao pid,ppid,pcpu,pmem,comm -r | head -{n}"

    def listen_ports_cmd(self) -> str:
        return "netstat -an -p tcp | grep LISTEN"


class FreeBSDDialect(Dialect):
    family = "freebsd"

    def metrics_cmd(self) -> str:
        return ("vmstat -H 2>/dev/null | tail -1; echo " + SEP + "; "
                "sysctl -n hw.physmem hw.ncpu 2>/dev/null; echo " + SEP + "; "
                "netstat -ibn 2>/dev/null | grep -E '^[a-z]'; echo " + SEP + "; "
                "sysctl -n kern.boottime 2>/dev/null")

    def parse_metrics(self, raw: str) -> dict:
        parts = raw.split(SEP)
        out: dict = {}
        if len(parts) < 4:
            return out
        # vmstat -H 各系统输出不同，这里只在拿得到时填 CPU
        cols = _first_line(parts[0]).split()
        if len(cols) >= 20:
            try:
                out["cpu_idle"] = float(cols[-5])
                out["cpu_total"] = 100.0
            except ValueError:
                pass
        hw = parts[1].split()
        if hw:
            out["mem_total"] = int(hw[0])
            out["ncpu"] = int(hw[1]) if len(hw) > 1 else 1
        rx = tx = 0
        for line in parts[2].splitlines():
            cols2 = line.split()
            if len(cols2) >= 10 and not cols2[0].startswith("lo"):
                try:
                    rx += int(cols2[6])
                    tx += int(cols2[9])
                except (ValueError, IndexError):
                    pass
        out["net_rx"], out["net_tx"] = rx, tx
        m = re.search(r"sec\s*=\s*(\d+)", parts[3]) if len(parts) > 3 else None
        if m:
            import time
            out["uptime"] = max(0.0, time.time() - int(m.group(1)))
        return out

    def ip_cmd(self) -> str:
        return "ifconfig -a"

    def route_cmd(self) -> str:
        return "netstat -rn"

    def top_cmd(self, n: int = 10) -> str:
        return f"ps aux -r | head -{n}"


class WindowsDialect(Dialect):
    family = "windows"
    supports_procfs = False

    def metrics_cmd(self) -> str:
        # 少一次 CIM 查询就少 0.5s，网卡统计只调一次
        ps = (
            "$o=Get-CimInstance Win32_OperatingSystem;"
            "$c=(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average;"
            "$a=Get-NetAdapterStatistics;"
            "$n=($a | Measure-Object -Property ReceivedBytes -Sum).Sum;"
            "$t=($a | Measure-Object -Property SentBytes -Sum).Sum;"
            "$boot=$o.LastBootUpTime;"
            "'CPU='+[math]::Round($c)+';MEMT='+$o.TotalVisibleMemorySize+"
            "';MEMF='+$o.FreePhysicalMemory+';RX='+$n+';TX='+$t+"
            "';UP='+[math]::Round(((Get-Date)-$boot).TotalSeconds)"
        )
        return f'powershell -NoProfile -Command "{ps}"'

    def parse_metrics(self, raw: str) -> dict:
        out: dict = {}
        for key, val in re.findall(r"(\w+)=([\d\.]+)", raw):
            if key == "CPU":
                out["cpu_pct_direct"] = float(val)
            elif key == "MEMT":
                out["mem_total"] = int(val) * 1024
            elif key == "MEMF":
                out["mem_free"] = int(val) * 1024
            elif key == "RX":
                out["net_rx"] = int(val)
            elif key == "TX":
                out["net_tx"] = int(val)
            elif key == "UP":
                out["uptime"] = float(val)
        if "mem_total" in out and "mem_free" in out:
            out["mem_used"] = max(0, out["mem_total"] - out["mem_free"])
        return out

    def ping_cmd(self, host: str, count: int = 4) -> str:
        return f"ping -n {count} {host}"

    def traceroute_cmd(self, host: str) -> str:
        return f"tracert -d -w 1000 {host}"

    def ip_cmd(self) -> str:
        return "ipconfig /all"

    def route_cmd(self) -> str:
        return "route print"

    def os_info_cmd(self) -> str:
        return "systeminfo"

    def disk_cmd(self) -> str:
        # wmic 在 Win11 24H2 起默认不装了，改用 CIM。
        # 输出用 `|` 分隔（GB 为单位），解析成表格最稳 —— 中文表头的
        # Format-Table 文本会随列宽换行，没法可靠解析。
        return ("powershell -NoProfile -Command \"Get-CimInstance Win32_LogicalDisk "
                "-Filter 'DriveType=3' | ForEach-Object { "
                "'{0}|{1}|{2}|{3}' -f $_.DeviceID, $_.Size, "
                "$_.FreeSpace, $_.VolumeName }\"")

    def uptime_cmd(self) -> str:
        return "powershell -NoProfile -Command \"(Get-CimInstance Win32_OperatingSystem).LastBootUpTime\""

    def top_cmd(self, n: int = 12) -> str:
        # 用 JSON 而不是拼 `|`：脚本里不出现单引号和花括号，
        # `-Command "..."` 少一层引号解析，稳得多。
        return ("powershell -NoProfile -Command \"Get-Process | "
                "Sort-Object CPU -Descending | Select-Object -First "
                f"{n} Id,ProcessName,CPU,WorkingSet64 | ConvertTo-Json -Compress\"")

    def listen_ports_cmd(self) -> str:
        return "netstat -ano | findstr LISTENING"

    def dns_cmd(self) -> str:
        return ("powershell -NoProfile -Command \"Get-DnsClientServerAddress "
                "-AddressFamily IPv4 | Where-Object { $_.ServerAddresses } | "
                "ForEach-Object { 'nameserver|' + ($_.ServerAddresses -join ',')"
                " }\"")


class NetworkDialect(Dialect):
    """网络设备（交换机/路由器）—— 这类设备通常只有 telnet 与自有 CLI。"""

    family = "network"

    def ping_cmd(self, host: str, count: int = 4) -> str:
        return f"ping {host} count {count}"

    def traceroute_cmd(self, host: str) -> str:
        return f"traceroute {host}"

    def ip_cmd(self) -> str:
        return "show ip interface brief"

    def route_cmd(self) -> str:
        return "show ip route"

    def os_info_cmd(self) -> str:
        return "show version"

    def disk_cmd(self) -> str:
        return "dir"

    def uptime_cmd(self) -> str:
        return "show version | include uptime"

    def top_cmd(self, n: int = 10) -> str:
        return "show processes cpu"

    def listen_ports_cmd(self) -> str:
        return "show ip sockets"

    def dns_cmd(self) -> str:
        return "show ip name-server"


_TABLE = {
    "linux": Dialect,
    "posix": Dialect,
    "bsd": FreeBSDDialect,
    "darwin": DarwinDialect,
    "freebsd": FreeBSDDialect,
    "windows": WindowsDialect,
    "network": NetworkDialect,
}


def dialect_for(info: DeviceInfo) -> Dialect:
    return _TABLE.get(info.os_family, Dialect)()


# ------------------------------------------------------------------ 能力描述
def capabilities(info: DeviceInfo) -> list[str]:
    """界面上用一句话说明这台机器支持什么。"""
    d = dialect_for(info)
    caps = []
    if d.family in ("linux", "darwin", "freebsd", "bsd", "posix"):
        caps.append("性能采样")
    if info.os_family == "linux":
        caps.append("iptables/nftables")
    elif info.os_family == "windows":
        caps.append("Windows 防火墙")
    elif info.os_family == "darwin":
        caps.append("pf")
    elif info.os_family == "freebsd":
        caps.append("pf/ipfw")
    if info.device_type == "nas":
        caps.append("DSM 包管理")
    return caps
