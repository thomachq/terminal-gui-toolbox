# -*- coding: utf-8 -*-
"""安全体检的离线自测：只喂文本，不连设备、不执行命令。

覆盖的是「解析 + 判定」这一层——真正容易写错的地方：
sshd 的「第一次出现生效」、iptables 默认策略行不能算有规则、
中文 Windows 的 netsh 输出、空口令账号、UID 0 后门账号。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import secscan as S   # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def chk(ok, name: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, str(detail)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  —— {detail}" if detail else ""))
    return bool(ok)


def lv(checks, key) -> str:
    for c in checks:
        if c.key == key:
            return c.level
    return ""


SSHD = """#	$OpenBSD: sshd_config,v 1.103 $
Port 22
#PermitRootLogin prohibit-password
PermitRootLogin yes
PermitRootLogin no
PasswordAuthentication yes
PermitEmptyPasswords no
X11Forwarding yes
"""

LISTEN = """State  Recv-Q Send-Q   Local Address:Port    Peer Address:Port Process
LISTEN 0      128            0.0.0.0:22           0.0.0.0:*     users:(("sshd",pid=901,fd=3))
LISTEN 0      128            0.0.0.0:6379         0.0.0.0:*     users:(("redis-server",pid=902,fd=5))
LISTEN 0      128          127.0.0.1:3306         0.0.0.0:*     users:(("mysqld",pid=903,fd=6))
LISTEN 0      128               [::]:443             [::]:*     users:(("nginx",pid=904,fd=7))
"""

PASSWD = """root:x:0:0:root:/root:/bin/bash
toor:x:0:0::/root:/bin/sh
daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
bob:x:1000:1000:Bob:/home/bob:/bin/bash
backup:x:34:34:backup:/var/backups:/usr/sbin/nologin
"""

GROUP = """root:x:0:
sudo:x:27:bob
docker:x:999:bob,alice
wheel:x:10:
"""

SHADOW = """root:$6$abc:19000:0:99999:7:::
bob::19000:0:99999:7:::
"""


def test_sshd() -> None:
    print("== 1. sshd 配置 ==")
    conf = S.parse_kv_conf(SSHD)
    chk(conf.get("permitrootlogin") == "yes", "同名项取第一次出现的值",
        conf.get("permitrootlogin"))
    chk(conf.get("port") == "22", "注释行不算", conf.get("port"))
    checks = S.audit_sshd(conf)
    chk(lv(checks, "ssh_root") == S.BAD, "允许 root 登录判风险")
    chk(lv(checks, "ssh_pw") == S.WARN, "允许口令登录判注意")
    chk(lv(checks, "ssh_port") == S.INFO, "默认端口只给提示")
    empty = S.audit_sshd({"permitemptypasswords": "yes"})
    chk(any(c.key == "ssh_empty" and c.level == S.BAD for c in empty),
        "空口令判风险")
    chk(S.audit_sshd({})[0].level == S.INFO, "读不到配置时不当成良好")


def test_listen() -> None:
    print("== 2. 监听端口 ==")
    lst = S.parse_listeners(LISTEN)
    chk(len(lst) == 4, "四行监听都解析出来", str(len(lst)))
    ports = {x["port"]: x for x in lst}
    chk(ports[22]["addr"] == "0.0.0.0", "IPv4 地址正确", ports[22]["addr"])
    chk(ports[443]["addr"] == "[::]", "IPv6 方括号地址保留", ports[443]["addr"])
    chk(ports[22]["proc"] == "sshd", "进程名从 users:(()) 里取到", ports[22]["proc"])

    checks = S.audit_exposure(lst)
    keys = {c.key for c in checks}
    chk("port_6379" in keys, "Redis 对全网监听被点出来")
    chk("port_3306" not in keys, "只听 127.0.0.1 的 MySQL 不算暴露")
    chk("port_22" not in keys, "SSH 22 不在高危端口表里（它是必需品）")

    netstat = ("TCP    0.0.0.0:3389    0.0.0.0:0    LISTENING    1234\n"
               "TCP    127.0.0.1:1433   0.0.0.0:0    LISTENING    5678\n")
    rows = S.parse_listeners(netstat)
    chk(len(rows) == 2 and rows[0]["port"] == 3389, "netstat 格式也能解析",
        str([r["port"] for r in rows]))


def test_accounts() -> None:
    print("== 3. 账号 ==")
    passwd = S.parse_passwd(PASSWD)
    chk(len(passwd) == 5, "passwd 五行", str(len(passwd)))
    groups = S.parse_group_members(GROUP)
    checks = S.audit_accounts(passwd, SHADOW, groups, shadow_readable=True)
    chk(lv(checks, "acct_uid0") == S.BAD, "第二个 UID 0 账号判风险")
    chk(lv(checks, "acct_empty") == S.BAD, "空口令账号判风险")
    chk(lv(checks, "acct_docker") == S.WARN, "docker 组成员单独提示")
    none = S.audit_accounts(passwd, "", groups, shadow_readable=False)
    chk(lv(none, "acct_empty") == S.INFO,
        "读不到 shadow 时说「无法判断」而不是「良好」")
    chk(S.parse_shadow_empty(SHADOW) == ["bob"], "空口令识别准确",
        str(S.parse_shadow_empty(SHADOW)))


def test_auth() -> None:
    print("== 4. 失败登录 ==")
    chk(S.parse_auth_fail("137")["count"] == 137, "grep -c 的纯数字结果")
    lines = ("Failed password for invalid user admin from 10.0.0.9 port 22 ssh2\n"
             "Failed password for root from 10.0.0.9 port 22 ssh2\n"
             "Failed password for root from 1.2.3.4 port 22 ssh2\n")
    st = S.parse_auth_fail(lines)
    chk(st["count"] == 3, "逐行统计", str(st["count"]))
    chk(st["ips"][0] == ("10.0.0.9", 2), "来源 IP 按次数排序", str(st["ips"]))
    chk(S.audit_failed({"count": 0})[0].level == S.OK, "0 次判良好")
    chk(S.audit_failed({"count": 30})[0].level == S.WARN, "30 次判注意")
    bad = S.audit_failed({"count": 500, "ips": [("9.9.9.9", 300)], "users": []})
    chk(bad[0].level == S.BAD and "iptables" in bad[0].fix,
        "500 次判风险并给封禁示例")


def test_firewall_and_mac() -> None:
    print("== 5. 防火墙 / 强制访问控制 ==")
    empty = S.parse_firewall_status("Status: inactive\n3\n0\n")
    chk(S.audit_firewall(empty)[0].level == S.WARN,
        "iptables 只有 3 行默认策略 = 没规则")
    on = S.parse_firewall_status("Status: active\n12\n0\n")
    chk(S.audit_firewall(on)[0].level == S.OK, "ufw active 判良好")
    nft = S.parse_firewall_status("running\n0\n40\n")
    chk(S.audit_firewall(nft)[0].level == S.OK, "firewalld running 判良好")
    chk(S.audit_mac({"selinux": "enforcing"})[0].level == S.OK, "SELinux enforcing")
    chk(S.audit_mac({"selinux": "permissive"})[0].level == S.INFO,
        "permissive 只给提示")
    chk(S.audit_mac({"apparmor": True})[0].level == S.OK, "AppArmor 也算")


def test_windows() -> None:
    print("== 6. Windows ==")
    cn = """域配置文件 设置:
----------------------------------------------------------------------
状态                                 启用

专用配置文件 设置:
----------------------------------------------------------------------
状态                                 关闭
"""
    st = S.parse_win_firewall(cn)
    chk(st == {"domain": "on", "private": "off"}, "中文 netsh 输出", str(st))
    en = """Domain Profile Settings:
State                                 ON
Private Profile Settings:
State                                 OFF
Public Profile Settings:
State                                 ON
"""
    chk(S.parse_win_firewall(en) == {"domain": "on", "private": "off",
                                     "public": "on"}, "英文 netsh 输出")
    checks = S.augment_win_checks({"domain": "on", "private": "off"},
                                  rdp_open=True,
                                  admins=["Administrator", "Thomas"])
    chk(lv(checks, "win_fw") == S.BAD, "有 profile 关闭判风险")
    chk(lv(checks, "win_rdp") == S.WARN, "RDP 开启判注意")
    chk(lv(checks, "win_admin") == S.INFO, "管理员组只给提示")
    chk(S.parse_win_rdp("fDenyTSConnections    REG_DWORD    0x1") is False,
        "0x1 = 远程桌面关闭")
    chk(S.parse_win_rdp("fDenyTSConnections    REG_DWORD    0x0") is True,
        "0x0 = 远程桌面开启")
    chk(S.parse_win_rdp("没有这个键") is None, "读不到就返回 None")


def test_report() -> None:
    print("== 7. 报告与评分 ==")
    texts = {"sshd": SSHD, "listen": LISTEN, "passwd": PASSWD, "shadow": SHADOW,
             "group": GROUP, "auth": "137\n", "fw": "Status: inactive\n3\n0\n",
             "mac": "Enforcing", "cron_susp": "/etc/cron.d/miner\n"}
    rep = S.assemble_posix(texts)
    chk(rep.count(S.BAD) >= 4, "风险项数量合理", f"{rep.count(S.BAD)} 项")
    chk(rep.score < 60, "问题多时评分明显下降", str(rep.score))
    chk(rep.worst == S.BAD, "最差等级是风险")
    chk("风险" in rep.headline(), "结论文案带风险数", rep.headline())
    chk(len(rep.rows()) == len(rep.checks), "行数与检查项一致")
    tones = {r["_tone"] for r in rep.rows()}
    chk(tones <= {"ok", "warn", "err", "dim"}, "色调都在表格支持范围内",
        str(tones))
    fix = rep.to_fix_text()
    chk("PermitRootLogin" in fix and "iptables" in fix,
        "加固建议里既有 ssh 也有防火墙")
    chk("[风险]" in fix, "建议按等级标注")

    clean = S.assemble_posix({
        "sshd": "PermitRootLogin prohibit-password\nPasswordAuthentication no\n",
        "listen": "LISTEN 0 128 127.0.0.1:1 0.0.0.0:* users:((\"x\",pid=1,fd=1))\n",
        "passwd": "root:x:0:0::/root:/bin/bash\n",
        "shadow": "root:$6$a:1::::::\n", "group": "",
        "auth": "0\n", "fw": "Status: active\n30\n0\n",
        "mac": "Enforcing", "cron_susp": ""})
    chk(clean.score >= 90, "干净的机器评分高", str(clean.score))
    chk(clean.to_fix_text() == "没有需要处理的项目。", "干净机器没有待办")

    win = S.assemble_windows({
        "fw": "Domain Profile Settings:\nState ON\nPublic Profile Settings:\nState OFF\n",
        "listen": "TCP    0.0.0.0:445    0.0.0.0:0    LISTENING    4\n",
        "rdp": "fDenyTSConnections    REG_DWORD    0x0",
        "admins": "Administrator\nThomas\n",
    })
    chk(any(c.key == "port_445" for c in win.checks), "Windows 也查高危端口")
    chk(win.count(S.BAD) >= 1, "Windows 报告也有风险项", f"{win.count(S.BAD)}")


def main() -> int:
    test_sshd()
    test_listen()
    test_accounts()
    test_auth()
    test_firewall_and_mac()
    test_windows()
    test_report()
    npass = sum(1 for ok, _, _ in RESULTS if ok)
    print(f"\n合计 {len(RESULTS)} 项：通过 {npass}，失败 {len(RESULTS) - npass}")
    for ok, name, detail in RESULTS:
        if not ok:
            print(f"  FAIL · {name}  {detail}")
    return 0 if npass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
