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

import inspect
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


# 光标守卫的 bindtag 名。为什么要自己插一个 tag，见 _install_cursor_guard。
CUR_TAG = "TblCursorGuard"


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
        # ---- 分组折叠（默认关闭，只有显式 set_group() 才开）
        # 为什么做成开关而不是默认：模型列表按「分组名称」折叠是对的
        # （对齐 Cherry Studio），但防火墙规则、进程表折叠没有任何意义，
        # 强行给所有表加一层树只会让它们多出一条没用的折叠列。
        self._group_key: str | None = None
        self._group_empty = "未分组"
        self._group_open_default = True
        self._open: dict[str, bool] = {}          # 组名 → 展开状态（重建后要还原）
        self._gid_name: dict[str, str] = {}       # 组节点 iid → 组名
        self._groups_seen: list[str] = []         # 上回渲染出现的组（全展开/全收起用）
        self._iid_row: dict[str, dict] = {}       # iid → 行（分组后 iid 不再是序号）
        self._row_iid: dict[int, str] = {}        # _view 下标 → iid
        self._g0_w = U.px(168)                    # 分组列（#0）宽度
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
        # 用户点三角展开/收起时记下来 —— 表格一 rebuild（切供应商、
        # 增删一条模型）就整棵重建，不记的话刚收起的组会自己弹开。
        self.tree.bind("<<TreeviewOpen>>", self._remember_open, add="+")
        self.tree.bind("<<TreeviewClose>>", self._remember_open, add="+")
        # 面板拉伸 / 分栏拖动后空态要跟着重新居中，列宽也要重新分配余量
        self.tree.bind("<Configure>", self._on_tree_configure, add="+")
        self.tree.bind("<Configure>", lambda _e: self._place_empty(), add="+")
        # 松手才算「用户设定的宽度」：拖动过程中读到的中间值不能记
        self.tree.bind("<ButtonRelease-1>", self._remember_widths, add="+")
        # 列分隔线拖完要把光标还回箭头：ttk 自己在 <ButtonRelease-1> 里恢复
        # cursor，但松手那一瞬指针往往已经移出 Treeview（或者表格当场 rebuild
        # 换了控件），release 落不到原来那棵树上 —— 光标就一直卡在 ↔。
        # 三个地方兜底：松手、移动时发现左键早松了、指针离开表格。
        self.tree.bind("<ButtonRelease-1>", self._reset_cursor, add="+")
        self.tree.bind("<Leave>", self._reset_cursor, add="+")
        try:
            self.winfo_toplevel().bind("<ButtonRelease-1>",
                                       self._reset_cursor, add="+")
        except tk.TclError:
            pass
        self._install_cursor_guard()
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
        # 分组模式下最左边那列（#0）是实打实占宽的，不算进来的话
        # 一组模型就会平白多出一条横向滚动条。
        if self._group_key:
            try:
                avail -= int(self.tree.column("#0", "width")) + U.px(4)
            except tk.TclError:
                pass
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

    # ------------------------------------------------------------ 光标
    def _reset_cursor(self, _e=None) -> None:
        """把表格光标还原成普通箭头。

        注意设的是 `"arrow"` 而不是 `""`：ttk::treeview 的 <Motion> 里有一句
        `ttk::saveCursor $w State(userConfCursor) [ttk::cursor hresize]`，
        它会把「当前 cursor 值」记下来当成后续的默认值。留空的话等于把
        决定权全交给 ttk；显式写 arrow，ttk 就不会去猜。
        """
        try:
            if str(self.tree.cget("cursor")) != "arrow":
                self.tree.configure(cursor="arrow")
        except tk.TclError:
            pass

    def _install_cursor_guard(self) -> None:
        """往 bindtags 里插一个 tag，让光标纠正排在 ttk 的类绑定**之后**。

        为什么不能挂在控件上：bindtags 顺序是 widget → class → toplevel → all。
        ttk::treeview 的 <Motion> 是**类绑定**，永远在我们后面跑，所以
        `bind("<Motion>", ...)` 里怎么改都会被它当场改回去。

        为什么不能用 after_idle：idle 要等下一轮事件循环，而鼠标移动每秒几十
        次 —— 每次移动 ttk 先设一次 ↔，idle 才清掉，用户在这中间看到的就是
        「鼠标移到哪都是双箭头」。v1.0.12 就是这么修的，没修干净。

        插进 bindtags 之后，`_guard_cursor` 与 ttk 在**同一个事件**里先后执行，
        屏幕只渲染最终值，中间态不可见。
        """
        try:
            if str(self.tree.cget("cursor")) != "arrow":
                self.tree.configure(cursor="arrow")   # 先给一个明确的底色
            tags = list(self.tree.bindtags())
            if CUR_TAG in tags:
                return
            idx = tags.index("Treeview") + 1 if "Treeview" in tags else 1
            tags.insert(idx, CUR_TAG)
            self.tree.bindtags(tuple(tags))
            self.tree.bind_class(CUR_TAG, "<Motion>", self._guard_cursor)
            self.tree.bind_class(CUR_TAG, "<Leave>", self._guard_cursor)
        except tk.TclError:
            pass

    def _guard_cursor(self, event=None) -> None:
        """ttk 刚设完光标，这里按真实区域改一次（同一事件内，用户看不到）。

        · 列分隔线 -> ↔，提示这里能拖动列宽
        · 其它任何地方（表头、数据行、下方空白）-> 普通箭头
        · 左键按着（正在拖列宽）-> 不插手，ttk 保持 ↔ 是对的
        """
        if event is not None:
            try:
                if int(getattr(event, "state", 0)) & 0x100:     # Button1 按着
                    return
            except (TypeError, ValueError):
                pass
        want = "arrow"
        try:
            leaving = str(getattr(event, "type", "")) == "Leave"
            if not leaving:
                region = str(self.tree.identify_region(int(event.x),
                                                       int(event.y)))
                if region == "separator":
                    want = "sb_h_double_arrow"
            if str(self.tree.cget("cursor")) != want:
                self.tree.configure(cursor=want)
        except (tk.TclError, AttributeError):
            pass

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
            # 组头：比正文淡一档、加粗，看起来才像「标题」而不是一条数据行
            self.tree.tag_configure("grp", background=U.T.alt,
                                    foreground=U.T.text_dim,
                                    font=(U.FONT_FAMILY, 9, "bold"))
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
        self._apply_show()
        self._apply_columns()
        self._render()

    def set_group(self, key: str | None, empty: str = "未分组",
                  expanded: bool = True) -> None:
        """按某个字段把行折叠成组；`key=None` 关掉，回到平表。

        组头就是 Treeview 的父节点，所以折叠/展开是 Tk 自带的，不用自己画。
        代价是 `show="headings"` 得改成 `show="tree headings"`：最左边多出
        一列 `#0` 专门放组名与缩进，被分组的那一列本身则从 displaycolumns 里
        摘掉（组名已经在左边了，再重复一遍纯属占地方）。
        """
        self._group_key = key or None
        self._group_empty = empty or "未分组"
        self._group_open_default = bool(expanded)
        self._apply_show()
        self._render()

    def is_grouped(self) -> bool:
        return bool(self._group_key)

    def groups(self) -> list[str]:
        """上回渲染出现过的组名（顺序 = 显示顺序）。"""
        return list(self._groups_seen)

    def set_all_open(self, open_: bool) -> None:
        """全部展开 / 全部收起。记住整个组的名字，不只当前可见的那批。"""
        for g in set(self._groups_seen) | set(self._open):
            self._open[g] = bool(open_)
        self._group_open_default = bool(open_)
        self._render()

    def _apply_show(self) -> None:
        keys = [c.key for c in self.columns]
        try:
            if self._group_key:
                self.tree.configure(show="tree headings",
                                    displaycolumns=[k for k in keys
                                                    if k != self._group_key])
                self.tree.heading("#0", text="分组")
                self.tree.column("#0", width=self._g0_w,
                                 minwidth=U.px(96), stretch=False)
            else:
                self.tree.configure(show="headings", displaycolumns=keys)
        except tk.TclError:
            pass

    def _remember_open(self, _e=None) -> None:
        if not self._group_key:
            return
        try:
            kids = self.tree.get_children("")
        except tk.TclError:
            return
        for gid in kids:
            name = self._gid_name.get(gid)
            if name is None:
                continue
            try:
                self._open[name] = bool(self.tree.item(gid, "open"))
            except tk.TclError:
                continue

    def column_at(self, x: int) -> str:
        """鼠标横坐标落在哪一列（返回列 key，分组列返回 "#0"）。

        `identify_column` 只给 "#8" 这种显示序号，序号会随分组开关变化，
        所以统一换成列 key —— 调用方按 key 判断才不会被分组与否带偏。
        """
        try:
            cid = str(self.tree.identify_column(int(x)))
        except (tk.TclError, TypeError, ValueError):
            return ""
        if not cid.startswith("#"):
            return ""
        try:
            n = int(cid[1:])
        except ValueError:
            return ""
        if n == 0:
            return "#0"
        keys = [c.key for c in self.columns
                if not self._group_key or c.key != self._group_key]
        return keys[n - 1] if 0 < n <= len(keys) else ""

    def row_at(self, y: int) -> dict | None:
        """鼠标纵坐标落在哪一行（组头返回 None）。"""
        try:
            iid = self.tree.identify_row(int(y))
        except (tk.TclError, TypeError, ValueError):
            return None
        return self._iid_row.get(iid) if iid else None

    def select_index(self, idx: int) -> dict | None:
        """按 `_view` 下标选中一行。分组后 iid 不是序号，所以别再用 iid 猜。"""
        iid = self._row_iid.get(idx)
        if not iid:
            return None
        try:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)
        except tk.TclError:
            return None
        return self._view[idx] if 0 <= idx < len(self._view) else None

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
        # 分组后 iid 是 "r12"/"g3" 这种，不再是 _view 下标 —— 一律查表。
        # 组头行不进 _iid_row，所以点组头取到的就是 None（它本来也没有行数据）。
        return self._iid_row.get(sel[0])

    def select_first(self) -> None:
        self.select_index(0)

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
            if self.select_index(i) is None:
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

        if self._group_key:
            # 分组时排序必须**只组内排** —— 否则一排序组就被拆散，
            # 同一组的模型散落到列表各处，分组等于白做。
            def grp(r):
                return str(r.get(self._group_key) or self._group_empty).lower()
            self._view.sort(key=lambda r: (grp(r), num(r.get(key, ""))),
                            reverse=self._sort_desc)
            return
        self._view.sort(key=lambda r: num(r.get(key, "")), reverse=self._sort_desc)

    def _render(self) -> None:
        if self._group_key:
            self._render_grouped()
            return
        try:
            self.tree.delete(*self.tree.get_children())
        except tk.TclError:
            return
        self._iid_row.clear()
        self._row_iid.clear()
        self._gid_name.clear()
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
                iid = str(i)
                self.tree.insert("", "end", iid=iid, values=vals, tags=tuple(tags))
            except tk.TclError:
                continue
            self._iid_row[iid] = row
            self._row_iid[i] = iid
        self._render_tail("")

    def _render_grouped(self) -> None:
        """按 `_group_key` 折叠：一条组头（父节点）+ 若干模型行（子节点）。

        手风琴是 Tk 自带的（父节点三角），不自己画；展开状态存在 `_open` 里，
        rebuild 之后还原 —— 否则「收起一组 → 新增一个模型 → 组又弹开了」。
        """
        try:
            self.tree.delete(*self.tree.get_children(""))
        except tk.TclError:
            return
        self._iid_row.clear()
        self._row_iid.clear()
        self._gid_name.clear()
        shown = self._view[:MAX_ROWS]
        order: list[str] = []
        buckets: dict[str, list[tuple[int, dict]]] = {}
        for i, row in enumerate(shown):
            g = str(row.get(self._group_key) or "").strip() or self._group_empty
            if g not in buckets:
                buckets[g] = []
                order.append(g)
            buckets[g].append((i, row))
        for gi, g in enumerate(order):
            gid = "g%d" % gi
            is_open = self._open.get(g, self._group_open_default)
            try:
                self.tree.insert(
                    "", "end", iid=gid, text=f"{g}　({len(buckets[g])})",
                    values=[""] * len(self.columns), tags=("grp",), open=is_open)
            except tk.TclError:
                continue
            self._gid_name[gid] = g
            for i, row in buckets[g]:
                vals = [c.text(row) for c in self.columns]
                tags = ["even" if i % 2 else "odd"]
                tone = row.get("_tone")
                if tone in _TONE_FG:
                    tags.append("t_" + tone)
                if row.get("_strike"):
                    tags.append("t_mute")
                rid = "r%d" % i
                try:
                    self.tree.insert(gid, "end", iid=rid, values=vals,
                                     tags=tuple(tags))
                except tk.TclError:
                    continue
                self._iid_row[rid] = row
                self._row_iid[i] = rid
        self._groups_seen = list(order)
        self._render_tail(f"　·　{len(order)} 个分组" if order else "")

    def _render_tail(self, note: str) -> None:
        """行插完之后共用的收尾：计数、空态、表头排序标记。"""
        if len(self._view) > MAX_ROWS:
            self.lb_count.configure(
                text=f"{len(self._view)} 项（列表只渲染前 {MAX_ROWS} 条，用搜索缩小）")
        else:
            self.lb_count.configure(
                text=(f"{len(self._view)} 项{note}" if self._view else ""))
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

    @staticmethod
    def _takes_row(cb) -> bool:
        """右键回调要不要行数据。

        项目里两种写法并存：`lambda r: ...` 和 `lambda: self.xxx(self._pick())`。
        以前一律按 `cb(row)` 调，后者直接 TypeError —— 而 Tk 把回调异常
        打进 stderr 就完了，界面上表现就是「右键点编辑，什么都不发生」。
        """
        try:
            sig = inspect.signature(cb)
        except (TypeError, ValueError):
            return False
        for p in sig.parameters.values():
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD,
                          p.VAR_POSITIONAL):
                return True
        return False

    def _popup(self, event):
        if not self._menu_items:
            return
        # 先让「指针下那一行」成为选中行：右键菜单本来就该作用在手指着的那条上。
        # 以前读的是上一次的选中项，右键没选中的行 → None 或者别人的数据。
        try:
            iid = self.tree.identify_row(int(event.y))
        except (tk.TclError, TypeError, ValueError):
            iid = ""
        if iid and iid in self._iid_row:
            try:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
            except tk.TclError:
                pass
        row = self.selected()
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
            if cb is None:
                cmd = None
            elif self._takes_row(cb):
                cmd = lambda c=cb: c(row)          # noqa: E731
            else:
                cmd = lambda c=cb: c()             # noqa: E731
            self._menu.add_command(label=label, command=cmd,
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
        """items: [(label, value, tone|None[, (按钮文字, 回调)])]，整体重建。

        第 4 项必须原样保留 —— `_layout` 靠它画行尾按钮（令牌「复制」就是这么挂的），
        早年这里只截到 3 项，按钮传进来会被静默吃掉。
        """
        self._items = [
            (i[0], i[1], i[2] if len(i) > 2 else None,
             i[3] if len(i) > 3 else None)
            for i in items
        ]
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
        for i, item in enumerate(self._items):
            label, value = item[0], item[1]
            tone = item[2] if len(item) > 2 else None
            # 第 4 项可选：(按钮文字, 回调)。给「看得见但不该露」的值配一个
            # 复制按钮 —— 比如 API 令牌，屏幕上只有星号，要用就点一下复制。
            action = item[3] if len(item) > 3 else None
            r, c = divmod(i, ncol)
            lb = U.wlabel(self, label, "mute", U.FONT_S, anchor="w",
                          width=max(6, self.label_w // 8))
            lb.grid(row=r, column=c * 2, sticky="w",
                    padx=(0, U.px(10)), pady=U.px(5))
            role = {"ok": "ok", "err": "pageerr", "warn": "warn",
                    "brand": "brand"}.get(tone or "", "card")
            cell = tk.Frame(self, bg=U.T.card, bd=0)
            cell.grid(row=r, column=c * 2 + 1, sticky="w",
                      padx=(0, U.px(24)), pady=U.px(5))
            vl = U.wlabel(cell, str(value), role, U.FONT_S, anchor="w")
            vl.pack(side="left")
            if action:
                try:
                    U.RoundButton(cell, action[0], action[1], variant="ghost",
                                  font=U.FONT_XS, height=22, padx=8
                                  ).pack(side="left", padx=(U.px(6), 0))
                except Exception:  # noqa: BLE001  按钮挂不上不该拖垮整块信息
                    pass
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
