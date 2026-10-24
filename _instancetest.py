# -*- coding: utf-8 -*-
"""单实例限制（参考微信）：一个开着就不许再开第二个。

用户原话：
  「对于多进程的问题，你可以做个限制，参考微信，一个进程开着，
    就不允许另一个进程」

分段：
  1. 抢锁语义：第一个拿到，同名再抢被拒（同进程 + 真子进程两种都验）
  2. 不同 key 互不影响（测试不会互相挡）
  3. 叫醒：wake → consume 为真，且是自动重置（第二次为假）
  4. 兜底：找不到窗口时 bring_to_front 老实返回 False
  5. app.main() 里确实对测试参数放行（否则 _texe 逐页起窗会互相挡死）

不需要窗口，不需要联网。
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = FAIL = 0
FAILS: list[str] = []

import core.singleinstance as SI  # noqa: E402


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")
    print(("  ok   " if ok else "  FAIL ") + name
          + (f"  [{extra}]" if extra and not ok else ""))


def sec(t):
    print(f"\n-- {t}")


def run_child(code: str) -> str:
    """在真子进程里跑一段，返回 stdout（strip 过）。"""
    full = f"import sys; sys.path.insert(0, {str(HERE)!r})\n" + code
    r = subprocess.run([sys.executable, "-c", full],
                       capture_output=True, text=True, timeout=60)
    return (r.stdout or "").strip() or (r.stderr or "").strip()


# ================================================================== 1. 抢锁
sec("1. 抢锁：第一个拿到，后面的被拒")

KEY = "TBInstanceTest"
chk(SI.acquire(KEY), "本进程抢到锁")
chk(not SI.acquire(KEY), "同名再抢一次：被拒（同进程）")

out = run_child(
    "import core.singleinstance as S\n"
    f"print(S.acquire({KEY!r}))\n")
chk(out == "False", "真子进程抢同一名字：被拒", out)

out = run_child(
    "import core.singleinstance as S\n"
    "print(S.acquire('TBInstanceFree'))\n")
chk(out == "True", "子进程抢没人占的名字：成功", out)


# ================================================================== 2. 隔离
sec("2. 不同 key 互不影响")

chk(SI.acquire("TBInstanceOther"), "换一个 key：照样能抢到")
chk(SI.acquire(KEY) is False, "原 key 仍被自己占着（没被别的 key 顶掉）")


# ================================================================== 3. 叫醒
sec("3. 叫醒：wake → consume 为真且自动重置")

chk(not SI.consume(KEY), "没人叫过：consume 为假")
chk(SI.wake(KEY), "wake 发得出去")
chk(SI.consume(KEY), "叫过之后 consume 为真")
chk(not SI.consume(KEY), "自动重置：第二次 consume 为假")
chk(not SI.consume("TBInstanceNeverCreated"), "没有事件句柄的 key：安全返回 False")

# 子进程叫醒父进程（真实跨进程）
SI.consume(KEY)
out = run_child(
    "import core.singleinstance as S\n"
    f"print(S.wake({KEY!r}))\n")
chk(out == "True", "子进程能叫醒父进程（SetEvent 成功）", out)
chk(SI.consume(KEY), "父进程确实收到了这条唤醒")


# ================================================================== 4. 兜底
sec("4. 找不到窗口时老实返回 False")

chk(not SI.bring_to_front("这个标题的窗口绝对不存在 8f3a2b"), "不存在的标题：False")
chk(not SI.bring_to_front(""), "空标题：False")


# ================================================================== 5. 放行
sec("5. app.main() 对测试参数放行")

src = (HERE / "app.py").read_text(encoding="utf-8")
for a in ("--smoke", "--page", "--no-probe", "--dark", "--version"):
    chk(f'"{a}"' in src, f"跳过名单里有 {a}")
chk("singleinstance.acquire()" in src, "main() 里确实调了抢锁")
chk("singleinstance.wake()" in src, "抢不到时会叫醒已有实例")
chk("_wake_poll" in src, "已有实例有轮询唤醒的入口")


# ================================================================== 汇总
print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
if FAILS:
    for f in FAILS:
        print("   ✗", f)
sys.exit(1 if FAIL else 0)
