# -*- coding: utf-8 -*-
"""远程管理：sshd 配置 / SSH 密钥 / RDP / XRDP 的测试。全部纯函数，不连网。"""
from __future__ import annotations

import base64
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import remote as R  # noqa: E402

_p = _f = 0


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


SSHD = """# /etc/ssh/sshd_config
Port 22
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
#PasswordAuthentication yes
X11Forwarding no
MaxAuthTries 3
Subsystem sftp /usr/lib/openssh/sftp-server

Match User backup
    PasswordAuthentication yes
    Port 2222
"""

# ==================================================== 1. 解析 sshd_config
sec("1. 解析 sshd_config")
cfg = R.parse_sshd(SSHD)
chk(cfg.get("port") == "22", "读到端口", cfg.get("port"))
chk(cfg.get("passwordauthentication") == "no",
    "取第一次出现的值（注释行不算）", cfg.get("passwordauthentication"))
chk(cfg.get("maxauthtries") == "3", "读到 MaxAuthTries")
chk("match" not in cfg, "Match 段不塞进全局配置")
chk(R.parse_sshd("Port=2222\n")["port"] == "2222", "`Port=2222` 等号写法也认")
eff = R.sshd_effective(cfg)
chk(eff["KbdInteractiveAuthentication"] == "no", "没写就落到默认值")
chk(R.key_only(cfg) is True, "判定「只允许密钥登录」")
chk(R.key_only(R.parse_sshd("PasswordAuthentication yes\n")) is False,
    "允许密码时判定为 False")
# 没写 PasswordAuthentication 时 sshd 默认 yes
chk(R.key_only({}) is False, "空配置默认允许密码")

# ==================================================== 2. sshd 修改计划
sec("2. sshd 修改计划")
cmds = R.sshd_plan("/etc/ssh/sshd_config",
                   {"PasswordAuthentication": "no",
                    "PubkeyAuthentication": "yes"}, init="systemd", unit="ssh")
chk(cmds[0].startswith("cp -p '/etc/ssh/sshd_config'"), "第一步先备份", cmds[0])
sed = cmds[1]
chk(sed.count("-e ") == 4, "两个指令各写两条 -e（大小写两种）",
    str(sed.count("-e ")))
chk("Id'" not in sed, "没有用 busybox 不认的 I 修饰符")
chk("PasswordAuthentication no" in cmds[2]
    or "PasswordAuthentication no" in cmds[2], "追加新值", cmds[2])
check = cmds[-1]
chk("sshd -t" in check, "reload 前先 sshd -t 校验")
chk("cp -p" in check and "回滚" in check, "校验不过当场回滚")
chk("systemctl reload ssh" in check, "systemd 下优先 reload（不断现有连接）")
syn = R.sshd_plan("/etc/ssh/sshd_config", {"Port": "2222"}, init="syno")
chk("synopkg restart SSH" in syn[-1] or "synoservice" in syn[-1],
    "群晖用 synoservice/synopkg", syn[-1])
try:
    R.sshd_plan("/x", {"Port": "22;rm -rf /"})
    chk(False, "值里带 shell 元字符要拒绝")
except R.RemoteError as e:
    chk("不能用" in str(e), "值里带 shell 元字符要拒绝", str(e))

# ==================================================== 3. 生成密钥
sec("3. 生成密钥对")
priv, pub = R.gen_keypair("ed25519", comment="tb@host")
chk(priv.startswith("-----BEGIN OPENSSH PRIVATE KEY-----"),
    "私钥是 OpenSSH 格式（能直接给 ssh 用）", priv.splitlines()[0])
chk(pub.startswith("ssh-ed25519 "), "公钥是 ssh-ed25519 单行", pub[:30])
chk(pub.endswith("tb@host"), "注释写在末尾")
chk(R.fingerprint_openssh(pub).startswith("SHA256:"), "算得出 SHA256 指纹",
    R.fingerprint_openssh(pub))

priv2, pub2 = R.gen_keypair("rsa", bits=3072)
chk(pub2.startswith("ssh-rsa "), "RSA 公钥类型对", pub2[:20])
info = R.parse_pubkey(pub2)
chk(info["type"] == "ssh-rsa", "解析回类型", info["type"])
chk(info["source"] == "openssh", "来源标成 openssh")

# ==================================================== 4. 三种公钥格式
sec("4. 公钥三种格式互转")
blob = base64.b64decode(pub.split()[1])
b64 = base64.b64encode(blob).decode()
wrapped = "\n".join(textwrap.wrap(b64, 64))

ssh2 = ('---- BEGIN SSH2 PUBLIC KEY ----\n'
        'Comment: "converted"\n' + wrapped +
        '\n---- END SSH2 PUBLIC KEY ----\n')
i2 = R.parse_pubkey(ssh2)
chk(i2["type"] == "ssh-ed25519", "RFC4716 认出类型", i2["type"])
chk(i2["key"] == pub.split()[1], "RFC4716 转出来的公钥和原来一致")
chk(i2["comment"] == "converted", "Comment 读出来了", i2["comment"])
chk(i2["source"] == "ssh2", "来源标成 ssh2")

ppk = (f"PuTTY-User-Key-File-2: ssh-ed25519\n"
       f"Encryption: none\n"
       f"Comment: putty key\n"
       f"Public-Lines: {len(textwrap.wrap(b64, 64))}\n"
       + "\n".join(textwrap.wrap(b64, 64)) + "\n"
       f"Private-Lines: 2\nAAAA\nBBBB\n")
i3 = R.parse_pubkey(ppk)
chk(i3["type"] == "ssh-ed25519", ".ppk 认出类型", i3["type"])
chk(i3["key"] == pub.split()[1], ".ppk 的公钥和 OpenSSH 版一致")
chk(i3["comment"] == "putty key", ".ppk 的 Comment 读出来", i3["comment"])

chk(R.pub_to_line(i3) == f"ssh-ed25519 {pub.split()[1]} putty key",
    "统一成 authorized_keys 一行", R.pub_to_line(i3))

for bad in ("这不是公钥", "ssh-ed25519 AAAAgarbage", ""):
    try:
        R.parse_pubkey(bad)
        chk(False, f"垃圾输入要报错：{bad[:12]!r}")
    except R.RemoteError:
        chk(True, f"垃圾输入报错：{bad[:12]!r}")

# ==================================================== 5. 写入 authorized_keys
sec("5. 写 authorized_keys")
lines = R.authorized_keys_append_cmd(R.pub_to_line(i2))
joined = "\n".join(lines)
chk("mkdir -p" in joined and "chmod 700" in joined, "先建 .ssh 并给 700")
chk("chmod 600" in joined, "authorized_keys 给 600")
chk("grep -qF" in joined, "重复的公钥不再追加", joined[:120])
chk(joined.count(">>") == 1, "只有一条真正的写入")
try:
    R.authorized_keys_append_cmd("")
    chk(False, "空公钥要报错")
except R.RemoteError:
    chk(True, "空公钥要报错")

# ==================================================== 6. RDP
sec("6. RDP")
rdp = R.parse_rdp("enabled=1\nnla=1\nport=3389\nfw_rules=2\n")
chk(rdp["enabled"] and rdp["nla"] is True and rdp["port"] == 3389
    and rdp["fw_rules"] == 2, "四项都读对", str(rdp))
off = R.rdp_plan(enable=False)
chk(any("fDenyTSConnections -Value 1" in c for c in off), "关闭写 fDenyTSConnections=1")
chk(any("Disable-NetFirewallRule" in c for c in off), "顺手关防火墙规则")
on = R.rdp_plan(enable=True)
chk(any("fDenyTSConnections -Value 0" in c for c in on), "开启写 0")
chk(any("Enable-NetFirewallRule" in c for c in on),
    "开启时同时放行防火墙（否则外面还是连不上）")
p = R.rdp_plan(port=3390)
chk(any("PortNumber -Value 3390" in c for c in p), "改端口")
chk(any("LocalPort 3390" in c for c in p), "新端口加防火墙规则")
chk(any("Restart-Service TermService" in c for c in p), "最后重启 TermService")
chk(R.rdp_plan() == [], "什么都不改就返回空")
try:
    R.rdp_plan(port=70000)
    chk(False, "端口越界要报错")
except R.RemoteError:
    chk(True, "端口越界要报错")

# ==================================================== 7. XRDP
sec("7. XRDP")
ini = """[Globals]
port=3389
max_bpp=32
security_layer=negotiate
crypt_level=high

[Xorg]
name=Xorg
port=-1
"""
cfg2 = R.parse_ini(ini)
chk(cfg2.get("globals.port") == "3389", "读到 [Globals] 的 port")
chk(cfg2.get("xorg.port") == "-1", "别的段的同名键不混进来", cfg2.get("xorg.port"))
summ = R.xrdp_summary(cfg2)
chk(summ["port"] == "3389" and summ["installed"] is True, "摘要成形", str(summ))
xc = R.xrdp_plan("/etc/xrdp/xrdp.ini", {"port": "3390", "max_bpp": "24"})
chk(xc[0].startswith("cp -p"), "先备份")
chk(all("sec==\"[globals]\"" in c for c in xc[1:3]), "只改 [Globals] 段", xc[1][:120])
chk("exit 9" in xc[1], "键不存在时报错而不是往段外追加")
chk(xc[-1] == "systemctl restart xrdp", "最后重启 xrdp", xc[-1])
chk(R.xrdp_plan("/x", {}) == [], "没有要改的就返回空")

# ==================================================== 8. 探测
sec("8. 服务探测")
probe = R.parse_remote_probe("@os Linux\n@sshd 1\n@xrdp 0\n@vnc 1\n"
                             "@ports 0.0.0.0:22 0.0.0.0:5900 \n")
chk(probe["os"] == "Linux", "读到系统")
chk(probe["sshd"] and not probe["xrdp"] and probe["vnc"], "三个服务状态对",
    str(probe))
chk(probe["ports"] == ["0.0.0.0:22", "0.0.0.0:5900"], "监听端口列表",
    str(probe["ports"]))
cmd = R.probe_remote_cmd()
chk("@sshd" in cmd and "@xrdp" in cmd and "@vnc" in cmd, "探测命令问三样")
chk("/usr/sbin/sshd" in cmd, "用绝对路径判（PATH 里可能没有）")

print("\n" + "=" * 52)
print(f"结果：{_p} 通过 / {_f} 失败")
sys.exit(0 if _f == 0 else 1)
