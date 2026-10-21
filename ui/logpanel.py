# -*- coding: utf-8 -*-
"""命令日志面板 —— 不再是一个选项卡，而是可折叠、可分离的侧边栏。

设计要点：**内容与视图分开**。

  · LogModel 只存 (kind, text)，不持有任何控件；
  · LogPanel 是视图，可以被放到主窗口右侧的 dock，也可以扔进独立 Toplevel；
  · 分离/归位时销毁旧视图、在新容器里重建，再从 model 重放内容。

tkinter 不能把控件搬到另一个父容器，所以只能这么做 —— 但用户看不出区别。
"""
from __future__ import annotations

import tkinter as tk

import wbtui as U
from core.redact import scrub

MAX_LINES = 6000          # 超出后从头截断，避免长跑把内存吃干

EMPTY_HINT = (
    "还没有命令记录。\n\n"
    "连接设备后，每一条命令、返回码、耗时和输出都会按时间顺序记在这里；\n"
    "点右上角「分离」可以把这块日志拖到第二块屏幕上去看。\n"
)


class LogModel:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def add(self, kind: str, text: str) -> None:
        if not text:
            return
        # 第二道闸：调用方漏掉 _scrub 也还有这一层兜底
        self.lines.append((kind, scrub(text)))
        if len(self.lines) > MAX_LINES:
            del self.lines[: len(self.lines) - MAX_LINES]

    def clear(self) -> None:
        self.lines.clear()

    def text(self) -> str:
        return "".join(t for _k, t in self.lines)


class LogPanel(tk.Frame):
    """视图。floating=True 时用在独立窗口里。"""

    def __init__(self, master, model: LogModel, floating: bool = False,
                 on_float=None, on_dock=None, on_close=None):
        super().__init__(master, bg=U.T.card, bd=0, highlightthickness=0)
        self.model = model
        self.floating = floating
        self._auto_scroll = True

        # ---- 头部
        head = tk.Frame(self, bg=U.T.card, height=U.px(34))
        head.pack(fill="x", padx=U.px(10), pady=(U.px(6), 0))
        head.pack_propagate(False)

        tk.Label(head, text="命令日志", bg=U.T.card, fg=U.T.text,
                 font=U.FONT_B, bd=0).pack(side="left")
        self.lb_count = tk.Label(head, text="", bg=U.T.card, fg=U.T.text_mute,
                                 font=U.FONT_XS, bd=0)
        self.lb_count.pack(side="left", padx=(U.px(8), 0))

        self._act(head, "清空", self._clear)
        self._act(head, "复制全部", self._copy)
        if floating:
            self._act(head, "归位", on_dock or (lambda: None))
        else:
            self._act(head, "分离", on_float or (lambda: None))
        if on_close:
            self._act(head, "✕", on_close, color=U.T.err)

        tk.Frame(self, bg=U.T.border, height=1).pack(fill="x", pady=(U.px(6), 0))

        # ---- 正文
        wrap = tk.Frame(self, bg=U.T.console_bg, bd=0)
        wrap.pack(fill="both", expand=True)
        self.txt = tk.Text(
            wrap, bg=U.T.console_bg, fg=U.T.console_fg, font=U.MONO_S,
            bd=0, relief="flat", highlightthickness=0, wrap="word",
            padx=U.px(10), pady=U.px(8), insertwidth=0, cursor="arrow")
        sb = tk.Scrollbar(wrap, command=self.txt.yview, width=U.px(11))
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)

        self.txt.tag_configure("cmd", foreground=U.T.console_cmd)
        self.txt.tag_configure("ok", foreground=U.T.console_ok)
        self.txt.tag_configure("bad", foreground=U.T.console_bad)
        self.txt.tag_configure("meta", foreground=U.T.console_meta)
        self.txt.tag_configure("out", foreground=U.T.console_fg)
        self.txt.tag_configure("hint", foreground=U.T.console_meta,
                               spacing1=U.px(6))
        self.txt.configure(state="disabled")
        self._empty = True

        self.txt.bind("<MouseWheel>", self._on_wheel, add="+")
        self.txt.bind("<Button-4>", self._on_wheel, add="+")
        self.txt.bind("<Button-5>", self._on_wheel, add="+")

        self.replay()

    # ---------------- 头部小按钮
    def _act(self, parent, text: str, cmd, color: str | None = None) -> None:
        lb = tk.Label(parent, text=text, bg=U.T.card,
                      fg=color or U.T.brand, font=U.FONT_XS, bd=0,
                      padx=U.px(7), pady=U.px(2), cursor="hand2")
        lb.pack(side="right", padx=(U.px(4), 0))
        lb.bind("<Button-1>", lambda e: cmd())
        lb.bind("<Enter>", lambda e: lb.configure(bg=U.T.brand_soft))
        lb.bind("<Leave>", lambda e: lb.configure(bg=U.T.card))

    # ---------------- 内容
    def replay(self) -> None:
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        if not self.model.lines:
            self.txt.insert("end", EMPTY_HINT, "hint")
            self._empty = True
        else:
            for kind, text in self.model.lines:
                self.txt.insert("end", text, kind)
            self._empty = False
        self.txt.configure(state="disabled")
        self._to_end()
        self._update_count()

    def append(self, kind: str, text: str) -> None:
        if not text:
            return
        text = scrub(text)
        self.txt.configure(state="normal")
        if self._empty:
            # 第一行进来时先把空态提示抹掉
            self.txt.delete("1.0", "end")
            self._empty = False
        self.txt.insert("end", text, kind)
        self.txt.configure(state="disabled")
        self._trim()
        if self._auto_scroll:
            self._to_end()
        self._update_count()

    def _trim(self) -> None:
        # 视图里也保一个上限，避免长跑卡顿
        try:
            n = int(self.txt.index("end-1c").split(".")[0])
        except (ValueError, tk.TclError):
            return
        if n > MAX_LINES:
            self.txt.configure(state="normal")
            self.txt.delete("1.0", f"{n - MAX_LINES}.0")
            self.txt.configure(state="disabled")

    def _to_end(self) -> None:
        try:
            self.txt.see("end")
        except tk.TclError:
            pass

    def _on_wheel(self, e) -> None:
        # 用户往上翻就停止自动滚动，回到底部再恢复
        self.after(30, self._check_scroll)

    def _check_scroll(self) -> None:
        try:
            _first, last = self.txt.yview()
        except tk.TclError:
            return
        self._auto_scroll = last >= 0.999

    def _update_count(self) -> None:
        self.lb_count.configure(text=f"{len(self.model.lines)} 条")

    def _clear(self) -> None:
        self.model.clear()
        self.replay()

    def _copy(self) -> None:
        txt = self.model.text()
        if not txt:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(txt)
            self.lb_count.configure(text="已复制")
            self.after(1200, self._update_count)
        except tk.TclError:
            pass
