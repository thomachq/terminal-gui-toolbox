# -*- coding: utf-8 -*-
"""群晖防火墙持久化 —— **只读探测**，不写任何规则。

目的：搞清 DSM 上 iptables 规则重启为什么会丢、官方提供的持久化落点是什么，
再决定工具箱该怎么给建议/怎么一键持久化。

只跑 read-only 命令：cat / ls / command -v / iptables -S。绝不 -A / -I / -D。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import crypto_store                                # noqa: E402
from core.session import SSHSession                # noqa: E402
from profiles import ProfileStore                  # noqa: E402

DATA = ROOT / "data"
crypto_store.set_key_dir(DATA)

store = ProfileStore(DATA / "profiles.dat")
hosts = store.data.get("profiles") or []
print(f"档案 {len(hosts)} 条")
for h in hosts:
    print(f"  - {h.get('name')}  {h.get('host')}:{h.get('port')}  {h.get('user')}")

# 挑群晖那条。
# 坑：git 存档里有 chromas.synology.me（DDNS 域名），简单 substring 会命中它，
# 连上去其实是 Forgejo，命令全被当成 git 子命令吃掉。
# 判据按「可信度」打分，git 账号 / 公网域名一律排除。
def _score(h: dict) -> int:
    host = str(h.get("host") or "").lower()
    user = str(h.get("user") or "").lower()
    if user in ("git", "gitolite", "gitea", "forgejo"):
        return -1
    s = 0
    if host.startswith(("192.168.", "10.", "172.16.", "172.17.", "172.18.",
                        "172.19.", "172.2", "172.30.", "172.31.")):
        s += 100                      # 内网地址最可能是那台 NAS
    if str(h.get("name") or "").strip().lower() in ("synology", "dsm", "群晖"):
        s += 50
    if "synology" in host or "dsm" in host:
        s += 10
    if int(h.get("port") or 22) == 3022:
        s += 20                       # DSM 的 SSH 端口
    return s


ranked = sorted(hosts, key=_score, reverse=True)
target = ranked[0] if ranked and _score(ranked[0]) > 0 else None
if target is None:
    print("没找到群晖档案，退出")
    raise SystemExit(1)

print(f"\n连接 {target.get('name')} …")
sess = SSHSession()
try:
    sess.connect(target.get("host"), int(target.get("port") or 22),
                 target.get("user") or "",
                 password=target.get("password") or None,
                 key_path=target.get("key_path") or None,
                 key_passphrase=target.get("key_passphrase") or None)
except Exception as e:  # noqa: BLE001
    print("连不上：", type(e).__name__, e)
    raise SystemExit(2)
print("已连接")

CMDS = [
    ("DSM 版本", "cat /etc.defaults/VERSION 2>/dev/null | head -8"),
    ("iptables 版本", "iptables --version 2>&1"),
    ("是否有 synofirewall", "ls -l /usr/syno/bin/synofirewall /usr/syno/sbin/synofirewall 2>&1 | head -3"),
    ("规则目录 firewall.d", "ls -la /usr/syno/etc/firewall.d/ 2>&1 | head -10"),
    ("规则目录 /etc/firewall.d", "ls -la /etc/firewall.d/ 2>&1 | head -10"),
    ("rc.d 启动脚本目录", "ls -la /usr/local/etc/rc.d/ 2>&1 | head -10"),
    ("iptables-save 存在", "command -v iptables-save; ls -l /usr/sbin/iptables-save 2>&1"),
    ("已有 INPUT 规则数", "iptables -S INPUT 2>/dev/null | wc -l"),
    ("是否 root", "id -u"),
    ("sudo 是否免密", "sudo -n true 2>&1; echo rc=$?"),
    ("开机脚本示例", "ls /usr/local/etc/rc.d/*.sh 2>/dev/null | head -5"),
    ("DSM 自带防火墙服务", "ps w 2>/dev/null | grep -i 'synofirewall\\|firewall' | grep -v grep | head -5"),
]

for name, cmd in CMDS:
    rc, out, err = sess.run(cmd, timeout=25)
    out = (out or "").strip()
    err = (err or "").strip()
    print(f"\n### {name}   (rc={rc})")
    if out:
        print(out[:900])
    if err:
        print("[stderr]", err[:300])

sess.close()
print("\n只读探测结束（没有改任何规则）")
