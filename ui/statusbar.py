# -*- coding: utf-8 -*-
"""底部状态栏 —— 仿 MobaXterm 那种一格一格的设备状态条。

从左到右：设备（带系统图标）· CPU 占用 · CPU 迷你曲线 · 内存 ·
上传速率 · 下载速率 · 运行时长 · 右侧折叠/断开按钮。

全部用 Canvas 自绘图标，不依赖任何外部图片资源。
"""
from __future__ import annotations

import tkinter as tk

import wbtui as U
from core.metrics import Sample, human_gb, human_rate, human_uptime

H = 42          # 状态栏高度（设计稿像素）


# ------------------------------------------------------------------ 图标
def os_icon(cv: tk.Canvas, s: int, family: str, fg: str) -> None:
    """画一个 16-18px 的系统徽标。s 是画布边长。"""
    cv.delete("all")
    p = s * 0.09                      # 内边距
    if family == "linux":
        # 企鹅：黑身 + 白肚 + 橙嘴
        cv.create_oval(p, p, s - p, s - p * 0.5, fill="#1B1B1F", outline="")
        cv.create_oval(s * 0.24, s * 0.46, s * 0.76, s - p * 0.6,
                       fill="#F7FAFF", outline="")
        cv.create_polygon(s * 0.34, s * 0.40, s * 0.66, s * 0.40, s * 0.50, s * 0.54,
                          fill="#F0A63C", outline="")
    elif family == "windows":
        g = s * 0.06
        half = (s - g * 3) / 2
        for i in range(2):
            for j in range(2):
                x = g + i * (half + g)
                y = g + j * (half + g)
                cv.create_rectangle(x, y, x + half, y + half,
                                    fill="#0F7BD4", outline="")
    elif family == "darwin":
        cv.create_oval(p, p * 1.4, s - p, s - p * 0.2, fill=fg, outline="")
        cv.create_oval(s * 0.56, p * 0.4, s * 0.78, p * 2.2, fill=fg, outline="")
        cv.create_oval(s * 0.30, s * 0.30, s * 0.52, s * 0.52,
                       fill=U.T.card, outline="")
    elif family in ("freebsd", "bsd"):
        cv.create_oval(p, p, s - p, s - p, fill="#B03030", outline="")
        cv.create_text(s / 2, s / 2, text="F", fill="#FFFFFF",
                       font=("Consolas", max(7, int(s * 0.42)), "bold"))
    elif family == "network":
        cv.create_rectangle(p, s * 0.3, s - p, s * 0.7, fill=fg, outline="")
        for i in range(3):
            x = s * 0.22 + i * s * 0.24
            cv.create_oval(x - 1.5, s * 0.72, x + 1.5, s * 0.75, fill=fg, outline="")
    else:
        # 通用：终端窗口
        cv.create_rectangle(p, s * 0.22, s - p, s * 0.80,
                            fill=fg, outline="")
        cv.create_line(s * 0.28, s * 0.38, s * 0.44, s * 0.51,
                       s * 0.28, s * 0.64, fill=U.T.card, width=max(1, int(s * 0.09)))
        cv.create_line(s * 0.50, s * 0.64, s * 0.70, s * 0.64,
                       fill=U.T.card, width=max(1, int(s * 0.09)))


def cpu_icon(cv: tk.Canvas, s: int, fg: str) -> None:
    cv.delete("all")
    m = s * 0.26
    cv.create_rectangle(m, m, s - m, s - m, outline=fg, width=max(1, int(s * 0.10)))
    cv.create_rectangle(s * 0.40, s * 0.40, s * 0.60, s * 0.60, fill=fg, outline="")
    pin = max(1, int(s * 0.09))
    for i in range(3):
        o = s * (0.34 + i * 0.16)
        cv.create_line(o, m * 0.35, o, m, fill=fg, width=pin)
        cv.create_line(o, s - m, o, s - m * 0.35, fill=fg, width=pin)
        cv.create_line(m * 0.35, o, m, o, fill=fg, width=pin)
        cv.create_line(s - m, o, s - m * 0.35, o, fill=fg, width=pin)


def mem_icon(cv: tk.Canvas, s: int, fg: str) -> None:
    cv.delete("all")
    cv.create_rectangle(s * 0.10, s * 0.30, s * 0.90, s * 0.68,
                        outline=fg, width=max(1, int(s * 0.10)))
    for i in range(3):
        x = s * (0.28 + i * 0.22)
        cv.create_line(x, s * 0.38, x, s * 0.60, fill=fg,
                       width=max(1, int(s * 0.08)))
    for i in range(4):
        x = s * (0.20 + i * 0.20)
        cv.create_line(x, s * 0.68, x, s * 0.80, fill=fg,
                       width=max(1, int(s * 0.08)))


def arrow_icon(cv: tk.Canvas, s: int, up: bool, color: str) -> None:
    cv.delete("all")
    cx = s / 2
    w = max(1, int(s * 0.11))
    if up:
        cv.create_line(cx, s * 0.86, cx, s * 0.20, fill=color, width=w,
                       arrow="last", arrowshape=(s * 0.34, s * 0.40, s * 0.20))
    else:
        cv.create_line(cx, s * 0.14, cx, s * 0.80, fill=color, width=w,
                       arrow="last", arrowshape=(s * 0.34, s * 0.40, s * 0.20))


def clock_icon(cv: tk.Canvas, s: int, fg: str) -> None:
    cv.delete("all")
    cv.create_oval(s * 0.10, s * 0.10, s * 0.90, s * 0.90,
                   outline=fg, width=max(1, int(s * 0.09)))
    cv.create_line(s / 2, s / 2, s / 2, s * 0.28, fill=fg,
                   width=max(1, int(s * 0.08)))
    cv.create_line(s / 2, s / 2, s * 0.70, s * 0.60, fill=fg,
                   width=max(1, int(s * 0.08)))


# ------------------------------------------------------------------ 格子
class Cell(tk.Frame):
    def __init__(self, master, painter, text: str = "", width: int | None = None,
                 mono: bool = False):
        super().__init__(master, bg=U.T.card, bd=0, highlightthickness=0)
        self.painter = painter
        self.size = U.px(17)
        self.cv = tk.Canvas(self, width=self.size, height=self.size,
                            bg=U.T.card, highlightthickness=0, bd=0)
        self.cv.pack(side="left", padx=(U.px(9), U.px(5)))
        self.lb = tk.Label(self, text=text, bg=U.T.card, fg=U.T.text_dim,
                           font=U.MONO_S if mono else U.FONT_S, bd=0)
        self.lb.pack(side="left", padx=(0, U.px(9)))
        # 注意：不能用 self._w！tkinter.Misc 内部用 _w 存控件路径名，
        # 覆盖它会让后面所有 tk 调用报 "wrong # args"。
        self.fixed_w = width
        if width:
            self.configure(width=U.px(width))

    def paint(self, color: str | None = None) -> None:
        self.cv.configure(bg=U.T.card)
        self.lb.configure(bg=U.T.card, fg=U.T.text)
        self.painter(self.cv, self.size, color or U.T.text_dim)

    def set_text(self, text: str, fg: str | None = None) -> None:
        self.lb.configure(text=text, fg=fg or U.T.text)


class Sep(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=U.T.border, width=1, bd=0)
        self.pack(side="left", fill="y", pady=U.px(9))


class StatusBar(tk.Frame):
    """底部状态条。未连接时所有指标显示 —。"""

    def __init__(self, master, on_fold=None, on_unfold=None, on_disconnect=None):
        super().__init__(master, bg=U.T.card, bd=0, highlightthickness=1,
                         highlightbackground=U.T.border, height=U.px(H))
        self.pack_propagate(False)

        self._family = "unknown"
        self._folded = False
        self.on_fold = on_fold
        self.on_unfold = on_unfold
        self.on_disconnect = on_disconnect

        # ---- 设备
        self.cell_dev = Cell(self, self._paint_dev, "未连接")
        self.cell_dev.pack(side="left")
        Sep(self)

        # ---- CPU + 曲线
        self.cell_cpu = Cell(self, cpu_icon, "—", width=74)
        self.cell_cpu.pack(side="left")
        self.cv_hist = tk.Canvas(self, width=U.px(72), height=U.px(20),
                                 bg=U.T.card, highlightthickness=0, bd=0)
        self.cv_hist.pack(side="left", padx=(0, U.px(4)))
        Sep(self)

        # ---- 内存
        self.cell_mem = Cell(self, mem_icon, "—")
        self.cell_mem.pack(side="left")
        Sep(self)

        # ---- 网络
        self.cell_up = Cell(self, lambda cv, s, c: arrow_icon(cv, s, True, "#12855C"),
                            "—", width=112)
        self.cell_up.pack(side="left")
        self.cell_down = Cell(self, lambda cv, s, c: arrow_icon(cv, s, False, "#2F6FD0"),
                              "—", width=112)
        self.cell_down.pack(side="left")
        Sep(self)

        # ---- 运行时长
        self.cell_up2 = Cell(self, clock_icon, "—", width=104)
        self.cell_up2.pack(side="left")

        # ---- 右侧动作
        self.btn_close = self._mini_btn("✕", U.T.err, on_disconnect or (lambda: None))
        self.btn_close.pack(side="right", padx=(0, U.px(8)))
        self.btn_next = self._mini_btn("▶", U.T.text_dim, lambda: self._fold(False))
        self.btn_next.pack(side="right", padx=U.px(2))
        self.btn_prev = self._mini_btn("◀", U.T.text_dim, lambda: self._fold(True))
        self.btn_prev.pack(side="right", padx=(U.px(8), 0))

        # ---- 右侧状态字（可伸缩）
        self.lb_state = tk.Label(self, text="就绪", bg=U.T.card, fg=U.T.text_mute,
                                 font=U.FONT_XS, bd=0, anchor="e")
        self.lb_state.pack(side="right", padx=U.px(10))

        self.paint_all()
        self.clear()

    # ---------------- 内部
    def _mini_btn(self, text: str, color: str, cmd) -> tk.Label:
        lb = tk.Label(self, text=text, bg=U.T.card, fg=color,
                      font=(U.FONT_FAMILY, 10, "bold"), bd=0, padx=U.px(7),
                      cursor="hand2")
        lb.bind("<Button-1>", lambda e: cmd())
        lb.bind("<Enter>", lambda e: lb.configure(bg=U.T.brand_soft))
        lb.bind("<Leave>", lambda e: lb.configure(bg=U.T.card))
        return lb

    def _fold(self, folded: bool) -> None:
        self._folded = folded
        if folded and self.on_fold:
            self.on_fold()
        elif not folded and self.on_unfold:
            self.on_unfold()
        self.lb_state.configure(text="日志已折叠" if folded else "日志已展开")

    def _paint_dev(self, cv: tk.Canvas, s: int, color: str) -> None:
        os_icon(cv, s, self._family, U.T.text_dim)

    # ---------------- 对外
    def paint_all(self) -> None:
        """主题切换后重画（颜色变了）。"""
        self.configure(bg=U.T.card, highlightbackground=U.T.border)
        self.cell_dev.paint()
        self.cell_cpu.paint()
        self.cell_mem.paint()
        self.cell_up.paint()
        self.cell_down.paint()
        self.cell_up2.paint()
        self.cv_hist.configure(bg=U.T.card)
        self.btn_close.configure(bg=U.T.card)
        self.btn_prev.configure(bg=U.T.card, fg=U.T.text_dim)
        self.btn_next.configure(bg=U.T.card, fg=U.T.text_dim)
        self.lb_state.configure(bg=U.T.card, fg=U.T.text_mute)
        self._draw_hist()

    def clear(self) -> None:
        self._family = "unknown"
        self.cell_dev.set_text("未连接", U.T.text_mute)
        self.cell_dev.paint()
        for c in (self.cell_cpu, self.cell_mem, self.cell_up, self.cell_down,
                  self.cell_up2):
            c.set_text("—", U.T.text_mute)
        self.cv_hist.delete("all")
        self.lb_state.configure(text="就绪")

    def set_device(self, name: str, family: str) -> None:
        self._family = family or "unknown"
        self.cell_dev.set_text(name or "已连接", U.T.text)
        self.cell_dev.paint()

    def set_state(self, text: str, tone: str = "mute") -> None:
        color = {"ok": U.T.ok, "err": U.T.err, "warn": U.T.warn}.get(tone, U.T.text_mute)
        self.lb_state.configure(text=text, fg=color)

    def update_sample(self, s: Sample, hist: list[float] | None = None) -> None:
        if not s or not s.ok:
            self.set_state(s.error if s and s.error else "采样失败", "err")
            return
        self.cell_cpu.set_text(f"{s.cpu_pct:.0f}%",
                               U.T.err if s.cpu_pct >= 85
                               else (U.T.warn if s.cpu_pct >= 65 else U.T.text))
        self.cell_mem.set_text(f"{human_gb(s.mem_used)} / {human_gb(s.mem_total)}")
        self.cell_up.set_text(human_rate(s.net_tx_bps))
        self.cell_down.set_text(human_rate(s.net_rx_bps))
        self.cell_up2.set_text(human_uptime(s.uptime))
        self._draw_hist(hist if hist is not None else list(self._hist_cache))
        self._sync_fold_buttons()

    _hist_cache: tuple = ()

    def _sync_fold_buttons(self) -> None:
        pass

    def _draw_hist(self, hist: list[float] | None = None) -> None:
        self._hist_cache = tuple(hist or ())
        cv = self.cv_hist
        cv.delete("all")
        w = cv.winfo_width() or U.px(72)
        h = cv.winfo_height() or U.px(20)
        cv.create_line(0, h - 1, w, h - 1, fill=U.T.border)
        data = list(self._hist_cache)
        if len(data) < 2:
            return
        n = len(data)
        step = w / max(1, n - 1)
        pts: list[float] = []
        for i, v in enumerate(data):
            y = h - 2 - (max(0.0, min(100.0, v)) / 100.0) * (h - 4)
            pts.extend([i * step, y])
        # 面积
        poly = pts + [w, h - 1, 0, h - 1]
        cv.create_polygon(poly, fill=U.T.brand_soft, outline="")
        cv.create_line(*pts, fill=U.T.brand, width=max(1, int(U.px(1.4))),
                       smooth=True)
