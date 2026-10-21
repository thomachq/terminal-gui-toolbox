# -*- coding: utf-8 -*-
"""文件管理页 —— 参考 MobaXterm 的 SFTP 面板。

MobaXterm 的取法是「一个远端浏览器 + 上传 / 下载按钮」，WinSCP 的取法是
「左右双栏、双击即传」。这一页两种都留：

  设备  只看远端（默认，列表能占满高度，跟 MobaXterm 一样）
  本机  只看本机
  双栏  上下各一半（跟 WinSCP 一样，可以对着挑、双击即传）

为什么不是左右分栏：这一页和命令日志并排，横向只剩半屏，
左右分栏后每边塞不下文件名；上下分栏每栏都能用满宽度。

传输全在后台线程跑，进度用自绘细条（ttk.Progressbar 在 Windows
原生主题下不给改颜色）。可以一次排多个任务，按队列顺序传。
"""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import filedialog, ttk

import wbtui as U
from core import files as fl
from core.metrics import human_bytes
from ui import forms
from ui.table import Column, DataTable, Segmented

COLS = [
    Column("name", "名称", 240, stretch=True),
    Column("size", "大小", 92, anchor="e"),
    Column("mtime", "修改时间", 132),
    Column("mode", "权限", 108, mono=True),
]

UP_LABEL = ".. 上一级"


class FilesPage:
    # ------------------------------------------------------------ 构建
    def build_files(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(2, weight=1)

        self._fl_local = fl.LocalFS()
        self._fl_remote = None
        self._fl_busy = False
        self._fl_cancel = False
        self._fl_jobs: list[dict] = []
        self._fl_cur: dict | None = None
        self._fl_pct = 0.0
        self._fl_last_post = 0.0
        self._fl_win = fl.find_winscp()
        self._fl_mode = "remote"

        # ---- 概况 + 动作
        c = U.Card(pg, title="文件管理",
                   hint="SFTP · 双击即传 · 目录递归")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        head = U.wframe(b, "card")
        head.pack(fill="x")
        self.lbl_fl_side = U.wlabel(head, "未连接设备", "card", U.FONT_B)
        self.lbl_fl_side.pack(side="left")
        self.badge_fl = U.Pill(head, "未连接", "mute", height=24)
        self.badge_fl.pack(side="left", padx=(U.px(10), 0))
        self.lbl_fl_hint = U.wlabel(head, "", "mute", U.FONT_XS)
        self.lbl_fl_hint.pack(side="right")

        row = U.wframe(b, "card")
        row.pack(fill="x", pady=(U.px(10), 0))
        self.btn_fl_up = U.RoundButton(row, "上传 →", self.fl_upload,
                                       variant="primary", height=32, padx=14)
        self.btn_fl_up.pack(side="left")
        self.btn_fl_down = U.RoundButton(row, "← 下载", self.fl_download,
                                         variant="primary", height=32, padx=14)
        self.btn_fl_down.pack(side="left", padx=(U.px(8), 0))
        self.btn_fl_win = U.RoundButton(row, "WinSCP", self.fl_winscp,
                                        variant="ghost", height=32, padx=12)
        self.btn_fl_win.pack(side="left", padx=(U.px(8), 0))
        if not self._fl_win:
            self.btn_fl_win.set_enabled(False)

        # 进度条跟按钮共用一行：这一页要塞两个列表，纵向一寸都不能浪费。
        # 顺序很要紧：先左后右再中间伸展，否则「取消」会被伸展条挤没。
        self.btn_fl_stop = U.RoundButton(row, "取消", self._fl_abort,
                                         variant="danger", height=26, padx=10)
        self.btn_fl_stop.pack(side="right")
        self.btn_fl_stop.set_enabled(False)
        self._fl_track = tk.Frame(row, bg=U.T.alt, height=U.px(6), bd=0)
        self._fl_track.pack(side="left", fill="x", expand=True,
                            padx=(U.px(12), U.px(10)))
        self._fl_fill = tk.Frame(self._fl_track, bg=U.T.brand, height=U.px(6), bd=0)
        self._fl_fill.place(x=0, y=0, relwidth=0, relheight=1)
        self._fl_track.bind("<Configure>", lambda _e: self._fl_paint_bar())
        U.register(self, self._fl_theme)

        self.lbl_fl_prog = U.wlabel(b, "空闲", "dim", U.FONT_XS)
        self.lbl_fl_prog.pack(anchor="w", pady=(U.px(6), 0))

        seg_row = U.wframe(pg, "page")
        seg_row.grid(row=1, column=0, sticky="we", pady=(U.px(10), 0))
        self.seg_fl = Segmented(
            seg_row, [("remote", "设备"), ("local", "本机"), ("both", "双栏")],
            command=self._fl_set_mode)
        self.seg_fl.pack(side="left")

        holder = U.wframe(pg, "page")
        holder.grid(row=2, column=0, sticky="nsew", pady=(U.px(8), 0))
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=3)
        holder.rowconfigure(1, weight=2)
        self._fl_holder = holder

        self._fl_card_r = U.Card(holder, title="设备（远程）",
                                 hint="双击进目录 / 双击文件即下载")
        self._fl_card_l = U.Card(holder, title="本机（本地）", hint="双击文件即上传")

        self._fl_remote_ui = self._fl_pane_build(
            self._fl_card_r.body, "remote", height=9,
            empty_title="还没有连上设备",
            empty_hint="先在「连接与设备」用 SSH 连上目标设备，再回来浏览文件。")
        self._fl_local_ui = self._fl_pane_build(
            self._fl_card_l.body, "local", height=7,
            empty_title="本机目录为空",
            empty_hint="点路径栏旁边的「刷新」，或直接在上面输入路径。")

        self._fl_place()
        self._fl_sync_buttons()
        self.after(300, self.files_sync)

    # ------------------------------------------------------------ 布局
    def _fl_set_mode(self, mode: str):
        self._fl_mode = mode
        self._fl_place()
        self._fl_sync_buttons()

    def _fl_place(self):
        """按当前模式摆两张卡。单栏时另一张整块 grid_forget，不留空条。"""
        for card in (self._fl_card_r, self._fl_card_l):
            card.grid_forget()
        if self._fl_mode == "remote":
            self._fl_card_r.grid(row=0, column=0, rowspan=2, sticky="nsew")
        elif self._fl_mode == "local":
            self._fl_card_l.grid(row=0, column=0, rowspan=2, sticky="nsew")
        else:
            self._fl_card_r.grid(row=0, column=0, sticky="nsew")
            self._fl_card_l.grid(row=1, column=0, sticky="nsew",
                                 pady=(U.px(8), 0))

    def _fl_local_visible(self) -> bool:
        return self._fl_mode in ("local", "both")

    # ------------------------------------------------------------ 单栏
    def _fl_pane_build(self, parent, side: str, *, height: int,
                       empty_title: str, empty_hint: str) -> dict:
        bar = U.wframe(parent, "card")
        bar.pack(fill="x")
        ui = {"side": side}

        ui["btn_up"] = U.RoundButton(bar, "↑", lambda: self._fl_up(side),
                                     variant="ghost", height=28, padx=10)
        ui["btn_up"].pack(side="left")
        ui["var"] = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=ui["var"])
        ent.pack(side="left", fill="x", expand=True, padx=(U.px(8), U.px(8)))
        ent.bind("<Return>", lambda _e: self._fl_goto(side, ui["var"].get()))
        ui["entry"] = ent

        U.RoundButton(bar, "刷新", lambda: self._fl_reload(side),
                      variant="secondary", height=28, padx=12).pack(side="left")
        U.RoundButton(bar, "新建", lambda: self._fl_mkdir(side),
                      variant="ghost", height=28, padx=10).pack(
            side="left", padx=(U.px(4), 0))

        tbl = DataTable(parent, COLS, height=height, searchable=True,
                        empty_title=empty_title, empty_hint=empty_hint,
                        on_activate=lambda _r=None: self._fl_activate(side))
        tbl.pack(fill="both", expand=True, pady=(U.px(8), 0))
        tbl.set_menu([
            ("打开", lambda: self._fl_activate(side), lambda r: bool(r)),
            ("重命名…", lambda: self._fl_rename(side), lambda r: bool(r)),
            (None, None, None),
            ("上传到设备" if side == "local" else "下载到本机",
             (self.fl_upload if side == "local" else self.fl_download),
             lambda r: bool(r)),
            (None, None, None),
            ("删除", lambda: self._fl_delete(side), lambda r: bool(r)),
        ])
        ui["tbl"] = tbl
        return ui

    # ------------------------------------------------------------ 状态
    def files_sync(self):
        """跟着连接状态重建远程 FS。连上 / 断开 / 换设备都要走一遍。"""
        sess = getattr(self, "session", None)
        ok = bool(sess and sess.alive and hasattr(sess, "sftp"))
        if ok:
            self._fl_remote = fl.SFTPFS(sess)
            who = getattr(sess, "user", "") or ""
            host = getattr(sess, "host", "") or ""
            side = f"{who}@{host}" if (who or host) else "设备"
            self.lbl_fl_side.configure(text=side, fg=U.T.text)
            self.badge_fl.set("已连接", "ok")
            self.lbl_fl_hint.configure(
                text="WinSCP 已就绪" if self._fl_win else "内置 SFTP 传输")
            self._fl_remote_ui["tbl"].set_empty(
                "目录还没读出来", "点上面的「刷新」重新读一次。")
        else:
            self._fl_remote = None
            self.lbl_fl_side.configure(text="未连接设备", fg=U.T.text)
            self.badge_fl.set("未连接", "mute")
            self.lbl_fl_hint.configure(
                text="Telnet 没有文件通道" if (sess and sess.alive) else "")
            self._fl_remote_ui["tbl"].set_rows([])
            self._fl_remote_ui["tbl"].set_empty(
                "还没有连上设备",
                "先在「连接与设备」用 SSH 连上目标设备，再回来浏览文件。")
            self._fl_remote_ui["var"].set("")

        self._fl_sync_buttons()
        self._fl_reload("local")
        if self._fl_remote is not None:
            self._fl_reload("remote")

    def _fl_sync_buttons(self):
        has = self._fl_remote is not None
        for btn in (self.btn_fl_up, self.btn_fl_down):
            try:
                btn.set_enabled(has and not self._fl_busy)
            except Exception:  # noqa: BLE001
                pass
        try:
            self.btn_fl_win.set_enabled(bool(has and self._fl_win))
        except Exception:  # noqa: BLE001
            pass

    def _fl_fs(self, side: str):
        return self._fl_remote if side == "remote" else self._fl_local

    def _fl_ui(self, side: str) -> dict:
        return self._fl_remote_ui if side == "remote" else self._fl_local_ui

    def fl_reload_all(self):
        self._fl_reload("local")
        self._fl_reload("remote")

    # ------------------------------------------------------------ 读目录
    def _fl_reload(self, side: str):
        fs = self._fl_fs(side)
        ui = self._fl_ui(side)
        if fs is None:
            return
        path = ui["var"].get().strip() or fs.home()
        ui["var"].set(path)
        tbl = ui["tbl"]
        tbl.set_empty("正在读取…", path)

        def work():
            return fs.normalize(path), fs.listdir(path)

        def done(res):
            real, rows = res
            ui["var"].set(real)
            items = [r.row() for r in rows]
            up = fs.parent(real)
            if up != real:
                items.insert(0, {"name": UP_LABEL, "size": "", "size_b": 0,
                                 "mtime": "", "mtime_raw": 0, "mode": "",
                                 "kind": "up", "link": "", "path": up,
                                 "is_dir": True})
            tbl.set_rows(items)
            if not rows:
                tbl.set_empty("这个目录是空的",
                              "用「新建」建个目录，或从另一边上传文件过来。")
            self.status.set_state(
                f"{'设备' if side == 'remote' else '本机'} · {real} · {len(rows)} 项")

        def fail(msg):
            tbl.set_rows([])
            tbl.set_empty("读不了这个目录", str(msg))
            self.status.set_state(f"读目录失败：{msg}", "err")

        self.bg(work, ok=done, err=fail)

    def _fl_goto(self, side: str, path: str):
        fs = self._fl_fs(side)
        if fs is None:
            return
        p = (path or "").strip()
        if not p:
            return
        self._fl_ui(side)["var"].set(fs.normalize(p))
        self._fl_reload(side)

    def _fl_up(self, side: str):
        fs = self._fl_fs(side)
        if fs is None:
            return
        cur = self._fl_ui(side)["var"].get()
        self._fl_goto(side, fs.parent(cur))

    def _fl_activate(self, side: str):
        fs = self._fl_fs(side)
        ui = self._fl_ui(side)
        if fs is None:
            return
        row = ui["tbl"].selected()
        if not row:
            return
        if row.get("is_dir"):
            self._fl_goto(side, row["path"])
        elif side == "remote":
            self.fl_download()
        else:
            self.fl_upload()

    # ------------------------------------------------------------ 增删改名
    def _fl_mkdir(self, side: str):
        fs = self._fl_fs(side)
        if fs is None:
            self.toast("先连上设备", "warn")
            return
        cur = self._fl_ui(side)["var"].get()
        res = forms.FormDialog.open(
            self, "新建目录",
            [{"key": "name", "label": "目录名", "kind": "text", "required": True,
              "placeholder": "backup"}],
            subtitle=f"在 {cur} 下新建", submit="创建")
        if not res:
            return
        name = (res.get("name") or "").strip()
        if not name:
            return
        target = fs.join(cur, name)

        def work():
            fs.mkdir(target)
            return target

        self.bg(work, ok=lambda p: (self.toast(f"已创建 {p}", "ok"),
                                    self._fl_reload(side)),
                err=lambda m: self.status.set_state(f"新建失败：{m}", "err"))

    def _fl_rename(self, side: str):
        fs = self._fl_fs(side)
        ui = self._fl_ui(side)
        if fs is None:
            return
        row = ui["tbl"].selected()
        if not row or row.get("kind") == "up":
            self.toast("先选一个文件或目录", "warn")
            return
        res = forms.FormDialog.open(
            self, "重命名",
            [{"key": "name", "label": "新名称", "kind": "text", "required": True}],
            {"name": row["name"].rstrip("/")},
            subtitle=row["path"], submit="改名")
        if not res:
            return
        new = (res.get("name") or "").strip()
        if not new or new == row["name"].rstrip("/"):
            return
        dst = fs.join(fs.parent(row["path"]), new)

        def work():
            fs.rename(row["path"], dst)
            return dst

        self.bg(work, ok=lambda p: (self.toast(f"已改名为 {new}", "ok"),
                                    self._fl_reload(side)),
                err=lambda m: self.status.set_state(f"改名失败：{m}", "err"))

    def _fl_delete(self, side: str):
        fs = self._fl_fs(side)
        ui = self._fl_ui(side)
        if fs is None:
            return
        row = ui["tbl"].selected()
        if not row or row.get("kind") == "up":
            self.toast("先选一个文件或目录", "warn")
            return
        what = "目录及其全部内容" if row.get("is_dir") else "文件"
        if not forms.confirm(self, "确认删除",
                             f"删除{what}？\n\n{row['path']}\n\n"
                             "这一步不可撤销。", danger=True, ok_text="删除"):
            return

        def work():
            fs.remove(row["path"], bool(row.get("is_dir")))
            return row["path"]

        self.bg(work, ok=lambda p: (self.toast("已删除", "ok"),
                                    self._fl_reload(side)),
                err=lambda m: self.status.set_state(f"删除失败：{m}", "err"))

    # ------------------------------------------------------------ 传输入口
    def fl_upload(self):
        """本机 → 设备。本机栏可见就传选中的，否则弹文件选择框。"""
        dst = self._fl_remote
        if dst is None:
            self.toast("先连上设备再传文件", "warn")
            return
        dst_dir = self._fl_remote_ui["var"].get()
        jobs = []
        if self._fl_local_visible():
            row = self._fl_local_ui["tbl"].selected()
            if row and row.get("kind") != "up":
                jobs.append(self._fl_job(self._fl_local, row["path"], dst,
                                         dst_dir, "上传", "remote"))
            elif self._fl_mode == "local":
                self.toast("先在列表里选一个文件", "warn")
                return
        if not jobs:
            paths = filedialog.askopenfilenames(
                title="选择要上传的文件", parent=self)
            for p in paths or ():
                jobs.append(self._fl_job(self._fl_local, p, dst, dst_dir,
                                         "上传", "remote"))
            if not jobs:
                d = filedialog.askdirectory(title="选择要上传的整个目录",
                                            parent=self)
                if d:
                    jobs.append(self._fl_job(self._fl_local, d, dst, dst_dir,
                                             "上传", "remote"))
        if not jobs:
            return
        self._fl_enqueue(jobs)

    def fl_download(self):
        """设备 → 本机。本机栏可见就落到当前目录，否则弹目录选择框。"""
        src = self._fl_remote
        if src is None:
            self.toast("先连上设备再传文件", "warn")
            return
        row = self._fl_remote_ui["tbl"].selected()
        if not row or row.get("kind") == "up":
            self.toast("先在上面选一个文件", "warn")
            return
        if self._fl_local_visible():
            dst_dir = self._fl_local_ui["var"].get()
        else:
            dst_dir = filedialog.askdirectory(title="下载到哪个目录", parent=self)
            if not dst_dir:
                return
        self._fl_enqueue([self._fl_job(src, row["path"], self._fl_local,
                                       dst_dir, "下载", "local")])

    def _fl_job(self, src_fs, src_path, dst_fs, dst_dir, arrow, dst_side) -> dict:
        return {
            "src_fs": src_fs,
            "src_path": src_path,
            "dst_fs": dst_fs,
            "dst_dir": dst_dir,
            "arrow": arrow,
            "dst_side": dst_side,
            "name": fl.base_name(src_path) or src_path,
        }

    # ------------------------------------------------------------ 传输队列
    def _fl_enqueue(self, jobs: list[dict]):
        self._fl_jobs.extend([j for j in jobs if j])
        if not self._fl_busy:
            self._fl_run_next()

    def _fl_run_next(self):
        if not self._fl_jobs:
            # 队列空了就收摊，但**不要**把「上传完成 · 3 个文件 · 1.2 MB」
            # 这行结论擦回「空闲」—— 用户要看的正是这句。
            self._fl_finish()
            return
        job = self._fl_jobs.pop(0)
        self._fl_cur = job
        self._fl_busy = True
        self._fl_cancel = False
        self._fl_sync_buttons()
        self.btn_fl_stop.set_enabled(True)
        self._fl_set_pct(0)
        left = len(self._fl_jobs)
        tail = f"（还有 {left} 个排队）" if left else ""
        self.lbl_fl_prog.configure(
            text=f"{job['arrow']} {job['name']}…{tail}", fg=U.T.text_dim)
        self.status.set_state(f"{job['arrow']} {job['name']}…")

        def on_file(n):
            txt = f"{job['arrow']} {n}…"
            self.post(lambda: self.lbl_fl_prog.configure(text=txt,
                                                         fg=U.T.text_dim))

        def on_prog(done, _total, _name):
            if self._fl_cancel:
                raise fl.Cancelled()
            now = time.time()
            if now - self._fl_last_post < 0.1:
                return
            self._fl_last_post = now
            self.post(self._fl_tick, done, job)

        def work():
            _files, total = fl.collect(job["src_fs"], job["src_path"])
            job["total"] = max(0, total)
            return fl.transfer(job["src_fs"], job["src_path"], job["dst_fs"],
                               job["dst_dir"], on_progress=on_prog,
                               on_file=on_file)

        def done(st):
            self.lbl_fl_prog.configure(
                text=f"{job['arrow']}完成 · {st['files']} 个文件 · "
                     f"{human_bytes(st['bytes'])}", fg=U.T.ok)
            self.status.set_state(f"{job['arrow']}完成", "ok")
            self._fl_reload(job["dst_side"])
            self._fl_busy = False
            self.btn_fl_stop.set_enabled(False)
            self._fl_run_next()

        def fail(msg):
            cancelled = self._fl_cancel
            self._fl_busy = False
            self.btn_fl_stop.set_enabled(False)
            if cancelled:
                self._fl_jobs.clear()
                self.lbl_fl_prog.configure(
                    text=f"{job['arrow']}已取消", fg=U.T.warn)
                self.status.set_state(f"{job['arrow']}已取消", "warn")
            else:
                self.lbl_fl_prog.configure(
                    text=f"{job['arrow']}失败：{msg}", fg=U.T.err)
                self.status.set_state(f"{job['arrow']}失败", "err")
            self._fl_sync_buttons()
            if not cancelled:
                self._fl_run_next()

        self.bg(work, ok=done, err=fail)

    def _fl_finish(self):
        self._fl_busy = False
        self._fl_cancel = False
        self._fl_cur = None
        self._fl_sync_buttons()
        try:
            self.btn_fl_stop.set_enabled(False)
        except Exception:  # noqa: BLE001
            pass

    def _fl_abort(self):
        if self._fl_busy:
            self._fl_cancel = True
            self.lbl_fl_prog.configure(text="正在取消…", fg=U.T.warn)

    def _fl_tick(self, done: int, job: dict):
        total = job.get("total") or 0
        self._fl_set_pct((done / total) if total else 0.0)
        if total:
            self.lbl_fl_prog.configure(
                text=f"{job['arrow']} {job['name']} · "
                     f"{human_bytes(done)} / {human_bytes(total)}")
        else:
            self.lbl_fl_prog.configure(
                text=f"{job['arrow']} {job['name']} · {human_bytes(done)}")

    def _fl_set_pct(self, pct: float):
        self._fl_pct = max(0.0, min(1.0, pct))
        self._fl_paint_bar()

    def _fl_paint_bar(self):
        try:
            self._fl_fill.place(x=0, y=0, relwidth=self._fl_pct, relheight=1)
        except tk.TclError:
            pass

    def _fl_theme(self):
        try:
            self._fl_track.configure(bg=U.T.alt)
            self._fl_fill.configure(bg=U.T.brand)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ WinSCP
    def fl_winscp(self):
        sess = getattr(self, "session", None)
        if not (sess and sess.alive):
            self.toast("先连上设备", "warn")
            return
        if not self._fl_win:
            self._fl_win = fl.find_winscp()
        if not self._fl_win:
            return _ask_install_winscp(self)
        spec = getattr(sess, "spec", None)
        cur = self._fl_remote_ui["var"].get() if self._fl_remote else ""
        argv = fl.winscp_command(spec, cur) if spec else []
        if not argv:
            self.toast("WinSCP 参数拼不出来（缺连接档案）", "warn")
            return
        import subprocess
        try:
            subprocess.Popen(argv, close_fds=True)
            self.toast("已用 WinSCP 打开，密码请在弹窗里填", "ok", 5000)
        except OSError as e:
            self.status.set_state(f"启动 WinSCP 失败：{e}", "err")


def _ask_install_winscp(parent):
    """没装 WinSCP 时给一句人话 + 官网地址，不替用户做主去下载。"""
    from tkinter import messagebox
    messagebox.showinfo(
        "没有找到 WinSCP",
        "本机没检测到 WinSCP。\n\n"
        "内置的双栏浏览已经够用：上面的「上传 / 下载」走的就是 SFTP，\n"
        "目录会递归传输，也能取消。\n\n"
        "想要 WinSCP 的同步、批量、脚本能力，可以装一份：\n"
        "https://winscp.net/eng/download.php\n\n"
        "装完不用重启本程序，再点一次这个按钮就会自动找到它。",
        parent=parent)
