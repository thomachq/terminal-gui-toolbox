# -*- coding: utf-8 -*-
"""数据库页：一台机器上有几个实例、各是什么、能不能连、里面有什么。

顺序刻意是「先探测（不用口令）→ 再挑一个连（要口令）→ 连上才谈查询」，
因为探测完全只读、零凭据，用户点开就能看到东西；口令只在真要查的时候才问。

口令不落盘：`self.db_cred` 是内存里的 dict，切设备/切实例就清；
请求时写远端 600 临时文件、跑完即删（见 core.dbscan 的说明）。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import wbtui as U
from core import dbscan as D
from ui import forms
from ui.table import Column, DataTable, KVGrid, Segmented

DB_COLS = [
    Column("name", "库 / 键空间", 220, mono=True, stretch=True),
    Column("tables", "表 / 键数", 110),
    Column("size", "大小", 130),
]
ST_COLS = [
    Column("name", "项目", 240, mono=True, stretch=True),
    Column("value", "值", 220, mono=True),
]
TABS = [("dbs", "库列表"), ("status", "运行状态"), ("log", "操作记录")]


class DbPage:
    # ------------------------------------------------------------ 构建
    def build_db(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(3, weight=1)

        # ---- 实例
        c = U.Card(pg, title="数据库实例",
                   hint="ps + 端口 + 服务名三方对照 · 探测不需要口令")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        head = U.wframe(b, "card")
        head.pack(fill="x")
        self.lbl_db_title = U.wlabel(head, "正在探测数据库…", "card", U.FONT_B)
        self.lbl_db_title.pack(side="left")
        self.badge_db = U.Pill(head, "探测中", "mute", height=24)
        self.badge_db.pack(side="left", padx=(U.px(10), 0))
        self.lbl_db_cred = U.wlabel(head, "未连接（查询前需要口令）", "mute", U.FONT_XS)
        self.lbl_db_cred.pack(side="right")

        self.lbl_db_hint = U.wlabel(
            b, "MySQL / MariaDB、PostgreSQL、Redis、MongoDB、SQL Server 认进程与端口；"
               "SQLite 按文件找。口令只在内存里待着，连的时候写远端 600 临时文件，用完即删。",
            "dim", U.FONT_S, justify="left", wraplength=U.px(760))
        self.lbl_db_hint.pack(anchor="w", pady=(U.px(6), 0))

        row = U.wframe(b, "card")
        row.pack(fill="x", pady=(U.px(12), 0))
        U.RoundButton(row, "重新探测", self.db_probe, variant="secondary",
                      height=32, padx=16).pack(side="left")
        U.wlabel(row, "实例", "card", U.FONT_S).pack(side="left", padx=(U.px(16), 6))
        self.v_db_svc = tk.StringVar()
        self.cb_db_svc = ttk.Combobox(row, textvariable=self.v_db_svc, width=40,
                                      state="readonly")
        self.cb_db_svc.pack(side="left")
        self.cb_db_svc.bind("<<ComboboxSelected>>", lambda e: self.db_select())
        self.btn_db_conn = U.RoundButton(row, "连接设置…", self.db_conn_settings,
                                         variant="ghost", height=32, padx=12)
        self.btn_db_conn.pack(side="left", padx=(U.px(10), 0))

        # ---- 详情
        c2 = U.Card(pg, title="实例详情", hint="切换下拉即换这一块")
        c2.grid(row=1, column=0, sticky="we", pady=(U.px(10), 0))
        self.kk_db = KVGrid(c2.body, columns=3)
        self.kk_db.pack(fill="x")

        # ---- 巡检动作
        c3 = U.Card(pg, title="巡检", hint="只读查询 · 备份单独确认")
        c3.grid(row=2, column=0, sticky="we", pady=(U.px(10), 0))
        b3 = c3.body
        act = U.wframe(b3, "card")
        act.pack(fill="x")
        self.btn_db_ping = U.RoundButton(act, "连接测试", self.db_ping,
                                         variant="secondary", height=32, padx=14)
        self.btn_db_ping.pack(side="left")
        self.btn_db_list = U.RoundButton(act, "列出数据库", self.db_list,
                                         variant="primary", height=32, padx=16)
        self.btn_db_list.pack(side="left", padx=(U.px(8), 0))
        self.btn_db_stat = U.RoundButton(act, "运行状态", self.db_status,
                                         variant="ghost", height=32, padx=12)
        self.btn_db_stat.pack(side="left", padx=(U.px(6), 0))
        self.btn_db_bak = U.RoundButton(act, "备份选中库", self.db_backup,
                                        variant="ghost", height=32, padx=12)
        self.btn_db_bak.pack(side="left", padx=(U.px(6), 0))
        U.RoundButton(act, "复制连接命令", self.db_copy_cmd, variant="ghost",
                      height=32, padx=12).pack(side="left", padx=(U.px(6), 0))
        self.lbl_db_state = U.wlabel(act, "", "mute", U.FONT_XS)
        self.lbl_db_state.pack(side="right")

        # ---- 结果
        c4 = U.Card(pg, title="结果", hint="库列表 / 运行状态 / 操作记录")
        c4.grid(row=3, column=0, sticky="nsew", pady=(U.px(10), 0))
        box = c4.body
        self.seg_db = Segmented(box, TABS, command=self._db_tab)
        self.seg_db.pack(anchor="w", pady=(0, U.px(8)))
        holder = U.wframe(box, "card")
        holder.pack(fill="both", expand=True)
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        self.db_dbs_box = U.wframe(holder, "card")
        self.db_dbs_box.grid(row=0, column=0, sticky="nsew")
        self.tbl_db = DataTable(
            self.db_dbs_box, DB_COLS, height=8, searchable=True,
            empty_title="还没有读到库", empty_hint="先「连接设置」，再点「列出数据库」。",
            on_activate=lambda r: self._db_log(f"选中：{r.get('name')}"))
        self.tbl_db.pack(fill="both", expand=True)
        self.tbl_db.set_menu([
            ("备份这个库", self.db_backup, lambda r: bool(r)),
            ("复制库名", lambda r: self.copy_text(r.get("name", ""), "库名"),
             lambda r: bool(r)),
        ])

        self.db_st_box = U.wframe(holder, "card")
        self.db_st_box.grid(row=0, column=0, sticky="nsew")
        self.tbl_db_st = DataTable(self.db_st_box, ST_COLS, height=8,
                                   searchable=True, empty_title="还没有读状态",
                                   empty_hint="连上之后点「运行状态」。")
        self.tbl_db_st.pack(fill="both", expand=True)

        self.db_log_box = U.wframe(holder, "card")
        self.db_log_box.grid(row=0, column=0, sticky="nsew")
        self.box_db = U.TextBox(self.db_log_box, role="alt", font=U.MONO_S,
                                height=8, wrap="none")
        self.box_db.pack(fill="both", expand=True)
        self.db_dbs_box.tkraise()

        self.db_res: dict = {}
        self.db_cur: dict = {}
        self.db_cred: dict[str, D.Cred] = {}
        self._db_set_enabled(False)

    # ------------------------------------------------------------ 状态
    def _db_set_enabled(self, on: bool):
        for btn in (self.btn_db_ping, self.btn_db_list, self.btn_db_stat,
                    self.btn_db_bak):
            btn.set_enabled(on)

    def _db_key(self, svc: dict) -> str:
        return f"{svc.get('kind')}:{svc.get('port')}:{svc.get('pid') or svc.get('config')}"

    def _db_log(self, text: str) -> None:
        try:
            self.box_db.txt.configure(state="normal")
            self.box_db.txt.insert("end", text.rstrip() + "\n")
            self.box_db.txt.see("end")
        except tk.TclError:
            pass

    def _db_tab(self, key: str):
        box = {"dbs": self.db_dbs_box, "status": self.db_st_box,
               "log": self.db_log_box}.get(key)
        if box is not None:
            box.tkraise()

    # ------------------------------------------------------------ 探测
    def db_probe(self):
        s = self._active_session()
        dev = self._effective_device()
        self.badge_db.set("探测中", "mute")
        self.lbl_db_title.configure(text="正在探测数据库…", fg=U.T.text)
        self.status.set_state("探测数据库…")
        scanner = D.DbScanner(s, dev)

        def work():
            return scanner.discover()

        def done(res):
            self._db_apply(res)

        self.bg(work, ok=done, err=lambda m: self._db_apply(
            {"status": "error", "detail": str(m), "services": [], "commands": []}))

    def _db_apply(self, res: dict):
        self.db_res = res or {}
        svcs = self.db_res.get("services") or []
        status = self.db_res.get("status")
        if status == "error":
            self.lbl_db_title.configure(text="探测失败", fg=U.T.err)
            self.badge_db.set("失败", "err")
            self._db_log(f"[失败] {self.db_res.get('detail', '')}")
        elif not svcs:
            self.lbl_db_title.configure(text="没有发现数据库", fg=U.T.text_dim)
            self.badge_db.set("0 个", "mute")
        else:
            self.lbl_db_title.configure(text=D.summarize(svcs), fg=U.T.text)
            self.badge_db.set(f"{len(svcs)} 个实例", "ok")

        for cmd in (self.db_res.get("commands") or [])[-12:]:
            self._db_log(f"$ {cmd}")

        self.cb_db_svc.configure(values=[
            f"{s.get('label')}"
            f"{' · ' + s['config'] if s.get('kind') == 'sqlite' and s.get('config') else ''}"
            f"{' · :' + str(s['port']) if s.get('port') else ''}"
            f"{' · ' + s.get('state', '') if s.get('state') else ''}"
            for s in svcs])
        if svcs:
            self.cb_db_svc.current(0)
            self._db_set_enabled(True)
            self.db_select()
        else:
            self.v_db_svc.set("")
            self._db_set_enabled(False)
            self.db_cur = {}
            self._db_render()
            self.tbl_db.set_rows([])

    def db_select(self):
        svcs = (self.db_res or {}).get("services") or []
        idx = self.cb_db_svc.current()
        svc = svcs[idx] if 0 <= idx < len(svcs) else {}
        self.db_cur = svc or {}
        self._db_render()
        self.tbl_db.set_rows([])
        self.tbl_db_st.set_rows([])
        kind = svc.get("kind", "")
        can = kind in D.QUERYABLE
        for btn in (self.btn_db_ping, self.btn_db_list, self.btn_db_stat):
            btn.set_enabled(can)
        self.btn_db_bak.set_enabled(can)
        cred = self.db_cred.get(self._db_key(svc))
        self.lbl_db_cred.configure(
            text=(f"已记住 {cred.describe()}" if cred
                  else "未连接（查询前需要口令）"),
            fg=U.T.ok if cred else U.T.text_mute)
        if kind == "sqlite":
            self._db_log(f"[SQLite] {svc.get('config', '')}（只列文件，不做连接）")

    def _db_render(self):
        svc = self.db_cur
        if not svc:
            self.kk_db.set_items([("实例", "—", None)])
            return
        items = [
            ("类型", svc.get("label") or "—", "brand"),
            ("状态", svc.get("state") or "—",
             "ok" if "运行" in (svc.get("state") or "") else "mute"),
            ("监听", f"{svc.get('addr') or '—'}:{svc.get('port') or '—'}"
                     if svc.get("port") else "—", None),
            ("版本", svc.get("version") or "—（点连接测试可读到）", None),
            ("PID", svc.get("pid") or "—", None),
            ("运行用户", svc.get("user") or "—", None),
            ("数据目录", svc.get("datadir") or "—", None),
            ("配置文件", svc.get("config") or "—", None),
            ("大小", svc.get("size") or "—", None),
        ]
        self.kk_db.set_items(items)

    # ------------------------------------------------------------ 凭据
    def db_conn_settings(self):
        svc = self.db_cur
        if not svc:
            self.toast("先选一个实例", "warn")
            return
        kind = svc.get("kind", "")
        if kind not in D.QUERYABLE:
            self.toast("SQLite 文件不需要连接", "warn")
            return
        old = self.db_cred.get(self._db_key(svc)) or D.Cred()
        fields = [
            {"key": "host", "label": "主机", "default": old.host or "127.0.0.1",
             "width": 26, "hint": "本机就留 127.0.0.1"},
            {"key": "port", "label": "端口", "kind": "int",
             "default": old.port or svc.get("port") or D.DEFAULT_PORT.get(kind, 0),
             "min": 1, "max": 65535, "width": 10},
            {"key": "user", "label": "用户名",
             "default": old.user or ("root" if kind in ("mysql", "mariadb")
                                     else ("postgres" if kind == "postgres" else "")),
             "width": 22, "required": True},
            {"key": "password", "label": "口令", "kind": "password",
             "default": old.password, "width": 22,
             "hint": "只在内存里；不写盘、不进日志"},
            {"key": "db", "label": "默认库", "default": old.db,
             "width": 20, "hint": "可留空（PG 默认连 postgres）"},
            {"key": "socket", "label": "Socket 路径", "default": old.socket,
             "width": 30, "hint": "MySQL 走本地 socket 时填，填了就不走 TCP"},
        ]
        data = forms.FormDialog.open(
            self, "连接设置",
            fields, subtitle=f"{svc.get('label')} · 口令不落盘，用完即删")
        if data is None:
            return
        cred = D.Cred(user=str(data.get("user") or "").strip(),
                      password=str(data.get("password") or ""),
                      host=str(data.get("host") or "127.0.0.1").strip(),
                      port=int(data.get("port") or 0),
                      socket=str(data.get("socket") or "").strip(),
                      db=str(data.get("db") or "").strip())
        self.db_cred[self._db_key(svc)] = cred
        self.lbl_db_cred.configure(text=f"已记住 {cred.describe()}", fg=U.T.ok)
        self._db_log(f"[凭据] {cred.describe()}（口令不落盘）")
        self.db_ping()

    def _client(self) -> D.DbClient | None:
        svc = self.db_cur
        if not svc:
            self.toast("先选一个实例", "warn")
            return None
        cred = self.db_cred.get(self._db_key(svc))
        if cred is None:
            self.db_conn_settings()
            cred = self.db_cred.get(self._db_key(svc))
            if cred is None:
                return None
        return D.DbClient(self._active_session(), svc, cred)

    # ------------------------------------------------------------ 动作
    def db_ping(self):
        cli = self._client()
        if cli is None:
            return
        self.lbl_db_state.configure(text="连接中…")
        self.status.set_state("测试数据库连接…")
        svc = dict(self.db_cur)

        def work():
            ok, info = cli.ping()
            return svc, ok, info

        def done(res):
            svc, ok, info = res
            if ok:
                self.lbl_db_state.configure(text="已连接", fg=U.T.ok)
                self.badge_db.set("已连接", "ok")
                self._db_log(f"[连接] {svc.get('label')} 成功 · {info[:90]}")
                self.status.set_state("数据库连接成功", "ok")
                # 版本读到就顺手更新详情
                cur = dict(self.db_cur)
                cur["version"] = info[:80]
                self.db_cur = cur
                self._db_render()
                self._log_raw("ok", f"[数据库] 连接 {svc.get('label')} 成功\n")
            else:
                self.lbl_db_state.configure(text="连接失败", fg=U.T.err)
                self.badge_db.set("连接失败", "err")
                self._db_log(f"[连接] 失败：{info}")
                self.status.set_state("数据库连接失败", "err")
                forms.confirm(self, "连接失败",
                              f"{svc.get('label')} 连不上：\n\n{info}\n\n"
                              "常见原因：没有授权该主机登录、端口只监听本机、"
                              "或口令不对。MySQL 可先试 socket 路径。")

        self.bg(work, ok=done, err=lambda m: self._db_log(f"[连接] {m}"))

    def db_list(self):
        cli = self._client()
        if cli is None:
            return
        self.lbl_db_state.configure(text="读取库列表…")
        self.status.set_state("读取数据库列表…")

        def work():
            return cli.databases()

        def done(rows):
            self.tbl_db.set_rows(rows)
            self.tbl_db.set_empty("没有读到库",
                                  "连接是通的，但当前账号看不到任何库 —— "
                                  "通常是权限不足。")
            self.seg_db.select("dbs", fire=False)
            self._db_tab("dbs")
            self.lbl_db_state.configure(text=f"{len(rows)} 个库", fg=U.T.ok)
            self.status.set_state(f"读到 {len(rows)} 个库", "ok")
            self._db_log(f"[库列表] {len(rows)} 项")
            self._log_raw("info", f"[数据库] 列出 {len(rows)} 个库\n")

        def fail(msg):
            self.lbl_db_state.configure(text="读取失败", fg=U.T.err)
            self._db_log(f"[库列表] {msg}")
            self.status.set_state("读取库列表失败", "err")
            forms.confirm(self, "读取失败", str(msg))

        self.bg(work, ok=done, err=fail)

    def db_status(self):
        cli = self._client()
        if cli is None:
            return
        self.lbl_db_state.configure(text="读取状态…")

        def work():
            return cli.status()

        def done(pairs):
            self.tbl_db_st.set_rows([{"name": k, "value": v} for k, v in pairs])
            self.seg_db.select("status", fire=False)
            self._db_tab("status")
            summary = D.status_summary(self.db_cur.get("kind", ""), pairs)
            self.lbl_db_state.configure(text=summary, fg=U.T.ok)
            self._db_log(f"[状态] {summary}")
            self.status.set_state(summary, "ok")

        def fail(msg):
            self._db_log(f"[状态] {msg}")
            self.toast("读取状态失败", "err")

        self.bg(work, ok=done, err=fail)

    def db_backup(self, row=None):
        cli = self._client()
        if cli is None:
            return
        row = row or self.tbl_db.selected()
        kind = self.db_cur.get("kind", "")
        if not row:
            self.toast("先「列出数据库」并选中一个库", "warn")
            return
        db = row.get("name", "")
        fields = [
            {"key": "db", "label": "数据库", "kind": "readonly", "default": db},
            {"key": "target", "label": "保存到（设备上）",
             "default": D.default_target(kind, db), "width": 46,
             "hint": "目录必须先存在；文件名按库名 + 时间戳"},
        ]
        data = forms.FormDialog.open(
            self, "备份数据库", fields,
            subtitle=f"{self.db_cur.get('label')} · 用官方 dump 工具，不会锁表"
                     if kind in ("mysql", "mariadb")
                     else f"{self.db_cur.get('label')} · pg_dump -Fc")
        if not data:
            return
        target = str(data.get("target") or "").strip()
        if not target:
            return
        if not forms.confirm(
                self, "确认备份",
                f"库：{db}\n目标：{target}\n\n"
                "备份会写文件到设备上，大库可能跑很久。继续？",
                ok_text="开始备份"):
            return
        self.lbl_db_state.configure(text="备份中…")
        self.status.set_state("备份数据库…")
        self._db_log(f"[备份] {db} → {target}")

        def work():
            return cli.backup(db, target)

        def done(res):
            ok = res.get("ok")
            self._db_log(f"[备份] {'完成' if ok else '失败'} · {res.get('detail', '')}")
            self.lbl_db_state.configure(text="备份完成" if ok else "备份失败",
                                        fg=U.T.ok if ok else U.T.err)
            self.toast("备份完成" if ok else "备份失败", "ok" if ok else "err")
            self.status.set_state("备份完成" if ok else "备份失败",
                                 "ok" if ok else "err")
            self._log_raw("ok" if ok else "bad",
                          f"[数据库] 备份 {db} → {target} · "
                          f"{'成功' if ok else res.get('detail', '')}\n")

        self.bg(work, ok=done, err=lambda m: self._db_log(f"[备份] {m}"))

    def db_copy_cmd(self):
        """给用户一条能自己粘贴到终端里跑的连接命令（不含口令）。"""
        svc = self.db_cur
        if not svc:
            return
        kind = svc.get("kind", "")
        cred = self.db_cred.get(self._db_key(svc)) or D.Cred()
        port = cred.port or svc.get("port") or D.DEFAULT_PORT.get(kind, 0)
        host = cred.host or "127.0.0.1"
        if kind in ("mysql", "mariadb"):
            cmd = (f"mysql -h {host} -P {port} -u {cred.user or 'root'} -p")
        elif kind == "postgres":
            cmd = (f"psql -h {host} -p {port} -U {cred.user or 'postgres'} "
                   f"-d {cred.db or 'postgres'}")
        elif kind == "redis":
            cmd = f"redis-cli -h {host} -p {port}"
        elif kind == "mongo":
            cmd = f"mongosh --host {host} --port {port}"
        elif kind == "mssql":
            cmd = f"sqlcmd -S {host},{port} -U {cred.user}"
        else:
            cmd = f"sqlite3 {svc.get('config', '')}"
        self.copy_text(cmd + "\n# 口令由你自己输入（本工具不会把口令写进命令）",
                       "连接命令")
