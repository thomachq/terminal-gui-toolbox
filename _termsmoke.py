# -*- coding: utf-8 -*-
"""终端窗口自测：打开 → 敲命令 → 看回显 → 主程序命令旁路 → 主题切换 → 关闭。"""
from __future__ import annotations

import sys
import time
import tkinter as tk

import wbtui as U
from core.session import ConnSpec, LocalSession, open_session

PASS = FAIL = 0
BAD: list[str] = []


def chk(ok, name, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok   {name}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL += 1
        BAD.append(name)
        print(f"  FAIL {name}  [{detail}]")


def pump(root, sec=0.2):
    end = time.time() + sec
    while time.time() < end:
        try:
            root.update()
        except tk.TclError:
            return
        time.sleep(0.01)


def wait_idle(root, w, sec=10.0):
    """等到终端回到空闲。

    固定 pump(N) 是这张自测最不稳的地方：本机会话底下是 PowerShell，
    冷启动偶尔要两三秒，1.6s 的窗口就会假失败（正文里那条其实是 echo 的回显）。
    改成轮询 _busy，sec 只是兜底。
    """
    end = time.time() + sec
    while time.time() < end:
        try:
            root.update()
        except tk.TclError:
            return
        if not w._busy:
            pump(root, 0.15)
            return
        time.sleep(0.01)


class FakeCtrl:
    """冒充 ToolboxApp，只实现终端要的那几个方法。"""

    def __init__(self, session):
        self.s = session
        self.cwd = ""
        self.closed = None

    def term_ready(self):
        return self.s is not None and self.s.alive

    def term_describe(self):
        return "未连接" if self.s is None else "自测会话 · LOCAL"

    def term_cwd(self):
        if self.s is None:
            return "C:////"
        if not self.cwd:
            rc, out, err = self.s.run("pwd", timeout=8)
            lines = [x.strip() for x in (out or "").splitlines() if x.strip()]
            self.cwd = lines[-1] if lines else "C:\\"
        return self.cwd

    def term_prompt(self):
        return f"{self.term_cwd()}> "

    def term_chdir(self, path):
        import os
        p = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(p):
            self.cwd = p
            return True, p
        return False, "目录不存在"

    def term_run(self, cmd, done):
        import threading

        def work():
            t0 = time.time()
            rc, out, err = self.s.run(cmd, timeout=60)
            done(rc, out, err, int((time.time() - t0) * 1000))

        threading.Thread(target=work, daemon=True).start()

    def term_closed(self, w):
        self.closed = w


def main():
    root = tk.Tk()
    root.withdraw()
    U.configure_ttk(root)

    # ---------------- 后端
    print("\n1. 会话")
    s = open_session(ConnSpec(proto="local"))
    chk(s.alive, "本机会话已建立", s.label)
    ctrl = FakeCtrl(s)

    # ---------------- 窗口
    print("\n2. 窗口构建")
    from ui.terminal import TerminalWindow

    w = TerminalWindow(root, ctrl)
    pump(root, 0.4)
    chk(w.winfo_exists() == 1, "窗口已创建")
    chk(w.lbl_dev.cget("text").endswith("LOCAL"), "标题栏带设备名",
        w.lbl_dev.cget("text"))
    chk(w.title().startswith("终端"), "窗口标题", w.title())
    chk(w.entry.winfo_ismapped() == 1, "输入行可见",
        f"{w.entry.winfo_width()}x{w.entry.winfo_height()}")
    chk(w.text.winfo_ismapped() == 1, "正文可见",
        f"{w.text.winfo_width()}x{w.text.winfo_height()}")
    chk(U.MONO[0] in str(w.text.cget("font")), "正文是等宽字体",
        str(w.text.cget("font")))
    body = w.text.get("1.0", "end")
    chk("终端已就绪" in body, "打开时给了说明")
    chk("help" in body, "说明了内建命令")

    # ---------------- 敲命令
    print("\n3. 手动执行")
    w.v_in.set("echo terminal-hello")
    w._submit()
    wait_idle(root, w)
    body = w.text.get("1.0", "end")
    chk("terminal-hello" in body, "命令输出回来了", body.strip().splitlines()[-1][:60])
    chk(body.count("echo terminal-hello") >= 1, "命令被回显")
    chk(not w._busy, "执行完回到空闲")
    chk(w.lbl_ps1.cget("fg") == U.T.ok, "提示符颜色复位")

    # 历史
    w._hist_prev()
    chk(w.v_in.get() == "echo terminal-hello", "↑ 翻出上一条", w.v_in.get())
    w.v_in.set("")

    # 失败命令
    w.v_in.set("this-command-does-not-exist-xyz")
    w._submit()
    wait_idle(root, w)
    body = w.text.get("1.0", "end")
    chk("rc=" in body or "无法将" in body or "not recognized" in body.lower()
        or "not found" in body.lower(), "失败命令给了回执")

    # 内建
    print("\n4. 内建命令")
    n_before = len(w.text.get("1.0", "end").strip().splitlines())
    w.v_in.set("history")
    w._submit()
    pump(root, 0.3)
    body = w.text.get("1.0", "end")
    chk("echo terminal-hello" in body, "history 列出历史")
    w.v_in.set("help")
    w._submit()
    pump(root, 0.3)
    chk("内建命令" in w.text.get("1.0", "end"), "help 打在正文里")

    # cd
    print("\n5. cd")
    import os
    target = os.environ.get("SystemDrive", "C:") + "\\"
    w.v_in.set(f"cd {target}")
    w._submit()
    pump(root, 0.4)
    chk(ctrl.cwd.rstrip("\\").lower() == target.rstrip("\\").lower(),
        "cd 成功切目录", ctrl.cwd)
    w.v_in.set("cd Z:\\definitely-not-here")
    w._submit()
    pump(root, 0.4)
    chk("目录不存在" in w.text.get("1.0", "end") or "Cannot find" in
        w.text.get("1.0", "end") or "找不到" in w.text.get("1.0", "end"),
        "cd 到不存在的目录会报错")

    # ---------------- 旁路
    print("\n6. 主程序命令旁路")
    w.clear()
    w.feed("ip route", 0, "default via 10.0.0.1\n", "", 42)
    pump(root, 0.2)
    body = w.text.get("1.0", "end")
    chk("▸ ip route" in body, "程序命令带 ▸ 前缀")
    chk("default via 10.0.0.1" in body, "程序输出进来了")
    import re as _re
    chk(bool(_re.match(r"\d\d:\d\d:\d\d", body.lstrip())), "带时间戳",
        body[:12])

    w._sync.set(False)
    w.clear()
    w.feed("secret-cmd", 0, "should-not-show\n", "", 1)
    pump(root, 0.2)
    chk("should-not-show" not in w.text.get("1.0", "end"),
        "关掉「同步」后不再收程序命令")
    w._sync.set(True)

    # 自己的命令不该被旁路重复
    w.clear()
    w._own = True
    w.feed("echo x", 0, "x\n", "", 5)
    pump(root, 0.1)
    chk("▸" not in w.text.get("1.0", "end"), "自己执行时不重复回显")
    w._own = False

    # ---------------- 主题
    print("\n7. 主题")
    bg1 = w.text.cget("bg")
    U.toggle_theme()
    w._paint()
    pump(root, 0.3)
    bg2 = w.text.cget("bg")
    chk(bg1 != bg2, "切换主题后正文换底色", f"{bg1} -> {bg2}")
    chk(w.text.tag_cget("err", "foreground") == U.T.err, "错误色跟着主题走")
    U.toggle_theme()
    w._paint()
    pump(root, 0.2)

    # ---------------- 未连接
    print("\n8. 未连接状态")
    ctrl.s = None
    pump(root, 0.2)
    w2 = TerminalWindow(root, ctrl)
    pump(root, 0.4)
    chk("未连接" in w2.title(), "标题写未连接", w2.title())
    chk("当前未连接" in w2.text.get("1.0", "end"), "正文提示先连设备")
    w2.v_in.set("ls")
    w2._submit()
    pump(root, 0.3)
    chk("未连接" in w2.text.get("1.0", "end"), "敲命令会被拦住")

    # ---------------- 关闭
    print("\n9. 关闭")
    w2.close()
    pump(root, 0.2)
    chk(ctrl.closed is w2, "关闭时通知了主程序")

    # 长输出不卡死
    print("\n10. 长输出")
    from ui import terminal as T
    w3 = TerminalWindow(root, ctrl)
    pump(root, 0.3)
    for i in range(120):
        w3.write(f"line {i}\n", "out")
    pump(root, 0.6)
    n = int(w3.text.index("end-1c").split(".")[0])
    chk(n > 100, "能写入上百行", f"{n} 行")
    w3.clear()
    chk(w3.text.get("1.0", "end").strip() == "", "clear 清干净")
    w3.close()
    pump(root, 0.2)

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    if BAD:
        print(" 失败项：")
        for b in BAD:
            print("  ·", b)
    try:
        root.destroy()
    except tk.TclError:
        pass
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
