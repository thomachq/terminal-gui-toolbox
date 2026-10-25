# -*- coding: utf-8 -*-
"""
WorkBuddy 风格 tkinter 设计系统。

只负责「看起来像」，不含任何业务逻辑：
  · 设计令牌取自本机 WorkBuddy 皮肤 skin.css 的真实取值
  · 圆角按钮 / 卡片 / 侧边导航 / 状态胶囊 / 文本块
  · 浅色（默认）与深色两套配色，可在运行时切换

用法：
    import wbtui
    wbtui.configure_ttk(root)          # 先把 ttk 样式刷成 WorkBuddy 配色
    card = wbtui.Card(parent, title="SSH 连接")
    wbtui.RoundButton(card.body, "连接并探测", command=fn, variant="primary")
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk


# ---------------------------------------------------------------- HiDPI
def _enable_dpi_awareness() -> float:
    """声明 DPI 感知，并返回缩放系数（96 DPI = 1.0）。

    必须在创建 Tk 窗口之前调用，否则 Windows 会把窗口位图拉伸，
    在 125%/150% 缩放下文字会发虚。
    """
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor v2
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:  # noqa: BLE001
                return 1.0
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi and dpi > 0:
            return max(1.0, dpi / 96.0)
    except Exception:  # noqa: BLE001
        pass
    return 1.0


S: float = _enable_dpi_awareness()


def px(n: float) -> int:
    """把 96 DPI 下的设计像素换算成当前缩放下的真实像素。"""
    return int(round(n * S))


def elide(text: str, font, max_px: int, mode: str = "middle") -> str:
    """把 text 压进 max_px 像素以内，超出部分用 … 顶替。

    Tk 的 Label 既不换行也不省略 —— 宽度不够时直接从右边切掉，屏幕上就是
    「本机 · C:\\Users\\Thomas · 178」这种断在半截的句子，看着像程序坏了。
    这里在**写入文字之前**先按实测宽度缩短，至少让省略是看得见的。

    mode="middle" 保留头尾（路径类信息头尾都有用），"tail" 只保留开头。
    """
    text = str(text)
    if max_px <= 4 or not text:
        return "" if max_px <= 4 else text
    try:
        f = tkfont.Font(font=font)
    except Exception:  # noqa: BLE001  字体量不出来就别动原文
        return text
    if f.measure(text) <= max_px:
        return text
    dot = "\u2026"
    dw = f.measure(dot)
    if dw > max_px:
        return ""
    if mode == "tail":
        lo, hi, best = 0, len(text), dot
        while lo <= hi:
            mid = (lo + hi) // 2
            if f.measure(text[:mid]) + dw <= max_px:
                best = text[:mid] + dot
                lo = mid + 1
            else:
                hi = mid - 1
        return best
    lo, hi, best = 0, len(text), dot
    while lo <= hi:
        keep = (lo + hi) // 2
        head = (keep + 1) // 2
        tail = keep - head
        cand = text[:head] + dot + (text[-tail:] if tail else "")
        if f.measure(cand) <= max_px:
            best = cand
            lo = keep + 1
        else:
            hi = keep - 1
    return best

# ---------------------------------------------------------------- 字体
FONT_FAMILY = "Microsoft YaHei UI"
FONT = (FONT_FAMILY, 10)
FONT_S = (FONT_FAMILY, 9)
FONT_XS = (FONT_FAMILY, 8)
FONT_B = (FONT_FAMILY, 10, "bold")
FONT_T = (FONT_FAMILY, 11, "bold")      # 卡片标题
FONT_H = (FONT_FAMILY, 16, "bold")      # 应用标题
MONO = ("Consolas", 9)
MONO_S = ("Consolas", 8)


# ---------------------------------------------------------------- 配色
class Palette:
    def __init__(self, name: str, **kw):
        self.name = name
        self.__dict__.update(kw)

    def __repr__(self) -> str:
        return f"<Palette {self.name}>"


# 浅色：直接采用 WorkBuddy「clear-blue / 柔和蓝」皮肤的值
LIGHT = Palette(
    "light",
    page="#F5F9FF",          # --cb-bg-primary
    card="#FFFFFF",          # --cb-panel-bg-primary
    alt="#F4F8FE",
    border="#DCE8FF",        # --cb-main-area-border-color
    border2="#C9DCF7",
    brand="#3A66AC",         # --cb-button-primary
    brand_deep="#22406E",
    brand_hover="#4E79BE",
    brand_soft="#E6EFFF",    # --cb-bg-secondary
    brand_soft2="#DCEAFF",
    text="#001F4F",          # --cb-text-primary
    text_dim="#5B7093",
    text_mute="#8A9BB5",
    ok="#12855C", ok_soft="#E4F5EC",
    warn="#9A7412", warn_soft="#FCF3D7",
    err="#D93B34", err_soft="#FDECEB",
    input_bg="#FFFFFF", input_border="#DCE8FF",
    console_bg="#0E1A2B", console_fg="#D7E3F4",
    console_cmd="#7DD3FC", console_ok="#86EFAC",
    console_bad="#FCA5A5", console_meta="#8FA3BF",
    shadow="#E8F0FE",
)

# 深色：同一套相对关系，深色底重新取的等效值
DARK = Palette(
    "dark",
    page="#0F1729", card="#16203A", alt="#1B2742",
    border="#243354", border2="#2F4068",
    brand="#5B8BD6", brand_deep="#BFD4F5", brand_hover="#6E9BE0",
    brand_soft="#1E2C4C", brand_soft2="#26375C",
    text="#E8EFFB", text_dim="#9FB0CC", text_mute="#7186A6",
    ok="#4ADE80", ok_soft="#14301F",
    warn="#FBBF24", warn_soft="#33280B",
    err="#F87171", err_soft="#3A1616",
    input_bg="#111B2F", input_border="#2A3A5C",
    console_bg="#0A1020", console_fg="#D7E3F4",
    console_cmd="#7DD3FC", console_ok="#86EFAC",
    console_bad="#FCA5A5", console_meta="#8FA3BF",
    shadow="#0B1220",
)

T: Palette = LIGHT

# ---------------------------------------------------------------- 主题注册
_themables: list = []          # [(widget, callback)]


def register(widget: tk.Misc, callback) -> None:
    _themables.append((widget, callback))


def _prune() -> None:
    alive = []
    for w, cb in _themables:
        try:
            if w.winfo_exists():
                alive.append((w, cb))
        except tk.TclError:
            pass
    _themables[:] = alive


def apply_theme(name_or_palette) -> None:
    """切换主题：重刷 ttk 样式 + 通知所有自绘控件重绘。"""
    global T
    if isinstance(name_or_palette, Palette):
        T = name_or_palette
    else:
        T = DARK if str(name_or_palette).lower() == "dark" else LIGHT
    _prune()
    if _root is not None:
        configure_ttk(_root)
    for _, cb in list(_themables):
        try:
            cb()
        except tk.TclError:
            pass


_root: tk.Misc | None = None


def toggle_theme() -> None:
    apply_theme(DARK if T.name == "light" else LIGHT)


# ---------------------------------------------------------------- ttk 样式
def configure_ttk(root: tk.Misc) -> None:
    global _root
    _root = root
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    try:
        # 1 磅 = dpi/72 像素，保证字号与 px() 换算一致
        root.tk.call("tk", "scaling", S * 96.0 / 72.0)
    except tk.TclError:
        pass
    try:
        root.configure(bg=T.page)
    except tk.TclError:
        pass

    st.configure(".", font=FONT, background=T.page, foreground=T.text)
    st.configure("TFrame", background=T.page, borderwidth=0)
    st.configure("TLabel", background=T.page, foreground=T.text)

    # 输入框：白底、1px 描边、获焦变主色
    for name in ("TEntry", "Card.TEntry"):
        bg = T.input_bg
        st.configure(name, fieldbackground=bg, background=bg, foreground=T.text,
                     bordercolor=T.input_border, lightcolor=T.input_border,
                     darkcolor=T.input_border, insertcolor=T.text,
                     padding=(px(8), px(5)), borderwidth=1, relief="flat")
        st.map(name,
               bordercolor=[("focus", T.brand)],
               lightcolor=[("focus", T.brand)],
               darkcolor=[("focus", T.brand)],
               fieldbackground=[("disabled", T.alt)],
               foreground=[("disabled", T.text_mute)])

    st.configure("TCombobox", fieldbackground=T.input_bg, background=T.input_bg,
                 foreground=T.text, bordercolor=T.input_border,
                 lightcolor=T.input_border, darkcolor=T.input_border,
                 arrowcolor=T.text_dim, padding=(px(7), px(4)), borderwidth=1, relief="flat")
    st.map("TCombobox",
           bordercolor=[("focus", T.brand)],
           lightcolor=[("focus", T.brand)],
           darkcolor=[("focus", T.brand)],
           fieldbackground=[("readonly", T.input_bg)],
           foreground=[("readonly", T.text)],
           selectbackground=[("readonly", T.input_bg)],
           selectforeground=[("readonly", T.text)])
    try:
        root.option_add("*TCombobox*Listbox.background", T.card)
        root.option_add("*TCombobox*Listbox.foreground", T.text)
        root.option_add("*TCombobox*Listbox.selectBackground", T.brand_soft)
        root.option_add("*TCombobox*Listbox.selectForeground", T.brand_deep)
        root.option_add("*TCombobox*Listbox.font", FONT)
    except tk.TclError:
        pass

    # clam 的指示器用的是 indicatorbackground / indicatorforeground，
    # 用 indicatorcolor 是不生效的（深色主题下会露出白底）。
    for name, bg in (("TCheckbutton", T.page), ("Card.TCheckbutton", T.card),
                     ("TRadiobutton", T.page), ("Card.TRadiobutton", T.card)):
        st.configure(name, background=bg, foreground=T.text, font=FONT_S,
                     focuscolor=bg,
                     indicatorbackground=T.input_bg,
                     indicatorforeground=T.brand,
                     upperbordercolor=T.border2, lowerbordercolor=T.border2,
                     indicatormargin=px(3), padding=(px(2), px(2)))
        st.map(name,
               background=[("active", bg)],
               foreground=[("disabled", T.text_mute)],
               indicatorbackground=[("selected", T.brand), ("active", T.brand_soft2)],
               indicatorforeground=[("selected", "#FFFFFF")],
               upperbordercolor=[("selected", T.brand)],
               lowerbordercolor=[("selected", T.brand)])

    st.configure("Treeview", background=T.card, fieldbackground=T.card,
                 foreground=T.text, borderwidth=0, rowheight=px(27), font=FONT_S)
    st.map("Treeview", background=[("selected", T.brand_soft)],
           foreground=[("selected", T.brand_deep)])
    st.configure("Treeview.Heading", background=T.alt, foreground=T.text_dim,
                 font=(FONT_FAMILY, 9, "bold"), borderwidth=0, relief="flat",
                 padding=(px(6), px(7)))
    st.map("Treeview.Heading", background=[("active", T.brand_soft2)])
    try:
        st.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    except tk.TclError:
        pass

    st.configure("TScrollbar", background=T.border, troughcolor=T.page,
                 bordercolor=T.page, arrowcolor=T.text_mute, width=px(11),
                 relief="flat", borderwidth=0)
    st.map("TScrollbar", background=[("active", T.border2)])
    st.configure("Card.Vertical.TScrollbar", background=T.border,
                 troughcolor=T.card, bordercolor=T.card,
                 arrowcolor=T.text_mute, width=px(11), relief="flat", borderwidth=0)

    st.configure("TNotebook", background=T.page, borderwidth=0)
    st.configure("TNotebook.Tab", background=T.page, foreground=T.text_dim,
                 padding=(px(16), px(8)), borderwidth=0)
    st.map("TNotebook.Tab", background=[("selected", T.card)],
           foreground=[("selected", T.text)])
    st.configure("TSeparator", background=T.border)


# ---------------------------------------------------------------- 基础工具
def _parent_bg(master: tk.Misc) -> str:
    try:
        return str(master.cget("bg"))
    except tk.TclError:
        pass
    try:
        return str(master.cget("background"))
    except tk.TclError:
        return T.page


_ROLE = {
    "page": ("page", "text"),
    "card": ("card", "text"),
    "alt": ("alt", "text"),
    "dim": ("card", "text_dim"),
    "pagedim": ("page", "text_dim"),
    "mute": ("card", "text_mute"),
    "pagemute": ("page", "text_mute"),
    "err": ("card", "err"),
    "pageerr": ("page", "err"),
    "ok": ("card", "ok"),
    "pageok": ("page", "ok"),
    "warn": ("card", "warn"),
    "brand": ("card", "brand"),
    "pagebrand": ("page", "brand"),
}


def role_colors(role: str) -> tuple[str, str]:
    bgkey, fgkey = _ROLE.get(role, ("card", "text"))
    return getattr(T, bgkey), getattr(T, fgkey)


def wframe(master: tk.Misc, role: str = "page", **kw) -> tk.Frame:
    bg, _ = role_colors(role)
    f = tk.Frame(master, bg=bg, bd=0, **kw)
    register(f, lambda: f.configure(bg=role_colors(role)[0]))
    return f


def wlabel(master: tk.Misc, text: str = "", role: str = "card",
           font=FONT, anchor: str = "w", **kw) -> tk.Label:
    bg, fg = role_colors(role)
    lb = tk.Label(master, text=text, bg=bg, fg=fg, font=font, anchor=anchor, bd=0, **kw)

    def _paint():
        b, f2 = role_colors(role)
        lb.configure(bg=b, fg=f2)

    register(lb, _paint)
    return lb


def rect_points(x1, y1, x2, y2, r) -> list:
    """12 点圆角矩形（配合 smooth=True）。"""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def round_rect(cv: tk.Canvas, x1, y1, x2, y2, r, **kw):
    return cv.create_polygon(rect_points(x1, y1, x2, y2, r), smooth=True, **kw)


# ---------------------------------------------------------------- 圆角按钮
class RoundButton(tk.Canvas):
    """WorkBuddy 风格的圆角按钮。

    variant: primary / secondary / ghost / danger / soft
    """

    def __init__(self, master, text: str, command=None, variant: str = "secondary",
                 height: int = 34, radius: int = 9, font=FONT, padx: int = 16,
                 width: int | None = None, bg: str | None = None):
        pbg = bg if bg is not None else _parent_bg(master)
        height = px(height)
        radius = px(radius)
        padx = px(padx)
        if width:
            width = px(width)
        self._pbg = pbg
        self.text = text
        self.command = command
        self.variant = variant
        self.radius = radius
        self.font = font
        self.padx = padx
        self.height = height
        self._enabled = True
        self._hover = False
        f = tkfont.Font(font=font)
        w = width if width else f.measure(text) + padx * 2
        self._btnw = max(w, height)
        super().__init__(master, bg=pbg, highlightthickness=0, bd=0,
                         width=self._btnw, height=height, takefocus=0)
        self._bg = round_rect(self, 1, 1, self._btnw - 1, height - 1, radius,
                              fill=T.card, outline="")
        self._tx = self.create_text(self._btnw / 2, height / 2, text=text, font=font)
        self._paint()
        self.configure(cursor="hand2")
        # 只绑画布本身。canvas 的 item 绑定和 widget 绑定会各跑一遍，
        # 两套都绑就等于一次点击触发两次 command——「启动服务」起来马上停
        # 就是被这么点了两下（start → stop）。
        self._pressed = False
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._click)
        self.bind("<ButtonRelease-1>", self._release)
        register(self, self._paint)

    # -- 颜色
    def _colors(self):
        v = self.variant
        if not self._enabled:
            return T.alt, T.text_mute, T.border
        if v == "primary":
            return (T.brand_hover if self._hover else T.brand), "#FFFFFF", None
        if v == "secondary":
            return (T.brand_soft if self._hover else T.card), T.text, T.border2
        if v == "ghost":
            return (T.brand_soft if self._hover else self._pbg), T.brand, None
        if v == "danger":
            return (T.err_soft if self._hover else T.card), T.err, T.border2
        if v == "soft":
            return (T.brand_soft2 if self._hover else T.brand_soft), T.brand_deep, None
        return T.card, T.text, T.border

    def _paint(self):
        fill, fg, outline = self._colors()
        try:
            self.configure(bg=self._pbg)
            self.itemconfigure(self._bg, fill=fill, outline=outline or fill)
            self.itemconfigure(self._tx, fill=fg)
        except tk.TclError:
            pass

    # -- 状态
    def set_text(self, text: str) -> None:
        self.text = text
        f = tkfont.Font(font=self.font)
        need = f.measure(text) + self.padx * 2
        if need > self._btnw:
            self._btnw = need
            self.configure(width=self._btnw)
            self.coords(self._bg, *self._rect_pts())
            self.coords(self._tx, self._btnw / 2, self.height / 2)
        self.itemconfigure(self._tx, text=text)

    def _rect_pts(self):
        return rect_points(1, 1, self._btnw - 1, self.height - 1, self.radius)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._paint()

    # 兼容旧调用
    def state(self, *a):
        pass

    # -- 事件
    def _enter(self, _=None):
        if self._enabled:
            self._hover = True
            self._paint()

    def _leave(self, _=None):
        self._hover = False
        self._paint()

    def _click(self, _=None):
        if not self._enabled:
            return
        self._pressed = True
        self._paint()

    def _release(self, _=None):
        # 只在画布上按下去过才算一次点击，避免从外面拖进来松手也触发
        if not getattr(self, "_pressed", False):
            return
        self._pressed = False
        if self._enabled and self.command:
            self._hover = True
            self._paint()
            self.command()


# ---------------------------------------------------------------- 卡片
class Card(tk.Frame):
    """白底 + 1px 描边的卡片，标题栏可选。"""

    def __init__(self, master, title: str | None = None, hint: str | None = None,
                 pad: int = 14, **kw):
        super().__init__(master, bg=T.card, highlightthickness=1,
                         highlightbackground=T.border, highlightcolor=T.border,
                         bd=0, **kw)
        self.inner = tk.Frame(self, bg=T.card, bd=0)
        self.inner.pack(fill="both", expand=True, padx=px(pad), pady=px(pad))
        self._title = None
        self._hint = None
        if title:
            head = tk.Frame(self.inner, bg=T.card, bd=0)
            head.pack(fill="x", pady=(0, px(10)))
            self._title = tk.Label(head, text=title, bg=T.card, fg=T.text,
                                   font=FONT_T, anchor="w", bd=0)
            self._title.pack(side="left")
            if hint:
                self._hint = tk.Label(head, text=hint, bg=T.card, fg=T.text_mute,
                                      font=FONT_S, anchor="e", bd=0)
                self._hint.pack(side="right")
        self.body = tk.Frame(self.inner, bg=T.card, bd=0)
        self.body.pack(fill="both", expand=True)
        register(self, self._paint)

    def _paint(self):
        self.configure(bg=T.card, highlightbackground=T.border, highlightcolor=T.border)
        for w in (self.inner, self.body):
            w.configure(bg=T.card)
        for lb in (self._title, self._hint):
            if lb is not None:
                lb.configure(bg=T.card)
        if self._title is not None:
            self._title.configure(fg=T.text)
        if self._hint is not None:
            self._hint.configure(fg=T.text_mute)


# ---------------------------------------------------------------- 文本块
class TextBox(tk.Frame):
    """带描边的多行文本块，可选只读。"""

    def __init__(self, master, role: str = "alt", font=MONO, height: int = 8,
                 readonly: bool = True, wrap: str = "word", padx: int = 10,
                 pady: int = 8):
        super().__init__(master, bg=T.card, bd=0, highlightthickness=1,
                         highlightbackground=T.border, highlightcolor=T.border)
        self._role = role
        self.txt = tk.Text(self, font=font, height=height, wrap=wrap, bd=0,
                           relief="flat", padx=px(padx), pady=px(pady),
                           highlightthickness=0,
                           insertbackground=T.text)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.txt.yview,
                           style="Card.Vertical.TScrollbar")
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._sb = sb
        if readonly:
            self.txt.bind("<Key>", self._readonly_key)
        register(self, self._paint)
        self._paint()

    def _readonly_key(self, event):
        # 允许复制、全选、翻页，禁止编辑
        if event.state & 0x4 and event.keysym.lower() in ("c", "a"):
            return None
        if event.keysym in ("Up", "Down", "Left", "Right", "Prior", "Next", "Home", "End"):
            return None
        return "break"

    def _paint(self):
        bg = getattr(T, "console_bg" if self._role == "console" else self._role)
        fg = T.console_fg if self._role == "console" else T.text
        self.configure(bg=bg, highlightbackground=T.border, highlightcolor=T.border)
        self.txt.configure(bg=bg, fg=fg, insertbackground=fg,
                           selectbackground=T.brand_soft, selectforeground=T.brand_deep)

    # 便捷代理
    def get(self, *a):
        return self.txt.get(*a)

    def set_text(self, s: str) -> None:
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", s)
        if str(self.txt.cget("state")) == "normal":
            pass

    def clear(self) -> None:
        self.txt.delete("1.0", "end")


# ---------------------------------------------------------------- 状态胶囊
class Pill(tk.Canvas):
    """小圆角状态标签，如「● NAS 未连接」。"""

    def __init__(self, master, text: str = "", tone: str = "mute",
                 height: int = 24, radius: int = 12, padx: int = 11, font=FONT_S):
        pbg = _parent_bg(master)
        height = px(height)
        radius = px(radius)
        padx = px(padx)
        self._pbg = pbg
        self.tone = tone
        self.font = font
        self.height = height
        self.radius = radius
        self.padx = padx
        self.text = ""
        f = tkfont.Font(font=font)
        w = f.measure(text) + padx * 2 + 12
        super().__init__(master, bg=pbg, highlightthickness=0, bd=0,
                         width=w, height=height, takefocus=0)
        self._bg = round_rect(self, 0, 0, w, height, radius, fill=T.alt, outline="")
        self._dot = self.create_oval(padx, height / 2 - 3, padx + 6, height / 2 + 3,
                                     outline="", fill=T.text_mute)
        self._tx = self.create_text(padx + 12, height / 2, text="", font=font, anchor="w")
        register(self, self._paint)
        self.set(text, tone)

    def _tone_colors(self):
        t = self.tone
        if t == "ok":
            return T.ok_soft, T.ok
        if t == "err":
            return T.err_soft, T.err
        if t == "warn":
            return T.warn_soft, T.warn
        if t == "brand":
            return T.brand_soft, T.brand
        return T.alt, T.text_dim

    def set(self, text: str, tone: str | None = None) -> None:
        if tone:
            self.tone = tone
        self.text = text
        f = tkfont.Font(font=self.font)
        w = f.measure(text) + self.padx * 2 + 12
        self.configure(width=w)
        self.coords(self._bg, *self._pts(w))
        self.itemconfigure(self._tx, text=text)
        self._paint()

    def _pts(self, w):
        return rect_points(0, 0, w, self.height, self.radius)

    def _paint(self):
        bg, fg = self._tone_colors()
        try:
            self.configure(bg=self._pbg)
            self.itemconfigure(self._bg, fill=bg, outline=bg)
            self.itemconfigure(self._dot, fill=fg)
            self.itemconfigure(self._tx, fill=fg)
        except tk.TclError:
            pass


# ---------------------------------------------------------------- 勾选框
class Check(tk.Frame):
    """自绘勾选框。

    clam 主题自带的勾选标记是个「叉」，容易被误读成关闭状态，
    所以这里自己画一个圆角方框 + 对勾。
    """

    def __init__(self, master, text: str = "", variable: tk.BooleanVar | None = None,
                 command=None, font=FONT_S, bg: str | None = None):
        pbg = bg if bg is not None else _parent_bg(master)
        super().__init__(master, bg=pbg, bd=0, cursor="hand2")
        self._pbg = pbg
        self.var = variable if variable is not None else tk.BooleanVar()
        self.command = command
        self.font = font
        self._hover = False
        self._size = px(18)
        self.box = tk.Canvas(self, width=self._size, height=self._size,
                             bg=pbg, highlightthickness=0, bd=0, takefocus=0)
        self.box.pack(side="left")
        self.lbl = tk.Label(self, text=text, bg=pbg, fg=T.text, font=font,
                            bd=0, anchor="w", padx=px(7))
        self.lbl.pack(side="left")
        for w in (self, self.box, self.lbl):
            w.bind("<Button-1>", self._toggle)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)
        try:
            self._vtrace = self.var.trace_add("write", lambda *a: self._draw())
        except tk.TclError:
            self._vtrace = None
        register(self, self._paint)
        self._draw()

    def destroy(self) -> None:
        # StringVar 归调用方持有，比这个控件活得久。trace 不摘掉，
        # 控件销毁后动一下变量就会抛 invalid command name。
        tid = getattr(self, "_vtrace", None)
        if tid:
            try:
                self.var.trace_remove("write", tid)
            except (tk.TclError, AttributeError):
                pass
            self._vtrace = None
        super().destroy()

    def _toggle(self, _=None):
        try:
            self.var.set(not self.var.get())
        except tk.TclError:
            return
        if self.command:
            self.command()

    def _enter(self, _=None):
        self._hover = True
        self._draw()

    def _leave(self, _=None):
        self._hover = False
        self._draw()

    def get(self) -> bool:
        try:
            return bool(self.var.get())
        except tk.TclError:
            return False

    def set(self, val: bool) -> None:
        self.var.set(bool(val))

    def set_text(self, text: str) -> None:
        self.lbl.configure(text=text)

    def _draw(self) -> None:
        c = self.box
        try:
            c.delete("all")
        except tk.TclError:
            return
        s = self._size
        on = self.get()
        if on:
            fill, outline = (T.brand_hover if self._hover else T.brand), None
        else:
            fill = T.brand_soft2 if self._hover else T.input_bg
            outline = T.brand if self._hover else T.border2
        round_rect(c, 1, 1, s - 1, s - 1, px(5), fill=fill, outline=outline or fill)
        if on:
            c.create_line(s * 0.26, s * 0.52, s * 0.44, s * 0.70, s * 0.76, s * 0.32,
                          fill="#FFFFFF", width=max(1, px(2)),
                          capstyle="round", joinstyle="round")

    def _paint(self) -> None:
        try:
            # 父容器换主题后底色会变，这里必须重新取一次；
            # 用构造时缓存的 _pbg 会让勾选框在深色下留一块白底。
            self._pbg = _parent_bg(self.master)
            self.configure(bg=self._pbg)
            self.box.configure(bg=self._pbg)
            self.lbl.configure(bg=self._pbg, fg=T.text)
        except tk.TclError:
            return
        self._draw()


# ---------------------------------------------------------------- 侧边导航
class _NavItem(tk.Frame):
    def __init__(self, master, label: str, command, indent: int = 0):
        super().__init__(master, bg=T.page, bd=0, cursor="hand2")
        self.label_text = label
        self.command = command
        self.active = False
        self._hover = False
        # 二级项往右缩一点：分组标题下的条目要能看出「它属于上面那组」
        self._indent = int(indent)
        self.accent = tk.Frame(self, bg=T.page, width=px(3), bd=0)
        self.accent.pack(side="left", fill="y")
        self.lbl = tk.Label(self, text=label, bg=T.page, fg=T.text_dim,
                            font=FONT, anchor="w", bd=0,
                            padx=px(13 + self._indent), pady=px(9))
        self.lbl.pack(side="left", fill="both", expand=True)
        for w in (self, self.lbl):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)
        register(self, self._paint)

    def _click(self, _=None):
        self.command()

    def _enter(self, _=None):
        self._hover = True
        self._paint()

    def _leave(self, _=None):
        self._hover = False
        self._paint()

    def set_active(self, on: bool) -> None:
        self.active = on
        self._paint()

    def _paint(self):
        if self.active:
            bg, fg, ac = T.brand_soft, T.brand_deep, T.brand
        elif self._hover:
            bg, fg, ac = T.brand_soft2, T.text, T.page
        else:
            bg, fg, ac = T.page, T.text_dim, T.page
        try:
            self.configure(bg=bg)
            self.lbl.configure(bg=bg, fg=fg, font=FONT_B if self.active else FONT)
            self.accent.configure(bg=ac)
        except tk.TclError:
            pass


class ScrollBox(tk.Frame):
    """竖向滚动容器：Canvas + 内嵌 Frame，滚轮按指针位置路由。

    Tk 没有现成的「滚动画布」。以前两处（助手侧栏、设置页）各写一份，
    都用 `<Enter>` 抢 `bind_all`、`<Leave>` 解绑 —— 结果指针一移到卡片上
    （卡片是 Canvas 的子控件，盖住了内胆）就收到 Leave，滚轮当场失效，
    表现为「在这个区域滚不动，得挪到卡片之间的缝里」。

    这里换一种判法：把滚轮绑在**本窗口的 toplevel** 上，事件来了从
    `event.widget` 沿着 master 链往上走，先碰到哪个滚动容器的内胆就归谁滚。
      · 不依赖 Enter/Leave，卡片怎么叠都行；
      · 不依赖 `winfo_containing`（那个走 WindowFromPoint，窗口被别的程序
        盖住就返回 None，结论会随桌面状态变）；
      · 遇到 Text / Listbox / Treeview 直接让开 —— 它们自己能滚。
    """

    def __init__(self, master, bg: str | None = None, **kw):
        bg = T.page if bg is None else bg
        super().__init__(master, bg=bg, bd=0, **kw)
        self.cv = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vs = tk.Scrollbar(self, orient="vertical", command=self.cv.yview,
                               width=px(10), bd=0, relief="flat")
        self.cv.configure(yscrollcommand=self.vs.set)
        # 先 pack 滚动条、再 pack 画布：画布带 expand，先 pack 会把整条宽度吃光，
        # 滚动条只能分到 1px（表现为「滚动条不见了，滚轮也压不到它」）。
        self.vs.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.cv, bg=bg, bd=0)
        # 标记：嵌套滚动容器时，从里往外找，先碰到谁就归谁滚
        self.inner._is_scroll_inner = True
        self._win = self.cv.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._grow)
        self.cv.bind("<Configure>", self._fit)
        self.vs.bind("<MouseWheel>", self._wheel)
        self._wheel_ids: list[str] = []
        self._bind_wheel()
        self.bind("<Destroy>", self._unbind_wheel, add="+")

    # ---------------------------------------------------------- 滚轮
    def _bind_wheel(self) -> None:
        try:
            top = self.winfo_toplevel()
        except tk.TclError:
            return
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                self._wheel_ids.append(top.bind(seq, self._wheel, add="+"))
            except tk.TclError:
                pass

    def _unbind_wheel(self, e=None) -> None:
        if e is not None and getattr(e, "widget", None) is not self:
            return
        try:
            top = self.winfo_toplevel()
        except tk.TclError:
            return
        for seq, fid in zip(("<MouseWheel>", "<Button-4>", "<Button-5>"),
                            self._wheel_ids):
            try:
                top.unbind(seq, fid)
            except tk.TclError:
                pass
        self._wheel_ids = []

    def _owns(self, widget) -> bool:
        node = widget
        while node is not None:
            if node is self.cv or node is self.vs:
                return True
            if isinstance(node, (tk.Text, tk.Listbox, ttk.Treeview)):
                return False
            if getattr(node, "_is_scroll_inner", False):
                return node is self.inner
            node = getattr(node, "master", None)
        return False

    def _grow(self, _e=None) -> None:
        try:
            self.cv.configure(scrollregion=self.cv.bbox("all") or (0, 0, 0, 0))
        except tk.TclError:
            pass

    def _fit(self, e) -> None:
        try:
            self.cv.itemconfigure(self._win, width=e.width)
        except tk.TclError:
            pass

    def _wheel(self, e):
        try:
            if not self._owns(getattr(e, "widget", None)):
                return None
        except tk.TclError:
            return None
        if getattr(e, "num", None) == 4:
            step = -3
        elif getattr(e, "num", None) == 5:
            step = 3
        else:
            step = -3 if getattr(e, "delta", 0) > 0 else 3
        try:
            self.cv.yview_scroll(step, "units")
        except tk.TclError:
            return None
        return "break"

    # ---------------------------------------------------------- 常用动作
    def to_end(self) -> None:
        self.update_idletasks()
        self._grow()
        self.cv.yview_moveto(1.0)

    def clear(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self.cv.yview_moveto(0.0)


class Nav(tk.Frame):
    """左侧导航。

    `items` 支持两种写法：
      ("key", "标题")                          —— 一级条目
      ("group", "分组标题", [("key", "标题")]) —— 带小标题的一组二级条目

    分组不是纯装饰：资产类（主机 / 凭证 / 脚本）和操作类（防火墙 / 数据库）
    混在一列里时，找东西全靠记顺序。分两组之后一眼能扫到。

    为什么套一层 Canvas：

      导航项是纵向堆叠的，窗口一拉矮（或者笔记本屏幕本来就矮），靠下的
      「日志管理」会被直接切掉，连点都点不到。这里把条目放进 Canvas，
      装不下时自动出竖向滚动条，装得下就把滚动条收起来 —— 常态下不该
      多一条灰条占地方。
    """

    def __init__(self, master, items, on_change, width: int = 180,
                 alias: dict | None = None):
        super().__init__(master, bg=T.page, bd=0, width=px(width))
        self.pack_propagate(False)
        self.on_change = on_change
        # 旧键 → 新键。--page fw / conn 这类历史参数与旧脚本都还要能用
        self.alias = dict(alias or {})
        self.items: dict[str, _NavItem] = {}
        self.current = None

        self._canvas = tk.Canvas(self, bg=T.page, bd=0, highlightthickness=0,
                                 yscrollincrement=px(20))
        self._sb = ttk.Scrollbar(self, orient="vertical",
                                 command=self._canvas.yview,
                                 style="Card.Vertical.TScrollbar")
        self._sb_visible = False
        self._canvas.configure(yscrollcommand=self._on_scroll_set)

        self._inner = tk.Frame(self._canvas, bg=T.page, bd=0)
        self._win = self._canvas.create_window((0, 0), window=self._inner,
                                               anchor="nw")
        self._canvas.pack(side="left", fill="both", expand=True)
        self._inner.bind("<Configure>", self._sync)
        self._canvas.bind("<Configure>", self._sync)
        self._bind_wheel(self._canvas)

        top = tk.Frame(self._inner, bg=T.page, bd=0, height=px(8))
        top.pack(fill="x")
        for it in items:
            if it and it[0] == "group":
                self._add_group(it[1], it[2])
            else:
                self._add_item(it[0], it[1], 0)
        register(self, lambda: self.configure(bg=T.page))
        register(self._canvas, lambda: self._canvas.configure(bg=T.page))
        register(self._inner, lambda: self._inner.configure(bg=T.page))
        self._fit_width(items, width)

    def _fit_width(self, items, base: int) -> None:
        """宽度按**最长标题**自适应，别写死一个数。

        固定 170 逻辑像素时「隧道客户端证书管理」差 7px 被 tk 直接裁字
        （Label 不换行也不省略）。以后再加长标题还会撞同一堵墙，所以这里
        按实际字体量一遍，取「默认宽度」和「量出来的需求」中的大者，
        上限 220 —— 侧栏不能无限吃主区的宽度。
        """
        try:
            f = tkfont.Font(font=FONT)
            need = 0
            for it in items:
                grouped = bool(it) and it[0] == "group"
                subs = it[2] if grouped else [it]
                ind = 11 if grouped else 0
                for pair in subs or []:
                    if not pair or len(pair) < 2:
                        continue
                    # 文字 + 标签左右 padx + 左侧 accent + 条目外边距
                    need = max(need, f.measure(str(pair[1]))
                               + px(13 + ind) * 2 + px(16))
            self.configure(width=max(px(base), min(px(220), need)))
        except Exception:  # noqa: BLE001  量不出来就用默认宽度，不该拖垮导航
            pass

    # ---------------------------------------------------------- 滚动
    def _sync(self, _e=None) -> None:
        """内容高度或可视高度变了：更新滚动区域，顺手决定滚动条去留。

        拖动窗口时会连续来几十次 Configure，每次都去 configure(scrollregion)
        会带着内层 Frame 再重排一遍 —— 一次几十毫秒，累积起来就是肉眼可见
        的卡顿。所以这里先比尺寸，真变了才动，并且合并到空闲时执行。
        """
        if getattr(self, "_sync_pending", False):
            return
        self._sync_pending = True
        try:
            self.after_idle(self._sync_apply)
        except tk.TclError:
            self._sync_pending = False

    def _sync_apply(self) -> None:
        self._sync_pending = False
        try:
            cw = self._canvas.winfo_width()
            ch = self._canvas.winfo_height()
            ih = self._inner.winfo_reqheight()
        except tk.TclError:
            return
        if (cw, ch, ih) == getattr(self, "_sync_seen", None):
            return
        self._sync_seen = (cw, ch, ih)
        try:
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
            # 内容宽度跟着可视宽度走，否则条目只占一半
            self._canvas.itemconfigure(self._win, width=cw)
        except tk.TclError:
            return
        need = ih > ch + 1
        if need and not self._sb_visible:
            self._sb.pack(side="right", fill="y")
            self._sb_visible = True
        elif not need and self._sb_visible:
            self._sb.pack_forget()
            self._sb_visible = False
            try:
                self._canvas.yview_moveto(0)
            except tk.TclError:
                pass

    def _on_scroll_set(self, first, last) -> None:
        """Canvas 的 yscrollcommand：把比例转给滚动条。

        Canvas 自己不会去 call 滚动条，必须手动转发；漏了它滚动条就是死的。
        """
        try:
            self._sb.set(first, last)
        except tk.TclError:
            pass

    def _bind_wheel(self, w) -> None:
        w.bind("<MouseWheel>", self._wheel)
        w.bind("<Button-4>", self._wheel)     # X11 上滚
        w.bind("<Button-5>", self._wheel)     # X11 下滚
        for c in w.winfo_children():
            self._bind_wheel(c)

    def _wheel(self, event):
        try:
            if self._inner.winfo_reqheight() <= self._canvas.winfo_height() + 1:
                return None
        except tk.TclError:
            return None
        # event.num 在 Windows 的 MouseWheel 上是占位符 '??'，int() 会炸，
        # 所以先按 delta 判，X11 的 Button-4/5 才看 num
        try:
            num = int(getattr(event, "num", 0) or 0)
        except (TypeError, ValueError):
            num = 0
        if num == 4:
            step = -3
        elif num == 5:
            step = 3
        else:
            try:
                delta = int(getattr(event, "delta", 0) or 0)
            except (TypeError, ValueError):
                delta = 0
            if delta == 0:
                return None
            step = -3 if delta > 0 else 3
        try:
            self._canvas.yview_scroll(step, "units")
        except tk.TclError:
            return None
        return "break"

    # ---------------------------------------------------------- 条目
    def _add_group(self, title: str, subs) -> None:
        # 标题为空表示「这组就是当前模块本身」，不画小标题 ——
        # 顶部页签已经写着模块名，侧栏再来一行是重复噪音。
        if title:
            lb = tk.Label(self._inner, text=title, bg=T.page, fg=T.text_mute,
                          font=FONT_XS, anchor="w", bd=0, padx=px(15),
                          pady=px(1))
            lb.pack(fill="x", pady=(px(11), px(1)))
            register(lb, lambda: lb.configure(bg=T.page, fg=T.text_mute))
            self._bind_wheel(lb)
        else:
            tk.Frame(self._inner, bg=T.page, height=px(2), bd=0).pack(fill="x")
        for key, label in subs:
            self._add_item(key, label, 11)

    def _add_item(self, key: str, label: str, indent: int) -> None:
        it = _NavItem(self._inner, label, lambda k=key: self.select(k),
                      indent=indent)
        it.pack(fill="x", pady=px(1), padx=(px(2), px(8)))
        self.items[key] = it
        self._bind_wheel(it)

    def select(self, key: str) -> None:
        key = self.alias.get(key, key)
        # 不存在的键静默忽略：旧脚本可能还在传已经并掉的页面名，
        # 直接抛 KeyError 会让整个启动流程断在导航上。
        if key not in self.items or key == self.current:
            return
        self.current = key
        for k, it in self.items.items():
            it.set_active(k == key)
        if self.on_change:
            self.on_change(key)


# ---------------------------------------------------------------- 小工具
def hsep(master, role: str = "card", pady=(8, 8)) -> tk.Frame:
    """1px 分隔线。"""
    bg = T.border
    holder = tk.Frame(master, bg=role_colors(role)[0], bd=0)
    holder.pack(fill="x", pady=pady)
    line = tk.Frame(holder, bg=bg, height=px(1), bd=0)
    line.pack(fill="x")
    register(line, lambda: line.configure(bg=T.border))
    register(holder, lambda: holder.configure(bg=role_colors(role)[0]))
    return holder
