# -*- coding: utf-8 -*-
"""主机清单 —— 把连接档案看成一份「资产台账」的纯函数层。

为什么单独一层：档案的存取还是 `profiles.ProfileStore`（同一个加密文件，
不另开一套存储），这里只负责**怎么把这些记录组织成清单**——分组、标签、
搜索、排序、校验、行格式化。全是纯函数，喂固定样本就能离线验，不需要开窗口。

三条硬规矩：
  · 分组和标签都是**展示层概念**，空值统一归一成「未分组」/空列表，
    绝不把 None 漏到界面上。
  · 校验只说「哪里不对」，不改数据 —— 谁调它谁决定怎么提示。
  · 行格式化不读任何外部状态（不碰磁盘、不碰网络），同一份输入永远同一份输出。
"""
from __future__ import annotations

import re
import time

UNGROUPED = "未分组"
TAG_SEP = re.compile(r"[,，;；、\s]+")
# 主机名/地址允许的字符：够宽（支持 IPv6、域名、中文主机名），但挡住空白与路径符
_BAD_ADDR = re.compile(r"[\s/\\]")
_PROTO_LABEL = {"ssh": "SSH", "telnet": "Telnet", "local": "本机"}
_AUTH_LABEL = {"key": "私钥", "password": "密码"}


# ------------------------------------------------------------------ 标签
def parse_tags(s) -> list[str]:
    """`"web, 生产 ; 上海"` → `["web", "生产", "上海"]`。去重保序。"""
    if not s:
        return []
    if isinstance(s, (list, tuple, set)):
        parts = [str(x) for x in s]
    else:
        parts = TAG_SEP.split(str(s))
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        t = p.strip().lstrip("#")
        if not t:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def join_tags(tags) -> str:
    return ", ".join(parse_tags(tags))


def group_of(p: dict) -> str:
    g = str((p or {}).get("group") or "").strip()
    return g or UNGROUPED


def proto_of(p: dict) -> str:
    pr = str((p or {}).get("proto") or "").strip().lower()
    if pr in _PROTO_LABEL:
        return pr
    return "telnet" if str((p or {}).get("port") or "") == "23" else "ssh"


# ------------------------------------------------------------------ 地址
def addr_of(p: dict) -> str:
    """`user@host:port` —— 缺哪段就少哪段，不留 `@:22` 这种残句。

    本机档案没有 host（连的就是自己），列表里显示 `localhost` 比「—」清楚：
    用户一眼能看出这条不是没填完，而是本来就没有远端地址。
    """
    p = p or {}
    if proto_of(p) == "local":
        return "localhost"
    host = str(p.get("host") or "").strip()
    user = str(p.get("user") or "").strip()
    port = str(p.get("port") or "").strip()
    if not host:
        return "—"
    tail = host
    if port and port not in ("22", "23"):
        tail = f"{host}:{port}"
    elif port == "23" and proto_of(p) == "telnet":
        tail = f"{host}:{port}"
    return f"{user}@{tail}" if user else tail


def display_name(p: dict) -> str:
    n = str((p or {}).get("name") or "").strip()
    if n:
        return n
    h = str((p or {}).get("host") or "").strip()
    return h or "未命名主机"


# ------------------------------------------------------------------ 行模型
def host_view(p: dict, cred_name: str = "", proxy_name: str = "") -> dict:
    """把一个档案 dict 变成表格行。

    额外塞两个下划线开头的键（`_raw`）给调用方取原始记录 —— 表格行里
    不放不可见状态，但点击时需要能回到源对象，否则得靠 name 二次查找，
    而 name 是可以重复的（旧档案没有唯一约束）。
    """
    p = p or {}
    proto = proto_of(p)
    tags = parse_tags(p.get("tags"))
    return {
        "name": display_name(p),
        "addr": addr_of(p),
        "host": str(p.get("host") or ""),
        "port": str(p.get("port") or ""),
        "user": str(p.get("user") or ""),
        "proto": _PROTO_LABEL.get(proto, proto),
        "proto_key": proto,
        "group": group_of(p),
        "tags": ", ".join(tags),
        "tag_list": tags,
        "auth": _AUTH_LABEL.get(str(p.get("auth") or ""), "私钥"),
        "cred": cred_name or ("—" if not p.get("cred_id") else "已失效引用"),
        "proxy": proxy_name or ("—" if not p.get("proxy_id") else "已失效引用"),
        "note": str(p.get("note") or ""),
        "last_used": fmt_ts(p.get("last_used")) or "从未连接",
        "created": fmt_ts(p.get("created")),
        "_raw": p,
    }


def fmt_ts(v) -> str:
    """秒级时间戳 → `2026/10/23 00:29`。认 int / float / 数字字符串。"""
    if v in (None, "", 0, "0"):
        return ""
    try:
        ts = float(v)
    except (TypeError, ValueError):
        return str(v)
    if ts <= 0:
        return ""
    try:
        return time.strftime("%Y/%m/%d %H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return ""


def now_ts() -> int:
    return int(time.time())


def touch(p: dict) -> dict:
    """连接成功后给记录打时间戳（返回新 dict，不改入参）。"""
    out = dict(p or {})
    out["last_used"] = str(now_ts())
    if not out.get("created"):
        out["created"] = out["last_used"]
    return out


# ------------------------------------------------------------------ 筛选
def filter_hosts(rows: list[dict], q: str = "", group: str = "",
                 proto: str = "") -> list[dict]:
    """关键词命中 名称 / 地址 / 用户 / 分组 / 标签 / 备注 任一项。

    空 group 表示「全部分组」；显式传 UNGROUPED 只匹配没有分组的那些。
    关键词按空格切词，要求**全部命中**（AND），这样多打几个词是收窄而不是放宽。
    """
    out = list(rows or [])
    if group:
        out = [r for r in out if group_of(r) == group]
    if proto:
        want = proto.lower()
        out = [r for r in out if proto_of(r) == want]
    words = [w for w in TAG_SEP.split((q or "").strip()) if w]
    if words:
        def hit(r: dict) -> bool:
            pool = " ".join([
                display_name(r), str(r.get("host") or ""),
                str(r.get("user") or ""), group_of(r),
                " ".join(parse_tags(r.get("tags"))),
                str(r.get("note") or ""),
            ]).lower()
            return all(w.lower() in pool for w in words)
        out = [r for r in out if hit(r)]
    return out


def group_list(rows: list[dict]) -> list[tuple[str, int]]:
    """[(分组名, 台数)]，按台数降序、同数按名字。空清单返回空列表。"""
    cnt: dict[str, int] = {}
    for r in rows or []:
        g = group_of(r)
        cnt[g] = cnt.get(g, 0) + 1
    return sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))


def group_choices(rows: list[dict]) -> list[str]:
    """下拉框选项：全部分组 + 现有分组（含「未分组」）。"""
    return ["全部分组"] + [g for g, _ in group_list(rows)]


def sort_rows(rows: list[dict], key: str = "name", desc: bool = False) -> list[dict]:
    """按 name / group / last_used / host 排。空值永远沉底。"""
    def val(r: dict):
        if key == "group":
            return (group_of(r).lower(), display_name(r).lower())
        if key == "last_used":
            try:
                return (float(r.get("last_used") or 0),)
            except (TypeError, ValueError):
                return (0.0,)
        if key == "host":
            return (str(r.get("host") or "").lower(),)
        return (display_name(r).lower(),)

    filled = [r for r in rows or [] if val(r) not in (("",), (0.0,))]
    empty = [r for r in rows or [] if val(r) in (("",), (0.0,))]
    filled.sort(key=val, reverse=desc)
    return filled + empty


def summary(rows: list[dict], total: int | None = None) -> str:
    """`共 8 台 · 3 个分组`。筛选后 total 给全量数，便于看出被筛掉多少。"""
    n = len(rows or [])
    g = len(group_list(rows)) if n else 0
    if total is not None and total != n:
        return f"筛选出 {n} 台 / 共 {total} 台 · {g} 个分组"
    if not n:
        return "还没有主机"
    return f"共 {n} 台 · {g} 个分组"


# ------------------------------------------------------------------ 校验
def validate(rec: dict, others: list[dict] | None = None) -> list[str]:
    """返回错误文案列表，空列表表示通过。

    只做「不拦住就会出事」的检查：
      · 名字不能空（档案靠名字做主键）
      · 远程主机必须有地址；地址里不能有空白或路径分隔符
      · 端口必须是 1–65535 的整数
      · 名字不能和别的记录撞（会直接覆盖别人）
    """
    errs: list[str] = []
    name = str((rec or {}).get("name") or "").strip()
    if not name:
        errs.append("名称不能为空")
    proto = proto_of(rec)
    if proto != "local":
        host = str((rec or {}).get("host") or "").strip()
        if not host:
            errs.append("地址不能为空")
        elif _BAD_ADDR.search(host):
            errs.append("地址里不能有空格或斜杠")
        port = str((rec or {}).get("port") or "").strip()
        if not port:
            errs.append("端口不能为空")
        else:
            try:
                pn = int(port)
            except ValueError:
                errs.append("端口必须是数字")
            else:
                if not (0 < pn < 65536):
                    errs.append("端口必须在 1–65535 之间")
    if name and others:
        for o in others:
            if o is rec:
                continue
            if str(o.get("name") or "").strip() == name:
                errs.append(f"名称「{name}」已被占用")
                break
    return errs


def blank_record(proto: str = "ssh") -> dict:
    """新建主机的空记录。默认值都放这儿，别散在界面代码里。

    `sudo` / `elevate` / `shell` 只有「本机」协议用得上：
      · Linux 本机 —— `sudo` 勾上后连上就 sudo -i（口令存 sudo_password）；
      · Windows 本机 —— 没有 sudo，`elevate` 表示「以管理员权限运行」（UAC），
        再选一个 `shell`（cmd / powershell）。
    """
    return {
        "name": "", "host": "", "port": "22" if proto != "telnet" else "23",
        "user": "", "auth": "key", "key_path": "", "proto": proto,
        "group": "", "tags": "", "note": "", "cred_id": "", "proxy_id": "",
        "remember": False, "password": "", "key_passphrase": "",
        "sudo_password": "", "sudo": False, "elevate": False, "shell": "",
        "created": str(now_ts()), "last_used": "",
    }


def record_from_form(data: dict, base: dict | None = None) -> dict:
    """表单值 → 档案记录。保留 base 里的口令等不可见字段。"""
    out = dict(base or blank_record())
    for k in ("name", "host", "port", "user", "auth", "key_path", "proto",
              "group", "note", "cred_id", "proxy_id", "shell"):
        if k in data:
            out[k] = str(data.get(k) or "").strip()
    for k in ("sudo", "elevate"):
        if k in data:
            out[k] = bool(data.get(k))
    if "tags" in data:
        out["tags"] = join_tags(data.get("tags"))
    if not out.get("created"):
        out["created"] = str(now_ts())
    return out


def import_hint(added: int, skipped: int) -> str:
    """导入 ssh config 的一句话结果。0 新增时要说清为什么。"""
    if added and skipped:
        return f"新增 {added} 台，跳过 {skipped} 台（同名已存在）"
    if added:
        return f"新增 {added} 台"
    if skipped:
        return f"没有新增：{skipped} 台都已在清单里"
    return "没在 ~/.ssh/config 里找到可用主机"


def revoke_cred(rec: dict) -> dict:
    """凭证被删时把主机上的引用清掉（返回新 dict）。"""
    out = dict(rec or {})
    out["cred_id"] = ""
    return out


def revoke_proxy(rec: dict) -> dict:
    out = dict(rec or {})
    out["proxy_id"] = ""
    return out
