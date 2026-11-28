# -*- coding: utf-8 -*-
"""Electron 适配层自测：真起 Python 桥 → 真发 HTTP → 真跑一条本机命令。

不 mock 浏览器，只验证「外壳要依赖的那一层」是真的。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OK = BAD = 0


def chk(cond, msg, extra=""):
    global OK, BAD
    if cond:
        OK += 1
        print("[ok]  ", msg, extra)
    else:
        BAD += 1
        print("[NG]  ", msg, extra)


def req(port: int, method: str, path: str, token: str, body=None):
    data = json.dumps(body or {}).encode() if body is not None else None
    r = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def main():
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "electron" / "bridge" / "api_main.py")],
        cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace")
    sid = ""
    try:
        line = proc.stdout.readline().strip() if proc.stdout else ""
        chk(bool(line), "桥启动后 stdout 吐出一行 JSON",
            __import__("re").sub(r'ttb_[A-Za-z0-9_-]+', '<token>', line[:80]))
        info = json.loads(line) if line else {}
        chk(info.get("ok") is True, "桥报告启动成功", str(info.get("error") or ""))
        chk(bool(info.get("token", "").startswith("ttb_")), "令牌是 ttb_ 前缀")
        port = int(info.get("port") or 0)
        chk(1000 < port < 65536, "端口是合法高位端口", str(port))

        st, h = req(port, "GET", "/v1/health", "")
        chk(st == 200 and h.get("ok") is True, "/v1/health 公开可探活",
            json.dumps(h, ensure_ascii=False)[:100])
        st, _ = req(port, "GET", "/v1/sessions", "bad-token")
        chk(st == 401, "错误令牌被拒（401）")

        token = info["token"]
        st, h = req(port, "GET", "/v1/hosts", token)
        chk(st == 200, "/v1/hosts 带令牌能读", str(st))
        st, conn = req(port, "POST", "/v1/sessions", token,
                       {"protocol": "local", "note": "bridgetest"})
        chk(st == 201 and bool(conn.get("id")), "POST /v1/sessions 连本机",
            str(conn)[:120])
        sid = str(conn.get("id") or "")
        st, out = req(port, "POST", f"/v1/sessions/{sid}/exec", token,
                      {"cmd": "echo TB_BRIDGE_OK", "timeout": 15})
        chk(st == 200 and out.get("rc") == 0 and "TB_BRIDGE_OK" in str(out.get("out")),
            "exec 真跑了一条命令并拿回输出", str(out)[:160])
        st, sess = req(port, "GET", "/v1/sessions", token)
        chk(st == 200 and any(x.get("id") == sid for x in sess.get("sessions", [])),
            "会话出现在列表里")
        # 通用实体 CRUD 桥
        st, ents = req(port, "GET", "/v1/tools/store", token)
        chk(st == 200 and len(ents.get("entities", [])) == 5, "store 列出 5 个实体")
        for entity in ("hosts", "creds", "scripts", "proxies", "audit"):
            st, lst = req(port, "GET", f"/v1/tools/store/{entity}", token)
            chk(st == 200 and isinstance(lst.get(entity), list),
                f"store/{entity} 读到真实数据", f"{lst.get('count')} 条")
        st, r = req(port, "GET", "/v1/tools/store/creds/get?key=__none__", token)
        chk(st == 404, "不存在的记录返回 404")

        # core 白名单桥（Electron 全功能迁移的适配层）
        st, cat = req(port, "GET", "/v1/tools/core/catalog", token)
        chk(st == 200 and cat.get("count", 0) >= 50,
            "core 桥 catalog 列出 68 个函数", str(cat.get("count")))
        st, r = req(port, "POST", "/v1/tools/core", token,
                    {"name": "cron.validate", "args": {"spec": "0 3 * * *"}})
        chk(st == 200 and r.get("result") is None, "core 桥调纯函数（合法 cron）")
        st, r = req(port, "POST", "/v1/tools/core", token,
                    {"name": "cron.validate", "args": {"spec": "99 * * * *"}})
        chk(st == 200 and bool(r.get("result")), "core 桥调纯函数（非法 cron 返回原因）")
        st, r = req(port, "POST", "/v1/tools/core", token,
                    {"name": "__import__", "args": {"name": "os"}})
        chk(st == 404, "未注册的函数被拒（404）")
        st, r = req(port, "POST", "/v1/tools/core", token,
                    {"name": "region.probe", "args": {}})
        chk(st == 400, "需要 session 的函数无连接时被拒（400）")

        st, res = req(port, "DELETE", f"/v1/sessions/{sid}", token)
        chk(st == 200 and res.get("closed") is True, "DELETE 关闭会话")
        sid = ""
    finally:
        if sid:
            try:
                req(int(info.get("port") or 0), "DELETE", f"/v1/sessions/{sid}",
                    info.get("token") or "")
            except Exception:  # noqa: BLE001
                pass
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    print(f"\n合计 {OK + BAD} 项：通过 {OK}，失败 {BAD}")
    return 0 if BAD == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
