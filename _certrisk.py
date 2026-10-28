# -*- coding: utf-8 -*-
"""「一键装证书」写入风险评估 —— 纯只读。

只回答四件事：
  1. nginx 在哪、root 下 `nginx -t` 能不能过（决定「先校验后 reload」可不可行）
  2. 服务管理是 systemctl 还是 synosystemctl（决定 reload 怎么下）
  3. 当前默认证书 / 在用证书指向哪个 id（决定「动它会牵连谁」）
  4. 群晖 synowebapi 是否可用（官方绑定入口，绝对路径才准）

一律只读：ls / cat / grep / test / command -v / nginx -t（只解析配置）。
不 tar、不 cp、不写、不 reload。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import crypto_store                                              # noqa: E402
from core.session import SSHSession, SessionError                 # noqa: E402
from profiles import ProfileStore                                # noqa: E402

DATA = Path(r"D:\Tools\Terminal-GUI-Toolbox\data")
NAS = ("192.168.3.12", "飞牛OS")

COMMON = [
    ("nginx 二进制", "for p in /usr/syno/sbin/nginx /usr/sbin/nginx /usr/bin/nginx "
                     "/usr/local/nginx/sbin/nginx /usr/trim/bin/nginx; do "
                     "[ -x $p ] && echo OK:$p; done; echo '--- PATH ---'; command -v nginx 2>/dev/null || echo '(无)'"),
    ("nginx 主进程", "ps -eo pid,ppid,user,args 2>/dev/null | grep '[n]ginx: master' | head -3"),
    ("init / 服务管理", "cat /proc/1/comm 2>/dev/null; echo '--- 工具 ---'; "
                         "for c in systemctl synosystemctl synowebapi nginx; do "
                         "command -v $c >/dev/null 2>&1 && echo YES:$c || echo NO:$c; done"),
    ("群晖绝对路径工具", "for p in /usr/syno/bin/synowebapi /usr/syno/sbin/synosystemctl "
                         "/usr/syno/bin/synosystemctl /usr/sbin/synosystemctl; do "
                         "[ -e $p ] && echo FOUND:$p; done; echo '(空=没有)'"),
]

# 需要 root 的（配置与证书目录）
ROOT_CMDS_SYNO = [
    ("nginx -t（root，只解析）", "/usr/bin/nginx -t 2>&1 | tail -4"),
    ("默认证书 id", "cat /usr/syno/etc/certificate/_archive/DEFAULT 2>&1; "
                     "echo '--- 目录 ---'; ls /usr/syno/etc/certificate/_archive/ 2>&1 | head -12"),
    ("DSM 系统证书指向", "ls -l /usr/syno/etc/certificate/system/ 2>&1 | head -12; "
                          "echo '--- default 内容 ---'; ls -lL /usr/syno/etc/certificate/system/default/ 2>&1 | head -8"),
    ("nginx 引用的证书（谁会被牵连）",
     "grep -rhoE 'ssl_certificate(_key)?[[:space:]]+[^;]+;' /etc/nginx /usr/syno/etc/nginx "
     "/usr/local/etc/nginx 2>/dev/null | sed 's/[[:space:]]\\+/ /g' | sort | uniq -c | sort -rn | head -14"),
    ("证书目录权限", "ls -ld /usr/syno/etc/certificate/_archive 2>&1"),
]

ROOT_CMDS_FNOS = [
    ("nginx -t（root，只解析）", "N=$(ls -d /usr/trim/bin/nginx /usr/sbin/nginx /usr/bin/nginx 2>/dev/null | head -1); "
                                  "echo bin=$N; [ -n \"$N\" ] && $N -t 2>&1 | tail -4"),
    ("证书配置全文", "cat /usr/trim/etc/network_cert_all.conf 2>&1 | head -60"),
    ("nginx 引用的证书（谁会被牵连）",
     "grep -rhoE 'ssl_certificate(_key)?[[:space:]]+[^;]+;' /etc/nginx /usr/trim/etc /usr/trim/var "
     "2>/dev/null | sed 's/[[:space:]]\\+/ /g' | sort | uniq -c | sort -rn | head -14"),
    ("ssls 目录", "ls -l /usr/trim/var/trim_connect/ssls/ 2>&1 | head -10"),
    ("证书权限", "ls -ld /usr/trim/etc/network_cert_all.conf /usr/trim/var/trim_connect/ssls 2>&1"),
]


def probe(h: dict) -> None:
    print("=" * 72)
    print(f"档案 {h.get('name')}  {h.get('host')}:{h.get('port')}  {h.get('user')}")
    sess = SSHSession()
    try:
        sess.connect(h.get("host") or "", int(h.get("port") or 22),
                     h.get("user") or "", key_path=h.get("key_path") or "",
                     key_passphrase=h.get("key_passphrase") or "")
    except (SessionError, Exception) as e:                       # noqa: BLE001
        print(f"连不上：{type(e).__name__}: {e}")
        return
    try:
        rc, out, _ = sess.run("test -d /usr/syno/etc/certificate; echo $?", timeout=20)
        is_syno = (out or "").strip().endswith("0")
        print(f"平台：{'群晖 DSM' if is_syno else '飞牛 fnOS'}")
        for title, cmd in COMMON:
            _do(sess, title, cmd, admin=False)
        pw = h.get("sudo_password") or ""
        if not sess.sudo_ok(pw):
            print("\n（sudo 密码没过，跳过 root 部分 —— 不重试，免得累计 passwd_tries）")
            return
        for title, cmd in (ROOT_CMDS_SYNO if is_syno else ROOT_CMDS_FNOS):
            _do(sess, title, cmd, admin=True)
    finally:
        sess.close()


def _do(sess, title: str, cmd: str, admin: bool) -> None:
    try:
        rc, out, err = sess.run_admin(cmd, timeout=60) if admin else sess.run(cmd, timeout=60)
    except Exception as e:                                       # noqa: BLE001
        print(f"\n### {title}\n[异常] {e}")
        return
    print(f"\n### {title}  (rc={rc})")
    body = (out or "").strip()
    print(body[:2200] if body else "(空)")
    tail = (err or "").strip()
    if tail:
        print(f"[stderr] {tail[:300]}")


def main() -> int:
    crypto_store.set_key_dir(DATA)
    store = ProfileStore(DATA / "profiles.dat")
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for h in store.profiles:
        if only and only not in (h.get("name") or ""):
            continue
        if (h.get("name") or "") not in NAS:
            continue
        probe(h)
    print("\n只读结束：没有上传、没有改配置、没有 reload。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
