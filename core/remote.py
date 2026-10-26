# -*- coding: utf-8 -*-
"""远程管理：SSH 服务配置、SSH 密钥、RDP、XRDP、VNC。

三条设计约束，都来自「改错了就再也连不上」：

1. **sshd_config 改完必须 `sshd -t` 验一遍再 reload**，验不过当场回滚。
   直接 reload 的错误配置会让下一次 SSH 连接失败 —— 而我们正是靠 SSH
   在操作远端，等于把自己关在门外。
2. **只改关键项，其余原样保留**：用「删掉同名行 + 追加」的方式，而不是
   整份重写。用户配置文件里的 `Match` 段、注释、include 都不动。
3. **密钥永远不落明文日志**。生成在本机、公钥可以随便传；私钥只在
   「导出」时由用户点，且写盘权限 600。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa


class RemoteError(Exception):
    pass


# ==================================================================== SSH 服务
# 图形化要覆盖的项。key 是 sshd 的指令名（大小写不敏感），
# 值是 (中文名, 可选值, 默认值, 说明)。option 为 None 表示自由文本。
SSHD_FIELDS = (
    ("Port", "SSH 端口", None, "22",
     "改完记得同步改防火墙；不然新端口连不上、老端口已关"),
    ("PermitRootLogin", "允许 root 登录", ["prohibit-password", "yes", "no", "forced-commands-only"],
     "prohibit-password", "prohibit-password = 只能用密钥，这是推荐值"),
    ("PasswordAuthentication", "允许密码登录", ["yes", "no"], "yes",
     "关掉就成了「只允许密钥登录」"),
    ("PubkeyAuthentication", "允许密钥登录", ["yes", "no"], "yes", ""),
    ("KbdInteractiveAuthentication", "允许键盘交互认证",
     ["yes", "no"], "no", "PAM 那条路，关掉更彻底"),
    ("PermitEmptyPasswords", "允许空密码", ["yes", "no"], "no", ""),
    ("MaxAuthTries", "最大认证次数", None, "6", ""),
    ("MaxSessions", "同一连接最大会话数", None, "10", ""),
    ("ClientAliveInterval", "客户端保活间隔（秒）", None, "0",
     "0 = 不主动探测；远端网络差时设 60 能少掉线"),
    ("X11Forwarding", "允许 X11 转发", ["yes", "no"], "no",
     "不用图形转发就关掉，少一个攻击面"),
    ("AllowUsers", "只允许这些用户", None, "",
     "空格分隔，例如 alice bob；留空 = 不限制"),
    ("AllowGroups", "只允许这些组", None, "", "留空 = 不限制"),
)

# 「要密码」和「要密钥」是同一件事的两面，页面上一勾就同时改这两项。
KEY_ONLY_SET = {"PasswordAuthentication": "no",
                "PubkeyAuthentication": "yes",
                "KbdInteractiveAuthentication": "no",
                "PermitEmptyPasswords": "no"}


def parse_sshd(text: str) -> dict:
    """读 sshd_config。

    注意 sshd 自己的语义：**同一个指令出现多次，以第一次为准**（不是最后
    一次）。所以这里也取第一次，否则界面显示的值和 sshd 实际用的不一致。
    """
    out: dict[str, str] = {}
    match_blocks = 0
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Match 段之后的配置对不同用户不同，不能算进全局值
        if re.match(r"^Match\b", line, re.I):
            match_blocks += 1
            continue
        if match_blocks:
            continue
        parts = re.split(r"[\s=]+", line, maxsplit=1)
        if len(parts) < 2:
            continue
        key, val = parts[0], parts[1].strip()
        out.setdefault(key.lower(), val)
    return out


def sshd_effective(cfg: dict) -> dict:
    """把解析结果补上默认值（sshd 没写就是默认值在生效）。"""
    eff = {}
    for key, _label, _opts, default, _note in SSHD_FIELDS:
        eff[key] = cfg.get(key.lower(), default)
    return eff


def key_only(cfg: dict) -> bool:
    """当前是不是「只允许密钥登录」。"""
    eff = sshd_effective(cfg)
    return (str(eff.get("PasswordAuthentication", "yes")).lower() == "no"
            and str(eff.get("PubkeyAuthentication", "yes")).lower() == "yes")


# 这些值写进 sed 会被解释成正则/命令，必须挡住
_BAD_VALUE = re.compile(r"[`$\\\"'\n;|&<>]")


def sshd_plan(conf_path: str, settings: dict, init: str = "",
              unit: str = "") -> list[str]:
    """生成「备份 → 改 → 校验 → 通过才 reload，否则回滚」的一整条脚本。

    settings 只包含要改的项；没给的项原样不动。
    """
    stamp = _stamp()
    bak = f"{conf_path}.bak.{stamp}"
    # 每个指令写两条 -e：首字母大写和全小写各一条。**不能用 `I` 修饰符** ——
    # GNU sed 认、busybox sed（群晖就是）不认，一用就是「改了没生效」。
    deletes = " ".join(
        f"-e '/^[[:space:]]*{re.escape(k)}[[:space:]=]/d' "
        f"-e '/^[[:space:]]*{k.lower()}[[:space:]=]/d'"
        for k in settings)
    cmds = [f"cp -p '{conf_path}' '{bak}'",
            f"sed -i {deletes} '{conf_path}'"]
    add = []
    for key, val in settings.items():
        v = str(val)
        if v == "":
            continue
        if _BAD_VALUE.search(v):
            raise RemoteError(f"{key} 的值里有不能用的字符：{v}")
        add.append(f"{key} {v}")
    if add:
        body = "\\n".join(add)
        cmds.append(f"printf '%b\\n' '{body}' >> '{conf_path}'")
    # 校验 + 回滚放在**同一条** shell 里：中间断了就没有「改了但没验」的
    # 状态。`sshd -t` 对群晖/老发行版可能叫 /usr/sbin/sshd，先试两个路径。
    reload_cmd = _reload_cmd(init, unit)
    cmds.append(
        "if (sshd -t 2>/tmp/.tb_sshd_err || /usr/sbin/sshd -t 2>/tmp/.tb_sshd_err); then "
        f"{reload_cmd} && echo '设置已生效'; "
        f"else echo '配置校验没通过，已回滚：'; cat /tmp/.tb_sshd_err; "
        f"cp -p '{bak}' '{conf_path}'; exit 3; fi")
    return cmds


def _reload_cmd(init: str, unit: str = "") -> str:
    if init == "syno":
        return "/usr/syno/bin/synopkg restart SSH 2>/dev/null || " \
               "/usr/syno/sbin/synoservice --restart sshd 2>/dev/null || " \
               "/etc/init.d/sshd restart"
    if init == "openwrt":
        return "/etc/init.d/dropbear restart 2>/dev/null || /etc/init.d/sshd restart"
    if init == "sysv":
        return f"service {unit or 'sshd'} reload 2>/dev/null || service {unit or 'sshd'} restart"
    # systemd：reload 只重读配置，不断开现有连接，优先用它
    return f"systemctl reload {unit or 'sshd'} 2>/dev/null || " \
           f"systemctl restart {unit or 'sshd'}"


def sshd_read_cmd(conf_path: str = "/etc/ssh/sshd_config") -> str:
    return f"cat '{conf_path}' 2>/dev/null"


def sshd_unit_cmd() -> str:
    """判断 sshd 的 unit 叫 ssh 还是 sshd（Debian 是 ssh，RHEL 是 sshd）。"""
    return ("for u in sshd ssh; do systemctl is-active $u >/dev/null 2>&1 "
            "&& { echo $u; break; }; done")


def sshd_port_cmd() -> str:
    """读实际生效的端口（配置里可能被 include 覆盖）。"""
    return "sshd -T 2>/dev/null | awk '/^port /{print $2}' | head -5"


# ==================================================================== SSH 密钥
def _stamp() -> str:
    import time
    return time.strftime("%Y%m%d-%H%M%S")


def gen_keypair(kind: str = "ed25519", bits: int = 3072,
                comment: str = "") -> tuple[str, str]:
    """生成 OpenSSH 格式的密钥对，返回 (私钥 PEM, 公钥单行)。

    用 cryptography 而不是调 ssh-keygen：Windows 上 OpenSSH 客户端是可选
    功能，没装就没有 ssh-keygen.exe；而且调外部程序还得处理交互式询问口令。
    """
    if kind == "rsa":
        key = rsa.generate_private_key(public_exponent=65537,
                                       key_size=int(bits or 3072))
        pub_fmt = serialization.PublicFormat.OpenSSH
    else:
        key = ed25519.Ed25519PrivateKey.generate()
        pub_fmt = serialization.PublicFormat.OpenSSH
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption()).decode("ascii")
    pub = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, pub_fmt).decode("ascii")
    c = (comment or "").strip()
    if c:
        pub = f"{pub} {c}"
    return priv, pub


def fingerprint_openssh(pub_line: str) -> str:
    """算 SHA256 指纹（OpenSSH 的 `SHA256:xxx` 形式）。"""
    blob = _blob_from_line(pub_line)
    if blob is None:
        return ""
    digest = hashlib.sha256(blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _blob_from_line(pub_line: str) -> bytes | None:
    parts = str(pub_line or "").strip().split()
    if len(parts) < 2:
        return None
    try:
        return base64.b64decode(parts[1] + "=" * (-len(parts[1]) % 4),
                                validate=True)
    except (binascii.Error, ValueError):
        return None


def parse_pubkey(text: str) -> dict:
    """认三种输入：OpenSSH 单行 / SSH2(RFC4716) / PuTTY .ppk。

    返回 {type, key, comment, fingerprint, source}；认不出抛 RemoteError。
    """
    raw = str(text or "")
    if "PuTTY-User-Key-File" in raw:
        return _from_ppk(raw)
    if "BEGIN SSH2 PUBLIC KEY" in raw:
        return _from_ssh2(raw)
    one = _first_openssh_line(raw)
    if one:
        return one
    raise RemoteError(
        "认不出来。支持三种：OpenSSH 单行（ssh-ed25519 AAAA…）、"
        "SSH2 公钥（---- BEGIN SSH2 PUBLIC KEY ----）、PuTTY .ppk 文件")


_OSSH_TYPES = ("ssh-rsa", "ssh-ed25519", "ecdsa-sha2-nistp256",
               "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
               "sk-ssh-ed25519@openssh.com", "ssh-dss")


def _first_openssh_line(text: str) -> dict | None:
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split(None, 2)
        if len(parts) < 2 or parts[0] not in _OSSH_TYPES:
            continue
        blob = _blob_from_line(s)
        if blob is None:
            continue
        if not _blob_ok(blob, parts[0]):
            continue
        return {"type": parts[0], "key": parts[1],
                "comment": parts[2].strip() if len(parts) > 2 else "",
                "fingerprint": fingerprint_openssh(s), "source": "openssh"}
    return None


def _blob_ok(blob: bytes, typ: str) -> bool:
    """wire format 里第一个字段就是类型名，对得上才算这条公钥是真的。"""
    if len(blob) < 4:
        return False
    n = int.from_bytes(blob[:4], "big")
    if n <= 0 or 4 + n > len(blob):
        return False
    try:
        return blob[4:4 + n].decode("ascii") == typ
    except UnicodeDecodeError:
        return False


def _from_ssh2(text: str) -> dict:
    """RFC4716：头和尾之间是 base64，中间可能夹 `Comment: "..."` 行。"""
    body = []
    started = False
    comment = ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("---- BEGIN SSH2 PUBLIC KEY"):
            started = True
            continue
        if s.startswith("---- END SSH2 PUBLIC KEY"):
            break
        if not started or not s:
            continue
        if re.match(r"^Comment:", s, re.I):
            comment = s.split(":", 1)[1].strip().strip('"')
            continue
        if s.endswith("\\"):        # 续行
            s = s[:-1]
        body.append(s)
    if not body:
        raise RemoteError("SSH2 公钥里没找到 base64 内容")
    try:
        blob = base64.b64decode("".join(body))
    except (binascii.Error, ValueError) as e:
        raise RemoteError(f"SSH2 公钥的 base64 解不开：{e}")
    return _from_blob(blob, "ssh2", comment)


def _from_ppk(text: str) -> dict:
    """PuTTY .ppk：明文 v2 的 `Public-Lines:` 后面就是 SSH2 公钥 blob。

    v3 加密的 .ppk 公钥段也是明文存的（PuTTY 就是这么写的），所以照样能读；
    真读不到时给出「用 PuTTYgen 导出」的提示，而不是一句「失败」。
    """
    m = re.search(r"^Public-Lines:\s*(\d+)\s*$", text, re.M)
    if not m:
        raise RemoteError("这个 .ppk 里没有 Public-Lines 段")
    n = int(m.group(1))
    lines = text[m.end():].strip().splitlines()
    body = "".join(x.strip() for x in lines[:n])
    try:
        blob = base64.b64decode(body)
    except (binascii.Error, ValueError) as e:
        raise RemoteError(f".ppk 的公钥段解不开：{e}")
    comment = ""
    cm = re.search(r"^Comment:\s*(.+)$", text, re.M)
    if cm:
        comment = cm.group(1).strip().strip('"')
    return _from_blob(blob, "ppk", comment)


def _from_blob(blob: bytes, source: str, comment: str = "") -> dict:
    if len(blob) < 4:
        raise RemoteError("公钥内容太短，不像有效的 SSH 公钥")
    n = int.from_bytes(blob[:4], "big")
    if n <= 0 or 4 + n > len(blob):
        raise RemoteError("公钥结构不对（第一个字段长度越界）")
    try:
        typ = blob[4:4 + n].decode("ascii")
    except UnicodeDecodeError:
        raise RemoteError("公钥类型不是 ASCII，文件可能损坏")
    b64 = base64.b64encode(blob).decode()
    return {"type": typ, "key": b64, "comment": comment,
            "fingerprint": fingerprint_openssh(f"{typ} {b64}"),
            "source": source}


def pub_to_line(info: dict) -> str:
    """统一成可以贴进 authorized_keys 的一行。"""
    c = (info.get("comment") or "").strip()
    return f"{info['type']} {info['key']}" + (f" {c}" if c else "")


def authorized_keys_append_cmd(pub_line: str, user: str = "",
                               home: str = "") -> list[str]:
    """把公钥追加到 authorized_keys，权限一并修好。

    重复的就不加：同一把钥匙追加两次会让文件越来越乱，而且 sshd 对
    重复行没有任何好处。
    """
    if not pub_line.strip():
        raise RemoteError("公钥是空的")
    want = " ".join(pub_line.split()[:2])
    base = f"{home}/.ssh" if home else ("~" + (user or "") + "/.ssh" if user
                                       else "$HOME/.ssh")
    keyfile = f"{base}/authorized_keys"
    return [
        f"mkdir -p {base} && chmod 700 {base}",
        f"touch {keyfile} && chmod 600 {keyfile}",
        # grep -F 精确匹配「类型+内容」，注释不同不算重复
        f"grep -qF -- '{want}' {keyfile} || printf '%s\\n' "
        f"'{pub_line.strip()}' >> {keyfile}",
        f"echo 已写入 {keyfile}",
    ]


# ==================================================================== RDP（Windows）
def rdp_read_cmd() -> str:
    """一条 PowerShell 读齐 RDP 的四项关键状态。"""
    return (
        "powershell -NoProfile -Command \""
        "$t='HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server';"
        "$w='HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Terminal Server\\WinStations\\RDP-Tcp';"
        "$v=Get-ItemProperty -Path $t -Name fDenyTSConnections -ErrorAction SilentlyContinue;"
        "$n=Get-ItemProperty -Path $w -Name UserAuthentication -ErrorAction SilentlyContinue;"
        "$p=Get-ItemProperty -Path $w -Name PortNumber -ErrorAction SilentlyContinue;"
        "$fw=Get-NetFirewallRule -Group '@FirewallAPI.dll,-28752' -ErrorAction SilentlyContinue"
        " | Where-Object {$_.Enabled -eq 'True'} | Measure-Object | Select-Object -ExpandProperty Count;"
        "Write-Output ('enabled=' + $(if($v){if($v.fDenyTSConnections -eq 0){'1'}else{'0'}}else{'?'}));"
        "Write-Output ('nla=' + $(if($n){$n.UserAuthentication}else{'?'}));"
        "Write-Output ('port=' + $(if($p){$p.PortNumber}else{'?'}));"
        "Write-Output ('fw_rules=' + $fw);"
        "\""
    )


def parse_rdp(text: str) -> dict:
    out = {"enabled": False, "nla": None, "port": 3389, "fw_rules": 0,
           "raw": text or ""}
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("enabled="):
            out["enabled"] = s.split("=", 1)[1].strip() == "1"
        elif s.startswith("nla="):
            v = s.split("=", 1)[1].strip()
            out["nla"] = None if v in ("?", "") else v == "1"
        elif s.startswith("port="):
            try:
                out["port"] = int(s.split("=", 1)[1].strip())
            except ValueError:
                pass
        elif s.startswith("fw_rules="):
            try:
                out["fw_rules"] = int(s.split("=", 1)[1].strip())
            except ValueError:
                pass
    return out


def rdp_plan(enable: bool | None = None, nla: bool | None = None,
             port: int | None = None) -> list[str]:
    """生成 RDP 设置命令。只给要改的项，其余不动。"""
    ps = []
    if enable is not None:
        ps.append(
            "$t='HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server';"
            f"Set-ItemProperty -Path $t -Name fDenyTSConnections "
            f"-Value {0 if enable else 1} -Type DWord")
        if enable:
            # 只开服务不开防火墙 = 外面还是连不上（最常见的「开了但连不了」）
            ps.append("Enable-NetFirewallRule -Group '@FirewallAPI.dll,-28752'"
                      " -ErrorAction SilentlyContinue")
        else:
            ps.append("Disable-NetFirewallRule -Group '@FirewallAPI.dll,-28752'"
                      " -ErrorAction SilentlyContinue")
    if nla is not None:
        ps.append(
            "$w='HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Terminal Server\\WinStations\\RDP-Tcp';"
            f"Set-ItemProperty -Path $w -Name UserAuthentication "
            f"-Value {1 if nla else 0} -Type DWord")
    if port is not None:
        try:
            p = int(port)
        except (TypeError, ValueError):
            raise RemoteError(f"端口必须是数字：{port}")
        if not (1 <= p <= 65535):
            raise RemoteError("端口要在 1-65535 之间")
        ps.append(
            "$w='HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Terminal Server\\WinStations\\RDP-Tcp';"
            f"Set-ItemProperty -Path $w -Name PortNumber -Value {p} -Type DWord")
        ps.append("New-NetFirewallRule -DisplayName 'RDP (工具箱)' "
                  f"-Direction Inbound -Protocol TCP -LocalPort {p} "
                  "-Action Allow -ErrorAction SilentlyContinue")
    if not ps:
        return []
    # 改完要重启 TermService 才生效（端口/NLA 都是）
    ps.append("Restart-Service TermService -Force")
    return ["powershell -NoProfile -Command \"" + ";".join(ps) + "\""]


# ==================================================================== XRDP（Linux）
XRDP_KEYS = ("port", "max_bpp", "security_layer", "crypt_level", "use_vsock",
             "hidelogwindow", "allow_channels")


def parse_ini(text: str) -> dict:
    """读简单的 ini（xrdp.ini / sesman.ini 都是这种 [段] key=value）。"""
    out: dict[str, str] = {}
    sec = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            sec = line[1:-1].strip().lower()
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out.setdefault(f"{sec}.{k.strip().lower()}", v.strip())
    return out


def xrdp_summary(cfg: dict) -> dict:
    return {
        "port": cfg.get("globals.port", "3389"),
        "max_bpp": cfg.get("globals.max_bpp", "32"),
        "security_layer": cfg.get("globals.security_layer", "negotiate"),
        "crypt_level": cfg.get("globals.crypt_level", "high"),
        "installed": bool(cfg),
    }


def xrdp_plan(conf_path: str, settings: dict, init: str = "",
              unit: str = "xrdp") -> list[str]:
    """改 xrdp.ini 的 [Globals] 段：备份 → 逐项替换 → 重启。

    只用 awk 替换**已存在**的键，不做追加：xrdp.ini 里 `[Xorg]`/`[Xvnc]`
    段也有 `port=`，追加到文件末尾会被 xrdp 当成段外垃圾忽略，不如直接
    报错让用户知道「这一项你的 xrdp 里没有」。
    """
    if not settings:
        return []
    stamp = _stamp()
    bak = f"{conf_path}.bak.{stamp}"
    cmds = [f"cp -p '{conf_path}' '{bak}'"]
    for key, val in settings.items():
        v = str(val)
        if _BAD_VALUE.search(v):
            raise RemoteError(f"{key} 的值里有不能用的字符：{v}")
        cmds.append(
            f"awk -v k='{key}' -v v='{v}' '"
            "/^\\[/ { sec=tolower($0) } "
            'sec=="[globals]" && index(tolower($0), k "=")==1 '
            '{ print k "=" v; f=1; next } '
            "{ print } END { if (!f) exit 9 }' "
            f"'{conf_path}' > '{conf_path}.new' && mv '{conf_path}.new' '{conf_path}'")
    if init == "syno":
        cmds.append("/usr/syno/bin/synopkg restart xrdp 2>/dev/null || "
                    "systemctl restart xrdp")
    elif init == "sysv":
        cmds.append(f"service {unit} restart")
    else:
        cmds.append(f"systemctl restart {unit}")
    return cmds


# ==================================================================== 服务探测
def probe_remote_cmd() -> str:
    """一次问清楚「这台机器有哪些远程管理入口」。"""
    return (
        "ex(){ for f in $1; do [ -x \"$f\" ] && return 0; done; return 1; }; "
        "echo '@os '$(uname -s 2>/dev/null); "
        "if [ -d /run/systemd/system ]; then echo '@init systemd'; "
        "elif [ -x /usr/syno/bin/synopkg ]; then echo '@init syno'; "
        "elif [ -f /etc/init.d/rc.common ] || [ -d /etc/rc.d ]; then echo '@init openwrt'; "
        "elif [ -d /etc/init.d ]; then echo '@init sysv'; else echo '@init unknown'; fi; "
        "echo '@sshd '$(ex '/usr/sbin/sshd /usr/local/sbin/sshd' && echo 1 || echo 0); "
        "echo '@xrdp '$(ex '/usr/sbin/xrdp /usr/local/sbin/xrdp' && echo 1 || echo 0); "
        "echo '@vnc '$(ex '/usr/bin/x11vnc /usr/bin/vncserver /usr/bin/tigervncserver' "
        "&& echo 1 || echo 0); "
        "echo '@ports '$(ss -lnt 2>/dev/null | awk '/:22 |:3389 |:5900 |:5901 /{print $4}' "
        "| tr '\\n' ' ')"
    )


def parse_remote_probe(text: str) -> dict:
    out = {"os": "", "init": "", "sshd": False, "xrdp": False, "vnc": False,
           "ports": []}
    for line in (text or "").splitlines():
        s = line.strip()
        if not s.startswith("@"):
            continue
        tag, _, rest = s.partition(" ")
        rest = rest.strip()
        if tag == "@os":
            out["os"] = rest
        elif tag == "@init":
            out["init"] = rest
        elif tag == "@sshd":
            out["sshd"] = rest == "1"
        elif tag == "@xrdp":
            out["xrdp"] = rest == "1"
        elif tag == "@vnc":
            out["vnc"] = rest == "1"
        elif tag == "@ports":
            out["ports"] = [p for p in rest.split() if p]
    return out


__all__ = ["RemoteError", "SSHD_FIELDS", "KEY_ONLY_SET", "parse_sshd",
           "sshd_effective", "key_only", "sshd_plan", "sshd_read_cmd",
           "sshd_unit_cmd", "sshd_port_cmd", "gen_keypair", "parse_pubkey",
           "pub_to_line", "fingerprint_openssh", "authorized_keys_append_cmd",
           "rdp_read_cmd", "parse_rdp", "rdp_plan", "parse_ini",
           "xrdp_summary", "xrdp_plan", "probe_remote_cmd",
           "parse_remote_probe"]
