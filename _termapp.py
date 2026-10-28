# -*- coding: utf-8 -*-
"""终端与主程序集成：连本机 → 开终端 → 敲命令 → 主程序命令旁路 → 断开联动。

终端早已不是独立窗口，而是工作区里的一个页签（`app._term_panes["term"]`，
`app._term` 只在「拉出去」时才有值）。这个脚本原来按独立窗口写，
`a._term` 恒为 None，于是从「终端窗口已出现」开始全线失败 —— 现在按页签测。
"""
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
    w = a._term_live()
    # v1.0.31 起页签键按设备分（"term" / "term:<sid>"），连上本机也算一台设备，
    # 所以这里不能再写死 a.TERM_TAB —— 一律问当前设备那把键。
    tk_ = a._term_cur_key()
    chk(w is not None and w.winfo_exists() == 1, "终端页签已出现")
    chk(tk_ in a._term_panes, "页签登记在 _term_panes 里", tk_)
    chk(tk_ in a.tabbar.tabs, "页签栏上有终端", tk_)
    chk(a._term is None, "默认是内嵌页签，不是独立窗口")
    label = a._term_label()
    chk("终端" in label, "页签标题", label)
    chk("本机" in label or "LOCAL" in label, "标题带设备", label)
    chk(a.term_ready(), "term_ready 为真")
    ps = a.term_prompt()
    chk(ps.endswith("> ") or ps.endswith("$ "), "提示符成型", ps)

    print("\n4. 敲命令")
    # 默认形态是「行内」（不是输入框）：v_in 那时候是隐藏的，_submit 取的是
    # 正文里的内容。拿 v_in 注入等于什么都没输 —— 所以这里显式切到输入框模式。
    w._set_mode("box")
    pump(a, 0.3)
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

    print("\n7. 智能体通过 API 的操作也进终端")
    # 用户要求：「这个同步命令也要能同步 AI 智能体通过 API 的操作」。
    # 走的就是智能体那条路：api_pool.open → exec，不碰界面上任何按钮。
    conn = a.api_pool.open({"protocol": "local", "name": "自测-API"})
    w.clear()
    r = a.api_pool.exec(conn.id, "echo from-api-agent")
    pump(a, 1.6)
    body = w.text.get("1.0", "end")
    chk(r["rc"] == 0 and "from-api-agent" in r["out"], "API 命令自身有回显",
        str(r.get("rc")))
    chk("from-api-agent" in body, "API 命令同步进了终端", body.strip()[:60])
    chk("[API]" in body, "终端里标出这是智能体干的", body.strip()[:60])
    # 勾掉「同步命令」就该闭嘴（和手敲的命同一条规则）
    w._sync.set(False)
    w.clear()
    a.api_pool.exec(conn.id, "echo muted-agent")
    pump(a, 1.2)
    body2 = w.text.get("1.0", "end")
    chk("muted-agent" not in body2, "关掉同步后不再刷屏", body2.strip()[:40])
    w._sync.set(True)

    # 只关「同步 API」：智能体的命令闭嘴，自己敲的照常
    chk(hasattr(w, "chk_api"), "顶栏有「同步 API」开关")
    w._sync_api.set(False)
    w.clear()
    a.api_pool.exec(conn.id, "echo api-muted")
    pump(a, 1.6)
    chk("api-muted" not in w.text.get("1.0", "end"),
        "关掉「同步 API」后智能体的命令不显示",
        w.text.get("1.0", "end").strip()[:40])
    w.v_in.set("echo self-visible")
    w._submit()
    pump(a, 2.2)
    chk("self-visible" in w.text.get("1.0", "end"),
        "同一时刻自己敲的命令照常显示")
    w._sync_api.set(True)
    w.clear()
    a.api_pool.exec(conn.id, "echo api-back")
    pump(a, 1.6)
    chk("api-back" in w.text.get("1.0", "end"), "重新勾上就恢复同步")
    a.api_pool.close(conn.id)

    print("\n8. 断开联动")
    # v1.0.31：断开设备时它的终端页签一起关掉 —— 留着一个只能显示「未连接」
    # 的页签正是用户截图里那个「点不开的终端」。所以这里断言的是「清掉」。
    a._disconnect()
    pump(a, 0.4)
    chk(a._term_cwd_val == "", "断开后目录缓存清空")
    chk(tk_ not in a._term_panes, "那台的终端页签已注销", str(list(a._term_panes)))
    chk(tk_ not in a.tabbar.tabs, "页签栏上也移除了", str(list(a.tabbar.tabs)))
    chk(a._term_live() is None, "没有残留的活终端")
    # 断开后再点终端：开的是「本机/未连接」这个键，正文里要写明原因
    a.open_terminal()
    pump(a, 0.4)
    w2 = a._term_live()
    chk(w2 is not None, "未连接状态下也能开终端")
    chk("未连接" in a._term_label(), "终端标题跟着变", a._term_label())
    chk(w2 is not None and "未连接" in w2.text.get("1.0", "end"),
        "正文提示未连接")
    w2.v_in.set("echo nope")
    w2._submit()
    pump(a, 0.5)
    chk("未连接" in w2.text.get("1.0", "end"), "断开后敲命令被拦住")

    print("\n9. 再开一次不重复建页签")
    old = w2
    a.open_terminal()
    pump(a, 0.3)
    chk(a._term_live() is old, "已开着就只切过去，不新建")
    chk(len(a._term_panes) == 1, "同样状态下只留一个终端页签",
        str(len(a._term_panes)))

    print("\n10. 关闭")
    a._tab_close(a._term_cur_key())
    pump(a, 0.3)
    chk(a._term_live() is None, "关掉后主程序不再持有引用")
    chk(len(a._term_panes) == 0, "登记表里也清了", str(list(a._term_panes)))

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
