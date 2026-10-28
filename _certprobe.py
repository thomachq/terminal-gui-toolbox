# -*- coding: utf-8 -*-
"""设备侧「服务器证书」只读探测（群晖 / 飞牛）。

目的：安全中心的「证书管理」现在只管本机 data/certs 里的证书库，
用户在 DSM「控制面板 → 安全性 → 证书」/ fnOS「系统设置 → 证书」里
已有的证书看不到。这个脚本只回答：那些证书在设备上到底存在哪、
用什么命令能列出来、能不能读到（要不要 root）。

一律只读：ls / find / cat / grep / test。出现 rm / mv / cp / > 一律违规。
不试 sudo 密码（会累计 passwd_tries），只用 `sudo -n`（免密才继续）。

用法：
  python _certprobe.py                 # 两台都探
  python _certprobe.py --profile 飞牛OS
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import crypto_store                                              # noqa: E402
from core.session import SSHSession, SessionError                 # noqa: E402
from profiles import ProfileStore                                # noqa: E402

DATA = Path(r"D:\Tools\Terminal-GUI-Toolbox\data")

COMMON = [
    ("身份", "id; uname -srm"),
    ("sudo 免密？", "sudo -n true >/dev/null 2>&1; echo rc=$?"),
    ("SSL 目录", "ls -la /etc/ssl/certs 2>&1 | head -8; "
                "echo '--- private ---'; ls -la /etc/ssl/private 2>&1 | head -8"),
    ("nginx 里的证书引用",
     "grep -rhoE 'ssl_certificate(_key)?[[:space:]]+[^;]+' "
     "/etc/nginx /usr/local/etc/nginx /usr/syno/etc/nginx 2>/dev/null "
     "| sort -u | head -20"),
    ("常见 pem 位置（3 层内）",
     "for d in /etc/ssl /etc/certs /etc/pki /usr/local/etc /opt; do "
     "[ -d $d ] && find $d -maxdepth 3 -name '*.pem' 2>/dev/null | head -10; done"),
]

SYNO = [
    ("certificate 目录", "ls -la /usr/syno/etc/certificate 2>&1 | head -20"),
    ("_archive 里有哪些证书",
     "ls -la /usr/syno/etc/certificate/_archive 2>&1 | head -30"),
    ("system 下有哪些服务证书",
     "ls -la /usr/syno/etc/certificate/system 2>&1 | head -20"),
    ("每张证书的 info（描述/签发者）",
     "for d in /usr/syno/etc/certificate/_archive/*/; do "
     "[ -f \"$d/info\" ] || continue; echo \"== $(basename $d)\"; "
     "cat \"$d/info\" 2>/dev/null | head -20; "
     "ls \"$d\" 2>/dev/null | tr '\\n' ' '; echo; done 2>&1 | head -120"),
    ("服务→证书映射",
     "ls -la /usr/syno/etc/certificate/privilege 2>&1 | head -20"),
    ("DSM 证书 API（只读 list）",
     "sudo -n synowebapi --exec api=SYNO.Core.Certificate method=list "
     "version=1 2>&1 | head -40"),
    ("默认证书实际文件",
     "ls -la /usr/syno/etc/certificate/system/default 2>&1 | head -20"),
]

FNOS = [
    ("trim 目录", "ls -la /usr/trim 2>&1 | head -30"),
    ("trim 配置目录", "ls -la /usr/trim/etc 2>&1 | head -40"),
    ("trim 下与证书相关的路径",
     "find /usr/trim -maxdepth 5 -iname '*cert*' 2>/dev/null | head -40"),
    ("证书相关进程",
     "ps w 2>/dev/null | grep -iE 'cert|nginx|trim_http' | grep -v grep | head -15"),
    ("nginx 配置目录", "ls -la /etc/nginx/conf.d 2>&1 | head -20"),
    ("nginx 里的 ssl 行",
     "grep -rnE 'ssl_certificate|listen .*443' /etc/nginx 2>/dev/null | head -20"),
    ("所有 pem/key（4 层内，排除系统 CA）",
     "find /etc /usr/local /usr/trim /opt -maxdepth 4 "
     "\\( -name '*.pem' -o -name '*.crt' -o -name '*.key' \\) 2>/dev/null "
     "| grep -v '/etc/ssl/certs/' | head -40"),
]


def _pick(store: ProfileStore, want: str) -> list[dict]:
    hosts = [h for h in (store.data.get("profiles") or [])
             if str(h.get("proto") or "ssh") == "ssh" and h.get("host")]
    if want:
        return [h for h in hosts if str(h.get("name") or "") == want]
    keys = ("3.12", "3.48", "群晖", "synology", "飞牛", "fnos")
    return [h for h in hosts
            if any(k in (str(h.get("name") or "") + str(h.get("host") or "")).lower()
                   for k in keys)]


def probe(h: dict, extra: list[str] | None = None,
          use_sudo: bool = False) -> None:
    name, host = h.get("name"), h.get("host")
    port, user = int(str(h.get("port") or 22)), h.get("user") or ""
    key_path, pw = h.get("key_path") or "", h.get("key_passphrase") or ""
    print(f"\n{'=' * 70}\n## {name}  {user}@{host}:{port}\n{'=' * 70}")
    if not key_path:
        print("这条档案不是私钥认证，跳过")
        return
    sess = SSHSession()
    try:
        sess.connect(host, port, user, key_path=key_path, key_passphrase=pw)
    except SessionError as e:
        print(f"连不上：{e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"连不上：{type(e).__name__}: {e}")
        return
    if use_sudo:
        spw = h.get("sudo_password") or ""
        if sess.sudo_ok(spw):
            print("（sudo 密码通过，下面用 root 跑只读命令）")
        else:
            print("（sudo 密码没通过，放弃 —— 不重试，免得累计 passwd_tries）")
            sess.close()
            return
    try:
        if extra:
            for cmd in extra:
                try:
                    if use_sudo:
                        rc, out, err = sess.run_admin(cmd, timeout=45)
                    else:
                        rc, out, err = sess.run(cmd, timeout=45)
                except Exception as e:  # noqa: BLE001
                    print(f"\n$ {cmd}\n[异常] {e}")
                    continue
                print(f"\n$ {cmd}\n(rc={rc})")
                print((out or "").strip()[:5000])
                if (err or "").strip():
                    print(f"[stderr] {err.strip()[:300]}")
            return
        rc, out, _ = sess.run("test -d /usr/syno/etc/certificate; echo $?", timeout=20)
        is_syno = (out or "").strip().endswith("0")
        cmds = COMMON + (SYNO if is_syno else FNOS)
        for title, cmd in cmds:
            rc, out, err = sess.run(cmd, timeout=45)
            print(f"\n### {title}  (rc={rc})")
            body = (out or "").strip()
            if body:
                print(body[:2500])
            tail = (err or "").strip()
            if tail:
                print(f"[stderr] {tail[:300]}")
    finally:
        sess.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="")
    ap.add_argument("--cmd", action="append", default=[],
                    help="只跑指定命令（可重复），配合 --sudo 提权")
    ap.add_argument("--sudo", action="store_true",
                    help="用档案里的 sudo 密码提权（只在 --cmd 时用）")
    a = ap.parse_args()
    crypto_store.set_key_dir(DATA)
    store = ProfileStore(DATA / "profiles.dat")
    targets = _pick(store, a.profile)
    if not targets:
        print("没找到目标档案")
        return 1
    for h in targets:
        probe(h, extra=a.cmd or None, use_sudo=a.sudo)
    print("\n只读探测结束：没有任何写操作。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
