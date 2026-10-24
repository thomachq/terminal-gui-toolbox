# -*- coding: utf-8 -*-
"""添加/编辑主机表单自测（v1.0.9 起重写）。

用户这一轮的原话：
  「这里选了私钥就不要有登录密码，而是私钥密码了」
  「保存 sudo 密码的勾选没了」
  「你要探测目标的类型啊，Windows 的话就不是 sudo 密码了」

所以这一版按「认证二选一 + 口令各带保存勾 + 提权跟着目标系统走」验：

  1. 本机协议瘦身：远程字段全收
  2. SSH 认证二选一：私钥 ↔ 私钥密码；登录密码；Telnet 没有认证选择
  3. 目标系统决定提权：Linux 有 sudo 密码，Windows 没有，交换机是 enable
  4. 每个口令字段都带独立「保存」勾
  5. 真开一次表单：切协议 / 切认证 / 切目标系统时行会实时增减
  6. merge_host_form：口令三态（填了存 / 填了不存 / 留空保留 / 取消勾选删掉）
  7. 落盘：sudo / shell / ostype 能存能读

数据目录在临时目录，不联网。
"""
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.argv = [sys.argv[0], "--no-probe"]
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = FAIL = 0
FAILS: list[str] = []
TD = tempfile.mkdtemp(prefix="tbhost6_")

import app as appmod  # noqa: E402
from core import hosts as H  # noqa: E402
from core import session as S  # noqa: E402

appmod.APP_DIR = Path(TD)
appmod.DATA_DIR = Path(TD) / "data"
appmod.DATA_DIR.mkdir(parents=True, exist_ok=True)

import tkinter  # noqa: E402
from pages.hosts_page import host_fields, merge_host_form  # noqa: E402
from ui import forms  # noqa: E402

REMOTE_KEYS = ("host", "port", "user", "auth", "key_path", "password",
               "cred_id", "proxy_id", "ostype")


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")


def eq(a, b, name):
    chk(a == b, name, f"期望 {b!r} 实得 {a!r}")


def sec(t):
    print(f"\n-- {t}")


def pump(win, seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        try:
            win.update()
        except tkinter.TclError:
            return
        time.sleep(0.01)


def visible(fields, extra=None, is_win=False) -> dict[str, bool]:
    """按 show_if 的规则算一遍「这个字段现在显不显示」。

    支持三种 show_if：缺省（永远显示）、dict（逐项 AND）、谓词（lambda）。
    谓词式和 FormDialog 里一样，拿的是整份表单值的字典。
    """
    vals = {"proto": "ssh", "auth": "key", "sudo": False, "ostype": ""}
    vals.update(extra or {})
    out = {}
    for f in fields:
        spec = f.get("show_if")
        if not spec:
            out[f["key"]] = True
        elif callable(spec):
            try:
                out[f["key"]] = bool(spec(vals))
            except Exception:  # noqa: BLE001
                out[f["key"]] = True
        else:
            ok = True
            for k, allow in spec.items():
                if not isinstance(allow, (list, tuple, set)):
                    allow = (allow,)
                v = vals.get(k)
                if not any(str(v).strip().lower() == str(a).strip().lower()
                           for a in allow):
                    ok = False
                    break
            out[f["key"]] = ok
    return out


def keys_of(fields) -> list[str]:
    return [f["key"] for f in fields]


# ================================================================== 1. 本机
sec("1. 本机协议要瘦身")

fs_local = host_fields({"proto": "local", "name": "本机"}, is_win=False)
vis = visible(fs_local, {"proto": "local"}, is_win=False)
eq([k for k in REMOTE_KEYS if vis.get(k)], [], "切到本机后，远程字段一个都不显示")
for k in ("name", "proto", "group", "tags"):
    chk(vis.get(k) is True, f"「{k}」还在")
chk(vis.get("sudo") is True, "本机 Linux 有提权勾选")
chk(vis.get("elevate") is False, "本机 Linux 没有 UAC 勾选")
chk(vis.get("shell") is True, "本机有 Shell 下拉")
chk(vis.get("win_note") is False, "本机 Linux 没有 Windows 提示行")

vis_win = visible(host_fields({"proto": "local"}, is_win=True), {"proto": "local"},
                  is_win=True)
chk(vis_win.get("sudo") is False, "本机 Windows 没有 sudo")
chk(vis_win.get("elevate") is True, "本机 Windows 有 UAC 勾选")
chk(vis_win.get("sudo_pass") is False, "本机 Windows 没有 sudo 密码框")

# ================================================================== 2. 认证
sec("2. 选了私钥就不该有登录密码")

fs_ssh = host_fields({"proto": "ssh"}, is_win=False)
v_key = visible(fs_ssh, {"proto": "ssh", "auth": "key"})
chk(v_key.get("key_path") is True, "私钥认证：私钥路径在")
chk(v_key.get("key_passphrase") is True, "私钥认证：有「私钥密码」")
chk(v_key.get("password") is False, "私钥认证：没有「登录密码」")

v_pw = visible(fs_ssh, {"proto": "ssh", "auth": "password"})
chk(v_pw.get("key_path") is False, "密码认证：私钥路径收起来")
chk(v_pw.get("key_passphrase") is False, "密码认证：私钥密码收起来")
chk(v_pw.get("password") is True, "密码认证：有登录密码")

v_tel = visible(fs_ssh, {"proto": "telnet"})
chk(v_tel.get("auth") is False, "Telnet 没有「认证」下拉（它只有密码）")
chk(v_tel.get("password") is True, "Telnet 有登录密码")
chk(v_tel.get("key_path") is False, "Telnet 没有私钥路径")

# ================================================================== 3. 目标系统
sec("3. 提权跟着目标系统走，不跟本机平台走")

v_lin = visible(fs_ssh, {"proto": "ssh", "ostype": "linux", "sudo": True})
chk(v_lin.get("sudo") is True, "SSH + Linux：有「连接后自动提权」")
chk(v_lin.get("sudo_pass") is True, "SSH + Linux：有 sudo 密码")
chk(v_lin.get("enable_pass") is False, "SSH + Linux：没有 enable 密码")
chk(v_lin.get("win_note") is False, "SSH + Linux：没有 Windows 提示")

v_win = visible(fs_ssh, {"proto": "ssh", "ostype": "windows", "sudo": True})
chk(v_win.get("sudo") is False, "SSH + Windows：没有提权勾选")
chk(v_win.get("sudo_pass") is False, "SSH + Windows：**没有 sudo 密码**")
chk(v_win.get("win_note") is True, "SSH + Windows：给一句「没有 sudo」的说明")
chk(v_win.get("elevate") is False, "SSH + Windows：UAC 勾选不给（那是本机的事）")

v_net = visible(fs_ssh, {"proto": "telnet", "ostype": "network", "sudo": True})
chk(v_net.get("sudo") is True, "交换机：有「连接后自动提权」")
chk(v_net.get("enable_pass") is True, "交换机：有 enable 密码")
chk(v_net.get("sudo_pass") is False, "交换机：没有 sudo 密码")

# 不选目标系统时按协议猜：SSH → Linux，Telnet → 交换机
v_auto_ssh = visible(fs_ssh, {"proto": "ssh", "ostype": "", "sudo": True})
chk(v_auto_ssh.get("sudo_pass") is True, "自动 + SSH：默认按 Linux 问 sudo")
v_auto_tel = visible(fs_ssh, {"proto": "telnet", "ostype": "", "sudo": True})
chk(v_auto_tel.get("enable_pass") is True, "自动 + Telnet：默认按交换机问 enable")

# 不止 Windows / Linux —— 用户要能自己选 Mac OS、FreeBSD 这些，
# 而且它们都是类 Unix，提权问的是 sudo，不是 enable 也不是 UAC
v_mac = visible(fs_ssh, {"proto": "ssh", "ostype": "macos", "sudo": True})
chk(v_mac.get("sudo") is True, "SSH + Mac OS：有「连接后自动提权」")
chk(v_mac.get("sudo_pass") is True, "SSH + Mac OS：问的是 sudo 密码")
chk(v_mac.get("enable_pass") is False, "SSH + Mac OS：不是 enable 密码")
chk(v_mac.get("win_note") is False, "SSH + Mac OS：没有 Windows 提示")

v_bsd = visible(fs_ssh, {"proto": "ssh", "ostype": "freebsd", "sudo": True})
chk(v_bsd.get("sudo") is True, "SSH + FreeBSD：有「连接后自动提权」")
chk(v_bsd.get("sudo_pass") is True, "SSH + FreeBSD：问的是 sudo 密码")
chk(v_bsd.get("enable_pass") is False, "SSH + FreeBSD：不是 enable 密码")

opts = dict(next(f for f in fs_ssh if f["key"] == "ostype")["options"])
for k, lab in (("", "自动探测（连上就认）"), ("linux", "Linux / Unix"),
               ("macos", "Mac OS"), ("freebsd", "FreeBSD"),
               ("windows", "Windows"), ("network", "交换机 / 路由器")):
    eq(opts.get(k), lab, f"目标系统下拉里有「{lab}」")

eq(H.assume_os({"proto": "ssh", "ostype": "macos"}, False), "macos",
   "assume_os 认得 macOS（不被当成 Linux）")
eq(H.assume_os({"proto": "ssh", "ostype": "freebsd"}, False), "freebsd",
   "assume_os 认得 FreeBSD")
eq(H.assume_os({"proto": "ssh", "ostype": "darwin"}, False), "macos",
   "uname 给的 darwin 归到 macOS")
chk(H.is_unix("linux") and H.is_unix("macos") and H.is_unix("freebsd"),
    "三种类 Unix 都算 sudo 一侧")
chk(not H.is_unix("windows") and not H.is_unix("network"),
    "Windows / 交换机不算类 Unix")
eq(H.os_label({"proto": "ssh", "ostype": "macos"}), "Mac OS",
   "清单「系统」列显示 Mac OS")
eq(H.os_label({"proto": "ssh", "ostype": "freebsd"}), "FreeBSD",
   "清单「系统」列显示 FreeBSD")

# ================================================================== 4. 保存勾
sec("4. 每个口令框都有自己的「保存」")

for k in ("password", "key_passphrase", "sudo_pass", "enable_pass"):
    f = next((x for x in fs_ssh if x["key"] == k), None)
    chk(f is not None, f"字段表里有 {k}")
    chk(bool(f and f.get("save")), f"「{k}」带 save 标记（表单会长出一个保存勾）")
chk(not next(x for x in fs_ssh if x["key"] == "name").get("save"),
    "普通文本框不该长出保存勾")

# ================================================================== 5. 真表单
sec("5. 真开一次表单")

win = appmod.ToolboxApp()
win.geometry("+20+20")
win.deiconify()
pump(win, 0.9)

dlg = forms.FormDialog(win, "添加主机", host_fields({"proto": "ssh"}, is_win=False),
                       {"proto": "ssh", "name": "远程表单"}, width=580)
pump(win, 0.4)
chk(dlg.winfo_exists(), "表单开起来了")
chk("password__save" in dlg._getters, "口令框右侧真的建了保存勾",
    sorted(k for k in dlg._getters if k.endswith("__save")))


def mapped(key: str) -> bool:
    ws = dlg._row_widgets.get(key) or []
    return bool(ws) and bool(ws[0].winfo_ismapped())


chk(mapped("host"), "SSH 时地址行是显示的")
chk(mapped("key_passphrase"), "私钥认证：私钥密码行是显示的")
chk(not mapped("password"), "私钥认证：登录密码行收起来了")
h0 = dlg.winfo_height()

dlg._vars["auth"].set("登录密码")
pump(win, 0.3)
chk(mapped("password"), "切成登录密码：登录密码行露出来")
chk(not mapped("key_passphrase"), "切成登录密码：私钥密码行收起来")
chk(not mapped("key_path"), "切成登录密码：私钥路径也收起来")

dlg._vars["ostype"].set("Windows")
pump(win, 0.3)
chk(not mapped("sudo"), "目标选 Windows：提权勾选消失")
chk(not mapped("sudo_pass"), "目标选 Windows：sudo 密码消失")
chk(mapped("win_note"), "目标选 Windows：出现「没有 sudo」的说明")

dlg._vars["ostype"].set("Mac OS")
pump(win, 0.3)
chk(mapped("sudo"), "目标选 Mac OS：提权勾选在（macOS 也要 sudo）")
chk(not mapped("win_note"), "目标选 Mac OS：没有 Windows 那句提示")
chk(dlg._vars["ostype"].get() == "Mac OS", "下拉里能挑到 Mac OS",
    dlg._vars["ostype"].get())

dlg._vars["ostype"].set("FreeBSD")
pump(win, 0.3)
chk(mapped("sudo"), "目标选 FreeBSD：提权勾选在（FreeBSD 也要 sudo）")

dlg._vars["ostype"].set("Linux / Unix")
pump(win, 0.3)
chk(mapped("sudo"), "目标选 Linux：提权勾选回来")
if dlg._checks.get("sudo"):
    dlg._checks["sudo"].set(True)
pump(win, 0.2)
chk(mapped("sudo_pass"), "勾了提权：sudo 密码露出来")

dlg._vars["proto"].set("本机")
pump(win, 0.3)
chk(not mapped("host"), "选本机后地址行立刻收起来")
chk(not mapped("key_path"), "本机：私钥路径收起来")
chk(mapped("sudo") or mapped("elevate"), "本机提权那一行露出来")

# 必填校验不该管隐藏字段：本机模式下 host 是空的，不该报「地址不能为空」
dlg._widgets["name"].delete(0, "end")
dlg._ok()
pump(win, 0.2)
err = str(dlg.lb_err.cget("text"))
chk("名称" in err, "名称空着会被拦（唯一必填项）", err)
chk("地址" not in err, "隐藏的地址字段不参与必填校验", err)
dlg._widgets["name"].insert(0, "本机表单")
dlg._ok()
pump(win, 0.2)
chk(dlg.result is not None, "本机档案能保存（地址留空也算合法）")
if dlg.result:
    eq(dlg.result.get("proto"), "local", "存下来的是本机协议")
try:
    dlg.destroy()
except tkinter.TclError:
    pass

# ================================================================== 6. 口令三态
sec("6. 口令三态：填了存 / 填了不存 / 留空保留 / 取消勾选删掉")

base = {"name": "h1", "host": "10.0.0.5", "port": "22", "proto": "ssh",
        "auth": "password", "password": "old-pw", "key_passphrase": "",
        "sudo_password": "old-sudo", "remember": True}

m = merge_host_form({"name": "h1", "host": "10.0.0.5", "port": "22",
                     "proto": "ssh", "auth": "password",
                     "password": "new-pw", "password__save": True,
                     "sudo_pass": "new-sudo", "sudo_pass__save": True,
                     "sudo": True}, base=base, is_win=False)
eq(m.get("password"), "new-pw", "填了 + 勾保存 → 落盘")
eq(m.get("sudo_password"), "new-sudo", "sudo 密码填了 + 勾保存 → 落盘")

m = merge_host_form({"name": "h1", "proto": "ssh", "auth": "password",
                     "password": "typed-only", "password__save": False},
                    base=base, is_win=False)
eq(m.get("password"), "", "填了但没勾保存 → 不落盘（只当次用）")

m = merge_host_form({"name": "h1", "proto": "ssh", "auth": "password",
                     "password": "", "password__save": True,
                     "sudo_pass": "", "sudo_pass__save": True},
                    base=base, is_win=False)
eq(m.get("password"), "old-pw", "留空 + 勾着 → 保留原来的密码")
eq(m.get("sudo_password"), "old-sudo", "留空 + 勾着 → 保留原来的 sudo 密码")

m = merge_host_form({"name": "h1", "proto": "ssh", "auth": "password",
                     "password": "", "password__save": False,
                     "sudo_pass": "", "sudo_pass__save": False},
                    base=base, is_win=False)
eq(m.get("password"), "", "取消勾选 → 把存的密码删掉")
eq(m.get("sudo_password"), "", "取消勾选 → 把存的 sudo 密码删掉")
eq(m.get("remember"), False, "口令全清掉后 remember 也跟着关")

# 提权归一：Windows 目标即使勾了 sudo 也不留
m = merge_host_form({"name": "h2", "host": "10.0.0.6", "port": "22",
                     "proto": "ssh", "ostype": "windows", "sudo": True,
                     "elevate": True}, is_win=False)
eq(m.get("sudo"), False, "目标 Windows：sudo 勾不落盘")
eq(m.get("elevate"), False, "远程 Windows：本机 UAC 勾也不落盘")

m = merge_host_form({"name": "h3", "host": "10.0.0.7", "port": "23",
                     "proto": "telnet", "ostype": "network", "sudo": True,
                     "enable_pass": "en-pw", "enable_pass__save": True},
                    is_win=False)
eq(m.get("sudo"), True, "交换机：提权勾落盘")
eq(m.get("sudo_password"), "en-pw", "交换机：enable 密码存进 sudo_password")
eq(m.get("shell"), "", "远程档案不带本机 shell")

# macOS / FreeBSD 也是 sudo 一侧，别被「只认 linux」的写法吃掉
for _os in ("macos", "freebsd"):
    m = merge_host_form({"name": f"h-{_os}", "host": "10.0.0.8", "port": "22",
                         "proto": "ssh", "ostype": _os, "sudo": True,
                         "sudo_pass": "s-pw", "sudo_pass__save": True},
                        is_win=False)
    eq(m.get("ostype"), _os, f"{_os}：目标系统原样存下")
    eq(m.get("sudo"), True, f"{_os}：提权勾留得住")
    eq(m.get("sudo_password"), "s-pw", f"{_os}：sudo 密码存得住")

m = merge_host_form({"name": "h4", "proto": "local", "sudo": True,
                     "shell": "cmd"}, is_win=False)
eq(m.get("shell"), "cmd", "本机档案保留 shell")
eq(m.get("elevate"), False, "本机 Linux 不留 UAC")

# ================================================================== 7. 落盘
sec("7. 能存能读")

store = win.profiles
rec = H.blank_record("local")
rec.update({"name": "本机测试", "sudo": True, "elevate": False, "shell": "cmd",
            "sudo_password": "pw-x", "remember": True, "ostype": "linux",
            "os_name": "Debian 12"})
saved = store.upsert(rec)
chk(saved.get("sudo") is True, "sudo 存成布尔", saved.get("sudo"))
eq(saved.get("shell"), "cmd", "shell 存下来了")
eq(saved.get("ostype"), "linux", "目标系统存下来了")
again = store.get("本机测试") or {}
chk(again.get("sudo") is True and again.get("shell") == "cmd",
    "重读还在", f"{again.get('sudo')} / {again.get('shell')}")
chk(isinstance(again.get("sudo"), bool), "读回来还是布尔，不是字符串")
eq(H.os_label(again), "Debian 12", "清单「系统」列优先显示探测到的版本名")
eq(H.os_label({"proto": "ssh", "ostype": "windows"}), "Windows",
   "没版本名时显示系统类别")
eq(H.os_label({"proto": "ssh"}), "自动", "都没认出来时写「自动」")
eq(H.os_label({"proto": "local"}, "Windows（本机）"), "Windows（本机）",
   "本机记录直接给本机系统")

rec2 = H.blank_record("ssh")
rec2.update({"name": "远程测试", "host": "10.0.0.9", "sudo": True,
             "shell": "cmd", "ostype": "network"})
merged = H.record_from_form({"proto": "ssh"}, base=rec2)
eq(merged.get("proto"), "ssh", "协议切回 SSH")
eq(merged.get("ostype"), "network", "表单没动 ostype 时不乱改")
merged = H.record_from_form({"proto": "ssh", "ostype": ""},
                            base=H.blank_record("ssh"))
eq(merged.get("ostype"), "", "新档案默认「自动探测」")

# 外壳真的进了本地会话
s = S.open_session(S.ConnSpec(proto="local", shell="cmd"))
eq(getattr(s, "shell", ""), "cmd", "open_session 把 shell 传给了 LocalSession")
if sys.platform == "win32":
    argv = s.argv_for("dir c:\\")
    eq(argv[:2], ["cmd", "/c"], f"cmd 外壳用 cmd /c 起进程 {argv[:3]}")
    eq(s.ps_native, False, "cmd 模式下不再按 PowerShell 拼引号")
    s.set_shell("powershell")
    argv = s.argv_for("Get-ChildItem")
    eq(argv[0], "powershell", "powershell 外壳用 powershell -Command")
    s.set_shell("")
    eq(s.shell, "", "空值 = 自动")
    eq(s.ps_native, True, "自动模式回到 PowerShell 口径")
else:
    eq(s.argv_for("ls")[:2], ["/bin/sh", "-c"], "类 Unix 走 /bin/sh -c")

# ================================================================== 8. 编辑不改名
sec("8. 编辑主机：名字没改也能存（不是新建一条）")

import pages.hosts_page as HP  # noqa: E402


class _MB:
    """换个不弹窗的假 messagebox，把警告收起来断言。"""

    def __init__(self) -> None:
        self.warn: list[str] = []

    def showwarning(self, title, msg, **kw):
        self.warn.append(str(msg))

    def askyesno(self, *a, **kw):
        return True


_orig_mb = HP.messagebox
_orig_open = forms.FormDialog.open
mb = _MB()
HP.messagebox = mb

# 用户遇到的场景：档案里就这一台，点 ✎ / 点「编辑」，什么都不改直接保存，
# 结果弹「名称「DESKTOP-HBM6KCB」已被占用」—— 查重把自己算成了别人。
win.profiles.profiles.clear()
win.profiles.upsert({"name": "other", "host": "10.0.0.2", "port": "22",
                     "proto": "ssh", "auth": "key"})
win.profiles.upsert({"name": "DESKTOP-HBM6KCB", "host": "10.0.0.1",
                     "port": "22", "proto": "ssh", "auth": "key",
                     "note": "改之前"})
eq(len(win.profiles.profiles), 2, "备好两条档案")

forms.FormDialog.open = staticmethod(lambda *a, **k: {
    "name": "DESKTOP-HBM6KCB", "host": "10.0.0.1", "port": "22",
    "proto": "ssh", "auth": "key", "note": "改之后"})
mb.warn.clear()
win.host_edit(dict(win.profiles.get("DESKTOP-HBM6KCB")))   # 传副本 = 点 ✎ 那条路
eq(mb.warn, [], "编辑不改名：不再弹「已被占用」")
eq(len(win.profiles.profiles), 2, "编辑不改名：没有多出一条")
eq(str((win.profiles.get("DESKTOP-HBM6KCB") or {}).get("note")), "改之后",
   "编辑不改名：改动真的存下去了")

# 改成另一条已存在的名字 → 仍然要拦
forms.FormDialog.open = staticmethod(lambda *a, **k: {
    "name": "other", "host": "10.0.0.1", "port": "22",
    "proto": "ssh", "auth": "key", "note": "想抢名字"})
mb.warn.clear()
win.host_edit(dict(win.profiles.get("DESKTOP-HBM6KCB")))
chk(any("已被占用" in w for w in mb.warn), "编辑时撞别人的名字：拦住", mb.warn)
eq(len(win.profiles.profiles), 2, "撞名被拦后档案没动")

# 真改名 → 旧名删掉、新名加上，不留孤儿
forms.FormDialog.open = staticmethod(lambda *a, **k: {
    "name": "改过名的", "host": "10.0.0.1", "port": "22",
    "proto": "ssh", "auth": "key", "note": "改名"})
mb.warn.clear()
win.host_edit(dict(win.profiles.get("DESKTOP-HBM6KCB")))
eq(sorted(str(p.get("name")) for p in win.profiles.profiles),
   ["other", "改过名的"], "改名：旧名不残留")
eq(mb.warn, [], "改名：没有误报警告")

forms.FormDialog.open = _orig_open
HP.messagebox = _orig_mb

try:
    win.destroy()
except tkinter.TclError:
    pass

print("\n" + "=" * 50)
print(f"{'失败 ' + str(len(FAILS)) if FAILS else '全部通过'}：{PASS}/{PASS + FAIL}")
for x in FAILS:
    print("  ✗", x)
raise SystemExit(1 if FAILS else 0)
