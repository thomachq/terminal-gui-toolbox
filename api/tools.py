# -*- coding: utf-8 -*-
"""工具箱能力接口：`/v1/tools/*`。

为什么要有这一层
================
以前智能体接进来只拿到一个 `exec` —— 等于给了个终端。可工具箱里写好的东西
远不止终端：Docker 页有「逐个试绝对路径 + 三级 sudo + 字段模板解析」，文件页
有 SFTP，系统页有跨平台的 `disk_cmd/uptime_cmd`，安全中心有一整套只读体检。
这些让智能体自己去拼 shell 再解析，既容易错也没必要。

约定了三件事：

1. **所有执行都过 `SessionPool.exec`**。core 里那些引擎只认 `session.run`，
   直接塞真 session 会绕过 pool，于是「智能体通过 API 跑的命令」在日志里就
   没了 —— v1.0.19 辛苦做的 `[API]` 前缀分流等于白做。`_Exec` 这一层就是
   为了把它们接回审计链。
2. **不给连接就报清楚**，不假装成功：返回 400 + hint，让模型知道下一步该建连接。
3. **只读优先**。会改系统的只有 `files/write|remove|rename|mkdir` 这几个
   POST，且每次都单独留痕，事后查得到是谁动的。
"""
from __future__ import annotations

import base64
import binascii
from pathlib import Path

from core import files as fl
from core import parsers, webstack
from core.docker import DockerEngine
from core.metrics import Sampler, human_bytes

MAX_READ = 512 * 1024          # 单次读文件上限（字节），超了让调用方去下载
MAX_LIST = 5000                # 列目录最多返回这么多条


class _Fail(Exception):
    """带 HTTP 语义的失败。route() 负责转成 (status, payload)。"""

    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status, self.message, self.hint = status, message, hint

    def payload(self) -> dict:
        d = {"error": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d


class _Exec:
    """把 `SessionPool.exec` 包成 core 那些引擎认识的 session 形状。

    它们只调 `session.run(cmd, timeout=)`，所以这里只需要这一个方法；
    `alive` 顺手给上，免得某些调用点判 `session.alive` 时 AttributeError。
    """

    def __init__(self, pool, cid: str, conn):
        self.pool, self.cid, self._c = pool, cid, conn

    @property
    def alive(self) -> bool:
        try:
            return bool(self._c.session and self._c.session.alive)
        except Exception:  # noqa: BLE001
            return False

    def run(self, cmd: str, timeout: int = 60, **_kw):
        r = self.pool.exec(self.cid, cmd, timeout=int(timeout))
        return r["rc"], r["out"], r["err"]

    def run_admin(self, cmd: str, timeout: int = 60):
        r = self.pool.exec(self.cid, cmd, timeout=int(timeout), admin=True)
        return r["rc"], r["out"], r["err"]


TOOLS = [
    {"m": "GET", "path": "/v1/tools",
     "desc": "能力清单（智能体可以先读它）"},
    {"m": "GET", "path": "/v1/tools/sysinfo",
     "desc": "系统概览：设备/CPU/内存/磁盘/运行时长"},
    {"m": "GET", "path": "/v1/tools/docker",
     "desc": "Docker 检测 + 容器/镜像/卷/网络/占用（?what=containers 等）"},
    {"m": "POST", "path": "/v1/tools/docker/logs",
     "desc": "容器日志 {session,id,tail}"},
    {"m": "GET", "path": "/v1/tools/services",
     "desc": "监听端口与服务识别（nginx/mysql/redis…）"},
    {"m": "GET", "path": "/v1/tools/files",
     "desc": "列目录 ?path=（不给就是 home）"},
    {"m": "GET", "path": "/v1/tools/files/read",
     "desc": "读文件 ?path=&max=；文本直接给，二进制给 base64"},
    {"m": "POST", "path": "/v1/tools/files/write",
     "desc": "写文件 {path, text|content_b64}"},
    {"m": "POST", "path": "/v1/tools/files/mkdir",
     "desc": "建目录 {path}"},
    {"m": "POST", "path": "/v1/tools/files/remove",
     "desc": "删文件/目录 {path, dir?}"},
    {"m": "POST", "path": "/v1/tools/files/rename",
     "desc": "改名/移动 {path, to}"},
    {"m": "GET", "path": "/v1/hosts",
     "desc": "本机档案里的设备清单（不含口令），便于按名字连接"},
]


def _q(qs: dict, key: str, default: str = "") -> str:
    v = qs.get(key, default)
    if isinstance(v, (list, tuple)):
        v = v[0] if v else default
    return str(v if v is not None else default)


def _flag(qs: dict, key: str) -> bool:
    return _q(qs, key).strip().lower() in ("1", "true", "yes", "on", "y")


class ToolKit:
    """一组「智能体可以直接用」的工具端点。"""

    def __init__(self, pool, data_dir):
        self.pool = pool
        self.data_dir = Path(data_dir)

    # ------------------------------------------------------------ 路由
    def route(self, method: str, parts: list[str], qs: dict, body: dict):
        """parts 是 /v1/tools 之后的那几段。返回 (status, payload)。"""
        try:
            return self._route(method, parts, qs, body)
        except _Fail as e:
            return e.status, e.payload()
        except Exception as e:  # noqa: BLE001  端点不该把整个服务打挂
            return 500, {"error": f"{e.__class__.__name__}: {e}"}

    def _route(self, method, parts, qs, body):
        name = parts[0] if parts else ""
        if not name and method == "GET":
            return 200, {"tools": TOOLS, "count": len(TOOLS),
                         "note": "都能带 ?session=<id>；不给就用最近活跃的连接。"}

        if name == "sysinfo" and method == "GET":
            return 200, self.sysinfo(self._conn(qs))

        if name == "services" and method == "GET":
            return 200, self.services(self._conn(qs))

        if name == "docker":
            return self._docker(method, parts[1:], qs, body)

        if name == "files":
            return self._files(method, parts[1:], qs, body)

        raise _Fail(404, f"没有这个工具：{name or '(空)'}",
                    "GET /v1/tools 看完整能力清单")

    # ------------------------------------------------------------ 连接
    def _conn(self, qs: dict, body: dict | None = None):
        sid = _q(qs, "session") or str((body or {}).get("session") or "")
        if sid:
            try:
                return self.pool.get(sid)
            except Exception as e:  # noqa: BLE001
                raise _Fail(404, f"找不到连接 {sid}", str(e))
        items = self.pool.list()
        if not items:
            raise _Fail(400, "现在还没有连接",
                        "先 POST /v1/sessions 建一条（或带 ?session=<id>）")
        # 最近用过的优先：多台设备时「刚才那台」比「随便一台」合理
        best = max(items, key=lambda s: s.get("last_used") or 0)
        try:
            return self.pool.get(best["id"])
        except Exception as e:  # noqa: BLE001
            raise _Fail(409, "取连接失败", str(e))

    def _note(self, c, text: str, rc: int = 0, out: str = "", err: str = ""):
        """给不走 shell 的操作（SFTP）补一条审计。

        文件读写不是命令，但「智能体动了哪个文件」同样得留痕。走 session 上
        那个被 pool 包过的 `_trace`，前缀与命令一致，日志里一眼能对上。
        """
        trace = getattr(getattr(c, "session", None), "_trace", None)
        if callable(trace):
            try:
                trace(f"sftp {text}", rc, out, err, 0)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ 系统
    def sysinfo(self, c) -> dict:
        """一台机器的全景：谁、什么系统、多少 CPU/内存、盘占多少。

        分三次跑是因为 Windows 的 uptime 与 df 本来就是两条完全不同的命令，
        合成一条要写一堆 `command -v` 判断，反而更容易错。
        """
        d = c.dialect
        ex = _Exec(self.pool, c.id, c)
        dev = c.summary()["device"]

        sample = Sampler(ex, d).sample()
        disk = []
        try:
            rc, out, err = ex.run(d.disk_cmd(), timeout=30)
            if rc == 0:
                disk = parsers.disk_table(out)[:40]
        except Exception:  # noqa: BLE001
            pass

        up_raw = ""
        try:
            rc, out, err = ex.run(d.uptime_cmd(), timeout=20)
            up_raw = (out or err or "").strip().splitlines()[:2]
            up_raw = "\n".join(up_raw)
        except Exception:  # noqa: BLE001
            pass

        return {
            "session": c.id,
            "device": dev,
            "cpu": {"count": sample.ncpu, "pct": round(sample.cpu_pct, 1),
                    "ok": sample.ok, "error": sample.error},
            "memory": {
                "total": sample.mem_total, "used": sample.mem_used,
                "pct": round(sample.mem_pct, 1),
                "total_h": human_bytes(sample.mem_total),
                "used_h": human_bytes(sample.mem_used),
            },
            "uptime_seconds": int(sample.uptime),
            "net": {"rx_bps": round(sample.net_rx_bps, 1),
                    "tx_bps": round(sample.net_tx_bps, 1)},
            "disk": disk,
            "uptime_raw": up_raw,
            "unsupported": bool(sample.unsupported),
        }

    # ------------------------------------------------------------ 服务
    def services(self, c) -> dict:
        """监听中的端口 + 认出是哪个服务。

        复用 Web 服务页那套 `parse_listeners_detail` —— 它已经处理了
        ss / netstat 两种输出，以及 Tomcat 那种「进程叫 java 得看 pid」的情况。
        """
        d = c.dialect
        ex = _Exec(self.pool, c.id, c)
        rc, out, err = ex.run(d.listen_ports_cmd(), timeout=25)
        rows = webstack.parse_listeners_detail(out or err)
        items = [{"addr": a, "port": p, "process": n, "pid": pid,
                  "kind": webstack.kind_of(n)} for a, p, n, pid in rows]
        items.sort(key=lambda r: r["port"])
        return {"session": c.id, "rc": rc, "count": len(items),
                "listeners": items}

    # ------------------------------------------------------------ Docker
    def _docker(self, method, rest, qs, body):
        sub = rest[0] if rest else ""
        c = self._conn(qs, body)
        eng = DockerEngine(_Exec(self.pool, c.id, c), c.device)
        info = eng.detect()

        if method == "POST" and sub == "logs":
            if not eng.ready:
                raise _Fail(409, "Docker 现在不可用", str(info.get("detail")))
            cid = str(body.get("id") or "").strip()
            if not cid:
                raise _Fail(400, "需要容器 id 或名字")
            tail = int(body.get("tail") or 200)
            return 200, {"session": c.id, "id": cid,
                         "logs": eng.logs(cid, tail=max(1, min(tail, 2000)))}

        what = (sub or _q(qs, "what") or "all").lower()
        out = {"session": c.id, "docker": info, "ready": eng.ready}
        if not eng.ready:
            title, why = eng.message()
            out.update({"title": title, "why": why, "what": what})
            return 200, out

        if what in ("all", "containers"):
            out["containers"] = eng.ps(all_=_flag(qs, "all") or _q(qs, "all") != "0")
        if what in ("all", "images"):
            out["images"] = eng.images()
        if what in ("all", "volumes"):
            out["volumes"] = eng.volumes()
        if what in ("all", "networks"):
            out["networks"] = eng.networks()
        if what in ("all", "df"):
            out["df"] = eng.df()
        if what == "stats":
            out["stats"] = eng.stats()
        for k in ("containers", "images", "volumes", "networks", "df", "stats"):
            if k in out and isinstance(out[k], list):
                out[f"{k}_count"] = len(out[k])
        return 200, out

    # ------------------------------------------------------------ 文件
    def _fs(self, c):
        proto = str(c.spec.get("protocol") or "ssh").lower()
        if proto == "local":
            return fl.LocalFS()
        if proto == "telnet":
            raise _Fail(400, "Telnet 没有文件通道",
                        "文件操作需要 SSH 连接（SFTP）")
        if not c.session or not getattr(c.session, "sftp", None):
            raise _Fail(409, "这条连接不支持 SFTP")
        return fl.SFTPFS(c.session)

    def _files(self, method, rest, qs, body):
        sub = (rest[0] if rest else ("write" if method == "POST" else "list")).lower()
        c = self._conn(qs, body)
        fs = self._fs(c)

        if sub in ("list", "ls"):
            path = _q(qs, "path") or str(body.get("path") or "")
            real = fs.normalize(path) if path else fs.home()
            rows = fs.listdir(real)
            self._note(c, f"list {real}")
            cut = rows[:MAX_LIST]
            return 200, {
                "session": c.id, "path": real, "parent": fs.parent(real),
                "count": len(rows), "truncated": len(rows) > len(cut),
                "entries": [e.row() for e in cut],
            }

        if sub == "read":
            path = _q(qs, "path") or str(body.get("path") or "")
            if not path:
                raise _Fail(400, "需要 path")
            real = fs.normalize(path)
            cap = int(_q(qs, "max") or body.get("max") or MAX_READ)
            cap = max(1, min(cap, MAX_READ))
            with fs.open_read(real) as f:
                data = f.read(cap + 1)
            over = len(data) > cap
            data = data[:cap]
            self._note(c, f"read {real} ({len(data)}B)",
                       0, f"{len(data)} bytes")
            try:
                text = data.decode("utf-8")
                return 200, {"session": c.id, "path": real, "bytes": len(data),
                             "truncated": over, "encoding": "utf-8",
                             "text": text}
            except UnicodeDecodeError:
                return 200, {"session": c.id, "path": real, "bytes": len(data),
                             "truncated": over, "encoding": "base64",
                             "content_b64": base64.b64encode(data).decode()}

        if method != "POST":
            raise _Fail(405, f"{sub} 要用 POST")

        if sub == "write":
            path = str(body.get("path") or "")
            if not path:
                raise _Fail(400, "需要 path")
            if body.get("content_b64") is not None:
                try:
                    data = base64.b64decode(str(body["content_b64"]), validate=True)
                except (binascii.Error, ValueError):
                    raise _Fail(400, "content_b64 不是合法 base64")
            elif body.get("text") is not None:
                data = str(body["text"]).encode("utf-8")
            else:
                raise _Fail(400, "需要 text 或 content_b64")
            real = fs.normalize(path)
            with fs.open_write(real) as f:
                f.write(data)
            self._note(c, f"write {real} ({len(data)}B)", 0, f"{len(data)} bytes")
            return 200, {"session": c.id, "path": real, "bytes": len(data),
                         "ok": True}

        if sub == "mkdir":
            path = str(body.get("path") or "")
            if not path:
                raise _Fail(400, "需要 path")
            real = fs.normalize(path)
            fs.mkdir(real)
            self._note(c, f"mkdir {real}")
            return 200, {"session": c.id, "path": real, "ok": True}

        if sub == "remove":
            path = str(body.get("path") or "")
            if not path:
                raise _Fail(400, "需要 path")
            real = fs.normalize(path)
            is_dir = bool(body.get("dir")) or fs.is_dir(real)
            fs.remove(real, is_dir=is_dir)
            self._note(c, f"remove {'dir' if is_dir else 'file'} {real}")
            return 200, {"session": c.id, "path": real, "removed": True,
                         "dir": is_dir}

        if sub == "rename":
            path = str(body.get("path") or "")
            to = str(body.get("to") or "")
            if not path or not to:
                raise _Fail(400, "需要 path 和 to")
            a, b = fs.normalize(path), fs.normalize(to)
            fs.rename(a, b)
            self._note(c, f"rename {a} -> {b}")
            return 200, {"session": c.id, "from": a, "to": b, "ok": True}

        raise _Fail(404, f"没有这个文件操作：{sub}",
                    "可用：list / read / write / mkdir / remove / rename")

    # ------------------------------------------------------------ 档案
    def hosts(self) -> dict:
        """本机连接档案里的设备清单 —— 不给口令，只给「连什么」。

        智能体最容易卡在「我不知道有哪些机器」。读 profiles.dat（加密的，
        这里只取非敏感字段）就不会每次都反问用户。
        """
        import crypto_store
        import profiles as P

        # 密钥目录必须先指到 data/：ProfileStore 自己不会去设，
        # 主程序是在启动时调 set_key_dir() 的。API 跑在同一个进程里通常
        # 已经设过，但「独立起一个服务做验证」时没有 —— 那样便携密钥
        # （data/.vpncert-key）没生效，会退到 DPAPI，结果就是档案读不出来
        # 却一声不响地返回空列表。
        crypto_store.set_key_dir(self.data_dir)

        store = P.ProfileStore(self.data_dir / "profiles.dat")
        rows = []
        for p in (store.data.get("profiles") or []):
            rows.append({
                "name": p.get("name") or "",
                "host": p.get("host") or "",
                "port": int(p.get("port") or 0),
                "user": p.get("user") or "",
                "protocol": p.get("protocol") or "ssh",
                "group": p.get("group") or "",
                "tags": p.get("tags") or "",
                "os": p.get("os") or "",
                # 口令 / 私钥 / sudo 密码一概不出现，连"有没有存"都不说
            })
        return {"count": len(rows), "hosts": rows,
                "note": "只列地址与账号，不含口令；连接用 POST /v1/sessions。"}
