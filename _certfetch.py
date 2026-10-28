# -*- coding: utf-8 -*-
"""把设备 /root/certbak 里最新的证书包拉回本机，加密落盘并回读校验。

为什么要拉回来：设备系统盘一旦重装/挂掉，/root/certbak 跟着没。
包里含私钥，所以本机这一份必须是密文（crypto_store.seal_bytes），
落到 data/certbak/（data/ 在 .gitignore 里）。

流程（每台）：
  1) 找设备上最新的 *.tgz，记 sha256
  2) cp 到 /tmp 并 chmod 644（/root/certbak 是 700，sftp 读不到）
  3) sftp 下载 → 立刻删设备 /tmp 副本
  4) 本机算 sha256，与设备比对
  5) 加密写入 data/certbak/<name>-<ts>.tgz.enc
  6) 解密回读，再比 sha256 —— 这一步是证明密文真的能还原
  7) 删掉明文临时文件
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import crypto_store                                              # noqa: E402
from core.session import SSHSession, SessionError                 # noqa: E402
from profiles import ProfileStore                                # noqa: E402

DATA = Path(r"D:\Tools\Terminal-GUI-Toolbox\data")
OUT = DATA / "certbak"
NAS = ("192.168.3.12", "飞牛OS")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def one(h: dict) -> bool:
    name = h.get("name") or "?"
    print("=" * 72)
    print(f"{name}  {h.get('host')}")
    sess = SSHSession()
    try:
        sess.connect(h.get("host") or "", int(h.get("port") or 22),
                     h.get("user") or "", key_path=h.get("key_path") or "",
                     key_passphrase=h.get("key_passphrase") or "")
    except (SessionError, Exception) as e:                       # noqa: BLE001
        print(f"连不上：{type(e).__name__}: {e}")
        return False
    tmp_local = OUT / f".tmp-{os.getpid()}.tgz"
    try:
        if not sess.sudo_ok(h.get("sudo_password") or ""):
            print("sudo 密码没过，跳过")
            return False
        rc, out, _ = sess.run_admin(
            "ls -t /root/certbak/*.tgz 2>/dev/null | head -1", timeout=30)
        pkg = (out or "").strip()
        if not pkg:
            print("设备上没有备份包（先跑 _certbackup.py）")
            return False
        rc, out, _ = sess.run_admin(f"sha256sum '{pkg}' | cut -d' ' -f1", timeout=30)
        dev_sha = (out or "").strip()
        # 群晖的 sftp 被 chroot 到 /volume1（listdir('.') 看到的是共享文件夹），
        # 飞牛没有 chroot。所以：root 拷到真实家目录，再用候选路径 stat 试哪个可见。
        user = h.get("user") or ""
        rc, out, _ = sess.run(
            "awk -F: -v u=%s '$1==u{print $6}' /etc/passwd" % user, timeout=20)
        home = (out or "").strip().splitlines()[-1] if (out or "").strip() else ""
        if not home.startswith("/"):
            print(f"拿不到家目录（{out!r}）")
            return False
        srel = ".tbfetch.tgz"
        rc, out, err = sess.run_admin(
            f"cp -p '{pkg}' '{home}/{srel}' && chmod 644 '{home}/{srel}' && echo ok",
            timeout=60)
        if "ok" not in (out or ""):
            print(f"复制到家目录失败：{(err or out).strip()[:200]}")
            return False
        OUT.mkdir(parents=True, exist_ok=True)
        cands = [srel,
                 f"/homes/{user}/{srel}",           # 群晖 chroot 视角
                 f"{home}/{srel}",                  # 飞牛真实路径
                 f"/{user}/{srel}",
                 f"/home/{user}/{srel}"]
        try:
            sftp = sess.sftp()
            hit = ""
            for c in cands:
                try:
                    sftp.stat(c)
                    hit = c
                    break
                except Exception:                                # noqa: BLE001
                    continue
            if not hit:
                print(f"sftp 看不到文件（试过 {cands}；cwd={sftp.normalize('.')}）")
                return False
            print(f"    sftp 可见路径 {hit!r}")
            sftp.get(hit, str(tmp_local))
        finally:
            sess.run_admin(f"rm -f '{home}/{srel}'", timeout=30)
        raw = tmp_local.read_bytes()
        got = sha(raw)
        if got != dev_sha:
            print(f"下载校验失败\n  设备 {dev_sha}\n  本机 {got}")
            return False
        ts = Path(pkg).name.replace("tbcert-", "").replace(".tgz", "")
        dst = OUT / f"{name}-{ts}.tgz.enc"
        blob = crypto_store.seal_blob(raw)
        dst.write_bytes(blob)
        back = crypto_store.open_blob(dst.read_bytes())
        if back is None or sha(back) != got:
            print("加密回读校验失败 —— 密文解不回来，不保留")
            dst.unlink(missing_ok=True)
            return False
        print(f"OK  {len(raw):>7} 字节明文 → {len(blob):>7} 字节密文")
        print(f"    sha256 {got}")
        print(f"    {dst}")
        return True
    finally:
        try:
            if tmp_local.exists():
                tmp_local.unlink()
        except OSError:
            pass
        sess.close()


def main() -> int:
    crypto_store.set_key_dir(DATA)
    store = ProfileStore(DATA / "profiles.dat")
    ok = bad = 0
    for h in store.profiles:
        if (h.get("name") or "") not in NAS:
            continue
        if one(h):
            ok += 1
        else:
            bad += 1
    print("\n" + "=" * 72)
    print(f"拉取结果：{ok} 台成功，{bad} 台失败（明文临时文件已删除）")
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
