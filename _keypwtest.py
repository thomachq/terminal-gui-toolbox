# -*- coding: utf-8 -*-
"""私钥密码的三道判据 —— 「口令错」不许再说成「密钥坏了」。

起因（用户 2026-10-27 00:05）：
  「我这个 192.168.3.48（飞牛OS），ssh 密钥和 192.168.3.12 是一样，
   为什么连接的时候就提示密钥口令错误或密钥损坏呢」

真机实测（安装目录 D:\\Tools\\Terminal-GUI-Toolbox\\data）：
  · 两条档案的 key_path 完全相同：D:\\Tools\\putty-64bit\\chromas_openssh_ppk；
  · 192.168.3.12 的 key_passphrase 16 位 → 解出 RSAKey；
  · 飞牛OS 的 key_passphrase 12 位 → 解不开，而它**与该档案的 password、
    sudo_password 完全相等** —— 建档案时把账户登录密码贴进了「私钥密码」；
  · 3.48:3022 可达（OpenSSH_9.2p1 Debian-12），用正确口令实连成功
    uid=1000(fnosadmin)。密钥与网络都没问题。

要钉死的三件事：
  A. 解不开时，文案不许出现「密钥可能也坏了」这类把人引去重新生成的措辞，
     并且要带上是哪个私钥文件；
  B. 同一把私钥在别的档案里有能解开的口令时，要**点名是哪一条**；
  C. 保存档案时就要拦（原来拖到点「连接」才校验）。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core.session import SessionError, load_private_key  # noqa: E402
from profiles import inspect_key, passphrase_owner     # noqa: E402

_p = _f = _s = 0

GOOD = "correct-password"        # 16 位，和真机那串等长
BAD = "wrongpass12"              # 12 位 —— 真实事故里贴进去的就是这种长度


def chk(ok, label, extra=""):
    global _p, _f
    if ok:
        _p += 1
        print(f"  ok   {label}")
    else:
        _f += 1
        print(f"  FAIL {label}  [{extra}]")


def sec(t):
    global _s
    _s += 1
    print(f"\n{_s}. {t}")


class _Store:
    """只实现 passphrase_owner 用到的那一个方法。"""

    def __init__(self, recs):
        self._recs = list(recs)

    def profiles(self):
        return list(self._recs)


def _mk_key(path: Path) -> None:
    """造一把**传统 PEM + 口令加密**的私钥 —— 和真机那把同一种容器。"""
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import rsa

    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    blob = k.private_bytes(
        ser.Encoding.PEM,
        ser.PrivateFormat.TraditionalOpenSSL,          # 就是 BEGIN RSA PRIVATE KEY
        ser.BestAvailableEncryption(GOOD.encode()),
    )
    path.write_bytes(blob)


tmp = Path(tempfile.mkdtemp(prefix="tb_keypw_"))
key = tmp / "chromas_openssh_ppk"
_mk_key(key)
print(f"临时私钥: {key}  ({key.stat().st_size} 字节)")

try:
    # ---------------------------------------------------------------- A
    sec("闸门 A：口令错不许说成「密钥坏了」")
    try:
        load_private_key(str(key), BAD)
        chk(False, "错误口令应该抛异常", "居然解开了")
        msg = ""
    except SessionError as e:
        msg = str(e)
        chk(True, "错误口令抛 SessionError")
    except Exception as e:  # noqa: BLE001
        msg = ""
        chk(False, "错误口令抛 SessionError", f"{type(e).__name__}: {e}")

    chk("私钥密码不对" in msg, "文案直说「私钥密码不对」", msg)
    chk(key.name in msg, "文案带上私钥文件名（知道是哪把）", msg)
    chk("坏了" not in msg, "**不出现「坏了」**（旧文案把人引去重新生成密钥）", msg)
    chk("同一把私钥" in msg, "讲清「同一把私钥必须同一个密码」", msg)
    chk("重新生成" in msg, "明确说不需要重新生成", msg)
    chk(str(tmp) not in msg, "不带完整路径（只留文件名，避免长句撑破弹窗）", msg)

    info = inspect_key(str(key), BAD)
    chk(info.get("ok") is False, "inspect_key 判为不可用")
    chk(bool(info.get("encrypted")), "inspect_key 仍认出这是加密私钥")
    chk("坏了" not in str(info.get("error")), "inspect_key 的 error 也不含「坏了」",
        info.get("error"))

    # ---------------------------------------------------------------- B
    sec("闸门 B：点名「该抄哪一条档案」")
    other = {
        "name": "192.168.3.12", "proto": "ssh", "auth": "key",
        "key_path": str(key), "key_passphrase": GOOD,
    }
    mine_bad = {
        "name": "飞牛OS", "proto": "ssh", "auth": "key",
        "key_path": str(key), "key_passphrase": BAD,
    }
    mine_ok = dict(mine_bad, key_passphrase=GOOD)

    store = _Store([other, mine_bad])
    got = passphrase_owner(store, str(key), BAD)
    chk(got == "192.168.3.12", "同路径 + 别的档案能解开 → 返回那条名字", got)

    chk(passphrase_owner(store, str(key), GOOD) is None,
        "自己已经能解开时不提示（别让人以为自己错了）")
    chk(passphrase_owner(_Store([mine_bad]), str(key), BAD) is None,
        "只有自己一条时不乱点名")
    chk(passphrase_owner(_Store([other]), str(tmp / "id_other"), BAD) is None,
        "路径不同不算同一把私钥")
    chk(passphrase_owner(None, str(key), BAD) is None, "store 为 None 不炸")

    # ---------------------------------------------------------------- C
    sec("闸门 C：保存时就拦（不用等到点连接）")
    from pages.hosts_page import keypw_error  # noqa: E402

    chk(keypw_error(store, mine_ok) is None, "口令正确 → 允许保存")
    err = keypw_error(store, mine_bad)
    chk(bool(err), "口令错误 → 拦住保存")
    chk("192.168.3.12" in str(err), "拦住时点名该抄哪一条", err)
    chk("没坏" in str(err) or "不用重新生成" in str(err),
        "拦住时说明密钥没坏", err)

    err2 = keypw_error(store, dict(mine_bad, key_passphrase=""))
    chk("必须填" in str(err2), "加密私钥留空 → 说清「必须填」而不是「不用填」", err2)

    chk(keypw_error(store, dict(mine_bad, auth="password")) is None,
        "密码认证不看私钥")
    chk(keypw_error(store, dict(mine_bad, proto="local")) is None,
        "本机连接不看私钥")
    chk(keypw_error(store, dict(mine_bad, key_path="")) is None,
        "没选私钥不拦（改由连接时的缺参提示处理）")

    # ---------------------------------------------------------------- D
    sec("闸门 D：真机那把私钥（存在才测）")
    real = Path(r"D:\Tools\putty-64bit\chromas_openssh_ppk")
    if real.is_file():
        rmsg = ""
        try:
            load_private_key(str(real), BAD)
            chk(False, "真机私钥 + 错误口令应抛异常", "居然解开了")
        except SessionError as e:
            rmsg = str(e)          # except 块里的 e 出了块就没了，先接住
            chk("坏了" not in rmsg, "真机私钥的错误文案也不含「坏了」", rmsg)
        chk(real.name in rmsg, "文案带上真机私钥文件名", rmsg)
    else:
        chk(True, "真机私钥不在本机，跳过（不算失败）")

    # ---------------------------------------------------------------- E
    sec("闸门 E：改完的模块还能编译（防手滑）")
    import py_compile

    for fn in ("core/session.py", "profiles.py", "pages/hosts_page.py", "app.py"):
        try:
            py_compile.compile(str(HERE / fn), doraise=True)
            chk(True, f"{fn} 编译通过")
        except Exception as e:  # noqa: BLE001
            chk(False, f"{fn} 编译通过", e)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n结果：{_p} 通过 / {_f} 失败")
if _f:
    print(" 失败项见上面的 FAIL 行")
sys.exit(1 if _f else 0)
