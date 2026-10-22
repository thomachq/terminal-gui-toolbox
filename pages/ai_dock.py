# -*- coding: utf-8 -*-
"""智能运维助手 —— 右侧抽屉式的运维智能体（形态参考宝塔终端 / IDE 插件）。

为什么从「独立选项卡」改成「侧边栏」：

  把它放进导航里，它就只是「第 11 个页面」—— 想让它看你正在看的这页，
  得来回切；想让它顺手改一下当前设备，得先切回去。侧边栏是常驻的：
  左边该看什么看什么，右边随时能问，而且它能主动翻页、读表、点按钮。

四件套（都落在 data/agent.json）：

  名字 / 描述 —— 它是谁、能干什么，会拼进提示词
  提示词     —— 完整可改，占位符 {name} {desc} {tools} {context}
  技能       —— 勾选启用哪些 SOP；不勾就按关键词自动挑
  工具       —— 勾选允许它碰哪些能力。没勾的工具它会「看不见」，也没法调用

主机范围（多选）：

  侧栏底部有主机选择器：当前设备 / 全部主机 / 手选若干台。
  exec 会对范围内每台各跑一次（串行，同一台同时只跑一条命令）；
  模型也可以用它自己的 device 参数把某条命令定向到某一台 —— 跨机迁移就靠这个。
  默认**不是**「全部主机」：真运维里一条 rm 打到十台机器上是要命的。
"""

from __future__ import annotations

import base64
import shlex
import threading
import time
import tkinter as tk

import wbtui as U
from core import agent as A
from core import ai_skills, aiops, aitrace
from pages.model_page import ModelDialog
from ui import forms
from ui.md import MdView
from ui.table import Segmented

# ------------------------------------------------------------------ 常量
MODE_OPTS = [
    ("auto", "自动 · 只读与一般改动直接跑，高风险必问"),
    ("confirm", "逐条确认 · 凡有改动都先问"),
    ("readonly", "只读 · 只查不改"),
]
MODE_LABEL = dict(MODE_OPTS)
MODE_BY_LABEL = {v: k for k, v in MODE_OPTS}

AUTO_SKILL = "自动（按问题挑技能）"

# 空会话时的建议。写成「说人话的请求」，点一下就知道该怎么问 ——
# 比写一段「您可以这样问我：……」的说明有用得多。
SUGGESTIONS = [
    ("查磁盘占用", "哪块盘快满了、谁占的、inode 有没有爆",
     "查一下这台机器的磁盘占用，指出最该清理的目录"),
    ("跑一次安全体检", "开放端口、登录失败、可疑计划任务一起看",
     "把安全体检跑一遍，用一句话说结论"),
    ("排一下 Web 服务", "站点、域名绑定、证书到期时间",
     "看看现在有哪些 Web 站点，证书有没有快过期的"),
    ("把配置搬到另一台", "两端读取、比对、确认后才写",
     "把当前主机的 nginx 配置搬到另一台主机上"),
]

# 每页能触发的动作 —— 键是导航 key，值是 (动作 id, 按钮文案, 方法名, 风险)
# 新增页面时把动作加进来，助手就自动会用它，不用改提示词。
PAGE_META: dict[str, dict] = {
    "hosts": {"label": "主机管理", "table": "tbl_host",
              "actions": [("refresh", "刷新主机清单", "host_refresh", "read")]},
    "proxy": {"label": "代理池", "table": "tbl_proxy",
              "actions": [("refresh", "刷新代理列表", "proxy_refresh", "read")]},
    "cred": {"label": "凭证管理", "table": "tbl_cred",
             "actions": [("refresh", "刷新凭证列表", "cred_refresh", "read")]},
    "script": {"label": "脚本库", "table": "tbl_script",
               "actions": [("refresh", "刷新脚本库", "script_refresh", "read")]},
    "log": {"label": "日志管理", "table": "tbl_audit",
            "actions": [("refresh", "重新读取操作日志", "audit_reload", "read")]},
    "tools": {"label": "网络工具", "table": "tbl_tool", "actions": []},
    "sys": {"label": "系统信息", "table": "tbl_disk", "actions": []},
    "files": {"label": "文件管理", "table": "", "actions": []},
    "routes": {"label": "路由表", "table": "tbl_route",
               "actions": [("refresh", "重新读取路由表", "routes_refresh", "read")]},
    "sec": {"label": "安全中心", "table": "tbl_sec",
            "actions": [("scan", "跑一次安全体检", "sec_scan", "read")]},
    "docker": {"label": "Docker", "table": "tbl_dk",
               "actions": [("probe", "重新探测容器", "docker_probe", "read")]},
    "web": {"label": "Web 服务", "table": "tbl_site",
            "actions": [("probe", "重新探测 Web 服务", "web_probe", "read"),
                        ("sites", "读取站点与域名", "web_sites_load", "read")]},
    "db": {"label": "数据库", "table": "tbl_db",
           "actions": [("probe", "探测数据库实例", "db_probe", "read")]},
    "cron": {"label": "计划任务", "table": "tbl_cron",
             "actions": [("load", "重新读取计划任务", "cron_load", "read")]},
    "vpn": {"label": "隧道客户端证书管理", "table": "", "actions": []},
    "api": {"label": "智能体接入", "table": "tbl_sess",
            "actions": [("refresh", "刷新 API 状态", "api_refresh", "read")]},
}

ROW_LIMIT = 40          # page_read 最多回给模型多少行，多了它也用不上
RELAY_MAX = 512 * 1024  # relay 单文件上限（走内存中转，别搬大文件）
RELAY_CHUNK = 12000     # base64 分片长度：命令行有长度上限，必须切段写

LOCAL_SID = "__local__"  # 主机选择器里「本机」那一项的哨兵值


def _lines_to_text(lines: list[str], limit: int = 8) -> str:
    if len(lines) <= limit:
        return "\n".join(lines)
    return "\n".join(lines[:limit]) + f"\n…（共 {len(lines)} 行）"


class _Scroll(tk.Frame):
    """竖向滚动容器。tk 没有现成的「滚动画布」，只能 Canvas + 内嵌 Frame。

    滚轮用 bind_all 是为了让指针停在消息卡片上也能滚 —— 那些卡片是 Canvas 的
    子控件，事件不会冒泡到 Canvas。指针离开时立刻解绑，免得抢走别处的滚轮。
    """

    def __init__(self, master, bg: str):
        super().__init__(master, bg=bg, bd=0)
        self.cv = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vs = tk.Scrollbar(self, orient="vertical", command=self.cv.yview,
                               width=U.px(10), bd=0, relief="flat")
        self.cv.configure(yscrollcommand=self.vs.set)
        self.cv.pack(side="left", fill="both", expand=True)
        self.vs.pack(side="right", fill="y")
        self.inner = tk.Frame(self.cv, bg=bg, bd=0)
        self._win = self.cv.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._grow)
        self.cv.bind("<Configure>", self._fit)
        self.cv.bind("<Enter>", self._grab_wheel)
        self.cv.bind("<Leave>", self._drop_wheel)
        self.inner.bind("<Enter>", self._grab_wheel)
        self.inner.bind("<Leave>", self._drop_wheel)

    def _grow(self, _e=None):
        self.cv.configure(scrollregion=self.cv.bbox("all") or (0, 0, 0, 0))

    def _fit(self, e):
        self.cv.itemconfigure(self._win, width=e.width)

    def _grab_wheel(self, _e=None):
        self.cv.bind_all("<MouseWheel>", self._wheel)
        self.cv.bind_all("<Button-4>", self._wheel)
        self.cv.bind_all("<Button-5>", self._wheel)

    def _drop_wheel(self, _e=None):
        self.cv.unbind_all("<MouseWheel>")
        self.cv.unbind_all("<Button-4>")
        self.cv.unbind_all("<Button-5>")

    def _wheel(self, e):
        if getattr(e, "num", None) == 4:
            step = -3
        elif getattr(e, "num", None) == 5:
            step = 3
        else:
            step = -3 if e.delta > 0 else 3
        self.cv.yview_scroll(step, "units")
        return "break"

    def to_end(self) -> None:
        self.update_idletasks()
        self._grow()
        self.cv.yview_moveto(1.0)

    def clear(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self.cv.yview_moveto(0.0)


class AiDockPage:
    """侧栏：界面 + 工具落地 + 与主窗口/会话的桥接。"""

    # ================================================================ 构建
    def ai_dock_build(self, parent) -> tk.Frame:
        """造一次，之后开关只是 add / forget，不重建（会话内容要留住）。"""
        dock = U.wframe(parent, "page")
        dock.configure(width=U.px(self.agent_cfg.data.get("dock_width", 400)))
        dock.pack_propagate(False)
        self.ai_dock = dock
        self._ai_flow_labels: list[tk.Label] = []
        # Markdown 渲染出来的块：窗口变宽时它们不像 Label 那样自己重排，
        # 记下来，必要时整块重画（见 _ai_dock_reflow）
        self._ai_md_views: list = []
        self._ai_tool_cards: list[dict] = []

        # ---- 标题栏：头像 + 名字 + 状态；次要动作收进「⋯」，
        # 否则三个小按钮挤在右边，标题一长就被压没了。
        head = U.wframe(dock, "page")
        head.pack(fill="x", padx=U.px(12), pady=(U.px(10), U.px(6)))
        self.av_dock = self._ai_avatar(head, 22)
        self.av_dock.pack(side="left")
        self.lbl_dock_title = U.wlabel(head, self.agent_cfg.name, "page", U.FONT_T)
        self.lbl_dock_title.pack(side="left", padx=(U.px(8), 0))
        self.badge_dock = U.Pill(head, "待配置", "mute", height=22)
        self.badge_dock.pack(side="left", padx=(U.px(8), 0))

        self.btn_dock_close = U.RoundButton(head, "×", self.ai_dock_close,
                                            variant="ghost", font=U.FONT_S,
                                            height=24, padx=8)
        self.btn_dock_close.pack(side="right")
        self.btn_dock_more = U.RoundButton(head, "⋯", self._ai_more_menu,
                                           variant="ghost", font=U.FONT_S,
                                           height=24, padx=8)
        self.btn_dock_more.pack(side="right", padx=(0, U.px(2)))
        self.lbl_dock_tokens = U.wlabel(head, "", "mute", U.FONT_XS)
        self.lbl_dock_tokens.pack(side="right", padx=(U.px(6), U.px(8)))

        # 一条细线，把头与会话分开；不画的话顶部看着像浮在内容上
        self._ai_head_line = tk.Frame(dock, bg=U.T.border, height=U.px(1))
        self._ai_head_line.pack(fill="x", padx=U.px(10))
        U.register(self._ai_head_line,
                   lambda: self._ai_head_line.configure(bg=U.T.border))

        # ---- 消息流
        self._ai_scroll = _Scroll(dock, U.T.page)
        self._ai_scroll.pack(fill="both", expand=True, padx=U.px(8))
        self._ai_flow = self._ai_scroll.inner
        dock.bind("<Configure>", self._ai_dock_reflow)

        # ---- 输入区
        foot = U.wframe(dock, "page")
        foot.pack(fill="x", padx=U.px(10), pady=(U.px(6), U.px(8)))
        box = tk.Frame(foot, bg=U.T.card, bd=0, highlightthickness=1,
                       highlightbackground=U.T.border)
        box.pack(fill="x")

        self._ai_input = U.TextBox(box, role="card", height=3, readonly=False)
        self._ai_input.pack(fill="x", padx=U.px(6), pady=(U.px(6), 0))
        self._ai_input.txt.bind("<Control-Return>", self._ai_send_hotkey)

        bar = U.wframe(box, "card")
        bar.pack(fill="x", padx=U.px(6), pady=(U.px(4), U.px(6)))

        self.btn_ai_send = U.RoundButton(bar, "发送", self.ai_dock_send,
                                         variant="primary", font=U.FONT_XS,
                                         height=26, padx=14)
        self.btn_ai_send.pack(side="right")
        self.btn_ai_stop = U.RoundButton(bar, "停止", self.ai_dock_stop,
                                         variant="ghost", font=U.FONT_XS,
                                         height=26, padx=10)
        self.btn_ai_stop.pack(side="right", padx=(0, U.px(4)))

        # 会话宽度就这点，底部只留「＋」和一个模型名。
        # 主机范围 / 技能 / 模型管理 / 清空 全部收进 ＋ 里（WorkBuddy 的形态）。
        self.btn_ai_plus = U.RoundButton(bar, "＋", self._ai_plus_menu,
                                         variant="ghost", font=U.FONT_S,
                                         height=26, padx=10)
        self.btn_ai_plus.pack(side="left")
        # 这两个按钮不再占位，但对象保留：刷新逻辑还在更新它们的文案
        self.btn_ai_hosts = U.RoundButton(bar, "当前设备", self._ai_hosts_menu,
                                          variant="ghost", font=U.FONT_XS,
                                          height=26, padx=10)
        self.btn_ai_skill = U.RoundButton(bar, "技能", self._ai_skill_menu,
                                          variant="ghost", font=U.FONT_XS,
                                          height=26, padx=10)

        # 选模型：底部这一条最该有的东西。不切走页面就能看见「现在用的是谁」，
        # 点开就是一列可选模型（Cherry Studio / 宝塔的形态）。
        self.btn_ai_model = U.RoundButton(bar, "⌄ 模型", self._ai_model_menu,
                                          variant="ghost", font=U.FONT_XS,
                                          height=26, padx=10)
        self.btn_ai_model.pack(side="left", padx=(U.px(4), 0))

        self._ai_scope_all = False
        self._ai_scope_sids: list[str] = []   # sid 或 LOCAL_SID
        self._ai_skill_now = ""
        self._ai_pending = None
        self._ai_paint_welcome()
        self._ai_refresh_dock_state()
        return dock

    def _ai_paint_welcome(self) -> None:
        """空会话的引导页。

        不是把说明写成一坨灰字 —— 三条建议是**能点的**，点一下直接发出去。
        对着空白输入框想「我该问什么」是这类助手最容易劝退人的地方。
        """
        pad = U.px(14)
        box = tk.Frame(self._ai_flow, bg=U.T.page)
        box.pack(fill="x", padx=pad, pady=(U.px(22), U.px(6)))
        self.av_welcome = self._ai_avatar(box, 34)
        self.av_welcome.pack(anchor="w")
        self.lbl_welcome_title = tk.Label(
            box, text=self.agent_cfg.name, bg=U.T.page, fg=U.T.text,
            font=(U.FONT_FAMILY, 13, "bold"), bd=0, anchor="w", justify="left")
        self.lbl_welcome_title.pack(fill="x", pady=(U.px(10), U.px(3)))
        sub = ("我能读这台机器，也能操作左边所有页面 —— 该你点的按钮，"
               "我替你点；该看的输出，我读完给结论。")
        self.lbl_welcome_sub = tk.Label(
            box, text=sub, bg=U.T.page, fg=U.T.text_dim, font=U.FONT_S,
            bd=0, anchor="w", justify="left",
            wraplength=max(U.px(180), (self.ai_dock.winfo_width() or U.px(400))
                           - U.px(46)))
        self.lbl_welcome_sub.pack(fill="x")
        self._ai_flow_labels.append(self.lbl_welcome_sub)

        hint = tk.Frame(self._ai_flow, bg=U.T.page)
        hint.pack(fill="x", padx=pad, pady=(U.px(14), U.px(2)))
        tk.Label(hint, text="试试这些", bg=U.T.page, fg=U.T.text_mute,
                 font=U.FONT_XS, bd=0, anchor="w").pack(fill="x")

        self._ai_suggest_cards = []
        for title, desc, prompt in SUGGESTIONS:
            self._ai_suggest_card(title, desc, prompt)

        tail = ("危险动作会先问你；命令、输出和审批记录都留在「日志管理」里，"
                "事后能翻。")
        self.lbl_welcome_tail = tk.Label(
            self._ai_flow, text=tail, bg=U.T.page, fg=U.T.text_mute,
            font=U.FONT_XS, bd=0, anchor="w", justify="left",
            wraplength=max(U.px(180), (self.ai_dock.winfo_width() or U.px(400))
                           - U.px(46)))
        self.lbl_welcome_tail.pack(fill="x", padx=pad, pady=(U.px(12), U.px(10)))
        self._ai_flow_labels.append(self.lbl_welcome_tail)
        # 记下引导页占用的控件：发出第一条消息时整块收起来，
        # 否则一屏里既有一堆「试试这些」又有对话，看着像没进入状态
        self._ai_welcome_widgets = list(self._ai_flow.winfo_children())

    # ------------------------------------------------------------ 欢迎块的小件
    def _ai_avatar(self, parent, size: int = 22) -> tk.Canvas:
        """圆形 + 「AI」两个字。不用图片：换主题时重画一次就行，
        也不会因为缺图标文件变成一块空白。"""
        px = U.px(size)
        cv = tk.Canvas(parent, width=px, height=px,
                       bg=parent.cget("bg"), highlightthickness=0, bd=0)
        cv.create_oval(0, 0, px - 1, px - 1, fill=U.T.brand, outline="",
                       tags="disc")
        cv.create_text(px / 2, px / 2, text="AI", fill="#FFFFFF",
                       font=(U.FONT_FAMILY, max(7, int(size * 0.36)), "bold"),
                       tags="glyph")

        def paint():
            try:
                cv.configure(bg=parent.cget("bg"))
                cv.itemconfigure("disc", fill=U.T.brand)
            except tk.TclError:
                pass
        U.register(cv, paint)
        return cv

    def _ai_suggest_card(self, title: str, desc: str, prompt: str) -> None:
        """一张可点的建议卡。悬停变边框色，点了直接发出去。"""
        pad = U.px(14)
        outer = tk.Frame(self._ai_flow, bg=U.T.page)
        outer.pack(fill="x", padx=pad, pady=U.px(3))
        card = tk.Frame(outer, bg=U.T.card, bd=0, highlightthickness=1,
                        highlightbackground=U.T.border, cursor="hand2")
        card.pack(fill="x")
        inner = tk.Frame(card, bg=U.T.card)
        inner.pack(fill="x", padx=U.px(11), pady=U.px(8))
        row = tk.Frame(inner, bg=U.T.card)
        row.pack(fill="x")
        tk.Label(row, text=title, bg=U.T.card, fg=U.T.text, font=U.FONT_S,
                 bd=0, anchor="w").pack(side="left")
        tk.Label(row, text="›", bg=U.T.card, fg=U.T.text_mute, bd=0,
                 font=U.FONT_S).pack(side="right")
        lb = tk.Label(inner, text=desc, bg=U.T.card, fg=U.T.text_mute,
                      font=U.FONT_XS, bd=0, anchor="w", justify="left",
                      wraplength=max(U.px(150),
                                     (self.ai_dock.winfo_width() or U.px(400))
                                     - U.px(80)))
        lb.pack(fill="x", pady=(U.px(2), 0))

        def fire(_e=None):
            self._ai_use_suggestion(prompt)

        for w in (card, inner, row) + tuple(row.winfo_children()) + (lb,):
            w.bind("<Button-1>", fire)
        for w in (card, inner, row) + tuple(row.winfo_children()) + (lb,):
            w.bind("<Enter>", lambda e: card.configure(
                highlightbackground=U.T.brand))
            w.bind("<Leave>", lambda e: card.configure(
                highlightbackground=U.T.border))
        self._ai_flow_labels.append(lb)

    def _ai_use_suggestion(self, prompt: str) -> None:
        """点建议卡：填进输入框并直接发 —— 用户点它就是要这个。"""
        try:
            self._ai_input.set_text(prompt)
        except (AttributeError, tk.TclError):
            try:
                self._ai_input.txt.delete("1.0", "end")
                self._ai_input.txt.insert("1.0", prompt)
            except (AttributeError, tk.TclError):
                return
        self.ai_dock_send()

    def _ai_more_menu(self) -> None:
        rows = [
            ("模型设置", self.ai_model_settings),
            ("助手设置（人设 / 技能 / 工具）", self.ai_agent_settings),
            ("清空会话", self.ai_dock_clear),
            None,
            ("收起助手", self.ai_dock_close),
        ]

        def build(host):
            for r in rows:
                if r is None:
                    tk.Frame(host, bg=U.T.border, height=U.px(1)).pack(
                        fill="x", padx=U.px(8), pady=U.px(4))
                    continue
                label, cmd = r
                row = tk.Frame(host, bg=U.T.card, cursor="hand2")
                row.pack(fill="x", padx=U.px(6), pady=U.px(1))
                lb = tk.Label(row, text=label, bg=U.T.card, fg=U.T.text,
                              font=U.FONT_S, anchor="w", padx=U.px(8),
                              pady=U.px(6))
                lb.pack(fill="x")

                def act(_e=None, c=cmd, h=host):
                    try:
                        h.winfo_toplevel().destroy()
                    except tk.TclError:
                        pass
                    c()

                for w in (row, lb):
                    w.bind("<Button-1>", act)
                    w.bind("<Enter>", lambda e, r0=row, l0=lb: (
                        r0.configure(bg=U.T.brand_soft),
                        l0.configure(bg=U.T.brand_soft)))
                    w.bind("<Leave>", lambda e, r0=row, l0=lb: (
                        r0.configure(bg=U.T.card),
                        l0.configure(bg=U.T.card)))
        _popup(self, self.btn_dock_more, build, width=U.px(230))

    # ================================================================ 开关
    def ai_dock_toggle(self) -> None:
        if self._ai_dock_on:
            self.ai_dock_close()
        else:
            self.ai_dock_open()

    def ai_dock_open(self) -> None:
        if not getattr(self, "ai_dock", None):
            self.ai_dock_build(self.paned)
        # 助手和命令日志是同一类东西（都是面板），抢的是同一块地方。
        # 窗口不够宽时先收起日志栏，否则三个面板加起来超过窗口宽度，
        # 最右边的助手会被挤成 0 宽 —— 点了按钮却看不到面板。
        self._ai_log_hidden = False
        try:
            width = self.paned.winfo_width() or self.winfo_width()
        except tk.TclError:
            width = 0
        # 620（页面）+ 320（日志）+ 400（助手）+ 分隔条余量
        if width and width < U.px(1460):
            try:
                if str(getattr(self, "log_dock", None)) in [
                        str(p) for p in self.paned.panes()]:
                    self.paned.forget(self.log_dock)
                    self._ai_log_hidden = True
            except (tk.TclError, AttributeError):
                pass
        try:
            if str(self.ai_dock) not in [str(p) for p in self.paned.panes()]:
                self.paned.add(self.ai_dock, stretch="never",
                               minsize=U.px(300))
            # 分隔条要能一眼看出「这里可以拖」：左右箭头光标 + 松手记宽度
            self.paned.configure(sashcursor="sb_h_double_arrow")
            self.paned.bind("<ButtonRelease-1>", self._ai_sash_done, add="+")
        except tk.TclError:
            return
        self._ai_dock_on = True
        self.agent_cfg.data["dock_open"] = True
        self.agent_cfg.save()
        self.btn_ai_dock.set_text("收起助手")
        # 摆两次：60ms 那次常碰上 paned 还没撑满（宽度小就跳过），
        # 300ms 再摆一次，侧栏宽度才是记忆里的那个值而不是内容要多少给多少
        self.after(60, self._ai_dock_sash)
        self.after(300, self._ai_dock_sash)
        self.after(120, lambda: self._ai_input.txt.focus_set())

    def ai_dock_close(self) -> None:
        if not getattr(self, "ai_dock", None):
            return
        try:
            self.paned.forget(self.ai_dock)
        except tk.TclError:
            pass
        self._ai_dock_on = False
        self.agent_cfg.data["dock_open"] = False
        self.agent_cfg.save()
        # 刚才为了腾地方收起的日志栏，现在放回去
        if getattr(self, "_ai_log_hidden", False):
            try:
                if str(self.log_dock) not in [str(p) for p in self.paned.panes()]:
                    self.paned.add(self.log_dock, stretch="never",
                                   minsize=U.px(320))
            except (tk.TclError, AttributeError):
                pass
            self._ai_log_hidden = False
        self.btn_ai_dock.set_text("智能运维助手")

    def _ai_dock_sash(self) -> None:
        """把侧栏拉到设定宽度，日志栏让位但仍然留够。"""
        try:
            total = self.paned.winfo_width()
            # 宽度是可拖的（用户要求）。这里只夹在「窄到不可用」和
            # 「宽到把终端挤成一条缝」之间，正常的拖动结果原样尊重。
            want = U.px(int(self.agent_cfg.data.get("dock_width") or 400))
            want = max(U.px(300), min(want, U.px(720)))
            # Tk 的 PanedWindow 没有 index()，只能从 panes() 里自己数
            panes = [str(p) for p in self.paned.panes()]
            idx = panes.index(str(self.ai_dock)) - 1
            if total > want + U.px(420) and idx >= 0:
                self.paned.sash_place(idx, total - want, 0)
        except tk.TclError:
            pass

    def _ai_sash_done(self, _e=None) -> None:
        """用户拖完分隔条：把当前宽度记下来，下次打开还是这个宽度。"""
        if not getattr(self, "_ai_dock_on", False):
            return
        try:
            w = self.ai_dock.winfo_width()
        except tk.TclError:
            return
        if w < U.px(240):
            return
        self.agent_cfg.data["dock_width"] = int(round(w / max(0.5, U.S)))
        self.agent_cfg.save()

    def _ai_dock_reflow(self, event=None) -> None:
        """侧栏宽度变了，消息卡片跟着重排 —— 否则文字会一直按旧宽度折行。"""
        w = (event.width if event is not None else self.ai_dock.winfo_width())
        wrap = max(U.px(180), w - U.px(46))
        for lb in self._ai_flow_labels:
            try:
                lb.configure(wraplength=wrap)
            except tk.TclError:
                pass
        if event is not None and event.width > U.px(340):
            self.agent_cfg.data["dock_width"] = int(w)
            self.agent_cfg._clean()

    # ================================================================ 卡片
    def _ai_label(self, parent, text: str, fg: str, font, wrap: bool = True):
        lb = tk.Label(parent, text=text, bg=parent.cget("bg"), fg=fg, font=font,
                      justify="left", anchor="w", bd=0,
                      wraplength=max(U.px(180),
                                     (self.ai_dock.winfo_width() or U.px(400))
                                     - U.px(46)) if wrap else 0)
        return lb

    def _ai_note(self, text: str, tone: str = "mute") -> None:
        fg = {"mute": U.T.text_mute, "dim": U.T.text_dim,
              "ok": U.T.ok, "warn": U.T.warn, "err": U.T.err}.get(tone, U.T.text_dim)
        wrap = tk.Frame(self._ai_flow, bg=U.T.page)
        wrap.pack(fill="x", pady=(U.px(3), U.px(3)), padx=U.px(4))
        lb = self._ai_label(wrap, text, fg, U.FONT_XS)
        lb.pack(fill="x")
        self._ai_flow_labels.append(lb)

    def _ai_hide_welcome(self) -> None:
        """第一条消息一来就把引导页收掉（只收一次）。"""
        for w in getattr(self, "_ai_welcome_widgets", []):
            try:
                w.pack_forget()
            except tk.TclError:
                pass
        self._ai_welcome_widgets = []

    def _ai_bubble(self, text: str) -> None:
        """用户消息：靠右的浅色块，和助手内容区分开。

        左边留出一大段空白是刻意的 —— 宽度差一眼就能看出「谁说的」，
        不用额外画头像。
        """
        self._ai_hide_welcome()
        outer = tk.Frame(self._ai_flow, bg=U.T.page)
        outer.pack(fill="x", pady=(U.px(12), U.px(6)))
        box = tk.Frame(outer, bg=U.T.brand_soft, bd=0)
        box.pack(side="right", anchor="e", padx=(U.px(46), U.px(4)))
        lb = tk.Label(box, text=text, bg=U.T.brand_soft, fg=U.T.text,
                      font=U.FONT_S, justify="left", anchor="w",
                      wraplength=max(U.px(150),
                                     (self.ai_dock.winfo_width() or U.px(400))
                                     - U.px(110)),
                      padx=U.px(12), pady=U.px(8))
        lb.pack()
        self._ai_flow_labels.append(lb)

    def _ai_reply(self, text: str, step: int | None = None) -> None:
        """助手正文：左边头像 + 名字，正文按 Markdown 渲染后铺在页面上。

        正文不套卡片边框 —— 每句话都框一层，一屏下来全是方框，重点反而没了。
        需要「这属于一次动作」的是工具调用，那边才用卡片。
        加粗、行内代码、列表、代码块都由 ui.md 拆开渲染，否则一屏星号。
        """
        row = tk.Frame(self._ai_flow, bg=U.T.page)
        row.pack(fill="x", pady=(U.px(10), U.px(4)))
        av = self._ai_avatar(row, 22)
        av.pack(side="left", anchor="n", padx=(U.px(4), 0), pady=(U.px(1), 0))
        cell = tk.Frame(row, bg=U.T.page)
        cell.pack(side="left", fill="x", expand=True, padx=(U.px(9), U.px(6)))

        top = tk.Frame(cell, bg=U.T.page)
        top.pack(fill="x")
        tk.Label(top, text=self.agent_cfg.name, bg=U.T.page, fg=U.T.text,
                 font=(U.FONT_FAMILY, U.FONT_S[1], "bold"), bd=0).pack(
            side="left")
        if step:
            tk.Label(top, text=f"第 {step} 步", bg=U.T.page, fg=U.T.text_mute,
                     font=U.FONT_XS, bd=0).pack(side="left",
                                                padx=(U.px(6), 0))

        # 动作条放在名字右边、低对比度：要能点到，但不该抢正文的注意力
        acts = tk.Frame(top, bg=U.T.page)
        acts.pack(side="right")

        def act_btn(label, fn):
            lb = tk.Label(acts, text=label, bg=U.T.page, fg=U.T.text_mute,
                          font=U.FONT_XS, bd=0, cursor="hand2",
                          padx=U.px(4))
            lb.pack(side="right", padx=(U.px(2), 0))
            lb.bind("<Button-1>", lambda e: fn())
            lb.bind("<Enter>", lambda e: lb.configure(fg=U.T.brand))
            lb.bind("<Leave>", lambda e: lb.configure(fg=U.T.text_mute))
            return lb

        def copy_reply():
            try:
                self.clipboard_clear()
                self.clipboard_append(text)
                self.toast("已复制这条回复", "ok")
            except tk.TclError:
                pass

        act_btn("复制", copy_reply)

        wrap = max(U.px(160), (self.ai_dock.winfo_width() or U.px(400))
                   - U.px(80))
        md = MdView(cell, text, wrap_px=wrap, bg=U.T.page, fg=U.T.text,
                    font=U.FONT_S, mono=U.MONO_S, on_link=self._ai_open_link)
        md.frame.pack(fill="x", pady=(U.px(4), 0))
        self._ai_md_views.append(md)
        self._ai_scroll.to_end()

    def _ai_open_link(self, url: str) -> None:
        """点正文里的链接：先给用户看一眼去哪，再交给系统浏览器。"""
        if not forms.confirm(self, "打开链接", f"要在浏览器里打开这个地址吗？\n\n{url}",
                             ok_text="打开"):
            return
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception as e:  # noqa: BLE001
            self.toast(f"打不开：{e}", "err")

    def _ai_tool_card(self, call: dict) -> dict:
        """一次工具调用：标题行（工具 / 风险 / 状态）+ 代码块参数 + 可折叠输出。

        卡片左缩进与助手正文对齐，让人一眼看出「这是助手做的一件事」，
        而不是一条独立消息。
        """
        tid = call.get("tool") or "?"
        spec = A.TOOLS.get(tid)
        risk = call.get("risk") or "read"
        outer = tk.Frame(self._ai_flow, bg=U.T.page)
        outer.pack(fill="x", pady=(U.px(1), U.px(8)))
        card = tk.Frame(outer, bg=U.T.card, bd=0, highlightthickness=1,
                        highlightbackground=U.T.border)
        card.pack(fill="x", padx=(U.px(28), U.px(6)))

        head = tk.Frame(card, bg=U.T.card)
        head.pack(fill="x", padx=U.px(10), pady=(U.px(7), 0))
        tk.Label(head, text="⚙", bg=U.T.card, fg=U.T.brand, font=U.FONT_XS,
                 bd=0).pack(side="left")
        tk.Label(head, text=(spec.label if spec else tid), bg=U.T.card,
                 fg=U.T.text, font=U.FONT_S, bd=0).pack(side="left",
                                                       padx=(U.px(5), 0))
        U.Pill(head, aiops.RISK_LABEL.get(risk, risk),
               aiops.RISK_TONE.get(risk, "mute"), height=18).pack(
            side="left", padx=(U.px(6), 0))
        st = tk.Label(head, text="运行中…", bg=U.T.card, fg=U.T.text_mute,
                      font=U.FONT_XS, bd=0)
        st.pack(side="right")

        args = call.get("args") or {}
        if tid == "exec":
            body = args.get("cmd", "")
        elif tid == "relay":
            body = (f"{args.get('src', '?')}:{args.get('src_path', '?')}"
                    f"  →  {args.get('dst', '?')}:{args.get('dst_path', '?')}")
        else:
            body = " ".join(str(v) for v in args.values())
        if body:
            code = tk.Frame(card, bg=U.T.console_bg, bd=0)
            code.pack(fill="x", padx=U.px(10), pady=(U.px(6), U.px(2)))
            lb = tk.Label(code, text=body, bg=U.T.console_bg, fg=U.T.console_fg,
                          font=U.MONO_S, justify="left", anchor="w",
                          wraplength=max(U.px(150),
                                         (self.ai_dock.winfo_width() or U.px(400))
                                         - U.px(96)),
                          padx=U.px(8), pady=U.px(6))
            lb.pack(fill="x")
            self._ai_flow_labels.append(lb)
        why = call.get("why") or ""
        if why:
            lb = tk.Label(card, text=why, bg=U.T.card, fg=U.T.text_mute,
                          font=U.FONT_XS, justify="left", anchor="w",
                          wraplength=max(U.px(150),
                                         (self.ai_dock.winfo_width() or U.px(400))
                                         - U.px(96)),
                          padx=U.px(10), pady=U.px(2))
            lb.pack(fill="x")
            self._ai_flow_labels.append(lb)

        rec = {"card": card, "call": call, "full": "", "shown": False,
               "label": None, "footer": None, "status": st}
        self._ai_tool_cards.append(rec)
        self._ai_scroll.to_end()
        return rec

    def _ai_tool_result(self, rec: dict, ok: bool, text: str) -> None:
        card = rec["card"]
        rec["full"] = text
        try:
            rec["status"].configure(text=("完成" if ok else "失败"),
                                    fg=U.T.ok if ok else U.T.err)
        except (KeyError, tk.TclError):
            pass

        foot = tk.Frame(card, bg=U.T.card)
        foot.pack(fill="x", padx=U.px(10), pady=(U.px(4), U.px(8)))
        bar = tk.Frame(foot, bg=U.T.card)
        bar.pack(fill="x")
        n = len(text.splitlines())
        tk.Label(bar, text=f"{'输出' if ok else '错误输出'} · {n} 行",
                 bg=U.T.card, fg=U.T.text_mute, font=U.FONT_XS,
                 bd=0).pack(side="left")
        if n > 8:
            U.RoundButton(bar, "展开", lambda r=rec: self._ai_tool_toggle(r),
                          variant="ghost", font=U.FONT_XS, height=20,
                          padx=8).pack(side="right")
        U.RoundButton(bar, "复制", lambda r=rec: self.copy_text(r["full"], "输出"),
                      variant="ghost", font=U.FONT_XS, height=20,
                      padx=8).pack(side="right", padx=(0, U.px(4)))

        box = tk.Frame(foot, bg=U.T.console_bg, bd=0)
        box.pack(fill="x", pady=(U.px(4), 0))
        lb = tk.Label(box, text=_lines_to_text(text.splitlines()),
                      bg=U.T.console_bg, fg=U.T.console_fg, font=U.MONO_S,
                      justify="left", anchor="w",
                      wraplength=max(U.px(150),
                                     (self.ai_dock.winfo_width() or U.px(400))
                                     - U.px(96)),
                      padx=U.px(8), pady=U.px(6))
        lb.pack(fill="x")
        rec["label"] = lb
        rec["footer"] = foot
        self._ai_flow_labels.append(lb)
        self._ai_scroll.to_end()

    def _ai_tool_toggle(self, rec: dict) -> None:
        lb = rec.get("label")
        if lb is None:
            return
        rec["shown"] = not rec["shown"]
        body = rec["full"] if rec["shown"] else _lines_to_text(
            rec["full"].splitlines())
        lb.configure(text=body)

    # ================================================================ 状态
    def _ai_refresh_dock_state(self) -> None:
        if not getattr(self, "ai_dock", None):
            return
        try:
            self.lbl_dock_title.configure(text=self.agent_cfg.name)
            cfg = self.ai_cfg
            if cfg.preset().get("kind") == "dsh":
                ok = bool(aiops.find_dsh())
            else:
                ok = bool(cfg.get_key())
            self.badge_dock.set("就绪" if ok else "缺 Key", "ok" if ok else "warn")
            self.lbl_dock_tokens.configure(
                text=f"{self.agent.tokens} tok" if self.agent.tokens else "")
            try:
                self.btn_ai_model.set_text(self._ai_model_label())
            except (AttributeError, tk.TclError):
                pass
            self._ai_refresh_scope_btn()
        except tk.TclError:
            pass

    def _ai_refresh_scope_btn(self) -> None:
        btn = getattr(self, "btn_ai_hosts", None)
        if btn is None:
            return
        if self._ai_scope_all:
            text = "全部主机"
        elif self._ai_scope_sids:
            text = (self._ai_slot_label(self._ai_scope_sids[0])
                    if len(self._ai_scope_sids) == 1
                    else f"{len(self._ai_scope_sids)} 台主机")
        else:
            text = "当前设备"
        try:
            btn.set_text(text)
        except tk.TclError:
            pass
        lbl = getattr(self, "btn_ai_skill", None)
        if lbl is not None:
            lbl.set_text(f"技能 · {self._ai_skill_now}" if self._ai_skill_now else "技能")

    def _ai_slot_label(self, sid: str) -> str:
        if sid == LOCAL_SID:
            return "本机"
        slot = self.sessions.get(sid)
        if slot is None:
            return "已断开"
        return slot.label or sid

    # ================================================================ 主机范围
    def _ai_hosts_menu(self) -> None:
        """主机选择浮层：当前设备 / 全部主机 / 本机 / 逐台勾选。

        「当前设备」和「全部主机」互斥，勾主机则自动取消这两项；
        一个都不勾就退回「当前设备」—— 空白状态不能是一种可执行状态。
        """
        slots = list(self.sessions)

        def build(host):
            cur_all = self._ai_scope_all
            picked = set(self._ai_scope_sids)
            rows = [("cur", "当前设备", "跟随左侧：切到哪台就作用于哪台"),
                    ("all", "全部主机", f"{len(slots) + 1} 台（含本机）"),
                    (LOCAL_SID, "本机", "运行工具箱的这台机器")]
            for s in slots:
                rows.append((s.sid, s.label or s.sid,
                             getattr(s.session, "host", "") or
                             ("在线" if s.alive() else "离线")))
            vars_: dict[str, tk.BooleanVar] = {}

            def sync():
                if vars_["all"].get():
                    self._ai_scope_all = True
                    self._ai_scope_sids = []
                else:
                    self._ai_scope_all = False
                    self._ai_scope_sids = [
                        k for k, v in vars_.items()
                        if k not in ("cur", "all") and v.get()]
                self._ai_refresh_scope_btn()

            def only(key):
                def hook():
                    if vars_[key].get():
                        for k, v in vars_.items():
                            if k != key:
                                v.set(False)
                    else:
                        vars_["cur"].set(True)
                    sync()
                return hook

            for sid, label, sub in rows:
                on = ((sid == "cur" and not cur_all and not picked)
                      or (sid == "all" and cur_all)
                      or (sid not in ("cur", "all") and sid in picked))
                var = tk.BooleanVar(value=on)
                vars_[sid] = var
                row = tk.Frame(host, bg=U.T.card)
                row.pack(fill="x", padx=U.px(8), pady=U.px(2))
                U.Check(row, "", variable=var, command=only(sid)).pack(
                    side="left", anchor="n")
                cell = tk.Frame(row, bg=U.T.card)
                cell.pack(side="left", fill="x", expand=True, padx=(U.px(4), 0))
                tk.Label(cell, text=label, bg=U.T.card, fg=U.T.text,
                         font=U.FONT_S, anchor="w").pack(fill="x")
                tk.Label(cell, text=sub, bg=U.T.card, fg=U.T.text_mute,
                         font=U.FONT_XS, anchor="w").pack(fill="x")

        _popup(self, self.btn_ai_hosts, build, width=U.px(250))

    def _ai_targets(self) -> list[tuple[str, object]]:
        """按主机范围给出 [(显示名, session)]。默认只有当前设备那一台。"""
        if self._ai_scope_all:
            out: list[tuple[str, object]] = [
                (s.label or s.sid, s.session) for s in self.sessions if s.alive()]
            out.append(("本机", self._ai_local_session()))
            return out
        if self._ai_scope_sids:
            out = []
            for sid in self._ai_scope_sids:
                if sid == LOCAL_SID:
                    out.append(("本机", self._ai_local_session()))
                    continue
                slot = self.sessions.get(sid)
                if slot is not None and slot.alive():
                    out.append((slot.label or sid, slot.session))
            if out:
                return out
        return [("当前设备", self._active_session())]

    def _ai_local_session(self):
        if not getattr(self, "_local", None):
            from core.session import LocalSession
            self._local = LocalSession()
            try:
                self._local.tracer = self._trace_cmd
            except AttributeError:
                pass
        return self._local

    def _ai_plus_menu(self) -> None:
        """底部「＋」：主机范围 / 技能 / 模型管理 / 清空，一次收进去。

        会话窄，两个常驻 chip 就要占掉半行；收进 ＋ 之后底部只剩
        「＋ · 模型 · 发送」，一眼能看清当前用哪个模型。
        """
        holder: dict = {}

        def build(host):
            rows = [
                ("主机范围", self._ai_scope_label(), self._ai_hosts_menu),
                ("技能", self._ai_skill_now or "自动", self._ai_skill_menu),
                ("模型管理", "", self.ai_model_settings),
                ("清空会话", "", self.ai_dock_clear),
            ]
            for name, hint, cmd in rows:
                row = tk.Frame(host, bg=U.T.card, cursor="hand2")
                row.pack(fill="x", padx=U.px(6), pady=U.px(1))
                lb = tk.Label(row, text=name, bg=U.T.card, fg=U.T.text,
                              font=U.FONT_S, anchor="w", padx=U.px(8),
                              pady=U.px(7))
                lb.pack(side="left")
                if hint:
                    tk.Label(row, text=hint, bg=U.T.card, fg=U.T.text_mute,
                             font=U.FONT_XS, bd=0).pack(side="right",
                                                        padx=U.px(8))

                def act(_e=None, c=cmd):
                    try:
                        holder["top"].destroy()
                    except (KeyError, tk.TclError):
                        pass
                    c()

                for w in (row, lb):
                    w.bind("<Button-1>", act)
                for w in (row, lb):
                    w.bind("<Enter>", lambda e, r0=row, l0=lb: (
                        r0.configure(bg=U.T.brand_soft),
                        l0.configure(bg=U.T.brand_soft)))
                    w.bind("<Leave>", lambda e, r0=row, l0=lb: (
                        r0.configure(bg=U.T.card), l0.configure(bg=U.T.card)))

        top = _popup(self, self.btn_ai_plus, build, width=U.px(240))
        holder["top"] = top
        return top

    def _ai_scope_label(self) -> str:
        if self._ai_scope_all:
            return "全部主机"
        if self._ai_scope_sids:
            return f"{len(self._ai_scope_sids)} 台"
        return "当前设备"

    # ================================================================ 选模型
    def _ai_model_now(self) -> dict:
        try:
            return self.ai_cfg.current() or {}
        except Exception:  # noqa: BLE001
            return {}

    def _ai_model_label(self) -> str:
        m = self._ai_model_now()
        name = str(m.get("name") or m.get("model") or "模型")
        if len(name) > 12:
            name = name[:11] + "…"
        return "⌄ " + name

    def _ai_model_menu(self) -> None:
        """底部「⌄ 模型」：一列可选模型，点谁用谁。

        形态照抄 Cherry Studio 的输入框右下角 + 宝塔的模型浮层：能搜、当前打勾、
        底下给一个「模型管理…」的入口。切模型就等于把那条档案写回扁平字段，
        引擎下一次调用直接用新的 —— 不用重启，也不用回到设置页。
        """
        cfg = self.ai_cfg
        models = cfg.models()
        holder: dict = {}

        def build(host):
            box = tk.Frame(host, bg=U.T.card)
            box.pack(fill="both", expand=True, padx=U.px(6), pady=U.px(6))

            head = tk.Frame(box, bg=U.T.card)
            head.pack(fill="x", padx=U.px(2))
            tk.Label(head, text="选择模型", bg=U.T.card, fg=U.T.text,
                     font=U.FONT_S, bd=0).pack(side="left")
            tk.Label(head, text=f"{len(models)} 个", bg=U.T.card,
                     fg=U.T.text_mute, font=U.FONT_XS, bd=0).pack(side="right")

            sr = tk.Frame(box, bg=U.T.input_bg, bd=0, highlightthickness=1,
                          highlightbackground=U.T.input_border)
            sr.pack(fill="x", pady=(U.px(6), U.px(4)))
            var = tk.StringVar()
            ent = tk.Entry(sr, textvariable=var, bd=0, bg=U.T.input_bg,
                           fg=U.T.text, insertbackground=U.T.text,
                           font=U.FONT_S, highlightthickness=0)
            ent.pack(fill="x", padx=U.px(8), pady=U.px(5))
            # 占位提示：Entry 没有原生 placeholder，用一层灰字盖住，输入即消失
            ph = tk.Label(sr, text="搜索模型", bg=U.T.input_bg,
                          fg=U.T.text_mute, font=U.FONT_XS, bd=0)
            ph.place(x=U.px(8), rely=0.5, anchor="w")

            def _ph(*_a):
                try:
                    if var.get():
                        ph.place_forget()
                    else:
                        ph.place(x=U.px(8), rely=0.5, anchor="w")
                except tk.TclError:
                    pass

            ent.bind("<KeyRelease>", _ph)
            ph.bind("<Button-1>", lambda e: ent.focus_set())

            holder["ent"] = ent
            rows = tk.Frame(box, bg=U.T.card)
            rows.pack(fill="both", expand=True)

            def render(*_a):
                for w in rows.winfo_children():
                    w.destroy()
                q = var.get().strip().lower()
                hit = 0
                for m in models:
                    v = aiops.model_view(m, cfg.current_id())
                    hay = f"{v['name']} {v['model']} {v['provider']}".lower()
                    if q and q not in hay:
                        continue
                    self._ai_model_row(rows, v, holder)
                    hit += 1
                if not hit:
                    tk.Label(rows, text="没有匹配的模型", bg=U.T.card,
                             fg=U.T.text_mute, font=U.FONT_XS,
                             bd=0).pack(pady=U.px(12))
                try:
                    holder["top"].geometry("")
                except (KeyError, tk.TclError):
                    pass

            var.trace_add("write", render)
            render()

            foot = tk.Frame(box, bg=U.T.card)
            foot.pack(fill="x", pady=(U.px(2), 0))
            more = tk.Label(foot, text="模型管理…", bg=U.T.card, fg=U.T.brand,
                            font=U.FONT_XS, bd=0, cursor="hand2")
            more.pack(side="right", padx=U.px(2))
            more.bind("<Button-1>", lambda e: (holder["top"].destroy(),
                                                self.ai_model_settings()))

        top = _popup(self, self.btn_ai_model, build, width=U.px(300))
        holder["top"] = top
        try:
            holder["ent"].focus_set()
        except (KeyError, tk.TclError):
            pass
        return top

    def _ai_model_row(self, parent, v: dict, holder: dict) -> None:
        """浮层里的一行：图标 + 名称 + 服务商/模型名 + 当前打勾。"""
        cur = bool(v.get("current"))
        row = tk.Frame(parent, bg=U.T.brand_soft if cur else U.T.card)
        row.pack(fill="x", pady=U.px(1))

        icon = tk.Canvas(row, width=U.px(22), height=U.px(22), bd=0,
                         highlightthickness=0,
                         bg=U.T.brand_soft if cur else U.T.card)
        icon.pack(side="left", padx=(U.px(4), U.px(6)), pady=U.px(5))
        icon.create_oval(0, 0, U.px(22) - 1, U.px(22) - 1, fill=U.T.brand_soft2,
                         outline="")
        icon.create_text(U.px(11), U.px(11),
                         text=(v.get("provider") or "?")[:1],
                         fill=U.T.brand, font=(U.FONT_FAMILY, 8, "bold"))

        cell = tk.Frame(row, bg=row.cget("bg"))
        cell.pack(side="left", fill="x", expand=True)
        tk.Label(cell, text=v.get("name") or "未命名", bg=row.cget("bg"),
                 fg=U.T.text, font=U.FONT_S, bd=0, anchor="w").pack(fill="x")
        tk.Label(cell, text=f"{v.get('provider')} · {v.get('model')}",
                 bg=row.cget("bg"), fg=U.T.text_mute, font=U.FONT_XS,
                 bd=0, anchor="w").pack(fill="x")

        if cur:
            tk.Label(row, text="✓", bg=row.cget("bg"), fg=U.T.ok,
                     font=U.FONT_S, bd=0).pack(side="right", padx=U.px(8))
        elif v.get("tone") == "warn":
            tk.Label(row, text=v.get("state") or "", bg=row.cget("bg"),
                     fg=U.T.warn, font=U.FONT_XS, bd=0).pack(
                side="right", padx=U.px(8))

        def pick(_e=None):
            mid = v.get("_id") or ""
            if not cur:
                if not self.ai_cfg.set_active(mid):
                    self.toast("切换失败：找不到这条模型", "err")
                    return
                self.toast(f"已切到「{v.get('name')}」", "ok")
                try:
                    self._ai_refresh_dock_state()
                except tk.TclError:
                    pass
            try:
                holder["top"].destroy()
            except (KeyError, tk.TclError):
                pass

        bg0 = row.cget("bg")
        for w in (row, cell) + tuple(cell.winfo_children()):
            w.bind("<Button-1>", pick)
            w.bind("<Enter>", lambda e, r0=row, c0=cell: (
                r0.configure(bg=U.T.brand_soft), c0.configure(bg=U.T.brand_soft),
                [x.configure(bg=U.T.brand_soft) for x in c0.winfo_children()]))
            w.bind("<Leave>", lambda e, r0=row, c0=cell, b0=bg0: (
                r0.configure(bg=b0), c0.configure(bg=b0),
                [x.configure(bg=b0) for x in c0.winfo_children()]))

    # ================================================================ 技能
    def _ai_skill_menu(self) -> None:
        skills = self._ai_skills()

        def build(host):
            rows = [("", AUTO_SKILL, "按问题里的关键词挑，没合适的就不套")]
            for s in skills:
                rows.append((s.slug, s.name, s.description or s.source))
            for slug, label, sub in rows:
                row = tk.Frame(host, bg=U.T.card)
                row.pack(fill="x", padx=U.px(8), pady=U.px(2))
                on = (slug == self._ai_skill_now)
                btn = U.RoundButton(row, "●" if on else "○",
                                    lambda v=slug: self._ai_pick_skill_now(v),
                                    variant="ghost", font=U.FONT_XS,
                                    height=22, padx=6)
                btn.pack(side="left")
                cell = tk.Frame(row, bg=U.T.card)
                cell.pack(side="left", fill="x", expand=True)
                tk.Label(cell, text=label, bg=U.T.card, fg=U.T.text,
                         font=U.FONT_S, anchor="w").pack(fill="x")
                tk.Label(cell, text=sub[:60], bg=U.T.card, fg=U.T.text_mute,
                         font=U.FONT_XS, anchor="w").pack(fill="x")
            U.hsep(host, "card", pady=(6, 4))
            U.RoundButton(host, "打开技能库…", self._ai_open_skill_lib,
                          variant="ghost", font=U.FONT_XS, height=24,
                          padx=10).pack(anchor="w", padx=U.px(8),
                                        pady=(0, U.px(6)))

        _popup(self, self.btn_ai_skill, build, width=U.px(280))

    def _ai_pick_skill_now(self, slug: str) -> None:
        self._ai_skill_now = slug
        self._ai_refresh_scope_btn()

    def _ai_open_skill_lib(self) -> None:
        from pages.ai_page import SkillDialog
        SkillDialog(self, self)

    def _ai_skills(self) -> list:
        try:
            return self.ai_skill_store.all()
        except Exception:  # noqa: BLE001
            return []

    def _ai_pick_skill(self, goal: str):
        """返回 (skill, 说明)。手动点了就用点的，否则按关键词挑。"""
        skills = self._ai_skills()
        if not skills:
            return None, ""
        if self._ai_skill_now:
            sk = next((s for s in skills if s.slug == self._ai_skill_now), None)
            return (sk, "手动指定") if sk else (None, "")
        enabled = self.agent_cfg.skills
        pool = [s for s in skills if s.slug in enabled] if enabled else skills
        if not pool:
            return None, ""
        if len(pool) == 1:
            return pool[0], "只启用了这一个技能"
        slug, why = ai_skills.preferred_slug(goal, pool, "")
        sk = next((s for s in pool if s.slug == slug), None)
        return (sk, why) if sk else (None, why)

    # 兼容 SkillDialog 里的回写（它按旧页面的属性名找）
    def _ai_refresh_skills(self) -> None:
        self._ai_skill_now = ""
        self._ai_refresh_scope_btn()

    # ================================================================ 发送
    def _ai_send_hotkey(self, _e=None):
        self.ai_dock_send()
        return "break"

    def ai_dock_send(self) -> None:
        if self.ai_busy:
            self.toast("上一条还在跑，先停止或等它结束", "warn")
            return
        if not getattr(self, "ai_dock", None):
            self.ai_dock_open()
        goal = self._ai_input.get("1.0", "end-1c").strip()
        if not goal:
            return
        cfg = self.ai_cfg
        if cfg.preset().get("kind") == "dsh":
            if not aiops.find_dsh():
                forms.InfoDialog.show(
                    self, "找不到 dsh",
                    "选了本地 Harness 通道，但没找到 dsh 命令。\n"
                    "请先装 DeepSeek Harness，或改用其它服务商 + API Key。",
                    width=680, height=240)
                return
        elif not cfg.get_key():
            self.toast("还没填 API Key，点右上「设置」填一个", "warn")
            self.ai_agent_settings()
            return
        if not self.agent_cfg.tools:
            self.toast("一个工具都没启用，它会没法干活", "warn")

        self._ai_input.clear()
        self.ai_busy = True
        self._ai_stop.clear()
        self.btn_ai_send.set_text("执行中…")
        self._ai_bubble(goal)

        skill, why = self._ai_pick_skill(goal)
        skill_text = ai_skills.render(skill) if skill is not None else ""
        if skill is not None:
            self._ai_note(f"套用技能：{skill.name}（{why}）", "dim")
        # 终端里开一段「AI 活动流」：任务、模型请求、命令、输出、rc 都落在这儿。
        # 终端没开就顺手开一个 —— 否则用户在页面上等，看不见 AI 到底在忙什么。
        self.ai_trace(aitrace.banner() + aitrace.note(
            f"任务：{goal}" + (f"（技能 {skill.name}）" if skill is not None else ""),
            tone="ok"), ensure=True)
        ctx = self._ai_context_text()
        threading.Thread(target=self._ai_worker, args=(goal, ctx, skill_text),
                         daemon=True).start()

    def _ai_context_text(self) -> str:
        d = self._ai_device()
        lines = []
        base = (f"设备：{d.get('name')} · {d.get('proto')} · {d.get('os') or '未知系统'}"
                f" · {d.get('host_kind') or 'unix'}"
                + (f" · {d.get('admin')}" if d.get("admin") else ""))
        lines.append(base)
        scope = ("全部主机" if self._ai_scope_all else
                 f"{len(self._ai_scope_sids)} 台已选" if self._ai_scope_sids
                 else "当前设备")
        names = [n for n, _ in self._ai_targets()]
        lines.append(f"本轮生效的主机范围：{scope}（{len(names)} 台：{'、'.join(names)}）")
        others = [s.label or s.sid for s in self.sessions]
        if others:
            lines.append("可用的其它主机（exec 的 device 参数可指定）："
                         + "、".join(others))
        lines.append("可操作的页面：" + A.known_pages_hint(list(PAGE_META)))
        return "\n".join(lines)

    def _ai_device(self) -> dict:
        d = self._effective_device()
        s = self.session
        kind = str(getattr(s, "host_kind", "") or "")
        return {
            "name": (getattr(s, "name", None) or getattr(s, "host", None) or "本机"),
            "proto": getattr(s, "proto", "") or "local",
            "os": getattr(d, "os_name", "") or "",
            "host_kind": kind or ("windows" if getattr(d, "os_family", "") == "windows"
                                  else "unix"),
            "vendor": str(getattr(s, "vendor", "") or ""),
            "admin": "已提权" if getattr(s, "is_admin", False) else "",
        }

    def ai_dock_stop(self) -> None:
        if not self.ai_busy:
            return
        self._ai_stop.set()
        self._ai_note("已请求停止，等当前这个动作跑完", "warn")

    def ai_dock_clear(self) -> None:
        if self.ai_busy:
            self.toast("正在执行，先停止再清空", "warn")
            return
        if not forms.confirm(self, "清空会话", "清空对话与上下文？"
                                               "（已经执行过的动作不会撤销）"):
            return
        self.agent.reset()
        self._ai_scroll.clear()
        self._ai_flow_labels = []
        self._ai_md_views = []
        self._ai_tool_cards = []
        self._ai_paint_welcome()
        self._ai_refresh_dock_state()
        self.toast("会话已清空", "ok")

    # ================================================================ 工作线程
    def _ai_worker(self, goal: str, context: str, skill_text: str) -> None:
        try:
            res = self.agent.run(
                goal, self._ai_tool_exec, context=context, skill_text=skill_text,
                confirm=self._ai_confirm_call,
                on_event=lambda k, p: self.post(self._ai_dock_event, k, p),
                should_stop=lambda: self._ai_stop.is_set(),
                risk_of=self._ai_risk_of)
        except Exception as e:  # noqa: BLE001
            self.post(self._ai_dock_event, "error",
                      {"msg": f"{type(e).__name__}: {e}"})
            res = {"ok": False}
        self.post(self._ai_dock_done, res)

    def _ai_risk_of(self, call: dict) -> str:
        if call.get("tool") == "page_action":
            page = (call.get("args") or {}).get("page", "")
            act = (call.get("args") or {}).get("action", "")
            meta = PAGE_META.get(page) or {}
            for a in meta.get("actions") or []:
                if a[0] == act:
                    return a[3]
            return "write"
        return A.default_risk(call)

    def _ai_confirm_call(self, call: dict) -> bool:
        box: dict = {}
        ev = threading.Event()
        # 确认框一弹出来，终端里也留一笔：这一步为什么停住了，看得见
        args = call.get("args") or {}
        what = (args.get("cmd") if call.get("tool") == "exec"
                else " ".join(f"{k}={v}" for k, v in args.items()))
        self.ai_trace(aitrace.note(f"等待确认 · {what or ''}", tone="warn"),
                      ensure=True)
        self.post(self._ai_ask_one, call, box, ev)
        if not ev.wait(timeout=600):
            return False
        return bool(box.get("ok"))

    def _ai_ask_one(self, call, box, ev) -> None:
        tid = call.get("tool") or "?"
        spec = A.TOOLS.get(tid)
        args = call.get("args") or {}
        risk = call.get("risk") or "read"
        body = args.get("cmd") if tid == "exec" else " ".join(
            f"{k}={v}" for k, v in args.items())
        text = (f"助手想执行一步操作：\n\n"
                f"[{aiops.RISK_LABEL.get(risk, risk)}] {spec.label if spec else tid}\n"
                f"    {body}\n"
                + (f"\n理由：{call.get('why')}\n" if call.get("why") else "")
                + "\n确认执行？拒绝的话这一步跳过。")
        try:
            box["ok"] = bool(forms.confirm(self, "确认执行", text,
                                           danger=(risk == "danger"),
                                           ok_text="执行"))
        except Exception:  # noqa: BLE001
            box["ok"] = False
        finally:
            ev.set()

    # ================================================================ 事件
    def _ai_dock_event(self, kind: str, p: dict) -> None:
        if kind == "plan":
            text = (p.get("analysis") or "").strip()
            if text:
                self._ai_reply(text, p.get("step"))
            if p.get("note"):
                self._ai_note("· " + p["note"], "mute")
        elif kind == "need_confirm":
            self._ai_note("· 这一步要改动，等你确认…", "warn")
        elif kind == "call":
            self._ai_pending = self._ai_tool_card(p.get("call") or {})
        elif kind == "result":
            rec = getattr(self, "_ai_pending", None)
            if rec is not None:
                self._ai_tool_result(rec, bool(p.get("ok")), p.get("text") or "")
                self._ai_pending = None
        elif kind == "refused":
            self._ai_note("✕ 已拒绝：" + _call_title(p.get("call") or {}), "err")
        elif kind == "skip":
            self._ai_note("– 只读模式跳过：" + _call_title(p.get("call") or {}), "mute")
        elif kind == "stopped":
            self._ai_note("— 已停止 —", "warn")
        elif kind == "finish":
            self._ai_note(f"— {p.get('reason') or '结束'} —", "mute")
        elif kind == "error":
            self._ai_note("出错了：" + str(p.get("msg") or ""), "err")
            self.ai_trace(aitrace.note("出错：" + str(p.get("msg") or ""),
                                       tone="err"))
            self.toast("助手调用失败", "err")
        self._ai_refresh_dock_state()

    def _ai_dock_done(self, res) -> None:
        self.ai_busy = False
        try:
            self.btn_ai_send.set_text("发送")
        except tk.TclError:
            pass
        self._ai_refresh_dock_state()
        if isinstance(res, dict) and res.get("ok"):
            n = len(res.get("ran") or [])
            if n:
                self.status.set_state(f"助手执行了 {n} 个动作", "ok")
            self.ai_trace(aitrace.note(
                f"本次结束 · {res.get('steps') or 0} 轮 · 执行 {n} 个动作"
                + (f" · 拒绝 {len(res.get('refused') or [])}"
                   if res.get("refused") else ""),
                tone="ok"))

    # ================================================================ 工具落地
    def _ai_tool_exec(self, tool: str, args: dict):
        """工作线程里跑。只有碰 Tk 的工具才切回主线程。

        exec 自己会把「命令 + 输出 + rc」写进终端（那段更详细），其余动作
        在这里补一条收尾行 —— 用户要的是「AI 干了什么终端里全看得见」，
        翻页、读表这类看不见的动作也得留个痕。
        """
        ok, text = self._ai_tool_dispatch(tool, args)
        if tool not in ("exec", "device", "pages", "page_read"):
            label = A.TOOLS[tool].label if tool in A.TOOLS else tool
            self.ai_trace(aitrace.note(
                f"{label} · {'完成' if ok else '失败'} · {str(text or '')[:120]}",
                tone="ok" if ok else "err"), ensure=True)
        return ok, text

    def _ai_tool_dispatch(self, tool: str, args: dict):
        if tool == "device":
            return True, self._ai_device_text()
        if tool == "pages":
            return True, self._ai_pages_text()
        if tool == "exec":
            return self._ai_exec(args.get("cmd", ""), args.get("device", ""))
        if tool == "relay":
            return self._ai_relay(args.get("src", ""), args.get("src_path", ""),
                                  args.get("dst", ""), args.get("dst_path", ""))
        if tool == "open_page":
            return self._ai_ui_call(self._ai_open_page, args.get("page", ""))
        if tool == "page_action":
            return self._ai_ui_call(self._ai_page_action, args.get("page", ""),
                                    args.get("action", ""))
        if tool == "page_read":
            return self._ai_ui_call(self._ai_page_read, args.get("page", ""))
        return False, f"不认识这个工具：{tool}"

    def _ai_ui_call(self, fn, *a):
        """从工作线程切回主线程执行，并等结果 —— Tk 不是线程安全的。"""
        box: dict = {}
        ev = threading.Event()

        def run():
            try:
                box["r"] = fn(*a)
            except Exception as e:  # noqa: BLE001
                box["r"] = (False, f"{type(e).__name__}: {e}")
            finally:
                ev.set()

        self.post(run)
        if not ev.wait(timeout=40):
            return False, "界面没有响应（超时）"
        return box.get("r", (False, "没有结果"))

    # -- 设备
    def _ai_device_text(self) -> str:
        d = self._ai_device()
        lines = [f"{k}={v}" for k, v in d.items() if v]
        names = [n for n, _ in self._ai_targets()]
        lines.append("本轮主机范围：" + "、".join(names))
        return "\n".join(lines)

    def _ai_session_named(self, key: str):
        """按标签 / sid / 主机名找会话；空或「本机」返回本机会话。"""
        k = (key or "").strip()
        if not k or k.lower() in ("current", "local", "本机", "当前", "."):
            return "本机", self._ai_local_session()
        for s in self.sessions:
            if k in (s.sid, s.label, getattr(s.session, "host", "")):
                return s.label or s.sid, s.session
        return "", None

    def _ai_exec(self, cmd: str, device: str = ""):
        cmd = (cmd or "").strip()
        if not cmd:
            return False, "命令是空的"
        if device:
            name, sess = self._ai_session_named(device)
            if sess is None:
                return False, f"找不到主机「{device}」。可用：" + "、".join(
                    [s.label or s.sid for s in self.sessions] or ["本机"])
            targets = [(name, sess)]
        else:
            targets = self._ai_targets()

        blocks: list[str] = []
        all_ok = True
        risk = aiops.classify_risk(cmd)
        for name, sess in targets:
            # 先占位再执行：终端里要「实时」看到 AI 正在干什么，而不是等跑完
            # 才冒出一行。同时登记这条命令，免得 tracer 回过头又喂一遍。
            self._ai_mark_cmd(cmd)
            self.ai_trace(aitrace.cmd(host=name, command=cmd, risk=risk),
                          ensure=True)
            t0 = time.monotonic()
            try:
                rc, out, err = sess.run(cmd, timeout=90)
            except Exception as e:  # noqa: BLE001
                rc, out, err = 1, "", f"{type(e).__name__}: {e}"
            ms = int((time.monotonic() - t0) * 1000)
            body = ((out or "") + (("\n[stderr] " + err) if err else "")).strip()
            # 输出原样落到终端（该红的红、该白的白），末行补 rc 与耗时
            shown: list[tuple[str, str]] = []
            if (out or "").strip():
                shown.append((out if out.endswith("\n") else out + "\n", "out"))
            if (err or "").strip():
                shown.append((err if err.endswith("\n") else err + "\n", "err"))
            shown.extend(aitrace.result(rc=rc, ms=ms, out=out, err=err))
            self.ai_trace(shown)
            if rc != 0:
                all_ok = False
            head = f"── {name} · rc={rc} ──" if len(targets) > 1 else f"rc={rc}"
            blocks.append(f"{head}\n{body or '(无输出)'}")
            self._log_raw("ok" if rc == 0 else "bad",
                          f"[助手] {name} $ {cmd}\n{(body or '')[:4000]}\n")
        return all_ok, "\n\n".join(blocks)

    # -- 跨机中转
    def _ai_relay(self, src: str, src_path: str, dst: str, dst_path: str):
        src_path = (src_path or "").strip()
        dst_path = (dst_path or "").strip()
        if not src_path.startswith("/") or not dst_path.startswith("/"):
            return False, "relay 目前只支持 Linux / macOS 目标，两端路径必须是绝对路径"
        s_name, s_sess = self._ai_session_named(src)
        d_name, d_sess = self._ai_session_named(dst)
        if s_sess is None:
            return False, f"找不到源主机「{src}」"
        if d_sess is None:
            return False, f"找不到目标主机「{dst}」"
        if d_sess is self._ai_local_session() and not _is_unix_local():
            return False, "目标机是本机 Windows，relay 暂不支持"

        rc, out, err = s_sess.run(f"base64 < {shlex.quote(src_path)}", timeout=120)
        if rc != 0:
            return False, f"读源文件失败（{s_name}）：{(err or out).strip()[:300]}"
        b64 = "".join((out or "").split())
        if not b64:
            return False, f"源文件是空的或读不到：{src_path}"
        try:
            raw_size = len(base64.b64decode(b64))
        except Exception:  # noqa: BLE001
            return False, "源文件读回来的内容不是合法 base64，已中止"
        size = raw_size
        if size > RELAY_MAX:
            return False, (f"文件 {size // 1024} KB 超过中转上限 "
                           f"{RELAY_MAX // 1024} KB，大文件请在设备之间直接传")

        tmp = dst_path + ".toolbox-relay"
        d_sess.run(f"rm -f {shlex.quote(tmp)}", timeout=30)
        for i in range(0, len(b64), RELAY_CHUNK):
            part = b64[i:i + RELAY_CHUNK]
            op = ">" if i == 0 else ">>"
            rc, _o, e2 = d_sess.run(
                f"printf '%s' {shlex.quote(part)} {op} {shlex.quote(tmp)}",
                timeout=60)
            if rc != 0:
                d_sess.run(f"rm -f {shlex.quote(tmp)}", timeout=30)
                return False, f"写目标机失败（{d_name}）：{(e2 or '').strip()[:300]}"
        rc, _o, e3 = d_sess.run(
            f"test \"$(base64 -d < {shlex.quote(tmp)} | wc -c)\" -eq {size}"
            f" && mv -f {shlex.quote(tmp)} {shlex.quote(dst_path)}", timeout=60)
        if rc != 0:
            d_sess.run(f"rm -f {shlex.quote(tmp)}", timeout=30)
            return False, f"校验失败，已放弃写入：{(e3 or '').strip()[:300]}"
        return True, (f"已搬 {size} 字节：{s_name}:{src_path} → "
                      f"{d_name}:{dst_path}（已校验大小）")

    # -- 界面
    def _ai_pages_text(self) -> str:
        lines = []
        for key, meta in PAGE_META.items():
            acts = meta.get("actions") or []
            tail = ("　动作：" + "、".join(f"{a[0]}({a[1]})" for a in acts)) if acts else ""
            tb = "　可读数据" if meta.get("table") else ""
            lines.append(f"- {key} = {meta['label']}{tb}{tail}")
        return "\n".join(lines)

    def _ai_open_page(self, page: str):
        page = (page or "").strip()
        meta = PAGE_META.get(page)
        if meta is None:
            return False, ("没有这个页面：" + page + "。可用："
                           + "、".join(PAGE_META))
        if page not in self._pages:
            return False, f"页面「{meta['label']}」还没构建"
        self.nav.select(page)
        return True, f"已切到「{meta['label']}」"

    def _ai_page_action(self, page: str, action: str):
        page = (page or "").strip()
        action = (action or "").strip()
        meta = PAGE_META.get(page)
        if meta is None:
            return False, f"没有这个页面：{page}"
        for a in meta.get("actions") or []:
            if a[0] != action:
                continue
            fn = getattr(self, a[2], None)
            if fn is None:
                return False, f"页面「{meta['label']}」没有这个动作了：{a[2]}"
            self.nav.select(page)
            fn()
            return True, (f"已触发「{meta['label']} · {a[1]}」。"
                          "结果需要一点时间填进页面，读数用 page_read")
        return False, (f"「{meta['label']}」没有动作 {action}。可用："
                       + ("、".join(a[0] for a in meta.get("actions") or []) or "无"))

    def _ai_page_read(self, page: str):
        page = (page or "").strip()
        meta = PAGE_META.get(page)
        if meta is None:
            return False, f"没有这个页面：{page}"
        tname = meta.get("table") or ""
        if not tname:
            return False, f"「{meta['label']}」没有可读的表格数据"
        tbl = getattr(self, tname, None)
        if tbl is None:
            return False, f"「{meta['label']}」的表格还没建好"
        try:
            rows = tbl.rows()
        except Exception as e:  # noqa: BLE001
            return False, f"读表格失败：{type(e).__name__}: {e}"
        if not rows:
            return True, f"「{meta['label']}」当前没有数据（可能还没加载，先 page_action 触发一次）"
        cols = [c.title for c in getattr(tbl, "columns", [])]
        out = ["\t".join(cols)]
        for r in rows[:ROW_LIMIT]:
            cells = []
            for c in getattr(tbl, "columns", []):
                try:
                    cells.append(str(c.text(r)))
                except Exception:  # noqa: BLE001
                    cells.append(str(r.get(c.key, "")))
            out.append("\t".join(cells))
        if len(rows) > ROW_LIMIT:
            out.append(f"…（共 {len(rows)} 行，只给前 {ROW_LIMIT} 行）")
        return True, "\n".join(out)

    # ================================================================ 模型
    def ai_model_settings(self) -> None:
        """模型管理：一条配置一个模型，随时切换当前。

        形态对齐 DeepSeek Harness —— 这里不再是一个「填到底」的表单，
        而是清单 + 行操作（设为当前 / 编辑 / 试连 / 删除）。
        """
        ModelDialog(self, self)

    # ================================================================ 设置
    def ai_agent_settings(self) -> None:
        AgentDialog(self, self)


def _call_title(call: dict) -> str:
    tid = call.get("tool") or "?"
    spec = A.TOOLS.get(tid)
    args = call.get("args") or {}
    label = spec.label if spec else tid
    if tid == "exec":
        return f"{label} · {args.get('cmd', '')}"
    return f"{label} · " + " ".join(str(v) for v in args.values())


def _is_unix_local() -> bool:
    import sys
    return not sys.platform.startswith("win")


def _popup(app, anchor, builder, width: int = 260) -> tk.Toplevel:
    """贴着按钮弹出的浮层。点外面或按 Esc 就关 —— 不做模态。"""
    top = tk.Toplevel(app)
    top.withdraw()
    top.overrideredirect(True)
    top.configure(bg=U.T.border)
    shell = tk.Frame(top, bg=U.T.card, bd=0)
    shell.pack(fill="both", expand=True, padx=1, pady=1)
    try:
        builder(shell)
    except Exception:  # noqa: BLE001
        top.destroy()
        raise
    top.update_idletasks()
    w = max(width, shell.winfo_reqwidth())
    h = shell.winfo_reqheight()
    x = anchor.winfo_rootx()
    y = anchor.winfo_rooty() - h - U.px(6)
    sw = anchor.winfo_screenwidth()
    sh = anchor.winfo_screenheight()
    if y < 40:                       # 上面放不下就翻到下面
        y = anchor.winfo_rooty() + anchor.winfo_height() + U.px(6)
    x = max(8, min(x, sw - w - 12))
    y = max(8, min(y, sh - h - 48))
    top.geometry(f"{w}x{h}+{x}+{y}")
    top.deiconify()
    top.transient(app.winfo_toplevel())
    top.bind("<Escape>", lambda e: top.destroy())
    top.bind("<FocusOut>", lambda e: top.after(120, _close_if_unfocused, top))
    top.focus_set()
    return top


def _close_if_unfocused(top: tk.Toplevel) -> None:
    try:
        if top.focus_displayof() is None:
            top.destroy()
    except tk.TclError:
        pass


class AgentDialog(tk.Toplevel):
    """智能体设置：基本信息 / 提示词 / 技能 / 工具，四段。

    这里改的全是「它是什么、能干什么」，模型 Key 仍在「模型设置」里 —— 两件事分开，
    换模型不改人设，改人设不动凭据。
    """

    def __init__(self, parent, page):
        super().__init__(parent)
        self.page = page
        self.cfg = page.agent_cfg
        self.title("智能运维助手 · 设置")
        self.configure(bg=U.T.page)
        self.transient(parent)
        self._skill_vars: dict[str, tk.BooleanVar] = {}
        self._tool_vars: dict[str, tk.BooleanVar] = {}

        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(18), pady=U.px(16))

        head = U.wframe(wrap, "page")
        head.pack(fill="x")
        U.wlabel(head, "智能运维助手", "page", U.FONT_T).pack(side="left")
        U.wlabel(head, "描述 · 提示词 · 技能 · 工具", "pagedim",
                 U.FONT_XS).pack(side="left", padx=(U.px(10), 0))
        U.RoundButton(head, "模型设置", self._model, variant="ghost",
                      height=30, padx=14).pack(side="right", padx=(U.px(6), 0))
        U.RoundButton(head, "取消", self.destroy, variant="secondary",
                      height=30, padx=16).pack(side="right")
        U.RoundButton(head, "保存", self._save, variant="primary",
                      height=30, padx=18).pack(side="right", padx=(0, U.px(6)))

        self.seg = Segmented(wrap, [("base", "基本信息"), ("prompt", "提示词"),
                                    ("skill", "技能"), ("tool", "工具")],
                             command=self._tab)
        self.seg.pack(anchor="w", pady=(U.px(12), U.px(10)))

        self.body = U.wframe(wrap, "page")
        self.body.pack(fill="both", expand=True)
        self._frames = {
            "base": self._build_base(self.body),
            "prompt": self._build_prompt(self.body),
            "skill": self._build_skills(self.body),
            "tool": self._build_tools(self.body),
        }
        self.seg.select("base")

        self.geometry(f"{U.px(880)}x{U.px(640)}")
        self.minsize(U.px(680), U.px(460))
        self.bind("<Escape>", lambda e: self.destroy())

    def _tab(self, key: str) -> None:
        for k, f in self._frames.items():
            if k == key:
                f.pack(fill="both", expand=True)
            else:
                f.pack_forget()

    # -- 基本信息
    def _build_base(self, master) -> tk.Frame:
        f = U.wframe(master, "page")
        card = U.Card(f, title="名字与描述",
                      hint="这两段会拼进提示词，也会显示在侧栏标题上")
        card.pack(fill="x")
        b = card.body
        U.wlabel(b, "名字", "dim", U.FONT_S).pack(anchor="w")
        self.v_name = tk.StringVar(value=self.cfg.name)
        self._entry(b, self.v_name).pack(fill="x", pady=(U.px(4), U.px(10)))

        U.wlabel(b, "描述", "dim", U.FONT_S).pack(anchor="w")
        self.tx_desc = self._textarea(b, self.cfg.desc, 4)
        self.tx_desc.pack(fill="x", pady=(U.px(4), 0))

        card2 = U.Card(f, title="行为", hint="执行模式决定它跑命令前问不问你")
        card2.pack(fill="x", pady=(U.px(12), 0))
        b2 = card2.body
        row = U.wframe(b2, "card")
        row.pack(fill="x")
        U.wlabel(row, "执行模式", "dim", U.FONT_S).pack(side="left")
        self.v_mode = tk.StringVar(value=MODE_LABEL.get(self.cfg.mode,
                                                        MODE_LABEL["auto"]))
        from tkinter import ttk
        cb = ttk.Combobox(row, textvariable=self.v_mode,
                          values=[v for _, v in MODE_OPTS], state="readonly",
                          width=42)
        cb.pack(side="left", padx=(U.px(8), 0))

        row2 = U.wframe(b2, "card")
        row2.pack(fill="x", pady=(U.px(10), 0))
        U.wlabel(row2, "最多步数", "dim", U.FONT_S).pack(side="left")
        self.v_steps = tk.StringVar(value=str(self.cfg.max_steps))
        e = self._entry(row2, self.v_steps, width=6)
        e.pack(side="left", padx=(U.px(8), U.px(6)))
        U.wlabel(row2, "步（一次任务最多来回几轮，防绕圈）", "mute",
                 U.FONT_XS).pack(side="left")

        U.wlabel(f, "默认不启用「全部主机」：一条写操作同时落到十台机器上是事故，"
                    "不是效率。要批量就在侧栏的主机下拉里明确选。",
                 "mute", U.FONT_XS, justify="left",
                 wraplength=U.px(720)).pack(anchor="w", pady=(U.px(12), 0))
        return f

    # -- 提示词
    def _build_prompt(self, master) -> tk.Frame:
        f = U.wframe(master, "page")
        card = U.Card(f, title="系统提示词",
                      hint="占位符：{name} {desc} {tools} {context}，发送前会替换成真实内容")
        card.pack(fill="both", expand=True)
        b = card.body
        bar = U.wframe(b, "card")
        bar.pack(fill="x", pady=(0, U.px(8)))
        U.RoundButton(bar, "恢复默认", self._reset_prompt, variant="ghost",
                      font=U.FONT_XS, height=26, padx=12).pack(side="left")
        U.wlabel(bar, "改狠了它会不听话；改坏了点左边一键还原", "mute",
                 U.FONT_XS).pack(side="left", padx=(U.px(10), 0))
        self.tx_prompt = self._textarea(
            b, str(self.cfg.data.get("prompt") or A.DEFAULT_PROMPT), 20,
            mono=True)
        self.tx_prompt.pack(fill="both", expand=True)
        return f

    def _reset_prompt(self) -> None:
        self.tx_prompt.delete("1.0", "end")
        self.tx_prompt.insert("1.0", A.DEFAULT_PROMPT)

    # -- 技能
    def _build_skills(self, master) -> tk.Frame:
        f = U.wframe(master, "page")
        card = U.Card(f, title="技能",
                      hint="勾选后只在勾中的技能里挑；一个都不勾就是全库自动匹配")
        card.pack(fill="both", expand=True)
        b = card.body
        bar = U.wframe(b, "card")
        bar.pack(fill="x", pady=(0, U.px(8)))
        U.RoundButton(bar, "技能库…", self._skill_lib, variant="ghost",
                      font=U.FONT_XS, height=26, padx=12).pack(side="left")
        U.RoundButton(bar, "全选", lambda: self._set_all(self._skill_vars, True),
                      variant="ghost", font=U.FONT_XS, height=26,
                      padx=10).pack(side="left", padx=(U.px(6), 0))
        U.RoundButton(bar, "全不选", lambda: self._set_all(self._skill_vars, False),
                      variant="ghost", font=U.FONT_XS, height=26,
                      padx=10).pack(side="left", padx=(U.px(4), 0))

        box = _Scroll(b, U.T.card)
        box.pack(fill="both", expand=True)
        skills = self.page._ai_skills()
        if not skills:
            U.wlabel(box.inner, "技能库是空的。点「技能库…」导入一份 SOP。",
                     "mute", U.FONT_S).pack(anchor="w", padx=U.px(6), pady=U.px(8))
        for s in skills:
            row = tk.Frame(box.inner, bg=U.T.card)
            row.pack(fill="x", padx=U.px(6), pady=U.px(3))
            var = tk.BooleanVar(value=(not self.cfg.skills)
                               or s.slug in self.cfg.skills)
            self._skill_vars[s.slug] = var
            U.Check(row, "", variable=var).pack(side="left")
            cell = tk.Frame(row, bg=U.T.card)
            cell.pack(side="left", fill="x", expand=True, padx=(U.px(4), 0))
            tk.Label(cell, text=f"{s.name}　·　{s.source}", bg=U.T.card,
                     fg=U.T.text, font=U.FONT_S, anchor="w").pack(fill="x")
            tk.Label(cell, text=(s.description or "—")[:110], bg=U.T.card,
                     fg=U.T.text_mute, font=U.FONT_XS, anchor="w",
                     justify="left", wraplength=U.px(720)).pack(fill="x")
        return f

    def _skill_lib(self) -> None:
        self.page._ai_open_skill_lib()
        self.after(400, self._reload_skills)

    def _reload_skills(self) -> None:
        frame = self._frames.get("skill")
        if frame is None:
            return
        for w in frame.winfo_children():
            w.destroy()
        self._frames["skill"] = self._build_skills(self.body)
        self._tab("skill")

    # -- 工具
    def _build_tools(self, master) -> tk.Frame:
        f = U.wframe(master, "page")
        card = U.Card(f, title="可用工具",
                      hint="没勾的工具它看不见，也不会尝试调用")
        card.pack(fill="both", expand=True)
        b = card.body
        bar = U.wframe(b, "card")
        bar.pack(fill="x", pady=(0, U.px(8)))
        U.RoundButton(bar, "全部启用", lambda: self._set_all(self._tool_vars, True),
                      variant="ghost", font=U.FONT_XS, height=26,
                      padx=10).pack(side="left")
        U.RoundButton(bar, "只看只读", lambda: self._only_read(),
                      variant="ghost", font=U.FONT_XS, height=26,
                      padx=10).pack(side="left", padx=(U.px(6), 0))
        U.wlabel(bar, "relay 会往目标机写文件，属改动类操作", "mute",
                 U.FONT_XS).pack(side="left", padx=(U.px(10), 0))

        box = _Scroll(b, U.T.card)
        box.pack(fill="both", expand=True)
        for spec in A.TOOL_SPECS:
            row = tk.Frame(box.inner, bg=U.T.card)
            row.pack(fill="x", padx=U.px(6), pady=U.px(4))
            var = tk.BooleanVar(value=self.cfg.tool_on(spec.id))
            self._tool_vars[spec.id] = var
            U.Check(row, "", variable=var).pack(side="left", anchor="n")
            cell = tk.Frame(row, bg=U.T.card)
            cell.pack(side="left", fill="x", expand=True, padx=(U.px(4), 0))
            top = tk.Frame(cell, bg=U.T.card)
            top.pack(fill="x")
            tk.Label(top, text=spec.label, bg=U.T.card, fg=U.T.text,
                     font=U.FONT_S).pack(side="left")
            tk.Label(top, text=f"  {spec.id}　·　{spec.group}", bg=U.T.card,
                     fg=U.T.text_mute, font=U.FONT_XS).pack(side="left")
            U.Pill(top, aiops.RISK_LABEL.get(spec.risk, spec.risk),
                   aiops.RISK_TONE.get(spec.risk, "mute"), height=20).pack(
                side="left", padx=(U.px(6), 0))
            tk.Label(cell, text=spec.desc, bg=U.T.card, fg=U.T.text_dim,
                     font=U.FONT_XS, anchor="w", justify="left",
                     wraplength=U.px(720)).pack(fill="x")
            if spec.args:
                tk.Label(cell, text="参数：" + spec.args, bg=U.T.card,
                         fg=U.T.text_mute, font=U.FONT_XS, anchor="w").pack(fill="x")
        return f

    @staticmethod
    def _set_all(vars_: dict, value: bool) -> None:
        for v in vars_.values():
            v.set(bool(value))

    def _only_read(self) -> None:
        for tid, var in self._tool_vars.items():
            spec = A.TOOLS.get(tid)
            var.set(bool(spec and spec.risk == "read"))

    # -- 保存
    def _save(self) -> None:
        cfg = self.cfg
        cfg.data["name"] = self.v_name.get().strip() or A.AGENT_NAME
        cfg.data["desc"] = self.tx_desc.get("1.0", "end").strip()
        cfg.data["prompt"] = self.tx_prompt.get("1.0", "end").strip()
        cfg.data["mode"] = MODE_BY_LABEL.get(self.v_mode.get(), "auto")
        try:
            cfg.data["max_steps"] = int(str(self.v_steps.get()).strip() or 8)
        except ValueError:
            cfg.data["max_steps"] = 8
        picked = [slug for slug, v in self._skill_vars.items() if v.get()]
        # 全勾等于「不限制」，存空列表更好读，也省得以后装了新技能反而被排除
        cfg.data["skills"] = [] if len(picked) == len(self._skill_vars) else picked
        cfg.data["tools"] = [t for t, v in self._tool_vars.items() if v.get()]
        cfg.save()
        self.page._ai_refresh_dock_state()
        self.page.toast("设置已保存", "ok")
        self.destroy()

    def _model(self) -> None:
        self.page.ai_model_settings()

    # -- 小控件
    @staticmethod
    def _entry(master, var, width: int = 30):
        from tkinter import ttk
        e = ttk.Entry(master, textvariable=var, width=width, font=U.FONT_S)
        return e

    @staticmethod
    def _textarea(master, text: str, lines: int, mono: bool = False):
        t = tk.Text(master, height=lines, width=40,
                    font=U.MONO_S if mono else U.FONT_S, bd=0, relief="flat",
                    highlightthickness=1, highlightbackground=U.T.input_border,
                    highlightcolor=U.T.brand, bg=U.T.input_bg, fg=U.T.text,
                    insertbackground=U.T.text, wrap="word",
                    padx=U.px(8), pady=U.px(6))
        t.insert("1.0", text or "")
        return t
