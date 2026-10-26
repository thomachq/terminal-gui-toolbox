# -*- coding: utf-8 -*-
"""群晖防火墙（配置文件后端）的测试。

真机拿到的结构（DSM 7.2）：
  firewall_settings.json  {"profile":"default","status":true}
  1.json                   name="default"，rules.global 里 84 条
  2.json                   name="custom"，rules.global 里 1 条
文件名和 profile 名毫无关系，所以「当前 profile 是哪个文件」只能按 name 找。

这一批断言里最要紧的是**目标 IP 必须被拒绝**：群晖的规则表只有来源 IP，
静默丢掉目标会让人以为「只允许到某台机器」已经生效了。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import firewall as fw  # noqa: E402

_p = _f = _s = 0


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


def rule(policy=0, group=1, iplist=("192.168.3.0", "255.255.255.0"),
         itype=0, protocol=1, pgroup=1, plist=("2222",), enable=True,
         name="", port_direct=0):
    return {"adapterDirect": 1, "blLog": False,
            "chainList": ["FORWARD_FIREWALL", "INPUT_FIREWALL"],
            "enable": enable, "ipDirect": 1, "ipGroup": group,
            "ipList": list(iplist), "ipType": itype, "labelList": [],
            "name": name, "policy": policy, "portDirect": port_direct,
            "portGroup": pgroup, "portList": list(plist),
            "protocol": protocol, "ruleIndex": 0, "table": "filter"}


def doc(name, rules):
    return {"adapterPolicyMap": {"global": 2}, "name": name,
            "rules": {"global": rules}}


def feed(settings, files):
    """拼出 read_cmd 那种带 @@@TB- 分节的文本。"""
    out = ["@@@TB-SETTINGS", json.dumps(settings)]
    for fname, d in files.items():
        out.append(f"@@@TB-FILE {fname} {'a' * 64}")
        out.append(json.dumps(d))
    return "\n".join(out)


# ---------------------------------------------------------------- 1
sec("探测：认出群晖，别把它当普通 iptables")
r_tier, r_note = fw.rank("synofirewall", 0,
                        'SYNO_FW_OK\n{\n   "profile" : "default",\n'
                        '   "status" : true\n}')
chk(r_tier == 2, "有 SYNO_FW_OK → 正在用", (r_tier, r_note))
chk("default" in r_note, "说明里带上 profile 名", r_note)
chk(fw.rank("synofirewall", 1, "")[0] == 0, "没输出 → 0 分（非群晖正常跳过）")
chk(fw.rank("synofirewall", 0, "SYNO_FW_OK")[0] == 2, "没有 settings 也认")
chk(fw.PROBE_ORDER[0] == "synofirewall", "探测顺序排第一", fw.PROBE_ORDER[0])
chk(fw.BACKEND_CLASSES.get("synofirewall") is fw.SynoBackend, "注册表里有它")
_probe = fw.SynoBackend().probe_cmd()
# 真机上 /usr/syno/bin/synofirewall 是 -r-xr-x--- root:root，普通管理员
# 不能执行它；用 test -x 会让整条探测短路，群晖永远认不出来（实测踩过）。
chk("test -f" in _probe and "test -x" not in _probe,
    "探测用 test -f 而不是 test -x", _probe[:60])


# ---------------------------------------------------------------- 2
sec("读：profile 名与文件名无关，按 name 找当前 profile")
text = feed({"profile": "default", "status": True},
            {"1.json": doc("default", [rule(), rule(plist=["23333"])]),
             "2.json": doc("custom", [rule(plist=["6000"])])})
be = fw.SynoBackend()
rows = be.parse(text)
chk(len(rows) == 3, "两个 profile 的规则都读出来（共 3 条）", len(rows))
chk(be.cur_profile == "default", "当前 profile = default", be.cur_profile)
chk(be.cur_file == "1.json", "当前文件按 name 匹配到 1.json（不是按文件名猜）",
    be.cur_file)
chk(be.profiles == {"1.json": "default", "2.json": "custom"}, "文件名→名字映射",
    be.profiles)
chk(be.fw_on is True, "读到防火墙是启用状态")
chk(be.shas["1.json"] == "a" * 64, "每个文件的 sha256 都记下来（防并发改）")
chk(all(r["ref"].startswith("syno:") for r in rows), "ref 带 syno: 前缀")

# profile 名字对不上时不能瞎猜成第一个
text2 = feed({"profile": "不存在的", "status": False},
             {"9.json": doc("别的东西", [rule()])})
be2 = fw.SynoBackend()
be2.parse(text2)
chk(be2.cur_file == "9.json", "名字对不上时退回唯一的那个文件", be2.cur_file)
chk(be2.fw_on is False, "读到防火墙关闭")


# ---------------------------------------------------------------- 3
sec("字段语义：来源 / 协议 / 策略 / 端口")
rows = be.parse(feed({"profile": "default", "status": True}, {"1.json": doc("default", [
    rule(),                                          # tcp 2222 ← /24
    rule(policy=1),                                  # 拒绝
    rule(group=0, iplist=()),                        # 来源 any
    rule(iplist=("27.153.133.133",)),                # 单主机
    rule(protocol=2, plist=("53",)),                 # udp
    rule(protocol=3, pgroup=3, plist=()),            # 全部协议 + 所有端口
    rule(protocol=4, plist=()),                      # 其它
    rule(group=3, iplist=("CN",), itype=2),          # 位置
])}))
by_port = {}
for r in rows:
    by_port.setdefault(r["port"], []).append(r)

chk(rows[0]["src"] == "192.168.3.0/24", "掩码转成 CIDR 显示", rows[0]["src"])
chk(rows[0]["dst"] == "any", "目标恒为 any（群晖没有这一维）", rows[0]["dst"])
chk(rows[0]["action"] == "allow" and rows[1]["action"] == "deny",
    "policy 0/1 → 允许/拒绝")
chk(rows[2]["src"] == "any", "空 ipList → any", rows[2]["src"])
chk(rows[2]["editable"] is True, "来源 any 的规则可编辑")
chk(rows[3]["src"] == "27.153.133.133", "单主机原样显示", rows[3]["src"])
chk(rows[4]["proto"] == "udp", "protocol 2 → udp", rows[4]["proto"])
chk(rows[5]["proto"] == "" and rows[5]["port"] == "所有",
    "protocol 3 → 协议不限；空 portList → 所有端口",
    (rows[5]["proto"], rows[5]["port"]))
chk(rows[5]["editable"] is True,
    "portGroup 3 配空端口列表 = 所有端口，不能判成只读")
chk(rows[6]["editable"] is False and "IPSec" in rows[6]["_readonly_why"],
    "protocol 4 只读并说明原因", rows[6]["_readonly_why"])
chk(rows[7]["editable"] is False and "位置" in rows[7]["_readonly_why"],
    "GeoIP 来源只读", rows[7]["_readonly_why"])
chk(rows[7]["src"] == "CN", "位置来源显示地名", rows[7]["src"])


# ---------------------------------------------------------------- 4
sec("目标 IP：必须明确拒绝，不能静默丢掉")
be = fw.SynoBackend()
be.parse(feed({"profile": "default", "status": True}, {"1.json": doc("default", [rule()])}))
for dst in ("10.1.0.0/16", "192.168.3.10", "2001:db8::/32"):
    why = be.validate({"action": "allow", "proto": "tcp", "port": "22",
                       "src": "any", "dst": dst})
    chk(bool(why) and "目标" in why, f"填了目标 {dst} 会被拦下并解释", why or "没拦")
try:
    be.add_cmds({"action": "allow", "proto": "tcp", "port": "22",
                 "src": "any", "dst": "10.1.0.0/16"})
    chk(False, "add_cmds 直接抛错")
except fw.FirewallError as e:
    chk("目标" in str(e), "add_cmds 也抛（不只是 validate）")
# 只写 any 的规则能过
ok_dst = be.validate({"action": "allow", "proto": "tcp", "port": "22",
                      "src": "any", "dst": "any"})
chk(ok_dst is None, "目标写 any 正常通过", ok_dst)


# ---------------------------------------------------------------- 5
sec("其它拒绝：IPv6 / 服务名端口 / 乱写的地址")
chk(be.validate({"action": "allow", "proto": "tcp", "port": "22",
                 "src": "2001:db8::/32", "dst": "any"}) is not None,
    "IPv6 被拒（ipType 另有一套，没验证过不猜）")
chk(be.validate({"action": "allow", "proto": "tcp", "port": "ssh",
                 "src": "any", "dst": "any"}) is not None, "端口写服务名被拒")
chk(be.validate({"action": "allow", "proto": "tcp", "port": "22",
                 "src": "192.168.1.999/24", "dst": "any"}) is not None,
    "写错的地址被拒")
chk(be.validate({"action": "allow", "proto": "icmp", "port": "",
                 "src": "any", "dst": "any"}) is not None, "icmp 被拒（只支持 tcp/udp/全部）")
chk(be.validate({"action": "allow", "proto": "tcp", "port": "22",
                 "src": "any", "dst": "any"}) is None, "正常规则通过")


# ---------------------------------------------------------------- 6
sec("写：新规则的字段组合（只写验证过的）")
be = fw.SynoBackend()
be.parse(feed({"profile": "default", "status": True}, {"1.json": doc("default", [rule()])}))
item = be._rule_item({"action": "deny", "proto": "udp", "port": "80,443",
                      "src": "10.0.0.0/8", "dst": "any", "comment": "备注"}, "10.0.0.0/8")
chk(item["policy"] == 1, "deny → policy 1")
chk(item["protocol"] == 2, "udp → protocol 2")
chk(item["ipGroup"] == 1 and item["ipList"] == ["10.0.0.0", "255.0.0.0"],
    "来源网段 → ipGroup 1 + 地址/掩码", (item["ipGroup"], item["ipList"]))
chk(item["portGroup"] == 1, "有端口 → portGroup 1")
chk(item["portList"] == ["80", "443"], "逗号端口拆成列表", item["portList"])
chk(item["ipDirect"] == 1, "ipDirect 恒为 1（来源方向）")
chk(item["enable"] is True, "默认启用")
chk(item["chainList"] == ["FORWARD_FIREWALL", "INPUT_FIREWALL"], "链固定两条")
chk("dst" not in json.dumps(item), "写进去的规则里根本没有目标字段")

anyitem = be._rule_item({"action": "allow", "proto": "tcp", "src": "any"}, "")
chk(anyitem["ipGroup"] == 0 and anyitem["ipList"] == [],
    "来源 any → ipGroup 0 + 空列表")
chk(anyitem["portGroup"] == 0, "没端口 → portGroup 0（所有端口）")

# 编辑「所有端口」的原规则要沿用它的 portGroup（0 和 3 都是所有端口）
keep3 = rule(pgroup=3, plist=())
kept = be._rule_item({"action": "allow", "proto": "tcp", "port": "",
                      "src": "any"}, "", keep=keep3)
chk(kept["portGroup"] == 3, "编辑时保留原来的 portGroup 3", kept["portGroup"])


# ---------------------------------------------------------------- 7
sec("写：多来源展开 / 位置 / 备份与回滚都写在同一条 shell 里")
cmds = be.add_cmds({"action": "allow", "proto": "tcp", "port": "2222",
                    "src": "192.168.3.0/24, 10.0.0.0/8", "dst": "any",
                    "comment": "多来源"})
chk(len(cmds) == 1, "两个来源合成一条命令（只 reload 一次）", len(cmds))
sh = cmds[0]
chk("sha256sum" in sh and "$CUR" in sh, "先比 sha256（有人改过就不写）")
chk("TB:STALE" in sh, "sha 对不上时明确报「先刷新」")
chk("cp -p" in sh and ".tb-bak-" in sh, "写前原文备份")
chk("--update" in sh and "--reload" in sh, "改完 update + reload")
chk("TB:EMPTY" in sh and "cp -p \"$B\" \"$F\"" in sh, "重载后规则空了自动还原")
chk("jq -e ." in sh, "写出的 JSON 先过语法校验")
chk("os.replace" in sh and "0o644" in sh, "临时文件替换 + 权限 644")
chk(" -d " not in sh.split("TBEOF")[0], "命令里没有 -d 目标参数")

be.parse(feed({"profile": "default", "status": True},
              {"1.json": doc("default", [rule(), rule()])}))
chk(len(be._rows_global()) == 2, "内部记住当前 profile 的规则数组")


# ---------------------------------------------------------------- 8
sec("写：删 / 改 / 启停 / 调序")
rows = be.parse(feed({"profile": "default", "status": True},
                     {"1.json": doc("default", [rule(plist=["1111"]),
                                                rule(plist=["2222"]),
                                                rule(plist=["3333"])])}))
d = be.del_cmds(rows[1])
chk(len(d) == 1 and ".tb-bak-" in d[0], "删除带备份")
t = be.toggle_cmds(rows[0], False)
chk("toggle" in t[0] or "arr[i][\"enable\"]" in t[0], "启停走同一条 shell")
s = be.set_cmds(rows[0], {"action": "deny", "proto": "udp", "port": "99",
                          "src": "any", "dst": "any"})
chk(len(s) == 1, "就地替换只用一条命令（不留中间态）", len(s))
m = be.move_cmds(rows[2], -1)
chk(len(m) == 1, "上移一条命令")
try:
    be.move_cmds(rows[0], -1)
    chk(False, "第一条再上移应该报错")
except fw.FirewallError:
    chk(True, "已经在最前面时明确报错")
try:
    be.del_cmds({"ref": "x"})
    chk(False, "没有位置信息应该报错")
except fw.FirewallError:
    chk(True, "缺位置信息时拒绝删除")
# 只读规则不给改
ro = [r for r in be.parse(feed({"profile": "default", "status": True},
                              {"1.json": doc("default", [rule(protocol=4, plist=())])}))][0]
try:
    be.set_cmds(ro, {"action": "allow", "proto": "tcp", "port": "1",
                     "src": "any", "dst": "any"})
    chk(False, "只读规则不该能改")
except fw.FirewallError as e:
    chk("只读" in str(e) or "未验证" in str(e), "只读规则给出原因", str(e)[:60])


# ---------------------------------------------------------------- 9
sec("生成的 shell 必须过 bash -n")
bash = ""
for cand in (r"R:/WorkBuddy/resources/vendor/PortableGit/bin/bash.exe",
             shutil.which("bash") or ""):
    if cand and os.path.exists(cand):
        bash = cand
        break
if not bash:
    print("  --   没找到可用的 bash，跳过语法检查")
else:
    bad = []
    probe = fw.SynoBackend()
    probe.parse(feed({"profile": "default", "status": True},
                     {"1.json": doc("default", [rule(), rule(), rule()])}))
    prs = probe.parse(feed({"profile": "default", "status": True},
                           {"1.json": doc("default", [rule(), rule(), rule()])}))
    todo = {"add": probe.add_cmds({"action": "allow", "proto": "tcp", "port": "2222",
                                   "src": "192.168.3.0/24", "dst": "any",
                                   "comment": "中文"}),
            "set": probe.set_cmds(prs[0], {"action": "deny", "proto": "udp",
                                           "port": "88", "src": "any",
                                           "dst": "any"}),
            "del": probe.del_cmds(prs[1]),
            "toggle": probe.toggle_cmds(prs[1], False),
            "move": probe.move_cmds(prs[2], -1)}
    for k, cs in todo.items():
        for c in cs:
            p = tempfile.mktemp(suffix=".sh")
            io.open(p, "w", encoding="utf-8", newline="\n").write(c)
            r = subprocess.run([bash, "-n", p], capture_output=True)
            if r.returncode != 0:
                bad.append(f"{k}: {r.stderr.decode('utf-8', 'replace')[:120]}")
            os.unlink(p)
    chk(not bad, f"5 类命令语法都合法（{sum(len(v) for v in todo.values())} 条）",
        "；".join(bad))


# ---------------------------------------------------------------- 10
sec("锁死判定：SSH 端口不一定还是 22")
from pages.firewall_page import _port_hits  # noqa: E402

chk(_port_hits("2222", "2222"), "认得出自定义 SSH 端口（真机上就是 2222）")
chk(not _port_hits("2222", "22"), "2222 不命中 22（不能拿子串判）")
chk(_port_hits("22,2222", "2222"), "逗号列表里能挑出来")
chk(not _port_hits("22222", "2222"), "22222 不能当成 2222（不能用子串）")
chk(_port_hits("1000-3000", "2222"), "端口范围能命中")
chk(not _port_hits("1000-3000", "80"), "范围外的端口不命中")
chk(not _port_hits("所有", "22"), "「所有」交给另一个分支判")


# ---------------------------------------------------------------- 11
sec("说明与提示")
probe = fw.SynoBackend()
probe.parse(feed({"profile": "default", "status": True}, {"1.json": doc("default", [rule()])}))
note = probe.persist_note()
chk("1.json" in note and "default" in note, "持久化说明点明文件与 profile", note)
chk("reload" in note, "说明改完会 reload")
off = fw.SynoBackend()
off.parse(feed({"profile": "default", "status": False}, {"1.json": doc("default", [rule()])}))
chk("关闭" in off.persist_note(), "防火墙关着时明确提示", off.persist_note())


print(f"\n结果：{_p} 通过 / {_f} 失败")
if _f:
    print(" 失败项见上面的 FAIL 行")
sys.exit(1 if _f else 0)
