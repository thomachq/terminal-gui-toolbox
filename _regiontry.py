# -*- coding: utf-8 -*-
"""真机验证区域策略的 nft 脚本（用 _selftest 表，绝不碰 tb_region）。

跑法：
    python _regiontry.py 飞牛OS            # 探测 + 语法检查（需 root）
    python _regiontry.py 飞牛OS --apply    # 真建一张 tb_region_selftest 表再删掉

为什么用另一张表名：真表叫 tb_region，是用户实际在用的策略。自测如果直接
在它上面跑，等于拿用户的生效配置做实验。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import crypto_store                                   # noqa: E402
from core import region as R                          # noqa: E402
from core.detect import detect                        # noqa: E402
from core.session import ConnSpec, open_session       # noqa: E402
from profiles import ProfileStore                     # noqa: E402

DATA = Path(r"D:\Tools\Terminal-GUI-Toolbox\data")
TEST_TABLE = "tb_region_selftest"


def load(name: str) -> dict:
    crypto_store.set_key_dir(DATA)
    st = ProfileStore(DATA / "profiles.dat")
    for p in st.data.get("profiles") or []:
        if str(p.get("name")) == name or str(p.get("host")) == name:
            return p
    raise SystemExit(f"没找到档案 {name}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_apply = "--apply" in sys.argv
    h = load(args[0] if args else "飞牛OS")
    proto = "telnet" if str(h.get("proto") or "ssh") == "telnet" else "ssh"
    cs = ConnSpec(proto=proto, host=str(h.get("host") or ""),
                  port=int(h.get("port") or 22),
                  user=str(h.get("user") or ""),
                  auth="key" if h.get("key_path") else "password",
                  key_path=str(h.get("key_path") or ""),
                  password=str(h.get("password") or ""),
                  key_passphrase=str(h.get("key_passphrase") or ""),
                  sudo_password=str(h.get("sudo_password") or ""))
    if h.get("sudo_password"):
        cs.elevate = True
    sess = open_session(cs)
    sess.bind_device(detect(sess))
    if h.get("sudo_password"):
        # can_admin() 只看 self.sudo_pw / 免密探测；不设这一项它会说「需要 sudo 密码」
        sess.sudo_pw = str(h["sudo_password"])
    try:
        dev = detect(sess)
        print(f"== {h.get('name')} {cs.user}@{cs.host}:{cs.port}  "
              f"{dev.os_name} / {dev.kernel}  root={dev.is_root}")
        caps = R.probe(sess)
        print(f"caps: nft={caps.nft!r} ver={caps.version!r} "
              f"admin={caps.admin} ok={caps.ok}")
        print(f"      {caps.summary()}")
        if not caps.nft:
            return 0
        st = R.state(sess, caps, table=TEST_TABLE)
        print(f"state({TEST_TABLE}): {st.get('exists')} v4={st.get('v4')} "
              f"v6={st.get('v6')} action={st.get('action')!r}")
        plan = R.make_plan([{"key": "MO", "name": "中国澳门",
                             "group": "country"}])
        print(f"plan: {plan.summary()}")
        script = R.nft_script(plan, exists=bool(st.get("exists")),
                              table=TEST_TABLE)
        print("---- 脚本前 12 行 ----")
        print("\n".join(script.splitlines()[:12]))
        print(f"---- 脚本共 {len(script.splitlines())} 行 ----")
        if not caps.admin:
            print("没有 root：只能生成脚本，语法没法真验（nft -c 也要 CAP_NET_ADMIN）")
            return 0
        # 真建一张 **测试表**（tb_region_selftest，不是真表 tb_region），
        # 走完整的 upload → nft -f → 回读 → 删表，验证提权路径。测完就删。
        print("== 用测试表做一次完整下发（走 R.apply，不是手拼命令）")
        plan = R.make_plan([{"key": "MO", "name": "中国澳门",
                             "group": "country"}])
        rc, body, err = R.apply(sess, plan, caps, table=TEST_TABLE)
        print(f"[apply] rc={rc} {err}")
        if rc != 0:
            print(body[:600])
            return 1
        st2 = R.state(sess, caps, table=TEST_TABLE)
        print(f"[after] exists={st2.get('exists')} v4={st2.get('v4')} "
              f"v6={st2.get('v6')} action={st2.get('action')!r}")
        rc3, out3, _ = sess.run_admin(
            f"{caps.nft} delete table inet {TEST_TABLE} 2>&1; echo rc=$?",
            timeout=60)
        print(f"[cleanup] {(out3 or '').strip()[:200]}")
        st3 = R.state(sess, caps, table=TEST_TABLE)
        print(f"[verify] 删干净了 = {not st3.get('exists')}")
        return 0 if (rc == 0 and st2.get("exists")
                     and not st3.get("exists")) else 1
    finally:
        sess.close()


if __name__ == "__main__":
    sys.exit(main())
