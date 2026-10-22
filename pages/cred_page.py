# -*- coding: utf-8 -*-
"""凭证管理 —— 名称 + 用户名 + 口令/私钥，主机按 id 引用。

界面上永远看不到口令原文，只有「已设置 · 12 位」。这不是抠细节：
截图、录屏、远程协助时，一句口令出现在列表里就泄了。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import wbtui as U
from core import creds as C
from ui import forms
from ui.table import Column, DataTable

CRED_COLS = [
    Column("name", "名称", 170, stretch=True),
    Column("kind", "类型", 100),
    Column("user", "用户名", 140),
    Column("secret", "口令 / 私钥", 220),
    Column("usage", "使用中", 90),
    Column("created", "创建时间", 140),
]


class CredPage:
    # ================================================================ 构建
    def build_cred(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        bar = U.wframe(pg, "page")
        bar.grid(row=0, column=0, sticky="we", pady=(0, U.px(8)))
        U.RoundButton(bar, "＋ 新增凭证", self.cred_add, variant="primary",
                      font=U.FONT_S, height=30, padx=13).pack(side="left")
        U.RoundButton(bar, "刷新", self.cred_refresh, variant="ghost",
                      font=U.FONT_S, height=30, padx=10).pack(side="left",
                                                              padx=(U.px(6), 0))

        self.v_cred_kind = tk.StringVar(value="全部类型")
        self.cb_cred_kind = tk.OptionMenu(bar, self.v_cred_kind, "全部类型",
                                          command=lambda _e: self._cred_filter())
        self._style_optionmenu(self.cb_cred_kind, width=11)
        self.cb_cred_kind.pack(side="right")
        self.lbl_cred_sum = U.wlabel(bar, "", "pagedim", U.FONT_XS)
        self.lbl_cred_sum.pack(side="right", padx=(0, U.px(10)))

        self.tbl_cred = DataTable(
            pg, CRED_COLS, height=13,
            search_hint="搜索凭证名称、用户名",
            empty_title="还没有凭证",
            empty_hint="把常用的登录账号存成凭证，多台主机就能共用一份，改一处全跟着变。",
            on_activate=lambda r: self.cred_edit((r or {}).get("_raw") or {}))
        self.tbl_cred.grid(row=1, column=0, sticky="nsew")
        self.tbl_cred.set_menu([
            ("编辑", lambda: self.cred_edit(self._cred_pick()), None),
            ("复制用户名", lambda: self._cred_copy_user(), None),
            ("复制私钥路径", lambda: self._cred_copy_key(), None),
            (None, None, None),
            ("删除", lambda: self.cred_del(self._cred_pick()), None),
        ])

    # ================================================================ 数据
    def cred_refresh(self) -> None:
        usage = self.creds.usage(self.profiles.profiles)
        self._cred_rows_all = [
            C.cred_view(c, usage.get(str(c.get("id") or ""), []))
            for c in self.creds.creds]
        kinds = ["全部类型"] + [C.kind_label(k) for k in C.KIND_KEYS]
        try:
            m = self.cb_cred_kind["menu"]
            m.delete(0, "end")
            for k in kinds:
                m.add_command(label=k, command=lambda v=k: (
                    self.v_cred_kind.set(v), self._cred_filter()))
        except tk.TclError:
            pass
        if self.v_cred_kind.get() not in kinds:
            self.v_cred_kind.set("全部类型")
        self._cred_filter()

    def _cred_filter(self) -> None:
        rows = list(getattr(self, "_cred_rows_all", []))
        k = self.v_cred_kind.get()
        if k and k != "全部类型":
            rows = [r for r in rows if str(r.get("kind") or "") == k]
        q = ""
        try:
            q = self.tbl_cred.entry_search.get().strip()
        except (AttributeError, tk.TclError):
            pass
        if q:
            rows = [r for r in rows if all(
                w.lower() in " ".join([
                    str(r.get("name") or ""), str(r.get("user") or ""),
                    str(r.get("kind") or ""), str(r.get("note") or ""),
                ]).lower() for w in q.split())]
        self.tbl_cred.set_rows(rows)
        self.lbl_cred_sum.configure(
            text=C.summary(rows, total=len(getattr(self, "_cred_rows_all", []))))

    def _cred_pick(self) -> dict:
        row = self.tbl_cred.selected()
        return (row or {}).get("_raw") or {}

    def _cred_copy_user(self) -> None:
        c = self._cred_pick()
        if not c:
            return
        self.copy_text(str(c.get("user") or ""), "用户名")

    def _cred_copy_key(self) -> None:
        c = self._cred_pick()
        if not c:
            return
        if str(c.get("kind") or "") != "key":
            self.toast("这条凭证不是私钥类型", "warn")
            return
        self.copy_text(str(c.get("secret") or ""), "私钥路径")

    # ================================================================ 增删改
    def cred_add(self) -> None:
        rec = self._cred_form(None)
        if not rec:
            return
        try:
            self.creds.upsert(rec)
        except ValueError as e:
            messagebox.showwarning("保存不了", str(e), parent=self)
            return
        self.cred_refresh()
        self._audit("cred.add", "ok", f"新增凭证 {rec['name']}")
        self.toast(f"已新增凭证：{rec['name']}", "ok")

    def cred_edit(self, rec: dict) -> None:
        if not rec:
            self.toast("先选一行再编辑", "warn")
            return
        merged = self._cred_form(dict(rec))
        if not merged:
            return
        merged["id"] = str(rec.get("id") or "")
        try:
            self.creds.upsert(merged)
        except ValueError as e:
            messagebox.showwarning("保存不了", str(e), parent=self)
            return
        self.cred_refresh()
        self.toast(f"已保存：{merged['name']}", "ok")

    def cred_del(self, rec: dict) -> None:
        if not rec:
            self.toast("先选一行再删除", "warn")
            return
        cid = str(rec.get("id") or "")
        usage = self.creds.usage(self.profiles.profiles)
        used = usage.get(cid, [])
        reason = C.delete_block_reason(used)
        if reason:
            # 有引用就不让删：删了那些主机会在下次连接时静默失败
            messagebox.showwarning("删不了", reason, parent=self)
            return
        if not messagebox.askyesno("删除凭证",
                                   f"删除「{rec.get('name')}」？\n删掉后无法恢复。",
                                   parent=self):
            return
        self.creds.remove(cid)
        self.cred_refresh()
        self._audit("cred.remove", "ok", f"删除凭证 {rec.get('name')}")
        self.toast("凭证已删除", "ok")

    def _cred_form(self, rec: dict | None) -> dict | None:
        rec = rec or C.blank_cred()
        has_secret = bool(str(rec.get("secret") or "").strip())
        fields = [
            {"key": "name", "label": "名称", "kind": "text", "required": True,
             "default": str(rec.get("name") or ""),
             "hint": "例如「运维账号」「只读巡检」"},
            {"key": "kind", "label": "类型", "kind": "select",
             "options": [(k, C.KINDS[k]["label"]) for k in C.KIND_KEYS],
             "default": str(rec.get("kind") or "password"),
             "hint": "改类型会清空已填的口令（密码和路径不是一回事）"},
            {"key": "user", "label": "用户名", "kind": "text",
             "default": str(rec.get("user") or ""),
             "hint": "登录时用的账号；私钥类型可以留空"},
            {"key": "secret", "label": "口令 / 路径", "kind": "password",
             "default": "",
             "hint": "留空表示不修改" + ("（已设置）" if has_secret else "")},
            {"key": "note", "label": "备注", "kind": "text",
             "default": str(rec.get("note") or ""),
             "hint": "用途说明，比如「仅限内网跳板」"},
        ]
        data = forms.FormDialog.open(
            self, "编辑凭证" if rec.get("name") else "新增凭证", fields,
            subtitle="口令整份加密落盘；列表里只显示「已设置」，不回显原文")
        if not data:
            return None
        merged = C.cred_from_form(data, base=rec if rec.get("id") else None)
        if rec.get("id") and not str(data.get("secret") or "").strip():
            merged["keep_secret"] = True      # 校验时不再要求重填
        errs = C.validate(merged, others=self.creds.creds,
                          skip_id=str(rec.get("id") or ""))
        if errs:
            messagebox.showwarning("保存不了", "\n".join(errs), parent=self)
            return None
        merged.pop("keep_secret", None)
        return merged
