# -*- coding: utf-8 -*-
"""安全体检：把「这台机器现在有多容易被敲门」变成一张可读的清单。

设计上刻意分成两层：

  解析层   纯函数，输入是命令的原始文本，输出是 Check 列表。
           没有任何 IO、不碰 session —— 所以能用固定样本跑自测，
           不用真去连一台机器（见 `_sectest2.py`）。
  执行层   `SecurityScanner` 只负责把几条只读命令跑一遍，
           再把文本丢给解析层。

一条原则：**体检只读，绝不改任何配置**。每个 Check 带一句 `fix`，
是给用户自己复制去执行的建议命令 —— 程序不会替他跑。
改配置、重启服务这类事，归「AI 运维」或用户自己的终端。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ------------------------------------------------------------------ 等级
OK = "ok"
INFO = "info"
WARN = "warn"
BAD = "bad"

LEVEL_CN = {OK: "良好", INFO: "提示", WARN: "注意", BAD: "风险"}
LEVEL_ORDER = {BAD: 3, WARN: 2, INFO: 1, OK: 0}
_PENALTY = {BAD: 12, WARN: 5, INFO: 1, OK: 0}
# 表格只认 ok/warn/err/dim/mute/brand 这几种 tone，这里做一次映射，
# 页面就不用每处再写一遍。
TONE = {BAD: "err", WARN: "warn", INFO: "dim", OK: "ok"}


@dataclass
class Check:
    """一条体检结论。"""

    key: str
    title: str
    level: str = INFO
    detail: str = ""
    advice: str = ""
    fix: str = ""

    @property
    def level_cn(self) -> str:
        return LEVEL_CN.get(self.level, self.level)

    def as_row(self) -> dict:
        return {
            "level": self.level_cn,
            "title": self.title,
            "detail": self.detail,
            "advice": self.advice or "—",
            "_tone": TONE.get(self.level, "dim"),
            "_key": self.key,
            "fix": self.fix,
        }


@dataclass
class Report:
    score: int = 0
    checks: list[Check] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def worst(self) -> str:
        order = [c.level for c in self.checks]
        for lv in (BAD, WARN, INFO, OK):
            if lv in order:
                return lv
        return OK

    def count(self, level: str) -> int:
        return sum(1 for c in self.checks if c.level == level)

    def rows(self) -> list[dict]:
        order = {BAD: 0, WARN: 1, INFO: 2, OK: 3}
        items = sorted(self.checks, key=lambda c: (order.get(c.level, 9),
                                                   c.title))
        return [c.as_row() for c in items]

    def headline(self) -> str:
        n_bad, n_warn = self.count(BAD), self.count(WARN)
        if n_bad:
            return f"{n_bad} 项风险 · {n_warn} 项注意"
        if n_warn:
            return f"{n_warn} 项注意"
        return "未发现明显问题"

    def to_fix_text(self) -> str:
        """把所有「需要动手」的建议拼成一段可复制的文本。"""
        lines = []
        for c in sorted(self.checks, key=lambda c: LEVEL_ORDER.get(c.level, 0),
                        reverse=True):
            if c.level not in (BAD, WARN):
                continue
            lines.append(f"[{c.level_cn}] {c.title}")
            if c.detail:
                lines.append(f"  现状：{c.detail}")
            if c.advice:
                lines.append(f"  建议：{c.advice}")
            if c.fix:
                lines.append(f"  命令：{c.fix}")
            lines.append("")
        return "\n".join(lines).strip() or "没有需要处理的项目。"


# ------------------------------------------------------------------ 评分
def score_of(checks: list[Check]) -> int:
    s = 100 - sum(_PENALTY.get(c.level, 0) for c in checks)
    return max(0, min(100, s))


# ------------------------------------------------------------------ sshd
def parse_kv_conf(text: str) -> dict[str, str]:
    """解析 sshd_config 这类 `Key value` 配置。

    sshd 的规则是「**第一次出现生效**」，后面的同名项被忽略 ——
    所以这里不能简单覆盖，得保留第一个。
    """
    out: dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 1:
            continue
        key, val = parts[0].lower(), parts[1].strip()
        if key not in out:
            out[key] = val
    return out


def audit_sshd(conf: dict[str, str]) -> list[Check]:
    """SSH 是绝大多数 Linux 机器唯一对外的门，先看它。"""
    if not conf:
        return [Check("ssh", "SSH 配置", INFO,
                      "没有读到 sshd_config（可能没装 OpenSSH，或需要提权）",
                      "若这台机器确实跑 SSH，请用 root 再体检一次")]
    out: list[Check] = []

    root_login = (conf.get("permitrootlogin") or "prohibit-password").lower()
    if root_login in ("yes", "without-password", "prohibit-password", "forced-commands-only"):
        if root_login == "yes":
            out.append(Check(
                "ssh_root", "允许 root 直接登录", BAD,
                "PermitRootLogin yes", "改成 prohibit-password，改用普通账号 + sudo",
                "sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin prohibit-password/' "
                "/etc/ssh/sshd_config && sshd -t && systemctl reload sshd"))
        else:
            out.append(Check("ssh_root", "root 登录方式", OK,
                             f"PermitRootLogin {root_login}", "保持即可"))
    else:
        out.append(Check("ssh_root", "root 登录方式", OK,
                         f"PermitRootLogin {root_login}", "保持即可"))

    pw = (conf.get("passwordauthentication") or "yes").lower()
    if pw == "yes":
        out.append(Check(
            "ssh_pw", "允许口令登录", WARN,
            "PasswordAuthentication yes", "有公钥就关掉口令登录，能挡掉绝大多数爆破",
            "sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication no/' "
            "/etc/ssh/sshd_config && sshd -t && systemctl reload sshd"))
    else:
        out.append(Check("ssh_pw", "允许口令登录", OK,
                         f"PasswordAuthentication {pw}", "保持即可"))

    empty = (conf.get("permitemptypasswords") or "no").lower()
    if empty == "yes":
        out.append(Check("ssh_empty", "允许空口令", BAD,
                         "PermitEmptyPasswords yes",
                         "立刻关掉，空口令账号等于没有密码",
                         "sed -i 's/^#\\?PermitEmptyPasswords.*/PermitEmptyPasswords no/' "
                         "/etc/ssh/sshd_config && systemctl reload sshd"))

    port = (conf.get("port") or "22").split()[0]
    if port == "22":
        out.append(Check("ssh_port", "SSH 端口", INFO,
                         "22（默认端口）",
                         "改端口不算安全措施，但能少掉大量无脑扫描；真正有用的是禁口令 + 限制来源",
                         f"# 改端口还要同步防火墙与 SELinux：\n"
                         f"# semanage port -a -t ssh_port_t -p tcp <新端口>"))
    else:
        out.append(Check("ssh_port", "SSH 端口", OK, f"{port}（已改默认端口）"))

    x11 = (conf.get("x11forwarding") or "no").lower()
    if x11 == "yes":
        out.append(Check("ssh_x11", "X11 转发", INFO, "X11Forwarding yes",
                         "服务器上一般不需要，关掉能少一个转发面"))
    return out


# ------------------------------------------------------------------ 监听端口
_RISKY_PORTS = {
    21: ("FTP", "明文传输口令，尽量换成 SFTP"),
    23: ("Telnet", "明文协议，务必换 SSH"),
    111: ("rpcbind", "常被拿来做 NFS 放大反射"),
    445: ("SMB", "勒索软件最爱的入口，别对公网开"),
    1433: ("SQL Server", "数据库端口不该暴露在公网"),
    2375: ("Docker API（未加密）", "等于把 root 交给对方，必须关"),
    3306: ("MySQL", "只给内网或走 SSH 隧道"),
    3389: ("RDP", "爆破重点，至少加白名单"),
    5432: ("PostgreSQL", "只给内网"),
    5900: ("VNC", "多数无口令或弱口令"),
    6379: ("Redis", "未授权访问的重灾区"),
    9200: ("Elasticsearch", "未授权读写 + 删库勒索"),
    11211: ("Memcached", "UDP 放大反射"),
    27017: ("MongoDB", "未授权访问重灾区"),
}

_SS_RE = re.compile(
    r"^(?:LISTEN|UNCONN)\s+\d+\s+\d+\s+(\S+?):(\d+)\s+\S+\s*(.*)$")
_NETSTAT_RE = re.compile(
    r"^(TCP|UDP)\s+(\S+?):(\d+)\s+\S+\s+\S+.*$", re.I)


def parse_listeners(text: str) -> list[dict]:
    """解析 `ss -lntp` 或 `netstat -lntp` 的监听行。

    两家的列不一样，所以按行分别试；认不出来就跳过，不猜。
    """
    out: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith(("netid", "proto", "active")):
            continue
        m = _SS_RE.match(line)
        if m:
            addr, port, tail = m.group(1), int(m.group(2)), m.group(3)
            proc = ""
            pm = re.search(r'"([^"]+)"', tail)
            if pm:
                proc = pm.group(1)
            out.append({"proto": "tcp", "addr": addr, "port": port,
                        "proc": proc})
            continue
        m = _NETSTAT_RE.match(line)
        if m:
            out.append({"proto": m.group(1).lower(), "addr": m.group(2),
                        "port": int(m.group(3)), "proc": ""})
    return out


def _is_public(addr: str) -> bool:
    """非回环就算对外（含 0.0.0.0 / * / 具体网卡地址）。"""
    a = (addr or "").strip("[]")
    if a in ("", "127.0.0.1", "::1", "localhost"):
        return False
    if a.startswith("127.") or a.startswith("::ffff:127."):
        return False
    return True


def audit_exposure(listeners: list[dict]) -> list[Check]:
    """对外监听了什么 —— 这条比「装了什么杀毒」实在得多。"""
    out: list[Check] = []
    if not listeners:
        return [Check("listen", "对外监听端口", INFO,
                      "没有读到监听列表（ss/netstat 都没有输出）")]

    pub = [x for x in listeners if _is_public(x.get("addr", ""))]
    out.append(Check("listen_n", "监听端口数量", INFO if len(pub) < 40 else WARN,
                     f"共 {len(listeners)} 个监听，其中 {len(pub)} 个非本机回环"))
    for x in pub:
        if x.get("port") in _RISKY_PORTS:
            name, why = _RISKY_PORTS[x["port"]]
            out.append(Check(
                f"port_{x['port']}", f"{name} 对全网监听", WARN,
                f"{x.get('addr')}:{x['port']}"
                + (f"（{x['proc']}）" if x.get("proc") else ""),
                why,
                f"# 只允许内网访问（示例，按实际网段改）：\n"
                f"# iptables -A INPUT -p tcp --dport {x['port']} "
                f"-s 192.168.0.0/16 -j ACCEPT\n"
                f"# iptables -A INPUT -p tcp --dport {x['port']} -j DROP"))
    if not any(c.key.startswith("port_") for c in out):
        out.append(Check("port_risky", "高危端口暴露", OK,
                         "没有发现已知的高危端口对外监听"))
    return out


# ------------------------------------------------------------------ 账号
def parse_passwd(text: str) -> list[dict]:
    out = []
    for raw in (text or "").splitlines():
        parts = raw.strip().split(":")
        if len(parts) < 7:
            continue
        try:
            uid, gid = int(parts[2]), int(parts[3])
        except ValueError:
            continue
        out.append({"name": parts[0], "uid": uid, "gid": gid,
                    "home": parts[5], "shell": parts[6] or ""})
    return out


def parse_shadow_empty(text: str) -> list[str]:
    """shadow 里口令字段为空 = 无口令登录。"""
    out = []
    for raw in (text or "").splitlines():
        parts = raw.strip().split(":")
        if len(parts) >= 2 and parts[1] == "":
            out.append(parts[0])
    return out


def parse_group_members(text: str, groups=("sudo", "wheel", "admin", "docker")) -> dict:
    """从 /etc/group 里取出提权组与 docker 组成员。

    docker 组等价于 root（能挂宿主根目录），所以单独点出来。
    """
    want = {g.lower() for g in groups}
    out: dict[str, list[str]] = {}
    for raw in (text or "").splitlines():
        parts = raw.strip().split(":")
        if len(parts) < 4 or parts[0].lower() not in want:
            continue
        members = [m for m in parts[3].split(",") if m]
        if members:
            out[parts[0].lower()] = members
    return out


def audit_accounts(passwd: list[dict], shadow: str = "",
                   groups: dict | None = None, shadow_readable: bool = True) -> list[Check]:
    out: list[Check] = []
    if not passwd:
        return [Check("acct", "账号", INFO, "没有读到 /etc/passwd")]

    zero = [p["name"] for p in passwd if p["uid"] == 0]
    extra = [n for n in zero if n != "root"]
    if extra:
        out.append(Check("acct_uid0", "存在第二个 UID 0 账号", BAD,
                         "、".join(extra),
                         "UID 0 就是 root。不是你自己建的就立刻锁掉或删掉",
                         f"usermod -L {extra[0]}   # 先锁定，确认无影响再 userdel"))
    else:
        out.append(Check("acct_uid0", "root 唯一", OK, "只有 root 的 UID 是 0"))

    login = [p for p in passwd if p["uid"] >= 1000
             and not p["shell"].endswith(("nologin", "false"))]
    out.append(Check("acct_login", "可登录普通账号", INFO if len(login) < 8 else WARN,
                     f"{len(login)} 个："
                     + ("、".join(p["name"] for p in login[:8]) or "无")))

    if not shadow_readable or not shadow:
        out.append(Check("acct_empty", "空口令账号", INFO,
                         "读不到 /etc/shadow，无法判断（用 root 再体检一次）"))
    else:
        empties = parse_shadow_empty(shadow)
        if empties:
            out.append(Check("acct_empty", "存在空口令账号", BAD,
                             "、".join(empties),
                             "空口令等于没有密码，立刻设置口令或锁定",
                             f"passwd -l {empties[0]}"))
        else:
            out.append(Check("acct_empty", "空口令账号", OK, "没有空口令账号"))

    groups = groups or {}
    sudo = groups.get("sudo") or groups.get("wheel") or []
    if sudo:
        out.append(Check("acct_sudo", "可提权账号", INFO if len(sudo) < 5 else WARN,
                         f"{len(sudo)} 个：{'、'.join(sudo[:8])}",
                         "提权账号越少越好，用完就撤"))
    docker = groups.get("docker") or []
    if docker:
        out.append(Check("acct_docker", "docker 组成员", WARN,
                         "、".join(docker[:8]),
                         "docker 组等价于 root（能挂宿主根目录），别随便加人"))
    return out


# ------------------------------------------------------------------ 失败登录
_FAIL_RE = re.compile(r"Failed password for (?:invalid user )?(\S+) from (\S+)")
_FAIL_CNT_RE = re.compile(r"^\s*(\d+)\s*$")


def parse_auth_fail(text: str) -> dict:
    """返回 {"count": n, "ips": [(ip, 次数)], "users": [...]}。"""
    body = (text or "").strip()
    if not body:
        return {"count": 0, "ips": [], "users": []}
    # 只有一条纯数字：这是 `grep -c` 的结果
    m = _FAIL_CNT_RE.match(body.splitlines()[0])
    if m and len(body.splitlines()) == 1:
        return {"count": int(m.group(1)), "ips": [], "users": []}
    ips: dict[str, int] = {}
    users: dict[str, int] = {}
    for line in body.splitlines():
        mm = _FAIL_RE.search(line)
        if not mm:
            continue
        users[mm.group(1)] = users.get(mm.group(1), 0) + 1
        ips[mm.group(2)] = ips.get(mm.group(2), 0) + 1
    total = sum(ips.values())
    return {"count": total,
            "ips": sorted(ips.items(), key=lambda kv: -kv[1])[:5],
            "users": sorted(users.items(), key=lambda kv: -kv[1])[:5]}


def audit_failed(st: dict) -> list[Check]:
    n = int(st.get("count") or 0)
    if n >= 100:
        level, advice = BAD, "短期内大量失败登录，基本是在被爆破。建议禁口令登录 + 限制来源 IP"
    elif n >= 20:
        level, advice = WARN, "有一定量的失败登录，留意是不是有人在试口令"
    else:
        level, advice = OK, "失败登录次数正常"
    ips = st.get("ips") or []
    detail = f"近期 {n} 次失败登录"
    if ips:
        detail += "；来源 " + "、".join(f"{ip}×{c}" for ip, c in ips[:3])
    fix = ""
    if ips and n >= 20:
        fix = (f"# 临时封掉主要来源（示例）：\n"
               f"# iptables -I INPUT -s {ips[0][0]} -j DROP")
    return [Check("auth_fail", "SSH 失败登录", level, detail, advice, fix)]


# ------------------------------------------------------------------ 防火墙
def parse_firewall_status(text: str) -> dict:
    """把 ufw / firewalld / iptables / nft 的输出归一成一个状态。"""
    body = (text or "")
    low = body.lower()
    st = {"ufw": "", "firewalld": "", "iptables": 0, "nft": 0, "raw": body}
    m = re.search(r"Status:\s*(\w+)", body)
    if m:
        st["ufw"] = m.group(1).lower()
    if "running" in low and "not running" not in low:
        st["firewalld"] = "running"
    elif "not running" in low:
        st["firewalld"] = "stopped"
    nums = [int(x) for x in re.findall(r"^\s*(\d+)\s*$", body, re.M)]
    # 约定顺序：iptables 行数在前，nft 行数在后
    if len(nums) >= 1:
        st["iptables"] = nums[0]
    if len(nums) >= 2:
        st["nft"] = nums[1]
    return st


def audit_firewall(st: dict) -> list[Check]:
    # iptables -S 即使一条规则都没有也会打印 3 行默认策略，所以门槛是 >3；
    # nft 空规则集输出 0 行，任何输出都算有配置。
    active = (st.get("ufw") == "active" or st.get("firewalld") == "running"
              or st.get("iptables", 0) > 3 or st.get("nft", 0) > 0)
    if active:
        bits = []
        if st.get("ufw") == "active":
            bits.append("ufw active")
        if st.get("firewalld") == "running":
            bits.append("firewalld running")
        if st.get("iptables", 0) > 3:
            bits.append(f"iptables {st['iptables']} 行")
        if st.get("nft", 0) > 0:
            bits.append(f"nft {st['nft']} 行")
        return [Check("fw_on", "主机防火墙", OK, "；".join(bits) or "已启用",
                      "规则细节可到「安全中心 → 防火墙」里看")]
    return [Check("fw_on", "主机防火墙", WARN,
                  "没有检测到生效的规则（ufw/firewalld/iptables/nft 都是空的）",
                  "至少把不必要的端口挡掉；规则可在本工具「防火墙」页直接加",
                  "ufw default deny incoming && ufw allow 22/tcp && ufw enable")]


# ------------------------------------------------------------------ 强制访问控制
def parse_mac(text: str) -> dict:
    body = (text or "").strip()
    low = body.lower()
    out = {"selinux": "", "apparmor": False, "raw": body}
    for v in ("enforcing", "permissive", "disabled"):
        if v in low:
            out["selinux"] = v
            break
    if "apparmor-enabled" in low or "apparmor module is loaded" in low:
        out["apparmor"] = True
    return out


def audit_mac(st: dict) -> list[Check]:
    sel = st.get("selinux") or ""
    if sel == "enforcing":
        return [Check("mac", "强制访问控制", OK, "SELinux Enforcing")]
    if sel == "permissive":
        return [Check("mac", "强制访问控制", INFO, "SELinux Permissive（只记录不拦截）",
                      "确认没有拒绝日志后可以切 enforcing",
                      "setenforce 1")]
    if sel == "disabled":
        return [Check("mac", "强制访问控制", INFO, "SELinux Disabled",
                      "不是必须开，但开了能多一层兜底")]
    if st.get("apparmor"):
        return [Check("mac", "强制访问控制", OK, "AppArmor 已启用")]
    return [Check("mac", "强制访问控制", INFO, "未检测到 SELinux / AppArmor",
                  "多数桌面/容器环境本来就没有，按需处理")]


# ------------------------------------------------------------------ 计划任务里的可疑下载
_SUSP_RE = re.compile(r"(curl|wget)[^|;]*\|\s*(ba|z|da)?sh", re.I)


def audit_suspicious_cron(files: list[str]) -> list[Check]:
    files = [f for f in (files or []) if f.strip()]
    if not files:
        return [Check("cron_susp", "计划任务里的可疑下载", OK,
                      "没有发现 curl|bash 这类写法")]
    return [Check("cron_susp", "计划任务里有可疑下载执行", BAD,
                  "、".join(files[:5]),
                  "curl … | bash 常见于挖矿与后门。确认不是你自己写的就删掉",
                  "# 检查这些文件：\n" + "\n".join(f"# cat {f}" for f in files[:5]))]


# ------------------------------------------------------------------ Windows
_WIN_PROFILE = (("domain", "domain", "域"), ("private", "private", "专用"),
                ("public", "public", "公用"))


def parse_win_firewall(text: str) -> dict:
    """`netsh advfirewall show allprofiles state` → {profile: on/off}。

    中文系统的输出是「域配置文件设置: / 状态 启用」，英文是
    「Domain Profile Settings: / State ON」—— 所以先认段落标题，再认状态行，
    别指望整行正则一次匹配（本地化名字不一样）。
    """
    out: dict[str, str] = {}
    cur = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or set(line) <= {"-"}:
            continue
        low = line.lower()
        for key, en, cn in _WIN_PROFILE:
            if (en in low and "profile" in low) or (cn in line and "配置" in line):
                cur = key
                break
        if not cur:
            continue
        if re.search(r"^\s*(state|状态)\b", low) or low.startswith("状态"):
            val = low.replace("状态", "").replace("state", "").strip(" :\t")
            if val in ("on", "启用", "打开"):
                out[cur] = "on"
            elif val in ("off", "关闭", "禁用"):
                out[cur] = "off"
    return out


def parse_win_rdp(text: str) -> bool | None:
    """fDenyTSConnections = 0 表示允许远程桌面。"""
    m = re.search(r"fDenyTSConnections\s+REG_DWORD\s+0x([0-9a-f]+)", text or "",
                  re.I)
    if not m:
        return None
    return int(m.group(1), 16) == 0


def augment_win_checks(fw_state: dict, rdp_open: bool | None,
                       admins: list[str]) -> list[Check]:
    out: list[Check] = []
    if fw_state:
        off = [k for k, v in fw_state.items() if v == "off"]
        if off:
            out.append(Check("win_fw", "Windows 防火墙", BAD,
                             "已关闭的配置文件：" + "、".join(off),
                             "三个配置文件（域/专用/公用）都该开启",
                             "netsh advfirewall set allprofiles state on"))
        else:
            out.append(Check("win_fw", "Windows 防火墙", OK,
                             "域 / 专用 / 公用 均已开启"))
    else:
        out.append(Check("win_fw", "Windows 防火墙", INFO, "没有读到防火墙状态"))

    if rdp_open is True:
        out.append(Check("win_rdp", "远程桌面已开启", WARN,
                         "fDenyTSConnections = 0",
                         "如果不用远程桌面就关掉；要用就限制来源并开网络级认证",
                         "reg add \"HKLM\\SYSTEM\\CurrentControlSet\\Control\\"
                         "Terminal Server\" /v fDenyTSConnections /t REG_DWORD "
                         "/d 1 /f"))
    elif rdp_open is False:
        out.append(Check("win_rdp", "远程桌面", OK, "已关闭"))

    extra = [a for a in admins if a.lower() not in ("administrator", "admin",
                                                    "administrators")]
    if admins:
        out.append(Check("win_admin", "管理员组成员", INFO if len(extra) < 4 else WARN,
                         f"{len(admins)} 个" + (f"：{'、'.join(admins[:6])}" if admins else ""),
                         "管理员越少越好"))
    return out


# ------------------------------------------------------------------ 组装
def assemble_posix(texts: dict[str, str]) -> Report:
    """把各条命令的文本拼成一份报告。纯函数，可离线测。"""
    checks: list[Check] = []
    checks += audit_sshd(parse_kv_conf(texts.get("sshd", "")))
    checks += audit_exposure(parse_listeners(texts.get("listen", "")))
    shadow = texts.get("shadow", "")
    checks += audit_accounts(
        parse_passwd(texts.get("passwd", "")),
        shadow=shadow,
        groups=parse_group_members(texts.get("group", "")),
        shadow_readable=bool(shadow.strip()))
    checks += audit_failed(parse_auth_fail(texts.get("auth", "")))
    checks += audit_firewall(parse_firewall_status(texts.get("fw", "")))
    checks += audit_mac(parse_mac(texts.get("mac", "")))
    checks += audit_suspicious_cron(
        (texts.get("cron_susp", "") or "").split())
    rep = Report(checks=checks, score=score_of(checks))
    rep.detail = _detail_line(rep)
    return rep


def assemble_windows(texts: dict[str, str]) -> Report:
    fw_state = parse_win_firewall(texts.get("fw", ""))
    rdp = parse_win_rdp(texts.get("rdp", ""))
    admins = parse_win_lines(texts.get("admins", ""))
    listeners = parse_listeners(texts.get("listen", ""))
    checks: list[Check] = []
    checks += audit_exposure(listeners)
    checks += augment_win_checks(fw_state, rdp, admins)
    rep = Report(checks=checks, score=score_of(checks))
    rep.detail = _detail_line(rep)
    return rep


def parse_win_lines(text: str) -> list[str]:
    """`net localgroup administrators` 的成员列表：跳过表头与结尾提示。"""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("---") or line.startswith("===") or line.startswith("命令成功"):
            continue
        if "Alias name" in line or "别名" in line or "comment" in line.lower():
            continue
        out.append(line)
    return out


def _detail_line(rep: Report) -> str:
    bad, warn = rep.count(BAD), rep.count(WARN)
    if not rep.checks:
        return "没有采到任何检查项"
    return f"{len(rep.checks)} 项检查 · 风险 {bad} · 注意 {warn}"


# ------------------------------------------------------------------ 执行
POSIX_CMDS = [
    ("sshd", "cat /etc/ssh/sshd_config 2>/dev/null"),
    ("listen", "ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null"),
    ("passwd", "cat /etc/passwd 2>/dev/null"),
    ("shadow", "cat /etc/shadow 2>/dev/null"),
    ("group", "cat /etc/group 2>/dev/null"),
    ("auth", "grep -c 'Failed password' /var/log/auth.log 2>/dev/null || "
             "journalctl -u ssh -u sshd --since '-24h' --no-pager 2>/dev/null | "
             "grep -c 'Failed password'"),
    ("fw", "ufw status 2>/dev/null; firewall-cmd --state 2>/dev/null; "
           "iptables -S 2>/dev/null | wc -l; nft list ruleset 2>/dev/null | wc -l"),
    ("mac", "getenforce 2>/dev/null; aa-status --enabled 2>/dev/null && "
            "echo apparmor-enabled"),
    ("cron_susp", "grep -rEl '(curl|wget)[^|;]*\\|[[:space:]]*(ba|z|da)?sh' "
                  "/etc/crontab /etc/cron.d /var/spool/cron 2>/dev/null"),
]

WIN_CMDS = [
    ("fw", "netsh advfirewall show allprofiles state"),
    ("listen", "netstat -ano"),
    ("rdp", "reg query \"HKLM\\SYSTEM\\CurrentControlSet\\Control\\Terminal Server\" "
            "/v fDenyTSConnections"),
    ("admins", "net localgroup administrators"),
]


class SecurityScanner:
    """按平台跑几条只读命令，交给解析层出报告。"""

    def __init__(self, session, device=None):
        self.session = session
        self.device = device
        self.commands: list[str] = []

    def _family(self) -> str:
        fam = (getattr(self.device, "os_family", "") or "").lower()
        if fam:
            return fam
        try:
            import sys
            return {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
        except Exception:  # noqa: BLE001
            return "linux"

    def _run(self, cmd: str, timeout: int = 25) -> str:
        self.commands.append(cmd)
        s = self.session
        if s is None:
            return ""
        runner = getattr(s, "run_traced", None) or s.run
        try:
            _rc, out, _err = runner(cmd, timeout=timeout)
        except Exception:  # noqa: BLE001
            return ""
        # 有些检查要 root，读不到就是空 —— 解析层会把它标成「无法判断」，
        # 而不是伪造一个「良好」。
        return out or ""

    def scan(self) -> Report:
        fam = self._family()
        specs = WIN_CMDS if fam == "windows" else POSIX_CMDS
        texts: dict[str, str] = {}
        for key, cmd in specs:
            texts[key] = self._run(cmd)
        rep = (assemble_windows(texts) if fam == "windows"
               else assemble_posix(texts))
        rep.commands = list(self.commands)
        return rep
