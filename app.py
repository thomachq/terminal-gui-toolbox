# -*- coding: utf-8 -*-
"""Terminal GUI Toolbox —— 主程序。

多协议终端工具箱：SSH / Telnet / 本机，连接后自动探测操作系统，
底部状态栏实时显示 CPU / 内存 / 网络 / 运行时长，命令日志是可分离的侧边栏。

模块：
  · 连接与设备    —— 会话、探测、系统概览
  · 网络工具      —— ping / 路由追踪 / 端口 / 接口 / 路由表（表格化）
  · 系统信息      —— 版本、负载、磁盘、进程（表格化）
  · 路由表        —— 读 + 增删
  · 防火墙        —— nftables / iptables / netsh / pf / ipfw 读 + 增删
  · Docker        —— 容器 / 镜像 / 资源占用，未安装时明确提示
  · 隧道服务管理 —— 探测设备上装了什么隧道服务 / 启停 / 在线连接 / OpenVPN 客户端证书（内嵌页）
  · 智能体接入    —— 本地 HTTP API，给 AI 智能体保持 SSH 连接
"""
from __future__ import annotations

import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk

import crypto_store
import lockmgr
import wbtui as U
from api.pool import API_CMD_TAG
from api.server import make_default as make_api
from core import ai_skills, aitrace
from core import aiops, parsers, routes
from core import audit as audit_core
from core import creds as creds_core
from core import elev
from core import hosts as hosts_core
from core import proxies as proxies_core
from core import scripts as scripts_core
from core import redact
from core import singleinstance
from core import winstate
from core.detect import DeviceInfo, capabilities, detect, dialect_for
from core.metrics import (Sampler, human_bytes, human_gb, human_rate,
                          human_uptime)
from core.sessionmgr import SessionManager
from core.session import (ConnSpec, LocalSession, NeedElevation, SSHSession,
                          SessionError, open_session, term_exec)
from pages.ai_dock import AiDockPage
from pages.log_dock import LogDock
from pages.cred_page import CredPage
from pages.hosts_page import HostsPage
from pages.log_page import LogPage
from pages.proxy_page import ProxyPage
from pages.script_page import ScriptPage
from pages.settings_center import SettingsCenter
from core import agent as agent_core
from core import mcp as mcp_mod
from pages.api_page import ApiPage
from pages.cron_page import CronPage
from pages.db_page import DbPage
from pages.docker_page import DockerPage
from pages.files_page import FilesPage
from pages.remote_page import RemotePage
from pages.routes_page import RoutesPage
from pages.security_page import SecurityPage
from pages.web_page import WebPage
from profiles import ProfileStore, discover_keys, inspect_key, norm_path
from ui import forms
from ui.icon import apply_icon, set_app_id
from ui.logpanel import LogModel
from ui.statusbar import StatusBar
from ui.table import KVGrid, Column, DataTable, Segmented
from ui.tabs import TabBar
from ui.terminal import TerminalPane, TerminalWindow
from ui.tray import Tray

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
# 自测 / 截图时的数据目录重定向：`TB_DATA_DIR=<临时目录>` 一行就能把档案、
# 设置、窗口状态、审计日志全挪走，真实 data 一个字节都不动。
# 为什么放在模块级：这些存储都是 import 时按 DATA_DIR 定位的，事后改
# `appmod.DATA_DIR` 只对「还没建的对象」有效，容易漏 —— 从源头算更保险。
_env_data = os.environ.get("TB_DATA_DIR", "").strip()
if _env_data:
    DATA_DIR = Path(_env_data).expanduser().resolve()
SAMPLE_MS = 3000


def _asset(name: str) -> Path:
    """找资源文件：优先 exe 同级的 assets\\，其次一行打包时解压出来的临时目录。

    onefile 模式下 datas 会解到 sys._MEIPASS，如果用户只拷了 exe 却没带
    assets\\ 目录，图标不该跟着消失 —— 所以这里做两级兜底。
    """
    local = APP_DIR / "assets" / name
    if local.is_file():
        return local
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "assets" / name
        if bundled.is_file():
            return bundled
    return local


ICON_ICO = _asset("app.ico")

APP_NAME = "Terminal GUI Toolbox"
APP_VER = "1.0.28"
APP_SUB = ("多协议终端工具箱 · SSH / Telnet / 本机 · 设备探测 · 安全中心 / 数据库 / "
           "计划任务 / Web 服务 · AI 运维")

# 旧导航键 → 新导航键。防火墙并进安全中心之后，"fw" 仍然能用（--page fw、
# 以前的脚本、截图脚本都指着它），只是指到同一页。
# 已合并/改名的页面：旧键继续可用，--page 与旧脚本不会突然断掉。
NAV_ALIAS = {"fw": "sec", "conn": "hosts"}
# 主体是「一个大表格 + 工具栏」的页面。这些页压缩而不是加外层滚动条 ——
# 表格自己有内滚动，再套一层就成了双重滚动（滚轮不知道在滚哪一层）。
_TABLE_PAGES = {"hosts", "tools", "sys", "files", "routes"}

# 页面内容的「不许再压」下限（逻辑像素）。0/缺省 = 不设。
# 文件管理：双栏时每栏要有卡片头（~34）+ 路径条（~36）+ 列表头（~28）+ 几行，
# 400 能露 4 行左右；压到 400 以下就不如让整页滚。
# 表格页也有个下限：一张表 = 工具条 + 搜索框 + 表头 + 至少两行 + 底栏，
# 压到 240 逻辑像素以下就连工具条都被裁（实测 1200x760 窗口里 tools/sys/routes
# 各有 4~16 个控件落在视口外，滚也滚不到）。到那个份上不如让整页滚。
_MIN_PAGE_H = {"files": 400, "hosts": 260, "tools": 260, "sys": 260,
               "routes": 260}

# 需要页面级横向滚动条的页：内容本身有不可压缩的宽度，视口一窄右边就被切。
# 目前只有隧道页那种「一行排好几个输入框」的表单（实测 Entry + Combobox
# 超出视口 127 像素，怎么调列宽都压不下来）。表格页不要开 ——
# DataTable 自己有横滚，页面级再来一条就是两条横滚抢同一件事。
_HSCROLL_PAGES = {"vpn"}


# 运维工具那一组：这些页打的命令都落在「当前活动设备」上，所以顶部要显示
# 并允许切换「操作目标」。上面那组（主机管理 / 代理池 / 凭证 / 脚本）管的是
# 资产本身，跟连没连设备无关，不显示设备条。
TOOL_PAGES = frozenset({
    "tools", "rmt", "sys", "files", "routes", "sec", "docker",
    "web", "db", "cron", "vpn", "api",
})


SYS_COLS = {
    "disk": [Column("fs", "文件系统 / 卷", 200, mono=True, stretch=True),
             Column("total", "总容量", 100, anchor="e",
                    format=lambda v, r: human_bytes(v or 0)),
             Column("used", "已用", 100, anchor="e",
                    format=lambda v, r: human_bytes(v or 0)),
             Column("free", "可用", 100, anchor="e",
                    format=lambda v, r: human_bytes(v or 0)),
             Column("use_pct", "使用率", 90, anchor="e",
                    format=lambda v, r: f"{v}%"),
             Column("mount", "挂载点", 180, stretch=True)],
    "proc": [Column("pid", "PID", 80, anchor="e"),
             Column("comm", "进程", 220, stretch=True),
             Column("cpu", "CPU %", 80, anchor="e",
                    format=lambda v, r: f"{float(v or 0):.1f}"),
             Column("mem", "内存 %", 80, anchor="e",
                    format=lambda v, r: f"{float(v or 0):.1f}"),
             Column("ppid", "父 PID", 80, anchor="e")],
    # Windows 的 Get-Process 给的是「累计 CPU 秒」和「工作集字节」，不是百分比，
    # 列名必须跟着改，否则会让人以为某个进程占了 3000% CPU。
    "proc_win": [Column("pid", "PID", 80, anchor="e"),
                 Column("comm", "进程", 220, stretch=True),
                 Column("cpu_sec", "CPU 时间 (秒)", 120, anchor="e",
                        format=lambda v, r: f"{float(v or 0):.1f}"),
                 Column("mem_mb", "内存 (MB)", 120, anchor="e",
                        format=lambda v, r: f"{float(v or 0):.1f}")],
}

TOOL_COLS = {
    "ip": [Column("iface", "接口", 130), Column("cidr", "地址 / 掩码", 190, mono=True),
           Column("scope", "作用域", 96), Column("mac", "MAC", 150, mono=True),
           Column("state", "状态", 78)],
    "route": [Column("dst", "目标网段", 170, mono=True, stretch=True),
              Column("gateway", "网关", 140, mono=True),
              Column("dev", "接口", 120),
              Column("metric", "跃点", 64, anchor="center"),
              Column("kind", "类型", 70)],
    "ports": [Column("proto", "协议", 70, mono=True),
              Column("addr", "监听地址", 130, mono=True),
              Column("port", "端口", 80, mono=True, anchor="e"),
              Column("proc", "进程 / PID", 150),
              Column("state", "状态", 80)],
    "dns": [Column("kind", "类型", 130), Column("value", "值", 320, mono=True,
                                                 stretch=True)],
    "ping": [Column("seq", "序号", 70, anchor="center"),
             Column("rtt", "往返 (ms)", 110, anchor="e"),
             Column("note", "结果", 200)],
    "trace": [Column("hop", "跳", 60, anchor="center"),
              Column("host", "节点", 190, mono=True),
              Column("rtt", "RTT (ms)", 200, mono=True),
              Column("raw", "原始", 240, stretch=True)],
}


class ToolboxApp(RoutesPage, RemotePage, SecurityPage, DbPage, CronPage,
                 DockerPage, FilesPage, AiDockPage, ApiPage, WebPage,
                 SettingsCenter, HostsPage, ProxyPage, CredPage, ScriptPage,
                 LogPage, LogDock, tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.configure(bg=U.T.page)
        # ---- 窗口几何：先按屏幕工作区把默认尺寸压进去，再套上次退出时的样子
        self._win_state = winstate.WinState(DATA_DIR / "window.json")
        self._win_pref = dict(self._win_state.data)
        self._win_normal: tuple[int, int, int, int] | None = None
        self._apply_win_geometry()
        self.bind("<Configure>", self._on_win_configure)

        # ---- 状态
        # 多设备：一次能连好几台，session / device / dialect / sampler
        # 都是「当前那台」的视图（见类里的四个 property）。
        self.sessions = SessionManager()
        self._local_device = DeviceInfo()
        self.log_model = LogModel()
        self.q: queue.Queue = queue.Queue()
        self._pages: dict[str, tk.Frame] = {}
        # 每个页面的外层滚动宿主（Canvas + 按需滚动条）。_pages 里存的是内胆，
        # 页面的 builder 往内胆里摆控件；切换页面时 grid 的是宿主。
        self._page_host: dict[str, tk.Frame] = {}
        self._keypw_job = None
        self._lock_overlay = None
        self._last_active = time.time()
        self._lock_after = None
        self._sample_after = None
        self._drain_after = None
        # 定时器是否还有必要续期。Tk 的 after 挂在解释器上而不是控件上，
        # 窗口销毁后再排一次不会当场报错，等它真触发时命令已经被删，
        # 才会冒出 invalid command name ..._drain。
        self._alive = True
        self._sampling = False
        self._local_dev: DeviceInfo | None = None
        self._toast_job = None
        self._tray: Tray | None = None
        self._tray_hint_win: tk.Toplevel | None = None
        self._close_to_tray = False
        self._dk_probed = False
        self._web_probed = False
        self._routes_loaded = False
        self._sec_done = False
        self._db_probed = False
        self._cron_loaded = False
        self._hosts_ready = False
        self._proxy_ready = False
        self._cred_ready = False
        self._script_ready = False
        self._audit_ready = False
        self._host_rows_all: list = []
        self._proxy_rows_all: list = []
        self._cred_rows_all: list = []
        self._script_rows_all: list = []
        self.ai_dock = None
        self._ai_dock_on = False
        self._ai_pending = None
        self._api_inited = False
        self._vpn_booted = False
        self._term: tk.Toplevel | None = None
        self._term_cwd_val = ""
        # --no-probe：只把界面画出来，不主动跑任何探测。
        # 探测会去调系统命令（Windows 上是 reg.exe / schtasks.exe / netstat），
        # 在受限环境或自动化脚本里容易卡住、弹 UAC 或者干脆被拦掉；
        # 截图与打包验收只需要界面骨架，不需要真数据。
        # 它只压掉「打开页面时自动跑一次」，用户主动点按钮时仍然照跑。
        self._no_probe = "--no-probe" in sys.argv

        # ---- 机密与档案
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # 页面（尤其是防火墙/路由备份）要往 data 下写东西，统一从这里取，
        # 免得各页自己拼 APP_DIR 拼错。
        self.data_dir = DATA_DIR
        crypto_store.set_key_dir(DATA_DIR)
        self.settings = lockmgr.Settings(DATA_DIR / "settings.json")
        self.lock = lockmgr.LockManager(self.settings)
        self.profiles = ProfileStore(DATA_DIR / "profiles.dat")
        # 主机资源四件套：凭证 / 代理 / 脚本各自一个库，脚本是明文
        self.creds = creds_core.CredStore(DATA_DIR / "creds.dat")
        self.proxies = proxies_core.ProxyStore(DATA_DIR / "proxies.dat")
        self.scripts = scripts_core.ScriptStore(DATA_DIR / "scripts.json")
        # 操作审计：一天一个 jsonl，追加写
        self.audit = audit_core.AuditStore(DATA_DIR / "logs")

        # ---- 外观：先按上次保存的主题与配色定下调色板，再建控件。
        # 反过来做的话，第一帧会闪一下默认配色。
        self.app_ver = APP_VER
        self.app_name = APP_NAME
        # 默认 False：点 × 就是退出。以前默认 True，结果图标被 Win11 收进
        # 右下角「︿」溢出区，用户看到的是「窗口没了、进程还在」，只能去
        # 任务管理器强杀 —— 用户 2026-10-23 明确说这是 bug。想驻留托盘的人
        # 自己去设置里打开。
        self._close_to_tray = bool(self.settings.data.get("close_to_tray", False))
        self._apply_theme_pref()

        # ---- 本地 API（智能体接入）
        self.api_cfg, self.api_pool, self.api_server = make_api(
            DATA_DIR, on_log=self._api_event, on_cmd=self._trace_cmd)

        # ---- 自己的 AI（AI 运维页）：与「给别的智能体用的 API」是两件事
        self.ai_cfg = aiops.AIConfig(DATA_DIR / "ai.json")
        self.ai_assistant = aiops.Assistant(self.ai_cfg)
        self.ai_skill_store = ai_skills.make_store(DATA_DIR)
        # 智能体的「人设」与「模型」分开存：换模型不动人设，改人设不碰凭据
        self.agent_cfg = agent_core.AgentConfig(DATA_DIR / "agent.json")
        self.agent = agent_core.Agent(self.agent_cfg, self.ai_cfg)
        # MCP 库：清单 + 会话池。进程起在**本机**，被管设备上不落任何东西。
        # 懒启动 —— 没人用 MCP 的时候一个进程都不会起。
        self.mcp_store = mcp_mod.MCPStore(DATA_DIR / "mcp.json")
        self.mcp_pool = mcp_mod.MCPPool(self.mcp_store)
        self.ai_busy = False
        self._ai_stop = threading.Event()
        # 助手自己发出去的远端命令：终端别再喂一遍（见 _term_feed）
        self._ai_live_cmds: dict[str, float] = {}
        # 模型这一层的输入输出要能在终端里看见 —— 钩子挂在引擎上，
        # 因为只有引擎手里有请求全文与响应全文
        aiops.set_trace(self._ai_api_trace)

        # ---- 图标 / 任务栏身份
        set_app_id("TerminalToolbox.App.1")
        apply_icon(self, ICON_ICO)

        U.configure_ttk(self)
        self._header()
        self._devtabs()
        self._body()
        self._statusbar()
        self._drain_after = self.after(80, self._drain)
        self._load_last_profile()
        self._watch_activity()
        self._start_sampler()
        self._init_tray()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.agent_cfg.data.get("dock_open"):
            self.after(260, self.ai_dock_open)
        # 上次退出时日志面板是开着的，就还开着（高度也在 window.json 里）
        if getattr(self, "_log_want_open", False):
            self.after(500, self.log_dock_open)
        self._maybe_lock_on_start()
        self.after(600, self._boot_api)
        # 单实例：第二个进程双击时会把我们叫醒（见 core/singleinstance.py）。
        # 轮询而不是等 Win32 消息 —— Tk 不暴露窗口过程，收不到自定义消息。
        self.after(700, self._wake_poll)

    # ============================================================ 骨架
    def _header(self):
        bar = U.wframe(self, "page")
        bar.pack(fill="x", padx=U.px(18), pady=(U.px(14), U.px(8)))
        self.header_bar = bar

        left = U.wframe(bar, "page")
        left.pack(side="left")
        U.wlabel(left, APP_NAME, "page", U.FONT_H).pack(anchor="w")
        U.wlabel(left, APP_SUB, "pagedim", U.FONT_S).pack(anchor="w", pady=(2, 0))

        right = U.wframe(bar, "page")
        right.pack(side="right")
        # 最右是「设置」：外观、终端、AI、安全（含应用锁）、备份、关于都收在里面。
        # 单独的主题切换和锁按钮都撤了 —— 三个按钮抢同一个位置，不如一个入口。
        self.btn_settings = U.RoundButton(right, "设置", self.open_settings,
                                          variant="ghost", font=U.FONT_S,
                                          height=28, padx=12)
        self.btn_settings.pack(side="right", padx=(U.px(8), 0))
        # 紧挨「终端」右边：助手是常驻的，不该占一个选项卡
        self.btn_ai_dock = U.RoundButton(right, "智能运维助手", self.ai_dock_toggle,
                                         variant="primary", font=U.FONT_S,
                                         height=28, padx=12)
        self.btn_ai_dock.pack(side="right", padx=(U.px(8), 0))
        self.btn_term = U.RoundButton(right, "终端", self.open_terminal,
                                      variant="secondary", font=U.FONT_S,
                                      height=28, padx=12)
        self.btn_term.pack(side="right", padx=(U.px(8), 0))
        self.pill_dev = U.Pill(right, "未连接", "mute")
        self.pill_dev.pack(side="right", padx=U.px(5))
        self.pill_proto = U.Pill(right, "—", "mute")
        self.pill_proto.pack(side="right", padx=U.px(5))

    # ============================================================ 多设备标签
    def _devtabs(self):
        """顶部设备标签：本机 + 每个已连接设备，点一下切到那台。

        页面本身不知道有多台设备 —— 它们读的永远是「当前活动的那台」，
        切换的实质是改 sessions.active_id 再把当前页按新设备重取一遍。
        """
        self.host_tabs = U.wframe(self, "page")
        self._tabs_shown = False
        self._rebuild_devtabs()

    def _rebuild_devtabs(self):
        bar = getattr(self, "host_tabs", None)
        if bar is None:
            return
        for w in bar.winfo_children():
            try:
                w.destroy()
            except tk.TclError:
                pass
        active = self.sessions.active_id
        U.wlabel(bar, "设备", "pagedim", U.FONT_XS).pack(
            side="left", padx=(0, U.px(6)))
        U.RoundButton(bar, "本机", lambda: self._switch_device(""),
                      variant=("ghost" if active else "primary"),
                      font=U.FONT_S, height=26, padx=12).pack(side="left")
        for slot in self.sessions:
            box = U.wframe(bar, "page")
            box.pack(side="left", padx=(U.px(6), 0))
            tail = "" if slot.alive() else " · 离线"
            U.RoundButton(box, f"{slot.label}{tail}",
                          lambda sid=slot.sid: self._switch_device(sid),
                          variant=("primary" if slot.sid == active else "secondary"),
                          font=U.FONT_S, height=26, padx=12).pack(side="left")
            U.RoundButton(box, "×",
                          lambda sid=slot.sid: self._close_device(sid),
                          variant="ghost", font=U.FONT_S, height=26,
                          padx=8).pack(side="left")
        n = len(self.sessions)
        if n:
            U.wlabel(bar, f"{n} 台已连接", "pagedim", U.FONT_XS).pack(side="right")
        # 一台都没连的时候把整行收起来 —— 不能让切换器白占一行高度，
        # 小屏（任务栏还挡掉一截）连接页会被挤到看不全。
        if n and not self._tabs_shown:
            bar.pack(fill="x", padx=U.px(18), pady=(0, U.px(2)),
                     after=getattr(self, "header_bar", None))
            self._tabs_shown = True
        elif not n and self._tabs_shown:
            bar.pack_forget()
            self._tabs_shown = False

    def _switch_device(self, sid: str, open_term: bool = True):
        """切到某台设备。

        `open_term` —— 点顶部会话标签时是 True（用户要的是「看那台的终端」）；
        在运维工具页用「操作目标」下拉切换时是 False（人还在当前页，别被拽走）。
        """
        if sid and self.sessions.set_active(sid) is None:
            return
        if not sid:
            self.sessions.active_id = ""
        self._after_switch()
        if not open_term:
            return
        # 点会话标签的直觉就是「看那台设备的终端」——以前切完只是把上下文换掉，
        # 画面还停在原页面，用户点了半天以为没反应（截图反馈就是这个）。
        # 切完顺手打开终端页签；已经开着就只是切过去，不新建第二个视图。
        try:
            self.open_terminal()
        except (AttributeError, tk.TclError):
            pass

    def _close_device(self, sid: str):
        slot = self.sessions.get(sid)
        if slot is None:
            return
        if slot.alive():
            ok = messagebox.askokcancel(
                "断开这台设备",
                f"断开「{slot.label}」？\n该设备上正在跑的命令会中断，"
                f"其它设备不受影响。", parent=self)
            if not ok:
                return
        self.sessions.close(sid)
        self._after_switch()

    # ------------------------------------------- 运维工具顶部的「操作目标」条
    def _build_devstrip(self) -> None:
        strip = self.dev_strip
        U.wlabel(strip, "操作目标", "pagedim", U.FONT_XS).pack(
            side="left", padx=(0, U.px(6)))
        self.cb_target = ttk.Combobox(strip, state="readonly", width=22,
                                      font=U.FONT_S)
        self.cb_target.pack(side="left")
        self.cb_target.bind("<<ComboboxSelected>>", self._target_pick)
        self.pill_target = U.Pill(strip, "未连接", "mute", font=U.FONT_XS)
        self.pill_target.pack(side="left", padx=(U.px(8), 0))
        U.RoundButton(strip, "去连接…", lambda: self._show("hosts"),
                      variant="ghost", font=U.FONT_XS, height=24,
                      padx=10).pack(side="right")
        self._target_map: dict[str, str] = {}
        self._rebuild_devstrip()

    def _rebuild_devstrip(self) -> None:
        """按当前会话重填下拉：本机 + 每个已连接设备。

        只列「已连接」的会话 —— 运维工具发的是命令，没会话就发不出去，
        摆一台连不上的进来只会让人以为能点。要连新机器走右上角「去连接…」。
        """
        cb = getattr(self, "cb_target", None)
        if cb is None:
            return
        active = str(self.sessions.active_id or "")
        local_os = "Windows" if sys.platform == "win32" else "Linux"
        self._target_map = {}
        vals: list[str] = []

        def _add(label: str, sid: str) -> None:
            lab, i = label, 2
            while lab in self._target_map:   # 同名设备加序号，别出现两条一样的
                lab = f"{label} #{i}"
                i += 1
            self._target_map[lab] = sid
            vals.append(lab)

        _add(f"本机 · {local_os}", "")
        for slot in self.sessions:
            try:
                alive = bool(slot.alive())
            except Exception:  # noqa: BLE001
                alive = False
            _add(f"{slot.label}{'' if alive else '（离线）'}", slot.sid)
        cb["values"] = vals
        cb.set(next((k for k, v in self._target_map.items() if v == active),
                    vals[0]))
        self._sync_target_pill()

    def _sync_target_pill(self) -> None:
        pill = getattr(self, "pill_target", None)
        if pill is None:
            return
        active = str(self.sessions.active_id or "")
        if not active:
            alive = self._local_live()
        else:
            slot = self.sessions.get(active)
            try:
                alive = bool(slot is not None and slot.alive())
            except Exception:  # noqa: BLE001
                alive = False
        pill.set("已连接" if alive else "未连接", "ok" if alive else "mute")

    def _target_pick(self, _e=None) -> None:
        """在运维工具页换了操作目标。

        这里刻意**不**打开终端（open_term=False）：人还在系统信息 / 防火墙
        这一页，切完设备就该就地按新设备重取，被拽到终端反而打断。
        """
        sid = self._target_map.get(self.cb_target.get(), "")
        if sid == str(self.sessions.active_id or ""):
            return
        self._switch_device(sid, open_term=False)
        self.status.set_state(f"操作目标 → {self.cb_target.get()}", "ok")

    def _sync_devstrip(self, key: str) -> None:
        """只有运维工具那组页面需要这条，资产类页面收起来。"""
        strip = getattr(self, "dev_strip", None)
        if strip is None:
            return
        want = key in TOOL_PAGES
        on = bool(getattr(self, "_devstrip_on", False))
        if want and not on:
            strip.pack(side="top", fill="x", pady=(0, U.px(6)),
                       before=getattr(self, "stack", None))
            self._devstrip_on = True
        elif not want and on:
            strip.pack_forget()
            self._devstrip_on = False
        if want:
            self._rebuild_devstrip()

    def _after_switch(self):
        """切换/关闭设备后的统一收尾：重画标签、作废页面缓存、按新设备重取。"""
        self._rebuild_devtabs()
        try:
            self._rebuild_devstrip()      # 运维工具顶部那条「操作目标」跟着变
        except (AttributeError, tk.TclError):
            pass
        self._local_dev = None
        self._routes_loaded = False
        self._sec_done = False
        self._db_probed = False
        self._cron_loaded = False
        self._dk_probed = False
        self._web_probed = False
        self.fw_backend = None
        self._fw_raw = ""
        self._fw_undo = None
        self._routes_undo = None
        self._term_cwd_val = ""
        s = self.session
        dev = self.device
        if s is None:
            # 没有任何会话了 → 回到「本机」，把界面复位成未连接的样子
            self._local_device = DeviceInfo()
            self.status.clear()
            self.pill_dev.set("未连接", "mute")
            self.pill_proto.set("—", "mute")
            self._kv_empty()
            try:
                self.lbl_tool_os.configure(text="未连接 · 将使用本机命令")
                self.lbl_route.configure(text="点「刷新」读取设备当前路由表。")
                self.lbl_fw.configure(text="进页面自动探测用的是哪套防火墙。")
                self.badge_fw.set("未探测", "mute")
            except (AttributeError, tk.TclError):
                pass
            try:
                self._term_refresh_head()
            except (AttributeError, tk.TclError):
                pass
            self._refresh_current_page()
            return
        alive = True
        try:
            alive = bool(s.alive)
        except Exception:  # noqa: BLE001
            alive = False
        self._render_device(dev, s)
        self.status.set_device(dev.hostname or dev.os_name or s.label,
                               dev.os_family)
        self.status.set_state("已连接" if alive else "连接已断开",
                              "ok" if alive else "err")
        self.pill_dev.set(dev.os_name or "已连接", "ok" if alive else "err")
        self.pill_proto.set(s.kind.upper(), "ok" if alive else "err")
        self._log_raw("meta", f"[切换] 当前设备 → {dev.hostname or dev.os_name}\n")
        try:
            self._term_refresh_head()
        except (AttributeError, tk.TclError):
            pass
        self._refresh_current_page()

    def _body(self):
        # ---- 顶部页签栏：主机资源 常驻，终端按需加，可拉出成独立窗口
        self.tabbar = TabBar(self, on_select=self._on_tab,
                             on_new=self._tab_new)
        self.tabbar.on_close = self._tab_close
        self.tabbar.pack(fill="x", padx=U.px(10), pady=(U.px(8), 0))
        self._term_panes: dict[str, object] = {}

        # 外壳：左侧导航 + 页签内容在左，日志 / 助手在右。
        # 分栏容器必须挂在窗口这一层 —— 挂在「主机资源」页里面的话，终端一旦
        # 占满整页，右侧助手就被一起盖住，看不到「左边终端在跑、右边助手在说」。
        self.shell = U.wframe(self, "page")
        self.shell.pack(fill="both", expand=True)

        self.paned = tk.PanedWindow(self.shell, orient="horizontal", bg=U.T.page,
                                    bd=0, sashwidth=U.px(6), sashrelief="flat",
                                    showhandle=False)
        self.paned.pack(side="left", fill="both", expand=True,
                        padx=(U.px(6), U.px(8)), pady=(U.px(2), U.px(8)))

        # 底部日志面板的宿主（先不 pack，状态栏那个「日志」按钮点了才出现）
        self.log_dock_setup()

        # 左半：导航在最左，页签内容占它右边
        self.main = U.wframe(self.paned, "page")
        self.paned.add(self.main, stretch="always", minsize=U.px(620))

        # 所有页签的内容都 grid 在同一格，切页签就是 tkraise
        self.workspace = U.wframe(self.main, "page")
        self.workspace.pack(side="left", fill="both", expand=True)
        self.workspace.rowconfigure(0, weight=1)
        self.workspace.columnconfigure(0, weight=1)

        self.ws_hosts = U.wframe(self.workspace, "page")
        self.ws_hosts.grid(row=0, column=0, sticky="nsew")
        # 现在最上层的是哪块工作区。Tk 没有公开的 z 序查询接口，而
        # winfo_containing 在 Windows 上走 WindowFromPoint —— 窗口被别的程序
        # 盖住就返回 None，拿它做判断会时灵时不灵。所以在唯一抬窗的地方记一份。
        self._ws_top = self.ws_hosts

        # 左侧二级导航是「主机资源」这个页签自己的东西，不是窗口的东西。
        # 挂在这里，切到设置 / 终端页签时它会跟着整页一起被盖住 ——
        # 宝塔终端就是这样：每个页签自带宽窄不一的侧栏，而不是全局一条导航。
        # （之前挂在 self.main 上，导致「设置」页左边还杵着主机管理的菜单。）
        self.nav = U.Nav(self.ws_hosts, [
            # 上面一组是「有哪些机器、凭什么登、跑什么脚本」—— 资产。
            # 组名留空：顶部页签已经叫「主机资源」了，这里不再重复。
            ("group", "", [
                ("hosts", "主机管理"),
                ("proxy", "代理池"),
                ("cred", "凭证管理"),
                ("script", "脚本库"),
            ]),
            # 下面一组是「对机器做什么」—— 操作
            ("group", "运维工具", [
                ("tools", "网络工具"),
                ("rmt", "远程管理"),
                ("sys", "系统信息"),
                ("files", "文件管理"),
                ("routes", "路由表"),
                ("sec", "安全中心"),
                ("docker", "Docker"),
                ("web", "Web 服务"),
                ("db", "数据库"),
                ("cron", "计划任务"),
                ("vpn", "隧道服务管理"),
                ("api", "智能体接入"),
            ]),
            # 「日志管理」不再占导航位（用户要求）：它改成底部一条可开关的
            # 面板，入口在状态栏最右那个「日志」按钮上。
        ], on_change=self._show, width=176, alias=NAV_ALIAS)
        # 先 pack 导航、再 pack 内容：两个都是 side="left"，后来的会排在右边。
        # 顺序反了导航就跑到页面中间去了。
        self.nav.pack(side="left", fill="y", padx=(U.px(6), 0),
                      pady=(U.px(2), U.px(8)))

        body = U.wframe(self.ws_hosts, "page")
        body.pack(side="left", fill="both", expand=True, padx=U.px(10))
        body.pack_propagate(False)

        # 「操作目标」条：运维工具那组页面（网络工具 / 系统信息 / 文件管理 /
        # 路由表 / 安全中心 / Docker / Web / 数据库 / 计划任务 / 证书 / 智能体接入）
        # 打的命令全落在「当前活动设备」上，但页面本身一点都不显示是哪台 ——
        # 开了多台设备时根本分不清（用户反馈）。这里补一条常显的指示 + 切换器。
        # 默认不 pack：只在运维工具组出现，主机资源组（资产）跟设备无关。
        self.dev_strip = U.wframe(body, "page")
        self._build_devstrip()
        self._devstrip_on = False

        # 各页的内容容器（导航右侧）
        self.stack = U.wframe(body, "page")
        self.stack.pack(fill="both", expand=True)
        self.stack.grid_propagate(False)
        self.stack.rowconfigure(0, weight=1)
        self.stack.columnconfigure(0, weight=1)

        for key, builder in (("hosts", self.build_hosts),
                             ("proxy", self.build_proxy),
                             ("cred", self.build_cred),
                             ("script", self.build_script),
                             ("tools", self._build_tools),
                             ("rmt", self.build_remote),
                             ("sys", self._build_sys),
                             ("files", self.build_files),
                             ("routes", self.build_routes),
                             ("sec", self.build_sec),
                             ("docker", self.build_docker),
                             ("web", self.build_web),
                             ("db", self.build_db),
                             ("cron", self.build_cron),
                             ("vpn", self._build_vpn),
                             ("api", self.build_api)):
            # 每个页面套一层可滚宿主：内容比可视区高时出竖滚动条，装得下就收起。
            # fill=True 让内胆吃满可视高度 —— 表格页靠 rowconfigure(weight=1)
            # 撑满，内胆不给足高度的话，套上滚动层就会退回「固定 13 行 +
            # 下方一大片空白」。
            # shrink：主体是表格的页面只压缩、不加外层滚动条。表格自己有内滚动，
            # 再套一层就是双重滚动，滚一下不知道动的是哪一层。
            #
            # 但「压缩」有个下限：文件管理这种一页塞两张卡的页面，卡片头 +
            # 路径条 + 搜索框是固定高度，压狠了表格直接消失（实测日志升起时
            # 两栏各只剩 95 像素，连表头都露不全，用户报「显示不完整」）。
            # 给个 min_h 之后，压不下就让整页滚 —— 外层滚动条这时才真的有用。
            host = U.ScrollBox(self.stack, fill=True, auto_hide=True,
                               shrink=key in _TABLE_PAGES,
                               hscroll=key in _HSCROLL_PAGES,
                               min_h=U.px(_MIN_PAGE_H.get(key, 0)))

            # 页面容器不参与尺寸协商：16 个页面 grid 在 stack 的同一格里，
            # grid 会取「所有子页请求宽度的最大值」当列宽 —— 只要有一页里
            # 有张宽表（安全中心两张表列宽合计 1311），整个 stack 就被撑到
            # 1668，每页都跟着变宽，右边那一截被外框直接裁掉（用户看到的
            # 「撤销上一步按钮没了」就是这么来的）。请求宽度压到 1×1 之后，
            # 由 paned 的实际分配决定，各页自己在该宽度内排版。
            host.configure(width=1, height=1)
            host.grid_propagate(False)
            host.grid(row=0, column=0, sticky="nsew")
            page = host.inner
            self._pages[key] = page
            self._page_host[key] = host
            builder(page)
            # 建完立刻从布局里摘掉，只留当前页在场。
            # 16 个页面全 grid 在同一格里时，它们**都**处于 mapped 状态，
            # 拖动窗口边缘每改一像素就要重画 1100 多个控件 —— 实测一步
            # 1.5 秒，拖起来像卡死。摘掉之后其他页仍然存在（状态、滚动
            # 位置、输入框内容都不丢），只是不参与布局与重绘。
            host.grid_remove()

        # 右侧「命令日志」面板已撤（v1.0.6）：它和「日志管理」页记的是同一件事，
        # 两份记录只会让人纠结去哪查。命令与输出现在统一进「日志管理」，
        # 并且在那里做了口令脱敏。右边这一栏现在只留给智能运维助手。
        self.tabbar.add("hosts", "主机资源", closable=False)
        self.tabbar.select("hosts")
        # 所有页都 grid 在同一格，后建的在最上层 —— 必须显式选一页。
        # 但命令行 --page 已经选过的话就别覆盖（否则截图/调试永远停在第一页）。
        self.after(20, self._ensure_first_page)

    def _ensure_first_page(self) -> None:
        if not getattr(self.nav, "current", None):
            self.nav.select("hosts")

    def _statusbar(self):
        bar = U.wframe(self, "page")
        bar.pack(fill="x", padx=U.px(10), pady=(0, U.px(8)))
        self.status = StatusBar(bar, on_disconnect=self._disconnect,
                                on_log=self.log_dock_toggle)
        self.status.pack(fill="x")

    def _show(self, key: str):
        key = NAV_ALIAS.get(key, key)
        # 日志不再是页签：--page log 或者旧脚本点「日志管理」都走这条
        if key == "log":
            self.log_dock_open()
            return
        # 左侧导航属于「主机资源」页签。人在终端页签时点了导航，要先把工作区切回来，
        # 否则会变成「导航高亮了但画面还是终端」。
        tb = getattr(self, "tabbar", None)
        if tb is not None and tb.current not in ("hosts", ""):
            tb.select("hosts")
        for k, page in self._pages.items():
            host = self._page_host.get(k, page)
            if k == key:
                # 只把当前页放回布局（其余页在创建时已 grid_remove）。
                # 曾经所有页都 grid 在同一格、靠 tkraise 分先后 —— 那样
                # 被盖住的页照样参与重绘，缩放窗口会明显卡顿。
                try:
                    host.grid()
                    # 切页回到顶部：页面级滚动位置留着的话，切过去看到的是
                    # 上次停在哪儿的中间一截，标题和第一个卡片都不在，
                    # 比「显示不全」更让人困惑。
                    host.cv.yview_moveto(0.0)
                except tk.TclError:
                    pass
                host.tkraise()
            elif host.winfo_manager() == "grid":
                try:
                    host.grid_remove()
                except tk.TclError:
                    pass
        # 运维工具那组页面要显示「操作目标」条；资产类页面收起来。
        # 放在 _no_probe 之前 —— 纯界面元素，截图时也得在。
        try:
            self._sync_devstrip(key)
        except (AttributeError, tk.TclError):
            pass
        # --no-probe：只画界面、不主动跑探测。截图与打包验收用得上 ——
        # 那几个探测会去调系统的 reg / schtasks，在某些受限环境里会卡住。
        if self._no_probe:
            return
        # 每次回到主机页都补一次自动探测：TTL 内不会重复探，但「上次没探成」
        # （页面还没映射、窗口刚建）的情况能在这里补上，不至于一直是「未检测」。
        if key == "hosts":
            self.after(200, self._maybe_auto_probe)
        # 页面懒加载：连上设备后再探测，免得开局就对着本机乱跑
        if key == "hosts" and not self._hosts_ready:
            self._hosts_ready = True
            self.after(80, self.host_refresh)
        elif key == "proxy" and not self._proxy_ready:
            self._proxy_ready = True
            self.after(80, self.proxy_refresh)
        elif key == "cred" and not self._cred_ready:
            self._cred_ready = True
            self.after(80, self.cred_refresh)
        elif key == "script" and not self._script_ready:
            self._script_ready = True
            self.after(80, self.script_refresh)
        elif key == "log" and not self._audit_ready:
            self._audit_ready = True
            self.after(80, self.audit_reload)
        elif key == "docker" and not self._dk_probed:
            self._dk_probed = True
            self.after(200, self.docker_probe)
        elif key == "api":
            self.after(80, self.api_refresh)
        elif key == "web" and not self._web_probed:
            self._web_probed = True
            self.after(200, self.web_probe)
        elif key == "files":
            self.after(120, self.files_sync)
        elif key == "vpn":
            self.after(150, self._vpn_boot)
        # 没连设备时也照样读 —— 和 Docker 一致，按本机读，别让页面空着
        elif key == "routes" and not self._routes_loaded:
            self._routes_loaded = True
            self.after(200, self.routes_refresh)
        elif key == "sec" and not self._sec_done:
            self._sec_done = True
            self.after(200, self.sec_scan)
            self._sec_fw_maybe_probe()
        elif key == "db" and not self._db_probed:
            self._db_probed = True
            self.after(200, self.db_probe)
        elif key == "cron" and not self._cron_loaded:
            self._cron_loaded = True
            self.after(200, self.cron_load)

    # ============================================================ 托盘
    def _refresh_current_page(self) -> None:
        """连上/断开设备后，把当前页的数据按新设备重取一遍。"""
        key = NAV_ALIAS.get(getattr(self.nav, "current", None) or "", "")
        if not key:
            key = getattr(self.nav, "current", None) or ""
        if self._no_probe:
            return
        if key == "hosts":
            self.after(120, self.host_refresh)
        elif key == "proxy":
            self.after(120, self.proxy_refresh)
        elif key == "cred":
            self.after(120, self.cred_refresh)
        elif key == "script":
            self.after(120, self.script_refresh)
        elif key == "log":
            self.after(120, self.audit_reload)
        elif key == "routes" and self.session:
            self._routes_loaded = True
            self.after(150, self.routes_refresh)
        elif key == "sec":
            self._sec_done = True
            self.after(150, self.sec_scan)
            self._sec_fw_maybe_probe()
        elif key == "db":
            self._db_probed = True
            self.after(150, self.db_probe)
        elif key == "cron":
            self._cron_loaded = True
            self.after(150, self.cron_load)
        elif key == "docker":
            self._dk_probed = True
            self.after(150, self.docker_probe)
        elif key == "web":
            self._web_probed = True
            self.after(150, self.web_probe)
        elif key == "files":
            self.after(150, self.files_sync)

    def _init_tray(self) -> None:
        try:
            self._tray = Tray(self, APP_NAME, str(ICON_ICO),
                              menu_factory=self._tray_menu,
                              on_show=self._show_from_tray,
                              post=self.post)
            if not self._tray.start():
                self._log_raw("meta", f"[托盘] 未能启动：{self._tray.last_error}\n")
        except Exception as e:  # noqa: BLE001
            self._tray = None
            self._log_raw("meta", f"[托盘] 初始化失败：{e}\n")

    def _tray_menu(self):
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="显示主窗口", command=self._show_from_tray)
        m.add_separator()
        state = "已锁定" if self._lock_overlay is not None else "未锁定"
        m.add_command(label=f"状态：{state} · {self._conn_summary()}",
                      state="disabled")
        m.add_separator()
        if self._lock_overlay is None and self.lock.enabled():
            m.add_command(label="立即锁定", command=lambda: self._show_lock("已手动锁定"))
        m.add_command(label="应用锁设置…", command=self._open_lock_window)
        m.add_separator()
        api_on = bool(self.api_server and self.api_server.running)
        m.add_command(label=("停止 API 服务" if api_on else "启动 API 服务"),
                      command=self.api_toggle)
        m.add_separator()
        m.add_command(label="隐藏到托盘", command=self._hide_to_tray)
        m.add_command(label="退出", command=self._quit_app)
        return m

    def _conn_summary(self) -> str:
        if self.session and self.session.alive:
            return self.session.label or "已连接"
        return "未连接"

    def _hide_to_tray(self) -> None:
        # 先记几何再收起来：withdraw 之后 winfo_x/y 就不可信了
        self._win_state_save()
        if self._tray is None or not self._tray.running:
            self.iconify()
            self.toast("托盘不可用，已最小化到任务栏", "warn")
            return
        self.withdraw()
        # 系统气泡在 Win11 上经常被「专注助手」吞掉，用户就以为程序没了。
        # 所以除了气泡，再自己画一个悬浮提示 —— 这个是自绘窗口，一定看得见。
        try:
            self._tray.notify(APP_NAME, "已收进托盘，双击图标恢复")
        except Exception:  # noqa: BLE001
            pass
        self._tray_hint()

    def _tray_hint(self) -> None:
        """收起后给一个自绘小提示：图标去哪了、怎么彻底退出。

        不能只靠 Shell_NotifyIcon 的气泡：Win11 默认把新图标塞进任务栏右下角
        的「︿」溢出区，气泡也常被专注助手压掉，用户找不到就以为进程残留。
        """
        self._hide_tray_hint()
        try:
            area = winstate.work_area()
            w, h = U.px(376), U.px(92)
            x = area[0] + area[2] - w - U.px(16)
            y = area[1] + area[3] - h - U.px(16)
            win = tk.Toplevel(self)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.geometry(f"{w}x{h}+{x}+{y}")
            body = tk.Frame(win, bg=U.T.card, bd=0,
                            highlightthickness=1, highlightbackground=U.T.border)
            body.pack(fill="both", expand=True)
            U.wlabel(body, "已收进系统托盘", "card",
                     (U.FONT_FAMILY, 10, "bold")).pack(
                fill="x", padx=U.px(12), pady=(U.px(10), 0))
            U.wlabel(body,
                     "图标在任务栏右下角的「︿」里，双击恢复。\n"
                     "右键图标 → 退出，才是真正结束进程。",
                     "mute", U.FONT_XS, justify="left").pack(
                fill="x", padx=U.px(12), pady=(U.px(2), U.px(10)))
            win.bind("<Button-1>", lambda _e: self._hide_tray_hint())
            for child in body.winfo_children():
                child.bind("<Button-1>", lambda _e: self._hide_tray_hint())
            self._tray_hint_win = win
            self.after(9000, self._hide_tray_hint)
        except Exception:  # noqa: BLE001
            self._tray_hint_win = None

    def _hide_tray_hint(self) -> None:
        win, self._tray_hint_win = self._tray_hint_win, None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

    def _show_from_tray(self) -> None:
        self._hide_tray_hint()
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def _wake_poll(self) -> None:
        """有人又双击了一次程序图标 → 把自己叫到前台。

        「参考微信」：只允许一个进程，第二次双击就把已有窗口弹出来，
        而不是默默多开一份（多开会各自写一套 data，托盘里还两个图标）。
        """
        try:
            # key 必须和 main() 里 acquire 用的一致，否则测试实例（另一个
            # 命名空间）被人双击时，用户那个实例会收到跟自己无关的唤醒信号。
            if singleinstance.consume(singleinstance.key_from_argv(sys.argv)):
                self._show_from_tray()
                try:
                    self.status.set_state(
                        "已经有一个窗口在运行，给你叫到前面来了", "warn")
                except (AttributeError, tk.TclError):
                    pass
        except Exception:  # noqa: BLE001  单实例是锦上添花，不能拖垮主循环
            pass
        try:
            self.after(700, self._wake_poll)
        except tk.TclError:
            pass

    def _on_close(self) -> None:
        if self._close_to_tray and self._tray and self._tray.running:
            self._hide_to_tray()
            return
        self._quit_app()

    # ============================================================ 窗口几何
    def _apply_win_geometry(self) -> None:
        """开窗尺寸：先按工作区自适应，再套上次退出时的位置/大小/最大化。

        默认 1360×880 是**逻辑**像素，在 150% 缩放的笔记本上会算成
        2040×1320 物理像素 —— 比整块屏幕还高，一启动就被任务栏截掉、
        标题栏跑出屏幕。所以一律先压进 SPI_GETWORKAREA。
        """
        try:
            screen = (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())
        except tk.TclError:
            screen = (0, 0, 1280, 720)
        area = winstate.work_area(screen)
        box = winstate.restore_box(
            self._win_state.data, area, float(U.S),
            U.px(1360), U.px(880), U.px(900), U.px(560))
        self.geometry(box.geometry())
        self._win_normal = (box.x, box.y, box.w, box.h)
        # minsize 也得让位：屏幕比 minsize 还小时硬按 minsize 开，
        # 就是一个必然超屏的窗口 —— 跟默认高度过高是同一个坑。
        aw, ah = area[2], area[3]
        self.minsize(
            min(U.px(1150), max(U.px(760), aw - U.px(16))),
            min(U.px(720), max(U.px(460), ah - U.px(16))))
        if box.zoomed:
            # 最大化要等窗口真映射出来再设，太早会被 WM 忽略
            self.after(120, self._win_zoom)
        else:
            # 先按客户区摆一次，再按外框实测夹一次：Tk 的 geometry() 只说
            # 客户区，标题栏那三四十像素不在里面，光按客户区算窗口整体
            # 还是会把底部压到任务栏底下。
            self._win_fit_sync()

    def _win_fit_sync(self) -> None:
        """按 Win32 实测的**外框**把窗口重新夹进工作区。

        只在启动时做一次 —— 用户后面手动拉窗口/移动，那是他的事，
        再插一脚会变成跟用户抢方向盘。
        """
        try:
            if self.state() == "zoomed":
                return
            self.update_idletasks()
            hwnd = int(str(self.wm_frame()), 16)
            cw, ch = self.winfo_width(), self.winfo_height()
        except (tk.TclError, ValueError):
            return
        rect = winstate.window_rect(hwnd)
        if rect is None or cw <= 1 or ch <= 1:
            return
        try:
            screen = (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())
        except tk.TclError:
            screen = (0, 0, 1280, 720)
        ax, ay, aw, ah = winstate.work_area(screen)
        ox, oy, ow, oh = rect
        dw, dh = max(0, ow - cw), max(0, oh - ch)   # 标题栏 + 边框
        m = winstate.DEFAULT_MARGIN
        nw = min(cw, max(240, aw - m * 2 - dw))
        nh = min(ch, max(160, ah - m * 2 - dh))
        nx = min(max(ox, ax), ax + max(0, aw - (nw + dw)))
        ny = min(max(oy, ay), ay + max(0, ah - (nh + dh)))
        if (nw, nh, nx, ny) == (cw, ch, ox, oy):
            self._win_normal = (nx, ny, nw, nh)
            return
        try:
            self.geometry(f"{nw}x{nh}+{nx}+{ny}")
            self.update_idletasks()
        except tk.TclError:
            return
        self._win_normal = (nx, ny, nw, nh)

    def _win_zoom(self) -> None:
        try:
            self.state("zoomed")            # Windows / 部分 WM
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)   # X11
            except tk.TclError:
                pass

    def _on_win_configure(self, event) -> None:
        """记住「普通状态」下的矩形（最大化时不算，否则恢复回来是个满屏窗口）。"""
        # 子控件的 Configure 也会落到 toplevel 的 bindtags 上，不筛的话
        # 每动一下分隔条都在记窗口位置
        if event.widget is not self:
            return
        try:
            if self.state() == "zoomed":
                return
        except tk.TclError:
            return
        if event.width < U.px(240) or event.height < U.px(160):
            return
        try:
            self._win_normal = (self.winfo_x(), self.winfo_y(),
                                event.width, event.height)
        except tk.TclError:
            pass

    def _win_state_save(self) -> None:
        """退出前记下窗口的样子。最小化时坐标是 -32000，绝不落盘。"""
        try:
            st = self.state()
        except tk.TclError:
            return
        if st == "iconic":
            return
        zoomed = (st == "zoomed")
        nx, ny, nw, nh = self._win_normal or (0, 0, 0, 0)
        if not zoomed:
            # 非最大化时以当前实际矩形为准：用户可能刚拖过窗口，
            # Configure 里那份还没跟上
            try:
                nx, ny = self.winfo_x(), self.winfo_y()
                nw, nh = self.winfo_width(), self.winfo_height()
            except tk.TclError:
                return
        if nw <= 0 or nh <= 0:
            return
        try:
            x, y = self.winfo_x(), self.winfo_y()
        except tk.TclError:
            x, y = nx, ny
        log_open = bool(getattr(self, "_log_dock_on", False))
        log_h = 0
        if getattr(self, "_log_dock_built", False):
            try:
                log_h = int(self.log_dock_host.winfo_height())
            except tk.TclError:
                log_h = 0
        if log_h <= 1:
            log_h = int(getattr(self, "_log_dock_h", 0) or 0)
        self._win_state.save({
            "x": int(x), "y": int(y),
            "w": int(nw), "h": int(nh),
            "nx": int(nx), "ny": int(ny), "nw": int(nw), "nh": int(nh),
            "zoomed": bool(zoomed), "scale": float(U.S),
            "log_open": log_open, "log_h": log_h,
            # 这个高度是「程序按默认值定的」还是「用户自己拖出来的」。
            # 只存数字的话，改默认值等于白改（老存档会一直把旧默认带下去，
            # v1.0.19 和 v1.0.22 都栽在这上面）；只靠数值区间猜则会误伤
            # 用户真的拖到那个高度的情况。存个标记最省事。
            "log_h_auto": bool(getattr(self, "_log_dock_auto", False)),
        })

    def _cancel_timers(self) -> None:
        """撤掉所有自我续期的定时器，否则窗口销毁后 Tcl 会反复报
        invalid command name ..._tick / ...tick。"""
        self._alive = False
        for name in ("_lock_after", "_sample_after", "_drain_after"):
            tid = getattr(self, name, None)
            if tid is None:
                continue
            try:
                self.after_cancel(tid)
            except Exception:  # noqa: BLE001
                pass
            setattr(self, name, None)
        # 还有一批一次性 after（toast 复位、密钥信息刷新、锁屏抖动动画…）
        # 各自挂着 python 回调，窗口没了就不该再跑。Tcl 的 after 队列挂在
        # 解释器上而不是控件上，漏掉一个，下个实例泵事件时就会冒
        # invalid command name。这里一并撤干净。
        try:
            rest = self.tk.call("after", "info")
        except Exception:  # noqa: BLE001
            return
        if isinstance(rest, str):
            rest = (rest,) if rest else ()
        for tid in rest:
            try:
                self.tk.call("after", "cancel", tid)
            except Exception:  # noqa: BLE001
                pass

    def destroy(self) -> None:
        self._cancel_timers()
        # 提权助手是**管理员进程**：主程序退了它还活着，用户在任务管理器里
        # 看到会慌，也是一条没人管的提权后门。必须跟着一起收。
        try:
            elev.shutdown()
        except Exception:  # noqa: BLE001
            pass
        super().destroy()

    def _quit_app(self) -> None:
        self._win_state_save()
        self._hide_tray_hint()
        self._cancel_timers()
        try:
            if self._tray:
                self._tray.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            # 退出时的停服不是用户的意思，别把 enabled 落成 false，
            # 否则下次启动 _boot_api() 不会自动恢复
            self.api_server.stop(keep_enabled=True)
        except Exception:  # noqa: BLE001
            pass
        # MCP 是本机起的子进程，退出得收干净，否则会留下一堆孤儿进程
        try:
            self.mcp_pool.stop_all()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    # ============================================================ API
    def _boot_api(self) -> None:
        """上次退出前是开着的，就自动恢复。"""
        if self._api_inited:
            return
        self._api_inited = True
        if bool(self.api_cfg.data.get("enabled")):
            ok, msg = self.api_server.start()
            self._log_raw("meta", f"[API] {msg}\n")
        self.api_refresh()

    def _api_event(self, kind: str, text: str) -> None:
        """池里的事件（建连 / 关闭 / 重连）走日志与状态栏。

        除了内存流水，还要落一条操作审计 —— 「日志」底部面板读的是审计库，
        不落的话智能体自己建连、关连在日志里是空白（用户要求
        「日志也要显示智能体通过 API 的操作」）。
        """
        self.post(self._log_raw, kind, f"[API] {text}\n")
        if kind in ("ok", "warn", "bad"):
            self.post(self.status.set_state, text, kind)
        st = {"ok": "ok", "warn": "warn", "bad": "fail"}.get(kind, "info")
        self.post(self._audit, "ai.session", st, text, "", "智能体")

    def _api_log_line(self, kind: str, text: str) -> None:
        self._log_raw(kind, f"[API] {text}\n")

    # 给 api_page 用的别名
    def api_log_line(self, kind: str, text: str) -> None:
        self._api_log_line(kind, text)

    # ============================================================ 统一小件
    def _effective_device(self) -> DeviceInfo:
        """当前该按哪套系统方言办事：连着就是设备，没连就是本机。"""
        if self.session and self.session.alive and self.device.os_family:
            return self.device
        if self._local_dev is None:
            d = DeviceInfo()
            d.os_family = {"win32": "windows", "darwin": "darwin"}.get(
                sys.platform, "linux")
            names = {"windows": "Windows（本机）", "darwin": "macOS（本机）"}
            d.os_name = names.get(d.os_family, "Linux（本机）")
            d.shell = "powershell" if d.os_family == "windows" else "sh"
            d.user = self._local_user()
            d.is_root = self._local_is_root()
            self._local_dev = d
        return self._local_dev

    @staticmethod
    def _local_user() -> str:
        import os
        return os.environ.get("USERNAME") or os.environ.get("USER") or ""

    def _local_is_root(self) -> bool:
        if sys.platform != "win32":
            import os
            try:
                return os.geteuid() == 0
            except AttributeError:
                return False
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            return False

    def copy_text(self, text: str, label: str = "") -> bool:
        """统一写剪贴板：写完立刻 update_idletasks，别等事件循环空闲。

        Tk 的剪贴板由本进程持有，所以内容只在程序活着的时候有效 —— 复制后
        该弹窗的还是要弹窗，别指望用户「复制了但看不见」。
        """
        if not text:
            self.toast("没有可复制的内容", "warn")
            return False
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
        except tk.TclError as e:
            self.toast(f"复制失败：{e}", "err")
            return False
        self.toast(f"{label}已复制到剪贴板 · {len(text)} 字" if label
                   else f"已复制到剪贴板 · {len(text)} 字", "ok")
        return True

    # ---------------- 操作审计
    @property
    def _os_family(self) -> str:
        """当前按哪套系统方言办事（脚本库要按它挑默认脚本类型）。"""
        try:
            return str(self._effective_device().os_family or "")
        except Exception:  # noqa: BLE001
            return ""

    def _audit(self, event: str, status: str = "ok", detail: str = "",
               device: str = "", actor: str = "用户") -> None:
        """记一条操作审计。

        写日志失败绝不能把业务动作带崩（磁盘满、权限不对都可能写不进），
        但也不吞得无声无息 —— 状态栏提一句，用户知道「这步没留痕」。
        """
        try:
            dev = device
            if not dev:
                sess = getattr(self, "session", None)
                dev = str(getattr(sess, "label", "") or "")
            self.audit.record(event, status, detail, dev, actor)
        except Exception as e:  # noqa: BLE001
            try:
                self.toast(f"操作日志没写进去：{e}", "warn")
            except Exception:  # noqa: BLE001
                pass

    def toast(self, text: str, tone: str = "ok", ms: int = 3200) -> None:
        """状态栏上的临时提示（比 messagebox 轻，不打断操作）。"""
        try:
            self.status.set_state(text, tone)
        except Exception:  # noqa: BLE001
            pass
        if self._toast_job:
            try:
                self.after_cancel(self._toast_job)
            except Exception:  # noqa: BLE001
                pass
        self._toast_job = self.after(ms, lambda: self.status.set_state("就绪"))

    def _toggle_theme(self):  # noqa: D401  设置中心里也可以直接切

        U.toggle_theme()
        self.configure(bg=U.T.page)
        self.status.paint_all()

    # ============================================================ 线程与队列
    def post(self, fn, *a):
        self.q.put((fn, a))

    def bg(self, fn, ok=None, err=None, allow_elev: bool = True):
        def run():
            try:
                r = fn()
                if ok:
                    self.post(ok, r)
            except NeedElevation as e:
                # 缺 sudo / enable 密码：交给主线程弹窗问一次，问到就重跑这条任务。
                # 重跑时 allow_elev=False，避免密码一直错导致无限弹窗。
                if allow_elev:
                    self.post(self._ask_elevation, e, fn, ok, err)
                else:
                    self.post(self._fail_bg, str(e), err)
            except Exception as e:  # noqa: BLE001
                tb = traceback.format_exc()
                msg = str(e) or e.__class__.__name__
                self.post(self._fail_bg, msg, err, tb)
        threading.Thread(target=run, daemon=True).start()

    def _fail_bg(self, msg, err=None, tb: str = "") -> None:
        if err:
            try:
                err(msg)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        else:
            self.status.set_state(msg, "err")
        if tb:
            self._log_raw("bad", f"[异常] {msg}\n{tb}")

    def _ask_elevation(self, exc, fn, ok, err) -> None:
        """需要提权时的唯一入口：Windows 问要不要重启到管理员，其他问密码。

        用户的要求是「不勾选 sudo 密码时，需要提权就弹窗」——
        所以密码框不预填、不落盘，用完只在内存里留到本次会话结束。
        """
        s = self.session
        if exc.kind == "uac":
            if not messagebox.askyesno(
                    "需要管理员权限",
                    f"{exc}\n\n点「是」现在弹一次 UAC 授权，通过后自动重试这项操作。\n"
                    "**程序不会重启**，当前会话不受影响。",
                    parent=self):
                self.status.set_state("需要管理员权限", "warn")
                return

            def redo():
                self.bg(fn, ok, err, allow_elev=False)

            self._elevate_now(then=redo)
            return

        if s is None or not hasattr(s, "sudo_ok"):
            self.status.set_state(str(exc), "warn")
            return

        want_enable = exc.kind == "enable"
        data = forms.FormDialog.open(
            self,
            "进入特权模式" if want_enable else "需要 sudo 密码",
            [{"key": "pw",
              "label": "enable 密码" if want_enable else "sudo 密码",
              "kind": "password", "required": True, "width": 26,
              "hint": "只在内存里用到本次会话结束，不写进档案"}],
            subtitle=f"{exc}\n目标：{exc.target or '当前会话'}",
            submit="提权并继续")
        if not data:
            self.status.set_state("已取消提权", "warn")
            return
        pw = str(data.get("pw") or "")

        def work():
            return bool(s.sudo_ok(pw))

        def done(good):
            if good:
                self._log_raw("ok", "[提权] 密码通过，重试刚才的操作\n")
                self.bg(fn, ok=ok, err=err, allow_elev=False)
            else:
                self._log_raw("bad", "[提权] 密码没通过\n")
                messagebox.showwarning("提权失败", "密码没通过，操作已取消。",
                                       parent=self)
                self.status.set_state("提权失败", "warn")

        self.bg(work, ok=done,
                err=lambda m: self.status.set_state(m, "err"),
                allow_elev=False)

    def _drain(self):
        if not self._alive:
            return
        try:
            while True:
                fn, a = self.q.get_nowait()
                try:
                    fn(*a)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
        except queue.Empty:
            pass
        if not self._alive:
            return
        try:
            self._drain_after = self.after(80, self._drain)
        except tk.TclError:
            self._drain_after = None

    # ============================================================ 日志
    # 脱敏规则统一放在 core/redact.py：日志、工具输出、系统信息、终端、
    # VPN 控制台共用一套，避免加一个面板就漏一个出口。
    SCRUB = redact.PEM

    def _scrub(self, text: str) -> str:
        return redact.scrub(text)

    def _log_raw(self, kind: str, text: str) -> None:
        """留一份内存流水（诊断、导出时用）。

        界面上不再有第二份「命令日志」面板 —— 能查的地方只有「日志管理」，
        两边都摆一份只会让人纠结去哪看。这里只是留底。
        """
        self.log_model.add(kind, text)

    def _audit_command(self, cmd: str, rc: int, out: str, err: str,
                       ms: int, by_api: bool = False) -> None:
        """把一条命令执行记进操作审计（「日志管理」页读的就是它）。

        用户的要求是「日志里能看到执行了什么命令、输出了什么」，同时
        「涉及输入密码、口令的不要显示」。所以这里三件事必须做：

          1. 命令过 scrub_cmd：sshpass -p、mysql -pXXX、curl -u 用户:口令、
             URL 内嵌凭据、echo 口令 | sudo -S 全都打成 ******；
          2. 输出过 scrub：私钥正文整块换成一行说明，内容不上日志；
          3. 输出截断到 4000 字：一条几兆的记录会把日志页拖垮，
             定位问题也用不着那么多。

        by_api=True 表示这条命令是智能体通过本地 API 跑的：事件名用
        `ai.command`（分类「AI」），执行者是「智能体」而不是「用户」。
        混在一起的话，事后根本分不清那次改动是人点出来的还是模型跑出来的。
        """
        safe_cmd = redact.scrub_cmd(cmd or "")
        body = self._scrub(out or "")
        errs = self._scrub(err or "")
        detail = f"$ {safe_cmd}\nrc={rc} · {ms} ms"
        if body.strip():
            detail += "\n" + body[:4000]
            if len(body) > 4000:
                detail += "\n…（输出共 %d 字，已截断）" % len(body)
        if errs.strip():
            detail += "\n[stderr] " + errs[:1000]
        # 状态只能是 ok / fail / warn / info（audit.STATUS 里就这四个，
        # 传别的会被默默归一成 info —— 失败的命令就看不出来了）
        self._audit("ai.command" if by_api else "terminal.command",
                    "fail" if rc != 0 else "ok", detail,
                    actor="智能体" if by_api else "用户")

    def _trace_cmd(self, display, rc, out, err, ms):
        cmd = str(display or "")
        # 会话池给智能体跑的每条命令都带这个前缀（见 api/pool.py::_trace）。
        # 有它才分得清「人敲的」和「智能体跑的」—— 审计里连 actor 都不一样。
        by_api = cmd.startswith(API_CMD_TAG)
        shown = cmd

        def render():
            self._log_raw("meta", time.strftime("[%H:%M:%S] "))
            self._log_raw("ok" if rc == 0 else "bad", f"rc={rc}  {ms}ms\n")
            self._log_raw("cmd", "$ " + redact.scrub_cmd(shown) + "\n")
            o, e = self._scrub(out), self._scrub(err)
            if o.strip():
                self._log_raw("out", o if o.endswith("\n") else o + "\n")
            if e.strip():
                self._log_raw("bad", e if e.endswith("\n") else e + "\n")
            self._log_raw("meta", "\n")
            # 落一条操作审计 —— 运维工具跑的每条命令都会出现在「日志管理」里
            self._audit_command(cmd, rc, out, err, ms, by_api=by_api)
            # 终端开着就把同一条记录也送过去（终端自己敲的命令会被它标记跳过）
            self._term_feed(shown, rc, out, err, ms)
        self.post(render)

    # ============================================================ 采样
    def _start_sampler(self):
        def tick():
            # 会话可能是半拆状态（关闭中 / 传输层已断），这里探活失败不该让
            # 状态栏定时器每秒抛一次异常刷屏。
            try:
                need = (not self._sampling and self.session is not None
                        and bool(getattr(self.session, "alive", False))
                        and self.sampler is not None)
            except Exception:  # noqa: BLE001
                need = False
            if need:
                self._sampling = True

                def work():
                    try:
                        s = self.sampler.sample(timeout=12)
                    except Exception as e:  # noqa: BLE001
                        from core.metrics import Sample
                        s = Sample(ok=False, error=str(e))
                    self.post(self._on_sample, s)
                threading.Thread(target=work, daemon=True).start()
            if not self._alive:
                return
            try:
                self._sample_after = self.after(SAMPLE_MS, tick)
            except tk.TclError:
                self._sample_after = None
        self._sample_after = self.after(600, tick)

    def _on_sample(self, s):
        self._sampling = False
        if self.sampler:
            self.status.update_sample(s, list(self.sampler.cpu_hist))
            if not s.ok and s.error:
                self.status.set_state(s.error[:80], "err")

    # ============================================================ 连接
    # 「目标系统」下拉的选项：值 → 显示。提权方式由它决定。
    # 目标系统可选值的唯一来源在 core/hosts.OS_PICK（主机表单也用它）
    OS_UI = list(hosts_core.OS_PICK)

    def _sync_ostype_ui(self) -> None:
        """把 v_ostype 的值反映到下拉框上（档案里存的是 key，界面显示中文）。"""
        cb = getattr(self, "cb_ostype", None)
        var = getattr(self, "v_ostype", None)
        if cb is None or var is None:
            return
        try:
            key = str(var.get() or "")
        except tk.TclError:
            return
        try:
            cb.set(dict(self.OS_UI).get(key, self.OS_UI[0][1]))
        except tk.TclError:
            pass

    def _build_conn(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(2, weight=1)

        # ---- 会话
        c = U.Card(pg, title="会话", hint="SSH / Telnet / 本机")
        c.grid(row=0, column=0, sticky="we")
        b = c.body

        self.v_proto = tk.StringVar(value="ssh")
        r = U.wframe(b, "card")
        r.pack(fill="x")
        for val, label in (("ssh", "SSH"), ("telnet", "Telnet"), ("local", "本机")):
            ttk.Radiobutton(r, text=label, value=val, variable=self.v_proto,
                            style="Card.TRadiobutton",
                            command=self._proto_changed).pack(side="left", padx=(0, 20))

        # 目标系统：决定提权问的是 sudo 密码、enable 密码，还是根本不用密码。
        # 默认「自动」—— 连上后 detect() 会把真实系统写进档案，下次打开就是准的。
        self.v_ostype = tk.StringVar(value="")
        self.row_ostype = U.wframe(b, "card")
        self.row_ostype.pack(fill="x", pady=(8, 0))
        U.wlabel(self.row_ostype, "目标", "card", U.FONT_S).pack(side="left")
        _OS_KEY = {lab: k for k, lab in self.OS_UI}
        self.cb_ostype = ttk.Combobox(
            self.row_ostype, state="readonly", width=18, font=U.FONT_S,
            values=[lab for _, lab in self.OS_UI])
        self.cb_ostype.pack(side="left", padx=(8, 0))
        self._sync_ostype_ui()

        def _os_pick(_e=None, c=self.cb_ostype):
            try:
                self.v_ostype.set(_OS_KEY.get(c.get(), ""))
            except tk.TclError:
                return
            self._rebuild_elevation()

        self.cb_ostype.bind("<<ComboboxSelected>>", _os_pick)
        U.wlabel(self.row_ostype, "提权那一块跟着它变", "dim", U.FONT_XS).pack(
            side="left", padx=(10, 0))

        self.v_prof = tk.StringVar()
        # 每个口令各自一个「保存」开关：只勾私钥口令就只存私钥口令，
        # 不再用一个总开关去猜要存哪几项。
        self.v_save_keypw = tk.BooleanVar(value=False)
        self.v_save_pw = tk.BooleanVar(value=False)
        self.v_save_sudo = tk.BooleanVar(value=False)
        self.v_remember = tk.BooleanVar(value=False)   # 上面三项的汇总，只读
        self.v_host = tk.StringVar(value="192.168.3.12")
        self.v_port = tk.StringVar(value="22")
        self.v_user = tk.StringVar(value="")
        self.v_mode = tk.StringVar(value="key")
        self.v_key = tk.StringVar()
        self.v_keypw = tk.StringVar()
        self.v_pw = tk.StringVar()
        self.v_sudopw = tk.StringVar()

        # 档案
        rowp = U.wframe(b, "card")
        self.row_prof = rowp
        rowp.pack(fill="x", pady=(10, 0))
        U.wlabel(rowp, "档案", "card", U.FONT_S).pack(side="left")
        self.cb_prof = ttk.Combobox(rowp, textvariable=self.v_prof,
                                    state="readonly", width=20)
        self.cb_prof.pack(side="left", padx=(8, 6))
        self.cb_prof.bind("<<ComboboxSelected>>", self._on_profile_pick)
        U.RoundButton(rowp, "保存", self._profile_save, variant="secondary",
                      font=U.FONT_S, height=27, padx=11).pack(side="left")
        U.RoundButton(rowp, "删除", self._profile_delete, variant="danger",
                      font=U.FONT_S, height=27, padx=11).pack(side="left", padx=5)
        U.RoundButton(rowp, "导入 ssh config", self._profile_import, variant="ghost",
                      font=U.FONT_S, height=27, padx=9).pack(side="right")

        # 地址行
        row = U.wframe(b, "card")
        self.row_addr = row
        row.pack(fill="x", pady=(9, 0))
        row.columnconfigure(1, weight=1)
        U.wlabel(row, "地址", "card", U.FONT_S).grid(row=0, column=0, sticky="w")
        self.e_host = ttk.Entry(row, textvariable=self.v_host)
        self.e_host.grid(row=0, column=1, sticky="we", padx=(10, 8))
        U.wlabel(row, "端口", "card", U.FONT_S).grid(row=0, column=2, sticky="w")
        ttk.Entry(row, textvariable=self.v_port, width=7).grid(
            row=0, column=3, sticky="w", padx=(8, 0))
        U.wlabel(row, "用户", "card", U.FONT_S).grid(row=1, column=0,
                                                     sticky="w", pady=(7, 0))
        ttk.Entry(row, textvariable=self.v_user).grid(
            row=1, column=1, columnspan=3, sticky="we", padx=(10, 0), pady=(7, 0))

        # 认证
        self.row_auth = U.wframe(b, "card")
        self.row_auth.pack(fill="x", pady=(9, 0))
        ra = U.wframe(self.row_auth, "card")
        ra.pack(fill="x")
        ttk.Radiobutton(ra, text="私钥", value="key", variable=self.v_mode,
                        style="Card.TRadiobutton",
                        command=self._mode_changed).pack(side="left")
        ttk.Radiobutton(ra, text="登录密码", value="password", variable=self.v_mode,
                        style="Card.TRadiobutton",
                        command=self._mode_changed).pack(side="left", padx=18)

        self.row_key = U.wframe(self.row_auth, "card")
        self.row_key.pack(fill="x", pady=(7, 0))
        self.row_key.columnconfigure(0, weight=1)
        self.e_key = ttk.Entry(self.row_key, textvariable=self.v_key)
        self.e_key.grid(row=0, column=0, sticky="we")
        self.e_key.bind("<FocusOut>", lambda e: self._refresh_key_info())
        U.RoundButton(self.row_key, "浏览…", self._pick_key, variant="secondary",
                      font=U.FONT_S, height=27, padx=10).grid(row=0, column=1, padx=(6, 0))
        U.RoundButton(self.row_key, "发现 ▾", self._key_menu, variant="secondary",
                      font=U.FONT_S, height=27, padx=10).grid(row=0, column=2, padx=(6, 0))
        self.lbl_key = U.wlabel(self.row_key, "未选择私钥", "dim", U.FONT_XS,
                                justify="left")
        self.lbl_key.grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 0))

        rk = U.wframe(self.row_auth, "card")
        rk.pack(fill="x", pady=(6, 0))
        self.row_keypw = rk
        U.wlabel(rk, "私钥口令", "card", U.FONT_S).pack(side="left")
        self.e_keypw = ttk.Entry(rk, textvariable=self.v_keypw, show="*", width=18)
        self.e_keypw.pack(side="left", padx=(8, 0))
        self.v_keypw.trace_add("write", self._on_keypw_changed)
        # 「保存口令」就地放在这个框后面：勾了才在档案里留下口令，
        # 每项自己管自己，不需要滚到下面再找一个总开关。
        U.Check(rk, "保存口令", self.v_save_keypw).pack(side="left", padx=(8, 0))
        self.btn_eye = U.RoundButton(rk, "👁", self._toggle_keypw_eye,
                                     variant="ghost", font=U.FONT_S,
                                     height=24, padx=7)
        self.btn_eye.pack(side="left", padx=(2, 0))

        self.row_pw = U.wframe(self.row_auth, "card")
        self.row_pw.pack(fill="x", pady=(6, 0))
        U.wlabel(self.row_pw, "登录密码", "card", U.FONT_S).pack(side="left")
        self.e_pw = ttk.Entry(self.row_pw, textvariable=self.v_pw, show="*", width=22)
        self.e_pw.pack(side="left", padx=(8, 0))
        U.RoundButton(self.row_pw, "👁", lambda: self._toggle_eye(self.e_pw),
                      variant="ghost", font=U.FONT_S, height=24,
                      padx=7).pack(side="left", padx=(2, 0))
        U.Check(self.row_pw, "保存口令", self.v_save_pw).pack(side="left",
                                                             padx=(8, 0))

        # 提权：内容随「协议 + 本机系统」变，见 _rebuild_elevation()
        self.v_elevate = tk.BooleanVar(value=False)
        self.v_admin = tk.BooleanVar(value=False)
        # 本机 Windows 的执行外壳：auto / powershell / cmd
        self.v_shell = tk.StringVar(value="")
        self.row_elev = U.wframe(b, "card")
        self.row_elev.pack(fill="x", pady=(9, 0))
        self.elev_holder = self.row_elev

        # 凭据保存不再有总开关：每个口令框后面各有一个「保存口令」。
        # 这里只留一行说明，告诉用户勾与不勾分别会落在哪里。
        self.row_cred = U.wframe(b, "card")
        self.row_cred.pack(fill="x", pady=(9, 0))
        self.lbl_cred = U.wlabel(self.row_cred, "", "dim", U.FONT_XS, anchor="w")
        self.lbl_cred.pack(side="left")

        act = U.wframe(b, "card")
        act.pack(fill="x", pady=(11, 0))
        self.btn_connect = U.RoundButton(act, "连接并探测", self.do_connect,
                                         variant="primary", height=34, padx=20)
        self.btn_connect.pack(side="left")
        U.RoundButton(act, "断开", self._disconnect, variant="ghost",
                      height=34, padx=14).pack(side="left", padx=(8, 0))

        # 任何一项口令开关变化都重算一次提示，用户不用猜勾选框管谁
        for var in (self.v_save_keypw, self.v_save_pw, self.v_save_sudo,
                    self.v_keypw, self.v_pw, self.v_sudopw):
            var.trace_add("write", lambda *_: self._remember_synced())
        self._refresh_cred_hint()

        # ---- 设备概览
        c2 = U.Card(pg, title="设备概览", hint="连接后自动探测")
        c2.grid(row=1, column=0, sticky="we", pady=(10, 0))
        self.kv_holder = c2.body
        self._kv_empty()

        # ---- 最近输出
        c3 = U.Card(pg, title="最近输出", hint="右侧日志保留完整历史")
        c3.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        self.txt_out = tk.Text(c3.body, bg=U.T.console_bg, fg=U.T.console_fg,
                               font=U.MONO_S, bd=0, relief="flat",
                               highlightthickness=0, wrap="none",
                               padx=U.px(10), pady=U.px(8), height=8)
        self.txt_out.pack(fill="both", expand=True)
        self.txt_out.configure(state="disabled")

        self._mode_changed()
        self._rebuild_elevation()

    def _kv_empty(self):
        for w in self.kv_holder.winfo_children():
            w.destroy()
        U.wlabel(self.kv_holder, "尚未连接。填好上面几项后点「连接并探测」。",
                 "dim", U.FONT_S).pack(anchor="w")

    def _kv(self, label: str, value: str, tone: str = "card"):
        row = U.wframe(self.kv_holder, "card")
        row.pack(fill="x", pady=2)
        row.columnconfigure(1, weight=1)
        U.wlabel(row, label, "dim", U.FONT_S).grid(row=0, column=0, sticky="nw")
        role = {"ok": "ok", "err": "err", "warn": "warn", "dim": "dim"}.get(tone, "card")
        U.wlabel(row, value or "—", role, U.FONT_S, justify="left",
                 wraplength=U.px(560)).grid(row=0, column=1, sticky="w", padx=(14, 0))

    # ---------------- 交互
    def _proto_changed(self):
        proto = self.v_proto.get()
        if proto == "local":
            # 本机不需要 IP / 端口 / 用户 / 私钥，那些是给远程设备准备的；
            # 档案行也一并收起来 —— 本机没有「设备档案」这个概念。
            self.v_port.set("")
            self.e_host.configure(state="disabled")
            self.row_prof.pack_forget()
            self.row_addr.pack_forget()
            self.row_auth.pack_forget()
            try:
                self.row_ostype.pack_forget()
            except (AttributeError, tk.TclError):
                pass
        else:
            if not self.row_prof.winfo_manager():
                self.row_prof.pack(fill="x", pady=(10, 0), before=self.row_elev)
            if not self.row_addr.winfo_manager():
                self.row_addr.pack(fill="x", pady=(9, 0), before=self.row_elev)
            try:
                if not self.row_ostype.winfo_manager():
                    self.row_ostype.pack(fill="x", pady=(8, 0),
                                         before=self.row_prof)
            except (AttributeError, tk.TclError):
                pass
            self.e_host.configure(state="normal")
            if proto == "telnet":
                self.v_port.set("23")
                self.row_auth.pack_forget()
            else:
                if self.v_port.get() in ("", "23"):
                    self.v_port.set("22")
                if not self.row_auth.winfo_manager():
                    self.row_auth.pack(fill="x", pady=(9, 0),
                                       before=self.row_elev)
                self._mode_changed()
        # 本机 Windows 没有要输的口令，这行提示留着只会让人以为还有东西没填
        local_win = proto == "local" and sys.platform == "win32"
        if local_win:
            self.row_cred.pack_forget()
        elif not self.row_cred.winfo_manager():
            self.row_cred.pack(fill="x", pady=(9, 0))
        self._rebuild_elevation()

    def _mode_changed(self):
        """选私钥就只露私钥相关两行，选登录密码就只露密码一行。

        以前两行一直摆着，私钥口令框空着也显示，看着像「还有东西没填」。
        """
        use_key = self.v_mode.get() == "key"
        # 先全部收起再按当前选择依次排，顺序不会错，也不用去猜 before/after
        for w in (self.row_key, self.row_keypw, self.row_pw):
            w.pack_forget()
        if use_key:
            self.row_key.pack(fill="x", pady=(7, 0))
            self.row_keypw.pack(fill="x", pady=(6, 0))
        else:
            self.row_pw.pack(fill="x", pady=(6, 0))
        self._refresh_cred_hint()

    # ---------------- 提权区（随平台与协议变形）
    def _is_admin(self) -> bool:
        """当前能不能跑管理员命令（Windows UAC / Unix root）。

        注意「能跑」不等于「本进程是管理员」：勾了「以管理员身份运行」之后，
        命令由提权助手代跑（core/elev.py），进程权限一点没变。界面上再说
        「当前是普通用户」就是在骗人，连接时还会重复弹 UAC。
        """
        if sys.platform == "win32":
            try:
                import ctypes
                if bool(ctypes.windll.shell32.IsUserAnAdmin()):
                    return True
            except Exception:  # noqa: BLE001
                pass
            return elev.current() is not None
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False

    def _admin_ready(self) -> bool:
        """已经能以管理员身份执行命令（含提权助手通道）。"""
        return self._is_admin()

    def _elevate_now(self, then=None) -> bool:
        """弹一次 UAC 拿到管理员通道，**不重启应用**。

        返回 False = 这次没拿到（用户点了否 / 被策略拦 / 助手起不来），
        `then` 不会被调用。成功时先刷新界面上「已具备管理员权限」那行、
        提示符，再执行 `then`（通常是「把刚才失败的那次操作重跑一遍」）。
        """
        if self._admin_ready():
            if then:
                then()
            return True
        self.status.set_state("正在请求管理员授权…", "warn")

        def work():
            return elev.helper().start()

        def ok(got):
            if got:
                self._apply_elev()
                self.status.set_state("已获得管理员权限", "ok")
                self._log_raw("ok", "[提权] 已通过 UAC 拿到管理员通道（未重启程序）")
                if then:
                    then()
                return
            msg = elev.helper().last_error or "没拿到管理员权限"
            self.status.set_state("需要管理员权限", "warn")
            self._log_raw("warn", f"[提权] {msg}")
            messagebox.showwarning("需要管理员权限", msg, parent=self)

        def failed(msg):
            self.status.set_state("需要管理员权限", "warn")
            messagebox.showerror("提权失败", msg, parent=self)

        self.bg(work, ok, failed, allow_elev=False)
        return True

    def _apply_elev(self) -> None:
        """把「已提权」落到当前会话与界面上。"""
        s = self.session
        if s is not None and hasattr(s, "set_helper"):
            try:
                s.set_helper(True)
            except Exception:  # noqa: BLE001
                pass
        try:
            self._rebuild_elevation()
        except Exception:  # noqa: BLE001
            pass

    def _rebuild_elevation(self) -> None:
        """Windows 本机没有 sudo，说的是 UAC；类 Unix 才是 sudo 密码。

        用户的原话：「Windows 不适用 sudo 密码，终端得有管理员权限运行的勾选；
        只有本机是 Linux 才有勾选并输入 sudo 密码，进去后自动执行 sudo -i」。
        """
        holder = getattr(self, "elev_holder", None)
        if holder is None:
            return
        for w in holder.winfo_children():
            w.destroy()
        self.e_sudopw = None
        proto = self.v_proto.get()
        local_win = proto == "local" and sys.platform == "win32"

        if local_win:
            row = U.wframe(holder, "card")
            row.pack(fill="x")
            admin = self._is_admin()
            U.Check(row, "以管理员身份运行（UAC）", self.v_admin,
                    command=self._admin_toggled).pack(side="left")
            U.wlabel(row, "已具备管理员权限" if admin else "当前是普通用户",
                     "ok" if admin else "dim", U.FONT_XS).pack(
                side="left", padx=(12, 0))
            # Windows 端还有 cmd / PowerShell 两种情况 —— 用下拉，别让人猜
            sh = U.wframe(holder, "card")
            sh.pack(fill="x", pady=(6, 0))
            U.wlabel(sh, "外壳", "card", U.FONT_S).pack(side="left")
            cb = ttk.Combobox(sh, textvariable=self.v_shell, state="readonly",
                              width=14, font=U.FONT_S,
                              values=["自动（认命令前缀）", "PowerShell", "cmd"])
            cb.pack(side="left", padx=(8, 0))
            _SHELL_UI = {"": "自动（认命令前缀）", "powershell": "PowerShell",
                         "cmd": "cmd"}
            _SHELL_KEY = {v: k for k, v in _SHELL_UI.items()}
            try:
                cb.set(_SHELL_UI.get(self.v_shell.get(), "自动（认命令前缀）"))
            except tk.TclError:
                pass

            def _shell_pick(_e=None, c=cb):
                try:
                    self.v_shell.set(_SHELL_KEY.get(c.get(), ""))
                except tk.TclError:
                    pass

            cb.bind("<<ComboboxSelected>>", _shell_pick)
            U.wlabel(holder,
                     "本机 Windows 没有 sudo：改路由 / 防火墙、终端里的提权命令，"
                     "都要整个程序以管理员身份运行。没勾选时遇到这类操作会提示你。",
                     "dim", U.FONT_XS, justify="left").pack(
                anchor="w", pady=(6, 0))
            self._refresh_cred_hint()
            return

        target = hosts_core.assume_os(
            {"proto": proto, "ostype": self.v_ostype.get()},
            sys.platform == "win32")
        if target == "windows":
            # 远程 Windows：SSH 进来也没有 sudo 这回事，别摆一个用不上的密码框
            row = U.wframe(holder, "card")
            row.pack(fill="x")
            U.wlabel(row, "目标系统是 Windows：没有 sudo，也不需要 sudo 密码",
                     "card", U.FONT_S).pack(side="left")
            U.wlabel(holder,
                     "提权靠管理员账户（runas / UAC）；「以管理员身份运行」只对"
                     "本机 Windows 有意义。",
                     "dim", U.FONT_XS, justify="left").pack(anchor="w", pady=(6, 0))
            self._refresh_cred_hint()
            return

        row = U.wframe(holder, "card")
        row.pack(fill="x")
        if target == "network":
            title = "连接后自动进特权模式（enable）"
            pw_label = "enable 密码"
            hint = ("交换机 / 路由器用 enable 密码，连上就执行 enable。"
                    "不勾选也能看，写操作时才会问你要密码。"
                    "密码留空则不提权。")
        elif proto == "local":
            title = "使用 sudo 提权（进入后自动 sudo -i）"
            pw_label = "sudo 密码"
            hint = ("本机的提权就是 sudo：勾选后终端与页面操作都以 root 执行；"
                    "不勾选也能用，需要 root 时会弹窗问你要密码。"
                    "密码留空则不提权。")
        else:
            title = "使用提权（登录后自动 sudo -i）"
            pw_label = "sudo 密码"
            hint = ("勾选后在连上时真的执行一次 sudo -i 进 root，"
                    "之后终端里敲的命令都是 root；不勾选也能用，"
                    "需要提权时会弹窗问你要密码。"
                    "sudo 密码留空则先试登录密码；sudo -i 被禁用时"
                    "自动退回「每条命令套 sudo」。")
        U.Check(row, title, self.v_elevate,
                command=self._elev_toggled).pack(side="left")
        U.wlabel(row, pw_label, "card", U.FONT_S).pack(side="left", padx=(14, 8))
        self.e_sudopw = ttk.Entry(row, textvariable=self.v_sudopw, show="*",
                                  width=18)
        self.e_sudopw.pack(side="left")
        U.RoundButton(row, "👁", lambda: self._toggle_eye(self.e_sudopw),
                      variant="ghost", font=U.FONT_S, height=24,
                      padx=7).pack(side="left", padx=(2, 0))
        U.Check(row, "保存口令", self.v_save_sudo).pack(side="left", padx=(8, 0))
        U.wlabel(holder, hint, "dim", U.FONT_XS, justify="left").pack(
            anchor="w", pady=(6, 0))
        self._elev_toggled()

    def _elev_toggled(self) -> None:
        on = bool(self.v_elevate.get())
        if self.e_sudopw is not None:
            try:
                self.e_sudopw.configure(state="normal" if on else "disabled")
            except tk.TclError:
                pass
        self._refresh_cred_hint()

    def _admin_toggled(self) -> None:
        """勾上「以管理员身份运行」就当场要一次 UAC —— **不重启程序**。

        以前这里问「要不要以管理员身份重新启动」，用户点「是」之后窗口
        直接消失（新进程在 `main()` 里抢不到单实例锁就退出了），表现就是
        「点了 UAC 通过，然后闪退」。现在改成拉一个管理员助手代跑命令，
        界面、终端、已经连着的会话全都留着。
        """
        if not self.v_admin.get() or self._admin_ready():
            return
        if messagebox.askyesno(
                "需要管理员权限",
                "点「是」现在弹出 UAC 授权，通过后即可用管理员权限执行命令，\n"
                "**程序不会重启**，当前窗口和已连接的会话都保留。\n\n"
                "（选「否」也行，真正需要管理员权限时再问你一次。）",
                parent=self):
            self._elevate_now()

    def _restart_as_admin(self) -> bool:
        """兜底：整个进程以管理员身份重启（提权助手起不来时才走）。

        两个坑都在这里：
          1. `ShellExecuteW` 返回时新进程已经在跑，而老进程还要活几百毫秒 ——
             新进程在 `main()` 里抢单实例锁会失败，然后 `return` 退出，
             用户看到的就是「点完 UAC 闪退」。所以**先放锁再拉起**。
          2. 新进程带 `--elev-restart`，`main()` 那边会等锁释放而不是一撞就退。
        """
        key = singleinstance.key_from_argv(sys.argv)
        try:
            import ctypes
            args = [a for a in sys.argv[1:] if a != "--elev-restart"]
            args.append("--elev-restart")
            if getattr(sys, "frozen", False):
                exe, params = sys.executable, subprocess.list2cmdline(args)
            else:
                exe = sys.executable
                params = subprocess.list2cmdline([str(Path(sys.argv[0]).resolve())] + args)
            singleinstance.release(key)      # 必须在新进程抢锁之前
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe, params, str(Path.cwd()), 1)
            if rc > 32:
                self.after(600, self.destroy)
                return True
            # 没起来：把锁抢回来，否则这台机器从此可以多开
            singleinstance.acquire(key)
            messagebox.showerror("提权失败", elev.shell_err(rc), parent=self)
        except Exception as e:  # noqa: BLE001
            singleinstance.acquire(key)
            messagebox.showerror("提权失败", str(e), parent=self)
        return False

    def _on_keypw_changed(self, *_):
        if self._keypw_job:
            try:
                self.after_cancel(self._keypw_job)
            except Exception:  # noqa: BLE001
                pass
        self._keypw_job = self.after(450, self._refresh_key_info)

    # ---------------- 凭据保存
    def _toggle_eye(self, entry) -> None:
        """口令框的显示/隐藏。输错了看不见是最常见的「存不上」误判来源。"""
        try:
            entry.configure(show="" if entry.cget("show") else "*")
        except tk.TclError:
            pass

    def _toggle_keypw_eye(self) -> None:
        self._toggle_eye(self.e_keypw)

    def _remember_changed(self, *_):
        self._refresh_cred_hint()

    def _remember_synced(self, *_):
        """三个分项开关汇总成 v_remember（老代码与档案格式还在用它）。"""
        try:
            any_save = bool(self.v_save_keypw.get() or self.v_save_pw.get()
                            or self.v_save_sudo.get())
            if bool(self.v_remember.get()) != any_save:
                self.v_remember.set(any_save)
        except tk.TclError:
            pass
        self._refresh_cred_hint()

    def _refresh_cred_hint(self) -> None:
        """只说清「勾了的项会在点保存时落盘」，不再代替用户做决定。"""
        lbl = getattr(self, "lbl_cred", None)
        if lbl is None:
            return
        try:
            keep = []
            proto = self.v_proto.get()
            if self.v_save_keypw.get() and self.v_mode.get() == "key":
                keep.append("私钥口令")
            if self.v_save_pw.get() and self.v_mode.get() == "password":
                keep.append("登录密码")
            if self.v_save_sudo.get():
                target = hosts_core.assume_os(
                    {"proto": proto, "ostype": self.v_ostype.get()},
                    sys.platform == "win32")
                keep.append("enable 密码" if target == "network" else "sudo 密码")
            if not keep:
                lbl.configure(text="未勾选「保存口令」· 口令只留在本次运行的内存里",
                              fg=U.T.text_mute)
                return
            lbl.configure(text="将加密保存：" + "、".join(keep), fg=U.T.ok)
        except tk.TclError:
            pass

    def _pick_key(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            title="选择 SSH 私钥", parent=self,
            filetypes=[("私钥文件", "id_* *.pem *.key *"), ("所有文件", "*.*")])
        if p:
            self.v_key.set(norm_path(p))
            self._refresh_key_info()

    def _key_menu(self):
        keys = discover_keys()
        if not keys:
            return messagebox.showinfo("没有发现私钥",
                                       "在 ~/.ssh 下没找到可用的私钥文件，请用「浏览…」手动选择。",
                                       parent=self)
        m = tk.Menu(self, tearoff=0)
        for k in keys[:18]:
            m.add_command(label=f"{k['name']}    ({k['dir']})",
                          command=lambda p=k["path"]: self._use_key(p))
        m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _use_key(self, path: str):
        self.v_key.set(norm_path(path))
        self._refresh_key_info()

    def _refresh_key_info(self):
        self._keypw_job = None
        path = norm_path(self.v_key.get())
        if not path:
            self.lbl_key.configure(text="未选择私钥", fg=U.T.text_mute)
            return
        self.lbl_key.configure(text="正在读取密钥信息…", fg=U.T.text_mute)
        pp = self.v_keypw.get() or None

        def done(info):
            if not info.get("ok"):
                if info.get("format") == "ppk":
                    self.lbl_key.configure(
                        text="⚠ PuTTY .ppk 无法直接读取 —— 用 PuTTYgen 导出 OpenSSH 私钥",
                        fg=U.T.err)
                elif info.get("encrypted") and not pp:
                    self.lbl_key.configure(text="🔑 " + info.get("error", ""),
                                           fg=U.T.warn)
                else:
                    self.lbl_key.configure(text="⚠ " + info.get("error", "无法读取"),
                                           fg=U.T.err)
                return
            txt = (f"{info['type']} {info['bits']} 位 · {info['fingerprint']}"
                   + ("  🔒 带口令" if info.get("encrypted") else ""))
            self.lbl_key.configure(text=txt, fg=U.T.text_dim)

        self.bg(lambda: inspect_key(path, pp), ok=done,
                err=lambda m: self.lbl_key.configure(text="⚠ " + m, fg=U.T.err))

    # ---------------- 档案
    def _collect_profile(self) -> dict:
        p = {
            "name": self.v_prof.get().strip(),
            "host": self.v_host.get().strip(),
            "port": self.v_port.get().strip() or "22",
            "user": self.v_user.get().strip(),
            "auth": self.v_mode.get(),
            "key_path": norm_path(self.v_key.get()),
            "remember": bool(self.v_remember.get()),
            "proto": self.v_proto.get(),
            "ostype": str(self.v_ostype.get() or ""),
        }
        # 提权那两个开关按**目标系统**存：Linux 有 sudo、交换机有 enable、
        # Windows 两者都没有（它要的是 UAC）。不这么分的话，一条远程档案
        # 拿到本机 Windows 上打开就会冒出一个用不上的 sudo 密码框。
        target = hosts_core.assume_os(p, sys.platform == "win32")
        p["sudo"] = (bool(self.v_elevate.get())
                     if (hosts_core.is_unix(target) or target == "network")
                     else False)
        p["elevate"] = bool(self.v_admin.get()) if target == "windows" else False
        p["shell"] = str(self.v_shell.get() or "") if p["proto"] == "local" else ""
        # 每一项单独决定存不存：勾了私钥口令就只存私钥口令，
        # 别把一个没勾的口令也顺手写进文件。
        if self.v_save_pw.get():
            p["password"] = self.v_pw.get()
        if self.v_save_keypw.get():
            p["key_passphrase"] = self.v_keypw.get()
        if self.v_save_sudo.get():
            p["sudo_password"] = self.v_sudopw.get()
        return p

    def _refresh_profile_list(self):
        names = self.profiles.names()
        self.cb_prof["values"] = names
        if self.v_prof.get() not in names:
            self.v_prof.set("")

    def _apply_profile(self, p: dict):
        self.v_prof.set(p.get("name", ""))
        self.v_host.set(p.get("host", ""))
        self.v_port.set(p.get("port", "22") or "22")
        self.v_user.set(p.get("user", ""))
        auth = p.get("auth", "key")
        self.v_mode.set(auth if auth in ("key", "password") else "key")
        self._mode_changed()
        self.v_ostype.set(hosts_core.os_of(p))
        self._sync_ostype_ui()
        self.v_key.set(norm_path(p.get("key_path")))
        self.v_keypw.set(p.get("key_passphrase", "") or "")
        self.v_pw.set(p.get("password", "") or "")
        self.v_sudopw.set(p.get("sudo_password", "") or "")
        # 本机档案里的两套提权（Linux sudo / Windows UAC）与外壳
        self.v_elevate.set(bool(p.get("sudo") or p.get("sudo_password")))
        self.v_admin.set(bool(p.get("elevate")))
        self.v_shell.set(str(p.get("shell") or ""))
        # 档案里真存了哪几项，就把对应开关打开，界面上如实反映
        self.v_save_keypw.set(bool(p.get("key_passphrase")))
        self.v_save_pw.set(bool(p.get("password")))
        self.v_save_sudo.set(bool(p.get("sudo_password")))
        self._remember_synced()
        self._mode_changed()
        if self.v_key.get():
            self._refresh_key_info()
        else:
            self.lbl_key.configure(text="未选择私钥", fg=U.T.text_mute)
        self._refresh_cred_hint()

    def _on_profile_pick(self, _e=None):
        p = self.profiles.get(self.v_prof.get())
        if p:
            self._apply_profile(p)

    def _profile_save(self, quiet: bool = False) -> bool:
        name = self.v_prof.get().strip()
        if not name:
            if quiet:
                return False
            from tkinter import simpledialog
            name = simpledialog.askstring("保存档案", "档案名：", parent=self) or ""
            if not name:
                return False
            self.v_prof.set(name)
        rec = self._collect_profile()
        try:
            self.profiles.upsert(rec)
        except ValueError as e:
            if quiet:
                return False
            messagebox.showwarning("无法保存", str(e), parent=self)
            return False
        self._refresh_profile_list()
        self._refresh_cred_hint()
        if rec.get("remember"):
            kept = [n for n, k in (("私钥口令", "key_passphrase"),
                                   ("登录密码", "password"),
                                   ("sudo 密码", "sudo_password"))
                    if rec.get(k)]
            tail = f" · 已加密保存 {'、'.join(kept)}" if kept else " · 未填口令"
            self.status.set_state(f"档案已保存：{name}{tail}", "ok")
        else:
            self.status.set_state(
                f"档案已保存：{name} · 未勾「记住凭据」，口令没有落盘", "warn")
        return True

    def _autosave_creds(self) -> None:
        """连接成功后静默落盘凭据。

        档案名为空时用 `user@host` 造一个 —— 用户勾了「记住凭据」，
        意思就是「下次别再问我」，不该因为没有档案名而失败。
        """
        name = self.v_prof.get().strip()
        if not name:
            host = self.v_host.get().strip()
            user = self.v_user.get().strip()
            if not host:
                return
            name = f"{user}@{host}" if user else host
            self.v_prof.set(name)
            try:
                self.cb_prof["values"] = self.profiles.names() + [name]
            except tk.TclError:
                pass
        try:
            self.profiles.upsert(self._collect_profile())
        except ValueError:
            return
        self._refresh_profile_list()
        self.v_prof.set(name)
        kept = [n for n, k in (("私钥口令", "key_passphrase"),
                               ("登录密码", "password"),
                               ("sudo 密码", "sudo_password"))
                if self._collect_profile().get(k)]
        if kept:
            self.toast(f"凭据已加密保存到档案「{name}」：{'、'.join(kept)}", "ok")

    def _profile_delete(self):
        name = self.v_prof.get().strip()
        if not name:
            return
        if not messagebox.askyesno("删除档案", f"删除「{name}」？", parent=self):
            return
        self.profiles.remove(name)
        self._refresh_profile_list()
        self.status.set_state("档案已删除")

    def _profile_import(self):
        added, skipped = self.profiles.import_from_ssh_config()
        self._refresh_profile_list()
        messagebox.showinfo("导入完成",
                            f"新增 {added} 个，跳过 {skipped} 个（同名已存在）。",
                            parent=self)

    def _load_last_profile(self):
        try:
            names = self.profiles.names()
            self.cb_prof["values"] = names
            last = self.profiles.last
            if last and last in names:
                self.v_prof.set(last)
                self._apply_profile(self.profiles.get(last))
            elif names:
                self.v_prof.set(names[0])
                self._apply_profile(self.profiles.get(names[0]))
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 连接动作
    def do_connect(self):
        proto = self.v_proto.get()
        self.btn_connect.set_enabled(False)
        self.status.set_state("正在连接…")

        is_local = proto == "local"
        local_win = is_local and sys.platform == "win32"
        # 本机 Windows 走 UAC（提权助手代跑），类 Unix 与远程走 sudo / enable
        elevate = bool(self.v_elevate.get()) or (local_win and self.v_admin.get())
        if local_win and self.v_admin.get() and not self._admin_ready():
            self.btn_connect.set_enabled(True)
            if not messagebox.askyesno(
                    "需要管理员权限",
                    "勾了「以管理员身份运行」。点「是」会弹一次 UAC 授权，\n"
                    "通过后自动继续连接，**程序不会重启**。",
                    parent=self):
                return
            # 授权是异步的（UAC 可能要等十几秒），拿到后自己再进一次本函数
            self._elevate_now(then=self.do_connect)
            return

        spec = ConnSpec(proto=proto,
                        host="" if is_local else self.v_host.get().strip(),
                        port=0 if is_local else int(
                            self.v_port.get() or (23 if proto == "telnet" else 22)),
                        user=self.v_user.get().strip(),
                        auth=self.v_mode.get(),
                        key_path=norm_path(self.v_key.get()),
                        password=self.v_pw.get(),
                        key_passphrase=self.v_keypw.get(),
                        # 本机 Windows 的执行外壳（cmd / powershell / 自动）
                        shell=(self.v_shell.get() if is_local else ""),
                        # 密码**总是**带上：勾了自动提权就用它进 root；没勾也留着，
                        # 页面上的按需操作（Docker / 防火墙 / 路由）要用时才有得用。
                        # 以前只在 elevate 时才传，于是「密码填了、框没勾」的用户
                        # 一点 Docker 就报「无权访问 / 未安装」，白填一次密码。
                        sudo_password=self.v_sudopw.get().strip(),
                        elevate=elevate)

        if proto == "ssh":
            if spec.auth == "key":
                if not spec.key_path:
                    self.btn_connect.set_enabled(True)
                    return messagebox.showwarning("缺少参数", "请选择私钥文件", parent=self)
                info = inspect_key(spec.key_path, spec.key_passphrase or None)
                if not info.get("ok"):
                    self.btn_connect.set_enabled(True)
                    if info.get("format") == "ppk":
                        return messagebox.showerror(
                            "私钥格式不支持",
                            "PuTTY 的 .ppk 无法直接读取。\n"
                            "用 PuTTYgen → Conversions → Export OpenSSH key 导出后再选。",
                            parent=self)
                    if info.get("encrypted") and not spec.key_passphrase:
                        return messagebox.showwarning("缺少私钥口令",
                                                      "这把私钥带口令保护，请先填「私钥口令」。",
                                                      parent=self)
                    return messagebox.showerror("私钥不可用",
                                                info.get("error", "无法读取"), parent=self)
            elif not spec.password:
                self.btn_connect.set_enabled(True)
                return messagebox.showwarning("缺少参数", "请填登录密码", parent=self)

        def work():
            old = self.session
            if old:
                try:
                    old.close()
                except Exception:  # noqa: BLE001
                    pass
            s = open_session(spec)
            s.tracer = self._trace_cmd
            # 先探测出「对方到底是什么」，再决定提权怎么提：
            # 交换机 / 路由器是 enable，Linux / Unix 是 sudo —— 同一个密码框。
            dev = detect(s)
            try:
                s.bind_device(dev)
            except Exception:  # noqa: BLE001
                pass
            # 没勾「自动提权」也把 sudo 密码挂到会话上：页面上的按需操作
            # （Docker、防火墙、路由表）本来就要 root，有这个密码就能直接过，
            # 不用每次弹窗问。注意**只设密码、不设 elevated** —— 提权与否
            # 是另一件事，终端该不该整条走 root 由 elevate_root 决定。
            if not spec.elevate and spec.sudo_password and \
                    getattr(dev, "os_family", "") != "network":
                try:
                    s.sudo_pw = spec.sudo_password
                except Exception:  # noqa: BLE001
                    pass
            elev = ""
            elev_why = ""
            used_login_pw = False
            if spec.elevate:
                # 提权密码留空时用登录密码兜底 —— 服务器上两者绝大多数是同一个。
                # 不兜底的话，用户勾了框、填了登录密码，就被判成「缺密码、没提权」，
                # 界面上只留一句警告，很容易被当成「已经提权了但没生效」。
                # 交换机不兜底：enable 密码和登录密码是两回事，试错还会锁账号。
                pw = (spec.sudo_password or "").strip()
                if not pw and spec.password and \
                        getattr(dev, "os_family", "") != "network":
                    pw = spec.password
                    used_login_pw = True
                okadm = False
                if pw:
                    try:
                        # 真的进去：类 Unix 开一条持久 shell 执行 sudo -i，
                        # 交换机 / 路由器走 enable。不是只验一下密码就算数 ——
                        # 光验密码的话终端里手敲的命令仍然不带提权。
                        okadm = bool(s.elevate_root(pw))
                    except Exception:  # noqa: BLE001
                        okadm = False
                # 提权那步返回 True 还不算完：终端里真正跑的通道是另一条
                # （root shell / 逐条 sudo），通不通得实测。这一步把
                # 「提示符已经是 # 了、敲命令却还是普通用户」彻底堵死。
                if okadm:
                    try:
                        okadm, detail = s.verify_root()
                    except Exception as e:  # noqa: BLE001
                        okadm, detail = False, str(e)
                    if okadm:
                        elev = {"sudo-i": "sudo -i", "percmd": "逐条 sudo",
                                "enable": "enable", "root": "root 登录"
                                }.get(getattr(s, "elev_method", ""), "root 权限")
                        if used_login_pw:
                            elev += "（用的是登录密码）"
                    else:
                        elev, elev_why = "failed", detail
                else:
                    elev = "failed"
                    elev_why = (getattr(s, "elev_msg", "") or "").strip()
                if not pw:
                    elev = "nopw"
            return s, dev, elev, elev_why

        def ok(res):
            s, dev, elev, elev_why = res
            # 多设备：连上就加一个槽位，顶部标签栏多一个；同一台重连则替换
            slot = self.sessions.add(s, dev)
            slot.elev = elev
            self._rebuild_devtabs()
            self.btn_connect.set_enabled(True)
            self._render_device(dev, s)
            self.status.set_device(dev.hostname or dev.os_name or s.label, dev.os_family)
            self.status.set_state("已连接", "ok")
            self.pill_dev.set(dev.os_name or "已连接", "ok")
            self.pill_proto.set(s.kind.upper(), "ok")
            # 探测到的才是真的：把目标系统写回档案，下次表单 / 提权区就跟着准
            self._remember_detected_os(dev)
            # 换了设备：页面上的旧数据全部作废，重新按新设备探测
            self._local_dev = None
            self._routes_loaded = False
            self._fw_probed = False
            self._dk_probed = False
            self._web_probed = False
            self.fw_backend = None
            self._log_raw("meta", f"[设备] {dev.os_name or dev.os_family} · "
                                  f"{dev.kernel or ''} · {dev.arch or ''}\n")
            if dev.os_family == "network":
                self._log_raw("meta", f"[厂商] {dev.vendor or '未知'} · "
                                      f"{dev.device_type} · 命令按厂商 CLI 生成\n")
            if elev == "failed":
                # 原因原文照抄：密码错、不在 sudoers、sudo -i 被关掉……
                # 只写「提权未生效」等于让用户去猜。
                self._log_raw("bad", f"[提权] 没成功：{elev_why or '原因不明'}\n")
                self.status.set_state(
                    f"已连接（提权未生效：{elev_why[:40]}）" if elev_why
                    else "已连接（提权未生效）", "warn")
            elif elev == "nopw":
                self._log_raw("warn", "[提权] 勾了「连接后自动提权」但既没填 "
                                      "sudo / enable 密码，也没有登录密码可用，"
                                      "这次没有提权\n")
                self.status.set_state("已连接（未提权：缺密码）", "warn")
            elif elev:
                self._log_raw("ok", f"[提权] 已生效（{elev}），并已实测 "
                                     f"`id -u` = 0 —— 终端里敲的命令与页面操作"
                                     f"都以 root / 特权身份执行\n")
                self.status.set_state(f"已连接（root · {elev}）", "ok")
            # 勾了「记住凭据」就顺手把档案落盘 —— 口令要真的进文件，
            # 不能指望用户连通之后还想起来点一次「保存」。
            if self.v_remember.get() and self.v_prof.get().strip():
                self._autosave_creds()
            self._refresh_current_page()
            # 终端已经开着的话，把新会话的提权状态补一行进去（否则还显示旧提示符）
            self._term_elev_note()

        def err(msg):
            self.btn_connect.set_enabled(True)
            self.status.set_state(msg, "err")
            messagebox.showerror("连接失败", msg, parent=self)

        self.bg(work, ok=ok, err=err)

    def _remember_detected_os(self, dev) -> None:
        """把探测到的目标系统写回档案。

        光靠本机平台猜提权方式是不对的：SSH 打到一台 Windows 上，
        那边根本没有 sudo。连上认准之后把系统写进档案，
        下次打开表单、下次连接，提权区就是对的。

        本机连接一律不动：这台机器的系统不是「目标系统」，写进下拉里会让
        下次切回 SSH 时目标还停在 Windows —— 于是 sudo 密码框整个不出现。
        """
        if self.v_proto.get() == "local":
            return
        fam = str(getattr(dev, "os_family", "") or "").strip().lower()
        # 认准了是什么系统就记什么 —— 以前 darwin / freebsd 一律写成 linux，
        # 于是 macOS 和 FreeBSD 的机器在档案里都变成「Linux」，用户改不动也看不出来。
        if fam in ("windows", "network"):
            key = fam
        elif fam in ("darwin", "macos", "mac", "osx"):
            key = "macos"
        elif fam in ("freebsd", "openbsd", "netbsd", "bsd"):
            key = "freebsd"
        elif fam in ("linux", "posix"):
            key = "linux"
        else:
            return
        try:
            if str(self.v_ostype.get()) != key:
                self.v_ostype.set(key)
                self._sync_ostype_ui()
                self._rebuild_elevation()
        except (AttributeError, tk.TclError):
            pass
        name = str(getattr(dev, "os_name", "") or "")
        pname = self.v_prof.get().strip()
        if not pname:
            return
        rec = self.profiles.get(pname)
        if rec is None:
            return
        # 连的地址跟档案里记的不是同一台，也别写 —— 手动改过地址时尤其容易错。
        host = str(self.v_host.get() or "").strip().lower()
        if host and str(rec.get("host") or "").strip().lower() != host:
            return
        if rec.get("ostype") == key and str(rec.get("os_name") or "") == name:
            return
        rec = dict(rec)
        rec["ostype"] = key
        if name:
            rec["os_name"] = name
        try:
            self.profiles.upsert(rec)
        except (ValueError, OSError):
            return
        try:
            self.host_refresh()
        except Exception:  # noqa: BLE001  页面还没建好也不该影响连接
            pass

    def _disconnect(self):
        """只断当前这一台；关掉后自动切到剩下的上一台（没有就回本机）。"""
        sid = self.sessions.active_id
        if sid:
            self.sessions.close(sid)
        else:
            s = self.sessions.session
            if s is not None:
                try:
                    s.close()
                except Exception:  # noqa: BLE001
                    pass
        self._local_device = DeviceInfo()
        self._after_switch()

    # ============================================================ 终端（页签 / 独立窗口）
    TERM_TAB = "term"

    def _term_live(self):
        """当前活着的终端：页签优先，其次是拉出去的独立窗口。

        同一个会话只该有一个终端视图 —— 两处同时显示会让「命令打到哪」变糊。
        """
        pane = self._term_panes.get(self.TERM_TAB)
        if pane is not None:
            try:
                if pane.winfo_exists():
                    return pane
            except tk.TclError:
                pass
            self._term_panes.pop(self.TERM_TAB, None)
        w = self._term
        if w is not None:
            try:
                if w.winfo_exists():
                    return w
            except tk.TclError:
                self._term = None
        return None

    def _term_label(self) -> str:
        try:
            d = self.term_describe()
        except Exception:  # noqa: BLE001
            d = ""
        return f"终端 · {d}" if d else "终端"

    def _term_add_tab(self):
        """在工作区里新建一个终端页签，返回它的 TerminalPane。"""
        holder = U.wframe(self.workspace, "page")
        holder.grid(row=0, column=0, sticky="nsew")
        pane = TerminalPane(holder, self)
        pane.pack(fill="both", expand=True)
        self._term_panes[self.TERM_TAB] = pane
        self.tabbar.add(self.TERM_TAB, self._term_label(), closable=True,
                        on_popout=self.term_popout)
        self.tabbar.select(self.TERM_TAB)
        return pane

    def open_terminal(self):
        """打开终端。默认嵌成页签 —— 需要双屏时点页签上的 ⧉ 拉出去。"""
        if self._term_live() is not None:
            if self.TERM_TAB in self._term_panes:
                self.tabbar.select(self.TERM_TAB)
            else:
                self.term_embed(self._term)      # 独立窗口 → 收回页签
            self.status.set_state("终端已打开 · 命令跑在当前会话上", "ok")
            return
        self._term_add_tab()
        self.status.set_state("终端已打开 · 命令跑在当前会话上", "ok")

    def _term_elev_note(self) -> None:
        """连上 / 重连之后，让已经开着的终端立刻反映新的提权状态。

        终端是「每次执行时重画提示符」的，所以重连之后老的 `$` 提示符会一直
        挂在屏幕上，直到用户敲下一条命令才更新 —— 这中间的空档正好是最容易
        被当成「没提权」的时候。这里主动重画一次。
        用户正在输入时不打断（会把人家敲的字一起换掉）。
        """
        pane = self._term_live()
        if pane is None:
            return
        try:
            if getattr(pane, "mode", "") == "inline":
                typed = pane.text.get("input_start", "end-1c")
                if typed.strip():
                    return
            pane._refresh_head()
        except Exception:  # noqa: BLE001
            pass

    def term_popout(self) -> None:
        """页签 → 独立窗口。正文与命令历史一起搬过去，不丢上下文。"""
        pane = self._term_panes.pop(self.TERM_TAB, None)
        text, hist = "", []
        if pane is not None:
            try:
                text = pane.text.get("1.0", "end-1c")
                hist = list(pane._hist)
            except (tk.TclError, AttributeError):
                pass
            holder = pane.master
            try:
                pane.destroy()
                holder.destroy()
            except tk.TclError:
                pass
        self.tabbar.remove(self.TERM_TAB)
        w = TerminalWindow(self, self, title=f"终端 · {self.term_describe()}")
        self._term = w
        if text.strip():
            w.write(text.rstrip("\n") + "\n", "out")
        if hist:
            w._hist = hist
            w._hist_at = len(hist)
        w.lift()
        w.focus_force()
        self.status.set_state("终端已拉出为独立窗口 · 想收回点窗口里的「嵌回」", "ok")

    def term_embed(self, w=None) -> None:
        """独立窗口 → 页签。"""
        w = w or self._term
        text, hist = "", []
        if w is not None:
            try:
                text = w.text.get("1.0", "end-1c")
                hist = list(w._hist)
            except (tk.TclError, AttributeError):
                pass
        self._term = None
        if w is not None:
            try:
                w.destroy()
            except tk.TclError:
                pass
        if self.TERM_TAB in self.tabbar.tabs:
            self.tabbar.remove(self.TERM_TAB)
        pane = self._term_add_tab()
        if text.strip():
            pane.write(text.rstrip("\n") + "\n", "out")
        if hist:
            pane._hist = hist
            pane._hist_at = len(hist)
        self.status.set_state("终端已嵌回主界面", "ok")

    def term_closed(self, w) -> None:
        """独立窗口被关掉（点 × 或调 close）。"""
        if self._term is w:
            self._term = None

    def _term_refresh_head(self) -> None:
        t = self._term_live()
        if t is None:
            return
        try:
            t._refresh_head()
        except (tk.TclError, AttributeError):
            pass
        if self.TERM_TAB in self.tabbar.tabs:
            self.tabbar.set_label(self.TERM_TAB, self._term_label())

    def _term_feed(self, display, rc, out, err, ms) -> None:
        """tracer 的旁路：终端开着就把同一条命令推过去。

        助手执行的命令不走这条路 —— 它自己会写一份更完整的（带主机名、
        执行前就写、输出与 rc 分明），再喂一遍就成了双份。
        """
        if self._ai_owns_cmd(display):
            return
        t = self._term_live()
        if t is None:
            return
        # 智能体经 API 发的命令带 [API] 前缀，单独受「同步 API」开关管
        by_api = str(display or "").startswith(API_CMD_TAG)
        try:
            t.feed(display, rc, out, err, ms, by_api=by_api)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ AI 活动流
    # 用户的要求：AI 执行的命令要实时出现在终端里，连调模型的输入输出也要看得见。
    # 所以这里给助手开了一条专用通道，写进去的行比「主程序命令」更详细：
    #   AI ⇢ 请求 ／ AI ⇠ 响应 ／ AI ▶ 命令 ／ 输出 ／ AI ■ rc
    def _ai_owns_cmd(self, display: str) -> bool:
        """这条命令是不是助手刚发出去的（助手自己已经写过，别重复）。"""
        key = (display or "").strip()
        if not key:
            return False
        at = self._ai_live_cmds.get(key)
        if at is None:
            return False
        # 同一秒里清理一次，避免这张表越攒越大
        now = time.monotonic()
        for k, v in list(self._ai_live_cmds.items()):
            if now - v > 120:
                self._ai_live_cmds.pop(k, None)
        return key in self._ai_live_cmds

    def _ai_mark_cmd(self, cmd: str) -> None:
        key = (cmd or "").strip()
        if key:
            self._ai_live_cmds[key] = time.monotonic()

    def _ai_open_term(self) -> None:
        """助手要用终端时把它叫出来，并切到那个页签。

        不这么做的话，AI 在后台跑完一串命令，终端里什么都不留 ——
        用户的原话就是「AI 执行的命令会实时显示在终端」。只在还没终端时
        才自动开，用户自己关掉的不会被反复弹出（那是下一次点了才开）。
        """
        if self._term_live() is None:
            try:
                self.open_terminal()
            except Exception:  # noqa: BLE001
                return
        try:
            if self.TERM_TAB in self._term_panes:
                self.tabbar.select(self.TERM_TAB)
        except Exception:  # noqa: BLE001
            pass

    def ai_trace(self, lines, ensure: bool = False) -> None:
        """把核心层拼好的 (文本, 标签) 行写进终端。线程安全。

        ensure=True 表示这行很重要（比如 AI 要开始执行命令了），
        终端没开就先开一个 —— 其余装饰性日志不主动弹窗。
        """
        rows = list(lines or [])
        if not rows:
            return

        def go():
            try:
                if ensure:
                    self._ai_open_term()
                t = self._term_live()
                if t is None:
                    return
                t.ai_feed(rows)
            except tk.TclError:
                pass

        try:
            if threading.current_thread() is threading.main_thread():
                go()
            else:
                self.post(go)
        except Exception:  # noqa: BLE001
            pass

    def _ai_api_trace(self, kind: str, info: dict) -> None:
        """core.aiops 的引擎回调：模型请求 / 响应各写一段。"""
        try:
            if kind == "req":
                self.ai_trace(aitrace.api_request(
                    base=info.get("base", ""), model=info.get("model", ""),
                    messages=info.get("messages"), provider=info.get("provider", "")))
            elif kind == "resp":
                self.ai_trace(aitrace.api_response(
                    ok=bool(info.get("ok")), ms=info.get("ms"),
                    code=info.get("code", ""), tokens=info.get("tokens"),
                    text=info.get("text", ""), err=info.get("err", "")))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 页签
    def _ws_raise(self, widget) -> None:
        """把一块工作区显出来，并把其它几块**从布局里摘掉**。

        以前只做 tkraise：谁在上面全靠 z 序。z 序有两个坑 ——
          · 被 raise 的那块如果此刻不在布局里（grid_remove 过），
            tkraise 什么也不做，于是「点了页签画面不动」；
          · 新建的容器天然在最上层，和老容器交错 raise 之后，
            谁在上面取决于操作顺序，偶尔会出现「页签是设置、画面是主机资源」。
        改成显式的「留一块、摘其余」：结论只由这一行决定，不看历史。
        摘掉不等于销毁 —— 状态、滚动位置、输入内容都还在。
        """
        try:
            # 必须用 winfo_children() 而不是 grid_slaves()：被 grid_remove 掉的
            # 那块不在 slaves 名单里，用它找目标会「找不到要显示的那块」，
            # 结果把所有块都摘了、一个都没放回来（白屏）。
            for w in self.workspace.winfo_children():
                if w is widget:
                    if w.winfo_manager() != "grid":
                        w.grid()
                elif w.winfo_manager() == "grid":
                    w.grid_remove()
            widget.tkraise()
        except tk.TclError:
            return
        self._ws_top = widget

    def _on_tab(self, key: str) -> None:
        if key == "hosts":
            self._ws_raise(self.ws_hosts)
            # 从终端 / 设置那些页签切回来时，运维工具条要按当前页重新收放
            try:
                self._sync_devstrip(getattr(self.nav, "current", "") or "hosts")
            except (AttributeError, tk.TclError):
                pass
            return
        pane = self._tab_pane(key)
        if pane is not None:
            try:
                self._ws_raise(pane.master)
            except tk.TclError:
                pass

    def _tab_pane(self, key: str):
        """按页签键找它对应的面板。终端和设置各有一张登记表。"""
        pane = self._term_panes.get(key)
        if pane is not None:
            return pane
        return getattr(self, "_settings_panes", {}).get(key)

    def _tab_new(self) -> None:
        """页签栏的「+」：开一个终端。"""
        self.open_terminal()

    def _tab_close(self, key: str) -> None:
        """页签关闭回调。返回 False 表示拒绝关闭。"""
        # 两张登记表都查一遍：同一个键只会落在其中一张里。
        for reg in (self._term_panes, getattr(self, "_settings_panes", {})):
            pane = reg.pop(key, None)
            if pane is None:
                continue
            holder = pane.master
            try:
                pane.destroy()
                holder.destroy()
            except tk.TclError:
                pass
            break
        return None

    # ---- 终端要的四个接口
    def term_ready(self) -> bool:
        s = self.session
        try:
            return s is not None and bool(s.alive)
        except Exception:  # noqa: BLE001
            return s is not None

    def term_describe(self) -> str:
        s = self.session
        if s is None:
            return "未连接"
        dev = self._effective_device()
        name = dev.hostname or dev.os_name or s.label or "设备"
        desc = f"{name} · {s.kind.upper()}"
        if bool(getattr(s, "is_root", False)) or bool(getattr(s, "elevated", False)):
            desc += " · root"
        return desc

    def term_cwd(self) -> str:
        return self._term_cwd_val or self._term_probe_cwd()

    def _term_probe_cwd(self) -> str:
        s = self.session
        win = self._effective_device().os_family == "windows"
        fallback = "C:\\" if win else "/"
        if s is None:
            return fallback
        try:
            rc, out, err = s.run("pwd", timeout=8)
            lines = [x.strip() for x in (out or "").splitlines() if x.strip()]
            got = lines[-1] if lines else ""
        except Exception:  # noqa: BLE001
            got = ""
        self._term_cwd_val = got or fallback
        return self._term_cwd_val

    def term_prompt(self) -> str:
        """终端提示符。**必须反映真实身份**，否则用户没法判断提权到底成没成。

        以前只用 `is_root`（= 登录用户本身就是 root），而 `sudo -i` 之后
        我们刻意不动 `is_root`（exec 通道还是原用户，run_admin 仍要套 sudo）。
        结果是：明明已经提权、命令确实是 root 跑的，提示符却还顶着 `$`，
        用户一看就断定「没提权成功」—— 这条提示符本身就是错的。
        现在按 `is_root or elevated` 判，提权后显示 `root@host …#`。
        """
        s = self.session
        if s is None:
            return "$ "
        cwd = self.term_cwd()
        if s.kind == "local":
            return f"{cwd}> " if self._effective_device().os_family == "windows" \
                else f"{cwd}$ "
        user = getattr(s, "user", "") or "user"
        host = getattr(s, "host", "") or self._effective_device().hostname or "host"
        rooted = bool(getattr(s, "is_root", False)) or \
            bool(getattr(s, "elevated", False))
        mark = "#" if rooted else "$"
        # 提权后命令确实以 root 身份跑，就照实写 root@host；
        # 藏着原用户名会让人以为 sudo 没生效。
        who = "root" if rooted else user
        return f"{who}@{host} {cwd}{mark} "

    def term_chdir(self, path: str) -> tuple[bool, str]:
        s = self.session
        if s is None:
            return False, "未连接"
        win = self._effective_device().os_family == "windows"
        if win:
            cmd = f"Set-Location -LiteralPath '{path}' -ErrorAction Stop; pwd"
        else:
            cmd = f"cd -- {shlex.quote(path)} 2>/dev/null && pwd"
        try:
            rc, out, err = s.run(cmd, timeout=10)
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        lines = [x.strip() for x in (out or "").splitlines() if x.strip()]
        if rc != 0 or not lines:
            return False, (err or "目录不存在").strip().splitlines()[-1]
        self._term_cwd_val = lines[-1]
        return True, self._term_cwd_val

    def term_run(self, cmd: str, done) -> None:
        """在后台跑，结果回投到 Tk 线程。拼上当前目录，但终端只回显用户敲的那行。"""
        s = self.session
        if s is None:
            done(1, "", "未连接", 0)
            return
        cwd = self.term_cwd()
        win = self._effective_device().os_family == "windows"
        if win:
            full = f"Set-Location -LiteralPath '{cwd}' -ErrorAction SilentlyContinue; {cmd}"
        else:
            full = f"cd -- {shlex.quote(cwd)} 2>/dev/null; {cmd}"

        def work():
            t0 = time.time()
            try:
                # 提权生效了就走提权通道（root shell / sudo / 设备 enable），
                # 否则与以前一样普通执行。见 core.session.term_exec。
                rc, out, err = term_exec(s, full, timeout=180)
            except Exception as e:  # noqa: BLE001
                rc, out, err = 1, "", str(e)
            done(rc, out, err, int((time.time() - t0) * 1000))

        threading.Thread(target=work, daemon=True).start()

    def _render_device(self, dev: DeviceInfo, s):
        for w in self.kv_holder.winfo_children():
            w.destroy()
        self._kv("连接", f"{s.kind.upper()} · {s.label}", "ok")
        self._kv("操作系统", dev.os_name)
        self._kv("内核", dev.kernel)
        self._kv("架构", dev.arch)
        self._kv("主机名", dev.hostname)
        self._kv("设备类型", dev.device_type)
        self._kv("Shell", dev.shell)
        self._kv("当前用户", dev.user + ("（root）" if dev.is_root else ""))
        caps = capabilities(dev)
        if caps:
            self._kv("可用能力", " · ".join(caps), "ok")

    # ============================================================ 网络工具
    def _build_tools(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        c = U.Card(pg, title="目标", hint="留空则对本机执行")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        self.v_target = tk.StringVar()
        row = U.wframe(b, "card")
        row.pack(fill="x")
        U.wlabel(row, "主机 / IP", "card", U.FONT_S).pack(side="left")
        ttk.Entry(row, textvariable=self.v_target, width=30).pack(
            side="left", padx=(10, 0))
        self.lbl_tool_os = U.wlabel(row, "未连接 · 将使用本机命令", "dim", U.FONT_XS)
        self.lbl_tool_os.pack(side="right")

        grid = U.wframe(b, "card")
        grid.pack(fill="x", pady=(U.px(10), 0))
        tools = [
            ("Ping 测试", "ping"), ("路由追踪", "trace"),
            ("IP 地址", "ip"), ("路由表", "route"),
            ("监听端口", "ports"), ("DNS 配置", "dns"),
        ]
        for i, (label, key) in enumerate(tools):
            U.RoundButton(grid, label, lambda k=key: self.do_tool(k),
                          variant="secondary", font=U.FONT_S,
                          height=30, padx=14).grid(
                row=0, column=i, padx=(0, 8), pady=2, sticky="w")

        c3 = U.Card(pg, title="输出", hint="表格是解析后的视图，原始回显随时可切回")
        c3.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        head = U.wframe(c3.body, "card")
        head.pack(fill="x")
        self.seg_tool = Segmented(head, [("table", "表格"), ("raw", "原始输出")],
                                  command=self._tool_view)
        self.seg_tool.pack(side="left")
        self.lbl_ping = U.wlabel(head, "", "dim", U.FONT_XS, justify="right",
                                 wraplength=U.px(420))
        self.lbl_ping.pack(side="right", padx=(U.px(10), 0))

        holder = U.wframe(c3.body, "card")
        holder.pack(fill="both", expand=True, pady=(U.px(8), 0))
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        self.tbl_tool = DataTable(
            holder, TOOL_COLS["ip"], height=9, searchable=True,
            empty_title="还没有执行工具",
            empty_hint="上面选一个工具跑一下，结果会以表格显示。")
        self.tbl_tool.grid(row=0, column=0, sticky="nsew")

        self.raw_tool = tk.Frame(holder, bg=U.T.card, bd=0)
        self.raw_tool.grid(row=0, column=0, sticky="nsew")
        self.txt_tool = tk.Text(self.raw_tool, bg=U.T.console_bg, fg=U.T.console_fg,
                                font=U.MONO_S, bd=0, relief="flat",
                                highlightthickness=0, wrap="none",
                                padx=U.px(10), pady=U.px(8))
        sb = tk.Scrollbar(self.raw_tool, command=self.txt_tool.yview,
                          width=U.px(11))
        self.txt_tool.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt_tool.pack(side="left", fill="both", expand=True)
        self.txt_tool.configure(state="disabled")
        self.raw_tool.grid_remove()

    def _tool_view(self, key: str):
        if key == "raw":
            self.raw_tool.grid()
            self.tbl_tool.grid_remove()
            self.raw_tool.tkraise()
        else:
            self.tbl_tool.grid()
            self.raw_tool.grid_remove()
            self.tbl_tool.tkraise()

    # ============================================================ 当前设备视图
    # 这四个属性都不存状态，一律指向「当前活动的那台」。多设备切换靠改
    # SessionManager.active_id，页面里读的还是这同一套名字，改动面最小。
    @property
    def session(self):
        return self.sessions.session

    @session.setter
    def session(self, s):
        # 兼容直接赋值的旧写法（外部脚本/自测会这么干）：塞给「当前这台」，
        # 没有当前台就新建一个槽位。正常连接流程走 sessions.add()。
        if s is None:
            self.sessions.active_id = ""
            return
        slot = self.sessions.active
        if slot is None:
            self.sessions.add(s, self._local_device)
        else:
            slot.session = s
            slot.reset_sampler()

    @property
    def device(self) -> DeviceInfo:
        return self.sessions.device or self._local_device

    @property
    def dialect(self):
        return self.sessions.dialect

    @property
    def sampler(self):
        return self.sessions.sampler

    def _active_session(self):
        """工具默认跑在当前会话上；没连接就跑本机。"""
        if self.session and self.session.alive:
            return self.session
        if not getattr(self, "_local", None):
            self._local = LocalSession()
            self._local.tracer = self._trace_cmd
        return self._local

    def _active_dialect(self):
        if self.dialect:
            return self.dialect
        # 本机
        from core.detect import WindowsDialect, DarwinDialect
        if sys.platform == "win32":
            return WindowsDialect()
        if sys.platform == "darwin":
            return DarwinDialect()
        from core.detect import Dialect
        return Dialect()

    def do_tool(self, key: str):
        s = self._active_session()
        d = self._active_dialect()
        target = self.v_target.get().strip()
        if key in ("ping", "trace") and not target:
            return messagebox.showwarning("缺少目标", "请填一个主机名或 IP", parent=self)

        cmds = {
            "ping": d.ping_cmd(target, 4),
            "trace": d.traceroute_cmd(target),
            "ip": d.ip_cmd(),
            "route": d.route_cmd(),
            "ports": d.listen_ports_cmd(),
            "dns": d.dns_cmd(),
        }
        cmd = cmds[key]
        self.lbl_ping.configure(text=f"正在执行…", fg=U.T.text_dim)
        self.status.set_state("执行中…")

        def work():
            rc, out, err = s.run_traced(cmd, timeout=60)
            return key, rc, out, err

        def done(res):
            k, rc, out, err = res
            self._set_tool_out(out + (("\n[stderr]\n" + err) if err.strip() else ""))
            rows, cols, summary = self._tool_rows(k, out, rc)
            self.tbl_tool.set_columns(cols)
            self.tbl_tool.set_rows(rows)
            self.tbl_tool.set_empty(
                "这条命令没有可结构化的内容",
                f"{cmd} 的输出无法解析成表格，切到「原始输出」看原文。")
            self.lbl_ping.configure(
                text=summary, fg=U.T.text if rc == 0 else U.T.warn)
            if rows:
                self.seg_tool.select("table", fire=False)
                self._tool_view("table")
            else:
                self.seg_tool.select("raw", fire=False)
                self._tool_view("raw")
            self.status.set_state("完成" if rc == 0 else f"rc={rc}",
                                  "ok" if rc == 0 else "warn")

        self.bg(work, ok=done, err=lambda m: (
            self.lbl_ping.configure(text="失败：" + m, fg=U.T.err),
            self.status.set_state(m, "err")))

    def _set_tool_out(self, text: str) -> None:
        self.txt_tool.configure(state="normal")
        self.txt_tool.delete("1.0", "end")
        self.txt_tool.insert("1.0", self._scrub(text))
        self.txt_tool.configure(state="disabled")

    def _tool_rows(self, key: str, out: str, rc: int):
        """返回 (rows, columns, 一句话摘要)。解析不出来就回退到原始输出。"""
        cols = TOOL_COLS.get(key, TOOL_COLS["ip"])
        rows: list[dict] = []
        summary = ""
        if key == "ip":
            rows = parsers.ip_table("", out)
            summary = f"{len(rows)} 个地址"
        elif key == "route":
            rows = parsers.route_table(out)
            for r in rows:
                r["kind"] = "默认" if routes.is_default(r) else (
                    "直连" if not r.get("gateway") else "静态")
            summary = f"{len(rows)} 条路由"
        elif key == "ports":
            rows = parsers.port_table(out)
            summary = f"{len(rows)} 个监听端口"
        elif key == "dns":
            rows = parsers.dns_rows(out)
            summary = f"{len(rows)} 条 DNS 配置"
        elif key == "ping":
            series = parsers.ping_series(out)
            rows = [{"seq": x["seq"],
                     "rtt": "" if x["rtt"] is None else f"{x['rtt']:.1f}",
                     "note": "超时 / 不可达" if x["rtt"] is None else "响应",
                     "_tone": "err" if x["rtt"] is None else "ok"}
                    for x in series]
            summary = self._ping_summary(out)
        elif key == "trace":
            rows = parsers.traceroute_rows(out)
            summary = f"{len(rows)} 跳"
        if rc != 0:
            summary = (summary + " · " if summary else "") + f"rc={rc}"
        return rows, cols, summary

    @staticmethod
    def _ping_summary(out: str) -> str:
        """把 ping 回显压成一行人话。

        三种格式都要认：
          Linux     4 packets transmitted, 4 received, 0% packet loss
          Win 英文  Sent = 4, Received = 4, Lost = 0 (0% loss)
          Win 中文  已发送 = 4，已接收 = 4，丢失 = 0 (0% 丢失)
        """
        if not out.strip():
            return "无输出"
        sent = recv = loss = None
        rtt = ""
        avg = mn = mx = None

        # ---- 包数
        m = re.search(r"(\d+)\s+packets transmitted,\s*(\d+)\s+(?:packets\s+)?received",
                      out)
        if m:
            sent, recv = int(m.group(1)), int(m.group(2))
            loss = 0 if sent == 0 else round((sent - recv) * 100 / sent)
        if sent is None:
            m = re.search(r"Sent\s*=\s*(\d+)\s*[,，]\s*Received\s*=\s*(\d+)"
                          r"\s*[,，]\s*Lost\s*=\s*(\d+)", out)
            if m:
                sent, recv, loss = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if sent is None:
            m = re.search(r"已发送\s*=\s*(\d+)\s*[，,]\s*已接收\s*=\s*(\d+)"
                          r"\s*[，,]\s*丢失\s*=\s*(\d+)", out)
            if m:
                sent, recv, loss = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if sent is None:
            # 兜底：从「X% 丢失 / X% loss」反推
            m = re.search(r"(\d+)%\s*(?:丢失|loss)", out)
            if m:
                loss = int(m.group(1))
                sent = 4
                recv = round(sent * (100 - loss) / 100)

        # ---- 往返时间
        m = re.search(r"=\s*[\d.]+/([\d.]+)/([\d.]+)/[\d.]+\s*ms", out)
        if m:
            avg, mx = m.group(1), m.group(2)
            mn = re.search(r"=\s*([\d.]+)/", out)
            mn = mn.group(1) if mn else avg
        if avg is None:
            m = re.search(r"Minimum\s*=\s*(\d+)ms\s*[,，]\s*Maximum\s*=\s*(\d+)ms"
                          r"\s*[,，]\s*Average\s*=\s*(\d+)ms", out)
            if m:
                mn, mx, avg = m.group(1), m.group(2), m.group(3)
        if avg is None:
            m = re.search(r"最短\s*=\s*(\d+)ms\s*[，,]\s*最长\s*=\s*(\d+)ms"
                          r"\s*[，,]\s*平均\s*=\s*(\d+)ms", out)
            if m:
                mn, mx, avg = m.group(1), m.group(2), m.group(3)
        if avg is not None:
            rtt = f" · 平均 {avg} ms（最小/最大 {mn}/{mx}）"

        if sent is None:
            # 连不上时 Windows 只给一句人话，没有统计
            for hint in ("请求超时", "无法访问目标主机", "找不到主机",
                         "could not find host", "Request timed out",
                         "Destination host unreachable", "100% packet loss"):
                if hint.lower() in out.lower():
                    return f"目标无响应（{hint}）"
            return "已执行（输出格式未识别）"
        return f"发送 {sent} · 接收 {recv} · 丢包 {loss}%{rtt}"

    # ============================================================ 系统信息
    def _build_sys(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(2, weight=1)

        c = U.Card(pg, title="系统概览", hint="按探测到的系统生成命令")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        row = U.wframe(b, "card")
        row.pack(fill="x")
        for label, key in (("版本信息", "os"), ("负载", "uptime"),
                           ("磁盘", "disk"), ("进程 TOP", "top")):
            U.RoundButton(row, label, lambda k=key: self.do_sys(k),
                          variant="secondary", font=U.FONT_S,
                          height=30, padx=14).pack(side="left", padx=(0, 8))
        U.RoundButton(row, "重新探测设备", self._redetect, variant="ghost",
                      font=U.FONT_S, height=30, padx=12).pack(side="right")

        self.kk_sys = KVGrid(b, columns=3)
        self.kk_sys.pack(fill="x", pady=(U.px(12), 0))
        self.kk_sys.set_items([("系统", "尚未读取", None), ("内核", "—", None),
                               ("架构", "—", None)])

        head = U.wframe(pg, "page")
        head.grid(row=1, column=0, sticky="we", pady=(U.px(10), 0))
        self.seg_sys = Segmented(head, [("disk", "磁盘"), ("proc", "进程"),
                                        ("raw", "原始输出")],
                                 command=self._sys_view)
        self.seg_sys.pack(side="left")
        self.lbl_sys = U.wlabel(head, "", "pagedim", U.FONT_XS)
        self.lbl_sys.pack(side="right")

        c2 = U.Card(pg, title="明细")
        c2.grid(row=2, column=0, sticky="nsew", pady=(U.px(8), 0))
        holder = U.wframe(c2.body, "card")
        holder.pack(fill="both", expand=True)
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        self.tbl_disk = DataTable(holder, SYS_COLS["disk"], height=8,
                                  searchable=False,
                                  empty_title="还没有读取磁盘",
                                  empty_hint="点上面的「磁盘」。")
        self.tbl_proc = DataTable(holder, SYS_COLS["proc"], height=8,
                                  searchable=True,
                                  empty_title="还没有读取进程",
                                  empty_hint="点上面的「进程 TOP」。")
        self.raw_sys = tk.Frame(holder, bg=U.T.card, bd=0)
        self.txt_sys = tk.Text(self.raw_sys, bg=U.T.console_bg, fg=U.T.console_fg,
                               font=U.MONO_S, bd=0, relief="flat",
                               highlightthickness=0, wrap="none",
                               padx=U.px(10), pady=U.px(8))
        sb = tk.Scrollbar(self.raw_sys, command=self.txt_sys.yview, width=U.px(11))
        self.txt_sys.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt_sys.pack(side="left", fill="both", expand=True)
        self.txt_sys.configure(state="disabled")

        for w in (self.tbl_disk, self.tbl_proc, self.raw_sys):
            w.grid(row=0, column=0, sticky="nsew")
        self._sys_views = {"disk": self.tbl_disk, "proc": self.tbl_proc,
                           "raw": self.raw_sys}
        self._sys_view("disk")

    def _sys_view(self, key: str):
        for k, w in self._sys_views.items():
            if k == key:
                w.grid()
                w.tkraise()
            else:
                w.grid_remove()

    def do_sys(self, key: str):
        s = self._active_session()
        d = self._active_dialect()
        cmd = {"os": d.os_info_cmd(), "uptime": d.uptime_cmd(),
               "disk": d.disk_cmd(), "top": d.top_cmd()}[key]
        self.status.set_state("执行中…")

        def work():
            return key, s.run_traced(cmd, timeout=60)

        def done(res):
            k, (rc, out, err) = res
            body = out or err
            self.txt_sys.configure(state="normal")
            self.txt_sys.delete("1.0", "end")
            self.txt_sys.insert("1.0", self._scrub(body))
            self.txt_sys.configure(state="disabled")
            if k == "os":
                self.kk_sys.set_items(self._os_overview(body))
                self._sys_view("raw")
                self.seg_sys.select("raw", fire=False)
                self.lbl_sys.configure(text="版本信息已解析到上方概览")
            elif k == "uptime":
                rows = self._uptime_rows(body)
                self.kk_sys.set_items(rows[:3] + self.kk_sys_items_keep())
                self._sys_view("raw")
                self.seg_sys.select("raw", fire=False)
                self.lbl_sys.configure(text=body.strip().splitlines()[0][:80]
                                       if body.strip() else "")
            elif k == "disk":
                rows = parsers.disk_table(body)
                self.tbl_disk.set_rows(rows)
                self._sys_view("disk")
                self.seg_sys.select("disk", fire=False)
                self.lbl_sys.configure(text=f"{len(rows)} 个分区")
            elif k == "top":
                rows = parsers.proc_table(body)
                fam = self._effective_device().os_family
                self.tbl_proc.set_columns(
                    SYS_COLS["proc_win"] if fam == "windows" else SYS_COLS["proc"])
                self.tbl_proc.set_rows(rows)
                self._sys_view("proc")
                self.seg_sys.select("proc", fire=False)
                self.lbl_sys.configure(text=f"{len(rows)} 个进程")
            self.status.set_state("完成" if rc == 0 else f"rc={rc}",
                                  "ok" if rc == 0 else "warn")

        self.bg(work, ok=done, err=lambda m: self.status.set_state(m, "err"))

    def kk_sys_items_keep(self):
        """切换概览内容时保留「系统 / 内核 / 架构」这三项。"""
        d = self.device if self.device.os_family else self._effective_device()
        return [("系统", d.os_name or d.os_family, "brand"),
                ("内核", d.kernel or "—", None),
                ("架构", d.arch or "—", None)]

    def _os_overview(self, text: str) -> list[tuple]:
        d = self.device if self.device.os_family else self._effective_device()
        items = [("系统", d.os_name or d.os_family, "brand"),
                 ("内核", d.kernel or "—", None),
                 ("架构", d.arch or "—", None)]
        pairs = []
        for ln in text.splitlines():
            if ":" in ln:
                k, _, v = ln.partition(":")
                if v.strip():
                    pairs.append((k.strip(), v.strip().strip('"')))
        want = ("PRETTY_NAME", "VERSION", "NAME", "ID", "OS 名称", "OS 版本",
                "系统型号", "系统类型", "处理器", "物理内存总量", "OS Name",
                "OS Version", "System Model", "Processor", "Total Physical Memory")
        seen = set()
        for k, v in pairs:
            if k in want and k not in seen:
                seen.add(k)
                label = {"PRETTY_NAME": "发行版", "VERSION": "版本",
                         "NAME": "名称", "ID": "ID"}.get(k, k)
                items.append((label, v[:52], None))
        # uname 行
        m = re.search(r"^(Linux|Darwin|FreeBSD)\s+(\S+)\s+(\S+)", text, re.M)
        if m:
            items.append(("主机名", m.group(2), None))
        if len(items) < 5:
            items.append(("提示", "未识别到发行版信息，原始输出里有完整内容", "warn"))
        return items[:9]

    def _uptime_rows(self, text: str) -> list[tuple]:
        out = []
        m = re.search(r"up\s+([^,]+)", text)
        if m:
            out.append(("已运行", m.group(1).strip(), "brand"))
        m = re.search(r"load averages?:\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)",
                      text)
        if m:
            out.append(("负载 (1/5/15)", "/".join(m.groups()), None))
        m = re.search(r"\busers?,\s*|(\d+)\s+users?", text)
        if m and m.group(1):
            out.append(("登录用户", m.group(1), None))
        if not out:
            out.append(("负载", text.strip().splitlines()[0][:60] if text.strip() else "—", None))
        return out

    def _redetect(self):
        s = self._active_session()

        def work():
            return detect(s)

        def ok(dev):
            slot = self.sessions.active
            if slot is not None:
                slot.device = dev
                slot.dialect = dialect_for(dev)
                slot.reset_sampler()
                self._render_device(dev, slot.session)
            else:
                self._local_device = dev
            self.status.set_device(dev.hostname or dev.os_name, dev.os_family)
            self.pill_dev.set(dev.os_name or "已连接", "ok")
            messagebox.showinfo("探测完成",
                                f"{dev.os_name}\n{dev.kernel}\n{dev.device_type}",
                                parent=self)

        self.bg(work, ok=ok, err=lambda m: messagebox.showerror("探测失败", m, parent=self))

    # ============================================================ VPN 模块
    def _build_vpn(self, pg):
        """证书控制台直接长在这一页里（不再是独立进程）。

        它本身就是个 tk.Frame，这里只负责挂上去 + 首次进入时才读磁盘。
        """
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(0, weight=1)
        from vpncert_gui import App as VpnConsole
        self.vpn_console = VpnConsole(pg, embedded=True,
                                      on_status=self._vpn_status,
                                      on_quit=self._quit_app)
        self.vpn_console.grid(row=0, column=0, sticky="nsew")

    def _vpn_status(self, text: str) -> None:
        """控制台内部的状态文案转发到主程序状态栏。"""
        try:
            tone = "err" if str(text).startswith("✗") else \
                "ok" if str(text).startswith("✓") else None
            self.status.set_state(str(text)[:110], tone)
        except Exception:  # noqa: BLE001
            pass

    def _vpn_boot(self) -> None:
        if getattr(self, "_vpn_booted", True):
            return
        self._vpn_booted = True
        try:
            self.vpn_console.boot()
        except Exception as e:  # noqa: BLE001
            self.status.set_state(f"隧道服务管理初始化失败：{e}", "err")

    # ============================================================ 防火墙模块
    # ============================================================ 应用锁
    def _lock_btn_text(self) -> str:
        return "🔒 锁定" if self.lock.enabled() else "设置锁"

    def _watch_activity(self):
        for seq in ("<Any-KeyPress>", "<Any-ButtonPress>", "<MouseWheel>"):
            self.bind_all(seq, self._touch, add="+")
        self._lock_tick()

    def _touch(self, _e=None):
        self._last_active = time.time()

    def _lock_tick(self):
        try:
            if self._lock_overlay is None and self.lock.enabled():
                t = self.lock.timeout()
                if t and time.time() - self._last_active > t:
                    self._show_lock("闲置超时，已自动锁定")
        except Exception:  # noqa: BLE001
            pass
        if not self._alive:
            return
        try:
            self._lock_after = self.after(5000, self._lock_tick)
        except tk.TclError:
            self._lock_after = None

    def _maybe_lock_on_start(self):
        if not self.lock.enabled():
            return
        if self.lock.auto_login():
            ok, _ = self.lock.unlock_with_remembered()
            if ok:
                return
        self._show_lock("启动需要解锁")

    def _show_lock(self, reason: str = ""):
        """锁屏：紧凑卡片 + 明确的解锁按钮。

        之前用 `U.Card` 自适应高度，在部分 DPI / ttk 环境下输入框和按钮会被
        算成 0 高而看不见 —— 这里改成固定宽度 + 逐项显式 pack，并保留抖动反馈。
        """
        if self._lock_overlay is not None:
            return
        try:
            self._disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.lock.forget()

        ov = tk.Frame(self, bg=U.T.page, bd=0, highlightthickness=0)
        ov.place(x=0, y=0, relwidth=1, relheight=1)
        ov.lift()
        wrap = tk.Frame(ov, bg=U.T.page, bd=0)
        wrap.place(relx=0.5, rely=0.42, anchor="center")

        card = tk.Frame(wrap, bg=U.T.card, bd=0, highlightthickness=1,
                        highlightbackground=U.T.border)
        card.pack()
        body = tk.Frame(card, bg=U.T.card, bd=0)
        body.pack(padx=U.px(26), pady=U.px(22))

        # 一把锁的小图标（Canvas 自绘，不依赖图片资源）
        icon = tk.Canvas(body, width=U.px(46), height=U.px(46),
                         bg=U.T.card, highlightthickness=0, bd=0)
        icon.pack()
        icon.create_arc(U.px(11), U.px(4), U.px(35), U.px(28), start=0,
                        extent=180, style="arc", outline=U.T.brand,
                        width=max(2, U.px(2.2)))
        icon.create_rectangle(U.px(8), U.px(18), U.px(38), U.px(42),
                              outline=U.T.brand, width=max(2, U.px(2.2)),
                              fill=U.T.brand_soft)
        icon.create_oval(U.px(21), U.px(26), U.px(25), U.px(30),
                         outline="", fill=U.T.brand)
        icon.create_line(U.px(23), U.px(30), U.px(23), U.px(35),
                         fill=U.T.brand, width=max(2, U.px(2)))

        tk.Label(body, text="应用已锁定", bg=U.T.card, fg=U.T.text,
                 font=U.FONT_T).pack(pady=(U.px(10), 0))
        tk.Label(body, text=reason or "输入密码解锁", bg=U.T.card,
                 fg=U.T.text_dim, font=U.FONT_XS).pack(pady=(U.px(3), 0))

        v = tk.StringVar()
        e = ttk.Entry(body, textvariable=v, show="*", width=26, font=U.FONT)
        e.pack(fill="x", pady=(U.px(14), 0), ipady=U.px(2))
        msg = tk.Label(body, text="", bg=U.T.card, fg=U.T.err, font=U.FONT_XS)
        msg.pack(pady=(U.px(6), 0))

        btn = U.RoundButton(body, "解 锁", None, variant="primary",
                            height=34, padx=18, font=U.FONT_B)
        btn.pack(fill="x", pady=(U.px(8), 0))
        quit_btn = U.RoundButton(body, "退出程序", lambda: self._quit_app(),
                                 variant="ghost", height=30, padx=12,
                                 font=U.FONT_S)
        quit_btn.pack(pady=(U.px(6), 0))

        def shake(i: int = 0):
            if i >= 6:
                try:
                    wrap.place_configure(x=0)
                except tk.TclError:
                    pass
                return
            try:
                wrap.place_configure(x=U.px(9 if i % 2 == 0 else -9))
            except tk.TclError:
                return
            self.after(45, shake, i + 1)

        def unlock(_e=None):
            if self.lock.check(v.get()):
                self.lock.adopt(v.get())
                self._last_active = time.time()
                ov.destroy()
                self._lock_overlay = None
                self.status.set_state("已解锁", "ok")
                return
            msg.configure(text="密码不对，再试一次")
            v.set("")
            e.focus_set()
            shake()

        btn.command = unlock
        e.bind("<Return>", unlock)
        self._lock_overlay = ov
        self.after(60, lambda: (e.focus_force(), e.icursor("end")))

    def _lock_dialog(self):
        if not self.lock.enabled():
            self._open_lock_window()
            return
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="立即锁定", command=lambda: self._show_lock("已手动锁定"))
        m.add_command(label="锁设置…", command=self._open_lock_window)
        m.add_separator()
        m.add_command(label="关闭应用锁", command=self._open_lock_window)
        m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _open_lock_window(self):
        """锁设置。已启用时必须先输当前密码才能改 / 关 —— 否则锁没意义。"""
        enabled = self.lock.enabled()
        dlg = tk.Toplevel(self)
        dlg.title("应用锁")
        dlg.configure(bg=U.T.page)
        dlg.transient(self)
        dlg.resizable(False, False)

        card = U.Card(dlg, pad=20)
        card.pack(fill="both", expand=True, padx=U.px(12), pady=U.px(12))
        b = card.body
        U.wlabel(b, "应用锁", "card", U.FONT_H).pack(anchor="w")
        U.wlabel(b, self.lock.describe(), "dim", U.FONT_S).pack(
            anchor="w", pady=(U.px(3), U.px(12)))

        v_old = tk.StringVar()
        v_pw, v_pw2 = tk.StringVar(), tk.StringVar()
        v_min = tk.StringVar(value=str(max(0, self.lock.timeout() // 60)))
        v_rem = tk.BooleanVar(value=self.lock.auto_login())

        def field(label: str, var: tk.StringVar, hint: str = ""):
            U.wlabel(b, label, "card", U.FONT_S).pack(anchor="w")
            ent = ttk.Entry(b, textvariable=var, show="*", width=32)
            ent.pack(fill="x", pady=(U.px(3), U.px(8)))
            if hint:
                U.wlabel(b, hint, "mute", U.FONT_XS).pack(
                    anchor="w", pady=(0, U.px(8)))
            return ent

        e_old = None
        if enabled:
            e_old = field("当前密码（修改必须验证）", v_old,
                          "已启用应用锁，改密码或关闭都要先输对当前密码。")
        field("新密码（至少 6 位）", v_pw)
        field("再输一次", v_pw2)

        r = U.wframe(b, "card")
        r.pack(fill="x", pady=(U.px(2), 0))
        U.wlabel(r, "闲置", "card", U.FONT_S).pack(side="left")
        ttk.Entry(r, textvariable=v_min, width=6).pack(side="left", padx=(6, 4))
        U.wlabel(r, "分钟后锁定（0 = 永不）", "card", U.FONT_S).pack(side="left")
        U.Check(b, "记住密码（本机加密保存，启动自动解锁）", v_rem).pack(
            anchor="w", pady=(U.px(10), 0))

        msg = U.wlabel(b, "", "err", U.FONT_XS, justify="left",
                       wraplength=U.px(400))
        msg.pack(anchor="w", pady=(U.px(8), 0))
        act = U.wframe(b, "card")
        act.pack(fill="x", pady=(U.px(12), 0))

        def apply_pw(close_lock: bool = False):
            old = v_old.get() if enabled else None
            if v_pw.get() != v_pw2.get():
                return msg.configure(text="两次输入不一致")
            try:
                mins = int(v_min.get() or "30")
            except ValueError:
                return msg.configure(text="分钟数必须是数字")
            if close_lock:
                ok, text = self.lock.clear_password(old_password=old)
                if not ok:
                    return msg.configure(text=text)
                self._settings_sync()
                self.toast(text, "ok")
                dlg.destroy()
                return
            ok, text = self.lock.set_password(
                v_pw.get(), timeout=0 if mins <= 0 else mins * 60,
                remember=v_rem.get(), old_password=old)
            if not ok:
                return msg.configure(text=text)
            self._settings_sync()
            self.toast(text, "ok")
            dlg.destroy()

        U.RoundButton(act, "保存" if enabled else "启用", apply_pw,
                      variant="primary", height=32, padx=18).pack(side="left")
        if enabled:
            U.RoundButton(act, "关闭应用锁",
                          lambda: apply_pw(close_lock=True),
                          variant="danger", height=32, padx=14).pack(
                side="left", padx=(U.px(8), 0))
        U.RoundButton(act, "取消", dlg.destroy, variant="ghost",
                      height=32, padx=14).pack(side="right")

        w = U.px(470)
        dlg.update_idletasks()
        h = dlg.winfo_reqheight()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - w) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - h) // 3)
        dlg.geometry(f"{w}x{h}+{x}+{y}")
        if e_old is not None:
            e_old.focus_set()
        else:
            dlg.focus_set()
        dlg.grab_set()

def main():
    # 一次性 UAC 提权助手（由「以管理员身份运行」拉起）：**不开窗口、不建 Tk**，
    # 只从指定目录里领命令、把结果写回去，主程序退出时它自己退出。
    # 必须排在单实例检查和 Tk 之前，否则提权进程会去抢互斥量、再建一个窗口。
    if "--elev-serve" in sys.argv:
        i = sys.argv.index("--elev-serve")
        path = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        parent = 0
        if "--parent" in sys.argv:
            j = sys.argv.index("--parent")
            try:
                parent = int(sys.argv[j + 1])
            except (IndexError, ValueError):
                parent = 0
        raise SystemExit(elev.serve(path, parent))

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 隧道控制台已经内嵌在「隧道服务管理」页里，不再有 --vpncert 子进程形态。
    # 想单独调它可以直接 `python vpncert_gui.py`（它自己能独立跑）。

    # 单实例（参考微信）：已经有一套在跑，就把那个窗口叫到前台，自己退出。
    #
    # 早先的做法是「带 --page / --no-probe / --dark 就整个跳过单实例」，
    # 因为 _texe 要连着起好几次窗口。代价是测试实例能和用户那个并存 ——
    # 托盘里堆出两三个图标，用户分不清哪个是自己开的。
    # 现在只让 `--smoke` / `--version`（根本不出窗口的那种）跳过，
    # 其余情况一律走单实例；测试脚本改用 `--instance-key <名字>` 换命名空间，
    # 例如 `--instance-key exe`，各测各的，谁也不挡谁。
    _NO_SINGLETON = ("--smoke", "--version", "--elev-serve")
    if not any(a in sys.argv for a in _NO_SINGLETON):
        key = singleinstance.key_from_argv(sys.argv)
        # 「把已有那个窗口叫到前台」这条兜底是按**窗口标题**找的，不认命名空间。
        # 测试实例（--instance-key exeN）被拦时要是也走这条路，就会把用户正在
        # 用的那个窗口拉到前面来 —— 测试不该碰用户的东西。
        is_default = key == singleinstance.DEFAULT_KEY
        # `--elev-restart` 是新进程的标记：老进程刚放锁、还要几百毫秒才退，
        # 这里必须**等一会儿**再抢。一撞就退正是「点完 UAC 闪退」的那条路。
        if "--elev-restart" in sys.argv:
            got = singleinstance.wait_acquire(key, timeout=25.0)
        else:
            got = singleinstance.acquire(key)
        if not got:
            singleinstance.wake(key)        # 叫醒它（本版本起会自己弹到前台）
            if is_default:
                # 老版本收不到事件，兜底直接动它的窗口，然后自己退出。
                singleinstance.bring_to_front(APP_NAME)
            return

    if "--dark" in sys.argv:
        U.T = U.DARK
    app = ToolboxApp()
    if "--page" in sys.argv:
        i = sys.argv.index("--page")
        if i + 1 < len(sys.argv):
            want = NAV_ALIAS.get(sys.argv[i + 1], sys.argv[i + 1])
            if want == "ai":
                # 助手已经不占选项卡了，但 --page ai 还得能用
                app.after(200, app.ai_dock_open)
                want = "hosts"
            if want in app._pages:
                app.nav.select(want)
    if "--smoke" in sys.argv:
        app.after(1500, app.destroy)
    app.mainloop()


if __name__ == "__main__":
    main()
