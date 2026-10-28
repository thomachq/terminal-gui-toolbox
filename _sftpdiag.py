# -*- coding: utf-8 -*-
"""诊断：sftp 视角、家目录、cp 落地位置。只读（除 /tmp 下一个探针文件）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import crypto_store                                              # noqa: E402
from core.session import SSHSession                              # noqa: E402
from profiles import ProfileStore                                # noqa: E402

DATA = Path(r"D:\Tools\Terminal-GUI-Toolbox\data")


def run(h):
    print("=" * 60)
    print(h.get("name"))
    s = SSHSession()
    s.connect(h.get("host") or "", int(h.get("port") or 22),
              h.get("user") or "", key_path=h.get("key_path") or "",
              key_passphrase=h.get("key_passphrase") or "")
    try:
        for c in ["pwd; echo HOME=$HOME; id",
                  "awk -F: -v u=%s '$1==u{print \"passwd_home=\"$6}' /etc/passwd"
                  % h.get("user")]:
            rc, out, err = s.run(c, timeout=20)
            print(f"$ {c}\n(rc={rc}) {(out or '').strip()}")
        print("sudo_ok:", s.sudo_ok(h.get("sudo_password") or ""))
        rc, out, _ = s.run_admin(
            "ls -t /root/certbak/*.tgz 2>/dev/null | head -1", timeout=20)
        pkg = (out or "").strip()
        print(f"pkg={pkg}")
        home = "/var/services/homes/" + (h.get("user") or "")
        rc, out, err = s.run_admin(
            f"cp -p '{pkg}' {home}/sftpprobe.tgz && chmod 644 {home}/sftpprobe.tgz "
            f"&& ls -l {home}/sftpprobe.tgz", timeout=30)
        print(f"cp home rc={rc} {(out or '').strip()} {(err or '').strip()[:200]}")
        sf = s.sftp()
        print("sftp normalize('.'):", sf.normalize("."))
        try:
            print("sftp listdir('.'):", sf.listdir(".")[:8])
        except Exception as e:                                   # noqa: BLE001
            print("listdir('.') 失败:", e)
        for p in ["/tmp/sftpprobe.tgz", "sftpprobe.tgz", "/sftpprobe.tgz",
                  "/var/services/homes/%s/sftpprobe.tgz" % h.get("user")]:
            try:
                st = sf.stat(p)
                print(f"  stat {p!r} -> OK {st.st_size} 字节")
            except Exception as e:                               # noqa: BLE001
                print(f"  stat {p!r} -> {type(e).__name__}")
        s.run_admin(f"rm -f /tmp/sftpprobe.tgz {home}/sftpprobe.tgz", timeout=20)
    finally:
        s.close()


def main():
    crypto_store.set_key_dir(DATA)
    st = ProfileStore(DATA / "profiles.dat")
    for h in st.profiles:
        if (h.get("name") or "") in ("192.168.3.12", "飞牛OS"):
            try:
                run(h)
            except Exception as e:                               # noqa: BLE001
                print(f"[异常] {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
