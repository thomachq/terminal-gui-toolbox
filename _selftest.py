# -*- coding: utf-8 -*-
"""PKI 离线自检：不碰 NAS、不联网、不开窗口，只验证证书与打包链路。

跑法：
    python _selftest.py
"""
from __future__ import annotations

import io
import shutil
import sys
import zipfile
from pathlib import Path

from cryptography import x509

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pkilib import PLATFORMS, PLATFORM_ORDER, PKI, PKIError  # noqa: E402

DATA = HERE / "_test_data"
if DATA.exists():
    shutil.rmtree(DATA)
DATA.mkdir(parents=True)

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def expect_error(name: str, fn) -> None:
    try:
        fn()
    except (PKIError, ValueError) as e:
        print(f"  PASS  {name}   （按预期被拒：{e}）")
        return
    print(f"  FAIL  {name}   （没有被拒绝）")
    fails.append(name)


pki = PKI(DATA)

try:
    print("\n[1] 初始状态")
    st = pki.state()
    check("CA 未初始化", st["ca"] is None)
    check("证书列表为空", len(st["certs"]) == 0)
    check("has_ca() 为假", pki.has_ca() is False)
    check("CRL 版本为 0", st.get("crl_number", 0) == 0, str(st.get("crl_number")))

    print("\n[2] 建 CA")
    ca = pki.init_ca("Test VPN CA", 365, 2048, None, False)
    check("建 CA 成功", ca["cn"] == "Test VPN CA", str(ca))
    ca_crt = x509.load_pem_x509_certificate((DATA / "ca" / "ca.crt").read_bytes())
    bc = ca_crt.extensions.get_extension_for_class(x509.BasicConstraints).value
    check("CA 带 CA:TRUE", bc.ca is True)
    check("CA 有 crl.pem", (DATA / "ca" / "crl.pem").exists())
    check("CA 文件齐全", (DATA / "ca" / "ca.key").exists())
    check("已有 CA 时 has_ca() 为真", pki.has_ca() is True)
    check("CA 指纹可读", bool(ca.get("fingerprint")), str(ca.get("fingerprint")))

    print("\n[3] 签发服务端 + 两台设备")
    srv = pki.issue("srv", "server", cn="vpn.example.com", days=365,
                    san="vpn.example.com")
    check("签服务端证书", srv["kind"] == "server", str(srv))
    dev1 = pki.issue("dev1", "client")
    check("签 dev1", dev1["kind"] == "client" and not dev1.get("encrypted"), str(dev1))
    dev2 = pki.issue("dev2", "client", passphrase="abc123")
    check("签 dev2（加密私钥）", dev2["encrypted"] is True, str(dev2))

    print("\n[4] 证书内容校验")
    d1 = x509.load_pem_x509_certificate((DATA / "devices" / "dev1.crt").read_bytes())
    eku = d1.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    check("dev1 是 clientAuth", x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH in eku)
    check("dev1 由 CA 签发", d1.issuer == ca_crt.subject)
    s = x509.load_pem_x509_certificate((DATA / "devices" / "srv.crt").read_bytes())
    san = s.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    check("srv 含 SAN", "vpn.example.com" in san.get_values_for_type(x509.DNSName))
    sku = s.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    check("srv 是 serverAuth", x509.oid.ExtendedKeyUsageOID.SERVER_AUTH in sku)

    print("\n[5] 多平台打包 .ovpn")
    ta = ("-----BEGIN OpenVPN Static key V1-----\n" + "ab" * 32
          + "\n-----END OpenVPN Static key V1-----")
    params = dict(remote="vpn.example.com", port=11094, proto="tcp-client",
                  auth="SHA512", comp_lzo=True, tls_auth=ta,
                  verify_name="vpn.example.com", extra="")
    made = [pki.build_ovpn(name="dev1", platform=p, **params) for p in PLATFORM_ORDER]
    check("6 个平台各一份", len(made) == 6, str(len(made)))
    names = {m["file"] for m in made}
    check("文件名带平台后缀",
          all(f"dev1-{p}.ovpn" in names for p in PLATFORM_ORDER), str(sorted(names)))
    check("PLATFORMS 覆盖全部平台",
          set(PLATFORM_ORDER) == set(PLATFORMS), str(PLATFORM_ORDER))

    out = pki.out_dir
    text = (out / "dev1-windows.ovpn").read_text(encoding="utf-8")
    for tag in ("<ca>", "<cert>", "<key>", "<tls-auth>"):
        check(f"windows 版含 {tag}", tag in text)
    check("windows 版有路径说明", "C:\\Users\\<你的用户名>\\OpenVPN\\config" in text)
    check("windows 版有双反斜杠示例", "C:\\\\OpenVPN\\\\config" in text)
    check("ovpn 含 remote", "remote vpn.example.com 11094" in text)
    check("ovpn 含 remote-cert-tls server", "remote-cert-tls server" in text)
    check("ovpn 含 verify-x509-name", "verify-x509-name vpn.example.com name" in text)

    android = (out / "dev1-android.ovpn").read_text(encoding="utf-8")
    check("android 版提到 Download 目录", "Download" in android)
    harmony = (out / "dev1-harmony.ovpn").read_text(encoding="utf-8")
    check("鸿蒙版提到卓易通", "卓易通" in harmony)
    linux = (out / "dev1-linux.ovpn").read_text(encoding="utf-8")
    check("linux 版提到 /etc/openvpn/client", "/etc/openvpn/client" in linux)
    macos = (out / "dev1-macos.ovpn").read_text(encoding="utf-8")
    check("macos 版提到 Tunnelblick", "Tunnelblick" in macos)
    ios = (out / "dev1-ios.ovpn").read_text(encoding="utf-8")
    check("ios 版说明沙盒限制", "沙盒" in ios)

    print("\n[6] 导出证书文件")
    files = pki.export_cert_files("dev1")
    check("导出 crt/key", len(files) == 2, str(files))
    check("crt 内容正确",
          "BEGIN CERTIFICATE" in (out / "dev1.crt").read_text(encoding="utf-8"))
    check("key 内容正确",
          "PRIVATE KEY" in (out / "dev1.key").read_text(encoding="utf-8"))

    print("\n[7] 设备完整包 zip")
    res = pki.export_bundle(name="dev1", platforms=PLATFORM_ORDER, **params)
    check("导出 zip", res["file"].endswith(".zip"), str(res))
    z = zipfile.ZipFile(io.BytesIO((out / res["file"]).read_bytes()))
    entries = z.namelist()
    check("zip 含 crt", "dev1/dev1.crt" in entries, str(entries))
    check("zip 含 key", "dev1/dev1.key" in entries)
    check("zip 含 6 个 ovpn", sum(1 for e in entries if e.endswith(".ovpn")) == 6)
    check("zip 含 README", "dev1/README.txt" in entries)
    readme = z.read("dev1/README.txt").decode("utf-8")
    check("README 有 Windows 路径", "OpenVPN\\config" in readme)
    check("README 有 Linux 路径", "/etc/openvpn/client" in readme)
    check("README 有卓易通说明", "卓易通" in readme)

    print("\n[8] 吊销 + CRL")
    r = pki.revoke("dev2")
    check("吊销 dev2", bool(r), str(r))
    check("CRL 版本递增", r.get("crl_number") == 2, str(r.get("crl_number")))
    crl = x509.load_pem_x509_crl((DATA / "ca" / "crl.pem").read_bytes())
    revoked = {c.serial_number for c in crl}
    d2 = x509.load_pem_x509_certificate((DATA / "devices" / "dev2.crt").read_bytes())
    check("CRL 含 dev2 序列号", d2.serial_number in revoked)
    check("CRL 不含 dev1 序列号", d1.serial_number not in revoked)
    check("CRL 由 CA 签名", crl.issuer == ca_crt.subject)
    st = pki.state()
    check("dev2 状态为已吊销",
          any(c["name"] == "dev2" and c["revoked"] for c in st["certs"]))
    check("dev1 仍未吊销",
          any(c["name"] == "dev1" and not c["revoked"] for c in st["certs"]))

    print("\n[9] 重复吊销应报错")
    expect_error("重复吊销被拒", lambda: pki.revoke("dev2"))

    print("\n[10] 非法输入被拒")
    expect_error("路径穿越名称被拒", lambda: pki.issue("../evil", "client"))
    expect_error("不存在的证书不能吊销", lambda: pki.revoke("no-such-cert"))
    expect_error("不存在的证书不能导出", lambda: pki.export_cert_files("no-such-cert"))

    print("\n[11] 重建 CRL")
    n = pki.build_crl()
    check("重建 CRL 返回版本号", isinstance(n, int) and n >= 2, str(n))
    check("重建后 CRL 仍含 dev2",
          d2.serial_number in {c.serial_number for c in
                               x509.load_pem_x509_crl((DATA / "ca" / "crl.pem").read_bytes())})

finally:
    pass

print("\n" + ("全部通过 ✅" if not fails else f"失败 {len(fails)} 项 ❌: {fails}"))
sys.exit(1 if fails else 0)
