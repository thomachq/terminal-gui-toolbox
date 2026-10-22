# -*- coding: utf-8 -*-
"""代理池 —— 跳板机 / 代理链，一台主机可以指定从哪条链路进来。

结构是一条**链**而不是一堆平铺的节点：每条代理可以再指向另一条代理当上游，
`parent_id` 为空表示直连目标。所以「东京跳板 → 香港跳板 → 目标」这种两跳
链路，就是把香港那条的 parent 指到东京那条。

最怕的不是配错端口，是**配出环**：A 的上一跳是 B、B 的上一跳是 A，
连接时会在两个跳板之间无限打转。`validate()` 每次都从当前节点往上走一遍，
撞到自己就报错 —— 环在这里必须死，不能留到连接时才炸。
"""
from __future__ import annotations

import json
import os
import re
import secrets as _secrets
import time
from pathlib import Path

import crypto_store

KINDS: dict[str, dict] = {
    "ssh": {"label": "SSH 跳板", "default_port": "22",
            "hint": "通过另一台 SSH 主机转发，最常用"},
    "socks5": {"label": "SOCKS5", "default_port": "1080",
               "hint": "本地或远端 SOCKS5 代理"},
    "http": {"label": "HTTP 代理", "default_port": "8080",
             "hint": "HTTP/HTTPS CONNECT 代理"},
}
KIND_KEYS = tuple(KINDS)
_BAD_HOST = re.compile(r"[\s/\\]")
ARROW = " → "


def kind_label(kind: str) -> str:
    return KINDS.get(kind, KINDS["socks5"])["label"]


def default_port(kind: str) -> str:
    return KINDS.get(kind, KINDS["socks5"])["default_port"]


def new_id(existing: list[str] | None = None) -> str:
    have = set(existing or [])
    for _ in range(64):
        v = _secrets.token_hex(4)
        if v not in have:
            return v
    raise RuntimeError("代理 id 生成失败：连续 64 次撞号")


def now_ts() -> int:
    return int(time.time())


def fmt_ts(v) -> str:
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


# ------------------------------------------------------------------ 链路
def chain_of(proxies: list[dict], pid: str) -> list[dict]:
    """从最外层跳到最近一跳：`[东京, 香港]`。找不到或成环时安全截断。

    截断而不是抛异常，是因为这个函数也被表格渲染调用 —— 一条脏数据
    不该让整个页面白屏。环由 `validate()` 在写入前拦住。
    """
    if not pid:
        return []
    by_id = {str(p.get("id") or ""): p for p in proxies or [] if isinstance(p, dict)}
    out: list[dict] = []
    seen: set[str] = set()
    cur = str(pid)
    while cur and cur in by_id and cur not in seen:
        seen.add(cur)
        node = by_id[cur]
        out.append(node)
        cur = str(node.get("parent_id") or "")
    out.reverse()
    return out


def chain_text(proxies: list[dict], pid: str) -> str:
    """`东京 → 香港 → 目标`。空链路返回「直连」。"""
    chain = chain_of(proxies, pid)
    if not chain:
        return "直连"
    names = [str(p.get("name") or p.get("host") or "?") for p in chain]
    return ARROW.join(names + ["目标"])


def depth_of(proxies: list[dict], pid: str) -> int:
    return len(chain_of(proxies, pid))


def detect_cycle(proxies: list[dict], pid: str) -> bool:
    """从 pid 往上走，能走回 pid 就是环。"""
    if not pid:
        return False
    by_id = {str(p.get("id") or ""): p for p in proxies or [] if isinstance(p, dict)}
    if pid not in by_id:
        return False
    seen = {pid}
    cur = str(by_id[pid].get("parent_id") or "")
    while cur:
        if cur in seen:
            return True
        seen.add(cur)
        node = by_id.get(cur)
        if node is None:
            return False
        cur = str(node.get("parent_id") or "")
    return False


def children_of(proxies: list[dict], pid: str) -> list[str]:
    """哪些代理把 pid 当上一跳（删除时的依赖检查）。"""
    return [str(p.get("name") or "") for p in proxies or []
            if str((p or {}).get("parent_id") or "") == pid]


def addr_of(p: dict) -> str:
    host = str((p or {}).get("host") or "").strip()
    port = str((p or {}).get("port") or "").strip()
    if not host:
        return "—"
    return f"{host}:{port}" if port else host


def proxy_view(p: dict, proxies: list[dict] | None = None,
               used_by: list[str] | None = None) -> dict:
    p = p or {}
    pid = str(p.get("id") or "")
    chain = chain_of(proxies or [], pid)
    used = list(used_by or [])
    return {
        "name": str(p.get("name") or p.get("host") or "未命名代理"),
        "kind": kind_label(str(p.get("kind") or "")),
        "kind_key": str(p.get("kind") or "socks5"),
        "addr": addr_of(p),
        "user": str(p.get("user") or "—"),
        "chain": chain_text(proxies or [], pid),
        "depth": f"{len(chain)} 跳" if chain else "—",
        "usage": f"{len(used)} 台" if used else "未使用",
        "usage_n": len(used),
        "used_names": used,
        "note": str(p.get("note") or ""),
        "created": fmt_ts(p.get("created")),
        "_id": pid,
        "_raw": p,
    }


def filter_proxies(rows: list[dict], q: str = "", kind: str = "") -> list[dict]:
    out = list(rows or [])
    if kind:
        out = [r for r in out if str(r.get("kind_key") or "") == kind]
    words = [w for w in re.split(r"[,，;；、\s]+", (q or "").strip()) if w]
    if words:
        def hit(r: dict) -> bool:
            pool = " ".join([
                str(r.get("name") or ""), str(r.get("addr") or ""),
                str(r.get("user") or ""), str(r.get("chain") or ""),
                str(r.get("note") or ""),
            ]).lower()
            return all(w.lower() in pool for w in words)
        out = [r for r in out if hit(r)]
    return out


def summary(rows: list[dict], total: int | None = None) -> str:
    n = len(rows or [])
    if total is not None and total != n:
        return f"筛选出 {n} 条 / 共 {total} 条代理"
    return f"共 {n} 条代理" if n else "代理池是空的"


# ------------------------------------------------------------------ 校验
def validate(rec: dict, others: list[dict] | None = None,
             skip_id: str = "") -> list[str]:
    errs: list[str] = []
    rec = rec or {}
    name = str(rec.get("name") or "").strip()
    if not name:
        errs.append("名称不能为空")
    kind = str(rec.get("kind") or "")
    if kind not in KINDS:
        errs.append("类型不对（只能是 SSH 跳板 / SOCKS5 / HTTP 代理）")
    host = str(rec.get("host") or "").strip()
    if not host:
        errs.append("地址不能为空")
    elif _BAD_HOST.search(host):
        errs.append("地址里不能有空格或斜杠")
    port = str(rec.get("port") or "").strip()
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
    rid = str(rec.get("id") or "")
    parent = str(rec.get("parent_id") or "")
    if parent:
        if parent == rid and rid:
            errs.append("上一跳不能是它自己")
        else:
            # 用「假如这条已经写进去」的视角做环检测
            probe = [dict(x) for x in (others or []) if isinstance(x, dict)]
            probe = [x for x in probe if str(x.get("id") or "") != rid]
            probe.append({**rec, "id": rid or "__new__"})
            if detect_cycle(probe, rid or "__new__"):
                errs.append("这条链路成环了：上一跳的链路里绕回了它自己")
    if name and others:
        for o in others or []:
            if not isinstance(o, dict):
                continue
            if skip_id and str(o.get("id") or "") == skip_id:
                continue
            if str(o.get("name") or "").strip().lower() == name.lower():
                errs.append(f"名称「{name}」已存在")
                break
    return errs


def delete_block_reason(hosts_used: list[str], proxies_used: list[str]) -> str:
    """代理被主机或别的代理引用时，给出一句拦截理由。"""
    parts: list[str] = []
    if hosts_used:
        head = "、".join(hosts_used[:3])
        more = f" 等 {len(hosts_used)} 台" if len(hosts_used) > 3 else ""
        parts.append(f"{len(hosts_used)} 台主机在用（{head}{more}）")
    if proxies_used:
        head = "、".join(proxies_used[:3])
        more = f" 等 {len(proxies_used)} 条" if len(proxies_used) > 3 else ""
        parts.append(f"{len(proxies_used)} 条代理把它当上一跳（{head}{more}）")
    if not parts:
        return ""
    return "；".join(parts) + "。请先解除这些引用。"


def blank_proxy(kind: str = "ssh") -> dict:
    k = kind if kind in KINDS else "ssh"
    return {"id": "", "name": "", "kind": k, "host": "",
            "port": default_port(k), "user": "", "cred_id": "",
            "parent_id": "", "note": "", "created": str(now_ts())}


def proxy_from_form(data: dict, base: dict | None = None) -> dict:
    out = dict(base or blank_proxy())
    for k in ("name", "host", "port", "user", "cred_id", "parent_id", "note"):
        if k in data:
            out[k] = str(data.get(k) or "").strip()
    if "kind" in data and str(data.get("kind") or "") in KINDS:
        kind = str(data.get("kind"))
        old = str(out.get("kind") or "")
        out["kind"] = kind
        # 类型换了但端口还是旧类型的默认值 → 跟着换，别留个 1080 的 SSH
        if old and old != kind and str(out.get("port") or "") == default_port(old):
            out["port"] = default_port(kind)
    if not out.get("created"):
        out["created"] = str(now_ts())
    return out


def usage_map(proxies: list[dict], hosts: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for h in hosts or []:
        pid = str((h or {}).get("proxy_id") or "")
        if pid:
            out.setdefault(pid, []).append(str((h or {}).get("name") or ""))
    for lst in out.values():
        lst.sort()
    return out


# ------------------------------------------------------------------ 存储
class ProxyStore:
    """`data/proxies.dat`：整份密文（代理的地址与账号也算情报）。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.encrypted = False
        self.data: dict = {"version": 1, "proxies": []}
        self.load()

    def load(self) -> None:
        self.data = {"version": 1, "proxies": []}
        self.encrypted = False
        if not self.path.is_file():
            return
        try:
            raw = self.path.read_bytes()
        except OSError:
            return
        plain = crypto_store.open_bytes(raw)
        if plain is None:
            plain = raw
        else:
            self.encrypted = True
        try:
            obj = json.loads(plain.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(obj, dict) or not isinstance(obj.get("proxies"), list):
            return
        self.data = {
            "version": int(obj.get("version") or 1),
            "proxies": [self._clean(p) for p in obj["proxies"]
                        if isinstance(p, dict)],
        }
        if not self.encrypted:
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        plain = json.dumps(self.data, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            blob = crypto_store.seal_bytes(plain)
            self.encrypted = True
        except OSError:
            blob = plain
            self.encrypted = False
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, self.path)

    @staticmethod
    def _clean(p: dict) -> dict:
        kind = str(p.get("kind") or "socks5")
        if kind not in KINDS:
            kind = "socks5"
        port = str(p.get("port") or "").strip() or default_port(kind)
        return {
            "id": str(p.get("id") or ""),
            "name": str(p.get("name") or ""),
            "kind": kind,
            "host": str(p.get("host") or ""),
            "port": port,
            "user": str(p.get("user") or ""),
            "cred_id": str(p.get("cred_id") or ""),
            "parent_id": str(p.get("parent_id") or ""),
            "note": str(p.get("note") or ""),
            "created": str(p.get("created") or ""),
        }

    @property
    def proxies(self) -> list[dict]:
        return self.data["proxies"]

    def ids(self) -> list[str]:
        return [p["id"] for p in self.proxies if p.get("id")]

    def get(self, pid: str) -> dict | None:
        for p in self.proxies:
            if p.get("id") == pid:
                return p
        return None

    def names(self) -> list[str]:
        return [str(p.get("name") or "") for p in self.proxies]

    def chain_text_of(self, pid: str) -> str:
        return chain_text(self.proxies, pid)

    def upsert(self, rec: dict) -> dict:
        p = self._clean(rec)
        if not p["name"]:
            raise ValueError("代理名不能为空")
        if not p["id"]:
            p["id"] = new_id(self.ids())
        for i, old in enumerate(self.proxies):
            if old.get("id") == p["id"]:
                self.proxies[i] = p
                break
        else:
            self.proxies.append(p)
        self.proxies.sort(key=lambda x: str(x.get("name") or "").lower())
        self.save()
        return p

    def remove(self, pid: str) -> bool:
        n0 = len(self.proxies)
        self.data["proxies"] = [p for p in self.proxies if p.get("id") != pid]
        changed = len(self.data["proxies"]) != n0
        if changed:
            self.save()
        return changed
