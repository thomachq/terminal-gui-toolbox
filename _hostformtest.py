# -*- coding: utf-8 -*-
"""添加主机表单自测（v1.0.7 T5）。

四段：
  1. 字段表：协议选「本机」后，地址/端口/认证/私钥/密码/凭证/代理全不可见
  2. 两套本机提权：Linux 有 sudo（勾了才出密码框），Windows 有 UAC + cmd/powershell
  3. 真开一次表单：切协议时行会实时收起来，高度跟着变，必填校验不管隐藏字段
  4. 落盘：sudo / elevate / shell 能存能读；本机外壳真的进了 LocalSession

数据目录在临时目录。不联网。
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
TD = tempfile.mkdtemp(prefix="tbhost5_")

import app as appmod  # noqa: E402
from core import hosts as H  # noqa: E402
from core import session as S  # noqa: E402

appmod.APP_DIR = Path(TD)
appmod.DATA_DIR = Path(TD) / "data"
appmod.DATA_DIR.mkdir(parents=True, exist_ok=True)

import tkinter  # noqa: E402
from pages.hosts_page import host_fields  # noqa: E402
from ui import forms  # noqa: E402

REMOTE_KEYS = ("host", "port", "user", "auth", "key_path", "password",
               "cred_id", "proxy_id")


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


def visible(fields, rec_proto="ssh", extra=None) -> dict[str, bool]:
    """按 show_if 的规则算一遍「这个字段现在显不显示」。"""
    vals = {"proto": rec_proto, "auth": "key", "sudo": False}
    vals.update(extra or {})
    out = {}
    for f in fields:
        spec = f.get("show_if")
        if not spec:
            out[f["key"]] = True
            continue
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


# ================================================================== 1. 字段表
sec("1. 本机协议要瘦身")

fs_local = host_fields({"proto": "local", "name": "本机"}, is_win=False)
vis = visible(fs_local, "local")
hidden = [k for k in REMOTE_KEYS if vis.get(k)]
eq(hidden, [], "切到本机后，远程字段一个都不显示")
for k in ("name", "proto", "group", "tags"):
    chk(vis.get(k) is True, f"「{k}」还在")
chk(vis.get("sudo") is True, "Linux 分支有 sudo 勾选")
chk(vis.get("elevate") is None, "Linux 分支没有 UAC 勾选")
chk(vis.get("shell") is None, "Linux 分支没有 Shell 下拉")

vis_ssh = visible(host_fields({"proto": "ssh"}, is_win=False), "ssh")
chk(all(vis_ssh.get(k) for k in REMOTE_KEYS),
    "协议是 SSH 时远程字段都在", [k for k in REMOTE_KEYS
                                  if not vis_ssh.get(k)])
chk(vis_ssh.get("sudo") is False, "SSH 时不显示 sudo")
chk(vis_ssh.get("key_path") is True, "私钥认证下私钥路径要在")
vis_pw = visible(host_fields({"proto": "ssh", "auth": "password"},
                             is_win=False), "ssh", {"auth": "password"})
chk(vis_pw.get("key_path") is False, "改成密码认证后私钥路径收起来")

# ================================================================== 2. Windows
sec("2. Windows 分支")

fs_win = host_fields({"proto": "local"}, is_win=True)
keys = [f["key"] for f in fs_win]
chk("elevate" in keys, "Windows 分支有「以管理员权限运行」")
chk("shell" in keys, "Windows 分支有 Shell 下拉")
chk("sudo" not in keys and "sudo_pass" not in keys,
    "Windows 分支没有 sudo", keys)
shell_f = next(f for f in fs_win if f["key"] == "shell")
opts = [o[0] for o in shell_f["options"]]
chk("cmd" in opts and "powershell" in opts, "下拉里有 cmd 和 powershell", opts)

fs_lin = host_fields({"proto": "local"}, is_win=False)
lkeys = [f["key"] for f in fs_lin]
chk("sudo" in lkeys and "sudo_pass" in lkeys, "Linux 分支有 sudo 与 sudo 密码")
chk("elevate" not in lkeys and "shell" not in lkeys,
    "Linux 分支没有 UAC / Shell", lkeys)
vis_on = visible(fs_lin, "local", {"sudo": True})
chk(vis_on.get("sudo_pass") is True, "勾了 sudo 才出现 sudo 密码框")
vis_off = visible(fs_lin, "local", {"sudo": False})
chk(vis_off.get("sudo_pass") is False, "不勾 sudo 就没有密码框")

# ================================================================== 3. 真表单
sec("3. 真开一次表单")

win = appmod.ToolboxApp()
win.geometry("+20+20")
win.deiconify()
pump(win, 0.9)

dlg = forms.FormDialog(win, "添加主机", host_fields({"proto": "ssh"}, is_win=False),
                       {"proto": "ssh", "name": "本机表单"}, width=560)
pump(win, 0.4)
chk(dlg.winfo_exists(), "表单开起来了")


def mapped(key: str) -> bool:
    ws = dlg._row_widgets.get(key) or []
    return bool(ws) and bool(ws[0].winfo_ismapped())


chk(mapped("host"), "SSH 时地址行是显示的")
h_ssh = dlg.winfo_height()

dlg._vars["proto"].set("本机")
pump(win, 0.3)
chk(not mapped("host"), "选本机后地址行立刻收起来")
chk(not mapped("key_path"), "私钥路径也收起来")
chk(mapped("sudo") or mapped("elevate"), "本机提权那一行露出来")
h_local = dlg.winfo_height()
chk(h_local < h_ssh, "收起来之后窗口跟着变矮", f"{h_ssh} -> {h_local}")

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

# ================================================================== 4. 落盘
sec("4. 能存能读")

store = win.profiles
rec = H.blank_record("local")
rec.update({"name": "本机测试", "sudo": True, "elevate": False, "shell": "cmd",
            "sudo_password": "pw-x", "remember": True})
saved = store.upsert(rec)
chk(saved.get("sudo") is True, "sudo 存成布尔", saved.get("sudo"))
eq(saved.get("shell"), "cmd", "shell 存下来了")
again = store.get("本机测试") or {}
chk(again.get("sudo") is True and again.get("shell") == "cmd",
    "重读还在", f"{again.get('sudo')} / {again.get('shell')}")
chk(isinstance(again.get("sudo"), bool), "读回来还是布尔，不是字符串")
# 非本机协议不该带着本机那三个字段出门
rec2 = H.blank_record("ssh")
rec2.update({"name": "远程测试", "host": "10.0.0.9", "sudo": True,
             "shell": "cmd"})
merged = H.record_from_form({"proto": "ssh"}, base=rec2)
eq(merged.get("proto"), "ssh", "协议切回 SSH")

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

try:
    win.destroy()
except tkinter.TclError:
    pass

print("\n" + "=" * 50)
print(f"{'失败 ' + str(len(FAILS)) if FAILS else '全部通过'}：{PASS}/{PASS + FAIL}")
for x in FAILS:
    print("  ✗", x)
raise SystemExit(1 if FAILS else 0)
