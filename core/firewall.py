# -*- coding: utf-8 -*-
"""防火墙：后端探测、规则读取、增删。

支持的后端与其取舍：

  nftables   首选（Linux 新发行版 / DSM 7.2+）。用 `nft -j` 拿 JSON，
             规则带 handle，删除靠 handle，最稳。
  iptables   老 Linux / 部分 NAS。用 `iptables -S` 的规则语法读，
             删除就是把 `-A CHAIN` 换成 `-D CHAIN`。
  netsh      Windows Defender 防火墙，`show rule verbose` 之后按字段解析。
  pf         macOS / FreeBSD。**只读**——pf 的规则要写 /etc/pf.conf 再重载，
             程序替你改系统配置风险太大，这里只展示并给出该改哪里。
  ipfw       FreeBSD 老工具，读写都支持。

统一行结构见 `RULE_KEYS`。
"""
from __future__ import annotations

import json
import re

RULE_KEYS = ("ref", "family", "table", "chain", "handle", "action", "proto",
             "src", "dst", "port", "iface", "comment", "raw", "enabled",
             "editable", "persistent")

_ACTION_CN = {"accept": "允许", "allow": "允许", "drop": "丢弃", "block": "阻止",
              "reject": "拒绝", "deny": "拒绝", "pass": "放行"}
_CN_ACTION = {"允许": "accept", "阻止": "drop", "丢弃": "drop", "拒绝": "reject",
              "放行": "accept"}


class FirewallError(Exception):
    pass


def action_label(a: str) -> str:
    a = (a or "").lower()
    return _ACTION_CN.get(a, a or "—")


def _tone(action: str) -> str:
    a = (action or "").lower()
    if a in ("accept", "allow", "pass"):
        return "ok"
    if a in ("drop", "block", "deny"):
        return "warn"
    if a in ("reject",):
        return "err"
    return ""


class FirewallBackend:
    key = "none"
    label = "未探测"
    can_write = True
    direction_hint = ""

    def __init__(self, session=None, device=None):
        self.session = session
        self.device = device

    # ---------------- 探测
    def probe_cmd(self) -> str:
        raise NotImplementedError

    def read_cmd(self) -> str:
        raise NotImplementedError

    def parse(self, text: str) -> list[dict]:
        raise NotImplementedError

    def status_cmd(self) -> str:
        return ""

    # ---------------- 写
    def add_cmds(self, r: dict) -> list[str]:
        raise NotImplementedError

    def del_cmds(self, r: dict) -> list[str]:
        raise NotImplementedError

    def validate(self, r: dict) -> str | None:
        return None

    def persist_note(self) -> str:
        return ""


# ==================================================================== nftables
class NftBackend(FirewallBackend):
    key = "nftables"
    label = "nftables"

    def probe_cmd(self) -> str:
        return "nft --version 2>/dev/null"

    def read_cmd(self) -> str:
        return "nft -j list ruleset 2>/dev/null || nft -a list ruleset"

    def status_cmd(self) -> str:
        return "nft list tables 2>/dev/null | head -20"

    # -- 解析
    def parse(self, text: str) -> list[dict]:
        body = (text or "").strip()
        if body.startswith("{"):
            try:
                return self._parse_json(json.loads(body))
            except json.JSONDecodeError:
                pass
        return self._parse_text(body)

    def _parse_json(self, doc: dict) -> list[dict]:
        rows = []
        for item in doc.get("nftables", []):
            rule = item.get("rule")
            if not rule:
                continue
            fam = rule.get("family", "")
            tbl = rule.get("table", "")
            chain = rule.get("chain", "")
            handle = str(rule.get("handle", ""))
            expr = rule.get("expr") or []
            action, proto, src, dst, port = self._read_expr(expr)
            rows.append({
                "ref": f"nft:{fam}:{tbl}:{chain}:{handle}",
                "family": fam, "table": tbl, "chain": chain, "handle": handle,
                "action": action, "proto": proto, "src": src, "dst": dst,
                "port": port, "iface": "",
                "comment": rule.get("comment", "") or "",
                "raw": self._expr_text(expr),
                "enabled": True, "editable": True,
                "persistent": "由 /etc/nftables.conf 决定",
                "_tone": _tone(action),
            })
        return rows

    @staticmethod
    def _read_expr(expr: list) -> tuple[str, str, str, str, str]:
        action, proto, src, dst, port = "", "", "any", "any", ""
        for e in expr:
            if not isinstance(e, dict):
                continue
            for k in ("accept", "drop", "reject", "jump", "goto", "return",
                      "queue", "continue", "log"):
                if k in e:
                    action = k
            m = e.get("match")
            if not m:
                continue
            left = m.get("left") or {}
            right = m.get("right")
            payload = left.get("payload") if isinstance(left, dict) else None
            if payload:
                field = payload.get("field", "")
                proto = payload.get("protocol", "") or proto
                if field == "saddr":
                    src = str(right)
                elif field == "daddr":
                    dst = str(right)
                elif field in ("dport", "sport") and field == "dport":
                    port = str(right)
        if isinstance(action, dict):
            action = "jump"
        return action or "—", proto, src, dst, port

    @staticmethod
    def _expr_text(expr: list) -> str:
        try:
            return " ".join(json.dumps(e, ensure_ascii=False) for e in expr)
        except (TypeError, ValueError):
            return str(expr)

    def _parse_text(self, text: str) -> list[dict]:
        """`nft -a list ruleset` 的文本兜底。"""
        rows = []
        cur_table = cur_chain = ""
        for ln in text.splitlines():
            s = ln.strip()
            m = re.match(r"^table (\w+) (\S+) \{", s)
            if m:
                cur_table = f"{m.group(1)} {m.group(2)}"
                continue
            m = re.match(r"^chain (\S+) \{", s)
            if m:
                cur_chain = m.group(1)
                continue
            m = re.match(r"^(.*?)\s+#\s*handle (\d+)\s*$", s)
            if m and cur_chain:
                body, handle = m.group(1).strip(), m.group(2)
                if not body or body in ("}",):
                    continue
                fam, _, tbl = cur_table.partition(" ")
                action = ""
                for a in ("accept", "drop", "reject", "jump", "return"):
                    if re.search(rf"\b{a}\b", body):
                        action = a
                port = ""
                mp = re.search(r"\b(?:tcp|udp) dport (\d+)", body)
                if mp:
                    port = mp.group(1)
                proto = ""
                mpr = re.search(r"\b(?:ip|ip6) (?:saddr|daddr)?.*?\b(tcp|udp|icmp)\b", body)
                if mpr:
                    proto = mpr.group(1)
                elif re.search(r"\btcp\b", body):
                    proto = "tcp"
                elif re.search(r"\budp\b", body):
                    proto = "udp"
                src = ""
                ms = re.search(r"\bip saddr (\S+)", body)
                if ms:
                    src = ms.group(1)
                rows.append({
                    "ref": f"nft:{fam}:{tbl}:{cur_chain}:{handle}",
                    "family": fam, "table": tbl, "chain": cur_chain,
                    "handle": handle, "action": action or "—", "proto": proto,
                    "src": src or "any", "dst": "any", "port": port, "iface": "",
                    "comment": "", "raw": body, "enabled": True,
                    "editable": True, "persistent": "", "_tone": _tone(action),
                })
        return rows

    # -- 写
    def validate(self, r: dict) -> str | None:
        if not r.get("chain"):
            return "需要指定链（chain）"
        port = str(r.get("port") or "").strip()
        if port and not re.match(r"^[\d,\-: ]+$", port):
            return "端口写 22 或 80,443 或 1000-2000"
        return None

    def add_cmds(self, r: dict) -> list[str]:
        fam = r.get("family") or "inet"
        tbl = r.get("table") or "filter"
        chain = r.get("chain")
        parts = ["nft", "add", "rule", fam, tbl, chain]
        proto = (r.get("proto") or "").lower()
        src = (r.get("src") or "").strip()
        dst = (r.get("dst") or "").strip()
        port = str(r.get("port") or "").strip()
        if src and src != "any":
            parts += ["ip", "saddr", src]
        if dst and dst != "any":
            parts += ["ip", "daddr", dst]
        if proto and proto != "any":
            parts.append(proto)
            if port:
                parts += ["dport", port]
        act = (r.get("action") or "accept").lower()
        parts.append("accept" if act in ("accept", "allow") else
                     ("drop" if act in ("drop", "block") else act))
        return [" ".join(parts)]

    def del_cmds(self, r: dict) -> list[str]:
        fam = r.get("family") or "inet"
        tbl = r.get("table") or "filter"
        chain = r.get("chain") or ""
        h = r.get("handle") or ""
        if not h:
            raise FirewallError("这条规则没有 handle，无法定位删除")
        return [f"nft delete rule {fam} {tbl} {chain} handle {h}"]

    def persist_note(self) -> str:
        return ("nft 的运行时规则默认重启丢失。要持久请改 /etc/nftables.conf "
                "后 `nft list ruleset > /etc/nftables.conf`。")


# ==================================================================== iptables
class IptablesBackend(FirewallBackend):
    key = "iptables"
    label = "iptables"

    def probe_cmd(self) -> str:
        return "iptables --version 2>/dev/null"

    def read_cmd(self) -> str:
        return ("iptables -S 2>/dev/null; echo '### nat'; "
                "iptables -t nat -S 2>/dev/null")

    def parse(self, text: str) -> list[dict]:
        rows = []
        table = "filter"
        for ln in (text or "").splitlines():
            s = ln.strip()
            if s.startswith("###"):
                table = s.replace("###", "").strip() or "filter"
                continue
            if not s.startswith("-A "):
                continue
            toks = s.split()
            chain = toks[1] if len(toks) > 1 else ""
            r = {"ref": "", "family": "ip", "table": table, "chain": chain,
                 "handle": "", "action": "", "proto": "", "src": "any",
                 "dst": "any", "port": "", "iface": "", "comment": "",
                 "raw": s, "enabled": True, "editable": True,
                 "persistent": "", "_tone": ""}
            i = 2
            while i < len(toks):
                t = toks[i]
                nxt = toks[i + 1] if i + 1 < len(toks) else ""
                if t in ("-j", "--jump"):
                    r["action"] = nxt.lower()
                elif t in ("-p", "--protocol"):
                    r["proto"] = nxt
                elif t in ("-s", "--source"):
                    r["src"] = nxt
                elif t in ("-d", "--destination"):
                    r["dst"] = nxt
                elif t in ("--dport", "--destination-port"):
                    r["port"] = nxt
                elif t in ("-i", "--in-interface"):
                    r["iface"] = nxt
                elif t in ("--comment",):
                    r["comment"] = nxt.strip('"')
                i += 1
            r["ref"] = f"iptables:{table}:{chain}:{s}"
            r["_tone"] = _tone(r["action"])
            rows.append(r)
        return rows

    def validate(self, r: dict) -> str | None:
        if not r.get("chain"):
            return "需要指定链（INPUT / OUTPUT / FORWARD）"
        port = str(r.get("port") or "").strip()
        if port and not re.match(r"^[\d,\-: ]+$", port):
            return "端口写 22 或 80,443 或 1000-2000"
        return None

    def add_cmds(self, r: dict) -> list[str]:
        table = r.get("table") or "filter"
        parts = ["iptables", "-t", table, "-A", r.get("chain") or "INPUT"]
        proto = (r.get("proto") or "").lower()
        if proto and proto != "any":
            parts += ["-p", proto]
        src = (r.get("src") or "").strip()
        if src and src != "any":
            parts += ["-s", src]
        dst = (r.get("dst") or "").strip()
        if dst and dst != "any":
            parts += ["-d", dst]
        if r.get("iface"):
            parts += ["-i", r["iface"]]
        port = str(r.get("port") or "").strip()
        if port:
            parts += ["--dport", port]
        act = (r.get("action") or "ACCEPT").upper()
        if act in ("ALLOW", "DROP", "BLOCK"):
            act = {"ALLOW": "ACCEPT", "BLOCK": "DROP"}.get(act, act)
        parts += ["-j", act]
        if r.get("comment"):
            parts += ["-m", "comment", "--comment", f'"{r["comment"]}"']
        return [" ".join(parts)]

    def del_cmds(self, r: dict) -> list[str]:
        raw = r.get("raw") or ""
        table = r.get("table") or "filter"
        if raw.startswith("-A "):
            spec = "-D " + raw[3:]
            return [f"iptables -t {table} {spec}"]
        raise FirewallError("这条规则没有可用的原始文本，无法删除")

    def persist_note(self) -> str:
        return ("iptables 规则重启丢失。持久化建议 `iptables-save > "
                "/etc/iptables/rules.v4` 或在发行版的 netfilter-persistent 里启用。")


# ==================================================================== Windows
class NetshBackend(FirewallBackend):
    key = "netsh"
    label = "Windows Defender 防火墙"

    _KEYMAP = {
        "rule name": "name", "规则名称": "name",
        "enabled": "enabled", "已启用": "enabled",
        "direction": "direction", "方向": "direction",
        "profiles": "profiles", "配置文件": "profiles",
        "action": "action", "操作": "action",
        "protocol": "proto", "协议": "proto",
        "localport": "lport", "本地端口": "lport",
        "remoteport": "rport", "远程端口": "rport",
        "localip": "lip", "本地ip地址": "lip",
        "remoteip": "rip", "远程ip地址": "rip",
        "program": "program", "程序": "program",
    }

    def probe_cmd(self) -> str:
        return "netsh advfirewall show allprofiles state"

    def read_cmd(self) -> str:
        return "netsh advfirewall firewall show rule name=all verbose"

    def status_cmd(self) -> str:
        return "netsh advfirewall show allprofiles state"

    def parse(self, text: str) -> list[dict]:
        rows: list[dict] = []
        cur: dict = {}

        def flush():
            nonlocal cur
            if not cur.get("name"):
                cur = {}
                return
            act = str(cur.get("action", "")).lower()
            act = {"允许": "allow", "阻止": "block", "allow": "allow",
                   "block": "block"}.get(act, act)
            proto = str(cur.get("proto", "")).strip()
            port = str(cur.get("lport", "")).strip()
            if port.lower() in ("any", "任何"):
                port = ""
            enabled = str(cur.get("enabled", "")).lower() not in ("no", "否", "false")
            r = {
                "ref": f"netsh:{cur['name']}",
                "family": "win", "table": "advfirewall",
                "chain": str(cur.get("direction", "")),
                "handle": "", "action": act, "proto": proto,
                "src": str(cur.get("rip", "any")),
                "dst": str(cur.get("lip", "any")),
                "port": port, "iface": "",
                "comment": str(cur.get("program", "")),
                "raw": f'{cur["name"]} | {cur.get("direction","")} | '
                       f'{proto} {port} | {act}',
                "enabled": enabled, "editable": True,
                "persistent": "注册表（永久）",
                "_tone": _tone(act) if enabled else "mute",
            }
            rows.append(r)
            cur = {}

        for ln in (text or "").splitlines():
            if not ln.strip():
                flush()
                continue
            if ln.strip().startswith("---"):
                continue
            if ":" not in ln:
                continue
            k, _, v = ln.partition(":")
            key = re.sub(r"\s+", " ", k.strip().lower())
            mapped = self._KEYMAP.get(key)
            if mapped:
                if mapped == "name" and cur.get("name"):
                    flush()
                cur[mapped] = v.strip()
        flush()
        return rows

    def validate(self, r: dict) -> str | None:
        if not (r.get("name") or "").strip():
            return "规则名不能为空"
        port = str(r.get("port") or "").strip()
        if port and not re.match(r"^[\d,\-]+$", port):
            return "端口写 22 或 80,443 或 1000-2000"
        return None

    def add_cmds(self, r: dict) -> list[str]:
        name = (r.get("name") or "TerminalToolbox Rule").replace('"', "'")
        direction = "in" if str(r.get("direction", "in")).lower().startswith(
            ("in", "入")) else "out"
        act = str(r.get("action", "allow")).lower()
        act = "allow" if act in ("allow", "accept", "允许", "放行") else "block"
        proto = str(r.get("proto") or "TCP").upper()
        if proto.lower() in ("any", "", "任何"):
            proto = "any"
        port = str(r.get("port") or "").strip()
        parts = ["netsh advfirewall firewall add rule",
                 f'name="{name}"', f"dir={direction}", f"action={act}",
                 f"protocol={proto}"]
        if port and proto.lower() in ("tcp", "udp"):
            parts.append(f"localport={port}")
        src = str(r.get("src") or "any").strip()
        parts.append(f"remoteip={src or 'any'}")
        return [" ".join(parts)]

    def del_cmds(self, r: dict) -> list[str]:
        name = (r.get("name") or "").replace('"', "'")
        if not name:
            name = str(r.get("ref", "")).replace("netsh:", "")
        return [f'netsh advfirewall firewall delete rule name="{name}"']

    def persist_note(self) -> str:
        return "Windows 防火墙规则写在注册表里，重启后仍在"


# ==================================================================== pf / ipfw
class PfBackend(FirewallBackend):
    key = "pf"
    label = "pf（只读）"
    can_write = False

    def probe_cmd(self) -> str:
        return "pfctl -s info 2>/dev/null | head -3"

    def read_cmd(self) -> str:
        return "pfctl -sr 2>/dev/null"

    def status_cmd(self) -> str:
        return "pfctl -s info 2>/dev/null | head -6"

    def parse(self, text: str) -> list[dict]:
        rows = []
        for i, ln in enumerate((text or "").splitlines()):
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            act = ""
            m = re.search(r"\b(pass|block|match|scrub)\b", s)
            if m:
                act = m.group(1)
            proto = ""
            mp = re.search(r"\bproto (\w+)", s)
            if mp:
                proto = mp.group(1)
            port = ""
            mpp = re.search(r"\bport (\d+|[\w-]+)", s)
            if mpp:
                port = mpp.group(1)
            rows.append({
                "ref": f"pf:{i}", "family": "pf", "table": "", "chain": "",
                "handle": str(i), "action": act or "—", "proto": proto,
                "src": "", "dst": "", "port": port, "iface": "",
                "comment": "", "raw": s, "enabled": True, "editable": False,
                "persistent": "/etc/pf.conf", "_tone": _tone(act),
            })
        return rows

    def validate(self, r: dict) -> str | None:
        return "pf 规则需要写 /etc/pf.conf 并重载，本程序不代改系统配置"

    def add_cmds(self, r: dict) -> list[str]:
        raise FirewallError(self.validate(r))

    def del_cmds(self, r: dict) -> list[str]:
        raise FirewallError(self.validate(r))

    def persist_note(self) -> str:
        return "编辑 /etc/pf.conf 后执行 `pfctl -f /etc/pf.conf` 生效"


class IpfwBackend(FirewallBackend):
    key = "ipfw"
    label = "ipfw"

    def probe_cmd(self) -> str:
        return "ipfw -V 2>/dev/null || which ipfw"

    def read_cmd(self) -> str:
        return "ipfw list 2>/dev/null"

    def parse(self, text: str) -> list[dict]:
        rows = []
        for ln in (text or "").splitlines():
            s = ln.strip()
            m = re.match(r"^(\d+)\s+(.*)$", s)
            if not m:
                continue
            num, body = m.group(1), m.group(2)
            act = body.split()[0].lower() if body.split() else ""
            proto = ""
            mp = re.search(r"\b(tcp|udp|icmp|ip)\b", body)
            if mp:
                proto = mp.group(1)
            port = ""
            mpp = re.search(r"\b(?:dst-port|dport)\s+(\d+)", body)
            if mpp:
                port = mpp.group(1)
            rows.append({
                "ref": f"ipfw:{num}", "family": "ipfw", "table": "", "chain": "",
                "handle": num, "action": act, "proto": proto, "src": "",
                "dst": "", "port": port, "iface": "", "comment": "",
                "raw": body, "enabled": True, "editable": True,
                "persistent": "/etc/ipfw.rules", "_tone": _tone(act),
            })
        return rows

    def add_cmds(self, r: dict) -> list[str]:
        act = (r.get("action") or "allow").lower()
        act = {"accept": "allow", "block": "deny", "drop": "deny"}.get(act, act)
        proto = (r.get("proto") or "ip").lower()
        src = (r.get("src") or "any").strip() or "any"
        dst = (r.get("dst") or "any").strip() or "any"
        parts = ["ipfw", "add", act, proto, "from", src, "to", dst]
        if r.get("port"):
            parts += ["dst-port", str(r["port"])]
        return [" ".join(parts)]

    def del_cmds(self, r: dict) -> list[str]:
        return [f"ipfw delete {r.get('handle')}"]

    def persist_note(self) -> str:
        return "ipfw 规则重启丢失，持久化写 /etc/ipfw.rules 并在 rc.conf 里启用"


# ==================================================================== 工厂
def candidates(device) -> list[FirewallBackend]:
    fam = getattr(device, "os_family", "") or ""
    if fam == "windows":
        return [NetshBackend()]
    if fam == "darwin":
        return [PfBackend()]
    if fam == "freebsd":
        return [PfBackend(), IpfwBackend()]
    return [NftBackend(), IptablesBackend()]


def detect(session, device, timeout: int = 20):
    """按顺序探测，返回 (backend|None, 说明文本)。

    探测命令本身也进日志，用户能看到到底试了什么。
    """
    tried = []
    for be in candidates(device):
        be.session = session
        try:
            rc, out, err = session.run_traced(be.probe_cmd(), timeout=timeout)
        except Exception as e:  # noqa: BLE001
            tried.append(f"{be.label}: {e}")
            continue
        text = (out or "") + (err or "")
        if rc == 0 and text.strip():
            return be, f"{be.label} · {text.strip().splitlines()[0][:90]}"
        tried.append(f"{be.label}: rc={rc}")
    return None, "；".join(tried) or "未找到可用的防火墙后端"


def apply(session, backend: FirewallBackend, action: str, rule: dict,
          timeout: int = 45) -> tuple[int, str, str]:
    if action == "add":
        err = backend.validate(rule)
        if err:
            raise FirewallError(err)
        cmds = backend.add_cmds(rule)
    elif action == "del":
        cmds = backend.del_cmds(rule)
    else:
        raise FirewallError(f"未知操作 {action}")

    rc, out, err = 0, "", ""
    for c in cmds:
        rc, o, e = session.run_admin(c, timeout=timeout)
        out += o
        err += e
        if rc != 0:
            break
    return rc, out.strip(), err.strip()
