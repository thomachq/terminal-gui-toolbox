# -*- coding: utf-8 -*-
"""助手的多会话存储 —— 一个会话一个 JSON，落在 data/agent_sessions/。

为什么不用一个大文件：会话是「一条一条长出来的」，每次追加都要整文件重写，
写一半断电就把所有会话一起毁了。一会话一文件，坏了只坏一个，其余照旧。

存什么：
  id / title / created / updated   —— 列表页要看的东西
  history / tokens                 —— 接着聊要用的上下文（Agent 的两块状态）
  transcript                       —— 只给人看的气泡流水（重启后能重新渲染出来）
  files                            —— 这次对话里出现过的本机文件（「引用」用）

不存什么：界面控件。切会话时按 transcript 重画，控件本来就是一次性的。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

MAX_TRANSCRIPT = 300          # 一个会话最多留 300 条气泡
MAX_HISTORY = 60              # 给模型的历史最多 60 条（30 轮）
TITLE_MAX = 16


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def clip_title(text: str, limit: int = TITLE_MAX) -> str:
    """把第一句话压成标签页上放得下的名字。"""
    s = " ".join(str(text or "").split())
    if not s:
        return "新会话"
    if len(s) > limit:
        return s[:limit - 1] + "…"
    return s


class Session:
    """一个会话的全部可持久状态。故意做成薄壳，方便直接 dump 成 JSON。"""

    __slots__ = ("id", "title", "created", "updated", "history", "tokens",
                 "transcript", "files", "pinned")

    def __init__(self, sid: str = "", title: str = "新会话"):
        self.id = sid or _new_id()
        self.title = title or "新会话"
        self.created = _now()
        self.updated = self.created
        self.history: list[dict] = []
        self.tokens = 0
        self.transcript: list[dict] = []
        self.files: dict[str, str] = {}
        self.pinned = False            # 用户手动改过名字就不再自动改名

    # ---------------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}

    @classmethod
    def from_dict(cls, obj: dict) -> "Session":
        s = cls(str(obj.get("id") or ""), str(obj.get("title") or "新会话"))
        for k in ("created", "updated", "tokens"):
            try:
                setattr(s, k, float(obj.get(k) or 0) if k != "tokens"
                        else int(obj.get(k) or 0))
            except (TypeError, ValueError):
                pass
        h = obj.get("history")
        s.history = [x for x in h if isinstance(x, dict)][-MAX_HISTORY:] \
            if isinstance(h, list) else []
        t = obj.get("transcript")
        s.transcript = [x for x in t if isinstance(x, dict)][-MAX_TRANSCRIPT:] \
            if isinstance(t, list) else []
        f = obj.get("files")
        s.files = {str(k): str(v) for k, v in f.items()} \
            if isinstance(f, dict) else {}
        s.pinned = bool(obj.get("pinned"))
        if not s.created:
            s.created = _now()
        if not s.updated:
            s.updated = s.created
        return s

    # ---------------------------------------------------------------- 追加
    def say(self, role: str, text: str, attach=None) -> dict:
        """记一条气泡。role: user / ai / note / err。"""
        rec = {"role": role, "text": str(text or ""), "ts": _now()}
        if attach:
            rec["attach"] = [str(a) for a in attach]
        self.transcript.append(rec)
        if len(self.transcript) > MAX_TRANSCRIPT:
            del self.transcript[:-MAX_TRANSCRIPT]
        self.updated = _now()
        return rec

    def trim_history(self) -> None:
        if len(self.history) > MAX_HISTORY:
            del self.history[:-MAX_HISTORY]

    def auto_title(self, goal: str) -> bool:
        """首问之后按问题改名。用户自己改过（pinned）就不动。"""
        if self.pinned or self.title not in ("", "新会话"):
            return False
        self.title = clip_title(goal)
        self.pinned = False
        return True

    def digest(self) -> str:
        """列表里那句「最后说了什么」。"""
        for rec in reversed(self.transcript):
            if rec.get("role") in ("user", "ai") and rec.get("text"):
                return " ".join(str(rec["text"]).split())[:60]
        return "（还没有对话）"


class Store:
    """data/agent_sessions 目录的读写门面。出错一律不抛，返回空/False。"""

    def __init__(self, root):
        self.root = Path(root) / "agent_sessions"

    # ---------------------------------------------------------------- 磁盘
    def _path(self, sid: str) -> Path:
        safe = "".join(c for c in str(sid) if c.isalnum() or c in "-_")
        return self.root / f"{safe}.json"

    def ensure(self) -> bool:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False

    def save(self, sess: Session) -> bool:
        if not self.ensure():
            return False
        p = self._path(sess.id)
        tmp = p.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(sess.to_dict(), ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, p)      # 原子替换：写一半崩了也还是旧文件
            return True
        except (OSError, ValueError, TypeError):
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    def load(self, sid: str) -> Session | None:
        try:
            obj = json.loads(self._path(sid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return Session.from_dict(obj) if isinstance(obj, dict) else None

    def delete(self, sid: str) -> bool:
        try:
            self._path(sid).unlink()
            return True
        except OSError:
            return False

    def all(self) -> list[Session]:
        """按「最近聊过」倒序。坏文件直接跳过，不让它拖垮整个列表。"""
        out: list[Session] = []
        try:
            files = list(self.root.glob("*.json"))
        except OSError:
            return out
        for p in files:
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(obj, dict):
                out.append(Session.from_dict(obj))
        out.sort(key=lambda s: s.updated or 0, reverse=True)
        return out

    def create(self, title: str = "新会话") -> Session:
        s = Session(title=title)
        self.save(s)
        return s

    def prune(self, keep: int = 60) -> int:
        """文件太多了删最老的。返回删了几个。"""
        items = self.all()
        if len(items) <= keep:
            return 0
        n = 0
        for s in items[keep:]:
            if self.delete(s.id):
                n += 1
        return n
