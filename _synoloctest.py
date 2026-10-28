# -*- coding: utf-8 -*-
"""群晖「位置（国家）」规则 —— 不与真机配置发生任何写入的验证。

用户要求（2026-10-26 22:47）：
  「补上吧，但是你不要修改群晖里的实际配置，但要保证能用」

不改配置怎么证明能用？**逐字段对齐真机已有的位置规则**。
基准来自真机只读取回的 /usr/syno/etc/firewall.d/1.json 第 10 条
（ipGroup=3 的第一条，原文见 BENCH）：

    {"adapterDirect":1,"blLog":false,
     "chainList":["FORWARD_FIREWALL","INPUT_FIREWALL"],
     "enable":false,"ipDirect":1,"ipGroup":3,"ipList":["CN"],"ipType":2,
     "labelList":[],"name":"","policy":0,"portDirect":0,"portGroup":1,
     "portList":["39443"],"protocol":1,"ruleIndex":9,"table":"filter"}

如果同一组合成出来的 dict 与它逐键相等（enable / ruleIndex 是我们主动
决定的差异，见 DYN），那 synofirewall 看到的输入就跟 DSM 自己写的一模一样，
没有理由不认 —— 这比「跑一次 --update 看结果」还硬，因为没有任何中间态风险。

真机另外三条实证（同一份 1.json）：
  · 9 条位置规则的 ipList 全是 ["CN"] —— 没有多国家样本，所以只放一个；
  · (ipGroup, ipType) 严格绑定：3↔2、5↔2、0/1↔0；
  · 位置规则在 iptables 里是 `-m geoip --source-country CN`，一条顶一整国。
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


# 真机原文（只读取回，见文件头注释）
BENCH = {
    "adapterDirect": 1, "blLog": False,
    "chainList": ["FORWARD_FIREWALL", "INPUT_FIREWALL"],
    "enable": False, "ipDirect": 1, "ipGroup": 3, "ipList": ["CN"], "ipType": 2,
    "labelList": [], "name": "", "policy": 0, "portDirect": 0, "portGroup": 1,
    "portList": ["39443"], "protocol": 1, "ruleIndex": 9, "table": "filter",
}
# 这两个键是「新建」与「已存在」的必然差异，不参与逐字段比对：
#   enable     —— 真机那条是用户手动禁用的；新建默认启用
#   ruleIndex  —— 插入位置由后端按当前条数算
DYN = {"enable", "ruleIndex"}

be = mk(0)

# ---------------------------------------------------------------- 1
sec("位置码解析")
chk(fw._loc_parts("CN") == ["CN"], "单个国码")
chk(fw._loc_parts(" cn ") == ["CN"], "小写与空格自动归一")
chk(fw._loc_parts("CN,HK") == ["CN", "HK"], "英文逗号拆分")
chk(fw._loc_parts("CN，HK") == ["CN", "HK"], "中文逗号也认")
chk(fw._loc_parts("") == [], "空 → 空")
chk(fw._loc_code("cn") == "CN", "_loc_code 归一成大写")
chk(fw._loc_code("中国") == "", "中文国名不是合法码（不会被误当代码）")
chk(fw._loc_code("CHN") == "", "三字母不认")
chk(fw._loc_code("") == "", "空 → 空串")

# ---------------------------------------------------------------- 2
sec("位置规则 vs 真机原文：逐字段比对")
got = be._rule_item({"loc": "CN", "port": "39443", "proto": "tcp",
                     "action": "allow", "comment": ""}, "")
diff = {k: (BENCH.get(k), got.get(k)) for k in set(BENCH) | set(got)
        if k not in DYN and BENCH.get(k) != got.get(k)}
chk(not diff, "与真机那条位置规则逐键相等（只放过 enable / ruleIndex）", diff)
chk(got.get("ipGroup") == 3, "ipGroup = 3（位置）", got.get("ipGroup"))
chk(got.get("ipType") == 2, "ipType = 2（跟 ipGroup 绑死）", got.get("ipType"))
chk(got.get("ipList") == ["CN"], "ipList = [\"CN\"]（国家码，不是网段）",
    got.get("ipList"))
chk(got.get("portGroup") == 1, "有端口 → portGroup 1", got.get("portGroup"))
chk(got.get("protocol") == 1, "tcp → protocol 1", got.get("protocol"))
chk(got.get("policy") == 0, "允许 → policy 0", got.get("policy"))
chk(got.get("enable") is True, "新建默认启用")
chk(set(got) == set(BENCH), "键集合完全一致（不多不少）",
    set(got) ^ set(BENCH))

# 所有端口的位置规则：portGroup 应该 0
got2 = be._rule_item({"loc": "HK", "port": "", "proto": "any",
                      "action": "deny", "comment": ""}, "")
chk(got2.get("portGroup") == 0, "没端口 → portGroup 0（真机 6 条就是 0）",
    got2.get("portGroup"))
chk(got2.get("portList") == [], "没端口 → portList 空")
chk(got2.get("protocol") == 3, "全部协议 → protocol 3", got2.get("protocol"))
chk(got2.get("policy") == 1, "拒绝 → policy 1", got2.get("policy"))
chk(got2.get("ipList") == ["HK"], "换国家只换 ipList")

# 对照：自定义网段那条仍是 ipGroup 1 / ipType 0（别被位置分支带偏）
got3 = be._rule_item({"src": "192.168.3.0/24", "port": "2222",
                      "proto": "tcp", "action": "allow"}, "192.168.3.0/24")
chk((got3.get("ipGroup"), got3.get("ipType")) == (1, 0),
    "自定义网段仍是 (1, 0)", (got3.get("ipGroup"), got3.get("ipType")))
chk(got3.get("ipList") == ["192.168.3.0", "255.255.255.0"],
    "自定义仍是 [地址,掩码]", got3.get("ipList"))

# ---------------------------------------------------------------- 3
sec("校验：位置能过，但只放一个、不许和来源地址并存")
ok_base = {"action": "allow", "proto": "tcp", "port": "39443"}
chk(be.validate({**ok_base, "loc": "CN", "src": "any"}) is None,
    "loc=CN + src=any → 通过")
chk(be.validate({**ok_base, "loc": "cn"}) is None, "小写 cn 也通过（归一化）")
e_multi = be.validate({**ok_base, "loc": "CN,HK"})
chk(e_multi is not None, "两个国家 → 拒绝（没有多值实证）")
chk(e_multi and "单值" in e_multi, "报错说明为什么只放一个", e_multi or "")
chk(e_multi and "分条" in e_multi, "报错给出路：分条建", e_multi or "")
e_bad = be.validate({**ok_base, "loc": "中国"})
chk(e_bad is not None, "中文国名 → 拒绝", e_bad or "")
chk(e_bad and "两字母" in e_bad, "报错点明要两字母代码", e_bad or "")
e_both = be.validate({**ok_base, "loc": "CN", "src": "192.168.3.0/24"})
chk(e_both is not None, "位置 + 来源地址同时填 → 拒绝", e_both or "")
chk(e_both and "只能填一个" in e_both, "报错说明二选一", e_both or "")

# ---------------------------------------------------------------- 4
sec("额度：位置规则只占 1 条（不像网段一段一条）")
be85 = mk(85)
chk(be85.validate({**ok_base, "loc": "CN"}) is None,
    "85 条已用 + 1 条位置规则 → 通过")
full = mk(100)
chk(full.validate({**ok_base, "loc": "CN"}) is not None,
    "满额 100 条时再加位置规则 → 拒绝（规则数也是数）")
chk(full.validate({**ok_base, "loc": "CN"}, replacing=1) is None,
    "满额时「改一条成位置规则」→ 通过")

# ---------------------------------------------------------------- 5
sec("下发：位置规则只生成一条，不按网段拆")
cmds = be85.add_cmds({**ok_base, "loc": "CN", "src": "any"})
chk(len(cmds) == 1, "只生成 1 条命令", len(cmds))
blob = cmds[0]
chk("base64" in blob, "规则体走 base64 传（不会被引号吃掉）", blob[:40])
# 把 payload 解出来，确认里面真的是 ipGroup 3 + ["CN"]
import base64 as _b64  # noqa: E402
import re as _re  # noqa: E402

m = _re.search(r'python3 - "\$F" ([A-Za-z0-9+/=]+)', blob)
chk(m is not None, "命令里能找到 base64 载荷（python3 - \"$F\" <b64>）")
if m:
    try:
        payload = json.loads(_b64.b64decode(m.group(1)).decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        payload = None
        chk(False, "载荷能解析成 JSON", e)
    if payload is not None:
        items = payload.get("items") or payload.get("rules") or []
        if items and isinstance(items[0], dict):
            it = items[0]
            chk(it.get("ipGroup") == 3 and it.get("ipType") == 2
                and it.get("ipList") == ["CN"],
                "载荷里的规则就是位置规则", {k: it.get(k) for k in
                                       ("ipGroup", "ipType", "ipList")})
        else:
            chk(False, "载荷里有 items/rules", list(payload)[:6])

# ---------------------------------------------------------------- 6
sec("界面：位置下拉只给国家/地区，且含港澳台")
import pages.firewall_page as fp  # noqa: E402


class _B:
    def __init__(self, key):
        self.key = key


page = fp.FirewallPage.__new__(fp.FirewallPage)
fields = page._fw_fields(_B("synofirewall"))
locs = [x for x in fields if x.get("key") == "loc"]
chk(len(locs) == 1, "群晖表单里有「位置（国家）」字段")
if locs:
    f0 = locs[0]
    opts = f0.get("options") or []
    codes = [o[0] for o in opts if isinstance(o, (tuple, list))]
    chk(f0.get("kind") == "select", "是下拉而不是手填（填错就是永不匹配的规则）")
    chk("" in codes, "有「不用位置」的空选项")
    for cc in ("CN", "HK", "MO", "TW"):
        chk(cc in codes, f"下拉里有 {cc}", codes[:6])
    chk(len(codes) >= 40, "国家数够用（≥40）", len(codes))
    hint = f0.get("hint") or ""
    chk("没有省份" in hint, "提示明确没有省份", hint)
    chk("别填" in hint or "只能" in hint, "提示讲清与来源地址二选一", hint)
# 省级不该出现在位置下拉里
if locs:
    codes = [o[0] for o in (locs[0].get("options") or [])
             if isinstance(o, (tuple, list))]
    chk(not any(str(c).isdigit() for c in codes if c),
        "没有混进省级行政区划码（110000 这类）")

# ---------------------------------------------------------------- 7
sec("其它后端不带这个字段（别把群晖的东西塞给别人）")
for key in ("iptables", "nftables", "ufw", "netsh"):
    fs = page._fw_fields(_B(key))
    chk(all(x.get("key") != "loc" for x in fs), f"{key} 表单里没有 loc 字段")

print(f"\n结果：{_p} 通过 / {_f} 失败")
if _f:
    print(" 失败项见上面的 FAIL 行")
sys.exit(1 if _f else 0)
