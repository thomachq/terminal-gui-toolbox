# -*- coding: utf-8 -*-
"""防火墙页：后端探测 + 规则列表 + 增 / 改 / 删 / 启停 / 调序（DSM 风格）。

后端不由用户选 —— 进页面自动探（`core/firewall.detect`），探到哪个用哪个，
因为 ubuntu 上是 ufw、centos 上是 firewalld、老机器是裸 iptables、
Windows 是 netsh，让用户先自己判断「我这是哪套」等于没做探测。

写操作一律：
  1. 先用后端拼出命令给用户看（`fw.plan`），确认框里逐条列出来；
  2. 动手前把当前规则原文备份到 `data/fw_backup/`；
  3. 跑完进审计（`fw.change`）；
  4. 重读列表并高亮受影响的那行；
  5. 留一条「撤销上一步」，撤销命令也是先给人看再跑。
"""
from __future__ import annotations

import re
import tkinter as tk

import wbtui as U
from core import firewall as fw
from ui import forms
from ui.table import Column, DataTable, KVGrid

COLS = [
    Column("action", "动作", 80, format=lambda v, r: fw.action_label(v)),
    Column("proto", "协议", 66, mono=True),
    Column("port", "端口 / 服务", 110, mono=True),
    Column("src", "来源", 150, mono=True, stretch=True),
    Column("dst", "目标", 130, mono=True),
    Column("chain", "链 / 方向", 100),
    Column("comment", "备注", 150, stretch=True),
    Column("enabled", "状态", 62, format=lambda v, r: "启用" if v else "停用"),
]

# 每种后端的「动作」取值（界面显示中文，值是命令里那个词）
_ACTIONS = {
    "ufw": [("allow", "允许"), ("deny", "拒绝"), ("reject", "拒绝并回包"),
            ("limit", "限速允许")],
    "firewalld": [("allow", "允许")],
    "nftables": [("accept", "允许"), ("drop", "丢弃"), ("reject", "拒绝")],
    "iptables": [("ACCEPT", "允许"), ("DROP", "丢弃"), ("REJECT", "拒绝")],
    "netsh": [("allow", "允许"), ("block", "阻止")],
    "ipfw": [("allow", "允许"), ("deny", "拒绝")],
    "pf": [("pass", "放行"), ("block", "阻止")],
    # 群晖只有 allow / deny 两种（配置文件里 policy 0 / 1）
    "synofirewall": [("allow", "允许"), ("deny", "拒绝")],
}


def _port_hits(port_text, target) -> bool:
    """端口框里有没有命中某个端口（支持 22 / 80,443 / 1000-2000）。"""
    t = str(target or "").strip()
    if not t.isdigit():
        return False
    for x in re.split(r"[,，\s]+", str(port_text or "")):
        x = x.strip()
        if not x:
            continue
        if x == t:
            return True
        a, sep, b = x.partition("-")
        if sep and a.isdigit() and b.isdigit() and int(a) <= int(t) <= int(b):
            return True
    return False


class FirewallPage:
    # ------------------------------------------------------------ 构建
    def build_fw(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        c = U.Card(pg, title="防火墙",
                   hint="自动探测 群晖 / ufw / firewalld / nftables / iptables / netsh")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        head = U.wframe(b, "card")
        head.pack(fill="x")
        self.badge_fw = U.Pill(head, "未探测", "mute", height=24)
        self.badge_fw.pack(side="left")
        self.lbl_fw = U.wlabel(
            head, "进页面自动探测用的是哪套防火墙。", "dim", U.FONT_S,
            justify="left", wraplength=U.px(560))
        self.lbl_fw.pack(side="left", padx=(U.px(10), 0))

        info = U.wframe(b, "card")
        info.pack(fill="x", pady=(U.px(10), 0))
        self.kk_fw = KVGrid(info, columns=3)
        self.kk_fw.pack(fill="x")

        # 两排按钮：第一排是「看」，第二排是「改」。改的那排默认灰着，
        # 探到可写后端才亮 —— 用户不用先点一下再被告知不能改。
        row = U.wframe(b, "card")
        row.pack(fill="x", pady=(U.px(12), 0))
        U.RoundButton(row, "重新探测", self.fw_probe, variant="secondary",
                      height=32, padx=16).pack(side="left")
        self.btn_fw_refresh = U.RoundButton(row, "刷新规则", self.fw_refresh,
                                            variant="secondary", height=32,
                                            padx=16)
        self.btn_fw_refresh.pack(side="left", padx=(U.px(8), 0))
        self.btn_fw_bk = U.RoundButton(row, "备份", self.fw_show_backups,
                                       variant="ghost", height=32, padx=14)
        self.btn_fw_bk.pack(side="left", padx=(U.px(8), 0))

        row2 = U.wframe(b, "card")
        row2.pack(fill="x", pady=(U.px(8), 0))
        self.btn_fw_add = U.RoundButton(row2, "新增规则", self.fw_add,
                                        variant="primary", height=32, padx=16)
        self.btn_fw_add.pack(side="left")
        self.btn_fw_edit = U.RoundButton(row2, "编辑", self.fw_edit,
                                        variant="secondary", height=32, padx=14)
        self.btn_fw_edit.pack(side="left", padx=(U.px(8), 0))
        self.btn_fw_del = U.RoundButton(row2, "删除选中", self.fw_del,
                                        variant="danger", height=32, padx=14)
        self.btn_fw_del.pack(side="left", padx=(U.px(8), 0))
        self.btn_fw_toggle = U.RoundButton(row2, "启用 / 停用",
                                           self.fw_toggle, variant="secondary",
                                           height=32, padx=14)
        self.btn_fw_toggle.pack(side="left", padx=(U.px(8), 0))
        self.btn_fw_up = U.RoundButton(row2, "上移", self.fw_move_up,
                                       variant="ghost", height=32, padx=12)
        self.btn_fw_up.pack(side="left", padx=(U.px(8), 0))
        self.btn_fw_down = U.RoundButton(row2, "下移", self.fw_move_down,
                                         variant="ghost", height=32, padx=12)
        self.btn_fw_down.pack(side="left", padx=(U.px(6), 0))
        self.btn_fw_undo = U.RoundButton(row2, "撤销上一步", self.fw_undo,
                                         variant="ghost", height=32, padx=12)
        self.btn_fw_undo.pack(side="right")

        c2 = U.Card(pg, title="规则",
                    hint="双击编辑 · 右键更多 · 写操作前自动备份")
        c2.grid(row=1, column=0, sticky="nsew", pady=(U.px(10), 0))
        self.tbl_fw = DataTable(
            c2.body, COLS, height=10, searchable=True,
            empty_title="还没有读取规则",
            empty_hint="等自动探测完成，或点「刷新规则」。",
            on_activate=self.fw_edit)
        self.tbl_fw.pack(fill="both", expand=True)
        self.tbl_fw.set_menu([
            ("编辑这条规则", self.fw_edit, lambda r: bool(r)),
            ("启用 / 停用", self.fw_toggle, lambda r: bool(r)),
            ("上移", self.fw_move_up, lambda r: bool(r)),
            ("下移", self.fw_move_down, lambda r: bool(r)),
            (None, None, None),
            ("查看原始规则", self._fw_detail, lambda r: bool(r)),
            ("复制为命令", self._fw_copy_cmd, lambda r: bool(r)),
            (None, None, None),
            ("删除这条规则", self.fw_del, lambda r: bool(r)),
        ])
        self.tbl_fw.set_empty("还没有读取规则", "等自动探测完成，或点「刷新规则」。")

        self.fw_backend = None
        self._fw_raw = ""          # 最近一次读到的规则原文（备份用）
        self._fw_undo = None       # (backend, action, old, new)
        self._fw_set_writable(False)

    def _fw_set_writable(self, ok: bool) -> None:
        for btn, op in ((self.btn_fw_add, "add"), (self.btn_fw_edit, "edit"),
                        (self.btn_fw_del, "del"), (self.btn_fw_toggle, "toggle"),
                        (self.btn_fw_up, "move"), (self.btn_fw_down, "move")):
            be = self.fw_backend
            on = bool(ok and be is not None and fw.supports(be, op))
            try:
                btn.set_enabled(on)
            except Exception:  # noqa: BLE001
                pass
        try:
            self.btn_fw_undo.set_enabled(bool(ok and self._fw_undo))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 探测
    def fw_probe(self):
        s = self._active_session()
        dev = self._effective_device()
        self.badge_fw.set("探测中", "mute")
        self.status.set_state("探测防火墙后端…")
        self.lbl_fw.configure(text="正在试 群晖 → ufw → firewalld → nftables → iptables …",
                              fg=U.T.text_dim)

        def work():
            return fw.detect(s, dev)

        def done(res):
            backend, note = res
            self.fw_backend = backend
            if not backend:
                self.badge_fw.set("未知", "warn")
                self.lbl_fw.configure(text="没探到可用的防火墙：" + note,
                                      fg=U.T.warn)
                self.kk_fw.set_items([
                    ("后端", "未探测到", "warn"),
                    ("说明", note[:70], None),
                    ("可写", "—", None),
                ])
                self.tbl_fw.set_empty("这台设备上没有认识的后端",
                                      note[:300])
                self._fw_set_writable(False)
                self.status.set_state("没有可用后端", "warn")
                return
            self.badge_fw.set(backend.label, "ok" if backend.can_write else "warn")
            self.lbl_fw.configure(text=f"后端：{note}", fg=U.T.ok)
            self.status.set_state(f"已识别 {backend.label}", "ok")
            self.fw_refresh()

        self.bg(work, ok=done, err=lambda m: (
            self.badge_fw.set("探测失败", "err"),
            self.lbl_fw.configure(text="探测失败：" + m, fg=U.T.err),
            self.status.set_state(m, "err"),
            self._fw_set_writable(False)))

    def _fw_can_write(self, backend):
        if backend is None:
            return False, "未探测"
        if not backend.can_write:
            return False, "只读后端"
        try:
            s = self._active_session()
            if s is None:
                return False, "没有会话"
            return s.can_admin()
        except Exception:  # noqa: BLE001
            return False, "无法提权"

    def _ensure_backend(self):
        if self.fw_backend is None:
            self.fw_probe()
            return None
        return self.fw_backend

    # ------------------------------------------------------------ 读
    def fw_refresh(self):
        backend = self.fw_backend or self._ensure_backend()
        if backend is None:
            return
        s = self._active_session()
        if s is None:
            self.tbl_fw.set_empty("没有连接设备", "先在主机管理里连一台设备。")
            return
        backend.session = s
        self.status.set_state("读取防火墙规则…")

        def work():
            rc, out, err = s.run_traced(backend.read_cmd(), timeout=60)
            return backend, rc, out, err

        def done(res):
            backend, rc, out, err = res
            if rc != 0 and not out.strip():
                self.tbl_fw.set_empty("读取规则失败",
                                      (err or "命令返回空").strip()[:400])
                self.status.set_state("读取失败", "err")
                return
            self._fw_raw = out or ""
            rows = backend.parse(out)
            self.tbl_fw.set_rows(rows)
            self.tbl_fw.set_empty(
                "规则是空的",
                f"{backend.label} 没有返回任何规则。若确实配了规则，"
                "可能是权限不足（试试填写 sudo 密码）。")
            ok, why = self._fw_can_write(backend)
            self.kk_fw.set_items([
                ("后端", backend.label, "brand"),
                ("规则条数", str(len(rows)), None),
                ("允许", str(sum(1 for r in rows if r.get("_tone") == "ok")), "ok"),
                ("拒绝 / 丢弃", str(sum(1 for r in rows
                                     if r.get("_tone") in ("err", "warn"))), "warn"),
                ("可写", "是" if ok else why, "ok" if ok else "warn"),
                ("持久化", backend.persist_note()[:70] or "—", None),
            ])
            self._fw_set_writable(ok)
            self.status.set_state(f"规则已读取（{len(rows)} 条）", "ok")

        self.bg(work, ok=done, err=lambda m: (
            self.tbl_fw.set_empty("读取失败", m),
            self.status.set_state(m, "err")))

    # ------------------------------------------------------------ 表单
    def _fw_fields(self, backend, row=None) -> list[dict]:
        """按后端出字段；`row` 给了就是编辑，值回填。"""
        key = backend.key
        acts = _ACTIONS.get(key) or [("allow", "允许"), ("deny", "拒绝")]

        def d(name, default=""):
            if row is None:
                return default
            v = row.get(name)
            return default if v is None else v

        if key == "netsh":
            dir0 = str((row or {}).get("chain") or "").strip().lower()
            dir_default = "out" if dir0.startswith(("out", "出")) else "in"
            return [
                {"key": "name", "label": "规则名", "required": True,
                 "default": d("name") or "TerminalToolbox Rule", "width": 34},
                {"key": "direction", "label": "方向", "kind": "select",
                 "options": [("in", "入站"), ("out", "出站")],
                 "default": dir_default},
                {"key": "action", "label": "动作", "kind": "select",
                 "options": acts, "default": (row or {}).get("action") or "allow"},
                {"key": "proto", "label": "协议", "kind": "select",
                 "options": ["TCP", "UDP", "any"],
                 "default": str(d("proto") or "TCP").upper()
                 if str(d("proto") or "").upper() in ("TCP", "UDP", "ANY")
                 else "TCP"},
                {"key": "port", "label": "本地端口", "default": d("port"),
                 "hint": "22 或 80,443 或 1000-2000；any 协议留空"},
                {"key": "src", "label": "远程 IP", "kind": "iplist", "lines": 2,
                 "default": d("src") or "any",
                 "hint": "一个框可以填多个（逗号或换行分隔），IPv4/IPv6 都行；"
                         "点「地址库…」按省份/国家成批选",
                 "hint_below": True, "width": 32},
            ]
        if key == "ufw":
            return [
                {"key": "action", "label": "动作", "kind": "select",
                 "options": acts, "default": (row or {}).get("action") or "allow"},
                {"key": "port", "label": "端口", "default": d("port"),
                 "hint": "22 或 8080/tcp 或 80,443 或 1000:2000"},
                {"key": "proto", "label": "协议", "kind": "select",
                 "options": [("", "不限"), ("tcp", "TCP"), ("udp", "UDP")],
                 "default": (row or {}).get("proto") or ""},
                {"key": "src", "label": "来源地址", "kind": "iplist", "lines": 2,
                 "default": d("src") or "any",
                 "hint": "any 或 192.168.1.0/24；填多个会拆成多条命令；"
                         "IPv6 也认；点「地址库…」按省份/国家成批选",
                 "hint_below": True, "width": 32},
                {"key": "comment", "label": "备注", "default": d("comment")},
            ]
        if key == "firewalld":
            is_svc = (row or {}).get("_kind") == "service"
            return [
                {"key": "mode", "label": "放行方式", "kind": "select",
                 "options": [("port", "按端口"), ("service", "按服务名")],
                 "default": "service" if is_svc else "port"},
                {"key": "port", "label": "端口", "default": "" if is_svc else d("port"),
                 "hint": "8080；与下面的服务名二选一",
                 "show_if": {"mode": ["port"]}},
                {"key": "proto", "label": "端口协议", "kind": "select",
                 "options": [("tcp", "TCP"), ("udp", "UDP")],
                 "default": (row or {}).get("proto") or "tcp",
                 "show_if": {"mode": ["port"]}},
                {"key": "name", "label": "服务名", "default": d("_name") if is_svc else "",
                 "hint": "http / https / ssh（firewalld 自带的服务）",
                 "show_if": {"mode": ["service"]}},
                {"key": "src", "label": "来源地址", "kind": "iplist", "lines": 2,
                 "default": d("src") or "any",
                 "hint": "any 或 192.168.1.0/24（填了会生成 rich rule）；"
                         "可以填多个，IPv4/IPv6 自动分 family",
                 "hint_below": True, "width": 32},
            ]
        if key == "iptables":
            chain = (row or {}).get("chain") or "INPUT"
            return [
                {"key": "table", "label": "表", "kind": "select",
                 "options": ["filter", "nat"],
                 "default": (row or {}).get("table") or "filter"},
                {"key": "chain", "label": "链", "kind": "select",
                 "options": ["INPUT", "FORWARD", "OUTPUT", "PREROUTING",
                             "POSTROUTING"],
                 "default": chain if chain in ("INPUT", "FORWARD", "OUTPUT",
                                               "PREROUTING", "POSTROUTING")
                 else "INPUT"},
                {"key": "action", "label": "动作", "kind": "select",
                 "options": acts, "default": (row or {}).get("action") or "ACCEPT"},
                {"key": "proto", "label": "协议", "kind": "select",
                 "options": ["tcp", "udp", "icmp", "any"],
                 "default": (row or {}).get("proto") or "tcp"},
                {"key": "port", "label": "目标端口", "default": d("port"),
                 "hint": "22 或 80,443 或 1000-2000"},
                {"key": "src", "label": "来源地址", "kind": "iplist", "lines": 2,
                 "default": d("src") or "any",
                 "hint": "any 或 192.168.1.0/24；可以填多个（逗号/换行分隔），"
                         "会拆成多条命令；点「地址库…」按省份/国家成批选",
                 "hint_below": True, "width": 32},
                {"key": "dst", "label": "目标地址", "kind": "iplist", "lines": 2,
                 "default": d("dst") or "any",
                 "hint": "IPv4 / IPv6 都行；填多个会按地址族自动走 ip6tables",
                 "hint_below": True, "width": 32},
                {"key": "comment", "label": "备注", "default": d("comment")},
            ]
        if key == "synofirewall":
            # 群晖的规则表里没有「目标 IP」这一列 —— 目标恒为 any。
            # 所以这里刻意不出目标字段：出了就是骗人（填了存不进去）。
            return [
                {"key": "action", "label": "动作", "kind": "select",
                 "options": acts, "default": (row or {}).get("action") or "allow"},
                {"key": "proto", "label": "协议", "kind": "select",
                 "options": [("tcp", "TCP"), ("udp", "UDP"), ("", "全部")],
                 "default": (row or {}).get("proto") or "tcp"},
                {"key": "port", "label": "端口", "default":
                 "" if str(d("port")) in ("所有", "any") else d("port"),
                 "hint": "22 或 80,443 或 1000-2000；留空 = 所有端口。"
                         "群晖内置服务名（ssh / ntp 这类）请到 DSM 界面里选"},
                {"key": "src", "label": "来源地址", "kind": "iplist", "lines": 2,
                 "default": d("src") or "any",
                 "hint": "群晖的规则只有来源 IP，目标恒为 any（DSM 界面也不显示"
                         "目标这一列）。一条规则只带一个网段，填多个会拆成多条；"
                         "IPv6 在群晖上没验证过，不写",
                 "hint_below": True, "width": 32},
                {"key": "comment", "label": "备注", "default": d("comment")},
            ]
        if key == "ipfw":
            return [
                {"key": "action", "label": "动作", "kind": "select",
                 "options": acts, "default": (row or {}).get("action") or "allow"},
                {"key": "proto", "label": "协议", "kind": "select",
                 "options": ["tcp", "udp", "ip"],
                 "default": (row or {}).get("proto") or "tcp"},
                {"key": "src", "label": "来源", "kind": "iplist", "lines": 2,
                 "default": d("src") or "any",
                 "hint": "可以填多个，会拆成多条命令", "hint_below": True,
                 "width": 32},
                {"key": "dst", "label": "目标", "kind": "iplist", "lines": 2,
                 "default": d("dst") or "any", "width": 32},
                {"key": "port", "label": "目标端口", "default": d("port")},
            ]
        # nftables
        chain = (row or {}).get("chain") or "input"
        return [
            {"key": "family", "label": "地址族", "kind": "select",
             "options": ["inet", "ip", "ip6"],
             "default": (row or {}).get("family") or "inet"},
            {"key": "table", "label": "表", "default": (row or {}).get("table") or "filter"},
            {"key": "chain", "label": "链", "default": chain},
            {"key": "action", "label": "动作", "kind": "select",
             "options": acts, "default": (row or {}).get("action") or "accept"},
            {"key": "proto", "label": "协议", "kind": "select",
             "options": ["tcp", "udp", "icmp", "any"],
             "default": (row or {}).get("proto") or "tcp"},
            {"key": "port", "label": "目标端口", "default": d("port"),
             "hint": "22 或 80,443"},
            {"key": "src", "label": "来源地址", "kind": "iplist", "lines": 2,
             "default": d("src") or "any",
             "hint": "IPv4 / IPv6 都行（会按地址族自动写 ip / ip6）；"
                     "可以填多个，一条网段一条规则；点「地址库…」成批选",
             "hint_below": True, "width": 32},
            {"key": "dst", "label": "目标地址", "kind": "iplist", "lines": 2,
             "default": d("dst") or "any", "width": 32},
        ]

    # ------------------------------------------------------------ 写
    def fw_add(self):
        self._fw_open_form(None)

    def fw_edit(self, row=None):
        row = row if isinstance(row, dict) and row.get("ref") else \
            self.tbl_fw.selected()
        if not row:
            return self._warn("没有选中", "先在列表里选一条规则。")
        self._fw_open_form(row)

    def _fw_open_form(self, row):
        backend = self.fw_backend or self._ensure_backend()
        if backend is None:
            return
        ok, why = self._fw_can_write(backend)
        if not ok:
            return self._warn("不可写", f"{backend.label}：{why}")
        editing = row is not None
        data = forms.FormDialog.open(
            self, "编辑防火墙规则" if editing else "新增防火墙规则",
            self._fw_fields(backend, row),
            subtitle=f"后端：{backend.label}。保存后会先给你看命令，"
                     "确认了才执行。",
            submit="保存" if editing else "确定")
        if not data:
            return
        try:
            if editing:
                cmds = fw.plan(backend, "edit", data, old=row)
            else:
                cmds = fw.plan(backend, "add", data)
        except Exception as e:  # noqa: BLE001
            return self._warn("命令拼装失败", str(e))
        risk = "danger" if editing and self._is_self_lockout(backend, row) else None
        if not forms.confirm(
                self, "确认编辑规则" if editing else "确认新增规则",
                "即将执行：\n\n" + "\n".join(cmds) +
                f"\n\n{backend.persist_note()}\n\n继续吗？",
                danger=bool(risk), ok_text="执行"):
            return
        self._fw_apply("edit" if editing else "add", data,
                       "规则已修改" if editing else "规则已添加", old=row)

    def _is_self_lockout(self, backend, row) -> bool:
        """改的是「会把自己关在门外」的规则时加一层危险提示。

        不做严格判定（各后端字段差太多），只抓两类：
          · 拒绝**所有端口** —— 一定把自己断掉；
          · 拒绝**当前这条 SSH 连接用的端口** —— 群晖这类设备 SSH 端口
            经常被改成 2222/22222，只认 22 会漏（真机上就有一条 2222 的规则）。
        """
        try:
            port = str(row.get("port") or "")
            act = str(row.get("action") or "").lower()
            if act not in ("deny", "drop", "block", "reject"):
                return False
            if port.strip() in ("", "所有", "any", "全部"):
                return True
            try:
                cur = str(getattr(self._active_session(), "port", "") or "22")
            except Exception:  # noqa: BLE001
                cur = "22"
            if _port_hits(port, cur) or _port_hits(port, "22"):
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def fw_del(self, row=None):
        row = row if isinstance(row, dict) and row.get("ref") else \
            self.tbl_fw.selected()
        if not row:
            return self._warn("没有选中", "先在列表里选一条规则。")
        backend = self.fw_backend
        if backend is None:
            return
        ok, why = self._fw_can_write(backend)
        if not ok:
            return self._warn("不可写", f"{backend.label}：{why}")
        try:
            cmds = fw.plan(backend, "del", row)
        except Exception as e:  # noqa: BLE001
            return self._warn("无法删除", str(e))
        if not forms.confirm(
                self, "确认删除规则",
                f"规则：{(row.get('raw') or '')[:160]}\n\n即将执行：\n"
                + "\n".join(cmds) +
                "\n\n删错可能把自己关在门外，确认继续？",
                danger=True, ok_text="删除"):
            return
        self._fw_apply("del", row, "规则已删除", old=row)

    def fw_toggle(self, row=None):
        row = row if isinstance(row, dict) and row.get("ref") else \
            self.tbl_fw.selected()
        if not row:
            return self._warn("没有选中", "先在列表里选一条规则。")
        backend = self.fw_backend
        if backend is None:
            return
        if not fw.supports(backend, "toggle"):
            return self._warn("不支持",
                              f"{backend.label} 没有单条规则的启用/停用，"
                              "只能删除或调整顺序。")
        ok, why = self._fw_can_write(backend)
        if not ok:
            return self._warn("不可写", f"{backend.label}：{why}")
        enable = not bool(row.get("enabled"))
        try:
            cmds = fw.plan(backend, "toggle", row, enable=enable)
        except Exception as e:  # noqa: BLE001
            return self._warn("无法切换", str(e))
        verb = "启用" if enable else "停用"
        if not forms.confirm(self, f"确认{verb}规则",
                             f"规则：{(row.get('raw') or '')[:160]}\n\n"
                             "即将执行：\n" + "\n".join(cmds),
                             danger=not enable, ok_text=verb):
            return
        self._fw_apply("toggle", {**row, "enabled": enable},
                       f"规则已{verb}", old=row, enable=enable)

    def _fw_move(self, delta):
        row = self.tbl_fw.selected()
        if not row:
            return self._warn("没有选中", "先在列表里选一条规则。")
        backend = self.fw_backend
        if backend is None or not fw.supports(backend, "move"):
            return self._warn("不支持", "这个后端不能调顺序。")
        ok, why = self._fw_can_write(backend)
        if not ok:
            return self._warn("不可写", f"{backend.label}：{why}")
        try:
            cmds = getattr(backend, "move_cmds")(row, delta)
        except Exception as e:  # noqa: BLE001
            return self._warn("无法调整顺序", str(e))
        if not forms.confirm(self, "调整规则顺序",
                             "即将执行：\n" + "\n".join(cmds) +
                             "\n\n注意：先删后插，中间那一瞬间这条规则不生效。",
                             ok_text="执行"):
            return
        self._fw_apply("move", row, "顺序已调整", old=row)

    def fw_move_up(self, row=None):
        self._fw_move(-1)

    def fw_move_down(self, row=None):
        self._fw_move(1)

    # ------------------------------------------------------------ 执行
    def _fw_backup(self, backend) -> str:
        """写之前先把当前规则存一份，失败就中止（不带备份不乱改）。

        注意这里跑在后台线程里 —— 不能弹窗，只能抛，由 bg 的 err 收尾。
        """
        root = getattr(self, "data_dir", None)
        if not root:
            return ""
        try:
            p = fw.save_backup(root, backend.key, self._fw_raw or "")
        except fw.FirewallError as e:
            raise fw.FirewallError(f"{e}（没有备份就不动防火墙）") from e
        self._fw_last_backup = p
        return str(p)

    def _fw_apply(self, action, payload, ok_text, old=None, enable=True):
        backend = self.fw_backend
        s = self._active_session()
        if backend is None or s is None:
            return
        self.status.set_state("正在修改防火墙…")

        def work():
            bpath = self._fw_backup(backend)
            rc, out, err = fw.apply(s, backend, action, payload, old=old,
                                    enable=enable)
            return rc, out, err, bpath

        def done(res):
            rc, out, err, bpath = res
            desc = str(payload.get("raw") or payload.get("port") or
                       payload.get("name") or "")[:80]
            self._audit("fw.change", "ok" if rc == 0 else "fail",
                        f"{backend.label} · {action} · {desc}"
                        + (f" · 备份 {bpath}" if bpath else ""))
            if rc == 0:
                self.status.set_state(ok_text, "ok")
                self._fw_undo = (backend, action, old, dict(payload))
                self._fw_remember(backend, action, old, payload)
                self.fw_refresh()
                self._fw_highlight(payload)
            else:
                self.status.set_state(f"命令失败 rc={rc}", "err")
                self._warn("修改失败", (err or out or f"rc={rc}").strip()[:600])

        self.bg(work, ok=done, err=lambda m: (
            self.status.set_state(m, "err"), self._warn("修改失败", m)))

    def _fw_remember(self, backend, action, old, new):
        self._fw_undo = (backend, action, old, new)
        try:
            self.btn_fw_undo.set_enabled(True)
        except Exception:  # noqa: BLE001
            pass

    def _fw_highlight(self, row):
        """改完把那一行选出来 —— 不然一长串规则里根本不知道自己改的是哪条。"""
        try:
            key = row.get("port") or row.get("_name") or row.get("name") or ""
            if not key:
                return
            self.tbl_fw.select_row(lambda r: (r.get("port") or
                                              r.get("_name") or
                                              r.get("name") or "") == key)
        except Exception:  # noqa: BLE001
            pass

    def fw_undo(self):
        if not self._fw_undo:
            return self._warn("没有可撤销的", "还没改过任何规则。")
        backend, action, old, new = self._fw_undo
        if backend is not self.fw_backend:
            self._fw_undo = None
            return self._warn("换了设备", "上一台设备的改动不能在这里撤销。")
        try:
            cmds, why = fw.undo_cmds(backend, action, old, new)
        except Exception as e:  # noqa: BLE001
            cmds, why = [], str(e)
        if not cmds:
            return self._warn("撤不回来", f"{why}\n\n"
                              f"改动前的规则已备份在 data/fw_backup/，"
                              "可以照着里面的原文手工恢复。")
        if not forms.confirm(self, "撤销上一步",
                             f"{why}\n\n即将执行：\n" + "\n".join(cmds) +
                             "\n\n撤销本身也是一次改动，会再备份一次。",
                             danger=True, ok_text="撤销"):
            return
        s = self._active_session()

        def work():
            bpath = self._fw_backup(backend) if getattr(self, "data_dir", None) else ""
            rc, out, err = fw.run_cmds(s, cmds)
            return rc, out, err, bpath

        def done(res):
            rc, out, err, bpath = res
            self._audit("fw.change", "ok" if rc == 0 else "fail",
                        f"{backend.label} · undo · {why}"
                        + (f" · 备份 {bpath}" if bpath else ""))
            self._fw_undo = None
            try:
                self.btn_fw_undo.set_enabled(False)
            except Exception:  # noqa: BLE001
                pass
            if rc == 0:
                self.status.set_state("已撤销上一步", "ok")
                self.fw_refresh()
            else:
                self._warn("撤销失败", (err or out or f"rc={rc}").strip()[:600])

        self.bg(work, ok=done, err=lambda m: self._warn("撤销失败", m))

    # ------------------------------------------------------------ 备份
    def fw_show_backups(self):
        root = getattr(self, "data_dir", None)
        files = fw.list_backups(root) if root else []
        if not files:
            return forms.InfoDialog.show(
                self, "防火墙备份", "还没有备份。\n\n"
                "每次修改规则前会自动把当前规则原文存一份到 "
                "data/fw_backup/。", width=620, height=320)
        lines = []
        for p in files:
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            lines.append(f"{p.name}   {size} 字节")
        forms.InfoDialog.show(
            self, "防火墙备份",
            "修改前的规则原文（按时间倒序，最近 20 份）：\n\n"
            + "\n".join(lines)
            + f"\n\n目录：{fw.backup_dir(root)}", width=760, height=420)

    # ------------------------------------------------------------ 小动作
    def _fw_detail(self, row):
        if not row:
            return
        lines = [f"{k:<10}: {row.get(k, '')}" for k in
                 ("ref", "family", "table", "chain", "handle", "pos", "action",
                  "proto", "src", "dst", "port", "iface", "comment",
                  "enabled", "persistent")]
        body = "\n".join(lines) + f"\n\n原始规则：\n{row.get('raw', '')}"
        forms.InfoDialog.show(self, "规则详情", body,
                              subtitle=row.get("ref", ""), width=680, height=460)

    def _fw_copy_cmd(self, row):
        backend = self.fw_backend
        if not backend or not row:
            return
        try:
            cmd = "\n".join(fw.plan(backend, "del", row))
        except Exception:  # noqa: BLE001
            cmd = row.get("raw", "")
        try:
            self.clipboard_clear()
            self.clipboard_append(cmd)
            self.status.set_state("命令已复制到剪贴板", "ok")
        except tk.TclError:
            pass

    def _warn(self, title, msg):
        from tkinter import messagebox
        messagebox.showwarning(title, msg, parent=self)
