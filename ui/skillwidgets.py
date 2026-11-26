# -*- coding: utf-8 -*-
"""技能库的两个控件：内嵌面板 `SkillPanel` 与独立窗口 `SkillDialog`。

**2026-11-27 从 `pages/ai_page.py` 搬过来的。** 那个文件里原本还有一整个
「AI 运维大页」（`AiPage`，约 430 行）——它是侧栏助手（`ai_dock.py`）之前的
旧形态，改版后 `app.py` 一次都没引用过，属于点不到的死人。搬走这两个仍然
活着的控件之后，那个文件整体删掉了（R-006）。

搬出来的好处不只是少 430 行：以前改助手要同时惦记两个文件，模型/模式常量
两边各写一份，改一边忘一边就出现「侧栏显示自动、旧页显示每步确认」这种
自相矛盾。
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, ttk

import wbtui as U
from core import ai_skills, aiops
from ui import forms


def _open_in_shell(path: str) -> None:
    """用系统文件管理器打开目录（Windows 走 explorer，其它平台按各自命令）。"""
    import os
    import subprocess
    import sys
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])

class SkillPanel(tk.Frame):
    """技能库：看正文、装第三方技能、打开目录。

    技能只是文本 —— 这个面板里所有按钮都不执行技能目录里的任何脚本。

    做成 Frame 而不是 Toplevel：设置中心要把它当成一个内嵌页签
    （「智能体 / 模型 / 技能库 / MCP 库」），另开一个窗口会让人来回找。
    需要独立窗口时用下面的 SkillDialog 包一层。
    """

    def __init__(self, master, page, on_close=None):
        super().__init__(master, bg=U.T.page, bd=0)
        self.page = page
        self.store = page.ai_skill_store
        self.on_close = on_close
        self._items: list = []

        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(14), pady=U.px(12))

        head = U.wframe(wrap, "page")
        head.pack(fill="x")
        U.wlabel(head, "技能库", "page", U.FONT_T).pack(side="left")
        self.lbl_info = U.wlabel(head, "", "pagedim", U.FONT_XS)
        self.lbl_info.pack(side="left", padx=(U.px(10), 0))
        if on_close is not None:
            U.RoundButton(head, "关闭", on_close, variant="secondary",
                          height=30, padx=16).pack(side="right")

        bar = U.wframe(wrap, "page")
        bar.pack(fill="x", pady=(U.px(10), 0))
        U.RoundButton(bar, "导入技能…", self._import, variant="primary",
                      height=30, padx=14).pack(side="left")
        U.RoundButton(bar, "打开技能目录", self._open_dir, variant="ghost",
                      height=30, padx=14).pack(side="left", padx=(U.px(8), 0))
        U.RoundButton(bar, "刷新", self._reload, variant="ghost",
                      height=30, padx=14).pack(side="left", padx=(U.px(6), 0))
        self.btn_use = U.RoundButton(bar, "用这个技能", self._use, variant="ghost",
                                     height=30, padx=14)
        self.btn_use.pack(side="right")

        body = U.wframe(wrap, "page")
        body.pack(fill="both", expand=True, pady=(U.px(10), 0))

        left = U.Card(body)
        left.pack(side="left", fill="y")
        self.lst = tk.Listbox(left.body, width=30, height=14, bd=0,
                              relief="flat", highlightthickness=0,
                              activestyle="none", exportselection=False,
                              font=U.FONT_S, bg=U.T.card, fg=U.T.text,
                              selectbackground=U.T.brand_soft,
                              selectforeground=U.T.brand_deep)
        self.lst.pack(fill="both", expand=True, padx=U.px(2), pady=U.px(2))
        self.lst.bind("<<ListboxSelect>>", lambda e: self._preview())

        right = U.Card(body, title="正文")
        right.pack(side="left", fill="both", expand=True, padx=(U.px(10), 0))
        self.tb = U.TextBox(right.body, role="console", height=14, readonly=True)
        self.tb.pack(fill="both", expand=True)

        self._reload()

    # -- 数据
    def _reload(self):
        self._items = self.store.all() if hasattr(self.store, "all") else []
        self.lst.delete(0, "end")
        for s in self._items:
            self.lst.insert("end", f"[{s.source}] {s.name}")
        bad = getattr(self.store, "errors", [])
        txt = f"{len(self._items)} 个技能"
        if bad:
            txt += f" · {len(bad)} 个坏文件已跳过"
        self.lbl_info.configure(text=txt)
        if self._items:
            self.lst.selection_set(0)
            self._preview()

    def _sel(self):
        idx = self.lst.curselection()
        if not idx or idx[0] >= len(self._items):
            return None
        return self._items[idx[0]]

    def _preview(self):
        s = self._sel()
        if s is None:
            return
        head = (f"{s.name}　（{s.slug} · {s.source}）\n"
                f"{s.description or '—'}\n"
                f"标签：{('、'.join(s.tags) if s.tags else '—')}\n"
                f"文件：{s.path}\n"
                + "-" * 60 + "\n\n")
        self.tb.clear()
        self.tb.txt.insert("1.0", head + (s.body or "（正文为空）"))

    # -- 动作
    def _use(self):
        s = self._sel()
        if s is None:
            return
        page = self.page
        page.ai_cfg.data["skill"] = s.slug
        page.ai_cfg.save()
        page._ai_refresh_skills()
        page.v_ai_skill.set(next((lab for lab, slug in page._ai_skill_labels.items()
                                  if slug == s.slug), AUTO_LABEL))
        page.toast(f"已选用「{s.name}」", "ok")

    def _import(self):
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(), title="选择技能包",
            filetypes=[("技能包", "*.md *.zip"), ("Markdown", "*.md"),
                       ("Zip", "*.zip"), ("全部文件", "*.*")])
        if not path:
            return
        ok, msg = self.store.install(path)
        self.page.toast(msg, "ok" if ok else "err")
        if ok:
            self._reload()
            self.page._ai_refresh_skills()
        else:
            forms.InfoDialog.show(self, "装不上", msg + "\n\n"
                                  "技能包的入口文件必须是 SKILL.md，"
                                  "frontmatter 要有 name 字段。", width=720, height=280)

    def _open_dir(self):
        try:
            self.store.dir.mkdir(parents=True, exist_ok=True)
            _open_in_shell(str(self.store.dir))
        except Exception as e:  # noqa: BLE001
            self.page.toast(f"打不开目录：{e}", "err")


class SkillDialog(tk.Toplevel):
    """独立窗口形态的技能库（侧栏「技能库…」还用这个）。"""

    def __init__(self, parent, page):
        super().__init__(parent)
        self.title("技能库")
        self.configure(bg=U.T.page)
        self.transient(parent)
        self.panel = SkillPanel(self, page, on_close=self.destroy)
        self.panel.pack(fill="both", expand=True)
        self.geometry(f"{U.px(980)}x{U.px(600)}")
        self.minsize(U.px(720), U.px(420))
        self.bind("<Escape>", lambda e: self.destroy())
