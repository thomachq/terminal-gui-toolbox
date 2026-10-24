# -*- coding: utf-8 -*-
"""勾了「连接后自动提权」，终端里就必须真的是 root。

用户原话：
  「勾选了自动提权，但是终端还是没有提权」
  「如果勾了自动提权，那你要执行一下 sudo -i 或 enable 治理之类的命令」

旧实现的两个问题，这里逐条钉住：
  · `sudo_ok()` 只做 `sudo -v` 验密码，**没有真的 `sudo -i`** 进 root；
  · 终端固定走 `run_traced()`，那是**不带任何提权**的通道 —— 只有页面上
    显式调 `run_admin()` 的按钮才带 sudo。所以提示写着「之后的命令都带
    root 权限」，用户敲 `id` 看到的还是自己。

分段：
  1. 默认状态：所有会话 elevated 都是 False；SSHSession 只剩一个 sudo_ok
  2. elevate_root：网络设备走 enable、root 用户直接过、无密码拒绝
  3. elevate_root：类 Unix 开持久 shell 执行 sudo -i（成功 / 失败 / 复用）
  4. term_exec：三档优先级，提权生效就走提权通道
  5. close()：把 root shell 关掉、elevated 复位
  6. sudo_i：前置拒绝条件

不需要真设备，不需要窗口。
"""
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = FAIL = 0
FAILS: list[str] = []

import core.session as S  # noqa: E402
import core.ssh_shell as SSH  # noqa: E402
from core.cli_channel import CLIClient  # noqa: E402


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")
    print(("  ok   " if ok else "  FAIL ") + name
          + (f"  [{extra}]" if extra and not ok else ""))


def sec(t):
    print(f"\n-- {t}")


# ---------------------------------------------------------------- 假对象
class FakeShell:
    """冒充 SSHShellClient：只实现 elevate_root / term_exec 会碰的那几个。"""
    ok = True
    dur = None
    made: list["FakeShell"] = []

    def __init__(self, client=None, **kw):
        self.client = client
        self.closed = False
        self.calls: list[str] = []
        self.pw = None
        FakeShell.made.append(self)

    def alive(self):
        return not self.closed

    def sudo_i(self, pw, timeout=25):
        self.pw = pw
        return FakeShell.ok

    def command(self, cmd, timeout=30):
        if FakeShell.dur:
            raise FakeShell.dur
        self.calls.append(cmd)
        return "0\n"

    def close(self):
        self.closed = True


class FakeCli:
    """冒充交换机持久 CLI。"""
    def __init__(self, enable_ok=True):
        self.enable_ok = enable_ok
        self.enable_pw = None
        self.calls: list[str] = []
        self.closed = False

    def alive(self):
        return not self.closed

    def enable(self, pw="", timeout=15):
        self.enable_pw = pw
        return self.enable_ok

    def command(self, cmd, timeout=30):
        self.calls.append(cmd)
        return "ok\n"

    def close(self):
        self.closed = True


class FakeTransport:
    def is_active(self):
        return True


class FakeClient:
    def get_transport(self):
        return FakeTransport()


class ProbeShell(CLIClient):
    """只测 sudo_i 的前置拒绝，不碰真实 channel。"""
    def __init__(self, alive_=True):
        super().__init__()
        self._alive = alive_

    def alive(self):
        return self._alive

    def _send(self, text):
        pass

    def _drain(self, deadline, quiet=0.6):
        return ""


SSH.SSHShellClient = FakeShell     # elevate_root 里是函数内 import，改这里有效


def mk_ssh(kind="unix", user="ops"):
    s = S.SSHSession()
    s.host_kind = kind
    s.user = user
    s.client = FakeClient()
    return s


# ---------------------------------------------------------------- 1
sec("1. 默认状态")
chk(S.SSHSession().elevated is False, "SSHSession 默认未提权")
chk(S.SSHSession()._root_shell is None, "SSHSession 默认没有 root shell")
chk(S.LocalSession().elevated is False, "LocalSession 默认未提权")
chk(S.TelnetSession().elevated is False, "TelnetSession 默认未提权")

# 曾经 SSHSession 里有两个 sudo_ok（后者覆盖前者，前一个是死代码），
# 读代码的人会以为 SSH 提权只有 sudo 一条路。
defined = sum(1 for c in S.SSHSession.__mro__ if "sudo_ok" in c.__dict__)
chk(defined == 1, "SSHSession 只有一个 sudo_ok（死代码已清）", str(defined))


# ---------------------------------------------------------------- 2
sec("2. elevate_root：网络设备与 root 用户")
net = mk_ssh("network", user="admin")
fake_cli = FakeCli(enable_ok=True)
net._cli = fake_cli
chk(net.elevate_root("en-pass") is True, "交换机：enable 成功返回 True")
chk(net.elevated is True and fake_cli.enable_pw == "en-pass",
    "交换机：elevated 置真且真把密码交给了 enable")
chk(net._root_shell is None, "交换机：不开 root shell（它没有 sudo）")

net2 = mk_ssh("network", user="admin")
net2._cli = FakeCli(enable_ok=False)
chk(net2.elevate_root("wrong") is False, "交换机：enable 失败返回 False")
chk(net2.elevated is False, "交换机：enable 失败不置 elevated")

rt = mk_ssh("unix", user="root")
chk(rt.elevate_root("whatever") is True, "登录用户就是 root：直接算提权")
chk(rt.elevated is True, "root 用户：elevated 置真")
chk(rt._root_shell is None, "root 用户：不用再开 shell")

nopw = mk_ssh("unix", user="ops")
chk(nopw.elevate_root("") is False, "没填密码：返回 False")
chk(nopw.elevated is False, "没填密码：不置 elevated")


# ---------------------------------------------------------------- 3
sec("3. elevate_root：类 Unix 开持久 shell 执行 sudo -i")
FakeShell.made.clear()
FakeShell.ok = True
u = mk_ssh("unix", user="ops")
chk(u.elevate_root("secret") is True, "sudo_i 成功：返回 True")
chk(u.elevated is True, "sudo_i 成功：elevated 置真")
chk(getattr(u, "_root_shell", None) is not None, "sudo_i 成功：留下 root shell")
sh = u._root_shell
chk(sh.pw == "secret", "sudo_i 拿到的正是用户填的密码")
chk(sh.closed is False, "成功的 shell 不关")

# 复用：已经有一条活着的 root shell，不该再开第二条
before = len(FakeShell.made)
chk(u.elevate_root("secret") is True and len(FakeShell.made) == before,
    "已有 root shell：直接复用，不重开")

FakeShell.ok = False
FakeShell.made.clear()
bad = mk_ssh("unix", user="ops")
chk(bad.elevate_root("wrong") is False, "sudo_i 失败：返回 False")
chk(bad.elevated is False, "sudo_i 失败：不置 elevated")
chk(bad._root_shell is None, "sudo_i 失败：不留下坏 shell")
chk(FakeShell.made and FakeShell.made[0].closed is True,
    "sudo_i 失败：把开了一半的 shell 关掉，不泄漏通道")

FakeShell.ok = True


# ---------------------------------------------------------------- 4
sec("4. term_exec：终端命令走哪条通道")
class Recorder:
    """记录被调用了哪个执行方法。"""
    def __init__(self, elevated=False, shell=None):
        self.elevated = elevated
        self._root_shell = shell
        self.used = []

    def run_traced(self, cmd, timeout=30):
        self.used.append("traced")
        return 0, "plain", ""

    def run_admin(self, cmd, timeout=60):
        self.used.append("admin")
        return 0, "root", ""


s1 = Recorder(elevated=True, shell=FakeShell())
rc, out, _ = S.term_exec(s1, "id -u")
chk(s1.used == [] and out == "0\n",
    "有 root shell：直接发给 shell，不经过 run_*", f"{s1.used} {out!r}")
chk(s1._root_shell.calls == ["id -u"], "有 root shell：命令原文送到 shell")

s2 = Recorder(elevated=True)          # 本机类 Unix / 交换机：没有持久 shell
S.term_exec(s2, "id -u")
chk(s2.used == ["admin"], "提权生效但无 shell：走 run_admin", str(s2.used))

s3 = Recorder(elevated=False)
S.term_exec(s3, "id -u")
chk(s3.used == ["traced"], "没提权：仍走 run_traced（与以前一致）", str(s3.used))

FakeShell.dur = RuntimeError("shell 断了")
try:
    rc, out, err = S.term_exec(Recorder(elevated=True, shell=FakeShell()), "ls")
    chk(rc == 1 and out == "" and "shell 断了" in err,
        "shell 出错：如实返回错误，不抛出砸终端")
finally:
    FakeShell.dur = None

need = Recorder(elevated=True)

def _raise(cmd, timeout=60):
    raise S.NeedElevation("要密码", kind="sudo", target="x")

need.run_admin = _raise
rc, out, err = S.term_exec(need, "id -u")
chk(rc == 1 and "要密码" in err, "run_admin 抛 NeedElevation：转成错误文本")


# ---------------------------------------------------------------- 5
sec("5. close() 收尾")
c = mk_ssh("unix", user="ops")
c.elevate_root("secret")
shell = c._root_shell
chk(shell is not None and c.elevated is True, "close 前：shell 在、已提权")
c.close()
chk(shell.closed is True, "close：root shell 被关掉")
chk(c._root_shell is None and c.elevated is False,
    "close：引用清空、elevated 复位（下次连接不会误判已提权）")


# ---------------------------------------------------------------- 6
sec("6. sudo_i 的前置拒绝")
chk(ProbeShell(alive_=True).sudo_i("") is False, "没密码：不进 sudo -i")
chk(ProbeShell(alive_=False).sudo_i("pw") is False, "连接已断：不进 sudo -i")


# ---------------------------------------------------------------- 7
sec("7. sudo_i 的真实交互时序")
class ScriptedCLI(CLIClient):
    """按剧本回应：发出去的命令里出现某个 key，就把对应的回包放到队列。

    这是真正验证 `sudo_i` 的地方 —— 上面那些 FakeShell 只验「有没有调」，
    这一段验「发什么、按什么判成功」。判定标准错了（比如只看提示符有没有
    `#`），用户在 root 也用 `$` 的发行版上就会被骗。
    """
    def __init__(self, replies):
        super().__init__()
        self.replies = list(replies)
        self.sent: list[str] = []
        self.queue: list[bytes] = []

    def alive(self):
        return True

    def _send(self, text):
        self.sent.append(text)
        for item in list(self.replies):
            key, resp = item
            if key in text:
                self.queue.append(resp)
                self.replies.remove(item)
                break

    def _recv_once(self, wait: float = 0.35):
        if not self.queue:
            time.sleep(min(wait, 0.2))
            return ""
        return self._decode(self.queue.pop(0))


ok_shell = ScriptedCLI([
    ("sudo -i", b"[sudo] password for ops: "),
    ("secret", b"root@h:~# "),
    ("id -u", b"id -u\r\n0\r\nroot@h:~# "),
])
chk(ok_shell.sudo_i("secret") is True, "密码对、id -u 是 0：成功")
chk(ok_shell.sent[0] == "sudo -i\r\n", "第一步发的确实是 sudo -i")
chk(any("secret" in x for x in ok_shell.sent),
    "口令真的发出去了（`[sudo] password for ops:` 这种带用户名的提示也得认）",
    str(ok_shell.sent))
chk(any("id -u" in x for x in ok_shell.sent),
    "进完 shell 会跑 id -u 落锤（不靠提示符猜）")

bad_pw = ScriptedCLI([
    ("sudo -i", b"[sudo] password for ops: "),
    ("wrong", b"Sorry, try again.\r\n[sudo] password for ops: "),
])
chk(bad_pw.sudo_i("wrong") is False, "密码错（Sorry, try again）：判失败")

no_root = ScriptedCLI([
    ("sudo -i", b"[sudo] password for ops: "),
    ("secret", b"-bash: /root: Permission denied\r\nops@h:~$ "),
    ("id -u", b"id -u\r\n1000\r\nops@h:~$ "),
])
chk(no_root.sudo_i("secret") is False,
    "密码对了但 id -u 还是 1000：判失败，不假装提权成功")


# ---------------------------------------------------------------- 8
sec("8. 失败必须说清楚原因（不是只有 True/False）")

# 用户在群晖上碰到的就是这类：提权没成，界面只说「未生效」，猜不到为什么。
bad_pw2 = ScriptedCLI([
    ("sudo -i", b"[sudo] password for ops: "),
    ("wrong", b"Sorry, try again.\r\n[sudo] password for ops: "),
])
bad_pw2.sudo_i("wrong")
chk("Sorry" in bad_pw2.last_error,
    "密码错：last_error 里有原文", repr(bad_pw2.last_error))

nosudo = ScriptedCLI([
    ("sudo -i", b"ops is not in the sudoers file.  This incident will be reported.\r\n"),
])
nosudo.sudo_i("secret")
chk("not in the sudoers" in nosudo.last_error,
    "不在 sudoers：原因照抄回显", repr(nosudo.last_error))

still_user = ScriptedCLI([
    ("sudo -i", b"[sudo] password for ops: "),
    ("secret", b"ops@h:~$ "),
    ("id -u", b"id -u\r\n1000\r\nops@h:~$ "),
])
still_user.sudo_i("secret")
chk("1000" in still_user.last_error,
    "sudo -i 后仍不是 root：原因里带上 id -u", repr(still_user.last_error))

ok2 = ScriptedCLI([
    ("sudo -i", b"[sudo] password for ops: "),
    ("secret", b"root@h:~# "),
    ("id -u", b"id -u\r\n0\r\nroot@h:~# "),
])
ok2.sudo_i("secret")
chk(ok2.last_error == "", "成功时不留下陈旧的失败原因", repr(ok2.last_error))


# ---------------------------------------------------------------- 9
sec("9. sudo -i 进不去时退回逐条 sudo")

FakeShell.ok = False
FakeShell.made.clear()
fb = mk_ssh("unix", user="ops")
# 逐条 sudo 能过（exec 通道里 sudo -S 是另一条路）
fb.run = lambda cmd, timeout=30, sudo=False: (0, "0\n", "")
chk(fb.elevate_root("secret") is True, "sudo -i 失败但逐条 sudo 可用：算提权成功")
chk(fb.elevated is True and fb.elev_method == "percmd",
    "提权方式记成 percmd（终端会走 run_admin）", fb.elev_method)
chk(fb._root_shell is None, "percmd 模式不留 root shell")

FakeShell.ok = False
bad2 = mk_ssh("unix", user="ops")
bad2.run = lambda cmd, timeout=30, sudo=False: (1, "", "sudo: a password is required")
chk(bad2.elevate_root("secret") is False, "两条路都不通：判失败")
chk(bool(bad2.elev_msg), "两条路都不通：留下失败原因", repr(bad2.elev_msg))

FakeShell.ok = True
chk(mk_ssh("unix", user="ops").elevate_root("s") is True, "恢复：sudo -i 又能用了")


# ---------------------------------------------------------------- 10
sec("10. verify_root：连接后实测 id -u，不听提权函数的自述")
v1 = mk_ssh("unix", user="ops")
v1.elevated = True
v1.elev_method = "percmd"
v1.sudo_pw = "secret"             # 真实流程里 sudo_ok 一定会留下密码
v1.run = lambda cmd, timeout=30, sudo=False: (0, "0\n", "")
ok, detail = v1.verify_root()
chk(ok is True and detail == "0", "id -u = 0：核实通过", f"{ok} {detail!r}")

v2 = mk_ssh("unix", user="ops")
v2.elevated = True
v2.sudo_pw = "secret"
v2.run = lambda cmd, timeout=30, sudo=False: (0, "1000\n", "")
ok, detail = v2.verify_root()
chk(ok is False and "1000" in detail,
    "id -u = 1000：核实不通过，且说明里带上真实 uid", detail)

v3 = mk_ssh("network", user="admin")
v3.enabled = True
chk(v3.verify_root()[0] is True, "交换机：进了 enable 就算核实通过")


# ---------------------------------------------------------------- 11
sec("11. 口令提示的写法（这一条就是 v1.0.16 提权失败的根因）")
from core.cli_channel import _PASSWORD  # noqa: E402

for sample, want in (
    ("Password: ", True),
    ("[sudo] password for ops: ", True),      # sudo 默认就是这么打印的
    ("password for cloudadmin:", True),
    ("[sudo] password: ", True),
    ("密码：", True),
    ("ops@h:~$ ", False),
    ("SW1#", False),
):
    got = bool(_PASSWORD.search(sample))
    chk(got == want, f"提示 {sample!r} → {'认' if want else '不认'}",
        f"认成了 {got}")


print(f"\n{'=' * 52}")
print(f"通过 {PASS}，失败 {FAIL}")
if FAILS:
    print("失败项：")
    for f in FAILS:
        print("  -", f)
sys.exit(1 if FAIL else 0)
