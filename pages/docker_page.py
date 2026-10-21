# -*- coding: utf-8 -*-
"""Docker 管理页。

第一件事永远是**判断这台设备上到底有没有 Docker**，而不是直接跑
`docker ps` 然后把一坨报错甩给用户。四种状态分别给不同的话：

  未安装        → 「当前设备未安装 Docker」+ 怎么装
  服务没起来    → 「已安装但服务未运行」+ 怎么启
  没权限        → 「无权访问」+ docker 组 / sudo
  可用          → 容器 / 镜像 / 资源占用三张表
"""
from __future__ import annotations

import tkinter as tk

import wbtui as U
from core import docker as dk
from ui import forms
from ui.table import Column, DataTable, Segmented

CONTAINER_COLS = [
    Column("name", "容器", 180, stretch=True),
    Column("image", "镜像", 190, stretch=True),
    Column("state", "状态", 92,
           format=lambda v, r: {"running": "运行中", "exited": "已停止",
                                "paused": "已暂停", "created": "已创建",
                                "restarting": "重启中"}.get(str(v).lower(), str(v))),
    Column("status", "详情", 190, stretch=True),
    Column("ports", "端口", 160),
    Column("id", "ID", 96, mono=True),
]

IMAGE_COLS = [
    Column("repo", "仓库", 210, stretch=True),
    Column("tag", "标签", 110, mono=True),
    Column("id", "ID", 100, mono=True),
    Column("size", "大小", 90, anchor="e"),
    Column("created", "创建", 130),
]

STATS_COLS = [
    Column("name", "容器", 190, stretch=True),
    Column("cpu", "CPU %", 84, anchor="e", format=lambda v, r: f"{float(v or 0):.1f}"),
    Column("mem_pct", "内存 %", 84, anchor="e",
           format=lambda v, r: f"{float(v or 0):.1f}"),
    Column("mem", "内存用量", 130, anchor="e"),
    Column("net", "网络 I/O", 150, anchor="e"),
    Column("block", "磁盘 I/O", 150, anchor="e"),
    Column("pids", "进程", 60, anchor="e"),
]


class DockerPage:
    # ------------------------------------------------------------ 构建
    def build_docker(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(2, weight=1)

        c = U.Card(pg, title="Docker", hint="本机或远端设备上的容器")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        head = U.wframe(b, "card")
        head.pack(fill="x")
        self.lbl_dk_title = U.wlabel(head, "正在检测 Docker…", "card", U.FONT_B)
        self.lbl_dk_title.pack(side="left")
        self.badge_dk = U.Pill(head, "检测中", "mute", height=24)
        self.badge_dk.pack(side="left", padx=(U.px(10), 0))
        self.lbl_dk_ver = U.wlabel(head, "", "mute", U.FONT_XS)
        self.lbl_dk_ver.pack(side="right")

        self.lbl_dk_hint = U.wlabel(
            b, "未连接设备时检测的是本机。", "dim", U.FONT_S,
            justify="left", wraplength=U.px(760))
        self.lbl_dk_hint.pack(anchor="w", pady=(U.px(6), 0))

        row = U.wframe(b, "card")
        row.pack(fill="x", pady=(U.px(12), 0))
        U.RoundButton(row, "重新检测", self.docker_probe, variant="secondary",
                      height=32, padx=16).pack(side="left")
        U.RoundButton(row, "刷新", self.docker_refresh, variant="secondary",
                      height=32, padx=16).pack(side="left", padx=(U.px(8), 0))
        U.RoundButton(row, "新起容器", self.docker_run, variant="primary",
                      height=32, padx=16).pack(side="left", padx=(U.px(8), 0))
        self.btn_dk_stop = U.RoundButton(row, "停止", lambda: self.docker_action("stop"),
                                         variant="ghost", height=32, padx=12)
        self.btn_dk_stop.pack(side="left", padx=(U.px(14), 0))
        self.btn_dk_start = U.RoundButton(row, "启动", lambda: self.docker_action("start"),
                                          variant="ghost", height=32, padx=12)
        self.btn_dk_start.pack(side="left", padx=(U.px(4), 0))
        self.btn_dk_restart = U.RoundButton(row, "重启",
                                            lambda: self.docker_action("restart"),
                                            variant="ghost", height=32, padx=12)
        self.btn_dk_restart.pack(side="left", padx=(U.px(4), 0))
        self.btn_dk_logs = U.RoundButton(row, "日志", self.docker_logs,
                                         variant="ghost", height=32, padx=12)
        self.btn_dk_logs.pack(side="left", padx=(U.px(4), 0))
        self.btn_dk_rm = U.RoundButton(row, "删除", self.docker_remove,
                                       variant="danger", height=32, padx=12)
        self.btn_dk_rm.pack(side="left", padx=(U.px(4), 0))

        seg_row = U.wframe(pg, "page")
        seg_row.grid(row=1, column=0, sticky="we", pady=(U.px(10), 0))
        self.seg_dk = Segmented(
            seg_row, [("containers", "容器"), ("images", "镜像"),
                      ("stats", "资源占用")],
            command=self._dk_tab)
        self.seg_dk.pack(side="left")

        holder = U.wframe(pg, "page")
        holder.grid(row=2, column=0, sticky="nsew", pady=(U.px(8), 0))
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        c1 = U.Card(holder, title="容器", hint="双击看详情")
        c2 = U.Card(holder, title="镜像", hint="双击删除镜像")
        c3 = U.Card(holder, title="资源占用", hint="docker stats 单次快照")
        for card in (c1, c2, c3):
            card.grid(row=0, column=0, sticky="nsew")
        self._dk_cards = {"containers": c1, "images": c2, "stats": c3}

        self.tbl_dk = DataTable(c1.body, CONTAINER_COLS, height=9,
                                searchable=True, on_activate=self.docker_detail)
        self.tbl_dk.pack(fill="both", expand=True)
        self.tbl_dk.set_menu([
            ("查看详情", self.docker_detail, lambda r: bool(r)),
            ("查看日志", lambda r: self.docker_logs(r), lambda r: bool(r)),
            (None, None, None),
            ("启动", lambda r: self.docker_action("start", r), lambda r: bool(r)),
            ("停止", lambda r: self.docker_action("stop", r), lambda r: bool(r)),
            ("重启", lambda r: self.docker_action("restart", r), lambda r: bool(r)),
            (None, None, None),
            ("删除容器", lambda r: self.docker_remove(r), lambda r: bool(r)),
        ])

        self.tbl_img = DataTable(c2.body, IMAGE_COLS, height=9, searchable=True,
                                 on_activate=self.docker_rm_image)
        self.tbl_img.pack(fill="both", expand=True)

        self.tbl_stat = DataTable(c3.body, STATS_COLS, height=9, searchable=False)
        self.tbl_stat.pack(fill="both", expand=True)
        self.tbl_stat.set_empty("资源占用需要在有运行中的容器时才有数据",
                               "先启动一个容器，再点「刷新」。")

        self.engine: dk.DockerEngine | None = None
        self._dk_current = "containers"
        self._dk_set_enabled(False)
        c1.tkraise()

    # ------------------------------------------------------------ 状态
    def _dk_set_enabled(self, on: bool):
        for btn in (self.btn_dk_start, self.btn_dk_stop, self.btn_dk_restart,
                    self.btn_dk_logs, self.btn_dk_rm):
            btn.set_enabled(on)

    def _dk_tab(self, key: str):
        self._dk_current = key
        card = self._dk_cards.get(key)
        if card:
            card.tkraise()

    # ------------------------------------------------------------ 探测
    def docker_probe(self):
        s = self._active_session()
        dev = self._effective_device()
        self.engine = dk.DockerEngine(s, dev)
        self.lbl_dk_title.configure(text="正在检测 Docker…", fg=U.T.text)
        self.badge_dk.set("检测中", "mute")
        self.status.set_state("检测 Docker…")

        def work():
            return self.engine.detect()

        def done(info):
            self._apply_dk_state(info)
            if info.get("status") == dk.STATUS_OK:
                self.docker_refresh()

        self.bg(work, ok=done, err=lambda m: self._apply_dk_state(
            {"status": dk.STATUS_UNKNOWN, "detail": m, "version": ""}))

    def _apply_dk_state(self, info: dict):
        status = info.get("status")
        title, hint = (self.engine.message() if self.engine else ("Docker", ""))
        ok = status == dk.STATUS_OK
        tone = {dk.STATUS_OK: "ok", dk.STATUS_ABSENT: "warn",
                dk.STATUS_DAEMON: "warn", dk.STATUS_PERM: "err"}.get(status, "mute")
        badge = {dk.STATUS_OK: "可用", dk.STATUS_ABSENT: "未安装",
                 dk.STATUS_DAEMON: "未运行", dk.STATUS_PERM: "无权限"}.get(
            status, "未知")
        self.lbl_dk_title.configure(text=title, fg=U.T.text if ok else U.T.warn)
        self.badge_dk.set(badge, tone)
        self.lbl_dk_hint.configure(text=hint, fg=U.T.text_dim if ok else U.T.text_dim)
        self.lbl_dk_ver.configure(
            text=f"docker {info.get('version')}" if info.get("version") else "")
        self._dk_set_enabled(ok)
        if not ok:
            # 三张表统一换成「为什么没有数据」，让人一眼知道不是列表坏了
            for tbl in (self.tbl_dk, self.tbl_img, self.tbl_stat):
                tbl.set_rows([])
            self.tbl_dk.set_empty(title, hint)
            self.tbl_img.set_empty(title, hint)
            self.tbl_stat.set_empty(title, hint)
        self.status.set_state(title, "ok" if ok else "warn")
        # 探测本身用的都是 run()（不刷命令日志），所以这里补一条结论。
        # 否则「未安装」这种正常状态在日志里会留下一堆 PowerShell 红字，
        # 看着像程序出错。
        dev = self._effective_device()
        where = dev.os_name or dev.os_family or "本机"
        self._log_raw("ok" if ok else "meta",
                      f"[Docker] {where} · {badge}"
                      f"{' · ' + info.get('version', '') if info.get('version') else ''}\n")

    # ------------------------------------------------------------ 读
    def docker_refresh(self):
        eng = self.engine
        if not eng or not eng.ready:
            return self.docker_probe()
        self.status.set_state("读取 Docker 列表…")
        tab = self._dk_current

        def work():
            if tab == "images":
                return tab, eng.images()
            if tab == "stats":
                return tab, eng.stats()
            return tab, eng.ps(all_=True)

        def done(res):
            which, rows = res
            if which == "images":
                self.tbl_img.set_rows(rows)
                self.tbl_img.set_empty("没有镜像",
                                       "还没有拉取过镜像。可以用「新起容器」时顺手拉。")
            elif which == "stats":
                self.tbl_stat.set_rows(rows)
                self.tbl_stat.set_empty(
                    "没有运行中的容器",
                    "docker stats 只统计运行中的容器。")
            else:
                self.tbl_dk.set_rows(rows)
                self.tbl_dk.set_empty(
                    "还没有容器",
                    "点「新起容器」从镜像启动一个，或先拉取镜像。")
            self.status.set_state(f"已读取（{len(rows)} 项）", "ok")

        self.bg(work, ok=done, err=lambda m: (
            self.status.set_state(m, "err"),
            self.tbl_dk.set_empty("读取 Docker 失败", m)))

    # ------------------------------------------------------------ 操作
    def _dk_selected(self):
        row = self.tbl_dk.selected()
        if not row:
            self._warn("没有选中", "先在容器列表里选一个容器。")
        return row

    def docker_action(self, verb: str, row=None):
        row = row or self._dk_selected()
        if not row or not self.engine:
            return
        name = row.get("name") or row.get("id")
        zh = {"start": "启动", "stop": "停止", "restart": "重启",
              "pause": "暂停", "unpause": "恢复"}.get(verb, verb)
        if verb in ("stop", "restart") and not forms.confirm(
                self, f"确认{zh}", f"对容器「{name}」执行{zh}？"):
            return
        self.status.set_state(f"正在{zh} {name}…")

        def work():
            return self.engine.action(verb, row.get("id") or name)

        def done(res):
            rc, out, err = res
            if rc == 0:
                self.status.set_state(f"{name} 已{zh}", "ok")
                self.docker_refresh()
            else:
                self.status.set_state(f"失败 rc={rc}", "err")
                self._warn(f"{zh}失败", (err or out).strip()[:400])

        self.bg(work, ok=done, err=lambda m: (
            self.status.set_state(m, "err"), self._warn(f"{zh}失败", m)))

    def docker_remove(self, row=None):
        row = row or self._dk_selected()
        if not row or not self.engine:
            return
        name = row.get("name") or row.get("id")
        force = str(row.get("state", "")).lower() == "running"
        msg = f"删除容器「{name}」？"
        if force:
            msg += "\n\n它正在运行，会先强制停止（docker rm -f）。容器里的数据除非在卷里，否则一起没了。"
        if not forms.confirm(self, "删除容器", msg, danger=True, ok_text="删除"):
            return
        self.status.set_state(f"正在删除 {name}…")

        def work():
            return self.engine.remove(row.get("id") or name, force=force)

        def done(res):
            rc, out, err = res
            if rc == 0:
                self.status.set_state(f"{name} 已删除", "ok")
                self.docker_refresh()
            else:
                self._warn("删除失败", (err or out).strip()[:400])
                self.status.set_state("删除失败", "err")

        self.bg(work, ok=done, err=lambda m: self._warn("删除失败", m))

    def docker_logs(self, row=None):
        row = row or self._dk_selected()
        if not row or not self.engine:
            return
        name = row.get("name") or row.get("id")
        self.status.set_state(f"读取 {name} 日志…")

        def work():
            return self.engine.logs(row.get("id") or name, tail=400)

        def done(text):
            forms.InfoDialog.show(self, f"{name} · 日志", text,
                                  subtitle="最近 400 行", width=900, height=600)
            self.status.set_state("日志已读取", "ok")

        self.bg(work, ok=done, err=lambda m: self._warn("读取日志失败", m))

    def docker_detail(self, row):
        if not row or not self.engine:
            return
        name = row.get("name") or row.get("id")
        self.status.set_state(f"读取 {name} 详情…")

        def work():
            return self.engine.inspect(row.get("id") or name)

        def done(text):
            forms.InfoDialog.show(self, f"{name} · inspect", text,
                                  subtitle="docker inspect 原始 JSON",
                                  width=900, height=620)
            self.status.set_state("详情已读取", "ok")

        self.bg(work, ok=done, err=lambda m: self._warn("读取详情失败", m))

    def docker_rm_image(self, row):
        if not row or not self.engine:
            return
        ref = f"{row.get('repo')}:{row.get('tag')}"
        if not forms.confirm(self, "删除镜像", f"删除镜像 {ref}？",
                             danger=True, ok_text="删除"):
            return

        def work():
            return self.engine.remove_image(ref, force=True)

        def done(res):
            rc, out, err = res
            if rc == 0:
                self.status.set_state("镜像已删除", "ok")
                self.docker_refresh()
            else:
                self._warn("删除失败", (err or out).strip()[:400])

        self.bg(work, ok=done, err=lambda m: self._warn("删除失败", m))

    def docker_run(self):
        if not self.engine or not self.engine.ready:
            return self._warn("Docker 不可用", "先让 Docker 处于可用状态。")
        fields = [
            {"key": "image", "label": "镜像", "required": True,
             "default": "", "width": 36, "hint": "如 nginx:alpine；本地没有会自动 pull"},
            {"key": "name", "label": "容器名", "default": "",
             "hint": "留空由 Docker 随机命名"},
            {"key": "ports", "label": "端口映射", "default": "",
             "hint": "8080:80; 443:443（分号或逗号分隔）"},
            {"key": "volumes", "label": "卷", "default": "",
             "hint": "/host/path:/container/path; data:/data"},
            {"key": "envs", "label": "环境变量", "default": "",
             "hint": "KEY=value; KEY2=value2"},
            {"key": "restart", "label": "重启策略", "kind": "select",
             "options": ["", "no", "always", "unless-stopped", "on-failure"],
             "default": "unless-stopped"},
            {"key": "extra", "label": "附加参数", "default": "",
             "hint": "--network host 之类，原样拼进去"},
            {"key": "detach", "label": "后台运行（-d）", "kind": "check",
             "default": True},
        ]
        data = forms.FormDialog.open(self, "新起容器", fields,
                                     subtitle="相当于 docker run，参数会先给你确认")
        if not data:
            return
        preview = ["docker run " + ("-d " if data.get("detach") else "") +
                   f"{data.get('image','')}"]
        if not forms.confirm(self, "确认启动容器",
                             "即将执行（在设备上）：\n\n" + "\n".join(preview) +
                             "\n\n首次拉镜像可能要几分钟。"):
            return
        self.status.set_state("正在启动容器…")

        def work():
            return self.engine.run_container(
                data.get("image", ""), name=data.get("name", ""),
                ports=data.get("ports", ""), envs=data.get("envs", ""),
                volumes=data.get("volumes", ""), restart=data.get("restart", ""),
                extra=data.get("extra", ""), detach=bool(data.get("detach")),
                timeout=900)

        def done(res):
            rc, out, err = res
            if rc == 0:
                self.status.set_state("容器已启动", "ok")
                self._dk_tab("containers")
                self.seg_dk.select("containers", fire=False)
                self.docker_refresh()
            else:
                self._warn("启动失败", (err or out).strip()[:600])
                self.status.set_state("启动失败", "err")

        self.bg(work, ok=done, err=lambda m: self._warn("启动失败", m))

    def _warn(self, title, msg):
        from tkinter import messagebox
        messagebox.showwarning(title, msg, parent=self)
