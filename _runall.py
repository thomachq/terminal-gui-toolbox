# -*- coding: utf-8 -*-
"""统一跑自测：一次把所有 `_*test*.py` / `_*smoke*.py` 跑完，最后给总账。

为什么要有它（这是交接说明 §7-1 那条欠账）：60+ 个自测脚本，发版时靠人工
逐个敲命令，漏一个没人知道 —— 而「漏跑」和「跑过且通过」在记录里长得一样。
这个运行器把「哪些脚本存在」变成清单，「哪些跑了、通过几项」变成数字。

用法：

    python _runall.py --list                # 只看清单（带 GUI / 手动 标记）
    python _runall.py --fast                # 跳过 GUI（要开窗口的那种），最快
    python _runall.py --only caps vision    # 只跑名字里含 caps 或 vision 的
    python _runall.py --skip dock           # 跳过名字含 dock 的
    python _runall.py                       # 全跑（会逐个弹窗，慢）
    python _runall.py --timeout 600         # 单个脚本超时（默认 300 秒）

退出码：全绿 0；有失败或超时 1（可直接接进发布链的闸门）。

跳过规则（三条，都能解释，不是"跑不动就跳过"）：
  · 脚本头部注释 `# RUNALL: manual` —— 需要真设备/真 Key，人工触发；
  · 名字里带 `try`（`_visiontry.py`、`_regiontry.py` 这类实验脚本）；
  · `--fast` 时跳过 GUI（要开 Tk 窗口的），它们慢且会抢焦点。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMEOUT = 300

#: (正则, 第二组是"失败数"还是"总数")
#: 这里踩过一次：`全部通过：36/36` 的第二个数字是**总数**，不是失败数。
#: 当成失败数会得出「36/72」，把 10 个全绿的脚本判成一半失败。
PASS_RE = [
    (re.compile(r"通过\s*(\d+)\D{0,6}失败\s*(\d+)"), "fail"),   # 合计 N 项：通过 X，失败 Y
    (re.compile(r"(\d+)\s*通过\s*/\s*(\d+)\s*失败"), "fail"),   # === 58 通过 / 0 失败
    (re.compile(r"全部通过[：:]\s*(\d+)\s*/\s*(\d+)"), "total"),  # 全部通过：36/36
]


def discover() -> list[Path]:
    """仓库根下 `_` 开头的自测脚本（test / smoke 两种命名）。"""
    out = []
    for p in sorted(HERE.glob("_*.py")):
        name = p.name.lower()
        if "test" in name or "smoke" in name:
            out.append(p)
    return out


def head_text(p: Path, limit: int = 4000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def is_gui(p: Path) -> bool:
    """要开真窗口的脚本：跑起来会抢焦点，--fast 时跳过。"""
    head = head_text(p)
    return ("ToolboxApp(" in head) or ("tkinter" in head and "Tk()" in head)


def is_manual(p: Path) -> bool:
    """需要真设备/真 Key 的人工脚本（脚本自己用注释声明）。"""
    return "# RUNALL: manual" in head_text(p)


def is_experiment(p: Path) -> bool:
    """`_xxxxtry.py` 这类一次性实验脚本，不是闸门。"""
    return "try" in p.stem.lower()


def parse_result(text: str) -> tuple[int, int] | None:
    """从输出末尾找「通过 X，失败 Y」。认不出返回 None（不猜）。"""
    tail = "\n".join(text.strip().splitlines()[-10:])
    for rx, kind in PASS_RE:
        m = rx.search(tail)
        if not m:
            continue
        passed = int(m.group(1))
        second = int(m.group(2))
        failed = second - passed if kind == "total" else second
        return passed, max(0, failed)
    return None


def run_one(p: Path, timeout: int) -> dict:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    t0 = time.time()
    try:
        cp = subprocess.run([sys.executable, p.name], cwd=str(HERE),
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout, env=env)
        out = (cp.stdout or "") + (cp.stderr or "")
        nums = parse_result(out)
        passed, failed = nums if nums else (0, 1 if cp.returncode else 0)
        if nums is None and cp.returncode == 0:
            # 认不出数字但退出码 0：算过，但标成「无统计」
            return {"name": p.name, "ok": True, "passed": 0, "failed": 0,
                    "ms": int((time.time() - t0) * 1000), "note": "无统计",
                    "out": out}
        return {"name": p.name, "ok": failed == 0 and cp.returncode == 0,
                "passed": passed, "failed": failed,
                "ms": int((time.time() - t0) * 1000),
                "note": "" if nums else f"rc={cp.returncode}", "out": out}
    except subprocess.TimeoutExpired:
        return {"name": p.name, "ok": False, "passed": 0, "failed": 1,
                "ms": int((time.time() - t0) * 1000),
                "note": f"超时 {timeout}s", "out": ""}
    except Exception as e:  # noqa: BLE001
        return {"name": p.name, "ok": False, "passed": 0, "failed": 1,
                "ms": 0, "note": f"{type(e).__name__}: {e}", "out": ""}


def main() -> int:
    ap = argparse.ArgumentParser(description="统一跑自测脚本")
    ap.add_argument("--list", action="store_true", help="只列清单")
    ap.add_argument("--fast", action="store_true", help="跳过要开窗口的 GUI 自测")
    ap.add_argument("--only", nargs="*", default=[], help="只跑名字含这些词的")
    ap.add_argument("--skip", nargs="*", default=[], help="跳过名字含这些词的")
    ap.add_argument("--timeout", type=int, default=TIMEOUT, help="单个脚本超时秒数")
    a = ap.parse_args()

    allp = discover()
    picked: list[tuple[Path, str]] = []      # (路径, 跳过原因或空)
    for p in allp:
        name = p.name
        reason = ""
        if is_experiment(p):
            reason = "实验脚本"
        elif is_manual(p):
            reason = "需真设备/真Key"
        elif a.fast and is_gui(p):
            reason = "GUI（--fast 跳过）"
        if a.only and not any(k.lower() in name.lower() for k in a.only):
            continue
        if any(k.lower() in name.lower() for k in a.skip):
            continue
        picked.append((p, reason))

    runnable = [(p, r) for p, r in picked if not r]
    skipped = [(p, r) for p, r in picked if r]

    if a.list:
        print(f"共 {len(allp)} 个自测脚本，按筛选后 {len(picked)} 个：")
        for p, r in picked:
            print(f"  {'跳过' if r else '跑  '}  {p.name:<34} {r}")
        return 0

    print(f"要跑 {len(runnable)} 个，跳过 {len(skipped)} 个（--fast={a.fast}）")
    print("-" * 66)
    results = []
    for p, _r in runnable:
        res = run_one(p, a.timeout)
        results.append(res)
        mark = "ok  " if res["ok"] else "FAIL"
        cnt = (f"{res['passed']}/{res['passed'] + res['failed']}"
               if (res["passed"] or res["failed"]) else "-")
        extra = f"  {res['note']}" if res["note"] else ""
        print(f"[{mark}] {p.name:<34} {cnt:>8}  {res['ms'] / 1000:5.1f}s{extra}",
              flush=True)

    bad = [r for r in results if not r["ok"]]
    total_pass = sum(r["passed"] for r in results)
    total_fail = sum(r["failed"] for r in results)
    print("-" * 66)
    print(f"合计 {len(results)} 个脚本：通过 {len(results) - len(bad)}，"
          f"失败 {len(bad)}；断言 {total_pass} 通过 / {total_fail} 失败")
    if bad:
        print("失败清单：")
        for r in bad:
            print(f"  - {r['name']}  {r['note'] or ''}")
            tail = "\n".join((r.get("out") or "").strip().splitlines()[-6:])
            for ln in tail.splitlines():
                print("      " + ln[:110])
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
