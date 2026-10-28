# -*- coding: utf-8 -*-
"""`--version` 的闸门。

起因（用户 2026-10-29 01:32）：发 v1.0.31 时想拿 `TerminalToolbox.exe --version`
核对版本，结果「无输出、exit 1」。查下来 `--version` 只出现在 `_NO_SINGLETON`
元组里、**根本没有处理分支**，所以它会照常起 GUI；而 windowed 的 onefile exe
没有控制台，`sys.stdout` 是 None，即使加了 print 也看不见 —— 两层坑叠在一起。

这个脚本钉住四件事：
  A. 源码方式 `app.py --version` 打一行就退（不建窗口、不建 data/）；
  B. 处理分支必须排在 `DATA_DIR.mkdir` 之前（只读参数不该改动用户目录）；
  C. `_emit_version()` 的三段兜底：现成 stdout → AttachConsole → False；
  D. 打包后的 exe 也真能输出（这条才是用户遇到的症状，源码能跑不算数）。
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

EXE = HERE / "TerminalToolbox.exe"
SRC = HERE / "app.py"

_p = _f = 0


def chk(ok, label, extra=""):
    global _p, _f
    if ok:
        _p += 1
        print(f"  ok   {label}")
    else:
        _f += 1
        print(f"  FAIL {label}" + (f"  → {extra}" if extra else ""))


def sec(t):
    print(f"\n{t}")


ver = re.search(r'^APP_VER = "([^"]+)"', SRC.read_text(encoding="utf-8"), re.M)
chk(bool(ver), "app.py 里能读到 APP_VER", "正则没命中")
VER = ver.group(1) if ver else "?"

# ---------------------------------------------------------------- A
sec("A. 源码方式：只打一行就退出")
t0 = time.time()
r = subprocess.run([sys.executable, str(SRC), "--version"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", cwd=str(HERE), timeout=90)
cost = time.time() - t0
chk(r.returncode == 0, "退出码 0", f"rc={r.returncode} err={r.stderr[:200]}")
chk(r.stdout.strip() == f"Terminal GUI Toolbox v{VER}",
    "stdout 恰好是「Terminal GUI Toolbox v<版本>」", repr(r.stdout[:120]))
# 起 GUI 至少要建 Tk、读配置，通常 3s 以上；正常这里 0.5s 内退
chk(cost < 15, f"没有卡在 GUI 上（{cost:.1f}s）")
chk("Traceback" not in r.stderr, "stderr 没有异常", r.stderr[:200])

# ---------------------------------------------------------------- B
sec("B. 分支位置：必须排在 DATA_DIR.mkdir 之前")
src = SRC.read_text(encoding="utf-8")
i_ver = src.find('if "--version" in sys.argv:')
i_mkdir = src.find("DATA_DIR.mkdir(parents=True, exist_ok=True)", i_ver - 4000)
chk(i_ver > 0, "找得到 --version 分支")
chk(i_mkdir > 0 and i_ver < i_mkdir,
    "分支在 DATA_DIR.mkdir 之前（--version 不建 data/）",
    f"branch@{i_ver} mkdir@{i_mkdir}")
# 注意别写 `"x" in y is False` —— 那是链式比较（`("x" in y) and (y is False)`），
# 字符串永远 `is not False`，整个表达式恒假，只能靠后半段碰巧通过。
seg_br = src[i_ver:i_mkdir]
chk("app = ToolboxApp()" not in seg_br,
    "分支里没有建 Tk 应用（建 app 之前就 return）", "仍会起 GUI")

# ---------------------------------------------------------------- C
sec("C. _emit_version 的三段兜底")
seg = src[src.find("def _emit_version"):src.find("def main()")]
chk("def _emit_version" in src, "_emit_version 存在")
chk("out = sys.stdout" in seg, "先试现成的 sys.stdout")
chk("AttachConsole" in seg, "再挂父进程控制台（windowed 没控制台时唯一出路）")
chk("return False" in seg, "两条路都不通时返回 False，由调用方决定怎么显示")
chk("AttributeError" in seg, "吞得掉「windowed 下 sys.stdout 是 None」那类异常")

# ---------------------------------------------------------------- D
sec("D. 打包后的 exe（用户实际踩的就是这里）")
if not EXE.is_file():
    print("  skip 没有 TerminalToolbox.exe，跳过打包后校验")
else:
    t1 = time.time()
    r = subprocess.run([str(EXE), "--version"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(HERE),
                       timeout=180)
    exe_cost = time.time() - t1
    chk(r.returncode == 0, "exe --version 退出码 0", f"rc={r.returncode}")
    chk(r.stdout.strip() == f"Terminal GUI Toolbox v{VER}",
        "exe 的输出带当前版本号", repr(r.stdout[:120]))
    # onefile 首次运行会被 Defender 扫，几十秒都可能；这里只拦「挂死」
    chk(exe_cost < 90, f"exe 没有长时间挂住（{exe_cost:.1f}s）")

    r2 = subprocess.run([str(EXE), "--smoke"], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", cwd=str(HERE),
                        timeout=300)
    chk(r2.returncode == 0, "exe --smoke 仍然 exit 0", f"rc={r2.returncode}")

# ---------------------------------------------------------------- E
sec("E. 别再退回「只挂在 _NO_SINGLETON 里」")
m = re.search(r"_NO_SINGLETON = \(([^)]*)\)", src)
chk(bool(m), "找到 _NO_SINGLETON")
if m:
    chk('"--version"' in m.group(1) or "'--version'" in m.group(1),
        "--version 仍在免单实例名单里（双保险，不靠它兜底也行）")
chk(src.count('if "--version" in sys.argv:') == 1,
    "只有一个 --version 处理点（不会两处打架）",
    str(src.count('if "--version" in sys.argv:')))

print(f"\n结果：{_p} 通过 / {_f} 失败")
if _f:
    print(" 失败项见上面的 FAIL 行")
sys.exit(1 if _f else 0)
