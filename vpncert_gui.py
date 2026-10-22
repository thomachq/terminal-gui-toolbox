# -*- coding: utf-8 -*-
"""
Client Cert Console —— 原生 Windows 窗口程序（tkinter）

隧道客户端证书管理：给 NAS 上的隧道服务补上客户端证书。
  建私有 CA、签设备证书、多平台打包配置文件、吊销 + CRL、部署到 NAS。
  SSH 侧用开源库 paramiko，执行的每条命令与原始输出都进「命令日志」页。

界面风格参考 WorkBuddy（浅色蓝调），功能为独立实现。
依赖：paramiko、cryptography。
"""
from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import wbtui as U
import crypto_store
import lockmgr
from core import redact
from nasops import NasError, NasSession
from pkilib import PLATFORMS, PLATFORM_ORDER, PKI, PKIError
from profiles import (ProfileStore, chmod_600_warning,
                      discover_keys, inspect_key)

# 打包成 exe 后 __file__ 指向 PyInstaller 的临时解压目录（onefile 会随退出删除），
# 所以数据目录必须锚定到可执行文件所在位置，否则每次运行 CA 都"消失"。
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"


def wrap_to(holder, *labels, pad: int = 14, floor: int = 280) -> None:
    """让标签的 wraplength 跟着 holder 的实际宽度走。

    这些提示语原来写死 wraplength=U.px(980)，独立窗口 1220 宽刚好；
    嵌进主程序后可用宽度会变（还能拖分隔条），写死就会被裁掉半句。
    """
    def on_cfg(e):
        w = max(U.px(floor), e.width - U.px(pad) * 2)
        for lb in labels:
            try:
                lb.configure(wraplength=w)
            except tk.TclError:
                pass

    holder.bind("<Configure>", on_cfg, add="+")


class App(tk.Frame):
    """隧道客户端证书管理控制台主体。

    两种用法：

    * 独立运行 —— ``App(root)``，窗口标题 / 尺寸由 :func:`main` 管；
    * 嵌入主程序 —— ``App(parent, embedded=True)``，不碰窗口标题与尺寸，
      不重复做应用锁（主程序已经有一把），状态文本转发给主程序状态栏。
      数据初始化推迟到 :meth:`boot`，避免主程序启动时白等一次磁盘扫描。
    """

    def __init__(self, master, embedded: bool = False,
                 on_status=None, on_quit=None):
        super().__init__(master, bg=U.T.page, bd=0, highlightthickness=0)
        self.embedded = embedded
        self._on_status = on_status
        self._on_quit = on_quit
        self._booted = False

        self.pki = PKI(DATA_DIR)
        self.nas = NasSession()
        self.nas.tracer = self._trace_cmd

        # 机密存储：档案与「记住的密码」都靠它。密钥取 data 目录（便携优先）。
        crypto_store.set_key_dir(DATA_DIR)
        self.settings = lockmgr.Settings(DATA_DIR / "settings.json")
        self.lock = lockmgr.LockManager(self.settings)
        self.profiles = ProfileStore(DATA_DIR / "profiles.dat")

        self.q: queue.Queue = queue.Queue()
        self.status = tk.StringVar(value="就绪")
        self._pages: dict[str, tk.Frame] = {}
        self._key_cache: list[dict] = []
        self._lock_overlay: tk.Frame | None = None
        self._last_active = time.time()
        self._keypw_job = None

        U.configure_ttk(self.winfo_toplevel())
        self._header()
        self._main()
        self.after(70, self._drain)

        if embedded:
            # 嵌入时状态走主程序状态栏；应用锁由主程序统一管。
            if on_status:
                self.status.trace_add("write",
                                      lambda *_: on_status(self.status.get()))
        else:
            self._statusbar()
            self._load_last_profile()
            self.refresh_all()
            self._watch_activity()
            self._maybe_lock_on_start()

    def boot(self) -> None:
        """首次真正显示时再读档案 / 扫 CA（嵌入模式用，幂等）。"""
        if self._booted:
            return
        self._booted = True
        self._load_last_profile()
        self.refresh_all()

    # ============================================================ 骨架
    def _header(self):
        # 嵌入主程序时，应用锁和主题都由主程序管，这里不再重复两个按钮。
        # 先落成 None，内嵌早退之后这两个属性也存在（接口一致，自测也好断言）。
        self.btn_lock = None
        self.btn_theme = None

        # 内嵌时「隧道客户端证书管理」这个标题主程序导航已经写了，
        # 再顶一行大字只会白吃 60px 高度；状态胶囊挪到分段条那一行去。
        if self.embedded:
            return
        bar = U.wframe(self, "page")
        bar.pack(fill="x", padx=20, pady=(16, 8))

        left = U.wframe(bar, "page")
        left.pack(side="left")
        U.wlabel(left, "Client Cert Console", "page", U.FONT_H).pack(anchor="w")
        U.wlabel(left, "隧道客户端证书管理 · 建 CA / 签证书 / 多平台打包 / 吊销",
                 "pagedim", U.FONT_S).pack(anchor="w", pady=(2, 0))

        right = U.wframe(bar, "page")
        right.pack(side="right")
        if not self.embedded:
            self.btn_lock = U.RoundButton(right, self._lock_btn_text(),
                                          self._lock_dialog, variant="ghost",
                                          font=U.FONT_S, height=28, padx=12)
            self.btn_lock.pack(side="right", padx=(10, 0))
            self.btn_theme = U.RoundButton(
                right, "浅色" if U.T.name == "dark" else "深色", self._toggle_theme,
                variant="ghost", font=U.FONT_S, height=28, padx=12)
            self.btn_theme.pack(side="right", padx=(10, 0))
        self.pill_nas = U.Pill(right, "NAS 未连接", "err")
        self.pill_nas.pack(side="right", padx=6)
        self.pill_ca = U.Pill(right, "CA 未初始化", "mute")
        self.pill_ca.pack(side="right", padx=6)

    def _toggle_theme(self):
        U.toggle_theme()
        self.configure(bg=U.T.page)
        if self.btn_theme is not None:
            self.btn_theme.set_text("深色" if U.T.name == "light" else "浅色")
        self._apply_console_tags()

    # ============================================================ 应用锁
    def _lock_btn_text(self) -> str:
        return "🔒 锁定" if self.lock.enabled() else "设置锁"

    def _maybe_lock_on_start(self):
        if not self.lock.enabled():
            return
        if self.lock.auto_login():
            ok, _msg = self.lock.unlock_with_remembered()
            if ok:
                self.status.set("已用记住的密码自动解锁")
                return
        self._show_lock("启动需要解锁")

    def _watch_activity(self):
        for seq in ("<Any-KeyPress>", "<Any-ButtonPress>", "<MouseWheel>"):
            if self.embedded:
                self.bind(seq, self._touch, add="+")
            else:
                self.bind_all(seq, self._touch, add="+")
        self._lock_tick()

    def _touch(self, _e=None):
        self._last_active = time.time()

    def _lock_tick(self):
        try:
            if self._lock_overlay is None and self.lock.enabled():
                t = self.lock.timeout()
                if t and time.time() - self._last_active > t:
                    self._show_lock("闲置超时，已自动锁定")
        except Exception:  # noqa: BLE001
            pass
        try:
            self.after(5000, self._lock_tick)
        except tk.TclError:
            pass

    def _show_lock(self, reason: str = ""):
        if self._lock_overlay is not None:
            return
        try:
            self.nas.close()
            self.pill_nas.set("NAS 未连接", "err")
        except Exception:  # noqa: BLE001
            pass
        self.lock.forget()

        ov = tk.Frame(self, bg=U.T.page, bd=0, highlightthickness=0)
        ov.place(x=0, y=0, relwidth=1, relheight=1)
        ov.lift()

        wrap = U.wframe(ov, "page")
        wrap.place(relx=0.5, rely=0.44, anchor="center")
        card = U.Card(wrap, pad=26)
        card.pack()
        body = card.body
        tk.Frame(body, bg=U.T.card, width=U.px(330), height=1).pack()

        U.wlabel(body, "应用已锁定", "card", U.FONT_H).pack(anchor="w")
        U.wlabel(body, reason or "输入密码解锁", "dim", U.FONT_S).pack(
            anchor="w", pady=(3, 14))

        v = tk.StringVar()
        e = ttk.Entry(body, textvariable=v, show="*", width=30)
        e.pack(fill="x")
        e.focus_set()

        msg = U.wlabel(body, "", "err", U.FONT_XS)
        msg.pack(anchor="w", pady=(6, 0))

        row = U.wframe(body, "card")
        row.pack(fill="x", pady=(14, 0))

        def try_unlock(_e=None):
            if self.lock.check(v.get()):
                self.lock.adopt(v.get())
                self._last_active = time.time()
                ov.destroy()
                self._lock_overlay = None
                self.status.set("已解锁")
            else:
                msg.configure(text="密码不对")
                v.set("")
                e.focus_set()

        U.RoundButton(row, "解锁", try_unlock, variant="primary",
                      height=34, padx=22).pack(side="left")
        U.RoundButton(row, "退出程序", self._quit, variant="ghost",
                      height=34, padx=16).pack(side="left", padx=(8, 0))
        e.bind("<Return>", try_unlock)

        self._lock_overlay = ov

    def _lock_dialog(self):
        if not self.lock.enabled():
            self._open_lock_window()
            return
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="立即锁定", command=lambda: self._show_lock("已手动锁定"))
        m.add_command(label="锁设置…", command=self._open_lock_window)
        m.add_separator()
        m.add_command(label="关闭应用锁", command=self._close_lock)
        try:
            m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            m.grab_release()

    def _close_lock(self):
        if not messagebox.askyesno("关闭应用锁", "关闭后启动不再需要密码，确定吗？",
                                   parent=self):
            return
        self.lock.clear_password()
        self.btn_lock.set_text(self._lock_btn_text())
        self.status.set("应用锁已关闭")

    def _open_lock_window(self):
        dlg = tk.Toplevel(self)
        dlg.title("应用锁")
        dlg.configure(bg=U.T.page)
        dlg.transient(self)
        dlg.resizable(False, False)
        w, h = U.px(520), U.px(560)
        x = self.winfo_rootx() + max(0, (self.winfo_width() - w) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - h) // 2)
        dlg.geometry(f"{w}x{h}+{x}+{y}")

        card = U.Card(dlg, pad=22)
        card.pack(fill="both", expand=True, padx=U.px(14), pady=U.px(14))
        b = card.body

        U.wlabel(b, "应用锁", "card", U.FONT_H).pack(anchor="w")
        U.wlabel(b, self.lock.describe(), "dim", U.FONT_S).pack(anchor="w", pady=(3, 12))

        v_pw = tk.StringVar()
        v_pw2 = tk.StringVar()
        v_min = tk.StringVar(value=str(max(0, self.lock.timeout() // 60)))
        v_rem = tk.BooleanVar(value=self.lock.auto_login())

        U.wlabel(b, "密码（至少 6 位，字符不限）", "card", U.FONT_S).pack(anchor="w")
        e1 = ttk.Entry(b, textvariable=v_pw, show="*")
        e1.pack(fill="x", pady=(3, 8))
        U.wlabel(b, "再输一次", "card", U.FONT_S).pack(anchor="w")
        e2 = ttk.Entry(b, textvariable=v_pw2, show="*")
        e2.pack(fill="x", pady=(3, 8))

        r = U.wframe(b, "card")
        r.pack(fill="x", pady=(4, 0))
        U.wlabel(r, "闲置", "card", U.FONT_S).pack(side="left")
        ttk.Entry(r, textvariable=v_min, width=6).pack(side="left", padx=(6, 4))
        U.wlabel(r, "分钟后自动锁定（0 = 永不）", "card", U.FONT_S).pack(side="left")

        U.Check(b, "记住密码（本机密钥加密保存，启动自动解锁）", v_rem).pack(
            anchor="w", pady=(12, 0))

        U.wlabel(b, "锁的是界面。档案本身由本机密钥加密，"
                    "但能拿到整个 data 目录的人仍可解密 —— 要防这一层，"
                    "请把文件夹放在受控位置。",
                 "dim", U.FONT_XS, justify="left",
                 wraplength=U.px(430)).pack(anchor="w", pady=(10, 0))

        msg = U.wlabel(b, "", "err", U.FONT_XS, justify="left",
                       wraplength=U.px(430))
        msg.pack(anchor="w", pady=(8, 0))

        act = U.wframe(b, "card")
        act.pack(fill="x", pady=(12, 0))

        def apply_pw():
            pw, pw2 = v_pw.get(), v_pw2.get()
            if pw != pw2:
                msg.configure(text="两次输入不一致", fg=U.T.err)
                return
            try:
                mins = int(v_min.get() or "30")
            except ValueError:
                msg.configure(text="分钟数必须是数字", fg=U.T.err)
                return
            ok, text = self.lock.set_password(
                pw, timeout=0 if mins <= 0 else mins * 60,
                remember=v_rem.get())
            if not ok:
                msg.configure(text=text, fg=U.T.err)
                return
            self.btn_lock.set_text(self._lock_btn_text())
            self.status.set(text)
            dlg.destroy()

        U.RoundButton(act, "启用 / 修改", apply_pw, variant="primary",
                      height=34, padx=18).pack(side="left")
        U.RoundButton(act, "取消", dlg.destroy, variant="ghost",
                      height=34, padx=14).pack(side="right")

        e1.focus_set()
        dlg.grab_set()

    def _main(self):
        main = U.wframe(self, "page")
        main.pack(fill="both", expand=True, padx=10)
        main.pack_propagate(False)

        items = [("conn", "连接"), ("cert", "证书"), ("pack", "打包"),
                 ("deploy", "部署"), ("cons", "命令日志")]

        if self.embedded:
            # 内嵌时用横向分段：竖导航那 168px 在这一页是纯浪费，
            # 而且左侧主程序已经有一列竖导航，两列竖的并排看着像迷宫。
            bar = U.wframe(main, "page")
            bar.pack(fill="x", padx=(U.px(6), U.px(8)),
                     pady=(U.px(2), U.px(8)))
            from ui.table import Segmented
            self.nav = Segmented(bar, items, command=self._show)
            self.nav.pack(side="left")
            self.pill_nas = U.Pill(bar, "NAS 未连接", "err")
            self.pill_nas.pack(side="right", padx=(U.px(6), 0))
            self.pill_ca = U.Pill(bar, "CA 未初始化", "mute")
            self.pill_ca.pack(side="right", padx=U.px(6))
        else:
            self.nav = U.Nav(main, items, on_change=self._show, width=168)
            self.nav.pack(side="left", fill="y", padx=(6, 0), pady=(2, 8))

        self.stack = U.wframe(main, "page")
        if self.embedded:
            self.stack.pack(fill="both", expand=True,
                            padx=(U.px(4), U.px(8)), pady=(0, U.px(8)))
        else:
            self.stack.pack(side="left", fill="both", expand=True,
                            padx=(U.px(8), U.px(8)),
                            pady=(U.px(2), U.px(14)))
        self.stack.grid_propagate(False)
        self.stack.rowconfigure(0, weight=1)
        self.stack.columnconfigure(0, weight=1)

        for key, builder in (("conn", self._build_conn), ("cert", self._build_cert),
                             ("pack", self._build_pack), ("deploy", self._build_deploy),
                             ("cons", self._build_console)):
            page = U.wframe(self.stack, "page")
            page.grid(row=0, column=0, sticky="nsew")
            self._pages[key] = page
            builder(page)

        self.nav.select("conn")

    def _show(self, key: str):
        pg = self._pages.get(key)
        if pg is not None:
            pg.tkraise()

    def _quit(self):
        if self._on_quit:
            self._on_quit()
            return
        self.winfo_toplevel().destroy()

    def _statusbar(self):
        bar = U.wframe(self, "page")
        bar.pack(fill="x", padx=22, pady=(0, 10))
        U.wlabel(bar, "", "pagedim", U.FONT_S, textvariable=self.status).pack(side="left")
        U.wlabel(bar, f"数据目录 {DATA_DIR}", "pagemute", U.FONT_XS).pack(side="right")

    # ---------------------------------------------------- 线程 / 队列
    def post(self, fn, *args):
        self.q.put((fn, args))

    def bg(self, fn, ok=None, err=None):
        def worker():
            try:
                res = fn()
                if ok:
                    self.post(ok, res)
            except (PKIError, NasError) as e:
                self.post(self._fail, str(e), err)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self.post(self._fail, f"{type(e).__name__}: {e}", err)
        threading.Thread(target=worker, daemon=True).start()

    def _fail(self, msg, cb=None):
        self.status.set("✗ " + msg)
        if cb:
            try:
                cb(msg)
                return
            except Exception:  # noqa: BLE001
                pass
        messagebox.showerror("操作失败", msg, parent=self)

    def _drain(self):
        try:
            while True:
                fn, args = self.q.get_nowait()
                try:
                    fn(*args)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
        except queue.Empty:
            pass
        self.after(70, self._drain)

    # ============================================================ 1 · 连接
    def _build_conn(self, pg: tk.Frame):
        pg.rowconfigure(0, weight=1)

        col = U.wframe(pg, "page")
        if self.embedded:
            # 内嵌时横向只剩 900 出头，固定的 486(≈729px) 会把右栏压成一条缝。
            # 改成 3:2 按比例分，两边都能用。
            pg.columnconfigure(0, weight=3, uniform="conncol")
            pg.columnconfigure(1, weight=2, uniform="conncol")
            col.grid(row=0, column=0, sticky="nsew", padx=(2, 12))
        else:
            pg.columnconfigure(1, weight=1)
            col.grid(row=0, column=0, sticky="nsw", padx=(2, 12))
            col.configure(width=U.px(486))
            col.grid_propagate(False)
            col.pack_propagate(False)

        # ---------- 连接档案 ----------
        c = U.Card(col, title="连接档案", hint="只存地址与密钥路径")
        c.pack(fill="x")
        b = c.body
        self.v_prof = tk.StringVar()
        self.cb_prof = ttk.Combobox(b, textvariable=self.v_prof, state="readonly", width=26)
        self.cb_prof.pack(fill="x")
        self.cb_prof.bind("<<ComboboxSelected>>", self._on_profile_pick)

        r = U.wframe(b, "card")
        r.pack(fill="x", pady=(9, 0))
        U.RoundButton(r, "保存", self._profile_save, variant="secondary",
                      font=U.FONT_S, height=29, padx=13).pack(side="left")
        U.RoundButton(r, "另存为…", self._profile_save_as, variant="secondary",
                      font=U.FONT_S, height=29, padx=13).pack(side="left", padx=6)
        U.RoundButton(r, "删除", self._profile_delete, variant="danger",
                      font=U.FONT_S, height=29, padx=13).pack(side="left")
        U.RoundButton(r, "导入 ssh config", self._profile_import, variant="ghost",
                      font=U.FONT_S, height=29, padx=11).pack(side="right")

        self.v_remember = tk.BooleanVar(value=False)
        U.Check(b, "记住密码与私钥口令（本机加密保存）", self.v_remember).pack(
            anchor="w", pady=(9, 0))

        U.wlabel(b, "档案文件整体是密文，明文里看不到地址与口令；不勾上面这项时，"
                    "一个口令字段都不会写入。",
                 "dim", U.FONT_XS, justify="left",
                 wraplength=U.px(430)).pack(anchor="w", pady=(6, 0))

        # ---------- 服务器 ----------
        c = U.Card(col, title="服务器")
        c.pack(fill="x", pady=(10, 0))
        b = c.body
        b.columnconfigure(1, weight=1)
        self.v_host = tk.StringVar(value="192.168.3.12")
        self.v_port = tk.StringVar(value="3022")
        self.v_user = tk.StringVar(value="cloudadmin")

        U.wlabel(b, "地址", "card", U.FONT_S).grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(b, textvariable=self.v_host).grid(row=0, column=1, sticky="we",
                                                    padx=(10, 8), pady=3)
        U.wlabel(b, "端口", "card", U.FONT_S).grid(row=0, column=2, sticky="w", pady=3)
        ttk.Entry(b, textvariable=self.v_port, width=7).grid(row=0, column=3, sticky="w",
                                                             padx=(8, 0), pady=3)
        U.wlabel(b, "用户", "card", U.FONT_S).grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(b, textvariable=self.v_user).grid(row=1, column=1, columnspan=3,
                                                    sticky="we", padx=(10, 0), pady=3)

        # ---------- 认证 ----------
        c = U.Card(col, title="认证方式")
        c.pack(fill="x", pady=(10, 0))
        b = c.body
        self.v_mode = tk.StringVar(value="key")
        self.v_key = tk.StringVar()
        self.v_keypw = tk.StringVar()
        self.v_pw = tk.StringVar()

        r = U.wframe(b, "card")
        r.pack(fill="x")
        ttk.Radiobutton(r, text="私钥（推荐）", value="key", variable=self.v_mode,
                        style="Card.TRadiobutton",
                        command=self._mode_changed).pack(side="left")
        ttk.Radiobutton(r, text="登录密码", value="password", variable=self.v_mode,
                        style="Card.TRadiobutton",
                        command=self._mode_changed).pack(side="left", padx=18)

        self.row_key = U.wframe(b, "card")
        self.row_key.pack(fill="x", pady=(8, 0))
        self.row_key.columnconfigure(0, weight=1)
        self.e_key = ttk.Entry(self.row_key, textvariable=self.v_key)
        self.e_key.grid(row=0, column=0, sticky="we")
        self.e_key.bind("<FocusOut>", lambda e: self._refresh_key_info())
        U.RoundButton(self.row_key, "浏览…", self._pick_key, variant="secondary",
                      font=U.FONT_S, height=29, padx=12).grid(row=0, column=1, padx=(6, 0))
        U.RoundButton(self.row_key, "发现 ▾", self._key_menu, variant="secondary",
                      font=U.FONT_S, height=29, padx=12).grid(row=0, column=2, padx=(6, 0))

        self.lbl_key = U.wlabel(self.row_key, "未选择私钥", "dim", U.FONT_XS, justify="left")
        self.lbl_key.grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 0))

        r2 = U.wframe(b, "card")
        r2.pack(fill="x", pady=(7, 0))
        U.wlabel(r2, "私钥口令", "card", U.FONT_S).pack(side="left")
        ttk.Entry(r2, textvariable=self.v_keypw, show="*", width=20).pack(side="left", padx=(8, 0))
        self.btn_reveal = U.RoundButton(r2, "显示", lambda: self._reveal(
            self.v_keypw, self.btn_reveal), variant="ghost", font=U.FONT_XS,
            height=26, padx=9)
        self.btn_reveal.pack(side="left", padx=(6, 0))
        # 口令是「私钥状态」行的输入之一：一边敲一边重算，
        # 否则填对了口令、连接也成功了，那行红字还挂着，看着像失败。
        self.v_keypw.trace_add("write", self._on_keypw_changed)

        self.row_pw = U.wframe(b, "card")
        U.wlabel(self.row_pw, "登录密码", "card", U.FONT_S).pack(side="left")
        e_pw = ttk.Entry(self.row_pw, textvariable=self.v_pw, show="*", width=24)
        e_pw.pack(side="left", padx=(8, 0))
        self.btn_reveal2 = U.RoundButton(self.row_pw, "显示", lambda: self._reveal(
            self.v_pw, self.btn_reveal2), variant="ghost", font=U.FONT_XS,
            height=26, padx=9)
        self.btn_reveal2.pack(side="left", padx=(6, 0))

        # ---------- 提权 ----------
        c = U.Card(col, title="提权", hint="部署/改配置必需")
        c.pack(fill="x", pady=(10, 0))
        b = c.body
        self.v_sudopw = tk.StringVar()
        r = U.wframe(b, "card")
        r.pack(fill="x")
        U.wlabel(r, "sudo 密码", "card", U.FONT_S).pack(side="left")
        ttk.Entry(r, textvariable=self.v_sudopw, show="*", width=22).pack(side="left", padx=(8, 0))
        self.btn_reveal3 = U.RoundButton(r, "显示", lambda: self._reveal(
            self.v_sudopw, self.btn_reveal3), variant="ghost", font=U.FONT_XS,
            height=26, padx=9)
        self.btn_reveal3.pack(side="left", padx=(6, 0))
        U.wlabel(b, "留空只做只读探测；填了才能推送证书与重启服务。",
                 "dim", U.FONT_XS).pack(anchor="w", pady=(6, 0))

        # ---------- 动作 ----------
        act = U.wframe(col, "page")
        act.pack(fill="x", pady=(12, 0))
        self.btn_connect = U.RoundButton(act, "连接并探测", self.do_connect,
                                         variant="primary", height=36, padx=22)
        self.btn_connect.pack(side="left")
        U.RoundButton(act, "断开", self.do_disconnect, variant="secondary",
                      height=36, padx=18).pack(side="left", padx=8)

        # ---------- 右：服务端现状 ----------
        right = U.wframe(pg, "page")
        right.grid(row=0, column=1, sticky="nsew")
        card = U.Card(right, title="服务端现状", hint="连接后自动探测")
        card.pack(fill="both", expand=True)
        b = card.body
        self.kv_holder = U.wframe(b, "card")
        self.kv_holder.pack(fill="x")
        U.wlabel(self.kv_holder, "尚未连接。填好左侧参数后点「连接并探测」。",
                 "dim", U.FONT_S).pack(anchor="w")

        U.wlabel(b, "openvpn.conf（原始）", "dim", U.FONT_XS).pack(anchor="w", pady=(12, 4))
        self.box_conf = U.TextBox(b, role="alt", font=U.MONO_S, height=12)
        self.box_conf.txt.configure(width=1)
        self.box_conf.pack(fill="both", expand=True)

    # ---- 认证行切换
    def _mode_changed(self):
        if self.v_mode.get() == "key":
            self.row_pw.pack_forget()
            self.row_key.pack(fill="x", pady=(8, 0))
        else:
            self.row_key.pack_forget()
            self.row_pw.pack(fill="x", pady=(8, 0))

    def _reveal(self, var: tk.StringVar, btn):
        # 在明文与掩码之间切换：找到所有绑定该变量的 Entry 统一改 show
        target = str(var)

        def walk(w):
            for ch in w.winfo_children():
                try:
                    if isinstance(ch, ttk.Entry) and str(ch.cget("textvariable")) == target:
                        cur = str(ch.cget("show"))
                        ch.configure(show="" if cur else "*")
                        return not cur
                except tk.TclError:
                    pass
                got = walk(ch)
                if got is not None:
                    return got
            return None

        now_plain = walk(self)
        if now_plain:
            btn.set_text("隐藏")
        elif now_plain is False:
            btn.set_text("显示")

    # ---- 密钥选择
    def _pick_key(self):
        init = str(Path.home() / ".ssh")
        if self.v_key.get().strip():
            init = str(Path(self.v_key.get().strip()).parent)
        p = filedialog.askopenfilename(
            title="选择 SSH 私钥",
            initialdir=init,
            filetypes=[("私钥文件", "id_* *.pem *.key"), ("所有文件", "*.*")])
        if p:
            self.v_key.set(p)
            self._refresh_key_info()

    def _key_menu(self):
        self._key_cache = discover_keys()
        m = tk.Menu(self, tearoff=0, font=U.FONT_S,
                    bg=U.T.card, fg=U.T.text, activebackground=U.T.brand_soft,
                    activeforeground=U.T.brand_deep, bd=0)
        if not self._key_cache:
            m.add_command(label="（~/.ssh 下没找到私钥）", state="disabled")
        else:
            for k in self._key_cache[:24]:
                m.add_command(label=f"{k['name']}   —   {k['dir']}",
                              command=lambda p=k["path"]: self._use_key(p))
        m.add_separator()
        m.add_command(label="浏览其它位置…", command=self._pick_key)
        try:
            x = self.e_key.winfo_rootx()
            y = self.e_key.winfo_rooty() + self.e_key.winfo_height() + 2
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _use_key(self, path: str):
        self.v_key.set(path)
        self.v_mode.set("key")
        self._mode_changed()
        self._refresh_key_info()

    def _on_keypw_changed(self, *_):
        """私钥口令也是「私钥状态」的输入项：边敲边重算，带防抖。

        不这么做的话，填对口径后那行红字还挂着 —— 连接明明成功了，
        界面却一直显示错误，很难判断到底哪步出了问题。
        """
        if self._keypw_job:
            try:
                self.after_cancel(self._keypw_job)
            except Exception:  # noqa: BLE001
                pass
        self._keypw_job = self.after(450, self._refresh_key_info)

    def _refresh_key_info(self):
        self._keypw_job = None
        path = self.v_key.get().strip()
        if not path:
            self.lbl_key.configure(text="未选择私钥", fg=U.T.text_mute)
            return
        self.lbl_key.configure(text="正在读取密钥信息…", fg=U.T.text_mute)
        pp = self.v_keypw.get() or None

        def done(info):
            if not info.get("ok"):
                if info.get("format") == "ppk":
                    self.lbl_key.configure(
                        text="⚠ PuTTY .ppk 无法直接读取 —— 用 PuTTYgen 导出 OpenSSH 私钥",
                        fg=U.T.err)
                elif info.get("encrypted") and not pp:
                    # 「需要口令」是提示不是故障，用提示色，别用报警红
                    self.lbl_key.configure(text="🔑 " + info.get("error", ""),
                                           fg=U.T.warn)
                else:
                    self.lbl_key.configure(text="⚠ " + info.get("error", "无法读取"),
                                           fg=U.T.err)
                return
            txt = (f"{info['type']} {info['bits']} 位 · {info['fingerprint']}"
                   + ("  🔒 带口令" if info.get("encrypted") else ""))
            warn = chmod_600_warning(path)
            if warn:
                txt += f"\n{warn}"
            self.lbl_key.configure(text=txt, fg=U.T.warn if warn else U.T.text_dim)

        self.bg(lambda: inspect_key(path, pp), ok=done,
                err=lambda m: self.lbl_key.configure(text="⚠ " + m, fg=U.T.err))

    # ---- 档案
    def _collect_profile(self) -> dict:
        p = {
            "name": self.v_prof.get().strip(),
            "host": self.v_host.get().strip(),
            "port": self.v_port.get().strip() or "22",
            "user": self.v_user.get().strip(),
            "auth": self.v_mode.get(),
            "key_path": self.v_key.get().strip(),
            "remember": bool(self.v_remember.get()),
        }
        if p["remember"]:
            # 只有勾了「记住密码」才写口令；文件本身是密文
            p["password"] = self.v_pw.get()
            p["key_passphrase"] = self.v_keypw.get()
            p["sudo_password"] = self.v_sudopw.get()
        return p

    def _refresh_profile_list(self):
        names = self.profiles.names()
        self.cb_prof["values"] = names
        if self.v_prof.get() not in names:
            self.v_prof.set("")

    def _apply_profile(self, p: dict):
        self.v_prof.set(p.get("name", ""))
        self.v_host.set(p.get("host", ""))
        self.v_port.set(p.get("port", "22") or "22")
        self.v_user.set(p.get("user", ""))
        auth = p.get("auth", "key")
        self.v_mode.set(auth if auth in ("key", "password") else "key")
        self._mode_changed()
        self.v_key.set(p.get("key_path", "") or "")
        # 只有勾了「记住密码」的档案才带口令，其余本来就是空串
        self.v_keypw.set(p.get("key_passphrase", "") or "")
        self.v_pw.set(p.get("password", "") or "")
        self.v_sudopw.set(p.get("sudo_password", "") or "")
        self.v_remember.set(bool(p.get("remember")))
        if self.v_key.get():
            self._refresh_key_info()
        else:
            self.lbl_key.configure(text="未选择私钥", fg=U.T.text_mute)

    def _on_profile_pick(self, _evt=None):
        p = self.profiles.get(self.v_prof.get())
        if p:
            self._apply_profile(p)
            self.profiles.set_last(p["name"])
            self.status.set(f"已载入档案 {p['name']}")

    def _profile_save(self):
        name = self.v_prof.get().strip()
        if not name:
            return self._profile_save_as()
        self._do_upsert(name)

    def _profile_save_as(self):
        name = simpledialog.askstring("保存连接档案", "档案名（例如 nas-home）：",
                                      parent=self)
        if not name or not name.strip():
            return
        self._do_upsert(name.strip())

    def _do_upsert(self, name: str):
        p = self._collect_profile()
        p["name"] = name
        if not p["host"]:
            return messagebox.showwarning("缺少参数", "NAS 地址不能为空", parent=self)
        try:
            saved = self.profiles.upsert(p)
        except Exception as e:  # noqa: BLE001
            return messagebox.showerror("保存失败", str(e), parent=self)
        self.v_prof.set(saved["name"])
        self._refresh_profile_list()
        self.profiles.set_last(saved["name"])
        self.status.set(f"档案已保存：{saved['name']}")

    def _profile_delete(self):
        name = self.v_prof.get().strip()
        if not name:
            return messagebox.showinfo("未选择", "请先在下拉框里选一个档案", parent=self)
        if not messagebox.askyesno("删除档案", f"确定删除档案「{name}」？", parent=self):
            return
        self.profiles.remove(name)
        self.v_prof.set("")
        self._refresh_profile_list()
        self.status.set(f"已删除档案 {name}")

    def _profile_import(self):
        added, skipped = self.profiles.import_from_ssh_config()
        self._refresh_profile_list()
        if added:
            self.status.set(f"从 ~/.ssh/config 导入 {added} 个档案")
            messagebox.showinfo("导入完成", f"新增 {added} 个，跳过 {skipped} 个已存在。",
                                parent=self)
        else:
            messagebox.showinfo(
                "没有可导入的条目",
                "未在 ~/.ssh/config 找到可用 Host 条目。\n"
                "（含 * 或 ? 的通配 Host 会被跳过）", parent=self)

    def _load_last_profile(self):
        self._refresh_profile_list()
        last = self.profiles.last
        if last and self.profiles.get(last):
            self._apply_profile(self.profiles.get(last))
            self.status.set(f"已载入上次使用的档案 {last}")

    # ---- 连接
    def do_connect(self):
        mode = self.v_mode.get()
        if mode == "key":
            path = self.v_key.get().strip()
            if not path:
                return messagebox.showwarning("缺少参数", "请选择私钥文件", parent=self)
            # 先本地验一遍私钥。不这么做的话，paramiko 解析失败会静默转去试
            # ssh-agent 里的其他钥匙 —— 可能「连上了」，但用的根本不是这把。
            info = inspect_key(path, self.v_keypw.get() or None)
            if not info.get("ok"):
                if info.get("format") == "ppk":
                    return messagebox.showerror(
                        "私钥格式不支持",
                        "这是 PuTTY 的 .ppk 私钥，paramiko 读不了。\n\n"
                        "用 PuTTYgen 打开它 → Conversions → Export OpenSSH key，\n"
                        "再用「浏览…」选导出的那个文件。", parent=self)
                if info.get("encrypted") and not self.v_keypw.get():
                    return messagebox.showwarning(
                        "缺少私钥口令",
                        "这把私钥带口令保护，请先在「私钥口令」里填上。", parent=self)
                return messagebox.showerror(
                    "私钥不可用", info.get("error", "无法读取私钥"), parent=self)
        if mode == "password" and not self.v_pw.get():
            return messagebox.showwarning("缺少参数", "请填写登录密码", parent=self)
        try:
            port = int(self.v_port.get() or 22)
        except ValueError:
            return messagebox.showwarning("参数错误", "端口必须是数字", parent=self)
        self.status.set("正在连接…")
        self.btn_connect.set_enabled(False)

        def work():
            self.nas.connect(
                host=self.v_host.get().strip(), port=port,
                user=self.v_user.get().strip(),
                password=self.v_pw.get() if mode == "password" else None,
                key_path=self.v_key.get().strip() if mode == "key" else None,
                key_passphrase=self.v_keypw.get() or None,
            )
            sudo_ok = None
            if self.v_sudopw.get():
                sudo_ok = self.nas.sudo_ok(self.v_sudopw.get())
                if not sudo_ok:
                    self.nas.sudo_pw = None
            return sudo_ok, self.nas.probe()

        self.bg(work, ok=self._on_connected,
                err=lambda m: self.btn_connect.set_enabled(True))

    def _kv_clear(self):
        for w in self.kv_holder.winfo_children():
            w.destroy()

    def _kv(self, label: str, value: str, tone: str = "card"):
        row = U.wframe(self.kv_holder, "card")
        row.pack(fill="x", pady=2)
        row.columnconfigure(1, weight=1)
        U.wlabel(row, label, "dim", U.FONT_S).grid(row=0, column=0, sticky="nw")
        fg_role = {"ok": "ok", "err": "err", "warn": "warn", "dim": "dim"}.get(tone, "card")
        U.wlabel(row, value or "—", fg_role, U.FONT_S, justify="left",
                 wraplength=U.px(520)).grid(row=0, column=1, sticky="w", padx=(14, 0))

    def _on_connected(self, res):
        sudo_ok, info = res
        self.btn_connect.set_enabled(True)
        p = info.get("params", {})

        self._kv_clear()
        self._kv("连接", f"{self.nas.user}@{self.nas.host}:{self.nas.port}")
        # 明确写出「到底用了哪把钥匙」，避免出现「以为在用 A，其实用的是 B」
        if self.v_mode.get() == "key":
            kp = self.v_key.get().strip()
            ki = inspect_key(kp, self.v_keypw.get() or None)
            if ki.get("ok"):
                self._kv("认证方式",
                         f"私钥 {ki['type']} {ki['bits']} 位 · {ki['fingerprint']}", "ok")
            else:
                self._kv("认证方式", f"私钥 {Path(kp).name}", "dim")
            self._refresh_key_info()
        else:
            self._kv("认证方式", "登录密码", "dim")
        self._kv("sudo 提权", "成功" if sudo_ok else ("失败" if sudo_ok is False else "未提供密码"),
                 "ok" if sudo_ok else ("err" if sudo_ok is False else "dim"))
        self._kv("DSM 版本", info.get("version", ""))
        self._kv("用户组", info.get("groups", ""))
        self._kv("配置文件", info.get("conf_path", ""))
        self._kv("证书目录", info.get("keys_dir", ""))
        vm = info.get("verify_mode")
        self._kv("客户端证书校验",
                 "关闭（verify-client-cert none）" if vm == "none"
                 else ("要求证书" if vm == "require" else "未知"),
                 "err" if vm == "none" else ("ok" if vm == "require" else "dim"))
        self._kv("CRL 配置", "已启用" if info.get("has_crl_line") else "未启用",
                 "ok" if info.get("has_crl_line") else "warn")
        self._kv("ta.key", "已读取" if info.get("ta_key_ok") else "未读取",
                 "ok" if info.get("ta_key_ok") else "warn")
        self._kv("监听", info.get("listening", ""))
        self._kv("服务端参数", str(p))
        self.box_conf.set_text(info.get("conf", "") or "(空)")

        self.pill_nas.set(f"{self.nas.user}@{self.nas.host}", "ok")
        self.status.set("连接成功")
        if info.get("ta_key_ok"):
            self.box_ta.txt.delete("1.0", "end")
            self.box_ta.txt.insert("1.0", info.get("ta_key", ""))
        if p.get("port"):
            self.v_pport.set(str(p["port"]))
        if p.get("auth"):
            self.v_pauth.set(p["auth"])
        if p.get("proto"):
            self.v_pproto.set("tcp-client" if str(p["proto"]).startswith("tcp") else "udp")

    def do_disconnect(self):
        self.nas.close()
        self.nas.sudo_pw = None
        self.pill_nas.set("NAS 未连接", "err")
        self.status.set("已断开")

    # ============================================================ 2 · 证书
    def _build_cert(self, pg: tk.Frame):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(2, weight=1)

        ca = U.Card(pg, title="私有 CA", hint="只需建一次")
        ca.grid(row=0, column=0, sticky="we")
        b = ca.body
        b.columnconfigure(1, weight=1)
        self.v_cacn = tk.StringVar(value="Client Cert CA")
        self.v_cadays = tk.StringVar(value="3650")
        self.v_cabits = tk.StringVar(value="3072")
        self.v_capw = tk.StringVar()
        self.v_caover = tk.BooleanVar(value=False)

        U.wlabel(b, "CA 名称 (CN)", "card", U.FONT_S).grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(b, textvariable=self.v_cacn).grid(row=0, column=1, sticky="we",
                                                    padx=(10, 8), pady=3)
        U.wlabel(b, "有效期(天)", "card", U.FONT_S).grid(row=0, column=2, sticky="w")
        ttk.Entry(b, textvariable=self.v_cadays, width=8).grid(row=0, column=3, padx=(8, 8))
        U.wlabel(b, "密钥长度", "card", U.FONT_S).grid(row=0, column=4, sticky="w")
        ttk.Combobox(b, textvariable=self.v_cabits, values=["2048", "3072", "4096"],
                     width=7, state="readonly").grid(row=0, column=5, padx=(8, 8))
        U.wlabel(b, "CA 口令", "card", U.FONT_S).grid(row=0, column=6, sticky="w")
        ttk.Entry(b, textvariable=self.v_capw, show="*", width=15).grid(row=0, column=7,
                                                                        padx=(8, 0))

        r = U.wframe(b, "card")
        r.grid(row=1, column=0, columnspan=8, sticky="w", pady=(11, 0))
        U.RoundButton(r, "创建 CA", self.do_init_ca, variant="primary",
                      height=32, padx=17).pack(side="left")
        U.Check(r, "覆盖已有 CA（旧证书全部失效）", self.v_caover).pack(side="left", padx=14)
        self.lbl_ca_info = U.wlabel(r, "尚未初始化", "dim", U.FONT_XS)
        self.lbl_ca_info.pack(side="left", padx=10)

        iss = U.Card(pg, title="签发证书", hint="客户端与服务端通用")
        iss.grid(row=1, column=0, sticky="we", pady=(10, 0))
        b = iss.body
        b.columnconfigure(1, weight=1)
        self.v_isname = tk.StringVar()
        self.v_iskind = tk.StringVar(value="client")
        self.v_iscn = tk.StringVar()
        self.v_isdays = tk.StringVar(value="825")
        self.v_ispw = tk.StringVar()
        self.v_issan = tk.StringVar()

        U.wlabel(b, "名称", "card", U.FONT_S).grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(b, textvariable=self.v_isname).grid(row=0, column=1, sticky="we",
                                                     padx=(10, 8), pady=3)
        U.wlabel(b, "类型", "card", U.FONT_S).grid(row=0, column=2, sticky="w")
        cb = ttk.Combobox(b, textvariable=self.v_iskind, width=9, state="readonly",
                          values=["client", "server"])
        cb.grid(row=0, column=3, padx=(8, 8))
        cb.bind("<<ComboboxSelected>>", lambda e: self._kind_changed())
        U.wlabel(b, "CN", "card", U.FONT_S).grid(row=0, column=4, sticky="w")
        ttk.Entry(b, textvariable=self.v_iscn, width=18).grid(row=0, column=5, padx=(8, 8))
        U.wlabel(b, "有效期(天)", "card", U.FONT_S).grid(row=0, column=6, sticky="w")
        ttk.Entry(b, textvariable=self.v_isdays, width=8).grid(row=0, column=7, padx=(8, 0))

        r = U.wframe(b, "card")
        r.grid(row=1, column=0, columnspan=8, sticky="we", pady=(9, 0))
        U.wlabel(r, "私钥口令", "card", U.FONT_S).pack(side="left")
        ttk.Entry(r, textvariable=self.v_ispw, show="*", width=16).pack(side="left", padx=(8, 0))
        self.lbl_san = U.wlabel(r, "服务端 SAN/域名", "card", U.FONT_S)
        self.lbl_san.pack(side="left", padx=(18, 0))
        self.e_san = ttk.Entry(r, textvariable=self.v_issan, width=24)
        self.e_san.pack(side="left", padx=(8, 0))
        self.e_san.state(["disabled"])
        U.RoundButton(r, "签发", self.do_issue, variant="primary",
                      height=32, padx=17).pack(side="left", padx=14)
        U.wlabel(r, "服务端证书 CN/SAN 必须等于客户端 remote 地址",
                 "dim", U.FONT_XS).pack(side="left")

        lst = U.Card(pg, title="已签发证书", hint="选中后可导出或吊销")
        lst.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        b = lst.body

        cols = ("name", "kind", "cn", "not_after", "left", "state")
        self.tree = ttk.Treeview(b, columns=cols, show="headings", height=5)
        sb = ttk.Scrollbar(b, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        for c, t, w in (("name", "名称", 150), ("kind", "类型", 70), ("cn", "CN", 200),
                        ("not_after", "到期", 100), ("left", "剩余", 70), ("state", "状态", 80)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=U.px(w), anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        r = U.wframe(pg, "page")
        r.grid(row=3, column=0, sticky="we", pady=(10, 0))
        U.RoundButton(r, "导出证书（crt + key）", self.do_export_cert,
                      variant="secondary", height=34, padx=16).pack(side="left")
        U.RoundButton(r, "吊销所选", self.do_revoke, variant="danger",
                      height=34, padx=16).pack(side="left", padx=8)
        U.RoundButton(r, "重建 CRL", self.do_rebuild_crl, variant="secondary",
                      height=34, padx=16).pack(side="left")
        self.lbl_crl = U.wlabel(r, "", "pagedim", U.FONT_S)
        self.lbl_crl.pack(side="left", padx=14)
        U.RoundButton(r, "打开输出目录", lambda: os.startfile(str(self.pki.out_dir)),
                      variant="ghost", height=34, padx=14).pack(side="right")

    def _kind_changed(self):
        if self.v_iskind.get() == "server":
            self.e_san.state(["!disabled"])
            self.lbl_san.configure(fg=U.T.text)
        else:
            self.e_san.state(["disabled"])
            self.lbl_san.configure(fg=U.T.text_dim)

    def do_init_ca(self):
        if self.pki.has_ca() and not self.v_caover.get():
            return messagebox.showwarning(
                "CA 已存在",
                "已经有一个 CA 了。\n如果确实要重建（会让所有已发证书失效），"
                "请勾选「覆盖已有 CA」。", parent=self)
        if self.v_caover.get() and not messagebox.askyesno(
                "确认重建 CA", "重建 CA 会让现有全部客户端证书失效，且不可恢复。确定继续？",
                parent=self):
            return
        self.status.set("正在生成 CA（3072 位约需数秒）…")

        def work():
            return self.pki.init_ca(self.v_cacn.get().strip() or "Client Cert CA",
                                    int(self.v_cadays.get() or 3650),
                                    int(self.v_cabits.get() or 3072),
                                    self.v_capw.get() or None,
                                    self.v_caover.get())
        self.bg(work, ok=lambda ca: (self.refresh_all(),
                                     self.status.set(f"CA 已创建：{ca['cn']}")))

    def do_issue(self):
        name = self.v_isname.get().strip()
        if not name:
            return messagebox.showwarning("缺少参数", "请填写名称", parent=self)
        kind = self.v_iskind.get()

        def work():
            return self.pki.issue(name=name, kind=kind,
                                  cn=self.v_iscn.get().strip() or None,
                                  days=int(self.v_isdays.get() or 825),
                                  passphrase=self.v_ispw.get() or None,
                                  ca_passphrase=self.v_capw.get() or None,
                                  san=self.v_issan.get().strip() or None)
        self.bg(work, ok=lambda c: (self.refresh_all(),
                                    self.status.set(f"已签发 {c['name']}（{c['kind']}）")))

    def do_revoke(self):
        sel = self.tree.selection()
        if not sel:
            return messagebox.showwarning("未选择", "请先在列表里选中要吊销的证书", parent=self)
        name = self.tree.item(sel[0], "values")[0]
        if not messagebox.askyesno(
                "确认吊销",
                f"确定吊销 {name}？\n该设备的配置文件将立刻无法连接（需重新部署 crl.pem）。",
                parent=self):
            return
        self.bg(lambda: self.pki.revoke(name, ca_passphrase=self.v_capw.get() or None),
                ok=lambda r: (self.refresh_all(),
                              self.status.set(f"已吊销 {name}，CRL v{r['crl_number']}")))

    def do_rebuild_crl(self):
        self.bg(lambda: self.pki.build_crl(ca_passphrase=self.v_capw.get() or None),
                ok=lambda n: (self.refresh_all(), self.status.set(f"CRL 已重建，版本 {n}")))

    def do_export_cert(self):
        sel = self.tree.selection()
        if not sel:
            return messagebox.showwarning("未选择", "请先选中证书", parent=self)
        names = [self.tree.item(i, "values")[0] for i in sel]
        files = []
        for n in names:
            files += self.pki.export_cert_files(n)
        self.status.set(f"已导出 {len(files)} 个文件到 {self.pki.out_dir}")
        if messagebox.askyesno(
                "导出完成",
                "已导出：\n" + "\n".join(f["file"] for f in files) +
                f"\n\n目录：{self.pki.out_dir}\n\n现在打开目录？", parent=self):
            os.startfile(str(self.pki.out_dir))

    # ============================================================ 3 · 打包
    def _build_pack(self, pg: tk.Frame):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(2, weight=1)

        par = U.Card(pg, title="连接参数", hint="与 NAS 上的 openvpn.conf 保持一致")
        par.grid(row=0, column=0, sticky="we")
        b = par.body
        b.columnconfigure(1, weight=1)
        self.v_premote = tk.StringVar(value="chromas.synology.me")
        self.v_pport = tk.StringVar(value="11094")
        self.v_pproto = tk.StringVar(value="tcp-client")
        self.v_pcipher = tk.StringVar(value="AES-256-CBC")
        self.v_pauth = tk.StringVar(value="SHA512")
        self.v_pverify = tk.StringVar(value="chromas.synology.me")
        self.v_plzo = tk.BooleanVar(value=True)

        # 一行塞七个字段在 1220 宽的独立窗口里刚好，内嵌后只剩 1000 出头就会
        # 把带 weight 的那一列压成 0 —— 表现出来就是「远程地址」只剩最后一个字母。
        # 折成两行，每行的固定宽度加起来都留足余量。
        b.columnconfigure(1, weight=1, minsize=U.px(150))

        U.wlabel(b, "远程地址", "card", U.FONT_S).grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(b, textvariable=self.v_premote).grid(row=0, column=1, sticky="we",
                                                      padx=(10, 10), pady=3)
        U.wlabel(b, "端口", "card", U.FONT_S).grid(row=0, column=2, sticky="w")
        ttk.Entry(b, textvariable=self.v_pport, width=8).grid(row=0, column=3, padx=(8, 10))
        U.wlabel(b, "协议", "card", U.FONT_S).grid(row=0, column=4, sticky="w")
        ttk.Combobox(b, textvariable=self.v_pproto, width=11, state="readonly",
                     values=["tcp-client", "udp"]).grid(row=0, column=5, sticky="w",
                                                        padx=(8, 0))

        U.wlabel(b, "加密", "card", U.FONT_S).grid(row=1, column=0, sticky="w", pady=(8, 3))
        ttk.Entry(b, textvariable=self.v_pcipher, width=13).grid(
            row=1, column=1, sticky="w", padx=(10, 10), pady=(8, 3))
        U.wlabel(b, "HMAC", "card", U.FONT_S).grid(row=1, column=2, sticky="w", pady=(8, 3))
        ttk.Entry(b, textvariable=self.v_pauth, width=10).grid(
            row=1, column=3, sticky="w", padx=(8, 10), pady=(8, 3))
        U.wlabel(b, "verify-x509-name", "card", U.FONT_S).grid(
            row=1, column=4, sticky="w", pady=(8, 3))
        ttk.Entry(b, textvariable=self.v_pverify, width=22).grid(
            row=1, column=5, sticky="we", padx=(8, 0), pady=(8, 3))

        U.Check(b, "启用 comp-lzo", self.v_plzo).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        pl = U.Card(pg, title="导出平台", hint="每个平台一份配置文件，注释写明该平台放置路径")
        pl.grid(row=1, column=0, sticky="we", pady=(10, 0))
        b = pl.body
        self.plat_vars: dict[str, tk.BooleanVar] = {}
        # 复选框只写平台名（各平台用哪个客户端写在该平台配置文件的注释里），
        # 名字短了，三列两行刚好排平，不用再挤成两列。
        for i, key in enumerate(PLATFORM_ORDER):
            v = tk.BooleanVar(value=True)
            self.plat_vars[key] = v
            U.Check(b, PLATFORMS[key]["label"], v).grid(
                row=i // 3, column=i % 3, sticky="w", padx=(0, 40), pady=2)

        mid = U.wframe(pg, "page")
        mid.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        mid.columnconfigure(0, weight=3, minsize=U.px(300))
        mid.columnconfigure(1, weight=2)
        mid.rowconfigure(0, weight=1)

        dev = U.Card(mid, title="选择设备", hint="吊销后自动消失")
        dev.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.dev_holder = U.wframe(dev.body, "card")
        self.dev_holder.pack(fill="both", expand=True)

        ta = U.Card(mid, title="tls-auth", hint="留空则连接 NAS 时自动读取")
        ta.grid(row=0, column=1, sticky="nsew")
        self.box_ta = U.TextBox(ta.body, role="alt", font=U.MONO_S, height=6,
                                readonly=False, wrap="none")
        self.box_ta.txt.configure(width=1)   # 别用它自己的 80 字符请求宽度抢列
        self.box_ta.pack(fill="both", expand=True)

        act = U.wframe(pg, "page")
        act.grid(row=3, column=0, sticky="we", pady=(10, 0))
        U.RoundButton(act, "打包选中设备", self.do_pack, variant="primary",
                      height=34, padx=18).pack(side="left")
        U.wlabel(act, "单设备完整包", "pagedim", U.FONT_S).pack(side="left", padx=(18, 6))
        self.v_bundle = tk.StringVar()
        self.cb_bundle = ttk.Combobox(act, textvariable=self.v_bundle, width=20,
                                      state="readonly")
        self.cb_bundle.pack(side="left")
        U.RoundButton(act, "导出 zip", self.do_bundle, variant="secondary",
                      height=34, padx=16).pack(side="left", padx=8)
        U.RoundButton(act, "打开输出目录", lambda: os.startfile(str(self.pki.out_dir)),
                      variant="ghost", height=34, padx=14).pack(side="right")

        out = U.Card(pg, title="生成结果")
        out.grid(row=4, column=0, sticky="we", pady=(10, 0))
        self.box_pack = U.TextBox(out.body, role="alt", font=U.MONO_S, height=6)
        self.box_pack.txt.configure(width=1)
        self.box_pack.pack(fill="both", expand=True)

    def _pack_params(self) -> dict:
        return dict(remote=self.v_premote.get().strip(),
                    port=int(self.v_pport.get() or 11094),
                    proto=self.v_pproto.get(),
                    cipher=self.v_pcipher.get().strip() or None,
                    auth=self.v_pauth.get().strip() or "SHA512",
                    comp_lzo=self.v_plzo.get(),
                    tls_auth=self.box_ta.get("1.0", "end").strip(),
                    verify_name=self.v_pverify.get().strip() or None, extra="")

    def do_pack(self):
        names = [n for n, v in getattr(self, "dev_vars", {}).items() if v.get()]
        plats = [k for k, v in self.plat_vars.items() if v.get()]
        if not names:
            return messagebox.showwarning("未选择", "请至少勾选一台设备", parent=self)
        if not plats:
            return messagebox.showwarning("未选择", "请至少勾选一个平台", parent=self)
        if not self.v_premote.get().strip():
            return messagebox.showwarning("缺少参数", "请填写远程地址", parent=self)
        params = self._pack_params()
        ta = params["tls_auth"]
        made = []
        for n in names:
            for p in plats:
                made.append(self.pki.build_ovpn(name=n, platform=p, **params))
        self.box_pack.clear()
        for m in made:
            self.box_pack.txt.insert("end", f"✓ {m['file']}   ({m['bytes']} 字节)\n")
        if not ta:
            self.box_pack.txt.insert("end", "⚠ 未包含 tls-auth（ta.key 为空）\n")
        self.box_pack.txt.insert("end", f"\n输出目录：{self.pki.out_dir}\n")
        self.status.set(f"已生成 {len(made)} 份配置文件")

    def do_bundle(self):
        name = self.v_bundle.get()
        if not name:
            return messagebox.showwarning("未选择", "请选择要打包的设备", parent=self)
        plats = [k for k, v in self.plat_vars.items() if v.get()]
        res = self.pki.export_bundle(name=name, platforms=plats, **self._pack_params())
        self.box_pack.clear()
        self.box_pack.txt.insert(
            "end", f"✓ {res['file']}   ({res['bytes']} 字节, {len(res['platforms'])} 个平台)\n"
                   f"含：{name}.crt / {name}.key / 各平台配置文件 / README.txt\n\n"
                   f"目录：{self.pki.out_dir}\n")
        self.status.set("已导出设备包")

    # ============================================================ 4 · 部署
    def _build_deploy(self, pg: tk.Frame):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(3, weight=1)

        box = U.Card(pg, title="部署到 NAS", hint="需已连接 + sudo")
        box.grid(row=0, column=0, sticky="we")
        b = box.body
        b.columnconfigure(1, weight=1)
        self.v_srv = tk.StringVar()
        U.wlabel(b, "服务端证书", "card", U.FONT_S).grid(row=0, column=0, sticky="w")
        self.cb_srv = ttk.Combobox(b, textvariable=self.v_srv, width=32, state="readonly")
        self.cb_srv.grid(row=0, column=1, sticky="w", padx=(10, 0))
        U.wlabel(b, "替换 NAS 上的 server.crt / server.key", "dim", U.FONT_XS).grid(
            row=0, column=2, sticky="w", padx=(12, 0))

        self.v_bak = tk.BooleanVar(value=True)
        self.v_rcrl = tk.BooleanVar(value=True)
        self.v_restart = tk.BooleanVar(value=True)
        r = U.wframe(b, "card")
        r.grid(row=1, column=0, columnspan=3, sticky="w", pady=(11, 0))
        U.Check(r, "部署前备份", self.v_bak).pack(side="left")
        U.Check(r, "推送 crl.pem", self.v_rcrl).pack(side="left", padx=20)
        U.Check(r, "重启 VPNCenter", self.v_restart).pack(side="left")

        r = U.wframe(b, "card")
        r.grid(row=2, column=0, columnspan=3, sticky="w", pady=(11, 0))
        U.RoundButton(r, "开始部署", self.do_deploy, variant="primary",
                      height=34, padx=18).pack(side="left")
        U.RoundButton(r, "仅重新应用配置", self.do_reapply, variant="secondary",
                      height=34, padx=16).pack(side="left", padx=8)

        warn = U.Card(pg, pad=12)
        warn.grid(row=1, column=0, sticky="we", pady=(10, 0))
        w1 = U.wlabel(warn.body,
                      "⚠ 替换服务端证书后，所有旧客户端配置会立即失效。"
                      "请先签好证书、分发新配置文件，最后才部署。",
                      "err", U.FONT_S, justify="left")
        w1.pack(anchor="w", fill="x")
        w2 = U.wlabel(warn.body,
                      "⚠ openvpn.conf 由 DSM 动态生成，网页上改任何 OpenVPN 设置都会"
                      "覆盖掉证书校验与 CRL 配置，届时点「仅重新应用配置」恢复。",
                      "warn", U.FONT_S, justify="left")
        w2.pack(anchor="w", fill="x", pady=(4, 0))
        wrap_to(warn.body, w1, w2)

        out = U.Card(pg, title="部署输出")
        out.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        self.box_deploy = U.TextBox(out.body, role="alt", font=U.MONO_S, height=8)
        self.box_deploy.pack(fill="both", expand=True)

    def _deploy_out(self, text: str):
        self.box_deploy.txt.insert("end", redact.scrub(text) + "\n")
        self.box_deploy.txt.see("end")

    def _need_root(self) -> bool:
        if not self.nas.connected:
            messagebox.showwarning("未连接", "请先在「连接」页连上 NAS", parent=self)
            return False
        if not self.nas.sudo_pw:
            messagebox.showwarning("缺少 sudo", "该操作需要 root，请在连接页填写 sudo 密码并重新连接",
                                   parent=self)
            return False
        return True

    def do_deploy(self):
        if not self._need_root():
            return
        srv = self.v_srv.get()
        if not srv:
            return messagebox.showwarning("未选择", "请选择服务端证书", parent=self)
        if not messagebox.askyesno(
                "确认部署",
                "将替换 NAS 上的 CA / 服务端证书并重启隧道服务。\n"
                "所有旧客户端配置会立即失效。确定继续？", parent=self):
            return
        self.box_deploy.clear()
        self.status.set("部署中…")

        def work():
            mat = self.pki.read_material(srv)
            steps = []
            if self.v_bak.get():
                steps.append(f"备份完成：{self.nas.backup()}")
            steps += self.nas.deploy_material(
                ca_pem=self.pki.ca_pem(), server_crt=mat["cert"], server_key=mat["key"],
                crl_pem=self.pki.crl_pem() if (self.v_rcrl.get() and self.pki.crl_pem()) else None)
            steps += self.nas.patch_conf()
            log = ""
            if self.v_restart.get():
                log = self.nas.restart()
                steps.append("已重启 VPNCenter")
            return steps, log
        self.bg(work, ok=self._on_deployed)

    def _on_deployed(self, res):
        steps, log = res
        for s in steps:
            self._deploy_out("✓ " + s)
        if log:
            self._deploy_out("\n" + log)
        self.status.set("部署完成")

    def do_reapply(self):
        if not self._need_root():
            return
        from nasops import REAPPLY, REAPPLY_SH

        def work():
            self.nas.write_text(REAPPLY, REAPPLY_SH, "755")
            return self.nas.run(
                f"sh {REAPPLY} && grep -nE 'verify-client-cert|crl-verify' "
                f"/var/packages/VPNCenter/etc/openvpn/openvpn.conf", sudo=True)
        self.bg(work, ok=lambda r: (self._deploy_out("✓ 已重新应用配置\n" + r[1]),
                                    self.status.set("配置已重新应用")))

    # ============================================================ 5 · 命令日志
    def _build_console(self, pg: tk.Frame):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        bar = U.wframe(pg, "page")
        bar.grid(row=0, column=0, sticky="we", pady=(0, 8))
        U.wlabel(bar, "应用执行的每条 SSH 命令与原始输出都会记在这里",
                 "pagedim", U.FONT_S).pack(side="left")
        U.RoundButton(bar, "清空", self._clear_console, variant="ghost",
                      font=U.FONT_S, height=29, padx=13).pack(side="right")
        U.RoundButton(bar, "复制全部", self._copy_console, variant="secondary",
                      font=U.FONT_S, height=29, padx=13).pack(side="right", padx=6)

        card = U.Card(pg, title="命令日志", hint="需已连接 + sudo 密码")
        card.grid(row=1, column=0, sticky="nsew")

        # 输入行先占住卡片底部，日志再长也挤不掉它
        b = tk.Frame(card.body, bg=U.T.card, bd=0)
        b.pack(side="bottom", fill="x", pady=(U.px(10), 0))
        tk.Frame(b, bg=U.T.border, height=1).pack(fill="x", pady=(0, U.px(10)))

        self.v_manual = tk.StringVar()
        self.v_msudo = tk.BooleanVar(value=True)
        U.wlabel(b, "手动执行", "mute", U.FONT_S).pack(side="left", padx=(0, 8))
        e = ttk.Entry(b, textvariable=self.v_manual, font=U.MONO)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.do_manual())
        U.Check(b, "sudo", self.v_msudo).pack(side="left", padx=9)
        U.RoundButton(b, "执行", self.do_manual, variant="primary",
                      height=32, padx=17).pack(side="left")

        self.box_cons = U.TextBox(card.body, role="console", font=U.MONO,
                                  height=10, readonly=True, wrap="word", pady=10)
        self.box_cons.pack(side="top", fill="both", expand=True)
        self.txt_cons = self.box_cons.txt
        self._apply_console_tags()

        self._cons_write("meta", "[就绪] 这里会实时显示 paramiko 建立的连接与命令往返。\n")

    def _apply_console_tags(self):
        t = getattr(self, "txt_cons", None)
        if t is None:
            return
        t.tag_configure("cmd", foreground=U.T.console_cmd)
        t.tag_configure("ok", foreground=U.T.console_ok)
        t.tag_configure("bad", foreground=U.T.console_bad)
        t.tag_configure("meta", foreground=U.T.console_meta)
        t.tag_configure("out", foreground=U.T.console_fg)

    def _cons_write(self, tag: str, text: str):
        # 控制台的唯一写入出口，脱敏放在这一层，谁调都漏不出去
        self.txt_cons.insert("end", redact.scrub(text), tag)
        self.txt_cons.see("end")

    def _clear_console(self):
        self.txt_cons.delete("1.0", "end")

    def _copy_console(self):
        self.clipboard_clear()
        self.clipboard_append(self.txt_cons.get("1.0", "end"))
        self.status.set("已复制全部日志")

    # 私钥 / ta.key / ppk 正文：任何情况下都不进界面。
    # 规则本体在 core/redact.py，这里只是保留一个类属性给自测用。
    _SECRET_BLOCK = redact.PEM

    def _scrub(self, text: str) -> str:
        """把私钥正文与 ta.key / ppk 正文整块打码，只留首尾标记。"""
        return redact.scrub(text)

    def _trace_cmd(self, display, rc, out, err, ms):
        """由工作线程调用，转到主线程渲染。"""
        self.post(self._render_trace, display, rc, out, err, ms)

    def _render_trace(self, display, rc, out, err, ms):
        self._cons_write("meta", time.strftime("[%H:%M:%S] "))
        self._cons_write("ok" if rc == 0 else "bad", f"rc={rc}  {ms}ms\n")
        self._cons_write("cmd", "$ " + self._scrub(display) + "\n")
        out, err = self._scrub(out), self._scrub(err)
        if out.strip():
            self._cons_write("out", out if out.endswith("\n") else out + "\n")
        if err.strip():
            self._cons_write("bad", err if err.endswith("\n") else err + "\n")
        self._cons_write("meta", "\n")

    def do_manual(self):
        cmd = self.v_manual.get().strip()
        if not cmd:
            return
        if not self.nas.connected:
            return messagebox.showwarning("未连接", "请先在「连接」页连上 NAS", parent=self)
        if self.v_msudo.get() and not self.nas.sudo_pw:
            return messagebox.showwarning("缺少 sudo", "勾了 sudo 但没有 sudo 密码，请先连接时填写",
                                          parent=self)
        self.v_manual.set("")
        self.bg(lambda: self.nas.run(cmd, sudo=self.v_msudo.get()),
                ok=lambda r: self.status.set(f"命令结束，rc={r[0]}"))

    # ============================================================ 刷新
    def refresh_all(self):
        st = self.pki.state()
        ca = st.get("ca")
        if ca:
            self.pill_ca.set(f"CA · {ca['cn']}", "ok")
            self.lbl_ca_info.configure(
                text=f"CN={ca['cn']} · {ca['key_size']} 位 · SHA256 {ca['fingerprint'][:24]}…")
        else:
            self.pill_ca.set("CA 未初始化", "mute")
            self.lbl_ca_info.configure(text="尚未初始化")
        self.lbl_crl.configure(text=f"CRL 版本 v{st.get('crl_number', 0)}")

        self.tree.delete(*self.tree.get_children())
        for c in st.get("certs", []):
            if c.get("revoked"):
                state = "已吊销"
            elif c.get("expired"):
                state = "已过期"
            else:
                state = "有效"
            self.tree.insert("", "end", values=(
                c["name"], c["kind"], c["cn"], (c.get("not_after") or "")[:10],
                f"{c.get('days_left')} 天" if c.get("days_left") is not None else "—", state))

        for w in getattr(self, "_dev_widgets", []):
            w.destroy()
        self._dev_widgets = []
        self.dev_vars: dict[str, tk.BooleanVar] = {}
        clients = [c for c in st.get("certs", []) if c["kind"] == "client" and not c.get("revoked")]
        if not clients:
            lbl = U.wlabel(self.dev_holder, "暂无可用设备证书，请先在「证书」页签发",
                           "dim", U.FONT_S)
            lbl.grid(row=0, column=0, sticky="w")
            self._dev_widgets.append(lbl)
        for i, c in enumerate(clients):
            v = tk.BooleanVar(value=True)
            self.dev_vars[c["name"]] = v
            cb = U.Check(self.dev_holder,
                         c["name"] + (" 🔒" if c.get("encrypted") else ""), v)
            cb.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 18), pady=2)
            self._dev_widgets.append(cb)

        servers = [c["name"] for c in st.get("certs", [])
                   if c["kind"] == "server" and not c.get("revoked")]
        self.cb_srv["values"] = servers
        if servers and self.v_srv.get() not in servers:
            self.v_srv.set(servers[0])
        names = [c["name"] for c in clients]
        self.cb_bundle["values"] = names
        if self.v_bundle.get() not in names:
            self.v_bundle.set(names[0] if names else "")


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if "--dark" in sys.argv:
        U.T = U.DARK

    root = tk.Tk()
    root.title("Client Cert Console")
    root.geometry(f"{U.px(1220)}x{U.px(830)}")
    root.minsize(U.px(1080), U.px(700))
    root.configure(bg=U.T.page)

    app = App(root)
    app.pack(fill="both", expand=True)
    app.boot()

    # --page cert|pack|deploy|cons  直接落到某一页（便于截图 / 排查）
    if "--page" in sys.argv:
        i = sys.argv.index("--page")
        if i + 1 < len(sys.argv) and sys.argv[i + 1] in app._pages:
            app.nav.select(sys.argv[i + 1])

    if "--smoke" in sys.argv:
        root.after(1200, root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
