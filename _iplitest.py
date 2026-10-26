# -*- coding: utf-8 -*-
"""IP 地址库 + IPv4/IPv6 防火墙命令。

用户原话（2026-10-26 00:32）：
  「在防火墙管理那里，IP 支持 IPv4、IPv6，而且可以多选，而且弄一个地址库
    出来，有常用网段、中国省级（含港澳台）、国家/地区」。

分段：
  1. 地址归一化（裸地址补 /32、/128、any、各种写错）
  2. 一框多值的拆分与去重（全角逗号、换行、空格混着来）
  3. 合并（相邻 /24 并成 /23）
  4. 地址库：常用网段 10 条 / 省级 34 个（含港澳台）/ 国家地区
  5. 数据优先级：data/iplib 覆盖 assets/iplib；clear() 退回内置
  6. 防火墙命令：IPv6 必须 ip6tables / nft 的 ip6 关键字；多值拆多条命令
  7. validate：写错的地址、v4/v6 混配都在下发前拦住

不需要联网（数据已在 assets/iplib 里）；也不需要窗口。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = FAIL = SKIP = 0
FAILS: list[str] = []

TD = Path(tempfile.mkdtemp(prefix="tb_iplib_"))
os.environ["TB_DATA_DIR"] = str(TD)      # 别让 clear() 动到真实 data

from core import firewall as FW          # noqa: E402
from core import ipaddr                  # noqa: E402
from core import iplib                   # noqa: E402


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")
    print(("  ok   " if ok else "  FAIL ") + name
          + (f"  [{extra}]" if extra and not ok else ""))


def sec(t):
    print(f"\n-- {t}")


# ============================================================ 1. 归一化
sec("1. 地址归一化")
chk(ipaddr.norm("192.168.1.0/24") == ("192.168.1.0/24", ""), "IPv4 网段原样")
chk(ipaddr.norm("192.168.1.7/24")[0] == "192.168.1.0/24",
    "主机位非零 → 归到网络地址（否则下发给 iptables 会报错）")
chk(ipaddr.norm("10.0.0.5")[0] == "10.0.0.5/32", "裸 IPv4 当 /32")
chk(ipaddr.norm("fe80::/10")[0] == "fe80::/10", "IPv6 网段")
chk(ipaddr.norm("::1")[0] == "::1/128", "裸 IPv6 当 /128")
chk(ipaddr.norm("2001:db8::1")[0] == "2001:db8::1/128", "IPv6 主机地址")
for t in ("any", "ANY", "", "  ", "*", "0.0.0.0/0", "::/0"):
    chk(ipaddr.norm(t)[0] == "any", f"「{t}」= 不限")
for t in ("192.168.1.300", "1.2.3", "abc", "192.168.1.0/33", "fe80::/129"):
    cidr, err = ipaddr.norm(t)
    chk(cidr is None and err, f"「{t}」判为非法并给出人话", err)
chk("前缀" in ipaddr.norm("192.168.1.0/33")[1], "掩码越界的提示说清了是前缀问题",
    ipaddr.norm("192.168.1.0/33")[1])

sec("2. 地址族")
chk([ipaddr.family(x) for x in ("10.0.0.0/8", "fe80::/10", "any", "坏的")] ==
    [4, 6, 0, 0], "family：4 / 6 / 0（any 与非法都算 0）")

# ============================================================ 2. 多值
sec("3. 一框多值")
ok, bad = ipaddr.split("192.168.1.0/24, 10.0.0.0/8\nfe80::/10；::1  10.0.0.5")
chk(ok == ["192.168.1.0/24", "10.0.0.0/8", "fe80::/10", "::1/128",
           "10.0.0.5/32"], "逗号/换行/全角分号/空格混着切得对", ok)
chk(not bad, "没有误判成非法", bad)
ok2, bad2 = ipaddr.split("10.0.0.0/8, 10.1.2.3/8, 10.0.0.0/8")
chk(ok2 == ["10.0.0.0/8"], "归一化之后去重（同一个网段的两种写法只留一条）", ok2)
ok3, bad3 = ipaddr.split("10.0.0.0/8, 不好, 10.0.0.9")
chk(bad3 == ["不好"], "非法项单独列出，不拖累合法项", bad3)
chk(len(ok3) == 2, "合法项照样收下", ok3)
ok4, _ = ipaddr.split("any, 10.0.0.0/8, any")
chk(ok4 == ["any", "10.0.0.0/8"], "any 只留一次", ok4)
chk(ipaddr.join(["10.0.0.0/8", "fe80::/10"]) == "10.0.0.0/8, fe80::/10",
    "join 拼回一框多值")
chk(ipaddr.join([]) == "any" and ipaddr.join(["any"]) == "any",
    "空的/纯 any 都写 any")

sec("4. 合并")
chk(ipaddr.expand(["192.168.0.0/24", "192.168.1.0/24"]) == ["192.168.0.0/23"],
    "相邻两个 /24 并成 /23（省级几千条能压掉一大截）",
    str(ipaddr.expand(["192.168.0.0/24", "192.168.1.0/24"])))
m = ipaddr.expand(["10.0.0.0/8", "fe80::/10"])
chk(len(m) == 2 and "10.0.0.0/8" in m and "fe80::/10" in m,
    "v4 与 v6 一起合并不串族", str(m))
chk(ipaddr.expand(["any", "坏的"]) == [], "any 与非法项不进结果")
chk(ipaddr.mixed_family(["10.0.0.0/8", "fe80::/10"]), "认出 v4/v6 混配")
chk(not ipaddr.mixed_family(["10.0.0.0/8", "192.168.0.0/16"]), "同一族不算混配")

# ============================================================ 3. 地址库
sec("5. 地址库：常用网段")
cm = iplib.common_items()
chk(len(cm) == 10, "常用网段 10 条（和截图一致）", str(len(cm)))
want = {"127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12",
        "192.168.0.0/16", "fc00::/7", "169.254.0.0/16", "fe80::/10",
        "224.0.0.0/4", "ff00::/8"}
chk({i.cidrs[0] for i in cm} == want, "10 条网段逐条对得上",
    str({i.cidrs[0] for i in cm} ^ want))
chk(all(not ipaddr.norm(i.cidrs[0])[1] for i in cm), "10 条全是合法 CIDR")
chk(sum(1 for i in cm if ipaddr.family(i.cidrs[0]) == 6) == 4,
    "IPv6 4 条、IPv4 6 条（截图里那 10 条）",
    str(sum(1 for i in cm if ipaddr.family(i.cidrs[0]) == 6)))

sec("6. 地址库：中国省级（含港澳台）")
pv = iplib.province_items()
chk(len(pv) == 34, "34 个省级行政区", str(len(pv)))
names = [i.name for i in pv]
for n in ("中国香港", "中国澳门", "中国台湾"):
    chk(n in names, f"{n} 在列表里（按中国省级行政区处理，不当国家）")
chk(not any(n in ("香港", "澳门", "台湾") for n in names),
    "不会出现不带国名前缀的写法")
ready = [i for i in pv if i.cidrs]
if ready:
    chk(len(ready) == 34, f"34 个省级都有数据（实际 {len(ready)}）")
    bad = []
    for it in ready:
        for c in it.cidrs[:50]:
            if ipaddr.norm(c)[1]:
                bad.append(f"{it.name}:{c}")
    chk(not bad, "抽样的省级网段全部合法", str(bad[:3]))
    gd = [i for i in pv if i.name == "广东省"][0]
    chk(gd.count > 50, "广东省网段数量像样", str(gd.count))
    merged = iplib.expand_items([gd])
    chk(0 < len(merged) <= gd.count, "展开后条数不增加（合并只会更少）",
        f"{len(merged)} vs {gd.count}")
    chk(all(not ipaddr.norm(c)[1] for c in merged), "展开结果全是合法 CIDR")
else:
    SKIP += 1
    print("  skip 省级数据（assets/iplib/province.json 还没生成）")

sec("7. 地址库：国家 / 地区")
ct = iplib.country_items()
chk(len(ct) >= 30, f"国家地区 {len(ct)} 项", str(len(ct)))
iso = [i.key for i in ct]
chk("CN" in iso and "US" in iso, "含中国与美国")
chk("HK" in iso and "MO" in iso and "TW" in iso, "含港澳台")
hk = [i for i in ct if i.key == "HK"][0]
chk(hk.name == "中国香港", "HK 显示为「中国香港」", hk.name)

sec("8. 搜索")
chk([i.name for i in iplib.search("province", "广东")] == ["广东省"],
    "按名字搜：广东 → 广东省")
chk(any(i.name.startswith("回环") for i in iplib.search("common", "127")),
    "按网段搜：127 → 回环")
chk(len(iplib.search("province", "")) == 34, "空关键词返回全部")
chk(iplib.search("没有这个分组", "") == [], "未知分组安全返回空")

# ============================================================ 4. 数据优先级
sec("9. data/iplib 覆盖内置，clear() 退回")
d = iplib.data_dir()
d.mkdir(parents=True, exist_ok=True)
(d / "province.json").write_text(
    json.dumps({"440000": ["203.0.113.0/24"]}), encoding="utf-8")
gd = [i for i in iplib.province_items() if i.key == "440000"][0]
chk(gd.cidrs == ["203.0.113.0/24"], "用户更新的数据优先于内置", str(gd.cidrs))
st = iplib.installed()
chk(st["province"] == 1, "installed() 按新数据统计", str(st))
chk(iplib.clear() == 1, "clear() 删掉了用户数据")
after = [i for i in iplib.province_items() if i.key == "440000"][0]
chk(after.cidrs != ["203.0.113.0/24"], "clear() 后退回内置那份", str(after.count))

# ============================================================ 5. 防火墙命令
sec("10. nft：IPv6 用 ip6 关键字")
nft = FW.NftBackend()
c4 = nft.add_cmds({"chain": "input", "dst": "192.168.1.0/24", "proto": "tcp",
                   "port": "22", "action": "accept"})
chk(len(c4) == 1 and "ip daddr 192.168.1.0/24" in c4[0],
    "IPv4 目标 → ip daddr", str(c4))
c6 = nft.add_cmds({"chain": "input", "dst": "fe80::/10", "proto": "tcp",
                   "port": "22", "action": "accept"})
chk(len(c6) == 1 and "ip6 daddr fe80::/10" in c6[0] and " ip daddr" not in c6[0],
    "IPv6 目标 → ip6 daddr（老代码硬编码 ip，必错）", str(c6))
cm2 = nft.add_cmds({"chain": "input", "dst": "10.0.0.0/8, fe80::/10",
                    "action": "drop"})
chk(len(cm2) == 2, "多值拆成 2 条命令", str(cm2))
chk(any("ip daddr 10.0.0.0/8" in c for c in cm2) and
    any("ip6 daddr fe80::/10" in c for c in cm2), "两条各自带对族的关键字", str(cm2))
cs = nft.add_cmds({"chain": "input", "src": "::1", "action": "accept"})
chk("ip6 saddr ::1/128" in cs[0], "IPv6 源 → ip6 saddr", str(cs))

sec("11. iptables：IPv6 走 ip6tables")
ipt = FW.IptablesBackend()
i4 = ipt.add_cmds({"chain": "INPUT", "dst": "10.0.0.0/8", "proto": "tcp",
                   "port": "80", "action": "ACCEPT"})
chk(len(i4) == 1 and i4[0].startswith("iptables "), "IPv4 用 iptables", str(i4))
i6 = ipt.add_cmds({"chain": "INPUT", "dst": "fe80::/10", "proto": "tcp",
                   "port": "80", "action": "ACCEPT"})
chk(len(i6) == 1 and i6[0].startswith("ip6tables "), "IPv6 用 ip6tables", str(i6))
chk("-d fe80::/10" in i6[0], "IPv6 目标落成 -d", str(i6))
im = ipt.add_cmds({"chain": "INPUT", "dst": "10.0.0.0/8, 2001:db8::/32",
                   "action": "DROP"})
chk(len(im) == 2, "多值拆成 2 条", str(im))
chk(im[0].startswith("iptables ") and im[1].startswith("ip6tables "),
    "v4 那条走 iptables、v6 那条走 ip6tables（顺序按输入）", str(im))
chk(all("-j DROP" in c for c in im), "两条都带了动作", str(im))

sec("12. validate 在下发前拦住")
chk(nft.validate({"chain": "input", "dst": "192.168.1.0/24"}) is None,
    "正常 IPv4 通过")
chk(nft.validate({"chain": "input", "dst": "fe80::/10"}) is None,
    "正常 IPv6 通过")
e1 = nft.validate({"chain": "input", "dst": "192.168.1.0/33"})
chk(e1 and "写错" in e1, "掩码越界被拦下并说明", str(e1))
e2 = ipt.validate({"chain": "INPUT", "src": "10.0.0.0/8", "dst": "fe80::/10"})
chk(e2 and "地址族不一致" in e2, "v4 源 + v6 目标被拦下", str(e2))
e3 = nft.validate({"chain": "input", "dst": "1.1.1.0/24,2.2.2.0/24,"
                                       "3.3.3.0/24,4.4.4.0/24,5.5.5.0/24"})
# 多网段现在是**功能**（地址库就是成批塞），不能再当错误拦。只卡硬上限：
# 超过 400 条下发到一半断掉，比拒绝更糟。
chk(e3 is None, "5 个网段是合法的（地址库会成批塞）", str(e3))
many = ",".join(f"10.{i // 256}.{i % 256}.0/24" for i in range(500))
e4 = nft.validate({"chain": "input", "dst": many})
chk(e4 and "上限" in e4, "超过单条上限时拦下并说明", str(e4))
e5 = nft.validate({"chain": "input",
                   "src": ",".join(f"10.{i}.0.0/16" for i in range(30)),
                   "dst": ",".join(f"172.16.{i}.0/24" for i in range(30))})
chk(e5 and "来源 × 目标" in e5, "源 × 目标 相乘爆掉也拦下", str(e5))
chk(nft.validate({"chain": "input", "port": "80;rm -rf /"}) is not None,
    "端口里的怪字符仍然被拦（不许放过注入）")

# ============================================================ 汇总
print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
if FAILS:
    for f in FAILS:
        print("   ✗", f)
sys.exit(1 if FAIL else 0)
