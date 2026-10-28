# -*- coding: utf-8 -*-
"""证书目录「备份 + 解包校验」演练 —— 只读原目录，只写 /tmp 与 /root/certbak。

为什么要有这一步：
  只 tar 出一个包，不代表它能还原。包里少一个文件、权限没保住、
  解出来是半截，都要等到真出事那天才发现。所以这里把包解开、
  逐文件 sha256 跟原目录比一遍，全等才算数。

每台设备在一条 shell 里完成（sudo 凭证按 tty 缓存，不能拆成多条）：
  1) cp -a 到 staging（保权限/时间戳）
  2) tar 打包
  3) 解包到另一个目录
  4) 两边 find + sha256sum 排序后 cmp
  5) 全等才把包复制到 /root/certbak/（重启存活）
  6) 清理 /tmp

不碰任何现有配置文件，不改证书，不 reload。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import crypto_store                                              # noqa: E402
from core.session import SSHSession, SessionError                 # noqa: E402
from profiles import ProfileStore                                # noqa: E402

DATA = Path(r"D:\Tools\Terminal-GUI-Toolbox\data")
NAS = ("192.168.3.12", "飞牛OS")

# 群晖：一个目录
SYNO_SH = r"""
set -e
TS=$(date +%Y%m%d-%H%M%S)
STAGE=/tmp/tbstage-$TS
UNPK=/tmp/tbunpk-$TS
rm -rf $STAGE $UNPK
mkdir -p $STAGE $UNPK
cp -a /usr/syno/etc/certificate/_archive $STAGE/
tar czf /tmp/tbcert-$TS.tgz -C $STAGE .
tar xzf /tmp/tbcert-$TS.tgz -C $UNPK
( cd $STAGE && find . -type f -exec sha256sum {} + | sort ) > /tmp/tba-$TS.txt
( cd $UNPK  && find . -type f -exec sha256sum {} + | sort ) > /tmp/tbb-$TS.txt
N=$(wc -l < /tmp/tba-$TS.txt)
SZ=$(stat -c%s /tmp/tbcert-$TS.tgz)
if cmp -s /tmp/tba-$TS.txt /tmp/tbb-$TS.txt; then
  mkdir -p /root/certbak
  cp -p /tmp/tbcert-$TS.tgz /root/certbak/
  chmod 700 /root/certbak
  chmod 600 /root/certbak/tbcert-$TS.tgz
  echo "VERIFY=OK files=$N tarbytes=$SZ pkg=/root/certbak/tbcert-$TS.tgz"
else
  echo "VERIFY=FAIL files=$N"
  diff /tmp/tba-$TS.txt /tmp/tbb-$TS.txt | head -6
fi
rm -rf $STAGE $UNPK /tmp/tba-$TS.txt /tmp/tbb-$TS.txt
ls -l /root/certbak/ | tail -4
"""

# 飞牛：conf + ssls 两个路径
FNOS_SH = r"""
set -e
TS=$(date +%Y%m%d-%H%M%S)
STAGE=/tmp/tbstage-$TS
UNPK=/tmp/tbunpk-$TS
rm -rf $STAGE $UNPK
mkdir -p $STAGE $UNPK
cp -a /usr/trim/etc/network_cert_all.conf $STAGE/
cp -a /usr/trim/var/trim_connect/ssls $STAGE/
tar czf /tmp/tbcert-$TS.tgz -C $STAGE .
tar xzf /tmp/tbcert-$TS.tgz -C $UNPK
( cd $STAGE && find . -type f -exec sha256sum {} + | sort ) > /tmp/tba-$TS.txt
( cd $UNPK  && find . -type f -exec sha256sum {} + | sort ) > /tmp/tbb-$TS.txt
N=$(wc -l < /tmp/tba-$TS.txt)
SZ=$(stat -c%s /tmp/tbcert-$TS.tgz)
if cmp -s /tmp/tba-$TS.txt /tmp/tbb-$TS.txt; then
  mkdir -p /root/certbak
  cp -p /tmp/tbcert-$TS.tgz /root/certbak/
  chmod 700 /root/certbak
  chmod 600 /root/certbak/tbcert-$TS.tgz
  echo "VERIFY=OK files=$N tarbytes=$SZ pkg=/root/certbak/tbcert-$TS.tgz"
else
  echo "VERIFY=FAIL files=$N"
  diff /tmp/tba-$TS.txt /tmp/tbb-$TS.txt | head -6
fi
rm -rf $STAGE $UNPK /tmp/tba-$TS.txt /tmp/tbb-$TS.txt
ls -l /root/certbak/ | tail -4
"""


def probe(h: dict) -> bool:
    name = h.get("name")
    print("=" * 72)
    print(f"{name}  {h.get('host')}:{h.get('port')}")
    sess = SSHSession()
    try:
        sess.connect(h.get("host") or "", int(h.get("port") or 22),
                     h.get("user") or "", key_path=h.get("key_path") or "",
                     key_passphrase=h.get("key_passphrase") or "")
    except (SessionError, Exception) as e:                       # noqa: BLE001
        print(f"连不上：{type(e).__name__}: {e}")
        return False
    try:
        rc, out, _ = sess.run("test -d /usr/syno/etc/certificate; echo $?", timeout=20)
        is_syno = (out or "").strip().endswith("0")
        if not sess.sudo_ok(h.get("sudo_password") or ""):
            print("sudo 密码没过，跳过（不重试，免得累计 passwd_tries）")
            return False
        rc, out, err = sess.run_admin(SYNO_SH if is_syno else FNOS_SH, timeout=180)
        body = (out or "").strip()
        print(body or "(空输出)")
        if (err or "").strip():
            print(f"[stderr] {err.strip()[:400]}")
        return "VERIFY=OK" in body
    finally:
        sess.close()


def main() -> int:
    crypto_store.set_key_dir(DATA)
    store = ProfileStore(DATA / "profiles.dat")
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    ok = bad = 0
    for h in store.profiles:
        n = h.get("name") or ""
        if n not in NAS:
            continue
        if only and only not in n:
            continue
        if probe(h):
            ok += 1
        else:
            bad += 1
    print("\n" + "=" * 72)
    print(f"演练结果：{ok} 台校验通过，{bad} 台失败")
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
