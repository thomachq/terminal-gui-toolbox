# -*- coding: utf-8 -*-
"""地址库选择框 + 表单 iplist 字段的界面测试。

不联网：只用随程序打包的 `assets/iplib/`（省级 34 项 / 国家 40 项）。
数据没装的话相关判据会标 skip，不算红。
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import wbtui as U  # noqa: E402
from core import ipaddr, iplib  # noqa: E402

_p = _f = _s = 0


def chk(ok, label, extra=""):
    global _p, _f, _s
    if ok:
        _p += 1
        print(f"  ok   {label}")
    else:
        _f += 1
        print(f"  FAIL {label}  [{extra}]")


def skip(label):
    global _s
    _s += 1
    print(f"  skip {label}")


def sec(t):
    print(f"\n-- {t}")


root = tk.Tk()
root.geometry("1200x800+40+40")
U.apply_theme("light")
root.update()

from ui.ipick import IpPickDialog, _REFUSE_AT  # noqa: E402

st = iplib.installed()
has_prov = st["province"] > 0
has_ctry = st["country"] > 0

# ==================================================== 1. 分组与列表
sec("1. 三个分组")
dlg = IpPickDialog(root)
root.update()
chk(dlg.seg.current == "common", "默认停在「常用网段」")
chk(len(dlg.seg.items) == 3, "三个页签", str(dlg.seg.items))
chk([k for k, _l in dlg.seg.items] == ["common", "province", "country"],
    "页签顺序：常用 / 省级 / 国家")

rows = dlg.tb.rows()
chk(len(rows) == len(iplib.COMMON),
    f"常用网段 {len(iplib.COMMON)} 条", str(len(rows)))
chk(all(r["pick"] == "" for r in rows), "一开始一个都没勾")

dlg.seg.select("province")
root.update()
prows = dlg.tb.rows()
chk(len(prows) == 34, "省级 34 项", str(len(prows)))
names = [r["name"] for r in prows]
for nm in ("中国香港", "中国澳门", "中国台湾"):
    chk(any(nm in n for n in names), f"省级列表里有 {nm}")
if has_prov:
    chk(all(r["count"] != "—" for r in prows), "省级每条都有网段数")
else:
    skip("省级数据未安装")

dlg.seg.select("country")
root.update()
crows = dlg.tb.rows()
chk(len(crows) == 40, "国家地区 40 项", str(len(crows)))

# ==================================================== 2. 勾选
sec("2. 勾选与展开")
dlg.seg.select("common")
root.update()
r0 = dlg.tb.rows()[0]


class Ev:
    def __init__(self, x, y):
        self.x, self.y = x, y


# 用真实坐标点第一行那一列，验证「点哪列才能勾」
iid = dlg.tb.tree.get_children()[0]
bb = dlg.tb.tree.bbox(iid, "pick")
if bb:
    dlg._click(Ev(bb[0] + bb[2] // 2, bb[1] + bb[3] // 2))
    root.update()
chk(len(dlg._picked) == 1, "点✓列能勾上一项", str(list(dlg._picked)))
chk(dlg.tb.rows()[0]["pick"] == "✓", "勾上的行显示 ✓")

# 点名称列不该触发勾选，只选行
bb2 = dlg.tb.tree.bbox(iid, "name")
if bb2:
    dlg._click(Ev(bb2[0] + bb2[2] // 2, bb2[1] + bb2[3] // 2))
    root.update()
chk(len(dlg._picked) == 1, "点名称列不会误勾/误取消", str(list(dlg._picked)))

dlg._pick_all()
root.update()
chk(len(dlg._picked) == len(iplib.COMMON), "「全选本组」勾满",
    str(len(dlg._picked)))
txt = dlg.lb_sum.cget("text")
chk("已选" in txt and "展开" in txt, "底部汇总写了条数", txt)

dlg._clear()
root.update()
chk(not dlg._picked, "「清空」清掉全部")

# ==================================================== 3. 结果文本
sec("3. 生成的地址文本")
if has_prov:
    dlg.seg.select("province")
    root.update()
    gd = next(r for r in dlg.tb.rows() if "广东" in r["name"])
    dlg._picked[gd["_key"]] = gd["_item"]
    dlg._render()
    root.update()
    dlg._ok()
    root.update()
    chk(dlg.result is not None, "点「加入」产出结果")
    lines = [x for x in (dlg.result or "").splitlines() if x.strip()]
    chk(len(lines) > 0, f"广东展开 {len(lines)} 条", str(len(lines)))
    bad = [x for x in lines if ipaddr.norm(x)[1]]
    chk(not bad, "每一行都是合法 CIDR", str(bad[:3]))
    chk(all(ipaddr.family(x) in (4, 6) for x in lines), "地址族都能认出来")
else:
    skip("省级数据未安装，跳过展开校验")

# 已有的值要保留（用户手填的网段不能被地址库抹掉）
dlg2 = IpPickDialog(root, initial="10.0.0.0/8\n192.168.1.5")
root.update()
chk(len(dlg2._initial) == 2, "读得懂地址框里原有的两条",
    str(dlg2._initial))
dlg2._picked["cloudflare"] = iplib.common_items()[-1]
dlg2._ok()
root.update()
vals = [x for x in (dlg2.result or "").splitlines() if x.strip()]
chk("10.0.0.0/8" in vals and "192.168.1.5/32" in vals,
    "原有的两条在结果里（裸地址归一化成 /32）", str(vals[:4]))

# ==================================================== 4. 上限保护
sec("4. 条数上限")
dlg3 = IpPickDialog(root)
root.update()
big = iplib.Item("fake", "假数据", "",
                 # 步长 4 是为了**不让 collapse 合并**：连续的 /24 会被并成
                 # 几个大网段，那样根本到不了上限，测的就成了「合并算法」。
                 [f"10.{(i * 4) // 256}.{(i * 4) % 256}.0/24"
                  for i in range(_REFUSE_AT + 50)],
                 "province")
dlg3._picked["fake"] = big
chk(len(iplib.expand_items([big])) > _REFUSE_AT,
    "造的数据展开后确实超过上限", str(len(iplib.expand_items([big]))))
dlg3._ok()
root.update()
chk(dlg3.winfo_exists() and dlg3.result is None,
    f"超过 {_REFUSE_AT} 条拒绝回填，窗口不关")
chk("太多" in dlg3.lb_err.cget("text") or "上限" in dlg3.lb_err.cget("text"),
    "并说清楚原因", dlg3.lb_err.cget("text"))
dlg3.destroy()

# ==================================================== 5. 表单字段
sec("5. 表单里的 iplist 字段")
from ui import forms  # noqa: E402

fd = forms.FormDialog(
    root, "测试",
    [{"key": "src", "label": "来源地址", "kind": "iplist", "lines": 2,
      "default": "any"}])
root.update()
w = fd._widgets.get("src")
chk(isinstance(w, tk.Text), "iplist 渲染成多行 Text", type(w).__name__)
chk("地址库…" in [str(getattr(c, "text", "")) for c in w.master.winfo_children()
                 if isinstance(c, tk.Canvas)], "旁边有「地址库…」按钮",
    str([getattr(c, "text", "") for c in w.master.winfo_children()
         if isinstance(c, tk.Canvas)]))
w.delete("1.0", "end")
w.insert("1.0", "10.0.0.0/8\n2001:db8::/32")
root.update()
chk(fd._getters["src"]().count("\n") == 1, "取到的值保留换行",
    fd._getters["src"]())
fd.destroy()
root.update()

# ==================================================== 6. 防火墙表单接线
sec("6. 防火墙表单用的是 iplist")
from core import firewall as fw  # noqa: E402
from pages.firewall_page import FirewallPage  # noqa: E402

# 直接查字段定义（不需要真连设备）
import inspect  # noqa: E402

src_txt = inspect.getsource(FirewallPage._fw_fields)
for key in ("nftables", "iptables", "ufw", "firewalld", "ipfw", "netsh"):
    pass
chk(src_txt.count('"kind": "iplist"') >= 5, "至少 5 处地址字段换成 iplist",
    str(src_txt.count('"kind": "iplist"')))

# 后端真的会拆多值
nft = fw.NftBackend()
cmds = nft.add_cmds({"chain": "input",
                     "src": "10.0.0.0/24\n10.0.1.0/24",
                     "dst": "2001:db8::/32", "proto": "tcp", "port": "22",
                     "action": "accept"})
chk(len(cmds) == 2, "nft：2 源 × 1 目标 → 2 条命令", str(cmds))
chk(all("ip6 daddr 2001:db8::/32" in c for c in cmds),
    "IPv6 目标用 ip6 daddr（不是 ip daddr）", str(cmds[0]))
ipt = fw.IptablesBackend()
c2 = ipt.add_cmds({"chain": "INPUT", "dst": "2001:db8::/32"})
chk(c2 and c2[0].startswith("ip6tables"), "iptables：v6 目标走 ip6tables",
    str(c2))
ufw = fw.UfwBackend()
c3 = ufw.add_cmds({"action": "allow", "port": "22", "proto": "tcp",
                   "src": "1.1.1.0/24\n2.2.2.0/24"})
chk(len(c3) == 2, "ufw：两个来源 → 两条命令", str(c3))
fwd = fw.FirewalldBackend()
c4 = fwd.add_cmds({"src": "2001:db8::/32", "port": "443", "proto": "tcp"})
chk(any("family=ipv6" in c for c in c4),
    "firewalld：IPv6 来源用 family=ipv6（原来写死 ipv4）", str(c4))
chk(c4[-1] == "firewall-cmd --reload", "firewalld：reload 只发一次", str(c4))
nsh = fw.NetshBackend()
c5 = nsh.add_cmds({"name": "t", "direction": "in", "src": "1.1.1.0/24\n2.2.2.0/24"})
chk(len(c5) == 1 and "remoteip=1.1.1.0/24, 2.2.2.0/24" in c5[0],
    "netsh：多个地址合成 remoteip 逗号列表（不拆命令）", str(c5[0]))

# ==================================================== 7. 别漏了关闭
sec("7. 收尾")
for d in list(root.winfo_children()):
    if isinstance(d, tk.Toplevel):
        try:
            d.grab_release()
            d.destroy()
        except tk.TclError:
            pass
root.update()
root.destroy()

print("\n" + "=" * 52)
print(f"结果：{_p} 通过 / {_f} 失败" + (f" / {_s} 跳过" if _s else ""))
sys.exit(0 if _f == 0 else 1)
