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


# ==================================================================== 网络设备
class NetworkRoute(RouteBackend):
    """交换机 / 路由器：Cisco 用 show + ip route，H3C/华为用 display + ip route-static。"""

    key = "network"
    label = "网络设备 CLI"
    need_root = False        # 读不需要特权；写要 enable（由会话层判断）

    def __init__(self, session, device=None):
        super().__init__(session, device)
        vendor = (getattr(device, "vendor", "") or "").lower()
        self.vendor = vendor or "generic"
        self.display = self.vendor in ("h3c", "huawei", "ruijie")
        names = {"cisco": "Cisco IOS", "h3c": "H3C Comware",
                 "huawei": "Huawei VRP", "ruijie": "锐捷 RGOS",
                 "juniper": "Juniper JunOS"}
        self.label = names.get(self.vendor, "网络设备 CLI")
        self.needs_enable = True

    @property
    def can_write(self) -> bool:
        """能不能改配置，取决于**对端是不是网络设备 + 有没有持久 CLI**，
        而不是用什么协议连上去的。

        以前这里写的是 `kind == "telnet"`，等于「SSH 连交换机只能只读」——
        但 SSH 一样进得去配置视图，只要走 invoke_shell 拿一条持久会话
        （见 core/ssh_shell.py）。反过来 Telnet 连 Linux 也不会被当成
        可写设备，因为 host_kind 是 unix，压根不会走到这个后端。
        后端在 backend_for() 里构造时 session 还是 None，所以必须每次按
        当前会话判，不能写死在 __init__。
        """
        s = self.session
        if s is None or getattr(s, "host_kind", "") != "network":
            return False
        if self.vendor not in ("cisco", "h3c", "huawei", "ruijie"):
            return False
        return bool(getattr(s, "can_cli", False))

    def read_cmd(self) -> str:
        return ("display ip routing-table" if self.display
                else "show ip route")

    def parse(self, text: str) -> list[dict]:
        rows = parsers.parse_route_network(text)
        for r in rows:
            r["_persist"] = "已保存配置" if r.get("proto") == "static" else "动态/直连"
        return rows

    def _netmask(self, r: dict) -> tuple[str, str]:
        return dst_to_net_mask(r.get("dst") or "default")

    def add_cmds(self, r: dict) -> list[str]:
        net, mask = self._netmask(r)
        gw = (r.get("gateway") or "").strip()
        if not gw:
            raise RouteError("网络设备新增静态路由必须指定下一跳")
        if self.vendor == "cisco":
            return [f"ip route {net} {mask} {gw}"]
        return [f"ip route-static {net} {mask} {gw}"]

    def del_cmds(self, r: dict) -> list[str]:
        net, mask = self._netmask(r)
        gw = (r.get("gateway") or "").strip()
        tail = f" {gw}" if gw else ""
        if self.vendor == "cisco":
            return [f"no ip route {net} {mask}{tail}"]
        return [f"undo ip route-static {net} {mask}{tail}"]

    def persist_note(self) -> str:
        if not self.can_write:
            return ("SSH 到网络设备只能在特权模式下执行查看命令；"
                    "改配置请用 Telnet 连接，或直接在终端里手动操作")
        return "写入前会自动进 configure terminal / system-view，改完 save 才长期生效"


def backend_for(device) -> RouteBackend:
    fam = getattr(device, "os_family", "") or ""
    if fam == "network":
        return NetworkRoute(None, device)
    if fam == "windows":
        return WindowsRoute(None, device)
    if fam in ("darwin", "freebsd", "bsd"):
        return BSDRoute(None, device)
    return LinuxRoute(None, device)


def backend_of(device, session) -> RouteBackend:
    b = backend_for(device)
    b.session = session
    return b


# ==================================================================== 改 / 设为默认
def edit_cmds(backend: RouteBackend, old: dict, new: dict) -> list[str]:
    """改一条路由 = 删旧的 + 加新的。

    没有平台通用的「就地改路由」命令（Windows 的 route change 要求目标已存在
    且不支持改接口），所以统一走 删+加，行为可预期。
    """
    err = backend.validate(new)
    if err:
        raise RouteError(err)
    return backend.del_cmds(old) + backend.add_cmds(new)


def default_cmds(backend: RouteBackend, r: dict) -> list[str]:
    """把这条路由变成默认路由（目标网段 0.0.0.0/0）。

    Linux 用 `ip route replace` 一步到位（原来有默认路由会被替换，
    不会报 File exists）；Windows 只能先删再加，所以两跳。
    """
    gw = (r.get("gateway") or "").strip()
    if not gw:
        raise RouteError("设为默认路由必须有网关")
    if isinstance(backend, LinuxRoute):
        parts = ["ip", "route", "replace", "default", "via", gw]
        if r.get("dev"):
            parts += ["dev", r["dev"]]
        if str(r.get("metric") or "").strip():
            parts += ["metric", str(r["metric"])]
        return [" ".join(parts)]
    if isinstance(backend, WindowsRoute):
        net, mask = "0.0.0.0", "0.0.0.0"
        return [f"route delete {net} mask {mask}",
                f"route -p add {net} mask {mask} {gw}"]
    if isinstance(backend, BSDRoute):
        return ["route -n change -default " + gw]
    raise RouteError(f"{backend.label} 不支持在界面里改默认路由")


def plan(backend: RouteBackend, action: str, route: dict,
         old: dict | None = None) -> list[str]:
    if action == "add":
        err = backend.validate(route)
        if err:
            raise RouteError(err)
        return backend.add_cmds(route)
    if action == "del":
        return backend.del_cmds(route)
    if action == "edit":
        return edit_cmds(backend, old or route, route)
    if action == "default":
        return default_cmds(backend, route)
    raise RouteError(f"未知操作 {action}")


def undo_cmds(backend: RouteBackend, action: str, old: dict,
              new: dict | None = None) -> tuple[list[str], str]:
    """撤销上一步。拼不出来就给 ([], 原因)，不猜。"""
    try:
        if action == "add" and new:
            return backend.del_cmds(new), "删掉刚加的那条"
        if action == "del" and old:
            return backend.add_cmds(old), "把删掉的那条加回来"
        if action in ("edit", "default") and old and new:
            return (backend.del_cmds(new) + backend.add_cmds(old),
                    "先删改后的，再把原来的加回来")
    except RouteError as e:
        return [], str(e)
    except Exception as e:  # noqa: BLE001
        return [], f"拼不出撤销命令：{e}"
    return [], "没有可撤销的上一步"


# ==================================================================== 执行
def apply(session, backend: RouteBackend, action: str, route: dict,
          timeout: int = 45, old: dict | None = None) -> tuple[int, str, str]:
    """action: add / del / edit / default。返回最后一条命令的结果。"""
    cmds = plan(backend, action, route, old=old)
    if isinstance(backend, NetworkRoute):
        return _apply_network(session, backend, cmds, timeout)
    rc, out, err = 0, "", ""
    for c in cmds:
        rc, o, e = session.run_admin(c, timeout=timeout)
        out += o
        err += e
        if rc != 0:
            break
    return rc, out.strip(), err.strip()


def _apply_network(session, backend: NetworkRoute, cmds: list[str],
                   timeout: int) -> tuple[int, str, str]:
    """网络设备改路由：必须先进配置视图，改完再退出来。

    Cisco 是 `configure terminal` / `end`，H3C 与华为是 `system-view` / `return`。
    这里要求一条**持久**会话：Telnet 本身就是，SSH 走 invoke_shell
    （core/ssh_shell.py）也一样能保持模式。做不到就由 can_write 挡掉。
    """
    if not backend.can_write:
        raise RouteError(backend.persist_note())
    cisco = backend.vendor == "cisco"
    enter = "configure terminal" if cisco else "system-view"
    leave = "end" if cisco else "return"
    out = ""
    session.run_admin(enter, timeout=timeout)
    rc = 0
    try:
        for c in cmds:
            rc, o, e = session.run_admin(c, timeout=timeout)
            out += o
            if rc != 0:
                break
    finally:
        try:
            session.run_admin(leave, timeout=timeout)
        except Exception:  # noqa: BLE001
            pass
    return rc, out.strip(), ""


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
