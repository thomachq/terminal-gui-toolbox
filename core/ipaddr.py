# -*- coding: utf-8 -*-
"""IP 地址 / 网段：校验、多值拆分、按地址族归一。

为什么单独一个模块：防火墙、地址库、远程管理都要处理「一个框里塞进来
一堆东西」——`192.168.1.0/24, 10.0.0.5`、换行分隔、`fe80::/10`、`any`。
散在几个页面里各写一遍正则，迟早出现「这个页面认、那个页面不认」。

三条约定：
  · `any`（大小写、空白都算）表示「不限」，不参与校验，也不生成规则参数；
  · 裸地址按主机处理：`192.168.1.5` → `192.168.1.0/32`，`::1` → `::1/128`；
  · 一律返回**归一化**后的写法（网络地址 + 掩码），交给 iptables/nft 时
    不会因为 `192.168.1.7/24` 这种写法被对端拒绝。
"""
from __future__ import annotations

import ipaddress
import re

# 分隔符：逗号（半角/全角）、分号、空白、换行。用户从文档里复制粘贴
# 出来的东西经常是这几种混着来。
_SEP = re.compile(r"[,，;；\s]+")


def is_any(text: str) -> bool:
    s = str(text or "").strip().lower()
    return s in ("", "any", "all", "*", "0.0.0.0/0", "::/0")


def norm(text: str) -> tuple[str | None, str]:
    """单个网段 → 归一化 CIDR。返回 (cidr, 错误)。

    `any` 返回 ("any", "")；写错返回 (None, 人话)。
    """
    s = str(text or "").strip()
    if is_any(s):
        return "any", ""
    # 前缀越界先自己认出来：`ip_network` 对 /33 抛的是
    # 「does not appear to be an IPv4 or IPv6 network」，会把人误导成
    # 「地址写错了」，其实是掩码多打了一位。
    if "/" in s:
        head, _, tail = s.rpartition("/")
        if tail.isdigit():
            n = int(tail)
            if n > (128 if ":" in head else 32):
                return None, (f"「{s}」的前缀长度不对"
                              f"（IPv4 最大 /32，IPv6 最大 /128）")
    try:
        net = ipaddress.ip_network(s, strict=False)
    except ValueError as e:
        msg = str(e)
        if "does not appear to be an IPv4 or IPv6" in msg:
            return None, f"「{s}」不是合法的 IP 或网段"
        if "netmask" in msg or "prefix" in msg:
            return None, f"「{s}」的前缀长度不对（IPv4 最大 /32，IPv6 最大 /128）"
        return None, f"「{s}」不是合法网段：{msg}"
    return str(net), ""


def family(cidr: str) -> int:
    """4 / 6；`any` 或读不出来返回 0。"""
    s = str(cidr or "").strip()
    if is_any(s):
        return 0
    try:
        return ipaddress.ip_network(s, strict=False).version
    except ValueError:
        return 0


def split(text: str) -> tuple[list[str], list[str]]:
    """一框多值 → (合法列表, 不合法原文列表)，顺序保留、自动去重。

    去重是按**归一化之后**的写法：`192.168.1.0/24` 和 `192.168.1.7/24`
    是同一个网段，不该生成两条一模一样的规则。
    """
    raw = [x for x in _SEP.split(str(text or "")) if x.strip()]
    ok: list[str] = []
    bad: list[str] = []
    seen: set[str] = set()
    for item in raw:
        cidr, err = norm(item)
        if err:
            bad.append(item.strip())
            continue
        if cidr in seen:            # any 也去重，但只在列表里留一次
            continue
        seen.add(cidr)
        ok.append(cidr)
    return ok, bad


def join(cidrs) -> str:
    """列表 → 一框多值的写法（逗号分隔），`any` 单独出现。"""
    items = [str(c) for c in (cidrs or []) if str(c).strip()]
    if not items or items == ["any"]:
        return "any"
    items = [c for c in items if c != "any"]
    return ", ".join(items) if items else "any"


def expand(cidrs) -> list[str]:
    """把一堆网段合并成最少条数的等价集合（相邻的 /24 会并成 /23）。

    省级地址库动辄几千条，先 collapse 再交给防火墙，规则条数和下发时间
    都能压下来一大截。
    """
    nets = []
    for c in cidrs or []:
        s = str(c or "").strip()
        if is_any(s):
            continue
        try:
            nets.append(ipaddress.ip_network(s, strict=False))
        except ValueError:
            continue
    v4 = [n for n in nets if n.version == 4]
    v6 = [n for n in nets if n.version == 6]
    out: list[str] = []
    for group in (v4, v6):
        try:
            out += [str(n) for n in ipaddress.collapse_addresses(group)]
        except (TypeError, ValueError):
            out += [str(n) for n in group]
    return out


def mixed_family(cidrs) -> bool:
    """列表里 IPv4 与 IPv6 混着没有 —— 页面要用它提醒「会拆成两批命令」。"""
    fams = {family(c) for c in (cidrs or [])}
    fams.discard(0)
    return len(fams) > 1


def check_port(text: str) -> str | None:
    """端口/端口段校验（和防火墙后端那条规则保持一致）。"""
    s = str(text or "").strip()
    if not s:
        return None
    if not re.match(r"^[\d,\-: ]+$", s):
        return "端口写 22 或 80,443 或 1000-2000"
    return None
