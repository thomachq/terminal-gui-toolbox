# -*- coding: utf-8 -*-
"""连接形态 / 提权 / 厂商方言 的离线自检。

覆盖用户提的四件事：
  1. Windows 本机不出现 sudo 密码，只有「以管理员身份运行」
  2. 本机 Linux 才有 sudo 密码勾选，连上后自动 sudo -i
  3. 任何连接方式没给密码时，需要提权就抛 NeedElevation（由主程序弹窗）
  4. 交换机/路由器按厂商（Cisco / H3C / 华为）生成命令

Telnet 部分在本地起一个假设备（socket 服务端），走的是真实协议代码。
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import parsers, routes                      # noqa: E402
from core.detect import (CiscoDialect, DeviceInfo, H3CDialect,  # noqa: E402
                         HuaweiDialect, _detect_network, dialect_for)
from core.metrics import Sampler                      # noqa: E402
from core.session import (ConnSpec, LocalSession, NeedElevation,  # noqa: E402
                          SSHSession, TelnetSession, open_session)

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f"  <- {detail}" if detail else ""))


# ------------------------------------------------------------------ 假会话
class FakeSession:
    """把命令 → 输出写成一张表，用来喂探测逻辑。"""

    kind = "telnet"
    host = "sw1"
    user = "admin"
    platform_hint = ""
    ps_native = False

    def __init__(self, table: dict, prompt: str = "SW1#" ) -> None:
        self.table = table
        self.prompt = prompt
        self.seen: list[str] = []

    def run(self, cmd: str, timeout: int = 10):
        self.seen.append(cmd)
        for key, val in self.table.items():
            if key in cmd:
                return 0, val, ""
        return 1, "", "% Invalid input detected at '^' marker."

    def run_traced(self, cmd: str, timeout: int = 10):
        return self.run(cmd, timeout)


class FakeTelnetSession(TelnetSession):
    """只为判断 can_write 用：kind 已经是 telnet。"""

    def __init__(self) -> None:
        super().__init__()
        self.host = "sw1"


class FakeSSHSession(SSHSession):
    def __init__(self) -> None:
        super().__init__()
        self.host = "sw1"
        self.user = "admin"


CISCO_VER = """Cisco IOS Software, C2960 Software (C2960-LANBASEK9-M), Version 15.0(2)SE11
Copyright (c) 1986-2017 by Cisco Systems, Inc.
SW1 uptime is 3 weeks, 2 days, 4 hours
SW1#
"""
H3C_VER = """H3C Comware Software, Version 7.1.070, Release 6329P02
Copyright (c) 2004-2021 New H3C Technologies Co., Ltd.
H3C S5130S-28P-EI uptime is 0 weeks, 1 day, 2 hours
SW1#
"""
HW_VER = """Huawei Versatile Routing Platform Software
VRP (R) software, Version 5.170 (S5720 V200R019C10SPC500)
Copyright (C) 2000-2019 HUAWEI TECH CO., LTD
SW1#
"""


# ------------------------------------------------------------------ 1. 连接页
def test_conn_page() -> None:
    print("\n[1] 连接页三种形态")
    import json
    p = HERE / "data" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    d.update({"lock_enabled": False, "lock_hash": "", "lock_timeout": 0,
              "lock_auto_login": False, "lock_pass_enc": ""})
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    import app as APP
    a = APP.ToolboxApp()
    a.update()

    def pump(sec=0.3):
        end = time.time() + sec
        while time.time() < end:
            try:
                a.update()
            except tk.TclError:
                return
            time.sleep(0.02)

    def shown(w) -> bool:
        return bool(w.winfo_manager())

    a.v_proto.set("local")
    a._proto_changed()
    pump()
    check("本机：档案行隐藏", not shown(a.row_prof))
    check("本机：地址/端口行隐藏", not shown(a.row_addr))
    check("本机：私钥/密码行隐藏", not shown(a.row_auth))
    check("本机：提权区仍在", shown(a.row_elev))
    if sys.platform == "win32":
        check("本机 Windows：没有 sudo 密码输入框", a.e_sudopw is None)
        check("本机 Windows：有 UAC 勾选", a.v_admin is not None)
        check("本机 Windows：凭据行隐藏", not shown(a.row_cred),
              a.row_cred.winfo_manager())
    else:
        check("本机 Linux：有 sudo 密码输入框", a.e_sudopw is not None)
        check("本机 Linux：勾选前密码框禁用",
              str(a.e_sudopw.cget("state")) == "disabled")

    a.v_proto.set("ssh")
    a._proto_changed()
    pump()
    check("SSH：档案行恢复", shown(a.row_prof))
    check("SSH：地址行恢复", shown(a.row_addr))
    check("SSH：私钥/密码行恢复", shown(a.row_auth))
    check("SSH：有 sudo 密码输入框", a.e_sudopw is not None)
    check("SSH：勾选前密码框禁用",
          str(a.e_sudopw.cget("state")) == "disabled")

    a.v_elevate.set(True)
    a._elev_toggled()
    pump(0.15)
    check("SSH：勾选后密码框可用",
          str(a.e_sudopw.cget("state")) == "normal")

    a.v_proto.set("telnet")
    a._proto_changed()
    pump()
    check("Telnet：私钥/密码行隐藏", not shown(a.row_auth))
    check("Telnet：提示的是 enable 密码",
          "enable" in str(a.chk_remember.lbl.cget("text")).lower()
          or a.e_sudopw is not None)

    a.v_proto.set("local")
    a._proto_changed()
    pump()
    check("切回本机仍然干净", not shown(a.row_addr))
    a.destroy()


# ------------------------------------------------------------------ 2. 提权
def test_elevation() -> None:
    print("\n[2] NeedElevation 三个来源")

    local = LocalSession()
    if sys.platform == "win32":
        local.is_admin = False           # 假装普通用户
        try:
            local.run_admin("route add 1.1.1.1 mask 255.255.255.255 2.2.2.2")
            check("Windows 非管理员：run_admin 抛 NeedElevation", False)
        except NeedElevation as e:
            check("Windows 非管理员：run_admin 抛 NeedElevation", True)
            check("Windows 非管理员：kind == uac", e.kind == "uac", e.kind)
        local.is_admin = True
        rc, out, err = local.run_admin("cmd /c echo hi")
        check("Windows 管理员：命令能跑", rc == 0 and "hi" in out, f"{rc} {out}")
        check("Windows：admin_kind 是 uac", local.admin_kind == "uac")
    else:
        local.is_admin = False
        local.sudo_pw = None
        try:
            local.run_admin("id")
            check("Linux 无密码：抛 NeedElevation", False)
        except NeedElevation as e:
            check("Linux 无密码：抛 NeedElevation", e.kind == "sudo")

    ssh = SSHSession()
    ssh.user = "thomas"
    ssh.host = "nas"
    try:
        ssh.run_admin("ip route add 10.0.0.0/8 via 1.1.1.1")
        check("SSH 无 sudo 密码：抛 NeedElevation", False)
    except NeedElevation as e:
        check("SSH 无 sudo 密码：抛 NeedElevation", True)
        check("SSH：kind == sudo", e.kind == "sudo", e.kind)
        check("SSH：提示里带目标", "thomas@nas" in (e.target or ""), e.target)
    check("SSH：admin_kind == sudo", ssh.admin_kind == "sudo")

    tn = TelnetSession()

    class _FakeImpl:                 # 假装已经连上设备
        def enable(self, pw):
            return False

        def command(self, cmd, timeout=10):
            return ""

        def close(self):
            pass

    tn._impl = _FakeImpl()
    try:
        tn.run_admin("ip route 0.0.0.0 0.0.0.0 1.1.1.1")
        check("Telnet 未 enable：抛 NeedElevation", False)
    except NeedElevation as e:
        check("Telnet 未 enable：抛 NeedElevation", e.kind == "enable", e.kind)
    check("Telnet：admin_kind == enable", tn.admin_kind == "enable")
    check("Telnet 未 enable：can_admin 说缺密码",
          tn.can_admin()[0] is False and "enable" in tn.can_admin()[1])

    # open_session 的 elevate 分支：给了密码就该自动提权（这里没真设备，只验参数搬运）
    spec = ConnSpec(proto="local", elevate=True, sudo_password="x")
    s = open_session(spec)
    check("open_session 接受 elevate/sudo_password",
          isinstance(s, LocalSession), type(s).__name__)


# ------------------------------------------------------------------ 3. 厂商
def test_vendors() -> None:
    print("\n[3] 厂商识别与方言")

    cases = (("cisco", CISCO_VER, CiscoDialect, "switch"),
             ("h3c", H3C_VER, H3CDialect, "switch"),
             ("huawei", HW_VER, HuaweiDialect, "switch"))
    for vendor, banner, cls, dev_type in cases:
        info = DeviceInfo()
        sess = FakeSession({"show version": banner, "display version": banner},
                           prompt="SW1#")
        ok = _detect_network(sess, info)
        check(f"{vendor}：识别成功", ok)
        check(f"{vendor}：vendor 正确", info.vendor == vendor, info.vendor)
        check(f"{vendor}：os_family == network", info.os_family == "network")
        check(f"{vendor}：设备类型 {dev_type}",
              info.device_type == dev_type, info.device_type)
        check(f"{vendor}：方言类正确",
              isinstance(dialect_for(info), cls),
              type(dialect_for(info)).__name__)
        check(f"{vendor}：主机名从提示符取到", info.hostname == "SW1", info.hostname)

    info = DeviceInfo(os_family="network", vendor="cisco")
    d = dialect_for(info)
    check("Cisco：用 show", d.ip_cmd().startswith("show"), d.ip_cmd())
    check("Cisco：ping 用 repeat", "repeat" in d.ping_cmd("1.1.1.1"))

    h3c = dialect_for(DeviceInfo(os_family="network", vendor="h3c"))
    check("H3C：用 display", h3c.ip_cmd().startswith("display"), h3c.ip_cmd())
    check("H3C：ping 用 -c", "-c" in h3c.ping_cmd("1.1.1.1"))
    check("H3C：看配置用 display current-configuration",
          h3c.running_config_cmd() == "display current-configuration")
    check("H3C：没有性能采样命令", h3c.metrics_cmd() == "")

    hw = dialect_for(DeviceInfo(os_family="network", vendor="huawei"))
    check("华为：用 display", hw.ip_cmd().startswith("display"))
    check("华为：tracert 不是 traceroute", hw.traceroute_cmd("1.1.1.1").startswith("tracert"))

    # 不认识品牌也要能当网络设备处理
    info = DeviceInfo()
    sess = FakeSession({"show version": "SomeSwitch OS 1.0\nSW1#"}, prompt="SW1#")
    check("未知品牌仍认作网络设备",
          _detect_network(sess, info) and info.os_family == "network")
    check("未知品牌回落 NetworkDialect",
          type(dialect_for(info)).__name__ in ("NetworkDialect", "CiscoDialect"),
          type(dialect_for(info)).__name__)


# ------------------------------------------------------------------ 4. 解析
def test_routes() -> None:
    print("\n[4] 路由表解析（Cisco / H3C）")

    cisco = """Codes: L - local, C - connected, S - static
Gateway of last resort is 192.168.1.1 to network 0.0.0.0

S*    0.0.0.0/0 [1/0] via 192.168.1.1
      10.0.0.0/8 is variably subnetted, 2 subnets, 2 masks
C        192.168.1.0/24 is directly connected, Vlan1
O E2  172.16.0.0/16 [110/20] via 10.1.1.2, 00:10:00, GigabitEthernet0/1
"""
    rows = parsers.parse_route_network(cisco)
    dsts = [r["dst"] for r in rows]
    check("Cisco：默认路由解析出来", "0.0.0.0/0" in dsts, dsts)
    check("Cisco：默认路由网关正确",
          next(r for r in rows if r["dst"] == "0.0.0.0/0")["gateway"] == "192.168.1.1")
    check("Cisco：subnetted 分节行没被当成路由",
          "10.0.0.0/8" not in dsts, dsts)
    check("Cisco：直连带出接口",
          next(r for r in rows if r["dst"] == "192.168.1.0/24")["dev"] == "Vlan1")
    check("Cisco：OSPF 条目识别为 ospf",
          next(r for r in rows if r["dst"] == "172.16.0.0/16")["proto"] == "ospf")

    h3c = """Destination/Mask    Proto   Pre  Cost      NextHop         Interface
0.0.0.0/0           Static  60   0         192.168.1.1     GE1/0/1
192.168.1.0/24      Direct  0    0         192.168.1.2     Vlan-interface1
"""
    rows = parsers.parse_route_network(h3c)
    check("H3C：条目数正确", len(rows) == 2, len(rows))
    check("H3C：默认路由网关",
          rows[0]["gateway"] == "192.168.1.1", rows[0]["gateway"])
    check("H3C：接口带型号",
          rows[0]["dev"] == "GE1/0/1", rows[0]["dev"])
    check("H3C：协议名小写", rows[0]["proto"] == "static", rows[0]["proto"])

    # 后端：命令按厂商生成
    dev = DeviceInfo(os_family="network", vendor="cisco")
    be = routes.backend_for(dev)
    check("Cisco 后端类", type(be).__name__ == "NetworkRoute")
    check("Cisco：读用 show ip route", be.read_cmd() == "show ip route")
    check("Cisco：加路由用 ip route",
          be.add_cmds({"dst": "10.0.0.0/8", "gateway": "1.1.1.1"})
          == ["ip route 10.0.0.0 255.0.0.0 1.1.1.1"],
          be.add_cmds({"dst": "10.0.0.0/8", "gateway": "1.1.1.1"}))
    check("Cisco：删路由用 no ip route",
          be.del_cmds({"dst": "10.0.0.0/8", "gateway": "1.1.1.1"})[0]
          .startswith("no ip route"))

    dev = DeviceInfo(os_family="network", vendor="h3c")
    be = routes.backend_for(dev)
    check("H3C：读用 display ip routing-table",
          be.read_cmd() == "display ip routing-table")
    check("H3C：加路由用 ip route-static",
          be.add_cmds({"dst": "10.0.0.0/8", "gateway": "1.1.1.1"})
          == ["ip route-static 10.0.0.0 255.0.0.0 1.1.1.1"],
          be.add_cmds({"dst": "10.0.0.0/8", "gateway": "1.1.1.1"}))
    check("H3C：删路由用 undo ip route-static",
          be.del_cmds({"dst": "10.0.0.0/8", "gateway": "1.1.1.1"})[0]
          .startswith("undo ip route-static"))

    # SSH 到网络设备写不了配置（每条命令一个新通道，进不了 config 模式）
    be_ssh = routes.backend_of(DeviceInfo(os_family="network", vendor="cisco"),
                               FakeSSHSession())
    check("SSH 到交换机：只读", be_ssh.can_write is False)
    be_tel = routes.backend_of(DeviceInfo(os_family="network", vendor="cisco"),
                               FakeTelnetSession())
    check("Telnet 到交换机：可写", be_tel.can_write is True)


# ------------------------------------------------------------------ 5. 采样
def test_sampler() -> None:
    print("\n[5] 网络设备不采样性能")

    class Sess:
        kind = "telnet"
        host = "sw1"

        def run(self, cmd, timeout=10):
            raise AssertionError("不该发出命令")

    smp = Sampler(Sess(), H3CDialect())
    s = smp.sample()
    check("采样返回 unsupported", s.unsupported is True)
    check("unsupported 不带 error（状态栏不会变红）", s.error == "", s.error)


# ------------------------------------------------------------------ 6. Telnet
class FakeDevice(threading.Thread):
    """本地假交换机：跑真实 Telnet 协议，验证客户端而不是 mock。"""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.stage = "login"
        self.buf = b""
        self.multi = False

    def run(self) -> None:
        try:
            conn, _ = self.srv.accept()
        except OSError:
            return
        conn.settimeout(6)
        conn.sendall(b"\r\nUser Access Verification\r\nUsername: ")
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                self.buf += data
                # 翻页是「发一个空格」，没有回车 —— 真实设备也是这样。
                # 所以要在按行切分之前先把裸空格消化掉。
                if self.multi and b" " in self.buf:
                    self.buf = self.buf.replace(b" ", b"", 1)
                    conn.sendall(b"\r\n!\r\nreturn\r\nSW1#\r\n")
                    self.multi = False
                while b"\n" in self.buf or b"\r" in self.buf:
                    line, self.buf = self.buf.split(b"\n", 1) if b"\n" in self.buf \
                        else self.buf.split(b"\r", 1)
                    cmd = line.decode("utf-8", "replace").strip()
                    if not cmd:
                        continue
                    if self.stage == "login":
                        if cmd.lower() == "admin":
                            self.stage = "pw"
                            conn.sendall(b"Password: ")
                        else:
                            conn.sendall(b"Username: ")
                    elif self.stage == "pw":
                        if cmd == "admin123":
                            self.stage = "user"
                            conn.sendall(b"\r\n<SW1>\r\n")
                        else:
                            self.stage = "login"
                            conn.sendall(b"\r\n% Authentication failed\r\nUsername: ")
                    elif self.stage == "enable_pw":
                        conn.sendall(b"\r\nSW1#\r\n")
                        self.stage = "priv"
                    else:
                        if cmd == "enable":
                            self.stage = "enable_pw"
                            conn.sendall(b"Password: ")
                        elif cmd.startswith("display version"):
                            conn.sendall(("\r\n" + H3C_VER + "\r\nSW1>"
                                          if self.stage != "priv"
                                          else "\r\n" + H3C_VER + "\r\nSW1#")
                                          .encode())
                        elif cmd.startswith("display ip routing-table"):
                            body = ("Destination/Mask    Proto   Pre  Cost"
                                    "      NextHop         Interface\r\n"
                                    "0.0.0.0/0           Static  60   0"
                                    "         192.168.1.1     GE1/0/1\r\n")
                            conn.sendall((body + "SW1#\r\n").encode())
                        elif cmd.startswith("display current-configuration"):
                            # 分页：必须先回 --More--，客户端应当自动翻页
                            conn.sendall(b"\r\nline vty 0 4\r\n--More--")
                            self.multi = True
                        elif cmd == " " and self.multi:
                            conn.sendall(b"\r\n!\r\nreturn\r\nSW1#\r\n")
                            self.multi = False
                        else:
                            conn.sendall(b"\r\n% Unrecognized command\r\nSW1#\r\n")
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self.srv.close()


def test_telnet() -> None:
    print("\n[6] Telnet 协议层（本地假设备）")
    dev = FakeDevice()
    dev.start()
    time.sleep(0.3)

    from core.telnet_session import TelnetClient, TelnetError

    cli = TelnetClient("127.0.0.1", dev.port, timeout=6)
    banner = cli.login("admin", "admin123")
    check("登录拿到 <SW1> 提示符", "<SW1>" in banner, banner[:80])
    check("连上后 alive", cli.alive())
    check("提示符被记住", cli.last_prompt == "<SW1>", cli.last_prompt)

    out = cli.command("display version")
    check("命令能取回输出", "Comware" in out, out[:80])
    check("命令后提示符更新成 >", cli.last_prompt.endswith(">"), cli.last_prompt)

    ok = cli.enable("admin123")
    check("enable 成功", ok is True)
    check("enable 后提示符是 #", cli.last_prompt.endswith("#"), cli.last_prompt)

    r = cli.command("display ip routing-table")
    check("特权模式下读路由表", "192.168.1.1" in r, r[:80])

    paged = cli.command("display current-configuration", timeout=8)
    check("分页被自动翻过（拿到第二页内容）", "return" in paged, paged[:120])

    cli.close()
    check("close 之后 alive 为假", not cli.alive())

    # 连不上要有可读的错误，而不是裸 socket 异常
    bad = TelnetClient("127.0.0.1", 1, timeout=1)
    try:
        bad.connect()
        check("连不上抛 TelnetError", False)
    except TelnetError as e:
        check("连不上抛 TelnetError", "连不上" in str(e), str(e)[:60])


# ------------------------------------------------------------------ 7. 弹窗
def test_elevation_dialog() -> None:
    print("\n[7] 提权弹窗与重试")
    import json
    p = HERE / "data" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    d.update({"lock_enabled": False, "lock_hash": "", "lock_timeout": 0,
              "lock_auto_login": False, "lock_pass_enc": ""})
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    import app as APP
    from ui import forms as UI_FORMS

    a = APP.ToolboxApp()
    a.update()

    def pump(sec=0.4):
        end = time.time() + sec
        while time.time() < end:
            try:
                a.update()
            except tk.TclError:
                return
            time.sleep(0.02)

    calls = {"ask": 0, "form": 0}
    orig_ask, orig_open = APP.messagebox.askyesno, UI_FORMS.FormDialog.open

    class _Sess:
        kind = "ssh"
        host = "nas"
        user = "thomas"
        is_root = False
        sudo_pw = None

        def sudo_ok(self, pw):
            self.sudo_pw = pw
            return pw == "good"

        def can_admin(self):
            return (bool(self.sudo_pw), "" if self.sudo_pw else "需要 sudo 密码")

    a.session = _Sess()
    try:
        # ① UAC：问「要不要以管理员重启」
        APP.messagebox.askyesno = lambda *args, **kw: calls.__setitem__(
            "ask", calls["ask"] + 1) or False
        a._ask_elevation(NeedElevation("要管理员", kind="uac"),
                         lambda: 1, None, None)
        pump(0.2)
        check("UAC 会弹「以管理员重启」确认框", calls["ask"] == 1, calls)

        # ② sudo：弹表单，用户取消
        UI_FORMS.FormDialog.open = lambda *args, **kw: (
            calls.__setitem__("form", calls["form"] + 1) or None)
        a._ask_elevation(NeedElevation("要密码", kind="sudo", target="t@nas"),
                         lambda: 1, None, None)
        pump(0.2)
        check("sudo 会弹密码表单", calls["form"] == 1, calls)

        # ③ 给了正确密码：应该自动重跑刚才失败的那个操作
        UI_FORMS.FormDialog.open = lambda *args, **kw: {"pw": "good"}
        ran = []

        def _retry():
            ran.append(1)
            return "ok"

        a._ask_elevation(NeedElevation("要密码", kind="sudo", target="t@nas"),
                         _retry, None, None)
        for _ in range(40):
            pump(0.1)
            if ran:
                break
        check("密码通过后自动重试原操作", len(ran) == 1, ran)
        check("重试时带上了新密码", a.session.sudo_pw == "good", a.session.sudo_pw)

        # ④ 密码错：不该重跑，也不该无限弹窗
        UI_FORMS.FormDialog.open = lambda *args, **kw: {"pw": "bad"}
        a.session.sudo_pw = None
        ran2 = []
        a._ask_elevation(NeedElevation("要密码", kind="sudo"),
                         lambda: ran2.append(1), None, None)
        pump(1.2)
        check("密码错时不重跑原操作", not ran2, ran2)
    finally:
        APP.messagebox.askyesno = orig_ask
        UI_FORMS.FormDialog.open = orig_open
        a.destroy()


def main() -> int:
    test_vendors()
    test_routes()
    test_sampler()
    test_elevation()
    test_telnet()
    test_conn_page()
    test_elevation_dialog()
    print(f"\n===== 结果：{passed} 通过 / {failed} 失败 =====")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
