# -*- coding: utf-8 -*-
"""隧道服务：探测 / 解析 / 启停 / 客户端 / 证书认证模式的测试。

全部纯函数（喂假回显），另外真跑一次本机 Windows 的探测命令确认不报错。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import tunnels as T  # noqa: E402

_p = _f = _s = 0


def chk(ok, label, extra=""):
    global _p, _f
    if ok:
        _p += 1
        print(f"  ok   {label}")
    else:
        _f += 1
        print(f"  FAIL {label}  [{extra}]")


def sec(t):
    print(f"\n-- {t}")


# ==================================================== 1. 探测命令本身
sec("1. 探测命令")
cmd = T.probe_cmd()
chk("alive()" in cmd, "有读 /proc comm 的存活判断")
chk("grep" not in cmd.split("@svc")[0], "不靠 ps|grep 判存活（会自匹配）")
chk("/proc/[0-9]*/comm" in cmd, "用 comm 而不是命令行")
chk(cmd.count("@svc ") == len(T.SERVICES), "每个服务一条 @svc 输出",
    str(cmd.count("@svc ")))
chk("@wg " in cmd, "WireGuard 单独判 /sys/class/net")
chk("/var/packages/VPNCenter/target/bin/openvpn" in cmd,
    "群晖的绝对路径在候选里（PATH 里没有）")
for bad in ("&& echo syno; } ||", "bash "):
    chk(bad not in cmd, f"命令里没有 {bad!r} 这种坑")

# ==================================================== 2. 解析探测结果
sec("2. 解析")
fake = """@os Linux
@init syno
@synopkg 'VPNCenter' is started
@svc openvpn 1 1
@svc strongswan 1 0
@svc xl2tpd 1 1
@svc pptpd 0 0
@svc ocserv 0 0
@svc softether 0 0
@wg wg0
"""
r = T.parse_probe(fake)
chk(r["os"] == "Linux", "读到系统", r["os"])
chk(r["init"] == "syno", "读到 init 系统（群晖）", r["init"])
by = {s["key"]: s for s in r["services"]}
chk(by["openvpn"]["active"] and by["openvpn"]["installed"], "openvpn 在跑")
chk(by["strongswan"]["installed"] and not by["strongswan"]["active"],
    "strongswan 装了没跑")
chk(not by["pptpd"]["installed"], "pptpd 没装（页面不该显示）")
chk("wireguard" in by and by["wireguard"]["active"],
    "WireGuard 靠 /sys 判出来（没有进程）")
chk(by["wireguard"]["ifaces"] == ["wg0"], "WireGuard 接口名",
    str(by["wireguard"]["ifaces"]))
chk(len(r["services"]) == 7, "6 个 + WireGuard", str(len(r["services"])))

# 一个都没装
r0 = T.parse_probe("@os Linux\n@init systemd\n@svc openvpn 0 0")
chk(len(r0["services"]) == 1 and not r0["services"][0]["installed"],
    "什么都没装时列表为空壳（页面显示空态）")

# ==================================================== 3. 启停命令
sec("3. 启停命令")
ovo = by["openvpn"]
c = T.control_cmds(ovo, "restart", init="syno")
chk(c == ["/usr/syno/bin/synopkg restart VPNCenter"],
    "群晖走 synopkg（不是 systemctl）", str(c))
c = T.control_cmds(ovo, "start", init="systemd")
chk(c == ["systemctl start openvpn@server"], "systemd 走 systemctl", str(c))
c = T.control_cmds(by["strongswan"], "stop", init="systemd")
chk(c == ["systemctl stop strongswan-starter"], "strongSwan 的 unit 名对",
    str(c))
c = T.control_cmds(ovo, "restart", init="openwrt")
chk(c == ["/etc/init.d/openvpn restart"], "OpenWrt 走 init.d", str(c))
c = T.control_cmds(ovo, "stop", init="sysv")
chk(c == ["service openvpn@server stop"], "sysv 走 service", str(c))
xl2 = by["xl2tpd"]
for a in ("start", "stop", "restart"):
    got = T.control_cmds(xl2, a, "systemd")
    chk(len(got) == 1 and got[0].startswith(f"systemctl {a} "),
        f"xl2tpd {a} 命令成形", str(got))

# ==================================================== 4. 在线客户端
sec("4. 在线客户端")
ovpn_status = """TITLE,OpenVPN 2.4.7
TIME,Mon Oct 26 10:00:00 2026
HEADER,CLIENT_LIST,Common Name,Real Address,Virtual Address,Bytes Received,Bytes Sent,Connected Since,Connected Since (time_t),Username,Client ID,Peer ID
CLIENT_LIST,alice,203.0.113.5:51234,10.8.0.2,1024,2048,Mon Oct 26 09:10:00 2026,1750000000,alice,1,0
CLIENT_LIST,bob,198.51.100.9:41000,10.8.0.3,300,600,Mon Oct 26 09:30:00 2026,1750000000,bob,2,0
HEADER,ROUTING_TABLE,Virtual Address,Common Name,Real Address,Last Ref
ROUTING_TABLE,10.8.0.2,alice,203.0.113.5:51234,Mon Oct 26 10:00:00 2026
END
"""
rows = T.parse_clients("openvpn", ovpn_status)
chk(len(rows) == 2, "OpenVPN 逗号格式认出 2 个人", str(len(rows)))
chk(rows[0]["name"] == "alice" and rows[0]["virtual"] == "10.8.0.2",
    "名字 / 虚拟 IP 对", str(rows[0]))
tabbed = ovpn_status.replace(",", "\t")
chk(len(T.parse_clients("openvpn", tabbed)) == 2, "tab 分隔也认（2.4 默认）")

wg_dump = """wg0\tpeerkey1==\t(none)\t203.0.113.7:51820\t10.9.0.2/32\t1234\t5678\t42
wg0\tpeerkey2==\t(none)\t(none)\t10.9.0.3/32\t0\t0\t0
"""
wrows = T.parse_clients("wireguard", wg_dump)
chk(len(wrows) == 2, "WireGuard 两个 peer", str(len(wrows)))
chk(wrows[0]["state"] == "在线" and wrows[1]["state"] == "无端点",
    "有端点=在线、没端点单独标", str([x["state"] for x in wrows]))

chk(T.parse_clients("l2tp", "") == [], "没数据返回空列表，不炸")

# ==================================================== 5. 客户端列表命令
sec("5. 客户端列表命令")
c = T.clients_cmd(ovo)
chk("/etc/openvpn/openvpn-status.log" in c, "OpenVPN 先找常见 status 文件")
chk("@status none" in c and "1194" in c, "没有 status 文件时退回看端口")
c = T.clients_cmd(by["wireguard"])
chk("wg show wg0 dump" in c, "WireGuard 指定接口", c)
c = T.clients_cmd({"kind": "l2tp"})
chk("xl2tpd" in c or "1701" in c, "L2TP 看 1701")

# ==================================================== 6. 证书认证模式
sec("6. OpenVPN 客户端证书认证")
chk(T.cert_mode("port 1194\nproto udp\n") == "require",
    "没写这一行 = require（2.4 默认）")
chk(T.cert_mode("client-cert-not-required\nusername-as-common-name\n") == "none",
    "老语法 client-cert-not-required → none")
chk(T.cert_mode("verify-client-cert optional\n") == "optional", "optional 认得")
chk(T.cert_mode("# verify-client-cert none\nverify-client-cert require\n")
    == "require", "注释掉的那行不算")
cmds = T.cert_mode_cmds("/etc/openvpn/server.conf", "require", init="systemd")
chk(cmds[0].startswith("cp -p '/etc/openvpn/server.conf'"),
    "先备份原配置", cmds[0])
chk("sed -i" in cmds[1] and "verify-client-cert" in cmds[1]
    and "client-cert-not-required" in cmds[1]
    and "username-as-common-name" in cmds[1],
    "三条互斥指令一起删干净", cmds[1])
chk("verify-client-cert require" in cmds[2], "再追加要求证书", cmds[2])
chk("username-as-common-name" not in cmds[2],
    "require 模式不写 username-as-common-name（那会认错人）")
chk(cmds[-2] == "systemctl restart openvpn@server", "重启生效", cmds[-2])
cmds_none = T.cert_mode_cmds("/etc/openvpn/server.conf", "none", init="syno")
chk("username-as-common-name" in cmds_none[2],
    "none 模式必须带 username-as-common-name（要不服务端拿不到用户名）",
    cmds_none[2])
chk("synopkg restart VPNCenter" in " ".join(cmds_none), "群晖用 synopkg 重启")
try:
    T.cert_mode_cmds("/x", "whatever")
    chk(False, "非法模式要抛错")
except ValueError:
    chk(True, "非法模式抛错")

# 状态文件路径从配置里读
cf = "port 1194\nstatus /var/log/openvpn-status.log 5\n"
chk(T.status_files_from_conf(cf) == ["/var/log/openvpn-status.log"],
    "从配置里读出 status 文件路径", str(T.status_files_from_conf(cf)))
chk(T.status_files_from_conf("status 5\n") == [],
    "`status 5`（只给间隔）不会被当路径")

# ==================================================== 7. 本机 Windows 探测
sec("7. 本机 Windows 真跑一次")
try:
    p = subprocess.run(T.windows_probe_cmd(), shell=True, capture_output=True,
                       text=True, timeout=60,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    chk("@os|" in out, "Windows 探测有系统行", out[:120])
    wr = T.parse_windows_probe(out)
    chk(bool(wr["os"]), "读到 Windows 版本", wr["os"])
    chk(isinstance(wr["services"], list), "服务解析不炸")
    print("    本机隧道服务：" + (", ".join(
        f"{s['name']}={'在跑' if s['active'] else '未启动'}"
        for s in wr["services"]) or "（一个都没有）"))
except Exception as e:  # noqa: BLE001
    _s += 1
    print(f"  skip 本机 Windows 探测（{type(e).__name__}: {e}）")

# 假数据也要认
wfake = ("@os|Microsoft Windows 11 专业版\n"
         "@winsvc|RemoteAccess|Running|Automatic\n"
         "@winsvc|IKEEXT|Running|Manual\n"
         "@winsvc|OpenVPNService|Stopped|Manual\n")
wr = T.parse_windows_probe(wfake)
keys = [s["key"] for s in wr["services"]]
chk(keys == ["rras", "ikeext", "openvpn"], "三个服务都认出来", str(keys))
chk(wr["services"][0]["active"] and not wr["services"][2]["active"],
    "运行状态对（RRAS 在跑、OpenVPN 停了）")

print("\n" + "=" * 52)
print(f"结果：{_p} 通过 / {_f} 失败" + (f" / {_s} 跳过" if _s else ""))
sys.exit(0 if _f == 0 else 1)
