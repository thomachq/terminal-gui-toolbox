# -*- coding: utf-8 -*-
"""脚本库 —— 内置常用动作 + 自定义脚本，双击就在当前主机上跑。

内置的那 24 条不是摆设：它们对应本应用其他页在做的事（磁盘水位、端口监听、
计划任务、证书到期、Docker 状态）。用户在图形页里看到的是结构化结果，
在脚本库里拿到的是同一份事实的原文 —— 需要贴给别人的时候用得上。
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox

import wbtui as U
from core import scripts as S
from ui import forms
from ui.table import Column, DataTable

SCRIPT_COLS = [
    Column("name", "名称", 190, stretch=True),
    Column("kind", "类型", 96),
    Column("preview", "内容预览", 320, mono=True),
    Column("lines", "行数", 60),
    Column("source", "来源", 76),
]


class ScriptPage:
    # ================================================================ 构建
    def build_script(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        bar = U.wframe(pg, "page")
        bar.grid(row=0, column=0, sticky="we", pady=(0, U.px(8)))
        U.RoundButton(bar, "＋ 新建脚本", self.script_add, variant="primary",
                      font=U.FONT_S, height=30, padx=13).pack(side="left")
        U.RoundButton(bar, "运行", self.script_run_pick, variant="secondary",
                      font=U.FONT_S, height=30, padx=12).pack(side="left",
                                                               padx=(U.px(6), 0))
        U.RoundButton(bar, "刷新", self.script_refresh, variant="ghost",
                      font=U.FONT_S, height=30, padx=10).pack(side="left",
                                                              padx=(U.px(6), 0))

        self.v_script_kind = tk.StringVar(value="全部类型")
        self.cb_script_kind = tk.OptionMenu(bar, self.v_script_kind, "全部类型",
                                            command=lambda _e: self._script_filter())
        self._style_optionmenu(self.cb_script_kind, width=10)
        self.cb_script_kind.pack(side="right")

        self.v_script_src = tk.StringVar(value="全部来源")
        self.cb_script_src = tk.OptionMenu(bar, self.v_script_src, "全部来源",
                                           command=lambda _e: self._script_filter())
        self._style_optionmenu(self.cb_script_src, width=9)
        self.cb_script_src.pack(side="right", padx=(0, U.px(6)))

        self.lbl_script_sum = U.wlabel(bar, "", "pagedim", U.FONT_XS)
        self.lbl_script_sum.pack(side="right", padx=(0, U.px(10)))

        self.tbl_script = DataTable(
            pg, SCRIPT_COLS, height=13,
            search_hint="搜索脚本名称、说明、标签",
            empty_title="还没有脚本",
            empty_hint="内置脚本已经在下面了；需要自己的动作就点「新建脚本」。",
            on_activate=lambda r: self.script_run((r or {}).get("_raw") or {}))
        self.tbl_script.grid(row=1, column=0, sticky="nsew")
        self.tbl_script.set_menu([
            ("运行", lambda: self.script_run(self._script_pick()), None),
            ("查看内容", lambda: self.script_view(self._script_pick()), None),
            (None, None, None),
            ("编辑", lambda: self.script_edit(self._script_pick()), None),
            ("复制为自定义", lambda: self.script_clone(self._script_pick()), None),
            ("复制脚本内容", lambda: self._script_copy(), None),
            (None, None, None),
            ("删除", lambda: self.script_del(self._script_pick()), None),
        ])

        self._script_busy = False

    # ================================================================ 数据
    def script_refresh(self) -> None:
        self._script_rows_all = [S.script_view(s) for s in self.scripts.all()]
        kinds = ["全部类型"] + [S.kind_label(k) for k in S.KIND_KEYS]
        srcs = ["全部来源", S.SOURCE_BUILTIN, S.SOURCE_CUSTOM]
        for cb, var, opts in ((self.cb_script_kind, self.v_script_kind, kinds),
                              (self.cb_script_src, self.v_script_src, srcs)):
            try:
                m = cb["menu"]
                m.delete(0, "end")
                for v in opts:
                    m.add_command(label=v, command=lambda x=v, vr=var: (
                        vr.set(x), self._script_filter()))
            except tk.TclError:
                pass
            if var.get() not in opts:
                var.set(opts[0])
        self._script_filter()

    def _script_filter(self) -> None:
        rows = list(getattr(self, "_script_rows_all", []))
        k = self.v_script_kind.get()
        if k and k != "全部类型":
            rows = [r for r in rows if str(r.get("kind") or "") == k]
        src = self.v_script_src.get()
        if src and src != "全部来源":
            rows = [r for r in rows if str(r.get("source") or "") == src]
        q = ""
        try:
            q = self.tbl_script.entry_search.get().strip()
        except (AttributeError, tk.TclError):
            pass
        if q:
            rows = [r for r in rows if all(
                w.lower() in " ".join([
                    str(r.get("name") or ""), str(r.get("desc") or ""),
                    str(r.get("tags") or ""), str(r.get("preview") or ""),
                ]).lower() for w in q.split())]
        self.tbl_script.set_rows(rows)
        self.lbl_script_sum.configure(
            text=S.summary(rows, total=len(getattr(self, "_script_rows_all", []))))

    def _script_pick(self) -> dict:
        row = self.tbl_script.selected()
        return (row or {}).get("_raw") or {}

    # ================================================================ 运行
    def script_run_pick(self) -> None:
        rec = self._script_pick()
        if not rec:
            self.toast("先选一个脚本", "warn")
            return
        self.script_run(rec)

    def script_run(self, rec: dict) -> None:
        if not rec:
            self.toast("先选一个脚本", "warn")
            return
        if self._script_busy:
            self.toast("上一个脚本还在跑", "warn")
            return
        sess = None
        try:
            sess = self._active_session()
        except Exception:  # noqa: BLE001
            sess = None
        if sess is None:
            self.toast("先连上一台主机，脚本要在目标机上跑", "warn")
            return
        name = str(rec.get("name") or "脚本")
        if not messagebox.askyesno(
                "运行脚本",
                f"在「{getattr(sess, 'label', '当前设备')}」上运行「{name}」？\n\n"
                f"{S.preview(rec.get('body'), 80)}",
                parent=self):
            return
        script = dict(rec)
        cmd = S.shell_cmd(script)
        self._script_busy = True
        self.toast(f"正在运行：{name}…", "info")
        dev = getattr(sess, "label", "") or ""
        self._audit("script.run", "info", f"运行脚本「{name}」", dev)

        def work():
            try:
                rc, out, err = sess.run(cmd, timeout=180)
            except Exception as e:  # noqa: BLE001
                rc, out, err = 1, "", str(e)
            self.after(0, lambda: self._script_done(name, rc, out, err, dev))

        threading.Thread(target=work, daemon=True).start()

    def _script_done(self, name: str, rc: int, out: str, err: str,
                     dev: str) -> None:
        self._script_busy = False
        body = (out or "")
        if err:
            body += ("\n" if body else "") + "==== 错误输出 ====\n" + err
        if not body.strip():
            body = "(没有输出)"
        status = "ok" if rc == 0 else "fail"
        self._audit("script.run", status,
                    f"脚本「{name}」退出码 {rc}", dev)
        try:
            self._log_raw("out", f"[脚本] {name} → rc={rc}\n{body}\n")
        except (AttributeError, tk.TclError):
            pass
        self.toast(f"脚本「{name}」结束，退出码 {rc}",
                   "ok" if rc == 0 else "err")
        forms.InfoDialog(
            self, f"脚本输出 · {name}",
            body, subtitle=f"退出码 {rc}" + (f" · {dev}" if dev else ""),
            width=900, height=560)

    # ================================================================ 增删改
    def script_add(self) -> None:
        kind = S.suggested_kind(getattr(self, "_os_family", ""), "")
        rec = self._script_form(S.blank_script(kind))
        if not rec:
            return
        try:
            saved = self.scripts.upsert(rec)
        except ValueError as e:
            messagebox.showwarning("保存不了", str(e), parent=self)
            return
        self.script_refresh()
        self._audit("script.save", "ok", f"新建脚本 {saved.get('name')}")
        self.toast(f"已保存脚本：{saved.get('name')}", "ok")

    def script_edit(self, rec: dict) -> None:
        if not rec:
            self.toast("先选一个脚本", "warn")
            return
        if rec.get("builtin"):
            # 内置的改不了 —— 改了下次升级会被覆盖，等于白改
            if messagebox.askyesno(
                    "内置脚本不可编辑",
                    f"「{rec.get('name')}」是内置脚本，改不了。\n\n"
                    "要不要复制一份成自定义脚本，在副本上改？", parent=self):
                self.script_clone(rec)
            return
        merged = self._script_form(dict(rec))
        if not merged:
            return
        merged["id"] = str(rec.get("id") or "")
        try:
            self.scripts.upsert(merged)
        except ValueError as e:
            messagebox.showwarning("保存不了", str(e), parent=self)
            return
        self.script_refresh()
        self.toast(f"已保存：{merged.get('name')}", "ok")

    def script_clone(self, rec: dict) -> None:
        if not rec:
            self.toast("先选一个脚本", "warn")
            return
        new = S.clone_as_custom(rec, self.scripts.ids())
        try:
            saved = self.scripts.upsert(new)
        except ValueError as e:
            messagebox.showwarning("复制不了", str(e), parent=self)
            return
        self.script_refresh()
        self.toast(f"已复制为「{saved.get('name')}」，可以直接编辑了", "ok")

    def script_view(self, rec: dict) -> None:
        if not rec:
            self.toast("先选一个脚本", "warn")
            return
        src = S.SOURCE_BUILTIN if rec.get("builtin") else S.SOURCE_CUSTOM
        forms.InfoDialog(
            self, str(rec.get("name") or "脚本"),
            str(rec.get("body") or ""),
            subtitle=f"{S.kind_label(str(rec.get('kind') or ''))} · {src}"
                     + (f" · {rec.get('desc')}" if rec.get("desc") else ""),
            width=820, height=520)

    def _script_copy(self) -> None:
        rec = self._script_pick()
        if not rec:
            return
        self.copy_text(str(rec.get("body") or ""), "脚本内容")

    def script_del(self, rec: dict) -> None:
        if not rec:
            self.toast("先选一个脚本", "warn")
            return
        sid = str(rec.get("id") or "")
        if self.scripts.is_builtin(sid):
            messagebox.showinfo("内置脚本不可删除",
                                "内置脚本跟着程序走，删了下次启动还会回来。\n"
                                "不想看见它，可以按「来源」筛成「自定义」。",
                                parent=self)
            return
        if not messagebox.askyesno("删除脚本",
                                   f"删除「{rec.get('name')}」？", parent=self):
            return
        self.scripts.remove(sid)
        self.script_refresh()
        self.toast("脚本已删除", "ok")

    def _script_form(self, rec: dict) -> dict | None:
        fields = [
            {"key": "name", "label": "名称", "kind": "text", "required": True,
             "default": str(rec.get("name") or "")},
            {"key": "kind", "label": "类型", "kind": "select",
             "options": [(k, S.KINDS[k]["label"]) for k in S.KIND_KEYS],
             "default": str(rec.get("kind") or "shell"),
             "hint": "跟着目标机的系统选：Linux/macOS 用 Shell，Windows 用 PowerShell"},
            {"key": "tags", "label": "标签", "kind": "text",
             "default": str(rec.get("tags") or ""),
             "hint": "逗号分隔，方便以后筛"},
            {"key": "desc", "label": "说明", "kind": "text",
             "default": str(rec.get("desc") or ""),
             "hint": "一句话说清它查什么，别人（和以后的你）看得懂"},
            {"key": "body", "label": "脚本内容", "kind": "textarea",
             "default": str(rec.get("body") or ""), "rows": 10,
             "hint": "不要在这里写口令 —— 脚本会被复制和导出"},
        ]
        data = forms.FormDialog.open(
            self, "编辑脚本" if rec.get("name") else "新建脚本", fields,
            subtitle="存 data/scripts.json（明文，方便直接改文件）")
        if not data:
            return None
        merged = S.script_from_form(data, base=rec if rec.get("id") else None)
        errs = S.validate(merged, others=self.scripts.all(),
                          skip_id=str(rec.get("id") or ""))
        if errs:
            messagebox.showwarning("保存不了", "\n".join(errs), parent=self)
            return None
        warn = S.name_warning(str(merged.get("name") or ""), self.scripts.all())
        if warn:
            self.toast(warn, "warn")
        return merged
