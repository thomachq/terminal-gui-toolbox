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
# 形如 PASSWORD=xxx / PASSWD=xxx 的环境变量赋值。
# 特意不收裸 `PWD`：那在 Linux 里是「当前目录」，遮掉等于把定位信息毁了；
# 只认 `_PWD` 这种明显是口令的（MYSQL_PWD、DB_PWD）。
ENV_SECRET = re.compile(
    r"(?P<key>\b(?:[A-Z0-9_]*(?:PASSWORD|PASSWD|PASSPHRASE|_PWD|_PW|SECRET"
    r"|TOKEN))\s*=\s*)(?P<val>\"[^\"]*\"|'[^']*'|\S+)")

# 客户端把口令直接挂在参数上。这几条是运维里最常见的几种，照着它们的
# 语法单独写，不敢用通用的 `-p`：ssh 的 -p 是端口、tar 的 -p 是权限，
# 一律打码只会让人看不懂日志。
#   sshpass -p xxx / mysql -pXXX / mysqldump -pXXX / redis-cli -a xxx
# 注意值必须以非 `-` 开头：`mysql -p -u root` 是「提示输入」，不该打码。
CLIENT_SECRET = re.compile(
    r"(?P<key>\b(?:sshpass\s+-p|redis-cli\s+-a|docker\s+login\s+-p"
    r"|(?:mysql|mysqldump|mysqladmin)\b[^\n]*?\s-p)\s*)"
    r"(?P<val>[^-\s]\S*)", re.I)

# openssl -passin pass:xxx —— 格式自带一个 pass: 前缀，单独一条更好读
PASSIN = re.compile(r"(?P<key>\s-passin\s+pass:)(?P<val>\S+)", re.I)

# curl -u user:pass —— 冒号前是用户名（留着），冒号后才是口令
CURL_AUTH = re.compile(
    r"(?P<key>\bcurl\b[^\n]*?\s-u\s+[^\s:]+:)(?P<val>\S+)", re.I)

# URL 里内嵌的凭据：https://user:pass@host、redis://:pass@host
URL_AUTH = re.compile(
    r"(?P<key>[a-z][a-z0-9+.-]*://[^/\s:@]*:)(?P<val>[^@\s/]+)(?=@)", re.I)

# echo '密码' | sudo -S —— 把口令喂给 sudo 的经典写法
ECHO_SUDO = re.compile(
    r"(?P<key>\becho\s+(?P<q>['\"]))(?P<val>.*?)(?P=q)(?=\s*\|\s*sudo\b)",
    re.I | re.S)

# smbclient -U user%pass
SMB_SECRET = re.compile(
    r"(?P<key>\bsmbclient\b[^\n]*?-U\s+[^\s%]*%)(?P<val>\S+)", re.I)

# htpasswd -b 文件 用户 口令 —— -b 的意思就是「口令写在命令行里」
HTPASSWD = re.compile(
    r"(?P<key>\bhtpasswd\b[^\n]*?\s-[a-zA-Z]*b[a-zA-Z]*\s+\S+\s+\S+\s+)"
    r"(?P<val>[^\s-]\S*)", re.I)

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
    """命令行里内联的口令换成 ******（PEM 正文同样处理）。

    顺序有讲究：先挡「整块私钥」，再挡参数与环境变量，最后是几种客户端
    自己的写法。都是幂等的 —— 同一行连过两遍不会越遮越乱。
    """
    out = scrub(cmd)
    out = ARG_SECRET.sub(lambda m: m.group("key") + MASK, out)
    out = ENV_SECRET.sub(lambda m: m.group("key") + MASK, out)
    out = CLIENT_SECRET.sub(lambda m: m.group("key") + MASK, out)
    out = CURL_AUTH.sub(lambda m: m.group("key") + MASK, out)
    out = URL_AUTH.sub(lambda m: m.group("key") + MASK, out)
    out = ECHO_SUDO.sub(lambda m: m.group("key") + MASK + m.group("q"), out)
    out = SMB_SECRET.sub(lambda m: m.group("key") + MASK, out)
    out = HTPASSWD.sub(lambda m: m.group("key") + MASK, out)
    out = PASSIN.sub(lambda m: m.group("key") + MASK, out)
    return out


def has_secret(text: str) -> bool:
    """文本里是否含私钥正文（自测用）。"""
    return bool(PEM.search(text or "") or PPK.search(text or ""))
