# -*- coding: utf-8 -*-
"""「勾了连接后自动提权、sudo 密码留空」这条路径的自检。

用户报的现象：飞牛OS 那条档案勾了「连接后自动提权」，sudo 密码框留空
（界面上写的是「留空表示每次现问」），连上之后终端里 `id` 还是普通用户，
状态栏只有一句「未提权：缺密码」。

这里钉住三件事：
  1. 空密码也要先试**免密 sudo**（群晖默认 NOPASSWD，不该被判成缺密码）；
  2. 免密不行的机器，连上后要**真的弹一次密码框**（"每次现问"必须兑现），
     而且弹框拿到密码后要 `verify_root()` 实测 uid，不能只验密码；
  3. 弹框这条路不能悄悄退回「什么都不做」。

跑法：python _sudopwtest.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PASS, FAIL = 0, 0


def chk(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


# ============================================================ 1. 会话层
class _FakeSSH:
    """只测空密码分支里用到的那几个成员，不碰网络。"""

    def __init__(self, nopasswd: bool):
        self.calls: list[str] = []
        self._nopasswd = nopasswd

    def sudo_n_ok(self) -> bool:
        return self._nopasswd


def test_session_nopasswd() -> None:
    print("\n[1] elevate_root(\"\") —— 空密码先试免密 sudo")
    from core.session import SSHSession

    def make(nopasswd: bool):
        s = object.__new__(SSHSession)
        s.host_kind = "unix"
        s.is_root = False
        s.user = "fnosadmin"
        s.elevated = False
        s.elev_method = ""
        s.elev_msg = ""
        s._root_shell = None
        s._sudo_n = nopasswd          # 预置探测结果，避免真跑命令
        return s

    s = make(True)
    ok = s.elevate_root("")
    chk("免密机器 + 空密码 → 提权成功", ok is True, f"返回 {ok!r}")
    chk("走的是「逐条 sudo」通道（免密没持久 shell）",
        s.elev_method == "percmd", f"elev_method={s.elev_method!r}")
    chk("elevated 置 True（term_exec 才会走 run_admin）", s.elevated is True)
    chk("不留下错误说明", not s.elev_msg, s.elev_msg)

    s2 = make(False)
    ok2 = s2.elevate_root("")
    chk("要密码的机器 + 空密码 → 不提权", ok2 is False)
    chk("说明写「没有可用的 sudo 密码」（上层据此弹框）",
        "没有可用的 sudo 密码" in (s2.elev_msg or ""), s2.elev_msg)
    chk("不把 elevated 置 True", s2.elevated is False)


def test_can_admin_nopasswd() -> None:
    print("\n[2] can_admin() —— 免密机器不能显示「需要 sudo 密码」")
    from core.session import SSHSession

    s = object.__new__(SSHSession)
    s.host_kind = "unix"
    s.is_root = False
    s.user = "admin"
    s.sudo_pw = None
    s._sudo_n = True
    got, why = s.can_admin()
    chk("免密 sudo → 可以改配置", got is True, why)


def test_no_double_pty() -> None:
    print("\n[3] root shell 通道 —— invoke_shell 之后不许再 get_pty")

    from core.ssh_shell import SSHShellClient

    state = {"pty": 0, "blocking": 0}

    class FakeChan:
        closed = False
        active = True

        def get_pty(self, *a, **k):
            state["pty"] += 1
            # paramiko 5.1 上重复 pty-req 会让服务端直接关掉 channel：
            # 真实表现就是 sudo_i 一开口就报「连接已断开，开不了 root shell」。
            self.closed = True
            raise RuntimeError("Channel closed.")

        def settimeout(self, t):
            pass

        def setblocking(self, f):
            state["blocking"] += 1

    class FakeClient:
        def invoke_shell(self, **k):
            return FakeChan()

    sh = SSHShellClient(FakeClient())
    chk("建通道时没有再申请一次 pty", state["pty"] == 0,
        f"get_pty 被调 {state['pty']} 次 —— 会把 channel 弄死")
    chk("建好就是活的（alive True）", sh.alive() is True)
    chk("channel 没有被我们关掉", sh.chan.closed is False)

    src = (HERE / "core" / "ssh_shell.py").read_text(encoding="utf-8")
    chk("源码里没有 self.chan.get_pty( 这种重复申请",
        "self.chan.get_pty(" not in src)


# ============================================================ 4. 重试次数上限
class _Var:
    def __init__(self, v=""):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


class _Status:
    def __init__(self):
        self.last = ("", "")

    def set_state(self, text, kind=""):
        self.last = (text, kind)


def test_retry_limits() -> None:
    """sudo 口令 / 私钥口令都是「最多两次，之后请认真核对」。

    用户的原话：「注意 sudo 和密钥口令都有重复次数限制，超过两次就提示让用户
    认真检查了」。所以两条路都必须（a）能重输、（b）到点会停、（c）停下时给的
    是「去核对什么」，而不是又一句「失败」。
    """
    print("\n[4] 重试上限 —— 两次之后必须停手并说清该核对什么")

    import app as A
    import ui.forms as forms
    from tkinter import messagebox

    asked: list[str] = []
    errors: list[str] = []
    orig_open, orig_err = forms.FormDialog.open, messagebox.showerror
    forms.FormDialog.open = classmethod(
        lambda cls, parent, title, fields, **k: (asked.append(title),
                                                 {"pw": "again-wrong", "save": True})[1])
    messagebox.showerror = lambda title, body, **k: errors.append(f"{title}|{body}")

    class _Store:
        profiles: list = []

    class _Fake:
        profiles = _Store()
        v_keypw = _Var("old-wrong")
        v_prof = _Var("飞牛OS")
        status = _Status()

        def __init__(self):
            self.saved, self.connects, self.logs = 0, [], []

        def _autosave_creds(self):
            self.saved += 1

        def _log_raw(self, *_a):
            self.logs.append(_a)

        def do_connect(self, keypw_try=0):
            self.connects.append(keypw_try)

    class _Spec:
        key_path = r"D:\Tools\putty-64bit\chromas_openssh_ppk"
        key_passphrase = "old-wrong"

    try:
        # tries=0：第一次就问，问完带 keypw_try=1 重连
        f = _Fake()
        A.ToolboxApp._ask_keypw_then_retry(f, _Spec(), 0, "解不开")
        chk("私钥口令：tries=0 会弹框", len(asked) == 1, f"弹了 {len(asked)} 次")
        chk("私钥口令：填完按 tries+1 重连", f.connects == [1], f"实际 {f.connects}")
        chk("私钥口令：勾了保存就落盘", f.saved == 1, f"实际 {f.saved}")

        # tries=1：还剩最后一次，仍然问
        asked.clear()
        f = _Fake()
        A.ToolboxApp._ask_keypw_then_retry(f, _Spec(), 1, "解不开")
        chk("私钥口令：tries=1 仍会弹一次（第二次机会）", len(asked) == 1)
        chk("私钥口令：第二次填完带 tries=2 回去", f.connects == [2])

        # tries=2：停手，讲清去哪儿核对
        asked.clear(); errors.clear()
        f = _Fake()
        A.ToolboxApp._ask_keypw_then_retry(f, _Spec(), 2, "解不开")
        chk("私钥口令：tries=2 不再弹框", not asked, f"还是弹了 {asked}")
        chk("私钥口令：tries=2 会明确报错停下", len(errors) == 1)
        chk("私钥口令：停下时提到 MaxAuthTries / fail2ban",
            "MaxAuthTries" in errors[0] and "fail2ban" in errors[0], errors[:1])
        chk("私钥口令：停下时告诉人去哪儿改（主机管理）",
            "主机管理" in errors[0])
        chk("私钥口令：停下时不再发起连接", not f.connects)
        chk("私钥口令：停下时状态栏写清原因",
            "私钥口令没通过" in f.status.last[0], f.status.last)

        # sudo：源码里必须是「attempt < 1」—— 即 1 次初始 + 1 次重问 = 两次封顶
        src = (HERE / "app.py").read_text(encoding="utf-8")
        chk("sudo 弹框：上限写在源码里（attempt < 1）",
            re.search(r"if attempt < 1 and _sudo_pw_maybe_wrong\(detail\)", src) is not None)
        chk("sudo 弹框：停下时说的是「两次都没通过」",
            "sudo 密码两次都没通过" in src)
        chk("sudo 弹框：停下时点明重复试错会被记账",
            "passwd_tries" in src and "faillock" in src)
        chk("sudo 弹框：自带「存进档案」勾选",
            re.search(r'"key": "save", "kind": "check"[\s\S]{0,120}?存进档案', src)
            is not None)
        chk("私钥弹框：自带「存进档案」勾选",
            re.search(r'"key": "save", "kind": "check"[\s\S]{0,160}?存进档案',
                      (HERE / "app.py").read_text(encoding="utf-8")) is not None)
        # 保存档案时口令错 → 重开表单，别把刚填的东西一起丢
        hsrc = (HERE / "pages" / "hosts_page.py").read_text(encoding="utf-8")
        chk("保存表单：口令错会重开表单（不丢其它字段）",
            re.search(r"base = merged\s*#", hsrc) is not None)
        chk("保存表单：重开也有上限（两次）",
            re.search(r"if kpw_try >= 2:", hsrc) is not None)
    finally:
        forms.FormDialog.open = orig_open
        messagebox.showerror = orig_err


# ============================================================ 5. 存档密码被拒
def test_stored_pw_rejected() -> None:
    """档案里存着 sudo 密码、但机器不认 —— 必须弹框重问，不能只写一行日志。

    用户第二次报的现象：飞牛OS 勾了自动提权，仍然没有 root，而且**窗口里
    从没出现过密码框**。根因是档案里那条 `sudo_password`（就是他填的登录密码）
    被飞牛的 sudo 拒了（`sudo: 1 incorrect password attempt`），而代码走的是
    「pw 非空 → 试一次 → 失败就 elev='failed'」—— 一句话都不问就放弃了。
    群晖之所以"可以"，是因为它 administrators 是 NOPASSWD，走的是空密码分支。
    """
    print("\n[5] 档案密码被拒 —— 必须重问，不能默默放弃")

    import app as A

    cases = [
        ("密码不对（sudo 原文：Sorry, try again.）", True, "标准密码错"),
        ("sorry, try again.", True, "小写原文"),
        ("1 incorrect password attempt", True, "sudo 计数原文"),
        ("这个账号不在 sudoers 里（要先授权才能提权）", False, "不在 sudoers 换个密码也没用"),
        ("sudo: command not found", False, "没装 sudo"),
        ("找不到 sudo", False, "没装 sudo（中文）"),
        ("连接已断开，开不了 root shell", False, "通道死了，问密码没意义"),
        ("没有可用的 sudo 密码", False, "空密码分支自己的文案，别绕回来"),
        ("", False, "空原因"),
    ]
    for msg, want, tag in cases:
        got = A._sudo_pw_maybe_wrong(msg)
        chk(f"{tag} → {'重问' if want else '不重问'}", got is want, f"实际 {got}（{msg!r}）")

    # sudo_ok 失败时不许把错密码留在会话上：can_admin() 只认「有没有密码」，
    # 留着会让页面上的 Docker / 防火墙拿错密码去 sudo，报出来的错完全对不上。
    from core.session import SSHSession
    s = object.__new__(SSHSession)
    s.sudo_pw = "old-right-pw"
    s.elevated = True
    s.elev_method = "sudo-i"
    s.elev_msg = ""

    class _S:
        def run(self, cmd, sudo=False, timeout=20):
            return 1, "", "Sorry, try again.\n"

    orig = SSHSession.run
    SSHSession.run = lambda self, cmd, timeout=30, sudo=False: _S().run(cmd, sudo)
    try:
        ok = s.sudo_ok("wrong-pw")
    finally:
        SSHSession.run = orig
    chk("错密码 → sudo_ok 返回 False", ok is False)
    chk("错密码没有被留在会话上", s.sudo_pw == "old-right-pw", f"现在留下的是 {s.sudo_pw!r}")
    chk("失败后不再自称已提权", s.elevated is False)
    chk("原因是「密码不对」而不是「返回非 0」",
        "密码不对" in (s.elev_msg or ""), f"实际 {s.elev_msg!r}")

    # sudo_ok 成功后必须把密码记住，否则页面操作没法用它
    s2 = object.__new__(SSHSession)
    s2.sudo_pw = ""; s2.elevated = False; s2.elev_method = ""; s2.elev_msg = ""
    SSHSession.run = lambda self, cmd, timeout=30, sudo=False: (0, "0\n", "")
    try:
        ok2 = s2.sudo_ok("right-pw")
    finally:
        SSHSession.run = orig
    chk("对密码 → sudo_ok 返回 True", ok2 is True)
    chk("对密码会被记住", s2.sudo_pw == "right-pw")
    chk("对密码 → elev_method=percmd", s2.elev_method == "percmd")


# ============================================================ 5. 源码结构
def test_source() -> None:
    print("\n[6] 源码结构 —— 弹框这条路必须真的存在")
    app_src = (HERE / "app.py").read_text(encoding="utf-8")
    ses_src = (HERE / "core" / "session.py").read_text(encoding="utf-8")

    chk("app.py 有「ask」这个提权状态分支",
        'elev == "ask"' in app_src)
    chk("旧的无动作分支 elev = \"nopw\" 已移除",
        'elev = "nopw"' not in app_src,
        "还有人在这里默默放弃")
    chk("定义了 _ask_sudo_after_connect",
        "def _ask_sudo_after_connect(" in app_src)
    chk("连上之后会主动调它（after 调度到主线程）",
        re.search(r"self\.after\(\s*\d+\s*,\s*lambda:\s*self\._safe_ask_sudo",
                  app_src) is not None)
    chk("after 外壳吞掉「窗口已关」的 TclError",
        re.search(r"def _safe_ask_sudo[\s\S]{0,300}?except tk\.TclError",
                  app_src) is not None)
    chk("空密码也调 elevate_root（先试免密）",
        re.search(r"elif not net_dev:\s*\n(?:\s*#[^\n]*\n)*\s*try:\s*\n\s*okadm = bool\(s\.elevate_root\(\"\"\)\)",
                  app_src) is not None,
        "没找到 elevate_root(\"\") 的空密码调用")
    chk("弹框里拿到密码后走 elevate_root(pw)",
        re.search(r"def _ask_sudo_after_connect[\s\S]{0,3000}?s\.elevate_root\(pw\)",
                  app_src) is not None)
    chk("提权后实测 uid（verify_root），不是只验密码",
        re.search(r"def _ask_sudo_after_connect[\s\S]{0,3500}?s\.verify_root\(\)",
                  app_src) is not None)
    chk("弹框取消时有明确状态，不是静默",
        "取消了密码输入" in app_src)
    chk("弹框里勾了「保存」会写进档案（或主表单勾过保存）",
        re.search(r'if data\.get\("save"\) or self\.v_save_sudo\.get\(\):', app_src)
        is not None)
    chk("session.py 空密码分支先探 sudo -n",
        re.search(r"if not pw:[\s\S]{0,700}?if self\.sudo_n_ok\(\):",
                  ses_src) is not None)
    chk("交换机不试空密码（避免 enable 试错锁号）",
        "交换机不试空密码" in app_src)

    # ---- 档存密码被拒 → 重问（这一轮的第二个真 bug）
    chk("连接流程里用 _sudo_pw_maybe_wrong 判断该不该重问",
        app_src.count("_sudo_pw_maybe_wrong(") >= 2,
        f"只出现 {app_src.count('_sudo_pw_maybe_wrong(')} 次 —— 两条失败分支都要判")
    chk("失败原因会带进弹框（elev_why 传给 _safe_ask_sudo）",
        re.search(r"self\._safe_ask_sudo\(s,\s*slot,\s*elev_why\)", app_src) is not None)
    chk("弹框抬头区分「没存密码」与「存的被拒」",
        '"sudo 密码没通过" if retry else "需要 sudo 密码"' in app_src)
    chk("重问有次数上限（别把账号试锁）",
        re.search(r"if attempt < 1 and _sudo_pw_maybe_wrong\(detail\)", app_src) is not None)
    chk("提权失败提示会点出「可能是 root 密码」",
        "root 密码" in app_src)


def main() -> int:
    print("=" * 66)
    print("勾了自动提权 + 密码留空 → 连上后必须真的问一次")
    print("=" * 66)
    test_session_nopasswd()
    test_can_admin_nopasswd()
    test_no_double_pty()
    test_retry_limits()
    test_stored_pw_rejected()
    test_source()
    print("\n" + "=" * 66)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 66)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
