# -*- coding: utf-8 -*-
"""群晖「按地区过滤」的能力边界 —— 闸门测试。

起因（用户 2026-10-26 22:43）：
  「如果防火墙不支持精确到省份的话，是不是得给用户一个限制」

真机实测（DSM 7.4.1 / 192.168.3.12）：
  · 位置规则 → iptables `-m geoip --source-country CN`，一条规则吃一整个国家；
  · 自定义 IP → `-s 192.168.3.0/24`，**一段一条规则**，iprange 模块 0 条；
  · 位置库只有 /var/db/geoip-database/xt_geoip/<CC>.iv4|iv6（254 国 × 2），
    没有省级文件（CN.iv4 解出来是 8177 个 [start,end] 区间）；
  · 设备上 64 MB 的 GeoLite2-City.mmdb（含省市）防火墙**根本不读**；
  · ipset 完全没有 → ipset 方案在群晖上不成立；
  · 官方规格：规则中的位置上限 15、规则数量上限 100。

所以三道闸必须钉死：
  A. 规则总数超 100 就拒绝（不是 _MAX_RULES 那个 400，那是单条拆几段）；
  B. 编辑时要把旧规则占的额度还回来，否则「改一条」会被误拦；
  C. 群晖后端的地址库不许出现「中国省级」——群晖没有省级位置库。
"""
from __future__ import annotations

import json
import sys
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
         itype=0, protocol=1, pgroup=1, plist=("2222",)):
    return {"adapterDirect": 1, "blLog": False,
            "chainList": ["FORWARD_FIREWALL", "INPUT_FIREWALL"],
            "enable": True, "ipDirect": 1, "ipGroup": group,
            "ipList": list(iplist), "ipType": itype, "labelList": [],
            "name": "", "policy": policy, "portDirect": 0,
            "portGroup": pgroup, "portList": list(plist),
            "protocol": protocol, "ruleIndex": 0, "table": "filter"}


def mk(n: int) -> fw.SynoBackend:
    """造一个已经有 n 条规则的群晖后端（真机 1.json 就有 84 条）。"""
    doc = {"adapterPolicyMap": {"global": 2}, "name": "default",
           "rules": {"global": [rule() for _ in range(n)]}}
    text = "\n".join([
        "@@@TB-SETTINGS",
        json.dumps({"profile": "default", "status": True}),
        "@@@TB-FILE 1.json " + "a" * 64,
        json.dumps(doc),
    ])
    be = fw.SynoBackend()
    be.parse(text)
    return be


def src(*nets):
    return ",".join(nets)


# ---------------------------------------------------------------- 1
sec("能力声明：后端要自己说清「能到多细」")
chk(fw.FirewallBackend.max_rules == 0, "基类默认不限（0）")
chk(fw.FirewallBackend.one_cidr_per_rule is False, "基类默认不假定一段一条")
chk(fw.FirewallBackend.location_granularity == "any", "基类默认不限制粒度")
chk(fw.SynoBackend.max_rules == 100, "群晖规则总数上限 100（官方规格）",
    fw.SynoBackend.max_rules)
chk(fw.SynoBackend.one_cidr_per_rule is True, "群晖一段一条规则（真机 iptables 实证）")
chk(fw.SynoBackend.location_granularity == "country", "群晖位置只能到国家")
chk("省份" in fw.SynoBackend.location_note, "说明里点明没有省份",
    fw.SynoBackend.location_note)
for cls in (fw.UfwBackend, fw.NftBackend, fw.IptablesBackend):
    chk(getattr(cls, "max_rules", None) == 0
        and getattr(cls, "location_granularity", None) == "any",
        f"{cls.label} 没被误标上限/粒度")

# ---------------------------------------------------------------- 2
sec("闸门 A：规则总数超 100 必须拒绝")
be = mk(85)                                     # 真机就是这个量级
chk(len(be._rows_global()) == 85, "造出 85 条", len(be._rows_global()))
err = be.validate({"action": "allow", "proto": "tcp", "port": "22",
                   "src": src(*[f"10.{i}.0.0/16" for i in range(20)])})
chk(err is not None, "85 条时再加 20 段 → 拒绝")
chk(err and "100" in err, "报错里给出真实上限 100", err or "")
chk(err and "85" in err, "报错里给出当前已有条数", err or "")
chk(err and "省份" in err, "报错里解释「按省走不通」", err or "")
chk(err and "iptables" in err, "报错里给替代方案", err or "")

err2 = be.validate({"action": "allow", "proto": "tcp", "port": "22",
                    "src": src(*[f"10.{i}.0.0/16" for i in range(15)])})
chk(err2 is None, "85 条时加 15 段（正好到 100）→ 通过", err2 or "")

err3 = be.validate({"action": "allow", "proto": "tcp", "port": "22",
                    "src": src(*[f"10.{i}.0.0/16" for i in range(16)])})
chk(err3 is not None, "85 条时加 16 段（101）→ 拒绝", err3 or "")

# ---------------------------------------------------------------- 3
sec("闸门 B：编辑要把旧规则占的额度还回来")
full = mk(100)
chk(len(full._rows_global()) == 100, "造一个满额 100 条的配置")
chk(full.validate({"action": "allow", "proto": "tcp", "port": "22",
                   "src": "10.1.0.0/16"}) is not None,
    "满额时新增 1 条 → 拒绝")
chk(full.validate({"action": "allow", "proto": "tcp", "port": "22",
                   "src": "10.1.0.0/16"}, replacing=1) is None,
    "满额时「改这一条」→ 通过（额度还回来了）")
chk(full.validate({"action": "allow", "proto": "tcp", "port": "22",
                   "src": src("10.1.0.0/16", "10.2.0.0/16")},
                  replacing=1) is not None,
    "满额时改成 2 段（净增 1）→ 拒绝")

# ---------------------------------------------------------------- 4
sec("闸门 C：群晖的地址库不许出现省级")
import pages.firewall_page as fp  # noqa: E402


class _B:
    """_fw_fields 只读 backend.key，不需要真后端。"""

    def __init__(self, key):
        self.key = key


# 不建窗口：FirewallPage 是普通类，_fw_fields 是纯函数（只读 self 上的无用东西）
page = fp.FirewallPage.__new__(fp.FirewallPage)


def fields_for(key):
    try:
        return page._fw_fields(_B(key))
    except Exception as e:  # noqa: BLE001
        chk(False, f"_fw_fields({key}) 能跑通", e)
        return None


f380 = fields_for("synofirewall")
if f380:
    srcf = [x for x in f380 if x.get("key") == "src"][0]
    chk(srcf.get("lib_groups") == ("country", "common"),
        "群晖来源地址的地址库只给 国家/地区 + 常用网段",
        srcf.get("lib_groups"))
    chk("province" not in (srcf.get("lib_groups") or ()),
        "地址库里没有「中国省级」", srcf.get("lib_groups"))
    hint = srcf.get("hint") or ""
    chk("位置" in hint, "提示里讲清「按地区只能用位置」", hint[:40])
    chk("国家" in hint, "提示里讲清位置只到国家/地区", hint[:40])
    chk("100" in hint, "提示里给出 100 条上限", hint[:40])

# 别的后端不该被加限制
f_ip = fields_for("iptables")
if f_ip:
    s2 = [x for x in f_ip if x.get("key") == "src"][0]
    chk("lib_groups" not in s2, "iptables 的地址库不设限（省级照样能选）")

# ---------------------------------------------------------------- 5
sec("地址库对话框：allowed 过滤真的生效")
ipick_src = (HERE / "ui" / "ipick.py").read_text(encoding="utf-8")
chk("allowed=None" in ipick_src, "open() 接受 allowed 参数")
chk("def __init__" in ipick_src and "allowed=None" in ipick_src,
    "__init__ 也接受 allowed")
chk("self._groups = [g for g in iplib.GROUPS" in ipick_src,
    "按 allowed 过滤分组")
chk("self._groups = list(iplib.GROUPS)" in ipick_src,
    "allowed 全不匹配时退回全部（不至于空白对话框）")
chk("for k, lab, _fn in self._groups" in ipick_src,
    "分段控件用过滤后的分组")
chk("没有省级" in ipick_src or "没有省份" in ipick_src,
    "顶部说明会告诉用户为什么少了省级")

forms_src = (HERE / "ui" / "forms.py").read_text(encoding="utf-8")
chk("allowed=f.get(\"lib_groups\")" in forms_src,
    "表单把字段里的 lib_groups 传给对话框")

fwpage_src = (HERE / "pages" / "firewall_page.py").read_text(encoding="utf-8")
chk('"lib_groups": ("country", "common")' in fwpage_src,
    "群晖字段上写死了 lib_groups")

# ---------------------------------------------------------------- 6
sec("别把两个上限搞混")
chk(fw._MAX_RULES == 400, "单条规则拆分的软上限还是 400（没被顺手改掉）",
    fw._MAX_RULES)
chk(fw.SynoBackend.max_rules != fw._MAX_RULES,
    "400（单条拆几段）≠ 100（总规则数），两个数不是一回事")
# 其它后端仍然能正常校验（签名加默认参数不该影响它们）
u = fw.UfwBackend()
try:
    u.validate({"action": "allow", "proto": "tcp", "port": "22",
                "src": "10.0.0.0/8"})
    chk(True, "UfwBackend.validate 仍可单参调用")
except TypeError as e:
    chk(False, "UfwBackend.validate 仍可单参调用", e)


print(f"\n结果：{_p} 通过 / {_f} 失败")
if _f:
    print(" 失败项见上面的 FAIL 行")
sys.exit(1 if _f else 0)
