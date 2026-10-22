# -*- coding: utf-8 -*-
"""
本地 PKI 引擎 —— 与 easy-rsa 能力对齐，但用 cryptography 直接实现，
不依赖 NAS 上的 bash / openssl 版本。

职责：
  * init_ca        建私有 CA
  * issue          签发服务端 / 设备证书
  * revoke         吊销 + 重建 CRL
  * build_ovpn     把 ca / cert / key / tls-auth 内嵌进单文件配置
"""
from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

UTC = datetime.timezone.utc
ONE_DAY = datetime.timedelta(days=1)


def _now() -> datetime.datetime:
    return datetime.datetime.now(UTC)


def _pem_cert(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _pem_key(key, passphrase: str | None = None) -> str:
    if passphrase:
        enc = serialization.BestAvailableEncryption(passphrase.encode("utf-8"))
    else:
        enc = serialization.NoEncryption()
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=enc,
    ).decode("ascii")


def _dt(s: str | None) -> datetime.datetime | None:
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        return None


class PKIError(Exception):
    pass


class PKI:
    """所有材料都落在 base 目录下，CA 私钥不出本机。"""

    def __init__(self, base: str | Path):
        self.base = Path(base)
        self.ca_dir = self.base / "ca"
        self.dev_dir = self.base / "devices"
        self.out_dir = self.base / "output"
        self.db_path = self.base / "db.json"
        for d in (self.ca_dir, self.dev_dir, self.out_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ---------- db ----------
    def _load(self) -> dict:
        if self.db_path.exists():
            try:
                return json.loads(self.db_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"ca": None, "certs": [], "crl_number": 0}

    def _save(self, db: dict) -> None:
        self.db_path.write_text(
            json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------- 读取 CA ----------
    def has_ca(self) -> bool:
        return (self.ca_dir / "ca.crt").exists() and (self.ca_dir / "ca.key").exists()

    def ca_cert(self) -> x509.Certificate:
        if not self.has_ca():
            raise PKIError("尚未初始化 CA")
        return x509.load_pem_x509_certificate((self.ca_dir / "ca.crt").read_bytes())

    def ca_key(self, passphrase: str | None = None):
        data = (self.ca_dir / "ca.key").read_bytes()
        if passphrase:
            return serialization.load_pem_private_key(data, passphrase.encode("utf-8"))
        try:
            return serialization.load_pem_private_key(data, None)
        except TypeError:
            raise PKIError("CA 私钥已加密，需要提供 CA 口令")

    # ---------- 建 CA ----------
    def init_ca(
        self,
        cn: str,
        days: int = 3650,
        key_size: int = 3072,
        passphrase: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        if self.has_ca() and not overwrite:
            raise PKIError("CA 已存在。确认要重建请勾选覆盖（旧证书将全部失效）")
        key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
        subject = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, cn),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Client Cert Console"),
            ]
        )
        now = _now()
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - ONE_DAY)
            .not_valid_after(now + days * ONE_DAY)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256())
        )
        (self.ca_dir / "ca.crt").write_text(_pem_cert(cert), encoding="ascii")
        (self.ca_dir / "ca.key").write_text(_pem_key(key, passphrase), encoding="ascii")
        self._write_crl([], 1)

        db = self._load()
        db["ca"] = {
            "cn": cn,
            "created": now.isoformat(),
            "days": days,
            "key_size": key_size,
            "encrypted": bool(passphrase),
            "fingerprint": cert.fingerprint(hashes.SHA256()).hex().upper(),
            "serial": format(cert.serial_number, "x"),
        }
        db["certs"] = []
        db["crl_number"] = 1
        self._save(db)
        return db["ca"]

    # ---------- 签发 ----------
    def issue(
        self,
        name: str,
        kind: str = "client",
        cn: str | None = None,
        days: int = 825,
        passphrase: str | None = None,
        ca_passphrase: str | None = None,
        san: str | None = None,
    ) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name or ""):
            raise PKIError("设备名只能用字母、数字、点、下划线、短横线（1~64 位）")
        if kind not in ("client", "server"):
            raise PKIError("kind 只能是 client 或 server")

        db = self._load()
        if any(c["name"] == name for c in db["certs"]):
            raise PKIError(f"证书名 {name} 已存在，换一个名字或先删除")

        ca_cert = self.ca_cert()
        ca_key = self.ca_key(ca_passphrase)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn or name)])
        now = _now()

        eku = ExtendedKeyUsageOID.SERVER_AUTH if kind == "server" else ExtendedKeyUsageOID.CLIENT_AUTH
        b = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - ONE_DAY)
            .not_valid_after(now + days * ONE_DAY)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=True,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        )
        if kind == "server":
            dns = san or cn or name
            b = b.add_extension(
                x509.SubjectAlternativeName([x509.DNSName(dns)]), critical=False
            )
        cert = b.sign(ca_key, hashes.SHA256())

        (self.dev_dir / f"{name}.crt").write_text(_pem_cert(cert), encoding="ascii")
        (self.dev_dir / f"{name}.key").write_text(_pem_key(key, passphrase), encoding="ascii")

        entry = {
            "name": name,
            "cn": cn or name,
            "kind": kind,
            "serial": format(cert.serial_number, "x"),
            "issued": now.isoformat(),
            "not_after": cert.not_valid_after_utc.isoformat(),
            "days": days,
            "encrypted": bool(passphrase),
            "revoked": False,
            "revoked_at": None,
        }
        db["certs"].append(entry)
        self._save(db)
        return entry

    # ---------- 吊销 ----------
    def revoke(self, name: str, ca_passphrase: str | None = None) -> dict:
        db = self._load()
        entry = next((c for c in db["certs"] if c["name"] == name), None)
        if not entry:
            raise PKIError(f"找不到证书 {name}")
        if entry["revoked"]:
            raise PKIError(f"{name} 已经吊销过了")
        entry["revoked"] = True
        entry["revoked_at"] = _now().isoformat()
        db["crl_number"] = int(db.get("crl_number", 0)) + 1
        self._save(db)
        self._write_crl(db["certs"], db["crl_number"], ca_passphrase)
        return {"revoked": entry, "crl_number": db["crl_number"]}

    def build_crl(self, ca_passphrase: str | None = None) -> int:
        db = self._load()
        db["crl_number"] = int(db.get("crl_number", 0)) + 1
        self._save(db)
        self._write_crl(db["certs"], db["crl_number"], ca_passphrase)
        return db["crl_number"]

    def _write_crl(self, certs: list, number: int, ca_passphrase: str | None = None) -> None:
        ca_cert = self.ca_cert()
        ca_key = self.ca_key(ca_passphrase)
        now = _now()
        builder = (
            x509.CertificateRevocationListBuilder()
            .issuer_name(ca_cert.subject)
            .last_update(now)
            .next_update(now + 30 * ONE_DAY)
            .add_extension(x509.CRLNumber(number), critical=False)
        )
        for c in certs:
            if not c.get("revoked"):
                continue
            when = _dt(c.get("revoked_at")) or now
            rc = (
                x509.RevokedCertificateBuilder()
                .serial_number(int(c["serial"], 16))
                .revocation_date(when)
                .build()
            )
            builder = builder.add_revoked_certificate(rc)
        crl = builder.sign(ca_key, hashes.SHA256())
        (self.ca_dir / "crl.pem").write_text(
            crl.public_bytes(serialization.Encoding.PEM).decode("ascii"), encoding="ascii"
        )

    # ---------- 查询 ----------
    def state(self) -> dict:
        db = self._load()
        out = []
        now = _now()
        for c in db["certs"]:
            na = _dt(c.get("not_after"))
            item = dict(c)
            item["expired"] = bool(na and na < now)
            item["days_left"] = (na - now).days if na else None
            out.append(item)
        return {
            "ca": db.get("ca"),
            "certs": out,
            "crl_number": db.get("crl_number", 0),
            "has_crl": (self.ca_dir / "crl.pem").exists(),
            "base": str(self.base),
        }

    def read_material(self, name: str) -> dict:
        crt = self.dev_dir / f"{name}.crt"
        key = self.dev_dir / f"{name}.key"
        if not crt.exists() or not key.exists():
            raise PKIError(f"证书文件缺失：{name}")
        return {
            "cert": crt.read_text(encoding="ascii"),
            "key": key.read_text(encoding="ascii"),
        }

    def ca_pem(self) -> str:
        return (self.ca_dir / "ca.crt").read_text(encoding="ascii")

    def crl_pem(self) -> str:
        p = self.ca_dir / "crl.pem"
        return p.read_text(encoding="ascii") if p.exists() else ""

    # ---------- 单文件配置打包 ----------
    def ovpn_text(
        self,
        name: str,
        remote: str,
        port: int = 11094,
        proto: str = "tcp-client",
        cipher: str | None = "AES-256-CBC",
        auth: str = "SHA512",
        comp_lzo: bool = True,
        tls_auth: str = "",
        verify_name: str | None = None,
        extra: str = "",
        platform: str = "generic",
    ) -> str:
        m = self.read_material(name)
        ca = self.ca_pem().strip()
        lines = platform_preamble(platform)
        lines += [
            "",
            f"# 设备：{name}",
            "client",
            "dev tun",
            f"proto {proto}",
            f"remote {remote} {port}",
            "resolv-retry infinite",
            "nobind",
            "persist-key",
            "persist-tun",
            "pull",
            "tls-client",
        ]
        if cipher:
            lines.append(f"cipher {cipher}")
        lines.append(f"auth {auth}")
        if comp_lzo:
            lines.append("comp-lzo")
        lines += ["auth-user-pass", "reneg-sec 0", "verb 3", "remote-cert-tls server"]
        if verify_name:
            lines.append(f"verify-x509-name {verify_name} name")
        if extra.strip():
            lines.append(extra.strip())
        parts = [
            "\n".join(lines), "",
            "<ca>", ca, "</ca>", "",
            "<cert>", m["cert"].strip(), "</cert>", "",
            "<key>", m["key"].strip(), "</key>",
        ]
        if tls_auth.strip():
            parts += ["", "key-direction 1", "<tls-auth>", tls_auth.strip(), "</tls-auth>"]
        return "\n".join(parts) + "\n"

    def build_ovpn(self, name: str, platform: str = "generic", **kw) -> dict:
        text = self.ovpn_text(name, platform=platform, **kw)
        fname = f"{name}.ovpn" if platform == "generic" else f"{name}-{platform}.ovpn"
        (self.out_dir / fname).write_text(text, encoding="utf-8")
        return {"name": name, "platform": platform, "file": fname,
                "bytes": len(text.encode("utf-8"))}

    def export_cert_files(self, name: str) -> list[dict]:
        """把设备证书/私钥复制到输出目录，供单独下载。"""
        m = self.read_material(name)
        out = []
        for base, content in ((f"{name}.crt", m["cert"]), (f"{name}.key", m["key"])):
            (self.out_dir / base).write_text(content, encoding="ascii")
            out.append({"file": base, "bytes": len(content.encode("ascii"))})
        return out

    def export_bundle(self, name: str, platforms: list[str], **kw) -> dict:
        """一个 zip 打包：crt + key + 各平台配置文件 + README。"""
        import zipfile

        platforms = [p for p in platforms if p in PLATFORMS] or list(PLATFORM_ORDER)
        m = self.read_material(name)
        zpath = self.out_dir / f"{name}-bundle.zip"
        readme = [
            "=" * 62,
            f" OpenVPN 客户端包 —— {name}",
            "=" * 62,
            "",
            "本包含：",
            f"  {name}.crt          客户端证书（可单独用于其他客户端）",
            f"  {name}.key          客户端私钥（若已加密，导入时会要求输入口令）",
        ]
        for p in platforms:
            readme.append(f"  {name}-{p}.ovpn    {PLATFORMS[p]['label']}")
        readme += [
            "",
            "所有配置文件都是「单文件内嵌」格式：ca / cert / key / tls-auth 全部写在",
            "文件内部，不依赖任何外部文件，可以直接导入手机或桌面客户端。",
            "",
        ]
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"{name}/{name}.crt", m["cert"])
            z.writestr(f"{name}/{name}.key", m["key"])
            for p in platforms:
                text = self.ovpn_text(name, platform=p, **kw)
                z.writestr(f"{name}/{name}-{p}.ovpn", text)
                readme += ["-" * 62, PLATFORMS[p]["label"], "-" * 62]
                readme += [l.lstrip("# ").rstrip() for l in PLATFORMS[p]["notes"]]
                readme += [""]
            readme += [
                "-" * 62,
                "安全提示",
                "-" * 62,
                "1) 本文件包含私钥，等同于该设备的凭证，勿转发给他人。",
                "2) 设备丢失请在控制台吊销该证书并重新部署 crl.pem。",
                "3) 若私钥设置了口令，首次连接会提示输入。",
            ]
            z.writestr(f"{name}/README.txt", "\n".join(readme) + "\n")
        return {"file": zpath.name, "bytes": zpath.stat().st_size, "platforms": platforms}


# ---------------- 各平台说明（写进配置文件注释） ----------------
PLATFORM_ORDER = ["windows", "android", "ios", "harmony", "linux", "macos"]

PLATFORMS: dict[str, dict] = {
    "windows": {
        "label": "Windows",
        "notes": [
            "# ===== Windows 端 =====",
            "# 推荐客户端：OpenVPN GUI 或 OpenVPN Connect（openvpn.net 下载）",
            "#",
            "# 放置位置（任选其一）：",
            "#   1. OpenVPN GUI 配置目录，导入后出现在托盘菜单里：",
            "#        C:\\Users\\<你的用户名>\\OpenVPN\\config\\",
            "#      也可以右键托盘图标 → Import file 直接选本文件",
            "#   2. OpenVPN Connect：放任意目录，打开程序后 File → Browse 导入",
            "#",
            "# 如果你想改用「外部证书文件」而不是内嵌（一般没必要）：",
            "#   把 cert / key 与本文件放同一目录，写相对路径即可：",
            "#        cert client.crt",
            "#        key  client.key",
            "#   绝对路径必须用双反斜杠，且路径含空格要加引号：",
            '#        cert "C:\\\\OpenVPN\\\\config\\\\client.crt"',
        ],
    },
    "android": {
        "label": "Android",
        "notes": [
            "# ===== Android 端 =====",
            "# 推荐客户端：OpenVPN Connect（应用商店搜索）",
            "#",
            "# 放置位置：",
            "#   把本文件拷到手机的 Download 目录（文件管理 → 内部存储 → Download）",
            "#   OpenVPN Connect → Import Profile → FILE → 选中该文件 → 导入",
            "#",
            "# 说明：Android 客户端不支持引用外部证书路径，本文件已把所有内容内嵌，",
            "#       直接导入即可，不要试图拆分 cert / key。",
        ],
    },
    "ios": {
        "label": "iOS / iPadOS",
        "notes": [
            "# ===== iOS / iPadOS 端 =====",
            "# 推荐客户端：OpenVPN Connect（App Store）",
            "#",
            "# 放置位置（沙盒限制，必须单文件导入）：",
            "#   1. AirDrop / iCloud Drive / 邮件附件把本文件传到设备",
            "#   2. 在「文件」App 里点开本文件 → 分享 → 用 OpenVPN Connect 打开",
            "#   3. 或打开 OpenVPN Connect → Import → File → 从「文件」中选择",
            "#",
            "# 说明：iOS 不允许应用读取任意路径，因此没有「证书路径」这回事；",
            "#       本文件已内嵌 ca / cert / key / tls-auth。",
        ],
    },
    "harmony": {
        "label": "鸿蒙 / 卓易通",
        "notes": [
            "# ===== 鸿蒙 / 卓易通 =====",
            "# 鸿蒙 NEXT 通过「卓易通」运行安卓应用，因此使用安卓版客户端：OpenVPN Connect",
            "#",
            "# 放置位置：",
            "#   手机「文件管理」→ Download 目录放本文件",
            "#   在卓易通里打开 OpenVPN Connect → Import Profile → FILE → 选择该文件",
            "#",
            "# 常见问题：",
            "#   - 卓易通沙盒看不到某些目录 → 优先放 Download",
            "#   - 卓易通内找不到文件 → 先用「文件管理」把配置文件分享/复制到 Download 再试",
            "#   - 鸿蒙原生 OpenVPN 客户端尚不成熟，不建议使用",
        ],
    },
    "linux": {
        "label": "Linux",
        "notes": [
            "# ===== Linux 端 =====",
            "# 客户端：命令行 openvpn，或桌面 NetworkManager（导入配置文件即可）",
            "#",
            "# 放置位置：",
            "#   用户级： ~/.config/openvpn/<文件名>.ovpn",
            "#           启动： sudo openvpn --config ~/.config/openvpn/<文件名>.ovpn --daemon",
            "#   系统级： /etc/openvpn/client/<文件名>.conf",
            "#           启动： sudo systemctl start openvpn-client@<文件名>",
            "#           （systemd 实例名要去掉 .conf，例如 home.conf → openvpn-client@home）",
            "#",
            "# 若改用「外部证书文件」：与本文件同目录放 client.crt / client.key，写相对路径",
            "#        cert client.crt",
            "#        key  client.key",
            "#   绝对路径示例： cert /etc/openvpn/client/client.crt",
            "#",
            "# 注意：需要 root；auth-user-pass 会交互式询问账号密码。无人值守可写：",
            "#        auth-user-pass /etc/openvpn/client/auth.txt   （两行：用户名、密码；chmod 600）",
        ],
    },
    "macos": {
        "label": "macOS",
        "notes": [
            "# ===== macOS 端 =====",
            "# 推荐客户端：Tunnelblick（免费开源）或 OpenVPN Connect",
            "#",
            "# 放置位置：",
            "#   Tunnelblick：直接双击本配置文件即可导入，程序自己管理存放位置",
            "#   手工放置目录：",
            "#     ~/Library/Application Support/Tunnelblick/Configurations/<名字>.tblk/Contents/Resources/",
            "#   OpenVPN Connect：Import → File → 选择本文件",
            "#",
            "# 若改用「外部证书文件」：与本文件同目录放 client.crt / client.key，写相对路径",
            "#        cert client.crt",
            "#        key  client.key",
            "#   绝对路径示例： cert /Users/<你的用户名>/Documents/openvpn/client.crt",
        ],
    },
}

GENERIC_NOTES = [
    "# ===== 通用配置（单文件内嵌） =====",
    "# 本文件已内嵌 ca / cert / key / tls-auth，可直接被任意 OpenVPN 客户端导入。",
    "# 若你的客户端要求外部文件：把 ca.crt / client.crt / client.key 放在本文件同目录，",
    "# 并改用相对路径（cert client.crt / key client.key）；绝对路径需按各系统格式书写。",
]


def platform_preamble(platform: str) -> list[str]:
    meta = PLATFORMS.get(platform)
    if not meta:
        return list(GENERIC_NOTES)
    return list(meta["notes"])

