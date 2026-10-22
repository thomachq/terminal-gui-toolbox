# -*- coding: utf-8 -*-
"""操作审计日志 —— 「谁在什么时候对哪台机器做了什么，成没成」。

这份日志记录的是**动作**，不是命令输出：AI 在终端执行了一条命令、批准了一次
审批、写入了一个本地文件、建立/关闭了一个终端会话。命令原文与输出由命令日志
（侧边栏那份）负责，两者分开放，查的时候才知道自己在查什么。

存法：`data/logs/audit-YYYYMMDD.jsonl`，一天一个文件，追加写。
  · 追加写不会因为程序崩溃丢掉已经记下的部分（这是审计日志的底线）
  · 按天切分，查最近一周不用把一年的量读进内存
  · 事件名用点分层级（`capability.terminal.exec`），前端才有东西可分类

`title_of()` 把事件名翻成人话。翻译不到的**不猜**，原样显示事件名 ——
一条看不太懂的真实记录，比一条编得很像的假标题有用。
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
from pathlib import Path

# ------------------------------------------------------------------ 事件字典
# 键是事件名，值是 (中文标题, 分类)。分类用于筛选，粗一点就够用。
EVENTS: dict[str, tuple[str, str]] = {
    # 终端
    "terminal.session.create": ("创建终端会话", "终端"),
    "terminal.session.close": ("关闭终端会话", "终端"),
    "terminal.session.reconnect": ("重连终端会话", "终端"),
    "capability.terminal.exec": ("AI 在终端执行命令", "终端"),
    "capability.terminal.write": ("AI 向终端写入", "终端"),
    "terminal.command": ("执行命令", "终端"),
    "terminal.sudo": ("提权执行", "终端"),
    # 审批
    "approval.requested": ("发起审批", "审批"),
    "approval.approved": ("批准审批", "审批"),
    "approval.rejected": ("拒绝审批", "审批"),
    "approval.timeout": ("审批超时", "审批"),
    # 文件
    "fs.read": ("读取本地文件", "文件"),
    "fs.write": ("写入本地文件", "文件"),
    "fs.delete": ("删除本地文件", "文件"),
    "fs.upload": ("上传文件", "文件"),
    "fs.download": ("下载文件", "文件"),
    "capability.fs.write": ("写入本地文件", "文件"),
    "capability.fs.read": ("读取本地文件", "文件"),
    # AI
    "ai.chat": ("AI 对话", "AI"),
    "ai.tool.call": ("AI 调用工具", "AI"),
    "ai.step": ("AI 推进一步", "AI"),
    "ai.error": ("AI 执行出错", "AI"),
    # 主机与配置
    "host.connect": ("连接主机", "主机"),
    "host.disconnect": ("断开主机", "主机"),
    "host.add": ("添加主机", "主机"),
    "host.remove": ("删除主机", "主机"),
    "host.import": ("导入主机", "主机"),
    "cred.add": ("新增凭证", "凭证"),
    "cred.remove": ("删除凭证", "凭证"),
    "proxy.add": ("新增代理", "代理"),
    "proxy.remove": ("删除代理", "代理"),
    "script.run": ("运行脚本", "脚本"),
    "script.save": ("保存脚本", "脚本"),
    "sec.scan": ("执行安全体检", "安全"),
    "fw.change": ("修改防火墙", "安全"),
    "cron.change": ("修改计划任务", "安全"),
    "site.bind": ("绑定站点域名", "Web"),
    "tls.issue": ("签发证书", "证书"),
    "backup.export": ("导出备份", "备份"),
}

# 前缀兜底：精确表没命中时按第一段归类，标题给个「某类操作」
PREFIX: dict[str, tuple[str, str]] = {
    "capability.terminal": ("终端操作", "终端"),
    "capability.fs": ("本地文件操作", "文件"),
    "capability": ("能力调用", "AI"),
    "terminal": ("终端操作", "终端"),
    "fs": ("文件操作", "文件"),
    "ai": ("AI 操作", "AI"),
    "host": ("主机操作", "主机"),
    "approval": ("审批", "审批"),
    "cred": ("凭证操作", "凭证"),
    "proxy": ("代理操作", "代理"),
    "script": ("脚本操作", "脚本"),
    "sec": ("安全操作", "安全"),
}

STATUS: dict[str, tuple[str, str]] = {
    "ok": ("成功", "ok"),
    "fail": ("失败", "err"),
    "warn": ("警告", "warn"),
    "info": ("信息", "dim"),
}
STATUS_KEYS = tuple(STATUS)

PAGE_SIZES = (10, 20, 50, 100)
DEFAULT_PAGE_SIZE = 10
_SEARCH_SPLIT = re.compile(r"[,，;；、\s]+")


def title_of(event: str) -> str:
    """事件名 → 人话。翻不出来就照原样显示。"""
    e = (event or "").strip()
    if not e:
        return "(未命名事件)"
    hit = EVENTS.get(e)
    if hit:
        return hit[0]
    for prefix, val in PREFIX.items():
        if e == prefix or e.startswith(prefix + "."):
            return val[0]
    return e


def group_of(event: str) -> str:
    e = (event or "").strip()
    hit = EVENTS.get(e)
    if hit:
        return hit[1]
    for prefix, val in PREFIX.items():
        if e == prefix or e.startswith(prefix + "."):
            return val[1]
    return "其他"


def status_label(status: str) -> tuple[str, str]:
    return STATUS.get((status or "").strip(), ("信息", "dim"))


def groups() -> list[str]:
    seen: list[str] = []
    for _, g in list(EVENTS.values()) + list(PREFIX.values()):
        if g not in seen:
            seen.append(g)
    if "其他" not in seen:
        seen.append("其他")
    return seen


# ------------------------------------------------------------------ 时间
def now_ts() -> int:
    return int(time.time())


def fmt_full(v) -> str:
    """`2026/10/23 00:29:32` —— 审计日志要秒，排查时差一分钟都对不上。"""
    ts = _to_ts(v)
    if ts is None:
        return str(v or "")
    try:
        return time.strftime("%Y/%m/%d %H:%M:%S", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return ""


def _to_ts(v):
    if v in (None, "", 0, "0"):
        return None
    try:
        ts = float(v)
    except (TypeError, ValueError):
        return None
    return ts if ts > 0 else None


def day_key(ts: float | None = None) -> str:
    t = ts if ts is not None else time.time()
    return time.strftime("%Y%m%d", time.localtime(t))


def today_label() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


# ------------------------------------------------------------------ 记录
def make_event(event: str, status: str = "ok", detail: str = "",
               device: str = "", actor: str = "用户", dur_ms: int = 0,
               ts: float | None = None, extra: dict | None = None) -> dict:
    """造一条事件。`id` 用时间 + 序号拼，同秒多条也不会撞。"""
    t = float(ts if ts is not None else time.time())
    ev = {
        "id": f"{int(t * 1000):013d}-{_seq():04d}",
        "ts": str(int(t)),
        "event": str(event or "").strip(),
        "status": status if status in STATUS else "info",
        "detail": str(detail or ""),
        "device": str(device or ""),
        "actor": str(actor or ""),
        "dur": int(dur_ms or 0),
    }
    if extra:
        for k, v in extra.items():
            if k not in ev:
                ev[k] = v
    return ev


_SEQ = [0]


def _seq() -> int:
    _SEQ[0] = (_SEQ[0] + 1) % 10000
    return _SEQ[0]


def event_view(ev: dict) -> dict:
    """表格行。副标题放事件名 —— 那是排查时的检索锚点，别藏起来。"""
    ev = ev or {}
    ev_name = str(ev.get("event") or "")
    label, tone = status_label(str(ev.get("status") or ""))
    return {
        "title": title_of(ev_name),
        "event": ev_name or "—",
        "status": label,
        "status_key": str(ev.get("status") or "info"),
        "tone": tone,
        "time": fmt_full(ev.get("ts")),
        "group": group_of(ev_name),
        "device": str(ev.get("device") or "—"),
        "actor": str(ev.get("actor") or "—"),
        "detail": str(ev.get("detail") or ""),
        "dur": f"{int(ev.get('dur') or 0)} ms" if ev.get("dur") else "",
        "_raw": ev,
    }


def filter_events(evs: list[dict], q: str = "", status: str = "",
                  group: str = "", since: float | None = None) -> list[dict]:
    """关键词命中 标题 / 事件名 / 详情 / 设备 / 操作者。多词是 AND。"""
    out = list(evs or [])
    if status:
        out = [e for e in out if str((e or {}).get("status") or "") == status]
    if group:
        out = [e for e in out if group_of(str((e or {}).get("event") or "")) == group]
    if since:
        out = [e for e in out if (_to_ts((e or {}).get("ts")) or 0) >= since]
    words = [w for w in _SEARCH_SPLIT.split((q or "").strip()) if w]
    if words:
        def hit(e: dict) -> bool:
            name = str((e or {}).get("event") or "")
            pool = " ".join([
                title_of(name), name, str((e or {}).get("detail") or ""),
                str((e or {}).get("device") or ""),
                str((e or {}).get("actor") or ""),
                group_of(name),
            ]).lower()
            return all(w.lower() in pool for w in words)
        out = [e for e in out if hit(e)]
    return out


def sort_events(evs: list[dict], desc: bool = True) -> list[dict]:
    def key(e: dict):
        return (_to_ts((e or {}).get("ts")) or 0, str((e or {}).get("id") or ""))
    return sorted(evs or [], key=key, reverse=desc)


def stats(evs: list[dict]) -> dict:
    out = {"total": 0, "ok": 0, "fail": 0, "warn": 0, "info": 0}
    for e in evs or []:
        out["total"] += 1
        k = str((e or {}).get("status") or "info")
        out[k if k in out else "info"] += 1
    return out


def summary(rows: list[dict], total: int | None = None) -> str:
    n = len(rows or [])
    if total is not None and total != n:
        return f"筛选出 {n} 条 / 共 {total} 条日志"
    return f"共 {n} 条日志" if n else "还没有日志"


# ------------------------------------------------------------------ 分页
def paginate(rows: list[dict], page: int = 1,
             size: int = DEFAULT_PAGE_SIZE) -> tuple[list[dict], int, int]:
    """→ (本页数据, 总页数, 校正后的页码)。

    页码越界不报错，夹回有效范围 —— 删掉最后一页的最后一条之后，
    界面还停在一个不存在的页码上是最常见的翻车点。
    """
    total = len(rows or [])
    size = size if size in PAGE_SIZES else DEFAULT_PAGE_SIZE
    pages = max(1, (total + size - 1) // size)
    p = min(max(1, int(page or 1)), pages)
    start = (p - 1) * size
    return list(rows or [])[start:start + size], pages, p


def page_window(page: int, pages: int, span: int = 2) -> list[int | None]:
    """页码条：`[1, None, 4, 5, 6, None, 24]`。None 表示省略号。

    首页与末页永远显示；当前页两侧各 `span` 个。页数少时直接全列。
    """
    pages = max(1, int(pages or 1))
    page = min(max(1, int(page or 1)), pages)
    if pages <= span * 2 + 5:
        return list(range(1, pages + 1))
    want = {1, pages, page}
    for d in range(1, span + 1):
        if page - d >= 1:
            want.add(page - d)
        if page + d <= pages:
            want.add(page + d)
    out: list[int | None] = []
    prev = 0
    for n in sorted(want):
        if prev and n - prev > 1:
            out.append(None)
        out.append(n)
        prev = n
    return out


def range_text(page: int, size: int, total: int) -> str:
    """`第 1-10 条，共 235 条`。空清单说「共 0 条」。"""
    total = max(0, int(total or 0))
    if not total:
        return "共 0 条"
    size = size if size in PAGE_SIZES else DEFAULT_PAGE_SIZE
    pages = max(1, (total + size - 1) // size)
    p = min(max(1, int(page or 1)), pages)
    lo = (p - 1) * size + 1
    hi = min(p * size, total)
    return f"第 {lo}-{hi} 条，共 {total} 条"


# ------------------------------------------------------------------ 导出
def export_csv(path: str | Path, evs: list[dict]) -> int:
    """导出 CSV。用 utf-8-sig，Excel 双击打开不乱码。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = sort_events(evs, desc=False)
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["时间", "事件", "标题", "分类", "状态", "设备", "操作者",
                    "耗时(ms)", "详情"])
        for e in rows:
            e = e or {}
            w.writerow([
                fmt_full(e.get("ts")), str(e.get("event") or ""),
                title_of(str(e.get("event") or "")),
                group_of(str(e.get("event") or "")),
                status_label(str(e.get("status") or ""))[0],
                str(e.get("device") or ""), str(e.get("actor") or ""),
                int(e.get("dur") or 0), str(e.get("detail") or ""),
            ])
    return len(rows)


def export_jsonl(path: str | Path, evs: list[dict]) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = sort_events(evs, desc=False)
    with p.open("w", encoding="utf-8") as f:
        for e in rows:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return len(rows)


# ------------------------------------------------------------------ 存储
class AuditStore:
    """按天追加的 jsonl。读的时候从最新一天往前扫，够 limit 就停。"""

    MAX_BYTES = 8 * 1024 * 1024      # 单文件超过 8MB 就不载入（防手滑塞大文件）

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._cache: list[dict] | None = None
        self._cache_stamp: tuple = ()
        self._cache_days = 0

    def _day_path(self, ts: float | None = None) -> Path:
        return self.root / f"audit-{day_key(ts)}.jsonl"

    # -- 写
    def log(self, ev: dict) -> dict:
        e = ev if isinstance(ev, dict) else make_event(str(ev))
        if not e.get("ts"):
            e["ts"] = str(now_ts())
        if not e.get("id"):
            e["id"] = make_event("")["id"]
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            ts = _to_ts(e.get("ts")) or time.time()
            with self._day_path(ts).open("a", encoding="utf-8") as f:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except OSError:
            # 写不进日志不该让业务操作失败 —— 但也不能假装成功
            return e
        self._cache = None
        return e

    def record(self, event: str, status: str = "ok", detail: str = "",
               device: str = "", actor: str = "用户",
               dur_ms: int = 0) -> dict:
        return self.log(make_event(event, status, detail, device, actor, dur_ms))

    # -- 读
    def files(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted((p for p in self.root.glob("audit-*.jsonl")),
                      key=lambda p: p.name, reverse=True)

    def _stamp(self) -> tuple:
        out = []
        for p in self.files()[:3]:
            try:
                st = p.stat()
                out.append((p.name, st.st_mtime, st.st_size))
            except OSError:
                continue
        return tuple(out)

    def load(self, limit: int = 3000, days: int = 60) -> list[dict]:
        """跨天读，返回**时间倒序**。days 限制最多回看几天。"""
        stamp = self._stamp()
        days = max(1, int(days))
        if (self._cache is not None and stamp == self._cache_stamp
                and days == self._cache_days):
            return self._cache[:limit]
        out: list[dict] = []
        for i, p in enumerate(self.files()):
            if i >= max(1, int(days)):
                break
            try:
                if p.stat().st_size > self.MAX_BYTES:
                    continue
                with p.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(obj, dict) and obj.get("event"):
                            out.append(obj)
            except OSError:
                continue
            if len(out) >= limit * 2:
                break
        out = sort_events(out, desc=True)
        self._cache = out
        self._cache_stamp = stamp
        self._cache_days = days
        return out[:limit]

    def clear_cache(self) -> None:
        self._cache = None
        self._cache_stamp = ()
        self._cache_days = 0

    def export(self, path: str | Path, evs: list[dict],
               fmt: str = "csv") -> int:
        if str(fmt).lower() == "jsonl":
            return export_jsonl(path, evs)
        return export_csv(path, evs)

    def tail(self, n: int = 50) -> list[dict]:
        return self.load(limit=max(1, int(n)))
