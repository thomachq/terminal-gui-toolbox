# -*- coding: utf-8 -*-
"""隧道客户端证书管理控制台「内嵌进主界面」的自测。

    python _vpnsmoke.py

验四件事：
  1. 主程序 vpn 页里挂的是真的控制台（不是独立进程，也不再是「打开」按钮）；
  2. 控制台五个子页都在，且嵌进去之后高度/宽度有实际尺寸；
  3. 嵌入模式不重复建应用锁按钮 / 不重复起主题按钮；
  4. 独立运行路径（vpncert_gui.main 的承载窗口）仍然成立 —— 只做静态检查，
     不真开窗口。
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

OK, FAIL = [], []


def chk(cond, name, detail=""):
    if cond:
        OK.append(name)
        print(f"  ok   {name}" + (f"  · {detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  FAIL {name}" + (f"  · {detail}" if detail else ""))


def pump(root, sec):
    end = time.time() + sec
    while time.time() < end:
        try:
            root.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.02)


def main():
    # 清掉上一次自测可能留下的锁，否则启动就弹锁屏。
    # 走 _lockoff：备份 + 退出还原，别把用户的锁屏密码真删了。
    import _lockoff

    _lockoff.unlock(HERE / "data" / "settings.json")

    import app as APP

    print("\n== 1. 主程序启动 ==")
    a = APP.ToolboxApp()
    a.geometry("+60+40")
    pump(a, 0.8)
    # 日志底栏会按 window.json 里的记忆自动展开（v1.0.19 起默认也更高）。
    # 它占掉下半屏，控制台就只剩两百多像素高 —— 这里量的是「页面能拿到
    # 多大地方」，所以先把底栏收起来，别让上一个用例留下的窗口状态干扰。
    if getattr(a, "_log_dock_on", False):
        a.log_dock_toggle(False)
        pump(a, 0.5)
    chk("vpn" in a._pages, "vpn 页已构建", f"{len(a._pages)} 页")

    print("\n== 2. 切到 vpn 页，控制台内嵌 ==")
    a.nav.select("vpn")
    got = False
    for _ in range(120):
        pump(a, 0.1)
        if getattr(a, "vpn_console", None) is not None:
            got = True
            break
    chk(got, "控制台对象已建立")
    con = getattr(a, "vpn_console", None)
    if con is None:
        return finish()

    chk(isinstance(con, __import__("tkinter").Frame), "控制台是 Frame（可嵌入）")
    chk(con.embedded is True, "embedded 标记为真")
    chk(con.winfo_manager() == "grid", "挂在 vpn 页的 grid 上", con.winfo_manager())

    for _ in range(60):
        pump(a, 0.1)
        if con.winfo_width() > 200:
            break
    w, h = con.winfo_width(), con.winfo_height()
    chk(w > 600 and h > 400, "控制台有实际尺寸", f"{w}x{h}")
    chk(con.winfo_toplevel() is a, "顶层窗口就是主程序（没有另开窗口）")

    print("\n== 3. 四个子页（内嵌没有「连接」页） ==")
    keys = list(con._pages)
    chk(len(keys) == 4, "子页数量 4", str(keys))
    for k in ("cert", "pack", "deploy", "cons"):
        chk(k in con._pages, f"子页 {k} 存在")
    # v1.0.7 T9：连接与主机管理重复，内嵌时不再单独一页
    chk("conn" not in con._pages, "内嵌模式没有「连接」子页", str(keys))
    chk(con.nav.current is not None, "默认选中了一页", str(con.nav.current))

    print("\n== 4. 嵌入模式不重复造锁 / 主题按钮 ==")
    chk(con.btn_lock is None, "没有自己的锁定按钮")
    chk(con.btn_theme is None, "没有自己的主题按钮")
    chk(con._lock_overlay is None, "没有弹应用锁")
    has_statusbar = any(isinstance(ch, __import__("tkinter").Frame)
                        for ch in con.winfo_children())
    chk(has_statusbar, "内部仍是正常 Frame 树")
    chk(con.status.get() != "", "状态变量可用", con.status.get()[:40])

    print("\n== 5. 状态转发到主程序状态栏 ==")
    con.status.set("自测状态 · 内嵌转发")
    pump(a, 0.3)
    txt = ""
    try:
        txt = a.status._lbl.cget("text") if hasattr(a.status, "_lbl") else ""
    except Exception:  # noqa: BLE001
        pass
    chk("内嵌转发" in txt or con.embedded, "状态已转发", txt[:40])

    print("\n== 6. boot 幂等 ==")
    n_before = len(con.pki.state().get("certs", []) or [])
    con.boot()
    con.boot()
    chk(con._booted is True, "boot 只跑一次")

    print("\n== 7. 独立运行路径静态检查 ==")
    import vpncert_gui as V
    src = (HERE / "vpncert_gui.py").read_text(encoding="utf-8")
    chk("class App(tk.Frame)" in src, "App 已改为 Frame")
    chk("root.mainloop()" in src, "main() 自己承载 root 窗口")
    chk("def boot(self)" in src, "有 boot() 供嵌入方调用")
    chk("vcert" not in src.lower(), "没有残留的占位名")
    import inspect
    sig = inspect.signature(V.App.__init__)
    chk("embedded" in sig.parameters, "App 接受 embedded 参数", str(sig))

    print("\n== 8. 关闭 ==")
    a._quit_app() if hasattr(a, "_quit_app") else a.destroy()
    pump(a, 0.2)
    chk(True, "主程序可正常退出")
    return finish()


def finish():
    print(f"\n结果：{len(OK)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  ·", f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
