# -*- coding: utf-8 -*-
"""
连接档案 + SSH 密钥发现 —— 参考开源 SSH 客户端（OpenSSH / PuTTY / WinSCP）的常见做法。

  · ProfileStore      连接档案的读写（只存地址/端口/用户/密钥路径，绝不存口令与密码）
  · discover_keys()   扫描 ~/.ssh 里像私钥的文件
  · parse_ssh_config() 解析 ~/.ssh/config 的 Host 块，可一键导入
  · inspect_key()     读取密钥类型、位数、SHA256 指纹（用于人工核对，类似 ssh-keygen -lf）
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

import crypto_store

KEY_NAMES = ("id_ed25519", "id_ecdsa", "id_rsa", "id_dsa")

# 只有这些字段会落盘。
# password / key_passphrase / sudo_password 三个是「记住密码」时才写，
# 且整个档案文件本身是密文（见 crypto_store），不是明文 json。
PROFILE_FIELDS = (
    "name", "host", "port", "user", "auth", "key_path", "note",
    "remember", "password", "key_passphrase", "sudo_password",
    # ---- 主机资源（v1.0.4 起）：分组、标签、协议、引用关系
    # 这几个字段只描述「这台机器是什么」，不含任何凭据；
    # cred_id / proxy_id 指向别的库，本文件里永远只有 id，没有口令。
    "proto", "group", "tags", "cred_id", "proxy_id",
    # ---- 本机协议专用（v1.0.7）：Linux 是 sudo，Windows 是 UAC + 外壳
    "sudo", "elevate", "shell",
    # ---- 目标系统（v1.0.9）：ostype 决定提权问 sudo 还是 enable；
    # os_name 是连上之后 detect() 探到的具体版本名（Debian 12 / Windows 11），
    # 只用来显示，不含任何凭据。
    "ostype", "os_name",
    "created", "last_used",
)


def norm_path(p: str | os.PathLike | None) -> str:
    """把路径统一成当前系统的写法，并且把 `~` 展开。

    为什么需要它：`os.path.expanduser("~/.ssh/id_ed25519")` 在 Windows 上
    只替换开头的 `~`，后面的 `/` 原样保留，得到
    `C:\\Users\\Thomas/.ssh/id_ed25519` 这种半反半斜的串。
    文件系统认，ssh 也认，但显示出来就是错的、复制粘贴也容易出错。
    过一遍 pathlib 就能按平台归一：Windows 全反斜杠，POSIX 全正斜杠。
    """
    if p is None:
        return ""
    s = str(p).strip().strip('"').strip("'")
    if not s:
        return ""
    try:
        s = os.path.expanduser(s)
    except (KeyError, RuntimeError):
        pass
    # Windows 上把 `/etc/ssh/key` 这类 POSIX 绝对路径过一遍 pathlib 会变成
    # `\etc\ssh\key`（挂到当前盘符根），等于把用户写的路径悄悄改坏 —— 原样留着。
    if os.name == "nt" and s.startswith("/") and not s.startswith("//"):
        return s
    try:
        out = str(Path(s))
    except (OSError, ValueError):
        return s
    # Path("") == "." 这种退化情况不要覆盖原值
    return out if out else s


def ssh_dir() -> Path:
    return Path(norm_path("~/.ssh"))


# ------------------------------------------------------------------ 密钥发现
def discover_keys(extra_dirs: list[str] | None = None) -> list[dict]:
    """返回看起来可以当私钥用的文件。

    判据：文件名以 id_ 开头、或以 .pem/.key 结尾，或文件头含 PRIVATE KEY。
    """
    roots = [ssh_dir()]
    for d in extra_dirs or []:
        p = Path(d)
        if p.is_dir():
            roots.append(p)
    found: list[dict] = []
    seen: set[str] = set()
    for root in roots:
        try:
            entries = sorted(root.iterdir(), key=lambda x: x.name.lower())
        except (OSError, PermissionError):
            continue
        for p in entries:
            if not p.is_file():
                continue
            name = p.name
            if name.endswith(".pub") or name in ("known_hosts", "config", "authorized_keys"):
                continue
            likely = name.startswith("id_") or name.endswith((".pem", ".key", ".ppk"))
            if not likely:
                # 再看文件头
                try:
                    head = p.open("rb").read(120)
                except OSError:
                    continue
                if b"PRIVATE KEY" not in head and b"PuTTY-User-Key-File" not in head:
                    continue
            key = norm_path(str(p.resolve()))
            if key in seen:
                continue
            seen.add(key)
            found.append({"path": key, "name": name, "dir": str(root)})
    # id_* 排前面
    found.sort(key=lambda k: (not k["name"].startswith("id_"), k["name"].lower()))
    return found


def inspect_key(path: str, passphrase: str | None = None) -> dict:
    """读取密钥元信息。返回 {ok, type, bits, fingerprint, encrypted, error, format}"""
    if not path:
        return {"ok": False, "error": "未指定私钥文件"}
    # 归一：用户可能手打 `~/.ssh/id_ed25519`，或粘贴正斜杠的路径
    path = norm_path(path)
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "文件不存在"}
    if not p.is_file():
        return {"ok": False, "error": "不是普通文件"}
    try:
        head = p.open("rb").read(200)
    except OSError as e:
        return {"ok": False, "error": f"无法读取：{e}"}

    if b"PuTTY-User-Key-File" in head:
        return {"ok": False, "format": "ppk",
                "error": "这是 PuTTY 的 .ppk 格式，paramiko 无法直接读取。"}
    if b"ENCRYPTED PRIVATE KEY" in head or b"Proc-Type: 4,ENCRYPTED" in head:
        encrypted = True
    else:
        encrypted = False
    if b"PRIVATE KEY" not in head and b"BEGIN OPENSSH PRIVATE KEY" not in head:
        return {"ok": False, "error": "文件头不像私钥（可能是公钥或证书）"}

    import paramiko
    from paramiko.ssh_exception import PasswordRequiredException

    # 统一走 core.session.load_private_key：它会把口令转 bytes。
    # 之前这里直接 `password=passphrase`（str），cryptography 一律抛
    # `TypeError: password must be bytes`，界面上显示成「无法解析：TypeError」，
    # 用户只会以为私钥坏了。
    try:
        from core.session import load_private_key
    except Exception:  # noqa: BLE001
        load_private_key = None

    if load_private_key is not None:
        try:
            k = load_private_key(str(p), passphrase or None)
            fp = str(k.fingerprint)
            if not fp.startswith("SHA256:"):
                fp = "SHA256:" + fp
            return {
                "ok": True,
                "type": k.get_name(),
                "bits": k.get_bits(),
                "fingerprint": fp,
                "encrypted": encrypted,
                # 传统 PEM 与 OpenSSH 两种容器都能读，格式名只做展示
                "format": "openssh" if b"OPENSSH PRIVATE KEY" in head else "pem",
            }
        except Exception as e:  # noqa: BLE001
            from core.session import SessionError
            if isinstance(e, SessionError):
                return {"ok": False, "encrypted": encrypted, "error": str(e)}
            # 其它异常交给下面的兜底循环再试一遍

    classes = []
    for attr in ("Ed25519Key", "ECDSAKey", "RSAKey", "DSSKey"):
        cls = getattr(paramiko, attr, None)
        if cls is not None:
            classes.append(cls)
    last = None
    pw = passphrase.encode("utf-8") if passphrase else None   # 见上：必须是 bytes
    for cls in classes:
        try:
            k = cls.from_private_key_file(str(p), password=pw)
            # paramiko 的 PKey.fingerprint 本身已经带 `SHA256:` 前缀，
            # 再拼一次就成了 `SHA256:SHA256:xxx` —— 这里去掉重复的前缀。
            fp = str(k.fingerprint)
            if not fp.startswith("SHA256:"):
                fp = "SHA256:" + fp
            return {
                "ok": True,
                "type": k.get_name(),
                "bits": k.get_bits(),
                "fingerprint": fp,
                "encrypted": encrypted,
                "format": "openssh",
            }
        except PasswordRequiredException:
            return {"ok": False, "encrypted": True,
                    "error": "私钥带口令，请在「私钥口令」里填上再试"}
        except Exception as e:  # noqa: BLE001
            last = e
    return {"ok": False, "error": f"无法解析：{type(last).__name__}: {last}"}


def passphrase_owner(store, key_path: str, passphrase: str | None = None) -> str | None:
    """同一把私钥在**别的档案**里能解开 → 返回那条档案的名字，否则 None。

    为什么需要它：私钥口令是按「档案」存的，同一把私钥可以配两个不同的口令
    而没有任何人拦。实测过一次：两条档案指向同一个加密 PEM，一条 16 位能解，
    另一条 12 位是**账户登录密码**（和该档案的 password 完全相等）。连接时
    只能报「密码不对」，但人真正需要知道的是「该抄哪一条」。

    只读、只在内存里试解密，不落盘、不打印口令。
    """
    want = norm_path(key_path)
    if not want or store is None:
        return None
    try:
        recs = store.profiles()
    except Exception:  # noqa: BLE001
        return None
    for rec in recs or []:
        if norm_path(str(rec.get("key_path") or "")) != want:
            continue
        pw = str(rec.get("key_passphrase") or "")
        if not pw or pw == str(passphrase or ""):
            continue
        try:
            if inspect_key(want, pw).get("ok"):
                return str(rec.get("name") or "")
        except Exception:  # noqa: BLE001
            continue
    return None


def chmod_600_warning(path: str) -> str | None:
    """OpenSSH 会拒绝过宽权限的私钥，这里只做提示。"""
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return None
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return "该私钥对其他用户可读，建议 chmod 600"
    return None


# ------------------------------------------------------------------ ssh config
_SSH_KEYS = {
    "hostname": "host",
    "port": "port",
    "user": "user",
    "identityfile": "key_path",
    "proxyjump": "proxy",
}


def parse_ssh_config(path: str | None = None) -> list[dict]:
    """解析 ~/.ssh/config，返回可导入的档案列表（跳过含 * 或 ? 的通配 Host）。"""
    cfg = Path(path) if path else (ssh_dir() / "config")
    if not cfg.is_file():
        return []
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    out: list[dict] = []
    cur: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 支持 "Key=Value" 与 "Key Value"
        m = re.match(r"^(\S+?)\s*(?:=|\s)\s*(.+)$", line)
        if not m:
            continue
        k, v = m.group(1).lower(), m.group(2).strip().strip('"')
        if k == "host":
            if cur and not any(ch in cur["_pattern"] for ch in "*?"):
                out.append(cur)
            cur = {"_pattern": v, "name": v.split()[0], "host": "",
                   "port": "", "user": "", "key_path": ""}
            continue
        if cur is None:
            continue
        field = _SSH_KEYS.get(k)
        if not field:
            continue
        if field == "key_path":
            v = norm_path(v)
        if not cur.get(field):
            cur[field] = v
    if cur and not any(ch in cur["_pattern"] for ch in "*?"):
        out.append(cur)

    res = []
    for c in out:
        c.pop("_pattern", None)
        c["host"] = c.get("host") or c.get("name") or ""
        c["auth"] = "key" if c.get("key_path") else "password"
        c["note"] = "来自 ~/.ssh/config"
        if c["host"]:
            res.append(c)
    return res


# ------------------------------------------------------------------ 档案存储
class ProfileStore:
    """连接档案。整个文件加密，明文里看不到任何地址、用户名与口令。

    `remember=True` 的档案会把登录密码 / 私钥口令 / sudo 密码一起写进去，
    但它们只存在于密文文件中，落盘后不可读（便携密钥或 DPAPI）。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.encrypted = False
        self.data: dict = {"version": 2, "last": "", "profiles": []}
        self.load()

    # -- io
    def load(self) -> None:
        self.data = {"version": 2, "last": "", "profiles": []}
        self.encrypted = False

        legacy = self.path.with_suffix(".json")
        source = None
        if self.path.is_file():
            source = self.path
        elif legacy.is_file():
            source = legacy          # 旧版明文档案，读进来后立刻升级成密文
        if source is None:
            return
        try:
            raw = source.read_bytes()
        except OSError:
            return

        plain = crypto_store.open_bytes(raw)
        if plain is None:
            plain = raw              # 不是密文，按旧版明文 json 处理
        else:
            self.encrypted = True

        try:
            obj = json.loads(plain.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(obj, dict) or not isinstance(obj.get("profiles"), list):
            return

        self.data = {
            "version": 2,
            "last": obj.get("last", "") or "",
            "profiles": [self._clean(p) for p in obj["profiles"]
                         if isinstance(p, dict)],
        }
        if not self.encrypted:
            self.save()              # 明文 → 密文，静默迁移
            if source == legacy:
                try:
                    legacy.unlink()
                except OSError:
                    pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        plain = json.dumps(self.data, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            blob = crypto_store.seal_bytes(plain)
            self.encrypted = True
        except OSError:
            blob = plain             # 加密不可用时退回明文，至少程序还能用
            self.encrypted = False
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, self.path)

    @staticmethod
    def _clean(p: dict) -> dict:
        out: dict = {}
        for k in PROFILE_FIELDS:
            v = p.get(k)
            out[k] = (bool(v) if k in ("remember", "sudo", "elevate")
                      else ("" if v is None else str(v)))
        out.setdefault("name", "")
        out.setdefault("auth", "key")
        out["port"] = out.get("port") or "22"
        # 协议留空时按 22/23 端口猜，别让老档案全变成「未标协议」
        if not out.get("proto"):
            out["proto"] = "telnet" if out["port"] == "23" else "ssh"
        if not out.get("group"):
            out["group"] = ""
        if not out.get("tags"):
            out["tags"] = ""
        # 旧档案里可能存着 `C:\Users\x/.ssh/id_ed25519` 这种半斜杠路径，
        # 读进来就顺手归一，免得界面上一直显示成错的。
        out["key_path"] = norm_path(out.get("key_path"))
        if not out.get("remember"):
            # 没勾「记住密码」就一个口令字段都不留
            for k in ("password", "key_passphrase", "sudo_password"):
                out[k] = ""
        return out

    # -- 查询
    @property
    def profiles(self) -> list[dict]:
        return self.data["profiles"]

    def names(self) -> list[str]:
        return [p["name"] for p in self.profiles]

    def get(self, name: str) -> dict | None:
        for p in self.profiles:
            if p["name"] == name:
                return p
        return None

    @property
    def last(self) -> str:
        return self.data.get("last", "")

    def set_last(self, name: str) -> None:
        self.data["last"] = name or ""
        self.save()

    # -- 写入
    def upsert(self, profile: dict) -> dict:
        p = self._clean(profile)
        if not p["name"]:
            raise ValueError("档案名不能为空")
        for i, old in enumerate(self.profiles):
            if old["name"] == p["name"]:
                self.profiles[i] = p
                break
        else:
            self.profiles.append(p)
        self.profiles.sort(key=lambda x: x["name"].lower())
        self.save()
        return p

    def remove(self, name: str) -> bool:
        n0 = len(self.profiles)
        self.data["profiles"] = [p for p in self.profiles if p["name"] != name]
        if self.data.get("last") == name:
            self.data["last"] = ""
        changed = len(self.data["profiles"]) != n0
        if changed:
            self.save()
        return changed

    def import_from_ssh_config(self) -> tuple[int, int]:
        """返回 (新增数, 已存在跳过数)。同名不覆盖。"""
        added = skipped = 0
        for c in parse_ssh_config():
            base = c["name"] or c["host"]
            name = base
            i = 2
            while self.get(name):
                if self.get(name) and self.get(name)["host"] == c["host"]:
                    skipped += 1
                    name = None
                    break
                name = f"{base}-{i}"
                i += 1
            if name is None:
                continue
            c["name"] = name
            self.profiles.append(self._clean(c))
            added += 1
        if added:
            self.profiles.sort(key=lambda x: x["name"].lower())
            self.save()
        return added, skipped
