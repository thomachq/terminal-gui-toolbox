# -*- coding: utf-8 -*-
"""在真设备的「一次性网络命名空间」里验证区域策略脚本（不碰真机的表）。

怎么做到不改设备：`unshare -Urn` 开一个新的 user+net namespace，里面我们是
root、有 CAP_NET_ADMIN，但网络栈是**全新的空表**，退出即销毁。所以可以
真跑 `nft -f`、真读回 JSON，而宿主机上一条规则都不留 —— 也不需要 sudo 密码。

两个阶段（首次建表 / 整表重建）必须在**同一个** netns 里连着跑，否则第二次
`delete table` 找不到表（新建的 namespace 是空的），测出来的是假红。

跑法：
    python _regionnetns.py 飞牛OS
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import crypto_store                                     # noqa: E402
from core import region as R                            # noqa: E402
from core.session import ConnSpec, open_session         # noqa: E402
from profiles import ProfileStore                       # noqa: E402

DATA = Path(r"D:\Tools\Terminal-GUI-Toolbox\data")
FAIL = []


def load(name: str) -> dict:
    crypto_store.set_key_dir(DATA)
    st = ProfileStore(DATA / "profiles.dat")
    for p in st.data.get("profiles") or []:
        if name in (str(p.get("name")), str(p.get("host"))):
            return p
    raise SystemExit(f"没找到档案 {name}")


def chk(ok: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if ok else "  FAIL ") + label +
          (f"  [{extra}]" if extra else ""))
    if not ok:
        FAIL.append(label)


def run_case(s, caps, title: str, plan) -> None:
    print(f"\n== {title}: v4={len(plan.v4)} v6={len(plan.v6)}")
    fresh = R.nft_script(plan, exists=False)
    rebuild = R.nft_script(plan, exists=True)
    for i, (name, text) in enumerate((("f", fresh), ("r", rebuild)), 1):
        ok, info = R.upload(s, text, path=f"/tmp/.rt{i}")
        chk(ok, f"{name} 脚本上传（{len(text)} 字符）", info)
        if not ok:
            return
    inner = ("echo '@@P1'; " + caps.nft + " -f /tmp/.rt1; echo \"@@R1=$?\"; "
             "echo '@@P2'; " + caps.nft + " -f /tmp/.rt2; echo \"@@R2=$?\"; "
             "echo '@@J'; " + caps.nft + f" -j list table inet {R.TABLE}")
    rc, out, err = s.run("unshare -Urn sh -c " + json.dumps(inner), timeout=240)
    text = (out or "") + (err or "")
    r1 = r2 = None
    js: list[str] = []
    grab = False
    for line in text.splitlines():
        if line.startswith("@@R1="):
            r1 = line[5:].strip()
        elif line.startswith("@@R2="):
            r2 = line[5:].strip()
        elif line.startswith("@@J"):
            grab = True
        elif grab:
            js.append(line)
    st = R.parse_state("\n".join(js))
    chk(r1 == "0", "首次建表 rc=0", str(r1))
    chk(r2 == "0", "整表重建 rc=0（delete + 建，同一事务）", str(r2))
    chk(st["exists"], "表真的存在于 nft 里")
    chk(st["v4"] > 0 if plan.v4 else st["v4"] == 0,
        f"IPv4 元素读回来 {st['v4']} 条（计划 {len(plan.v4)}，auto-merge 会并掉一些）")
    chk(st["v6"] > 0 if plan.v6 else True, f"IPv6 元素 {st['v6']} 条")
    chk(st["action"] == plan.verdict, "读到的动作与计划一致", st["action"])
    if r1 != "0" or r2 != "0":
        print("    " + "\n    ".join(
            [ln for ln in text.splitlines() if "Error" in ln or "error" in ln][:6]))
    for i in (1, 2):
        s.run(f"rm -f /tmp/.rt{i}", timeout=20)


def main() -> int:
    h = load(sys.argv[1] if len(sys.argv) > 1 else "飞牛OS")
    cs = ConnSpec(proto="ssh", host=str(h["host"]),
                  port=int(h.get("port") or 22), user=str(h["user"]),
                  auth="key", key_path=str(h.get("key_path") or ""),
                  key_passphrase=str(h.get("key_passphrase") or ""))
    s = open_session(cs)
    try:
        caps = R.probe(s)
        print(f"nft = {caps.nft!r} / {caps.version!r} / admin={caps.admin}")
        if not caps.nft:
            print("没有 nft，跳过")
            return 0
        rc, out, _ = s.run("command -v unshare >/dev/null && echo yes || echo no")
        if "yes" not in (out or ""):
            print("没有 unshare，跳过（只能人工验）")
            return 0

        run_case(s, caps, "澳门（58 段 → 合并后）",
                 R.make_plan([{"key": "MO", "name": "中国澳门",
                               "group": "country"}]))
        run_case(s, caps, "澳门 + 香港（大表，几百 KB 脚本）",
                 R.make_plan([{"key": "MO", "name": "中国澳门",
                               "group": "country"},
                              {"key": "HK", "name": "中国香港",
                               "group": "country"}]))
        # 地址库里没有 IPv6 数据，这里手造一份验证 ipv6_addr 表 + ip6 saddr 规则
        run_case(s, caps, "IPv4 + IPv6 混合（手造）",
                 R.Plan(picks=[{"key": "x", "name": "手造样本"}],
                        v4=["223.5.5.0/24", "223.6.6.0/24"],
                        v6=["2001:db8::/32", "2400:3200::/32"]))
        print("\n结论：" + ("全部通过" if not FAIL else f"{len(FAIL)} 处不对"))
        return 0 if not FAIL else 1
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
