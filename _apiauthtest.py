# -*- coding: utf-8 -*-
"""API 连接的认证方式选择（`api/pool.py::_auth_of`）。

背景（2026-10-27 实机）：
  飞牛OS（192.168.3.48）档案是「私钥 + 口令」认证，但里面还存了账户登录密码
  （备 sudo 用）。API 原来的写法是

      auth = "password" if spec.get("password") else "key"

  —— 只要有密码就走密码认证，私钥被直接丢掉。而该机 sshd 上密码认证是关的，
  于是 `POST /v1/sessions` 返回 400「认证失败：检查用户名/密码/私钥（及私钥口令）」，
  同一把私钥用 paramiko 直连却完全正常。用户看到的是「工具箱连不上，手动 ssh 能连」，
  报错还把人引去查密钥。所以这个优先级必须钉死：

  1. 请求里声明了 auth 就照它办（档案里用户自己选的）；
  2. 没声明就看 key_path —— 指定了私钥文件，不该被顺手存的密码顶掉；
  3. 都没有才是密码。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import api.pool as P                     # noqa: E402
from core.session import SessionError    # noqa: E402

FAIL = []
PASS = [0]


def chk(ok, title, detail=""):
    if ok:
        PASS[0] += 1
        print(f"  ok   {title}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL.append(title)
        print(f"  FAIL {title}" + (f"  [{detail}]" if detail else ""))


print("1. 纯函数：auth 优先级")
cases = [
    ({"auth": "key", "password": "acc", "key_path": "/k"}, "key", "私钥档案顺带存了密码"),
    ({"auth": "key", "password": "acc"}, "key", "声明 key 但没给 key_path"),
    ({"auth": "password", "password": "pw"}, "password", "声明 password"),
    ({"auth": "password", "key_path": "/k"}, "password", "声明 password 即使有私钥"),
    ({"key_path": "/k", "password": "acc"}, "key", "未声明：有私钥就用私钥"),
    ({"key_path": "/k"}, "key", "未声明：只有私钥"),
    ({"password": "pw"}, "password", "未声明：只有密码"),
    ({}, "password", "什么都没给"),
    ({"auth": "KEY", "password": "x"}, "key", "大小写不敏感"),
    ({"auth": "  key  ", "password": "x"}, "key", "首尾空格"),
    ({"auth": "bogus", "key_path": "/k", "password": "x"}, "key", "非法值回落到常规判断"),
    ({"auth": None, "key_path": "", "password": "x"}, "password", "auth=None 不炸"),
]
for spec, want, why in cases:
    got = P._auth_of(spec)
    chk(got == want, f"{why} → {want}", f"得到 {got}")

print("\n2. 端到端：pool.open 造出来的 ConnSpec 走对认证")
CAP = []


def fake_open(cs):
    CAP.append(cs)
    raise SessionError("测试桩：不真连")


real_open = P.open_session
P.open_session = fake_open
try:
    pool = P.SessionPool()
    try:
        pool.open({"protocol": "ssh", "host": "192.168.3.48", "port": 3022,
                   "user": "fnosadmin", "key_path": "/k/id_rsa",
                   "key_passphrase": "pp", "password": "账户密码12位"})
    except P.PoolError:
        pass
    chk(len(CAP) == 1, "ConnSpec 已被造出来")
    if CAP:
        cs = CAP[0]
        chk(cs.auth == "key", "私钥档案 + 有密码 → 仍然用 key", f"auth={cs.auth}")
        chk(cs.key_path == "/k/id_rsa", "私钥路径没丢")
        chk(cs.key_passphrase == "pp", "私钥口令没丢")

    CAP.clear()
    try:
        pool.open({"protocol": "ssh", "host": "h", "user": "u", "password": "pw"})
    except P.PoolError:
        pass
    chk(bool(CAP) and CAP[0].auth == "password", "纯密码档案 → password",
        f"auth={CAP[0].auth if CAP else '-'}")
finally:
    P.open_session = real_open

print("\n3. 防回归：代码里不该再出现「有密码就用密码」的写法")
src = (ROOT / "api" / "pool.py").read_text(encoding="utf-8")
# 只看赋值形态，别把 docstring 里「原来的写法是……」那段说明也判成残留
bad = ['auth="password" if', "auth='password' if"]
chk(not any(b in src for b in bad), "没有残留的旧三元表达式")
chk(src.count("_auth_of(spec)") == 2, "两处连接点都改用它（open + 重连）",
    f"出现 {src.count('_auth_of(spec)')} 次")

print("\n4. OpenAPI 里说清了 auth 字段")
srv = (ROOT / "api" / "server.py").read_text(encoding="utf-8")
chk('"auth"' in srv and "认证方式" in srv, "schema 有 auth 字段与说明")

print("\n5. 改完的文件还能编译")
import py_compile  # noqa: E402
for rel in ("api/pool.py", "api/server.py"):
    try:
        py_compile.compile(str(ROOT / rel), doraise=True)
        chk(True, f"{rel} 编译通过")
    except py_compile.PyCompileError as e:
        chk(False, f"{rel} 编译失败", str(e)[:200])

print(f"\n结果：{PASS[0]} 通过 / {len(FAIL)} 失败")
if FAIL:
    for f in FAIL:
        print("  · " + f)
    sys.exit(1)
