# -*- coding: utf-8 -*-
"""终端与主程序集成：连本机 → 开终端 → 敲命令 → 主程序命令旁路 → 断开联动。"""
from __future__ import annotations

import sys
import time
import tkinter as tk

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


def main():
    from pathlib import Path
    # 别让上一次自测留下的锁拦住宿主；走 _lockoff：备份 + 退出还原
    try:
        import _lockoff

        _lockoff.unlock(Path("data/settings.json"))
    except Exception:  # noqa: BLE001
        pass

    import app as APP
    a = APP.ToolboxApp()
    a.withdraw()
    a.after(10, lambda: pump(a, 0.1))
    pump(a, 0.8)

    print("\n1. 终端入口")
    chk(a._term is None, "默认不打开终端")
    chk(hasattr(a, "btn_term"), "头部有「终端」按钮")
    chk(a.btn_term.winfo_exists() == 1, "按钮已构建")

    print("\n2. 先连本机")
    a.v_proto.set("local")
    a._proto_changed()
    a.do_connect()
    for _ in range(160):
        pump(a, 0.1)
        if a.session is not None:
            break
    chk(a.session is not None, "本机会话已建立",
        a.session.kind if a.session else "-")

    print("\n3. 打开终端")
    a.open_terminal()
    pump(a, 0.6)
    w = a._term
    chk(w is not None and w.winfo_exists() == 1, "终端窗口已出现")
    chk("终端" in w.title(), "标题栏", w.title())
    chk("本机" in w.title() or "LOCAL" in w.title(), "标题带设备", w.title())
    chk(a.term_ready(), "term_ready 为真")
    ps = a.term_prompt()
    chk(ps.endswith("> ") or ps.endswith("$ "), "提示符成型", ps)

    print("\n4. 敲命令")
    w.v_in.set("echo from-terminal")
    w._submit()
    pump(a, 2.2)
    body = w.text.get("1.0", "end")
    chk("from-terminal" in body, "命令真的执行了")
    chk(not w._busy, "回到空闲")

    print("\n5. 主程序命令进终端")
    w.clear()
    a.do_tool("ip")
    pump(a, 4.0)
    body = w.text.get("1.0", "end")
    chk("ipconfig" in body or "以太网" in body or "网卡" in body
        or "IPv4" in body, "工具命令出现在终端", body.strip()[:60])

    print("\n6. 目录")
    a._term_cwd_val = ""
    cwd = a.term_cwd()
    chk(bool(cwd) and (cwd[1:2] == ":" or cwd.startswith("/")), "探测到工作目录", cwd)
    ok, msg = a.term_chdir("C:\\")
    chk(ok, "cd 到盘根", msg)

    print("\n7. 断开联动")
    a._disconnect()
    pump(a, 0.4)
    chk(a._term_cwd_val == "", "断开后目录缓存清空")
    chk("未连接" in w.title(), "终端标题跟着变", w.title())
    w.v_in.set("echo nope")
    w._submit()
    pump(a, 0.5)
    chk("未连接" in w.text.get("1.0", "end"), "断开后敲命令被拦住")

    print("\n8. 再开一次不重复建窗")
    old = a._term
    a.open_terminal()
    pump(a, 0.3)
    chk(a._term is old, "已开着就只前置，不新建")

    print("\n9. 关闭")
    w.close()
    pump(a, 0.3)
    chk(a._term is None, "关掉后主程序不再持有引用")

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    if BAD:
        print(" 失败项：")
        for b in BAD:
            print("  ·", b)
    try:
        a._close_to_tray = False
        a.destroy()
    except tk.TclError:
        pass
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
