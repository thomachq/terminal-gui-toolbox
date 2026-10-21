# -*- coding: utf-8 -*-
"""路由表：读取、校验、增删。

每个系统一套后端，界面只跟统一的行结构打交道：

  {dst, gateway, dev, metric, proto, scope, flags, src, _tone, _persist}

写操作一律走 `session.run_admin()`，非 root 时自动套 sudo。
"""
from __future__ import annotations

import ipaddress
import re
import shlex

from core import parsers


class RouteError(Exception):
    pass


def _valid_dst(dst: str) -> bool:
    d = (dst or "").strip()
    if d in ("default", "0.0.0.0/0", "::/0"):
        return True
    try:
        ipaddress.ip_network(d, strict=False)
        return True
    except ValueError:
        return False


def _valid_ip(v: str, allow_empty: bool = True) -> bool:
    v = (v or "").strip()
    if not v:
        return allow_empty
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False


def prefix_to_mask(prefix: int) -> str:
    p = max(0, min(32, int(prefix)))
    return str(ipaddress.IPv4Network(f"0.0.0.0/{p}").netmask)


def dst_to_net_mask(dst: str) -> tuple[str, str]:
    d = (dst or "").strip()
    if d in ("default", "0.0.0.0/0"):
        return "0.0.0.0", "0.0.0.0"
    if "/" not in d:
        return d, "255.255.255.255"
    net = ipaddress.ip_network(d, strict=False)
    return str(net.network_address), prefix_to_mask(net.prefixlen)


class RouteBackend:
    key = "generic"
    label = "通用"
    can_write = True
    need_root = True

    def __init__(self, session, device=None):
        self.session = session
        self.device = device

    # ---------------- 读
    def read_cmd(self) -> str:
        raise NotImplementedError

    def parse(self, text: str) -> list[dict]:
        raise NotImplementedError

    def reload(self, timeout: int = 30) -> list[dict]:
        rc, out, err = self.session.run_traced(self.read_cmd(), timeout=timeout)
        if rc != 0 and not out.strip():
            raise RouteError((err or "读取路由表失败").strip())
        return self.parse(out)

    # ---------------- 写
    def validate(self, r: dict) -> str | None:
        if not _valid_dst(r.get("dst", "")):
            return "目标网段格式不对，用 192.168.1.0/24 或 default"
        if not _valid_ip(r.get("gateway", "")):
            return "网关不是合法 IP"
        m = str(r.get("metric", "") or "").strip()
        if m and not m.isdigit():
            return "跃点数需要是整数"
        return None

    def add_cmds(self, r: dict) -> list[str]:
        raise NotImplementedError

    def del_cmds(self, r: dict) -> list[str]:
        raise NotImplementedError

    def persist_note(self) -> str:
        return "改动只对当前运行有效，重启后失效"


# ==================================================================== Linux
class LinuxRoute(RouteBackend):
    key = "iproute"
    label = "ip route（Linux）"

    def read_cmd(self) -> str:
        return "ip route show 2>/dev/null || netstat -rn"

    def parse(self, text: str) -> list[dict]:
        rows = parsers.parse_route_linux(text)
        if not rows:
            rows = parsers.parse_route_bsd(text)
        for r in rows:
            r["_persist"] = "运行时"
        return rows

    def add_cmds(self, r: dict) -> list[str]:
        dst = r["dst"]
        parts = ["ip", "route", "add", dst]
        if r.get("gateway"):
            parts += ["via", r["gateway"]]
        if r.get("dev"):
            parts += ["dev", r["dev"]]
        if str(r.get("metric") or "").strip():
            parts += ["metric", str(r["metric"])]
        return [" ".join(shlex.quote(x) if " " in x else x for x in parts)]

    def del_cmds(self, r: dict) -> list[str]:
        dst = r.get("dst") or "default"
        parts = ["ip", "route", "del", dst]
        if r.get("gateway"):
            parts += ["via", r["gateway"]]
        if r.get("dev"):
            parts += ["dev", r["dev"]]
        return [" ".join(parts)]

    def persist_note(self) -> str:
        return ("Linux 的 ip route 重启即失效。要长期生效请写进 "
                "netplan / systemd-networkd / rc.local ——本程序不改系统配置。")


# ==================================================================== Windows
class WindowsRoute(RouteBackend):
    key = "route"
    label = "route（Windows）"

    def read_cmd(self) -> str:
        return "route print -4"

    def parse(self, text: str) -> list[dict]:
        rows = parsers.parse_route_win(text)
        for r in rows:
            r["_persist"] = "会话"
            r["dev"] = r.get("dev", "")
        return rows

    def add_cmds(self, r: dict) -> list[str]:
        net, mask = dst_to_net_mask(r["dst"])
        persistent = bool(r.get("persistent"))
        parts = ["route"]
        parts.append("-p" if persistent else "")
        parts += ["add", net, "mask", mask]
        if r.get("gateway"):
            parts.append(r["gateway"])
        if str(r.get("metric") or "").strip():
            parts += ["metric", str(r["metric"])]
        return [" ".join(x for x in parts if x)]

    def del_cmds(self, r: dict) -> list[str]:
        net, mask = dst_to_net_mask(r.get("dst", "0.0.0.0/0"))
        return [f"route delete {net} mask {mask}"]

    def persist_note(self) -> str:
        return "勾选「永久」会用 route -p 写入注册表，重启后仍在"


# ==================================================================== BSD / macOS
class BSDRoute(RouteBackend):
    key = "bsdroute"
    label = "route（BSD / macOS）"

    def read_cmd(self) -> str:
        return "netstat -rn -f inet"

    def parse(self, text: str) -> list[dict]:
        rows = parsers.parse_route_bsd(text)
        for r in rows:
            r["_persist"] = "运行时"
        return rows

    def _target(self, r: dict) -> tuple[str, str]:
        dst = (r.get("dst") or "default").strip()
        if dst in ("0.0.0.0/0", "default"):
            return "-default", "default"
        if "/" in dst:
            return "-net", dst
        return "-host", dst

    def add_cmds(self, r: dict) -> list[str]:
        flag, dst = self._target(r)
        gw = r.get("gateway") or ""
        if not gw:
            raise RouteError("BSD/macOS 新增路由必须指定网关")
        return [f"route -n add {flag} {dst} {gw}"]

    def del_cmds(self, r: dict) -> list[str]:
        flag, dst = self._target(r)
        return [f"route -n delete {flag} {dst}"]

    def persist_note(self) -> str:
        return "macOS / BSD 的 route 命令重启失效，长期生效需写进网络服务配置"


def backend_for(device) -> RouteBackend:
    fam = getattr(device, "os_family", "") or ""
    if fam == "windows":
        return WindowsRoute(None, device)
    if fam in ("darwin", "freebsd", "bsd"):
        return BSDRoute(None, device)
    return LinuxRoute(None, device)


def backend_of(device, session) -> RouteBackend:
    b = backend_for(device)
    b.session = session
    return b


# ==================================================================== 执行
def apply(session, backend: RouteBackend, action: str, route: dict,
          timeout: int = 45) -> tuple[int, str, str]:
    """action: add / del。返回最后一条命令的结果。"""
    if action == "add":
        err = backend.validate(route)
        if err:
            raise RouteError(err)
        cmds = backend.add_cmds(route)
    elif action == "del":
        cmds = backend.del_cmds(route)
    else:
        raise RouteError(f"未知操作 {action}")

    rc, out, err = 0, "", ""
    for c in cmds:
        rc, o, e = session.run_admin(c, timeout=timeout)
        out += o
        err += e
        if rc != 0:
            break
    return rc, out.strip(), err.strip()


def fmt_metric(v) -> str:
    s = str(v or "").strip()
    return s if s.isdigit() else ""


def is_default(r: dict) -> bool:
    return str(r.get("dst", "")) in ("0.0.0.0/0", "default")


def clean_cidr(text: str) -> str:
    """用户可能填 192.168.1.0/24、192.168.1.0 255.255.255.0、default。"""
    t = (text or "").strip()
    if not t:
        return ""
    if t.lower() == "default":
        return "0.0.0.0/0"
    m = re.match(r"^([\d.]+)\s+([\d.]+)$", t)
    if m:
        try:
            net = ipaddress.ip_network(f"{m.group(1)}/{m.group(2)}", strict=False)
            return str(net)
        except ValueError:
            return t
    if "/" not in t and re.match(r"^[\d.]+$", t):
        return t + "/32"
    return t
