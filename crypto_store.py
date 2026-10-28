# -*- coding: utf-8 -*-
"""机密存储 —— 档案与「记住的密码」都走这里。

两种方案，按运行环境自动选：

  · **便携（默认）** —— `data\\.vpncert-key` 放 32 字节随机密钥，AES-256-GCM 加密。
    整个文件夹搬走就能解开，所以强度是「防明文被顺手看到/被搜索」，
    **不防拿到整个文件夹的人**。便携与保密本来就对立，这里选了便携。

  · **DPAPI** —— `CryptProtectData`，密钥由当前 Windows 用户主密钥派生。
    换机器、换用户都解不开。data 目录不可写时用它兜底。

密文都带前缀（`V1P:` / `V1D:`），解码时按前缀分派：
便携包在别的机器上遇到 DPAPI 密文会返回 None，上层表现成「解不开，请重新输入」，
不会拿到乱码。
"""
from __future__ import annotations

import base64
import ctypes
import os
import secrets
import sys
from ctypes import wintypes
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

IS_WINDOWS = sys.platform == "win32"

P_PORT = "V1P:"
P_DPAPI = "V1D:"
_ENTROPY = b"VPNCertConsole/DPAPI/v1"
KEY_NAME = ".vpncert-key"

_key_dir: Path | None = None


def set_key_dir(path: str | Path) -> None:
    """由主程序在启动时指定密钥文件所在目录（通常是 data\\）。"""
    global _key_dir
    _key_dir = Path(path)


def _key_path() -> Path | None:
    if _key_dir is None:
        return None
    return _key_dir / KEY_NAME


# ------------------------------------------------------------------ 便携密钥
def _portable_key(create: bool = True) -> bytes | None:
    p = _key_path()
    if p is None:
        return None
    try:
        if p.is_file():
            k = p.read_bytes()
            if len(k) >= 32:
                return k[:32]
        if not create:
            return None
        k = secrets.token_bytes(32)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(k)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return k
    except OSError:
        return None


def _portable_protect(plain: bytes) -> str | None:
    key = _portable_key()
    if not key:
        return None
    nonce = secrets.token_bytes(12)
    blob = AESGCM(key).encrypt(nonce, plain, None)
    return P_PORT + base64.b64encode(nonce + blob).decode("ascii")


def _portable_unprotect(b64: str) -> str | None:
    key = _portable_key(create=False)
    if not key:
        return None
    try:
        raw = base64.b64decode(b64)
        if len(raw) < 12 + 16:
            return None
        return AESGCM(key).decrypt(raw[:12], raw[12:], None).decode("utf-8")
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ DPAPI
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _make_in(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _read_out(blob) -> bytes:
    if not blob.pbData:
        return b""
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def dpapi_available() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        ctypes.windll.crypt32
        return True
    except Exception:  # noqa: BLE001
        return False


def _dpapi_protect(plain: bytes) -> str | None:
    try:
        bi, _k1 = _make_in(plain)
        ei, _k2 = _make_in(_ENTROPY)
        bo = _DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(bi), None, ctypes.byref(ei), None, None, 0,
            ctypes.byref(bo))
        if not ok:
            return None
        return P_DPAPI + base64.b64encode(_read_out(bo)).decode("ascii")
    except Exception:  # noqa: BLE001
        return None


def _dpapi_unprotect(b64: str) -> str | None:
    if not dpapi_available():
        return None
    try:
        raw = base64.b64decode(b64)
        bi, _k1 = _make_in(raw)
        # ★ entropy 必须与加密时完全一致，否则 CryptUnprotectData 直接失败。
        ei, _k2 = _make_in(_ENTROPY)
        bo = _DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(bi), None, ctypes.byref(ei), None, None, 0,
            ctypes.byref(bo))
        if not ok:
            return None
        return _read_out(bo).decode("utf-8")
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ 对外
def scheme() -> str:
    """当前使用的方案名，给界面/自检显示。"""
    return "portable" if _key_path() is not None else "dpapi"


def scheme_label() -> str:
    return {
        "portable": "便携密钥 · AES-256-GCM",
        "dpapi": "Windows DPAPI",
    }.get(scheme(), scheme())


def protect(plaintext: str | bytes) -> str | None:
    """明文 -> 带前缀的 base64 密文；失败返回 None。"""
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    if _key_path() is not None:
        out = _portable_protect(plaintext)
        if out:
            return out
    return _dpapi_protect(plaintext)


def unprotect(b64: str | None) -> str | None:
    """带前缀的密文 -> 明文；失败返回 None。无前缀的历史数据按 DPAPI 处理。"""
    if not b64:
        return None
    if b64.startswith(P_PORT):
        return _portable_unprotect(b64[len(P_PORT):])
    if b64.startswith(P_DPAPI):
        return _dpapi_unprotect(b64[len(P_DPAPI):])
    return _dpapi_unprotect(b64)


def seal_bytes(data: bytes) -> bytes:
    """整个文件加密：返回可直接落盘的字节串。失败则抛 OSError。"""
    enc = protect(data)
    if not enc:
        raise OSError("加密不可用（密钥目录未设置或写入失败）")
    return enc.encode("ascii")


def open_bytes(raw: bytes) -> bytes | None:
    """还原整个文件；失败返回 None。

    注意：`unprotect()` 把结果 `.decode("utf-8")`，所以这里**只适合文本**。
    任意二进制（tar/zip/图片）请用 `seal_blob` / `open_blob`，否则能加密、
    但解回来时会被 utf-8 解码弄坏（静默变成 None 或乱码）。
    """
    try:
        txt = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    plain = unprotect(txt)
    return plain.encode("utf-8") if plain is not None else None


def seal_blob(data: bytes) -> bytes:
    """任意二进制 → 可直接落盘的密文。

    内部先 base64 再走 `seal_bytes`，因为 `unprotect()` 强制 utf-8 解码，
    不包装的话二进制会在解密时损坏。
    """
    return seal_bytes(base64.b64encode(data))


def open_blob(raw: bytes) -> bytes | None:
    """`seal_blob` 的逆运算；失败返回 None。"""
    txt = open_bytes(raw)
    if txt is None:
        return None
    try:
        return base64.b64decode(txt, validate=True)
    except Exception:  # noqa: BLE001
        return None


def selftest() -> tuple[bool, str]:
    """可执行的证据链，避免「看起来能加密」其实空转。"""
    msg = "vpn-cert-selftest-中文-42"
    enc = protect(msg)
    if not enc:
        return False, "protect() 返回 None"
    if msg in enc:
        return False, "密文里出现了明文，加密没生效"
    if unprotect(enc) != msg:
        return False, "解回来不一致"
    try:                                  # 篡改一个字节必须解不开
        body = enc.split(":", 1)[1]
        i = len(body) // 2
        ch = "A" if body[i] != "A" else "B"
        bad = enc.split(":", 1)[0] + ":" + body[:i] + ch + body[i + 1:]
        if unprotect(bad) == msg:
            return False, "密文被改过还能解开，完整性校验无效"
    except Exception:  # noqa: BLE001
        pass
    return True, f"{scheme_label()} · {enc[:22]}..."


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        set_key_dir(td)
        ok, info = selftest()
        print("selftest:", "PASS" if ok else "FAIL", "|", info)
