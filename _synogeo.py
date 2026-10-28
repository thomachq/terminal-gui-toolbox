# -*- coding: utf-8 -*-
"""群晖「位置（GeoIP）」列表探测 —— **纯只读**，只 cat / ls / grep / find / strings。

要回答两件事：
  1. 中国大陆能不能细化到省份（现有数据源是什么粒度、界面下拉从哪来）；
  2. 换掉它的位置数据库，DSM 升级后会不会被覆盖（文件在哪个分区、属于配置还是系统文件）。

绝不写：没有 -A / -I / -D / --reload / --update，没有 sed -i，没有 mv。
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


def _score(h: dict) -> int:
    # 注意：按 name 认群晖，不能拿 host 去匹配 "synology"（DDNS 域名有，
    # 局域网 IP 没有）；否则 10.9.9.9 那条会跟真群晖打平甚至抢先。
    host = str(h.get("host") or "").lower()
    name = str(h.get("name") or "").lower()
    if str(h.get("user")) == "git":
        return 0
    s = 0
    if "synology" in name or "dsm" in name:
        s += 4
    if "synology" in host:
        s += 1
    if host.startswith(("192.168.", "10.")):
        s += 2
    return s


ranked = sorted(hosts, key=_score, reverse=True)
target = ranked[0] if ranked and _score(ranked[0]) > 0 else None
if target is None:
    print("没找到群晖档案")
    raise SystemExit(1)

print(f"连接 {target.get('name')} ({target.get('host')}:{target.get('port')}) …", flush=True)
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
print("已连接\n", flush=True)

CMDS: list[tuple[str, str]] = [
    ("DSM 版本与型号",
     "cat /etc.defaults/VERSION 2>/dev/null | head -8; "
     "cat /proc/sys/kernel/syno_hw_version 2>/dev/null"),

    ("【1】GeoIP 相关文件全找（/usr/syno 下）",
     "find /usr/syno -maxdepth 5 -iname '*geoip*' 2>/dev/null | head -40"),

    ("【1b】GeoIP 相关文件（其它常见位置）",
     "find /var/lib /var/packages /etc -maxdepth 5 -iname '*geoip*' 2>/dev/null | head -30"),

    ("【1c】.mmdb / GeoLite / 国家库文件",
     "find /usr /var /etc -maxdepth 6 \\( -iname '*.mmdb' -o -iname 'GeoLite*' "
     "-o -iname '*geoip*.dat' -o -iname '*country*code*' \\) 2>/dev/null | head -30"),

    ("【2】位置规则的 ipList 到底存什么（ipGroup=3）",
     "for f in /usr/syno/etc/firewall.d/*.json; do "
     "python3 -c \"import json,sys;d=json.load(open(sys.argv[1]));"
     "r=[x for x in d.get('rules',[]) if x.get('ipGroup')==3];"
     "print(sys.argv[1], len(r));"
     "[print('   ', json.dumps({k:x.get(k) for k in ('name','ipGroup','ipList','policy','protocol')}, ensure_ascii=False)) for x in r[:6]]\" "
     "\"$f\" 2>/dev/null; done"),

    ("【3】synofirewall 里跟位置有关的字符串",
     "strings /usr/syno/bin/synofirewall 2>/dev/null | grep -iE 'geoip|country|locat|ipset|region' | head -30"),

    ("【3b】synofirewall 用到的库",
     "ls -l /usr/syno/bin/synofirewall* 2>&1 | head -5; "
     "find /usr/syno -maxdepth 4 -name 'libsynofirewall*' -o -maxdepth 4 -name 'synofw*' 2>/dev/null | head -10"),

    ("【4】内核有没有 geoip / ipset 支撑",
     "lsmod 2>/dev/null | grep -iE 'geoip|ipset|xt_set' ; "
     "ls /lib/modules/$(uname -r)/kernel/net/netfilter/ 2>/dev/null | grep -iE 'geoip|ipset'; "
     "command -v ipset; ipset list -n 2>/dev/null | head -10"),

    ("【5】synofirewall 的帮助（只读）",
     "sudo -n /usr/syno/bin/synofirewall --help 2>&1 | head -45"),

    ("【6】系统分区在哪、升级会重写哪",
     "mount | grep -E ' / |/usr/syno|/tmp' | head -8; echo '---'; df -h / /usr/syno/etc 2>/dev/null"),

    ("【6b】/usr/syno/etc 与 /etc.defaults 的关系",
     "ls -ld /usr/syno/etc /etc /etc.defaults /etc.defaults/geoip 2>&1; "
     "echo '--- 谁是符号链接 ---'; readlink -f /etc /usr/syno/etc 2>/dev/null"),

    ("【7】群晖 GUI 的位置下拉从哪来（API）",
     "grep -rl 'geoip\\|GeoIP' /usr/syno/webapi /usr/syno/web 2>/dev/null | head -10"),

    ("【8】位置库若被改，有没有同步机制",
     "ls -la /usr/syno/etc/ 2>/dev/null | grep -iE 'geo|fw|fire' ; "
     "cat /usr/syno/etc/firewall.d/firewall_settings.json 2>/dev/null | head -20"),
]

out_lines: list[str] = []
for label, cmd in CMDS:
    print("=" * 70)
    print(label)
    print("-" * 70, flush=True)
    try:
        rc, out, err = sess.run(cmd, timeout=90)
    except Exception as e:  # noqa: BLE001
        print(f"[err] {type(e).__name__}: {e}")
        continue
    text = (out or "").rstrip()
    if err and err.strip():
        text += "\n[stderr] " + err.strip()[:400]
    print(text if text else "(空)")
    out_lines.append(f"{'=' * 70}\n{label}\n{'-' * 70}\n{text}\n")

Path(ROOT / "data" / "_synogeo.txt").write_text("\n".join(out_lines), encoding="utf-8")
print("\n结果已存 data/_synogeo.txt")
