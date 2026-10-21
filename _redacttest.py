# -*- coding: utf-8 -*-
"""脱敏自测：私钥正文一个字节都不许出现在界面上。

覆盖四个出口：
  1. core.redact 本体（PEM / ta.key / ppk / 内联密码 / 幂等）
  2. 命令日志 LogModel + LogPanel
  3. 实时终端 TerminalWindow.write（所有输出都过这里）
  4. VPN 控制台 _cons_write 与部署输出

判定标准是"打码后的文本里搜不到原文任何一个特征串"，而不是"看着像打码了"。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tkinter as tk          # noqa: E402

from core import redact       # noqa: E402

passed = failed = 0


def chk(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {name}" + (f"  · {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL {name}" + (f"  <- {detail}" if detail else ""))


ED_KEY = """-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACBq1l9mS3crz0PvXKxHnDk1lPqQ7wZc8nWfYk2tVbY0sQAAAJg7Hk3Z
Ox5N2TseTdk7Hk3ZOx5N2TseTdk7Hk3ZOx5N2TseTdk7Hk3ZOx5N2TseTdk7Hk3ZOx5N
-----END OPENSSH PRIVATE KEY-----"""

RSA_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun
VTLw7onLRnrq0/IzW7yWR7QkrmBL7jTKEn5u+qKhbwKfBstIs+bMY2Zkp18gnTxK
-----END RSA PRIVATE KEY-----"""

TA_KEY = """-----BEGIN OpenVPN Static key V1-----
6acef03f62675b4b1bbd03e53b187727
423cea742242106cb2916a8a4c829756
-----END OpenVPN Static key V1-----"""

PPK = """PuTTY-User-Key-File-3: ssh-ed25519
Encryption: none
Comment: imported
Public-Lines: 2
AAAAC3NzaC1lZDI1NTE5AAAAIGrWX2ZLdyvPQ+9crEecOTWU+pDvBlzydZ9i
Ta1VtjSx
Private-Lines: 1
AAAAIGrWX2ZLdyvPQ+9crEecOTWU+pDvBlzydZ9iTa1VtjSxAAAAI
Private-MAC: 1a2b3c4d5e6f
"""

SECRETS = ("b3BlbnNzaC1rZXktdjEAAAAA", "MIIEowIBAAKCAQEAu1SU1LfVLPHC",
           "6acef03f62675b4b1bbd03e53b187727", "AAAAIGrWX2ZLdyvPQ+9crEecOTWU")


def clean(text: str) -> bool:
    return all(s not in text for s in SECRETS)


def main() -> int:
    # ---------------------------------------------------------------- 1
    print("\n== 1. core.redact 本体 ==")
    for name, body in (("OpenSSH 私钥", ED_KEY), ("RSA 私钥", RSA_KEY),
                       ("ta.key", TA_KEY)):
        out = redact.scrub(f"$ cat key\n{body}\n$ done\n")
        chk(f"{name} 正文已打码", clean(out), out.splitlines()[1] if len(out.splitlines()) > 1 else "")
        chk(f"{name} 保留 BEGIN/END 标记", "BEGIN" in out and "END" in out)
        chk(f"{name} 上下文没被吃掉", out.startswith("$ cat key") and out.rstrip().endswith("$ done"))
    out = redact.scrub(PPK)
    # Public-Lines 是公钥，可以留着；Private-Lines 的尾巴才是私钥
    chk("ppk 私钥正文已打码", "Ta1VtjSxAAAAI" not in out, out)
    chk("ppk 公钥行保留", "Public-Lines: 2" in out)
    chk("ppk 头部仍有 Private-Lines", "Private-Lines:" in out, out.splitlines()[-3:])
    chk("ppk Private-MAC 不受影响", "Private-MAC" in out)

    once = redact.scrub(ED_KEY)
    twice = redact.scrub(once)
    chk("scrub 幂等（二次不叠加）", once == twice, f"{once.count('已隐藏')} / {twice.count('已隐藏')}")

    cert = ("-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIU\n"
            "-----END CERTIFICATE-----")
    chk("证书正文不误伤", redact.scrub(cert) == cert)

    chk("空文本安全", redact.scrub("") == "" and redact.scrub(None) is None)
    chk("普通输出原样", redact.scrub("hello 世界\n") == "hello 世界\n")

    cmd = "ssh -p 22 -i k host; ipsec --passphrase 'abc123' ; PASSWD=topsecret run"
    c = redact.scrub_cmd(cmd)
    chk("内联 --passphrase 已遮", "abc123" not in c, c)
    chk("内联 PASSWD= 已遮", "topsecret" not in c, c)
    chk("命令其余部分保留", "ssh -p 22 -i k host" in c, c)

    chk("has_secret 能识别", redact.has_secret(ED_KEY) and not redact.has_secret("hi"))

    # ---------------------------------------------------------------- 2
    print("\n== 2. 命令日志出口 ==")
    from ui.logpanel import LogModel
    m = LogModel()
    m.add("cmd", "$ cat ~/.ssh/id_ed25519\n")
    m.add("out", ED_KEY + "\n")
    t = m.text()
    chk("LogModel 里搜不到私钥正文", clean(t))
    chk("LogModel 有打码回执", "私钥内容已隐藏" in t)

    # ---------------------------------------------------------------- 3
    print("\n== 3. 实时终端出口 ==")
    root = tk.Tk()
    root.withdraw()
    from ui.terminal import TerminalWindow

    class Ctrl:
        def term_ready(self): return True
        def term_prompt(self): return "me@nas:~$ "
        def term_describe(self): return "自测会话"
        def term_run(self, cmd, done): done(0, "", "", 1)
        def term_cwd(self): return "/tmp"
        def term_chdir(self, p): return True, ""
        def term_closed(self, w): pass

    w = TerminalWindow(root, Ctrl())
    w.withdraw()
    w.write("$ cat key\n" + RSA_KEY + "\n", "out")
    w.feed("cat ta.key", 0, TA_KEY, "", 12)
    w.push(PPK, "out")
    body = w.text.get("1.0", "end")
    chk("终端里搜不到私钥正文", clean(body), body[:60].replace("\n", " "))
    chk("终端里没有 ppk 正文", "AAAAIGrWX2ZLdyvPQ+9crEecOTWU" not in body)
    chk("终端仍显示打码回执", body.count("私钥内容已隐藏") >= 2,
        str(body.count("私钥内容已隐藏")))

    # 手敲命令：输入里粘了私钥，历史也不能留原文
    w.v_in.set("echo " + ED_KEY.replace("\n", " "))
    w._submit()
    hist = " ".join(w._hist)
    chk("历史记录里不留私钥正文", "b3BlbnNzaC1rZXktdjEAAAAA" not in hist)

    # ---------------------------------------------------------------- 4
    print("\n== 4. VPN 控制台出口 ==")
    import vpncert_gui as V
    chk("控制台 _SECRET_BLOCK 指向统一规则", V.App._SECRET_BLOCK is redact.PEM)

    class Holder:
        _scrub = V.App._scrub

    h = Holder()
    out = h._scrub("$ cat /var/packages/VPNCenter/etc/openvpn/ta.key\n" + TA_KEY)
    chk("控制台 _scrub 生效", clean(out) and "私钥内容已隐藏" in out)

    # ---------------------------------------------------------------- 5
    print("\n== 5. 本地 API 出口 ==")
    import inspect
    src = inspect.getsource(__import__("api.pool", fromlist=["x"]).SessionPool.exec)
    chk("exec 返回值过 scrub", "scrub(out)" in src and "scrub(err)" in src)

    root.destroy()
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
