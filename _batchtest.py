# -*- coding: utf-8 -*-
"""脚本库批量执行 + 结果对比（R-005）。

不连真主机：用假会话（同 `.run(cmd, timeout)` 接口）验「跑」与「比」。
真机那一段（多台 SSH 并发）留给手动验收，判据与这里一致。

    python _batchtest.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import batchrun as B                 # noqa: E402

OK = 0
BAD = 0


def chk(cond, msg, extra=None):
    global OK, BAD
    if cond:
        OK += 1
        print(f"  [ok] {msg}" + (f"  —— {extra}" if extra not in (None, "") else ""))
    else:
        BAD += 1
        print(f"  [NG] {msg}" + (f"  —— {extra}" if extra not in (None, "") else ""))


def sec(t):
    print(f"\n-- {t}")


class FakeSess:
    def __init__(self, out="", rc=0, delay=0.0, boom=False, err=""):
        self.out, self.rc, self.delay = out, rc, delay
        self.boom, self.err = boom, err
        self.calls = 0

    def run(self, cmd, timeout=180):
        self.calls += 1
        time.sleep(self.delay)
        if self.boom:
            raise OSError("connection reset by peer")
        return self.rc, self.out, self.err


def res(label, out="", rc=0, err="", error="", sid=""):
    return B.HostResult(label=label, sid=sid or label, rc=rc, out=out,
                        err=err, error=error)


sec("1. 规范化：去噪声，不去真差异")
chk(B.normalize("a  \nb\t\n\n\n") == "a\nb", "行尾空白与末尾空行去掉")
chk(B.normalize("  a\nb") == "  a\nb", "行首空白保留（那是真差异）")
chk(B.normalize("A\nB") != B.normalize("a\nb"), "大小写不忽略")
chk(B.normalize("") == "", "空输出还是空")

sec("2. 对比分组")
r_same = [res("甲", "ok\n"), res("乙", "ok  \n\n"), res("丙", "ok")]
c = B.compare(r_same)
chk(c["all_same"], "行尾空白不同不算不同（三台一致）", c["summary"])
chk(all(v == "same" for v in c["status"].values()), "全部标 same")

r_mix = [res("甲", "ok"), res("乙", "ok"), res("丙", "不一样")]
c2 = B.compare(r_mix)
chk(not c2["all_same"], "有一台不同 → 不是全同")
chk(c2["status"]["甲"] == "same" and c2["status"]["乙"] == "same",
    "多数派两台标 same")
chk(c2["status"]["丙"] == "diff", "少数派标 diff")
chk("1 台输出不同" in c2["summary"], "汇总人话", c2["summary"])

r_fail = [res("甲", "ok"), res("乙", rc=2, err="boom"), res("丙", error="超时")]
c3 = B.compare(r_fail)
chk(c3["status"]["乙"] == "fail" and c3["status"]["丙"] == "fail",
    "退出码非 0 与本地异常都算失败")
chk(c3["status"]["甲"] == "same", "唯一成功的标 same（没有对比对象）")
chk("2 台失败" in c3["summary"], "失败数进汇总", c3["summary"])
chk(B.compare([])["summary"] == "没有可对比的结果", "空结果不装出结论")

sec("3. 并发跑：一台炸不拖累整批")
targets = [("甲", "s1", FakeSess("ok", delay=0.35)),
           ("乙", "s2", FakeSess("ok", delay=0.35)),
           ("丙", "s3", FakeSess(boom=True))]
t0 = time.monotonic()
rs = B.run_batch(targets, "echo ok", timeout=5)
dt = time.monotonic() - t0
chk(len(rs) == 3, "三台都有结果")
chk(dt < 0.65, "并发而非串行（3×0.35s 串行要 1.05s）", f"{dt:.2f}s")
chk([r.label for r in rs] == ["甲", "乙", "丙"], "结果按传入顺序摆回（不是完成序）")
chk(rs[2].rc == -1 and "connection reset" in rs[2].error,
    "抛异常的那台变成一行失败结果", rs[2].error)
chk(rs[0].ok and rs[1].ok, "其它两台不受影响")
chk(all(t[2].calls == 1 for t in targets), "每台只跑一次")
chk(B.run_batch([], "x") == [], "空目标直接返回")

sec("4. 逐行对比报告")
rep = B.diff_report([res("甲", "a\nb\nc"), res("乙", "a\nX\nc")])
chk("1 行不同" in rep and "2\tb\tX" in rep, "只列差异行，带行号与两台的值", rep)
chk(B.diff_report(r_same) == "所有主机输出一致。", "全同一句话")
chk("不足两台" in B.diff_report([res("甲", "a")]), "成功不足两台不硬比")

sec("5. 界面接线（静态）")
src = (HERE / "pages" / "script_page.py").read_text(encoding="utf-8")
chk("批量执行" in src and "script_batch" in src, "工具条与右键菜单都有批量执行")
chk("sl.alive()" in src, "只取活着的已连接会话")
chk("len(slots) < 2" in src, "不足两台给提示而不是硬跑")
chk("forms.confirm" in src and "danger=True" in src, "跑之前有危险确认")

print(f"\n合计 {OK + BAD} 项：通过 {OK}，失败 {BAD}")
sys.exit(1 if BAD else 0)
