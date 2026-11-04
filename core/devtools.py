# -*- coding: utf-8 -*-
"""外部调试组件（adb / hdc）的定位、占用检测与设备清单。

为什么单独一层：
  · 「用环境变量里的，还是用应用目录 bin 里的」是一条**用户可切**的策略，
    必须只有一个地方说了算（界面 / 连接 / 设置页 / 助手都调这里）；
  · adb 与 hdc 都是「客户端 + 本机常驻 server」的形态，server 端口被
    别的软件占住是**必然会遇到**的事（Android Studio / DevEco / 手机助手 /
    投屏工具都会各带一份），必须能查出是谁占的、并把后果说清楚；
  · 这层不碰 Tk、不弹窗：它只回答问题（是谁、能不能用、会断什么），
    弹不弹、杀不杀由界面决定 —— 否则后台线程里弹窗必崩。

端口约定（官方默认）：
  adb  5037
  hdc  8710

一条硬规矩：**没有用户点头，绝不杀别人的进程。** 这里只提供 `kill_pid()`，
调用方必须先把 `conflict_text()` 给用户看过。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

WIN = sys.platform == "win32"

#: bus → (端口, 可执行名, 显示名)
BUS = {
    "adb": {"port": 5037, "exe": "adb", "label": "ADB"},
    "hdc": {"port": 8710, "exe": "hdc", "label": "HDC"},
}

SRC_AUTO = "auto"
SRC_ENV = "env"
SRC_BUNDLED = "bundled"
SRC_LABEL = {
    SRC_AUTO: "自动（环境变量优先，找不到用内置）",
    SRC_ENV: "环境变量（PATH）",
    SRC_BUNDLED: "内置组件（应用目录 bin）",
}


class ToolMissing(Exception):
    """组件没找到 / 不能用。消息直接给人看。"""


class ToolError(Exception):
    """组件跑起来了但报错（连不上、端口冲突……）。"""


def norm_src(src: str | None) -> str:
    s = str(src or "").strip().lower()
    return s if s in (SRC_AUTO, SRC_ENV, SRC_BUNDLED) else SRC_AUTO


def app_dir() -> Path:
    """应用目录：打包后是 exe 所在目录，源码运行时是项目根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bin_dir() -> Path:
    return app_dir() / "bin"


def exe_name(bus: str) -> str:
    base = BUS[bus]["exe"] if bus in BUS else str(bus)
    return base + (".exe" if WIN else "")


# ------------------------------------------------------------------ 定位
def _env_path(bus: str) -> str:
    base = BUS[bus]["exe"] if bus in BUS else str(bus)
    p = shutil.which(base)
    if p:
        return str(Path(p))
    # POSIX 上偶尔只有 adb 没有 adb.exe；Windows 上反过来
    alt = base + ("" if WIN else ".exe")
    if alt != base:
        p = shutil.which(alt)
        if p:
            return str(Path(p))
    return ""


def _bundled_path(bus: str) -> str:
    p = bin_dir() / exe_name(bus)
    return str(p) if p.is_file() else ""


def resolve_tool(bus: str, src: str | None = SRC_AUTO) -> dict:
    """找组件。返回 {bus, path, src_used, exists, tried, error}。

    `auto` 的顺序是**环境变量优先、内置兜底**，与用户的原话一致
    （「让用户选择是使用环境变量还是内置组件，如果没有环境变量，
    则使用应用目录的 bin 文件夹」）。
    """
    bus = str(bus or "").lower()
    if bus not in BUS:
        return {"bus": bus, "path": "", "src_used": "", "exists": False,
                "tried": [], "error": f"不认识的组件：{bus}"}
    want = norm_src(src)
    order = [SRC_ENV, SRC_BUNDLED] if want == SRC_AUTO else [want]
    tried: list[dict] = []
    for s in order:
        p = _env_path(bus) if s == SRC_ENV else _bundled_path(bus)
        tried.append({"src": s, "path": p})
        if p:
            return {"bus": bus, "path": p, "src_used": s, "exists": True,
                    "tried": tried, "error": ""}
    where = (f"{bin_dir() / exe_name(bus)}")
    if want == SRC_ENV:
        err = (f"环境变量里找不到 {BUS[bus]['exe']}（PATH 里没有）。\n"
               f"可以改用「内置组件」，把 {exe_name(bus)} 放进：{where}")
    elif want == SRC_BUNDLED:
        err = (f"应用目录里没有内置的 {exe_name(bus)}。\n"
               f"放一份到：{where}\n"
               f"或在档案 / 设置里改回「自动 / 环境变量」。")
    else:
        err = (f"没找到 {BUS[bus]['exe']}：PATH 里没有，应用目录也没有。\n"
               f"把 {exe_name(bus)} 放进：{where}\n"
               f"或安装对应 SDK 并加入 PATH。")
    return {"bus": bus, "path": "", "src_used": "", "exists": False,
            "tried": tried, "error": err}


def run_tool(bus: str, args: list[str], src: str | None = SRC_AUTO,
             timeout: int = 25) -> tuple[int, str, str]:
    """跑一次组件命令。组件缺失抛 ToolMissing（带怎么修好）。"""
    info = resolve_tool(bus, src)
    if not info["exists"]:
        raise ToolMissing(info["error"])
    argv = [info["path"]] + [str(a) for a in (args or [])]
    try:
        p = subprocess.run(
            argv, capture_output=True, timeout=timeout,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if WIN else 0))
    except subprocess.TimeoutExpired:
        return 124, "", f"{BUS.get(bus, {}).get('label', bus)} 超时（{timeout}s）"
    except OSError as e:
        return 126, "", f"启动 {info['path']} 失败：{e}"
    from core.session import decode_bytes
    return p.returncode, decode_bytes(p.stdout), decode_bytes(p.stderr)


def tool_version(bus: str, src: str | None = SRC_AUTO) -> str:
    """一行版本号。取不到就返回空串（界面上显示「版本未知」，不报错）。"""
    bus = str(bus or "").lower()
    args = ["version"] if bus == "adb" else ["-v"]
    try:
        rc, out, err = run_tool(bus, args, src, timeout=12)
    except ToolMissing:
        return ""
    text = ((out or "") + "\n" + (err or "")).strip()
    for line in text.splitlines():
        low = line.lower()
        if bus == "adb" and ("version" in low or "android debug" in low):
            return line.strip()
        if bus == "hdc" and low.startswith("ver"):
            return line.strip()
    return text.splitlines()[0].strip() if text else ""


# ------------------------------------------------------------------ 进程
def process_info(pid: int) -> dict:
    """PID → {pid, name, path}。查不到就只给 pid。"""
    pid = int(pid or 0)
    out = {"pid": pid, "name": "", "path": ""}
    if pid <= 0:
        return out
    if WIN:
        try:
            import ctypes
            import ctypes.wintypes as wt
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32.OpenProcess.restype = ctypes.c_void_p
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if h:
                try:
                    buf = ctypes.create_unicode_buffer(1024)
                    size = wt.DWORD(1024)
                    if k32.QueryFullProcessImageNameW(h, 0, buf,
                                                      ctypes.byref(size)):
                        out["path"] = buf.value
                        out["name"] = Path(buf.value).name
                finally:
                    k32.CloseHandle(h)
        except Exception:  # noqa: BLE001
            pass
        if not out["name"]:
            try:
                p = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True, timeout=6,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                from core.session import decode_bytes
                line = decode_bytes(p.stdout).strip().splitlines()
                if line and line[0].startswith('"'):
                    out["name"] = line[0].split('","')[0].strip('"')
            except Exception:  # noqa: BLE001
                pass
        return out
    for name in ("name", ""):
        p = Path(f"/proc/{pid}/comm")
        try:
            if p.is_file():
                out["name"] = p.read_text(encoding="utf-8").strip()
                break
        except OSError:
            break
    try:
        out["path"] = os.readlink(f"/proc/{pid}/exe")
        out["name"] = out["name"] or Path(out["path"]).name
    except OSError:
        pass
    return out


def _listen_rows(text: str) -> list[tuple[str, int]]:
    """从 netstat 输出里抠出 (本地地址, PID) 的 LISTENING 行。

    单独抽出来是为了能离线测：不同语言 / 不同 Windows 版本的表头是本地化的，
    只认「这一行有监听状态 + 数字 PID」，不认表头文字。
    """
    rows: list[tuple[str, int]] = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        # 形如：TCP  0.0.0.0:5037  0.0.0.0:0  LISTENING  12345
        proto = parts[0].upper()
        if proto not in ("TCP", "TCPV6", "UDP", "UDPV6"):
            continue
        state = " ".join(parts[3:-1]).upper()
        # Windows 的 netstat 状态名一般保持英文，但个别本地化版本会译成
        # 「侦听 / 监听」；两种都认，别因为表头语言漏掉真正的占用者。
        if not any(k in state for k in ("LISTEN", "侦听", "监听")):
            continue
        addr = parts[1]
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        rows.append((addr, pid))
    return rows


def _port_of(addr: str) -> int:
    """`0.0.0.0:5037` / `[::]:5037` / `*:5037` → 5037。"""
    m = re.search(r":(\d+)$", str(addr or ""))
    return int(m.group(1)) if m else 0


def port_owner(port: int) -> dict | None:
    """谁在监听这个端口。没人监听返回 None。"""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    cmd = ["netstat", "-ano"] + (["-p", "tcp"] if WIN else [])
    try:
        p = subprocess.run(
            cmd, capture_output=True, timeout=8,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if WIN else 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    from core.session import decode_bytes
    text = decode_bytes(p.stdout)
    if not text:
        return None
    for addr, pid in _listen_rows(text):
        if _port_of(addr) == port:
            info = process_info(pid)
            info["addr"] = addr
            info["port"] = port
            return info
    return None


def kill_pid(pid: int) -> tuple[bool, str]:
    """结束一个进程。先 ctypes，失败再 taskkill（都不成就把原因说清楚）。"""
    pid = int(pid or 0)
    if pid <= 0:
        return False, "PID 不合法"
    if WIN:
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            PROCESS_TERMINATE = 0x0001
            SYNCHRONIZE = 0x00100000
            k32.OpenProcess.restype = ctypes.c_void_p
            h = k32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
            if h:
                try:
                    if k32.TerminateProcess(h, 1):
                        k32.WaitForSingleObject(h, 3000)
                        return True, f"已结束进程 {pid}"
                    return False, f"结束进程 {pid} 失败（系统错误 {ctypes.get_last_error()}）"
                finally:
                    k32.CloseHandle(h)
        except Exception:  # noqa: BLE001
            pass
        try:
            p = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            from core.session import decode_bytes
            msg = (decode_bytes(p.stdout) + decode_bytes(p.stderr)).strip()
            return p.returncode == 0, msg or f"taskkill rc={p.returncode}"
        except Exception as e:  # noqa: BLE001
            return False, f"结束进程失败：{e}"
    try:
        import signal
        os.kill(pid, signal.SIGTERM)
        return True, f"已结束进程 {pid}"
    except OSError as e:
        return False, f"结束进程失败：{e}"


def conflict_text(bus: str, owner: dict | None) -> str:
    """把「杀了它会怎样」写成用户能判断的一段话。

    必须点名**具体哪些工具会断**：用户点「由我结束」之前，唯一能帮他决策的
    信息就是这个。只说「可能影响其他应用」等于什么都没说。
    """
    if not owner:
        return ""
    label = BUS.get(bus, {}).get("label", bus)
    who = owner.get("name") or "未知进程"
    path = owner.get("path") or ""
    pid = owner.get("pid") or "?"
    if bus == "adb":
        hurt = ("Android Studio（含 Logcat / Run）、手机助手（小米 / 华为 / ASUS 等）、\n"
                "投屏或远程控制工具、安卓模拟器、以及本机其它 adb 图形前端。\n"
                "它们会立刻失去设备连接；多数会在下一次操作时自动重启自己的 adb 服务，\n"
                "**可能又把 5037 抢回去**。正在跑的安装 / 抓日志会中断。")
    elif bus == "hdc":
        hurt = ("DevEco Studio（运行 / 调试 / 查看日志）、鸿蒙模拟器、\n"
                "以及其它基于 hdc 的鸿蒙调试工具。\n"
                "它们会立刻掉设备；DevEco 下次操作时通常会自己拉起 hdc 服务，\n"
                "**可能再次占用 8710**。正在进行的安装 / 调试会中断。")
    else:
        hurt = "占用该端口的其它程序会立刻断开。"
    lines = [
        f"{label} 的默认端口被其它程序占用了。",
        "",
        f"占用者：{who}（PID {pid}）",
    ]
    if path:
        lines.append(f"路径：{path}")
    lines += [
        "",
        "如果由本程序结束它，会导致：",
        hurt,
        "",
        "如果这个程序正在做重要的事，请选「我自己关掉它，然后重试」。",
    ]
    return "\n".join(lines)


def probe_server(bus: str, src: str | None = SRC_AUTO) -> tuple[bool, str]:
    """试一下「现在这个 server 能不能被我们用」。

    端口被占 ≠ 一定冲突：Android Studio 先起的 adb 只要版本一致，
    我们的 adb 会直接复用它（这是官方行为）。只有版本不匹配 / 连不上
    才是真冲突。判据是**客户端自己的这次探测**，不是猜。

    注意：这里跑的都是只读命令（`adb devices` / `hdc checkserver`），
    不会 kill 任何东西。
    """
    bus = str(bus or "").lower()
    try:
        if bus == "adb":
            rc, out, err = run_tool("adb", ["devices", "-l"], src, timeout=20)
            text = (out + "\n" + err).strip()
            bad = re.search(
                r"doesn'?t match|cannot connect|failed to (start|check|connect)"
                r"|protocol fault|could not install|error:", text, re.I)
            return (rc == 0 and not bad), text
        rc, out, err = run_tool("hdc", ["checkserver"], src, timeout=15)
        text = (out + "\n" + err).strip()
        bad = re.search(r"not match|mismatch|fail|not connect", text, re.I)
        return (rc == 0 and not bad), text
    except ToolMissing as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def preflight(bus: str, src: str | None = SRC_AUTO,
              port: int | None = None) -> dict:
    """连接前的体检：组件在不在、端口被谁占、算不算冲突。

    「同一个可执行文件」不算冲突：那正是我们要复用的 server。
    不同路径（哪怕都叫 adb.exe）就要问用户 —— 版本不匹配时 adb 会
    自己 kill 掉对方，这一步没有用户同意不能做。
    """
    bus = str(bus or "").lower()
    default_port = BUS.get(bus, {}).get("port", 0)
    port = int(port or default_port or 0)
    info = resolve_tool(bus, src)
    owner = port_owner(port) if port else None
    same = bool(owner and info.get("path")
                and _same_path(owner.get("path", ""), info["path"]))
    conflict = bool(owner) and not same
    note = ""
    if not info["exists"]:
        note = info["error"]
    elif conflict:
        # 端口被别人占着，但那可能是「同版本、可以直接复用」的 server
        # （Android Studio / DevEco 先起来的）。先探一次再决定要不要吓用户：
        # 版本兼容就复用，不弹窗、不杀进程。
        compatible, probe_text = probe_server(bus, src)
        if compatible:
            conflict = False
            note = (f"端口 {port} 由 {owner.get('name') or '其它程序'}"
                    f"（PID {owner.get('pid')}）占用，但版本兼容，"
                    f"会直接复用它，不会结束它。")
        else:
            note = conflict_text(bus, owner)
            tail = (probe_text or "").strip().splitlines()
            if tail:
                note += "\n\n探测结果：" + tail[-1][:200]
    return {
        "bus": bus, "tool": info, "version": tool_version(bus, src) if info["exists"] else "",
        "port": port, "owner": owner, "same_tool": same,
        "conflict": conflict, "ok": bool(info["exists"]) and not conflict,
        "note": note,
    }


def _same_path(a: str, b: str) -> bool:
    if not a or not b:
        return False
    try:
        return os.path.normcase(os.path.realpath(a)) == \
            os.path.normcase(os.path.realpath(b))
    except OSError:
        return os.path.normcase(a) == os.path.normcase(b)


# ------------------------------------------------------------------ server
def ensure_server(bus: str, src: str | None = SRC_AUTO) -> tuple[bool, str]:
    """把本机 server 拉起来。返回 (是否可用, 原文)。

    版本不匹配 / 端口绑不上 / 已有别的 server，都从原文里认出来并
    翻成一句能照着做的话 —— 不把 adb 那串英文原样丢给用户。
    """
    bus = str(bus or "").lower()
    if bus == "adb":
        rc, out, err = run_tool("adb", ["start-server"], src, timeout=20)
        text = (out + "\n" + err).strip()
        low = text.lower()
        if "cannot bind" in low or "failed to start daemon" in low:
            return False, ("ADB 服务起不来：5037 端口被别的程序占用。\n"
                           "关掉那个程序（或让它改端口）后再连。\n\n" + text)
        if "doesn't match" in low or "does not match" in low:
            return False, ("本机的 ADB 服务是另一个版本（通常是别的软件自带的 adb）。\n"
                           "请选择「由本程序结束占用进程」再重试。\n\n" + text)
        if rc != 0 and "daemon" in low:
            return False, text
        return True, text
    rc, out, err = run_tool("hdc", ["start"], src, timeout=20)
    text = (out + "\n" + err).strip()
    low = text.lower()
    if "fail" in low or "error" in low:
        return False, ("HDC 服务起不来：8710 端口可能被别的 hdc / DevEco 占用。\n"
                       "可以选「由本程序结束占用进程」，或先关掉 DevEco Studio。\n\n"
                       + text)
    # checkserver 会明确说客户端与服务端版本是否一致
    rc2, out2, err2 = run_tool("hdc", ["checkserver"], src, timeout=12)
    t2 = (out2 + "\n" + err2).strip()
    if "not match" in t2.lower() or "mismatch" in t2.lower():
        return False, ("HDC 客户端与服务端版本不一致（服务端多半是另一个 SDK 拉起的）。\n"
                       "请选择「由本程序结束占用进程」再重试。\n\n" + t2)
    return True, (text + "\n" + t2).strip()


# ------------------------------------------------------------------ 设备
def parse_adb_devices(text: str) -> list[dict]:
    """解析 `adb devices -l`。"""
    rows: list[dict] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.lower().startswith("list of devices"):
            continue
        if s.startswith("*") or s.lower().startswith("adb server"):
            continue
        parts = s.split("\t") if "\t" in s else s.split(None, 1)
        if len(parts) < 2:
            continue
        serial = parts[0].strip()
        rest = parts[1].strip()
        state = rest.split()[0] if rest else "unknown"
        model = ""
        m = re.search(r"\bmodel:(\S+)", rest)
        if m:
            model = m.group(1).replace("_", " ")
        rows.append({
            "serial": serial, "state": state, "model": model,
            "remote": (":" in serial and not serial.startswith("emulator-")),
            "detail": rest,
        })
    return rows


_HDC_STATES = {"connected", "offline", "unknown", "ready", "unauthorized"}


def parse_hdc_targets(text: str) -> list[dict]:
    """解析 `hdc list targets [-v]`。

    带 `-v` 时列是「key / USB|TCP / 状态 / 主机 / 版本」；
    不带 `-v` 时每行只有一个 key。两种都要吃。
    """
    rows: list[dict] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.lower() in ("empty", "[empty]"):
            continue
        parts = re.split(r"\s+", s)
        key = parts[0]
        kind = parts[1] if len(parts) > 1 else ""
        state = parts[2] if len(parts) > 2 else ("Connected" if len(parts) == 1 else "")
        if len(parts) > 1 and kind.lower() not in ("usb", "tcp", "uart", "bt"):
            # 不是 -v 输出：第二列可能就是状态
            state = kind if kind.lower() in _HDC_STATES else state
            kind = ""
        rows.append({
            "serial": key, "state": state or "Connected",
            "type": kind, "model": "",
            "remote": (":" in key and kind.upper() == "TCP") or key.lower().startswith("tcp"),
            "detail": s,
        })
    return rows


def list_devices(bus: str, src: str | None = SRC_AUTO) -> list[dict]:
    """列当前设备。组件缺失时抛 ToolMissing（界面据此提示去放 bin）。"""
    bus = str(bus or "").lower()
    if bus == "adb":
        rc, out, err = run_tool("adb", ["devices", "-l"], src, timeout=20)
        if rc != 0 and not out.strip():
            raise ToolError((err or "adb devices 失败").strip())
        return parse_adb_devices(out)
    rc, out, err = run_tool("hdc", ["list", "targets", "-v"], src, timeout=20)
    rows = parse_hdc_targets(out)
    if not rows:
        rc2, out2, err2 = run_tool("hdc", ["list", "targets"], src, timeout=20)
        rows = parse_hdc_targets(out2)
    if rc != 0 and not rows:
        raise ToolError((err or "hdc list targets 失败").strip())
    return rows


def connect_device(bus: str, target: str, src: str | None = SRC_AUTO,
                   timeout: int = 20) -> tuple[bool, str]:
    """USB 之外的那条路：adb connect / hdc tconn。"""
    bus = str(bus or "").lower()
    target = str(target or "").strip()
    if not target:
        return False, "没给地址（形如 192.168.1.5:5555）"
    if bus == "adb":
        rc, out, err = run_tool("adb", ["connect", target], src, timeout=timeout)
        text = (out + "\n" + err).strip()
        ok = "connected to" in text.lower() and "failed" not in text.lower()
        return ok, text or ("连接成功" if ok else "adb connect 没有输出")
    rc, out, err = run_tool("hdc", ["tconn", target], src, timeout=timeout)
    text = (out + "\n" + err).strip()
    low = text.lower()
    ok = ("connect ok" in low or "connected" in low) and "fail" not in low
    return ok, text or ("连接成功" if ok else "hdc tconn 没有输出")


def disconnect_device(bus: str, target: str, src: str | None = SRC_AUTO,
                      timeout: int = 20) -> tuple[bool, str]:
    bus = str(bus or "").lower()
    target = str(target or "").strip()
    if not target:
        return False, "没给地址"
    if bus == "adb":
        rc, out, err = run_tool("adb", ["disconnect", target], src, timeout=timeout)
        return rc == 0, (out + "\n" + err).strip()
    rc, out, err = run_tool("hdc", ["tconn", target, "-remove"], src, timeout=timeout)
    return rc == 0, (out + "\n" + err).strip()


def pick_serial(bus: str, rows: list[dict], want: str = "") -> tuple[str, str]:
    """从设备清单里挑一台。返回 (serial, 错误说明)。

    多设备又没指定时必须停手：随便挑一台执行命令，比报错危险得多 ——
    用户以为在 A 上看日志，其实把 A 的配置改到了 B 上。
    """
    want = str(want or "").strip()
    alive = [r for r in rows if str(r.get("state", "")).lower() in
             ("device", "connected", "ready")]
    # hdc 会把本机**所有串口**列成 `UART / Ready` 的目标（它支持
    # `hdc tconn COM3,921600` 走串口）。这些不是「插着的鸿蒙设备」，
    # 自动挑一台就会连到用户根本没接设备的 COM1 上。所以自动选择时
    # 只认 USB / TCP；用户**明确写了** COM3 才走 UART。
    if str(bus or "").lower() == "hdc":
        alive = [r for r in alive
                 if str(r.get("type", "")).upper() != "UART"]
    if want:
        for r in rows:
            if r.get("serial") == want:
                return want, ""
        return "", (f"没找到设备「{want}」。当前在线："
                    + ("、".join(r["serial"] for r in alive) or "（没有）"))
    if not alive:
        if rows and str(bus or "").lower() == "hdc" and all(
                str(r.get("type", "")).upper() == "UART" for r in rows):
            return "", ("只看到本机串口（hdc 把 COM 口也列成了 UART 目标），"
                        "没有 USB / 网络上的鸿蒙设备。\n"
                        "如果确实要走串口，请把串口名（例如 COM3）填进"
                        "「设备序列号」；否则检查数据线与「USB 调试」。")
        if rows:
            return "", ("设备在，但状态不是可用："
                        + "；".join(f"{r['serial']}={r['state']}" for r in rows)
                        + "\n（USB 调试没授权时先在设备上点「允许」）")
        return "", ("没有发现设备。USB 连接请确认数据线、开发者选项与 USB 调试；"
                    "远程连接请先填对 IP:端口。")
    if len(alive) > 1:
        return "", ("当前有多台设备，请指定序列号（档案里的「设备序列号」字段）或"
                    "点「扫描设备…」选一台：\n"
                    + "\n".join(f"  · {r['serial']}（{r.get('model') or r.get('state')}）"
                                for r in alive))
    return str(alive[0]["serial"]), ""
