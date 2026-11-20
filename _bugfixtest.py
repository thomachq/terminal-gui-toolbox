# -*- coding: utf-8 -*-
"""v1.0.38 回归：4 个用户新报 bug。

  1. 行首「连接/断开」按钮点击分发错位 —— 树列缩进让按钮图从 x≈26 才起画，
     旧逻辑按绝对 x<=U.px(52) 判断，连接按钮右半被误判成「编辑」。
  2. 编辑主机里关掉 sudo -i 保存无效 —— _apply_profile 用 `sudo or sudo_password`，
     存着密码就把提权勾选又打上，连接时重新 sudo -i。
  3. 终端上键不显示上次命令 —— 切回终端页签时焦点丢了，按键落到旧焦点上。
  4. Tab 不补全指令 —— 根本没绑 <Tab>。

全部离线：按钮/表单用真 Tk 控件，连接走假会话，不联外网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.argv = [sys.argv[0], "--no-probe"]
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import app as appmod  # noqa: E402
from core import hosts as H  # noqa: E402

TD = Path(tempfile.mkdtemp(prefix="tbbugfix_"))
appmod.APP_DIR = TD
appmod.DATA_DIR = TD / "data"
appmod.DATA_DIR.mkdir(parents=True, exist_ok=True)

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


def eq(a, b, name):
    chk(a == b, name, f"期望 {b!r} 实得 {a!r}")


def sec(t):
    print(f"\n-- {t}")


def pump(root, sec=0.2):
    end = time.time() + sec
    while time.time() < end:
        try:
            root.update()
        except tk.TclError:
            return
        time.sleep(0.01)


import tkinter as tk  # noqa: E402
import wbtui as U  # noqa: E402
from pages import hosts_page as HP  # noqa: E402
from ui.table import DataTable, Column  # noqa: E402
from ui.terminal import TerminalWindow, BUILTIN_CMDS  # noqa: E402

root = tk.Tk()
root.geometry("900x600+50+50")
U.configure_ttk(root)

# ================================================================ 1. 按钮分发
sec("1. 行首连接/断开按钮点击分发（修复树列缩进错位）")
imgs = HP._conn_button_images()
chk("conn" in imgs and "disc" in imgs, "两张按钮图都加载")
img = imgs["disc"]

tv = DataTable(root, columns=[Column("act", "", 116, anchor="w"),
                              Column("edit", "", 34, anchor="center"),
                              Column("status", "状态", 74)],
               icon_col=["act", "edit"], icon_of=lambda row: img)
tv.pack(fill="both", expand=True)
tv.set_rows([{"name": "nas", "status": "● 已连接",
              "_raw": {"name": "nas", "host": "192.168.3.12",
                       "port": "22", "proto": "ssh"}}])
root.update()
iid = tv.tree.get_children("")[0]
bbox = tv.tree.bbox(iid, "#0")
ymid = bbox[1] + bbox[3] // 2
img_x0 = next((x for x in range(bbox[2])
               if tv.tree.identify_element(x, ymid) == "image"), bbox[0])
chk(img_x0 > 0, "树列确实有缩进（按钮图不在 x=0）", f"img_x0={img_x0}")


class _Page:
    def __init__(self):
        self.tbl_host = tv
        self.toggles: list[dict] = []
        self.edits: list[dict] = []

    def host_toggle_conn(self, row):
        self.toggles.append(row)

    def host_edit(self, rec):
        self.edits.append(rec)


page = _Page()


def click_at(x):
    ev = tk.Event()
    ev.x, ev.y = x, ymid
    return HP.HostsPage._host_row_click(page, ev)


conn_right = img_x0 + U.px(52) - 4       # 连接按钮最右一段（旧逻辑误判区）
conn_left = img_x0 + 10                   # 连接按钮左侧
edit_zone = img_x0 + U.px(52) + 20        # 编辑按钮
click_at(conn_right)
click_at(conn_left)
click_at(edit_zone)
eq(len(page.toggles), 2, "连接按钮整段（含右半）都走连接/断开")
eq(len(page.edits), 1, "编辑按钮走编辑")
chk(all(r.get("_raw", {}).get("name") == "nas" for r in page.toggles),
    "toggle 落到正确那一行")

# ================================================================ 2. sudo 关闭
sec("2. 关掉 sudo 保存后连接不再重新提权")

# 纯函数层：merge 认 sudo=False
base = H.blank_record()
base.update({"name": "nas", "proto": "ssh", "host": "192.168.3.12",
             "port": "22", "user": "admin", "ostype": "linux",
             "sudo": False, "sudo_password": "kept-secret"})
data = {"name": "nas", "proto": "ssh", "host": "192.168.3.12",
        "port": "22", "user": "admin", "ostype": "linux", "sudo": False}
m = HP.merge_host_form(data, base=base)
eq(m.get("sudo"), False, "表单关掉 sudo → 档案 sudo=False")

# 连接层：_apply_profile 用 sudo（不是 sudo_password）决定提权勾
src = Path(appmod.__file__).read_text(encoding="utf-8")
chk('self.v_elevate.set(bool(p.get("sudo")))' in src,
    "连接表单提权勾只认 sudo 字段（不被残留密码重新激活）")
chk('bool(p.get("sudo") or p.get("sudo_password"))' not in src,
    "旧的「sudo 或 密码」写法已移除")

# ================================================================ 3/4. 终端
sec("3/4. 终端上键历史 + Tab 补全")


class FakeCtrl:
    def term_ready(self): return True
    def term_describe(self): return "bugfix"
    def term_cwd(self): return "C:\\"
    def term_prompt(self): return "C:\\> "
    def term_chdir(self, p): return False, ""
    def term_run(self, cmd, done): done(0, "", "", 1)
    def term_closed(self, w): pass


for mode in ("box", "inline"):
    w = TerminalWindow(root, FakeCtrl(), mode=mode)
    pump(root, 0.35)
    w._focus_input()
    pump(root, 0.15)
    for c in ("echo one", "echo two"):
        w._set_input(c)
        w._submit()
        pump(root, 0.25)
    w._focus_input()
    pump(root, 0.1)

    w._set_input("")
    ev = tk.Event()
    w._hist_prev(ev)
    eq(w._get_input(), "echo two", f"[{mode}] 上键取回上一条命令")

    w._set_input("hi")
    w._complete(ev)
    eq(w._get_input().strip(), "history", f"[{mode}] Tab 补全 hi → history")

    w._set_input("")
    w._complete(ev)
    chk(BUILTIN_CMDS[0] in w.text.get("1.0", "end"), f"[{mode}] 空前缀列出候选")

    w._set_input("zz-no-such-cmd")
    before = w._get_input()
    w._complete(ev)
    eq(w._get_input(), before, f"[{mode}] 无候选时输入原样不动")

    w.destroy()

# 切回终端页签会重新聚焦（代码闸门）
chk("_term_refocus" in src and "after_idle" in src,
    "切回终端页签有重新聚焦逻辑（上键/Tab 按键不再落到旧焦点）")

root.destroy()

print("\n" + "=" * 50)
if FAIL == 0:
    print(f"全部通过：{PASS}/{PASS}")
else:
    print(f"失败 {FAIL} 项：{BAD}")
sys.exit(1 if FAIL else 0)
