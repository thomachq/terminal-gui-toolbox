# -*- coding: utf-8 -*-
"""DSM 风格数据表格。

设计要点（对齐群晖的列表观感）：
  · 卡片内一块表，表头浅底、行高舒适、斑马纹、无竖线
  · 顶部一条工具条：左边搜索框，右边动作按钮区（由页面挂载）
  · 空态不是一片空白：图标 + 一句话 + 一句怎么办
  · 点表头排序、右键出菜单、双击触发主操作

Treeview 只能整行着色，所以「状态」用文字色 + 前置圆点表达，
不做单元格级配色 —— 否则要么改 Canvas 自绘，要么视觉噪声过大。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

import wbtui as U

# 行级色调 → 前景色属性名
MAX_ROWS = 3000        # 单表最多渲染多少行（Windows 防火墙动辄三千多条）

_TONE_FG = {
    "ok": "ok", "warn": "warn", "err": "err",
    "dim": "text_dim", "mute": "text_mute", "brand": "brand",
}

DOT = {
    "ok": "●", "warn": "●", "err": "●", "brand": "●",
    "dim": "○", "mute": "○",
}


class Column:
    """列定义。

    title    表头文字
    key      取值字段
    width    像素宽（px 换算前）
    anchor   w / e / center
    stretch  是否随窗口拉伸
    mono     等宽字体（用于 IP、命令这类需要对位的内容）
    format   值格式化函数
    """

    def __init__(self, key: str, title: str, width: int = 120,
                 anchor: str = "w", stretch: bool = False, mono: bool = False,
                 format=None):
        self.key = key
        self.title = title
        self.width = width
        self.anchor = anchor
        self.stretch = stretch
        self.mono = mono
        self.format = format

    def text(self, row: dict) -> str:
        v = row.get(self.key, "")
        if self.format:
            try:
                return self.format(v, row)
            except Exception:  # noqa: BLE001
                return str(v)
        if v is None:
            return ""
        return str(v)


class EmptyState(tk.Frame):
    """空态：一句标题 + 一句说明（+ 可选按钮）。"""

    def __init__(self, master, title: str = "暂无数据", hint: str = ""):
        super().__init__(master, bg=U.T.card, bd=0)
        box = U.wframe(self, "card")
        box.pack(expand=True)
        self._icon = tk.Canvas(box, width=U.px(54), height=U.px(46),
                               bg=U.T.card, highlightthickness=0, bd=0)
        self._icon.pack(pady=(0, U.px(10)))
        self.lb_title = U.wlabel(box, title, "dim", U.FONT_B)
        self.lb_title.pack()
        self.lb_hint = U.wlabel(box, hint, "mute", U.FONT_S,
                                justify="center", wraplength=U.px(420))
        self.lb_hint.pack(pady=(U.px(6), 0))
        self.actions = U.wframe(box, "card")
        self.actions.pack(pady=(U.px(14), 0))
        U.register(self, self._paint)
        self._paint()

    def config_text(self, title: str, hint: str = "") -> None:
        self.lb_title.configure(text=title)
        self.lb_hint.configure(text=hint)

    def _paint(self):
        self.configure(bg=U.T.card)
        self._icon.configure(bg=U.T.card)
        self._icon.delete("all")
        c = U.T.text_mute
        w, h = U.px(54), U.px(46)
        # 一个「空列表」的线框
        self._icon.create_rectangle(U.px(6), U.px(4), w - U.px(6), h - U.px(4),
                                    outline=c, width=max(1, U.px(1.2)))
        for i in range(3):
            y = U.px(14) + i * U.px(9)
            self._icon.create_line(U.px(13), y, w - U.px(13), y,
                                   fill=c, dash=(3, 3))


class DataTable(tk.Frame):
    """带工具条、搜索、排序、空态、右键菜单的列表。"""

    def __init__(self, master, columns: list[Column], *, height: int = 9,
                 searchable: bool = True, search_hint: str = "搜索…",
                 empty_title: str = "暂无数据", empty_hint: str = "",
                 on_activate=None, on_select=None, bg_role: str = "card"):
        super().__init__(master, bg=U.T.card, bd=0)
        self.columns = list(columns)
        self._rows: list[dict] = []
        self._view: list[dict] = []
        self._sort_key: str | None = None
        self._sort_desc = False
        self._on_activate = on_activate
        self._on_select = on_select
        self._menu_items: list[tuple] = []
        self._empty_title = empty_title
        self._empty_hint = empty_hint
        self._search_var = tk.StringVar()
        # 用户拖出来的列宽（物理像素）。Tk 的 stretch 自动布局不能用 ——
        # 内容比可视宽的时候，它会把带 stretch 的列压回 minwidth，
        # 用户拖完一松手就弹回去（实测 255 → 72）。所以全部 stretch=False，
        # 余量由 _refit_columns() 自己补给「填充列」，且只增不减。
        self._user_w: dict[str, int] = {}
        self._fit_job = None
        self._sbx_job = None
        self._sbx_need = False

        # ---- 工具条
        self.toolbar = U.wframe(self, bg_role)
        self.toolbar.pack(fill="x", pady=(0, U.px(8)))
        self.search_wrap = tk.Frame(self.toolbar, bg=U.T.input_bg, bd=0,
                                    highlightthickness=1,
                                    highlightbackground=U.T.input_border,
                                    highlightcolor=U.T.input_border)
        self.search_wrap.pack(side="left")
        self._search_ic = tk.Canvas(self.search_wrap, width=U.px(24),
                                    height=U.px(24), bg=U.T.input_bg,
                                    highlightthickness=0, bd=0)
        self._search_ic.pack(side="left", padx=(U.px(4), 0))
        self.entry_search = tk.Entry(self.search_wrap, textvariable=self._search_var,
                                     bd=0, relief="flat", width=22,
                                     bg=U.T.input_bg, fg=U.T.text,
                                     insertbackground=U.T.text, font=U.FONT_S)
        self.entry_search.pack(side="left", padx=(0, U.px(8)), pady=U.px(4))
        if not searchable:
            self.search_wrap.pack_forget()
        self._search_ic.bind("<Button-1>", lambda e: self.entry_search.focus_set())
        self._search_var.trace_add("write", lambda *a: self._refilter())

        self.lb_count = U.wlabel(self.toolbar, "", "mute", U.FONT_XS)
        self.lb_count.pack(side="left", padx=(U.px(10), 0))
        # 右侧动作区：页面往里塞按钮
        self.actions = U.wframe(self.toolbar, bg_role)
        self.actions.pack(side="right")

        # ---- 表格
        self.table_wrap = tk.Frame(self, bg=U.T.card, bd=0)
        self.table_wrap.pack(fill="both", expand=True)
        keys = [c.key for c in self.columns]
        self.tree = ttk.Treeview(self.table_wrap, columns=keys, show="headings",
                                 height=height, selectmode="browse")
        self._apply_columns()
        self.sb = ttk.Scrollbar(self.table_wrap, orient="vertical",
                                command=self.tree.yview,
                                style="Card.Vertical.TScrollbar")
        # 横向滚动条：列多的时候（防火墙规则、进程）总宽会超过面板，
        # Treeview 不会换行也不会省略，直接裁字 —— 必须能横向滚。
        self.sb_x = ttk.Scrollbar(self.table_wrap, orient="horizontal",
                                  command=self.tree.xview)
        self.tree.configure(yscrollcommand=self.sb.set,
                            xscrollcommand=self._xscroll_set)
        self.table_wrap.columnconfigure(0, weight=1)
        self.table_wrap.rowconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.sb.grid(row=0, column=1, sticky="ns")
        self.sb_x.grid(row=1, column=0, sticky="ew")
        self.sb_x.grid_remove()          # 不需要时整条收起来，别白占 16px

        self.empty = EmptyState(self.table_wrap, empty_title, empty_hint)

        self.tree.bind("<Double-1>", self._dbl)
        self.tree.bind("<<TreeviewSelect>>", self._sel)
        self.tree.bind("<Button-3>", self._popup)
        # 面板拉伸 / 分栏拖动后空态要跟着重新居中，列宽也要重新分配余量
        self.tree.bind("<Configure>", self._on_tree_configure, add="+")
        self.tree.bind("<Configure>", lambda _e: self._place_empty(), add="+")
        # 松手才算「用户设定的宽度」：拖动过程中读到的中间值不能记
        self.tree.bind("<ButtonRelease-1>", self._remember_widths, add="+")
        self._menu = tk.Menu(self, tearoff=0)

        U.register(self, self._paint)
        self._paint()
        self._refresh_empty()

    # ------------------------------------------------------------ 列宽
    def _filler_key(self) -> str:
        """窗口变宽时把余量补给哪一列：优先 Column(stretch=True)，否则最后一列。"""
        for c in self.columns:
            if c.stretch:
                return c.key
        return self.columns[-1].key if self.columns else ""

    def _width_of(self, key: str) -> int:
        for c in self.columns:
            if c.key == key:
                return int(self._user_w.get(key, U.px(c.width)))
        return 0

    def _apply_columns(self) -> None:
        """按定义把列建出来。stretch 一律 False —— 拉伸自己算（见 _refit_columns）。"""
        for c in self.columns:
            self.tree.heading(c.key, text=c.title,
                              command=lambda k=c.key: self._sort_by(k))
            self.tree.column(c.key, width=self._width_of(c.key),
                             minwidth=U.px(48), anchor=c.anchor, stretch=False)
        self.after_idle(self._refit_columns)

    def _refit_columns(self) -> None:
        """把「比总宽多出来的那点」补给填充列；内容超宽就一个字都不动。

        关键是**只增不减**：一旦内容比可视宽（要靠横向滚动条），任何收缩都会
        把用户刚拖好的列挤回去。窗口变窄时填充列退回用户设定的宽度，
        超出部分交给横向滚动条。
        """
        self._fit_job = None
        try:
            avail = self.tree.winfo_width()
        except tk.TclError:
            return
        if avail <= 1 or not self.columns:
            return
        key = self._filler_key()
        want = {c.key: self._width_of(c.key) for c in self.columns}
        total = sum(want.values())
        if key and total < avail:
            want[key] += avail - total
        for c in self.columns:
            try:
                if int(self.tree.column(c.key, "width")) != want[c.key]:
                    self.tree.column(c.key, width=want[c.key])
            except tk.TclError:
                return

    def _on_tree_configure(self, _e=None) -> None:
        """窗口/面板尺寸变了才重排。拖动过程中 Tk 也会发 <Configure>，
        这里 debounce 一下，避免每一像素都重算列宽跟手打架。"""
        if self._fit_job:
            try:
                self.after_cancel(self._fit_job)
            except Exception:  # noqa: BLE001
                pass
        self._fit_job = self.after(40, self._refit_columns)

    def _remember_widths(self, _e=None) -> None:
        """松手时把当前宽度记成「用户设定值」，后面 resize 就以它为准。"""
        for c in self.columns:
            try:
                self._user_w[c.key] = int(self.tree.column(c.key, "width"))
            except tk.TclError:
                return
        # 拖完之后余量要重新分配（可能拖窄了，空出一截）
        self._on_tree_configure()

    # ------------------------------------------------------------ 主题
    def _xscroll_set(self, first, last) -> None:
        """横向滚动条按需显隐。

        回调在拖动列宽时**每像素**都会触发，这里只记状态、不立刻动布局：
        同步 grid()/grid_remove() 会让 Treeview 的高度变化 → <Configure> →
        再触发列宽重排，于是拖动变得一顿一顿的。
        """
        try:
            self.sb_x.set(first, last)
        except tk.TclError:
            return
        try:
            need = bool(self._view) and (float(first) > 0.001 or float(last) < 0.999)
        except (TypeError, ValueError):
            need = bool(self._view)
        if need == self._sbx_need:
            return
        self._sbx_need = need
        if self._sbx_job:
            try:
                self.after_cancel(self._sbx_job)
            except Exception:  # noqa: BLE001
                pass
        self._sbx_job = self.after(80, self._apply_sb_x)

    def _apply_sb_x(self) -> None:
        self._sbx_job = None
        try:
            shown = bool(self.sb_x.winfo_manager())
            if self._sbx_need and not shown:
                self.sb_x.grid()
            elif not self._sbx_need and shown:
                self.sb_x.grid_remove()
        except tk.TclError:
            pass

    def _paint(self):
        self.configure(bg=U.T.card)
        for w in (self.table_wrap, self.actions, self.toolbar):
            w.configure(bg=U.T.card if w is not self.toolbar else U.T.card)
        self.search_wrap.configure(bg=U.T.input_bg,
                                   highlightbackground=U.T.input_border,
                                   highlightcolor=U.T.input_border)
        self._search_ic.configure(bg=U.T.input_bg)
        self._search_ic.delete("all")
        c = U.T.text_mute
        self._search_ic.create_oval(U.px(7), U.px(6), U.px(15), U.px(14),
                                    outline=c, width=max(1, U.px(1.3)))
        self._search_ic.create_line(U.px(14), U.px(13), U.px(18), U.px(17),
                                    fill=c, width=max(1, U.px(1.3)))
        self.entry_search.configure(bg=U.T.input_bg, fg=U.T.text,
                                    insertbackground=U.T.text)
        try:
            self.tree.tag_configure("odd", background=U.T.card, foreground=U.T.text)
            self.tree.tag_configure("even", background=U.T.alt, foreground=U.T.text)
            for tone, key in _TONE_FG.items():
                col = getattr(U.T, key)
                self.tree.tag_configure("t_" + tone, foreground=col)
        except tk.TclError:
            pass
        try:
            style = ttk.Style(self)
            style.configure("Table.Treeview", background=U.T.card,
                            fieldbackground=U.T.card, foreground=U.T.text,
                            rowheight=U.px(30), borderwidth=0, font=U.FONT_S)
            style.configure("Table.Treeview.Heading", background=U.T.alt,
                            foreground=U.T.text_dim,
                            font=(U.FONT_FAMILY, 9, "bold"),
                            padding=(U.px(8), U.px(8)), relief="flat")
            self.tree.configure(style="Table.Treeview")
        except tk.TclError:
            pass
        self._render()

    # ------------------------------------------------------------ 数据
    def set_columns(self, columns: list[Column]) -> None:
        """换一套列（不同工具输出结构不一样，共用一个表格控件）。"""
        self.columns = list(columns)
        keys = [c.key for c in self.columns]
        # 只保留仍然存在的列的拖动记录，否则同一 key 换了定义还沿用老宽度
        self._user_w = {k: v for k, v in self._user_w.items() if k in keys}
        try:
            self.tree.configure(columns=keys, displaycolumns=keys)
        except tk.TclError:
            return
        self._apply_columns()
        self._render()

    def set_rows(self, rows: list[dict]) -> None:
        self._rows = [r for r in (rows or []) if isinstance(r, dict)]
        self._refilter()

    def rows(self) -> list[dict]:
        return list(self._view)

    def all_rows(self) -> list[dict]:
        return list(self._rows)

    def set_empty(self, title: str, hint: str = "") -> None:
        self._empty_title, self._empty_hint = title, hint
        self.empty.config_text(title, hint)
        self._refresh_empty()

    def selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            i = int(sel[0])
        except (TypeError, ValueError):
            return None
        return self._view[i] if 0 <= i < len(self._view) else None

    def select_first(self) -> None:
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set(kids[0])
            self.tree.focus(kids[0])

    def select_row(self, pred) -> dict | None:
        """按谓词找第一行并选中它（改完规则后高亮新行用）。

        pred 可以是 dict 里的字段子集（`{"port": "8080"}`），也可以是
        `lambda r: ...`。找不到返回 None，不抛。
        """
        if callable(pred):
            match = pred
        else:
            keys = dict(pred or {})

            def match(r):
                return all(str(r.get(k, "")).strip() == str(v).strip()
                           for k, v in keys.items())
        for i, row in enumerate(self._view):
            try:
                if not match(row):
                    continue
            except Exception:  # noqa: BLE001  谓词写错了就当没匹配上
                return None
            try:
                iid = str(i)
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
            except tk.TclError:
                return None
            return row
        return None

    # ------------------------------------------------------------ 内部
    @staticmethod
    def _search_blob(row: dict) -> str:
        parts = []
        for k, v in row.items():
            if k.startswith("_"):
                continue
            parts.append(str(v))
        return " ".join(parts).lower()

    def _refilter(self) -> None:
        kw = self._search_var.get().strip().lower()
        if kw:
            self._view = [r for r in self._rows if kw in self._search_blob(r)]
        else:
            self._view = list(self._rows)
        if self._sort_key:
            self._apply_sort()
        self._render()

    def _sort_by(self, key: str) -> None:
        if self._sort_key == key:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key, self._sort_desc = key, False
        self._apply_sort()
        self._render()

    def _apply_sort(self) -> None:
        key = self._sort_key
        if not key:
            return

        def num(v):
            try:
                return (0, float(str(v).replace("%", "").replace(",", "")))
            except (TypeError, ValueError):
                return (1, str(v).lower())

        self._view.sort(key=lambda r: num(r.get(key, "")), reverse=self._sort_desc)

    def _render(self) -> None:
        try:
            self.tree.delete(*self.tree.get_children())
        except tk.TclError:
            return
        shown = self._view[:MAX_ROWS]
        for i, row in enumerate(shown):
            vals = [c.text(row) for c in self.columns]
            tags = ["even" if i % 2 else "odd"]
            tone = row.get("_tone")
            if tone in _TONE_FG:
                tags.append("t_" + tone)
            if row.get("_strike"):
                tags.append("t_mute")
            try:
                # iid 用序号：排序/过滤后仍能反查 _view
                self.tree.insert("", "end", iid=str(i), values=vals, tags=tuple(tags))
            except tk.TclError:
                pass
        if len(self._view) > MAX_ROWS:
            self.lb_count.configure(
                text=f"{len(self._view)} 项（列表只渲染前 {MAX_ROWS} 条，用搜索缩小）")
        else:
            self.lb_count.configure(
                text=f"{len(self._view)} 项" if self._view else "")
        self._refresh_empty()
        hdr = [(c.title, self._sort_key == c.key, self._sort_desc) for c in self.columns]
        for c, (title, active, desc) in zip(self.columns, hdr):
            mark = (" ▼" if desc else " ▲") if active else ""
            try:
                self.tree.heading(c.key, text=title + mark)
            except tk.TclError:
                pass

    def _refresh_empty(self) -> None:
        try:
            if self._view:
                if not self.sb.winfo_manager():
                    self.sb.grid()
                self.empty.place_forget()
            else:
                # 空表没有内容可滚，竖/横滚动条一起收起来，别在空态上画两条轨道
                self._sbx_need = False
                self.sb.grid_remove()
                self.sb_x.grid_remove()
                self._place_empty()
                self.after_idle(self._place_empty)
        except tk.TclError:
            pass

    def _place_empty(self) -> None:
        """空态居中在**表头之下**的正文区。

        之前用固定 `rely=0.44`：表短的时候（连接池 4~5 行）那个位置正好压在
        表头文字上，图标和标题叠着列名，很脏。
        """
        try:
            if self._view:
                return
            th = self.tree.winfo_height()
            hdr = U.px(30)
            body = max(U.px(80), th - hdr)
            self.empty.place(relx=0.5, y=hdr + body // 2, anchor="center")
            self.empty.lift()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 事件
    def _dbl(self, _e=None):
        row = self.selected()
        if row and self._on_activate:
            self._on_activate(row)

    def _sel(self, _e=None):
        if self._on_select:
            self._on_select(self.selected())

    def set_menu(self, items: list[tuple]) -> None:
        """items: [(label, callback, enabled_fn|None)]，label 为 None 表示分隔线。"""
        self._menu_items = items or []

    def _popup(self, event):
        if not self._menu_items:
            return
        row = self.selected()
        if row is None:
            # 右键空白行也允许（比如「新建」）
            pass
        self._menu.delete(0, "end")
        for label, cb, enabled in self._menu_items:
            if label is None:
                self._menu.add_separator()
                continue
            ok = True
            if enabled:
                try:
                    ok = bool(enabled(row))
                except Exception:  # noqa: BLE001
                    ok = False
            self._menu.add_command(label=label,
                                   command=(lambda c=cb: c(row)) if cb else None,
                                   state="normal" if ok else "disabled")
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    def bind_enter(self, callback) -> None:
        self.entry_search.bind("<Return>", lambda e: callback())


class KVGrid(tk.Frame):
    """两列信息网格（标签 / 值），比纯文本输出更适合「概览」。

    `columns` 只是**上限**：宽度不够时会自动降列（3 → 2 → 1）。
    早先固定 3 列的写法在半宽面板里会把值硬截成 `route (Windo`、`192.16:`，
    格子被压窄后 tk 直接裁字，比换行还难看，所以在 <Configure> 里按实测宽度重排。
    """

    def __init__(self, master, columns: int = 2, bg_role: str = "card",
                 label_w: int = 108):
        super().__init__(master, bg=U.T.card, bd=0)
        self.ncol_max = max(1, columns)
        self.ncol = self.ncol_max
        self.label_w = label_w
        self._cells: list[tuple] = []
        self._items: list[tuple] = []
        self._laid_out = 0
        self._reflow_job = None
        self.bg_role = bg_role
        self._font = None
        self.bind("<Configure>", self._on_configure)

    # ------------------------------------------------ 自适应列数
    def _measure(self, text: str) -> int:
        try:
            if self._font is None:
                self._font = tkfont.Font(font=U.FONT_S)
            return self._font.measure(str(text))
        except Exception:  # noqa: BLE001
            return len(str(text)) * 7

    def _cell_width(self) -> int:
        """一列（标签 + 值 + 两侧留白）需要多宽。"""
        pad = U.px(10) + U.px(24)
        widest = 0
        for label, value, *_ in self._items:
            widest = max(widest, self._measure(label) + self._measure(value) + pad)
        return max(widest, self._measure("字" * 8) + pad)

    def _fit_columns(self) -> int:
        w = self.winfo_width()
        if w <= 1 or not self._items:
            return self.ncol_max
        need = self._cell_width()
        if need <= 0:
            return self.ncol_max
        return max(1, min(self.ncol_max, int(w // need) or 1))

    def _on_configure(self, _e=None) -> None:
        if self._reflow_job:
            try:
                self.after_cancel(self._reflow_job)
            except Exception:  # noqa: BLE001
                pass
        self._reflow_job = self.after(60, self._maybe_reflow)

    def _maybe_reflow(self) -> None:
        self._reflow_job = None
        n = self._fit_columns()
        if n != self._laid_out:
            self._layout(n)

    # ------------------------------------------------ 内容
    def set_items(self, items: list[tuple]) -> None:
        """items: [(label, value, tone|None)]，整体重建。"""
        self._items = [(i[0], i[1], i[2] if len(i) > 2 else None) for i in items]
        self._laid_out = 0
        self._layout(self._fit_columns())

    def _layout(self, ncol: int) -> None:
        for w in self.winfo_children():
            w.destroy()
        self._cells.clear()
        ncol = max(1, min(ncol, self.ncol_max))
        self.ncol = self._laid_out = ncol
        for i in range(ncol):
            self.columnconfigure(i * 2, weight=0)
            self.columnconfigure(i * 2 + 1, weight=1)
        for i, (label, value, tone) in enumerate(self._items):
            r, c = divmod(i, ncol)
            lb = U.wlabel(self, label, "mute", U.FONT_S, anchor="w",
                          width=max(6, self.label_w // 8))
            lb.grid(row=r, column=c * 2, sticky="w",
                    padx=(0, U.px(10)), pady=U.px(5))
            role = {"ok": "ok", "err": "pageerr", "warn": "warn",
                    "brand": "brand"}.get(tone or "", "card")
            vl = U.wlabel(self, str(value), role, U.FONT_S, anchor="w")
            vl.grid(row=r, column=c * 2 + 1, sticky="w",
                    padx=(0, U.px(24)), pady=U.px(5))
            self._cells.append((lb, vl))


class Segmented(tk.Frame):
    """DSM 风格的分段切换（列表 / 详情 / 原始输出）。"""

    def __init__(self, master, items: list[tuple], command=None,
                 bg_role: str = "card"):
        super().__init__(master, bg=U.T.card, bd=0, highlightthickness=1,
                         highlightbackground=U.T.border)
        self.command = command
        self.items = items                 # [(key, label)]
        self.current = items[0][0] if items else ""
        self._btns: dict[str, tk.Label] = {}
        for i, (key, label) in enumerate(items):
            lb = tk.Label(self, text=label, font=U.FONT_S, bd=0,
                          padx=U.px(14), pady=U.px(5), cursor="hand2")
            lb.pack(side="left")
            lb.bind("<Button-1>", lambda e, k=key: self.select(k))
            self._btns[key] = lb
        U.register(self, self._paint)
        self._paint()

    def select(self, key: str, fire: bool = True) -> None:
        self.current = key
        self._paint()
        if fire and self.command:
            self.command(key)

    def _paint(self):
        self.configure(bg=U.T.alt, highlightbackground=U.T.border)
        for key, lb in self._btns.items():
            on = key == self.current
            lb.configure(bg=U.T.card if on else U.T.alt,
                         fg=U.T.text if on else U.T.text_dim)
