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


# ================================================================== 5. 策略
sec("5. main() 的单实例策略（测试实例不许绕过去）")

import re  # noqa: E402

src = (HERE / "app.py").read_text(encoding="utf-8")
m = re.search(r"_NO_SINGLETON\s*=\s*\(([^)]*)\)", src)
names = re.findall(r'"([^"]+)"', m.group(1)) if m else []
# 2026-10-25 用户反馈「任务栏都是终端工具箱的托盘」：根因就是原来把
# --page / --no-probe / --dark 一起放进放行名单，于是测试实例能和用户那个
# 并存，托盘里堆出好几个图标。现在只有「根本不出窗口」的参数才放行。
chk(names == ["--smoke", "--version"],
    f"只放行不出窗口的参数（实际 {names}）", str(names))
for a in ("--page", "--no-probe", "--dark"):
    chk(a not in names, f"{a} 不再绕过单实例检查")
chk("key_from_argv" in src, "main() 按 --instance-key 取命名空间")
chk("singleinstance.acquire(key)" in src, "抢锁带上了 key")
chk("singleinstance.wake(key)" in src, "叫醒用的是同一个 key")
chk("key_from_argv(sys.argv)" in src, "唤醒轮询也用同一个 key（不会串台）")

sec("5b. 被拦下时只叫醒、不自己再开一个")
# 「把已有窗口叫到前台」是按标题找的，不认命名空间 —— 测试实例要是也走这条
# 就会把用户正在用的窗口拉到前面。只有默认命名空间才准动别人的窗口。
chk("is_default = key == singleinstance.DEFAULT_KEY" in src,
    "认得「这是默认命名空间」")
chk(re.search(r"if not singleinstance\.acquire\(key\):\s*\n"
              r"\s*singleinstance\.wake\(key\)", src) is not None,
    "抢不到锁时先叫醒对面")
chk(re.search(r"if is_default:\s*\n\s*(?:#[^\n]*\n\s*)*"
              r"singleinstance\.bring_to_front\(APP_NAME\)", src) is not None,
    "只有默认命名空间才去动别人的窗口")
# 被拦下之后必须退出。原来 onefile 的 terminate 只杀 bootloader、真正的子
# 进程带着窗口活下来（2026-10-25 实测：杀完 20 秒仍持锁），所以「对面还活着」
# 是常态而不是异常 —— 这里不需要也不应该等锁重试，否则用户重复双击要白等。
chk(re.search(r"singleinstance\.bring_to_front\(APP_NAME\)\s*\n\s*return", src)
    is not None, "叫醒之后自己退出，不再开第二个窗口")

sec("6. key_from_argv 解析")

chk(SI.key_from_argv(["app.py"]) == SI.DEFAULT_KEY,
    "没有 --instance-key 时用默认命名空间")
chk(SI.key_from_argv(["app.py", "--instance-key", "exe"])
    == SI.DEFAULT_KEY + "-exe", "带值时拼成 <默认>-<值>")
chk(SI.key_from_argv(["app.py", "--instance-key"]) == SI.DEFAULT_KEY,
    "给了开关没给值：回退默认")
chk(SI.key_from_argv(["app.py", "--instance-key", "--page"]) == SI.DEFAULT_KEY,
    "值位置上是另一个开关时不当值")
chk(SI.key_from_argv(["app.py", "--instance-key", " "]) == SI.DEFAULT_KEY,
    "空白值回退默认")
chk(SI.key_from_argv(["app.py", "--instance-key", "exe"]) != SI.DEFAULT_KEY,
    "测试命名空间和用户那个不是同一个（不会互相挡）")


# ================================================================== 汇总
print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
if FAILS:
    for f in FAILS:
        print("   ✗", f)
sys.exit(1 if FAIL else 0)
