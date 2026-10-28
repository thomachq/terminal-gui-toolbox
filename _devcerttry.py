# -*- coding: utf-8 -*-
"""真机验证：core.devicecert 读群晖 / 飞牛的证书清单（只读）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import crypto_store                                              # noqa: E402
from core import devicecert as DC                                 # noqa: E402
from core.session import SSHSession                               # noqa: E402
from profiles import ProfileStore                                 # noqa: E402

DATA = Path(r"D:\Tools\Terminal-GUI-Toolbox\data")


def main() -> int:
    crypto_store.set_key_dir(DATA)
    store = ProfileStore(DATA / "profiles.dat")
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    for h in store.data.get("profiles") or []:
        if h.get("proto") not in ("ssh", "") or not h.get("host"):
            continue
        if want and str(h.get("name")) != want:
            continue
        if not want and not any(k in str(h.get("name") or "")
                                for k in ("3.12", "飞牛")):
            continue
        print(f"\n===== {h.get('name')} =====")
        sess = SSHSession()
        sess.connect(h["host"], int(str(h.get("port") or 22)),
                     h.get("user") or "", key_path=h.get("key_path") or "",
                     key_passphrase=h.get("key_passphrase") or "")
        if h.get("sudo_password"):
            sess.sudo_pw = h["sudo_password"]
        try:
            kind = DC.detect(sess)
            print("kind =", kind or "(未识别)")
            rows = DC.list_device(sess, kind)
            print(DC.summary(rows))
            for r in rows:
                print(f"  - {r.get('name')} | CN={r.get('cn')} | "
                      f"SAN={','.join(r.get('sans') or [])} | "
                      f"到期={r.get('not_after')} ({r.get('days_left')}天) | "
                      f"私钥={'有' if r.get('has_key') else '无'} | "
                      f"在用={'是' if r.get('used') else '否'} | "
                      f"pem={len(r.get('pem') or '')}B | "
                      f"err={r.get('error') or '-'}")
                if r.get("services"):
                    print(f"      服务：{'、'.join(r['services'][:6])}")
        except Exception as e:  # noqa: BLE001
            print(f"失败：{type(e).__name__}: {e}")
            if getattr(e, "hint", ""):
                print(f"提示：{e.hint}")
        finally:
            sess.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
