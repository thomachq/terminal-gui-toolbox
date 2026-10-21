# -*- coding: utf-8 -*-
"""防火墙页：后端探测 + 规则列表 + 增删（DSM 风格）。"""
from __future__ import annotations

import tkinter as tk

import wbtui as U
from core import firewall as fw
from ui import forms
from ui.table import Column, DataTable, KVGrid

COLS = [
    Column("action", "动作", 84, format=lambda v, r: fw.action_label(v)),
    Column("proto", "协议", 76, mono=True),
    Column("port", "端口", 92, mono=True),
    Column("src", "来源", 150, mono=True, stretch=True),
    Column("dst", "目标", 140, mono=True),
    Column("chain", "链 / 方向", 110),
    Column("comment", "备注", 160, stretch=True),
]


class FirewallPage:
    # ------------------------------------------------------------ 构建
    def build_fw(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        c = U.Card(pg, title="防火墙", hint="Linux nftables / iptables · Windows netsh · pf / ipfw")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        self.lbl_fw = U.wlabel(
            b, "点「探测后端」判断设备用哪套防火墙。", "dim", U.FONT_S,
            justify="left", wraplength=U.px(720))
        self.lbl_fw.pack(anchor="w")
        info = U.wframe(b, "card")
        info.pack(fill="x", pady=(U.px(10), 0))
        self.kk_fw = KVGrid(info, columns=3)
        self.kk_fw.pack(fill="x")

        row = U.wframe(b, "card")
        row.pack(fill="x", pady=(U.px(12), 0))
        U.RoundButton(row, "探测后端", self.fw_probe, variant="secondary",
                      height=32, padx=16).pack(side="left")
        U.RoundButton(row, "刷新规则", self.fw_refresh, variant="secondary",
                      height=32, padx=16).pack(side="left", padx=(U.px(8), 0))
        self.btn_fw_add = U.RoundButton(row, "新增规则", self.fw_add,
                                        variant="primary", height=32, padx=16)
        self.btn_fw_add.pack(side="left", padx=(U.px(8), 0))
        self.btn_fw_del = U.RoundButton(row, "删除选中", self.fw_del,
                                        variant="danger", height=32, padx=14)
        self.btn_fw_del.pack(side="left", padx=(U.px(8), 0))

        c2 = U.Card(pg, title="规则", hint="双击看原始规则 · 右键更多")
        c2.grid(row=1, column=0, sticky="nsew", pady=(U.px(10), 0))
        self.tbl_fw = DataTable(
            c2.body, COLS, height=11, searchable=True,
            empty_title="还没有读取规则",
            empty_hint="先「探测后端」，再「刷新规则」。",
            on_activate=self._fw_detail)
        self.tbl_fw.pack(fill="both", expand=True)
        self.tbl_fw.set_menu([
            ("查看原始规则", self._fw_detail, lambda r: bool(r)),
            ("复制为命令", self._fw_copy_cmd, lambda r: bool(r)),
            (None, None, None),
            ("删除这条规则", self.fw_del, lambda r: bool(r)),
        ])

        self.fw_backend = None

    # ------------------------------------------------------------ 探测
    def fw_probe(self):
        s = self._active_session()
        dev = self._effective_device()
        self.status.set_state("探测防火墙后端…")
        self.lbl_fw.configure(text="正在探测 nftables / iptables / netsh / pf …",
                              fg=U.T.text_dim)

        def work():
            return fw.detect(s, dev)

        def done(res):
            backend, note = res
            self.fw_backend = backend
            if not backend:
                self.lbl_fw.configure(text="未找到可用的防火墙后端：" + note,
                                      fg=U.T.warn)
                self.kk_fw.set_items([
                    ("后端", "未探测到", "warn"),
                    ("说明", note[:80], None),
                    ("可写", "—", None),
                ])
                self.btn_fw_add.set_enabled(False)
                self.btn_fw_del.set_enabled(False)
                self.status.set_state("没有可用后端", "warn")
                return
            self.lbl_fw.configure(text=f"后端：{note}", fg=U.T.ok)
            ok, why = self._fw_can_write(backend)
            self.btn_fw_add.set_enabled(ok)
            self.btn_fw_del.set_enabled(ok)
            self.kk_fw.set_items([
                ("后端", backend.label, "brand"),
                ("可写", "是" if ok else why, "ok" if ok else "warn"),
                ("持久化", backend.persist_note()[:70] or "—", None),
            ])
            self.status.set_state(f"已识别 {backend.label}", "ok")
            self.fw_refresh()

        self.bg(work, ok=done, err=lambda m: (
            self.lbl_fw.configure(text="探测失败：" + m, fg=U.T.err),
            self.status.set_state(m, "err")))

    def _fw_can_write(self, backend):
        if not backend.can_write:
            return False, "只读后端"
        try:
            return self._active_session().can_admin()
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
            rows = backend.parse(out)
            self.tbl_fw.set_rows(rows)
            self.tbl_fw.set_empty(
                "规则是空的",
                f"{backend.label} 没有返回任何规则。若确实配了规则，"
                "可能是权限不足（试试填写 sudo 密码）。")
            ok, why = self._fw_can_write(backend)
            self.btn_fw_add.set_enabled(ok)
            self.btn_fw_del.set_enabled(ok)
            self.kk_fw.set_items([
                ("后端", backend.label, "brand"),
                ("规则条数", str(len(rows)), None),
                ("允许", str(sum(1 for r in rows if r.get("_tone") == "ok")), "ok"),
                ("拒绝 / 丢弃", str(sum(1 for r in rows
                                     if r.get("_tone") in ("err", "warn"))), "warn"),
                ("可写", "是" if ok else why, "ok" if ok else "warn"),
                ("持久化", backend.persist_note()[:70] or "—", None),
            ])
            self.status.set_state(f"规则已读取（{len(rows)} 条）", "ok")

        self.bg(work, ok=done, err=lambda m: (
            self.tbl_fw.set_empty("读取失败", m),
            self.status.set_state(m, "err")))

    # ------------------------------------------------------------ 写
    def _fw_fields(self, backend):
        key = backend.key
        if key == "netsh":
            return [
                {"key": "name", "label": "规则名", "required": True,
                 "default": "TerminalToolbox Rule", "width": 34},
                {"key": "direction", "label": "方向", "kind": "select",
                 "options": [("in", "入站"), ("out", "出站")], "default": "in"},
                {"key": "action", "label": "动作", "kind": "select",
                 "options": [("allow", "允许"), ("block", "阻止")],
                 "default": "allow"},
                {"key": "proto", "label": "协议", "kind": "select",
                 "options": ["TCP", "UDP", "any"], "default": "TCP"},
                {"key": "port", "label": "本地端口", "default": "",
                 "hint": "22 或 80,443 或 1000-2000；any 协议留空"},
                {"key": "src", "label": "远程 IP", "default": "any",
                 "hint": "any 或 192.168.1.0/24"},
            ]
        if key == "iptables":
            return [
                {"key": "table", "label": "表", "kind": "select",
                 "options": ["filter", "nat"], "default": "filter"},
                {"key": "chain", "label": "链", "kind": "select",
                 "options": ["INPUT", "FORWARD", "OUTPUT", "PREROUTING",
                             "POSTROUTING"], "default": "INPUT"},
                {"key": "action", "label": "动作", "kind": "select",
                 "options": [("ACCEPT", "允许"), ("DROP", "丢弃"),
                             ("REJECT", "拒绝")], "default": "ACCEPT"},
                {"key": "proto", "label": "协议", "kind": "select",
                 "options": ["tcp", "udp", "icmp", "any"], "default": "tcp"},
                {"key": "port", "label": "目标端口", "default": "",
                 "hint": "22 或 80,443 或 1000-2000"},
                {"key": "src", "label": "来源地址", "default": "any"},
                {"key": "dst", "label": "目标地址", "default": "any"},
                {"key": "comment", "label": "备注", "default": ""},
            ]
        if key == "ipfw":
            return [
                {"key": "action", "label": "动作", "kind": "select",
                 "options": [("allow", "允许"), ("deny", "拒绝")],
                 "default": "allow"},
                {"key": "proto", "label": "协议", "kind": "select",
                 "options": ["tcp", "udp", "ip"], "default": "tcp"},
                {"key": "src", "label": "来源", "default": "any"},
                {"key": "dst", "label": "目标", "default": "any"},
                {"key": "port", "label": "目标端口", "default": ""},
            ]
        # nftables
        return [
            {"key": "family", "label": "地址族", "kind": "select",
             "options": ["inet", "ip", "ip6"], "default": "inet"},
            {"key": "table", "label": "表", "default": "filter"},
            {"key": "chain", "label": "链", "kind": "select",
             "options": ["input", "forward", "output"], "default": "input"},
            {"key": "action", "label": "动作", "kind": "select",
             "options": [("accept", "允许"), ("drop", "丢弃"),
                         ("reject", "拒绝")], "default": "accept"},
            {"key": "proto", "label": "协议", "kind": "select",
             "options": ["tcp", "udp", "icmp", "any"], "default": "tcp"},
            {"key": "port", "label": "目标端口", "default": "",
             "hint": "22 或 80,443"},
            {"key": "src", "label": "来源地址", "default": "any"},
            {"key": "dst", "label": "目标地址", "default": "any"},
        ]

    def fw_add(self):
        backend = self.fw_backend or self._ensure_backend()
        if backend is None:
            return
        ok, why = self._fw_can_write(backend)
        if not ok:
            return self._warn("不可写", f"{backend.label}：{why}")
        fields = self._fw_fields(backend)
        data = forms.FormDialog.open(
            self, "新增防火墙规则", fields,
            subtitle=f"后端：{backend.label}。"
                     "命令会先给你确认，再执行。")
        if not data:
            return
        try:
            cmds = backend.add_cmds(data)
        except Exception as e:  # noqa: BLE001
            return self._warn("命令拼装失败", str(e))
        if not forms.confirm(
                self, "确认新增规则",
                "即将执行：\n\n" + "\n".join(cmds) +
                f"\n\n{backend.persist_note()}\n\n继续吗？"):
            return
        self._fw_apply("add", data, "规则已添加")

    def fw_del(self):
        row = self.tbl_fw.selected()
        if not row:
            return self._warn("没有选中", "先在列表里选一条规则。")
        backend = self.fw_backend
        if backend is None:
            return
        ok, why = self._fw_can_write(backend)
        if not ok:
            return self._warn("不可写", f"{backend.label}：{why}")
        try:
            cmds = backend.del_cmds(row)
        except Exception as e:  # noqa: BLE001
            return self._warn("无法删除", str(e))
        if not forms.confirm(
                self, "确认删除规则",
                f"规则：{row.get('raw','')[:160]}\n\n即将执行：\n" + "\n".join(cmds) +
                "\n\n删错可能把自己关在门外，确认继续？",
                danger=True, ok_text="删除"):
            return
        self._fw_apply("del", row, "规则已删除")

    def _fw_apply(self, action, payload, ok_text):
        backend = self.fw_backend
        s = self._active_session()
        self.status.set_state("正在修改防火墙…")

        def work():
            return fw.apply(s, backend, action, payload)

        def done(res):
            rc, out, err = res
            if rc == 0:
                self.status.set_state(ok_text, "ok")
                self.fw_refresh()
            else:
                self.status.set_state(f"命令失败 rc={rc}", "err")
                self._warn("修改失败", (err or out or f"rc={rc}").strip()[:600])

        self.bg(work, ok=done, err=lambda m: (
            self.status.set_state(m, "err"), self._warn("修改失败", m)))

    # ------------------------------------------------------------ 小动作
    def _fw_detail(self, row):
        if not row:
            return
        lines = [f"{k:<10}: {row.get(k, '')}" for k in
                 ("ref", "family", "table", "chain", "handle", "action",
                  "proto", "src", "dst", "port", "iface", "comment",
                  "enabled", "persistent")]
        body = "\n".join(lines) + f"\n\n原始规则：\n{row.get('raw','')}"
        forms.InfoDialog.show(self, "规则详情", body,
                              subtitle=row.get("ref", ""), width=680, height=460)

    def _fw_copy_cmd(self, row):
        backend = self.fw_backend
        if not backend or not row:
            return
        try:
            cmd = backend.del_cmds(row)[0]
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
