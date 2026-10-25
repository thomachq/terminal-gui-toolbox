# -*- coding: utf-8 -*-
"""会话池：给 AI 智能体用的「一直连着」的连接。

为什么要池：SSH 握手 + 认证 + 系统探测加起来常常两三秒。智能体一次任务里
可能要跑十几条命令，每条都新建连接既慢又容易把设备的 sshd 顶到连接上限。
所以这里把连接**挂住**，命令按 id 打过来，用完不关，靠 keepalive 保活。

生命周期：
  open()  → 连接 + 探测 + 建采样器
  exec()  → 加锁跑命令（同一连接不允许并发 exec，避免输出串台）
  tick()  → 每 5 秒扫一遍：idle 超 keepalive 就发 SSH keepalive；
            断了就按保存的凭证重连（有限次）
  close() → 用户显式关，或超 idle_ttl

安全边界：
  · 凭证只存在内存（self.spec），不落盘、不进日志
  · 日志里出现的命令/输出由上层 tracer 决定，池本身不打日志
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field

from core.detect import DeviceInfo, detect, dialect_for
from core.metrics import Sampler
from core.redact import scrub
from core.session import ConnSpec, SessionError, open_session

MAX_OUT_CHARS = 200_000        # 单条命令回传上限，防止把智能体上下文撑爆

# 智能体通过 API 执行的命令，进日志/终端时统一带这个前缀。
# 主程序靠它区分「人敲的」和「模型跑的」（事件名、actor 都不一样），
# 所以两边必须用同一个常量，不要各自写死字符串。
API_CMD_TAG = "[API] "


def _tagged(tracer):
    """把主程序的 tracer 包一层，给每条命令加上 API 标记。

    挂在会话上（而不是只在 exec 里加），是为了让这个会话干的所有事都带标记 ——
    包括 open() 时那几轮设备探测，否则日志里会冒出一串「谁也不知道是谁跑的」命令。
    """
    def wrapped(display, rc, out, err, ms):
        text = str(display or "")
        if not text.startswith(API_CMD_TAG):
            text = API_CMD_TAG + text
        return tracer(text, rc, out, err, ms)
    return wrapped


@dataclass
class Conn:
    id: str
    spec: dict
    session: object
    device: DeviceInfo
    dialect: object
    sampler: Sampler | None = None
    name: str = ""
    created: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    keepalive: int = 25
    keepalive_on: bool = True
    execs: int = 0
    errors: int = 0
    reconnects: int = 0
    last_error: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)

    def touch(self) -> None:
        self.last_used = time.time()

    def summary(self) -> dict:
        d = self.device
        return {
            "id": self.id,
            "name": self.name or self.id,
            "protocol": self.spec.get("protocol", "ssh"),
            "host": self.spec.get("host", ""),
            "port": int(self.spec.get("port", 0) or 0),
            "user": self.spec.get("user", ""),
            "alive": bool(self.session and self.session.alive),
            "created": int(self.created),
            "last_used": int(self.last_used),
            "idle_seconds": int(time.time() - self.last_used),
            "keepalive": self.keepalive if self.keepalive_on else 0,
            "execs": self.execs,
            "errors": self.errors,
            "reconnects": self.reconnects,
            "device": {
                "os_family": d.os_family,
                "os_name": d.os_name,
                "kernel": d.kernel,
                "arch": d.arch,
                "hostname": d.hostname,
                "device_type": d.device_type,
                "shell": d.shell,
                "user": d.user,
                "is_root": d.is_root,
                "vendor": d.vendor,
            },
            "last_error": self.last_error,
        }


class PoolError(Exception):
    pass


class SessionPool:
    def __init__(self, keepalive_default: int = 25, idle_ttl: int = 0,
                 on_event=None, tracer=None):
        self.conns: dict[str, Conn] = {}
        self.lock = threading.RLock()
        self.keepalive_default = max(0, int(keepalive_default))
        self.idle_ttl = max(0, int(idle_ttl))
        self.on_event = on_event            # on_event(kind, text)
        # tracer(display, rc, out, err, ms) —— 和 session.tracer 同签名。
        # 由主程序注入：智能体通过 API 跑的每条命令都要进「日志」、
        # 也要同步到终端，所以池建的会话必须挂上它（见 open()）。
        self.tracer = tracer
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ 事件
    def _emit(self, kind: str, text: str) -> None:
        if self.on_event:
            try:
                self.on_event(kind, text)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ 开
    def open(self, spec: dict) -> Conn:
        proto = str(spec.get("protocol") or spec.get("proto") or "ssh").lower()
        if proto not in ("ssh", "local"):
            raise PoolError("API 目前支持 ssh / local 两种协议")
        cs = ConnSpec(
            proto=proto,
            host=str(spec.get("host") or "").strip(),
            port=int(spec.get("port") or (22 if proto == "ssh" else 0) or 0),
            user=str(spec.get("user") or "").strip(),
            auth="password" if spec.get("password") else "key",
            key_path=str(spec.get("key_path") or "").strip(),
            password=str(spec.get("password") or ""),
            key_passphrase=str(spec.get("key_passphrase") or ""),
        )
        if proto == "ssh" and not cs.host:
            raise PoolError("缺少 host")

        try:
            sess = open_session(cs)
        except SessionError as e:
            raise PoolError(str(e)) from e

        sudo_pw = str(spec.get("sudo_password") or "")
        if sudo_pw and hasattr(sess, "sudo_pw"):
            sess.sudo_pw = sudo_pw

        # ★ 挂 tracer：不挂的话，智能体在设备上跑的命令一条都不会进
        # 「日志」，也不会同步到终端 —— 用户反馈「日志里看不到智能体的操作」
        # 就是这个原因（池走的是 run()/run_admin()，不像主程序走 run_traced()）。
        # 包一层加前缀：连 detect 阶段的探测命令也算「智能体干的」，
        # 不然同一台设备谁在乱扫，事后看不出来。
        if self.tracer is not None:
            try:
                sess.tracer = _tagged(self.tracer)
            except Exception:  # noqa: BLE001
                pass

        try:
            dev = detect(sess)
        except Exception:  # noqa: BLE001
            dev = DeviceInfo()
        sess.is_root = bool(getattr(dev, "is_root", False))
        dia = dialect_for(dev)

        conn = Conn(
            id=uuid.uuid4().hex[:12],
            spec=dict(spec),
            session=sess,
            device=dev,
            dialect=dia,
            name=str(spec.get("name") or ""),
            keepalive=int(spec.get("keepalive") or self.keepalive_default or 0),
            keepalive_on=bool(spec.get("keepalive_enabled", True)),
        )
        try:
            conn.sampler = Sampler(sess, dia)
        except Exception:  # noqa: BLE001
            conn.sampler = None

        with self.lock:
            self.conns[conn.id] = conn
        self._emit("ok", f"API 会话已建立 {conn.id} → "
                         f"{cs.user + '@' if cs.user else ''}{cs.host or '本机'}")
        return conn

    # ------------------------------------------------------------ 取
    def get(self, cid: str) -> Conn:
        with self.lock:
            c = self.conns.get(cid)
        if c is None:
            raise PoolError(f"没有这个会话：{cid}")
        return c

    def list(self) -> list[dict]:
        with self.lock:
            items = list(self.conns.values())
        return [c.summary() for c in sorted(items, key=lambda x: x.created)]

    # ------------------------------------------------------------ 关
    def close(self, cid: str, reason: str = "") -> bool:
        with self.lock:
            c = self.conns.pop(cid, None)
        if not c:
            return False
        try:
            c.session.close()
        except Exception:  # noqa: BLE001
            pass
        self._emit("dim", f"API 会话已关闭 {cid}" + (f"（{reason}）" if reason else ""))
        return True

    def close_all(self) -> None:
        for cid in list(self.conns):
            self.close(cid, "程序退出")

    # ------------------------------------------------------------ 执行
    def exec(self, cid: str, cmd: str, timeout: int = 60,
             admin: bool = False) -> dict:
        c = self.get(cid)
        cmd = (cmd or "").strip()
        if not cmd:
            raise PoolError("cmd 不能为空")
        timeout = max(1, min(int(timeout or 60), 3600))
        t0 = time.time()
        with c.lock:
            if not c.session.alive:
                self._reconnect(c)
            try:
                if admin:
                    rc, out, err = c.session.run_admin(cmd, timeout=timeout)
                else:
                    rc, out, err = c.session.run(cmd, timeout=timeout)
            except SessionError:
                # 掉线 → 重连再试一次
                self._reconnect(c)
                if admin:
                    rc, out, err = c.session.run_admin(cmd, timeout=timeout)
                else:
                    rc, out, err = c.session.run(cmd, timeout=timeout)
            c.execs += 1
            if rc != 0:
                c.errors += 1
            c.touch()
        ms = int((time.time() - t0) * 1000)
        truncated = False
        if len(out) > MAX_OUT_CHARS:
            out = out[:MAX_OUT_CHARS] + f"\n…（已截断，原长 {len(out)} 字符）"
            truncated = True
        # 记一次「谁执行的」：前缀 [API] 是刻意的 —— 人敲的命令和智能体跑的
        # 命令在日志里长得一模一样，出事时根本分不清是谁干的。
        self._trace(c, cmd, rc, out, err, ms)
        return {"rc": rc, "out": scrub(out), "err": scrub(err), "ms": ms,
                "truncated": truncated, "session": c.id}

    def _trace(self, c, cmd: str, rc: int, out: str, err: str, ms: int) -> None:
        """把这条 API 命令交给上层 tracer（app._trace_cmd）。

        一手托三家：写操作审计（「日志」底部面板读的就是它）、推给终端
        （「同步命令」那个勾），以及内存流水。tracer 抛错绝不能把命令结果
        弄丢 —— 智能体要的是 rc/out，不是日志。

        `[API]` 前缀由 `_tagged()` 统一加，这里传原始命令。
        exec() 必须显式走这一步：它用的是 run()/run_admin()，不像主程序
        那样有 run_traced() 自动带 tracer。
        """
        trace = getattr(c.session, "_trace", None)
        if not callable(trace):
            return
        try:
            trace(cmd, rc, out, err, ms)
        except Exception:  # noqa: BLE001
            pass

    def metrics(self, cid: str) -> dict:
        c = self.get(cid)
        if not c.sampler:
            raise PoolError("该会话没有采样器")
        with c.lock:
            s = c.sampler.sample(timeout=25)
            c.touch()
        return {
            "ok": s.ok, "error": s.error,
            "cpu_pct": round(s.cpu_pct, 1),
            "mem_used": s.mem_used, "mem_total": s.mem_total,
            "mem_pct": round(s.mem_pct, 1),
            "net_rx_bps": round(s.net_rx_bps, 1),
            "net_tx_bps": round(s.net_tx_bps, 1),
            "uptime": round(s.uptime, 0), "ncpu": s.ncpu,
        }

    # ------------------------------------------------------------ 文件
    def upload(self, cid: str, path: str, data: bytes) -> dict:
        c = self.get(cid)
        if not path:
            raise PoolError("path 不能为空")
        sess = c.session
        with c.lock:
            if hasattr(sess, "sftp"):
                try:
                    sftp = sess.sftp()
                    with sftp.file(path, "wb") as f:
                        f.write(data)
                    c.touch()
                    return {"ok": True, "path": path, "bytes": len(data)}
                except Exception as e:  # noqa: BLE001
                    raise PoolError(f"SFTP 上传失败：{e}") from e
        raise PoolError("该协议不支持文件上传")

    def download(self, cid: str, path: str, limit: int = 4 * 1024 * 1024) -> dict:
        c = self.get(cid)
        if not path:
            raise PoolError("path 不能为空")
        sess = c.session
        with c.lock:
            if not hasattr(sess, "sftp"):
                raise PoolError("该协议不支持文件下载")
            try:
                sftp = sess.sftp()
                st = sftp.stat(path)
                if st.st_size and st.st_size > limit:
                    raise PoolError(f"文件太大（{st.st_size} 字节），上限 {limit}")
                with sftp.file(path, "rb") as f:
                    data = f.read()
                c.touch()
                return {"ok": True, "path": path, "bytes": len(data), "data": data}
            except PoolError:
                raise
            except Exception as e:  # noqa: BLE001
                raise PoolError(f"SFTP 下载失败：{e}") from e

    # ------------------------------------------------------------ 保活
    def _reconnect(self, c: Conn) -> None:
        if c.spec.get("protocol", "ssh") == "local":
            raise PoolError("本机会话不可重连")
        if c.reconnects >= 5:
            raise PoolError(f"重连次数过多（{c.reconnects}），请重新建立会话")
        c.reconnects += 1
        spec = c.spec
        try:
            sess = open_session(ConnSpec(
                proto=spec.get("protocol", "ssh"),
                host=spec.get("host", ""),
                port=int(spec.get("port") or 22),
                user=spec.get("user", ""),
                auth="password" if spec.get("password") else "key",
                key_path=spec.get("key_path", ""),
                password=spec.get("password", ""),
                key_passphrase=spec.get("key_passphrase", ""),
            ))
            if spec.get("sudo_password") and hasattr(sess, "sudo_pw"):
                sess.sudo_pw = spec["sudo_password"]
            sess.is_root = getattr(c.device, "is_root", False)
            c.session = sess
            try:
                c.sampler = Sampler(sess, c.dialect)
            except Exception:  # noqa: BLE001
                pass
            c.last_error = ""
            self._emit("warn", f"API 会话 {c.id} 已自动重连（第 {c.reconnects} 次）")
        except Exception as e:  # noqa: BLE001
            c.last_error = str(e)
            self._emit("bad", f"API 会话 {c.id} 重连失败：{e}")
            raise PoolError(f"连接已断开且重连失败：{e}") from e

    def tick(self) -> None:
        now = time.time()
        with self.lock:
            items = list(self.conns.values())
        for c in items:
            try:
                if self.idle_ttl and now - c.last_used > self.idle_ttl:
                    self.close(c.id, "闲置超时")
                    continue
                if not c.keepalive_on or not c.keepalive:
                    continue
                if now - c.last_used < c.keepalive:
                    continue
                self._keep(c)
            except Exception:  # noqa: BLE001
                c.last_error = traceback.format_exc(limit=1)

    def _keep(self, c: Conn) -> None:
        sess = c.session
        if not sess or not sess.alive:
            try:
                self._reconnect(c)
            except PoolError:
                return
            return
        transport = getattr(getattr(sess, "client", None), "get_transport", None)
        if transport:
            tr = transport()
            if tr and tr.is_active():
                # send_ignore 只发一个 SSH 层心跳，不占 session channel、不产生日志
                tr.send_ignore()
                c.touch()
                return
        # 本地会话或没有 transport：轻量探一句
        try:
            with c.lock:
                sess.run("echo .", timeout=8)
            c.touch()
        except Exception:  # noqa: BLE001
            try:
                self._reconnect(c)
            except PoolError:
                pass

    # ------------------------------------------------------------ 线程
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(5.0):
                try:
                    self.tick()
                except Exception:  # noqa: BLE001
                    pass

        self._thread = threading.Thread(target=loop, daemon=True, name="pool-keepalive")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
