# -*- coding: utf-8 -*-
"""敏感内容脱敏 —— 所有要落到界面上的文本都必须先过这里。

为什么单独一个模块：私钥正文的泄露点不止一处。命令日志、工具输出面板、
系统信息面板、实时终端（程序旁路 + 用户手敲命令的返回）、VPN 控制台日志，
以前各自处理，加一处漏一处。现在统一到一个函数，新面板只要调 scrub()
就自动有保护。

挡住三类正文：

  1. PEM 私钥          -----BEGIN [OPENSSH|RSA|EC|DSA|PRIVATE] KEY-----
  2. OpenVPN ta.key    -----BEGIN OpenVPN Static key V1-----
  3. PuTTY .ppk        Private-Lines: N 之后的 base64 块

只打码正文，首尾标记保留 —— 用户能看出"这里原本是私钥"，但看不到内容，
也不会被误认成程序出错。证书（CERTIFICATE）不在范围内，那是公开内容，
VPN 控制台要显示它。
"""
from __future__ import annotations

import re

# PEM 私钥 / OpenVPN 静态密钥。\1 反向引用保证 BEGIN 和 END 是同一类型，
# 不会把相邻两个不同块吞成一个。
PEM = re.compile(
    r"-----BEGIN ((?:[A-Z0-9 ]*PRIVATE KEY)|(?:OpenVPN Static key V1))-----"
    r".*?"
    r"-----END \1-----", re.S)

# PuTTY .ppk：头是明文，正文在 Private-Lines: 之后
PPK = re.compile(
    r"(Private-Lines:\s*)(\d+)(\s*\n)"
    r"(?:[A-Za-z0-9+/=]+[ \t]*\n){1,}", re.I)

# 命令行里直接写密码的几种常见形态：--password xxx / --passphrase=xxx。
# 注意不碰 `-p`：ssh 的 -p 是端口、tar 的 -p 是权限，误伤比漏遮更烦人。
ARG_SECRET = re.compile(
    r"(?P<key>(?:^|\s)(?:--password|--passwd|--passphrase|--key-passphrase)"
    r"(?:=|\s+))(?P<val>\"[^\"]*\"|'[^']*'|\S+)", re.I)
# 形如 PASSWORD=xxx / PASSWD=xxx 的环境变量赋值
ENV_SECRET = re.compile(
    r"(?P<key>\b(?:[A-Z0-9_]*(?:PASSWORD|PASSWD|PASSPHRASE|_PW|SECRET|TOKEN))"
    r"\s*=\s*)(?P<val>\"[^\"]*\"|'[^']*'|\S+)")

MASK = "******"
HIDDEN_HINT = "私钥内容已隐藏"


def _pem_sub(m: re.Match) -> str:
    body = m.group(0)
    if HIDDEN_HINT in body:      # 已经打过码，原样返回（scrub 幂等）
        return body
    n = body.count("\n")
    return (f"-----BEGIN {m.group(1)}-----\n"
            f"[{HIDDEN_HINT}，共 {n} 行]\n"
            f"-----END {m.group(1)}-----")


def _ppk_sub(m: re.Match) -> str:
    if HIDDEN_HINT in m.group(0):
        return m.group(0)
    lines = [ln for ln in m.group(0).splitlines()[1:] if ln.strip()]
    return (f"{m.group(1)}{m.group(2)}{m.group(3)}"
            f"[{HIDDEN_HINT}，共 {len(lines)} 行]\n")


def scrub(text: str) -> str:
    """把私钥正文整块换成一行说明，其余原文不动。"""
    if not text or "-----" not in text and "Private-Lines" not in text:
        return text
    out = PEM.sub(_pem_sub, text)
    out = PPK.sub(_ppk_sub, out)
    return out


def scrub_cmd(cmd: str) -> str:
    """命令行里内联的密码换成 ******（PEM 正文同样处理）。"""
    out = scrub(cmd)
    out = ARG_SECRET.sub(lambda m: m.group("key") + MASK, out)
    out = ENV_SECRET.sub(lambda m: m.group("key") + MASK, out)
    return out


def has_secret(text: str) -> bool:
    """文本里是否含私钥正文（自测用）。"""
    return bool(PEM.search(text or "") or PPK.search(text or ""))
