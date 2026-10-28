# -*- coding: utf-8 -*-
"""区域策略：把「某个省 / 某个国家」变成一张 nftables 独立表，一条规则生效。

为什么不能像别处那样「一个网段一条规则」：
  广东省 26,609 条网段、一个国家动辄十几万条。iptables 那套逐条 `-A` 的写法
  光是下发就要跑几分钟，中途断了还留个半成品；群晖那种后端更直接（每个配置
  最多 100 条规则），根本装不下。nftables 的 interval set 天生干这个：
  `type ipv4_addr; flags interval; auto-merge`，两万多条元素压成一棵树，
  一条 `ip saddr @region_v4 drop` 就完事，下发是**一次事务**，失败自动回滚。

为什么只做「拒绝」不做「放行」：
  本工具的表挂在 `hook input priority -10`，nft 里 `drop` 在这一层是终局
  （后面的链接不到包），所以「拒绝这些区域」立刻有效。反过来 `accept` 只对
  当前这条链有效，包还会继续往下走系统自己的链 —— 用户在系统里配的拒绝照样
  把包丢掉。写成「放行」就是在骗人：界面上打着勾，实际不生效。
  真要做白名单，得把系统链的默认策略改成 drop，那不是本工具该替用户决定的事。
  所以这一页只提供拒绝；想要「只允许中国 IP 访问 443」请用系统防火墙的
  默认拒绝 + 本模块的拒绝表配合，界面上会把这话说清楚。

三条硬约束（都写进闸门了）：
  1. 只碰自己那张表（`tb_region`），脚本里**绝不出现** `flush ruleset`，
     也不碰 `inet filter` / `ip filter` 这些系统表。
  2. 下发前先查「这批网段是不是把当前这条连接也包进去了」—— 封到自己头上
     等于一次远程自杀，实测很难救回来。命中就拒绝，除非用户显式确认。
  3. 下发后回读一遍元素数量，和计划不符就明确报错（不许「看着成功」）。
"""
from __future__ import annotations

import base64
import ipaddress
import json
from dataclasses import dataclass, field

from core import iplib

TABLE = "tb_region"
SET_V4 = "region_v4"
SET_V6 = "region_v6"
HOOK_PRIORITY = -10       # 早于系统链（默认 0），drop 才是终局

# nft 只认真实路径：飞牛 / 群晖的 PATH 都缺 /usr/sbin（`command -v nft`
# 恒为 MISSING，这是实测踩过的），所以只认绝对路径，不依赖 PATH。
NFT_PATHS = ("/usr/sbin/nft", "/sbin/nft", "/usr/bin/nft", "/bin/nft",
             "/usr/local/sbin/nft", "/usr/local/bin/nft")

# 元素总量上限。nft 自己能塞几十万，这里卡一道是防手滑（选了几十个国家
# 一次性下发，设备要解析很久）。超过就让人先减少条目。
MAX_ELEMS = 200000

VERDICT_LABEL = {"drop": "拒绝（直接丢包）", "reject": "拒绝（回 ICMP）"}


class RegionError(Exception):
    """带人话提示的失败。`hint` 给界面第二行。"""

    def __init__(self, msg: str, hint: str = "") -> None:
        super().__init__(msg)
        self.hint = hint


@dataclass
class Caps:
    """这台设备能不能自动管区域表。"""
    nft: str = ""                 # nft 绝对路径；空 = 没装
    version: str = ""             # 第一行版本字符串
    admin: bool = False           # 能不能提到 root（nft 改规则必须 root）
    why: str = ""                 # 不能自动管的原因（给界面显示）

    @property
    def ok(self) -> bool:
        return bool(self.nft) and self.admin

    @property
    def label(self) -> str:
        if self.ok:
            return "nftables 独立表"
        if self.nft:
            return "只能导出脚本"
        return "不支持"

    def summary(self) -> str:
        if self.ok:
            ver = (self.version.replace("nftables ", "").strip()
                   .lstrip("v").strip()) or "?"
            return f"{self.nft} · v{ver} · 可自动下发"
        return self.why or "这台设备不能自动下发区域策略"


@dataclass
class Plan:
    """一次「拒绝这些区域」的完整计划。"""
    picks: list[dict] = field(default_factory=list)   # [{key,name,group}]
    v4: list[str] = field(default_factory=list)       # 合并后的 IPv4 网段
    v6: list[str] = field(default_factory=list)
    verdict: str = "drop"
    raw_v4: int = 0                                   # 合并前的条数（界面显示压缩比）
    raw_v6: int = 0

    @property
    def total(self) -> int:
        return len(self.v4) + len(self.v6)

    @property
    def empty(self) -> bool:
        return not self.v4 and not self.v6

    def names(self) -> str:
        return "、".join(str(p.get("name") or p.get("key")) for p in self.picks)

    def summary(self) -> str:
        if self.empty:
            return "没有可用的网段"
        s = f"{self.names()} → {self.total} 条网段"
        if self.raw_v4 + self.raw_v6 > self.total:
            s += f"（原始 {self.raw_v4 + self.raw_v6} 条，已合并）"
        return s


# ------------------------------------------------------------------ 探测
PROBE_SH = (
    "for p in " + " ".join(NFT_PATHS) + '; do '
    'if [ -x "$p" ]; then echo "@@NFT $p"; '
    '"$p" --version 2>&1 | head -1; break; fi; done'
)


def probe(sess) -> Caps:
    """只读探测：找 nft、读版本、判断能不能提权。不改任何东西。"""
    caps = Caps()
    try:
        _rc, out, _err = sess.run(PROBE_SH, timeout=20)
    except Exception as e:  # noqa: BLE001
        caps.why = f"探测失败：{type(e).__name__}: {e}"
        return caps
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("@@NFT "):
            caps.nft = line[6:].strip()
        elif line.startswith("nftables") or "nftables v" in line:
            caps.version = line
    if not caps.nft:
        caps.why = ("这台设备上没有 nft（老 iptables 系）。区域策略靠 nft 的"
                    " interval set 才能一条规则顶几万条网段；这里只能导出"
                    " iptables 脚本给你自己跑。")
        return caps
    try:
        ok, why = sess.can_admin()
    except Exception as e:  # noqa: BLE001
        ok, why = False, f"{type(e).__name__}: {e}"
    caps.admin = bool(ok)
    if not ok:
        caps.why = ("有 nft，但改规则要 root：" + (why or "需要 sudo 密码") +
                    "。到「主机管理」给这条档案填 sudo 密码，"
                    "或者用「导出脚本」自己跑。")
    return caps


# ------------------------------------------------------------------ 计划
def make_plan(picks: list[dict], verdict: str = "drop") -> Plan:
    """把勾选的区域展开成网段并合并。

    `picks` 只认名字（`{"key","name","group"}`），不认调用方传进来的网段 ——
    网段必须从 `iplib` 现取，避免界面把过期的数据带下来（地址库可以更新）。
    """
    if verdict not in VERDICT_LABEL:
        raise RegionError(f"不认识的动作：{verdict}")
    plan = Plan(picks=list(picks or []), verdict=verdict)
    if not plan.picks:
        raise RegionError("先勾选至少一个区域",
                          "省级在「中国省级」组，国家在「国家/地区」组。")
    items = []
    for p in plan.picks:
        group = str(p.get("group") or "province")
        key = str(p.get("key") or "")
        hit = [it for it in iplib.search(group, key) if it.key == key]
        if not hit:
            raise RegionError(f"地址库里没有这一项：{key or p.get('name')}",
                              "可能地址库还没更新，去「地址库」页点一次更新。")
        it = hit[0]
        if not it.cidrs:
            raise RegionError(
                f"「{it.name}」这一项还没有网段数据",
                "地址库里只存了名字、没下载数据。到「地址库」点「更新」再试。")
        items.append(it)
    flat = [c for it in items for c in it.cidrs]
    plan.raw_v4 = sum(1 for c in flat if _fam(c) == 4)
    plan.raw_v6 = sum(1 for c in flat if _fam(c) == 6)
    merged = iplib.expand_items(items)          # 已按族分开合并
    plan.v4 = [c for c in merged if _fam(c) == 4]
    plan.v6 = [c for c in merged if _fam(c) == 6]
    if plan.total > MAX_ELEMS:
        raise RegionError(
            f"一共 {plan.total:,} 条网段，超过一次能下发的上限 "
            f"{MAX_ELEMS:,} 条",
            "少选几个区域，或者分几次下发（每次都是整表重建，后一次会覆盖前一次）。")
    return plan


def _fam(c: str) -> int:
    try:
        return ipaddress.ip_network(str(c), strict=False).version
    except ValueError:
        return 0


# ------------------------------------------------------------------ 防自锁
def covers(cidrs, addr: str) -> str:
    """`addr` 是否落在 `cidrs` 里；命中返回那条网段（给提示用），否则空串。"""
    try:
        a = ipaddress.ip_address(str(addr).strip())
    except ValueError:
        return ""
    for c in cidrs or []:
        try:
            n = ipaddress.ip_network(str(c), strict=False)
        except ValueError:
            continue
        if n.version == a.version and a in n:
            return str(c)
    return ""


def lockout(plan: Plan, addrs) -> str:
    """这批网段会不会把当前连接也封掉。命中返回人话，否则空串。

    为什么必须有这道闸：跑这条命令的 SSH 连接本身就来自某个地址，
    选「拒绝中国」几乎必然把自己包进去 —— 一按下去终端就断了，而且
    远端 root 表已经落盘，只能去机房/控制台救。实测代价太大，直接拒绝。
    """
    for ip in addrs or []:
        ip = str(ip or "").strip()
        if not ip:
            continue
        hit = covers(plan.v4, ip) or covers(plan.v6, ip)
        if hit:
            return (f"这条策略会把当前连接的地址 {ip} 一起封掉"
                    f"（命中 {hit}），一执行这台设备就连不上了。")
    return ""


def peer_addrs(sess) -> list[str]:
    """当前这条 SSH 连接的两个地址：设备侧、以及设备看到的我们这一侧。

    `$SSH_CLIENT` 是 sshd 记下的「谁连过来的」，比猜本机网卡准 ——
    走 NAT / 跳板时只有它知道对端真实地址。拿不到就退回 session.host。
    """
    out: list[str] = []
    try:
        _rc, txt, _err = sess.run(
            'echo "$SSH_CLIENT"; echo "$SSH_CONNECTION"', timeout=15)
    except Exception:  # noqa: BLE001
        txt = ""
    for line in (txt or "").splitlines():
        parts = line.split()
        if parts:
            out.append(parts[0])
    host = str(getattr(sess, "host", "") or "")
    if host:
        out.append(host)
    seen, uniq = set(), []
    for a in out:
        a = a.strip()
        if a and a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq


# ------------------------------------------------------------------ 生成脚本
def nft_script(plan: Plan, *, exists: bool = False,
               table: str = TABLE) -> str:
    """生成 `nft -f` 用的整表脚本（一次事务，失败自动回滚）。

    `exists=True` 时先 `delete table` —— 整表重建比「删元素再补」简单得多，
    而且 nft 的 -f 是一次原子事务：脚本里任何一行不合法，整批都不生效，
    表还是原来那份，不会留下半套规则。
    """
    if plan.empty:
        raise RegionError("没有网段可下发")
    act = plan.verdict
    out: list[str] = []
    if exists:
        out.append(f"delete table inet {table}")
    out.append(f"table inet {table} {{")
    for name, cidrs, typ in ((SET_V4, plan.v4, "ipv4_addr"),
                             (SET_V6, plan.v6, "ipv6_addr")):
        if not cidrs:
            continue
        out.append(f"\tset {name} {{")
        out.append(f"\t\ttype {typ}")
        out.append("\t\tflags interval")
        out.append("\t\tauto-merge")
        out.append("\t\telements = {")
        out.extend("\t\t\t" + c + "," for c in cidrs)
        out.append("\t\t}")
        out.append("\t}")
    out.append("")
    out.append("\tchain input {")
    out.append(f"\t\ttype filter hook input priority {HOOK_PRIORITY}; "
               "policy accept;")
    # 已建立的连接先放行：不然「拒绝某区域」会把正在用的长连接一起掐掉
    # （drop 在这一层是终局，后面谁都救不回来）。
    out.append("\t\tct state established,related accept")
    if plan.v4:
        out.append(f"\t\tip saddr @{SET_V4} {act}")
    if plan.v6:
        out.append(f"\t\tip6 saddr @{SET_V6} {act}")
    out.append("\t}")
    out.append("}")
    return "\n".join(out) + "\n"


def plain_script(plan: Plan, chain: str = "INPUT") -> str:
    """没有 nft / 提不了权时的兜底：生成 iptables 脚本，用户自己跑。

    故意不自动执行：几千条 `-A` 在远端跑几分钟，中途断了没有回滚办法，
    而 nft 那边是原子的。既然做不到「安全地下发」，就只把脚本给出去。
    """
    if plan.empty:
        raise RegionError("没有网段可下发")
    head = [
        "#!/bin/sh",
        "# 区域策略（" + VERDICT_LABEL.get(plan.verdict, plan.verdict) + "）",
        "# 由 Terminal GUI Toolbox 生成。没有 nft 的设备只能这样逐条下发，",
        "# 命令很多（" + str(plan.total) + " 条），中途断了没有回滚。",
        "# 想撤销：把下面的 -A 换成 -D 再跑一遍。",
        "set -e",
        "",
    ]
    body = []
    for c in plan.v4:
        body.append(f"iptables -A {chain} -s {c} -j DROP")
    for c in plan.v6:
        body.append(f"ip6tables -A {chain} -s {c} -j DROP")
    return "\n".join(head + body) + "\n"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# 一次 echo 能把多少字节塞进同一条命令。Linux 单个参数上限是 128KB
# （MAX_ARG_STRLEN），base64 再把体积放大 4/3 —— 广东省那份脚本约 500KB，
# 一次性 `echo '<b64>' | nft -f -` 直接报 “Argument list too long”，
# 而且在自动测试里表现为 rc 空、什么都没发生（实测踩到）。
# 所以按 24KB 原始字节分块追加，每块 base64 后 32KB，离上限还有 4 倍余量。
CHUNK = 24000
REMOTE_FILE = "/tmp/.tb_region.nft"


def upload(sess, text: str, path: str = REMOTE_FILE) -> tuple[bool, str]:
    """把脚本写到设备上的临时文件（分块，避免撞 shell 参数长度上限）。

    上传**不用提权**：/tmp 谁都能写，只有最后 `nft -f` 需要 root。
    这样只要 sudo 密码对，中间几十次写入不会反复触发 sudo。
    """
    data = text.encode("utf-8")
    rc, out, err = sess.run(f": > {path} 2>&1; echo \"@@RC=$?\"", timeout=20)
    if "@@RC=0" not in (out or ""):
        return False, f"建不了临时文件 {path}：{(out or err or '').strip()[:200]}"
    for i in range(0, len(data), CHUNK):
        part = base64.b64encode(data[i:i + CHUNK]).decode("ascii")
        rc, out, err = sess.run(
            f"echo '{part}' | base64 -d >> {path} 2>&1; echo \"@@RC=$?\"",
            timeout=60)
        if "@@RC=0" not in (out or ""):
            return False, (f"写第 {i // CHUNK + 1} 块失败："
                           f"{(out or err or '').strip()[:200]}")
    rc, out, err = sess.run(f"wc -c < {path}", timeout=20)
    try:
        got = int((out or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        got = -1
    if got != len(data):
        return False, f"上传后大小对不上：设备 {got} 字节，本地 {len(data)} 字节"
    return True, f"{got} 字节"


def apply_script(nft: str, path: str = REMOTE_FILE) -> str:
    """套上 shell 包装：执行临时文件里的表 + 删掉临时文件。"""
    return (f"{nft} -f {path} 2>&1; rc=$?; "
            f"rm -f {path}; echo \"@@RC=$rc\"")


# ------------------------------------------------------------------ 下发 / 读取
def apply(sess, plan: Plan, caps: Caps | None = None,
          table: str = TABLE) -> tuple[int, str, str]:
    """下发整表。返回 (rc, out, err)，rc != 0 时 out 里是 nft 的报错原文。"""
    caps = caps or probe(sess)
    if not caps.ok:
        raise RegionError("这台设备不能自动下发区域策略", caps.why or "")
    st = state(sess, caps, table)
    script = nft_script(plan, exists=bool(st.get("exists")), table=table)
    ok, info = upload(sess, script)
    if not ok:
        raise RegionError("脚本传不到设备上", info)
    cmd = apply_script(caps.nft)
    rc, out, err = sess.run_admin(cmd, timeout=180)
    text = (out or "") + (err or "")
    m = None
    for line in text.splitlines():
        if line.startswith("@@RC="):
            m = line[5:].strip()
    try:
        real_rc = int(m) if m is not None else rc
    except ValueError:
        real_rc = rc
    body = "\n".join(ln for ln in text.splitlines()
                     if not ln.startswith("@@RC=")).strip()
    if real_rc != 0:
        return real_rc, body, "nft 拒绝了这份表（一条都没生效：整批是一个事务）"
    # 回读核对：不能只看 rc。rc=0 但表里什么都没有（比如 set 语法被接受、
    # 元素却被静默丢掉）就是「看着成功」，必须叫出来。
    #
    # 注意**不能要求数量完全相等**：`auto-merge` 会把相邻网段并成区间，
    # 读回来的条数天然比计划少（两条相邻 /24 会变成 1.0.0.0-1.0.1.255）。
    # 所以只卡两头：有该有的、不会多出来。
    got = state(sess, caps, table)
    got_v4, got_v6 = int(got.get("v4") or 0), int(got.get("v6") or 0)
    if not got.get("exists"):
        return 1, body, "下发后读不到区域表（nft 说成功，但表不存在）"
    bad = []
    if plan.v4 and got_v4 == 0:
        bad.append("IPv4 元素是 0")
    if plan.v6 and got_v6 == 0:
        bad.append("IPv6 元素是 0")
    if got_v4 > len(plan.v4) or got_v6 > len(plan.v6):
        bad.append(f"元素比计划还多（设备 {got_v4}/{got_v6}，"
                   f"计划 {len(plan.v4)}/{len(plan.v6)}）")
    if not got.get("action"):
        bad.append("表里没有读到 drop 规则")
    if bad:
        return 1, body, "下发后回读不对：" + "；".join(bad)
    return 0, body, ""


def clear(sess, caps: Caps | None = None, table: str = TABLE) -> tuple[int, str, str]:
    """删掉整张表 —— 这就是「撤销区域策略」，系统其它规则一点不动。"""
    caps = caps or probe(sess)
    if not caps.ok:
        raise RegionError("这台设备不能自动改区域策略", caps.why or "")
    st = state(sess, caps, table)
    if not st.get("exists"):
        return 0, "本来就没有区域表（无需撤销）", ""
    cmd = (f"{caps.nft} delete table inet {table} 2>&1; echo \"@@RC=$?\"")
    rc, out, err = sess.run_admin(cmd, timeout=60)
    text = (out or "") + (err or "")
    real = rc
    for line in text.splitlines():
        if line.startswith("@@RC="):
            try:
                real = int(line[5:].strip())
            except ValueError:
                pass
    body = "\n".join(ln for ln in text.splitlines()
                     if not ln.startswith("@@RC=")).strip()
    return real, body, "" if real == 0 else "删表失败"


def parse_state(doc_text: str) -> dict:
    """`nft -j list table` 的 JSON → {exists, v4, v6, action, raw}。

    单独抽出来纯函数，是为了能拿真机抓回来的 JSON 直接测解析（不用连设备）。
    """
    res = {"exists": False, "v4": 0, "v6": 0, "action": "", "raw": doc_text[:2000]}
    try:
        doc = json.loads(doc_text or "{}")
    except json.JSONDecodeError:
        return res
    items = doc.get("nftables")
    if not isinstance(items, list) or not items:
        return res
    res["exists"] = True
    for item in items:
        st = item.get("set") or {}
        name = st.get("name") or ""
        elems = st.get("elem")
        n = len(elems) if isinstance(elems, list) else 0
        if name == SET_V4:
            res["v4"] = n
        elif name == SET_V6:
            res["v6"] = n
        rule = item.get("rule") or {}
        expr = rule.get("expr")
        if isinstance(expr, list):
            txt = json.dumps(expr, ensure_ascii=False)
            for act in ("drop", "reject"):
                if '"' + act + '"' in txt:
                    res["action"] = act
    return res


def state(sess, caps: Caps | None = None, table: str = TABLE) -> dict:
    """读当前区域表：存在与否、IPv4/IPv6 元素数、动作。

    用 `nft -j`（JSON）而不是解析文本：文本格式各版本不一样
    （有的把 elements 折成 `{ 1.0.0.0/8, ... }` 一行），JSON 是稳定的。
    """
    empty = {"exists": False, "v4": 0, "v6": 0, "action": "", "raw": ""}
    caps = caps or probe(sess)
    if not caps.nft:
        return empty
    cmd = (f"{caps.nft} -j list table inet {table} 2>/dev/null; echo \"@@RC=$?\"")
    runner = sess.run_admin if caps.admin else sess.run
    try:
        _rc, out, _err = runner(cmd, timeout=60)
    except Exception:  # noqa: BLE001
        return empty
    text = out or ""
    rc = None
    keep = []
    for line in text.splitlines():
        if line.startswith("@@RC="):
            rc = line[5:].strip()
        else:
            keep.append(line)
    if rc not in (None, "0"):
        return empty
    return parse_state("\n".join(keep))


def describe(st: dict) -> str:
    """把 state() 翻译成一句人话，给界面徽章用。"""
    if not st.get("exists"):
        return "未启用"
    n = int(st.get("v4") or 0) + int(st.get("v6") or 0)
    act = VERDICT_LABEL.get(str(st.get("action") or ""), "规则在，但没读出动作")
    return f"已启用 · {n:,} 条网段 · {act}"
