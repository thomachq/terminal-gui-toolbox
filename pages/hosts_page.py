# -*- coding: utf-8 -*-
"""主机管理 —— 清单式的资产台账。

布局参考用户给的图：顶部一排动作（添加主机 / 本地终端 / 导入主机），
右侧是分组下拉 + 搜索，下面是主机行。段切换把「连接与设备」原样收进来 ——
不再另开一套平行列表，两处看到的是同一份档案。

连接动作刻意**复用** `do_connect`：填好表单再走原来那条路。主机清单不自己
实现一套连接逻辑，否则「清单里连不上、表单里连得上」这种事迟早发生。
"""
from __future__ import annotations

import socket
import sys
import time
import tkinter as tk
from tkinter import messagebox

import wbtui as U
from core import creds as C
from core import hosts as H
from core import proxies as P
from ui import forms
from ui.table import Column, DataTable, Segmented

HOST_COLS = [
    # 最左两列是动作列：连接 / 编辑。
    # 表格单元格只能是文字（ttk.Treeview 没法给单个格子塞真按钮），
    # 所以这两列靠 _host_row_click 按列名分发点击 —— 点哪列就是哪个动作。
    Column("act", "", 54, anchor="center"),
    Column("edit", "", 34, anchor="center"),
    Column("status", "状态", 74),
    Column("name", "名称", 150, stretch=True),
    Column("addr", "地址", 176, mono=True),
    Column("os", "系统", 86),
    Column("proto", "协议", 64),
    Column("group", "分组", 88),
    Column("tags", "标签", 84),
    Column("cred", "凭证", 72),
    Column("last_used", "最近连接", 100),
]

# ---------------------------------------------------------------- 连通性探测
PROBE_TIMEOUT = 2.0     # 单台最多等这么久
PROBE_MAX = 60          # 一次最多探这么多台（剩下的靠搜索缩小范围再探）
PROBE_TTL = 90          # 秒；缓存超过这个时间就重探
STATUS_TEXT = {
    "live": "● 已连接",
    "local": "● 本机",
    "ok": "● 可达",
    "bad": "● 不可达",
    "wait": "… 检测中",
    "none": "○ 未检测",
}


def probe_key(rec: dict) -> str:
    """同一台主机的探测缓存键。本机返回空串（它永远在，不用探）。"""
    proto = H.proto_of(rec)
    if proto == "local":
        return ""
    host = str(rec.get("host") or "").strip().lower()
    if not host:
        return ""
    port = str(rec.get("port") or (23 if proto == "telnet" else 22)).strip()
    return f"{proto}://{host}:{port}"


def probe_host(rec: dict, timeout: float = PROBE_TIMEOUT) -> tuple[str, str]:
    """摸一下端口在不在，返回 (状态, 说明)。

    只做一次 TCP 握手就断开：不登录、不下发命令、不在对方机器上留任何东西 ——
    「零安装」这条铁律对探测同样成立。
    """
    if H.proto_of(rec) == "local":
        return "local", "本机"
    host = str(rec.get("host") or "").strip()
    if not host:
        return "none", "没有地址"
    try:
        port = int(str(rec.get("port") or 22) or 22)
    except (TypeError, ValueError):
        return "none", "端口不是数字"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "ok", ""
    except OSError as e:
        return "bad", str(e) or "连不上"


def probe_many(recs: list[dict], timeout: float = PROBE_TIMEOUT,
               workers: int = 8) -> dict[str, tuple[str, str]]:
    """并发探一批，返回 {缓存键: (状态, 说明)}。

    串行探 20 台、每台 2 秒超时就是 40 秒，界面像死了一样；
    并发 8 路之后最坏也就几秒。
    """
    from concurrent.futures import ThreadPoolExecutor
    out: dict[str, tuple[str, str]] = {}
    todo = [r for r in (recs or []) if probe_key(r)]
    if not todo:
        return out
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo)))) as ex:
        futs = {ex.submit(probe_host, r, timeout): probe_key(r) for r in todo}
        for f, k in futs.items():
            try:
                out[k] = f.result()
            except Exception as e:  # noqa: BLE001  单台炸了不该拖垮整批
                out[k] = ("bad", str(e))
    return out


def host_fields(rec: dict | None = None, cred_opts=None, proxy_opts=None,
                is_win: bool | None = None) -> list[dict]:
    """「添加/编辑主机」的字段表。

    做成模块级纯函数是为了能单独测：表单本身是模态窗，测试开它要等用户点。
    这里只描述「有哪些字段、什么时候显示」，跟界面怎么画无关。

    四条规矩：

      · 协议选「本机」时，地址 / 端口 / 认证 / 私钥 / 凭证 / 代理全部收起来 ——
        本机连接不需要地址也不需要口令，让用户填这些是纯折磨。
      · 认证选「私钥」就只问私钥和私钥密码；选「登录密码」才问登录密码。
        两个框同时摆着，用户根本分不清哪个是哪个、哪个留空算正常。
      · 提权那块由**目标系统**决定，不是由本机平台决定：Linux 问 sudo 密码，
        交换机 / 路由器问 enable 密码，Windows 根本没有 sudo 这回事。
      · 每个口令框后面跟一个「保存」：勾了才落盘，留空表示这次不改。
    """
    rec = rec or H.blank_record()
    cred_opts = cred_opts if cred_opts is not None else [("", "不使用凭证（手填口令）")]
    proxy_opts = proxy_opts if proxy_opts is not None else [("", "直连（不走代理）")]
    is_win = sys.platform == "win32" if is_win is None else bool(is_win)
    remote = ["ssh", "telnet"]

    def when_os(want, extra=()):
        """提权区按「目标系统」显隐 —— 目标系统是自动时按协议猜（见 assume_os）。"""
        wants = (want,) if isinstance(want, str) else tuple(want)

        def fn(vals) -> bool:
            if H.assume_os(vals, is_win) not in wants:
                return False
            return all(bool(vals.get(k)) == bool(v) for k, v in extra)
        return fn

    def login_pw(vals) -> bool:
        """登录密码只在「Telnet」或「SSH + 密码认证」时出现。

        SSH 选了私钥就不该再有登录密码 —— 那时候要问的是私钥密码。
        """
        proto = H.proto_of(vals)
        if proto == "telnet":
            return True
        return proto == "ssh" and str(vals.get("auth") or "") == "password"

    saved = bool(rec.get("sudo_password"))
    fields = [
        {"key": "name", "label": "名称", "kind": "text", "required": True,
         "default": H.display_name(rec) if rec.get("name") else "",
         "hint": "清单里显示的名字，同一名称会覆盖原记录"},
        {"key": "proto", "label": "协议", "kind": "select",
         "options": [("ssh", "SSH"), ("telnet", "Telnet"), ("local", "本机")],
         "default": H.proto_of(rec)},
        {"key": "host", "label": "地址", "kind": "text",
         "show_if": {"proto": remote},
         "default": str(rec.get("host") or ""),
         "hint": "IP 或域名；IPv6 直接写"},
        {"key": "port", "label": "端口", "kind": "text",
         "show_if": {"proto": remote},
         "default": str(rec.get("port") or "22")},
        {"key": "user", "label": "用户", "kind": "text",
         "show_if": {"proto": remote},
         "default": str(rec.get("user") or "")},
        # 目标系统：决定下面提权问的是 sudo 密码、enable 密码，还是根本不用密码
        {"key": "ostype", "label": "目标系统", "kind": "select",
         "show_if": {"proto": remote},
         "options": H.pick_options(),
         "default": H.os_of(rec),
         "hint": "决定提权方式；留「自动」的话连上后按 uname / ver 认出来并记住"},
        {"key": "auth", "label": "认证", "kind": "select",
         "show_if": {"proto": ["ssh"]},
         "options": [("key", "私钥"), ("password", "登录密码")],
         "default": str(rec.get("auth") or "key")},
        {"key": "key_path", "label": "私钥路径", "kind": "file",
         "show_if": {"proto": ["ssh"], "auth": ["key"]},
         "default": str(rec.get("key_path") or ""),
         "pick_title": "选择 SSH 私钥",
         "filetypes": [("私钥文件",
                        "id_rsa* id_ed25519* id_ecdsa* id_dsa* *.pem *.key"),
                       ("全部文件", "*.*")],
         "hint": "留空表示用 SSH 默认密钥或已在 agent 里的密钥",
         "hint_below": True},
        # 私钥的口令叫「私钥密码」；选了私钥就不再问登录密码
        {"key": "key_passphrase", "label": "私钥密码", "kind": "password",
         "show_if": {"proto": ["ssh"], "auth": ["key"]},
         "default": "",
         "save": True, "save_default": bool(rec.get("key_passphrase")),
         # 这行右边只剩很窄一条（口令框 + 「保存」勾占满了），说明写长了
         # 会被折成「…加密保 / 存」。短句即可，「保存」勾本身已经说清了
         "hint": "口令不是必填"},
        {"key": "password", "label": "登录密码", "kind": "password",
         "show_if": login_pw, "watch": ["proto", "auth"],
         "default": "",
         "save": True, "save_default": bool(rec.get("password")),
         "hint": "填了会加密存进档案；多台共用建议改到「凭证管理」"},
        # ---- 提权：Linux 是 sudo，交换机是 enable，Windows 没有这回事
        {"key": "sudo", "label": "连接后自动提权",
         "kind": "check", "show_if": when_os(H.UNIX_OS + ("network",)),
         "watch": ["proto", "ostype"],
         "default": bool(rec.get("sudo")),
         "hint": "类 Unix 执行 sudo -i，交换机 / 路由器进 enable；"
                 "不勾也能用，写操作时才问密码"},
        {"key": "sudo_pass", "label": "sudo 密码", "kind": "password",
         "show_if": when_os(H.UNIX_OS, (("sudo", True),)),
         "watch": ["proto", "ostype", "sudo"],
         "default": "",
         "save": True, "save_default": saved,
         "hint": "留空表示每次现问"},
        {"key": "enable_pass", "label": "enable 密码", "kind": "password",
         "show_if": when_os("network", (("sudo", True),)),
         "watch": ["proto", "ostype", "sudo"],
         "default": "",
         "save": True, "save_default": saved,
         "hint": "留空表示每次现问"},
        {"key": "elevate", "label": "以管理员权限运行（UAC 提权）",
         "kind": "check",
         "show_if": lambda v: (H.proto_of(v) == "local"
                               and H.assume_os(v, is_win) == "windows"),
         "watch": ["proto", "ostype"],
         "default": bool(rec.get("elevate")),
         "hint": "启动会话时弹 UAC 让你同意；Windows 上没有 sudo"},
        {"key": "win_note", "label": "提权", "kind": "readonly",
         "show_if": {"proto": remote, "ostype": ["windows"]},
         "default": "目标判定为 Windows：没有 sudo，也不需要 sudo 密码",
         "hint": "管理操作靠管理员账户 / UAC"},
        {"key": "shell", "label": "Shell", "kind": "select",
         "show_if": {"proto": ["local"]},
         "default": str(rec.get("shell") or ""),
         "options": [("", "自动（认命令前缀）"), ("powershell", "PowerShell"),
                     ("cmd", "cmd")],
         "hint": "本机 Windows 用哪一种外壳执行命令"},
        {"key": "group", "label": "分组", "kind": "text",
         "default": str(rec.get("group") or ""),
         "hint": "留空归到「未分组」"},
        {"key": "tags", "label": "标签", "kind": "text",
         "default": str(rec.get("tags") or ""),
         "hint": "逗号分隔，例如：web, 生产, 上海"},
        {"key": "cred_id", "label": "凭证", "kind": "select",
         "show_if": {"proto": remote},
         "options": cred_opts,
         "default": str(rec.get("cred_id") or ""),
         "hint": "选了凭证就以凭证为准，上面手填的口令不生效"},
        {"key": "proxy_id", "label": "代理", "kind": "select",
         "show_if": {"proto": remote},
         "options": proxy_opts,
         "default": str(rec.get("proxy_id") or ""),
         "hint": "从「代理池」里挑一条链路进来"},
    ]
    # 同名 key 只留第一份 —— 提权那两个勾选框共用一个 `sudo` 字段，
    # 但表单里不能挂两个同 key 的控件（后建的会把先建的 getter 顶掉）。
    seen: set[str] = set()
    out: list[dict] = []
    for f in fields:
        if f["key"] in seen:
            continue
        seen.add(f["key"])
        out.append(f)
    return out


def merge_host_form(data: dict, base: dict | None = None,
                    is_win: bool | None = None) -> dict:
    """表单值 → 档案记录。纯函数，离线就能验。

    三件容易做错的事都在这里：

      · **口令三态**：填了且勾保存 → 落盘；填了没勾 → 只当次用；
        留空但勾着 → 保留原来的；留空且取消勾选 → 删掉存的。
        取消勾选是唯一的删除入口，所以必须真删，不然存进去就再也拿不出来。
      · **提权归一**：目标系统是 Windows 就没有 sudo，是 Linux / 交换机才有；
        留着不相干的勾会在下次连接时被误用。
      · **本机字段不外带**：本机的 shell 带进远程档案只会误导。
    """
    is_win = sys.platform == "win32" if is_win is None else bool(is_win)
    data = data or {}
    merged = H.record_from_form(data, base=base)
    # 两个提权密码框共用 `sudo_password` 这一个存储键（连接时本来就只看一个），
    # 所以按存储键分组处理 —— 否则「另一个框不存在」会把刚填的值再清一遍。
    for store, keys in (("password", ("password",)),
                        ("key_passphrase", ("key_passphrase",)),
                        ("sudo_password", ("sudo_pass", "enable_pass"))):
        typed = [(str(data.get(k, "") or "").strip(),
                  bool(data.get(k + "__save", False))) for k in keys]
        typed = [t for t in typed if t[0]]
        if typed:
            val, save = typed[0]
            merged[store] = val if save else ""
        elif any(bool(data.get(k + "__save", False)) for k in keys):
            pass                      # 留空 + 勾着：不动档案里已有的
        else:
            merged[store] = ""        # 没勾保存（或取消勾选）：清掉
    merged["remember"] = bool(
        merged.get("password") or merged.get("key_passphrase")
        or merged.get("sudo_password"))
    tgt = H.assume_os(merged, is_win)
    local_win = merged.get("proto") == "local" and tgt == "windows"
    merged["sudo"] = (bool(merged.get("sudo"))
                      if (H.is_unix(tgt) or tgt == "network") else False)
    merged["elevate"] = bool(merged.get("elevate")) if local_win else False
    if merged.get("proto") != "local":
        merged["shell"] = ""
    return merged


class HostsPage:
    """混入主窗口。所有方法都假设 self 已经是 ToolboxApp。"""

    # ================================================================ 构建
    def build_hosts(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(2, weight=1)

        # ---- 顶部动作条（不进卡片，紧贴页面上沿，像图里的那一排）
        bar = U.wframe(pg, "page")
        bar.grid(row=0, column=0, sticky="we", pady=(0, U.px(8)))

        U.RoundButton(bar, "＋ 添加主机", self.host_add, variant="primary",
                      font=U.FONT_S, height=30, padx=13).pack(side="left")
        # 头部也放一个「编辑」：右键菜单有人点不到（触控板 / 触摸屏），
        # 每行行首还有「连接」和 ✎，四个入口都干同一件事。
        U.RoundButton(bar, "编辑", self.host_edit_sel, variant="secondary",
                      font=U.FONT_S, height=30, padx=12).pack(side="left",
                                                               padx=(U.px(6), 0))
        U.RoundButton(bar, "本地终端", self.host_local_term, variant="secondary",
                      font=U.FONT_S, height=30, padx=12).pack(side="left",
                                                               padx=(U.px(6), 0))
        U.RoundButton(bar, "导入主机", self.host_import, variant="secondary",
                      font=U.FONT_S, height=30, padx=12).pack(side="left",
                                                               padx=(U.px(6), 0))
        U.RoundButton(bar, "检测连通", self.host_probe, variant="ghost",
                      font=U.FONT_S, height=30, padx=10).pack(side="left",
                                                              padx=(U.px(6), 0))

        # 右侧：分组下拉 + 刷新
        self.v_host_group = tk.StringVar(value="全部分组")
        self.cb_host_group = tk.OptionMenu(bar, self.v_host_group,
                                           "全部分组",
                                           command=lambda _e: self._hosts_apply_filter())
        self._style_optionmenu(self.cb_host_group, width=13)
        self.cb_host_group.pack(side="right")
        # 这里原本还有一个「分组」小标题。它和下拉里显示的值（全部分组 /
        # 生产 / 未分组）语义重复，却要吃掉 34px —— 开着 AI 侧栏时工具栏
        # 一窄，被挤到只剩 10px，屏幕上就是个「分」。直接去掉，下拉自己说话。
        U.RoundButton(bar, "刷新", self.host_refresh, variant="ghost",
                      font=U.FONT_S, height=30, padx=10).pack(side="right",
                                                              padx=(0, U.px(8)))

        # ---- 段切换：清单 / 连接与设备
        seg_row = U.wframe(pg, "page")
        seg_row.grid(row=1, column=0, sticky="we", pady=(0, U.px(6)))
        self.seg_hosts = Segmented(seg_row, [("list", "主机清单"),
                                             ("conn", "连接与设备")],
                                   command=self._hosts_tab)
        self.seg_hosts.pack(side="left")
        self.lbl_host_sum = U.wlabel(seg_row, "", "pagedim", U.FONT_XS)
        self.lbl_host_sum.pack(side="left", padx=(U.px(10), 0))

        # ---- 清单视图
        self.hosts_box = U.wframe(pg, "page")
        self.hosts_box.grid(row=2, column=0, sticky="nsew")
        self.hosts_box.rowconfigure(0, weight=1)
        self.hosts_box.columnconfigure(0, weight=1)

        self.tbl_host = DataTable(
            self.hosts_box, HOST_COLS, height=13,
            search_hint="搜索主机名称和 IP 地址",
            empty_title="还没有主机",
            empty_hint="点左上角「添加主机」建一条，或「导入主机」从 ~/.ssh/config 拉过来。",
            on_activate=self.host_connect_row,
            on_select=lambda r: None)
        self.tbl_host.grid(row=0, column=0, sticky="nsew")
        self.tbl_host.set_menu([
            ("连接", lambda: self.host_connect_row(self._host_pick()), None),
            ("在终端打开", lambda: self.host_term(self._host_pick()), None),
            (None, None, None),
            ("编辑…", lambda: self.host_edit(self._host_pick()), None),
            ("检测连通", lambda: self.host_probe([self._host_pick()]), None),
            ("复制地址", lambda: self.host_copy(self._host_pick()), None),
            ("复制为「新主机」", lambda: self.host_dup(self._host_pick()), None),
            (None, None, None),
            ("删除", lambda: self.host_del(self._host_pick()), None),
        ])
        # 行首那两列（连接 / ✎）：点一下就动这一行，不用先选中再点按钮。
        # 用 bind 到 tree 的 <Button-1> 而不是 class 绑定，所以不用插 bindtags
        # —— 这里只是「点完之后拦一下」，不需要盖过 Treeview 自己的选中逻辑。
        self.tbl_host.tree.bind("<Button-1>", self._host_row_click, add="+")
        self.tbl_host.entry_search.bind(
            "<KeyRelease>", lambda _e: self._hosts_apply_filter(), add="+")
        # 探测缓存：key → (状态, 时间戳)。切换页面不清，避免每次都重探。
        self._host_probe: dict[str, tuple[str, int]] = {}
        self._probe_busy = False
        self._probe_at = 0.0

        # ---- 连接视图（原「连接与设备」整页搬进来）
        self.conn_box = U.wframe(pg, "page")
        self.conn_box.grid(row=2, column=0, sticky="nsew")
        self._build_conn(self.conn_box)
        self.conn_box.grid_remove()

    # ================================================================ 小工具
    @staticmethod
    def _style_optionmenu(om, width: int = 12) -> None:
        """给 tk.OptionMenu 套上卡片外观。项目里没有现成的下拉组件，
        但 OptionMenu 是原生的、可靠，只需要把颜色和字对齐主题。"""
        try:
            om.configure(bg=U.T.card, fg=U.T.text, activebackground=U.T.alt,
                         activeforeground=U.T.text, bd=0, relief="flat",
                         highlightthickness=1, highlightbackground=U.T.border,
                         font=U.FONT_S, width=width, anchor="w",
                         padx=U.px(8), pady=U.px(4), cursor="hand2")
            m = om["menu"]
            m.configure(bg=U.T.card, fg=U.T.text, activebackground=U.T.brand_soft,
                        activeforeground=U.T.brand_deep, bd=0, font=U.FONT_S)
        except tk.TclError:
            pass

    def _host_pick(self) -> dict:
        row = self.tbl_host.selected()
        return (row or {}).get("_raw") or {}

    def host_edit_sel(self) -> None:
        """头部那个「编辑」按钮：作用于选中行。"""
        rec = self._host_pick()
        if not rec:
            self.toast("先选一行再编辑（也可以点行首的 ✎）", "warn")
            return
        self.host_edit(rec)

    def _host_row_click(self, event):
        """点行首的动作列：左起第一列「连接」，第二列「✎」编辑。

        返回 "break" 是为了不让同一次点击再去改选中态、也别让第二下被当成
        双击「连接」—— 点 ✎ 时尤其明显：不拦的话会先开编辑、再顺手连一台。
        """
        try:
            col = self.tbl_host.column_at(event.x)
        except (AttributeError, tk.TclError):
            return None
        if col not in ("act", "edit"):
            return None
        row = self.tbl_host.row_at(event.y)
        rec = (row or {}).get("_raw") or {}
        if not rec:
            return None
        if col == "act":
            self.host_connect_row(dict(row))
        else:
            self.host_edit(dict(rec))
        return "break"

    def _hosts_tab(self, key: str) -> None:
        if key == "conn":
            self.hosts_box.grid_remove()
            self.conn_box.grid()
            self.conn_box.tkraise()
        else:
            self.conn_box.grid_remove()
            self.hosts_box.grid()
            self.hosts_box.tkraise()
            self.host_refresh()

    # ================================================================ 数据
    def hosts_load(self) -> list[dict]:
        """把档案 + 凭证名 + 代理名组装成表格行。

        主机记录里只有 id，名字要现查 —— 这样凭证改名后清单立刻跟着变，
        不会出现「主机上写的是旧名字」这种对不上的情况。
        """
        store = getattr(self, "creds", None)
        cred_names = {str(c.get("id") or ""): str(c.get("name") or "")
                      for c in store.creds} if store is not None else {}
        pstore = getattr(self, "proxies", None)
        proxy_names = {str(p.get("id") or ""): str(p.get("name") or "")
                       for p in pstore.proxies} if pstore is not None else {}
        local_os = "Windows（本机）" if sys.platform == "win32" else "Linux（本机）"
        out = []
        for rec in self.profiles.profiles:
            row = H.host_view(
                rec,
                cred_name=cred_names.get(str(rec.get("cred_id") or ""), ""),
                proxy_name=proxy_names.get(str(rec.get("proxy_id") or ""), ""),
                local_os=local_os)
            row["status"], tone = self._host_status(rec)
            if tone:
                row["_tone"] = tone
            out.append(row)
        return out

    def _local_live(self) -> bool:
        """本机会话现在是不是连着。

        本机不占 DeviceSlot（它的 sid 是空串），所以只能看当前活动会话：
        活动设备是远程时，本机即便还能用也不算「正在连这台」。
        """
        if str(getattr(self.sessions, "active_id", "") or ""):
            return False
        s = getattr(self, "session", None)
        if s is None:
            return False
        try:
            if str(getattr(s, "kind", "") or "").lower() != "local":
                return False
            return bool(s.alive)
        except Exception:  # noqa: BLE001
            return False

    def _host_status(self, rec: dict) -> tuple[str, str]:
        """这一行现在什么状态 → (文案, 色调)。

        优先级：已连接的活会话 > 上次探测结果 > 未检测。
        活会话优先，因为「连着」比「端口通」是更强的事实。
        本机以前一律返回「● 本机」，于是连着本机的时候状态列永远看不到
        「已连接」—— 用户说的「连接状态依然没有」有一半是这个。
        """
        if H.proto_of(rec) == "local":
            if self._local_live():
                return STATUS_TEXT["live"], "ok"
            return STATUS_TEXT["local"], "ok"
        host = str(rec.get("host") or "").strip().lower()
        port = str(rec.get("port") or "").strip()
        if host and hasattr(self, "sessions"):
            for slot in self.sessions.slots():
                sess = getattr(slot, "session", None)
                if sess is None:
                    continue
                if str(getattr(sess, "host", "") or "").lower() != host:
                    continue
                if port and str(getattr(sess, "port", "") or "") not in ("", port):
                    continue
                try:
                    if slot.alive():
                        return STATUS_TEXT["live"], "ok"
                except Exception:  # noqa: BLE001
                    continue
        hit = getattr(self, "_host_probe", {}).get(probe_key(rec))
        if not hit:
            return STATUS_TEXT["none"], ""
        state = hit[0]
        if state == "wait":
            return STATUS_TEXT["wait"], "dim"
        if state == "ok":
            return STATUS_TEXT["ok"], ""
        return STATUS_TEXT["bad"], "warn"

    def host_probe(self, recs=None, timeout: float = PROBE_TIMEOUT) -> None:
        """TCP 摸一遍端口，结果记进「状态」列。

        零安装：只做一次握手就断开，不登录、不下发命令、对方机器上什么都不留。
        """
        if getattr(self, "_probe_busy", False):
            self.toast("正在检测，稍等一下…", "warn")
            return
        if not hasattr(self, "_host_probe"):
            self._host_probe = {}
        if recs is None:
            src = [r.get("_raw") or {} for r in getattr(self, "_host_rows_all", [])]
        else:
            src = [dict(r) for r in recs if r]
        todo, seen = [], set()
        for rec in src:
            k = probe_key(rec)
            if not k or k in seen:
                continue
            seen.add(k)
            todo.append(rec)
        if not todo:
            self.toast("没有可检测的主机（本机不用探）", "warn")
            return
        todo = todo[:PROBE_MAX]
        for rec in todo:
            self._host_probe[probe_key(rec)] = ("wait", 0)
        self._probe_busy = True
        self._probe_at = time.time()
        self._hosts_apply_filter()
        self.status.set_state(f"正在检测 {len(todo)} 台主机的连通性…")

        def work():
            return probe_many(todo, timeout=timeout)

        def ok(res):
            self._probe_busy = False
            now = int(time.time())
            for k, (state, _why) in (res or {}).items():
                self._host_probe[k] = (state, now)
            self.host_refresh()
            total = len(res or {})
            good = sum(1 for s, _ in (res or {}).values() if s == "ok")
            tone = "ok" if good == total else ("warn" if good else "err")
            self.toast(f"连通检测：{good}/{total} 台可达", tone)
            self.status.set_state(f"连通检测：{good}/{total} 台可达", tone)

        def err(msg):
            self._probe_busy = False
            self.toast(f"检测失败：{msg}", "err")

        self.bg(work, ok=ok, err=err)

    def _maybe_auto_probe(self) -> None:
        """进页面自动探一次。

        状态列如果默认全是「未检测」，等于没有这一列 —— 用户不会为了看红绿灯
        先去点一次按钮。所以第一次进这个页面就在后台探一轮，之后按 PROBE_TTL 节流。

        以前这里把 `winfo_ismapped()` 当硬门槛：页面还在构建、或者窗口刚建还没
        映射上去，它返回 False，于是**一次都没探过**，用户打开就是满屏「未检测」
        （截图反馈的「连接状态依然没有」就是这个）。现在改成：没映射就隔一会儿
        再看，最多试几次；页面被别的页盖住时不算失败。
        """
        if getattr(self, "_no_probe", False):
            return
        if getattr(self, "_probe_busy", False):
            return
        if not getattr(self, "_host_rows_all", None):
            return
        if time.time() - getattr(self, "_probe_at", 0.0) < PROBE_TTL:
            return
        try:
            mapped = bool(self.hosts_box.winfo_ismapped())
        except (AttributeError, tk.TclError):
            mapped = False
        if not mapped:
            n = int(getattr(self, "_probe_retry", 0))
            if n < 15:                      # 15 × 400ms ≈ 6 秒，够窗口映射完了
                self._probe_retry = n + 1
                try:
                    self.after(400, self._maybe_auto_probe)
                except tk.TclError:
                    pass
            return
        self._probe_retry = 0
        try:
            self.after(350, self.host_probe)
        except tk.TclError:
            pass

    def host_refresh(self) -> None:
        rows = self.hosts_load()
        self._host_rows_all = rows
        groups = H.group_choices(self.profiles.profiles)
        try:
            m = self.cb_host_group["menu"]
            m.delete(0, "end")
            for g in groups:
                m.add_command(label=g,
                              command=lambda v=g: (self.v_host_group.set(v),
                                                   self._hosts_apply_filter()))
        except tk.TclError:
            pass
        if self.v_host_group.get() not in groups:
            self.v_host_group.set(groups[0] if groups else "全部分组")
        self._hosts_apply_filter()
        self._maybe_auto_probe()

    def _hosts_apply_filter(self) -> None:
        rows = getattr(self, "_host_rows_all", [])
        g = self.v_host_group.get()
        if g and g != "全部分组":
            rows = H.filter_hosts(rows, group=g)
        else:
            rows = list(rows)
        q = ""
        try:
            q = self.tbl_host.entry_search.get().strip()
        except (AttributeError, tk.TclError):
            pass
        if q:
            rows = [r for r in rows
                    if all(w.lower() in (" ".join([
                        str(r.get("name") or ""), str(r.get("addr") or ""),
                        str(r.get("host") or ""), str(r.get("group") or ""),
                        str(r.get("tags") or ""), str(r.get("cred") or ""),
                    ])).lower() for w in q.split())]
        self.tbl_host.set_rows(rows)
        total = len(getattr(self, "_host_rows_all", []))
        self.lbl_host_sum.configure(text=H.summary(rows, total=total))

    # ================================================================ 表单
    def _host_form(self, rec: dict | None = None) -> dict | None:
        rec = rec or H.blank_record()
        cstore = getattr(self, "creds", None)
        cred_opts = [("", "不使用凭证（手填口令）")]
        if cstore is not None:
            for c in cstore.creds:
                cred_opts.append((str(c.get("id") or ""),
                                  f"{c.get('name')}（{C.kind_label(str(c.get('kind') or ''))}）"))
        pstore = getattr(self, "proxies", None)
        proxy_opts = [("", "直连（不走代理）")]
        if pstore is not None:
            for px in pstore.proxies:
                proxy_opts.append((str(px.get("id") or ""),
                                   str(px.get("name") or "")))
        fields = host_fields(rec, cred_opts, proxy_opts)
        title = "编辑主机" if rec.get("name") else "添加主机"
        data = forms.FormDialog.open(
            self, title, fields,
            subtitle="只填能连上的必要信息；口令勾了「保存」才加密落盘，不进日志")
        if not data:
            return None
        merged = merge_host_form(data, base=rec)
        # 名字就是主键，编辑时要按「改之前的名字」跳过自己：
        # merged 是合并出来的新 dict，validate 里的身份比对认不出它是同一条。
        errs = H.validate(merged, others=self.profiles.profiles,
                          skip_name=str(rec.get("name") or ""))
        if errs:
            messagebox.showwarning("保存不了", "\n".join(errs), parent=self)
            return None
        return merged

    def host_add(self) -> None:
        rec = self._host_form(None)
        if not rec:
            return
        try:
            self.profiles.upsert(rec)
        except ValueError as e:
            messagebox.showwarning("保存不了", str(e), parent=self)
            return
        self.host_refresh()
        self._refresh_profile_list()
        self._audit("host.add", "ok", f"新增主机 {rec['name']}", rec.get("host", ""))
        self.toast(f"已添加主机：{rec['name']}", "ok")

    def host_edit(self, rec: dict) -> None:
        if not rec:
            self.toast("先选一行再编辑", "warn")
            return
        merged = self._host_form(dict(rec))
        if not merged:
            return
        old_name = str(rec.get("name") or "")
        # 改名 = 删旧 + 增新。档案以名字做主键，不先删会留下一条改名前的孤儿。
        if old_name and old_name != merged.get("name"):
            self.profiles.remove(old_name)
        try:
            self.profiles.upsert(merged)
        except ValueError as e:
            messagebox.showwarning("保存不了", str(e), parent=self)
            return
        self.host_refresh()
        self._refresh_profile_list()
        self.toast(f"已保存：{merged['name']}", "ok")

    def host_dup(self, rec: dict) -> None:
        if not rec:
            return
        new = dict(rec)
        base = H.display_name(rec) + "-副本"
        name, i = base, 2
        while self.profiles.get(name):
            name = f"{base}-{i}"
            i += 1
        new["name"] = name
        new["last_used"] = ""
        self.profiles.upsert(new)
        self.host_refresh()
        self._refresh_profile_list()
        self.toast(f"已复制为「{name}」", "ok")

    def host_del(self, rec: dict) -> None:
        if not rec:
            self.toast("先选一行再删除", "warn")
            return
        name = H.display_name(rec)
        if not messagebox.askyesno("删除主机", f"从清单里删除「{name}」？\n"
                                              "只是删掉这条记录，不会动远端任何文件。",
                                   parent=self):
            return
        ok = self.profiles.remove(str(rec.get("name") or ""))
        self.host_refresh()
        self._refresh_profile_list()
        if not ok:
            # 删不掉要说出来。以前不管成功失败都报「已删除」，
            # 用户以为删了、其实档案还在（名字对不上 / 文件写不进去）。
            self._audit("host.remove", "fail", f"删除主机 {name}", rec.get("host", ""))
            self.toast(f"没删掉：清单里没有「{rec.get('name') or name}」这条记录",
                       "err")
            return
        self._audit("host.remove", "ok", f"删除主机 {name}", rec.get("host", ""))
        self.toast(f"已删除：{name}", "ok")

    def host_import(self) -> None:
        added, skipped = self.profiles.import_from_ssh_config()
        self.host_refresh()
        self._refresh_profile_list()
        if added:
            self._audit("host.import", "ok", f"导入 {added} 台主机")
        messagebox.showinfo("导入完成", H.import_hint(added, skipped), parent=self)

    def host_copy(self, rec: dict) -> None:
        if not rec:
            return
        self.copy_text(H.addr_of(rec), "地址")

    # ================================================================ 连接
    def host_connect_row(self, row: dict) -> None:
        """双击 / 右键「连接」：把记录灌进连接表单，再走原来的 do_connect。"""
        rec = (row or {}).get("_raw") or row or {}
        if not rec:
            return
        self._host_apply(rec)
        self.do_connect()

    def host_term(self, rec: dict) -> None:
        """「在终端打开」：先连，再开终端窗口。"""
        if not rec:
            self.toast("先选一台主机", "warn")
            return
        self._host_apply(rec)
        self.do_connect()
        try:
            self.after(400, self.open_terminal)
        except (AttributeError, tk.TclError):
            pass

    def _host_apply(self, rec: dict) -> None:
        """档案 → 连接表单。凭证库里的口令在这里才注入，不进档案明文。"""
        p = dict(rec)
        cid = str(p.get("cred_id") or "")
        if cid and getattr(self, "creds", None):
            c = self.creds.get(cid)
            if c:
                kind = str(c.get("kind") or "")
                sec = str(c.get("secret") or "")
                if kind == "password":
                    p["password"] = sec
                    p["auth"] = "password"
                elif kind == "key":
                    p["key_path"] = sec
                    p["auth"] = "key"
                elif kind == "passphrase":
                    p["key_passphrase"] = sec
                elif kind == "sudo":
                    p["sudo_password"] = sec
        self.v_proto.set(H.proto_of(p))
        self._proto_changed()
        self._apply_profile(p)
        self._proto_changed()
        # 打一次「最近连接」，清单按时间排序才有意义
        try:
            self.profiles.upsert(H.touch(p))
        except (ValueError, OSError):
            pass

    def host_local_term(self) -> None:
        """本地终端：不建档案，直接用本机协议连一次。"""
        self.v_proto.set("local")
        self._proto_changed()
        self.do_connect()
        try:
            self.after(400, self.open_terminal)
        except (AttributeError, tk.TclError):
            pass
