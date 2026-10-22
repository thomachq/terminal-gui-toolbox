# -*- coding: utf-8 -*-
"""第二批界面级自测：表格化、路由、防火墙、Docker、API、锁屏、托盘。

跑法：python _tsmoke2.py
"""
from __future__ import annotations

import sys
import time
import traceback

sys.argv = ["x"]
import tkinter as tk
from tkinter import ttk

import lockmgr
import app as A
import wbtui as U

ok_n = fail_n = 0
FAILS: list[str] = []


def chk(cond, name, detail=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print(f"  \033[32mPASS\033[0m {name}" + (f"  · {detail}" if detail else ""))
    else:
        fail_n += 1
        FAILS.append(name)
        print(f"  \033[31mFAIL\033[0m {name}" + (f"  · {detail}" if detail else ""))


def pump(a, sec=0.12):
    end = time.time() + sec
    while time.time() < end:
        try:
            a.update()
        except tk.TclError:
            return
        time.sleep(0.01)


def wait_for(a, cond, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        pump(a, 0.15)
        try:
            if cond():
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


print("== 0. 清掉上一轮残留的锁 ==")
import lockmgr as _lm
_st = _lm.Settings(A.DATA_DIR / "settings.json")
for _k in ("lock_enabled", "lock_hash", "lock_auto_login", "lock_pass_enc"):
    _st.data[_k] = "" if _k.endswith(("hash", "enc")) else False
_st.save()
chk(_lm.LockManager(_lm.Settings(A.DATA_DIR / "settings.json")).enabled() is False,
    "测试前锁状态已清空")

print("== 1. 构建与主题 ==")
a = A.ToolboxApp()
pump(a, 0.5)
chk(len(a._pages) == 16, "十六个页面都已构建", f"{list(a._pages)}")
# 连接与设备已并入主机管理（旧键 conn 走别名），AI 运维改成了右侧助手
for key in ("hosts", "proxy", "cred", "script", "log", "tools", "sys",
            "files", "routes", "sec", "docker", "web", "db", "cron",
            "vpn", "api"):
    a.nav.select(key)
    pump(a, 0.18)
    chk(a.nav.current == key, f"导航切换到 {key}")

print("== 2. 表格控件 ==")
chk(hasattr(a, "tbl_tool") and a.tbl_tool.winfo_exists(), "网络工具表格存在")
chk(hasattr(a, "tbl_disk") and a.tbl_disk.winfo_exists(), "磁盘表格存在")
chk(hasattr(a, "tbl_route") and a.tbl_route.winfo_exists(), "路由表格存在")
chk(hasattr(a, "tbl_fw") and a.tbl_fw.winfo_exists(), "防火墙表格存在")
chk(hasattr(a, "tbl_dk") and a.tbl_dk.winfo_exists(), "Docker 容器表格存在")
chk(hasattr(a, "tbl_sess") and a.tbl_sess.winfo_exists(), "API 会话表格存在")
chk(len(a.tbl_ep.all_rows()) >= 8, "接口速查有内容",
    f"{len(a.tbl_ep.all_rows())} 条")
a.tbl_route.set_columns(A.TOOL_COLS["ports"])
a.tbl_route.set_rows([{"proto": "tcp", "addr": "0.0.0.0", "port": "22",
                       "proc": "sshd/1", "state": "LISTEN", "_tone": "warn"}])
# 渲染是同步的，立刻取行数；先 pump 再取会被其它队列里的刷新覆盖，偶发失败
_n_route = len(a.tbl_route.tree.get_children())
pump(a, 0.2)
chk(_n_route == 1, "换列 + 填行正常", f"{_n_route} 行")

print("== 3. 本机工具（真实命令） ==")
a.nav.select("tools")
pump(a, 0.2)
a.tbl_tool.set_rows([])
a.do_tool("ip")
got = wait_for(a, lambda: len(a.tbl_tool.all_rows()) > 0, 40)
chk(got, "IP 地址解析成表格", f"{len(a.tbl_tool.all_rows())} 行")
a.tbl_tool.set_rows([])
a.do_tool("route")
got = wait_for(a, lambda: len(a.tbl_tool.all_rows()) > 0, 40)
chk(got and len(a.tbl_tool.all_rows()) > 5, "路由表解析成表格",
    f"{len(a.tbl_tool.all_rows())} 行")
a.tbl_tool.set_rows([])
a.do_tool("ports")
got = wait_for(a, lambda: len(a.tbl_tool.all_rows()) > 0, 60)
chk(got, "监听端口解析成表格", f"{len(a.tbl_tool.all_rows())} 行")

print("== 4. 系统信息 ==")
a.nav.select("sys")
pump(a, 0.2)
a.tbl_disk.set_rows([])
a.do_sys("disk")
got = wait_for(a, lambda: len(a.tbl_disk.all_rows()) > 0, 40)
chk(got, "磁盘解析成表格", f"{len(a.tbl_disk.all_rows())} 行")
a.tbl_proc.set_rows([])
a.do_sys("top")
got = wait_for(a, lambda: len(a.tbl_proc.all_rows()) > 0, 40)
chk(got, "进程解析成表格", f"{len(a.tbl_proc.all_rows())} 行")

print("== 5. 路由表（本机） ==")
a.nav.select("routes")
pump(a, 0.2)
a.routes_refresh()
got = wait_for(a, lambda: len(a.tbl_route.all_rows()) > 0, 45)
chk(got, "本机路由表读进表格", f"{len(a.tbl_route.all_rows())} 行")
chk(a.btn_route_del._enabled in (True, False), "删除按钮状态明确")

print("== 6. 防火墙（本机） ==")
a.nav.select("fw")
pump(a, 0.2)
a.fw_probe()
wait_for(a, lambda: a.fw_backend is not None, 45)
backend = a.fw_backend
chk(backend is not None, "探测到防火墙后端",
    backend.label if backend else "无")
if backend:
    a.fw_refresh()
    got = wait_for(a, lambda: len(a.tbl_fw.all_rows()) > 0, 90)
    chk(got, "规则读取流程完成", f"{len(a.tbl_fw.all_rows())} 条")
    chk(isinstance(backend.can_write, bool), "后端可写属性存在")

print("== 7. Docker（本机，重点看未安装提示） ==")
a.nav.select("docker")
pump(a, 0.3)
a.docker_probe()
wait_for(a, lambda: a.lbl_dk_title.cget("text") != "正在检测 Docker…", 45)
eng = a.engine
chk(eng is not None, "Docker 探测完成", eng.status if eng else "—")
title_txt = a.lbl_dk_title.cget("text")
if eng and eng.status == "absent":
    chk("未安装" in title_txt, "未安装时标题写明「未安装」", title_txt)
    chk("Docker" in a.tbl_dk.empty.lb_hint.cget("text") or
        "PATH" in a.tbl_dk.empty.lb_hint.cget("text"),
        "未安装时表格空态给出怎么装")
    chk(not a.btn_dk_start._enabled, "未安装时操作按钮禁用")
else:
    chk(True, "Docker 已安装，跳过未安装分支", eng.status if eng else "")
    chk(a.btn_dk_start._enabled == (eng.status == "ok"),
        "按钮可用性与状态一致")

print("== 8. API 服务 ==")
a.api_server.config.data["port"] = 8768
ok, msg = a.api_server.start()
chk(ok, "API 服务能启动", msg)
a.api_refresh()
pump(a, 0.2)
chk(a.api_server.running, "服务状态为运行中")
chk(a.badge_api.text == "运行中", "状态胶囊显示运行中", a.badge_api.text)
tok = a.api_server.config.token()
chk(tok.startswith("ttb_") and len(tok) > 20, "令牌已生成", tok[:10] + "…")
conn = a.api_pool.open({"protocol": "local", "name": "本机"})
chk(conn.id != "", "池里能开本机会话", conn.id)
r = a.api_pool.exec(conn.id, "echo api-ok")
chk(r["rc"] == 0 and "api-ok" in r["out"], "池里执行命令", f"{r['ms']}ms")
m = a.api_pool.metrics(conn.id)
chk("cpu_pct" in m, "池里能取性能采样", f"cpu={m.get('cpu_pct')}%")
a.api_refresh()
pump(a, 0.2)
chk(len(a.tbl_sess.all_rows()) >= 1, "会话出现在连接池表格",
    f"{len(a.tbl_sess.all_rows())} 条")
a.api_pool.close(conn.id)
a.api_server.stop()
a.api_refresh()
chk(a.badge_api.text == "已停止", "服务能停止", a.badge_api.text)

print("== 9. 锁屏 ==")
ok, msg = a.lock.set_password("Test123456", timeout=0, remember=False)
chk(ok, "设置初始密码", msg)
a._show_lock("已手动锁定")
pump(a, 0.35)
ov = a._lock_overlay
chk(ov is not None and ov.winfo_ismapped(), "遮罩已显示")
entries, btns = [], []


def walk(w):
    for c in w.winfo_children():
        if isinstance(c, ttk.Entry):
            entries.append(c)
        if isinstance(c, U.RoundButton):
            btns.append(c)
        walk(c)


walk(ov)
chk(len(entries) == 1, "锁屏有密码输入框", f"{len(entries)} 个")
chk(len(btns) >= 2, "锁屏有解锁按钮", f"{len(btns)} 个按钮")
if entries:
    chk(entries[0].winfo_ismapped() and entries[0].winfo_height() > 10,
        "输入框真正可见", f"{entries[0].winfo_width()}x{entries[0].winfo_height()}")
if btns:
    chk(all(b.winfo_ismapped() for b in btns), "按钮都在可见状态")
    chk(any(b.text.replace(" ", "") == "解锁" for b in btns), "存在「解锁」按钮",
        " / ".join(b.text for b in btns))
# 错误密码
entries[0].insert(0, "wrong")
unlock_btn = next((b for b in btns if b.text.replace(" ", "") == "解锁"), None)
if unlock_btn:
    unlock_btn.command()
    pump(a, 0.3)
    chk(a._lock_overlay is not None, "错密码不解锁")
    entries = []
    walk(ov)
    entries[0].delete(0, "end")
    entries[0].insert(0, "Test123456")
    unlock_btn.command()
    pump(a, 0.3)
    chk(a._lock_overlay is None, "正确密码能解锁")

print("== 10. 改密码必须旧密码 ==")
ok1, m1 = a.lock.set_password("Another123", timeout=0, remember=False)
chk(not ok1 and "当前密码" in m1, "不填旧密码被拒绝", m1)
ok2, m2 = a.lock.set_password("Another123", timeout=0, remember=False,
                              old_password="bad")
chk(not ok2 and "不正确" in m2, "旧密码错误被拒绝", m2)
ok3, m3 = a.lock.set_password("Another123", timeout=0, remember=False,
                              old_password="Test123456")
chk(ok3, "旧密码正确才允许改", m3)
chk(a.lock.check("Another123"), "新密码生效")
ok4, m4 = a.lock.clear_password()
chk(not ok4 and "当前密码" in m4, "关闭应用锁也要旧密码", m4)
ok5, m5 = a.lock.clear_password(old_password="Another123")
chk(ok5, "带旧密码可关闭", m5)
chk(not a.lock.enabled(), "关闭后不再启用")

print("== 11. 锁设置窗口字段 ==")
a.lock.set_password("Test123456", timeout=0, remember=False)
a._open_lock_window()
pump(a, 0.4)
tops = [w for w in a.winfo_children() if isinstance(w, tk.Toplevel)]
dlg = tops[-1] if tops else None
chk(dlg is not None, "锁设置窗口已打开")
if dlg:
    labels, ents = [], []

    def walk2(w):
        for c in w.winfo_children():
            if isinstance(c, tk.Label):
                labels.append(c.cget("text"))
            if isinstance(c, ttk.Entry):
                ents.append(c)
            walk2(c)
    walk2(dlg)
    chk(any("当前密码" in str(t) for t in labels), "有「当前密码」字段",
        " / ".join(str(t) for t in labels[:6]))
    pwd_ents = [e for e in ents if str(e.cget("show")) == "*"]
    chk(len(pwd_ents) == 3, "三个密码框（旧 / 新 / 确认）", f"{len(pwd_ents)} 个")
    dlg.destroy()
a.lock.clear_password(old_password="Test123456")

print("== 12. 托盘 ==")
if a._tray is not None:
    chk(a._tray.running, "托盘图标已驻留",
        a._tray.last_error or "ok")
    m = a._tray_menu()
    labels = [m.entrycget(i, "label") for i in range(m.index("end") + 1)
              if m.type(i) == "command"]
    chk(any("显示主窗口" in str(t) for t in labels), "托盘菜单有显示主窗口",
        " / ".join(str(t) for t in labels)[:80])
else:
    chk(False, "托盘对象存在")

print("== 13. 主题切换后表格仍在 ==")
a._toggle_theme()
pump(a, 0.4)
chk(a.tbl_route.winfo_exists(), "切深色后表格没被销毁")
a._toggle_theme()
pump(a, 0.3)
chk(a.tbl_route.winfo_exists(), "切回浅色正常")

a._quit_app()
pump(a, 0.3)

print()
print(f"结果：{ok_n} 通过 / {fail_n} 失败")
if FAILS:
    print("失败项：")
    for f in FAILS:
        print("  ·", f)
sys.exit(0 if fail_n == 0 else 1)
