# -*- coding: utf-8 -*-
"""计划任务页：把散在四处的定时任务摊成一张表。

  · 用户 crontab（`crontab -l`）
  · /etc/crontab 与 /etc/cron.d/*
  · systemd timer
  · Windows 计划任务（`schtasks`）

改的时候不是 sed 就地替换，而是「读原文 → 在原文上改 → 整份写回」：
cron 的格式太容易被一行 sed 改坏，而整份写回至少能保证改动只发生在目标行。
关掉一条 = 注释掉，不是删除 —— 真删要单独确认。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import wbtui as U
from core import cronjob as C
from ui import forms
from ui.table import Column, DataTable

COLS = [
    Column("source", "来源", 116),
    Column("user", "用户", 84, mono=True),
    Column("schedule", "调度 / 单元", 130, mono=True),
    Column("when", "频率", 130),
    Column("command", "命令 / 激活的单元", 300, mono=True, stretch=True),
    Column("state", "状态", 66),
    Column("next", "下次执行", 140, mono=True),
]

FILTERS = [("all", "全部来源"), ("crontab", "用户 crontab"),
           ("etccrontab", "/etc/crontab"), ("crond", "/etc/cron.d"),
           ("timer", "systemd timer"), ("schtasks", "Windows 计划任务")]


class CronPage:
    # ------------------------------------------------------------ 构建
    def build_cron(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        c = U.Card(pg, title="计划任务", hint="cron / cron.d / systemd timer / schtasks")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        head = U.wframe(b, "card")
        head.pack(fill="x")
        self.lbl_cron_title = U.wlabel(head, "正在读取计划任务…", "card", U.FONT_B)
        self.lbl_cron_title.pack(side="left")
        self.badge_cron = U.Pill(head, "读取中", "mute", height=24)
        self.badge_cron.pack(side="left", padx=(U.px(10), 0))
        self.lbl_cron_hint = U.wlabel(head, "", "mute", U.FONT_XS)
        self.lbl_cron_hint.pack(side="right")

        self.lbl_cron_desc = U.wlabel(
            b, "关掉一条任务＝把它注释掉（保留原文，随时能开回来）；"
               "删除要单独确认。systemd timer 的开关请用 systemctl。",
            "dim", U.FONT_S, justify="left", wraplength=U.px(760))
        self.lbl_cron_desc.pack(anchor="w", pady=(U.px(6), 0))

        row = U.wframe(b, "card")
        row.pack(fill="x", pady=(U.px(12), 0))
        U.RoundButton(row, "刷新", self.cron_load, variant="secondary",
                      height=32, padx=16).pack(side="left")
        U.wlabel(row, "来源", "card", U.FONT_S).pack(side="left", padx=(U.px(16), 6))
        self.v_cron_filter = tk.StringVar()
        self.cb_cron_filter = ttk.Combobox(
            row, textvariable=self.v_cron_filter, width=18, state="readonly",
            values=[label for _, label in FILTERS])
        self.cb_cron_filter.current(0)
        self.cb_cron_filter.pack(side="left")
        self.cb_cron_filter.bind("<<ComboboxSelected>>",
                                 lambda e: self._cron_render())
        self.btn_cron_run = U.RoundButton(row, "立即执行", self.cron_run_now,
                                          variant="ghost", height=32, padx=12)
        self.btn_cron_run.pack(side="right")
        self.btn_cron_toggle = U.RoundButton(row, "启用 / 停用",
                                             self.cron_toggle, variant="ghost",
                                             height=32, padx=12)
        self.btn_cron_toggle.pack(side="right", padx=(0, U.px(6)))
        self.btn_cron_del = U.RoundButton(row, "删除", self.cron_del,
                                          variant="danger", height=32, padx=14)
        self.btn_cron_del.pack(side="right", padx=(0, U.px(6)))
        self.btn_cron_add = U.RoundButton(row, "新增任务", self.cron_add,
                                          variant="primary", height=32, padx=16)
        self.btn_cron_add.pack(side="right", padx=(0, U.px(6)))

        c2 = U.Card(pg, title="任务列表", hint="双击看原文 · 右键更多")
        c2.grid(row=1, column=0, sticky="nsew", pady=(U.px(10), 0))
        self.tbl_cron = DataTable(
            c2.body, COLS, height=10, searchable=True,
            empty_title="还没有读取计划任务",
            empty_hint="点「刷新」。空不代表没有 —— 也可能是权限不够看不到 root 的 crontab。",
            on_activate=self.cron_detail)
        self.tbl_cron.pack(fill="both", expand=True)
        self.tbl_cron.set_menu([
            ("查看原始行", self.cron_detail, lambda r: bool(r)),
            ("复制命令", lambda r: self.copy_text(r.get("command", ""), "命令"),
             lambda r: bool(r)),
            (None, None, None),
            ("启用 / 停用", self.cron_toggle, lambda r: bool(r)),
            ("立即执行一次", self.cron_run_now, lambda r: bool(r)),
            ("删除这条", self.cron_del, lambda r: bool(r)),
        ])

        c3 = U.Card(pg, title="操作记录", hint="每次改写都留痕")
        c3.grid(row=2, column=0, sticky="nsew", pady=(U.px(10), 0))
        self.box_cron = U.TextBox(c3.body, role="alt", font=U.MONO_S, height=6,
                                  wrap="none")
        self.box_cron.pack(fill="both", expand=True)

        self.cron_jobs: list[C.Job] = []
        self.cron_bundle: dict = {}
        self.cron_win = False
        self._cron_set_enabled(False)

    # ------------------------------------------------------------ 状态
    def _cron_set_enabled(self, on: bool):
        for btn in (self.btn_cron_add, self.btn_cron_del, self.btn_cron_toggle,
                    self.btn_cron_run):
            btn.set_enabled(on)

    def _cron_log(self, text: str) -> None:
        try:
            self.box_cron.txt.configure(state="normal")
            self.box_cron.txt.insert("end", text.rstrip() + "\n")
            self.box_cron.txt.see("end")
        except tk.TclError:
            pass

    def _cron_clear(self) -> None:
        try:
            self.box_cron.txt.configure(state="normal")
            self.box_cron.clear()
        except tk.TclError:
            pass

    def _cron_is_win(self) -> bool:
        return (getattr(self._effective_device(), "os_family", "") or "") == "windows"

    # ------------------------------------------------------------ 读取
    def cron_load(self):
        s = self._active_session()
        self.status.set_state("读取计划任务…")
        self.badge_cron.set("读取中", "mute")
        win = self._cron_is_win()
        self.cron_win = win

        def work():
            if s is None:
                return {"win": win, "text": "", "bundle": {}, "jobs": []}
            if win:
                rc, out, err = s.run_traced("schtasks /query /fo csv /nh",
                                            timeout=40)
                return {"win": True, "text": out or "",
                        "bundle": {}, "jobs": C.parse_schtasks(out or "")}
            rc, out, err = s.run_traced(C.POSIX_BUNDLE, timeout=40)
            bundle = C.parse_bundle(out or "")
            user = getattr(self._effective_device(), "user", "") or ""
            return {"win": False, "text": out or "", "bundle": bundle,
                    "jobs": C.jobs_from_bundle(bundle, default_user=user)}

        def done(res):
            self.cron_bundle = res.get("bundle") or {}
            self.cron_jobs = res.get("jobs") or []
            self._cron_set_enabled(True)
            self._cron_render()
            self.status.set_state(f"读到 {len(self.cron_jobs)} 条任务", "ok")
            self._cron_log(f"[读取] {'Windows 计划任务' if res.get('win') else 'cron + timers'}"
                           f" · {len(self.cron_jobs)} 条")
            self._log_raw("info", f"[计划任务] 读到 {len(self.cron_jobs)} 条\n")

        def fail(msg):
            self.badge_cron.set("失败", "err")
            self.lbl_cron_title.configure(text="读取失败", fg=U.T.err)
            self._cron_log(f"[失败] {msg}")
            self.status.set_state("读取计划任务失败", "err")

        self.bg(work, ok=done, err=fail)

    def _cron_render(self):
        idx = self.cb_cron_filter.current()
        want = FILTERS[idx][0] if 0 <= idx < len(FILTERS) else "all"
        rows = [j.as_row() for j in self.cron_jobs
                if want == "all" or j.source == want]
        self.tbl_cron.set_rows(rows)
        n_on = sum(1 for j in self.cron_jobs if j.enabled)
        self.badge_cron.set(f"{len(self.cron_jobs)} 条 · {n_on} 启用",
                            "ok" if self.cron_jobs else "mute")
        self.lbl_cron_title.configure(
            text=("Windows 计划任务" if self.cron_win else "cron 与 systemd timer"),
            fg=U.T.text)
        self.lbl_cron_hint.configure(
            text=" · ".join(f"{k} {v}" for k, v in _count_by_source(
                self.cron_jobs).items()) or "")
        self.tbl_cron.set_empty(
            "这一来源下没有任务",
            "换一个筛选条件看看；也可能是权限不足看不到别的用户的 crontab。")

    # ------------------------------------------------------------ 选中
    def _cron_selected(self) -> C.Job | None:
        row = self.tbl_cron.selected()
        if not row:
            self.toast("先选一条任务", "warn")
            return None
        jid = row.get("_id")
        for j in self.cron_jobs:
            if j.id == jid:
                return j
        return None

    def cron_detail(self, row=None):
        job = None
        if row:
            for j in self.cron_jobs:
                if j.id == row.get("_id"):
                    job = j
                    break
        job = job or self._cron_selected()
        if job is None:
            return
        lines = [
            f"来源：{job.source_cn}",
            f"位置：{job.path or '（用户 crontab）' if job.source != 'timer' else job.unit}",
            f"用户：{job.user or '—'}",
            f"调度：{job.schedule}（{C.describe(job.schedule)}）",
            f"状态：{'启用' if job.enabled else '已禁用'}",
        ]
        if job.next_run:
            lines.append(f"下次执行：{job.next_run}")
        lines += ["", "命令：", job.command or "—", "", "原始行：", job.raw]
        if job.source == "timer":
            lines += ["", "systemd timer 的开关不在本页改，用：",
                      f"systemctl disable --now {job.unit}"]
        forms.InfoDialog.show(self, "任务详情", "\n".join(lines),
                              subtitle=job.source_cn, width=760, height=460)

    # ------------------------------------------------------------ 改写
    def _cron_text_of(self, job: C.Job) -> tuple[str, str]:
        """→ (可写文本的 key, 当前文本)。"""
        key = C.source_key(job)
        if not key:
            return "", ""
        if key == "crontab":
            return key, self.cron_bundle.get("crontab", "")
        if key == "/etc/crontab":
            return key, self.cron_bundle.get("etccrontab", "")
        return key, (self.cron_bundle.get("files") or {}).get(key, "")

    def _cron_write(self, key: str, text: str, label: str):
        s = self._active_session()
        if s is None:
            self.toast("没有连接的设备", "err")
            return
        cmd = C.write_cmd_for(key, text)
        needs_admin = key != "crontab"
        self.status.set_state(f"写入 {label}…")

        def work():
            return s.run_admin(cmd, timeout=45) if needs_admin else s.run(cmd, timeout=45)

        def done(res):
            rc, out, err = res
            if rc == 0:
                self._cron_log(f"[写入] {label} · 成功")
                self._log_raw("ok", f"[计划任务] 写入 {label}\n")
                self.status.set_state(f"{label} 已更新", "ok")
                self.toast(f"{label} 已更新", "ok")
                self.after(300, self.cron_load)
            else:
                msg = (err or out or f"rc={rc}").strip()[:400]
                self._cron_log(f"[写入] {label} · 失败：{msg}")
                self._log_raw("bad", f"[计划任务] 写入 {label} 失败：{msg}\n")
                self.status.set_state("写入失败", "err")
                forms.confirm(self, "写入失败", f"{label}\n\n{msg}")

        self.bg(work, ok=done, err=lambda m: (
            self._cron_log(f"[写入] {m}"), self.status.set_state(m, "err")))

    def cron_toggle(self, row=None):
        job = self._cron_pick(row)
        if job is None:
            return
        if job.source == "timer":
            return self._timer_hint(job)
        key, text = self._cron_text_of(job)
        if not key:
            self.toast("这条任务不在可改的位置", "warn")
            return
        new_text = C.toggle_raw(text, job.raw)
        if new_text == text:
            self.toast("没找到这一行，可能刚被别处改过；先刷新", "warn")
            return
        action = "停用（注释掉）" if job.enabled else "启用（取消注释）"
        if not forms.confirm(
                self, action,
                f"{action}：\n\n{job.raw}\n\n位置：{key}\n\n继续？",
                ok_text="修改"):
            return
        self._cron_write(key, new_text, key)

    def cron_del(self, row=None):
        job = self._cron_pick(row)
        if job is None:
            return
        if job.source == "timer":
            return self._timer_hint(job)
        key, text = self._cron_text_of(job)
        if not key:
            return
        new_text = C.remove_raw(text, job.raw)
        if new_text == text:
            self.toast("没找到这一行；先刷新", "warn")
            return
        if not forms.confirm(
                self, "删除任务",
                f"从 {key} 里删掉这一行：\n\n{job.raw}\n\n"
                "删除不可撤销（关掉任务请用「启用 / 停用」，那是注释掉）。",
                danger=True, ok_text="删除"):
            return
        self._cron_write(key, new_text, key)

    def cron_add(self):
        if self.cron_win:
            return self._cron_add_win()
        fields = [
            {"key": "where", "label": "写到", "kind": "select",
             "options": [("crontab", "当前用户的 crontab"),
                         ("file", "/etc/cron.d/<名字>（系统级）")],
             "default": "crontab"},
            {"key": "name", "label": "文件名", "default": "toolbox-task",
             "hint": "选 /etc/cron.d 时才用；只能字母数字._-"},
            {"key": "user", "label": "运行用户", "default": "root",
             "hint": "系统级任务需要；用户 crontab 忽略此项"},
            {"key": "schedule", "label": "调度", "default": "0 3 * * *",
             "required": True, "width": 24,
             "hint": "分 时 日 月 周，如 0 3 * * * / */10 * * * * / @daily"},
            {"key": "command", "label": "命令", "default": "", "required": True,
             "width": 46, "hint": "写绝对路径；不支持交互式命令"},
        ]
        data = forms.FormDialog.open(
            self, "新增计划任务", fields,
            subtitle="写之前会先校验调度表达式，再整份写回")
        if not data:
            return
        spec = str(data.get("schedule") or "").strip()
        err = C.validate_schedule(spec)
        if err:
            return forms.confirm(self, "调度表达式不合法", err)
        command = str(data.get("command") or "").strip()
        where = data.get("where") or "crontab"
        if where == "crontab":
            key = "crontab"
            text = self.cron_bundle.get("crontab", "")
            new_text = C.append_job(text, spec, command,
                                    comment="Terminal Toolbox 新增")
            label = "用户 crontab"
        else:
            name = str(data.get("name") or "").strip() or "toolbox-task"
            if not _safe_name(name):
                return forms.confirm(self, "文件名不合法",
                                     "只能用字母、数字、点、下划线、短横线。")
            key = f"/etc/cron.d/{name}"
            text = (self.cron_bundle.get("files") or {}).get(key, "")
            new_text = C.append_job(text, spec, command,
                                    user=str(data.get("user") or "root").strip(),
                                    comment="Terminal Toolbox 新增")
            label = key
        if not forms.confirm(
                self, "确认新增",
                f"写入 {label}：\n\n{new_text.strip().splitlines()[-1]}\n\n继续？",
                ok_text="写入"):
            return
        self._cron_write(key, new_text, label)

    def _cron_add_win(self):
        fields = [
            {"key": "name", "label": "任务名", "default": "ToolboxTask",
             "required": True},
            {"key": "schedule", "label": "频率", "kind": "select",
             "options": [("DAILY", "每天"), ("WEEKLY", "每周"),
                         ("HOURLY", "每小时"), ("ONSTART", "开机时")],
             "default": "DAILY"},
            {"key": "start", "label": "开始时间", "default": "03:00",
             "hint": "HH:MM，开机触发可留空"},
            {"key": "command", "label": "运行", "default": "", "required": True,
             "width": 46, "hint": "命令行，例：powershell -File C:\\x.ps1"},
        ]
        data = forms.FormDialog.open(self, "新增计划任务", fields,
                                     subtitle="用 schtasks /create 创建")
        if not data:
            return
        cmd = C.schtasks_cmd("create", str(data.get("name")).strip(),
                             command=str(data.get("command")).strip(),
                             schedule=data.get("schedule") or "DAILY",
                             start=str(data.get("start") or "03:00").strip() or "03:00")
        if not forms.confirm(self, "确认创建", cmd, ok_text="创建"):
            return
        self._cron_run_cmd(cmd, "创建计划任务")

    def cron_run_now(self, row=None):
        job = self._cron_pick(row)
        if job is None:
            return
        if not forms.confirm(
                self, "立即执行一次",
                f"{job.command or job.unit}\n\n"
                "这会真的跑一遍，可能产生副作用（写文件、发请求）。继续？",
                danger=True, ok_text="执行"):
            return
        if job.source == "schtasks":
            cmd = C.schtasks_cmd("run", job.unit or job.schedule)
        elif job.source == "timer":
            cmd = f"systemctl start {job.command or job.unit}"
        else:
            cmd = job.command
        self._cron_run_cmd(cmd, "立即执行")

    def _cron_run_cmd(self, cmd: str, label: str):
        s = self._active_session()
        if s is None or not cmd:
            return
        self._cron_log(f"[{label}] $ {cmd}")
        self.status.set_state(f"{label}…")

        def work():
            return s.run_traced(cmd, timeout=120)

        def done(res):
            rc, out, err = res
            body = ((out or "") + (err or "")).strip()
            self._cron_log(f"[{label}] rc={rc} {body[:220]}")
            self.toast(f"{label}{'完成' if rc == 0 else '失败'}",
                       "ok" if rc == 0 else "err")
            self.status.set_state(f"{label} rc={rc}", "ok" if rc == 0 else "err")
            if rc == 0 and label == "创建计划任务":
                self.after(300, self.cron_load)

        self.bg(work, ok=done, err=lambda m: self._cron_log(f"[{label}] {m}"))

    # ------------------------------------------------------------ 小件
    def _cron_pick(self, row=None) -> C.Job | None:
        if row:
            for j in self.cron_jobs:
                if j.id == row.get("_id"):
                    return j
        return self._cron_selected()

    def _timer_hint(self, job: C.Job):
        forms.confirm(
            self, "systemd timer 不在这里改",
            f"{job.unit} 由 systemd 管理，cron 的写法套不上。\n\n"
            f"停用：systemctl disable --now {job.unit}\n"
            f"启用：systemctl enable --now {job.unit}\n"
            f"看日志：journalctl -u {job.unit} -n 50\n\n"
            "要改触发时间，请编辑对应的 .timer 文件（systemctl cat 能看到路径）。")


def _safe_name(name: str) -> bool:
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name or ""))


def _count_by_source(jobs: list) -> dict:
    out: dict[str, int] = {}
    for j in jobs:
        label = C.SOURCE_CN.get(j.source, j.source)
        out[label] = out.get(label, 0) + 1
    return out
