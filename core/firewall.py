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

from core import ipaddr

RULE_KEYS = ("ref", "family", "table", "chain", "handle", "pos", "action", "proto",
             "src", "dst", "port", "iface", "comment", "raw", "enabled",
             "editable", "persistent")


def _split_pairs(text) -> list[tuple[str, bool]]:
    """一框多值 → [(网段, 是不是 IPv6)]，`any`/空 一律丢掉。"""
    out: list[tuple[str, bool]] = []
    ok, _bad = ipaddr.split(text)
    for c in ok:
        if c == "any":
            continue
        out.append((c, ipaddr.family(c) == 6))
    return out


def _first(text) -> str:
    """取第一个网段；没有就是 any。"""
    ok, _bad = ipaddr.split(text)
    ok = [c for c in ok if c != "any"]
    return ok[0] if ok else "any"


# 一条表单规则最多展开成多少条后端命令。地址库选一个省就是几百条网段，
# 源 × 目标 再一乘就是天文数字（iptables 逐条 -A，几十万条能把设备写死），
# 所以在生成命令前先卡一道：宁可让用户少选，也不要下发到一半断掉。
_MAX_RULES = 400


def _cross(src_text, dst_text) -> list[tuple[str, str]]:
    """源 × 目标 → [(src, dst)]，`any` 用空串表示。"""
    srcs = [c for c, _f in _split_pairs(src_text)] or [""]
    dsts = [c for c, _f in _split_pairs(dst_text)] or [""]
    return [(s, d) for s in srcs for d in dsts]


def _check_addrs(r: dict) -> str | None:
    """地址校验：写错的、一条规则塞多个的，都在下发前拦住。"""
    for key, label in (("src", "来源地址"), ("dst", "目标地址")):
        ok, bad = ipaddr.split(r.get(key))
        if bad:
            return (f"{label}写错了：{bad[0]}"
                    "（IPv4 如 192.168.1.0/24，IPv6 如 fe80::/10）")
        if not ok:
            continue
        # 多值不是错误：add_cmds 会把一个框里的网段拆成多条后端命令
        # （地址库选一个省就是几百条）。只卡硬上限 —— 超过了下发到一半
        # 断掉比拒绝更糟，用户会以为规则已经生效。
        n = len([c for c in ok if c != "any"])
        if n > _MAX_RULES:
            return (f"{label}有 {n} 个网段，超过单条规则上限 "
                    f"{_MAX_RULES} 条，请少选几项或分成多条规则")
    n_src = len(_split_pairs(r.get("src")))
    n_dst = len(_split_pairs(r.get("dst")))
    if n_src and n_dst and n_src * n_dst > _MAX_RULES:
        return (f"来源 × 目标 会展开成 {n_src * n_dst} 条规则"
                f"（上限 {_MAX_RULES}），请缩小子集")
    # 一个 IPv4 源 + 一个 IPv6 目标，在任何后端上都是非法规则
    # （nft 会直接语法错、iptables 会报 network not found），提前拦住。
    sf = {ipaddr.family(c) for c in ipaddr.split(r.get("src"))[0]} - {0}
    df = {ipaddr.family(c) for c in ipaddr.split(r.get("dst"))[0]} - {0}
    if sf and df and not (sf & df):
        return "来源与目标地址族不一致（一个 IPv4、一个 IPv6），请拆成两条规则"
    return None

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
        return _check_addrs(r)

    def add_cmds(self, r: dict) -> list[str]:
        fam = r.get("family") or "inet"
        tbl = r.get("table") or "filter"
        chain = r.get("chain")
        proto = (r.get("proto") or "").lower()
        port = str(r.get("port") or "").strip()
        act = (r.get("action") or "accept").lower()
        act = ("accept" if act in ("accept", "allow")
               else ("drop" if act in ("drop", "block") else act))
        # 源 × 目标 一起拆：一个网段一条命令。nft 的匹配上下文关键字
        # **必须**跟着地址族走 —— IPv6 写 `ip saddr` 会直接语法错
        # （老版本这里硬编码 `ip`，于是「地址填 IPv6」永远下发失败）。
        out: list[str] = []
        for src, dst in _cross(r.get("src"), r.get("dst")):
            parts = ["nft", "add", "rule", fam, tbl, chain]
            if src:
                parts += [("ip6" if ipaddr.family(src) == 6 else "ip"),
                          "saddr", src]
            if dst:
                parts += [("ip6" if ipaddr.family(dst) == 6 else "ip"),
                          "daddr", dst]
            if proto and proto != "any":
                parts.append(proto)
                if port:
                    parts += ["dport", port]
            parts.append(act)
            out.append(" ".join(parts))
        return out

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
        return _check_addrs(r)

    def add_cmds(self, r: dict) -> list[str]:
        table = r.get("table") or "filter"
        chain = r.get("chain") or "INPUT"
        proto = (r.get("proto") or "").lower()
        act = (r.get("action") or "ACCEPT").upper()
        if act in ("ALLOW", "DROP", "BLOCK"):
            act = {"ALLOW": "ACCEPT", "BLOCK": "DROP"}.get(act, act)
        port = str(r.get("port") or "").strip()
        out: list[str] = []
        # 源 × 目标 → 一个网段一条命令；IPv6 必须走 ip6tables（iptables 只认
        # v4，拿 v6 地址喂它会报「host/network not found」）。只要两侧有一侧
        # 是 v6，整条规则就得走 ip6tables，否则地址族对不上。
        for src, cidr in _cross(r.get("src"), r.get("dst")):
            parts = ["ip6tables" if (ipaddr.family(cidr) == 6 or
                                     ipaddr.family(src) == 6) else "iptables",
                     "-t", table, "-A", chain]
            if proto and proto != "any":
                parts += ["-p", proto]
            if src:
                parts += ["-s", src]
            if cidr:
                parts += ["-d", cidr]
            if r.get("iface"):
                parts += ["-i", r["iface"]]
            if port:
                parts += ["--dport", port]
            parts += ["-j", act]
            if r.get("comment"):
                parts += ["-m", "comment", "--comment", f'"{r["comment"]}"']
            out.append(" ".join(parts))
        return out

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
        # netsh 的 remoteip 本身就支持逗号分隔多个地址（IPv4/IPv6 都行），
        # 所以这里不拆命令，把一框多值归一化成 `a,b,c` 一次下发。
        # 归一化必须做：地址库里带出来的是换行分隔，直接塞进命令行会断成两截。
        src = ipaddr.join([c for c, _f in _split_pairs(r.get("src"))])
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


# ==================================================================== Synology
# 群晖的防火墙**不是靠 iptables 持久化的**。DSM 界面里配的规则写在
# /usr/syno/etc/firewall.d/<n>.json，再由 synofirewall 翻译成 iptables 规则；
# 直接 `iptables -A` 加进去的那条只活到下次重载/重启 —— 用户在 DSM 里配的
# 规则重启还在，靠的就是这份 JSON。所以这里改的是配置文件本身。
#
# 真机（DSM 7.2 / geminilake_224+）逐字段核对过的语义：
#   policy     0=允许  1=拒绝
#   protocol   1=tcp  2=udp  3=全部  4=其它（实测生成 -p esp/ah，只读）
#   ipDirect   恒为 1。**群晖 NAS 的规则只有来源 IP，没有目标 IP**，
#              目标恒为 any（DSM 的规则表里连这一列都不显示）。
#   ipGroup    0=所有 IP  1=自定义（配 ipList）  3/5=位置/系统保留（只读）
#   ipType     0=自定义 IPv4  2=位置组
#   portGroup  0=所有端口  1=自定义端口
#   portList   端口字符串，或群晖内置服务名（"ssh" / "ntp" / "cifs"…）
# ipList 是「地址 + 掩码」两元素形态：["192.168.3.0","255.255.255.0"]，
# 单主机就是 ["1.2.3.4"]。
#
# 写流程（一条 shell 里做完，不留中间态）：
#   sha256 对不上 → 停（有人刚在 DSM 界面改过）
#   → cp -p 备份 → python3 改 JSON → jq 校验 → --update + --reload
#   → --enum 数一遍，规则集空了就拿备份还原再 reload
_SYNO_DIR = "/usr/syno/etc/firewall.d"
_SYNO_BIN = "/usr/syno/bin/synofirewall"
_SYNO_PROTO = {1: "tcp", 2: "udp", 3: ""}       # 空串 = 协议不限
_PROTO_TO_SYNO = {"tcp": 1, "udp": 2, "any": 3, "": 3, "all": 3}
_SYNO_ALLOW = ("allow", "accept", "pass")


def _mask_to_prefix(addr: str, mask: str) -> int | None:
    """"255.255.255.0" → 24。掩码不连续或不合法时给 None（按原文显示）。"""
    import ipaddress
    try:
        return ipaddress.IPv4Network(f"{addr}/{mask}", strict=False).prefixlen
    except ValueError:
        return None


def _syno_iplist(cidr: str) -> list[str]:
    """CIDR → 群晖的 ["地址","掩码"] 形态（/32 只留地址）。"""
    import ipaddress
    if not cidr:
        return []
    try:
        n = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return [cidr]
    if n.version != 4:
        return [cidr]
    if n.prefixlen == 32:
        return [str(n.network_address)]
    return [str(n.network_address), str(n.netmask)]


def _plain_ports(text) -> list[str]:
    """端口框 → 群晖 portList（逗号/空格/中文逗号都认）。"""
    out = []
    for x in re.split(r"[,，\s]+", str(text or "").strip()):
        if x:
            out.append(x)
    return out


def _ports_are_plain(plist) -> bool:
    """全是数字/范围才算「能安全改写」；服务名改写后语义会变。"""
    return all(re.fullmatch(r"\d{1,5}(-\d{1,5})?", str(x)) for x in plist)


class SynoBackend(FirewallBackend):
    key = "synofirewall"
    label = "群晖防火墙（配置文件）"

    def __init__(self, session=None, device=None):
        super().__init__(session, device)
        self.docs: dict[str, dict] = {}
        self.shas: dict[str, str] = {}
        self.profiles: dict[str, str] = {}      # 文件名 → profile 名
        self.cur_profile = ""
        self.cur_file = ""
        self.fw_on = True

    # ---------------- 探测 / 读
    def probe_cmd(self) -> str:
        # 注意是 `-f` 不是 `-x`：真机上 /usr/syno/bin/synofirewall 的权限是
        # `-r-xr-x---`（root:root），普通管理员账号**不可执行**，用 test -x
        # 会让整条探测短路，群晖永远认不出来。文件在不在，stat 一下就够。
        return (f"test -f {_SYNO_BIN} && test -d {_SYNO_DIR} && "
                f"echo SYNO_FW_OK && cat {_SYNO_DIR}/firewall_settings.json "
                "2>/dev/null")

    def read_cmd(self) -> str:
        return f"""D={_SYNO_DIR}
echo '@@@TB-SETTINGS'
cat "$D/firewall_settings.json" 2>/dev/null
for f in "$D"/*.json; do
  [ -f "$f" ] || continue
  case "$f" in *firewall_settings*) continue ;; esac
  echo "@@@TB-FILE $(basename "$f") $(sha256sum "$f" 2>/dev/null | cut -d' ' -f1)"
  cat "$f" 2>/dev/null
done"""

    @staticmethod
    def _blocks(text: str) -> dict:
        """@@@TB-XXX 分节 → {节名: 正文}。JSON 是多行的，只能按行切。"""
        got: dict[str, str] = {}
        cur, buf = None, []
        for ln in (text or "").splitlines():
            if ln.startswith("@@@TB-SETTINGS"):
                if cur:
                    got[cur] = "\n".join(buf)
                cur, buf = "@@settings", []
            elif ln.startswith("@@@TB-FILE "):
                if cur:
                    got[cur] = "\n".join(buf)
                cur, buf = ln[len("@@@TB-FILE "):].strip(), []
            else:
                buf.append(ln)
        if cur:
            got[cur] = "\n".join(buf)
        return got

    def parse(self, text: str) -> list[dict]:
        blocks = self._blocks(text)
        self.docs, self.shas, self.profiles = {}, {}, {}
        settings = {}
        try:
            settings = json.loads(blocks.get("@@settings") or "{}")
        except ValueError:
            settings = {}
        self.cur_profile = str(settings.get("profile") or "")
        self.fw_on = bool(settings.get("status"))

        rows: list[dict] = []
        for head, body in blocks.items():
            if head == "@@settings" or not (body or "").strip():
                continue
            parts = head.split(" ", 1)
            fname = parts[0]
            sha = parts[1].strip() if len(parts) > 1 else ""
            self.shas[fname] = sha
            try:
                doc = json.loads(body)
            except ValueError:
                continue
            if not isinstance(doc, dict):
                continue
            self.docs[fname] = doc
            prof = str(doc.get("name") or fname)
            self.profiles[fname] = prof
            rules = doc.get("rules") or {}
            if not isinstance(rules, dict):
                continue
            for key, arr in rules.items():
                if not isinstance(arr, list):
                    continue
                for i, it in enumerate(arr):
                    if isinstance(it, dict):
                        rows.append(self._row(fname, prof, key, i, it))
        # 当前 profile 的文件：按 name 找，别按文件名猜（真机上 1.json 叫
        # "default"、2.json 叫 "custom"，文件名跟名字毫无关系）
        self.cur_file = ""
        for fname, prof in self.profiles.items():
            if prof == self.cur_profile:
                self.cur_file = fname
                break
        if not self.cur_file and self.profiles:
            self.cur_file = sorted(self.profiles)[0]
        return rows

    def _row(self, fname: str, prof: str, key: str, idx: int,
             it: dict) -> dict:
        proto = int(it.get("protocol") or 0)
        policy = int(it.get("policy") or 0)
        group = int(it.get("ipGroup") or 0)
        itype = int(it.get("ipType") or 0)
        pgroup = int(it.get("portGroup") or 0)
        iplist = [str(x) for x in (it.get("ipList") or [])]
        plist = [str(x) for x in (it.get("portList") or [])]
        name = str(it.get("name") or "")

        if group in (3, 5) and not iplist:
            src = f"（位置组 {group}）"
        elif len(iplist) >= 2:
            pfx = _mask_to_prefix(iplist[0], iplist[1])
            src = f"{iplist[0]}/{pfx}" if pfx is not None else \
                f"{iplist[0]}/{iplist[1]}"
        elif iplist:
            src = iplist[0]
        else:
            src = "any"

        port = ",".join(plist) if plist else "所有"
        # 只读判定只用**实证过的**依据。portGroup 有 0/1/2/3 四种取值，
        # 其中 0 和 3 都表示「所有端口」（真机上 3 配空 portList 的那些
        # 规则，GUI 里显示的就是「所有」）—— 所以不能拿 portGroup 当门槛，
        # 要看 portList 里到底装的是什么。
        why = ""
        if proto not in (1, 2, 3):
            why = f"协议 {proto}（IPSec/ESP 一类）未验证，只读"
        elif group == 3:
            why = "来源是位置（GeoIP）规则，只读"
        elif group == 5 and not iplist:
            why = "来源标记为 5（系统保留/全部），语义未验证，只读"
        elif itype not in (0, 2):
            why = f"来源类型 {itype} 未验证，只读"
        elif not _ports_are_plain(plist):
            why = "端口用的是群晖内置服务名，改写会丢服务语义"

        action = "allow" if policy == 0 else "deny"
        proto_txt = _SYNO_PROTO.get(proto, f"其它({proto})")
        raw = (f"[{prof}] {'允许' if policy == 0 else '拒绝'} "
               f"{proto_txt or '全部'} {port} ← {src}")
        row = {
            "ref": f"syno:{fname}:{key}:{idx}",
            "family": "ip", "table": "filter", "handle": "",
            "chain": "所有界面" if key == "global" else key,
            "pos": idx + 1, "action": action, "proto": proto_txt,
            "src": src, "dst": "any",          # 群晖没有目标 IP
            "port": port, "iface": "", "comment": name, "raw": raw,
            "enabled": bool(it.get("enable")), "editable": not why,
            "persistent": fname, "_tone": _tone(action),
            "_syno_file": fname, "_syno_key": key, "_syno_idx": idx,
            "_syno_rule": json.loads(json.dumps(it)),
            "_readonly_why": why,
        }
        return row

    def status_cmd(self) -> str:
        return f"cat {_SYNO_DIR}/firewall_settings.json 2>/dev/null"

    def persist_note(self) -> str:
        if not self.cur_file:
            return f"规则存在 {_SYNO_DIR}/*.json"
        state = "已启用" if self.fw_on else "当前是关闭状态"
        # 界面里只留 70 个字符，写太长会被硬截成半句话
        return (f"{self.cur_file}（profile {self.cur_profile or '?'}，{state}）· "
                "跟 DSM 界面同一份配置，重启不丢 · 改完自动 reload")

    # ---------------- 校验
    def validate(self, r: dict) -> str | None:
        # 这条最要紧：用户在界面上填了目标 IP，群晖没有对应字段。
        # 静默丢掉会让人以为目标限制生效了，所以直接拒绝并说清楚。
        if _split_pairs(r.get("dst")):
            return ("群晖的防火墙规则只有「来源 IP」，配置文件里没有目标 IP "
                    "字段（DSM 界面连这一列都不显示，目标恒为 any）。"
                    "要按目标地址限制，请用 iptables / nftables / firewalld，"
                    "或者把规则改成按来源收窄。")
        ok, bad = ipaddr.split(r.get("src"))
        if bad:
            return (f"来源地址写错了：{bad[0]}"
                    "（IPv4 如 192.168.1.0/24）")
        for c in ok:
            if c != "any" and ipaddr.family(c) == 6:
                return ("群晖配置文件里的 IPv6 规则用的是另一套 ipType，"
                        "没在真机验证过，不替你猜。IPv6 请用 iptables / nftables。")
        n = len([c for c in ok if c != "any"])
        if n > _MAX_RULES:
            return (f"来源有 {n} 个网段，超过单条规则上限 {_MAX_RULES}，"
                    "请分成多条规则")
        port = str(r.get("port") or "").strip()
        if port and not re.fullmatch(r"[\d,，\s\-]+", port):
            return ("端口只填数字（22 / 80,443 / 1000-2000）；"
                    "群晖内置服务名请在 DSM 界面里选")
        proto = str(r.get("proto") or "").lower()
        if proto not in _PROTO_TO_SYNO:
            return f"群晖只支持 tcp / udp / 全部，不支持 {proto}"
        act = str(r.get("action") or "").lower()
        if act not in _SYNO_ALLOW + ("deny", "drop", "block", "reject"):
            return f"群晖只支持「允许 / 拒绝」，不支持 {act}"
        if not self.cur_file:
            return "没读到群晖的防火墙配置文件（需要 root 权限读 /usr/syno/etc/firewall.d）"
        return None

    # ---------------- 写
    def _rule_item(self, r: dict, cidr: str, keep: dict | None = None) -> dict:
        port = _plain_ports(r.get("port"))
        act = str(r.get("action") or "").lower()
        pgroup = 1 if port else 0
        # 编辑「所有端口」的规则时沿用原来的 portGroup：真机上 0 和 3 都
        # 表示「所有端口」，改写成一个值可能让群晖的语义变掉。
        if keep is not None and not port:
            old_pg = int(keep.get("portGroup") or 0)
            if old_pg in (0, 3):
                pgroup = old_pg
        return {
            "adapterDirect": 1, "blLog": False,
            "chainList": ["FORWARD_FIREWALL", "INPUT_FIREWALL"],
            "enable": True, "ipDirect": 1,
            "ipGroup": 1 if cidr else 0,
            "ipList": _syno_iplist(cidr),
            "ipType": 0, "labelList": [],
            "name": str(r.get("comment") or ""),
            "policy": 0 if act in _SYNO_ALLOW else 1,
            "portDirect": 0,
            "portGroup": pgroup,
            "portList": port,
            "protocol": _PROTO_TO_SYNO.get(
                str(r.get("proto") or "").lower(), 3),
            "ruleIndex": 0, "table": "filter",
        }

    def add_cmds(self, r: dict) -> list[str]:
        err = self.validate(r)
        if err:
            raise FirewallError(err)
        srcs = [c for c, _f in _split_pairs(r.get("src"))] or [""]
        items = [self._rule_item(r, s) for s in srcs]
        return [self._write_cmd("add", key="global",
                                items=items, pos=len(self._rows_global()))]

    def set_cmds(self, old: dict, new: dict) -> list[str]:
        """就地替换，不走「先删后加」—— 中间态会让那条规则短暂消失。"""
        err = self.validate(new)
        if err:
            raise FirewallError(err)
        if not old.get("editable"):
            why = old.get("_readonly_why") or "这条规则只读"
            raise FirewallError(
                f"{why}；要改请在 DSM 界面里改，或在下面「新增规则」重建一条。")
        srcs = [c for c, _f in _split_pairs(new.get("src"))] or [""]
        key = old.get("_syno_key") or "global"
        idx = int(old.get("_syno_idx") or 0)
        keep = old.get("_syno_rule")
        keep = keep if isinstance(keep, dict) else None
        if len(srcs) > 1:
            # 一条群晖规则只带一个来源网段，多来源只能拆：先删旧的再加多条
            return (self._write_cmd("del", key=key, idx=idx)
                    + [self._write_cmd(
                        "add", key=key,
                        items=[self._rule_item(new, s, keep=keep) for s in srcs],
                        pos=idx)])
        return [self._write_cmd("set", key=key, idx=idx,
                                rule=self._rule_item(new, srcs[0], keep=keep))]

    def del_cmds(self, r: dict) -> list[str]:
        if r.get("_syno_file") is None or r.get("_syno_idx") is None:
            raise FirewallError("这条规则不是从群晖配置文件里读出来的，无法确定位置")
        return [self._write_cmd("del", key=r.get("_syno_key") or "global",
                                idx=int(r["_syno_idx"]))]

    def toggle_cmds(self, r: dict, enable: bool) -> list[str]:
        if r.get("_syno_idx") is None:
            raise FirewallError("这条规则不是从群晖配置文件里读出来的")
        return [self._write_cmd("toggle", key=r.get("_syno_key") or "global",
                                idx=int(r["_syno_idx"]), enable=bool(enable))]

    def move_cmds(self, r: dict, delta: int) -> list[str]:
        i = int(r.get("_syno_idx") or 0)
        arr = self._rows_global(r.get("_syno_key") or "global")
        j = i + (1 if delta > 0 else -1)
        if j < 0:
            raise FirewallError("已经在最前面了")
        if j >= len(arr):
            raise FirewallError("已经在最后面了")
        return [self._write_cmd("move", key=r["_syno_key"],
                                idx=i, delta=(1 if delta > 0 else -1))]

    def _rows_global(self, key: str = "global") -> list:
        doc = self.docs.get(self.cur_file) or {}
        rules = doc.get("rules") or {}
        arr = rules.get(key) if isinstance(rules, dict) else None
        return arr if isinstance(arr, list) else []

    def _write_cmd(self, op: str, key: str = "global", items=None,
                   idx: int | None = None, rule=None, pos: int | None = None,
                   enable: bool = True, delta: int = 0) -> str:
        import base64
        import shlex

        fname = self.cur_file or "1.json"
        path = f"{_SYNO_DIR}/{fname}"
        sha = self.shas.get(fname, "")
        payload = {"op": op, "key": key, "items": items or [],
                   "idx": -1 if idx is None else int(idx),
                   "rule": rule, "pos": pos, "enable": bool(enable),
                   "delta": int(delta)}
        b64 = base64.b64encode(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode()
        # 远端脚本用 heredoc 传：里面的引号、中文都不用转义
        script = f"""set -e
F={shlex.quote(path)}
CUR=$(sha256sum "$F" 2>/dev/null | cut -d' ' -f1)
if [ -n {shlex.quote(sha)} ] && [ "$CUR" != {shlex.quote(sha)} ]; then
  echo "TB:STALE $F 在读取之后被改过（有人刚在 DSM 界面里动过规则），先刷新规则再试"
  exit 9
fi
B="$F.tb-bak-$(date +%Y%m%d-%H%M%S)"
cp -p "$F" "$B" || {{ echo "TB:NOBACKUP 备份失败，不动配置"; exit 8; }}
python3 - "$F" {shlex.quote(b64)} <<'TBEOF'
import base64, json, os, re, sys
p, b64 = sys.argv[1], sys.argv[2]
d = json.load(open(p, encoding="utf-8"))
a = json.loads(base64.b64decode(b64).decode("utf-8"))
key = a.get("key") or "global"
arr = d.setdefault("rules", {{}}).setdefault(key, [])
op, i = a["op"], a["idx"]
if op == "add":
    pos = a.get("pos")
    pos = len(arr) if pos is None or pos < 0 or pos > len(arr) else pos
    for it in a["items"]:
        arr.insert(pos, it)
        pos += 1
elif op in ("del", "set", "toggle"):
    if not (0 <= i < len(arr)):
        raise SystemExit("TB:INDEX 规则位置对不上（配置可能刚被改过），刷新后再试")
    if op == "del":
        arr.pop(i)
    elif op == "set":
        arr[i] = a["rule"]
    else:
        arr[i]["enable"] = bool(a["enable"])
elif op == "move":
    j = i + int(a["delta"])
    if not (0 <= i < len(arr) and 0 <= j < len(arr)):
        raise SystemExit("TB:EDGE 已经到头了")
    arr[i], arr[j] = arr[j], arr[i]
else:
    raise SystemExit("TB:OP 不认识的操作 " + op)
tmp = p + ".tb-tmp"
with open(tmp, "w", encoding="utf-8") as f:
    s = json.dumps(d, indent=3, ensure_ascii=False)
    s = re.sub(r'^(\\s*)"([^"]+)": ', r'\\1"\\2" : ', s, flags=re.M)
    f.write(s + "\\n")
os.chmod(tmp, 0o644)
os.replace(tmp, p)
print("TB:WROTE")
TBEOF
if ! (jq -e . "$F" >/dev/null 2>&1 || python3 -c 'import json,sys;json.load(open(sys.argv[1]))' "$F"); then
  cp -p "$B" "$F"
  echo "TB:BADJSON 写出的 JSON 不合法，已还原 $B"
  exit 7
fi
{_SYNO_BIN} --update >/dev/null 2>&1 || true
{_SYNO_BIN} --reload >/dev/null 2>&1 || true
sleep 1
N=$({_SYNO_BIN} --enum IPV4 2>/dev/null | grep -c '^-t filter\\|^-A ' || true)
if [ "${{N:-0}}" -lt 1 ]; then
  cp -p "$B" "$F"
  {_SYNO_BIN} --update >/dev/null 2>&1 || true
  {_SYNO_BIN} --reload >/dev/null 2>&1 || true
  echo "TB:EMPTY 重载后规则集空了，已还原 $B"
  exit 6
fi
echo "TB:OK n=$N backup=$B"
"""
        return script


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
        # ipfw 一条规则只能一个 from / 一个 to，多网段拆成多条。
        out: list[str] = []
        for src, dst in _cross(r.get("src"), r.get("dst")):
            parts = ["ipfw", "add", act, proto,
                     "from", src or "any", "to", dst or "any"]
            if r.get("port"):
                parts += ["dst-port", str(r["port"])]
            out.append(" ".join(parts))
        return out

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
        port = str(r.get("port") or "").strip()
        proto = str(r.get("proto") or "").lower()
        if proto == "any":
            proto = ""
        spec = ""
        if port:
            spec = port if (proto and "/" in port) else (
                f"{port}/{proto}" if proto else port)
        pairs = _cross(r.get("src"), r.get("dst"))
        if pairs == [("", "")]:
            # 两侧都不限：ufw 的简写形态（允许 22/tcp）
            if spec:
                return [f"ufw {act} {spec}"]
            if proto:
                return [f"ufw {act} {proto}"]
            return [f"ufw {act}"]
        out: list[str] = []
        # ufw 一条规则只表达一个 from/to，多网段只能拆成多条命令。
        for src, dst in pairs:
            if src:
                tail = f" to {dst or 'any'} port {port}" if port else (
                    f" to {dst}" if dst else "")
                cmd = f"ufw {act} from {src}{tail}"
                if port and proto:
                    cmd += f" proto {proto}"
                out.append(cmd)
            elif dst and port:
                cmd = f"ufw {act} to {dst} port {port}"
                if proto:
                    cmd += f" proto {proto}"
                out.append(cmd)
            elif dst:
                out.append(f"ufw {act} to {dst}")
            else:
                out.append(f"ufw {act} {spec or proto}")
        return out

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
        out: list[str] = []
        # rich rule 的 family 原来是写死的 ipv4 —— 填 IPv6 必错。现在按地址
        # 自己判；一个框里多个来源就生成多条 rich rule（一条一个 source）。
        for src, _dst in _cross(r.get("src"), None):
            if src:
                fam = "ipv6" if ipaddr.family(src) == 6 else "ipv4"
                rule = (f"rule family={fam} source address={src} "
                        + (f"port port={val.split('/')[0]} "
                           f"protocol={val.split('/')[1]} "
                           if kind == "port" else "")
                        + "accept")
                out.append(f"firewall-cmd --permanent --add-rich-rule='{rule}'")
            else:
                out.append(f"firewall-cmd --permanent --add-{kind}={val}")
        out.append("firewall-cmd --reload")
        return out

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
    "synofirewall": SynoBackend,
}

# 优先级 = 「哪个更能代表这台设备真正在用的防火墙」。
# 网络设备上不能一股脑全跑（一条命令卡 10 秒），所以 detect() 是逐个试、
# 一探到同档里优先级最高的就停。
# 群晖排最前：它底层也是 iptables，但持久化在 firewall.d/*.json 里 ——
# 认出群晖就别去改 iptables 了，那改完重启就没了。
PROBE_ORDER = ("synofirewall", "ufw", "firewalld", "nftables", "iptables",
               "netsh", "pf", "ipfw")


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
    # 群晖排第一个：非群晖机器上这条探测是 `test -x ... && ...`，立刻返回
    # 非 0，代价可以忽略；是群晖就必须由它接管，不能落到裸 iptables。
    return [SynoBackend(), UfwBackend(), FirewalldBackend(), NftBackend(),
            IptablesBackend()]


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
    if key == "synofirewall":
        # 只有「二进制 + 配置目录 + 设置文件」三样都在才算 —— 光有
        # /usr/syno/bin/synofirewall 却读不到 firewall.d 的机器不能接管。
        if "SYNO_FW_OK" in t:
            prof = ""
            m = re.search(r'"profile"\s*:\s*"([^"]+)"', t)
            if m:
                prof = m.group(1)
            return 2, (f"群晖 DSM 防火墙 · profile {prof}" if prof
                       else "群晖 DSM 防火墙（配置文件）")
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
