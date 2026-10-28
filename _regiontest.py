# -*- coding: utf-8 -*-
"""区域策略（core/region.py）的闸门。

不联网：脚本生成、防自锁、JSON 解析全是纯函数；下发/上传用假会话回放。
真机那一步在 `_regionnetns.py`（用 unshare 在一次性 netns 里真跑 nft，
不碰设备上的表），这里只钉「生成的东西对不对」和「失败会不会叫出来」。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import region as R          # noqa: E402

FAIL = []
PASS = [0]


def chk(ok, title, detail=""):
    if ok:
        PASS[0] += 1
        print(f"  ok   {title}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL.append(title)
        print(f"  FAIL {title}" + (f"  [{detail}]" if detail else ""))


def sec(t):
    print(f"\n-- {t}")


# ==================================================== 1. 计划
sec("1. 勾选 → 网段计划")
p = R.make_plan([{"key": "MO", "name": "中国澳门", "group": "country"}])
chk(len(p.v4) > 0 and len(p.v6) == 0, "澳门有 IPv4 网段",
    f"v4={len(p.v4)} v6={len(p.v6)}")
chk(p.raw_v4 >= len(p.v4), "合并后的条数不超过原始条数",
    f"{p.raw_v4} → {len(p.v4)}")
chk("中国澳门" in p.summary(), "摘要里有区域名", p.summary())

p2 = R.make_plan([{"key": "MO", "name": "中国澳门", "group": "country"},
                  {"key": "HK", "name": "中国香港", "group": "country"}])
chk(len(p2.v4) > len(p.v4), "两项比一项多", f"{len(p.v4)} → {len(p2.v4)}")
chk("、" in p2.summary(), "多个区域用顿号连起来", p2.summary()[:40])

for picks, why in (
        ([], "空选"),
        ([{"key": "NOPE", "name": "不存在", "group": "country"}], "地址库里没有"),
        ([{"key": "MO", "name": "澳门", "group": "province"}], "分组对不上"),
):
    try:
        R.make_plan(picks)
        chk(False, f"{why} 要报错")
    except R.RegionError as e:
        chk(True, f"{why} 报错且话说得清", str(e)[:40])

# 超量
old = R.MAX_ELEMS
R.MAX_ELEMS = 10
try:
    R.make_plan([{"key": "MO", "name": "中国澳门", "group": "country"}])
    chk(False, "超过 MAX_ELEMS 要拒绝")
except R.RegionError as e:
    chk("上限" in str(e), "超过总量上限时拒绝并说明", str(e)[:40])
finally:
    R.MAX_ELEMS = old

try:
    R.make_plan([{"key": "MO", "name": "中国澳门", "group": "country"}],
                verdict="allow")
    chk(False, "不认识的动作要拒绝")
except R.RegionError as e:
    chk("不认识的动作" in str(e), "动作只认 drop / reject", str(e)[:30])

# ==================================================== 2. 脚本
sec("2. nft 脚本")
s1 = R.nft_script(p)
chk("flags interval" in s1, "用了 interval 集合（不然一条规则装不下几万段）")
chk("auto-merge" in s1, "开了 auto-merge（相邻网段自动并）")
chk(s1.count("table inet tb_region") == 1, "只声明 tb_region 一张表")
chk("flush ruleset" not in s1, "绝不出现 flush ruleset")
chk("delete table" not in s1, "首次建表不带 delete")
chk("hook input priority -10" in s1, "挂在 input、priority -10（早于系统链）")
chk("ct state established,related accept" in s1,
    "先放行已建立连接（否则会掐断自己正在用的会话）")
chk(s1.index("established,related") < s1.index("@region_v4"),
    "established 规则在 drop 之前")
chk("ip saddr @region_v4 drop" in s1, "IPv4 规则用 ip saddr")
chk("policy accept" in s1, "本表默认策略是 accept（不替系统做默认拒绝）")

s2 = R.nft_script(p, exists=True)
chk(s2.startswith("delete table inet tb_region"), "整表重建时先删表",
    s2.splitlines()[0])
chk(s2.count("table inet tb_region") == 2, "delete + 声明各一处")

mix = R.Plan(picks=[{"key": "x", "name": "样本"}],
             v4=["223.5.5.0/24"], v6=["2400:3200::/32"])
sm = R.nft_script(mix)
chk("type ipv4_addr" in sm and "type ipv6_addr" in sm, "v4 / v6 两张集合")
chk("ip6 saddr @region_v6 drop" in sm, "IPv6 规则用 ip6 saddr")

only4 = R.Plan(picks=[{"key": "x", "name": "样本"}], v4=["223.5.5.0/24"])
chk("region_v6" not in R.nft_script(only4), "没有 IPv6 就不建空集合")

# 元素一个一行：几万条挤一行会让 nft 的报错行号毫无意义
lines = [ln for ln in sm.splitlines() if ln.strip().endswith(",")]
chk(len(lines) == 2, "每个元素单独一行", str(len(lines)))

try:
    R.nft_script(R.Plan(picks=[{"key": "x", "name": "空"}]))
    chk(False, "空计划要拒绝生成")
except R.RegionError as e:
    chk("没有网段" in str(e), "空计划不生成空脚本", str(e)[:20])

sec("3. iptables 兜底脚本")
sh = R.plain_script(mix)
chk(sh.startswith("#!/bin/sh"), "带 shebang")
chk("iptables -A INPUT -s 223.5.5.0/24 -j DROP" in sh, "IPv4 走 iptables")
chk("ip6tables -A INPUT -s 2400:3200::/32 -j DROP" in sh, "IPv6 走 ip6tables")
chk("没有 nft" in sh or "逐条下发" in sh, "脚本里写清了为什么是逐条")

# ==================================================== 4. 防自锁
sec("4. 防自锁（不然一按就失联）")
cn = R.make_plan([{"key": "CN", "name": "中国", "group": "country"}])
chk(bool(R.lockout(cn, ["1.2.4.8"])), "封中国 + 连接来自国内 → 拒绝",
    R.lockout(cn, ["1.2.4.8"])[:40])
chk(not R.lockout(cn, ["8.8.8.8"]), "封中国 + 连接来自境外 → 放行")
mo = R.make_plan([{"key": "MO", "name": "中国澳门", "group": "country"}])
chk(not R.lockout(mo, ["1.2.4.8"]), "封澳门 + 连接在国内 → 放行")
chk(not R.lockout(mo, []), "没有地址可比对时不拦（交给用户判断）")
chk(not R.lockout(mo, ["not-an-ip"]), "非法地址不炸")
chk(R.covers(["2400:3200::/32"], "2400:3200::1") == "2400:3200::/32",
    "IPv6 网段命中")
chk(R.covers(["223.5.5.0/24"], "2400:3200::1") == "", "跨族不算命中")

# ==================================================== 5. 状态解析
sec("5. 解析真机 nft -j 输出")
fx = ROOT / "_fixture_nft_state.json"
if fx.is_file():
    st = R.parse_state(fx.read_text(encoding="utf-8"))
    chk(st["exists"], "认得表存在")
    chk(st["v4"] == 2 and st["v6"] == 1, "数出 v4=2 / v6=1",
        f"{st['v4']}/{st['v6']}")
    chk(st["action"] == "drop", "认出 drop 规则", st["action"])
    chk("已启用" in R.describe(st) and "drop" not in R.describe(st),
        "describe 翻成人话", R.describe(st))
else:
    chk(False, "缺少真机样本 _fixture_nft_state.json")

chk(not R.parse_state("")["exists"], "空输出 = 没有表")
chk(not R.parse_state("not json")["exists"], "坏 JSON 不炸，当没有表")
chk(not R.parse_state('{"nftables": []}')["exists"], "空 nftables 当没有表")
chk(R.describe({}) == "未启用", "describe 对空状态")

# ==================================================== 6. 上传与下发
sec("6. 上传分块 + 下发的回读校验")


class FakeSess:
    """回放上传/下发。nft 的 JSON 按队列吐，模拟「下发前 / 下发后」。"""

    host = "192.168.3.48"
    label = "fnosadmin@192.168.3.48"

    def __init__(self, jsons=None, apply_rc=0, admin=True):
        self.files = {}
        self.calls = []
        self.jsons = list(jsons or [])
        self.apply_rc = apply_rc
        self.admin = admin

    # ---- 上传
    def run(self, cmd, timeout=30):
        self.calls.append(cmd)
        if cmd.startswith(": > "):
            self.files[cmd.split()[2]] = ""
            return 0, "@@RC=0", ""
        if "base64 -d >>" in cmd:
            import base64
            path = cmd.split(">>")[1].split()[0]
            b64 = cmd.split("'")[1]
            self.files[path] = self.files.get(path, "") + \
                base64.b64decode(b64).decode("utf-8")
            return 0, "@@RC=0", ""
        if cmd.startswith("wc -c"):
            path = cmd.split("<")[1].strip()
            return 0, f"{len(self.files.get(path, '').encode())}\n", ""
        if "SSH_CLIENT" in cmd:
            return 0, "8.8.8.8 51514 22\n8.8.8.8 51514 192.168.3.48 22", ""
        if "nft -j list table" in cmd:
            doc = self.jsons.pop(0) if self.jsons else '{"nftables": []}'
            return 0, doc + "\n@@RC=0", ""
        return 0, "", ""

    # ---- 提权执行
    def run_admin(self, cmd, timeout=60):
        self.calls.append("[ADMIN] " + cmd)
        if "nft -f" in cmd:
            return self.apply_rc, f"@@RC={self.apply_rc}", ""
        if "delete table" in cmd:
            return 0, "@@RC=0", ""
        if "nft -j list table" in cmd:
            doc = self.jsons.pop(0) if self.jsons else '{"nftables": []}'
            return 0, doc + "\n@@RC=0", ""
        return 0, "", ""

    def can_admin(self):
        return (self.admin, "" if self.admin else "需要 sudo 密码")


good = fx.read_text(encoding="utf-8") if fx.is_file() else '{"nftables": []}'
# 成功路径用一份「只有 IPv4、条数少于计划」的回读（auto-merge 会并掉一些）
ok_doc = json.dumps({"nftables": [
    {"set": {"name": "region_v4", "elem": [{"prefix": {"addr": "1.1.1.1",
                                                       "len": 32}}] * 10}},
    {"rule": {"expr": [{"drop": None}]}}]})
fs = FakeSess(jsons=['{"nftables": []}', ok_doc])
ok, info = R.upload(fs, R.nft_script(mo))
chk(ok, "上传成功（大小核对一致）", info)
chk(any("base64 -d >>" in c for c in fs.calls), "真的分了块上传")
big = R.Plan(picks=[{"key": "x", "name": "大"}],
             v4=[f"10.{(i * 4) // 256}.{(i * 4) % 256}.0/24" for i in range(3000)])
fs2 = FakeSess()
ok2, info2 = R.upload(fs2, R.nft_script(big))
chk(ok2, "3000 条网段也能传完", info2)
nchunk = sum(1 for c in fs2.calls if "base64 -d >>" in c)
chk(nchunk > 1, f"确实分成多块（{nchunk} 块）",
    f"单块上限 {R.CHUNK} 字节")
chk(all(len(c) < 90000 for c in fs2.calls), "每条命令都远低于 shell 单参数上限",
    f"最长 {max(len(c) for c in fs2.calls)} 字符")

caps = R.Caps(nft="/usr/sbin/nft", version="nftables v1.0.6", admin=True)
rc, body, err = R.apply(fs, mo, caps)
chk(rc == 0 and not err, "下发成功（回读对得上）", f"rc={rc} {err[:40]}")

# rc=0 但表里没东西 —— 必须报错，不许「看着成功」
bad = FakeSess(jsons=['{"nftables": []}', '{"nftables": []}'])
rc, body, err = R.apply(bad, mo, caps)
chk(rc != 0 and "读不到区域表" in err, "rc=0 但表不存在 → 报错", err[:50])

# 元素比计划还多 → 报错
too_many = json.dumps({"nftables": [
    {"set": {"name": "region_v4",
             "elem": [{"prefix": {"addr": "1.1.1.1", "len": 32}}] * 999}},
    {"rule": {"expr": [{"drop": None}]}}]})
odd = FakeSess(jsons=['{"nftables": []}', too_many])
rc, body, err = R.apply(odd, mo, caps)
chk(rc != 0 and "比计划还多" in err, "元素数异常也要报错", err[:60])

# 没有 drop 规则 → 报错（表建了但规则没进去，等于什么都没做）
nodrop = json.dumps({"nftables": [
    {"set": {"name": "region_v4", "elem": [{"prefix": {"addr": "1.1.1.1",
                                                       "len": 32}}]}}]})
nd = FakeSess(jsons=['{"nftables": []}', nodrop])
rc, body, err = R.apply(nd, mo, caps)
chk(rc != 0 and "drop 规则" in err, "表在但没 drop 规则 → 报错", err[:50])

# nft 自己报错
rej = FakeSess(jsons=['{"nftables": []}'], apply_rc=1)
rc, body, err = R.apply(rej, mo, caps)
chk(rc != 0 and "一条都没生效" in err, "nft 拒绝时说明是整批回滚", err[:60])

# 没提权就不让下发（不能悄悄跑成半套）
nocap = R.Caps(nft="/usr/sbin/nft", admin=False, why="需要 sudo 密码")
try:
    R.apply(FakeSess(), mo, nocap)
    chk(False, "没有 root 要拒绝下发")
except R.RegionError as e:
    chk("不能自动下发" in str(e), "没有 root 拒绝下发并给原因", str(e)[:30])

# 撤销
rc, body, err = R.clear(FakeSess(jsons=[good]), caps)
chk(rc == 0, "撤销（删表）走通", body[:20])
rc2, body2, _ = R.clear(FakeSess(jsons=['{"nftables": []}']), caps)
chk(rc2 == 0 and "本来就没有" in body2, "本来没表时不报错，说明白")

sec("7. peer_addrs：拿连接的真实出口地址")
pa = R.peer_addrs(FakeSess())
chk("8.8.8.8" in pa and "192.168.3.48" in pa, "SSH_CLIENT + 设备地址都在",
    str(pa))
chk(len(pa) == len(set(pa)), "去重", str(pa))

# ==================================================== 8. 源码钉住
sec("8. 源码约束")
src = (ROOT / "core" / "region.py").read_text(encoding="utf-8")
# 注释里提「不碰 flush ruleset / inet filter」是说明，真正要看的是**生成的脚本**
for label, text in (("首次建表", R.nft_script(mo)),
                    ("整表重建", R.nft_script(mo, exists=True)),
                    ("大表", R.nft_script(big)),
                    ("iptables 兜底", R.plain_script(mo))):
    chk("flush ruleset" not in text, f"{label}脚本里没有 flush ruleset")
    chk("inet filter" not in text, f"{label}脚本里不碰 inet filter")
chk(src.count("delete table inet") <= 2, "只有整表重建和撤销两处删表")
chk("CHUNK = " in src and "upload(" in src,
    "分块上传还在（大表塞不进一条命令，实测过 Argument list too long）")
page = (ROOT / "pages" / "security_page.py").read_text(encoding="utf-8")
# 区域策略**不是**顶级页签：它是防火墙的子视图。用户看到独立页签的第一反应
# 是「防火墙被你弄没了？」（原话），所以入口必须开在防火墙页里面。
chk('("region", "区域策略")' not in page, "没有把区域策略塞成第 5 个顶级页签")
chk('("fw", "防火墙")' in page, "防火墙页签还在")
chk("def region_open" in page and "def region_back" in page,
    "防火墙页里有「区域策略」入口与返回")
chk("区域策略（按国家 / 省）…" in page, "入口按钮文字点明它是防火墙的一部分")
chk("region_apply" in page and "region_clear" in page and "region_probe" in page,
    "应用 / 撤销 / 检测三个动作都在")

# ==================================================== 9. 编译
sec("9. 编译")
import py_compile  # noqa: E402
for rel in ("core/region.py", "pages/security_page.py", "ui/ipick.py"):
    try:
        py_compile.compile(str(ROOT / rel), doraise=True)
        chk(True, f"{rel} 编译通过")
    except py_compile.PyCompileError as e:
        chk(False, f"{rel} 编译失败", str(e)[:200])

print(f"\n结果：{PASS[0]} 通过 / {len(FAIL)} 失败")
if FAIL:
    for f in FAIL:
        print("  · " + f)
    sys.exit(1)
