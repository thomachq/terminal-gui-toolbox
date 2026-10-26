# -*- coding: utf-8 -*-
"""隧道服务：设备上**实际装了什么、在跑什么、谁连着**，以及怎么开/关。

为什么不写死一张「支持的服务表」：同一套 OpenVPN 在群晖（VPNCenter 包）、
Debian（systemd）、OpenWrt（procd）上的进程名、配置路径、启停命令全不一样。
所以探测一律走「已知绝对路径优先 → /proc comm → 端口监听」，
页面只显示真的存在的那几个 —— 用户要的就是「根据设备实际安装的服务为准」。

一条硬规矩：**探测命令里不许出现服务名的裸字面量给 ps/grep 自匹配**。
`ps | grep openvpn` 会匹配到我们自己这条 `sh -c` 命令行，永远显示「在跑」。
这里全部读 `/proc/<pid>/comm`（进程名，sh 进程的 comm 是 "sh"），不误判。
"""
from __future__ import annotations

import re
import time

# ------------------------------------------------------------------ 服务表
class Svc:
    """一个隧道服务的静态特征。`comm` 是 /proc/<pid>/comm 里的名字。"""

    __slots__ = ("key", "name", "kind", "bins", "comm", "ports", "confs", "note")

    def __init__(self, key, name, kind, bins, comm, ports=(), confs=(), note=""):
        self.key = key
        self.name = name
        self.kind = kind          # openvpn / ipsec / l2tp / pptp / wireguard / ...
        self.bins = tuple(bins)
        self.comm = comm
        self.ports = tuple(ports)
        self.confs = tuple(confs)
        self.note = note

    def __repr__(self) -> str:
        return f"<Svc {self.key}>"


# 二进制路径都要给「已知绝对路径」：群晖的 SSH 登录 PATH 只有
# /usr/bin:/bin:/usr/sbin:/sbin，VPNCenter 的 openvpn 在 /var/packages 下面，
# `command -v openvpn` 找不到 ≠ 没装（这条在 docker 上已经吃过一次亏）。
SERVICES: tuple[Svc, ...] = (
    Svc("openvpn", "OpenVPN", "openvpn",
        ("/usr/sbin/openvpn", "/usr/local/sbin/openvpn",
         "/var/packages/VPNCenter/target/bin/openvpn",
         "/opt/bin/openvpn"),
        "openvpn", (1194,),
        ("/etc/openvpn/server.conf", "/etc/openvpn/server/server.conf",
         "/etc/openvpn/openvpn.conf",
         "/var/packages/VPNCenter/target/etc/openvpn/server.conf",
         "/var/packages/VPNCenter/target/etc/openvpn/openvpn.conf"),
        "证书/用户名口令两种认证都支持"),
    Svc("strongswan", "IPSec / IKEv2（strongSwan）", "ipsec",
        ("/usr/sbin/ipsec", "/usr/lib/ipsec/charon", "/usr/libexec/ipsec/charon",
         "/usr/local/libexec/ipsec/charon",
         "/var/packages/VPNCenter/target/sbin/ipsec"),
        "charon", (500, 4500),
        ("/etc/ipsec.conf", "/etc/strongswan.conf",
         "/var/packages/VPNCenter/target/etc/ipsec.conf"), ""),
    Svc("xl2tpd", "L2TP over IPSec（xl2tpd）", "l2tp",
        ("/usr/sbin/xl2tpd", "/usr/local/sbin/xl2tpd",
         "/var/packages/VPNCenter/target/sbin/xl2tpd"),
        "xl2tpd", (1701,),
        ("/etc/xl2tpd/xl2tpd.conf", "/etc/xl2tpd.conf"), ""),
    Svc("pptpd", "PPTP（pptpd）", "pptp",
        ("/usr/sbin/pptpd", "/usr/local/sbin/pptpd"),
        "pptpd", (1723,), ("/etc/pptpd.conf",), "明文协议，只建议内网用"),
    Svc("ocserv", "AnyConnect（ocserv）", "ocserv",
        ("/usr/sbin/ocserv", "/usr/local/sbin/ocserv"),
        "ocserv", (443,), ("/etc/ocserv/ocserv.conf",), ""),
    Svc("softether", "SoftEther VPN", "softether",
        ("/usr/local/vpnserver/vpnserver", "/opt/vpnserver/vpnserver"),
        "vpnserver", (443, 992, 5555), (), ""),
)

# WireGuard 没有常驻用户态进程（内核模块），单独判 /sys/class/net/*/wireguard
_WG_PROBE = (
    'echo "@wg $(ls -d /sys/class/net/*/wireguard 2>/dev/null '
    '| sed "s#/sys/class/net/##;s#/wireguard##" | tr "\\n" " ")"'
)


# ------------------------------------------------------------------ 探测
def probe_cmd() -> str:
    """一条能在 sh / ash / busybox 上跑通的探测命令（POSIX，不用 bash 特性）。"""
    parts = [
        # 进程名只从 comm 读：`ps | grep xxx` 会匹配到自己这条命令行，
        # 结果永远是「在跑」。comm 长度上限 15 字符，这些服务名都够短。
        'alive(){ for c in /proc/[0-9]*/comm; do '
        '[ "$(cat "$c" 2>/dev/null)" = "$1" ] && return 0; done; return 1; }',
        'ex(){ for f in $1; do [ -x "$f" ] && return 0; done; return 1; }',
        'echo "@os $(uname -s 2>/dev/null)"',
        # init 系统决定「启停用哪条命令」。写成顺序分支而不是一长串
        # `&& ... || ...`：后者在 ash 里结合性很坑，判断一错就整条静默走最右。
        'init_of(){ if [ -d /run/systemd/system ]; then echo systemd; '
        'elif [ -x /usr/syno/bin/synopkg ]; then echo syno; '
        'elif [ -f /etc/init.d/rc.common ] || [ -d /etc/rc.d ]; then echo openwrt; '
        'elif [ -d /etc/init.d ]; then echo sysv; else echo unknown; fi; }',
        'echo "@init $(init_of)"',
        # 群晖的套件状态比进程更准（VPNCenter 里面有 openvpn + xl2tpd 两个）
        '[ -x /usr/syno/bin/synopkg ] && '
        'echo "@synopkg $(/usr/syno/bin/synopkg status VPNCenter 2>/dev/null '
        '| head -3 | tr \'\\n\' \' \')"',
    ]
    for s in SERVICES:
        bins = " ".join(s.bins)
        parts.append(
            f"echo \"@svc {s.key} $(ex '{bins}' && echo 1 || echo 0) "
            f"$(alive {s.comm} && echo 1 || echo 0)\"")
    parts.append(_WG_PROBE)
    return "; ".join(parts)


def parse_probe(text: str) -> dict:
    """解析探测回显 → {"os":..., "init":..., "services":[{...}]}。"""
    out = {"os": "", "init": "", "synopkg": "", "services": []}
    seen = {s.key: s for s in SERVICES}
    extra: dict[str, dict] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line.startswith("@"):
            continue
        tag, _, rest = line.partition(" ")
        tag = tag[1:]
        if tag == "os":
            out["os"] = rest.strip()
        elif tag == "init":
            out["init"] = rest.strip()
        elif tag == "synopkg":
            out["synopkg"] = rest.strip()
        elif tag == "wg":
            for iface in rest.split():
                if iface.strip():
                    extra.setdefault("wireguard", {
                        "key": "wireguard", "name": "WireGuard",
                        "kind": "wireguard", "installed": True,
                        "active": True, "ifaces": []})
                    extra["wireguard"]["ifaces"].append(iface.strip())
        elif tag == "svc":
            bits = rest.split()
            if len(bits) < 3:
                continue
            key, inst, act = bits[0], bits[1], bits[2]
            s = seen.get(key)
            if s is None:
                continue
            out["services"].append({
                "key": key, "name": s.name, "kind": s.kind,
                "installed": inst == "1" or act == "1",
                "active": act == "1",
                "ports": list(s.ports), "confs": list(s.confs),
                "note": s.note, "ifaces": [],
            })
    # WireGuard 没有进程，installed 只能靠 /sys 判断
    if "wireguard" in extra:
        wg = extra["wireguard"]
        wg["ports"] = [51820]
        wg["confs"] = ["/etc/wireguard"]
        wg["note"] = "无用户态进程，靠内核模块"
        if wg["ifaces"]:
            wg["name"] = f"WireGuard（{', '.join(wg['ifaces'])}）"
        out["services"].append(wg)
    return out


def windows_probe_cmd() -> str:
    """Windows 侧（本机或跳板）：RRAS 管 L2TP/PPTP/SSTP/IKEv2，另有各家服务。

    整条 PowerShell 只用**单引号**：外层必须是双引号（cmd /c 的规矩），
    PowerShell 里再写 `\\"` 会先被 cmd 吃掉一层，实测直接变成
    「'...' 不是内部或外部命令」。单引号在 PowerShell 里是字面量，正好。
    """
    ps = (
        "foreach($n in @('RemoteAccess','IKEEXT','OpenVPNService',"
        "'OpenVPN Interactive Service','WireGuard','SoftEther VPN Server',"
        "'OpenVPNServ2')){"
        "$s=Get-Service -Name $n -ErrorAction SilentlyContinue;"
        "if($s){Write-Output ('@winsvc|'+$s.Name+'|'+$s.Status+'|'+$s.StartType)}"
        "};"
        "Write-Output ('@os|'+(Get-CimInstance Win32_OperatingSystem).Caption)"
    )
    return f'powershell -NoProfile -Command "{ps}"'


def parse_windows_probe(text: str) -> dict:
    out = {"os": "", "init": "windows", "services": []}
    name_map = {
        "RemoteAccess": ("rras", "路由和远程访问（L2TP/PPTP/SSTP）"),
        "IKEEXT": ("ikeext", "IKE 与 AuthIP（IKEv2）"),
        "OpenVPNService": ("openvpn", "OpenVPN"),
        "OpenVPN Interactive Service": ("openvpn", "OpenVPN"),
        "OpenVPNServ2": ("openvpn", "OpenVPN"),
        "WireGuard": ("wireguard", "WireGuard"),
        "SoftEther VPN Server": ("softether", "SoftEther VPN"),
    }
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("@os|"):
            out["os"] = line.split("|", 1)[1].strip()
            continue
        if not line.startswith("@winsvc|"):
            continue
        bits = line.split("|")
        if len(bits) < 4:
            continue
        key, status, start = bits[1].strip(), bits[2].strip(), bits[3].strip()
        mapped = name_map.get(key)
        if not mapped:
            continue
        # 同一个 kind 可能有多条（OpenVPN 有三个服务名），合并成一条
        for s in out["services"]:
            if s["key"] == mapped[0]:
                s["active"] = s["active"] or status.lower() == "running"
                s["installed"] = True
                s["winsvc"].append(key)
                break
        else:
            out["services"].append({
                "key": mapped[0], "name": mapped[1], "kind": mapped[0],
                "installed": True, "active": status.lower() == "running",
                "ports": [], "confs": [], "note": f"服务名 {key}（{start}）",
                "ifaces": [], "winsvc": [key],
            })
    return out


# ------------------------------------------------------------------ 谁连着
_STATUS_CANDIDATES = (
    "/etc/openvpn/openvpn-status.log",
    "/var/log/openvpn/openvpn-status.log",
    "/var/log/openvpn-status.log",
    "/etc/openvpn/server/openvpn-status.log",
    "/var/packages/VPNCenter/target/etc/openvpn/openvpn-status.log",
    "/var/packages/VPNCenter/target/var/openvpn-status.log",
    "/tmp/openvpn-status.log",
)


def clients_cmd(svc: dict) -> str:
    """按服务类型挑一条「列出在线连接」的命令。"""
    kind = svc.get("kind") or svc.get("key")
    if kind == "openvpn":
        cands = list(svc.get("status_files") or []) + list(_STATUS_CANDIDATES)
        quoted = " ".join(f"'{p}'" for p in cands)
        # 状态文件优先；没有就退回「谁在连 1194 端口」（至少知道有几个人）
        return (
            f"for f in {quoted}; do [ -r \"$f\" ] && "
            "{ echo \"@status $f\"; cat \"$f\"; exit 0; }; done; "
            "echo '@status none'; ss -tnup 2>/dev/null | grep -E ':1194\\b' "
            "|| netstat -tnup 2>/dev/null | grep -E ':1194\\b'")
    if kind == "wireguard":
        ifs = svc.get("ifaces") or []
        target = " ".join(ifs) if ifs else "all"
        return f"wg show {target} dump 2>/dev/null || wg show all dump 2>/dev/null"
    if kind == "ipsec":
        return ("ipsec statusall 2>/dev/null | head -120; "
                "echo '---'; swanctl --list-sas 2>/dev/null | head -80")
    if kind == "l2tp":
        return ("echo '@xl2tpd'; "
                "ls /var/run/xl2tpd/ 2>/dev/null; "
                "ss -tn 2>/dev/null | grep -E ':1701\\b' || "
                "netstat -tn 2>/dev/null | grep -E ':1701\\b'")
    if kind == "pptp":
        return ("ss -tn 2>/dev/null | grep -E ':1723\\b' || "
                "netstat -tn 2>/dev/null | grep -E ':1723\\b'")
    return "echo '@unsupported'"


def parse_clients(kind: str, text: str) -> list[dict]:
    """统一成 [{name, remote, virtual, since, rx, tx, state}]。"""
    rows: list[dict] = []
    body = text or ""
    if kind == "openvpn":
        for line in body.splitlines():
            s = line.strip()
            if s.startswith("CLIENT_LIST,") or s.startswith("CLIENT_LIST\t"):
                # 2.3 用逗号、2.4 默认用 tab，两种都认
                f = re.split(r"[,\t]", s)[1:]
                if len(f) < 4:
                    continue
                rows.append({
                    "name": f[0].strip(),
                    "remote": f[1].strip(),
                    "virtual": f[2].strip(),
                    "since": f[6].strip() if len(f) > 6 else "",
                    "rx": f[3].strip() if len(f) > 3 else "",
                    "tx": f[4].strip() if len(f) > 4 else "",
                    "state": "在线",
                })
    elif kind == "wireguard":
        for line in body.splitlines():
            f = line.split("\t")
            if len(f) < 8:
                continue
            # `wg show <if> dump` 的列序是固定的：
            # iface / pubkey / psk / endpoint / allowed-ips / handshake / rx / tx
            # 判断「在不在线」只能看 endpoint（没端点 = 从来没连上过）。
            rows.append({
                "name": (f[4] or f[1])[:24],
                "remote": "" if f[3] in ("(none)", "") else f[3],
                "virtual": f[4],
                "since": "" if f[5] in ("0", "") else f[5],
                "rx": f[6], "tx": f[7],
                "state": "在线" if f[3] not in ("(none)", "") else "无端点",
            })
    return rows


# ------------------------------------------------------------------ 启停
def control_cmds(svc: dict, action: str, init: str = "") -> list[str]:
    """action = start / stop / restart。不同 init 系统各写一套。"""
    key = svc.get("key") or ""
    kind = svc.get("kind") or key
    if init == "syno" or svc.get("syno"):
        pkg = "VPNCenter"
        act = {"start": "start", "stop": "stop",
               "restart": "restart"}.get(action, action)
        return [f"/usr/syno/bin/synopkg {act} {pkg}"]
    unit = {
        "openvpn": "openvpn@server",
        "strongswan": "strongswan-starter",
        "ipsec": "strongswan-starter",
        "xl2tpd": "xl2tpd",
        "pptp": "pptpd",
        "ocserv": "ocserv",
        "softether": "vpnserver",
        "wireguard": "wg-quick@wg0",
    }.get(kind, key)
    if init == "openwrt":
        name = {"openvpn": "openvpn", "wireguard": "network"}.get(kind, key)
        return [f"/etc/init.d/{name} {action}"]
    if init == "sysv":
        return [f"service {unit} {action}"]
    return [f"systemctl {action} {unit}"]


# ------------------------------------------------------------------ 客户端证书认证
CERT_MODES = (
    ("require", "必须客户端证书（最安全）"),
    ("optional", "有证书就用，没有也能用口令登录"),
    ("none", "不要求证书，只靠用户名口令"),
)
_CERT_KEYS = ("verify-client-cert", "client-cert-not-required",
              "username-as-common-name")


def cert_mode(conf_text: str) -> str:
    """从配置文本里读当前模式。"""
    lines = [ln.strip() for ln in (conf_text or "").splitlines()]
    for ln in lines:
        if ln.startswith("#"):
            continue
        if re.match(r"^client-cert-not-required\b", ln):
            return "none"
        m = re.match(r"^verify-client-cert\s+(\w+)", ln)
        if m:
            mode = m.group(1).lower()
            if mode in ("require", "optional", "none"):
                return mode
    # 2.4 之后默认就是 require（服务端没写这一行也是要求证书）
    return "require"


def cert_mode_cmds(conf_path: str, mode: str, init: str = "",
                   unit: str = "openvpn@server") -> list[str]:
    """改 server.conf 里的认证方式：备份 → 删旧指令 → 追加新指令 → 重启。

    三行必须一起删：`verify-client-cert` / `client-cert-not-required` /
    `username-as-common-name` 互相冲突，留着任何一行都会让新设置不生效
    （2.4 对冲突写法是「后出现的赢」，但顺序一变就翻车，不如先清干净）。
    """
    if mode not in ("require", "optional", "none"):
        raise ValueError(f"不认识的认证模式：{mode}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = f"{conf_path}.bak.{stamp}"
    # 一条 sed 一个 -e：BRE 里 `|` 不是「或」（那是 ERE），而 `\|` 只有
    # GNU sed 认、busybox 不认。多 -e 在两边都对，这是唯一稳的写法。
    deletes = " ".join(f"-e '/^[[:space:]]*{k}/d'" for k in _CERT_KEYS)
    cmds = [f"cp -p '{conf_path}' '{bak}'",
            f"sed -i {deletes} '{conf_path}'"]
    add = f"verify-client-cert {mode}"
    if mode == "none":
        # 不要求证书时必须让用户名当 CN，否则服务端拿不到用户名
        add += "\nusername-as-common-name"
    cmds.append(f"printf '%s\\n' '{add}' >> '{conf_path}'")
    if init == "syno":
        cmds.append("/usr/syno/bin/synopkg restart VPNCenter")
    elif init == "sysv":
        cmds.append(f"service {unit} restart")
    else:
        cmds.append(f"systemctl restart {unit}")
    cmds.append(f"echo 备份：{bak}")
    return cmds


def read_conf_cmd(conf_path: str) -> str:
    return f"cat '{conf_path}' 2>/dev/null"


def status_files_from_conf(conf_text: str) -> list[str]:
    """从 server.conf 里读出 `status` 指令指向的文件（第一手线索）。"""
    out = []
    for ln in (conf_text or "").splitlines():
        s = ln.strip()
        if s.startswith("status "):
            bits = s.split()
            if len(bits) >= 2 and not bits[1].isdigit():
                out.append(bits[1])
    return out


__all__ = ["SERVICES", "Svc", "probe_cmd", "parse_probe", "windows_probe_cmd",
           "parse_windows_probe", "clients_cmd", "parse_clients",
           "control_cmds", "CERT_MODES", "cert_mode", "cert_mode_cmds",
           "read_conf_cmd", "status_files_from_conf"]
