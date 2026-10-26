# -*- coding: utf-8 -*-
"""服务器证书：自签名生成 / 解析 / 证书库（增删查导）的测试。

真生成证书（RSA 2048 与 ECDSA 各一张，4096 太慢不跑），但**不连网络**。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cryptography import x509  # noqa: E402

from core import servercert as SC  # noqa: E402

_p = _f = 0


def chk(ok, label, extra=""):
    global _p, _f
    if ok:
        _p += 1
        print(f"  ok   {label}")
    else:
        _f += 1
        print(f"  FAIL {label}  [{extra}]")


def sec(t):
    print(f"\n-- {t}")


# ==================================================== 1. 生成
sec("1. 自签名生成")
cert, key = SC.make_selfsigned("nas.local", ["nas.local", "192.168.1.10"],
                               days=365, bits=2048)
chk("BEGIN CERTIFICATE" in cert, "产出 PEM 证书")
chk("BEGIN PRIVATE KEY" in key, "产出 PEM 私钥（PKCS#8）")
obj = x509.load_pem_x509_certificate(cert.encode())
chk(obj.issuer == obj.subject, "签发者 == 主体（自签名）")
chk(obj.signature_hash_algorithm.name.lower() == "sha256", "签名用 SHA256",
    obj.signature_hash_algorithm.name)

info = SC.parse_pem(cert)
chk(info["cn"] == "nas.local", "CN 读得出", info["cn"])
chk(info["self_signed"], "识别为自签名")
chk("nas.local" in info["sans"] and "192.168.1.10" in info["sans"],
    "SAN 里既有域名也有 IP", str(info["sans"]))
chk(360 <= info["days_left"] <= 366, "剩余天数和给的一致", str(info["days_left"]))
chk(len(info["sha256"]) == 64, "SHA256 指纹 64 位 hex")
chk(bool(info["serial"]), "有序列号")

# SAN 里必须有 CN：只填 CN 的证书浏览器不认
c2, k2 = SC.make_selfsigned("only-cn.example", ["10.0.0.5"])
i2 = SC.parse_pem(c2)
chk("only-cn.example" in i2["sans"],
    "CN 不在 SAN 里时自动补进去（否则浏览器报不受信任）", str(i2["sans"]))

# SAN 完全不填 → 用 CN 顶上
c3, _ = SC.make_selfsigned("auto.example", [])
chk(SC.parse_pem(c3)["sans"] == ["auto.example"], "SAN 留空时用 CN 补")

# ECDSA
c4, k4 = SC.make_selfsigned("ec.example", ["ec.example"], kind="ec")
i4 = SC.parse_pem(c4)
chk(i4["cn"] == "ec.example", "ECDSA 也能生成")
chk("EC PRIVATE KEY" in k4 or "BEGIN PRIVATE KEY" in k4, "EC 私钥是 PEM")

# 非法输入
try:
    SC.make_selfsigned("", ["a.com"])
    chk(False, "空 CN 要报错")
except SC.CertError:
    chk(True, "空 CN 要报错")
try:
    c5, _k5 = SC.make_selfsigned("x.com", [])
    chk(SC.parse_pem(c5)["sans"] == ["x.com"], "空 SAN 用 CN 兜底，不报错")
except SC.CertError as e:
    chk(False, "空 SAN 用 CN 兜底，不报错", str(e))

try:
    SC.parse_pem("这不是证书")
    chk(False, "非 PEM 要抛错")
except SC.CertError as e:
    chk("PEM" in str(e), "非 PEM 抛错且说明清楚", str(e))

# ==================================================== 2. split_sans
sec("2. SAN 拆分")
chk(SC.split_sans("a.com, b.com\n192.168.1.1, A.COM") ==
    ["a.com", "b.com", "192.168.1.1"], "逗号/换行/大小写去重",
    str(SC.split_sans("a.com, b.com\n192.168.1.1, A.COM")))
chk(SC.split_sans("") == [], "空串给空列表")

# ==================================================== 3. 证书库
sec("3. 证书库")
base = Path(tempfile.mkdtemp()) / "certs"
st = SC.Store(base)
chk(st.list() == [], "空库返回空列表（目录都不存在也不炸）")
row = st.add("web", cert, key, meta={"kind": "rsa"})
chk(row["name"] == "web", "写入后名字对", row["name"])
chk((base / "web" / "cert.pem").is_file(), "cert.pem 落盘")
chk((base / "web" / "key.pem").is_file(), "key.pem 落盘")
chk((base / "web" / "meta.json").is_file(), "meta.json 落盘")

rows = st.list()
chk(len(rows) == 1, "列表 1 条")
chk(rows[0]["has_key"] is True, "标记出有私钥")
chk(rows[0]["kind"] == "rsa", "meta 里的字段合并进行里", str(rows[0].get("kind")))

# 重名不覆盖
try:
    st.add("web", cert, key)
    chk(False, "重名要报错")
except SC.CertError as e:
    chk("已经存在" in str(e), "重名报错并说明", str(e))
row2 = st.add("web", cert, key, overwrite=True)
chk(row2["name"] == "web", "overwrite=True 可以覆盖")

# 只导证书不导私钥
st.add("nokey", cert, "")
chk(st.list()[-1]["has_key"] is False or
    [r for r in st.list() if r["name"] == "nokey"][0]["has_key"] is False,
    "只导证书时不给私钥")

# 名字里的危险字符要洗掉
st.add("../../etc/evil", cert, key)
names = [r["name"] for r in st.list()]
chk(not any("/" in n or ".." in n for n in names),
    "路径穿越的名字被洗掉", str(names))

# 导出
out = Path(tempfile.mkdtemp())
made = st.export("web", out)
chk(len(made) == 2, "导出 crt + key 两个文件", str(made))
chk((out / "web.crt").is_file() and (out / "web.key").is_file(),
    "文件名是 <名字>.crt / .key")
chk((out / "web.crt").read_text(encoding="utf-8") == cert, "导出的内容一致")

# 读
rc, rk = st.read("web")
chk(rc == cert and rk == key, "read() 拿回原文")

# 删
chk(st.remove("web") is True, "删除返回 True")
chk(st.remove("web") is False, "删不存在的返回 False")
chk("web" not in [r["name"] for r in st.list()], "删完列表里没有")

# ==================================================== 4. 过期与提示
sec("4. 过期判断与部署提示")
c_old, k_old = SC.make_selfsigned("old.example", ["old.example"], days=1)
i_old = SC.parse_pem(c_old)
chk(not i_old["expired"], "1 天有效期还没过期")
# 造一个已过期的：把 not_after 往前挪做不到，这里只验字段逻辑
chk(i_old["days_left"] <= 1, "剩余天数 ≤1", str(i_old["days_left"]))

for key_w, must in (("synology", "群晖"), ("nginx", "ssl_certificate"),
                    ("iis", "IIS"), ("", "ssl_certificate_key")):
    h = SC.deploy_hint(key_w)
    chk(must in h, f"deploy_hint({key_w!r}) 提到 {must}", h[:60])

print("\n" + "=" * 52)
print(f"结果：{_p} 通过 / {_f} 失败")
sys.exit(0 if _f == 0 else 1)
