# -*- coding: utf-8 -*-
"""防火墙 / 路由表图形化编辑自测（v1.0.7 T8）。

五段，全程离线：
  1. 探测：6 种后端的探测输出各喂一份假 stdout，`detect_backend` 要认对；
     再测「装了没启用」的降级与优先级
  2. 防火墙命令：ufw / firewalld / nftables / iptables / netsh 五套
     add / del / edit / toggle / move 各断言命令原文
  3. 路由命令：新增 / 删除 / 编辑 / 设为默认（Linux、Windows、BSD、网络设备）
  4. 风险分级：`ip route del default`、`iptables -F`、`netsh advfirewall reset`
     必须是 danger；`ufw status` 不能被误判成写
  5. 界面：伪造一个假会话，走「刷新 → 新增规则 → 列表多一行 → 有备份」，
     再走一遍「编辑路由 → 命令是 删+加」

数据目录在临时目录，不联网、不碰真机。
"""
import os
import re
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
TD = tempfile.mkdtemp(prefix="tbfw8_")

import app as appmod  # noqa: E402

appmod.APP_DIR = Path(TD)
appmod.DATA_DIR = Path(TD) / "data"
appmod.DATA_DIR.mkdir(parents=True, exist_ok=True)

import tkinter  # noqa: E402

from core import aiops  # noqa: E402
from core import firewall as fw  # noqa: E402
from core import routes  # noqa: E402
from core.detect import DeviceInfo  # noqa: E402
from ui import forms  # noqa: E402


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")


def eq(a, b, name):
    chk(a == b, name, f"期望 {b!r} 实得 {a!r}")


def has(text, frag, name):
    chk(frag in str(text), name, f"命令里没有 {frag!r}：{text!r}")


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


# ================================================================== 1. 探测
sec("1. 后端探测（纯函数，喂假 stdout）")

FAKE = {
    "ufw": (0, "Status: active\n\n     To                         Action      From\n"
               "     --                         ------      ----\n"
               "[ 1] 22/tcp                     ALLOW IN    Anywhere\n"),
    "firewalld": (0, "running"),
    "nftables": (0, "table inet filter {\n\tchain input {\n"
                    "\t\ttype filter hook input priority filter; policy accept;\n"
                    "\t}\n}\n"),
    "iptables": (0, "-P INPUT ACCEPT\n-P FORWARD ACCEPT\n"
                    "-A INPUT -p tcp --dport 22 -j ACCEPT\n"),
    "netsh": (0, "Domain Profile Settings:\n"
                 "----------------------------------------------------------------------\n"
                 "State                                 ON\n"
                 "Firewall Policy                       BlockInbound,AllowOutbound\n"),
    "pf": (0, "Status: Enabled for 0 days 00:01:22           Debug: Urgent\n"),
    "ipfw": (0, "65535 allow ip from any to any\n"),
}
LABEL = {"ufw": "ufw", "firewalld": "firewalld", "nftables": "nftables",
         "iptables": "iptables", "netsh": "netsh", "pf": "pf", "ipfw": "ipfw"}

for key in ("ufw", "firewalld", "nftables", "iptables", "netsh", "pf"):
    got, note = fw.detect_backend({key: FAKE[key]})
    eq(got, key, f"只给 {LABEL[key]} 的探测输出 → 认成 {LABEL[key]}")
    chk(bool(note), f"{LABEL[key]} 的探测有说明文字", note)
eq(fw.detect_backend({"ipfw": FAKE["ipfw"]})[0], "ipfw", "ipfw 也认得")
eq(fw.detect_backend({})[0], "", "什么都没有 → 认不出（空字符串）")
eq(fw.detect_backend({"ufw": (127, "command not found")})[0], "",
   "命令不存在（rc=127）不算探到")
eq(fw.detect_backend({"ufw": (0, "Status: inactive")})[0], "ufw",
   "ufw 装了但没启用：仍认 ufw（只是降一档）")

# 优先级：ufw 启用 + iptables 也有规则 → 用户的意图是 ufw
eq(fw.detect_backend({"ufw": FAKE["ufw"], "iptables": FAKE["iptables"]})[0],
   "ufw", "ufw 正在用时优先于 iptables")

# 优先级：ufw 没启用、iptables 有规则 → 用 iptables
eq(fw.detect_backend({"ufw": (0, "Status: inactive"),
                      "iptables": FAKE["iptables"]})[0], "iptables",
   "ufw 没启用时退到 iptables")
# 全都只是「装了」→ 按 PROBE_ORDER 取第一个
eq(fw.detect_backend({"nftables": (0, "nftables v1.0.6 (Lester Gooch)"),
                      "iptables": (0, "iptables v1.8.7 (nf_tables)")})[0],
   "nftables", "都只是装了 → 按优先级取 nftables")
eq(fw.rank("iptables", 0, "-P INPUT ACCEPT")[0], 1, "iptables 无规则算「装了」")
eq(fw.rank("iptables", 0, "-A INPUT -j DROP")[0], 2, "iptables 有 -A 算「在用」")
eq(fw.rank("netsh", 0, "State OFF\nProfile")[0], 2,
   "Windows 防火墙关着也算探到了（要看得到、改得了）")

# ================================================================== 2. 防火墙命令
sec("2. 防火墙写命令（五套后端）")

be_ufw = fw.UfwBackend()
add = be_ufw.add_cmds({"action": "allow", "port": "8080", "proto": "tcp"})
eq(add[0], "ufw allow 8080/tcp", "ufw 新增端口")
eq(be_ufw.add_cmds({"action": "deny", "port": "22", "proto": "tcp"})[0],
   "ufw deny 22/tcp", "ufw 拒绝端口")
eq(be_ufw.add_cmds({"action": "allow", "src": "192.168.1.0/24",
                    "port": "22", "proto": "tcp"})[0],
   "ufw allow from 192.168.1.0/24 to any port 22 proto tcp",
   "ufw 带来源")
rows_ufw = be_ufw.parse(FAKE["ufw"][1])
eq(len(rows_ufw), 1, "ufw 编号列表解析出一行")
eq(rows_ufw[0]["port"], "22", "ufw 解析出端口")
eq(rows_ufw[0]["proto"], "tcp", "ufw 解析出协议")
eq(rows_ufw[0]["action"], "allow", "ufw 解析出动作")
eq(rows_ufw[0]["src"], "Anywhere", "ufw 解析出来源")
eq(be_ufw.del_cmds(rows_ufw[0])[0], "ufw --force delete 1", "ufw 按编号删除")
chk(be_ufw.validate({"port": "80,443"}) is None, "ufw 端口列表合法")
chk(be_ufw.validate({"port": "abc"}) is not None, "ufw 乱写端口被拦住")
chk(fw.supports(be_ufw, "move"), "ufw 支持调顺序")

be_fwd = fw.FirewalldBackend()
eq(be_fwd.add_cmds({"port": "8080", "proto": "tcp"})[0],
   "firewall-cmd --permanent --add-port=8080/tcp", "firewalld 新增端口")
eq(be_fwd.add_cmds({"port": "8080", "proto": "tcp"})[1],
   "firewall-cmd --reload", "firewalld 加完要 reload")
eq(be_fwd.add_cmds({"name": "https", "_kind": "service"})[0],
   "firewall-cmd --permanent --add-service=https", "firewalld 按服务名放行")
eq(be_fwd.add_cmds({"port": "8080", "proto": "tcp",
                    "src": "10.0.0.0/8"})[0],
   "firewall-cmd --permanent --add-rich-rule='rule family=ipv4 "
   "source address=10.0.0.0/8 port port=8080 protocol=tcp accept'",
   "firewalld 带来源走 rich rule")
rows_fwd = be_fwd.parse(
    "public (active)\n  target: default\n  services: ssh dhcpv6-client\n"
    "  ports: 8080/tcp 443/tcp\n  rich rules:\n")
eq(len(rows_fwd), 4, "firewalld 解析出 2 服务 + 2 端口")
eq(be_fwd.del_cmds([r for r in rows_fwd if r["port"] == "8080"][0])[0],
   "firewall-cmd --permanent --remove-port=8080/tcp", "firewalld 删端口")
eq(be_fwd.del_cmds([r for r in rows_fwd if r["_kind"] == "service"][0])[0],
   "firewall-cmd --permanent --remove-service=ssh", "firewalld 删服务")

be_nft = fw.NftBackend()
eq(be_nft.add_cmds({"family": "inet", "table": "filter", "chain": "input",
                    "proto": "tcp", "port": "8080", "action": "accept"})[0],
   "nft add rule inet filter input tcp dport 8080 accept", "nftables 新增规则")
rows_nft = be_nft.parse(FAKE["nftables"][1])
chk(fw.supports(be_nft, "add") and not fw.supports(be_nft, "move"),
    "nftables 能增删但不能调顺序")

be_ipt = fw.IptablesBackend()
eq(be_ipt.add_cmds({"table": "filter", "chain": "INPUT", "proto": "tcp",
                    "port": "8080", "action": "ACCEPT"})[0],
   "iptables -t filter -A INPUT -p tcp --dport 8080 -j ACCEPT",
   "iptables 新增规则")
rows_ipt = be_ipt.parse(FAKE["iptables"][1])
eq(len(rows_ipt), 1, "iptables 解析出一行（-P 不算规则）")
eq(rows_ipt[0]["pos"], 1, "iptables 记下了链内位置")
eq(be_ipt.del_cmds(rows_ipt[0])[0],
   "iptables -t filter -D INPUT -p tcp --dport 22 -j ACCEPT",
   "iptables 删除（-A 换 -D）")
mv = be_ipt.move_cmds(rows_ipt[0], 1)
eq(mv[0], "iptables -t filter -D INPUT -p tcp --dport 22 -j ACCEPT",
   "iptables 下移：先删")
eq(mv[1], "iptables -t filter -I INPUT 2 -p tcp --dport 22 -j ACCEPT",
   "iptables 下移：再插到 2 号位")

be_win = fw.NetshBackend()
eq(be_win.add_cmds({"name": "Web 8080", "direction": "in", "action": "allow",
                    "proto": "TCP", "port": "8080", "src": "any"})[0],
   'netsh advfirewall firewall add rule name="Web 8080" dir=in action=allow '
   'protocol=TCP localport=8080 remoteip=any', "netsh 新增规则")
eq(be_win.toggle_cmds({"name": "Web 8080", "chain": "In"}, False)[0],
   'netsh advfirewall firewall set rule name="Web 8080" dir=in new enable=no',
   "netsh 停用一条规则")
eq(be_win.toggle_cmds({"name": "Web 8080", "chain": "In"}, True)[0],
   'netsh advfirewall firewall set rule name="Web 8080" dir=in new enable=yes',
   "netsh 启用一条规则")
old_win = {"name": "Web 8080", "action": "allow", "proto": "TCP",
           "port": "8080", "src": "any", "chain": "In"}
eq(be_win.set_cmds(old_win, {**old_win, "action": "block"})[0],
   'netsh advfirewall firewall set rule name="Web 8080" dir=in new '
   'action=block', "netsh 就地改成阻止（不是删了重建）")
chk(fw.supports(be_win, "toggle"), "netsh 支持单条启停")
chk(not fw.supports(fw.PfBackend(), "add"), "pf 是只读后端")

# 编辑 = 删旧 + 加新（通用路径）
ec = fw.edit_cmds(be_ufw, rows_ufw[0], {"action": "deny", "port": "22",
                                        "proto": "tcp"})
eq(len(ec), 2, "ufw 编辑出两条命令（删旧、加新）")
eq(ec[1], "ufw deny 22/tcp", "ufw 编辑的第二条是新的规则")

# 撤销
uc, why = fw.undo_cmds(be_ufw, "add", None, {"action": "allow", "port": "8080",
                                             "proto": "tcp"})
eq(len(uc), 1, f"撤销「新增」= 一条删除命令 {uc}")
uc2, why2 = fw.undo_cmds(be_ufw, "del", rows_ufw[0], None)
eq(uc2[0], "ufw allow 22/tcp", "撤销「删除」= 把原规则加回来")
uc3, why3 = fw.undo_cmds(fw.NftBackend(), "add", None,
                         {"family": "inet", "table": "filter",
                          "chain": "input", "action": "accept"})
eq(uc3, [], "nft 没有 handle 时老实说撤不回来")
chk(bool(why3), "撤不回来要给原因", why3)

# 备份
bp = fw.save_backup(TD, "ufw", FAKE["ufw"][1])
chk(Path(bp).is_file(), "备份文件真的写出来了", str(bp))
chk("ALLOW IN" in Path(bp).read_text(encoding="utf-8"),
    "备份里是规则原文")
eq(len(fw.list_backups(TD)), 1, "能列到刚写的那份备份")

# ================================================================== 3. 路由
sec("3. 路由写命令")

dev_lin = DeviceInfo(os_family="posix")
be_lin = routes.backend_of(dev_lin, None)
eq(be_lin.add_cmds({"dst": "10.8.0.0/24", "gateway": "192.168.1.1",
                    "dev": "eth0", "metric": ""})[0],
   "ip route add 10.8.0.0/24 via 192.168.1.1 dev eth0", "Linux 新增路由")
row_lin = routes.parsers.parse_route_linux(
    "default via 192.168.1.1 dev eth0 metric 100\n"
    "10.8.0.0/24 dev eth0 proto kernel scope link src 10.8.0.5\n")
eq(len(row_lin), 2, "Linux 路由表解析出两行")
eq(be_lin.del_cmds(row_lin[1])[0], "ip route del 10.8.0.0/24 dev eth0",
   "Linux 删除路由")
eq(routes.default_cmds(be_lin, {"gateway": "10.0.0.1", "dev": "eth1"})[0],
   "ip route replace default via 10.0.0.1 dev eth1",
   "Linux 设为默认用 replace（一步到位）")
ed = routes.edit_cmds(be_lin, row_lin[1],
                      {"dst": "10.8.0.0/24", "gateway": "10.0.0.9"})
eq(len(ed), 2, "Linux 编辑路由 = 删 + 加")
has(ed[1], "via 10.0.0.9", "编辑后的新网关进了命令")

dev_win = DeviceInfo(os_family="windows")
be_win_r = routes.backend_of(dev_win, None)
eq(be_win_r.add_cmds({"dst": "10.8.0.0/24", "gateway": "192.168.1.1",
                      "metric": "5", "persistent": True})[0],
   "route -p add 10.8.0.0 mask 255.255.255.0 192.168.1.1 metric 5",
   "Windows 新增路由（-p 持久）")
eq(be_win_r.del_cmds({"dst": "10.8.0.0/24"})[0],
   "route delete 10.8.0.0 mask 255.255.255.0", "Windows 删除路由")
dfw = routes.default_cmds(be_win_r, {"gateway": "192.168.1.1"})
eq(len(dfw), 2, "Windows 设为默认要删再加")
has(dfw[1], "route -p add 0.0.0.0 mask 0.0.0.0 192.168.1.1",
    "Windows 默认路由 -p 写注册表")

dev_bsd = DeviceInfo(os_family="darwin")
be_bsd = routes.backend_of(dev_bsd, None)
chk("route -n add" in be_bsd.add_cmds(
    {"dst": "10.8.0.0/24", "gateway": "192.168.1.1"})[0], "BSD 新增路由")

dev_net = DeviceInfo(os_family="network", vendor="cisco")
be_net = routes.backend_of(dev_net, None)
eq(be_net.add_cmds({"dst": "10.8.0.0/24", "gateway": "192.168.1.1"})[0],
   "ip route 10.8.0.0 255.255.255.0 192.168.1.1", "Cisco 静态路由")
eq(be_net.del_cmds({"dst": "10.8.0.0/24", "gateway": "192.168.1.1"})[0],
   "no ip route 10.8.0.0 255.255.255.0 192.168.1.1", "Cisco 删静态路由")
dev_h3c = DeviceInfo(os_family="network", vendor="h3c")
eq(routes.backend_of(dev_h3c, None).add_cmds(
    {"dst": "10.8.0.0/24", "gateway": "192.168.1.1"})[0],
   "ip route-static 10.8.0.0 255.255.255.0 192.168.1.1", "H3C 用 ip route-static")

uc_r, _ = routes.undo_cmds(be_lin, "add", None,
                           {"dst": "10.8.0.0/24", "gateway": "10.0.0.9"})
eq(len(uc_r), 1, f"撤销新增路由 = 一条删除 {uc_r}")

# ================================================================== 4. 风险
sec("4. 风险分级")

for cmd in ("ip route del default via 192.168.1.1",
            "ip route flush default",
            "route delete 0.0.0.0 mask 0.0.0.0",
            "iptables -F",
            "netsh advfirewall reset",
            "ufw reset",
            "nft flush ruleset",
            "ip route replace default via 10.0.0.1"):
    eq(aiops.classify_risk(cmd), "danger", f"{cmd} → danger")
for cmd in ("ip route add 10.8.0.0/24 via 192.168.1.1",
            "route -p add 10.8.0.0 mask 255.255.255.0 192.168.1.1",
            "ufw allow 8080/tcp",
            "firewall-cmd --permanent --add-port=8080/tcp",
            'netsh advfirewall firewall add rule name="x" dir=in action=allow'):
    eq(aiops.classify_risk(cmd), "write", f"{cmd} → write")
for cmd in ("ufw status", "ip route show", "netsh advfirewall show allprofiles",
            "iptables -S", "firewall-cmd --list-all"):
    eq(aiops.classify_risk(cmd), "read", f"{cmd} → read（只是看）")

# ================================================================== 5. 界面
sec("5. 界面：新增规则 / 编辑路由")


class FakeSession:
    """假 Ubuntu：ufw 规则列表会跟着 allow 命令长出来。"""

    alive = True
    host_kind = "unix"
    label = "fake-ubuntu"
    sudo_ok = False

    def __init__(self):
        self.cmds = []
        self.rules = ["[ 1] 22/tcp   ALLOW IN   Anywhere",
                      "[ 2] 80/tcp   ALLOW IN   Anywhere"]

    def can_admin(self):
        return True, ""

    def run_traced(self, cmd, timeout=30):
        self.cmds.append(cmd)
        if "ufw status numbered" in cmd:
            body = ("Status: active\n\n     To    Action    From\n"
                    "     --    ------    ----\n" + "\n".join(self.rules) + "\n")
            return 0, body, ""
        if "ip route" in cmd:
            return 0, ("default via 10.0.0.1 dev eth0 metric 100\n"
                       "10.8.0.0/24 dev eth0 proto kernel scope link src 10.8.0.5\n"), ""
        return 0, "", ""

    def run_admin(self, cmd, timeout=45):
        self.cmds.append(cmd)
        m = re.match(r"ufw allow (\d+)(?:/(tcp|udp))?$", cmd)
        if m:
            self.rules.append(f"[ {len(self.rules) + 1:,}] {m.group(1)}/{m.group(2) or 'tcp'}"
                              "   ALLOW IN   Anywhere".replace(",", ""))
        return 0, "", ""


win = appmod.ToolboxApp()
pump(win, 0.8)
fs = FakeSession()
win.session = fs
win.data_dir = Path(TD) / "data"
win.data_dir.mkdir(parents=True, exist_ok=True)

be = fw.UfwBackend(fs, DeviceInfo(os_family="posix"))
win.fw_backend = be

# 表单字段按后端生成（用户不用手打命令）
fields = win._fw_fields(be)
keys = [f["key"] for f in fields]
chk("port" in keys and "action" in keys, "ufw 表单有端口和动作", keys)
acts = [o[0] for o in next(f for f in fields if f["key"] == "action")["options"]]
chk("allow" in acts and "deny" in acts, "ufw 动作下拉有允许/拒绝", acts)
fwd_fields = win._fw_fields(fw.FirewalldBackend())
modes = next(f for f in fwd_fields if f["key"] == "mode")
chk([o[0] for o in modes["options"]] == ["port", "service"],
    "firewalld 表单能选「按端口 / 按服务名」")

win.fw_refresh()
pump(win, 0.8)
eq(len(win.tbl_fw.all_rows()), 2, "刷新后列表里两条 ufw 规则")
chk(win.btn_fw_add.cget("state") != "disabled"
    if "state" in win.btn_fw_add.keys() else True, "可写时「新增规则」按钮是亮的")

# 点「新增规则」：假装用户在表单里填了 8080/tcp
orig_open = forms.FormDialog.open
orig_confirm = forms.confirm
opened = {}


def fake_open(cls, parent, title, fields, values=None, **kw):
    opened["title"] = title
    opened["keys"] = [f["key"] for f in fields]
    return {"action": "allow", "port": "8080", "proto": "tcp", "src": "any"}


forms.FormDialog.open = classmethod(fake_open)
forms.confirm = lambda *a, **k: True
try:
    win.fw_add()
    pump(win, 1.0)
    eq(opened.get("title"), "新增防火墙规则", "弹的是新增规则表单")
    chk(any("ufw allow 8080/tcp" in c for c in fs.cmds),
        "真的执行了 ufw allow 8080/tcp", [c for c in fs.cmds][-4:])
    eq(len(win.tbl_fw.all_rows()), 3, "列表多了一行")
    bk = fw.list_backups(win.data_dir)
    chk(len(bk) >= 1, "改动前留了备份", [str(p) for p in bk])
    chk(win._fw_undo is not None, "记下了可撤销的上一步")

    # 编辑那条 22/tcp：命令必须是 删 + 加
    forms.FormDialog.open = classmethod(
        lambda cls, parent, title, fields, values=None, **kw:
        {"action": "deny", "port": "22", "proto": "tcp", "src": "any"})
    fs.cmds.clear()
    win.fw_edit(win.tbl_fw.all_rows()[0])
    pump(win, 1.0)
    chk(any("ufw --force delete 1" in c for c in fs.cmds),
        "编辑先删旧的", fs.cmds[-4:])
    chk(any("ufw deny 22/tcp" in c for c in fs.cmds),
        "再把改后的加进去", fs.cmds[-4:])

    # 路由：编辑一条静态路由 → ip route del + add
    fs.cmds.clear()
    win.routes_refresh()
    pump(win, 0.8)
    eq(len(win.tbl_route.all_rows()), 2, "路由表读到两行")
    routes_be = routes.backend_of(win._effective_device(), fs)
    eq(type(routes_be).__name__, "LinuxRoute", "本机（posix）用 ip route")
    forms.FormDialog.open = classmethod(
        lambda cls, parent, title, fields, values=None, **kw:
        {"dst": "10.8.0.0/24", "gateway": "10.0.0.254", "dev": "eth0",
         "metric": ""})
    win.route_edit(win.tbl_route.all_rows()[1])
    pump(win, 1.0)
    chk(any(c.startswith("ip route del 10.8.0.0/24") for c in fs.cmds),
        "编辑路由先删旧的", fs.cmds[-4:])
    chk(any(c.startswith("ip route add 10.8.0.0/24 via 10.0.0.254")
            for c in fs.cmds), "再加新的", fs.cmds[-4:])
    chk(len(fw.list_backups(win.data_dir, tag="route")) >= 1,
        "改路由也留了备份")
    # 撤销：把编辑前的加回来
    fs.cmds.clear()
    win.route_undo()
    pump(win, 1.0)
    chk(any("via 10.0.0.1" in c or "dev eth0" in c for c in fs.cmds),
        "撤销上一步真的发了命令", fs.cmds[-3:])
finally:
    forms.FormDialog.open = orig_open
    forms.confirm = orig_confirm

try:
    win.destroy()
except tkinter.TclError:
    pass

print("\n" + "=" * 52)
print(f"{'失败 ' + str(len(FAILS)) if FAILS else '全部通过'}：{PASS}/{PASS + FAIL}")
for x in FAILS:
    print("  ✗", x)
raise SystemExit(1 if FAILS else 0)
