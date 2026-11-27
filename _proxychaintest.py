# -*- coding: utf-8 -*-
"""代理链连通性验证的自测（R-004）。

不连外网、不依赖真代理：本地起两台迷你代理（SOCKS5 / HTTP CONNECT）和一个
迷你目标服务器，用**产品代码** `core.proxycheck` 真的穿过去。

    python _proxychaintest.py

覆盖：
  · 单跳 / 两跳串联（第二跳是**在第一跳里**握手的，这是链式最容易写错的地方）
  · SOCKS5 用户名密码、密码错要给出「认证失败」而不是「网络不通」
  · HTTP CONNECT 单跳
  · 目标端口拒绝时，要说清是「代理通、目标不通」
  · 成环要拦住
  · 直连（pid 为空）
"""
from __future__ import annotations

import select
import socket
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import proxycheck as PC              # noqa: E402

OK = 0
BAD = 0


def chk(cond, msg, extra=None):
    global OK, BAD
    if cond:
        OK += 1
        print(f"  [ok] {msg}" + (f"  —— {extra}" if extra not in (None, "") else ""))
    else:
        BAD += 1
        print(f"  [NG] {msg}" + (f"  —— {extra}" if extra not in (None, "") else ""))


def sec(t):
    print(f"\n-- {t}")


def relay(a, b) -> None:
    """双向转发，任一侧关闭就收工。"""
    socks = [a, b]
    try:
        while True:
            r, _w, _x = select.select(socks, [], [], 5)
            if not r:
                break
            for s in r:
                other = b if s is a else a
                data = s.recv(65536)
                if not data:
                    return
                other.sendall(data)
    except OSError:
        pass


class MiniProxy(threading.Thread):
    """够用的迷你代理：socks5 或 http CONNECT，可选用户名密码。"""

    def __init__(self, kind="socks5", user="", password=""):
        super().__init__(daemon=True)
        self.kind = kind
        self.user = user
        self.password = password
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.running = True
        self.hits = 0

    def stop(self) -> None:
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass

    def run(self) -> None:
        while self.running:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(c,), daemon=True).start()

    # -- 一次连接
    def _handle(self, c) -> None:
        up = None
        try:
            self.hits += 1
            target = (self._socks(c) if self.kind == "socks5"
                      else self._http(c))
            if target is None:
                return
            up = socket.create_connection(target, timeout=5)
            relay(c, up)
        except OSError:
            pass
        finally:
            for s in (c, up):
                try:
                    if s is not None:
                        s.close()
                except OSError:
                    pass

    @staticmethod
    def _read_exact(s, n):
        buf = b""
        while len(buf) < n:
            ch = s.recv(n - len(buf))
            if not ch:
                raise OSError("closed")
            buf += ch
        return buf

    def _socks(self, c):
        ver, n = self._read_exact(c, 2)
        if ver != 5:
            return None
        methods = self._read_exact(c, n)
        need_auth = bool(self.user)
        if need_auth and 2 not in methods:
            c.sendall(b"\x05\xff")
            return None
        if need_auth:
            c.sendall(b"\x05\x02")
            _v, ulen = self._read_exact(c, 2)
            user = self._read_exact(c, ulen).decode()
            plen = self._read_exact(c, 1)[0]
            pw = self._read_exact(c, plen).decode()
            if user != self.user or pw != self.password:
                c.sendall(b"\x01\x01")
                return None
            c.sendall(b"\x01\x00")
        else:
            c.sendall(b"\x05\x00")
        ver, cmd, _rsv, atyp = self._read_exact(c, 4)
        if atyp == 1:
            host = socket.inet_ntoa(self._read_exact(c, 4))
        elif atyp == 3:
            ln = self._read_exact(c, 1)[0]
            host = self._read_exact(c, ln).decode()
        else:
            host = socket.inet_ntop(socket.AF_INET6, self._read_exact(c, 16))
        port = int.from_bytes(self._read_exact(c, 2), "big")
        try:
            socket.create_connection((host, port), timeout=3).close()
        except OSError:
            c.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
            return None
        c.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
        return host, port

    def _http(self, c):
        head = b""
        while b"\r\n\r\n" not in head:
            ch = c.recv(1)
            if not ch:
                return None
            head += ch
        first = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = first.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            c.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            return None
        if self.user:
            import base64
            want = "Basic " + base64.b64encode(
                f"{self.user}:{self.password}".encode()).decode()
            if want not in head.decode("latin-1"):
                c.sendall(b"HTTP/1.1 407 Proxy Auth Required\r\n\r\n")
                return None
        host, _, port = parts[1].rpartition(":")
        try:
            socket.create_connection((host, int(port)), timeout=3).close()
        except OSError:
            c.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return None
        c.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        return host, int(port)


class TinyTarget(threading.Thread):
    """一个只会回 `HTTP/1.0 200 OK` 的目标服务器。"""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.running = True

    def stop(self) -> None:
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass

    def run(self) -> None:
        while self.running:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            try:
                c.settimeout(3)
                try:
                    c.recv(4096)
                except OSError:
                    pass
                c.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 0\r\n"
                          b"X-Test: tiny\r\n\r\n")
            except OSError:
                pass
            finally:
                try:
                    c.close()
                except OSError:
                    pass


def node(pid: str, kind: str, port: int, name: str = "", parent: str = "",
         user: str = "", pw_enc: str = "") -> dict:
    return {"id": pid, "name": name or pid, "kind": kind, "host": "127.0.0.1",
            "port": str(port), "parent_id": parent, "user": user,
            "pass_enc": pw_enc}


# ------------------------------------------------------------------ 准备
target = TinyTarget()
target.start()
p_s1 = MiniProxy("socks5")
p_s1.start()
p_s2 = MiniProxy("socks5")
p_s2.start()
p_h1 = MiniProxy("http")
p_h1.start()
p_auth = MiniProxy("socks5", user="u1", password="pw1")
p_auth.start()
time.sleep(0.25)

T = ("127.0.0.1", target.port)

sec("1. tcp_probe：通与不通都要说清")
r = PC.tcp_probe("127.0.0.1", target.port, timeout=2)
chk(r["ok"] and r["ms"] >= 0, "本地目标可达", f"{r['ms']} ms")
dead = socket.socket()
dead.bind(("127.0.0.1", 0))
dead_port = dead.getsockname()[1]
dead.close()
r2 = PC.tcp_probe("127.0.0.1", dead_port, timeout=1)
chk(not r2["ok"] and r2["err"], "关掉的端口连不上，且给了原因", r2["err"])
chk(not PC.tcp_probe("127.0.0.1", 0)["ok"], "端口 0 被当成参数错误")

sec("2. 单跳 SOCKS5")
proxies = [node("p1", "socks5", p_s1.port, "一号")]
res = PC.check_chain(proxies, "p1", T, timeout=3, expect_http=True)
chk(res["ok"], "穿过一台 SOCKS5 连到目标", res.get("error") or res["e2e"].get("detail"))
chk(len(res["hops"]) == 2, "结果里有两跳：直连代理 + 代理到目标",
    [h["name"] for h in res["hops"]])
chk(res["e2e"]["ok"] and "200" in str(res["e2e"].get("detail")), "端到端拿到 HTTP 200",
    res["e2e"].get("detail"))

sec("3. 两跳串联（第二跳在第一跳里握手）")
proxies2 = [node("p1", "socks5", p_s1.port, "一号"),
            node("p2", "socks5", p_s2.port, "二号", parent="p1")]
res2 = PC.check_chain(proxies2, "p2", T, timeout=3, expect_http=True)
chk(res2["ok"], "两跳都能过", res2.get("error") or res2["e2e"].get("detail"))
chk(len(res2["hops"]) == 3, "结果里三跳：直连一号 / 一号到二号 / 二号到目标",
    len(res2["hops"]))
chk(p_s1.hits >= 1 and p_s2.hits >= 1, "两台代理都真的被用到了",
    f"p1={p_s1.hits} p2={p_s2.hits}")
chk("→" in res2["chain"], "链路文字描述带箭头", res2["chain"])

sec("4. HTTP CONNECT 单跳")
proxies3 = [node("h1", "http", p_h1.port, "HTTP 代理")]
res3 = PC.check_chain(proxies3, "h1", T, timeout=3, expect_http=True)
chk(res3["ok"], "穿过 HTTP CONNECT 代理", res3.get("error") or res3["e2e"].get("detail"))

sec("5. SOCKS5 认证：密码错要说「认证失败」")
proxies4 = [node("a1", "socks5", p_auth.port, "要密码的", user="u1",
                 pw_enc="")]
# 故意不填密码（pass_enc 空 → 明文口令为空）
res4 = PC.check_chain(proxies4, "a1", T, timeout=3)
chk(not res4["ok"], "密码不对时验证失败")
chk("认证" in str(res4.get("error") or "") or "认证" in str(res4["hops"][-1]["err"]),
    "错误信息点明是认证问题，不是网络不通", res4.get("error"))

sec("6. 成环要拦住")
cyc = [node("c1", "socks5", p_s1.port, "甲", parent="c2"),
       node("c2", "socks5", p_s2.port, "乙", parent="c1")]
res5 = PC.check_chain(cyc, "c1", T, timeout=2)
chk(not res5["ok"] and "成环" in str(res5.get("error")), "成环直接拒绝验证",
    res5.get("error"))

sec("7. 目标拒绝：代理通、目标不通")
res6 = PC.check_chain(proxies, "p1", ("127.0.0.1", dead_port), timeout=2,
                      expect_http=True)
chk(not res6["ok"], "目标端口关着 → 整体不算通", res6.get("error"))
chk("目标" in str(res6.get("error") or "") or "拒绝" in str(res6.get("error") or ""),
    "错误信息能区分「目标不通」", res6.get("error"))

sec("8. 直连（pid 为空）")
res7 = PC.check_chain([], "", T, timeout=2, expect_http=True)
chk(res7["ok"] and len(res7["hops"]) == 1, "不走代理也能验（一条结果）",
    res7["chain"])

sec("9. 第一跳端口不通")
bad = [node("x1", "socks5", dead_port, "不存在的代理")]
res8 = PC.check_chain(bad, "x1", T, timeout=1)
chk(not res8["ok"] and res8["error"], "第一跳连不上要报错", res8["error"][:60])

sec("10. 报告排版：断在哪一跳必须写出来")
_rep = "\n".join(PC.format_report(res2))
chk("✔" in _rep and "端到端：通" in _rep, "成功的链路报告有逐跳 ✔ 与端到端结论")
_bad = "\n".join(PC.format_report(res6))
chk("✘" in _bad and "目标" in _bad, "目标不通时报告里有 ✘ 和「目标」字样",
    _bad.splitlines()[-1])
chk("结论：" in _bad, "报告末尾一定有结论行")

# 收尾
for p in (p_s1, p_s2, p_h1, p_auth, target):
    p.stop()

print(f"\n合计 {OK + BAD} 项：通过 {OK}，失败 {BAD}")
sys.exit(1 if BAD else 0)
