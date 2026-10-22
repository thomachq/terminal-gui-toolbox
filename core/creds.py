# -*- coding: utf-8 -*-
"""凭证管理 —— 用户名 + 口令/私钥，独立成库，主机只引用 id。

为什么不让口令直接躺在主机档案里：一台机器换口令，要改的是一处；
十台机器共用一套运维账号，改一次十台全跟着变。主机记录里只留 `cred_id`，
删除凭证时能精确知道「还有哪些主机在用它」，拦住误删。

落盘：`data/creds.dat`，整份密文（`crypto_store`），和 profiles.dat 同一套。
内存里 secret 是明文（要用才能连），但**任何展示路径都不回显它** ——
界面只显示「已设置 · 12 位」，复制按钮复制的是用户名，不是口令。
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
    "password": {"label": "登录密码", "hint": "SSH / Telnet 登录口令", "tone": "brand"},
    "key": {"label": "私钥", "hint": "私钥文件路径（不复制私钥内容）", "tone": "ok"},
    "passphrase": {"label": "私钥口令", "hint": "解密私钥用的口令", "tone": "warn"},
    "sudo": {"label": "sudo 密码", "hint": "提权时自动使用", "tone": "warn"},
}
KIND_KEYS = tuple(KINDS)
MASK = "••••••••"
_BAD_NAME = re.compile(r"[\x00-\x1f]")


def kind_label(kind: str) -> str:
    return KINDS.get(kind, KINDS["password"])["label"]


def new_id(existing: list[str] | None = None) -> str:
    """8 位十六进制。撞了就重摇 —— 概率极低，但撞了就是数据串味。"""
    have = set(existing or [])
    for _ in range(64):
        v = _secrets.token_hex(4)
        if v not in have:
            return v
    raise RuntimeError("凭证 id 生成失败：连续 64 次撞号")


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


# ------------------------------------------------------------------ 展示
def secret_state(secret: str) -> tuple[str, str]:
    """(文案, 色调)。只描述「有没有」，不描述「是什么」。"""
    if not (secret or "").strip():
        return "未设置", "err"
    return "已设置", "ok"


def secret_len_hint(secret: str) -> str:
    n = len((secret or "").strip())
    return f"{n} 位" if n else "—"


def secret_brief(c: dict) -> str:
    """给表格用的一句话：`已设置 · 12 位` / `未设置`。"""
    sec = (c or {}).get("secret") or ""
    if not sec.strip():
        return "未设置"
    if (c or {}).get("kind") == "key":
        # 私钥凭证存的是路径，路径本身不是机密，可以显示
        return sec.strip()
    return f"已设置 · {secret_len_hint(sec)}"


def cred_view(c: dict, used_by: list[str] | None = None) -> dict:
    """表格行。`used_by` 是引用它的主机名列表。"""
    c = c or {}
    used = list(used_by or [])
    return {
        "name": str(c.get("name") or "未命名凭证"),
        "kind": kind_label(str(c.get("kind") or "")),
        "kind_key": str(c.get("kind") or "password"),
        "user": str(c.get("user") or "—"),
        "secret": secret_brief(c),
        "usage": f"{len(used)} 台" if used else "未使用",
        "usage_n": len(used),
        "used_names": used,
        "note": str(c.get("note") or ""),
        "created": fmt_ts(c.get("created")),
        "_id": str(c.get("id") or ""),
        "_raw": c,
    }


def filter_creds(rows: list[dict], q: str = "", kind: str = "") -> list[dict]:
    out = list(rows or [])
    if kind:
        out = [r for r in out if str(r.get("kind_key") or "") == kind]
    words = [w for w in re.split(r"[,，;；、\s]+", (q or "").strip()) if w]
    if words:
        def hit(r: dict) -> bool:
            pool = " ".join([
                str(r.get("name") or ""), str(r.get("user") or ""),
                str(r.get("kind") or ""), str(r.get("note") or ""),
                " ".join(r.get("used_names") or []),
            ]).lower()
            return all(w.lower() in pool for w in words)
        out = [r for r in out if hit(r)]
    return out


def summary(rows: list[dict], total: int | None = None) -> str:
    n = len(rows or [])
    if total is not None and total != n:
        return f"筛选出 {n} 条 / 共 {total} 条凭证"
    return f"共 {n} 条凭证" if n else "还没有凭证"


# ------------------------------------------------------------------ 校验
def validate(rec: dict, others: list[dict] | None = None,
             skip_id: str = "") -> list[str]:
    errs: list[str] = []
    rec = rec or {}
    name = str(rec.get("name") or "").strip()
    if not name:
        errs.append("名称不能为空")
    elif _BAD_NAME.search(name):
        errs.append("名称里不能有控制字符")
    if not str(rec.get("kind") or "") in KINDS:
        errs.append("类型不对（只能是 登录密码 / 私钥 / 私钥口令 / sudo 密码）")
    if str(rec.get("kind") or "") in ("password", "passphrase", "sudo"):
        if not str(rec.get("secret") or "").strip() and not rec.get("keep_secret"):
            errs.append("口令不能为空")
    if str(rec.get("kind") or "") == "key":
        sec = str(rec.get("secret") or "").strip()
        if not sec and not rec.get("keep_secret"):
            errs.append("私钥路径不能为空")
        elif sec and os.name == "nt" and sec.startswith("~"):
            errs.append("路径别用 ~，Windows 下请写完整路径")
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


def usage_map(creds: list[dict], hosts: list[dict]) -> dict[str, list[str]]:
    """{cred_id: [主机名…]}。给删除保护和「使用中」列用。"""
    out: dict[str, list[str]] = {}
    for h in hosts or []:
        cid = str((h or {}).get("cred_id") or "")
        if not cid:
            continue
        out.setdefault(cid, []).append(str((h or {}).get("name") or ""))
    for lst in out.values():
        lst.sort()
    return out


def blank_cred(kind: str = "password") -> dict:
    return {"id": "", "name": "", "kind": kind if kind in KINDS else "password",
            "user": "", "secret": "", "note": "", "created": str(now_ts())}


def cred_from_form(data: dict, base: dict | None = None) -> dict:
    """表单 → 记录。`secret` 留空且 keep_secret 时保留原口令（编辑时不强制重填）。"""
    out = dict(base or blank_cred())
    for k in ("name", "user", "note"):
        if k in data:
            out[k] = str(data.get(k) or "").strip()
    if "kind" in data and str(data.get("kind") or "") in KINDS:
        # 类型改了就不能沿用旧口令（一个是密码一个是路径，语义不同）
        if str(data.get("kind")) != out.get("kind"):
            out["secret"] = ""
        out["kind"] = str(data.get("kind"))
    new_secret = data.get("secret")
    if new_secret is not None and str(new_secret).strip():
        out["secret"] = str(new_secret)
    elif not base:
        out["secret"] = str(new_secret or "")
    if not out.get("created"):
        out["created"] = str(now_ts())
    return out


def delete_block_reason(used_by: list[str]) -> str:
    """还有主机引用时返回一句拦截理由，否则空串。"""
    used = list(used_by or [])
    if not used:
        return ""
    head = "、".join(used[:3])
    more = f" 等 {len(used)} 台" if len(used) > 3 else ""
    return f"还有 {len(used)} 台主机在用它：{head}{more}。请先在这些主机上换掉凭证。"


# ------------------------------------------------------------------ 存储
class CredStore:
    """`data/creds.dat`：整份密文。版本字段留着以后迁移用。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.encrypted = False
        self.data: dict = {"version": 1, "creds": []}
        self.load()

    def load(self) -> None:
        self.data = {"version": 1, "creds": []}
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
        if not isinstance(obj, dict) or not isinstance(obj.get("creds"), list):
            return
        self.data = {
            "version": int(obj.get("version") or 1),
            "creds": [self._clean(c) for c in obj["creds"] if isinstance(c, dict)],
        }
        if not self.encrypted:
            self.save()          # 明文（手工放进去的）顺手升级成密文

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
    def _clean(c: dict) -> dict:
        kind = str(c.get("kind") or "password")
        if kind not in KINDS:
            kind = "password"
        return {
            "id": str(c.get("id") or ""),
            "name": str(c.get("name") or ""),
            "kind": kind,
            "user": str(c.get("user") or ""),
            "secret": str(c.get("secret") or ""),
            "note": str(c.get("note") or ""),
            "created": str(c.get("created") or ""),
        }

    # -- 查询
    @property
    def creds(self) -> list[dict]:
        return self.data["creds"]

    def ids(self) -> list[str]:
        return [c["id"] for c in self.creds if c.get("id")]

    def get(self, cid: str) -> dict | None:
        for c in self.creds:
            if c.get("id") == cid:
                return c
        return None

    def by_name(self, name: str) -> dict | None:
        n = (name or "").strip().lower()
        for c in self.creds:
            if str(c.get("name") or "").strip().lower() == n:
                return c
        return None

    def names(self) -> list[str]:
        return [str(c.get("name") or "") for c in self.creds]

    def usage(self, hosts: list[dict]) -> dict[str, list[str]]:
        return usage_map(self.creds, hosts)

    # -- 写入
    def upsert(self, rec: dict) -> dict:
        c = self._clean(rec)
        if not c["name"]:
            raise ValueError("凭证名不能为空")
        if not c["id"]:
            c["id"] = new_id(self.ids())
        for i, old in enumerate(self.creds):
            if old.get("id") == c["id"]:
                self.creds[i] = c
                break
        else:
            self.creds.append(c)
        self.creds.sort(key=lambda x: str(x.get("name") or "").lower())
        self.save()
        return c

    def remove(self, cid: str) -> bool:
        n0 = len(self.creds)
        self.data["creds"] = [c for c in self.creds if c.get("id") != cid]
        changed = len(self.data["creds"]) != n0
        if changed:
            self.save()
        return changed

    def secret_of(self, cid: str) -> str:
        c = self.get(cid)
        return str((c or {}).get("secret") or "")
