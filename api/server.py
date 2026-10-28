# -*- coding: utf-8 -*-
"""本地 HTTP API —— 给 AI 智能体的「SSH 连接技能」用。

一次典型的智能体调用序列：

    POST /v1/sessions            {"host":"192.168.3.12","user":"admin","password":"…"}
    → {"id":"a1b2c3", "device":{...}}

    POST /v1/sessions/a1b2c3/exec {"cmd":"df -h"}
    → {"rc":0, "out":"…"}

    （中间隔十分钟，连接还在）

    DELETE /v1/sessions/a1b2c3

连接保持由 `api/pool.py` 负责：SSH 层 keepalive + 掉线自动重连，
所以智能体不必自己管连接生命周期。

安全：
  · 默认只绑 127.0.0.1，且要求 Bearer token（token 加密存在 data/api.json）
  · 凭证只在内存，响应里永远不回显 token / 密码
  · 上传下载走 SFTP，不接受任意本地路径写入以外的能力
"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import random
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import crypto_store
from api.tools import ToolKit
from api.pool import PoolError, SessionPool

API_VERSION = "1.0"
DEFAULT_PORT = 8760
# 自动挑端口用的区间：挑高位段，避开 8xxx 这种人人都爱抢的，也避开
# Windows 的临时端口段（默认 49152-65535，会跟系统分出去的端口撞）。
PORT_RANGE = (30000, 45000)
MAX_BODY = 32 * 1024 * 1024          # 32MB，够传脚本和小文件


def port_busy(port: int, host: str = "127.0.0.1") -> bool:
    """真绑一次看能不能绑上。不收 SO_REUSEADDR：Windows 上它允许抢占
    已被监听的端口，会把「占用中」误判成「空闲」。"""
    if not (1024 <= int(port) <= 65535):
        return True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, int(port)))
        except OSError:
            return True
    return False


def pick_free_port(host: str = "127.0.0.1", lo: int = 0, hi: int = 0) -> int:
    """在高位区间随机挑一个当前真能绑上的端口。

    不用 bind(0) 让系统分配：那是临时端口，重启就换一个，智能体里存下来的
    地址跟着失效。这里固定区间里随机试，绑得上才算数。
    """
    lo = int(lo or PORT_RANGE[0])
    hi = int(hi or PORT_RANGE[1])
    rng = random.SystemRandom()
    for _ in range(200):
        p = rng.randrange(lo, hi)
        if not port_busy(p, host):
            return p
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])
_UPTIME = time.time()


class ApiError(Exception):
    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.hint = hint


# ==================================================================== 配置
class ApiConfig:
    """data/api.json —— 开关、端口、token（密文）、保活参数。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {
            "enabled": False,
            "host": "127.0.0.1",
            "port": 0,              # 0 = 还没定，下面按空闲情况挑一个
            "token_enc": "",
            "keepalive": 25,
            "idle_ttl": 0,
        }
        self.load()
        if self._port_needs_pick():
            self.data["port"] = pick_free_port(
                str(self.data.get("host") or "127.0.0.1"))
            try:
                self.save()
            except OSError:
                pass          # 写不进去也不影响本次运行

    # 老版本默认值、以及人人都爱抢的端口。落在这里说明不是用户特意选的，
    # 启动时换成一个随机空闲端口，省得每次都撞。
    LEGACY_PORTS = (8760, 8768, 3000, 5000, 8000, 8080, 9000)

    def _port_needs_pick(self) -> bool:
        try:
            port = int(self.data.get("port") or 0)
        except (TypeError, ValueError):
            return True
        if port <= 0:                 # 全新配置：还没有端口
            return True
        return port in self.LEGACY_PORTS

    def load(self) -> None:
        try:
            if self.path.is_file():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for k in self.data:
                        if k in raw:
                            self.data[k] = raw[k]
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.path)

    # ---------------------------------------------------------- token
    def token(self) -> str:
        enc = str(self.data.get("token_enc") or "")
        tok = crypto_store.unprotect(enc) if enc else ""
        if not tok:
            tok = self.rotate()
        return tok

    def rotate(self) -> str:
        tok = "ttb_" + secrets.token_urlsafe(28)
        self.data["token_enc"] = crypto_store.protect(tok) or ""
        self.save()
        return tok

    def token_masked(self) -> str:
        """界面上只给星号 —— 令牌是「要就复制，别摊在屏幕上」的东西。

        以前显示成 `ttb_AbCd12…wxyz`：前 8 后 4 全露着，截图、投屏、录屏
        都等于把大半个令牌送出去了。现在只留前缀区分「这是什么」，
        真要用的地方点旁边的「复制」。
        """
        t = self.token()
        prefix = "ttb_" if t.startswith("ttb_") else ""
        return prefix + "*" * 12

    def public(self) -> dict:
        return {
            "enabled": bool(self.data.get("enabled")),
            "host": self.data.get("host", "127.0.0.1"),
            "port": int(self.data.get("port") or DEFAULT_PORT),
            "keepalive": int(self.data.get("keepalive") or 0),
            "idle_ttl": int(self.data.get("idle_ttl") or 0),
            "token_masked": self.token_masked(),
        }


# ==================================================================== 服务
class ApiServer:
    def __init__(self, config: ApiConfig, pool: SessionPool, on_log=None):
        self.config = config
        self.pool = pool
        self.on_log = on_log              # on_log(kind, text)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.requests = 0
        self.last_error = ""
        self.started_at = 0.0
        # 能力接口（/v1/tools/*）。由 make_default 挂上来，测试里可以手动塞。
        self.tools = None

    # ---------------------------------------------------------- 生命周期
    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> int:
        return int(self.config.data.get("port") or DEFAULT_PORT)

    def start(self) -> tuple[bool, str]:
        if self.running:
            return True, f"已在运行：http://{self._host()}:{self.port}"
        host = self._host()
        want = self.port
        httpd, err = self._bind(host, want)
        moved = ""
        if httpd is None:
            # 端口被别的程序占了：直接换一个空闲的，别让人自己猜该填哪个
            alt = pick_free_port(host)
            httpd, err2 = self._bind(host, alt)
            if httpd is not None:
                moved = f"端口 {want} 被占用，已自动改用 {alt}"
                self.config.data["port"] = alt
            else:
                err = err2
        if httpd is None:
            self.last_error = str(err)
            return False, (f"端口 {self.port} 起不来：{err}。"
                           "点「换端口」自动挑一个空闲端口，"
                           "或先关掉占用它的程序。")
        httpd.daemon_threads = True
        httpd.app = self                        # 供 handler 取用
        self._httpd = httpd
        self.started_at = time.time()
        self.config.data["enabled"] = True
        self.config.save()
        self.config.token()                     # 确保有 token
        self._thread = threading.Thread(target=httpd.serve_forever,
                                        kwargs={"poll_interval": 0.4},
                                        daemon=True, name="api-server")
        self._thread.start()
        self.pool.start()
        if moved:
            self._log("warn", moved)
        self._log("ok", f"API 服务已启动 {self.url()}")
        return True, (f"{moved}，已启动：{self.url()}" if moved
                      else f"已启动：{self.url()}")

    def _bind(self, host: str, port: int):
        try:
            return ThreadingHTTPServer((host, int(port)), _Handler), None
        except OSError as e:  # noqa: BLE001
            return None, e

    def relocate(self) -> tuple[bool, str]:
        """换一个新的空闲端口；服务在跑就停掉换完再起。

        智能体那边存的是完整地址，换完要把新地址重新给它。
        """
        was = self.running
        if was:
            self.stop(keep_enabled=True)
        old = self.port
        new = pick_free_port(self._host())
        self.config.data["port"] = new
        self.config.save()
        base = f"端口 {old} → {new}"
        self._log("meta", base)
        if not was:
            return True, f"{base}（服务未启动）"
        ok, msg = self.start()
        if not ok:
            return False, f"{base}，但重启没成功：{msg}"
        return True, f"{base}，已在新端口起来：{self.url()}"

    def stop(self, keep_enabled: bool = False) -> None:
        """停服务。

        `keep_enabled=True` 表示「这次停不是用户的意思」—— 程序退出、换端口
        重启前都会先停一下，这时候**不能**把 `enabled` 落成 false，
        否则 `_boot_api()` 那句「上次退出前是开着的就自动恢复」永远不成立
        （退出必然走 stop，等于每次启动都被判成「上次是关着的」）。
        用户自己点开关关掉，才落盘记成「关」。
        """
        httpd = self._httpd
        self._httpd = None
        if httpd:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
        self.pool.stop()
        self.pool.close_all()
        if not keep_enabled:
            self.config.data["enabled"] = False
            self.config.save()
        self._log("dim", "API 服务已停止")

    def _host(self) -> str:
        return str(self.config.data.get("host") or "127.0.0.1")

    def url(self) -> str:
        host = self._host()
        h = "127.0.0.1" if host in ("0.0.0.0", "") else host
        return f"http://{h}:{self.port}"

    def _log(self, kind: str, text: str) -> None:
        if self.on_log:
            try:
                self.on_log(kind, text)
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------- 鉴权
    def check_token(self, got: str) -> bool:
        want = self.config.token()
        if not want or not got:
            return False
        return hmac.compare_digest(want, got.strip())


# ==================================================================== 路由
class _Handler(BaseHTTPRequestHandler):
    server_version = "TerminalToolboxAPI/" + API_VERSION
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ---------- 基础
    @property
    def app(self) -> ApiServer:
        return self.server.app                # type: ignore[attr-defined]

    def log_message(self, fmt, *args):        # noqa: A003
        return                                # 静音默认 stderr 日志

    def do_OPTIONS(self):                     # noqa: N802
        self._send(204, None, raw=True)

    def do_GET(self):                         # noqa: N802
        self._handle("GET")

    def do_POST(self):                        # noqa: N802
        self._handle("POST")

    def do_DELETE(self):                      # noqa: N802
        self._handle("DELETE")

    def do_PUT(self):                         # noqa: N802
        self._handle("PUT")

    def _handle(self, method: str) -> None:
        app = self.app
        app.requests += 1
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ApiError(413, f"请求体太大（{n} 字节），上限 {MAX_BODY}")
            raw = self.rfile.read(n) if n else b""
            body = {}
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise ApiError(400, "请求体不是合法 JSON")
                if not isinstance(body, dict):
                    raise ApiError(400, "请求体必须是 JSON 对象")

            u = urlparse(self.path)
            parts = [p for p in u.path.split("/") if p]
            qs = {k: v[0] for k, v in parse_qs(u.query).items()}

            # 公开端点：探活与自描述，方便智能体第一次接进来
            if parts[:2] == ["v1", "health"]:
                return self._json(200, self._health())
            if parts[:2] == ["v1", "openapi.json"]:
                return self._json(200, OPENAPI)

            token = ""
            auth = self.headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
            if not token:
                token = (self.headers.get("X-Api-Token") or "").strip()
            if not token and qs.get("token"):
                token = qs["token"]
            if not app.check_token(token):
                raise ApiError(401, "令牌无效",
                               "在「智能体接入」页复制 Bearer Token，"
                               "放进 Authorization: Bearer <token>")

            status, payload = self._route(method, parts, qs, body)
            self._json(status, payload)
        except ApiError as e:
            self._json(e.status, {"error": e.message, "hint": e.hint})
        except PoolError as e:
            self._json(400, {"error": str(e)})
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            app.last_error = str(e)
            self._json(500, {"error": f"{e.__class__.__name__}: {e}"})

    # ---------- 端点
    def _route(self, method: str, parts: list[str], qs: dict, body: dict):
        app = self.app
        if parts[:1] != ["v1"]:
            raise ApiError(404, "未知路径", "所有接口都在 /v1 下")

        # /v1/tools/* 与 /v1/hosts：工具箱的具体功能（docker/文件/系统/服务）
        if parts[1:2] == ["tools"]:
            if app.tools is None:
                raise ApiError(503, "能力接口没装配", "重启一次程序")
            return app.tools.route(method, parts[2:], qs, body)
        if parts[1:2] == ["hosts"]:
            if app.tools is None:
                raise ApiError(503, "能力接口没装配", "重启一次程序")
            if method != "GET":
                raise ApiError(405, "/v1/hosts 只支持 GET")
            return 200, app.tools.hosts()

        # /v1/sessions...
        if parts[1:2] == ["sessions"]:
            rest = parts[2:]
            if not rest and method == "GET":
                return 200, {"sessions": app.pool.list(), "count": len(app.pool.list())}
            if not rest and method == "POST":
                conn = app.pool.open(body)
                return 201, {"id": conn.id, "device": conn.summary()["device"],
                             "protocol": conn.spec.get("protocol", "ssh"),
                             "note": "用 POST /v1/sessions/<id>/exec 执行命令；"
                                     "连接会保持，靠 keepalive 保活。"}
            if rest and method == "GET" and len(rest) == 1:
                return 200, app.pool.get(rest[0]).summary()
            if rest and method == "DELETE" and len(rest) == 1:
                ok = app.pool.close(rest[0], "客户端请求")
                return (200 if ok else 404), {"closed": ok, "id": rest[0]}
            if len(rest) >= 2:
                cid, action = rest[0], rest[1]
                if method == "POST" and action == "exec":
                    return 200, app.pool.exec(
                        cid, str(body.get("cmd") or ""),
                        timeout=int(body.get("timeout") or 60),
                        admin=bool(body.get("admin")))
                if method == "GET" and action == "metrics":
                    return 200, app.pool.metrics(cid)
                if method == "POST" and action == "metrics":
                    return 200, app.pool.metrics(cid)
                if method == "POST" and action == "upload":
                    path = str(body.get("path") or "")
                    if body.get("content_b64"):
                        try:
                            data = base64.b64decode(body["content_b64"], validate=True)
                        except (binascii.Error, ValueError):
                            raise ApiError(400, "content_b64 不是合法 base64")
                    elif body.get("text") is not None:
                        data = str(body["text"]).encode("utf-8")
                    else:
                        raise ApiError(400, "需要 content_b64 或 text")
                    return 200, app.pool.upload(cid, path, data)
                if method == "GET" and action == "download":
                    res = app.pool.download(cid, qs.get("path", ""))
                    return 200, {"ok": True, "path": res["path"],
                                 "bytes": res["bytes"],
                                 "content_b64": base64.b64encode(res["data"]).decode()}
                if method == "POST" and action == "detect":
                    conn = app.pool.get(cid)
                    return 200, conn.summary()["device"]
                if method == "POST" and action == "keepalive":
                    conn = app.pool.get(cid)
                    if "enabled" in body:
                        conn.keepalive_on = bool(body["enabled"])
                    if "seconds" in body:
                        conn.keepalive = max(0, int(body["seconds"]))
                    return 200, {"id": cid, "keepalive": conn.keepalive,
                                 "enabled": conn.keepalive_on}
            raise ApiError(404, f"没有这个会话接口：{'/'.join(parts)}")

        raise ApiError(404, f"未知路径 /{'/'.join(parts)}",
                       "GET /v1/openapi.json 看完整接口清单")

    # ---------- 响应
    def _health(self) -> dict:
        app = self.app
        return {
            "ok": True, "app": "Terminal GUI Toolbox", "api": API_VERSION,
            "sessions": len(app.pool.list()),
            "uptime_seconds": int(time.time() - _UPTIME),
            "time": int(time.time()),
        }

    def _json(self, status: int, payload) -> None:
        data = b"" if payload is None else json.dumps(
            payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers",
                             "Authorization, Content-Type, X-Api-Token")
            self.send_header("Access-Control-Allow-Methods",
                             "GET, POST, DELETE, PUT, OPTIONS")
            self.end_headers()
            if data:
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send(self, status: int, payload, raw: bool = False) -> None:
        self._json(status, payload)


# ==================================================================== 自描述
OPENAPI = {
    "openapi": "3.1.0",
    "info": {
        "title": "Terminal GUI Toolbox API",
        "version": API_VERSION,
        "description": (
            "本机终端工具箱的 HTTP 接口。为 AI 智能体的 SSH 技能提供"
            "**持久连接**：建会话一次，之后反复 exec，连接由服务端保活"
            "（SSH keepalive + 掉线自动重连）。"
        ),
    },
    "servers": [{"url": "http://127.0.0.1:8760"}],
    "security": [{"bearerAuth": []}],
    "components": {
        "securitySchemes": {
            "bearerAuth": {"type": "http", "scheme": "bearer",
                           "description": "在程序「智能体接入」页复制 token"}
        }
    },
    "paths": {
        "/v1/health": {
            "get": {"summary": "探活（无需令牌）", "security": [],
                    "responses": {"200": {"description": "服务健康"}}}
        },
        "/v1/openapi.json": {
            "get": {"summary": "本文件（无需令牌）", "security": []}
        },
        "/v1/hosts": {
            "get": {"summary": "本机档案里的设备清单（不含口令），按名字连接用"}
        },
        "/v1/tools": {
            "get": {"summary": "工具箱能力清单（建议先读这个）",
                    "description": "返回 docker / 文件 / 系统 / 服务 等具体能力，"
                                   "全部支持 ?session=<id>，不给就用最近活跃的连接"}
        },
        "/v1/tools/sysinfo": {
            "get": {"summary": "系统概览：设备、CPU、内存、磁盘、运行时长"}
        },
        "/v1/tools/services": {
            "get": {"summary": "监听端口 + 服务识别（nginx/mysql/redis…）"}
        },
        "/v1/tools/docker": {
            "get": {"summary": "Docker 状态 + 容器/镜像/卷/网络/占用",
                    "parameters": [{"name": "what", "in": "query",
                                    "schema": {"type": "string",
                                               "enum": ["all", "containers",
                                                        "images", "volumes",
                                                        "networks", "df",
                                                        "stats"]}}]}
        },
        "/v1/tools/docker/logs": {
            "post": {"summary": "容器日志 {session,id,tail}"}
        },
        "/v1/tools/files": {
            "get": {"summary": "列目录（?path=，不给就是 home）"}
        },
        "/v1/tools/files/read": {
            "get": {"summary": "读文件（?path=&max=；二进制给 base64）"}
        },
        "/v1/tools/files/write": {
            "post": {"summary": "写文件 {path, text|content_b64}"}
        },
        "/v1/tools/files/mkdir": {
            "post": {"summary": "建目录 {path}"}
        },
        "/v1/tools/files/remove": {
            "post": {"summary": "删文件/目录 {path, dir?}"}
        },
        "/v1/tools/files/rename": {
            "post": {"summary": "改名/移动 {path, to}"}
        },
        "/v1/sessions": {
            "get": {"summary": "列出所有会话（含 idle 秒数、设备信息）"},
            "post": {
                "summary": "建立并**保持**一个连接",
                "requestBody": {"required": True, "content": {"application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "protocol": {"type": "string", "enum": ["ssh", "local"],
                                         "default": "ssh"},
                            "host": {"type": "string"},
                            "port": {"type": "integer", "default": 22},
                            "user": {"type": "string"},
                            "auth": {"type": "string", "enum": ["key", "password"],
                                     "description": "认证方式。给了就以它为准；"
                                                    "不给则「有 key_path 用私钥、"
                                                    "否则用 password」"},
                            "password": {"type": "string"},
                            "key_path": {"type": "string",
                                         "description": "本机私钥路径（与 password 二选一）"},
                            "key_passphrase": {"type": "string"},
                            "sudo_password": {"type": "string",
                                              "description": "需要改路由/防火墙/Docker 时用"},
                            "keepalive": {"type": "integer", "default": 25,
                                          "description": "空闲多少秒发一次心跳，0=关闭"},
                            "name": {"type": "string",
                                     "description": "便于识别的别名"},
                        },
                        "required": ["host"],
                    }}}},
                "responses": {"201": {"description": "返回 id 与探测到的设备信息"}},
            },
        },
        "/v1/sessions/{id}": {
            "get": {"summary": "单个会话详情"},
            "delete": {"summary": "关闭并释放连接"},
        },
        "/v1/sessions/{id}/exec": {
            "post": {
                "summary": "执行一条命令（连接复用）",
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "properties": {
                        "cmd": {"type": "string"},
                        "timeout": {"type": "integer", "default": 60},
                        "admin": {"type": "boolean", "default": False,
                                  "description": "true 时用 sudo 执行"},
                    }, "required": ["cmd"]}}}},
                "responses": {"200": {"description": "{rc, out, err, ms, truncated}"}},
            }
        },
        "/v1/sessions/{id}/metrics": {
            "get": {"summary": "实时 CPU / 内存 / 网络速率"}
        },
        "/v1/sessions/{id}/upload": {
            "post": {"summary": "SFTP 上传（content_b64 或 text + path）"}
        },
        "/v1/sessions/{id}/download": {
            "get": {"summary": "SFTP 下载（?path=/etc/hosts → content_b64）"}
        },
        "/v1/sessions/{id}/keepalive": {
            "post": {"summary": "调整保活：{enabled:true, seconds:25}"}
        },
    },
}


def make_default(data_dir: Path, on_log=None, on_cmd=None) -> tuple[ApiConfig, SessionPool, ApiServer]:
    cfg = ApiConfig(Path(data_dir) / "api.json")
    pool = SessionPool(keepalive_default=int(cfg.data.get("keepalive") or 25),
                       idle_ttl=int(cfg.data.get("idle_ttl") or 0),
                       on_event=on_log, tracer=on_cmd)
    srv = ApiServer(cfg, pool, on_log=on_log)
    # 能力层要读 profiles.dat（主机档案），所以得知道 data_dir
    srv.tools = ToolKit(pool, Path(data_dir))
    return cfg, pool, srv
