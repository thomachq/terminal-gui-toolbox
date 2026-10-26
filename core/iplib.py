# -*- coding: utf-8 -*-
"""IP 地址库：常用网段 / 中国省级（含港澳台）/ 国家地区。

用户原话（2026-10-26 00:32）：「弄一个地址库出来，有常用网段、中国省级
（含港澳台）、国家/地区」。

**数据从哪来**（不能手写猜，防火墙规则错一条就是把自己关门外）：
  · 省级：`https://metowolf.github.io/iplist/data/cncity/<行政区划代码>.txt`
    按 GB/T 2260 的六位码，省级就是 `xx0000`（广东 440000）；
  · 国家/地区：`https://metowolf.github.io/iplist/data/country/<ISO>.txt`
  · 港澳台：地区码在 cncity 里没有独立文件，直接取 country 的 HK / MO / TW。
  这些文件本身由 OpenIPDB / IPinfo / bgp.tools 汇总而来，每小时更新。

**离线可用**：构建时（`_buildiplib.py`）把结果落到 `assets/iplib/*.json`
跟着程序走；运行时优先读 `data/iplib/*.json`（用户点「更新地址库」后的新版本），
没有就退回内置那份。全流程不联网也能用，联网只是让数据更新。

**为什么存「网段列表」而不是「区域对象」**：防火墙规则一条只能带一个
`-s/-d`。真要在一条规则里表达一个省，得上 ipset/nftables 的集合（那才叫
「区域」）。这里先做最通用的一档：选中后在保存时**展开成具体网段**，
条数太多时界面会劝你去用区域页 —— 和截图里那句提示一致。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from core import ipaddr

# ------------------------------------------------------------------ 常用网段
# 手写、逐条核对过的。这些是 IANA 特殊用途地址（RFC 6890 等），
# 不会随分配变化，所以内置不更新。
COMMON = [
    ("回环 (IPv4)", "127.0.0.0/8", "IPv4 网段"),
    ("回环 (IPv6)", "::1/128", "IPv6 网段"),
    ("私有网段 10/8", "10.0.0.0/8", "IPv4 网段"),
    ("私有网段 172.16/12", "172.16.0.0/12", "IPv4 网段"),
    ("私有网段 192.168/16", "192.168.0.0/16", "IPv4 网段"),
    ("IPv6 唯一本地", "fc00::/7", "IPv6 网段"),
    ("链路本地 169.254/16", "169.254.0.0/16", "IPv4 网段"),
    ("IPv6 链路本地", "fe80::/10", "IPv6 网段"),
    ("IPv4 组播", "224.0.0.0/4", "IPv4 网段"),
    ("IPv6 组播", "ff00::/8", "IPv6 网段"),
]

# 省级行政区（GB/T 2260 六位码）。港澳台单列，数据取 country 的 HK/MO/TW。
PROVINCES = [
    ("110000", "北京市"), ("120000", "天津市"), ("130000", "河北省"),
    ("140000", "山西省"), ("150000", "内蒙古自治区"), ("210000", "辽宁省"),
    ("220000", "吉林省"), ("230000", "黑龙江省"), ("310000", "上海市"),
    ("320000", "江苏省"), ("330000", "浙江省"), ("340000", "安徽省"),
    ("350000", "福建省"), ("360000", "江西省"), ("370000", "山东省"),
    ("410000", "河南省"), ("420000", "湖北省"), ("430000", "湖南省"),
    ("440000", "广东省"), ("450000", "广西壮族自治区"), ("460000", "海南省"),
    ("500000", "重庆市"), ("510000", "四川省"), ("520000", "贵州省"),
    ("530000", "云南省"), ("540000", "西藏自治区"), ("610000", "陕西省"),
    ("620000", "甘肃省"), ("630000", "青海省"), ("640000", "宁夏回族自治区"),
    ("650000", "新疆维吾尔自治区"),
    # 港澳台是中国的省级行政区，名称一律带国名前缀（不做国家对待）
    ("810000", "中国香港"), ("820000", "中国澳门"), ("710000", "中国台湾"),
]

# 国家 / 地区（ISO 3166-1 alpha-2）。只收常用的一批，全量 249 个列出来
# 反而找不到 —— 想扩可以在 data/iplib/country.json 里加。
COUNTRIES = [
    ("CN", "中国"), ("HK", "中国香港"), ("MO", "中国澳门"), ("TW", "中国台湾"),
    ("US", "美国"), ("JP", "日本"), ("KR", "韩国"), ("SG", "新加坡"),
    ("MY", "马来西亚"), ("TH", "泰国"), ("VN", "越南"), ("PH", "菲律宾"),
    ("ID", "印度尼西亚"), ("IN", "印度"), ("AU", "澳大利亚"), ("NZ", "新西兰"),
    ("RU", "俄罗斯"), ("DE", "德国"), ("GB", "英国"), ("FR", "法国"),
    ("NL", "荷兰"), ("IT", "意大利"), ("ES", "西班牙"), ("SE", "瑞典"),
    ("CH", "瑞士"), ("PL", "波兰"), ("TR", "土耳其"), ("AE", "阿联酋"),
    ("SA", "沙特阿拉伯"), ("IL", "以色列"), ("EG", "埃及"), ("ZA", "南非"),
    ("BR", "巴西"), ("AR", "阿根廷"), ("CL", "智利"), ("MX", "墨西哥"),
    ("CA", "加拿大"), ("UA", "乌克兰"), ("KZ", "哈萨克斯坦"), ("PK", "巴基斯坦"),
]

BASE = "https://metowolf.github.io/iplist/data"
UA = {"User-Agent": "TerminalToolbox-iplib/1.0"}


# ------------------------------------------------------------------ 路径
def app_dir() -> Path:
    """程序自己的目录。**不看 TB_DATA_DIR** —— 内置数据是随程序走的，
    测试把 data 重定向到临时目录时，仍然要能读到 assets/iplib。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """用户更新后放的地方（`data/iplib`，随 data 一起被 gitignore）。"""
    env = os.environ.get("TB_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve() / "iplib"
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data" / "iplib"
    return Path(__file__).resolve().parent.parent / "data" / "iplib"


def _bundled_dirs() -> list[Path]:
    """内置数据的可能位置：exe 同级 assets\\iplib，或 onefile 解压目录。"""
    out = [app_dir() / "assets" / "iplib"]
    mei = getattr(sys, "_MEIPASS", "")
    if mei:
        out.append(Path(mei) / "assets" / "iplib")
    return out


def _read(name: str) -> tuple[dict, str]:
    """(数据, 来源)。用户更新过的优先，其次内置。"""
    for p in [data_dir() / name] + [d / name for d in _bundled_dirs()]:
        try:
            if p.is_file():
                return json.loads(p.read_text(encoding="utf-8")), str(p)
        except (OSError, ValueError):
            continue
    return {}, ""


# ------------------------------------------------------------------ 查询
class Item:
    """地址库里的一条。`cidrs` 为空 = 这份数据还没下载过。"""

    __slots__ = ("key", "name", "note", "cidrs", "group")

    def __init__(self, key: str, name: str, note: str = "",
                 cidrs=None, group: str = "") -> None:
        self.key = key
        self.name = name
        self.note = note
        self.cidrs = list(cidrs or [])
        self.group = group

    @property
    def count(self) -> int:
        return len(self.cidrs)

    def text(self) -> str:
        """列表里给用户看的「条目」列。"""
        if self.cidrs:
            if len(self.cidrs) == 1:
                return self.cidrs[0]
            return f"{len(self.cidrs)} 个网段"
        return "（未安装）"

    def __repr__(self) -> str:
        return f"<Item {self.key} {self.name} {self.count}>"


def common_items() -> list[Item]:
    return [Item(c[1], c[0], c[2], [c[1]], "common") for c in COMMON]


def province_items() -> list[Item]:
    data, _ = _read("province.json")
    out = []
    for code, name in PROVINCES:
        cidrs = data.get(code) or []
        out.append(Item(code, name, f"行政区划码 {code}", cidrs, "province"))
    return out


def country_items() -> list[Item]:
    data, _ = _read("country.json")
    out = []
    for iso, name in COUNTRIES:
        cidrs = data.get(iso) or []
        out.append(Item(iso, name, f"ISO {iso}", cidrs, "country"))
    return out


GROUPS = (
    ("common", "常用网段", common_items),
    ("province", "中国省级", province_items),
    ("country", "国家/地区", country_items),
)


def _match(item: Item, kw: str) -> bool:
    if not kw:
        return True
    k = kw.strip().lower()
    if k in item.name.lower() or k in item.key.lower():
        return True
    # 搜网段：只在前几条上找，省级几千条全遍历会把界面卡住
    return any(k in c for c in item.cidrs[:200])


def search(group: str, kw: str = "") -> list[Item]:
    for key, _label, fn in GROUPS:
        if key == group:
            return [it for it in fn() if _match(it, kw)]
    return []


def installed() -> dict:
    """各分组的数据状况，界面拿来提示「未安装，点更新」。"""
    prov = province_items()
    ctry = country_items()
    return {
        "common": len(common_items()),
        "province": sum(1 for i in prov if i.cidrs),
        "province_total": len(prov),
        "country": sum(1 for i in ctry if i.cidrs),
        "country_total": len(ctry),
        "source": _read("province.json")[1] or "",
    }


# ------------------------------------------------------------------ 展开
def expand_items(items) -> list[str]:
    """选中项 → 去重合并后的网段列表（一个省的几千条会并到几百条）。"""
    flat: list[str] = []
    for it in items or []:
        flat += [c for c in getattr(it, "cidrs", []) if c]
    return ipaddr.expand(flat)


# ------------------------------------------------------------------ 更新
def _fetch(path: str, timeout: float = 40.0) -> list[str]:
    """拉一个 txt（每行一个 CIDR），返回合法网段列表。"""
    import urllib.request
    url = f"{BASE}/{path}"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        body = r.read().decode("utf-8", "replace")
    out = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cidr, err = ipaddr.norm(line)
        if not err and cidr != "any":
            out.append(cidr)
    return out


def update(province: bool = True, country: bool = True,
           progress=None) -> tuple[bool, str]:
    """从公开数据源更新地址库，写进 `data/iplib/`。

    返回 (是否成功, 说明)。单个省份/国家拉失败**不算整体失败** ——
    网络抖一下就把整份打到旧状态更糟，只报「哪几条没更新」。
    """
    import ipaddress

    d = data_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"建不了目录：{e}"
    bad: list[str] = []
    done = 0
    total = (len(PROVINCES) if province else 0) + (len(COUNTRIES) if country else 0)
    t0 = time.time()

    def tick(name: str) -> None:
        nonlocal done
        done += 1
        if progress:
            try:
                progress(done, total, name)
            except Exception:  # noqa: BLE001
                pass

    if province:
        out: dict[str, list[str]] = {}
        # 港澳台在 cncity 里没有独立文件 → 退回 country 的 HK/MO/TW
        gmt = {"810000": "HK", "820000": "MO", "710000": "TW"}
        for code, name in PROVINCES:
            path = ("cncity/%s.txt" % code if code not in gmt
                    else "country/%s.txt" % gmt[code])
            try:
                cidrs = _fetch(path)
                if not cidrs:
                    raise ValueError("空文件")
                nets = [ipaddress.ip_network(c, strict=False) for c in cidrs]
                out[code] = [str(n) for n in ipaddress.collapse_addresses(nets)]
            except Exception as e:  # noqa: BLE001
                bad.append(f"{name}（{type(e).__name__}）")
            tick(name)
        if out:
            _save(d / "province.json", out)

    if country:
        out2: dict[str, list[str]] = {}
        for iso, name in COUNTRIES:
            try:
                cidrs = _fetch(f"country/{iso}.txt")
                if not cidrs:
                    raise ValueError("空文件")
                nets = [ipaddress.ip_network(c, strict=False) for c in cidrs]
                out2[iso] = [str(n) for n in ipaddress.collapse_addresses(nets)]
            except Exception as e:  # noqa: BLE001
                bad.append(f"{name}（{type(e).__name__}）")
            tick(name)
        if out2:
            _save(d / "country.json", out2)

    if not bad:
        return True, f"已更新 {total} 项，耗时 {time.time() - t0:.0f} 秒"
    if done == len(bad):
        return False, "全部失败：" + "、".join(bad[:6])
    return True, f"部分成功，{len(bad)} 项没更新：" + "、".join(bad[:6])


def _save(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    os.replace(tmp, path)


def clear() -> int:
    """删掉用户更新过的数据（退回内置那份）。返回删了几个文件。"""
    n = 0
    for name in ("province.json", "country.json"):
        p = data_dir() / name
        try:
            if p.is_file():
                p.unlink()
                n += 1
        except OSError:
            pass
    return n
