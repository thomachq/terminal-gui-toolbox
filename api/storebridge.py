# -*- coding: utf-8 -*-
"""档案 / 凭证 / 脚本 / 代理 / 审计 的通用 CRUD 桥。

与 `corebridge` 的分工：
  · corebridge 暴露**纯函数**（解析、校验、拼命令）
  · storebridge 暴露**持久化实体**（增改删查）

五个 store 全部复用 `app.py` 里同一批类，密文文件格式不变，与经典版共用同一份 data/。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import crypto_store
from core.audit import AuditStore
from core.creds import CredStore
from core.proxies import ProxyStore
from core.scripts import ScriptStore
from profiles import ProfileStore

ENTITIES = ("hosts", "creds", "scripts", "proxies", "audit")

# 哪些字段回给前端（口令/密钥**绝不**在 list 里回传）
PUBLIC_FIELDS: dict[str, tuple[str, ...]] = {
    "hosts": ("name", "host", "port", "user", "auth", "proto", "note", "group",
              "tags", "cred_id", "proxy_id", "remember", "sudo", "elevate",
              "shell", "ostype", "os_name", "key_path", "com_port", "device_id",
              "created", "last_used"),
    "creds": ("id", "name", "kind", "user", "note", "created"),
    "scripts": ("id", "name", "kind", "desc", "tags", "builtin", "created", "body"),
    "proxies": ("id", "name", "kind", "host", "port", "user", "cred_id",
                "parent_id", "note", "created"),
}


class StoreBridge:
    def __init__(self, data_dir: Path):
        data_dir = Path(data_dir)
        crypto_store.set_key_dir(data_dir)
        self.hosts = ProfileStore(data_dir / "profiles.dat")
        self.creds = CredStore(data_dir / "creds.dat")
        self.scripts = ScriptStore(data_dir / "scripts.json")
        self.proxies = ProxyStore(data_dir / "proxies.dat")
        self.audit = AuditStore(data_dir / "logs")

    def store(self, entity: str):
        if entity not in ENTITIES:
            raise KeyError(f"未知实体：{entity}（可用：{', '.join(ENTITIES)}）")
        return getattr(self, entity)

    def list(self, entity: str) -> list[dict]:
        s = self.store(entity)
        if entity == "hosts":
            rows = s.profiles
        elif entity == "creds":
            rows = s.creds
        elif entity == "scripts":
            rows = s.all()
        elif entity == "proxies":
            rows = s.proxies
        else:
            return s.load()
        keys = PUBLIC_FIELDS.get(entity, ())
        return [{k: r.get(k) for k in keys if k in r} for r in rows]

    def get(self, entity: str, key: str) -> dict:
        s = self.store(entity)
        if entity == "audit":
            raise ValueError("审计记录用 list(limit=) 读取，不支持按 id 取")
        row = (s.get(key) if entity in ("hosts", "creds", "proxies")
               else s.get(key))
        if row is None:
            raise KeyError(f"找不到 {entity} / {key}")
        keys = PUBLIC_FIELDS.get(entity, ())
        return {k: row.get(k) for k in keys if k in row}

    def upsert(self, entity: str, rec: dict) -> dict:
        if entity == "audit":
            raise ValueError("审计记录只读追加，不能改")
        s = self.store(entity)
        # 经典版「口令三态」：secret/password/key_passphrase 留空 = 保留旧值
        key = (rec.get("name") if entity == "hosts" else
               rec.get("id") or rec.get("name") or "")
        if key:
            old = s.get(key)
            if isinstance(old, dict):
                for k in ("password", "key_passphrase", "sudo_password", "secret"):
                    if not rec.get(k):
                        rec[k] = old.get(k, "")
        s.upsert(dict(rec))
        s.save()
        return {"ok": True, "entity": entity, "key": key}

    def remove(self, entity: str, key: str) -> dict:
        if entity == "audit":
            raise ValueError("审计记录不能删")
        s = self.store(entity)
        ok = s.remove(key)
        if ok:
            s.save()
        return {"ok": bool(ok), "entity": entity, "key": key}

    def tail(self, n: int = 50) -> list[dict]:
        return self.audit.tail(n)
