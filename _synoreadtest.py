# -*- coding: utf-8 -*-
"""只读探测脚本的**安全闸门**：命令里出现任何写操作就判红。

为什么单独一个测试：`_synoread.py` 是要在别人（或用户自己）的生产 NAS 上跑的，
一旦哪次改顺手加了 `-A` 或者 `synofirewall --reload`，轻则规则被冲掉，
重则 SSH 进不去（DSM 的默认动作是 Deny 时，把自己关门外只能按 RESET 键）。
所以「只读」这件事不能靠人记，得让机器每次叫一遍。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import _synoread as S                                          # noqa: E402

PASS = 0
FAIL = 0


def chk(cond: bool, label: str, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}   {extra}")


# 写操作白名单：这些子串出现就说明不是"只读"了
BANNED = [
    (r"(?:^|\s)-A\s", "追加规则 -A"),
    (r"(?:^|\s)-I\s", "插入规则 -I"),
    (r"(?:^|\s)-D\s", "删除规则 -D"),
    (r"(?:^|\s)-F\b", "清空规则 -F"),
    (r"(?:^|\s)-X\b", "删链 -X"),
    (r"(?:^|\s)-P\s", "改默认策略 -P"),
    (r"--reload", "重载防火墙 --reload"),
    (r"iptables-restore", "iptables-restore"),
    (r"\brm\b", "rm"),
    (r"\bmv\b", "mv"),
    (r"\bcp\b", "cp"),
    (r"sed\s+-i", "sed -i 原地改"),
    (r"\btee\b", "tee 写文件"),
    (r"\bchmod\b", "chmod"),
    (r"\bchown\b", "chown"),
    (r"systemctl\s+(?:start|stop|restart|enable|disable)", "改服务状态"),
    (r"synowebapi", "调 DSM 写接口"),
]

# `>/dev/null`、`2>/dev/null` 是重定向到黑洞，`2>&1` 只是把 stderr 并到 stdout，
# 都不算写文件；其余 `>` 一旦出现在命令里就要人工看一眼。
REDIR_OK = re.compile(r"[12&]?>\s*/dev/null|[12]?>&[12]")

print("[1] 每条命令都不含写操作")
for title, cmd in S.CMDS:
    bad = [why for pat, why in BANNED if re.search(pat, cmd)]
    chk(not bad, f"只读：{title}", "含 " + "、".join(bad) if bad else "")

print("\n[2] 没有往真实文件重定向")
for title, cmd in S.CMDS:
    stripped = REDIR_OK.sub("", cmd)
    chk(">" not in stripped, f"无真重定向：{title}", stripped[:80])

print("\n[3] JSON schema 探测也守规矩")
bad = [why for pat, why in BANNED if re.search(pat, S.SCHEMA_CMD)]
chk(not bad, "只读：JSON 键名探测", "含 " + "、".join(bad) if bad else "")
chk("sed -n" in S.SCHEMA_CMD, "schema 用 sed -n（只打印不落盘）")
chk("sed -i" not in S.SCHEMA_CMD, "schema 没用 sed -i")

print("\n[4] 命令清单本身的结构")
chk(len(S.CMDS) >= 10, f"覆盖足够多的探测点（{len(S.CMDS)} 条）")
chk(all(isinstance(t, str) and t for t, _ in S.CMDS), "每条都有中文标题")
chk(any("firewall.d" in c for _, c in S.CMDS), "会去看 firewall.d 目录")
chk(any("synofirewall" in c for _, c in S.CMDS), "会去找 synofirewall 二进制")
chk(any("iptables -S" in c for _, c in S.CMDS), "会读内核态（-S 只列不改）")

print("\n[5] 脚本层面的护栏")
src = (ROOT / "_synoread.py").read_text(encoding="utf-8")
chk("getpass" in src, "口令用 getpass 输入（不回显）")
chk("SYNO_KEYPW" in src, "支持环境变量传口令")
chk("没有执行 --reload" in src.replace("也", "") or "也没有执行 --reload" in src,
    "结尾明确声明没执行 --reload")
chk("git" in src and "forgejo" in src, "排除 git 远端档案（synology.me 是 Forgejo）")

print("\n[6] 连接层用的是修好后的私钥加载")
chk("load_private_key" in (ROOT / "core" / "session.py").read_text(encoding="utf-8"),
    "SSHSession 走 load_private_key")

print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
sys.exit(1 if FAIL else 0)
