# -*- coding: utf-8 -*-
"""顶部页签栏 —— 浏览器式的工作区标签。

为什么要这一层：连接多台主机时，终端不该抢走整个界面，也不该只剩一个浮动窗口。
页签把「主机资源」和各个终端放进同一排，切过去就是那台机器的终端，切回来还是
清单。终端页签上带一个「拉出」按钮 —— 需要双屏对着敲命令时，一个点击就变成
独立窗口，收回来也是同一个按钮。

页签不做圆角也不是懒：Tk 里画圆角得整条用 Canvas 重绘，换来的是行高、点击区、
滚动全都要自己算。方角 + 底部色条已经能一眼看出哪个是当前页。
"""
from __future__ import annotations

import tkinter as tk

import wbtui as U


class TabBar(tk.Frame):
    def __init__(self, master, on_select=None, on_new=None):
        super().__init__(master, bg=U.T.alt, bd=0, height=U.px(34))
        self.pack_propagate(False)
        self.on_select = on_select
        self.on_new = on_new
        self.tabs: dict[str, dict] = {}
        self.current = ""

        self.holder = tk.Frame(self, bg=U.T.alt, bd=0)
        self.holder.pack(side="left", fill="both", expand=False,
                         padx=(U.px(4), 0))

        self.right = tk.Frame(self, bg=U.T.alt, bd=0)
        self.right.pack(side="right", fill="y", padx=(0, U.px(6)))
        self.btn_new = tk.Label(self.right, text="+", bg=U.T.alt,
                                fg=U.T.text_dim, font=(U.FONT_FAMILY, 13),
                                bd=0, padx=U.px(9), pady=U.px(2),
                                cursor="hand2")
        self.btn_new.pack(side="left")
        self.btn_new.bind("<Button-1>", lambda e: self.on_new and self.on_new())
        self.btn_new.bind("<Enter>", lambda e: self.btn_new.configure(
            bg=U.T.card, fg=U.T.text))
        self.btn_new.bind("<Leave>", lambda e: self.btn_new.configure(
            bg=U.T.alt, fg=U.T.text_dim))

        U.register(self, self._paint)

    # ------------------------------------------------------------ 增删查
    def add(self, key: str, label: str, closable: bool = True,
            on_popout=None, tooltip: str = "") -> None:
        if key in self.tabs:
            self.set_label(key, label)
            return
        box = tk.Frame(self.holder, bg=U.T.alt, bd=0, cursor="hand2")
        box.pack(side="left", fill="y", padx=(0, U.px(2)), pady=(U.px(4), 0))
        # 顶部 2px 的主色条：激活时才显示，比整块换底色更省眼睛
        bar = tk.Frame(box, bg=U.T.alt, height=U.px(2), bd=0)
        bar.pack(fill="x", side="top")
        inner = tk.Frame(box, bg=U.T.alt, bd=0)
        inner.pack(fill="both", expand=True)
        lb = tk.Label(inner, text=label, bg=U.T.alt, fg=U.T.text_dim,
                      font=U.FONT_S, bd=0, padx=U.px(11), pady=U.px(4),
                      anchor="w")
        lb.pack(side="left")

        btn_pop = None
        if on_popout is not None:
            btn_pop = tk.Label(inner, text="⧉", bg=U.T.alt, fg=U.T.text_mute,
                               font=U.FONT_XS, bd=0, padx=U.px(4),
                               cursor="hand2")
            btn_pop.pack(side="left")
            btn_pop.bind("<Button-1>",
                         lambda e: (on_popout(), "break")[1])

        btn_close = None
        if closable:
            btn_close = tk.Label(inner, text="×", bg=U.T.alt, fg=U.T.text_mute,
                                 font=(U.FONT_FAMILY, 11), bd=0,
                                 padx=U.px(7), cursor="hand2")
            btn_close.pack(side="left", padx=(0, U.px(3)))
            btn_close.bind("<Button-1>", lambda e: (self.close(key), "break")[1])

        # 顶部那 2px 色条也要能点：它在 box 的最上沿，视觉上属于页签，
        # 点上去没反应就成了「这个页签有时候点不动」。
        for w in (box, bar, inner, lb):
            w.bind("<Button-1>", lambda e, k=key: self.select(k))
            w.bind("<Enter>", lambda e, k=key: self._hover(k, True))
            w.bind("<Leave>", lambda e, k=key: self._hover(k, False))
        if tooltip:
            lb.bind("<Enter>", lambda e: None, add="+")

        self.tabs[key] = {"box": box, "bar": bar, "inner": inner, "label": lb,
                          "close": btn_close, "pop": btn_pop, "hover": False,
                          "closable": closable}
        self._paint()
        self.after(10, self._fit)

    def remove(self, key: str) -> None:
        t = self.tabs.pop(key, None)
        if not t:
            return
        try:
            t["box"].destroy()
        except tk.TclError:
            pass
        if self.current == key:
            self.current = ""
            # 关掉的正好是当前页：挪到剩下最右一个，别留下空白工作区
            if self.tabs:
                self.select(list(self.tabs)[-1])
        self._paint()

    def close(self, key: str) -> None:
        """关闭请求交给主程序处理（终端要先断开、普通页签直接移除）。"""
        cb = getattr(self, "on_close", None)
        if cb and cb(key) is False:
            return
        self.remove(key)

    def set_label(self, key: str, text: str) -> None:
        t = self.tabs.get(key)
        if not t:
            return
        try:
            t["label"].configure(text=text)
        except tk.TclError:
            pass
        self._fit()

    def label_of(self, key: str) -> str:
        t = self.tabs.get(key)
        try:
            return t["label"].cget("text") if t else ""
        except tk.TclError:
            return ""

    def select(self, key: str) -> None:
        if key not in self.tabs:
            return
        self.current = key
        self._paint()
        if self.on_select:
            self.on_select(key)

    def count(self) -> int:
        return len(self.tabs)

    # ------------------------------------------------------------ 绘制
    def _hover(self, key: str, on: bool) -> None:
        t = self.tabs.get(key)
        if not t or key == self.current:
            return
        t["hover"] = on
        self._paint()

    def _paint(self) -> None:
        try:
            self.configure(bg=U.T.alt)
            self.holder.configure(bg=U.T.alt)
            self.right.configure(bg=U.T.alt)
        except tk.TclError:
            pass
        for key, t in self.tabs.items():
            on = key == self.current
            bg = U.T.card if on else (U.T.line if t["hover"] else U.T.alt)
            fg = U.T.text if on else U.T.text_dim
            try:
                t["box"].configure(bg=bg)
                t["inner"].configure(bg=bg)
                t["label"].configure(bg=bg, fg=fg,
                                     font=U.FONT_B if on else U.FONT_S)
                t["bar"].configure(bg=U.T.brand if on else bg)
                for k in ("close", "pop"):
                    if t.get(k) is not None:
                        t[k].configure(bg=bg, fg=U.T.text_mute)
            except tk.TclError:
                continue

    def _fit(self) -> None:
        """页签太多时压缩内边距，宁可变窄也不横向溢出到看不见。"""
        n = max(1, len(self.tabs))
        pad = 11 if n <= 4 else (7 if n <= 7 else 4)
        for t in self.tabs.values():
            try:
                t["label"].configure(padx=U.px(pad))
            except tk.TclError:
                continue
