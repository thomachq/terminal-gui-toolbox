# -*- coding: utf-8 -*-
"""极简 Markdown 渲染 —— 只做聊天里真正会出现的那几种语法。

为什么不用现成的：Tk 里没有能直接吃 Markdown 的控件，而把整段文字塞进一个
Label 的结果就是**加粗变星号、列表挤成一坨、代码和正文一个样子** —— 看起来
就是「简陋」。这里按块拆开渲染：

  · 段落   → 没有行内标记就用 Label（高度自适应最准）；
             有 **加粗** / `行内代码` / [链接](url) 才换成带 tag 的 Text
  · 标题   → 单独一行，字号跟着 # 层级走
  · 列表   → 左边一个项目符号，右侧仍可带行内样式
  · 代码块 → 深色块 + 顶部一行「语言 / 复制」，正文等宽

刻意不做：表格、脚注、嵌套列表、HTML 内联。运维对话里几乎不出现，
真写了就退回纯文本显示，不会崩，也不会把界面撑破。
"""
from __future__ import annotations

import math
import re
import tkinter as tk

import wbtui as U

FENCE = re.compile(r"^```([\w+#.-]*)\s*$")
HEAD = re.compile(r"^(#{1,4})\s+(.*)$")
BULLET = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
QUOTE = re.compile(r"^>\s?(.*)$")
# 行内：**粗**、*斜*、`码`、[文字](链接)
INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\))")


def parse_blocks(text: str):
    """切成 [(kind, payload)]。认不出的行当普通段落，绝不丢内容。"""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[tuple] = []
    buf: list[str] = []
    i = 0

    def flush():
        if buf:
            out.append(("p", "\n".join(buf).strip("\n")))
            buf.clear()

    while i < len(lines):
        line = lines[i]
        m = FENCE.match(line.strip())
        if m:
            flush()
            lang = m.group(1) or ""
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            out.append(("code", (lang, "\n".join(body))))
            i += 1
            continue
        m = HEAD.match(line)
        if m:
            flush()
            out.append(("h", (len(m.group(1)), m.group(2).strip())))
            i += 1
            continue
        m = QUOTE.match(line)
        if m:
            flush()
            out.append(("quote", m.group(1).strip()))
            i += 1
            continue
        m = BULLET.match(line)
        if m:
            flush()
            raw = m.group(2)
            mark = raw if raw[0].isdigit() else "•"
            out.append(("li", (mark, m.group(3).strip())))
            i += 1
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        buf.append(line)
        i += 1
    flush()
    return out


def strip_inline(s: str) -> str:
    """去掉行内标记，只留字 —— 标题这种地方用。"""
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    return s


def has_inline(s: str) -> bool:
    return bool(INLINE.search(s or ""))


class MdView:
    """把一段 Markdown 渲染进一个 Frame。

    wrap_px 是可用宽度（像素）。Tk 不会替你算换行后的高度，所以这里
    要么交给 Label（wraplength，最准），要么按字符数估行数给 Text。
    """

    def __init__(self, parent, text: str, *, wrap_px: int, bg: str | None = None,
                 fg: str | None = None, font=None, mono=None,
                 on_link=None, compact: bool = False):
        self.bg = bg or U.T.page
        self.fg = fg or U.T.text
        self.font = font or U.FONT_S
        self.mono = mono or U.MONO_S
        self.wrap_px = max(U.px(120), int(wrap_px))
        self.on_link = on_link
        self.compact = compact
        self.frame = tk.Frame(parent, bg=self.bg, bd=0)
        self._boxes: list[tk.Widget] = []
        self._link_no = 0
        self._build(str(text or ""))

    # ------------------------------------------------------------ 构建
    def _build(self, text: str) -> None:
        for kind, payload in parse_blocks(text) or [("p", "")]:
            try:
                if kind == "code":
                    self._code(*payload)
                elif kind == "h":
                    self._head(*payload)
                elif kind == "li":
                    self._bullet(*payload)
                elif kind == "quote":
                    self._quote(payload)
                else:
                    self._para(payload)
            except tk.TclError:
                continue

    def _head(self, level: int, text: str) -> None:
        size = {1: 13, 2: 12, 3: 11, 4: 11}.get(level, 11)
        lb = tk.Label(self.frame, text=strip_inline(text), bg=self.bg,
                      fg=U.T.text, font=(U.FONT_FAMILY, size, "bold"),
                      justify="left", anchor="w", bd=0,
                      wraplength=self.wrap_px)
        lb.pack(fill="x", pady=(U.px(8 if level <= 2 else 5), U.px(2)))
        self._boxes.append(lb)

    def _quote(self, text: str) -> None:
        row = tk.Frame(self.frame, bg=self.bg)
        row.pack(fill="x", pady=(U.px(3), U.px(3)))
        tk.Frame(row, bg=U.T.border2, width=U.px(3)).pack(side="left", fill="y")
        inner = tk.Frame(row, bg=self.bg)
        inner.pack(side="left", fill="x", expand=True, padx=(U.px(8), 0))
        self._rich(inner, text, fg=U.T.text_dim)
        self._boxes.append(inner)

    def _bullet(self, mark: str, text: str) -> None:
        row = tk.Frame(self.frame, bg=self.bg)
        row.pack(fill="x", pady=(U.px(1), U.px(1)))
        tk.Label(row, text=mark, bg=self.bg, fg=U.T.text_mute,
                 font=self.font, bd=0, anchor="ne").pack(
            side="left", anchor="n", padx=(U.px(2), U.px(4)))
        inner = tk.Frame(row, bg=self.bg)
        inner.pack(side="left", fill="x", expand=True)
        self._rich(inner, text, fg=self.fg)
        self._boxes.append(inner)

    def _para(self, text: str) -> None:
        if not str(text).strip():
            return
        pad = (U.px(1), U.px(2)) if self.compact else (U.px(2), U.px(3))
        holder = tk.Frame(self.frame, bg=self.bg)
        holder.pack(fill="x", pady=pad)
        self._rich(holder, text, fg=self.fg)
        self._boxes.append(holder)

    def _rich(self, parent, text: str, *, fg: str = "") -> None:
        """没有行内标记就走 Label —— 高度最准，也不占额外资源。"""
        fg = fg or self.fg
        if not has_inline(text):
            lb = tk.Label(parent, text=text, bg=self.bg, fg=fg, font=self.font,
                          justify="left", anchor="w", bd=0,
                          wraplength=self.wrap_px)
            lb.pack(fill="x")
            return
        self._text(parent, text, fg=fg)

    def _text(self, parent, text: str, *, fg: str) -> tk.Text:
        base = self.font
        txt = tk.Text(parent, bg=self.bg, fg=fg, bd=0, highlightthickness=0,
                      wrap="char", font=base, width=1, height=1,
                      padx=0, pady=0, spacing1=1, spacing3=1, relief="flat",
                      cursor="arrow", insertwidth=0, takefocus=0,
                      selectbackground=U.T.brand_soft2,
                      selectforeground=U.T.text)
        txt.tag_configure("b", font=(base[0], base[1], "bold"))
        txt.tag_configure("i", font=(base[0], base[1], "italic"))
        txt.tag_configure("code", font=self.mono, background=U.T.alt,
                          foreground=U.T.text)
        pos = 0
        for m in INLINE.finditer(text):
            if m.start() > pos:
                txt.insert("end", text[pos:m.start()])
            tok = m.group(0)
            if tok.startswith("**"):
                txt.insert("end", tok[2:-2], "b")
            elif tok.startswith("`"):
                txt.insert("end", tok[1:-1], "code")
            elif tok.startswith("["):
                label, _, url = tok[1:-1].partition("](")
                self._link_no += 1
                tag = f"lk{self._link_no}"
                txt.tag_configure(tag, foreground=U.T.brand, underline=True)
                txt.insert("end", label, tag)
                txt.tag_bind(tag, "<Button-1>", lambda e, u=url: self._open(u))
                txt.tag_bind(tag, "<Enter>",
                             lambda e, w=txt: w.configure(cursor="hand2"))
                txt.tag_bind(tag, "<Leave>",
                             lambda e, w=txt: w.configure(cursor="arrow"))
            else:
                txt.insert("end", tok[1:-1], "i")
            pos = m.end()
        if pos < len(text):
            txt.insert("end", text[pos:])
        txt.pack(fill="x")
        txt.configure(state="disabled")
        # 高度：先按字符数估一个，等控件真的有了宽度再用 displaylines 校正。
        # 必须在有宽度之后算 —— width=1 的时候 Tk 会把每个字都算成一行，
        # 一算就是几十行空白（这个坑踩过一次）。
        per = max(8, int(self.wrap_px / max(6.0, base[1] * 0.62)))
        guess = sum(max(1, math.ceil(len(seg) / per)) for seg in text.split("\n"))
        txt.configure(height=max(1, min(guess, 40)))
        self._autosize(txt)
        return txt

    def _autosize(self, txt: tk.Text, tries: int = 0) -> None:
        """控件拿到真实宽度后，按实际折行数重设高度。"""
        try:
            if not txt.winfo_exists():
                return
            w = txt.winfo_width()
            if w <= 2 and tries < 6:
                txt.after(40, lambda: self._autosize(txt, tries + 1))
                return
            n = int(txt.count("1.0", "end-1c", "displaylines")[0])
            if n > 0:
                txt.configure(height=max(1, min(n, 40)))
        except (tk.TclError, TypeError, IndexError):
            pass

    def _code(self, lang: str, body: str) -> None:
        card = tk.Frame(self.frame, bg=U.T.console_bg, bd=0)
        card.pack(fill="x", pady=(U.px(5), U.px(5)))
        head = tk.Frame(card, bg=U.T.console_bg)
        head.pack(fill="x", padx=U.px(8), pady=(U.px(4), 0))
        tk.Label(head, text=(lang or "code"), bg=U.T.console_bg,
                 fg=U.T.console_meta, font=U.FONT_XS, bd=0).pack(side="left")
        btn = tk.Label(head, text="复制", bg=U.T.console_bg, fg=U.T.console_meta,
                       font=U.FONT_XS, bd=0, cursor="hand2", padx=U.px(6))
        btn.pack(side="right")

        def copy(_e=None):
            try:
                self.frame.clipboard_clear()
                self.frame.clipboard_append(body)
                btn.configure(text="已复制", fg=U.T.console_ok)
                self.frame.after(1200, lambda: btn.configure(
                    text="复制", fg=U.T.console_meta))
            except tk.TclError:
                pass

        btn.bind("<Button-1>", copy)
        lines = body.split("\n") or [""]
        txt = tk.Text(card, bg=U.T.console_bg, fg=U.T.console_fg, bd=0,
                      highlightthickness=0, wrap="char", font=self.mono,
                      height=max(1, min(len(lines), 24)), width=1,
                      padx=U.px(8), pady=U.px(5), relief="flat", takefocus=0)
        txt.insert("1.0", body)
        txt.configure(state="disabled")
        txt.pack(fill="x")
        self._boxes.append(card)

    def _open(self, url: str) -> None:
        if self.on_link:
            try:
                self.on_link(url)
                return
            except Exception:  # noqa: BLE001
                pass
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass


def height_hint(text: str, wrap_chars: int = 40) -> int:
    """粗略估计渲染后会有多高（行），滚动定位用。"""
    n = 0
    for kind, payload in parse_blocks(text):
        if kind == "code":
            n += len(payload[1].split("\n")) + 2
        elif kind == "h":
            n += 2
        elif kind == "p":
            n += max(1, len(payload) // wrap_chars + 1)
        else:
            n += 1
    return max(1, n)
