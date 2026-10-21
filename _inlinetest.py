# -*- coding: utf-8 -*-
"""终端两种输入形态的自测：输入框 / 行内，以及切换时的搬运。

重点验四件事：
  1. 行内的命令**不会被重复回显**（正文里那条就是用户敲的）；
  2. 命令输出不会冲掉正在敲的内容；
  3. 光标/退格翻不过提示符，删不掉提示符；
  4. 切形态时已经敲了一半的内容要跟着搬过去。
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import wbtui as U                     # noqa: E402
from core.session import ConnSpec, open_session   # noqa: E402
from _termsmoke import FakeCtrl, pump, wait_idle  # noqa: E402

passed = failed = 0


def chk(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {name}" + (f"  · {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL {name}" + (f"  <- {detail}" if detail else ""))


def body(w) -> str:
    return w.text.get("1.0", "end")


def main() -> int:
    root = tk.Tk()
    root.withdraw()
    U.configure_ttk(root)

    s = open_session(ConnSpec(proto="local"))
    ctrl = FakeCtrl(s)

    import ui.terminal as T
    print("\n== 1. 默认形态 ==")
    w = T.TerminalWindow(root, ctrl, mode="box")
    pump(root, 0.4)
    chk(w.mode == "box", "默认是输入框", w.mode)
    chk(w.row.winfo_manager() == "pack", "输入行可见")
    chk(str(w.text.cget("state")) == "disabled", "正文只读")
    chk("prompt" not in w.text.mark_names(), "正文里没有提示符行")

    print("\n== 2. 切到行内 ==")
    w.seg_mode.select("inline")
    pump(root, 0.2)
    chk(w.mode == "inline", "模式已切到行内", w.mode)
    chk(w.row.winfo_manager() == "", "输入行已收起", repr(w.row.winfo_manager()))
    chk(str(w.text.cget("state")) == "normal", "正文可编辑")
    chk("prompt" in w.text.mark_names(), "正文里有提示符标记")
    chk("input_start" in w.text.mark_names(), "正文里有输入起点标记")
    line_end = w.text.get("prompt", "end-1c")
    chk(line_end.strip().endswith(">"), "提示符行在末尾", line_end[-40:])

    print("\n== 3. 行内执行一条命令 ==")
    w.text.insert("input_start", "echo inline-hello")
    w._submit()
    wait_idle(root, w)
    txt = body(w)
    chk("命令被收进正文", "echo inline-hello" in txt)
    chk("回显只有一份", txt.count("echo inline-hello") == 1,
        str(txt.count("echo inline-hello")))
    chk("输出回来了", "inline-hello" in txt)
    chk("执行完回到空闲", not w._busy)
    chk("提示符又回到末尾", w.text.get("input_start", "end-1c") == "",
        repr(w.text.get("input_start", "end-1c")))
    n_lines = int(w.text.index("end-1c").split(".")[0])
    chk("输出在提示符之前（提示符是最后一行）",
        int(w.text.index("prompt").split(".")[0]) == n_lines,
        f"prompt@{w.text.index('prompt')} / 共 {n_lines} 行")

    print("\n== 4. 敲一半时有输出进不来 ==")
    w.text.insert("input_start", "echo HALF")
    pump(root, 0.1)
    w.feed("ls -l", 0, "total 0\n", "", 9)      # 主程序旁路插一条
    pump(root, 0.2)
    chk("旁路输出插进来了", "total 0" in body(w))
    chk("正在敲的内容没被冲掉",
        w.text.get("input_start", "end-1c") == "echo HALF",
        repr(w.text.get("input_start", "end-1c")))
    w.text.delete("input_start", "end-1c")       # 收拾干净

    print("\n== 5. 光标与退格不许越过提示符 ==")
    w.text.mark_set("insert", "1.0")
    w._clamp_key()
    chk("光标被拉回输入区",
        w.text.compare("insert", ">=", "input_start") == 1,
        w.text.index("insert"))
    w.text.mark_set("insert", "input_start")
    r = w._clamp_key()
    chk("退格在输入区起点被挡住", w.text.compare("insert", ">=", "input_start") == 1)
    chk("提示符正文没被删", w.text.get("prompt", "input_start") != "",
        repr(w.text.get("prompt", "input_start")))

    print("\n== 6. 历史 / 内建 / cd ==")
    w.text.insert("input_start", "echo SECOND")
    w._submit()
    wait_idle(root, w)
    chk("第二条也执行了", "SECOND" in body(w))
    w._hist_prev()
    chk("↑ 把上一条填进输入区",
        w.text.get("input_start", "end-1c") == "echo SECOND",
        repr(w.text.get("input_start", "end-1c")))
    w._hist_prev()
    chk("再按一次是更早那条",
        w.text.get("input_start", "end-1c") == "echo inline-hello",
        repr(w.text.get("input_start", "end-1c")))
    w.text.delete("input_start", "end-1c")

    w.text.insert("input_start", "history")
    w._submit()
    pump(root, 0.3)
    chk("history 有内容", "echo inline-hello" in body(w))

    w.text.insert("input_start", "clear")
    w._submit()
    pump(root, 0.3)
    leftover = body(w).strip()
    chk("clear 清干净（只剩一条提示符）", leftover == w._prompt_str.strip(),
        repr(leftover[:40]))
    chk("clear 之后提示符还在", "prompt" in w.text.mark_names())
    w.text.insert("input_start", "echo AFTER-CLEAR")
    w._submit()
    wait_idle(root, w)
    chk("clear 之后还能执行", "AFTER-CLEAR" in body(w))

    # cd 之后正文里的提示符要跟着换目录
    before_ps = w.text.get("prompt", "input_start")
    w.text.insert("input_start", "cd C:\\")
    w._submit()
    pump(root, 0.6)
    after_ps = w.text.get("prompt", "input_start")
    chk("cd 之后提示符变了", before_ps != after_ps, f"{before_ps[-12:]!r} -> {after_ps[-12:]!r}")
    chk("cd 之后仍能输入", w.text.get("input_start", "end-1c") == "",
        repr(w.text.get("input_start", "end-1c")))

    print("\n== 7. 行内也脱敏 ==")
    key = ("-----BEGIN OPENSSH PRIVATE KEY-----\n"
           "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ\n"
           "-----END OPENSSH PRIVATE KEY-----")
    w.write("$ cat key\n" + key + "\n", "out")
    pump(root, 0.2)
    txt = body(w)
    chk("私钥正文没进正文", "b3BlbnNzaC1rZXktdjEAAAAA" not in txt)
    chk("打码回执在", "私钥内容已隐藏" in txt)

    print("\n== 8. 切回输入框：内容跟着搬 ==")
    w.text.insert("input_start", "echo KEEP-ME")
    w.seg_mode.select("box")
    pump(root, 0.2)
    chk("模式切回输入框", w.mode == "box")
    chk("正文里的提示符行被移除", "prompt" not in w.text.mark_names())
    chk("正文回到只读", str(w.text.cget("state")) == "disabled")
    chk("输入行回来了", w.row.winfo_manager() == "pack")
    chk("敲了一半的内容搬进输入框", w.v_in.get() == "echo KEEP-ME",
        repr(w.v_in.get()))
    w._submit()
    wait_idle(root, w)
    chk("搬过来的命令能执行", "KEEP-ME" in body(w))

    print("\n== 9. 记忆上次选择 ==")
    w.seg_mode.select("inline")
    pump(root, 0.2)
    chk("模块级记住了行内", T._LAST_MODE == "inline", T._LAST_MODE)
    w.close()
    pump(root, 0.2)
    w2 = T.TerminalWindow(root, ctrl)
    pump(root, 0.4)
    chk("新窗口沿用行内", w2.mode == "inline", w2.mode)
    w2.seg_mode.select("box")
    pump(root, 0.2)
    w2.close()

    try:
        root.destroy()
    except tk.TclError:
        pass
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
