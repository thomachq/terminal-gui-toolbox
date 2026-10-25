# -*- coding: utf-8 -*-
"""私钥加载的回归测试（v1.0.24 的群晖事故）。

事故：群晖档案用的是传统 PEM 私钥（`-----BEGIN RSA PRIVATE KEY-----` +
`Proc-Type: 4,ENCRYPTED`）。GUI 把口令当 str 传给 paramiko 5，
`PKey.from_path` 抛的是

    encountered RSA key, expected OPENSSH key

—— 一个字都没提"加密"或"口令"，看着像文件格式坏了。口令转成 bytes 之后
同一把钥匙立刻能解开。群晖那边 SSH 又只开 publickey（实测
`allowed auth: ['publickey']`），所以表现就是"怎么都连不上群晖"。

本测试锁死四件事：
  1. 四种容器 × 三种密钥类型都能读（含 openssl 默认的 PKCS#8 加密）；
  2. 加密私钥不填口令 → 报"已加密，请填私钥密码"，不能报格式错误；
  3. 口令错 → 报"密码不对"，且与"没给口令"必须区分开（两种改法不一样）；
  4. `.ppk` / 不存在 / 目录 三类输入各自报自己的错。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization as S   # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa  # noqa: E402

from core.session import SessionError, load_private_key         # noqa: E402

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


def _gen(name: str):
    if name == "rsa2048":
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if name == "ed25519":
        return ed25519.Ed25519PrivateKey.generate()
    return ec.generate_private_key(ec.SECP256R1())


def _dump(priv, fmt, enc) -> bytes:
    return priv.private_bytes(encoding=S.Encoding.PEM, format=fmt,
                              encryption_algorithm=enc)


CASES = [
    ("传统 PEM 加密", S.PrivateFormat.TraditionalOpenSSL,
     S.BestAvailableEncryption(b"pw123"), "pw123"),
    ("传统 PEM 不加密", S.PrivateFormat.TraditionalOpenSSL, S.NoEncryption(), None),
    ("PKCS#8 加密", S.PrivateFormat.PKCS8,
     S.BestAvailableEncryption(b"pw123"), "pw123"),
    ("PKCS#8 不加密", S.PrivateFormat.PKCS8, S.NoEncryption(), None),
    ("OpenSSH 不加密", S.PrivateFormat.OpenSSH, S.NoEncryption(), None),
]

print("[1] 四种容器 × 三种密钥类型")
for kt in ("rsa2048", "ed25519", "ec-p256"):
    priv = _gen(kt)
    with tempfile.TemporaryDirectory() as td:
        for label, fmt, enc, pw in CASES:
            try:
                data = _dump(priv, fmt, enc)
            except Exception as e:  # noqa: BLE001
                print(f"  [SKIP] {kt} {label}：这种组合造不出来（{type(e).__name__}）")
                continue
            p = Path(td) / "k"
            p.write_bytes(data)
            # 口令对 → 必须读得出来
            try:
                key = load_private_key(str(p), pw)
                got = f"{key.get_name()} {key.get_bits()}"
                chk(bool(got), f"{kt} {label} 口令正确能读", got)
            except SessionError as e:
                chk(False, f"{kt} {label} 口令正确能读", str(e))
            if pw is None:
                continue
            # 没给口令 → 必须说"已加密"
            try:
                load_private_key(str(p), None)
                chk(False, f"{kt} {label} 不填口令要报加密", "居然读出来了")
            except SessionError as e:
                chk("加密" in str(e) and "私钥密码" in str(e),
                    f"{kt} {label} 不填口令报『已加密』", str(e))
            # 口令错 → 必须说"口令不对"，且不能说成"没填"
            try:
                load_private_key(str(p), "wrong-pw")
                chk(False, f"{kt} {label} 口令错要报错", "居然读出来了")
            except SessionError as e:
                chk("不对" in str(e), f"{kt} {label} 口令错报『密码不对』", str(e))

print("\n[2] 输入不合法时的报错")
with tempfile.TemporaryDirectory() as td:
    missing = Path(td) / "nope"
    try:
        load_private_key(str(missing), None)
        chk(False, "不存在的私钥要报错", "没抛")
    except SessionError as e:
        chk("不存在" in str(e), "不存在的私钥报『不存在』", str(e))

    try:
        load_private_key(td, None)
        chk(False, "目录要报错", "没抛")
    except SessionError as e:
        chk("不是普通文件" in str(e), "目录报『不是普通文件』", str(e))

    ppk = Path(td) / "id.ppk"
    ppk.write_text("PuTTY-User-Key-File-2: ssh-rsa\n", encoding="utf-8")
    try:
        load_private_key(str(ppk), None)
        chk(False, ".ppk 要报错", "没抛")
    except SessionError as e:
        chk("PuTTYgen" in str(e), ".ppk 提示用 PuTTYgen 转", str(e))

    blank = Path(td) / "blank"
    blank.write_text("", encoding="utf-8")
    try:
        load_private_key(str(blank), None)
        chk(False, "空文件要报错", "没抛")
    except SessionError as e:
        chk(bool(str(e)), "空文件报错不为空", str(e))

print("\n[3] 口令类型：str / bytes 等价")
priv = _gen("rsa2048")
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "k"
    p.write_bytes(_dump(priv, S.PrivateFormat.TraditionalOpenSSL,
                        S.BestAvailableEncryption(b"pw123")))
    try:
        a = load_private_key(str(p), "pw123")
        b = load_private_key(str(p), b"pw123")
        chk(a.asbytes() == b.asbytes(), "str 口令与 bytes 口令解出同一把钥匙")
    except SessionError as e:
        chk(False, "str 口令与 bytes 口令等价", str(e))

print("\n[4] 连接层不再把口令原样交给 paramiko")
src = (ROOT / "core" / "session.py").read_text(encoding="utf-8")
chk("pkey=pkey" in src, "connect() 用 pkey 传已解好的钥匙")
chk('passphrase=(key_passphrase or None)' not in src,
    "connect() 不再传 str 口令给 key_filename 分支")
chk("def load_private_key" in src, "core.session 提供 load_private_key")

print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
sys.exit(1 if FAIL else 0)
