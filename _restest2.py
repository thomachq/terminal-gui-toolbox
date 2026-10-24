# -*- coding: utf-8 -*-
"""主机资源五页的界面层自测：主机管理 / 代理池 / 凭证管理 / 脚本库 / 日志管理。

跑的是真窗口（Tk 真建控件），数据目录换成临时目录，不碰用户真实档案。
--no-probe 保证不会有任何探测/系统命令被拉起来。
"""
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.argv = [sys.argv[0], "--no-probe"]
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = FAIL = 0
FAILS: list[str] = []
TD = tempfile.mkdtemp(prefix="tbres_")

import app as appmod  # noqa: E402

appmod.APP_DIR = Path(TD)
appmod.DATA_DIR = Path(TD) / "data"
appmod.DATA_DIR.mkdir(parents=True, exist_ok=True)

import tkinter  # noqa: E402


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")


def eq(a, b, name):
    chk(a == b, name, f"期望 {b!r} 实得 {a!r}")


def sec(t):
    print(f"\n-- {t}")


def pump(win, seconds=0.35):
    end = time.time() + seconds
    while time.time() < end:
        try:
            win.update()
        except tkinter.TclError:
            return
        time.sleep(0.01)


win = appmod.ToolboxApp()
win.geometry("+30+30")
pump(win, 0.8)

# ================================================================== 骨架
sec("1. 页面骨架与导航")
# v1.0.6：「日志管理」不占页签了，改成底部可开关的日志面板；
# v1.0.9：「模型管理」并进设置中心的页签。所以顶层是 15 个页。
wanted = ["hosts", "proxy", "cred", "script", "tools", "sys", "files",
          "routes", "sec", "docker", "web", "db", "cron", "vpn", "api"]
for k in wanted:
    chk(k in win._pages, f"页面已构建：{k}")
eq(len(win._pages), 15, "页面总数（15 个顶层页）")
chk("conn" not in win._pages, "连接与设备不再是独立页")
chk("log" not in win._pages, "日志不再是独立页（改成底部面板）")
eq(len(win.nav.items), 15, "导航条目数")
chk("hosts" in win.nav.items, "导航含主机管理")
chk(win.nav.alias.get("conn") == "hosts", "旧键 conn 指向主机管理")
chk(win.nav.alias.get("fw") == "sec", "旧键 fw 仍指向安全中心")

labels = [it.lbl.cget("text") for it in win.nav.items.values()]
chk("主机管理" in labels and "日志管理" not in labels, "导航文案")


def _labels(w) -> list[str]:
    """导航现在放在可滚动 Canvas 里，标签不是 nav 的直接子控件，得递归找。"""
    out: list[str] = []
    for c in w.winfo_children():
        try:
            if isinstance(c, tkinter.Label):
                out.append(c.cget("text"))
        except tkinter.TclError:
            pass
        out += _labels(c)
    return out


group_titles = _labels(win.nav)
# 「主机资源」这组刻意不画标题 —— 顶部页签已经写了这四个字，重复一遍很啰嗦。
# 刻意只给第二组画小标题：顶部页签已经写着「主机资源」了。
chk("运维工具" in group_titles, "有「运维工具」分组标题", str(group_titles))
chk("主机资源" not in group_titles, "主机资源那组不重复画标题", str(group_titles))

# ================================================================== 主机
sec("2. 主机管理")
win.profiles.upsert({"name": "web-1", "host": "10.0.0.1", "port": "22",
                     "user": "root", "group": "生产", "tags": "web, 上海"})
win.profiles.upsert({"name": "db-1", "host": "10.0.0.2", "port": "2222",
                     "user": "dba", "group": "生产", "tags": "db"})
win.profiles.upsert({"name": "test", "host": "192.168.1.9", "port": "22",
                     "user": "ops"})
win.host_refresh()
pump(win, 0.2)
eq(len(win.tbl_host.all_rows()), 3, "清单三行")
chk("共 3 台 · 2 个分组" in win.lbl_host_sum.cget("text")
    or win.lbl_host_sum.cget("text").startswith("共 3 台"),
    "摘要文案", win.lbl_host_sum.cget("text"))

win.v_host_group.set("生产")
win._hosts_apply_filter()
eq(len(win.tbl_host.all_rows()), 2, "按分组筛选")
win.v_host_group.set("全部分组")
win.tbl_host.entry_search.delete(0, "end")
win.tbl_host.entry_search.insert(0, "web")
win._hosts_apply_filter()
eq(len(win.tbl_host.all_rows()), 1, "按关键词筛选")
win.tbl_host.entry_search.delete(0, "end")
win._hosts_apply_filter()

chk(hasattr(win, "seg_hosts"), "有清单/连接段切换")
eq(win.seg_hosts.current, "list", "默认在主机清单")
win.seg_hosts.select("conn")
pump(win, 0.2)
chk(bool(win.conn_box.winfo_ismapped()), "连接段可见")
win.seg_hosts.select("list")
pump(win, 0.2)
chk(bool(win.hosts_box.winfo_ismapped()), "清单段可见")
chk(hasattr(win, "e_host"), "连接段复用了原连接表单")

# 凭证注入：主机只存 id，口令在连接前一刻才取出来
cid = win.creds.upsert({"name": "运维号", "kind": "password", "user": "root",
                        "secret": "p@ss"})["id"]
win.profiles.upsert({"name": "cred-host", "host": "10.0.0.7", "port": "22",
                     "user": "root", "cred_id": cid})
win._host_apply(win.profiles.get("cred-host"))
eq(win.v_pw.get(), "p@ss", "凭证口令在连接前注入表单")
eq(win.v_host.get(), "10.0.0.7", "地址填进表单")
eq(win.v_user.get(), "root", "用户填进表单")
chk(bool(win.profiles.get("cred-host").get("last_used")), "连一次就打上最近使用")
win.v_pw.set("")

# ================================================================== 凭证
sec("3. 凭证管理")
win.creds.upsert({"name": "只读巡检", "kind": "password", "user": "viewer",
                  "secret": "abcdef"})
win.cred_refresh()
pump(win, 0.2)
eq(len(win.tbl_cred.all_rows()), 2, "凭证两行")
rows = win.tbl_cred.all_rows()
names = [r["name"] for r in rows]
chk("运维号" in names and "只读巡检" in names, "凭证名称")
secret_cells = " ".join(str(r["secret"]) for r in rows)
chk("p@ss" not in secret_cells and "abcdef" not in secret_cells,
    "表格里没有口令原文", secret_cells)
chk("已设置" in secret_cells, "显示「已设置」")
cred_row = [r for r in rows if r["name"] == "运维号"][0]
eq(cred_row["usage"], "1 台", "使用中计数")
chk("共 2 条凭证" in win.lbl_cred_sum.cget("text"), "凭证摘要",
    win.lbl_cred_sum.cget("text"))

# ================================================================== 代理
sec("4. 代理池")
a = win.proxies.upsert({"name": "东京", "kind": "ssh", "host": "1.1.1.1",
                        "port": "22"})
b = win.proxies.upsert({"name": "香港", "kind": "ssh", "host": "2.2.2.2",
                        "port": "22", "parent_id": a["id"]})
win.proxy_refresh()
pump(win, 0.2)
eq(len(win.tbl_proxy.all_rows()), 2, "代理两行")
hk = [r for r in win.tbl_proxy.all_rows() if r["name"] == "香港"][0]
eq(hk["chain"], "东京 → 香港 → 目标", "链路文案")
eq(hk["depth"], "2 跳", "跳数")
chk("共 2 条代理" in win.lbl_proxy_sum.cget("text"), "代理摘要")
win.proxies.upsert({"name": "被引用", "kind": "socks5", "host": "3.3.3.3",
                    "port": "1080"})
win.profiles.upsert({"name": "via-proxy", "host": "10.0.0.8", "port": "22",
                     "proxy_id": win.proxies.names() and
                     win.proxies.get([p["id"] for p in win.proxies.proxies
                                      if p["name"] == "被引用"][0])["id"]})
win.proxy_refresh()
pump(win, 0.2)
used = [r for r in win.tbl_proxy.all_rows() if r["name"] == "被引用"][0]
eq(used["usage"], "1 台", "代理被主机引用")

# ================================================================== 脚本
sec("5. 脚本库")
win.script_refresh()
pump(win, 0.2)
eq(len(win.tbl_script.all_rows()), len(appmod.scripts_core.BUILTIN),
   "脚本库默认全是内置")
chk(len(win.tbl_script.all_rows()) >= 20, "内置脚本数量")
chk("内置" in win.lbl_script_sum.cget("text"), "脚本摘要含内置")
win.scripts.upsert({"name": "我的巡检", "kind": "shell", "body": "echo hi"})
win.script_refresh()
pump(win, 0.2)
eq(len(win.tbl_script.all_rows()), len(appmod.scripts_core.BUILTIN) + 1,
   "自定义脚本进清单")
win.v_script_src.set("自定义")
win._script_filter()
eq(len(win.tbl_script.all_rows()), 1, "按来源筛选")
win.v_script_src.set("全部来源")
win.v_script_kind.set("PowerShell")
win._script_filter()
chk(all(r["kind_key"] == "powershell" for r in win.tbl_script.all_rows()),
    "按类型筛选")
win.v_script_kind.set("全部类型")
win._script_filter()
chk(not win._script_busy, "空闲态")
chk(hasattr(win, "script_run"), "有运行入口")

# ================================================================== 日志
sec("6. 日志管理")
for i in range(25):
    win.audit.record("capability.terminal.exec" if i % 2 else "fs.write",
                     "ok" if i % 3 else "fail", f"第 {i} 条", "web-1")
# 日志现在是底部 dock，点了才建界面 —— 不打开的话 tbl_audit 根本不存在
win.log_dock_open()
pump(win, 0.4)
win.audit_reload()
pump(win, 0.3)
eq(len(win._audit_events), 25, "日志读回 25 条")
eq(len(win.tbl_audit.all_rows()), 10, "默认每页 10 条")
chk("第 1-10 条，共 25 条" in win.lbl_audit_range.cget("text"), "范围文案",
    win.lbl_audit_range.cget("text"))
pager_btns = [w for w in win._pager_btns.winfo_children()]
chk(len(pager_btns) >= 5, "页码按钮已渲染", str(len(pager_btns)))
win._audit_goto(3)
pump(win, 0.2)
eq(len(win.tbl_audit.all_rows()), 5, "末页 5 条")
chk("第 21-25 条，共 25 条" in win.lbl_audit_range.cget("text"), "末页范围",
    win.lbl_audit_range.cget("text"))
win._audit_goto(99)
eq(win._audit_page, 3, "页码越界夹回末页")

win.v_audit_status.set("失败")
win._audit_reset()
pump(win, 0.2)
fails = win.tbl_audit.all_rows()
chk(all(r["status"] == "失败" for r in fails), "按状态筛选")
chk(len(fails) > 0, "确实有失败记录", str(len(fails)))
win.v_audit_status.set("全部状态")
win._audit_reset()

title = win.tbl_audit.all_rows()[0]["title"] if win.tbl_audit.all_rows() else ""
chk(title in ("AI 在终端执行命令", "写入本地文件"), "标题已中文化", title)
evname = win.tbl_audit.all_rows()[0]["event"] if win.tbl_audit.all_rows() else ""
chk("." in evname, "事件名保留在副标题", evname)

# ================================================================== 审计链路
sec("7. 关键动作留痕")
before = len(win.audit.load())
win.host_del  # noqa: B018  仅确认方法存在
chk(callable(getattr(win, "host_del", None)), "主机删除入口存在")
win._audit("host.add", "ok", "测试留痕", "10.0.0.9")
after = len(win.audit.load())
chk(after == before + 1, "审计能追加", f"{before} → {after}")
last = win.audit.load()[0]
eq(last["event"], "host.add", "审计事件名")
eq(last["device"], "10.0.0.9", "审计记录设备")

# ================================================================== 收尾
try:
    win.destroy()
except tkinter.TclError:
    pass
try:
    import shutil
    shutil.rmtree(TD, ignore_errors=True)
except OSError:
    pass

print(f"\n=== {PASS} 通过 / {FAIL} 失败")
for f in FAILS:
    print("  ✗", f)
sys.exit(1 if FAIL else 0)
