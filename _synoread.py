# -*- coding: utf-8 -*-
"""群晖防火墙 **只读** 探测 —— 只 cat / ls / grep，绝不 -A / -I / -D / --reload。

为什么要有这个脚本
------------------
工具箱的防火墙页只跑 `iptables -S`（内核当前态）。群晖的规则真身不在内核里，
而在 `/usr/syno/etc/firewall.d/*.json`，由 `synofirewall` 在开机 / 改设置时
重新刷进 iptables。所以「改完重启就没了」不是权限问题，是改错了层。

这个脚本只回答四个问题，一个字节都不写：
  1. 那份持久化配置到底在哪个路径、能不能读；
  2. 它的 JSON 里有没有「目标地址」这个字段（DSM GUI 没有，schema 里可能有）；
  3. 改完该用什么命令重载（synofirewall？netfilter-persistent？）；
  4. 重载会不会把 raw iptables 规则冲掉 —— 这决定「目标 IP」该怎么落。

用法
----
  python _synoread.py                 # 交互输入私钥口令（不回显、不进日志）
  python _synoread.py --list          # 只看要跑哪些命令，不连接
  python _synoread.py --save          # 结果另存一份 txt 到脚本同目录

口令也可以放在环境变量 SYNO_KEYPW 里，避免手动输入。
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import crypto_store                                              # noqa: E402
from core.session import SSHSession, SessionError                 # noqa: E402
from profiles import ProfileStore                                # noqa: E402

DATA = ROOT / "data"

# ---------------------------------------------------------------- 只读命令
# (标题, 命令)。全部是读：ls / cat / grep / command -v / -S / -L。
# 出现 -A / -I / -D / -F / --reload / > / rm 一律算违规，有单测盯着。
CMDS: list[tuple[str, str]] = [
    ("DSM 版本", "cat /etc.defaults/VERSION 2>/dev/null | head -8"),
    ("当前身份", "id"),
    ("sudo 是否免密", "sudo -n true >/dev/null 2>&1; echo rc=$?"),
    ("防火墙相关路径全找一遍",
     "find /usr/syno/etc -maxdepth 2 -iname '*firewall*' 2>/dev/null | head -20"),
    ("firewall.d 目录",
     "ls -la /usr/syno/etc/firewall.d/ 2>&1 | head -20"),
    ("synofirewall 在哪",
     "for p in /usr/syno/bin/synofirewall /usr/syno/sbin/synofirewall "
     "/usr/syno/bin/synofirewall.sh; do [ -e $p ] && ls -l $p; done; "
     "command -v synofirewall 2>/dev/null"),
    ("防火墙服务在跑吗",
     "ps w 2>/dev/null | grep -i 'synofirewall\\|netfilter' | grep -v grep | head -5"),
    ("内核里谁在管规则",
     "command -v nft; command -v iptables; command -v iptables-save"),
    ("INPUT 链现状（前 30 行）", "iptables -S INPUT 2>/dev/null | head -30"),
    ("群晖自己的链名", "iptables -S 2>/dev/null | grep -o '^-N [^ ]*' | head -20"),
    ("开机脚本目录", "ls -la /usr/local/etc/rc.d/ 2>&1 | head -10"),
    ("计划任务表", "cat /etc/crontab 2>/dev/null | head -20"),
    ("有没有 iptables 存档文件",
     "ls -l /etc/iptables/rules.v4 /etc/sysconfig/iptables 2>&1 | head -5"),
]

# JSON schema 探测：只取第一份，看键名，不打印值（值里可能有你的 IP）
SCHEMA_CMD = (
    "f=$(ls /usr/syno/etc/firewall.d/*.json 2>/dev/null | head -1); "
    "[ -n \"$f\" ] && { echo \"文件: $f\"; "
    "echo '--- 顶层键 ---'; "
    "sed -n '1,40p' \"$f\" | tr ',' '\\n' | grep -o '\"[A-Za-z_]\\+\"[[:space:]]*:' "
    "| sort -u | head -40; } 2>&1"
)


def _pick_target(store: ProfileStore) -> dict | None:
    """挑群晖那条。git 账号 / 公网 DDNS 域名一律排除。

    坑：`chromas.synology.me` 是 git 远端（Forgejo），名字里也有 synology，
    简单 substring 会选到它，上去之后 `iptables` 全被当成 git 子命令吃掉。
    """
    hosts = store.data.get("profiles") or []

    def score(h: dict) -> int:
        host = str(h.get("host") or "").lower()
        user = str(h.get("user") or "").lower()
        if user in ("git", "gitolite", "gitea", "forgejo"):
            return -1
        s = 0
        if host.startswith(("192.168.", "10.", "172.16.", "172.17.", "172.18.",
                            "172.19.", "172.2", "172.30.", "172.31.")):
            s += 100
        if str(h.get("name") or "").strip().lower() in ("synology", "dsm", "群晖"):
            s += 50
        if "synology" in host:
            s += 10
        if str(h.get("port") or "") == "3022":
            s += 20
        return s

    ranked = sorted(hosts, key=score, reverse=True)
    return ranked[0] if ranked and score(ranked[0]) > 0 else None


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--list", action="store_true", help="只列出要跑的只读命令")
    ap.add_argument("--save", action="store_true", help="结果另存 txt")
    ap.add_argument("--profile", default="", help="指定档案名（默认自动挑群晖）")
    args = ap.parse_args()

    if args.list:
        print("以下命令全部只读（ls / cat / grep / -S），不会改任何规则：\n")
        for i, (t, c) in enumerate(CMDS, 1):
            print(f"{i:2}. {t}\n    {c}")
        print(f"\n附. 持久化配置的 JSON 键名\n    {SCHEMA_CMD}")
        return 0

    crypto_store.set_key_dir(DATA)
    store = ProfileStore(DATA / "profiles.dat")
    if args.profile:
        target = next((h for h in (store.data.get("profiles") or [])
                       if str(h.get("name") or "") == args.profile), None)
    else:
        target = _pick_target(store)
    if target is None:
        print("没找到群晖档案（主机页里要有内网地址那条）。")
        return 1

    name = target.get("name")
    host = target.get("host")
    port = int(str(target.get("port") or 22))
    user = target.get("user") or ""
    key_path = target.get("key_path") or ""
    pw = target.get("key_passphrase") or os.environ.get("SYNO_KEYPW") or ""

    print(f"目标：{name}  {user}@{host}:{port}")
    print(f"私钥：{key_path or '（未指定）'}")
    if not key_path:
        print("这条档案没用私钥认证，本脚本只支持私钥方式。")
        return 1
    if not pw:
        print("档案里没存私钥口令，请输入（不回显、不保存、不进日志）：")
        try:
            pw = getpass.getpass("  私钥密码: ")
        except (KeyboardInterrupt, EOFError):
            print("\n已取消")
            return 1
    if not pw:
        print("没给口令，退出。")
        return 1

    sess = SSHSession()
    try:
        sess.connect(host, port, user, key_path=key_path, key_passphrase=pw)
    except SessionError as e:
        print(f"\n连不上：{e}")
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"\n连不上：{type(e).__name__}: {e}")
        return 2
    print("已连接（下面的命令全部只读）\n")

    lines: list[str] = []

    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    try:
        for title, cmd in CMDS:
            rc, out, err = sess.run(cmd, timeout=30)
            emit(f"### {title}   (rc={rc})")
            body = (out or "").strip()
            if body:
                emit(body[:1400])
            tail = (err or "").strip()
            if tail:
                emit(f"[stderr] {tail[:300]}")
            emit()
            # 有一批目录是非 root 读不到的，补一遍 sudo -n（只读，不改）
            if rc != 0 and "Permission denied" in (tail + body):
                rc2, out2, _ = sess.run("sudo -n sh -c " + repr(cmd), timeout=30)
                if rc2 == 0 and (out2 or "").strip():
                    emit("（用 sudo -n 重试，同样只读）")
                    emit((out2 or "").strip()[:1400])
                    emit()

        rc, out, _ = sess.run(SCHEMA_CMD, timeout=30)
        emit("### 持久化配置的 JSON 键名")
        emit((out or "(没找到 /usr/syno/etc/firewall.d/*.json)").strip()[:2000])
        emit()
    finally:
        sess.close()

    emit("只读探测结束：没有新增、删除或修改任何规则，也没有执行 --reload。")

    if args.save:
        out_path = ROOT / "群晖防火墙-只读探测.txt"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n已保存：{out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
