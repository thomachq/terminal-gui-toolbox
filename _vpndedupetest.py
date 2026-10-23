# -*- coding: utf-8 -*-
"""隧道证书控制台去重自测（v1.0.7 T9）。

用户的意见是「这个连接功能重复了，因为前面已经可以进行连接了」。
所以判据全是行为，不是看代码长得对不对：

  1. 内嵌时页签集合不含 conn，从「证书」开始
  2. 没有会话时头部说清「先在主机管理里连接一台设备」，且有跳转按钮，且不崩
  3. 有假会话时 do_probe 真的走主程序的会话，把结果填进「服务端现状」
  4. do_deploy 用的也是主程序会话（命令打到同一个 session 上）
  5. close() 不会把主程序的会话关掉（这是最容易踩的坑）
  6. 独立运行时 conn 页签仍在（不破坏 VPNCertConsole.exe 的用法）
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
TD = tempfile.mkdtemp(prefix="tbvpn9_")

import tkinter  # noqa: E402
import tkinter as tk  # noqa: E402

import vpncert_gui as V  # noqa: E402

V.DATA_DIR = Path(TD) / "data"
V.DATA_DIR.mkdir(parents=True, exist_ok=True)


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


def pump(win, seconds=0.4):
    end = time.time() + seconds
    while time.time() < end:
        try:
            win.update()
        except tkinter.TclError:
            return
        time.sleep(0.01)


CONF = """port 11094
proto tcp-server
dev tun
server 10.8.0.0 255.255.255.0
auth SHA256
verify-client-cert none
"""


class FakeSession:
    """假装成主程序已经连好的一台 NAS。"""

    alive = True
    host_kind = "unix"
    label = "fake-nas"
    host = "192.168.3.12"
    port = 3022
    user = "cloudadmin"
    sudo_pw = "x"

    def __init__(self):
        self.cmds = []
        self.closed = False

    def can_admin(self):
        return True, ""

    def run(self, cmd, timeout=30):
        self.cmds.append(cmd)
        if "cat /etc/VERSION" in cmd:
            return 0, "DSM 7.2.1 ", ""
        if "id -Gn" in cmd:
            return 0, "users administrators\n", ""
        if CONFIG_PATH in cmd:
            return 0, CONF, ""
        if "ls -la" in cmd:
            return 0, "total 8\nta.key\n", ""
        if "ta.key" in cmd:
            return 0, "-----BEGIN OpenVPN Static key V1-----\nabc\n", ""
        if "netstat" in cmd:
            return 0, "tcp 0 0 0.0.0.0:11094 LISTEN\n", ""
        if "tar -czf" in cmd:
            return 0, "-rw-r--r-- 1 root root 1024 /volume1/x.tar.gz\n", ""
        return 0, "", ""

    def run_admin(self, cmd, timeout=45):
        return self.run(cmd, timeout)

    def set_sudo_pw(self, pw):
        self.sudo_pw = pw

    def close(self):
        self.closed = True

    def sftp(self):
        raise AssertionError("测试里不该真去开 SFTP")


class BareSession(FakeSession):
    """本机 / Telnet 那种没有 SFTP 的会话。"""

    def __init__(self):
        super().__init__()
        self.sftp = None      # 属性存在但拿不到，等同于没有


CONFIG_PATH = V.os.environ.get("X", "") or "/var/packages/VPNCenter/etc/openvpn/openvpn.conf"


# ================================================================== 1. 内嵌
sec("1. 内嵌：页签里没有「连接」")

root = tk.Tk()
root.geometry("1100x760+40+40")
holder = tk.Frame(root, bg="#ffffff")
holder.pack(fill="both", expand=True)
con = V.App(holder, embedded=True)
con.pack(fill="both", expand=True)
pump(root, 0.5)

keys = list(con._pages)
chk("conn" not in keys, "内嵌模式没有 conn 子页", str(keys))
eq(keys[:1], ["cert"], "第一个子页是「证书」")
chk(len(keys) == 4, "一共四页（证书/打包/部署/命令日志）", str(keys))
eq(con.nav.current, "cert", "默认停在证书页")
chk(not hasattr(con, "v_host"), "连接页的输入变量没有被创建")

# 头部设备状态
chk(getattr(con, "lbl_dev", None) is not None, "头部有设备状态文字")
pump(root, 0.3)
txt = str(con.lbl_dev.cget("text") or "")
chk("主机管理" in txt, "没会话时提示去主机管理连", txt)
chk(getattr(con, "btn_goto_hosts", None) is not None, "有「主机管理」跳转按钮")
chk(con.nas.connected is False, "没有会话时 connected 是 False")
chk(con.nas.has_sudo is False, "没有会话时也不算有提权")
eq(con.nas.close(), None, "close() 不抛（会话归主程序）")

# 点「探测当前设备」：没会话时只提示，不能崩
warned = {}
_old_warn = V.messagebox.showwarning
V.messagebox.showwarning = lambda *a, **k: warned.setdefault("msg", a[1] if len(a) > 1 else "")
try:
    con.do_probe()
    pump(root, 0.2)
    chk("主机管理" in str(warned.get("msg", "")),
        "没会话时 do_probe 提示去主机管理", str(warned.get("msg"))[:60])
finally:
    V.messagebox.showwarning = _old_warn

# ================================================================== 2. 有会话
sec("2. 有会话：走主程序的 session")

fs = FakeSession()


class FakeApp:
    """最小的主程序替身：只提供 session 和 nav。"""

    def __init__(self, sess):
        self.session = sess
        self.nav = type("N", (), {"current": "vpn", "select": lambda self, k: None})()


con.host_app = FakeApp(fs)
con.nas.app = con.host_app
con._dev_sig = None
con._refresh_device()
pump(root, 0.3)
eq(con.nas.connected, True, "接上假会话后 connected 为 True")
eq(con.nas.label(), "cloudadmin@192.168.3.12", "label 是 user@host")
chk("192.168.3.12" in str(con.lbl_dev.cget("text") or ""),
    "头部显示当前设备", str(con.lbl_dev.cget("text")))

con.do_probe()
pump(root, 1.2)
chk(any("cat /etc/VERSION" in c for c in fs.cmds),
    "探测真的打到主程序会话上了", fs.cmds[:6])
_body = ""
for w in con.kv_holder.winfo_children():
    for c in w.winfo_children():
        try:
            _body += " " + str(c.cget("text"))
        except tkinter.TclError:
            pass
chk("DSM 7.2.1" in _body, "「服务端现状」里出现了探测到的 DSM 版本", _body[:160])
chk("cloudadmin@192.168.3.12" in _body, "现状里写清了设备", _body[:160])
chk("none" in str(con.box_conf.txt.get("1.0", "end-1c")).lower()
    or "verify-client-cert none" in str(con.box_conf.txt.get("1.0", "end-1c")),
    "openvpn.conf 原文填进了文本框")

# 部署：命令也必须落在同一个会话上
sec("3. 部署走同一个会话")
fs.cmds.clear()
con.nas.sudo_pw = "x"
res = None
try:
    res = con.nas.run("id -u", sudo=True)
except Exception as e:  # noqa: BLE001
    chk(False, "sudo 走主会话的 run_admin", str(e))
chk(res is not None and res[0] == 0, "sudo 命令执行成功")
chk("id -u" in fs.cmds, "sudo 命令确实进了假会话", fs.cmds[-3:])
chk(con.nas.connected, "跑完 sudo 仍是连着的")
chk(con.nas.close() is None and fs.closed is False,
    "close() 没有把主程序会话关掉（只清 sudo 口令）")
chk(con.nas.sudo_pw is None, "close() 清掉了本页的 sudo 口令")

# sftp 不支持时要给人话，不是 AttributeError
_bare = BareSession()
con.nas._session = _bare
try:
    con.nas.sftp()
    chk(False, "会话没有 sftp 时应当报错")
except V.NasError as e:
    chk("SFTP" in str(e), "缺 sftp 时给的是人话", str(e)[:60])
except Exception as e:  # noqa: BLE001
    chk(False, "缺 sftp 时抛的不是 NasError", type(e).__name__)

con.nas._session = None
for w in (con, holder):
    try:
        w.destroy()
    except tkinter.TclError:
        pass
try:
    root.destroy()
except tkinter.TclError:
    pass

# ================================================================== 4. 独立运行
sec("4. 独立运行：conn 页签仍在")

root2 = tk.Tk()
root2.geometry("1100x760+40+40")
con2 = V.App(root2, embedded=False)
con2.pack(fill="both", expand=True)
pump(root2, 0.5)
keys2 = list(con2._pages)
chk("conn" in keys2, "独立运行时保留「连接」页", str(keys2))
eq(con2.nav.current, "conn", "独立运行时默认停在连接页")
chk(isinstance(con2.nas, V.NasSession),
    "独立运行时用的是自己的 SSH 会话（NasSession）", type(con2.nas).__name__)
chk(hasattr(con2, "v_host") and hasattr(con2, "btn_connect"),
    "独立运行时连接表单还在")

try:
    con2.destroy()
    root2.destroy()
except tkinter.TclError:
    pass

print("\n" + "=" * 52)
print(f"{'失败 ' + str(len(FAILS)) if FAILS else '全部通过'}：{PASS}/{PASS + FAIL}")
for x in FAILS:
    print("  ✗", x)
raise SystemExit(1 if FAILS else 0)
