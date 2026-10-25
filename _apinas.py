# -*- coding: utf-8 -*-
"""用「工具箱自己的 HTTP API」连群晖，跑一遍**只读**探测。

为什么绕 API 而不直接 paramiko：API 走的是 `api/pool.py` 那条路
（`pool.open` → `open_session` → `SSHSession.connect` → `detect`），
和智能体、能力接口用的是同一条链路。直接 paramiko 只能证明"密码/私钥对"，
证明不了"工具箱能不能用"。

全部请求都打在 127.0.0.1 的临时端口上，服务是进程内起的，
不会碰用户在用的那个实例，也不会改 `data/api.json`（用临时目录）。

用法：
    python _apinas.py                # 口令从群晖档案读，没有就交互输入
    python _apinas.py --list         # 只打印要跑的命令
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import api.server as SV                                          # noqa: E402
import crypto_store                                              # noqa: E402
from _synoread import CMDS, SCHEMA_CMD, _pick_target             # noqa: E402
from profiles import ProfileStore                                # noqa: E402

DATA = ROOT / "data"


def http(base: str, token: str, method: str, path: str,
         body: dict | None = None, timeout: int = 120) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code} {path}\n{raw}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--profile", default="")
    args = ap.parse_args()

    if args.list:
        for i, (t, c) in enumerate(CMDS, 1):
            print(f"{i:2}. {t}\n    {c}")
        return 0

    crypto_store.set_key_dir(DATA)
    store = ProfileStore(DATA / "profiles.dat")
    hosts = store.data.get("profiles") or []
    if args.profile:
        prof = next((h for h in hosts if str(h.get("name") or "") == args.profile), None)
    else:
        prof = _pick_target(store)
    if prof is None:
        print("没找到群晖档案")
        return 1

    pw = prof.get("key_passphrase") or os.environ.get("SYNO_KEYPW") or ""
    if not pw:
        import getpass
        print(f"档案「{prof.get('name')}」没存私钥口令。请输入（不回显）：")
        try:
            pw = getpass.getpass("  私钥密码: ")
        except (KeyboardInterrupt, EOFError):
            return 1
    if not pw:
        print("没给口令，退出")
        return 1

    spec = {
        "host": prof.get("host"), "port": int(str(prof.get("port") or 22)),
        "user": prof.get("user"), "key_path": prof.get("key_path"),
        "key_passphrase": pw,
    }
    print(f"档案 {prof.get('name')} → {spec['user']}@{spec['host']}:{spec['port']}")

    with tempfile.TemporaryDirectory() as tmp:
        cfg, pool, srv = SV.make_default(Path(tmp))
        cfg.data["enabled"] = True
        cfg.save()
        ok, msg = srv.start()
        if not ok:
            print("API 起不来：", msg)
            return 2
        base = f"http://127.0.0.1:{srv.port}"
        token = cfg.token()
        print(f"API 已起：{base}")

        t0 = time.time()
        try:
            res = http(base, token, "POST", "/v1/sessions", spec)
        except SystemExit as e:
            print("建会话失败：", e)
            srv.stop(keep_enabled=True)
            return 3
        sid = res["id"]
        dev = res.get("device") or {}
        print(f"会话 {sid} 建好，用时 {time.time() - t0:.1f}s")
        print(f"  识别：{dev.get('os_name') or dev.get('os') or '?'} / "
              f"用户 {dev.get('user') or '?'} / root={dev.get('is_root')} / "
              f"{dev.get('vendor') or '通用'}")

        out_lines: list[str] = []
        try:
            print("\n" + "=" * 62)
            for title, cmd in CMDS:
                r = http(base, token, "POST", f"/v1/sessions/{sid}/exec",
                         {"cmd": cmd, "timeout": 40})
                out_lines.append(f"### {title}   (rc={r.get('rc')})")
                body = (r.get("out") or "").strip()
                if body:
                    out_lines.append(body[:1400])
                err = (r.get("err") or "").strip()
                if err:
                    out_lines.append(f"[stderr] {err[:300]}")
                out_lines.append("")
            r = http(base, token, "POST", f"/v1/sessions/{sid}/exec",
                     {"cmd": SCHEMA_CMD, "timeout": 40})
            out_lines.append("### 持久化配置的 JSON 键名")
            out_lines.append((r.get("out") or "(没找到 firewall.d/*.json)").strip()[:2000])
        finally:
            try:
                http(base, token, "DELETE", f"/v1/sessions/{sid}")
            except SystemExit:
                pass
            srv.stop(keep_enabled=True)

        text = "\n".join(out_lines)
        print(text[:9000])
        p = ROOT / "群晖防火墙-API测试.txt"
        p.write_text("档案：" + str(prof.get("name")) + "\n\n" + text, encoding="utf-8")
        print(f"\n完整输出已存：{p}")
        print("只读测试结束：没改任何规则，没执行 --reload。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
