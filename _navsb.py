# -*- coding: utf-8 -*-
"""量 Nav 的滚动条到底有没有露出来。

判据：内容比可视区高时，滚动条宽度必须 > 6 逻辑像素，且能点得到。
"""
import sys
import tkinter as tk

sys.path.insert(0, ".")
import wbtui as U

ITEMS = [
    ("hosts", "主机管理"),
    ("group", "运维工具", [
        ("tools", "运维工具"),
        ("net", "网络工具"),
        ("sysinfo", "系统信息"),
        ("files", "文件管理"),
        ("routes", "路由表"),
    ]),
    ("sec", "安全中心"),
    ("docker", "Docker 管理"),
    ("web", "Web 服务"),
    ("db", "数据库"),
    ("cron", "计划任务"),
]

root = tk.Tk()
U.configure_ttk(root)
root.geometry("900x420")

holder = tk.Frame(root, width=U.px(200))
holder.pack(side="left", fill="y")
holder.pack_propagate(False)

picked = []
nav = U.Nav(holder, ITEMS, lambda k: picked.append(k), width=180)
nav.pack(fill="both", expand=True)

fail = 0


def chk(ok, what, extra=""):
    global fail
    if not ok:
        fail += 1
    print(("  PASS  " if ok else "  FAIL  ") + what + (f"  [{extra}]" if extra else ""))


def probe():
    root.update_idletasks()
    root.update()
    cv = nav._canvas
    sb = nav._sb
    ih = nav._inner.winfo_reqheight()
    ch = cv.winfo_height()
    print(f"内容高={ih}  可视高={ch}  需要滚动={ih > ch + 1}")
    print(f"滚动条 is_mapped={sb.winfo_ismapped()}  w={sb.winfo_width()}"
          f"  可见标记={nav._sb_visible}")
    chk(ih > ch + 1, "矮窗口下内容确实装不下")
    chk(nav._sb_visible, "装不下时判为需要滚动条")
    chk(sb.winfo_ismapped(), "滚动条真的被 pack 出来了")
    chk(sb.winfo_width() > U.px(6), "滚动条宽度够点（不能被挤成 1px）",
        f"w={sb.winfo_width()} 期望>{U.px(6)}")
    # 画布 + 滚动条不能超出导航总宽
    total = cv.winfo_width() + sb.winfo_width()
    chk(total <= nav.winfo_width() + 2, "画布与滚动条不超宽",
        f"{total} vs {nav.winfo_width()}")
    # 滚轮能滚动
    before = cv.yview()[0]
    cv.yview_scroll(3, "units")
    root.update_idletasks()
    after = cv.yview()[0]
    chk(after > before, "滚轮/滚动条能滚下去", f"{before:.3f} -> {after:.3f}")
    # 拉到最底能看到最后一项
    cv.yview_moveto(1.0)
    root.update_idletasks()
    it = nav.items["cron"]
    y = it.winfo_rooty() - cv.winfo_rooty()
    bottom = y + it.winfo_height()
    chk(0 < y and bottom <= cv.winfo_height() + 2,
        "拉到最底时最后一项完整可见", f"y={y} bottom={bottom} ch={cv.winfo_height()}")

    # 拉高窗口 → 滚动条应该收起
    root.geometry("900x1200")
    for _ in range(6):
        root.update_idletasks()
        root.update()
    print(f"拉高后：内容={nav._inner.winfo_reqheight()} 可视={cv.winfo_height()}"
          f" 滚动条={nav._sb_visible}")
    chk(not nav._sb_visible, "装得下时滚动条自动收起")
    root.destroy()


root.after(400, probe)
root.mainloop()
print(f"\n===== 失败 {fail} 项 =====")
sys.exit(1 if fail else 0)
