# -*- coding: utf-8 -*-
"""代理链连通性验证：不是「端口能连上」，而是「连接真的能穿过整条链」。

为什么需要它（R-004）：`core/proxies.py` 只管档案与成环校验 —— 环在这里就被拦住了，
但**配好的一条链到底通不通**从来没验过。用户配了「东京 → 香港」两跳，
到连主机失败时只能猜：是跳板没起？密码错了？还是目标自己不通？

这里做三件事：
  1. `tcp_probe()`      —— 单节点 TCP 可达 + RTT（不需要凭据）；
  2. `build_chain()`    —— 逐跳握手（SOCKS5 / HTTP CONNECT / SSH 转发），
                           最终拿到一条**真的连到目标**的 socket；
  3. `check_chain()`    —— 把上面两步串起来，给出「第几跳坏了、为什么坏」的分级结论。

设计上的取舍：
  · **不新建 sshtunnel 依赖**：SSH 跳板直接用 paramiko 的 `direct-tcpip` 通道，
    项目本来就为 SSH 会话装了 paramiko，没必要再引一个包。
  · 端到端只做「能建立连接 + 目标有应答」：不做 HTTP 语义断言（不同目标千奇百怪），
    所以对任意 `host:port` 都能用，80/443/22 都行。
  · 每跳都单独计时：慢在哪一跳，一眼能看出来（链式故障里这几乎是最常见的问题）。
"""
from __future__ import annotations

import base64
import socket
import time
from typing import Any

#: 默认验证目标。选 80 而不是 443：不需要 TLS 握手，快一倍，
#: 而且「能过代理连出去」这件事和加密无关。
DEFAULT_TARGET = ("www.baidu.com", 80)

MAX_HOPS = 6
DEFAULT_TIMEOUT = 8.0


# ------------------------------------------------------------------ 小工具
def _plain_password(p: dict) -> str:
    """把档案里的密文口令解成明文（解不开就当没有）。"""
    enc = str((p or {}).get("pass_enc") or "")
    if not enc:
        return ""
    try:
        import crypto_store
        return crypto_store.unprotect(enc) or ""
    except Exception:  # noqa: BLE001  口令解不开不该让验证流程炸
        return ""


def _addr(p: dict) -> str:
    host = str((p or {}).get("host") or "").strip()
    port = str((p or {}).get("port") or "").strip()
    return f"{host}:{port}" if port else host


def _name(p: dict) -> str:
    return str((p or {}).get("name") or (p or {}).get("host") or "?")


def _wait(sock, timeout: float) -> float:
    """给 socket 设超时（SSH 通道没有 settimeout，忽略即可）。"""
    try:
        sock.settimeout(timeout)
    except (AttributeError, OSError):
        pass
    return timeout


class ChannelSock:
    """把 paramiko 的 Channel 包成「够 SOCKS5/HTTP 用」的 socket。

    只需要 `sendall` / `recv` / `close` 三个动作 —— 我们的握手代码加起来就用这些。
    不实现 `settimeout`：Channel 的阻塞由 paramiko 管，硬设会抛 AttributeError。
    """

    def __init__(self, chan):
        self._chan = chan

    def sendall(self, data: bytes) -> None:
        self._chan.sendall(data)

    def send(self, data: bytes) -> int:
        return self._chan.send(data)

    def recv(self, n: int) -> bytes:
        return self._chan.recv(n)

    def close(self) -> None:
        try:
            self._chan.close()
        except Exception:  # noqa: BLE001
            pass

    def settimeout(self, _t: float) -> None:      # 兼容调用方
        return None


def _recv_exact(sock, n: int) -> bytes:
    """读满 n 字节，读不够就抛（握手阶段少一个字节都不能继续）。"""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("连接被对端关闭（握手没收完）")
        buf += chunk
    return buf


# ------------------------------------------------------------------ 单节点
def tcp_probe(host: str, port: int | str, timeout: float = 6.0) -> dict:
    """到 host:port 的一次 TCP 连接。返回 {ok, ms, err}。"""
    host = str(host or "").strip()
    try:
        port = int(str(port or "").strip() or 0)
    except ValueError:
        return {"ok": False, "ms": 0, "err": f"端口不是数字：{port}"}
    if not host:
        return {"ok": False, "ms": 0, "err": "没填主机"}
    if not (0 < port < 65536):
        return {"ok": False, "ms": 0, "err": f"端口超出范围：{port}"}
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ms = int((time.time() - t0) * 1000)
        return {"ok": True, "ms": ms, "err": ""}
    except socket.timeout:
        return {"ok": False, "ms": int((time.time() - t0) * 1000),
                "err": f"连不上（{timeout:.0f} 秒超时，可能被防火墙丢包）"}
    except OSError as e:
        return {"ok": False, "ms": int((time.time() - t0) * 1000),
                "err": f"{e.strerror or e}"}


# ------------------------------------------------------------------ 逐跳握手
def socks5_connect(prev, host: str, port: int, user: str = "",
                   password: str = "", timeout: float = DEFAULT_TIMEOUT):
    """在 prev 之上用 SOCKS5 连到 host:port，返回同一个 socket。"""
    _wait(prev, timeout)
    methods = b"\x00\x02" if user else b"\x00"
    prev.sendall(b"\x05" + bytes([len(methods)]) + methods)
    ver, method = _recv_exact(prev, 2)
    if ver != 5:
        raise OSError(f"对端不是 SOCKS5（版本号 {ver}）")
    if method == 2:
        if not user:
            raise OSError("代理要求用户名密码，但这条档案没填")
        u = user.encode("utf-8")[:255]
        pw = password.encode("utf-8")[:255]
        prev.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(pw)]) + pw)
        _v, status = _recv_exact(prev, 2)
        if status != 0:
            raise OSError("SOCKS5 认证失败（用户名或密码不对）")
    elif method == 0xFF:
        raise OSError("代理不接受我们提供的方式（可能要认证）")
    elif method != 0:
        raise OSError(f"代理选了不支持的方式（0x{method:02x}）")
    raw = host.encode("idna") if host.isascii() else host.encode("utf-8")
    prev.sendall(b"\x05\x01\x00\x03" + bytes([len(raw)]) + raw
                 + int(port).to_bytes(2, "big"))
    ver, rep, _rsv, atyp = _recv_exact(prev, 4)
    if rep != 0:
        why = {1: "代理内部错误", 2: "规则不允许", 3: "网络不可达", 4: "主机不可达",
               5: "连接被拒绝", 6: "TTL 过期", 7: "不支持的命令",
               8: "不支持的地址类型"}.get(rep, f"错误码 {rep}")
        raise OSError(f"SOCKS5 连不上目标：{why}")
    if atyp == 1:
        _recv_exact(prev, 4 + 2)
    elif atyp == 3:
        ln = _recv_exact(prev, 1)[0]
        _recv_exact(prev, ln + 2)
    elif atyp == 4:
        _recv_exact(prev, 16 + 2)
    return prev


def http_connect(prev, host: str, port: int, user: str = "",
                 password: str = "", timeout: float = DEFAULT_TIMEOUT):
    """在 prev 之上用 HTTP CONNECT 连到 host:port。"""
    _wait(prev, timeout)
    lines = [f"CONNECT {host}:{int(port)} HTTP/1.1",
             f"Host: {host}:{int(port)}"]
    if user:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode()
        lines.append(f"Proxy-Authorization: Basic {token}")
    prev.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
    # 只读到第一个空行为止：有的代理后面跟着一堆头
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = prev.recv(1)
        if not chunk:
            raise OSError("代理没回 CONNECT 响应就断开了")
        head += chunk
        if len(head) > 8192:
            raise OSError("CONNECT 响应过长（不像 HTTP 代理）")
    first = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    if " 200" not in first and " 2" not in first[8:12]:
        raise OSError(f"代理拒绝 CONNECT：{first[:80]}")
    return prev


def ssh_forward(prev, host: str, port: int, user: str = "",
                password: str = "", timeout: float = DEFAULT_TIMEOUT):
    """在 prev 之上开一条 SSH `direct-tcpip` 通道到 host:port。"""
    try:
        import paramiko
    except ImportError as e:      # 打包版一定有，源码版可能没装
        raise OSError("没装 paramiko，SSH 跳板没法验") from e
    tr = paramiko.Transport(prev)
    tr.banner_timeout = timeout
    tr.start_client(timeout=timeout)
    try:
        if password:
            tr.auth_password(user or "root", password)
        else:
            tr.auth_none(user or "root")
    except Exception as e:  # noqa: BLE001
        try:
            tr.close()
        except Exception:  # noqa: BLE001
            pass
        raise OSError(f"SSH 认证失败：{type(e).__name__}: {e}") from e
    chan = tr.open_channel("direct-tcpip", (host, int(port)), ("127.0.0.1", 0),
                           timeout=timeout)
    if chan is None:
        raise OSError("SSH 拒绝打开转发通道")
    return ChannelSock(chan)


_OPENER = {
    "socks5": socks5_connect,
    "http": http_connect,
    "ssh": ssh_forward,
}


# ------------------------------------------------------------------ 整条链
def build_chain(chain: list[dict], dest: tuple[str, int],
                timeout: float = DEFAULT_TIMEOUT) -> tuple[Any, list[dict], str]:
    """按顺序穿过每一跳，最后连到 dest。返回 `(socket, 每跳结果, 错误)`。

    **不抛异常**：失败时把已走过的跳带回来，调用方才能说清「断在第几跳」。
    第一版是抛异常的，结果一失败调用方拿到空列表，所有故障都只会显示
    「连接失败：timed out」—— 用户最需要的那个信息（断在哪）刚好丢了。

    `stage` 字段区分两种失败：`via` = 用这一跳连下一跳时失败（代理本身有问题），
    `target` = 最后一跳连目标失败（代理是通的，目标不通）。这两种在排障时
    完全是两条路，不能混成一句。
    """
    hops: list[dict] = []
    sock = None
    if not chain:
        t0 = time.time()
        try:
            sock = socket.create_connection(dest, timeout=timeout)
        except OSError as e:
            hops.append({"name": f"{dest[0]}:{dest[1]}", "addr": f"{dest[0]}:{dest[1]}",
                         "kind": "direct", "ok": False, "stage": "target",
                         "ms": int((time.time() - t0) * 1000), "err": str(e)})
            return None, hops, str(e)
        hops.append({"name": dest[0], "addr": f"{dest[0]}:{dest[1]}",
                     "kind": "direct", "ok": True, "stage": "",
                     "ms": int((time.time() - t0) * 1000), "err": ""})
        return sock, hops, ""

    first = chain[0]
    t0 = time.time()
    try:
        sock = socket.create_connection((str(first.get("host") or ""),
                                         int(str(first.get("port") or 0))),
                                        timeout=timeout)
    except OSError as e:
        hops.append({"name": _name(first), "addr": _addr(first),
                     "kind": str(first.get("kind") or ""), "ok": False,
                     "stage": "via", "ms": int((time.time() - t0) * 1000),
                     "err": f"第一跳就连不上：{e}"})
        return None, hops, f"第一跳就连不上：{e}"
    hops.append({"name": _name(first), "addr": _addr(first),
                 "kind": str(first.get("kind") or ""), "ok": True, "stage": "",
                 "ms": int((time.time() - t0) * 1000), "err": ""})

    # 用第 i 跳去连第 i+1 跳；最后一跳去连目标
    targets = [(str(p.get("host") or ""), int(str(p.get("port") or 0)))
               for p in chain[1:]] + [dest]
    for i, (host, port) in enumerate(targets):
        via = chain[i]
        is_target = i + 1 == len(targets)
        opener = _OPENER.get(str(via.get("kind") or ""))
        t0 = time.time()
        if opener is None:
            err = (f"这一跳的类型不认识：{via.get('kind')}"
                   "（只支持 ssh / socks5 / http）")
            hops.append({"name": _name(via), "addr": f"{host}:{port}",
                         "kind": str(via.get("kind") or ""), "ok": False,
                         "stage": "target" if is_target else "via",
                         "ms": 0, "err": err})
            return sock, hops, err
        try:
            sock = opener(sock, host, port, str(via.get("user") or ""),
                          _plain_password(via), timeout)
        except OSError as e:
            stage = "target" if is_target else "via"
            label = (f"{_name(via)} → {host}:{port}" if is_target else _name(via))
            hops.append({"name": label, "addr": f"{host}:{port}",
                         "kind": str(via.get("kind") or ""), "ok": False,
                         "stage": stage,
                         "ms": int((time.time() - t0) * 1000), "err": str(e)})
            return sock, hops, str(e)
        label = _name(chain[i + 1]) if i + 1 < len(chain) else f"{host}:{port}"
        hops.append({"name": via.get("name") and f"{_name(via)} → {label}" or label,
                     "addr": f"{host}:{port}", "kind": str(via.get("kind") or ""),
                     "ok": True, "stage": "",
                     "ms": int((time.time() - t0) * 1000), "err": ""})
    return sock, hops, ""


def _e2e(sock, target: tuple[str, int], timeout: float = 6.0,
         expect_http: bool | None = None) -> dict:
    """在已建立的连接上说一句话，确认目标真的在应答。

    `expect_http=None` 时按端口猜（80/8080/8000 才发 HEAD）；测试或特殊目标
    可以显式指定，免得"连上就算通"把「代理通、目标其实是黑洞」也判成成功。
    """
    t0 = time.time()
    do_http = (expect_http if expect_http is not None
               else int(target[1]) in (80, 8080, 8000))
    try:
        if do_http:
            req = (f"HEAD / HTTP/1.0\r\nHost: {target[0]}\r\n"
                   "User-Agent: TerminalToolbox-proxycheck\r\n\r\n")
            _wait(sock, timeout)
            sock.sendall(req.encode())
            data = sock.recv(64)
            ms = int((time.time() - t0) * 1000)
            if not data:
                return {"ok": False, "ms": ms, "detail": "目标接受了连接但没回数据"}
            head = data.decode("latin-1", "replace").split("\r\n", 1)[0]
            if head.upper().startswith("HTTP/"):
                return {"ok": True, "ms": ms, "detail": head[:80]}
            return {"ok": True, "ms": ms,
                    "detail": f"目标有应答（不像 HTTP）：{head[:60]}"}
        # 非 HTTP 目标：连上就算通（端口探测语义）
        return {"ok": True, "ms": int((time.time() - t0) * 1000),
                "detail": "连接建立成功（非 HTTP 端口，不做协议断言）"}
    except socket.timeout:
        return {"ok": False, "ms": int((time.time() - t0) * 1000),
                "detail": f"目标 {target[0]}:{target[1]} 没有应答（{timeout:.0f} 秒超时）"}
    except OSError as e:
        return {"ok": False, "ms": int((time.time() - t0) * 1000),
                "detail": f"{e.strerror or e}"}


def format_report(res: dict) -> list[str]:
    """把 `check_chain()` 的结果排成给人看的多行文本。

    抽成纯函数是为了能测：界面只负责把它塞进对话框，排版与措辞在这儿定，
    `_proxychaintest.py` 才能断言「断在第几跳」这类关键信息没被写丢。
    """
    lines = [f"链路：{res.get('chain') or '直连'}  →  {res.get('target') or '目标'}",
             ""]
    for h in res.get("hops") or []:
        ok = bool(h.get("ok"))
        lines.append(f"{'✔' if ok else '✘'} {h.get('name')}  {h.get('ms') or 0} ms")
        if not ok and h.get("err"):
            lines.append(f"    {h['err']}")
    e2e = res.get("e2e") or {}
    if e2e:
        lines.append("")
        lines.append(f"端到端：{'通' if e2e.get('ok') else '不通'}  "
                     f"{e2e.get('ms') or 0} ms")
        if e2e.get("detail"):
            lines.append(f"    {e2e['detail']}")
    lines.append("")
    lines.append("结论：" + (res.get("error") or ("通" if res.get("ok") else "不通")))
    return lines


def check_chain(proxies: list[dict], pid: str,
                target: tuple[str, int] = DEFAULT_TARGET,
                timeout: float = DEFAULT_TIMEOUT,
                expect_http: bool | None = None) -> dict:
    """验证一条代理链（pid 为空 = 直连）。返回结构化结果，绝不抛异常。"""
    from core import proxies as P

    chain = P.chain_of(proxies, pid) if pid else []
    if len(chain) > MAX_HOPS:
        chain = chain[:MAX_HOPS]
    out: dict = {"ok": False, "chain": P.chain_text(proxies, pid) if pid else "直连",
                 "target": f"{target[0]}:{target[1]}", "hops": [], "e2e": {},
                 "error": "", "ms": 0}
    if pid and P.detect_cycle(proxies, pid):
        out["error"] = "这条链路成环了，没法验（先在代理页把环改掉）"
        return out

    t0 = time.time()
    sock = None
    try:
        sock, hops, err = build_chain(chain, target, timeout)
        out["hops"] = hops
        if err:
            bad = next((h for h in reversed(hops) if not h.get("ok")), {})
            # 两种失败分开说：代理本身不行 / 代理通但目标不通。
            # 合成一句「连接失败」的话，用户得从头猜。
            where = "第 %d 跳" % max(1, len(hops) - (1 if bad.get("stage") == "target" else 0))
            out["error"] = (f"{where}失败（{bad.get('addr') or '?'}）：{err}"
                            if bad.get("stage") != "target"
                            else f"代理通了，但目标 {bad.get('addr') or '?'} 连不上：{err}")
            return out
        e2e = _e2e(sock, target, timeout=min(6.0, timeout),
                   expect_http=expect_http)
        out["e2e"] = e2e
        out["ok"] = bool(e2e.get("ok"))
        if not e2e.get("ok"):
            out["error"] = f"链通了，但目标没应答：{e2e.get('detail')}"
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass
        out["ms"] = int((time.time() - t0) * 1000)
    return out
