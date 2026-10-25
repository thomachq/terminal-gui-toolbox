# -*- coding: utf-8 -*-
"""把命令回显解析成结构化行。

所有函数都遵守两条：**不抛异常**、**格式认不出就返回空列表**。
界面拿到空列表会显示空态并保留「原始输出」，不会因为解析失败就什么都看不到。

Windows 侧优先用 `|` 分隔的 PowerShell 输出（比解析中文表格稳得多），
解析器同时兼容老的中文表格文本，两路都能吃。
"""
from __future__ import annotations

import json
import re

_SPLIT = re.compile(r"\s{2,}")


def _clean(s: str) -> str:
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def _lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in _clean(text).split("\n")]


def _size_to_bytes(text: str) -> int:
    """'50G' / '1.2T' / '512M' / '4096' → 字节数。"""
    t = (text or "").strip().replace(",", "")
    m = re.match(r"^([\d.]+)\s*([KMGTPE]?)(i?B)?$", t, re.I)
    if not m:
        return 0
    try:
        n = float(m.group(1))
    except ValueError:
        return 0
    unit = (m.group(2) or "").upper()
    mul = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3,
           "T": 1024 ** 4, "P": 1024 ** 5, "E": 1024 ** 6}.get(unit, 1)
    return int(n * mul)


# ==================================================================== 接口 / IP
def parse_ip_addr(text: str) -> list[dict]:
    """Linux `ip -4 addr show`。"""
    rows: list[dict] = []
    cur: dict | None = None
    for ln in _lines(text):
        m = re.match(r"^\d+:\s+([^:]+):\s+<([^>]*)>.*?(?:state (\w+))?", ln)
        if m:
            cur = {"iface": m.group(1).strip().split("@")[0],
                   "state": (m.group(3) or "UNKNOWN").upper(),
                   "mac": "", "addr": "", "prefix": "", "scope": "",
                   "flags": m.group(2)}
            rows.append(cur)
            continue
        if cur is None:
            continue
        ms = re.search(r"link/\w+\s+([0-9a-f:]{11,17})", ln)
        if ms:
            cur["mac"] = ms.group(1)
        mi = re.search(r"inet\s+([\d.]+)/(\d+)(?:\s+brd\s+\S+)?\s+scope\s+(\S+)", ln)
        if mi:
            if cur["addr"]:      # 一个网卡多个地址 → 再开一行
                cur = dict(cur)
                cur.update({"addr": "", "prefix": "", "scope": ""})
                rows.append(cur)
            cur["addr"] = mi.group(1)
            cur["prefix"] = mi.group(2)
            cur["scope"] = mi.group(3)
    return [r for r in rows if r.get("addr")]


def parse_ifconfig(text: str) -> list[dict]:
    """macOS / BSD `ifconfig`。"""
    rows: list[dict] = []
    cur: dict | None = None
    for ln in _lines(text):
        m = re.match(r"^([a-z0-9_.]+):\s+flags=\S*\s*<([^>]*)>", ln, re.I)
        if m:
            cur = {"iface": m.group(1), "flags": m.group(2),
                   "state": "UP" if "UP" in m.group(2) else "DOWN",
                   "mac": "", "addr": "", "prefix": "", "scope": ""}
            rows.append(cur)
            continue
        if cur is None:
            continue
        m = re.search(r"ether\s+([0-9a-f:]{11,17})", ln, re.I)
        if m:
            cur["mac"] = m.group(1)
        m = re.search(r"inet\s+([\d.]+)\s+netmask\s+0x([0-9a-f]+)", ln, re.I)
        if m:
            cur["addr"] = m.group(1)
            cur["prefix"] = _mask_to_prefix(int(m.group(2), 16))
        else:
            m = re.search(r"inet\s+([\d.]+)/(\d+)", ln)
            if m:
                cur["addr"] = m.group(1)
                cur["prefix"] = m.group(2)
    return [r for r in rows if r.get("addr")]


def _ip_to_int(s: str) -> int:
    try:
        parts = [int(x) for x in (s or "").split(".")]
        if len(parts) != 4:
            return 0
        return (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]
    except ValueError:
        return 0


def _mask_to_prefix(mask: int) -> str:
    return str(bin(mask & 0xFFFFFFFF).count("1"))


def parse_ipconfig(text: str) -> list[dict]:
    """Windows `ipconfig /all`（中英文都吃）。

    注意不能拿第一个冒号切键值：IPv6 地址本身就带冒号，
    `IPv6 地址 . . . : 240e:37a::1` 会被切坏。键值之间一定有「空格+冒号+空格」。
    """
    rows: list[dict] = []
    cur: dict | None = None
    split_re = re.compile(r"\s+:\s+")

    def split_kv(line: str):
        parts = split_re.split(line.strip(), maxsplit=1)
        if len(parts) == 2:
            return parts[0].strip(" ."), parts[1].strip()
        return None, None

    for ln in _lines(text):
        if not ln.strip():
            continue
        if re.match(r"^\S", ln) and ("适配器" in ln or "adapter" in ln.lower()):
            cur = {"iface": ln.strip().rstrip(":"), "mac": "", "addr": "",
                   "prefix": "", "gateway": "", "state": "UP", "scope": "",
                   "dns": ""}
            rows.append(cur)
            continue
        if cur is None:
            continue
        head, val = split_kv(ln)
        if head is None:
            continue
        low = head.lower()
        if head.startswith("IPv4") or low.startswith("ipv4"):
            m = re.search(r"([\d.]+)", val)
            if m and not cur["addr"]:
                cur["addr"] = m.group(1)
            m2 = re.search(r"\(([^)]+)\)", val)
            if m2:
                cur["scope"] = m2.group(1)
        elif "子网掩码" in head or "subnet" in low:
            m = re.search(r"([\d.]+)", val)
            if m:
                cur["prefix"] = _mask_to_prefix(_ip_to_int(m.group(1)))
        elif ("默认网关" in head or "default gateway" in low) and not cur["gateway"]:
            m = re.fullmatch(r"([\d.]+)", val)
            if m:
                cur["gateway"] = m.group(1)
        elif "物理地址" in head or "physical address" in low:
            m = re.search(r"([0-9a-f-]{11,17})", val, re.I)
            if m:
                cur["mac"] = m.group(1).replace("-", ":").upper()
        elif "描述" in head or "description" in low:
            cur["desc"] = val
        elif "dns 服务器" in low or "dns servers" in low:
            m = re.search(r"([\d.]+)", val)
            if m and not cur["dns"]:
                cur["dns"] = m.group(1)
    return [r for r in rows if r.get("addr")]


def ip_table(session_label: str, text: str) -> list[dict]:
    """按文本特征自动挑解析器。"""
    body = _clean(text)
    if "适配器" in body or "adapter" in body.lower():
        rows = parse_ipconfig(body)
    elif re.search(r"^\d+:\s+\S+:\s+<", body, re.M):
        rows = parse_ip_addr(body)
    else:
        rows = parse_ifconfig(body)
    for r in rows:
        p = r.get("prefix") or ""
        r["cidr"] = f"{r.get('addr','')}/{p}" if p else r.get("addr", "")
        r["_tone"] = "ok" if r.get("state", "").upper().startswith("UP") else "mute"
    return rows


# ==================================================================== 路由表
def parse_route_linux(text: str) -> list[dict]:
    rows = []
    for ln in _lines(text):
        toks = ln.split()
        if not toks:
            continue
        r = {"dst": toks[0], "gateway": "", "dev": "", "metric": "",
             "proto": "", "scope": "", "src": "", "flags": ""}
        if r["dst"] == "default":
            r["dst"] = "0.0.0.0/0"
        elif "/" not in r["dst"] and re.match(r"^[\d.]+$", r["dst"]):
            r["dst"] += "/32"
        i = 1
        while i < len(toks) - 1:
            k, v = toks[i], toks[i + 1]
            if k in ("via", "dev", "metric", "proto", "scope", "src", "mtu",
                     "table", "pref"):
                if k in r:
                    r[k] = v
            i += 2
        r["_tone"] = "brand" if r["dst"] == "0.0.0.0/0" else ""
        rows.append(r)
    return rows


_CIDR = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})")
_VIA = re.compile(r"via\s+(\d{1,3}(?:\.\d{1,3}){3})", re.I)
_DIRECT = re.compile(r"directly connected,\s*(\S+)", re.I)
_CISCO_CODE = {"S": "static", "C": "connected", "L": "local", "R": "rip",
               "O": "ospf", "B": "bgp", "D": "eigrp", "i": "isis", "M": "mobile"}


def parse_route_network(text: str) -> list[dict]:
    """网络设备的路由表：Cisco `show ip route` 与 H3C/华为 `display ip routing-table`。

    两种格式差得挺远：
      Cisco  "S*  0.0.0.0/0 [1/0] via 192.168.1.1"
             "C   192.168.1.0/24 is directly connected, Vlan1"
      H3C    "0.0.0.0/0   Static  60  0  192.168.1.1  GE1/0/1"

    共同点是每行都带一个 CIDR，所以先用它定位，再按有没有 via / directly
    connected 分流，剩下的当表格按列切。
    """
    rows: list[dict] = []
    for ln in _lines(text):
        s = ln.strip()
        m = _CIDR.search(s)
        if not m:
            continue
        # "10.0.0.0/8 is variably subnetted, 2 subnets, 2 masks" 是分节标题，
        # 不是一条路由 —— 它有 CIDR，不排掉就会在表里多出一行假的。
        low = s.lower()
        if "subnetted" in low or "subnets" in low:
            continue
        row = {"dst": f"{m.group(1)}/{m.group(2)}", "gateway": "", "dev": "",
               "metric": "", "proto": "", "scope": "", "src": "", "flags": ""}
        via = _VIA.search(s)
        direct = _DIRECT.search(s)
        toks = s.split()
        head = toks[0]

        if re.fullmatch(r"\*?[A-Za-z]{1,2}\*?", head):
            # Cisco 风格：行首是路由码，可能带 * （候选默认路由）
            code = head.replace("*", "")
            row["proto"] = _CISCO_CODE.get(code[:1], code.lower())
            row["flags"] = "*" if "*" in head else ""
            if direct:
                row["dev"] = direct.group(1).rstrip(",")
                row["proto"] = row["proto"] or "connected"
            elif via:
                row["gateway"] = via.group(1)
            mv = re.search(r"\[(\d+)/(\d+)\]", s)
            if mv:
                row["metric"] = mv.group(1)
        else:
            # 表格风格：CIDR 之后是 Proto / Pre / Cost / NextHop / Interface
            idx = next((i for i, t in enumerate(toks) if _CIDR.search(t)), -1)
            rest = toks[idx + 1:] if idx >= 0 else []
            if rest and not re.match(r"^[\d.]+$", rest[0]):
                row["proto"] = rest[0].lower()
            nums = [t for t in rest if re.match(r"^[\d.]+$", t)]
            if nums:
                row["metric"] = nums[0]                       # Pre（优先级）
            ips = [t for t in rest if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", t)]
            if ips:
                row["gateway"] = ips[0]
            if len(rest) >= 2:
                tail = rest[-1]
                if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", tail) and not tail.isdigit():
                    row["dev"] = tail
        if not row["gateway"] and row["proto"] in ("direct", "connected", "local"):
            row["gateway"] = "直连"
        row["_tone"] = "brand" if row["dst"] == "0.0.0.0/0" else ""
        rows.append(row)
    return rows


def parse_route_win(text: str) -> list[dict]:
    rows = []
    started = False
    for ln in _lines(text):
        low = ln.lower()
        if ("network destination" in low or "网络目标" in ln) and \
                ("netmask" in low or "网络掩码" in ln):
            started = True
            continue
        if not started:
            continue
        if not ln.strip() or ln.strip().startswith("="):
            if rows:
                break
            continue
        toks = ln.split()
        if len(toks) < 5:
            continue
        try:
            dst, mask, gw, iface, metric = toks[0], toks[1], toks[2], toks[3], toks[4]
        except ValueError:
            continue
        if not re.match(r"^[\d.]+$", dst):
            continue
        pfx = _mask_to_prefix(_ip_to_int(mask))
        rows.append({
            "dst": f"{dst}/{pfx}", "gateway": "" if gw in ("On-link", "在链路上") else gw,
            "dev": iface, "metric": metric, "proto": "", "scope": "",
            "src": "", "flags": "",
            "_tone": "brand" if dst == "0.0.0.0" else "",
        })
    return rows


def parse_route_bsd(text: str) -> list[dict]:
    rows = []
    for ln in _lines(text):
        if "Destination" in ln and "Gateway" in ln:
            continue
        if not ln.strip() or ln.startswith(("Internet", "Routing", "Internet6")):
            continue
        toks = ln.split()
        if len(toks) < 4:
            continue
        dst, gw, flags = toks[0], toks[1], toks[2]
        if not re.match(r"^[\w.:/\-]+$", dst):
            continue
        if not re.match(r"^(U|H|G|S|C|R|W|L|B|I|D|M|X|b|c|d|g|i|l|m|r|s|w|1|2|3)+$", flags):
            continue
        dev = toks[3] if len(toks) > 3 and not toks[3].isdigit() else ""
        if dst == "default":
            dst = "0.0.0.0/0"
        elif "/" not in dst and re.match(r"^[\d.]+$", dst):
            dst += "/32"
        rows.append({"dst": dst, "gateway": "" if gw.startswith("link#") else gw,
                     "dev": dev, "metric": "", "proto": "",
                     "scope": "", "src": "", "flags": flags,
                     "_tone": "brand" if dst == "0.0.0.0/0" else ""})
    return rows


def route_table(text: str) -> list[dict]:
    body = _clean(text)
    if "Network Destination" in body or "网络目标" in body:
        return parse_route_win(body)
    if re.search(r"^\d+\.\d+\.\d+\.\d+/\d+\s+dev\s", body, re.M) or \
            re.search(r"^default\s+via\s", body, re.M):
        return parse_route_linux(body)
    return parse_route_bsd(body)


# ==================================================================== 端口
def parse_listen_ss(text: str) -> list[dict]:
    rows = []
    for ln in _lines(text):
        toks = ln.split()
        if not toks or toks[0] in ("State", "Netid"):
            continue
        if toks[0] not in ("LISTEN", "tcp", "udp"):
            continue
        if toks[0] == "LISTEN":
            if len(toks) < 4:
                continue
            local = toks[3]
            proc = ""
            m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', ln)
            if m:
                proc = f"{m.group(1)}/{m.group(2)}"
            proto = "tcp"
        else:                                    # netstat -tlnp
            if "LISTEN" not in ln:
                continue
            local = toks[3]
            proc = toks[6] if len(toks) > 6 else ""
            proto = toks[0]
        addr, _, port = local.rpartition(":")
        rows.append({"proto": proto, "addr": addr.strip("[]") or "0.0.0.0",
                     "port": port, "proc": proc, "state": "LISTEN"})
    return _dedup_ports(rows)


def parse_listen_win(text: str) -> list[dict]:
    rows = []
    for ln in _lines(text):
        toks = ln.split()
        if len(toks) < 5:
            continue
        if toks[0] not in ("TCP", "UDP"):
            continue
        if toks[0] == "TCP" and "LISTEN" not in ln.upper():
            continue
        local = toks[1]
        addr, _, port = local.rpartition(":")
        rows.append({"proto": toks[0].lower(), "addr": addr.strip("[]") or "0.0.0.0",
                     "port": port, "proc": toks[-1], "state": "LISTEN"})
    return _dedup_ports(rows)


def _dedup_ports(rows: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for r in rows:
        if not r.get("port") or r["port"] == "*":
            continue
        k = (r.get("proto"), r.get("port"), r.get("addr"))
        if k in seen:
            continue
        seen.add(k)
        r["_tone"] = "warn" if r["addr"] in ("0.0.0.0", "::", "*") else ""
        out.append(r)
    try:
        out.sort(key=lambda r: int(r["port"]))
    except (ValueError, TypeError):
        pass
    return out


def port_table(text: str) -> list[dict]:
    body = _clean(text)
    if "State" in body and ("Local Address" in body or "Local Address:Port" in body):
        return parse_listen_ss(body)
    if re.search(r"^\s*TCP\s+\S+:\d+", body, re.M | re.I):
        return parse_listen_win(body)
    return parse_listen_ss(body)


# ==================================================================== 磁盘
def parse_df(text: str) -> list[dict]:
    rows = []
    for ln in _lines(text):
        toks = ln.split()
        if len(toks) < 6 or ln.startswith("Filesystem"):
            continue
        if not re.match(r"^\d+%$", toks[4]):
            continue
        total, used, avail = _size_to_bytes(toks[1]), _size_to_bytes(toks[2]), \
            _size_to_bytes(toks[3])
        pct = int(toks[4].rstrip("%"))
        rows.append({"fs": toks[0], "total": total, "used": used, "free": avail,
                     "use_pct": pct, "mount": " ".join(toks[5:]),
                     "_tone": "err" if pct >= 90 else ("warn" if pct >= 80 else "")})
    return rows


def parse_disk_pipe(text: str) -> list[dict]:
    rows = []
    for ln in _lines(text):
        if "|" not in ln:
            continue
        p = [x.strip() for x in ln.split("|")]
        while len(p) < 4:
            p.append("")
        try:
            total = int(float(p[1])) if p[1] else 0
            free = int(float(p[2])) if p[2] else 0
        except ValueError:
            continue
        if total <= 0:
            continue
        used = max(0, total - free)
        pct = round(used * 100 / total)
        rows.append({"fs": p[0], "total": total, "used": used, "free": free,
                     "use_pct": pct, "mount": p[3] or p[0],
                     "_tone": "err" if pct >= 90 else ("warn" if pct >= 80 else "")})
    return rows


def disk_table(text: str) -> list[dict]:
    if "|" in text:
        return parse_disk_pipe(text)
    return parse_df(text)


# ==================================================================== 进程
def parse_ps(text: str) -> list[dict]:
    rows = []
    header = None
    for ln in _lines(text):
        toks = ln.split()
        if not toks:
            continue
        if header is None and ("PID" in toks or "pid" in toks):
            header = [t.lower() for t in toks]
            continue
        if len(toks) < 4:
            continue
        try:
            pid = int(toks[0])
        except ValueError:
            continue
        d = {"pid": pid, "ppid": "", "cpu": 0.0, "mem": 0.0, "comm": ""}
        if header and "pcpu" in header:
            try:
                d["cpu"] = float(toks[header.index("pcpu")])
            except (ValueError, IndexError):
                pass
            try:
                d["mem"] = float(toks[header.index("pmem")])
            except (ValueError, IndexError):
                pass
            try:
                d["ppid"] = toks[header.index("ppid")]
            except (ValueError, IndexError):
                pass
            try:
                d["comm"] = " ".join(toks[header.index("comm"):])
            except (ValueError, IndexError):
                d["comm"] = toks[-1]
        else:
            d["cpu"] = float(toks[2]) if toks[2].replace(".", "").isdigit() else 0.0
            d["mem"] = float(toks[3]) if toks[3].replace(".", "").isdigit() else 0.0
            d["comm"] = " ".join(toks[4:])
        d["_tone"] = "warn" if d["cpu"] >= 60 else ""
        rows.append(d)
    return rows


def parse_ps_pipe(text: str) -> list[dict]:
    rows = []
    for ln in _lines(text):
        if "|" not in ln:
            continue
        p = [x.strip() for x in ln.split("|")]
        while len(p) < 4:
            p.append("")
        try:
            rows.append({"pid": int(float(p[0])), "comm": p[1],
                         "cpu": float(p[2] or 0), "mem": float(p[3] or 0),
                         "ppid": p[4] if len(p) > 4 else "",
                         "_tone": "warn" if float(p[2] or 0) >= 60 else ""})
        except ValueError:
            continue
    return rows


def parse_ps_json(text: str) -> list[dict]:
    """Windows `ConvertTo-Json` 的进程列表（CPU 是累计秒数，内存是字节）。"""
    body = (text or "").strip()
    if not body.startswith(("{", "[")):
        return []
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    rows = []
    for d in data:
        if not isinstance(d, dict):
            continue
        try:
            pid = int(d.get("Id") or 0)
        except (TypeError, ValueError):
            continue
        try:
            cpu = float(d.get("CPU") or 0)
        except (TypeError, ValueError):
            cpu = 0.0
        mem = d.get("WorkingSet64") or 0
        try:
            mem_mb = round(float(mem) / (1024 * 1024), 1)
        except (TypeError, ValueError):
            mem_mb = 0.0
        rows.append({"pid": pid, "comm": d.get("ProcessName") or "",
                     "cpu": round(cpu, 1), "mem": mem_mb, "ppid": "",
                     "mem_pct": "", "cpu_sec": round(cpu, 1),
                     "mem_mb": mem_mb})
    return rows


def proc_table(text: str) -> list[dict]:
    if (text or "").lstrip().startswith(("{", "[")):
        jrows = parse_ps_json(text)
        if jrows:
            return jrows
    if "|" in text:
        return parse_ps_pipe(text)
    return parse_ps(text)


# ==================================================================== Docker
def _json_lines(text: str) -> list[dict]:
    out = []
    for ln in _lines(text):
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


# docker 列表的字段清单（顺序即分隔符格式里的顺序）。
# 为什么不用 `{{json .}}`：在群晖上实测 `docker ps -a --format '{{json .}}'`
# 要 **65 秒**，而只取这几个字段只要 **0.1 秒** —— 差在 .Labels/.Mounts/
# .Networks 这些字段上，daemon 要为每个容器多绕几圈。用自定义分隔符取字段，
# 又不用放弃逐行解析。
SEP = "\x1f"

PS_FIELDS = ("ID", "Names", "Image", "State", "Status", "Ports",
             "CreatedAt", "RunningFor")
IMG_FIELDS = ("Repository", "Tag", "ID", "CreatedSince", "Size")
STAT_FIELDS = ("Name", "CPUPerc", "MemUsage", "MemPerc", "NetIO", "BlockIO",
               "PIDs")
DF_FIELDS = ("Type", "TotalCount", "Active", "Size", "Reclaimable")


def fmt_fields(fields) -> str:
    """拼出 `{{.A}}<0x1f>{{.B}}` 这样的 docker --format 串。

    `\\x1f` 是 Go 模板里的转义（docker 用 Go 的 text/template），
    值里几乎不可能出现这个字符，比自己挑个 `|` 之类安全。
    """
    sep = '{{"\\x1f"}}'
    return sep.join("{{." + f + "}}" for f in fields)


def _records(text: str, fields) -> list[dict]:
    """两种输出都认：逐行 JSON，或者按 SEP 分隔的定长行（见 fmt_fields）。"""
    rows = []
    for ln in _lines(text):
        ln = ln.rstrip()
        if not ln.strip():
            continue
        if ln.lstrip().startswith("{"):
            try:
                o = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if isinstance(o, dict):
                rows.append(o)
            continue
        parts = ln.split(SEP)
        if len(parts) != len(fields):
            continue
        rows.append(dict(zip(fields, parts)))
    return rows


def docker_ps(text: str) -> list[dict]:
    rows = []
    for o in _records(text, PS_FIELDS):
        state = str(o.get("State") or "").lower()
        rows.append({
            "id": (o.get("ID") or "")[:12],
            "name": (o.get("Names") or "").split(",")[0],
            "image": o.get("Image") or "",
            "state": state or o.get("Status", ""),
            "status": o.get("Status") or "",
            "ports": o.get("Ports") or "",
            "created": o.get("CreatedAt") or "",
            "running": o.get("RunningFor") or "",
            "_tone": "ok" if state == "running" else
                     ("err" if state == "exited" else "warn"),
        })
    return rows


def docker_images(text: str) -> list[dict]:
    rows = []
    for o in _records(text, IMG_FIELDS):
        rows.append({
            "repo": o.get("Repository") or "<none>",
            "tag": o.get("Tag") or "<none>",
            "id": (o.get("ID") or "").replace("sha256:", "")[:12],
            "size": o.get("Size") or "",
            "created": o.get("CreatedSince") or "",
            "dangling": (o.get("Repository") in (None, "", "<none>")),
            "_tone": "mute" if o.get("Repository") in (None, "", "<none>") else "",
        })
    return rows


def docker_stats(text: str) -> list[dict]:
    rows = []
    for o in _records(text, STAT_FIELDS):
        cpu = str(o.get("CPUPerc") or "0%").rstrip("%")
        mem_pct = str(o.get("MemPerc") or "0%").rstrip("%")
        usage = str(o.get("MemUsage") or "")
        used = usage.split("/")[0].strip() if "/" in usage else usage

        def f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        rows.append({
            "name": o.get("Name") or o.get("Container") or "",
            "cpu": f(cpu), "mem_pct": f(mem_pct), "mem": used,
            "net": o.get("NetIO") or "", "block": o.get("BlockIO") or "",
            "pids": o.get("PIDs") or "",
            "_tone": "err" if f(cpu) >= 85 else ("warn" if f(cpu) >= 60 else ""),
        })
    return rows


def docker_df(text: str) -> list[dict]:
    """`docker system df --format ...`。"""
    rows = []
    for o in _records(text, DF_FIELDS):
        rows.append({"type": o.get("Type") or "", "total": o.get("TotalCount") or "",
                     "active": o.get("Active") or "", "size": o.get("Size") or "",
                     "reclaim": o.get("Reclaimable") or ""})
    return rows


# ==================================================================== 其它
def resolv_conf(text: str) -> list[dict]:
    rows = []
    for ln in _lines(text):
        toks = ln.split()
        if len(toks) >= 2 and toks[0] == "nameserver":
            rows.append({"kind": "nameserver", "value": toks[1]})
        elif len(toks) >= 2 and toks[0] == "search":
            rows.append({"kind": "search", "value": " ".join(toks[1:])})
        elif len(toks) >= 2 and toks[0] == "domain":
            rows.append({"kind": "domain", "value": toks[1]})
    return rows


def ping_series(text: str) -> list[dict]:
    """逐包 RTT，给时序图用。认 Linux 的 `time=1.23 ms` 与 Windows 的 `时间=1ms`。"""
    rows = []
    idx = 0
    for ln in _lines(text):
        m = re.search(r"(?:time|时间|時間)[=<]\s*([\d.]+)\s*ms", ln, re.I)
        if m:
            idx += 1
            rows.append({"seq": idx, "rtt": float(m.group(1))})
        elif re.search(r"(?:Request timed out|请求超时|请求超時|Destination .* unreachable|"
                       r"无法访问目标主机)", ln, re.I):
            idx += 1
            rows.append({"seq": idx, "rtt": None})
    return rows


def traceroute_rows(text: str) -> list[dict]:
    """traceroute / tracert 的逐跳结果。

    Linux:  ` 1  192.168.1.1  1.234 ms  1.100 ms  1.001 ms`
    Win:    `  1     1 ms     1 ms     1 ms  192.168.1.1`
    """
    rows = []
    for ln in _lines(text):
        s = ln.strip()
        if not s:
            continue
        m = re.match(r"^(\d+)\s+(.*)$", s)
        if not m:
            continue
        hop = int(m.group(1))
        rest = m.group(2)
        ips = re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", rest)
        rtts = [float(x) for x in re.findall(r"([\d.]+)\s*ms", rest)]
        star = "*" in rest
        rows.append({
            "hop": hop,
            "host": ips[0] if ips else ("*" if star or not ips else ""),
            "rtt": "/".join(f"{x:.0f}" for x in rtts[:3]) if rtts else "",
            "avg": round(sum(rtts) / len(rtts), 1) if rtts else "",
            "raw": rest[:80],
            "_tone": "mute" if (star and not ips) else "",
        })
    return rows


def dns_rows(text: str) -> list[dict]:
    rows = []
    if "|" in text:
        for ln in _lines(text):
            if "|" not in ln:
                continue
            kind, _, val = ln.partition("|")
            kind, val = kind.strip(), val.strip()
            if not val:
                continue
            for one in val.split(","):
                one = one.strip()
                if one:
                    rows.append({"kind": kind or "nameserver", "value": one,
                                 "_tone": "brand"})
        if rows:
            return rows
    for r in resolv_conf(text):
        rows.append({"kind": r["kind"], "value": r["value"],
                     "_tone": "brand" if r["kind"] == "nameserver" else ""})
    return rows


def first_value(text: str, *patterns: str) -> str:
    for p in patterns:
        m = re.search(p, text, re.M | re.I)
        if m:
            return m.group(1).strip()
    return ""
