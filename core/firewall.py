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

RULE_KEYS = ("ref", "family", "table", "chain", "handle", "pos", "action", "proto",
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
        seen: dict[str, int] = {}
        for ln in (text or "").splitlines():
            s = ln.strip()
            if s.startswith("###"):
                table = s.replace("###", "").strip() or "filter"
                continue
            if not s.startswith("-A "):
                continue
            toks = s.split()
            chain = toks[1] if len(toks) > 1 else ""
            seen[chain] = seen.get(chain, 0) + 1
            r = {"ref": "", "family": "ip", "table": table, "chain": chain,
                 "handle": "", "pos": seen[chain], "action": "", "proto": "",
                 "src": "any", "dst": "any", "port": "", "iface": "",
                 "comment": "", "raw": s, "enabled": True, "editable": True,
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

    def move_cmds(self, r: dict, delta: int) -> list[str]:
        """上移/下移 = 删掉再插到新位置（iptables 没有直接换序的命令）。

        插的位置要在**删完之后**看：删掉 p 之后，原来 p+1 那条占住了 p，
        所以下移要插到 p+1、上移要插到 p-1，正好是原来的目标位。
        """
        raw = r.get("raw") or ""
        pos = int(r.get("pos") or 0)
        if not raw.startswith("-A ") or pos <= 0:
            raise FirewallError("这条规则缺少位置信息，无法调整顺序")
        target = pos + (1 if delta > 0 else -1)
        if target < 1:
            raise FirewallError("已经在最前面了")
        table = r.get("table") or "filter"
        body = raw[3:]                        # "INPUT -p tcp --dport 22 -j ACCEPT"
        chain, _, tail = body.partition(" ")
        del_cmd = " ".join(x for x in (f"iptables -t {table} -D", chain, tail) if x)
        ins_cmd = " ".join(x for x in (f"iptables -t {table} -I", chain,
                                       str(target), tail) if x)
        return [del_cmd.strip(), ins_cmd.strip()]

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

    def _dir_arg(self, r: dict) -> str:
        d = str(r.get("direction") or r.get("chain") or "").lower()
        if d.startswith(("in", "入")):
            return "dir=in"
        if d.startswith(("out", "出")):
            return "dir=out"
        return ""

    def set_cmds(self, old: dict, new: dict) -> list[str]:
        """就地改：规则名不变就用 `set rule ... new`，改了名只能删了重建。"""
        old_name = (old.get("name") or
                    str(old.get("ref", "")).replace("netsh:", "")).strip()
        new_name = (new.get("name") or old_name).strip()
        if not old_name:
            raise FirewallError("这条规则没有名字，无法定位")
        if new_name != old_name:
            return self.del_cmds(old) + self.add_cmds({**new, "direction":
                                                       new.get("direction") or
                                                       old.get("direction")})
        parts = []
        act_old = str(old.get("action") or "").lower()
        act_new = "allow" if str(new.get("action") or "").lower() in (
            "allow", "accept", "允许", "放行") else "block"
        if act_new != act_old:
            parts.append(f"action={act_new}")
        proto_new = str(new.get("proto") or "").upper()
        if proto_new and proto_new != str(old.get("proto") or "").upper():
            parts.append(f"protocol={proto_new if proto_new != 'ANY' else 'any'}")
        port_new = str(new.get("port") or "").strip()
        if port_new != str(old.get("port") or "").strip():
            parts.append(f"localport={port_new or 'any'}")
        src_new = str(new.get("src") or "any").strip() or "any"
        if src_new != str(old.get("src") or "any").strip():
            parts.append(f"remoteip={src_new}")
        if not parts:
            raise FirewallError("没有任何改动")
        d = self._dir_arg(old)
        where = f'name="{old_name}"' + (f" {d}" if d else "")
        return [f"netsh advfirewall firewall set rule {where} new "
                + " ".join(parts)]

    def toggle_cmds(self, r: dict, enable: bool) -> list[str]:
        name = (r.get("name") or
                str(r.get("ref", "")).replace("netsh:", "")).strip()
        if not name:
            raise FirewallError("这条规则没有名字，无法启用/停用")
        d = self._dir_arg(r)
        where = f'name="{name}"' + (f" {d}" if d else "")
        return [f"netsh advfirewall firewall set rule {where} new "
                f"enable={'yes' if enable else 'no'}"]

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


# ==================================================================== ufw
class UfwBackend(FirewallBackend):
    """Ubuntu / Debian 默认那一套。规则是「编号列表」，改和删都靠编号。"""

    key = "ufw"
    label = "ufw"

    _DEF = re.compile(r"^\[\s*(\d+)\]\s+(.*)$")

    def probe_cmd(self) -> str:
        return "command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null"

    def read_cmd(self) -> str:
        return "ufw status numbered 2>/dev/null"

    def status_cmd(self) -> str:
        return "ufw status verbose 2>/dev/null | head -8"

    def parse(self, text: str) -> list[dict]:
        rows = []
        for ln in (text or "").splitlines():
            m = self._DEF.match(ln.strip())
            if not m:
                continue
            num, body = m.group(1), m.group(2).strip()
            # [ 1] 22/tcp   ALLOW IN   Anywhere
            # [ 4] Anywhere on eth0  ALLOW FWD  Anywhere on eth1
            act_m = re.search(r"\b(ALLOW|DENY|REJECT|LIMIT)\b\s*(IN|OUT|FWD)?",
                              body, re.I)
            action = (act_m.group(1).lower() if act_m else "")
            direction = (act_m.group(2) or "").upper() if act_m else ""
            left = body[:act_m.start()].strip() if act_m else body
            right = body[act_m.end():].strip() if act_m else ""
            port, proto = "", ""
            mp = re.match(r"^(\d+(?:[,:]\d+)*)(?:/(tcp|udp))?$", left)
            if mp:
                port = mp.group(1)
                proto = (mp.group(2) or "").lower()
            elif re.match(r"^\d+(?:,\d+)*$", left):
                port = left
            rows.append({
                "ref": f"ufw:{num}", "family": "ip", "table": "ufw",
                "chain": direction or "IN", "handle": num,
                "pos": int(num), "action": action, "proto": proto,
                "src": right or "any", "dst": "any", "port": port,
                "iface": "", "comment": "",
                "raw": body, "enabled": True, "editable": True,
                "persistent": "ufw 规则持久（/etc/ufw）",
                "_tone": "mute" if not action else _tone(action),
                "_spec": left,
            })
        return rows

    def validate(self, r: dict) -> str | None:
        port = str(r.get("port") or "").strip()
        if port and not re.match(r"^[\d,\-:/]+$", port):
            return "端口写 22 或 8080/tcp 或 80,443 或 1000:2000"
        proto = str(r.get("proto") or "").lower()
        if proto and proto not in ("tcp", "udp", "any", ""):
            return "ufw 只认 tcp / udp"
        if proto and not port:
            return "指定了协议就要给端口（ufw allow 8080/tcp）"
        return None

    def _act(self, r: dict) -> str:
        a = str(r.get("action") or "allow").lower()
        return {"accept": "allow", "pass": "allow", "drop": "deny",
                "block": "deny", "deny": "deny", "reject": "reject",
                "limit": "limit"}.get(a, "allow")

    @staticmethod
    def _is_any(v) -> bool:
        """「不限来源」在这套代码里有几种写法，都要认成空。"""
        return str(v or "").strip().lower() in ("", "any", "anywhere", "任何",
                                                "0.0.0.0/0", "::/0")

    def add_cmds(self, r: dict) -> list[str]:
        act = self._act(r)
        src = "" if self._is_any(r.get("src")) else str(r.get("src")).strip()
        dst = "" if self._is_any(r.get("dst")) else str(r.get("dst")).strip()
        port = str(r.get("port") or "").strip()
        proto = str(r.get("proto") or "").lower()
        if proto == "any":
            proto = ""
        spec = ""
        if port:
            spec = port if (proto and "/" in port) else (
                f"{port}/{proto}" if proto else port)
        if src:
            tail = f" to {dst or 'any'} port {port}" if port else (
                f" to {dst}" if dst else "")
            cmd = f"ufw {act} from {src}{tail}"
            if port and proto:
                cmd += f" proto {proto}"
            return [cmd]
        if dst and port:
            cmd = f"ufw {act} to {dst} port {port}"
            if proto:
                cmd += f" proto {proto}"
            return [cmd]
        if spec:
            return [f"ufw {act} {spec}"]
        if proto:
            return [f"ufw {act} {proto}"]
        return [f"ufw {act}"]

    def del_cmds(self, r: dict) -> list[str]:
        num = str(r.get("handle") or "").strip()
        if num.isdigit():
            return [f"ufw --force delete {num}"]
        spec = str(r.get("_spec") or "").strip()
        if spec:
            return [f"ufw --force delete {spec}"]
        # 刚加进去还没重读过列表时没有编号，用同样的写法反推一条删除
        try:
            added = self.add_cmds(r)[0]
        except FirewallError:
            added = ""
        if added.startswith("ufw "):
            return [f"ufw --force delete {added[4:]}"]
        raise FirewallError("这条 ufw 规则没有编号，无法定位删除")

    def move_cmds(self, r: dict, delta: int) -> list[str]:
        """ufw 的位置就是编号：删掉再 insert 到前/后一格。"""
        num = str(r.get("handle") or "").strip()
        spec = str(r.get("_spec") or "").strip()
        if not num.isdigit() or not spec:
            raise FirewallError("这条规则缺少编号或规则体，无法调整顺序")
        target = max(1, int(num) + (delta > 0))
        if target == int(num):
            raise FirewallError("已经在最前面了")
        act = self._act(r)
        return [f"ufw --force delete {num}", f"ufw insert {target} {spec}"
                if spec.split()[0] in ("allow", "deny", "reject", "limit")
                else f"ufw insert {target} {act} {spec}"]

    def persist_note(self) -> str:
        return "ufw 规则写在 /etc/ufw 里，重启后仍在（不需要额外保存）"


# ==================================================================== firewalld
class FirewalldBackend(FirewallBackend):
    """CentOS / RHEL / Rocky 那一套。改完必须 reload 才生效。"""

    key = "firewalld"
    label = "firewalld"

    def probe_cmd(self) -> str:
        return ("firewall-cmd --state 2>/dev/null || "
                "systemctl is-active firewalld 2>/dev/null")

    def read_cmd(self) -> str:
        return "firewall-cmd --list-all 2>/dev/null"

    def status_cmd(self) -> str:
        return "firewall-cmd --state 2>/dev/null; firewall-cmd --get-active-zones 2>/dev/null"

    def parse(self, text: str) -> list[dict]:
        rows: list[dict] = []
        zone = ""
        for ln in (text or "").splitlines():
            s = ln.strip()
            m = re.match(r"^(\S+)\s*(?:\((\w+)\))?$", s)
            if m and not s.startswith(("target", "icmp", "interfaces",
                                       "sources", "services", "ports", "protocols",
                                       "masquerade", "forward", "source-ports",
                                       "rich", "rules")):
                zone = m.group(1)
                continue
            if ":" not in s:
                continue
            k, _, v = s.partition(":")
            k = k.strip().lower()
            vals = [x for x in v.strip().split() if x]
            if k == "ports":
                for it in vals:
                    port, _, proto = it.partition("/")
                    rows.append(self._row(zone, "port", port, proto, "allow",
                                          it, ""))
            elif k == "services":
                for it in vals:
                    rows.append(self._row(zone, "service", it, "", "allow",
                                          it, ""))
            elif k == "protocols":
                for it in vals:
                    rows.append(self._row(zone, "protocol", "", it, "allow",
                                          it, ""))
        return rows

    def _row(self, zone, kind, port, proto, action, name, rich) -> dict:
        return {
            "ref": f"firewalld:{zone}:{kind}:{name}",
            "family": "ip", "table": zone or "public", "chain": kind.upper(),
            "handle": "", "pos": 0, "action": action, "proto": proto,
            "src": "any", "dst": "any", "port": port, "iface": "",
            "comment": rich, "raw": name, "enabled": True, "editable": True,
            "persistent": "firewalld --permanent + reload",
            "_tone": "ok", "_kind": kind, "_zone": zone or "public",
            "_name": name,
        }

    def validate(self, r: dict) -> str | None:
        port = str(r.get("port") or "").strip()
        if port:
            if not re.match(r"^[\d,\-]+$", port):
                return "端口写 8080 或 80,443 或 1000-2000"
            proto = str(r.get("proto") or "tcp").lower()
            if proto not in ("tcp", "udp", "sctp", "dccp"):
                return "firewalld 的端口协议只能是 tcp / udp"
            return None
        if not str(r.get("name") or "").strip():
            return "不给端口就要给服务名（http / https / ssh）"
        return None

    def _target(self, r: dict) -> tuple[str, str]:
        """返回 (类型, 值)：port=8080/tcp 或 service=https。"""
        name = str(r.get("name") or "").strip()
        if name and r.get("_kind") == "service":
            return "service", name
        port = str(r.get("port") or "").strip()
        if port:
            proto = str(r.get("proto") or "tcp").lower() or "tcp"
            return "port", f"{port}/{proto}" if "/" not in port else port
        if name:
            return "service", name
        raise FirewallError("要填端口或服务名")

    def add_cmds(self, r: dict) -> list[str]:
        kind, val = self._target(r)
        src = str(r.get("src") or "").strip()
        if src and src.lower() != "any":
            rule = (f"rule family=ipv4 source address={src} "
                    + (f"port port={val.split('/')[0]} protocol={val.split('/')[1]} "
                       if kind == "port" else "")
                    + "accept")
            return [f"firewall-cmd --permanent --add-rich-rule='{rule}'",
                    "firewall-cmd --reload"]
        return [f"firewall-cmd --permanent --add-{kind}={val}",
                "firewall-cmd --reload"]

    def del_cmds(self, r: dict) -> list[str]:
        name = str(r.get("_name") or r.get("raw") or "").strip()
        kind = str(r.get("_kind") or ("service" if not r.get("port") else "port"))
        if not name:
            raise FirewallError("这条 firewalld 规则没有可定位的值")
        return [f"firewall-cmd --permanent --remove-{kind}={name}",
                "firewall-cmd --reload"]

    def persist_note(self) -> str:
        return "加 --permanent 才持久；改完自动 firewall-cmd --reload 生效"


# ==================================================================== 工厂
BACKEND_CLASSES = {
    "ufw": UfwBackend,
    "firewalld": FirewalldBackend,
    "nftables": NftBackend,
    "iptables": IptablesBackend,
    "netsh": NetshBackend,
    "pf": PfBackend,
    "ipfw": IpfwBackend,
}

# 优先级 = 「哪个更能代表这台设备真正在用的防火墙」。
# 网络设备上不能一股脑全跑（一条命令卡 10 秒），所以 detect() 是逐个试、
# 一探到同档里优先级最高的就停。
PROBE_ORDER = ("ufw", "firewalld", "nftables", "iptables", "netsh", "pf", "ipfw")


def backend_class(key: str):
    return BACKEND_CLASSES.get(str(key or "").strip().lower())


def candidates(device) -> list[FirewallBackend]:
    fam = getattr(device, "os_family", "") or ""
    if fam == "windows":
        return [NetshBackend()]
    if fam == "darwin":
        return [PfBackend()]
    if fam == "freebsd":
        return [PfBackend(), IpfwBackend()]
    return [UfwBackend(), FirewalldBackend(), NftBackend(), IptablesBackend()]


# ------------------------------------------------------------------ 探测
def _has_lines(text: str, pat: str) -> bool:
    return bool(re.search(pat, text or "", re.M))


def rank(key: str, rc: int, text: str) -> tuple[int, str]:
    """给一份探测输出打分：2 = 正在用，1 = 装了但没启用，0 = 没有。

    这是整个探测的**唯一判据**，纯函数 —— 测试直接喂假 stdout 就行，
    不用连真机。
    """
    t = (text or "").strip()
    low = t.lower()
    if key == "ufw":
        if re.search(r"status:\s*active", low):
            return 2, "ufw 已启用"
        if re.search(r"status:\s*inactive", low):
            return 1, "ufw 已安装但未启用"
        return 0, ""
    if key == "firewalld":
        if low in ("running", "active") or "running" in low.split("\n")[0]:
            return 2, "firewalld 正在运行"
        if "inactive" in low or "not running" in low:
            return 1, "firewalld 已安装但未运行"
        return 0, ""
    if key == "nftables":
        if _has_lines(t, r"^\s*table\s+\w+\s+\S+"):
            return 2, "nftables 有规则集"
        if t.startswith("{"):
            return ((2, "nftables 有规则集") if '"rule"' in t
                    else (1, "nftables 已安装，规则集为空"))
        if re.search(r"^nftables v", low, re.M):
            return 1, "nftables 已安装"
        if rc == 0 and low.startswith("table"):
            return 1, "nftables 已安装，规则集为空"
        return 0, ""
    if key == "iptables":
        if _has_lines(t, r"^\s*-A\s"):
            return 2, "iptables 有规则"
        if re.search(r"^iptables v", low, re.M):
            return 1, "iptables 已安装"
        if rc == 0 and (_has_lines(t, r"^\s*-P\s") or not t):
            return 1, "iptables 可用，规则为空"
        return 0, ""
    if key == "netsh":
        if re.search(r"(state|状态)\s*[:：]?\s*(on|off|启用|关闭)", low) and \
                re.search(r"profile|配置文件", low):
            return 2, "Windows Defender 防火墙"
        return 0, ""
    if key == "pf":
        if re.search(r"status:\s*enabled", low):
            return 2, "pf 已启用"
        if re.search(r"status:\s*disabled", low):
            return 1, "pf 已安装但未启用"
        return 0, ""
    if key == "ipfw":
        if _has_lines(t, r"^\s*\d{1,5}\s+(allow|deny|accept|drop|count|check-state)"):
            return 2, "ipfw 有规则"
        if rc == 0 and t:
            return 1, "ipfw 可用"
        return 0, ""
    return 0, ""


def detect_backend(results: dict) -> tuple[str, str]:
    """`{key: (rc, stdout)}` → (后端 key, 说明)。认不出返回 ("", "")。

    先在「正在用」里按 PROBE_ORDER 挑，都够不上再退到「装了没开」。
    """
    best = ("", "", -1)
    for key in PROBE_ORDER:
        if key not in results:
            continue
        rc, text = results[key]
        tier, note = rank(key, rc, text)
        if tier > best[2]:
            best = (key, note, tier)
        if tier == 2 and best[0] == key:
            break        # 已经命中「正在用」的最高优先级，不用再看后面的
    if best[2] <= 0:
        return "", ""
    return best[0], best[1]


def probe_plan(device) -> list[tuple[str, str]]:
    """按设备类型给出「试哪些、每个跑什么」，顺序即优先级。"""
    out = []
    for be in candidates(device):
        out.append((be.key, be.probe_cmd()))
    return out


def detect(session, device, timeout: int = 20):
    """逐个探测，探到「正在用」的最高档就停。返回 (backend|None, 说明)。"""
    results: dict[str, tuple[int, str]] = {}
    tried = []
    for key, cmd in probe_plan(device):
        be = BACKEND_CLASSES[key](session, device)
        try:
            rc, out, err = session.run_traced(cmd, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            tried.append(f"{be.label}: {e}")
            continue
        text = (out or "") + (err or "")
        results[key] = (rc, text)
        tier, _note = rank(key, rc, text)
        if tier == 2:
            break
        tried.append(f"{be.label}: rc={rc}")
    key, note = detect_backend(results)
    if not key:
        return None, "；".join(tried) or "未找到可用的防火墙后端"
    be = BACKEND_CLASSES[key](session, device)
    be.session = session
    return be, f"{be.label} · {note}"


# ------------------------------------------------------------------ 能力
def supports(backend: FirewallBackend, op: str) -> bool:
    """界面靠这个决定按钮亮不亮，不用各处 if 后端类型。"""
    if not backend.can_write:
        return False
    if op in ("add", "edit", "del"):
        return True
    if op == "toggle":
        return hasattr(backend, "toggle_cmds")
    if op == "move":
        return hasattr(backend, "move_cmds")
    return False


def edit_cmds(backend: FirewallBackend, old: dict, new: dict) -> list[str]:
    """改一条规则 = 删旧的 + 加新的。netsh 例外，它支持就地 set。"""
    setter = getattr(backend, "set_cmds", None)
    if setter is not None:
        return setter(old, new)
    err = backend.validate(new)
    if err:
        raise FirewallError(err)
    return backend.del_cmds(old) + backend.add_cmds(new)


def toggle_cmds(backend: FirewallBackend, rule: dict, enable: bool) -> list[str]:
    fn = getattr(backend, "toggle_cmds", None)
    if fn is None:
        raise FirewallError(f"{backend.label} 不支持单条规则启用/停用")
    return fn(rule, enable)


def undo_cmds(backend: FirewallBackend, action: str, old: dict,
              new: dict | None = None) -> tuple[list[str], str]:
    """把上一步改动作废。返回 (命令, 说明)；拼不出来给 ([], 原因)。

    宁可老实说「这条撤不回来，备份在 data/fw_backup/」，也不要拼一条
    看起来像能撤、实际撤错的命令。
    """
    try:
        if action == "add" and new:
            return backend.del_cmds(new), "删除刚加的那条"
        if action == "del" and old:
            return backend.add_cmds(old), "把删掉的那条加回来"
        if action == "edit" and old and new:
            return (backend.del_cmds(new) + backend.add_cmds(old),
                    "先删改后的，再把原来的加回来")
    except FirewallError as e:
        return [], str(e)
    except Exception as e:  # noqa: BLE001
        return [], f"拼不出撤销命令：{e}"
    return [], "没有可撤销的上一步"


# ------------------------------------------------------------------ 备份
def backup_dir(root) -> "Path":
    from pathlib import Path
    return Path(root) / "fw_backup"


def save_backup(root, key: str, text: str, tag: str = "fw") -> "Path":
    """把当前规则原文存一份。

    写操作**之前**调用。存的是 `nft list ruleset` / `iptables -S` 那种
    能直接看、也能手工喂回去的原文，不做任何加工。
    """
    import time as _t
    from pathlib import Path
    d = backup_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    name = f"{tag}-{key}-{_t.strftime('%Y%m%d-%H%M%S')}.txt"
    p = d / name
    try:
        p.write_text(text or "", encoding="utf-8")
    except OSError as e:
        raise FirewallError(f"备份写不进去（{p}）：{e}")
    return p


def list_backups(root, tag: str = "fw", limit: int = 20) -> list:
    d = backup_dir(root)
    if not d.is_dir():
        return []
    files = sorted((p for p in d.glob(f"{tag}-*.txt")),
                   key=lambda p: p.name, reverse=True)
    return files[:limit]


# ------------------------------------------------------------------ 执行
def plan(backend: FirewallBackend, action: str, rule: dict,
         old: dict | None = None, enable: bool = True) -> list[str]:
    """动作 → 命令。界面先拿它做预览，用户确认了才真跑。"""
    if action == "add":
        err = backend.validate(rule)
        if err:
            raise FirewallError(err)
        return backend.add_cmds(rule)
    if action == "del":
        return backend.del_cmds(rule)
    if action == "edit":
        return edit_cmds(backend, old or rule, rule)
    if action == "toggle":
        return toggle_cmds(backend, rule, enable)
    raise FirewallError(f"未知操作 {action}")


def apply(session, backend: FirewallBackend, action: str, rule: dict,
          timeout: int = 45, old: dict | None = None,
          enable: bool = True) -> tuple[int, str, str]:
    cmds = plan(backend, action, rule, old=old, enable=enable)
    return run_cmds(session, cmds, timeout=timeout)


def run_cmds(session, cmds: list[str], timeout: int = 45):
    rc, out, err = 0, "", ""
    for c in cmds:
        rc, o, e = session.run_admin(c, timeout=timeout)
        out += o
        err += e
        if rc != 0:
            break
    return rc, out.strip(), err.strip()
