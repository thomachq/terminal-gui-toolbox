# -*- coding: utf-8 -*-
"""DSM 风格的表单与信息对话框。

字段定义是一张表，界面按表自动排 —— 这样「新增路由」「新增防火墙规则」
「新增容器」这些页面不必各写一套重复的布局代码。

字段字典支持的键：
  key       结果字典里的键名
  label     左侧标签
  kind      text / password / int / select / check / textarea / readonly
            file        —— 明文路径 + 「浏览…」按钮（私钥、证书这类）
            secret_file —— 打码显示 + 「浏览…」，选中文件后转为明文
  default   默认值
  options   select 用：["a","b"] 或 [("值","显示")]
  required  必填
  hint      控件下方灰色说明
  width     输入框宽度（字符）
  unit      值右侧的单位文字
  pattern   (正则, 错误提示)
  placeholder
  filetypes file/secret_file 用：[(“私钥”, “*.pem *.key”), ...]
  initialdir 浏览时的起始目录（不给就优先 ~/.ssh）
  show_if   {源字段: [可接受的值, ...]} 或 lambda 值字典 -> bool
            不满足就整行收起来（也跳过必填校验）。联动靠它 —— 例如协议选
            「本机」时，地址 / 端口 / 认证 / 私钥 / 凭证 / 代理全都不该出现。
"""
from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import filedialog, ttk

import wbtui as U


class FormDialog(tk.Toplevel):
    def __init__(self, parent, title: str, fields: list[dict], values: dict | None = None,
                 subtitle: str = "", submit: str = "确定", danger: bool = False,
                 width: int = 560):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.configure(bg=U.T.page)
        self.resizable(False, False)
        self.transient(parent)
        self.result: dict | None = None
        self.fields = fields
        self._getters: dict[str, callable] = {}
        self._checks: dict[str, U.Check] = {}
        self._widgets: dict[str, tk.Widget] = {}
        # 联动：每个字段那一行有哪些控件 + select/check 的变量
        self._row_widgets: dict[str, list[tk.Widget]] = {}
        self._vars: dict[str, tk.Variable] = {}
        values = values or {}

        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(20), pady=U.px(18))

        U.wlabel(wrap, title, "page", U.FONT_T).pack(anchor="w")
        if subtitle:
            U.wlabel(wrap, subtitle, "pagedim", U.FONT_S, justify="left",
                     wraplength=U.px(width - 44)).pack(anchor="w", pady=(U.px(4), 0))

        card = U.Card(wrap)
        card.pack(fill="both", expand=True, pady=(U.px(14), 0))
        body = card.body
        body.columnconfigure(1, weight=1)

        r = 0
        for f in fields:
            kind = f.get("kind", "text")
            if kind == "check":
                r = self._row_check(body, f, values, r)
            elif kind == "readonly":
                r = self._row_readonly(body, f, values, r)
            else:
                r = self._row_input(body, f, values, r)

        self.lb_err = U.wlabel(wrap, "", "pageerr", U.FONT_S, justify="left",
                               wraplength=U.px(width - 44))
        self.lb_err.pack(anchor="w", pady=(U.px(8), 0))

        foot = U.wframe(wrap, "page")
        foot.pack(fill="x", pady=(U.px(12), 0))
        U.RoundButton(foot, submit, self._ok,
                      variant="danger" if danger else "primary",
                      height=34, padx=22).pack(side="right")
        U.RoundButton(foot, "取消", self._cancel, variant="secondary",
                      height=34, padx=18).pack(side="right", padx=(0, U.px(8)))

        self.bind("<Escape>", lambda e: self._cancel())
        self.bind("<Return>", lambda e: self._ok())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.update_idletasks()
        self._center(parent, width)
        self._bind_show_if()
        self._apply_show_if()
        self.deiconify()
        self.grab_set()
        first = next((f for f in fields if f.get("kind", "text") in
                      ("text", "password", "int")), None)
        if first:
            w = self._widgets.get(first["key"])
            if w is not None:
                try:
                    w.focus_set()
                except tk.TclError:
                    pass

    # ------------------------------------------------------------ 行
    def _widgets_init(self):
        if not hasattr(self, "_widgets"):
            self._widgets: dict[str, tk.Widget] = {}

    def _label(self, body, f, r):
        self._widgets_init()
        lb = U.wlabel(body, f.get("label", f["key"]), "dim", U.FONT_S)
        lb.grid(row=r, column=0, sticky="w", padx=(0, U.px(14)),
                pady=(U.px(7), U.px(7)))
        if f.get("required"):
            lb.configure(text=f.get("label", f["key"]) + " *",
                         fg=U.T.text)
        return lb

    def _row_input(self, body, f, values, r) -> int:
        lb = self._label(body, f, r)
        kind = f.get("kind", "text")
        cell = U.wframe(body, "card")
        cell.grid(row=r, column=1, sticky="ew", pady=(U.px(7), U.px(7)))
        self._row_widgets[f["key"]] = [lb, cell]
        val = values.get(f["key"], f.get("default", ""))
        width = int(f.get("width", 30))

        if kind == "select":
            opts = f.get("options") or []
            labels = [o[1] if isinstance(o, (tuple, list)) else str(o) for o in opts]
            keys = [o[0] if isinstance(o, (tuple, list)) else str(o) for o in opts]
            var = tk.StringVar()
            cb = ttk.Combobox(cell, textvariable=var, values=labels,
                              state="readonly", width=max(8, width - 4),
                              font=U.FONT_S)
            cb.pack(side="left")
            try:
                idx = keys.index(val)
            except ValueError:
                idx = 0
            if labels:
                cb.current(idx)
            def _get_sel(ks=keys, ls=labels, v=var):
                cur = v.get()
                if cur in ls:
                    return ks[ls.index(cur)]
                return ks[0] if ks else ""

            self._getters[f["key"]] = _get_sel
            self._widgets[f["key"]] = cb
            self._vars[f["key"]] = var
        elif kind == "textarea":
            tx = tk.Text(cell, height=int(f.get("lines", 4)), width=width,
                         font=U.MONO_S, bd=0, relief="flat",
                         highlightthickness=1,
                         highlightbackground=U.T.input_border,
                         highlightcolor=U.T.brand,
                         bg=U.T.input_bg, fg=U.T.text, insertbackground=U.T.text,
                         padx=U.px(7), pady=U.px(5))
            tx.insert("1.0", str(val or ""))
            tx.pack(fill="x", expand=True)
            self._getters[f["key"]] = lambda t=tx: t.get("1.0", "end").rstrip("\n")
            self._widgets[f["key"]] = tx
        elif kind in ("file", "secret_file"):
            # 私钥/证书这类要填「路径」的字段。让用户手打一串
            # C:\Users\...\.ssh\id_ed25519 是折磨，还容易错一个字符查半天。
            masked = (kind == "secret_file")
            ent = ttk.Entry(cell, width=width, font=U.FONT_S,
                            show="•" if masked else "")
            ent.insert(0, str(val or ""))
            ent.pack(side="left")
            self._getters[f["key"]] = lambda e=ent: e.get().strip()
            self._widgets[f["key"]] = ent

            def browse(_e=None, e=ent, spec=f, mk=masked):
                start = str(e.get() or "").strip()
                d = os.path.dirname(start)
                if not (d and os.path.isdir(d)):
                    d = spec.get("initialdir") or ""
                    if not d:
                        # 私钥八成在 ~/.ssh：默认定到那儿，少点三下
                        ssh = os.path.join(os.path.expanduser("~"), ".ssh")
                        d = ssh if os.path.isdir(ssh) else os.path.expanduser("~")
                path = filedialog.askopenfilename(
                    parent=self, title=spec.get("pick_title") or "选择文件",
                    initialdir=d,
                    filetypes=spec.get("filetypes") or [("全部文件", "*.*")])
                if not path:
                    return
                if mk:
                    # 选中的是一个路径，不是口令 —— 转成明文，好核对
                    e.configure(show="")
                e.delete(0, "end")
                e.insert(0, path)

            U.RoundButton(cell, "浏览…", browse, variant="secondary",
                          font=U.FONT_XS, height=24, padx=10).pack(
                side="left", padx=(U.px(6), 0))
        else:
            show = "•" if kind == "password" else ""
            ent = ttk.Entry(cell, width=width, font=U.FONT_S, show=show)
            ent.insert(0, str(val or ""))
            ent.pack(side="left")
            if kind == "int":
                self._getters[f["key"]] = lambda e=ent: e.get().strip()
            else:
                self._getters[f["key"]] = lambda e=ent: e.get().strip()
            self._widgets[f["key"]] = ent

        if f.get("unit"):
            U.wlabel(cell, f["unit"], "mute", U.FONT_XS).pack(
                side="left", padx=(U.px(7), 0))
        if f.get("hint"):
            U.wlabel(cell, f["hint"], "mute", U.FONT_XS, justify="left",
                     wraplength=U.px(300)).pack(side="left", padx=(U.px(9), 0))
        return r + 1

    def _row_check(self, body, f, values, r) -> int:
        cell = U.wframe(body, "card")
        cell.grid(row=r, column=0, columnspan=2, sticky="w",
                  pady=(U.px(6), U.px(6)))
        self._row_widgets[f["key"]] = [cell]
        ck = U.Check(cell, f.get("label", f["key"]), bg=None)
        ck.pack(anchor="w")
        ck.set(bool(values.get(f["key"], f.get("default", False))))
        self._checks[f["key"]] = ck
        self._getters[f["key"]] = ck.get
        self._vars[f["key"]] = ck.var
        if f.get("hint"):
            U.wlabel(cell, f["hint"], "mute", U.FONT_XS).pack(
                anchor="w", padx=(U.px(26), 0))
        return r + 1

    def _row_readonly(self, body, f, values, r) -> int:
        lb = self._label(body, f, r)
        cell = U.wframe(body, "card")
        cell.grid(row=r, column=1, sticky="ew", pady=(U.px(6), U.px(6)))
        self._row_widgets[f["key"]] = [lb, cell]
        txt = str(values.get(f["key"], f.get("default", "")))
        box = tk.Label(cell, text=txt, font=U.MONO_S if f.get("mono") else U.FONT_S,
                       bg=U.T.alt, fg=U.T.text_dim, anchor="w", justify="left",
                       padx=U.px(8), pady=U.px(5), wraplength=U.px(320))
        box.pack(side="left", fill="x", expand=True)
        self._getters[f["key"]] = lambda v=txt: v
        return r + 1

    # ------------------------------------------------------------ 联动
    def _visible(self, f) -> bool:
        spec = f.get("show_if")
        if not spec:
            return True
        vals = self._collect()
        if callable(spec):
            try:
                return bool(spec(vals))
            except Exception:  # noqa: BLE001  联动写错了不该让窗口打不开
                return True
        for key, allow in dict(spec).items():
            if not isinstance(allow, (list, tuple, set)):
                allow = (allow,)
            v = vals.get(key)
            if not any(str(v).strip().lower() == str(a).strip().lower()
                       for a in allow):
                return False
        return True

    def _bind_show_if(self) -> None:
        """谁被 show_if 提到，就盯住谁的变量。"""
        watch: set[str] = set()
        for f in self.fields:
            spec = f.get("show_if")
            if isinstance(spec, dict):
                watch |= set(spec.keys())
        for key in watch:
            var = self._vars.get(key)
            if var is None:
                continue
            try:
                var.trace_add("write", lambda *_: self._apply_show_if())
            except tk.TclError:
                pass

    def _apply_show_if(self) -> None:
        changed = False
        for f in self.fields:
            ws = self._row_widgets.get(f["key"]) or []
            if not ws:
                continue
            vis = self._visible(f)
            for w in ws:
                try:
                    if vis:
                        w.grid()
                    else:
                        w.grid_remove()
                except tk.TclError:
                    continue
            changed = True
        if changed:
            self._refit()

    def _refit(self) -> None:
        """字段增减后重新量高度，宽度与位置不动。

        不重设 geometry 的话，Toplevel 会保留原来的高度 —— 收起几行之后
        底下留一大片空白，看起来像没生效。
        """
        try:
            self.update_idletasks()
            w = self.winfo_width() or self.winfo_reqwidth()
            h = self.winfo_reqheight()
            x, y = self.winfo_rootx(), self.winfo_rooty()
            self.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 动作
    def _center(self, parent, width: int) -> None:
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        w = max(w, U.px(width))
        try:
            px = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
            py = parent.winfo_rooty() + max(U.px(40),
                                            (parent.winfo_height() - h) // 3)
        except tk.TclError:
            px = py = 200
        self.geometry(f"{w}x{h}+{max(0, px)}+{max(0, py)}")

    def _collect(self) -> dict:
        out = {}
        for key, get in self._getters.items():
            try:
                out[key] = get()
            except Exception:  # noqa: BLE001
                out[key] = ""
        return out

    def _ok(self):
        data = self._collect()
        for f in self.fields:
            key = f["key"]
            # 被 show_if 收起来的字段不参与校验 —— 用户看不见的东西不能拦住他
            if not self._visible(f):
                continue
            v = data.get(key)
            if f.get("required") and (v is None or str(v).strip() == ""):
                return self._fail(f"{f.get('label', key)} 不能为空")
            if f.get("kind") == "int" and str(v).strip() != "":
                try:
                    data[key] = int(str(v).strip())
                except ValueError:
                    return self._fail(f"{f.get('label', key)} 需要整数")
                lo, hi = f.get("min"), f.get("max")
                if lo is not None and data[key] < lo:
                    return self._fail(f"{f.get('label', key)} 不能小于 {lo}")
                if hi is not None and data[key] > hi:
                    return self._fail(f"{f.get('label', key)} 不能大于 {hi}")
            pat = f.get("pattern")
            if pat and str(v or "").strip():
                if not re.match(pat[0], str(v).strip()):
                    return self._fail(pat[1])
        self.result = data
        self.destroy()

    def _fail(self, msg: str):
        self.lb_err.configure(text=msg)
        try:
            self.bell()
        except tk.TclError:
            pass
        return None

    def _cancel(self):
        self.result = None
        self.destroy()

    # ------------------------------------------------------------ 静态入口
    @classmethod
    def open(cls, parent, title: str, fields: list[dict], values: dict | None = None,
             **kw) -> dict | None:
        dlg = cls(parent, title, fields, values, **kw)
        try:
            parent.wait_window(dlg)
        except tk.TclError:
            pass
        return dlg.result


class InfoDialog(tk.Toplevel):
    """只读信息窗：原始输出、容器日志这类长文本。"""

    def __init__(self, parent, title: str, body: str, subtitle: str = "",
                 width: int = 860, height: int = 560, copyable: bool = True):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=U.T.page)
        self.transient(parent)
        self._copy_src = body or ""
        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(18), pady=U.px(16))
        head = U.wframe(wrap, "page")
        head.pack(fill="x")
        U.wlabel(head, title, "page", U.FONT_T).pack(side="left")
        if subtitle:
            U.wlabel(head, subtitle, "pagedim", U.FONT_XS).pack(
                side="left", padx=(U.px(10), 0))
        U.RoundButton(head, "关闭", self.destroy, variant="secondary",
                      height=30, padx=16).pack(side="right")
        # 只读窗口里的文本能选中，但很多人直接找按钮 —— 给一个明确的复制入口，
        # 也顺手解决「点了没反应」：按钮文字会当场变成「已复制」。
        if copyable:
            self.btn_copy = U.RoundButton(head, "复制全文", self._do_copy,
                                          variant="ghost", height=30, padx=14)
            self.btn_copy.pack(side="right", padx=(0, U.px(6)))

        card = U.Card(wrap)
        card.pack(fill="both", expand=True, pady=(U.px(12), 0))
        self.txt = tk.Text(card.body, bg=U.T.console_bg, fg=U.T.console_fg,
                           font=U.MONO_S, bd=0, relief="flat",
                           highlightthickness=0, wrap="none",
                           padx=U.px(10), pady=U.px(8))
        sb = ttk.Scrollbar(card.body, orient="vertical", command=self.txt.yview,
                           style="Card.Vertical.TScrollbar")
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.insert("1.0", body or "")
        self.txt.configure(state="disabled")

        self.geometry(f"{U.px(width)}x{U.px(height)}")
        self.minsize(U.px(560), U.px(360))
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
            y = parent.winfo_rooty() + max(U.px(30),
                                           (parent.winfo_height() - self.winfo_height()) // 3)
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    @classmethod
    def show(cls, parent, title: str, body: str, **kw) -> None:
        cls(parent, title, body, **kw)

    def _do_copy(self):
        """复制全文，并在按钮上留一个看得见的结果。"""
        text = self._copy_src
        if not text:
            self.btn_copy.set_text("没有内容")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
        except tk.TclError:
            self.btn_copy.set_text("复制失败")
            return
        self.btn_copy.set_text(f"已复制 {len(text)} 字")
        try:
            self.after(1800, lambda: (
                self.btn_copy.set_text("复制全文")
                if self.btn_copy.winfo_exists() else None))
        except tk.TclError:
            pass


def confirm(parent, title: str, message: str, danger: bool = False,
            ok_text: str = "确定") -> bool:
    """统一确认框，避免各页面自己拼 messagebox 的措辞。"""
    from tkinter import messagebox
    icon = messagebox.WARNING if danger else messagebox.QUESTION
    return bool(messagebox.askokcancel(title, message, icon=icon, parent=parent,
                                       default=messagebox.CANCEL))
