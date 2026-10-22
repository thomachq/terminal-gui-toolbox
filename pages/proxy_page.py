# -*- coding: utf-8 -*-
"""代理池 —— 跳板机与代理链。

链路是一列「上一跳」串起来的。界面上把整条链显示成 `东京 → 香港 → 目标`，
因为配的人真正关心的是「从哪进来」，不是某个节点的 parent 字段。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import wbtui as U
from core import creds as C
from core import proxies as P
from ui import forms
from ui.table import Column, DataTable

PROXY_COLS = [
    Column("name", "名称", 160, stretch=True),
    Column("kind", "类型", 100),
    Column("addr", "地址", 180, mono=True),
    Column("user", "用户名", 110),
    Column("chain", "链路", 240),
    Column("depth", "跳数", 70),
    Column("usage", "使用中", 90),
]


class ProxyPage:
    # ================================================================ 构建
    def build_proxy(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        bar = U.wframe(pg, "page")
        bar.grid(row=0, column=0, sticky="we", pady=(0, U.px(8)))
        U.RoundButton(bar, "＋ 添加代理", self.proxy_add, variant="primary",
                      font=U.FONT_S, height=30, padx=13).pack(side="left")
        U.RoundButton(bar, "刷新", self.proxy_refresh, variant="ghost",
                      font=U.FONT_S, height=30, padx=10).pack(side="left",
                                                              padx=(U.px(6), 0))

        self.v_proxy_kind = tk.StringVar(value="全部类型")
        self.cb_proxy_kind = tk.OptionMenu(bar, self.v_proxy_kind, "全部类型",
                                           command=lambda _e: self._proxy_filter())
        self._style_optionmenu(self.cb_proxy_kind, width=11)
        self.cb_proxy_kind.pack(side="right")
        self.lbl_proxy_sum = U.wlabel(bar, "", "pagedim", U.FONT_XS)
        self.lbl_proxy_sum.pack(side="right", padx=(0, U.px(10)))

        self.tbl_proxy = DataTable(
            pg, PROXY_COLS, height=13,
            search_hint="搜索代理名称、地址",
            empty_title="代理池是空的",
            empty_hint="需要经跳板机或 SOCKS5 才能到的主机，先在这里建一条链路。",
            on_activate=lambda r: self.proxy_edit((r or {}).get("_raw") or {}))
        self.tbl_proxy.grid(row=1, column=0, sticky="nsew")
        self.tbl_proxy.set_menu([
            ("编辑", lambda: self.proxy_edit(self._proxy_pick()), None),
            ("复制地址", lambda: self._proxy_copy_addr(), None),
            (None, None, None),
            ("删除", lambda: self.proxy_del(self._proxy_pick()), None),
        ])

    # ================================================================ 数据
    def proxy_refresh(self) -> None:
        used = P.usage_map(self.proxies.proxies, self.profiles.profiles)
        self._proxy_rows_all = [
            P.proxy_view(p, self.proxies.proxies,
                         used.get(str(p.get("id") or ""), []))
            for p in self.proxies.proxies]
        kinds = ["全部类型"] + [P.kind_label(k) for k in P.KIND_KEYS]
        try:
            m = self.cb_proxy_kind["menu"]
            m.delete(0, "end")
            for k in kinds:
                m.add_command(label=k, command=lambda v=k: (
                    self.v_proxy_kind.set(v), self._proxy_filter()))
        except tk.TclError:
            pass
        if self.v_proxy_kind.get() not in kinds:
            self.v_proxy_kind.set("全部类型")
        self._proxy_filter()

    def _proxy_filter(self) -> None:
        rows = list(getattr(self, "_proxy_rows_all", []))
        k = self.v_proxy_kind.get()
        if k and k != "全部类型":
            rows = [r for r in rows if str(r.get("kind") or "") == k]
        q = ""
        try:
            q = self.tbl_proxy.entry_search.get().strip()
        except (AttributeError, tk.TclError):
            pass
        if q:
            rows = [r for r in rows if all(
                w.lower() in " ".join([
                    str(r.get("name") or ""), str(r.get("addr") or ""),
                    str(r.get("user") or ""), str(r.get("chain") or ""),
                ]).lower() for w in q.split())]
        self.tbl_proxy.set_rows(rows)
        self.lbl_proxy_sum.configure(
            text=P.summary(rows, total=len(getattr(self, "_proxy_rows_all", []))))

    def _proxy_pick(self) -> dict:
        row = self.tbl_proxy.selected()
        return (row or {}).get("_raw") or {}

    def _proxy_copy_addr(self) -> None:
        p = self._proxy_pick()
        if p:
            self.copy_text(P.addr_of(p), "代理地址")

    # ================================================================ 增删改
    def proxy_add(self) -> None:
        rec = self._proxy_form(None)
        if not rec:
            return
        try:
            saved = self.proxies.upsert(rec)
        except ValueError as e:
            messagebox.showwarning("保存不了", str(e), parent=self)
            return
        self.proxy_refresh()
        self._audit("proxy.add", "ok", f"新增代理 {saved.get('name')}")
        self.toast(f"已添加代理：{saved.get('name')}", "ok")

    def proxy_edit(self, rec: dict) -> None:
        if not rec:
            self.toast("先选一行再编辑", "warn")
            return
        merged = self._proxy_form(dict(rec))
        if not merged:
            return
        merged["id"] = str(rec.get("id") or "")
        try:
            self.proxies.upsert(merged)
        except ValueError as e:
            messagebox.showwarning("保存不了", str(e), parent=self)
            return
        self.proxy_refresh()
        self.toast(f"已保存：{merged.get('name')}", "ok")

    def proxy_del(self, rec: dict) -> None:
        if not rec:
            self.toast("先选一行再删除", "warn")
            return
        pid = str(rec.get("id") or "")
        hosts_used = P.usage_map(self.proxies.proxies,
                                 self.profiles.profiles).get(pid, [])
        chain_used = P.children_of(self.proxies.proxies, pid)
        reason = P.delete_block_reason(hosts_used, chain_used)
        if reason:
            messagebox.showwarning("删不了", reason, parent=self)
            return
        if not messagebox.askyesno("删除代理",
                                   f"删除「{rec.get('name')}」？", parent=self):
            return
        self.proxies.remove(pid)
        self.proxy_refresh()
        self._audit("proxy.remove", "ok", f"删除代理 {rec.get('name')}")
        self.toast("代理已删除", "ok")

    def _proxy_form(self, rec: dict | None) -> dict | None:
        rec = rec or P.blank_proxy()
        cur_id = str(rec.get("id") or "")
        # 上一跳的候选里不能有它自己
        parent_opts = [("", "直连目标（不作上一跳）")]
        for p in self.proxies.proxies:
            pid = str(p.get("id") or "")
            if pid and pid != cur_id:
                parent_opts.append((pid, str(p.get("name") or pid)))
        cstore = getattr(self, "creds", None)
        cred_opts = [("", "不使用凭证")]
        if cstore is not None:
            for c in cstore.creds:
                cred_opts.append((str(c.get("id") or ""),
                                  f"{c.get('name')}（{C.kind_label(str(c.get('kind') or ''))}）"))

        fields = [
            {"key": "name", "label": "名称", "kind": "text", "required": True,
             "default": str(rec.get("name") or ""),
             "hint": "例如「东京跳板」"},
            {"key": "kind", "label": "类型", "kind": "select",
             "options": [(k, P.KINDS[k]["label"]) for k in P.KIND_KEYS],
             "default": str(rec.get("kind") or "ssh"),
             "hint": "换类型时端口会跟着换成默认值"},
            {"key": "host", "label": "地址", "kind": "text", "required": True,
             "default": str(rec.get("host") or "")},
            {"key": "port", "label": "端口", "kind": "text",
             "default": str(rec.get("port") or P.default_port(str(rec.get("kind") or "ssh")))},
            {"key": "user", "label": "用户名", "kind": "text",
             "default": str(rec.get("user") or "")},
            {"key": "cred_id", "label": "凭证", "kind": "select",
             "options": cred_opts,
             "default": str(rec.get("cred_id") or ""),
             "hint": "这条代理自己登录用的凭证"},
            {"key": "parent_id", "label": "上一跳", "kind": "select",
             "options": parent_opts,
             "default": str(rec.get("parent_id") or ""),
             "hint": "要先经过哪条代理才能到它；成环会被拦住"},
            {"key": "note", "label": "备注", "kind": "text",
             "default": str(rec.get("note") or "")},
        ]
        data = forms.FormDialog.open(
            self, "编辑代理" if rec.get("name") else "添加代理", fields,
            subtitle="代理地址与账号整份加密保存")
        if not data:
            return None
        merged = P.proxy_from_form(data, base=rec if cur_id else None)
        errs = P.validate(merged, others=self.proxies.proxies, skip_id=cur_id)
        if errs:
            messagebox.showwarning("保存不了", "\n".join(errs), parent=self)
            return None
        return merged
