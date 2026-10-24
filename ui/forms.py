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
  hint      控件下方灰色说明（按实际分到的宽度自动换行）
  hint_below 这一行太挤时，把说明挪到输入框下面单独一行
  width     输入框宽度（字符）
  unit      值右侧的单位文字
  pattern   (正则, 错误提示)
  placeholder
  filetypes file/secret_file 用：[(“私钥”, “*.pem *.key”), ...]
  initialdir 浏览时的起始目录（不给就优先 ~/.ssh）
  show_if   {源字段: [可接受的值, ...]} 或 lambda 值字典 -> bool
            不满足就整行收起来（也跳过必填校验）。联动靠它 —— 例如协议选
            「本机」时，地址 / 端口 / 认证 / 私钥 / 凭证 / 代理全都不该出现。
  watch     show_if 是谓词时，用它能反查依赖了谁（改这些字段要重算显隐）
  save      password 专用：框后面长一个「保存」勾，结果键是 `<key>__save`
  save_label  那个勾的文字，默认「保存」
  save_default 勾的初始状态（档案里已经有这个口令时应当默认勾上）
"""
from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import filedialog, ttk

import wbtui as U


def _autofit_hint(label: tk.Label) -> None:
    """让说明文字按「分到的宽度」换行，而不是按设计稿的固定像素。

    以前 wraplength 写死 300，窗口窄一点就变成「文字比格子宽」——
    Tk 不会换行，直接把右边切掉，看着像说明少了一截。
    """
    def _fit(event, lb=label):
        try:
            wrap = max(U.px(80), int(event.width) - U.px(4))
            if int(lb.cget("wraplength") or 0) != wrap:
                lb.configure(wraplength=wrap)
        except tk.TclError:
            pass
    label.bind("<Configure>", _fit)


def _hint(master, text: str, **kw) -> tk.Label:
    lb = U.wlabel(master, text, "mute", U.FONT_XS, justify="left",
                  wraplength=U.px(110), **kw)
    _autofit_hint(lb)
    return lb


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
        self._fixed_w = 0
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
        body = self._scroll_body(card.body)
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
        self._fit_body()
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

    # ------------------------------------------------------------ 滚动正文
    def _max_h(self) -> int:
        """这个窗口最高能长到多少（留出任务栏和标题栏的空间）。"""
        try:
            return max(U.px(320), int(self.winfo_screenheight() * 0.86))
        except tk.TclError:
            return U.px(680)

    def _scroll_body(self, parent) -> tk.Frame:
        """卡片里放一块可滚动的正文。

        字段一多（主机表单加上私钥密码、保存勾、目标系统就十几行），
        窗口会高过屏幕 —— 模态窗又不能滚，底下的「确定」直接被挤出屏幕，
        用户看到的就是「存不了」。所以正文一律可滚：
        够高时滚动条自动消失，不够高时也不会把按钮顶出去。
        """
        self._body_cv = tk.Canvas(parent, bg=U.T.card, bd=0, highlightthickness=0,
                                  width=U.px(400), height=U.px(200),
                                  yscrollincrement=U.px(18))
        self._body_sb = tk.Scrollbar(parent, orient="vertical",
                                     command=self._body_cv.yview,
                                     width=U.px(10), bd=0, relief="flat",
                                     troughcolor=U.T.card,
                                     activebackground=U.T.border,
                                     bg=U.T.border)
        self._body_cv.configure(yscrollcommand=self._body_sb.set)
        self._body_cv.pack(side="left", fill="both", expand=True)
        inner = U.wframe(self._body_cv, "card")
        self._body_inner = inner
        self._body_win = self._body_cv.create_window((0, 0), window=inner,
                                                     anchor="nw")
        inner.bind("<Configure>", self._on_body_inner)
        self._body_cv.bind("<Configure>", self._on_body_canvas)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", self._on_wheel)
        self.bind("<Button-5>", self._on_wheel)
        self._fit_job = None
        return inner

    def _on_body_inner(self, _e=None) -> None:
        try:
            self._body_cv.configure(scrollregion=self._body_cv.bbox("all"))
        except tk.TclError:
            return
        if self._fit_job is None:
            try:
                self._fit_job = self.after_idle(self._fit_body)
            except tk.TclError:
                pass

    def _on_body_canvas(self, event) -> None:
        try:
            self._body_cv.itemconfigure(self._body_win, width=max(1, event.width))
        except tk.TclError:
            pass
        if self._fit_job is None:
            try:
                self._fit_job = self.after_idle(self._fit_body)
            except tk.TclError:
                pass

    def _fit_body(self) -> None:
        """画布高度取「内容高度」，但不超过屏幕能给的空间。"""
        self._fit_job = None
        cv = getattr(self, "_body_cv", None)
        inner = getattr(self, "_body_inner", None)
        if cv is None or inner is None:
            return
        try:
            self.update_idletasks()
            chrome = max(0, self.winfo_reqheight() - cv.winfo_reqheight())
            avail = max(U.px(140), self._max_h() - chrome)
            want = max(U.px(120), inner.winfo_reqheight() + U.px(2))
            want = min(want, avail)
            if int(cv.cget("height")) != int(want):
                cv.configure(height=int(want))
            need = (inner.winfo_reqheight() > want + 2)
            if need and not self._body_sb.winfo_manager():
                self._body_sb.pack(side="right", fill="y", before=cv)
            elif not need and self._body_sb.winfo_manager():
                self._body_sb.pack_forget()
                cv.yview_moveto(0)
        except tk.TclError:
            pass

    def _on_wheel(self, event):
        """滚轮：内容比可视区高才滚，否则交给 Tk 默认处理。"""
        cv = getattr(self, "_body_cv", None)
        if cv is None:
            return None
        try:
            if self._body_inner.winfo_reqheight() <= cv.winfo_height() + 2:
                return None
            if getattr(event, "num", 0) == 4:
                step = -3
            elif getattr(event, "num", 0) == 5:
                step = 3
            else:
                step = -1 * int(getattr(event, "delta", 0) or 0) // 40 or -1
            cv.yview_scroll(int(step), "units")
        except tk.TclError:
            return None
        return "break"

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
            if f.get("save"):
                # 口令框后面就地放一个「保存」：勾了才落盘，留空表示这次不改。
                # 单独一个总开关会让人猜「到底存了哪几项」，所以一框一个。
                self._add_save_check(cell, f, values)

        if f.get("unit"):
            U.wlabel(cell, f["unit"], "mute", U.FONT_XS).pack(
                side="left", padx=(U.px(7), 0))
        if f.get("hint") and f.get("hint_below"):
            # 这一行控件太挤（输入框 + 浏览… + 说明），说明单独占一行更清楚
            lb2 = _hint(body, f["hint"])
            lb2.grid(row=r + 1, column=1, sticky="we", pady=(0, U.px(4)))
            self._row_widgets[f["key"]].append(lb2)
            return r + 2
        if f.get("hint"):
            # fill + expand：说明吃掉剩下的宽度，再按这个宽度自己换行
            _hint(cell, f["hint"]).pack(side="left", fill="x", expand=True,
                                        padx=(U.px(9), 0))
        return r + 1

    def _add_save_check(self, cell, f, values) -> None:
        """口令框后面那个「保存」勾。

        结果键是 `<字段>__save`，跟口令本身分开 —— 口令为空时「保不保存」
        仍然是一个独立的事实（留空 = 不改档案里已有的口令）。
        """
        key = f["key"] + "__save"
        var = tk.BooleanVar(
            value=bool(values.get(key, f.get("save_default", False))))
        ck = U.Check(cell, f.get("save_label", "保存"), var)
        ck.pack(side="left", padx=(U.px(7), 0))
        self._checks[key] = ck
        self._vars[key] = var
        self._getters[key] = ck.get

    def _row_check(self, body, f, values, r) -> int:
        cell = U.wframe(body, "card")
        # sticky="we"：整行都归它，说明文字才有地方换行（"w" 的话它只有
        # 勾选框那么宽，一句说明能被折成五行）
        cell.grid(row=r, column=0, columnspan=2, sticky="we",
                  pady=(U.px(6), U.px(6)))
        self._row_widgets[f["key"]] = [cell]
        ck = U.Check(cell, f.get("label", f["key"]), bg=None)
        ck.pack(anchor="w")
        ck.set(bool(values.get(f["key"], f.get("default", False))))
        self._checks[f["key"]] = ck
        self._getters[f["key"]] = ck.get
        self._vars[f["key"]] = ck.var
        if f.get("hint"):
            _hint(cell, f["hint"]).pack(anchor="w", fill="x", expand=True,
                                        padx=(U.px(26), 0))
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
        """谁被 show_if 提到，就盯住谁的变量。

        除了字典式 show_if 里点到的字段，还认 `watch` 列表 —— 谓词式
        show_if（lambda）没法反查依赖了谁，由字段自己声明。
        """
        watch: set[str] = set()
        for f in self.fields:
            spec = f.get("show_if")
            if isinstance(spec, dict):
                watch |= set(spec.keys())
            for k in (f.get("watch") or ()):
                watch.add(str(k))
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
        底下留一大片空白，看起来像没生效。高度上限压到屏幕内，
        超出的部分由正文区的滚动条接管。
        """
        try:
            self.update_idletasks()
            self._fit_body()
            self.update_idletasks()
            # 宽度用创建时定的那个：winfo_width() 在窗口还没映射时是 1，
            # 拿它当基准会让窗口被 reqwidth 缩小（580 → 470 这种事）。
            w = self._fixed_w or self.winfo_width() or self.winfo_reqwidth()
            h = min(self.winfo_reqheight(), self._max_h())
            x, y = self.winfo_rootx(), self.winfo_rooty()
            # 高度一变就可能顶出屏幕下沿：以前勾一下「连接后自动提权」多出一行，
            # 窗口往下长到 1238，可 y 还停在 +220 —— 底部的「确定」被屏幕边缘
            # 吃掉，用户看到的就是「存不了」。所以高度变了必须重新夹一次坐标。
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            x = max(0, min(x, sw - w - U.px(12)))
            # h 是客户区高度，外面还有标题栏和边框，一并让出来
            y = max(0, min(y, sh - h - U.px(64)))
            self.geometry(f"{w}x{h}+{x}+{y}")
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 动作
    def _center(self, parent, width: int) -> None:
        w = self.winfo_reqwidth()
        h = min(self.winfo_reqheight(), self._max_h())
        w = max(w, U.px(width))
        self._fixed_w = int(w)
        try:
            px = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
            py = parent.winfo_rooty() + max(U.px(30),
                                            (parent.winfo_height() - h) // 3)
            # 别让窗口顶到屏幕外面：底部按钮被任务栏吃掉是最烦的。
            # h 只是客户区，标题栏和边框还在外头，所以多留 px(64)。
            py = max(0, min(py, self.winfo_screenheight() - h - U.px(64)))
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
