# -*- coding: utf-8 -*-
"""档案保存（profiles.save_blob）的可靠性：WinError 5 不能再丢用户的数据。

背景（2026-10-28 实测）：用户存档案弹「保存失败 [WinError 5] 拒绝访问：
profiles.dat.tmp -> profiles.dat」。原因是 Windows 上目标被任何进程开着
（杀软扫描 / 并发读），os.replace 就拒绝访问；22:06 成功、22:11 失败，
纯看运气。失败时数据还在 .tmp，但用户改的东西等于白改。
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import profiles as P                    # noqa: E402

FAIL = []
PASS = [0]


def chk(ok, title, detail=""):
    if ok:
        PASS[0] += 1
        print(f"  ok   {title}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL.append(title)
        print(f"  FAIL {title}" + (f"  [{detail}]" if detail else ""))


import tempfile                                        # noqa: E402
work = Path(tempfile.mkdtemp(prefix="tb_savetest_"))
dst = work / "profiles.dat"
BLOB = b'{"profiles": [1, 2, 3]}'

real_replace = os.replace

# 1. 正常路径：一次 replace 成功，目标内容对、tmp 清掉
try:
    P.save_blob(dst, BLOB)
    chk(dst.read_bytes() == BLOB, "正常保存")
    chk(not dst.with_name(dst.name + ".tmp").exists(), "tmp 已清掉")
except OSError as e:
    chk(False, "正常保存", str(e))

# 2. 连着 4 次被占、第 5 次成功 → 应该重试成功而不是报错
state = {"n": 0}


def flaky(tmp, d):
    state["n"] += 1
    if state["n"] < 5:
        raise PermissionError(5, "拒绝访问")
    return real_replace(tmp, d)


os.replace = flaky
try:
    P.save_blob(dst, BLOB)
    chk(dst.read_bytes() == BLOB, "被占 4 次后第 5 次成功（重试生效）",
        f"试了 {state['n']} 次")
except OSError as e:
    chk(False, "被占 4 次后第 5 次成功（重试生效）", str(e))

# 3. 一直被占 → 覆写兜底：内容照样写进去，tmp 清掉，不抛错
os.replace = lambda tmp, d: (_ for _ in ()).throw(PermissionError(5, "拒绝访问"))
try:
    P.save_blob(dst, BLOB)
    chk(dst.read_bytes() == BLOB, "replace 全被占 → 覆写兜底把数据写进去了")
    chk(not dst.with_name(dst.name + ".tmp").exists(), "兜底成功后 tmp 也清掉")
except OSError as e:
    chk(False, "replace 全被占 → 覆写兜底把数据写进去了", str(e))

# 4. 连覆写都不行（目录锁死）→ 至少把错误抛出来，别静默假成功
import tempfile                                        # noqa: E402
# Windows 上 chmod 目录不拦写入，改用「父路径是个文件」：tmp 建不出来
blocked = work / "f.dat"
blocked.write_bytes(b"not a dir")
try:
    P.save_blob(blocked / "x.dat", b"x")
    chk(False, "路径根本写不了时要报错（不能静默假成功）")
except OSError:
    chk(True, "路径根本写不了时要报错（不能静默假成功）")
os.replace = real_replace

# 5. ProfileStore.save 真的走了 save_blob（防以后有人改回直连 replace）
src = (HERE / "profiles.py").read_text(encoding="utf-8")
chk("save_blob(self.path, blob)" in src, "ProfileStore.save 用 save_blob")
import re as _re                                        # noqa: E402
code = "\n".join(ln for ln in src.splitlines()
                 if not ln.strip().startswith("#")
                 and "`os.replace`" not in ln)
hits = _re.findall(r"(?m)^.*os\.replace.*$", code)
chk(len(hits) == 1, "profiles.py 里真正的 replace 只在 save_blob 里",
    str(hits)[:80])

print(f"\n结果：{PASS[0]} 通过 / {len(FAIL)} 失败")
if FAIL:
    for f in FAIL:
        print("  · " + f)
    sys.exit(1)
