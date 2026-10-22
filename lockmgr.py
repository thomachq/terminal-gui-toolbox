# -*- coding: utf-8 -*-
"""应用锁 —— 一把密码管「锁屏」。

设计要点（照 Rclone-GUI 的做法，去掉它那套 rclone 配置联动）：

  * 落盘的是口令的 **PBKDF2-HMAC-SHA256 校验值**，不是口令本身；
    口令只活在本次运行的内存里，除非用户勾了「记住密码」。
  * 「记住密码」= 用 crypto_store 把口令包起来存本机（便携密钥 / DPAPI），
    这样启动能自动解锁。不勾就每次手输。
  * 闲置超时自动锁定；0 表示永不。默认 30 分钟。
  * **没设密码时界面必须如实说「未启用」**，不能装作有保护。

注意一个能力边界：应用锁锁的是**界面**，不是档案文件。
档案由 crypto_store 用 data 目录里的密钥加密 —— 能拿到整个文件夹的人，
拆开写个解密脚本是能读的（密钥就在旁边）。要连这个也防住，就得把锁密码
当密钥派生因子，代价是**忘记密码 = CA 永久丢失**，对本工具太危险，故不做。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

import crypto_store

DEFAULT_TIMEOUT = 30 * 60      # 秒
MIN_TIMEOUT = 60
MAX_TIMEOUT = 24 * 3600
PBKDF2_ITER = 200_000
_HASH_SCHEME = "pbkdf2-sha256"
MIN_PASSWORD = 6

SETTING_KEYS = (
    "lock_enabled",     # bool
    "lock_hash",        # str：PBKDF2 校验值
    "lock_timeout",     # int：闲置秒数，0 = 永不
    "lock_auto_login",  # bool
    "lock_pass_enc",    # str：记住的口令（crypto_store 密文）
    # ---- 设置中心（外观 / 工作区）
    "theme",            # str：light / dark / system
    "accent",           # str：主题配色名
    "close_to_tray",    # bool：关闭窗口时收到托盘
    "log_open",         # bool：启动时展开日志栏
)


# ------------------------------------------------------------------ 口令
def hash_password(password: str, iterations: int = PBKDF2_ITER,
                  salt: bytes | None = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                             int(iterations))
    return "{}${}${}${}".format(
        _HASH_SCHEME, int(iterations),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"))


def verify_password(password: str, stored: str) -> bool:
    """失败关闭：stored 为空或格式不对一律 False。"""
    if not password or not stored:
        return False
    try:
        scheme, iters, salt_b64, hash_b64 = str(stored).split("$")
        if scheme != _HASH_SCHEME:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:  # noqa: BLE001
        return False


def validate_password(password: str) -> tuple[bool, str]:
    if not password:
        return False, "密码不能为空。"
    if password.strip() != password:
        return False, "密码首尾不能有空格（中间可以有）。"
    if "\n" in password or "\r" in password:
        return False, "密码里不能有换行符（其余字符都可以）。"
    n = len(password)
    if n < MIN_PASSWORD:
        return False, f"密码太短了，至少 {MIN_PASSWORD} 位（现在 {n} 位）。"
    if len(set(password)) == 1:
        return False, "密码不能由同一个字符重复组成，那等于没设密码。"
    return True, ""


def strength_note(password: str) -> str:
    n = len(password or "")
    if n == 0:
        return ""
    if n < MIN_PASSWORD:
        return f"还差 {MIN_PASSWORD - n} 位"
    if n < 8:
        return "刚好达标，建议 8 位以上"
    return "长度够用"


# ------------------------------------------------------------------ 状态存储
class Settings:
    """data/settings.json —— 只放非秘密内容（校验值、超时、密文口令）。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {}
        self.load()

    def load(self) -> None:
        try:
            if self.path.is_file():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.data = {k: raw[k] for k in SETTING_KEYS if k in raw}
        except (OSError, ValueError):
            self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, key: str, default=None):
        return self.data.get(key, default)


# ------------------------------------------------------------------ 锁本体
class LockManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._pw: str | None = None

    # ---------------------------------------------------------- 状态
    def enabled(self) -> bool:
        return (bool(self.settings.get("lock_enabled"))
                and bool(self.settings.get("lock_hash")))

    def timeout(self) -> int:
        try:
            t = int(self.settings.get("lock_timeout", DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            t = DEFAULT_TIMEOUT
        return max(0, min(t, MAX_TIMEOUT))

    def auto_login(self) -> bool:
        return (bool(self.settings.get("lock_auto_login"))
                and bool(self.settings.get("lock_pass_enc")))

    def describe(self) -> str:
        if not self.enabled():
            return "应用锁未启用"
        t = self.timeout()
        bits = ["已启用", "永不自动锁" if t == 0 else f"闲置 {t // 60} 分钟锁定"]
        if self.auto_login():
            bits.append("自动解锁")
        return " · ".join(bits)

    # ---------------------------------------------------------- 口令
    def password(self) -> str | None:
        if self._pw:
            return self._pw
        return self.remembered()

    def remembered(self) -> str | None:
        enc = self.settings.get("lock_pass_enc") or ""
        return crypto_store.unprotect(enc) if enc else None

    def check(self, password: str) -> bool:
        return verify_password(password, self.settings.get("lock_hash") or "")

    def adopt(self, password: str) -> None:
        self._pw = password

    def forget(self) -> None:
        self._pw = None

    # ---------------------------------------------------------- 动作
    def set_password(self, password: str, timeout: int | None = None,
                     remember: bool = False,
                     old_password: str | None = None) -> tuple[bool, str]:
        """设置或修改密码。

        **已启用时必须先给对旧密码** —— 否则登录进来的人（或留在桌面前的
        任何人）可以绕过旧密码直接换一把新的，锁就形同虚设。
        """
        if self.enabled():
            if not old_password:
                return False, "修改密码需要先输入当前密码。"
            if not self.check(old_password):
                return False, "当前密码不正确。"
        ok, msg = validate_password(password)
        if not ok:
            return False, msg
        self.settings.data["lock_enabled"] = True
        self.settings.data["lock_hash"] = hash_password(password)
        if timeout is not None:
            self.settings.data["lock_timeout"] = (
                0 if int(timeout) <= 0
                else max(MIN_TIMEOUT, min(int(timeout), MAX_TIMEOUT)))
        self.settings.data["lock_auto_login"] = bool(remember)
        self.settings.data["lock_pass_enc"] = (
            crypto_store.protect(password) or "") if remember else ""
        self.settings.save()
        self._pw = password
        return True, "应用锁已启用。" if not old_password else "密码已更新。"

    def clear_password(self, old_password: str | None = None) -> tuple[bool, str]:
        """关闭应用锁 —— 同样要旧密码，否则谁都能把锁摘掉。"""
        if not self.enabled():
            return True, "本来就没有设密码。"
        if not old_password:
            return False, "关闭应用锁需要先输入当前密码。"
        if not self.check(old_password):
            return False, "当前密码不正确。"
        for k in ("lock_enabled", "lock_hash", "lock_auto_login", "lock_pass_enc"):
            self.settings.data[k] = "" if k.endswith(("hash", "enc")) else False
        self.settings.save()
        self._pw = None
        return True, "应用锁已关闭。"

    def set_options(self, timeout: int | None = None,
                    remember: bool | None = None) -> tuple[bool, str]:
        if timeout is not None:
            self.settings.data["lock_timeout"] = max(
                MIN_TIMEOUT, min(int(timeout), MAX_TIMEOUT))
        if remember is not None:
            self.settings.data["lock_auto_login"] = bool(remember)
            if remember:
                pw = self.password()
                if pw:
                    self.settings.data["lock_pass_enc"] = (
                        crypto_store.protect(pw) or "")
            else:
                self.settings.data["lock_pass_enc"] = ""
        self.settings.save()
        return True, "已保存。"

    def unlock_with_remembered(self) -> tuple[bool, str]:
        pw = self.remembered()
        if not pw:
            return False, "没有记住密码。"
        if not self.check(pw):
            return False, "记住的密码对不上了（可能改过密码），请手动输入。"
        self._pw = pw
        return True, "已用记住的密码自动解锁。"
