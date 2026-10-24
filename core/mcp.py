# -*- coding: utf-8 -*-
"""MCP（Model Context Protocol）库 —— 清单 + 客户端。

零安装铁律不受影响：MCP 服务跑在**本机**（操作者这台），不是跑到对面的
被管设备上。对面依旧只吃 SSH / Telnet，不落任何东西。

怎么用：
    store = MCPStore(DATA_DIR / "mcp.json")
    pool = MCPPool(store)
    pool.tools()                 # [{"id": "mcp__fs__read_file", "spec": {...}}]
    pool.call("mcp__fs__read_file", {"path": "/etc/hosts"})   # 返回文本

协议实现故意只做三件事：initialize → tools/list → tools/call。
不碰 resources / prompts / sampling —— 这个工具箱里的 MCP 只有「给助手
多几个能调的工具」这一个用途，多实现的每一块都是没人用的风险面。

传输两种：
  · stdio —— 起子进程，按行 JSON-RPC（一行一条消息）
  · http  —— POST JSON-RPC，响应可能是 application/json 或 text/event-stream
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

PROTOCOLS = ("2025-06-18", "2024-11-05")
CLIENT_INFO = {"name": "terminal-toolbox", "version": "1.0"}


class MCPError(RuntimeError):
    """MCP 调用失败。消息直接给界面显示，所以必须是人话。"""


def slug(text: str) -> str:
    """把服务器名/工具名收敛成能塞进工具 id 的字符集。"""
    s = re.sub(r"[^0-9A-Za-z_]+", "_", str(text or "")).strip("_").lower()
    return s or "x"


def tool_id(sid: str, name: str) -> str:
    return f"mcp__{slug(sid)}__{slug(name)}"


def parse_tool_id(tid: str) -> tuple[str, str]:
    """`mcp__srv__tool_name` → ("srv", "tool_name")。

    注意工具名里本身可能有下划线（read_file），所以按「前两段」切，
    剩下的整段都算工具名。
    """
    parts = str(tid or "").split("__")
    if len(parts) < 3 or parts[0] != "mcp":
        return "", ""
    return parts[1], "__".join(parts[2:])


# ==================================================================== 清单
class MCPStore:
    """`data/mcp.json` 的读写。一条服务器一条记录。"""

    def __init__(self, path):
        self.path = path
        self.errors: list[str] = []
        self.data: dict = {"servers": []}
        self.load()

    # ---------------------------------------------------------- 读写
    def load(self) -> None:
        self.errors = []
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.data = {"servers": []}
            return
        except OSError as e:
            self.errors.append(f"读不了 {self.path}：{e}")
            self.data = {"servers": []}
            return
        try:
            obj = json.loads(raw or "{}")
        except json.JSONDecodeError as e:
            self.errors.append(f"mcp.json 不是合法 JSON：{e}")
            self.data = {"servers": []}
            return
        servers = obj.get("servers") if isinstance(obj, dict) else None
        self.data = {"servers": [self._norm(r) for r in (servers or [])
                                 if isinstance(r, dict)]}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            self.errors.append(f"存不了 {self.path}：{e}")

    @staticmethod
    def _norm(rec: dict) -> dict:
        out = dict(rec)
        out["sid"] = str(out.get("sid") or slug(out.get("name") or "srv"))
        out["name"] = str(out.get("name") or out["sid"])
        out["transport"] = "http" if str(out.get("transport")) == "http" else "stdio"
        out["command"] = str(out.get("command") or "")
        args = out.get("args")
        if isinstance(args, str):
            args = args.split()
        out["args"] = [str(a) for a in (args or [])]
        env = out.get("env")
        env = env if isinstance(env, dict) else {}
        out["env"] = {str(k): str(v) for k, v in env.items()}
        out["url"] = str(out.get("url") or "")
        headers = out.get("headers")
        out["headers"] = headers if isinstance(headers, dict) else {}
        try:
            out["timeout"] = max(3, min(120, int(out.get("timeout") or 20)))
        except (TypeError, ValueError):
            out["timeout"] = 20
        out["enabled"] = bool(out.get("enabled", True))
        out["note"] = str(out.get("note") or "")
        return out

    # ---------------------------------------------------------- 查询
    def all(self) -> list[dict]:
        return [dict(r) for r in self.data.get("servers") or []]

    def enabled(self) -> list[dict]:
        return [r for r in self.all() if r.get("enabled")]

    def get(self, sid: str) -> dict | None:
        for r in self.data.get("servers") or []:
            if r.get("sid") == sid:
                return dict(r)
        return None

    def upsert(self, rec: dict) -> dict:
        row = self._norm(rec)
        rows = self.data.setdefault("servers", [])
        for i, r in enumerate(rows):
            if r.get("sid") == row["sid"]:
                rows[i] = row
                break
        else:
            rows.append(row)
        self.save()
        return row

    def remove(self, sid: str) -> bool:
        rows = self.data.setdefault("servers", [])
        keep = [r for r in rows if r.get("sid") != sid]
        if len(keep) == len(rows):
            return False
        self.data["servers"] = keep
        self.save()
        return True

    def import_json(self, path) -> tuple[int, str]:
        """从别家的 mcp.json 导入（`{"mcpServers": {...}}` 或 `{"servers": [...]}`）。"""
        try:
            obj = json.loads(open(path, encoding="utf-8").read() or "{}")
        except (OSError, json.JSONDecodeError) as e:
            return 0, f"读不了这个文件：{e}"
        n = 0
        table = obj.get("mcpServers") if isinstance(obj, dict) else None
        if isinstance(table, dict):
            for name, cfg in table.items():
                if not isinstance(cfg, dict):
                    continue
                self.upsert({
                    "sid": slug(name), "name": name,
                    "transport": "http" if cfg.get("url") else "stdio",
                    "command": cfg.get("command") or "",
                    "args": cfg.get("args") or [],
                    "env": cfg.get("env") or {},
                    "url": cfg.get("url") or "",
                    "enabled": not cfg.get("disabled"),
                })
                n += 1
        rows = obj.get("servers") if isinstance(obj, dict) else None
        for cfg in rows or []:
            if isinstance(cfg, dict):
                self.upsert(cfg)
                n += 1
        return n, f"导入了 {n} 个服务器" if n else "这个文件里没有可识别的服务器"


# ==================================================================== 传输
class _StdioSession:
    """stdio 传输：起子进程，按行收发 JSON-RPC。"""

    def __init__(self, rec: dict):
        self.rec = rec
        self.proc: subprocess.Popen | None = None
        self._lines: queue.Queue = queue.Queue()
        self._stash: dict = {}
        self._lock = threading.Lock()
        self._seq = 0
        self.stderr_tail: list[str] = []
        self.protocol = ""

    # ---------------------------------------------------------- 生命周期
    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        cmd = [self.rec.get("command") or ""] + list(self.rec.get("args") or [])
        if not cmd[0]:
            raise MCPError("这条服务器没填启动命令")
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in (self.rec.get("env") or {}).items()})
        flags = 0
        if sys.platform.startswith("win"):
            # 不起黑窗口：这个进程是给助手用的，用户不该看到一堆控制台
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, bufsize=1, text=True,
                encoding="utf-8", errors="replace", cwd=self.rec.get("cwd") or None,
                creationflags=flags)
        except (OSError, ValueError) as e:
            raise MCPError(f"起不来：{e}") from e
        threading.Thread(target=self._pump_out, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump_out(self) -> None:
        p = self.proc
        if p is None or p.stdout is None:
            return
        for line in p.stdout:
            line = line.strip()
            if line:
                self._lines.put(line)
        self._lines.put(None)

    def _pump_err(self) -> None:
        p = self.proc
        if p is None or p.stderr is None:
            return
        for line in p.stderr:
            self.stderr_tail.append(line.rstrip()[:400])
            del self.stderr_tail[:-40]

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        p, self.proc = self.proc, None
        if p is None:
            return
        try:
            if p.stdin:
                p.stdin.close()
        except OSError:
            pass
        try:
            p.terminate()
            p.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                p.kill()
            except OSError:
                pass

    # ---------------------------------------------------------- 请求
    def request(self, method: str, params: dict | None, timeout: float) -> dict:
        self.start()
        with self._lock:
            self._seq += 1
            rid = self._seq
            payload = {"jsonrpc": "2.0", "id": rid, "method": method,
                       "params": params or {}}
            p = self.proc
            if p is None or p.stdin is None:
                raise MCPError("进程没起来")
            try:
                p.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                p.stdin.flush()
            except (OSError, ValueError) as e:
                raise MCPError(f"写不进去：{e}") from e
            deadline = time.time() + timeout
            while True:
                if rid in self._stash:
                    return self._stash.pop(rid)
                left = deadline - time.time()
                if left <= 0:
                    msg = f"{method} 超时（{int(timeout)} 秒）"
                    if self.stderr_tail:
                        msg += "；stderr：" + self.stderr_tail[-1][:160]
                    raise MCPError(msg)
                try:
                    line = self._lines.get(timeout=min(left, 0.5))
                except queue.Empty:
                    if self.proc is not None and self.proc.poll() is not None:
                        tail = self.stderr_tail[-1][:200] if self.stderr_tail else ""
                        raise MCPError("进程退出了" + (f"：{tail}" if tail else ""))
                    continue
                if line is None:
                    raise MCPError("进程关掉了输出")
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                mid = msg.get("id")
                if mid is None:
                    continue                      # 通知，丢掉
                if mid != rid:
                    self._stash[mid] = msg        # 别的请求的回复，先存着
                    continue
                if "error" in msg:
                    err = msg.get("error") or {}
                    raise MCPError(str(err.get("message") or err))
                return msg.get("result") or {}


class _HttpSession:
    """http 传输：POST JSON-RPC，兼容 JSON 与 SSE 两种响应。"""

    def __init__(self, rec: dict):
        self.rec = rec
        self.session_id = ""
        self.protocol = ""
        self._seq = 0
        self.stderr_tail: list[str] = []

    def start(self) -> None:
        if not self.rec.get("url"):
            raise MCPError("这条服务器没填 URL")

    def alive(self) -> bool:
        return True

    def stop(self) -> None:
        self.session_id = ""

    def request(self, method: str, params: dict | None, timeout: float) -> dict:
        self._seq += 1
        rid = self._seq
        payload = {"jsonrpc": "2.0", "id": rid, "method": method,
                   "params": params or {}}
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        for k, v in (self.rec.get("headers") or {}).items():
            headers[str(k)] = str(v)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(
            str(self.rec.get("url")), data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                ctype = (resp.headers.get("Content-Type") or "").lower()
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except OSError:
                pass
            raise MCPError(f"HTTP {e.code}" + (f"：{body}" if body else "")) from e
        except (urllib.error.URLError, OSError) as e:
            raise MCPError(f"连不上：{e}") from e
        msg = self._pick(raw, rid, "event-stream" in ctype)
        if msg is None:
            raise MCPError("响应里没有对应的 JSON-RPC 回复")
        if "error" in msg:
            err = msg.get("error") or {}
            raise MCPError(str(err.get("message") or err))
        return msg.get("result") or {}

    @staticmethod
    def _pick(raw: str, rid: int, sse: bool):
        """从 JSON 或 SSE 里挑出 id 对得上的那条回复。"""
        blobs: list[str] = []
        if sse:
            for line in raw.splitlines():
                if line.startswith("data:"):
                    blobs.append(line[5:].strip())
        else:
            blobs.append(raw.strip())
        for b in blobs:
            if not b:
                continue
            try:
                obj = json.loads(b)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("id") == rid:
                return obj
        for b in blobs:                       # 实在对不上就退回第一条像回复的
            try:
                obj = json.loads(b)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                return obj
        return None


# ==================================================================== 会话与池
class Session:
    """一条 MCP 服务器：管传输 + 握手 + 工具表。"""

    def __init__(self, rec: dict):
        self.rec = rec
        self.sid = str(rec.get("sid") or slug(rec.get("name") or "srv"))
        self.tools: list[dict] = []
        self.ready = False
        self.error = ""
        self.protocol = ""
        self._io = (_HttpSession(rec) if rec.get("transport") == "http"
                    else _StdioSession(rec))

    # ---------------------------------------------------------- 握手
    def start(self) -> bool:
        if self.ready:
            return True
        timeout = float(self.rec.get("timeout") or 20)
        last = ""
        for ver in PROTOCOLS:
            try:
                res = self._io.request("initialize", {
                    "protocolVersion": ver,
                    "capabilities": {},
                    "clientInfo": CLIENT_INFO,
                }, timeout)
            except MCPError as e:
                last = str(e)
                break                        # 连不上/超时，换协议版本也没用
            self.protocol = str(res.get("protocolVersion") or ver)
            try:
                self._notify("notifications/initialized", {})
            except MCPError:
                pass
            self.ready = True
            self.error = ""
            self.refresh_tools()
            return True
        self.error = last or "握手失败"
        return False

    def _notify(self, method: str, params: dict) -> None:
        io = self._io
        if isinstance(io, _StdioSession):
            io.start()
            p = io.proc
            if p is None or p.stdin is None:
                return
            p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method,
                                      "params": params}) + "\n")
            p.stdin.flush()

    def refresh_tools(self) -> list[dict]:
        timeout = float(self.rec.get("timeout") or 20)
        res = self._io.request("tools/list", {}, timeout)
        rows = res.get("tools") if isinstance(res, dict) else None
        self.tools = [t for t in (rows or []) if isinstance(t, dict)]
        return self.tools

    def call(self, name: str, args: dict) -> str:
        timeout = float(self.rec.get("timeout") or 20)
        res = self._io.request("tools/call",
                               {"name": name, "arguments": args or {}}, timeout)
        return flatten(res)

    def alive(self) -> bool:
        return self.ready and self._io.alive()

    def stop(self) -> None:
        try:
            self._io.stop()
        except Exception:  # noqa: BLE001
            pass
        self.ready = False


def flatten(res: dict) -> str:
    """把 tools/call 的返回拍平成人读的文本。

    结构是 {"content": [{"type": "text", "text": ...}, ...], "isError": bool}。
    图片/资源这类块不做渲染，只报一句「有个图片块」—— 运维助手看文字就够，
    把 base64 塞进上下文只会烧 token。
    """
    if not isinstance(res, dict):
        return str(res)
    parts: list[str] = []
    for blk in res.get("content") or []:
        if not isinstance(blk, dict):
            parts.append(str(blk))
            continue
        kind = blk.get("type")
        if kind == "text":
            parts.append(str(blk.get("text") or ""))
        elif kind == "image":
            parts.append(f"（图片块 {blk.get('mimeType') or ''}，已省略）")
        elif kind == "resource":
            res2 = blk.get("resource") or {}
            parts.append(str(res2.get("text") or f"（资源 {res2.get('uri')}）"))
        else:
            parts.append(json.dumps(blk, ensure_ascii=False)[:400])
    text = "\n".join(p for p in parts if p)
    if res.get("isError"):
        text = "调用失败：" + (text or "服务端没给原因")
    return text


class MCPPool:
    """会话池：按 sid 懒启动，工具表带缓存。"""

    def __init__(self, store: MCPStore):
        self.store = store
        self._sessions: dict[str, Session] = {}
        self._tool_map: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------- 会话
    def session(self, sid: str) -> Session | None:
        rec = self.store.get(sid)
        if rec is None:
            return None
        with self._lock:
            s = self._sessions.get(sid)
            if s is None or not s.alive():
                s = Session(rec)
                self._sessions[sid] = s
        return s

    def start(self, sid: str) -> tuple[bool, str]:
        s = self.session(sid)
        if s is None:
            return False, "找不到这条服务器"
        ok = s.start()
        return ok, s.error

    def stop(self, sid: str) -> None:
        with self._lock:
            s = self._sessions.pop(sid, None)
        if s is not None:
            s.stop()
        self.rebuild_tools()

    def stop_all(self) -> None:
        with self._lock:
            rows = list(self._sessions.values())
            self._sessions.clear()
        for s in rows:
            s.stop()

    def status(self, sid: str) -> str:
        s = self._sessions.get(sid)
        if s is None:
            return "未启动"
        if not s.ready:
            return "启动中" if not s.error else "失败"
        return f"就绪 · {len(s.tools)} 个工具"

    # ---------------------------------------------------------- 工具
    def rebuild_tools(self) -> dict[str, tuple[str, str]]:
        """把所有启用服务器的工具收成一张 id → (sid, tool) 的表。"""
        out: dict[str, tuple[str, str]] = {}
        for rec in self.store.enabled():
            sid = rec["sid"]
            s = self._sessions.get(sid)
            tools = s.tools if (s is not None and s.ready) else []
            for t in tools:
                out[tool_id(sid, str(t.get("name") or ""))] = (
                    sid, str(t.get("name") or ""))
        self._tool_map = out
        return out

    def ensure_tools(self) -> list[dict]:
        """启动所有启用中的服务器并拉一次工具表（失败的不拦别人）。"""
        for rec in self.store.enabled():
            sid = rec["sid"]
            s = self.session(sid)
            if s is None:
                continue
            try:
                if not s.ready:
                    s.start()
            except MCPError as e:
                s.error = str(e)
        self.rebuild_tools()
        return self.tools()

    def tools(self) -> list[dict]:
        """[{"id", "sid", "name", "desc", "schema"}] —— 给页面与提示词用。"""
        if not self._tool_map:
            self.rebuild_tools()
        out: list[dict] = []
        for rec in self.store.enabled():
            sid = rec["sid"]
            s = self._sessions.get(sid)
            if s is None or not s.ready:
                continue
            for t in s.tools:
                name = str(t.get("name") or "")
                out.append({
                    "id": tool_id(sid, name),
                    "sid": sid,
                    "server": rec.get("name") or sid,
                    "name": name,
                    "desc": str(t.get("description") or ""),
                    "schema": t.get("inputSchema") or {},
                })
        return out

    def call(self, tid: str, args: dict | None = None) -> str:
        sid, name = parse_tool_id(tid)
        if not sid:
            # 也允许直接传工具名（模型偶尔会省掉前缀）
            for k, (s, n) in (self._tool_map or self.rebuild_tools()).items():
                if k.endswith("__" + slug(tid)) or n == tid:
                    sid, name = s, n
                    break
        if not sid:
            raise MCPError(f"不认识这个工具：{tid}")
        s = self.session(sid)
        if s is None:
            raise MCPError(f"服务器 {sid} 不在清单里")
        if not s.start():
            raise MCPError(f"{sid} 起不来：{s.error}")
        return s.call(name, args or {})

    # ---------------------------------------------------------- 试连
    def probe(self, rec: dict) -> tuple[bool, str, list[dict]]:
        """按一条临时配置试连（不写清单、不影响已在跑的那个）。"""
        s = Session(MCPStore._norm(rec))
        try:
            ok = s.start()
            if not ok:
                return False, s.error or "握手失败", []
            return True, f"通了（协议 {s.protocol or '未知'}）", s.tools
        except MCPError as e:
            return False, str(e), []
        finally:
            s.stop()
