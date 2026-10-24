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
import os
import re
import shlex
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

import wbtui as U
from core import agent as A
from core import ai_skills, aiops, aisession, aitrace, clipfile
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

# 侧栏宽度（逻辑像素，会乘 U.S）。
# 参考 WorkBuddy：它常驻在右边，只占窗口的一条 —— 内容靠折行，不靠加宽。
# 之前默认 400 还只在宽窗口里生效，窗口一窄就按内容要多少给多少，
# 结果助手吃掉半个屏，左边的主机表被挤成一条。
DOCK_W_DEFAULT = 372
DOCK_W_MIN = 340          # 再窄的话消息卡片里一行只剩十来个字
DOCK_W_MAX = 560          # 再宽就不像侧栏了
DOCK_W_RATIO = 0.42       # 无论如何不超过窗口的这个比例

# 一次最多带几个附件。带多了模型会被文件内容挤爆上下文，
# 而且用户自己也记不住到底贴了几个。
MAX_ATTACH = 8

# 附件内容内联进提示词的上限。再大就让它用工具去读 —— 一次性灌进上下文，
# 既贵又容易把关键指令挤到窗口外面。
ATTACH_INLINE_LIMIT = 32 * 1024

# 当成「图片」显示的扩展名。决定 chip 上那个小图标，以及要不要内联内容 ——
# 图片内联成乱码没有任何意义，只报路径和尺寸。
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico"}

# 工具输出里认路径用的。只认「看起来像文件」的东西（有扩展名），
# 抓到的还要过一遍 os.path.isfile —— 宁可少列，不能列一堆点不开的。
_PATH_RE = re.compile(
    r"(?>[A-Za-z]:\\[^\s\"'<>|*?\r\n]+"
    r"|/(?:[\w.@%+~-]+/)*[\w.@%+~-]+\.[A-Za-z0-9]{1,8})")

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


class _Scroll(U.ScrollBox):
    """滚动容器（实现在 wbtui.ScrollBox：Canvas + 内嵌 Frame + 按指针路由滚轮）。"""


class AiDockPage:
    """侧栏：界面 + 工具落地 + 与主窗口/会话的桥接。"""

    # ================================================================ 构建
    def ai_dock_build(self, parent) -> tk.Frame:
        """造一次，之后开关只是 add / forget，不重建（会话内容要留住）。"""
        dock = U.wframe(parent, "page")
        dock.configure(width=U.px(self.agent_cfg.data.get("dock_width")
                                  or DOCK_W_DEFAULT))
        dock.pack_propagate(False)
        self.ai_dock = dock
        self._ai_flow_labels: list[tk.Label] = []
        self._ai_wrap_w = 0
        self._ai_reflow_job = None
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
        # 历史会话单独一个按钮：以前藏在「⋯ → 所有会话」里，等于没有 ——
        # 用户找历史第一反应是点右上角，不是去猜一个省略号。
        self.btn_dock_hist = U.RoundButton(head, "◷", self._ai_sess_list,
                                           variant="ghost", font=U.FONT_S,
                                           height=24, padx=8)
        self.btn_dock_hist.pack(side="right", padx=(0, U.px(2)))
        self.lbl_dock_tokens = U.wlabel(head, "", "mute", U.FONT_XS)
        self.lbl_dock_tokens.pack(side="right", padx=(U.px(6), U.px(8)))

        # 一条细线，把头与会话分开；不画的话顶部看着像浮在内容上
        self._ai_head_line = tk.Frame(dock, bg=U.T.border, height=U.px(1))
        self._ai_head_line.pack(fill="x", padx=U.px(10))
        U.register(self._ai_head_line,
                   lambda: self._ai_head_line.configure(bg=U.T.border))

        # ---- 会话页签条（CodeBuddy 形态：一条横排，末尾一个 ＋）
        # 每个页签是一次独立的对话，各有自己的上下文与消息流；
        # 关掉页签只是把它从这条横条上收起来，记录还在「所有会话」里。
        self._ai_sess_store = aisession.Store(self.agent_cfg.path.parent)
        self._ai_sessions: dict[str, aisession.Session] = {}
        self._ai_open: dict[str, dict] = {}
        self._ai_sess_order: list[str] = []
        self._ai_tabs = tk.Frame(dock, bg=U.T.page)
        self._ai_tabs.pack(fill="x", padx=U.px(8), pady=(U.px(4), 0))

        # ---- 消息流（每个会话一个，切换时换容器，不重放控件）
        self._ai_scroll = None
        self._ai_flow = None
        dock.bind("<Configure>", self._ai_dock_reflow)

        # ---- 输入区
        foot = U.wframe(dock, "page")
        foot.pack(side="bottom", fill="x", padx=U.px(10),
                  pady=(U.px(6), U.px(8)))
        self._ai_foot = foot
        box = tk.Frame(foot, bg=U.T.card, bd=0, highlightthickness=1,
                       highlightbackground=U.T.border)
        box.pack(fill="x")

        # ---- 输入区：附件条在文本框上面，有东西才占高度
        self._ai_attach: list[dict] = []
        self._ai_files_seen: dict[str, str] = {}
        self._ai_attach_bar = tk.Frame(box, bg=U.T.card)

        self._ai_input = U.TextBox(box, role="card", height=3, readonly=False)
        self._ai_input.pack(fill="x", padx=U.px(6), pady=(U.px(6), 0))
        self._ai_input.txt.bind("<Control-Return>", self._ai_send_hotkey)
        # Ctrl+V 先让 Tk 粘文本 —— 这条路它比谁都稳（输入法、撤销栈都在它手里）。
        # 只有剪贴板里根本不是文本（图片 / 在资源管理器里复制的文件）才轮到我们。
        for seq in ("<Control-v>", "<Control-V>", "<Shift-Insert>"):
            self._ai_input.txt.bind(seq, self._ai_paste)

        bar = U.wframe(box, "card")
        bar.pack(fill="x", padx=U.px(6), pady=(U.px(4), U.px(6)))

        # 发送 / 停止合成一个按钮：空闲是箭头 ➤，执行中变方块 ■，同一位置切换
        # （WorkBuddy 的形态）。侧栏就这么窄，并排两个按钮既挤又容易点错。
        self.btn_ai_send = U.RoundButton(bar, "➤", self._ai_send_or_stop,
                                         variant="primary", font=U.FONT_S,
                                         height=28, padx=13)
        self.btn_ai_send.pack(side="right")
        # 旧名字留个别名：其它地方（含测试）还在按 btn_ai_stop 找它
        self.btn_ai_stop = self.btn_ai_send

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

        # 权限档位：WorkBuddy 把它摆在输入框旁边，因为「这一句下去会不会自己动手」
        # 是发之前最该确认的事。收进 ＋ 里等于要人多点两层才看得见。
        self.btn_ai_perm = U.RoundButton(bar, "自动", self._ai_perm_menu,
                                        variant="ghost", font=U.FONT_XS,
                                        height=26, padx=8)
        self.btn_ai_perm.pack(side="left", padx=(U.px(4), 0))

        # 思考档位：模型不动、档位可换（DSH 那套 off/low/high/max）。
        # 模型不支持思考时这个按钮不占位 —— 摆一个点了没反应的按钮最伤人。
        self.btn_ai_think = U.RoundButton(bar, "思考", self._ai_think_menu,
                                          variant="ghost", font=U.FONT_XS,
                                          height=26, padx=8)

        # 上下文用量：已用 / 上限。放发送按钮左边，超 80% 变黄、95% 变红，
        # 并在提示里直说「建议新建会话」—— 真要爆的时候人是看不见数字的。
        self.lbl_ai_ctx = U.wlabel(bar, "", "pagemute", U.FONT_XS)
        self.lbl_ai_ctx.pack(side="right", padx=(0, U.px(6)))

        self._ai_scope_all = False
        self._ai_scope_sids: list[str] = []   # sid 或 LOCAL_SID
        self._ai_skill_now = ""
        self._ai_pending = None
        # 中途插话：这一轮跑着的时候用户补的话先进这个队列，
        # Agent 每一步开始前取走 —— 绝不并发再起一轮。
        self._ai_inject: list[str] = []
        # 会话底座：接着上次没聊完的，没有就开一个空的
        self._ai_sess_boot()
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
        st = self._ai_sess_state()
        if st is not None:
            st["welcome"] = self._ai_welcome_widgets

    def _ai_sess_state(self):
        """当前会话的界面临时状态（每个会话一份）。"""
        sess = getattr(self, "_ai_sess", None)
        if sess is None:
            return None
        return self._ai_open.get(sess.id)

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
        try:
            n = len(self._ai_sess_store.all())
        except (AttributeError, OSError):
            n = 0
        rows = [
            ("新建会话", self._ai_tab_new),
            (f"历史会话（{n}）", self._ai_sess_list),
            None,
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
        # 主窗被拉窄时，助手不能还占着当初的宽度 —— 跟着一起收
        try:
            self.paned.bind("<Configure>", self._ai_dock_clamp, add="+")
        except tk.TclError:
            pass

    def _ai_dock_clamp(self, _e=None) -> None:
        """窗口变窄时只做「收」，不主动变宽 —— 用户拖过的宽度要尊重。"""
        if not getattr(self, "_ai_dock_on", False):
            return
        try:
            total = self.paned.winfo_width()
            cur = self.ai_dock.winfo_width()
        except tk.TclError:
            return
        if total < U.px(320) or cur <= 1:
            return
        cap = min(U.px(DOCK_W_MAX), int(total * DOCK_W_RATIO))
        if cur > cap:
            self._ai_dock_sash()

    def ai_dock_close(self) -> None:
        if not getattr(self, "ai_dock", None):
            return
        # 收起之前先把宽度与当前会话落盘，否则这一轮的对话就没了
        self._ai_dock_remember()
        self._ai_sess_save()
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
        """把侧栏摆到设定宽度。

        宽度是可拖的（用户要求），但两头都要夹住：

          下限 —— 窄到消息卡片一行放不下十来个字就没法看了
          上限 —— 窗口的 42%（且不超过 560）。这是「侧栏」和「半个屏」的分界

        之前只在 `total > want + 420` 时才摆，窗口一窄就完全不生效，
        于是 pane 按内容要宽度 —— 越聊越宽。现在无条件摆，夹在区间里。
        """
        try:
            total = self.paned.winfo_width()
            if total < U.px(320):
                return
            cap = min(U.px(DOCK_W_MAX), int(total * DOCK_W_RATIO))
            floor = min(U.px(DOCK_W_MIN), max(U.px(260), int(total * 0.30)))
            want = U.px(int(self.agent_cfg.data.get("dock_width")
                            or DOCK_W_DEFAULT))
            want = max(floor, min(want, cap))
            # Tk 的 PanedWindow 没有 index()，只能从 panes() 里自己数
            panes = [str(p) for p in self.paned.panes()]
            idx = panes.index(str(self.ai_dock)) - 1
            if idx >= 0:
                self.paned.sash_place(idx, max(0, total - want), 0)
        except (tk.TclError, ValueError):
            pass

    def _ai_sash_done(self, _e=None) -> None:
        """用户拖完分隔条：把当前宽度记下来，下次打开还是这个宽度。"""
        if not getattr(self, "_ai_dock_on", False):
            return
        self._ai_dock_remember()

    def _ai_dock_remember(self) -> None:
        """记住当前实际宽度（存**逻辑**像素，跟 DOCK_W_* 同一套单位）。"""
        try:
            w = self.ai_dock.winfo_width()
        except tk.TclError:
            return
        if w < U.px(240):
            return
        # 除以缩放比才是逻辑像素。之前这里存的是物理像素，
        # 而读取时又乘了一遍 U.px —— 高 DPI 下每次开关都变宽一截。
        logical = int(round(w / max(0.5, float(U.S))))
        if logical == int(self.agent_cfg.data.get("dock_width") or 0):
            return
        self.agent_cfg.data["dock_width"] = logical
        self.agent_cfg.save()

    def _ai_dock_reflow(self, event=None) -> None:
        """侧栏宽度变了，消息卡片跟着重排 —— 否则文字会一直按旧宽度折行。

        **不在这里记宽度**：这个回调在程序摆放分隔条时也会被触发，
        边摆边记会把自己越记越偏（旧代码就这么把记忆搞坏的）。

        拖动边缘时 <Configure> 是按像素来的，气泡一多，同步遍历所有 Label
        逐个改 wraplength 会让窗口像卡住一样（每次都要重排整段文本）。
        所以这里只记宽度、debounce 70ms，等手停下来再统一改。
        """
        w = int(event.width if event is not None else self.ai_dock.winfo_width())
        if self._ai_wrap_w and abs(w - self._ai_wrap_w) < 2:
            return
        self._ai_wrap_w = w
        if self._ai_reflow_job:
            try:
                self.after_cancel(self._ai_reflow_job)
            except Exception:  # noqa: BLE001
                pass
        self._ai_reflow_job = self.after(70, self._ai_apply_wrap)

    def _ai_apply_wrap(self) -> None:
        self._ai_reflow_job = None
        wrap = max(U.px(180), int(self._ai_wrap_w or 0) - U.px(46))
        for lb in self._ai_flow_labels:
            try:
                if int(lb.cget("wraplength")) != wrap:
                    lb.configure(wraplength=wrap)
            except (tk.TclError, ValueError):
                pass

    # ================================================================ 卡片
    def _ai_label(self, parent, text: str, fg: str, font, wrap: bool = True):
        lb = tk.Label(parent, text=text, bg=parent.cget("bg"), fg=fg, font=font,
                      justify="left", anchor="w", bd=0,
                      wraplength=max(U.px(180),
                                     (self.ai_dock.winfo_width() or U.px(400))
                                     - U.px(46)) if wrap else 0)
        return lb

    def _ai_note(self, text: str, tone: str = "mute",
                 record: bool = False) -> None:
        """一行灰字提示。默认**不**进 transcript —— 这些是过程提示
        （「套用技能…」「已停止」），重放时再刷一遍只会让历史更乱。"""
        fg = {"mute": U.T.text_mute, "dim": U.T.text_dim,
              "ok": U.T.ok, "warn": U.T.warn, "err": U.T.err}.get(tone, U.T.text_dim)
        wrap = tk.Frame(self._ai_flow, bg=U.T.page)
        wrap.pack(fill="x", pady=(U.px(3), U.px(3)), padx=U.px(4))
        lb = self._ai_label(wrap, text, fg, U.FONT_XS)
        lb.pack(fill="x")
        self._ai_flow_labels.append(lb)
        if record:
            self._ai_sess_remember("note", text)

    def _ai_hide_welcome(self) -> None:
        """第一条消息一来就把引导页收掉（只收一次，按会话各算各的）。"""
        st = self._ai_sess_state()
        keep = st.get("welcome") if st is not None else None
        todo = keep if keep is not None else getattr(self, "_ai_welcome_widgets", [])
        for w in todo or []:
            try:
                w.pack_forget()
            except tk.TclError:
                pass
        if st is not None:
            st["welcome"] = []
        self._ai_welcome_widgets = []

    def _ai_bubble(self, text: str, attach=None, record: bool = True,
                   badge: str = "") -> None:
        """用户消息：靠右的浅色块，和助手内容区分开。

        左边留出一大段空白是刻意的 —— 宽度差一眼就能看出「谁说的」，
        不用额外画头像。带了附件就在文字下面补一行文件名：发出去之后
        用户还得能看见「我到底给了它什么」。

        `badge` 用来区分「这一轮跑到一半插进去的话」，跟普通提问同色但带标签。
        """
        self._ai_hide_welcome()
        outer = tk.Frame(self._ai_flow, bg=U.T.page)
        outer.pack(fill="x", pady=(U.px(12), U.px(6)))
        box = tk.Frame(outer, bg=U.T.brand_soft, bd=0)
        box.pack(side="right", anchor="e", padx=(U.px(46), U.px(4)))
        if badge:
            tk.Label(box, text=badge, bg=U.T.brand_soft, fg=U.T.brand,
                     font=U.FONT_XS, bd=0, anchor="e",
                     padx=U.px(12), pady=U.px(2)).pack(fill="x")
        lb = tk.Label(box, text=text, bg=U.T.brand_soft, fg=U.T.text,
                      font=U.FONT_S, justify="left", anchor="w",
                      wraplength=max(U.px(150),
                                     (self.ai_dock.winfo_width() or U.px(400))
                                     - U.px(110)),
                      padx=U.px(12), pady=U.px(8))
        lb.pack()
        self._ai_flow_labels.append(lb)
        if attach:
            names = "、".join(_clip_str(n, 18) for n in attach[:4])
            if len(attach) > 4:
                names += f" 等 {len(attach)} 个"
            tk.Label(box, text="▤ " + names, bg=U.T.brand_soft,
                     fg=U.T.text_dim, font=U.FONT_XS, justify="left",
                     anchor="w", wraplength=max(U.px(150),
                                                (self.ai_dock.winfo_width()
                                                 or U.px(400)) - U.px(110)),
                     padx=U.px(12), pady=U.px(3)).pack(fill="x")
        if record:
            self._ai_sess_remember("user", text, attach)

    def _ai_reply(self, text: str, step: int | None = None,
                  record: bool = True) -> None:
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
        if record:
            self._ai_sess_remember("ai", text)

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
                self._ai_send_icon(bool(getattr(self, "ai_busy", False)))
            except (AttributeError, tk.TclError):
                pass
            try:
                self.btn_ai_model.set_text(self._ai_model_label())
            except (AttributeError, tk.TclError):
                pass
            self._ai_refresh_scope_btn()
            self._ai_refresh_composer()
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

    # ================================================================ 会话
    # 一次对话 = 一个会话。页签就是「这次对话」，关掉只是收起来，
    # 记录还在「所有会话」里，随时能翻回来接着聊。
    def _ai_sess_boot(self) -> None:
        """建会话底座：接着上次那个聊，没有就开一个空的。"""
        store = self._ai_sess_store
        store.ensure()
        got = store.all()
        sess = got[0] if got else store.create()
        self._ai_sessions[sess.id] = sess
        self._ai_sess = sess
        self._ai_sess_order = [sess.id]
        st = self._ai_sess_mount(sess)
        try:
            st["scroll"].pack(fill="both", expand=True, padx=U.px(8))
        except tk.TclError:
            pass
        self._ai_sess_bind(sess, st)
        self._ai_sess_replay(sess, st)
        self._ai_tab_paint()

    def _ai_sess_mount(self, sess) -> dict:
        """给一个会话造它的消息流容器（造了先不摆）。"""
        sc = _Scroll(self.ai_dock, U.T.page)
        st = {"scroll": sc, "flow": sc.inner, "labels": [], "md": [],
              "cards": [], "attach": [], "files": dict(sess.files or {}),
              "painted": False}
        self._ai_open[sess.id] = st
        return st

    def _ai_sess_bind(self, sess, st) -> None:
        """把「当前会话」的指针全部指向这一个会话。

        这些属性原来是单体字段（一个助手只有一份），现在变成「当前会话的」。
        全部换指针，比给每个方法加参数改动小得多，也不会漏掉某处。
        """
        self._ai_scroll = st["scroll"]
        self._ai_flow = st["flow"]
        self._ai_flow_labels = st["labels"]
        self._ai_md_views = st["md"]
        self._ai_tool_cards = st["cards"]
        self._ai_attach = st["attach"]
        self._ai_files_seen = st["files"]
        self._ai_sess = sess
        # 直接给同一个 list：Agent 往里 append 就等于写进会话
        self.agent.history = sess.history
        self.agent.tokens = int(sess.tokens or 0)

    def _ai_sess_sync(self) -> None:
        """把当前会话的实时状态写回会话对象并落盘。"""
        sess = getattr(self, "_ai_sess", None)
        if sess is None:
            return
        sess.tokens = int(getattr(self.agent, "tokens", 0) or 0)
        sess.files = dict(self._ai_files_seen or {})
        sess.trim_history()
        self._ai_sess_store.save(sess)

    def _ai_sess_save(self) -> None:
        self._ai_sess_sync()

    def _ai_sess_activate(self, sid: str) -> None:
        """切到某个会话：换容器 + 换上下文，必要时把历史重画出来。"""
        cur = getattr(self, "_ai_sess", None)
        if cur is not None and sid == cur.id:
            self._ai_tab_paint()
            return
        self._ai_sess_sync()
        sess = self._ai_sessions.get(sid)
        if sess is None:
            sess = self._ai_sess_store.load(sid)
            if sess is None:
                self.toast("这个会话的记录已经不在了", "warn")
                return
            self._ai_sessions[sid] = sess
        st = self._ai_open.get(sid) or self._ai_sess_mount(sess)
        if self._ai_scroll is not None:
            try:
                self._ai_scroll.pack_forget()
            except tk.TclError:
                pass
        try:
            st["scroll"].pack(fill="both", expand=True, padx=U.px(8),
                              before=self._ai_foot)
        except tk.TclError:
            pass
        self._ai_sess_bind(sess, st)
        if sid not in self._ai_sess_order:
            self._ai_sess_order.append(sid)
        self._ai_sess_replay(sess, st)
        self._ai_attach_paint()
        self._ai_refresh_dock_state()
        self._ai_tab_paint()

    def _ai_sess_replay(self, sess, st) -> None:
        """按 transcript 把气泡重画一遍（每个会话只画一次）。"""
        if st.get("painted"):
            return
        st["painted"] = True
        if not sess.transcript:
            self._ai_paint_welcome()
            return
        self._ai_hide_welcome()
        for rec in sess.transcript:
            role = str(rec.get("role") or "")
            text = str(rec.get("text") or "")
            if not text:
                continue
            if role == "user":
                self._ai_bubble(text, rec.get("attach"), record=False)
            elif role == "ai":
                self._ai_reply(text, record=False)
            else:
                self._ai_note(text, "dim", record=False)
        try:
            self._ai_scroll.to_end()
        except (AttributeError, tk.TclError):
            pass
        self._ai_dock_reflow()

    def _ai_sess_remember(self, role: str, text: str, attach=None) -> None:
        """把一条消息记进当前会话，并在首问后给它起名字。"""
        sess = getattr(self, "_ai_sess", None)
        if sess is None or not text:
            return
        sess.say(role, text, attach)
        if role == "user" and sess.auto_title(text):
            self._ai_tab_paint()
        self._ai_sess_store.save(sess)

    # ------------------------------------------------------------ 页签
    def _ai_tab_paint(self) -> None:
        box = getattr(self, "_ai_tabs", None)
        if box is None:
            return
        try:
            for w in box.winfo_children():
                w.destroy()
        except tk.TclError:
            return
        ids = [i for i in self._ai_sess_order if i in self._ai_open]
        if not ids:
            return
        try:
            avail = self.ai_dock.winfo_width() - U.px(30)
        except tk.TclError:
            avail = U.px(DOCK_W_DEFAULT)
        chip = U.px(102)
        fit = max(1, int(max(avail, chip) // chip))
        show = ids[:fit]
        for sid in show:
            self._ai_tab_chip(box, sid)
        rest = len(ids) - len(show)
        if rest > 0:
            more = tk.Label(box, text=f"+{rest}", bg=U.T.page,
                            fg=U.T.text_mute, font=U.FONT_XS,
                            bd=0, cursor="hand2", padx=U.px(4))
            more.pack(side="left", pady=U.px(4))
            more.bind("<Button-1>", lambda e: self._ai_sess_list())
        plus = tk.Label(box, text="＋", bg=U.T.page, fg=U.T.text_dim,
                        font=U.FONT_S, bd=0, cursor="hand2", padx=U.px(6))
        plus.pack(side="left", pady=U.px(2))
        plus.bind("<Button-1>", lambda e: self._ai_tab_new())
        plus.bind("<Enter>", lambda e: plus.configure(fg=U.T.brand))
        plus.bind("<Leave>", lambda e: plus.configure(fg=U.T.text_dim))

    def _ai_tab_chip(self, box, sid: str) -> None:
        sess = self._ai_sessions.get(sid)
        title = (sess.title if sess is not None else "会话") or "会话"
        if len(title) > 7:
            title = title[:6] + "…"
        cur = sess is not None and sess is getattr(self, "_ai_sess", None)
        bg = U.T.brand_soft if cur else U.T.alt
        fg = U.T.brand if cur else U.T.text_dim
        f = tk.Frame(box, bg=bg, cursor="hand2")
        f.pack(side="left", padx=(0, U.px(4)), pady=U.px(3))
        lb = tk.Label(f, text=title, bg=bg, fg=fg, font=U.FONT_XS, bd=0,
                      padx=U.px(7), pady=U.px(4))
        lb.pack(side="left")
        x = tk.Label(f, text="×", bg=bg, fg=U.T.text_mute, font=U.FONT_XS,
                     bd=0, padx=U.px(5), pady=U.px(4), cursor="hand2")
        x.pack(side="left")

        def go(_e=None, s=sid):
            self._ai_sess_activate(s)

        for w in (f, lb):
            w.bind("<Button-1>", go)
        x.bind("<Button-1>", lambda e, s=sid: self._ai_tab_close(s))
        x.bind("<Enter>", lambda e: x.configure(fg=U.T.err))
        x.bind("<Leave>", lambda e: x.configure(fg=U.T.text_mute))

    def _ai_tab_new(self, _e=None):
        """＋：开一个新会话。新页签，不是清空当前那个。"""
        self._ai_sess_sync()
        sess = self._ai_sess_store.create()
        self._ai_sessions[sess.id] = sess
        self._ai_sess_order.append(sess.id)
        self._ai_sess_mount(sess)
        self._ai_sess_activate(sess.id)
        try:
            self._ai_input.txt.focus_set()
        except tk.TclError:
            pass
        return sess

    def _ai_tab_close(self, sid: str) -> None:
        """关页签：只是把它从横条上收起来，记录留着，随时能翻回来。"""
        cur = getattr(self, "_ai_sess", None)
        is_cur = cur is not None and sid == cur.id
        if is_cur and getattr(self, "ai_busy", False):
            self.toast("这个会话正在执行，先停止再关", "warn")
            return
        self._ai_sess_sync()
        st = self._ai_open.pop(sid, None)
        if st is not None:
            try:
                st["scroll"].pack_forget()
            except tk.TclError:
                pass
            try:
                st["scroll"].destroy()
            except tk.TclError:
                pass
        if sid in self._ai_sess_order:
            self._ai_sess_order.remove(sid)
        if is_cur:
            self._ai_scroll = None
            self._ai_flow = None
            self._ai_sess = None           # 让 activate 不会提前返回
            left = [i for i in self._ai_sess_order if i in self._ai_open]
            if left:
                self._ai_sess_activate(left[-1])
            else:
                self._ai_tab_new()
            return
        self._ai_tab_paint()

    def _ai_sess_list(self) -> None:
        """「历史会话」：盘上的记录全列出来，按最近聊过倒序，能搜，点一条接着聊。"""
        store = self._ai_sess_store
        items = store.all()          # 已经按 updated 倒序
        if not items:
            self.toast("还没有任何会话记录", "warn")
            return
        top = tk.Toplevel(self)
        top.title("历史会话")
        top.configure(bg=U.T.page)
        try:
            top.transient(self.winfo_toplevel())
            top.geometry(f"{U.px(500)}x{U.px(580)}")
        except tk.TclError:
            pass

        head = tk.Frame(top, bg=U.T.page)
        head.pack(fill="x", padx=U.px(14), pady=(U.px(12), U.px(6)))
        tk.Label(head, text=f"历史会话 · {len(items)} 个", bg=U.T.page,
                 fg=U.T.text, font=U.FONT_T, bd=0).pack(side="left")
        tk.Button(head, text="新建会话", bd=0, relief="flat",
                  bg=U.T.brand, fg="#FFFFFF", font=U.FONT_XS,
                  activebackground=U.T.brand_hover, activeforeground="#FFFFFF",
                  padx=U.px(10), pady=U.px(3), highlightthickness=0,
                  command=lambda: (top.destroy(), self._ai_tab_new())).pack(
            side="right")

        # 搜索框：会话多了以后翻列表是最笨的找法
        sr = tk.Frame(top, bg=U.T.input_bg, bd=0, highlightthickness=1,
                      highlightbackground=U.T.input_border)
        sr.pack(fill="x", padx=U.px(14), pady=(0, U.px(8)))
        var = tk.StringVar()
        ent = tk.Entry(sr, textvariable=var, bd=0, bg=U.T.input_bg,
                       fg=U.T.text, insertbackground=U.T.text, font=U.FONT_S,
                       highlightthickness=0)
        ent.pack(fill="x", padx=(U.px(22), U.px(8)), pady=U.px(5))
        tk.Label(sr, text="⌕", bg=U.T.input_bg, fg=U.T.text_mute,
                 font=U.FONT_S, bd=0).place(x=U.px(7), rely=0.5, anchor="w")
        ph = tk.Label(sr, text="搜索标题或内容", bg=U.T.input_bg,
                      fg=U.T.text_mute, font=U.FONT_XS, bd=0)
        ph.place(x=U.px(22), rely=0.5, anchor="w")
        ph.bind("<Button-1>", lambda e: ent.focus_set())

        body = tk.Frame(top, bg=U.T.page)
        body.pack(fill="both", expand=True, padx=U.px(8), pady=(0, U.px(10)))

        def fill(inner):
            def render(*_a):
                for w in inner.winfo_children():
                    w.destroy()
                q = var.get().strip().lower()
                hit = 0
                for s in items[:200]:
                    hay = f"{s.title or ''} {s.digest() or ''}".lower()
                    if q and q not in hay:
                        continue
                    hit += 1
                    row = tk.Frame(inner, bg=U.T.card, cursor="hand2")
                    row.pack(fill="x", pady=(0, U.px(3)))
                    cell = tk.Frame(row, bg=U.T.card)
                    cell.pack(side="left", fill="x", expand=True,
                              padx=U.px(10), pady=U.px(6))
                    line = tk.Frame(cell, bg=U.T.card)
                    line.pack(fill="x")
                    tk.Label(line, text=s.title or "会话", bg=U.T.card,
                             fg=U.T.text, font=U.FONT_S, bd=0,
                             anchor="w").pack(side="left")
                    tk.Label(line, text=time.strftime(
                        "%m-%d %H:%M", time.localtime(s.updated or s.created)),
                        bg=U.T.card, fg=U.T.text_mute, font=U.FONT_XS,
                        bd=0).pack(side="right")
                    tk.Label(cell, text=s.digest(), bg=U.T.card,
                             fg=U.T.text_dim, font=U.FONT_XS, bd=0,
                             anchor="w", wraplength=U.px(360),
                             justify="left").pack(fill="x", pady=(U.px(2), 0))
                    tk.Label(cell, text=f"{len(s.transcript)} 条 · "
                                        f"{s.tokens} tokens",
                             bg=U.T.card, fg=U.T.text_mute, font=U.FONT_XS,
                             bd=0, anchor="w").pack(fill="x")

                    def open_it(_e=None, sid=s.id):
                        top.destroy()
                        if sid in self._ai_open:
                            self._ai_sess_activate(sid)
                        else:
                            self._ai_sessions.pop(sid, None)
                            self._ai_sess_activate(sid)

                    for w in (row, cell) + tuple(cell.winfo_children()) + tuple(
                            line.winfo_children()):
                        w.bind("<Button-1>", open_it)
                if not hit:
                    tk.Label(inner, text="没有匹配的会话", bg=U.T.page,
                             fg=U.T.text_mute, font=U.FONT_XS,
                             bd=0).pack(pady=U.px(14))

            def hint_ph(*_a):
                try:
                    if var.get():
                        ph.place_forget()
                    else:
                        ph.place(x=U.px(22), rely=0.5, anchor="w")
                except tk.TclError:
                    pass

            var.trace_add("write", render)
            var.trace_add("write", hint_ph)
            render()

        _scroll_list(body, U.T.page, 430, fill)
        top.bind("<Escape>", lambda e: top.destroy())
        try:
            ent.focus_set()
        except tk.TclError:
            pass

    # ================================================================ 附件
    def _ai_attach_dir(self) -> Path:
        """粘贴进来的图片落在哪儿。跟 agent.json 同一个 data/ 目录，
        导出/备份的时候一起带走，不会散落在临时目录里。"""
        try:
            return Path(self.agent_cfg.path).parent / "attachments"
        except (AttributeError, TypeError, ValueError):
            return Path("data") / "attachments"

    def _ai_attach_paint(self) -> None:
        """重画附件条。空的时候整条收起来 —— 没附件还占一行高度最碍事。"""
        bar = getattr(self, "_ai_attach_bar", None)
        if bar is None:
            return
        for w in bar.winfo_children():
            w.destroy()
        if not self._ai_attach:
            try:
                bar.pack_forget()
            except tk.TclError:
                pass
            return
        try:
            bar.pack(fill="x", padx=U.px(6), pady=(U.px(6), 0),
                     before=self._ai_input)
        except tk.TclError:
            pass
        # 两列：侧栏最窄也有 300px，一张 chip 放文件名放不下更多信息了
        bar.columnconfigure(0, weight=1, uniform="att")
        bar.columnconfigure(1, weight=1, uniform="att")
        for i, a in enumerate(self._ai_attach):
            self._ai_attach_chip(bar, a).grid(
                row=i // 2, column=i % 2, sticky="ew",
                padx=(0, U.px(4)), pady=(0, U.px(4)))

    def _ai_attach_chip(self, parent, a: dict) -> tk.Frame:
        card = tk.Frame(parent, bg=U.T.alt, bd=0)
        row = tk.Frame(card, bg=U.T.alt)
        row.pack(fill="x", padx=U.px(6), pady=U.px(4))
        tk.Label(row, text="▣" if a.get("image") else "▤", bg=U.T.alt,
                 fg=U.T.brand, font=U.FONT_XS, bd=0).pack(side="left")
        name = a.get("name") or ""
        if len(name) > 14:
            name = name[:11] + "…"
        lb = tk.Label(row, text=name, bg=U.T.alt, fg=U.T.text,
                      font=U.FONT_XS, bd=0, anchor="w")
        lb.pack(side="left", padx=(U.px(4), 0))
        x = tk.Label(row, text="×", bg=U.T.alt, fg=U.T.text_mute,
                     font=U.FONT_XS, bd=0, cursor="hand2")
        x.pack(side="right", padx=(U.px(4), 0))
        x.bind("<Button-1>", lambda e, k=a["key"]: self._ai_attach_remove(k))
        return card

    def _ai_attach_paths(self, paths, *, source: str = "添加") -> int:
        """把一批路径加进附件：去重、必须真实存在、最多 MAX_ATTACH 个。"""
        added = 0
        for raw in paths:
            s = str(raw or "").strip().strip('"')
            if not s:
                continue
            try:
                path = Path(s)
                if not path.is_file():
                    continue
            except (OSError, ValueError):
                continue
            key = str(path).casefold() if os.name == "nt" else str(path)
            if any(a["key"] == key for a in self._ai_attach):
                continue
            if len(self._ai_attach) >= MAX_ATTACH:
                self.toast(f"最多带 {MAX_ATTACH} 个附件，先删几个再加", "warn")
                break
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            self._ai_attach.append({
                "key": key, "path": str(path), "name": path.name,
                "size": size,
                "image": path.suffix.lower() in IMAGE_EXT,
                "source": source,
            })
            self._ai_files_seen.setdefault(str(path), source)
            added += 1
        if added:
            self._ai_attach_paint()
        return added

    def _ai_attach_remove(self, key: str) -> None:
        # 原地删：_ai_attach 是会话状态里的那个 list，换新对象会让它脱钩
        self._ai_attach[:] = [a for a in self._ai_attach if a["key"] != key]
        self._ai_attach_paint()

    def _ai_attach_clear(self) -> None:
        self._ai_attach[:] = []
        self._ai_attach_paint()

    def _ai_add_files(self) -> None:
        """＋ → 添加文件。多选，选完直接挂在输入框上。"""
        paths = filedialog.askopenfilenames(
            parent=self, title="附加给助手的文件（可多选）")
        if not paths:
            return
        n = self._ai_attach_paths(paths)
        if n:
            self.toast(f"加了 {n} 个附件", "ok")
        else:
            self.toast("这些文件已经在附件里了", "warn")

    def _ai_paste(self, _e=None):
        """Ctrl+V：文本归 Tk，图片/文件归我们。

        先问一句「剪贴板里有文本吗」—— 有文本就让 Tk 走它自己的粘贴，那里面有
        输入法状态、撤销栈、选区替换，我们插手只会出错。取不到文本（TclError）
        说明剪贴板里根本不是文字，才轮到读文件/位图。
        """
        try:
            self.clipboard_get()
            return None
        except tk.TclError:
            pass
        paths, msg = clipfile.grab(self._ai_attach_dir())
        if paths:
            n = self._ai_attach_paths(paths, source="粘贴")
            if n:
                self.toast(f"{msg}，已加到附件", "ok")
            else:
                self.toast("这张图/这个文件已经在附件里了", "warn")
            return "break"
        self.toast(msg, "warn")
        return "break"

    def _ai_scan_files(self, text: str, source: str = "对话") -> None:
        """从工具输出里认出「本机真实存在的文件」，记进「引用对话中的文件」。

        远端路径不算：那台机器上的文件本地点不开，列出来就是一条死链接。
        所以这里唯一的标准是 os.path.isfile —— 宁可少列，不能点了个空。
        """
        if not text:
            return
        for m in _PATH_RE.finditer(text):
            p = m.group(0).rstrip(".,;:)]}，。；：）】")
            if len(p) < 5:
                continue
            try:
                if not os.path.isfile(p):
                    continue
            except (OSError, ValueError):
                continue
            self._ai_files_seen.setdefault(p, source)

    def _ai_session_files(self) -> list[tuple[str, str]]:
        """本会话出现过的、当前还存在的文件。已经挂成附件的不再重复列出。"""
        in_attach = {a["path"] for a in self._ai_attach}
        out: list[tuple[str, str]] = []
        for p, src in self._ai_files_seen.items():
            if p in in_attach:
                continue
            try:
                if not os.path.isfile(p):
                    continue
            except (OSError, ValueError):
                continue
            out.append((p, src))
        return out[:40]

    # ================================================================ 连接器
    def _ai_hosts_menu(self) -> None:
        _popup(self, self.btn_ai_hosts, self._ai_conn_build, width=U.px(250))

    def _ai_conn_build(self, host) -> None:
        """连接器面板：AI 能在哪些连接上执行（本机 / 各会话 / 全部）。

        「当前设备」和「全部主机」互斥，勾主机则自动取消这两项；一个都不勾就
        退回「当前设备」—— 空白状态不能是一种可执行状态。

        旧「当前设备」按钮和 ＋ → 连接器 共用这一份逻辑：两个入口两种形态，
        数据模型只有一套，免得哪天改一处漏一处。
        """
        slots = list(self.sessions)
        head = tk.Frame(host, bg=U.T.card)
        head.pack(fill="x", padx=U.px(10), pady=(U.px(9), 0))
        tk.Label(head, text="连接器", bg=U.T.card, fg=U.T.text,
                 font=U.FONT_S, bd=0).pack(side="left")
        tk.Label(head, text=f"{len(slots) + 1} 个", bg=U.T.card,
                 fg=U.T.text_mute, font=U.FONT_XS, bd=0).pack(side="right")
        tk.Label(host, text="助手只在你勾选的连接上执行命令", bg=U.T.card,
                 fg=U.T.text_mute, font=U.FONT_XS, bd=0, anchor="w").pack(
            fill="x", padx=U.px(10), pady=(0, U.px(4)))

        def build(host2):
            host = host2
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

        build(host)

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

    # ------------------------------------------------------------ ＋ 菜单
    def _ai_menu_row(self, parent, glyph: str, label: str, hint: str = "",
                     on_click=None, *, arrow: bool = False) -> tk.Frame:
        """一级菜单的一行：符号 + 名字 + 右侧当前值（+ ›）。

        右侧那个「当前值」是这套菜单的关键：技能写「自动」、连接器写「当前设备」，
        不点开也知道现在是什么状态 —— 否则菜单只是个入口列表。
        """
        row = tk.Frame(parent, bg=U.T.card, cursor="hand2")
        row.pack(fill="x", padx=U.px(6), pady=U.px(1))
        gl = tk.Label(row, text=glyph, bg=U.T.card, fg=U.T.text_dim,
                      font=U.FONT_S, bd=0, width=2, anchor="center")
        gl.pack(side="left", padx=(U.px(4), U.px(2)), pady=U.px(7))
        lb = tk.Label(row, text=label, bg=U.T.card, fg=U.T.text,
                      font=U.FONT_S, bd=0, anchor="w")
        lb.pack(side="left")
        kids: list[tk.Widget] = [gl, lb]
        if arrow:
            ar = tk.Label(row, text="›", bg=U.T.card, fg=U.T.text_mute,
                          font=U.FONT_S, bd=0)
            ar.pack(side="right", padx=(0, U.px(4)))
            kids.append(ar)
        if hint:
            hi = tk.Label(row, text=hint, bg=U.T.card, fg=U.T.text_mute,
                          font=U.FONT_XS, bd=0)
            hi.pack(side="right", padx=(0, U.px(6)))
            kids.append(hi)

        def enter(_e=None):
            for w in kids + [row]:
                try:
                    w.configure(bg=U.T.brand_soft)
                except tk.TclError:
                    pass

        def leave(_e=None):
            for w in kids + [row]:
                try:
                    w.configure(bg=U.T.card)
                except tk.TclError:
                    pass

        for w in kids + [row]:
            if on_click is not None:
                w.bind("<Button-1>", on_click)
            w.bind("<Enter>", enter)
            w.bind("<Leave>", leave)
        return row

    def _ai_plus_menu(self) -> None:
        """底部「＋」：一级分组，带 › 的点开是并排的二级浮层。

        形态照 WorkBuddy：会话窄，常驻 chip 全收进来；每一项右边写着「现在是
        什么」，右上角一个 › 表示「点开还有一层」。技能与连接器必须分两层 ——
        把十几个技能直接摊在一级菜单里，那菜单就没法看了。
        """
        holder: dict = {}

        def build(host):
            def close():
                try:
                    holder["top"].destroy()
                except (KeyError, tk.TclError):
                    pass

            def run(fn):
                def hook(_e=None):
                    close()
                    fn()
                return hook

            def sub(kind):
                return lambda _e=None: self._ai_open_sub(kind)

            self._ai_menu_row(host, "＋", "添加文件", "",
                              run(self._ai_add_files))
            self._ai_menu_row(host, "@", "引用对话中的文件",
                              f"{len(self._ai_session_files())} 个",
                              sub("quote"), arrow=True)
            U.hsep(host, "card", pady=(U.px(5), U.px(4)))
            self._ai_menu_row(
                host, "◑", "模式",
                MODE_LABEL.get(self.agent_cfg.mode, "").split(" ·")[0],
                sub("mode"), arrow=True)
            self._ai_menu_row(host, "◆", "技能", self._ai_skill_now or "自动",
                              sub("skill"), arrow=True)
            self._ai_menu_row(host, "⌁", "连接器", self._ai_scope_label(),
                              sub("conn"), arrow=True)
            U.hsep(host, "card", pady=(U.px(5), U.px(4)))
            self._ai_menu_row(host, "⌄", "模型管理", "",
                              run(self.ai_model_settings))
            self._ai_menu_row(host, "×", "清空会话", "",
                              run(self.ai_dock_clear))

        # 再点一次 ＋ 就是收起。点 ＋ 时主窗口的全局点击处理已经把菜单关了，
        # 所以这里还得看「刚刚是不是刚被那次点击关掉」—— 否则表现为「点两次没反应」。
        old = getattr(self, "_ai_plus_top", None)
        try:
            still_open = old is not None and old.winfo_exists()
        except tk.TclError:
            still_open = False
        if still_open:
            self._ai_close_menu()
            return None
        if time.time() - float(getattr(self, "_menu_closed_at", 0) or 0) < 0.30:
            return None

        top = _popup(self, self.btn_ai_plus, build, width=U.px(252))
        holder["top"] = top
        self._ai_plus_top = top
        return top

    def _ai_open_sub(self, kind: str) -> None:
        """在一级菜单右边开二级浮层。一级已经没了就什么都不做。"""
        top = getattr(self, "_ai_plus_top", None)
        try:
            if top is None or not top.winfo_exists():
                return
        except tk.TclError:
            return
        spec = {
            "quote": (self._ai_files_build, 340),
            "mode": (self._ai_mode_build, 300),
            "skill": (self._ai_skill_build, 340),
            "conn": (self._ai_conn_build, 310),
        }.get(kind)
        if spec is None:
            return
        _submenu(top, spec[0], width=U.px(spec[1]))

    def _ai_close_menu(self) -> None:
        top = getattr(self, "_ai_plus_top", None)
        try:
            if top is not None and top.winfo_exists():
                _close_chain(top)
        except tk.TclError:
            pass

    def _ai_close_all_menus(self) -> None:
        """把输入区可能同时开着的三个浮层一起收掉（＋ / 权限 / 思考）。

        三个浮层各自独立，`_ai_close_menu` 只管 ＋。在「模式」里选完一项之后
        如果只关 ＋，从「权限」按钮打开的那个菜单会挂在那儿 —— 所以要多一个
        收全部的入口。
        """
        for attr in ("_ai_plus_top", "_ai_perm_top", "_ai_think_top"):
            top = getattr(self, attr, None)
            try:
                if top is not None and top.winfo_exists():
                    _close_chain(top)
            except tk.TclError:
                pass
            setattr(self, attr, None)

    # ------------------------------------------------------------ 二级：引用文件
    def _ai_files_build(self, host) -> None:
        """＋ → 引用对话中的文件。"""
        files = self._ai_session_files()
        head = tk.Frame(host, bg=U.T.card)
        head.pack(fill="x", padx=U.px(10), pady=(U.px(9), 0))
        tk.Label(head, text="引用对话中的文件", bg=U.T.card, fg=U.T.text,
                 font=U.FONT_S, bd=0).pack(side="left")
        tk.Label(head, text=f"{len(files)} 个", bg=U.T.card, fg=U.T.text_mute,
                 font=U.FONT_XS, bd=0).pack(side="right")

        if not files:
            tk.Label(host,
                     text="这次对话里还没出现过本机文件。\n"
                          "用「添加文件」，或者直接 Ctrl+V 贴一张图。",
                     bg=U.T.card, fg=U.T.text_mute, font=U.FONT_XS,
                     justify="left", anchor="w").pack(
                fill="x", padx=U.px(10), pady=U.px(12))
            return

        def fill(inner):
            for p, src in files:
                row = tk.Frame(inner, bg=U.T.card, cursor="hand2")
                row.pack(fill="x", pady=U.px(1))
                ic = tk.Label(row, text="▣" if Path(p).suffix.lower() in IMAGE_EXT
                              else "▤", bg=U.T.card, fg=U.T.brand,
                              font=U.FONT_XS, bd=0)
                ic.pack(side="left", padx=(U.px(8), U.px(5)))
                cell = tk.Frame(row, bg=U.T.card)
                cell.pack(side="left", fill="x", expand=True)
                tk.Label(cell, text=Path(p).name, bg=U.T.card, fg=U.T.text,
                         font=U.FONT_S, bd=0, anchor="w").pack(fill="x")
                tk.Label(cell, text=_clip_str(str(Path(p).parent), 36),
                         bg=U.T.card, fg=U.T.text_mute, font=U.FONT_XS,
                         bd=0, anchor="w").pack(fill="x")
                tk.Label(row, text=src, bg=U.T.card, fg=U.T.text_mute,
                         font=U.FONT_XS, bd=0).pack(side="right",
                                                    padx=U.px(8))

                def pick(_e=None, path=p):
                    self._ai_close_menu()
                    if self._ai_attach_paths([path], source="引用"):
                        self.toast(f"已引用 {Path(path).name}", "ok")
                    else:
                        self.toast("这个文件已经在附件里了", "warn")

                for w in (row, ic, cell) + tuple(cell.winfo_children()):
                    w.bind("<Button-1>", pick)

        _scroll_list(host, U.T.card, 250, fill)

    # ------------------------------------------------------------ 二级：模式
    def _ai_mode_build(self, host) -> None:
        """＋ → 模式：自动 / 逐条确认 / 只读。"""
        cur = self.agent_cfg.mode
        head = tk.Frame(host, bg=U.T.card)
        head.pack(fill="x", padx=U.px(10), pady=(U.px(9), U.px(4)))
        tk.Label(head, text="执行模式", bg=U.T.card, fg=U.T.text,
                 font=U.FONT_S, bd=0).pack(side="left")
        for key, label in MODE_OPTS:
            title, _, sub = label.partition(" · ")
            on = key == cur
            row = tk.Frame(host, bg=U.T.brand_soft if on else U.T.card,
                           cursor="hand2")
            row.pack(fill="x", padx=U.px(6), pady=U.px(1))
            tick = tk.Label(row, text="✓" if on else "", bg=row.cget("bg"),
                            fg=U.T.brand, font=U.FONT_S, bd=0, width=2)
            tick.pack(side="left", padx=(U.px(4), 0), pady=U.px(6))
            cell = tk.Frame(row, bg=row.cget("bg"))
            cell.pack(side="left", fill="x", expand=True)
            tk.Label(cell, text=title, bg=row.cget("bg"), fg=U.T.text,
                     font=U.FONT_S, bd=0, anchor="w").pack(fill="x")
            tk.Label(cell, text=_clip_str(sub, 30), bg=row.cget("bg"),
                     fg=U.T.text_mute, font=U.FONT_XS, bd=0,
                     anchor="w").pack(fill="x")

            def pick(_e=None, k=key, t=title):
                self._ai_close_all_menus()
                self.agent_cfg.data["mode"] = k
                try:
                    self.agent_cfg.save()
                except OSError as e:
                    self.toast(f"存不下来：{e}", "err")
                    return
                self._ai_refresh_composer()
                self.toast(f"模式：{t}", "ok")

            for w in (row, tick, cell) + tuple(cell.winfo_children()):
                w.bind("<Button-1>", pick)

    # ------------------------------------------------------------ 输入区的三个档位
    def _ai_toggle_popup(self, attr: str, anchor, builder,
                         width: int = 210):
        """输入框旁的小浮层开关（再点一次收起）。

        `_popup` 的「点外面关掉」挂的是主窗口 `<Button-1>`，点锚点按钮时会先关
        一次、我们再开一次 —— 看起来就是「点了没反应」。所以这里两个都要判：
        已经开着 → 收起；刚刚被那次点击关掉（`_menu_closed_at` 在 0.3 秒内）→
        什么都不做。
        """
        old = getattr(self, attr, None)
        try:
            still_open = old is not None and old.winfo_exists()
        except tk.TclError:
            still_open = False
        if still_open:
            try:
                _close_chain(old)
            except tk.TclError:
                pass
            setattr(self, attr, None)
            return None
        if time.time() - float(getattr(self, "_menu_closed_at", 0) or 0) < 0.30:
            return None
        top = _popup(self, anchor, builder, width=U.px(width))
        setattr(self, attr, top)
        return top

    def _ai_perm_menu(self) -> None:
        """输入框旁的「权限」：自动 / 逐条确认 / 只读。

        选项直接复用 `_ai_mode_build`（＋ → 模式 里那份），一处改两处生效。
        """
        self._ai_toggle_popup("_ai_perm_top", self.btn_ai_perm,
                              self._ai_mode_build, width=248)

    def _ai_think_build(self, host) -> None:
        """思考档位：一列单选。当前档打勾，档位来自模型的 caps。"""
        levels = self.ai_cfg.thinking_levels()
        cur = self.ai_cfg.thinking()
        head = tk.Frame(host, bg=U.T.card)
        head.pack(fill="x", padx=U.px(10), pady=(U.px(9), U.px(4)))
        tk.Label(head, text="思考档位", bg=U.T.card, fg=U.T.text,
                 font=U.FONT_S, bd=0).pack(side="left")
        tk.Label(head, text=f"{len(levels)} 档", bg=U.T.card, fg=U.T.text_mute,
                 font=U.FONT_XS, bd=0).pack(side="right")
        for key in levels:
            on = key == cur
            row = tk.Frame(host, bg=U.T.brand_soft if on else U.T.card,
                           cursor="hand2")
            row.pack(fill="x", padx=U.px(6), pady=U.px(1))
            tick = tk.Label(row, text="✓" if on else "", bg=row.cget("bg"),
                            fg=U.T.brand, font=U.FONT_S, bd=0, width=2)
            tick.pack(side="left", padx=(U.px(4), 0), pady=U.px(6))
            lab = tk.Label(row, text=aiops.THINKING_LABEL.get(key, key),
                           bg=row.cget("bg"), fg=U.T.text, font=U.FONT_S,
                           bd=0, anchor="w")
            lab.pack(side="left", fill="x", expand=True)
            if key == "off":
                tk.Label(row, text="不发思考参数", bg=row.cget("bg"),
                         fg=U.T.text_mute, font=U.FONT_XS, bd=0).pack(
                    side="right", padx=(0, U.px(6)))

            def pick(_e=None, k=key):
                top = getattr(self, "_ai_think_top", None)
                try:
                    if top is not None:
                        _close_chain(top)
                except tk.TclError:
                    pass
                self._ai_think_top = None
                if not self.ai_cfg.set_thinking(k):
                    self.toast("这个档位不在这条模型的可用档位里", "warn")
                    return
                self._ai_refresh_composer()
                self.toast(f"思考档位：{aiops.THINKING_LABEL.get(k, k)}", "ok")

            for w in (row, tick, lab):
                w.bind("<Button-1>", pick)

    def _ai_think_menu(self) -> None:
        if not self.ai_cfg.thinking_levels():
            return
        self._ai_toggle_popup("_ai_think_top", self.btn_ai_think,
                              self._ai_think_build, width=190)

    def _ai_ctx_usage(self) -> tuple[str, str, str]:
        """(显示文案, 色调, 提示)。上限未知就不编数字。"""
        used = int(getattr(self.agent, "tokens", 0) or 0)
        limit = self.ai_cfg.context_limit()
        if not limit:
            text = f"{used:,} tok · 上限未知" if used else "上限未知"
            return text, "mute", "这条模型没填上下文窗口，去「模型管理」补一个"
        pct = used / limit if limit > 0 else 0.0
        text = f"{used:,} / {limit:,} · {pct * 100:.0f}%"
        if pct >= 0.95:
            return text, "err", "快满了，建议新建会话（这一轮再长就会丢前面的内容）"
        if pct >= 0.80:
            return text, "warn", "已经用掉八成，长任务建议新建会话"
        return text, "mute", "本轮已用 token / 模型上下文窗口"

    def _ai_refresh_composer(self) -> None:
        """输入区那三个小件的状态：权限文案、思考按钮、上下文用量。"""
        try:
            self.btn_ai_perm.set_text(
                MODE_LABEL.get(self.agent_cfg.mode, "自动").split(" ·")[0])
        except (AttributeError, tk.TclError):
            pass
        try:
            levels = self.ai_cfg.thinking_levels()
            if levels:
                cur = self.ai_cfg.thinking()
                self.btn_ai_think.set_text(
                    "思考·" + aiops.THINKING_LABEL.get(cur, cur))
                if not self.btn_ai_think.winfo_ismapped():
                    # 重新排到「权限」右边：先 forget 再 pack 才会落在末尾
                    self.btn_ai_think.pack_forget()
                    self.btn_ai_think.pack(side="left", padx=(U.px(4), 0))
            else:
                self.btn_ai_think.pack_forget()
        except (AttributeError, tk.TclError):
            pass
        try:
            text, tone, tip = self._ai_ctx_usage()
            self.lbl_ai_ctx.configure(
                text=text, fg=getattr(U.T, {"err": "err", "warn": "warn"}.get(
                    tone, "text_mute")))
            self.lbl_ai_ctx.configure(text=text)   # 保持 tip 供 tooltip/测试取
            self._ai_ctx_tip = tip
        except (AttributeError, tk.TclError):
            pass

    # ------------------------------------------------------------ 二级：技能
    def _ai_skill_build(self, host) -> None:
        """＋ → 技能：搜索 + 列表 + 底部两个入口（照 WorkBuddy 那一栏）。"""
        skills = self._ai_skills()
        sr = tk.Frame(host, bg=U.T.input_bg, bd=0, highlightthickness=1,
                      highlightbackground=U.T.input_border)
        sr.pack(fill="x", padx=U.px(8), pady=(U.px(8), U.px(4)))
        var = tk.StringVar()
        ent = tk.Entry(sr, textvariable=var, bd=0, bg=U.T.input_bg,
                       fg=U.T.text, insertbackground=U.T.text, font=U.FONT_S,
                       highlightthickness=0)
        ent.pack(fill="x", padx=(U.px(22), U.px(8)), pady=U.px(5))
        tk.Label(sr, text="⌕", bg=U.T.input_bg, fg=U.T.text_mute,
                 font=U.FONT_S, bd=0).place(x=U.px(7), rely=0.5, anchor="w")
        ph = tk.Label(sr, text="搜索技能", bg=U.T.input_bg, fg=U.T.text_mute,
                      font=U.FONT_XS, bd=0)
        ph.place(x=U.px(22), rely=0.5, anchor="w")
        ph.bind("<Button-1>", lambda e: ent.focus_set())

        def fill(inner):
            def render(*_a):
                for w in inner.winfo_children():
                    w.destroy()
                q = var.get().strip().lower()
                items = [("", AUTO_SKILL, "按问题里的关键词挑，没合适的就不套")]
                for s in skills:
                    items.append((s.slug, s.name, s.description or s.source))
                hit = 0
                for slug, label, desc in items:
                    if q and q not in f"{label} {desc}".lower():
                        continue
                    self._ai_skill_row(inner, slug, label, desc)
                    hit += 1
                if not hit:
                    tk.Label(inner, text="没有匹配的技能", bg=U.T.card,
                             fg=U.T.text_mute, font=U.FONT_XS,
                             bd=0).pack(pady=U.px(14))

            def hint_ph(*_a):
                try:
                    if var.get():
                        ph.place_forget()
                    else:
                        ph.place(x=U.px(22), rely=0.5, anchor="w")
                except tk.TclError:
                    pass

            var.trace_add("write", render)
            var.trace_add("write", hint_ph)
            render()

        _scroll_list(host, U.T.card, 232, fill)
        U.hsep(host, "card", pady=(U.px(4), U.px(2)))

        def open_lib(_e=None):
            self._ai_close_menu()
            self._ai_open_skill_lib()

        def add_local(_e=None):
            self._ai_close_menu()
            from pages.ai_page import SkillDialog
            dlg = SkillDialog(self, self)
            try:
                dlg.after(150, dlg._import)     # 直接弹导入，少点一次
            except AttributeError:
                pass

        self._ai_menu_row(host, "↑", "从本地添加技能", "", add_local)
        self._ai_menu_row(host, "◎", "管理技能", "", open_lib)

    def _ai_skill_row(self, parent, slug: str, label: str, desc: str) -> None:
        on = (slug == self._ai_skill_now)
        bg = U.T.brand_soft if on else U.T.card
        row = tk.Frame(parent, bg=bg, cursor="hand2")
        row.pack(fill="x", pady=U.px(1))
        badge = tk.Label(row, text=(label[:1] or "·").upper(),
                         bg=U.T.brand_soft if on else U.T.alt,
                         fg=U.T.brand, font=(U.FONT_FAMILY, 8, "bold"),
                         bd=0, width=2, pady=U.px(3))
        badge.pack(side="left", padx=(U.px(8), U.px(6)), pady=U.px(4))
        cell = tk.Frame(row, bg=bg)
        cell.pack(side="left", fill="x", expand=True)
        tk.Label(cell, text=label, bg=bg, fg=U.T.text, font=U.FONT_S,
                 bd=0, anchor="w").pack(fill="x")
        tk.Label(cell, text=_clip_str(desc, 40), bg=bg, fg=U.T.text_mute,
                 font=U.FONT_XS, bd=0, anchor="w").pack(fill="x")
        if on:
            tk.Label(row, text="✓", bg=bg, fg=U.T.brand, font=U.FONT_S,
                     bd=0).pack(side="right", padx=U.px(8))

        def pick(_e=None, v=slug, t=label):
            self._ai_pick_skill_now(v)
            self._ai_close_menu()
            self.toast("技能：" + (t if v else "自动"), "ok")

        for w in (row, badge, cell) + tuple(cell.winfo_children()):
            w.bind("<Button-1>", pick)

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

        形态照 WorkBuddy 的模型浮层：一行一个，左边圆标、名字后面跟徽章、右边
        是模型名与当前勾，底部一条分隔线 + 「配置自定义模型」。列表长了（>7 个）
        才出现搜索框 —— 三个模型还摆个搜索框，纯占地方。

        切模型 = 把那条档案写回扁平字段，引擎下一次调用直接用新的：不用重启，
        也不用回到设置页。
        """
        cfg = self.ai_cfg
        models = cfg.models()
        holder: dict = {}
        area: dict = {}

        def build(host):
            box = tk.Frame(host, bg=U.T.card)
            box.pack(fill="both", expand=True, padx=U.px(4), pady=U.px(4))

            def render(q: str = "") -> None:
                rows = area.get("rows")
                if rows is None:
                    return
                for w in rows.winfo_children():
                    w.destroy()
                hit = 0
                for m in models:
                    v = aiops.model_view(m, cfg.current_id())
                    hay = (f"{v['name']} {v['model']} {v['provider']}"
                           .lower())
                    if q and q not in hay:
                        continue
                    self._ai_model_row(rows, v, holder)
                    hit += 1
                if not hit:
                    tk.Label(rows, text="没有匹配的模型", bg=U.T.card,
                             fg=U.T.text_mute, font=U.FONT_XS,
                             bd=0).pack(pady=U.px(14))

            def fill(inner):
                rows = tk.Frame(inner, bg=U.T.card)
                rows.pack(fill="x")
                area["rows"] = rows
                render()

            if len(models) > 7:
                sr = tk.Frame(box, bg=U.T.input_bg, bd=0, highlightthickness=1,
                              highlightbackground=U.T.input_border)
                sr.pack(fill="x", pady=(0, U.px(4)))
                var = tk.StringVar()
                ent = tk.Entry(sr, textvariable=var, bd=0, bg=U.T.input_bg,
                               fg=U.T.text, insertbackground=U.T.text,
                               font=U.FONT_S, highlightthickness=0)
                ent.pack(fill="x", padx=U.px(8), pady=U.px(4))
                ph = tk.Label(sr, text="搜索模型", bg=U.T.input_bg,
                              fg=U.T.text_mute, font=U.FONT_XS, bd=0)
                ph.place(x=U.px(8), rely=0.5, anchor="w")

                def ph_sync(*_a):
                    try:
                        if var.get():
                            ph.place_forget()
                        else:
                            ph.place(x=U.px(8), rely=0.5, anchor="w")
                    except tk.TclError:
                        pass

                ph.bind("<Button-1>", lambda e: ent.focus_set())
                var.trace_add(
                    "write", lambda *a: render(var.get().strip().lower()))
                var.trace_add("write", ph_sync)
                holder["ent"] = ent

            if len(models) <= 8:
                fill(box)
            else:
                _scroll_list(box, U.T.card, 300, fill)

            U.hsep(box, "card", pady=(U.px(4), U.px(2)))
            foot = tk.Frame(box, bg=U.T.card, cursor="hand2")
            foot.pack(fill="x")
            tk.Label(foot, text="✎", bg=U.T.card, fg=U.T.text_dim,
                     font=U.FONT_XS, bd=0, width=2).pack(
                side="left", padx=(U.px(6), U.px(2)), pady=U.px(7))
            lb = tk.Label(foot, text="配置自定义模型", bg=U.T.card,
                          fg=U.T.text, font=U.FONT_S, bd=0)
            lb.pack(side="left")

            def more(_e=None):
                try:
                    holder["top"].destroy()
                except (KeyError, tk.TclError):
                    pass
                self.ai_model_settings()

            for w in (foot, lb) + tuple(foot.winfo_children()):
                w.bind("<Button-1>", more)
                w.bind("<Enter>", lambda e: (foot.configure(bg=U.T.brand_soft),
                                             lb.configure(bg=U.T.brand_soft)))
                w.bind("<Leave>", lambda e: (foot.configure(bg=U.T.card),
                                             lb.configure(bg=U.T.card)))

        top = _popup(self, self.btn_ai_model, build, width=U.px(320))
        holder["top"] = top
        try:
            holder["ent"].focus_set()
        except (KeyError, tk.TclError):
            pass
        return top

    def _ai_model_row(self, parent, v: dict, holder: dict) -> None:
        """浮层里的一行：圆标 + 名称 + 徽章 + 右侧模型名与当前勾。

        只有「当前」「本地」「缺 Key」这类值得一提的状态才挂徽章 —— 每行都挂个
        「就绪」等于没信息，还把名字挤窄了。
        """
        cur = bool(v.get("current"))
        bg = U.T.brand_soft if cur else U.T.card
        row = tk.Frame(parent, bg=bg, cursor="hand2")
        row.pack(fill="x", pady=U.px(1))

        icon = tk.Canvas(row, width=U.px(22), height=U.px(22), bd=0,
                         highlightthickness=0, bg=bg)
        icon.pack(side="left", padx=(U.px(6), U.px(7)), pady=U.px(5))
        icon.create_oval(0, 0, U.px(22) - 1, U.px(22) - 1,
                         fill=U.T.brand_soft2, outline="")
        icon.create_text(
            U.px(11), U.px(11),
            text=((v.get("provider_key") or v.get("provider") or "?")
                  [:1].upper()),
            fill=U.T.brand, font=(U.FONT_FAMILY, 8, "bold"))

        name = tk.Label(row, text=v.get("name") or "未命名", bg=bg,
                        fg=U.T.text, font=U.FONT_S, bd=0, anchor="w")
        name.pack(side="left")
        kids: list[tk.Widget] = [icon, name]

        badge = None
        if cur:
            badge = U.Pill(row, "当前", "brand", height=18, radius=9,
                           padx=7, font=U.FONT_XS)
        elif v.get("tone") == "ok" and v.get("provider_key") == "dsh":
            badge = U.Pill(row, "本地", "ok", height=18, radius=9,
                           padx=7, font=U.FONT_XS)
        elif v.get("tone") in ("warn", "err"):
            badge = U.Pill(row, v.get("state") or "异常", "warn", height=18,
                           radius=9, padx=7, font=U.FONT_XS)
        if badge is not None:
            try:
                badge.configure(cursor="hand2")
            except tk.TclError:
                pass
            badge.pack(side="left", padx=(U.px(7), 0))
            kids.append(badge)

        tick = tk.Label(row, text="✓" if cur else "", bg=bg, fg=U.T.ok,
                        font=U.FONT_S, bd=0)
        tick.pack(side="right", padx=(U.px(4), U.px(8)))
        mdl = tk.Label(row, text=_clip_str(v.get("model") or "", 16), bg=bg,
                       fg=U.T.text_mute, font=U.FONT_XS, bd=0)
        mdl.pack(side="right")
        kids += [tick, mdl]

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

        def paint(color: str) -> None:
            for w in kids + [row]:
                try:
                    w.configure(bg=color)
                except tk.TclError:
                    pass

        for w in kids + [row]:
            w.bind("<Button-1>", pick)
            w.bind("<Enter>", lambda e: paint(U.T.brand_soft))
            w.bind("<Leave>", lambda e: paint(bg))

    # ================================================================ 技能
    def _ai_skill_menu(self) -> tk.Toplevel:
        """旧「技能」按钮的浮层。内容和 ＋ → 技能 共用一份，不再各写一套。"""
        return _popup(self, self.btn_ai_skill, self._ai_skill_build,
                      width=U.px(300))

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
        """Ctrl+Enter：空闲时发送，跑着的时候是**插话**。

        为什么不让老板键顺手再起一轮：一轮任务里的上下文在 transcript 上，
        起第二轮等于把「刚才那几步」全丢了，还会两个线程抢同一个会话。
        插话才是这个场景真正要的行为（学 Cherry Studio）。
        """
        if getattr(self, "ai_busy", False):
            self._ai_inject_send()
        else:
            self.ai_dock_send()
        return "break"

    def _ai_inject_send(self) -> None:
        """把输入框里的话插进当前这一轮。"""
        if not getattr(self, "ai_busy", False):
            # 不在跑就没得插，按普通发送处理
            self.ai_dock_send()
            return
        text = self._ai_input.get("1.0", "end-1c").strip()
        if not text:
            self.toast("写一句话再插进去", "warn")
            return
        self._ai_inject.append(text)
        self._ai_input.clear()
        self._ai_bubble(text, badge="插话 · 已加入当前这一轮")
        self._ai_note("已插入当前任务：助手会在下一步开始前读到这句话。", "dim")
        self.toast("已插话，下一步生效", "ok")

    def _ai_take_inject(self) -> list[str]:
        """Agent 每步开始前取一次。取走即清空，避免同一句话被读两遍。"""
        got = list(getattr(self, "_ai_inject", []) or [])
        if got:
            self._ai_inject = []
        return got

    def ai_dock_send(self) -> None:
        if self.ai_busy:
            self.toast("上一条还在跑：按 Ctrl+Enter 可以插话，点 ■ 可以停下", "warn")
            return
        if not getattr(self, "ai_dock", None):
            self.ai_dock_open()
        goal = self._ai_input.get("1.0", "end-1c").strip()
        if not goal and not self._ai_attach:
            return
        if not goal:
            # 只贴了图/文件没写字：别逼用户再补一句，替他起个头。
            # 这句话会显示在气泡里，所以说得像人话。
            goal = "看看我附加的文件，有什么值得注意的地方"
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
        # 上一轮没被读走的插话不能漏到这一轮 —— 那已经不是同一个上下文了
        self._ai_inject = []
        self._ai_send_icon(True)
        self._ai_bubble(goal, attach=[a["name"] for a in self._ai_attach])

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
        # 上下文已经拼进 ctx，输入框上的附件可以撤了；_ai_files_seen 留着，
        # 下次还能从「引用对话中的文件」里把它拿回来。
        self._ai_attach_clear()
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
        block = self._ai_attach_text()
        if block:
            lines.append(block)
        return "\n".join(lines)

    def _ai_attach_text(self) -> str:
        """把本轮附件拼进上下文。

        文本类直接内联内容 —— 模型不用再花一步去「读文件」，问一句答一句更快。
        图片只报路径和尺寸：这一版没接视觉通道，把 PNG 塞进提示词纯属浪费；
        但它可以用 exec 去处理（比如识别不了也能 tell 用户图在哪）。
        二进制与超限文件只报存在，让模型自己决定要不要读。
        """
        if not self._ai_attach:
            return ""
        out = [f"用户附加的文件（本轮 {len(self._ai_attach)} 个）："]
        for a in self._ai_attach:
            p = Path(a["path"])
            head = f"- {p}（{_human_size(a.get('size') or 0)}"
            if a.get("image"):
                wh = _png_size(p)
                dim = f"，{wh[0]}×{wh[1]}" if wh else ""
                out.append(head + dim + "，图片）")
                out.append("  需要看图就调用工具处理这条路径；"
                           "不要假装看过图里的内容。")
                continue
            out.append(head + "）")
            body = _read_small_text(p, ATTACH_INLINE_LIMIT)
            if body is None:
                out.append("  内容未内联（二进制或超过 "
                           f"{ATTACH_INLINE_LIMIT // 1024} KB），需要时用工具读")
            else:
                out.append("  内容如下：")
                out.append("------")
                out.append(body.rstrip())
                out.append("------")
        return "\n".join(out)

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

    def _ai_send_or_stop(self) -> None:
        """同一个按钮两个身份：正在跑就是「停止」，否则就是「发送」。"""
        if getattr(self, "ai_busy", False):
            self.ai_dock_stop()
        else:
            self.ai_dock_send()

    def _ai_send_icon(self, busy: bool) -> None:
        btn = getattr(self, "btn_ai_send", None)
        if btn is None:
            return
        btn.set_text("■" if busy else "➤")

    def ai_dock_stop(self) -> None:
        if not self.ai_busy:
            return
        self._ai_stop.set()
        self._ai_note("已请求停止，等当前这个动作跑完", "warn")

    def ai_dock_clear(self) -> None:
        if self.ai_busy:
            self.toast("正在执行，先停止再清空", "warn")
            return
        if not forms.confirm(self, "清空会话", "清空这次对话与上下文？"
                                               "（已经执行过的动作不会撤销）"):
            return
        self.agent.reset()
        self._ai_scroll.clear()
        # 三个列表原地清空：它们和会话状态里的是同一个对象，换新的就脱钩了
        self._ai_flow_labels[:] = []
        self._ai_md_views[:] = []
        self._ai_tool_cards[:] = []
        self._ai_files_seen.clear()
        self._ai_attach_clear()
        # 会话记录一并清掉，并把页签改回「新会话」——
        # 否则清完了页签上还挂着旧标题，翻「所有会话」还能翻出刚清掉的内容
        sess = getattr(self, "_ai_sess", None)
        if sess is not None:
            sess.transcript = []
            sess.history = []
            sess.tokens = 0
            sess.files = {}
            sess.title = "新会话"
            sess.pinned = False
            self.agent.history = sess.history
            self._ai_sess_store.save(sess)
            st = self._ai_open.get(sess.id)
            if st is not None:
                st["painted"] = True
        self._ai_paint_welcome()
        self._ai_refresh_dock_state()
        self._ai_tab_paint()
        self.toast("会话已清空", "ok")

    # ================================================================ 工作线程
    def _ai_worker(self, goal: str, context: str, skill_text: str) -> None:
        # MCP 工具是动态的：每轮开始前刷新一次，别让模型拿着上一轮的清单乱调。
        # 这里在工作线程里跑（会起子进程），失败不影响这一轮的任务。
        try:
            rows = self.mcp_pool.ensure_tools() if hasattr(self, "mcp_pool") else []
        except Exception:  # noqa: BLE001
            rows = []
        A.register_dynamic(rows)
        try:
            res = self.agent.run(
                goal, self._ai_tool_exec, context=context, skill_text=skill_text,
                confirm=self._ai_confirm_call,
                on_event=lambda k, p: self.post(self._ai_dock_event, k, p),
                should_stop=lambda: self._ai_stop.is_set(),
                risk_of=self._ai_risk_of,
                # 中途插话：每一步开始前取一次队列，绝不并发起第二轮
                poll_inject=self._ai_take_inject)
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
        elif kind == "inject":
            # 插话被这一步读到了：气泡在用户按下时就画过了，这里只留一行痕迹，
            # 免得用户以为「我的话没被读到」。
            text = _clip_str(str(p.get("text") or ""), 40)
            self._ai_note(f"· 已读到你插入的话（第 {p.get('step') or '?'} 步）"
                          f"：{text}", "dim")
            self.ai_trace(aitrace.note(f"插话：{text}", tone="ok"))
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
            self._ai_send_icon(False)
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
            label = (A.TOOLS[tool].label if tool in A.TOOLS
                     else (A.DYNAMIC.get(tool) or {}).get("name") or tool)
            self.ai_trace(aitrace.note(
                f"{label} · {'完成' if ok else '失败'} · {str(text or '')[:120]}",
                tone="ok" if ok else "err"), ensure=True)
        return ok, text

    def _ai_tool_dispatch(self, tool: str, args: dict):
        if tool.startswith("mcp__"):
            return self._ai_mcp_call(tool, args)
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

    def _ai_mcp_call(self, tool: str, args: dict):
        """MCP 工具落地：真去调那条服务器的 tools/call。

        跑在工作线程里（MCP 调用本身是阻塞的），结果拍平成文本回灌给模型。
        出错不抛异常 —— 助手要能看见「为什么没成」再决定下一步。
        """
        try:
            from core import mcp as mcp_mod
        except Exception as e:  # noqa: BLE001
            return False, f"MCP 模块不可用：{e}"
        pool = getattr(self, "mcp_pool", None)
        if pool is None:
            return False, "MCP 会话池没起来（这一版没初始化）"
        try:
            text = pool.call(tool, args or {})
        except mcp_mod.MCPError as e:
            return False, f"MCP 调用失败：{e}"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"
        return True, str(text or "（这个工具没有输出）")

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
            # 输出里提到的本机文件记一笔，供「引用对话中的文件」用
            self._ai_scan_files(body, "命令输出")
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


def _clip_str(s: str, n: int) -> str:
    """按字数截断，超出补省略号。菜单里放不下长描述，也不该让它撑宽浮层。"""
    s = str(s or "")
    return s if len(s) <= n else s[:max(1, n - 1)] + "…"


def _human_size(n: int) -> str:
    n = int(n or 0)
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _read_small_text(path: Path, limit: int) -> str | None:
    """小文本文件直接读进来内联。二进制或超限返回 None —— 让模型自己去读。"""
    try:
        if not path.is_file() or path.stat().st_size > limit:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if not clipfile.looks_text(data):
        return None
    return data.decode("utf-8", "replace")


def _png_size(path: Path) -> tuple[int, int] | None:
    """PNG 头里的宽高。只认 PNG —— 从剪贴板存下来的就是 PNG，别的没这保证。"""
    try:
        with path.open("rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


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
    _bind_outside_click(app, top, anchor)
    top.focus_set()
    return top


def _bind_outside_click(app, top: tk.Toplevel, anchor=None) -> None:
    """点主窗口任何地方就收起（点浮层自己身上不算）。

    为什么非这样不可：`overrideredirect` 的 Toplevel 不参与焦点管理，
    而 Tk 的 Frame / Canvas 又根本不抢焦点 —— 于是 `<FocusOut>` 基本不会
    触发，用户「点别处」菜单纹丝不动。这是「＋ 菜单点别处不收」的真正原因。

    绑定挂在主窗口上而不是 `bind_all`：主窗口是所有子控件的 bindtag 之一，
    点在子控件上照样能收到；用 `bind_all` 会把别的模块的绑定一起冲掉。
    """
    root = app.winfo_toplevel()

    def _on_click(event=None) -> None:
        # 只有「点的是锚点按钮（＋）自己」才算收起动作 —— 否则点别处关掉之后
        # 用户马上再点 ＋ 会被误判成「刚关过」，表现为点了没反应。
        hit = getattr(event, "widget", None)
        inside = False
        while hit is not None:
            if hit is anchor:
                inside = True
                break
            hit = getattr(hit, "master", None)
        if inside:
            try:
                app._menu_closed_at = time.time()
            except Exception:  # noqa: BLE001
                pass
        _close_chain(top)

    funcid = root.bind("<Button-1>", _on_click, add="+")

    def _cleanup(_e=None) -> None:
        try:
            root.unbind("<Button-1>", funcid)
        except tk.TclError:
            pass

    top.bind("<Destroy>", _cleanup)


def _close_if_unfocused(top: tk.Toplevel) -> None:
    # 二级浮层开着的时候，一级失焦是正常的（焦点跑到旁边那个浮层去了）。
    # 这时候关掉一级，用户刚点开的「技能」会瞬间消失 —— 必须放行。
    child = getattr(top, "_child", None)
    try:
        if child is not None and child.winfo_exists():
            return
    except tk.TclError:
        pass
    try:
        if top.focus_displayof() is None:
            top.destroy()
    except tk.TclError:
        pass


def _close_chain(top: tk.Toplevel) -> None:
    """一级浮层和它挂着的二级浮层一起关掉。"""
    child = getattr(top, "_child", None)
    if child is not None:
        try:
            child.destroy()
        except tk.TclError:
            pass
        try:
            top._child = None
        except tk.TclError:
            pass
    try:
        top.destroy()
    except tk.TclError:
        pass


def _sub_close_if(top: tk.Toplevel) -> None:
    """二级浮层失焦 → 整条链收掉。

    例外：焦点还在二级里面（比如刚点了搜索框）就不动 —— 那只是内部换焦点。
    """
    child = getattr(top, "_child", None)
    try:
        if child is not None and child.focus_displayof() is not None:
            return
    except tk.TclError:
        pass
    _close_chain(top)


def _submenu(top: tk.Toplevel, builder, width: int = 300,
             max_h: int = 400) -> tk.Toplevel:
    """在一级浮层右边并排开一个二级浮层（WorkBuddy 那种「技能 ›」）。

    同一时刻只留一个 —— 再点别的分组先把上一个拆掉，否则几个浮层会叠成一片。
    右边放不下就翻到左边，免得贴着屏幕边被裁掉半截。
    """
    old = getattr(top, "_child", None)
    if old is not None:
        try:
            old.destroy()
        except tk.TclError:
            pass
        try:
            top._child = None
        except tk.TclError:
            pass

    sub = tk.Toplevel(top)
    sub.withdraw()
    sub.overrideredirect(True)
    sub.configure(bg=U.T.border)
    shell = tk.Frame(sub, bg=U.T.card, bd=0)
    shell.pack(fill="both", expand=True, padx=1, pady=1)
    builder(shell)
    sub.update_idletasks()
    w = max(width, shell.winfo_reqwidth())
    h = min(shell.winfo_reqheight(), U.px(max_h))
    x = top.winfo_rootx() + top.winfo_width() - 1
    if x + w > top.winfo_screenwidth() - 8:
        x = max(8, top.winfo_rootx() - w + 1)
    y = max(8, min(top.winfo_rooty(), top.winfo_screenheight() - h - 48))
    sub.geometry(f"{w}x{h}+{x}+{y}")
    top._child = sub          # 先登记再显示：紧随其后的 FocusOut 要能看到它
    sub.deiconify()
    sub.bind("<Escape>", lambda e: _close_chain(top))
    sub.bind("<FocusOut>", lambda e: sub.after(150, _sub_close_if, top))
    return sub


def _scroll_list(parent, bg: str, height: int, builder) -> "_Scroll":
    """浮层里的一段可滚动列表。高度写死，否则浮层会被长列表顶出屏幕。"""
    wrap = tk.Frame(parent, bg=bg, bd=0, height=U.px(height))
    wrap.pack(fill="both", expand=True, pady=(U.px(4), 0))
    wrap.pack_propagate(False)
    sc = _Scroll(wrap, bg)
    sc.pack(fill="both", expand=True)
    builder(sc.inner)
    return sc


class AgentPanel(tk.Frame):
    """智能体设置：基本信息 / 提示词 / 技能 / 工具，四段。

    这里改的全是「它是什么、能干什么」，模型 Key 仍在「模型设置」里 —— 两件事分开，
    换模型不改人设，改人设不动凭据。

    做成 Frame：设置中心把它当成内嵌页签（智能体 / 模型 / 技能库 / MCP 库）。
    要独立窗口就用下面的 AgentDialog 包一层。
    """

    def __init__(self, master, page, on_close=None):
        super().__init__(master, bg=U.T.page, bd=0)
        self.page = page
        self.cfg = page.agent_cfg
        self.on_close = on_close
        self._skill_vars: dict[str, tk.BooleanVar] = {}
        self._tool_vars: dict[str, tk.BooleanVar] = {}

        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(14), pady=U.px(12))

        head = U.wframe(wrap, "page")
        head.pack(fill="x")
        U.wlabel(head, "智能运维助手", "page", U.FONT_T).pack(side="left")
        U.wlabel(head, "描述 · 提示词 · 技能 · 工具", "pagedim",
                 U.FONT_XS).pack(side="left", padx=(U.px(10), 0))
        if on_close is not None:
            U.RoundButton(head, "模型设置", self._model, variant="ghost",
                          height=30, padx=14).pack(side="right", padx=(U.px(6), 0))
            U.RoundButton(head, "取消", on_close, variant="secondary",
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

    def _close(self) -> None:
        cb = self.on_close
        if cb is not None:
            cb()
            return
        try:
            self.winfo_toplevel().destroy()
        except tk.TclError:
            pass

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
        # 内嵌形态保存完不关（用户可能还要接着改别的段）；独立窗口形态才关掉
        self._close()

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


class AgentDialog(tk.Toplevel):
    """独立窗口形态的智能体设置（侧栏「⋯ → 助手设置」还用这个）。"""

    def __init__(self, parent, page):
        super().__init__(parent)
        self.title("智能运维助手 · 设置")
        self.configure(bg=U.T.page)
        self.transient(parent)
        self.panel = AgentPanel(self, page, on_close=self.destroy)
        self.panel.pack(fill="both", expand=True)
        self.geometry(f"{U.px(880)}x{U.px(640)}")
        self.minsize(U.px(680), U.px(460))
        self.bind("<Escape>", lambda e: self.destroy())
