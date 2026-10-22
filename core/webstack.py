# -*- coding: utf-8 -*-
"""Web 服务探测与服务器 TLS 证书管理。

一块设备上常常同时跑着好几个 Web 服务（Nginx 反代 + Apache 老站点 + Tomcat 应用，
NAS 上还可能有内置的 Web Station）。所以这里的第一件事是**数清楚有几个**：

  kind        nginx / apache / iis / tomcat / caddy / lighttpd
  进程画像     ps 里认 comm 与 args（java 要认 catalina.base）
  监听端口     ss / netstat 里按进程名归到服务上（0.0.0.0:80、[::]:443）
  配置路径     nginx -V / apachectl -V / ps 里的 -Dcatalina.base / 众所周知的默认路径
  站点根       nginx 的 root、Apache 的 DocumentRoot
  证书         nginx 的 ssl_certificate(_key)、Apache 的 SSLCertificateFile/
               KeyFile/ChainFile、Tomcat 的 certificateFile/keystoreFile

TLS 部分只做「看得见、改得动、改坏了能退回去」三件事：

  cert_report()    本地用 cryptography 解析 PEM：主题/颁发者/SAN/有效期/剩余天数/
                   自签与否/链长度/密钥类型
  key_matches()    私钥与证书是不是一对（比对公钥，不落私钥明文）
  TLSDeployer      备份 → 写入 → 回读校验 sha256 → 配置检查 → 重载；
                   配置检查或重载失败就自动把 .bak 还原回去再重载一次
"""
from __future__ import annotations

import base64
import hashlib
import re
import shlex
import time
from dataclasses import asdict, dataclass, field

KIND_LABEL = {
    "nginx": "Nginx",
    "apache": "Apache",
    "iis": "IIS",
    "tomcat": "Tomcat",
    "caddy": "Caddy",
    "lighttpd": "Lighttpd",
    "other": "其他",
}

# 每类服务的「惯例」：默认配置、systemd 单元前缀、版本命令、配置检查、重载、站点根指令
_KIND_META = {
    "nginx": {
        "units": ("nginx.service", "nginx"),
        "config_paths": ("/etc/nginx/nginx.conf", "/usr/local/nginx/conf/nginx.conf"),
        "version": "nginx -v 2>&1",
        "test": "nginx -t",
        "reload": "nginx -s reload",
        "docroot": r"^\s*root\s+([^;]+);",
    },
    "apache": {
        "units": ("apache2.service", "httpd.service", "apache2", "httpd"),
        "config_paths": ("/etc/apache2/apache2.conf", "/etc/httpd/conf/httpd.conf"),
        "version": "apache2 -v 2>&1 || httpd -v 2>&1",
        "test": "apache2ctl configtest 2>&1 || httpd -t 2>&1",
        "reload": "apache2ctl graceful 2>&1 || httpd -k graceful 2>&1",
        "docroot": r"^\s*DocumentRoot\s+[\"']?([^\"'\s]+)",
    },
    "tomcat": {
        "units": ("tomcat.service", "tomcat9.service", "tomcat10.service", "tomcat8.service"),
        "config_paths": ("/opt/tomcat/conf/server.xml", "/usr/local/tomcat/conf/server.xml",
                         "/usr/share/tomcat/conf/server.xml"),
        "version": "",
        "test": "",
        "reload": "",
        "docroot": r"<Context[^>]*\bdocBase=\"([^\"]+)\"",
    },
    "caddy": {
        "units": ("caddy.service", "caddy"),
        "config_paths": ("/etc/caddy/Caddyfile",),
        "version": "caddy version 2>/dev/null",
        "test": "caddy validate --config {config} 2>&1",
        "reload": "caddy reload --config {config} 2>&1",
        "docroot": r"^\s*root\s*\*?\s+(\S+)",
    },
    "lighttpd": {
        "units": ("lighttpd.service", "lighttpd"),
        "config_paths": ("/etc/lighttpd/lighttpd.conf",),
        "version": "lighttpd -v 2>&1",
        "test": "lighttpd -tt -f {config} 2>&1",
        "reload": "systemctl reload lighttpd 2>&1",
        "docroot": r"^\s*server\.document-root\s*=\s*\"([^\"]+)\"",
    },
}

_STATUS_ABSENT = "absent"
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_UNSUPPORTED = "unsupported"


# ------------------------------------------------------------------ 数据
@dataclass
class WebService:
    kind: str = "other"
    label: str = ""
    unit: str = ""
    version: str = ""
    running: bool = False
    listen: list[str] = field(default_factory=list)
    config: str = ""
    docroot: str = ""
    user: str = ""
    certs: list[dict] = field(default_factory=list)
    note: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = KIND_LABEL.get(self.kind, self.kind)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.unit or self.label}"

    @property
    def title(self) -> str:
        ver = f" {self.version}" if self.version else ""
        return f"{self.label}{ver}"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        d["title"] = self.title
        d["state"] = "运行中" if self.running else "未运行"
        return d


# ------------------------------------------------------------------ 解析
def kind_of_process(comm: str, args: str = "") -> str:
    """从进程名 / 命令行认出这是哪类 Web 服务。"""
    c = (comm or "").strip().lower()
    a = (args or "").strip().lower()
    if c == "nginx" or "/nginx" in a or a.startswith("nginx:"):
        return "nginx"
    if c in ("apache2", "httpd", "apache", "httpd.worker", "httpd.prefork"):
        return "apache"
    if "apache2" in a or "/httpd " in a or "/httpd\x00" in a:
        return "apache"
    if "catalina" in a or "org.apache.catalina" in a or "tomcat" in a:
        return "tomcat"
    if c == "caddy" or "/caddy" in a:
        return "caddy"
    if c == "lighttpd" or "/lighttpd" in a:
        return "lighttpd"
    return ""


_SS_RE = re.compile(r"(\S+):(\d+)\s+\S+\s+users:\(\(")
_NETSTAT_RE = re.compile(r"(\S+):(\d+)\s+\S+\s+\S+\s+(\d+)/(\S+)")


def parse_listeners_detail(text: str) -> list[tuple[str, int, str, int]]:
    """ss / netstat → [(地址, 端口, 进程名, pid)]。

    pid 很关键：Tomcat 的监听进程叫 `java`，光看进程名认不出是哪个服务，
    必须拿 pid 回到 ps 里对命令行（-Dcatalina.base=...）。
    """
    out: list[tuple[str, int, str, int]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "LISTEN" not in line.upper():
            continue
        m = _SS_RE.search(line)
        if m:
            addr, port = m.group(1), m.group(2)
            proc, pid = "", 0
            mm = re.search(r"\(\"([^\"]+)\",pid=(\d+)", line)
            if mm:
                proc, pid = mm.group(1), int(mm.group(2))
            out.append((addr, int(port), proc, pid))
            continue
        m = _NETSTAT_RE.search(line)
        if m:
            out.append((m.group(1), int(m.group(2)), m.group(4), int(m.group(3))))
    return out


def parse_listeners(text: str) -> list[tuple[str, int, str]]:
    """同 parse_listeners_detail，但不带 pid（打印/断言时更干净）。"""
    return [(a, p, n) for a, p, n, _pid in parse_listeners_detail(text)]


def parse_processes(text: str) -> list[tuple[str, str, str]]:
    """`ps -eo user=,pid=,comm=,args=` → [(用户, 进程名, 命令行)]。"""
    return [(u, c, a) for u, _pid, c, a in parse_processes_detail(text)]


def parse_processes_detail(text: str) -> list[tuple[str, int, str, str]]:
    """`ps -eo user=,pid=,comm=,args=` → [(用户, pid, 进程名, 命令行)]。"""
    rows = []
    for line in (text or "").splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue
        user, pid, comm = parts[0], parts[1], parts[2]
        args = parts[3] if len(parts) > 3 else comm
        try:
            pid_i = int(pid)
        except ValueError:
            pid_i = 0
        rows.append((user, pid_i, comm, args))
    return rows


def parse_systemd_units(text: str) -> dict[str, str]:
    """`systemctl list-unit-files` → {单元名: 状态}（只留 .service）。"""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(".service"):
            out[parts[0]] = parts[1]
    return out


def parse_version(kind: str, text: str) -> str:
    blob = (text or "").strip()
    if not blob:
        return ""
    if kind == "nginx":
        m = re.search(r"nginx/([\d.]+)", blob)
        if m:
            return m.group(1)
    if kind == "apache":
        m = re.search(r"Apache/([\d.]+)", blob)
        if m:
            return m.group(1)
    if kind == "lighttpd":
        m = re.search(r"lighttpd/([\d.]+)", blob)
        if m:
            return m.group(1)
    if kind == "caddy":
        m = re.search(r"v?([\d]+\.[\d]+\.[\d]+)", blob)
        if m:
            return m.group(1)
    first = blob.splitlines()[0].strip()
    return first[:40]


def resolve_path(base_dir: str, path: str) -> str:
    p = (path or "").strip().strip('"').strip("'")
    if not p:
        return ""
    if p.startswith("/") or re.match(r"^[a-zA-Z]:[\\/]", p) or p.startswith("\\\\"):
        return p
    if not base_dir:
        return p
    return base_dir.rstrip("/") + "/" + p.lstrip("./")


def parse_tls_directives(kind: str, text: str, base_dir: str = "") -> list[dict]:
    """从配置片段的 grep 结果里配出「证书 / 私钥 / 链」三元组。"""
    certs: list[str] = []
    keys: list[str] = []
    chains: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^ssl_certificate\s+(\S+?)\s*;?\s*$", line)
        if m:
            certs.append(resolve_path(base_dir, m.group(1)))
            continue
        m = re.match(r"^ssl_certificate_key\s+(\S+?)\s*;?\s*$", line)
        if m:
            keys.append(resolve_path(base_dir, m.group(1)))
            continue
        m = re.match(r"^SSLCertificateFile\s+(\S+)\s*$", line)
        if m:
            certs.append(resolve_path(base_dir, m.group(1)))
            continue
        m = re.match(r"^SSLCertificateKeyFile\s+(\S+)\s*$", line)
        if m:
            keys.append(resolve_path(base_dir, m.group(1)))
            continue
        m = re.match(r"^SSLCertificateChainFile\s+(\S+)\s*$", line)
        if m:
            chains.append(resolve_path(base_dir, m.group(1)))
            continue
        m = re.search(r'certificateFile\s*=\s*"([^"]+)"', line)
        if m:
            certs.append(resolve_path(base_dir, m.group(1)))
            continue
        m = re.search(r'certificateKeyFile\s*=\s*"([^"]+)"', line)
        if m:
            keys.append(resolve_path(base_dir, m.group(1)))
            continue
        m = re.search(r'keystoreFile\s*=\s*"([^"]+)"', line)
        if m:
            certs.append(resolve_path(base_dir, m.group(1)))
            continue
        m = re.match(r"^tls\s+(\S+)", line)
        if m and kind == "caddy":
            parts = m.group(1).split()
            if parts:
                certs.append(resolve_path(base_dir, parts[0]))
            if len(parts) > 1:
                keys.append(resolve_path(base_dir, parts[1]))
            continue
        m = re.match(r'^ssl\.pemfile\s*=\s*"([^"]+)"', line)
        if m:
            certs.append(resolve_path(base_dir, m.group(1)))
            continue
        m = re.match(r'^ssl\.privkey\s*=\s*"([^"]+)"', line)
        if m:
            keys.append(resolve_path(base_dir, m.group(1)))

    seen: set[str] = set()
    out: list[dict] = []
    for i, cert in enumerate(certs):
        if not cert or cert in seen:
            continue
        seen.add(cert)
        out.append({
            "cert": cert,
            "key": keys[i] if i < len(keys) else (keys[0] if len(keys) == 1 else ""),
            "chain": chains[i] if i < len(chains) else "",
        })
        if len(out) >= 8:
            break
    return out


def parse_docroot(kind: str, text: str) -> str:
    pat = _KIND_META.get(kind, {}).get("docroot")
    if not pat:
        return ""
    for line in (text or "").splitlines():
        m = re.search(pat, line)
        if m:
            val = m.group(1).strip().strip('"').strip("'")
            if val and "$" not in val:
                return val
    return ""


# ------------------------------------------------------------------ 证书
def _x509():
    from cryptography import x509  # noqa: PLC0415
    return x509


def load_pem_certs(pem_text: str) -> list:
    """一段 PEM（可能带证书链）→ 证书列表，顺序与文件里一致。"""
    x509 = _x509()
    out = []
    for block in re.findall(
            r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
            pem_text or "", re.S):
        try:
            out.append(x509.load_pem_x509_certificate(block.encode()))
        except Exception:  # noqa: BLE001
            continue
    return out


def pem_to_der_b64(pem_text: str) -> str:
    body = re.sub(r"-----[^-]+-----", "", pem_text or "")
    return re.sub(r"\s+", "", body)


def cert_report(pem_text: str) -> dict:
    """解析 PEM 证书 → 给人看的报表。解析不了就返回 {'ok': False, 'error': ...}。"""
    certs = load_pem_certs(pem_text)
    if not certs:
        return {"ok": False, "error": "没有找到 PEM 证书内容"}
    leaf = certs[0]
    now = time.time()
    try:
        not_before = leaf.not_valid_before_utc.timestamp()
        not_after = leaf.not_valid_after_utc.timestamp()
    except AttributeError:  # cryptography < 42
        not_before = leaf.not_valid_before.replace(tzinfo=None).timestamp()
        not_after = leaf.not_valid_after.replace(tzinfo=None).timestamp()
    days_left = int((not_after - now) // 86400)

    def _name(n) -> str:
        try:
            return n.rfc4514_string()
        except Exception:  # noqa: BLE001
            return str(n)

    cn = ""
    try:
        from cryptography.x509.oid import NameOID  # noqa: PLC0415
        for attr in leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME):
            cn = attr.value
            break
    except Exception:  # noqa: BLE001
        pass

    san: list[str] = []
    try:
        from cryptography.x509 import DNSName, IPAddress  # noqa: PLC0415
        from cryptography.x509.oid import ExtensionOID  # noqa: PLC0415
        ext = leaf.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        san = [str(v) for v in ext.value.get_values_for_type(DNSName)]
        try:
            san += [v.exploded for v in ext.value.get_values_for_type(IPAddress)]
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        san = []

    try:
        pub = leaf.public_key()
        key_type = pub.__class__.__name__.replace("PublicKey", "").upper()
        bits = getattr(pub, "key_size", 0)
    except Exception:  # noqa: BLE001
        key_type, bits = "", 0

    return {
        "ok": True,
        "count": len(certs),
        "subject": _name(leaf.subject),
        "cn": cn,
        "issuer": _name(leaf.issuer),
        "serial": format(leaf.serial_number, "x"),
        "not_before": time.strftime("%Y-%m-%d %H:%M", time.localtime(not_before)),
        "not_after": time.strftime("%Y-%m-%d %H:%M", time.localtime(not_after)),
        "days_left": days_left,
        "expired": days_left < 0,
        "san": san,
        "self_signed": leaf.subject == leaf.issuer,
        "chain": len(certs) - 1,
        "key_type": f"{key_type} {bits}".strip(),
        "sig": leaf.signature_hash_algorithm.name.upper()
        if leaf.signature_hash_algorithm else "",
    }


def key_matches(cert_pem: str, key_pem: str) -> bool:
    """证书与私钥是不是一对。只比对公钥，不保留私钥对象。"""
    certs = load_pem_certs(cert_pem)
    if not certs or not (key_pem or "").strip():
        return False
    try:
        from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
        priv = serialization.load_pem_private_key(
            key_pem.encode(), password=None)
        pub = priv.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        want = certs[0].public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        return pub == want
    except Exception:  # noqa: BLE001
        return False


def is_private_key_pem(text: str) -> bool:
    return bool(re.search(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----", text or ""))


def is_cert_pem(text: str) -> bool:
    return "-----BEGIN CERTIFICATE-----" in (text or "")


# ------------------------------------------------------------------ 扫描
def _need_elevation():
    try:
        from core.session import NeedElevation  # noqa: PLC0415
        return NeedElevation
    except Exception:  # noqa: BLE001
        return ()


class WebScanner:
    def __init__(self, session, device=None):
        self.session = session
        self.device = device
        self.family = (getattr(device, "os_family", "") or "").lower()
        self.status = STATUS_OK
        self.detail = ""
        self.log: list[str] = []

    # -------------------------------------------------- 底层
    def _run(self, cmd: str, timeout: int = 20, admin: bool = False) -> str:
        s = self.session
        if s is None:
            return ""
        elev = _need_elevation()
        try:
            if admin and hasattr(s, "run_admin"):
                rc, out, err = s.run_admin(cmd, timeout=timeout)
            else:
                rc, out, err = s.run(cmd, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            # 缺提权密码要往上抛，交给界面弹窗问一次；其余一律当「没读到」
            if elev and isinstance(e, elev):
                raise
            return ""
        self.log.append(cmd)
        return out or ""

    def _read(self, path: str, timeout: int = 20) -> str:
        """读设备上的文本文件：先普通读，读不到再试提权读。"""
        if not path:
            return ""
        q = shlex.quote(path)
        out = self._run(f"cat {q} 2>/dev/null", timeout=timeout)
        if out.strip():
            return out
        return self._run(f"cat {q} 2>/dev/null", timeout=timeout, admin=True)

    # -------------------------------------------------- 入口
    def scan(self) -> dict:
        if self.session is None:
            self.status, self.detail = STATUS_UNSUPPORTED, "尚未连接设备"
            return self.result([])
        if self.family == "network":
            self.status = STATUS_UNSUPPORTED
            self.detail = "网络设备（交换机 / 路由器）没有可管理的 Web 服务"
            return self.result([])
        if self.family == "windows":
            return self.result(self._scan_windows())
        return self.result(self._scan_posix())

    def result(self, services: list[WebService]) -> dict:
        if self.status == STATUS_OK and not services:
            self.status = STATUS_EMPTY
            self.detail = self.detail or "没有发现 Web 服务"
        return {
            "status": self.status,
            "detail": self.detail,
            "count": len(services),
            "services": [s.as_dict() for s in services],
            "commands": self.log[-400:],
        }

    # -------------------------------------------------- POSIX
    def _scan_posix(self) -> list[WebService]:
        ps_text = self._run(
            "ps -eo user=,pid=,comm=,args= 2>/dev/null || "
            "ps ax -o user=,pid=,comm=,args= 2>/dev/null")
        listen_text = self._run(
            "(ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null || "
            "netstat -an 2>/dev/null)")
        units = parse_systemd_units(self._run(
            "systemctl list-unit-files --type=service --no-legend --plain 2>/dev/null"))

        procs = parse_processes_detail(ps_text)
        found: dict[str, WebService] = {}
        pid_kind: dict[int, str] = {}
        for user, pid, comm, args in procs:
            kind = kind_of_process(comm, args)
            if not kind:
                continue
            if pid:
                pid_kind[pid] = kind
            svc = found.get(kind)
            if svc is None:
                svc = WebService(kind=kind, user=user)
                found[kind] = svc
            if not svc.user or svc.user in ("root", "www-data", "apache", "nginx"):
                svc.user = user
            if kind == "tomcat" and not svc.config:
                m = re.search(r"-Dcatalina\.base=(\S+)", args or "")
                if m:
                    svc.config = m.group(1).rstrip("/") + "/conf/server.xml"

        # 进程看不到（权限 / 精简 ps）时，用配置文件与命令兜底
        if not found:
            self._probe_by_binaries(found)

        listeners = parse_listeners_detail(listen_text)
        for (addr, port, proc, pid) in listeners:
            kind = pid_kind.get(pid) or kind_of_process(proc, proc)
            if not kind:
                continue
            svc = found.setdefault(kind, WebService(kind=kind))
            item = f"{port}"
            if addr not in ("0.0.0.0", "*", "::", "[::]", ":::"):
                item = f"{addr}:{port}"
            if item not in svc.listen:
                svc.listen.append(item)

        # systemd 补齐单元名与状态
        for kind, svc in found.items():
            meta = _KIND_META.get(kind, {})
            for unit in meta.get("units", ()):
                name = unit if unit.endswith(".service") else f"{unit}.service"
                if name in units or unit in units:
                    svc.unit = name
                    break
            state = ""
            if svc.unit:
                state = self._run(
                    f"systemctl is-active {shlex.quote(svc.unit)} 2>/dev/null").strip()
                svc.running = state == "active"
            elif kind == "tomcat":
                svc.running = True
            else:
                svc.running = bool(svc.listen)

        for kind, svc in found.items():
            meta = _KIND_META.get(kind, {})
            if meta.get("version"):
                svc.version = parse_version(kind, self._run(meta["version"]))
            if not svc.config:
                svc.config = self._guess_config(kind)
            if svc.config:
                svc.certs = self._find_tls(kind, svc.config)
                svc.docroot = parse_docroot(kind, self._read(svc.config, timeout=15))
            svc.listen.sort(key=lambda p: int(re.sub(r"\D", "", p) or 0))

        order = ["nginx", "apache", "iis", "tomcat", "caddy", "lighttpd", "other"]
        return sorted(found.values(), key=lambda s: order.index(s.kind)
                      if s.kind in order else 99)

    def _probe_by_binaries(self, found: dict[str, WebService]) -> None:
        """进程列表不可用时，按命令存在性与默认配置路径兜底。"""
        for kind, meta in _KIND_META.items():
            probe = {"nginx": "command -v nginx", "apache": "command -v apache2 || command -v httpd",
                     "tomcat": "ls -d /opt/tomcat* /usr/local/tomcat* 2>/dev/null",
                     "caddy": "command -v caddy", "lighttpd": "command -v lighttpd"}.get(kind)
            if not probe:
                continue
            out = self._run(f"{probe} 2>/dev/null").strip()
            if not out:
                continue
            svc = WebService(kind=kind)
            svc.config = self._guess_config(kind)
            svc.note = "仅在命令行里发现，未见运行中的进程"
            found[kind] = svc

    def _guess_config(self, kind: str) -> str:
        cands = _KIND_META.get(kind, {}).get("config_paths", ())
        if not cands:
            return ""
        q = " ".join(shlex.quote(c) for c in cands)
        out = self._run(f"ls -1 {q} 2>/dev/null").strip()
        if out:
            return out.splitlines()[0].strip()
        if kind == "tomcat":
            base = self._run(
                "ls -d /opt/tomcat* /usr/local/tomcat* 2>/dev/null").strip()
            if base:
                return base.splitlines()[0].rstrip("/") + "/conf/server.xml"
        return ""

    def _find_tls(self, kind: str, config: str) -> list[dict]:
        """在配置目录里找证书指令。目录不可读时退化成只读主配置。"""
        import os as _os
        conf_dir = _os.path.dirname(config) or "/etc"
        pats = {
            "nginx": r"ssl_certificate(_key)?[[:space:]]+[^;]+",
            "apache": r"(SSLCertificate(File|KeyFile|ChainFile))[[:space:]]+\S+",
            "tomcat": r"(certificateFile|certificateKeyFile|keystoreFile)[[:space:]]*=[[:space:]]*\"[^\"]*\"",
            "caddy": r"^\s*tls\s+\S+",
            "lighttpd": r"ssl\.(pemfile|privkey)[[:space:]]*=[[:space:]]*\"[^\"]*\"",
        }.get(kind)
        if not pats:
            return []
        out = self._run(
            f"grep -rhoE {shlex.quote(pats)} {shlex.quote(conf_dir)} 2>/dev/null | head -40")
        if not out.strip():
            out = self._run(
                f"grep -hoE {shlex.quote(pats)} {shlex.quote(config)} 2>/dev/null | head -40")
        # Tomcat 的 certificateFile 是相对 $CATALINA_BASE 写的（conf/xxx.crt），
        # 而配置在 <base>/conf/server.xml —— 基准目录要往上退一层，否则变成 conf/conf。
        base = conf_dir
        if kind == "tomcat":
            base = _os.path.dirname(conf_dir.rstrip("/")) or conf_dir
        return parse_tls_directives(kind, out, base)

    # -------------------------------------------------- Windows
    def _scan_windows(self) -> list[WebService]:
        ps = ("powershell -NoProfile -NonInteractive -Command ")
        out: list[WebService] = []

        svc_text = self._run(
            ps + shlex.quote(
                "Get-Service | Where-Object {$_.Name -match "
                "'^(W3SVC|W3SVC-WP|nginx|Apache|httpd|Tomcat|Caddy|lighttpd)'} | "
                "ForEach-Object { \"$($_.Name)|$($_.Status)\" }"), timeout=30)
        states: dict[str, str] = {}
        for line in (svc_text or "").splitlines():
            if "|" in line:
                name, st = line.split("|", 1)
                states[name.strip()] = st.strip()

        if "W3SVC" in states:
            iis = WebService(kind="iis", unit="W3SVC",
                             running=states.get("W3SVC", "").lower().startswith("running"))
            sites = self._run(
                ps + shlex.quote(
                    "if (Get-Command appcmd.exe -ErrorAction SilentlyContinue) { "
                    "& \"$env:windir\\system32\\inetsrv\\appcmd.exe\" list site }"),
                timeout=30)
            bind = self._run(
                ps + shlex.quote(
                    "& \"$env:windir\\system32\\inetsrv\\appcmd.exe\" list site "
                    "/text:name,bindings,physicalPath"), timeout=30)
            ports = re.findall(r":(\d{2,5}):", bind or "") + re.findall(
                r":(\d{2,5}):", sites or "")
            iis.listen = sorted({p for p in ports} | ({"80"} if sites else set()),
                                key=lambda p: int(p))
            iis.version = self._iis_version()
            iis.config = "%windir%\\System32\\inetsrv\\config\\applicationHost.config"
            iis.note = "IIS 的证书由 http.sys 绑定管理（netsh http show sslcert）"
            iis.certs = self._iis_certs()
            out.append(iis)

        for name, st in states.items():
            kind = kind_of_process(name, name)
            if not kind:
                continue
            svc = WebService(kind=kind, unit=name,
                             running=st.lower().startswith("running"))
            svc.config = self._windows_config(kind)
            if svc.config:
                svc.certs = self._find_tls_windows(kind, svc.config)
            out.append(svc)
        return out

    def _iis_version(self) -> str:
        ps = ("powershell -NoProfile -NonInteractive -Command ")
        out = self._run(
            ps + shlex.quote(
                "(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\InetStp' "
                "-ErrorAction SilentlyContinue).VersionString"), timeout=20)
        return (out or "").strip()

    def _iis_certs(self) -> list[dict]:
        """https 绑定 → 证书指纹 → 证书库里的详情。"""
        ps = ("powershell -NoProfile -NonInteractive -Command ")
        out = self._run(
            ps + shlex.quote(
                "& \"$env:windir\\system32\\inetsrv\\appcmd.exe\" list site "
                "/text:name,bindings"), timeout=30)
        thumbs: list[str] = []
        for line in (out or "").splitlines():
            if "https/" not in line.lower():
                continue
            for t in re.findall(r"certificateHash=([0-9A-Fa-f]{40})|([0-9A-Fa-f]{40})", line):
                thumbs.append((t[0] or t[1]).upper())
        netsh = self._run("netsh http show sslcert", timeout=20, admin=True)
        for m in re.finditer(r"Certificate Hash\s*:\s*([0-9a-fA-F]{40})", netsh or ""):
            thumbs.append(m.group(1).upper())
        uniq: list[str] = []
        for t in thumbs:
            if t and t not in uniq:
                uniq.append(t)
        detail = self._run(
            ps + shlex.quote(
                ";".join(f"$c=Get-ChildItem Cert:\\LocalMachine\\My\\{t} "
                         f"-ErrorAction SilentlyContinue; if($c)"
                         f"{{\"$($c.Thumbprint)|$($c.Subject)|$($c.NotAfter)\"}}"
                         for t in uniq[:6])), timeout=30)
        certs = []
        for line in (detail or "").splitlines():
            parts = line.split("|")
            if len(parts) >= 3:
                certs.append({"cert": f"Cert:\\LocalMachine\\My\\{parts[0].strip()}",
                              "key": "", "chain": "",
                              "subject": parts[1].strip(),
                              "not_after": parts[2].strip()})
        if not certs and uniq:
            certs = [{"cert": f"Cert:\\LocalMachine\\My\\{t}", "key": "", "chain": ""}
                     for t in uniq]
        return certs

    def _windows_config(self, kind: str) -> str:
        ps = ("powershell -NoProfile -NonInteractive -Command ")
        cands = {
            "nginx": ["C:\\nginx\\conf\\nginx.conf", "C:\\Program Files\\nginx\\conf\\nginx.conf"],
            "apache": ["C:\\Apache24\\conf\\httpd.conf",
                       "C:\\Program Files\\Apache24\\conf\\httpd.conf"],
            "tomcat": ["C:\\Program Files\\Apache Software Foundation\\Tomcat\\conf\\server.xml"],
            "caddy": ["C:\\Program Files\\Caddy\\Caddyfile"],
            "lighttpd": ["C:\\lighttpd\\conf\\lighttpd.conf"],
        }.get(kind, [])
        if not cands:
            return ""
        quoted = ",".join(f"'{c}'" for c in cands)
        out = self._run(
            ps + shlex.quote(f"@({quoted}) | Where-Object {{Test-Path $_}} | "
                             f"Select-Object -First 1"), timeout=20)
        return (out or "").strip()

    def _find_tls_windows(self, kind: str, config: str) -> list[dict]:
        ps = ("powershell -NoProfile -NonInteractive -Command ")
        pat = {"nginx": "ssl_certificate", "apache": "SSLCertificateFile",
               "tomcat": "certificateFile", "caddy": "^\\s*tls ",
               "lighttpd": "ssl.pemfile"}.get(kind, "")
        if not pat:
            return []
        out = self._run(
            ps + shlex.quote(
                f"Get-ChildItem -Path (Split-Path '{config}') -Recurse "
                f"-Include *.conf,*.xml,Caddyfile -ErrorAction SilentlyContinue | "
                f"Select-String -Pattern '{pat}' | "
                f"ForEach-Object {{ $_.Line.Trim() }}"), timeout=40)
        import os as _os
        base = _os.path.dirname(config)
        return parse_tls_directives(kind, out, base)


# ------------------------------------------------------------------ 部署
def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


class TLSDeployer:
    """把新证书写到服务在用的路径上，改坏了自动退回。

    顺序是固定的：备份 → 写入 → 回读校验 → 配置检查 → 重载。
    任一步失败都按相反顺序还原并再重载一次，绝不留半套文件。
    """

    def __init__(self, session, service, runner=None):
        self.session = session
        self.service = service          # dict 或 WebService
        self.runner = runner or self._default_runner
        self.steps: list[dict] = []

    # -------------------------------------------------- 底层
    def _default_runner(self, cmd: str, admin: bool) -> tuple[int, str, str]:
        s = self.session
        if s is None:
            return 1, "", "尚未连接设备"
        elev = _need_elevation()
        try:
            if admin and hasattr(s, "run_admin"):
                return s.run_admin(cmd, timeout=90)
            return s.run(cmd, timeout=90)
        except Exception as e:  # noqa: BLE001
            if elev and isinstance(e, elev):
                raise
            return 1, "", str(e)

    def _step(self, name: str, cmd: str, admin: bool = True) -> tuple[int, str, str]:
        rc, out, err = self.runner(cmd, admin)
        self.steps.append({"name": name, "cmd": cmd, "rc": rc,
                           "out": (out or "").strip()[:400],
                           "err": (err or "").strip()[:400]})
        return rc, out, err

    def _kind(self) -> str:
        return getattr(self.service, "kind", "") or (
            self.service.get("kind", "") if isinstance(self.service, dict) else "")

    def _attr(self, name: str, default=""):
        if isinstance(self.service, dict):
            return self.service.get(name, default)
        return getattr(self.service, name, default)

    # -------------------------------------------------- 部署
    def deploy(self, cert_text: str, key_text: str, cert_path: str = "",
               key_path: str = "", reload: bool = True) -> dict:
        self.steps = []
        kind = self._kind()
        if kind in ("iis", ""):
            return {"ok": False, "steps": [], "detail":
                    "IIS 的证书由 http.sys 绑定管理，请在 IIS 管理器导入 PFX 后"
                    "重新绑定 https（netsh http update sslcert）"}

        cert_path = (cert_path or "").strip()
        key_path = (key_path or "").strip()
        if not cert_path or not key_path:
            pairs = self._attr("certs", []) or []
            if pairs:
                cert_path = cert_path or str(pairs[0].get("cert") or "")
                key_path = key_path or str(pairs[0].get("key") or "")
        if not cert_path or not key_path:
            return {"ok": False, "steps": [], "detail":
                    "不知道该写到哪个文件：请填证书路径和私钥路径"}

        ts = time.strftime("%Y%m%d-%H%M%S")
        backups: list[tuple[str, str]] = []
        for path in (cert_path, key_path):
            q = shlex.quote(path)
            rc, out, _ = self._step(
                "备份", f"if [ -f {q} ]; then cp -a {q} {q}.bak-{ts} && echo {q}.bak-{ts}; "
                        f"fi")
            bak = (out or "").strip().splitlines()[-1] if (out or "").strip() else ""
            if bak:
                backups.append((path, bak))

        # 写入：base64 走管道，绕开引号 / 换行 / 特殊字符
        for path, text, mode in ((cert_path, cert_text, "644"),
                                 (key_path, key_text, "600")):
            b64 = base64.b64encode((text or "").encode()).decode()
            cmd = (f"printf '%s' {shlex.quote(b64)} | base64 -d > {shlex.quote(path)} "
                   f"&& chmod {mode} {shlex.quote(path)}")
            rc, out, err = self._step("写入", cmd)
            if rc != 0:
                self._rollback(backups)
                return {"ok": False, "steps": self.steps,
                        "detail": f"写入 {path} 失败：{(err or out).strip()[:200]}"}

        # 回读校验
        for path, text in ((cert_path, cert_text), (key_path, key_text)):
            rc, out, _ = self._step("校验", f"sha256sum {shlex.quote(path)}")
            got = (out or "").split(" ")[0].strip().lower()
            if rc == 0 and got and got != sha256_text(text):
                self._rollback(backups)
                return {"ok": False, "steps": self.steps,
                        "detail": f"{path} 写入后校验不一致，已还原"}

        if reload:
            rc, out, err = self._test_config()
            if rc != 0:
                self._rollback(backups, reload=True)
                return {"ok": False, "steps": self.steps,
                        "detail": f"配置检查未通过，已还原：{((err or out) or '').strip()[:220]}"}
            rc, out, err = self._reload()
            if rc != 0:
                self._rollback(backups, reload=True)
                return {"ok": False, "steps": self.steps,
                        "detail": f"重载失败，已还原：{((err or out) or '').strip()[:220]}"}

        return {"ok": True, "steps": self.steps,
                "detail": f"已部署到 {cert_path}，共 {len(self.steps)} 步",
                "cert": cert_path, "key": key_path, "backups": backups}

    def _test_config(self) -> tuple[int, str, str]:
        kind = self._kind()
        config = self._attr("config", "")
        meta = _KIND_META.get(kind, {})
        if kind == "nginx":
            return self._step("配置检查", "nginx -t 2>&1")
        if kind == "apache":
            return self._step("配置检查", "apache2ctl configtest 2>&1 || httpd -t 2>&1")
        tmpl = meta.get("test", "")
        if not tmpl:
            return 0, "", ""
        return self._step("配置检查", tmpl.format(config=shlex.quote(config)))

    def _reload(self) -> tuple[int, str, str]:
        kind = self._kind()
        unit = self._attr("unit", "")
        if unit:
            return self._step("重载", f"systemctl reload {shlex.quote(unit)} 2>&1")
        meta = _KIND_META.get(kind, {})
        tmpl = meta.get("reload", "")
        if not tmpl:
            return 0, "", ""
        return self._step("重载", tmpl.format(config=shlex.quote(self._attr("config", ""))))

    def _rollback(self, backups: list[tuple[str, str]], reload: bool = False) -> None:
        for path, bak in backups:
            self._step("还原", f"cp -a {shlex.quote(bak)} {shlex.quote(path)}")
        if reload:
            self._reload()

    def restore_latest_backup(self, cert_path: str, key_path: str) -> dict:
        """把最近一次的 .bak-* 退回原位（界面上「恢复上一次备份」用）。"""
        self.steps = []
        done = []
        for path in (cert_path, key_path):
            if not path:
                continue
            rc, out, _ = self._step(
                "查找备份",
                f"ls -1t {shlex.quote(path)}.bak-* 2>/dev/null | head -1")
            bak = (out or "").strip().splitlines()
            if not bak:
                continue
            rc, out, err = self._step(
                "还原", f"cp -a {shlex.quote(bak[0])} {shlex.quote(path)}")
            if rc == 0:
                done.append(f"{path} ← {bak[0]}")
        if not done:
            return {"ok": False, "steps": self.steps, "detail": "没有找到备份文件"}
        rc, out, err = self._reload()
        return {"ok": rc == 0, "steps": self.steps,
                "detail": "；".join(done) + ("" if rc == 0 else "（重载失败，请手动检查）")}


# ------------------------------------------------------------------ 小工具
def summarize(services: list[dict]) -> str:
    if not services:
        return "没有发现 Web 服务"
    names = [f"{s.get('label') or s.get('kind')}" for s in services]
    return f"发现 {len(services)} 个：{' / '.join(names)}"


def expiring(certs: list[dict], days: int = 30) -> list[dict]:
    """从 cert_report 的结果里挑出快到期的。"""
    return [c for c in certs if c.get("ok") and not c.get("expired")
            and int(c.get("days_left") or 0) <= days]


# ================================================================== 站点与域名
# 一个 Web 服务下面通常挂着好几个站点（nginx 的 server 块 / Apache 的
# VirtualHost）。宝塔把它们做成列表 + 域名绑定，这里照做，但**不假设**
# 配置文件的组织方式：nginx 用 `nginx -T` 把全部有效配置连同文件名一起吐出来，
# 文件名就在 `# configuration file xxx:` 这行标记里 —— 这是唯一可靠的信息源，
# 靠猜 include 路径迟早猜错。

_NGINX_MARK = re.compile(r"^#\s*configuration file\s+(.+?):\s*$")


@dataclass
class Site:
    """一个站点（nginx server 块 / Apache VirtualHost / IIS site）。"""

    kind: str
    file: str
    index: int              # 在该文件里的第几个块（0 起）
    names: list[str] = field(default_factory=list)
    listen: list[str] = field(default_factory=list)
    root: str = ""
    ssl: str = ""
    line: int = 0
    note: str = ""

    @property
    def primary(self) -> str:
        return self.names[0] if self.names else "（未命名）"

    def as_row(self) -> dict:
        tone = "warn" if not self.names else ("ok" if self.ssl else "brand")
        return {
            "name": self.primary,
            "aliases": ", ".join(self.names[1:]) or "—",
            "listen": ", ".join(self.listen) or "—",
            "root": self.root or "—",
            "ssl": "有" if self.ssl else "无",
            "file": self.file,
            "line": str(self.line or ""),
            "_tone": tone,
            "_index": self.index,
            "_file": self.file,
            "_kind": self.kind,
        }


def split_config_dump(text: str) -> dict[str, str]:
    """把 `# configuration file /path:` 分段的输出拆成 {路径: 正文}。

    nginx -T 与我们的 apache 兜底命令都用这个格式，所以一套解析通吃。
    """
    files: dict[str, str] = {}
    cur = ""
    buf: list[str] = []
    for raw in (text or "").splitlines():
        m = _NGINX_MARK.match(raw.rstrip())
        if m:
            if cur:
                files[cur] = "\n".join(buf).strip("\n")
            cur, buf = m.group(1).strip(), []
            continue
        if cur:
            buf.append(raw)
    if cur:
        files[cur] = "\n".join(buf).strip("\n")
    return files


def _strip_comment(line: str) -> str:
    """去掉引号之外的 `#` 之后的注释，再统计花括号才不会被注释带偏。"""
    out: list[str] = []
    quote = ""
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out)


def nginx_server_blocks(text: str) -> list[tuple[int, int]]:
    """→ [(起始行, 结束行)]，0 起、含首尾。只认 `server {`，不认 location。"""
    blocks: list[tuple[int, int]] = []
    stack: list[int] = []
    lines = (text or "").splitlines()
    for i, raw in enumerate(lines):
        body = _strip_comment(raw)
        for ch in body:
            if ch == "{":
                stack.append(i)
            elif ch == "}" and stack:
                st = stack.pop()
                head = lines[st].strip()
                if re.match(r"^server\b", head):
                    blocks.append((st, i))
    return blocks


_DIRECTIVE = {
    "listen": re.compile(r"^\s*listen\s+([^;]+);", re.I),
    "server_name": re.compile(r"^\s*server_name\s+([^;]+);", re.I),
    "root": re.compile(r"^\s*root\s+([^;]+);", re.I),
    "ssl_certificate": re.compile(r"^\s*ssl_certificate\s+([^;]+);", re.I),
}


def parse_nginx_sites(files: dict[str, str]) -> list[Site]:
    sites: list[Site] = []
    for path, text in (files or {}).items():
        blocks = nginx_server_blocks(text)
        lines = text.splitlines()
        for idx, (st, en) in enumerate(blocks):
            body = lines[st:en + 1]
            got: dict[str, list[str]] = {}
            for line in body:
                for key, pat in _DIRECTIVE.items():
                    m = pat.match(line)
                    if m:
                        got.setdefault(key, []).append(m.group(1).strip())
            names = " ".join(got.get("server_name", [])).split()
            sites.append(Site(
                kind="nginx", file=path, index=idx,
                names=[n for n in names if n != "_"],
                listen=got.get("listen", []),
                root=(got.get("root") or [""])[0],
                ssl=(got.get("ssl_certificate") or [""])[0],
                line=st + 1))
    return sites


def set_nginx_names(text: str, index: int, names: list[str]) -> str:
    """改写第 index 个 server 块的 server_name。没有这一行就补一行。"""
    blocks = nginx_server_blocks(text)
    if index < 0 or index >= len(blocks):
        raise ValueError(f"这个文件里没有第 {index + 1} 个 server 块")
    st, en = blocks[index]
    lines = (text or "").splitlines()
    new = " ".join(n for n in names if n) or "_"
    for i in range(st, min(en, len(lines) - 1) + 1):
        m = _DIRECTIVE["server_name"].match(lines[i])
        if m:
            indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            lines[i] = f"{indent}server_name {new};"
            return _join_like(text, lines)
    head_indent = lines[st][:len(lines[st]) - len(lines[st].lstrip())]
    lines.insert(st + 1, f"{head_indent}    server_name {new};")
    return _join_like(text, lines)


def _join_like(original: str, lines: list[str]) -> str:
    body = "\n".join(lines)
    return body + ("\n" if (original or "").endswith("\n") else "")


_VHOST_OPEN = re.compile(r"^\s*<VirtualHost\s+([^>]+)>", re.I)
_VHOST_CLOSE = re.compile(r"^\s*</VirtualHost>", re.I)
_APACHE_NAME = re.compile(r"^\s*ServerName\s+(\S+)", re.I)
_APACHE_ALIAS = re.compile(r"^\s*ServerAlias\s+([^#]+)", re.I)
_APACHE_ROOT = re.compile(r"^\s*DocumentRoot\s+\"?([^\"\s]+)\"?", re.I)


def apache_vhost_blocks(text: str) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    start = -1
    for i, raw in enumerate((text or "").splitlines()):
        if _VHOST_OPEN.match(raw):
            start = i
        elif _VHOST_CLOSE.match(raw) and start >= 0:
            blocks.append((start, i))
            start = -1
    return blocks


def parse_apache_sites(files: dict[str, str]) -> list[Site]:
    sites: list[Site] = []
    for path, text in (files or {}).items():
        lines = text.splitlines()
        for idx, (st, en) in enumerate(apache_vhost_blocks(text)):
            listen = _VHOST_OPEN.match(lines[st])
            name, alias, root = "", [], ""
            for line in lines[st:en + 1]:
                if not name:
                    m = _APACHE_NAME.match(line)
                    if m:
                        name = m.group(1)
                        continue
                m = _APACHE_ALIAS.match(line)
                if m:
                    alias += m.group(1).split()
                    continue
                m = _APACHE_ROOT.match(line)
                if m and not root:
                    root = m.group(1)
            names = ([name] if name else []) + alias
            sites.append(Site(
                kind="apache", file=path, index=idx,
                names=names, listen=[listen.group(1).strip()] if listen else [],
                root=root, line=st + 1))
    return sites


def set_apache_names(text: str, index: int, names: list[str]) -> str:
    """ServerName 取第一个域名，其余进 ServerAlias。"""
    blocks = apache_vhost_blocks(text)
    if index < 0 or index >= len(blocks):
        raise ValueError(f"这个文件里没有第 {index + 1} 个 VirtualHost")
    st, en = blocks[index]
    lines = (text or "").splitlines()
    names = [n for n in names if n]
    if not names:
        raise ValueError("域名不能为空")
    main, alias = names[0], names[1:]
    has_main = has_alias = False
    for i in range(st, min(en, len(lines) - 1) + 1):
        if _APACHE_NAME.match(lines[i]):
            indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            lines[i] = f"{indent}ServerName {main}"
            has_main = True
        elif _APACHE_ALIAS.match(lines[i]):
            indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            lines[i] = (f"{indent}ServerAlias {' '.join(alias)}" if alias
                        else f"# {indent}ServerAlias")
            has_alias = True
    if not has_main:
        indent = lines[st][:len(lines[st]) - len(lines[st].lstrip())] + "    "
        lines.insert(st + 1, f"{indent}ServerName {main}")
        en += 1
    if alias and not has_alias:
        indent = lines[st][:len(lines[st]) - len(lines[st].lstrip())] + "    "
        lines.insert(st + 2, f"{indent}ServerAlias {' '.join(alias)}")
    return _join_like(text, lines)


_IIS_SITE = re.compile(r'SITE\s+"([^"]+)"\s*\((.*?)\)', re.I)


def parse_iis_sites(text: str) -> list[Site]:
    """`appcmd list site` 只是给你看：改绑定的正确入口是 IIS 管理器。"""
    sites: list[Site] = []
    for line in (text or "").splitlines():
        m = _IIS_SITE.search(line)
        if not m:
            continue
        name, meta = m.group(1), m.group(2)
        bindings = ""
        bm = re.search(r"bindings:([^,]+)", meta)
        if bm:
            bindings = bm.group(1).strip()
        sites.append(Site(kind="iis", file="", index=len(sites),
                          names=[name], listen=[bindings] if bindings else [],
                          note="IIS 绑定请用 IIS 管理器或 netsh 修改"))
    return sites


def site_dump_cmd(kind: str) -> str:
    """取「全部有效配置 + 文件名」的一条命令。

    统一成 `# configuration file <path>:` 这个格式 —— 这也是 `nginx -T`
    本来的输出格式，所以解析端只需要一套 split_config_dump()。
    全部用单引号，免得在 Python 字符串里再套一层转义。
    """
    if kind == "nginx":
        return (
            'nginx -T 2>/dev/null || ('
            'for f in /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf '
            '/etc/nginx/nginx.conf; do '
            '[ -f "$f" ] && echo "# configuration file $f:" && cat "$f"; '
            'done)'
        )
    if kind == "apache":
        return (
            '(for f in /etc/apache2/sites-enabled/* /etc/apache2/conf-enabled/* '
            '/etc/httpd/conf.d/*.conf; do '
            '[ -f "$f" ] && echo "# configuration file $f:" && cat "$f"; '
            'done)'
        )
    if kind == "iis":
        return "appcmd list site"
    return ""


def parse_sites(kind: str, text: str) -> list[Site]:
    if kind == "iis":
        return parse_iis_sites(text)
    files = split_config_dump(text)
    if kind == "nginx":
        return parse_nginx_sites(files)
    if kind == "apache":
        return parse_apache_sites(files)
    return []


class SiteBinder:
    """改域名：备份 → 写入 → 配置检查 → 重载，任一步失败就还原。

    和证书部署是同一套路子。不一样的地方是这里写的是**配置文件**，
    所以「配置检查」比「回读 sha256」更重要 —— 语法错了服务就起不来。
    """

    def __init__(self, session, site: Site, runner=None):
        self.session = session
        self.site = site
        self.runner = runner or self._default_runner
        self.steps: list[dict] = []

    def _default_runner(self, cmd: str, admin: bool) -> tuple[int, str, str]:
        s = self.session
        if s is None:
            return 1, "", "尚未连接设备"
        elev = _need_elevation()
        try:
            if admin and hasattr(s, "run_admin"):
                return s.run_admin(cmd, timeout=60)
            return s.run(cmd, timeout=60)
        except Exception as e:  # noqa: BLE001
            if elev and isinstance(e, elev):
                raise
            return 1, "", str(e)

    def _step(self, name: str, cmd: str, admin: bool = True) -> tuple[int, str, str]:
        rc, out, err = self.runner(cmd, admin)
        self.steps.append({"name": name, "cmd": cmd, "rc": rc,
                           "out": (out or "").strip()[:300],
                           "err": (err or "").strip()[:300]})
        return rc, out, err

    def _test_cmd(self) -> str:
        if self.site.kind == "nginx":
            return "nginx -t 2>&1"
        return "apache2ctl configtest 2>&1 || httpd -t 2>&1"

    def _reload_cmd(self) -> str:
        if self.site.kind == "nginx":
            return "systemctl reload nginx 2>&1 || nginx -s reload 2>&1"
        return ("systemctl reload apache2 2>&1 || systemctl reload httpd 2>&1 "
                "|| apache2ctl graceful 2>&1")

    def update(self, names: list[str], reload: bool = True) -> dict:
        self.steps = []
        site = self.site
        if site.kind == "iis":
            return {"ok": False, "steps": [], "detail": site.note or
                    "IIS 的绑定不通过配置文件管理"}
        path = site.file
        if not path:
            return {"ok": False, "steps": [], "detail": "不知道站点在哪个文件里"}
        q = shlex.quote(path)
        rc, out, _ = self._step("读取", f"cat {q}")
        if rc != 0 or not out.strip():
            return {"ok": False, "steps": self.steps,
                    "detail": f"读不到 {path}（可能需要提权）"}
        old_text = out
        try:
            if site.kind == "nginx":
                new_text = set_nginx_names(old_text, site.index, names)
            else:
                new_text = set_apache_names(old_text, site.index, names)
        except ValueError as e:
            return {"ok": False, "steps": self.steps, "detail": str(e)}
        if new_text == old_text:
            return {"ok": True, "steps": self.steps,
                    "detail": "域名没有变化，什么都没写"}

        ts = time.strftime("%Y%m%d-%H%M%S")
        bak = f"{path}.bak-{ts}"
        rc, out, _ = self._step("备份", f"cp -a {q} {shlex.quote(bak)}")
        if rc != 0:
            return {"ok": False, "steps": self.steps,
                    "detail": f"备份失败，已中止（不冒险直接改配置）"}

        b64 = base64.b64encode(new_text.encode()).decode()
        rc, out, err = self._step(
            "写入", f"printf '%s' {shlex.quote(b64)} | base64 -d > {q}")
        if rc != 0:
            self._step("还原", f"cp -a {shlex.quote(bak)} {q}")
            return {"ok": False, "steps": self.steps,
                    "detail": f"写入失败：{(err or out).strip()[:200]}"}

        if reload:
            rc, out, err = self._step("配置检查", self._test_cmd())
            if rc != 0:
                self._step("还原", f"cp -a {shlex.quote(bak)} {q}")
                self._step("重载", self._reload_cmd())
                return {"ok": False, "steps": self.steps,
                        "detail": f"配置检查未通过，已还原："
                                  f"{((err or out) or '').strip()[:240]}"}
            rc, out, err = self._step("重载", self._reload_cmd())
            if rc != 0:
                self._step("还原", f"cp -a {shlex.quote(bak)} {q}")
                self._step("重载", self._reload_cmd())
                return {"ok": False, "steps": self.steps,
                        "detail": f"重载失败，已还原：{((err or out) or '').strip()[:240]}"}

        return {"ok": True, "steps": self.steps, "backup": bak,
                "detail": f"{path} 已更新（第 {site.index + 1} 个站点），"
                          f"域名：{' '.join(names)}"}

    def rollback(self) -> dict:
        """把最近一次 .bak-* 盖回去。"""
        path = self.site.file
        if not path:
            return {"ok": False, "detail": "没有可还原的文件"}
        q = shlex.quote(path)
        rc, out, _ = self._step(
            "找备份", f"ls -1t {q}.bak-* 2>/dev/null | head -1")
        bak = (out or "").strip().splitlines()[0] if (out or "").strip() else ""
        if not bak:
            return {"ok": False, "steps": self.steps, "detail": "没有找到 .bak-* 备份"}
        rc, out, err = self._step("还原", f"cp -a {shlex.quote(bak)} {q}")
        if rc != 0:
            return {"ok": False, "steps": self.steps,
                    "detail": f"还原失败：{(err or out).strip()[:200]}"}
        rc, out, err = self._step("重载", self._reload_cmd())
        return {"ok": rc == 0, "steps": self.steps, "backup": bak,
                "detail": f"已还原 {bak}" + ("" if rc == 0 else "（重载失败，请手动检查）")}


def summarize_sites(sites: list[Site]) -> str:
    if not sites:
        return "没有解析到站点"
    named = sum(1 for s in sites if s.names)
    return f"{len(sites)} 个站点" + (f"（{named} 个已绑定域名）" if named else "")
