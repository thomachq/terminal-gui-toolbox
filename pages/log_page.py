# -*- coding: utf-8 -*-
"""日志管理 —— 操作审计流，也是全程序唯一的一份日志。

形态照用户给的图：一行一条事件（标题 + 事件名 + 状态 + 时间），点开看详情，
底部是「第 x-y 条，共 n 条」加分页。分页放在这里不是好看 —— 审计日志动辄
上千条，一次全渲染会卡住窗口（Treeview 塞几千行很慢）。

v1.0.6 起，右侧那个「命令日志」面板已撤：两边记的是同一件事，分开只让人
纠结去哪查。运维工具执行的每条命令（命令原文 + 输出 + rc + 耗时）现在都由
app._audit_command 落成 `terminal.command` 事件，命令里的口令在落盘前就
被 core/redact.scrub_cmd 打成 ******，输出里的私钥正文也被整块替换。
"""
from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import wbtui as U
from core import audit as A
from ui import forms
from ui.table import Column, DataTable

LOG_COLS = [
    Column("title", "事件标题", 230, stretch=True),
    Column("event", "事件名", 220, mono=True),
    Column("group", "分类", 80),
    Column("status", "状态", 80),
    Column("device", "设备", 150),
    Column("time", "时间", 160),
]


class LogPage:
    # ================================================================ 构建
    def build_log(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(2, weight=1)

        bar = U.wframe(pg, "page")
        bar.grid(row=0, column=0, sticky="we", pady=(0, U.px(8)))
        U.RoundButton(bar, "刷新", self.audit_reload, variant="secondary",
                      font=U.FONT_S, height=30, padx=12).pack(side="left")
        U.RoundButton(bar, "导出日志", self.audit_export, variant="ghost",
                      font=U.FONT_S, height=30, padx=12).pack(side="left",
                                                               padx=(U.px(6), 0))

        self.v_audit_status = tk.StringVar(value="全部状态")
        self.cb_audit_status = tk.OptionMenu(bar, self.v_audit_status,
                                             "全部状态",
                                             command=lambda _e: self._audit_reset())
        self._style_optionmenu(self.cb_audit_status, width=10)
        self.cb_audit_status.pack(side="right")

        self.v_audit_group = tk.StringVar(value="全部分类")
        self.cb_audit_group = tk.OptionMenu(bar, self.v_audit_group, "全部分类",
                                            command=lambda _e: self._audit_reset())
        self._style_optionmenu(self.cb_audit_group, width=10)
        self.cb_audit_group.pack(side="right", padx=(0, U.px(6)))

        self.lbl_audit_sum = U.wlabel(bar, "", "pagedim", U.FONT_XS)
        self.lbl_audit_sum.pack(side="right", padx=(0, U.px(10)))

        self.tbl_audit = DataTable(
            pg, LOG_COLS, height=13,
            search_hint="搜索事件、详情、设备",
            empty_title="还没有操作记录",
            empty_hint="运维工具跑的命令与输出、AI 执行的动作、审批、"
                       "开关终端、增删主机都会记在这里；口令与私钥已打码。",
            on_activate=lambda r: self.audit_detail((r or {}).get("_raw") or {}))
        self.tbl_audit.grid(row=2, column=0, sticky="nsew")
        self.tbl_audit.set_menu([
            ("查看详情", lambda: self.audit_detail(self._audit_pick()), None),
            ("复制详情", lambda: self._audit_copy(), None),
            (None, None, None),
            ("只看这一类", lambda: self._audit_only_group(), None),
        ])

        # ---- 分页条（贴底一行）
        foot = U.wframe(pg, "page")
        foot.grid(row=3, column=0, sticky="we", pady=(U.px(6), 0))
        self.lbl_audit_range = U.wlabel(foot, "", "pagedim", U.FONT_XS)
        self.lbl_audit_range.pack(side="left")

        self.pager = U.wframe(foot, "page")
        self.pager.pack(side="right")
        self.v_audit_size = tk.StringVar(value=f"{A.DEFAULT_PAGE_SIZE} 条/页")
        self.cb_audit_size = tk.OptionMenu(
            self.pager, self.v_audit_size,
            *[f"{n} 条/页" for n in A.PAGE_SIZES],
            command=lambda _e: self._audit_reset())
        self._style_optionmenu(self.cb_audit_size, width=8)
        self.cb_audit_size.pack(side="left", padx=(0, U.px(8)))
        self._pager_btns = U.wframe(self.pager, "page")
        self._pager_btns.pack(side="left")

        self._audit_page = 1
        self._audit_rows: list[dict] = []      # 已筛选、已排序的全量
        self._audit_loaded = False

    # ================================================================ 取数
    def audit_reload(self) -> None:
        try:
            self.audit.clear_cache()
            evs = self.audit.load(limit=5000)
        except Exception as e:  # noqa: BLE001
            self.toast(f"读日志失败：{e}", "err")
            evs = []
        self._audit_events = evs
        # 日志面板现在是底部 dock，点了才建。在那之前筛选控件、表格都还不存在，
        # 直接往下走会 AttributeError（`_restest2` 就是这么炸的）。
        # 这里只负责把数据读进来；等 build_log 建好界面，它会自己再调一次。
        if getattr(self, "tbl_audit", None) is None:
            self._audit_loaded = True
            return
        groups = ["全部分类"] + A.groups()
        statuses = ["全部状态"] + [A.STATUS[k][0] for k in A.STATUS_KEYS]
        for cb, var, opts in ((self.cb_audit_group, self.v_audit_group, groups),
                              (self.cb_audit_status, self.v_audit_status, statuses)):
            try:
                m = cb["menu"]
                m.delete(0, "end")
                for v in opts:
                    m.add_command(label=v, command=lambda x=v, vr=var: (
                        vr.set(x), self._audit_reset()))
            except tk.TclError:
                pass
            if var.get() not in opts:
                var.set(opts[0])
        self._audit_loaded = True
        self._audit_reset()

    def _audit_reset(self) -> None:
        """筛选条件变了就回第一页 —— 停在第 7 页看一份更短的结果很莫名其妙。"""
        self._audit_page = 1
        self._audit_apply()

    def _audit_apply(self) -> None:
        # 界面还没建（dock 没打开）就没什么可刷的 —— 数据在 audit_reload 里已经缓存
        if getattr(self, "tbl_audit", None) is None:
            return
        evs = list(getattr(self, "_audit_events", []))
        st = self.v_audit_status.get()
        gr = self.v_audit_group.get()
        q = ""
        try:
            q = self.tbl_audit.entry_search.get().strip()
        except (AttributeError, tk.TclError):
            pass
        status_key = ""
        if st and st != "全部状态":
            for k, (label, _t) in A.STATUS.items():
                if label == st:
                    status_key = k
                    break
        evs = A.filter_events(evs, q=q, status=status_key,
                              group="" if gr == "全部分类" else gr)
        self._audit_rows = A.sort_events(evs, desc=True)

        size = self._audit_page_size()
        page_rows, pages, page = A.paginate(self._audit_rows, self._audit_page,
                                            size)
        self._audit_page = page
        self.tbl_audit.set_rows([A.event_view(e) for e in page_rows])
        self.lbl_audit_range.configure(
            text=A.range_text(page, size, len(self._audit_rows)))
        self.lbl_audit_sum.configure(
            text=A.summary(self._audit_rows,
                           total=len(getattr(self, "_audit_events", []))))
        self._pager_render(page, pages)

    def _audit_page_size(self) -> int:
        try:
            n = int(str(self.v_audit_size.get()).split()[0])
        except (ValueError, IndexError):
            return A.DEFAULT_PAGE_SIZE
        return n if n in A.PAGE_SIZES else A.DEFAULT_PAGE_SIZE

    def _pager_render(self, page: int, pages: int) -> None:
        for w in self._pager_btns.winfo_children():
            w.destroy()

        def btn(text, cmd, on=False, disabled=False):
            if disabled:
                lb = U.wlabel(self._pager_btns, text, "pagedim", U.FONT_S)
                lb.pack(side="left", padx=U.px(2))
                return
            b = U.RoundButton(self._pager_btns, text, cmd,
                              variant="primary" if on else "ghost",
                              font=U.FONT_S, height=26,
                              padx=9 if len(text) == 1 else 7)
            b.pack(side="left", padx=U.px(2))

        btn("‹", lambda: self._audit_goto(page - 1), disabled=page <= 1)
        for n in A.page_window(page, pages):
            if n is None:
                U.wlabel(self._pager_btns, "…", "pagedim",
                         U.FONT_S).pack(side="left", padx=U.px(3))
                continue
            btn(str(n), lambda v=n: self._audit_goto(v), on=(n == page))
        btn("›", lambda: self._audit_goto(page + 1), disabled=page >= pages)

    def _audit_goto(self, page: int) -> None:
        self._audit_page = max(1, int(page or 1))
        self._audit_apply()

    def _audit_pick(self) -> dict:
        row = self.tbl_audit.selected()
        return (row or {}).get("_raw") or {}

    # ================================================================ 详情
    def audit_detail(self, ev: dict) -> None:
        if not ev:
            self.toast("先选一条日志", "warn")
            return
        name = str(ev.get("event") or "")
        label, _tone = A.status_label(str(ev.get("status") or ""))
        head = [
            f"时间：{A.fmt_full(ev.get('ts'))}",
            f"事件：{name}",
            f"标题：{A.title_of(name)}",
            f"分类：{A.group_of(name)}",
            f"状态：{label}",
            f"设备：{ev.get('device') or '—'}",
            f"操作者：{ev.get('actor') or '—'}",
        ]
        if ev.get("dur"):
            head.append(f"耗时：{ev.get('dur')} ms")
        detail = str(ev.get("detail") or "")
        body = "\n".join(head)
        if detail:
            body += "\n\n---- 详情 ----\n" + detail
        body += "\n\n---- 原始记录 ----\n" + json.dumps(ev, ensure_ascii=False,
                                                       indent=2)
        forms.InfoDialog(self, "日志详情", body,
                         subtitle=A.title_of(name), width=760, height=520)

    def _audit_copy(self) -> None:
        ev = self._audit_pick()
        if not ev:
            return
        self.copy_text(json.dumps(ev, ensure_ascii=False, indent=2), "日志详情")

    def _audit_only_group(self) -> None:
        ev = self._audit_pick()
        if not ev:
            return
        g = A.group_of(str(ev.get("event") or ""))
        self.v_audit_group.set(g)
        self._audit_reset()

    # ================================================================ 导出
    def audit_export(self) -> None:
        rows = list(getattr(self, "_audit_rows", []))
        if not rows:
            self.toast("当前没有可导出的日志", "warn")
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="导出日志",
            defaultextension=".csv",
            initialfile=f"audit-{A.today_label()}.csv",
            filetypes=[("CSV（Excel 可直接打开）", "*.csv"),
                       ("JSON Lines", "*.jsonl")])
        if not path:
            return
        fmt = "jsonl" if Path(path).suffix.lower() == ".jsonl" else "csv"
        try:
            n = self.audit.export(path, rows, fmt)
        except OSError as e:
            messagebox.showerror("导出失败", str(e), parent=self)
            return
        self.toast(f"已导出 {n} 条日志到 {Path(path).name}", "ok")
