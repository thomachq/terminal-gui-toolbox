# -*- coding: utf-8 -*-
"""加密 / 锁 / 打码 的离线自检。不碰 NAS，不需要 GUI 主窗口。

核心是**证据**，不是"看着像"：
  · 落盘文件里搜不到主机名、用户名、密码的任何明文片段
  · 密文被改一个字节必须解不开
  · 「记住密码」关掉后，口令字段一个都不留
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import crypto_store            # noqa: E402
import lockmgr                 # noqa: E402
from profiles import ProfileStore  # noqa: E402

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f"  <- {detail}" if detail else ""))


def main() -> int:
    td = Path(tempfile.mkdtemp(prefix="vpnsec_"))
    crypto_store.set_key_dir(td)

    # ---------------------------------------------------------------- 1
    print("\n[1] 加密原语")
    ok, info = crypto_store.selftest()
    check("自检链路通过", ok, info)
    msg = "中文与符号 !@# 都要能过"
    enc = crypto_store.protect(msg)
    check("密文带 V1 前缀", bool(enc and enc.startswith(("V1P:", "V1D:"))), str(enc)[:12])
    check("密文里没有明文", msg not in (enc or ""))
    check("能解回原文", crypto_store.unprotect(enc) == msg)
    check("密钥文件已生成，长度 32",
          (td / crypto_store.KEY_NAME).is_file()
          and len((td / crypto_store.KEY_NAME).read_bytes()) == 32)
    bad = (enc or "")[:-4] + ("AAAA" if not (enc or "").endswith("AAAA") else "BBBB")
    check("篡改后解不开", crypto_store.unprotect(bad) is None)

    # ---------------------------------------------------------------- 2
    print("\n[2] 档案整体加密")
    store = ProfileStore(td / "profiles.dat")
    store.upsert({
        "name": "NAS", "host": "192.168.3.12", "port": "3022",
        "user": "cloudadmin", "auth": "key",
        "key_path": r"D:\tools\chromas_openssh_ppk",
        "remember": True, "password": "PLAINTEXT-PW-123",
        "key_passphrase": "KEYPW-abc", "sudo_password": "SUDOPW-xyz",
    })
    store.set_last("NAS")

    raw = (td / "profiles.dat").read_bytes()
    check("档案文件已加密（非 json）", not raw.startswith(b"{"), raw[:20])
    for probe in (b"192.168.3.12", b"cloudadmin", b"PLAINTEXT-PW-123",
                  b"KEYPW-abc", b"SUDOPW-xyz"):
        check(f"明文不落盘：{probe.decode()}", probe not in raw)

    again = ProfileStore(td / "profiles.dat")
    p = again.get("NAS")
    check("能读回档案", p is not None and p["host"] == "192.168.3.12")
    check("记住的密码能还原", p["password"] == "PLAINTEXT-PW-123")
    check("上次使用被记住", again.last == "NAS")

    # ---------------------------------------------------------------- 3
    print("\n[3] 不勾「记住密码」时不留口令")
    again.upsert({
        "name": "NoRemember", "host": "10.0.0.5", "user": "root",
        "auth": "password", "remember": False,
        "password": "SHOULD-NOT-SAVE", "key_passphrase": "X",
        "sudo_password": "Y",
    })
    q = again.get("NoRemember")
    check("password 被清空", q["password"] == "")
    check("key_passphrase 被清空", q["key_passphrase"] == "")
    check("sudo_password 被清空", q["sudo_password"] == "")
    raw2 = (td / "profiles.dat").read_bytes()
    check("文件里也没有它", b"SHOULD-NOT-SAVE" not in raw2)

    # ---------------------------------------------------------------- 4
    print("\n[4] 旧明文档案自动升级")
    legacy = td / "legacy.dat"
    legacy.write_text('{"version":1,"last":"","profiles":'
                      '[{"name":"old","host":"1.2.3.4","port":"22","user":"u","auth":"key"}]}',
                      encoding="utf-8")
    st2 = ProfileStore(legacy)
    check("旧明文能读入", st2.get("old") is not None)
    check("读入后已变成密文", not legacy.read_bytes().startswith(b"{"),
          legacy.read_bytes()[:16])
    check("升级后仍能读回", ProfileStore(legacy).get("old")["host"] == "1.2.3.4")

    # ---------------------------------------------------------------- 5
    print("\n[5] 应用锁")
    settings = lockmgr.Settings(td / "settings.json")
    lock = lockmgr.LockManager(settings)
    check("初始未启用", not lock.enabled())
    check("描述如实", "未启用" in lock.describe())

    ok, m = lock.set_password("abc", timeout=1800)
    check("太短被拒", not ok, m)
    ok, m = lock.set_password("aaaaaa", timeout=1800)
    check("全同字符被拒", not ok, m)
    ok, m = lock.set_password("vpn-secret-1", timeout=1800, remember=False)
    check("正常设置成功", ok, m)
    check("已启用", lock.enabled())
    check("超时 30 分钟", lock.timeout() == 1800, str(lock.timeout()))
    check("口令正确能过", lock.check("vpn-secret-1"))
    check("口令错误不通过", not lock.check("vpn-secret-2"))
    check("没勾记住 → 不自动登录", not lock.auto_login())

    raw_set = (td / "settings.json").read_text(encoding="utf-8")
    check("settings 里没有明文口令", "vpn-secret-1" not in raw_set)
    check("存的是 PBKDF2 校验值", "pbkdf2-sha256$" in raw_set)

    lock.forget()
    check("forget 后无法自动取用", lock.password() is None)

    # 第三批起：改密码 / 关锁都必须先验旧密码
    ok, m = lock.set_password("vpn-secret-2", timeout=0, remember=True)
    check("改密码不带旧密码被拒", not ok and "当前密码" in m, m)
    ok, m = lock.set_password("vpn-secret-2", timeout=0, remember=True,
                              old_password="wrong-one")
    check("旧密码错也被拒", not ok, m)
    ok, m = lock.set_password("vpn-secret-2", timeout=0, remember=True,
                              old_password="vpn-secret-1")
    check("旧密码对才能改密码并记住", ok, m)
    check("0 分钟 = 永不锁定", lock.timeout() == 0, str(lock.timeout()))
    check("自动登录已开", lock.auto_login())
    lock.forget()
    ok, m = lock.unlock_with_remembered()
    check("用记住的口令自动解锁", ok, m)
    check("解锁后内存里有口令", lock.password() == "vpn-secret-2")

    ok, m = lock.clear_password()
    check("关锁不带旧密码被拒", not ok and "当前密码" in m, m)
    ok, m = lock.clear_password(old_password="vpn-secret-2")
    check("带旧密码能关锁", ok, m)
    check("关闭后未启用", not lock.enabled())
    check("关闭后不再自动登录", not lock.auto_login())
    check("settings 里清掉了校验值",
          "pbkdf2-sha256" not in (td / "settings.json").read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- 6
    print("\n[6] 私钥正文打码")
    import vpncert_gui as G
    rx = G.App._SECRET_BLOCK
    sample = (
        "before\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA1234\nABCDEF\n"
        "-----END RSA PRIVATE KEY-----\n"
        "after\n"
    )
    scrubbed = rx.sub("[HIDDEN]", sample)
    check("RSA 私钥被替换", "MIIEowIBAAKCAQEA1234" not in scrubbed)
    check("首尾普通文本保留", "before" in scrubbed and "after" in scrubbed)

    sample2 = ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk=\n"
               "-----END OPENSSH PRIVATE KEY-----")
    check("OpenSSH 私钥被替换", "b3BlbnNzaC1rZXk=" not in rx.sub("[H]", sample2))

    sample3 = ("-----BEGIN OpenVPN Static key V1-----\n"
               "deadbeef00112233\n-----END OpenVPN Static key V1-----")
    check("ta.key 被替换", "deadbeef00112233" not in rx.sub("[H]", sample3))

    cert = ("-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIU\n"
            "-----END CERTIFICATE-----")
    check("证书正文不受影响（非私钥）", "MIIBszCCAVmgAwIBAgIU" in rx.sub("[H]", cert))

    print(f"\n===== 结果：{passed} 通过 / {failed} 失败 =====")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
