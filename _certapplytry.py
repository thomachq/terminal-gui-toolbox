# -*- coding: utf-8 -*-
"""真机演练：把一张证书**装进设备证书库**，校验，再撤干净。

    python _certapplytry.py --profile Synology_DSM_VM
    python _certapplytry.py --profile 飞牛OS

做四件事，每步都打印判据：
  1. 本机生成一张一次性自签名证书（CN=tb-install-test.local）
  2. CI.apply  → 设备上的写入
  3. CI.verify → 文件 sha256 / 登记项 / 服务有没有重启
  4. CI.rollback → 撤销，并复查设备回到原样

跑完设备上不该留任何东西；`/root/certbak/` 里会多一个备份（含私钥，
只在设备上，刻意不删 —— 它是「出事了还能还原」的那一层）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import crypto_store                                              # noqa: E402
from core import certinstall as CI                               # noqa: E402
from core import devicecert as DC                                # noqa: E402
from core import servercert as SC                                # noqa: E402
from core.session import SSHSession                              # noqa: E402
from profiles import ProfileStore                                # noqa: E402

DATA = Path(r"D:\Tools\Terminal-GUI-Toolbox\data")


def hr(t: str) -> None:
    print(f"\n{'=' * 66}\n{t}\n{'=' * 66}")


def run(run_fn, cmd: str, timeout: int = 60):
    try:
        return run_fn(cmd, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return 1, "", f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--keep", action="store_true", help="装完不撤销（默认撤销）")
    a = ap.parse_args()

    crypto_store.set_key_dir(DATA)
    store = ProfileStore(DATA / "profiles.dat")
    target = None
    for h in store.profiles:
        if a.profile in (h.get("name"), h.get("host")):
            target = h
            break
    if target is None:
        print("找不到档案:", a.profile)
        return 1

    host, user = target.get("host"), target.get("user") or ""
    port = int(str(target.get("port") or 22))
    key_path = target.get("key_path") or ""
    pw = target.get("key_passphrase") or ""
    hr(f"{target.get('name')}  {user}@{host}:{port}")
    if not key_path:
        print("这条档案不是私钥认证，跳过")
        return 1

    sess = SSHSession()
    try:
        sess.connect(host, port, user, key_path=key_path, key_passphrase=pw)
    except Exception as e:  # noqa: BLE001
        print(f"连不上：{type(e).__name__}: {e}")
        return 1
    try:
        spw = target.get("sudo_password") or ""
        if not spw or not sess.sudo_ok(spw):
            print("sudo 密码没通过，放弃（不重试，免得累计 passwd_tries）")
            return 1
        print("sudo 通过")

        kind = DC.detect(sess)
        print("设备证书模块:", kind or "（认不出）")
        if not kind:
            return 1

        hr("0. 装之前的状态")
        run_fn = sess.run_admin
        rc, before_rows, _ = run(
            run_fn, "ls -1 /usr/syno/etc/certificate/_archive" if kind == CI.KIND_SYNO
            else "wc -c < /usr/trim/etc/network_cert_all.conf")
        print((before_rows or "").strip()[:500] or "（空）")
        rc, h0, _ = run(
            run_fn, "sha256sum /usr/syno/etc/certificate/_archive/INFO"
            if kind == CI.KIND_SYNO
            else "sha256sum /usr/trim/etc/network_cert_all.conf")
        print("登记文件 sha256:", (h0 or "").strip()[:80])
        rc, w0, _ = run(sess.run, "ps -eo args | grep -c '[n]ginx'")
        print("nginx 进程数:", (w0 or "").strip())

        hr("1. 本机生成一次性证书")
        cert, key = SC.make_selfsigned(
            "tb-install-test.local", ["tb-install-test.local"], days=30)
        info = CI.parse_bundle(cert, key)
        print(f"CN={info['cn']} SAN={info['sans']} 到期 {info['not_after']} "
              f"配对={info['key_ok']} 问题={info['problems']}")

        hr("2. 装进设备")
        t0 = time.time()
        res = CI.apply(sess, kind, cert, key, "", "toolbox-selftest-cert")
        print(f"用时 {time.time() - t0:.1f}s")
        print("id/domain:", res.get("id") or res.get("domain"))
        print("目录:", res.get("dir"))
        for p in res.get("files") or []:
            print("  文件:", p)
        print("备份:", res.get("backup"))

        hr("3. 回读校验")
        ok, msg = CI.verify(sess, res)
        print(("PASS " if ok else "FAIL ") + msg)

        hr("4. 服务有没有被打断")
        rc, w1, _ = run(sess.run, "ps -eo args | grep -c '[n]ginx'")
        print("nginx 进程数:", (w1 or "").strip(), "（装之前 ",
              (w0 or "").strip(), "）")
        if kind == CI.KIND_SYNO:
            rc, out, err = run(
                run_fn, "/usr/bin/nginx -t -c /etc/nginx/nginx.conf.run")
            print("nginx -t:", ((out or "") + (err or "")).strip().splitlines()[-1:])
        else:
            rc, out, _ = run(sess.run, "curl -s -m 5 -o /dev/null -w '%{http_code}' "
                                       "http://127.0.0.1:15666/")
            print("15666 http:", (out or "").strip())

        if a.keep:
            print("\n--keep：保留这次写入，不撤销")
            return 0 if ok else 1

        hr("5. 撤销")
        rb, rm = CI.rollback(sess, res)
        print(("PASS " if rb else "FAIL ") + rm)

        hr("6. 撤销后复查")
        rc, after_rows, _ = run(
            run_fn, "ls -1 /usr/syno/etc/certificate/_archive" if kind == CI.KIND_SYNO
            else "wc -c < /usr/trim/etc/network_cert_all.conf")
        same = (before_rows or "").strip() == (after_rows or "").strip()
        print("装之前:", (before_rows or "").strip()[:200])
        print("撤之后:", (after_rows or "").strip()[:200])
        print("一致？", "是" if same else "否（下面看差异）")
        if not same:
            print(after_rows)
        rc, h1, _ = run(
            run_fn, "sha256sum /usr/syno/etc/certificate/_archive/INFO"
            if kind == CI.KIND_SYNO
            else "sha256sum /usr/trim/etc/network_cert_all.conf")
        print("登记文件 sha256:", (h1 or "").strip()[:80])
        print("登记文件字节级一致？",
              "是" if (h0 or "").split()[0:1] == (h1 or "").split()[0:1] else "否")
        rc, out, _ = run(run_fn, f"ls -d {res.get('dir')} 2>&1")
        print("目录还在？", (out or "").strip())

        ok_all = bool(ok and rb and same)
        print("\n总判定:", "PASS" if ok_all else "FAIL")
        return 0 if ok_all else 1
    finally:
        sess.close()


if __name__ == "__main__":
    raise SystemExit(main())
