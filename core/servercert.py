# -*- coding: utf-8 -*-
"""服务器证书：自签名生成、导入、查看、导出。

和 `pkilib.PKI`（给 OpenVPN 客户端签证书那套）分开是故意的：
那边是「一个 CA 签一堆客户端证书」，这边是「给某个 Web 服务一张能用的
证书」——不需要 CA，也不需要 CRL，就要一张自签名 + 私钥，能塞进群晖的
「控制面板 → 安全性 → 证书」或者 nginx 的 ssl_certificate。

两个必须做对的地方：
  · **SAN 一定要写**。Chrome / Edge / iOS 从 2017 年起不再认 CN，只认
    subjectAltName；只填 CN 的证书在浏览器里永远是「不安全」。
  · 私钥落盘权限 0600（Windows 上靠目录 ACL），并且默认**只有导出时**才
    离开本机 —— 生成在本地、上传由用户点。
"""
from __future__ import annotations

import datetime
import hashlib
import ipaddress
import json
import os
import re
import shutil
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


class CertError(Exception):
    pass


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def split_sans(text: str) -> list[str]:
    """「a.com, b.com / 192.168.1.5」→ 列表，去重保序。"""
    raw = [x for x in re.split(r"[,，;；\s]+", str(text or "")) if x.strip()]
    out, seen = [], set()
    for x in raw:
        k = x.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(x.strip())
    return out


def _san_ext(names: list[str]) -> x509.SubjectAlternativeName:
    items = []
    for n in names:
        try:
            items.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            items.append(x509.DNSName(n))
    if not items:
        raise CertError("至少要给一个域名或 IP（证书要写在 SAN 里，只写 CN 浏览器不认）")
    return x509.SubjectAlternativeName(items)


def make_selfsigned(cn: str, sans: list[str], days: int = 825,
                    bits: int = 2048, kind: str = "rsa") -> tuple[str, str]:
    """生成自签名证书 + 私钥，返回 (cert_pem, key_pem)。"""
    cn = str(cn or "").strip()
    sans = split_sans(", ".join(sans) if isinstance(sans, (list, tuple)) else sans)
    if not cn:
        raise CertError("证书名称（CN）不能为空")
    if not sans:
        sans = [cn]
    if cn.lower() not in {s.lower() for s in sans}:
        # CN 不在 SAN 里的话，多数客户端仍然按 SAN 校验 → 补上，免得
        # 用户填了 CN=nas.local、SAN 只写 192.168.1.5，结果用域名访问报错。
        sans = [cn] + sans
    try:
        if kind == "ec":
            key = ec.generate_private_key(ec.SECP256R1())
        else:
            if int(bits) not in (2048, 3072, 4096):
                bits = 2048
            key = rsa.generate_private_key(public_exponent=65537,
                                           key_size=int(bits))
    except Exception as e:  # noqa: BLE001
        raise CertError(f"生成私钥失败：{e}")

    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Terminal Toolbox"),
    ])
    now = _now()
    try:
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)                     # 自签名 = 签发者是自己
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=int(days)))
            .add_extension(_san_ext(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                           critical=True)
            .add_extension(x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(
                digital_signature=True, key_encipherment=(kind != "ec"),
                content_commitment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False), critical=True)
            .sign(key, hashes.SHA256())
        )
    except Exception as e:  # noqa: BLE001
        raise CertError(f"签名失败：{e}")

    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode("ascii")
    return cert_pem, key_pem


def parse_pem(text: str) -> dict:
    """读一张 PEM 证书 → 摘要信息（界面表格用）。"""
    body = str(text or "")
    begin = body.find("-----BEGIN CERTIFICATE-----")
    if begin < 0:
        raise CertError("这不是 PEM 证书（找不到 -----BEGIN CERTIFICATE-----）")
    end = body.find("-----END CERTIFICATE-----", begin)
    chunk = body[begin:end + len("-----END CERTIFICATE-----")] if end > 0 else body
    try:
        cert = x509.load_pem_x509_certificate(chunk.encode("ascii", "ignore"))
    except Exception as e:  # noqa: BLE001
        raise CertError(f"解析证书失败：{e}")
    try:
        not_after = cert.not_valid_after_utc
        not_before = cert.not_valid_before_utc
    except AttributeError:                       # cryptography < 42
        not_after = cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
        not_before = cert.not_valid_before.replace(tzinfo=datetime.timezone.utc)
    try:
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    except (IndexError, ValueError):
        cn = ""
    sans: list[str] = []
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        sans = [str(v) for v in ext.value.get_values_for_type(x509.DNSName)]
        sans += [str(v) for v in ext.value.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        pass
    left = (not_after - _now()).days
    return {
        "cn": cn,
        "issuer": cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        if cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME) else "",
        "self_signed": cert.issuer == cert.subject,
        "sans": sans,
        "not_before": not_before.strftime("%Y-%m-%d"),
        "not_after": not_after.strftime("%Y-%m-%d"),
        "days_left": left,
        "serial": format(cert.serial_number, "x"),
        "sha256": hashlib.sha256(
            cert.public_bytes(serialization.Encoding.DER)).hexdigest(),
        "expired": left < 0,
    }


def _safe_name(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+", "_", str(name or "")).strip("._")
    return s or "cert"


class Store:
    """本机证书库：`<base>/<名字>/cert.pem + key.pem + meta.json`。"""

    def __init__(self, base: str | Path):
        self.base = Path(base)

    def _dir(self, name: str) -> Path:
        return self.base / _safe_name(name)

    def ensure(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict]:
        if not self.base.is_dir():
            return []
        rows = []
        for d in sorted(self.base.iterdir()):
            if not d.is_dir():
                continue
            cf = d / "cert.pem"
            if not cf.is_file():
                continue
            row = {"name": d.name, "dir": str(d)}
            try:
                row.update(parse_pem(cf.read_text(encoding="utf-8")))
            except CertError as e:
                row.update({"cn": "", "error": str(e), "days_left": 0,
                            "not_after": "", "sans": []})
            meta = d / "meta.json"
            if meta.is_file():
                try:
                    row.update({k: v for k, v in
                                json.loads(meta.read_text(encoding="utf-8")).items()
                                if k not in row})
                except (OSError, ValueError):
                    pass
            row["has_key"] = (d / "key.pem").is_file()
            rows.append(row)
        return rows

    def add(self, name: str, cert_pem: str, key_pem: str = "",
            meta: dict | None = None, overwrite: bool = False) -> dict:
        info = parse_pem(cert_pem)
        d = self._dir(name)
        if d.exists() and not overwrite:
            raise CertError(f"「{d.name}」已经存在，换个名字或先删掉")
        d.mkdir(parents=True, exist_ok=True)
        (d / "cert.pem").write_text(cert_pem, encoding="utf-8")
        if key_pem:
            kf = d / "key.pem"
            kf.write_text(key_pem, encoding="utf-8")
            try:
                os.chmod(kf, 0o600)
            except OSError:
                pass
        # 目录名是洗过的（去掉路径分隔符等），但界面上要显示用户原本起的
        # 名字 —— 否则「群晖 DSM」会变成「群晖_DSM」，用户以为改名了。
        payload = {"name": d.name, "label": str(name), "cn": info["cn"],
                   "sans": info["sans"],
                   "not_after": info["not_after"],
                   "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
        payload.update(meta or {})
        (d / "meta.json").write_text(json.dumps(payload, ensure_ascii=False,
                                                indent=2), encoding="utf-8")
        return {**info, "name": d.name, "has_key": bool(key_pem)}

    def read(self, name: str) -> tuple[str, str]:
        d = self._dir(name)
        cert = (d / "cert.pem").read_text(encoding="utf-8")
        key = ""
        if (d / "key.pem").is_file():
            key = (d / "key.pem").read_text(encoding="utf-8")
        return cert, key

    def remove(self, name: str) -> bool:
        d = self._dir(name)
        if not d.is_dir():
            return False
        shutil.rmtree(d, ignore_errors=True)
        return True

    def export(self, name: str, directory: str | Path) -> list[str]:
        """把 cert.pem / key.pem 复制到用户选的目录，返回写出的路径。"""
        cert, key = self.read(name)
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        stem = _safe_name(name)
        made = []
        cp = out / f"{stem}.crt"
        cp.write_text(cert, encoding="utf-8")
        made.append(str(cp))
        if key:
            kp = out / f"{stem}.key"
            kp.write_text(key, encoding="utf-8")
            try:
                os.chmod(kp, 0o600)
            except OSError:
                pass
            made.append(str(kp))
        return made


def deploy_hint(target: str = "") -> str:
    """不同用途该把证书放到哪 —— 页面直接显示，省得用户去搜。"""
    t = (target or "").lower()
    if "synology" in t or "群晖" in t or "dsm" in t:
        return ("群晖：控制面板 → 安全性 → 证书 → 新增 → 导入证书，"
                "或放到 /usr/syno/etc/certificate/system/default/"
                "（cert.pem / privkey.pem），再在「设置」里指定给对应服务")
    if "nginx" in t:
        return ("nginx：ssl_certificate /path/fullchain.pem; "
                "ssl_certificate_key /path/privkey.pem; 然后 nginx -t && "
                "systemctl reload nginx")
    if "iis" in t:
        return "IIS：导入到「服务器证书」后，在站点绑定的 HTTPS 上选择它"
    return ("通用：证书文件给服务当 ssl_certificate、私钥当 ssl_certificate_key；"
            "自签名证书首次访问会提示不受信任，可把 cert.pem 导入客户端"
            "「受信任的根证书颁发机构」")


__all__ = ["CertError", "make_selfsigned", "parse_pem", "split_sans", "Store",
           "deploy_hint"]
