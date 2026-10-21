# -*- coding: utf-8 -*-
"""路由表页（DSM 风格：列表 + 工具条 + 表单弹窗改配置）。"""
from __future__ import annotations

import tkinter as tk

import wbtui as U
from core import routes
from ui import forms
from ui.table import Column, DataTable, KVGrid

COLS = [
    Column("dst", "目标网段", 190, mono=True, stretch=True),
    Column("gateway", "网关", 140, mono=True),
    Column("dev", "接口 / 出站", 130, mono=True),
    Column("metric", "跃点", 64, anchor="center"),
    Column("proto", "来源", 90),
    Column("kind", "类型", 90),
]


class RoutesPage:
    # ------------------------------------------------------------ 构建
    def build_routes(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        c = U.Card(pg, title="路由表", hint="按探测到的系统选择 ip route / route / netstat")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        self.lbl_route = U.wlabel(
            b, "点「刷新」读取设备当前路由表。", "dim", U.FONT_S, justify="left",
            wraplength=U.px(720))
        self.lbl_route.pack(anchor="w")

        info = U.wframe(b, "card")
        info.pack(fill="x", pady=(U.px(10), 0))
        self.kk_route = KVGrid(info, columns=3)
        self.kk_route.pack(fill="x")

        row = U.wframe(b, "card")
        row.pack(fill="x", pady=(U.px(12), 0))
        U.RoundButton(row, "刷新", self.routes_refresh, variant="secondary",
                      height=32, padx=16).pack(side="left")
        U.RoundButton(row, "新增路由", self.route_add, variant="primary",
                      height=32, padx=16).pack(side="left", padx=(U.px(8), 0))
        self.btn_route_del = U.RoundButton(row, "删除选中", self.route_del,
                                           variant="danger", height=32, padx=14)
        self.btn_route_del.pack(side="left", padx=(U.px(8), 0))

        c2 = U.Card(pg, title="当前路由", hint="双击查看原始行 · 右键更多操作")
        c2.grid(row=1, column=0, sticky="nsew", pady=(U.px(10), 0))
        self.tbl_route = DataTable(
            c2.body, COLS, height=10, searchable=True,
            empty_title="还没有读取路由表",
            empty_hint="点上面的「刷新」，或先连接设备再刷新。",
            on_activate=self._route_detail,
        )
        self.tbl_route.pack(fill="both", expand=True)
        self.tbl_route.set_menu([
            ("查看原始行", self._route_detail, lambda r: bool(r)),
            ("复制目标网段", self._route_copy, lambda r: bool(r)),
            (None, None, None),
            ("删除这条路由", self.route_del, lambda r: bool(r)),
        ])

    # ------------------------------------------------------------ 读
    def routes_refresh(self):
        s = self._active_session()
        be = routes.backend_of(self._effective_device(), s)
        self.lbl_route.configure(text=f"后端：{be.label} · 正在读取…",
                                 fg=U.T.text_dim)
        self.status.set_state("读取路由表…")

        def work():
            return be, be.reload(timeout=40)

        def done(res):
            backend, rows = res
            for r in rows:
                r["kind"] = "默认" if routes.is_default(r) else (
                    "直连" if not r.get("gateway") else "静态")
                r["_tone"] = r.get("_tone") or (
                    "brand" if routes.is_default(r) else "")
            self.tbl_route.set_rows(rows)
            self.tbl_route.set_empty(
                "没有读到路由",
                f"后端 {backend.label} 返回为空。原始输出见右侧日志。")
            ok, why = self._can_write(backend)
            self.btn_route_del.set_enabled(ok)
            self.kk_route.set_items([
                ("后端", backend.label, "brand"),
                ("路由条数", str(len(rows)), None),
                ("默认路由", next((r["gateway"] for r in rows
                                   if routes.is_default(r)), "无"), None),
                ("可写", "是" if ok else why, "ok" if ok else "warn"),
                ("持久化", "改动只在本次运行有效" if "Windows" not in backend.label
                 else "勾选永久后写注册表", None),
            ])
            self.lbl_route.configure(
                text=f"后端：{backend.label} · {len(rows)} 条路由 · {backend.persist_note()}",
                fg=U.T.text_dim)
            self.status.set_state(f"路由表已读取（{len(rows)} 条）", "ok")

        def bad(m):
            self.lbl_route.configure(text="读取失败：" + m, fg=U.T.err)
            self.tbl_route.set_empty("读取路由表失败", m)
            self.status.set_state(m, "err")

        self.bg(work, ok=done, err=bad)

    def _can_write(self, backend):
        if not backend.can_write:
            return False, "该后端只读"
        s = self._active_session()
        try:
            return s.can_admin()
        except Exception:  # noqa: BLE001
            return False, "无法提权"

    # ------------------------------------------------------------ 写
    def route_add(self):
        be = routes.backend_of(self._effective_device(), self._active_session())
        if not be.can_write:
            return forms.confirm(self, "不可写", f"{be.label} 不支持在界面里改路由。",
                                 danger=False)
        fields = [
            {"key": "dst", "label": "目标网段", "required": True,
             "default": "0.0.0.0/0", "hint": "192.168.1.0/24 · 10.0.0.5/32 · default",
             "pattern": (r"^(default|[\d.]+(/\d+)?)$",
                         "写法：192.168.1.0/24 或 default")},
            {"key": "gateway", "label": "网关", "required": True,
             "hint": "下一跳 IP，例如 192.168.1.1"},
            {"key": "dev", "label": "接口", "default": "",
             "hint": "Linux 网卡名（eth0），Windows 可留空或填接口号"},
            {"key": "metric", "label": "跃点", "default": "", "kind": "int",
             "min": 0, "max": 9999, "hint": "越小优先级越高，留空用系统默认"},
        ]
        if "Windows" in be.label:
            fields.append({"key": "persistent", "label": "重启后保留（route -p）",
                           "kind": "check", "default": True})
        data = forms.FormDialog.open(
            self, "新增路由", fields,
            subtitle=f"将执行：{be.label} 的添加命令，需要管理员 / sudo 权限")
        if not data:
            return
        data["dst"] = routes.clean_cidr(data.get("dst", ""))
        try:
            preview = be.add_cmds({**data, "metric": data.get("metric") or ""})
        except Exception as e:  # noqa: BLE001
            return self._warn("命令拼装失败", str(e))
        if not forms.confirm(
                self, "确认新增路由",
                "即将执行：\n\n" + "\n".join(preview) +
                f"\n\n{be.persist_note()}\n\n继续吗？"):
            return
        self._route_apply(be, "add", data, "已新增路由")

    def route_del(self):
        row = self.tbl_route.selected()
        if not row:
            return self._warn("没有选中", "先在列表里选一条路由。")
        be = routes.backend_of(self._effective_device(), self._active_session())
        if not be.can_write:
            return self._warn("不可写", f"{be.label} 不支持在界面里改路由。")
        try:
            cmds = be.del_cmds(row)
        except Exception as e:  # noqa: BLE001
            return self._warn("无法删除", str(e))
        if not forms.confirm(
                self, "确认删除路由",
                f"目标网段：{row.get('dst')}\n网关：{row.get('gateway') or '直连'}\n\n"
                "即将执行：\n" + "\n".join(cmds) + "\n\n删除可能立刻断掉正在用的连接。",
                danger=True, ok_text="删除"):
            return
        self._route_apply(be, "del", row, "已删除路由")

    def _route_apply(self, backend, action, payload, ok_text):
        s = self._active_session()
        self.status.set_state("正在修改路由…")

        def work():
            return routes.apply(s, backend, action, payload)

        def done(res):
            rc, out, err = res
            if rc == 0:
                self.status.set_state(ok_text, "ok")
                self.routes_refresh()
            else:
                self.status.set_state(f"命令失败 rc={rc}", "err")
                self._warn("修改失败", (err or out or f"rc={rc}").strip()[:600])

        self.bg(work, ok=done, err=lambda m: (
            self.status.set_state(m, "err"), self._warn("修改失败", m)))

    # ------------------------------------------------------------ 小动作
    def _route_detail(self, row):
        if not row:
            return
        body = (f"目标网段 : {row.get('dst','')}\n"
                f"网关     : {row.get('gateway') or '直连'}\n"
                f"接口     : {row.get('dev','')}\n"
                f"跃点     : {row.get('metric','')}\n"
                f"来源     : {row.get('proto','')}\n"
                f"标志     : {row.get('flags','')}\n"
                f"源地址   : {row.get('src','')}\n\n"
                f"原始行   :\n{row.get('raw') or row.get('dst','')}")
        forms.InfoDialog.show(self, "路由详情", body,
                              subtitle=f"{row.get('dev','')} · 跃点 {row.get('metric','')}",
                              width=620, height=420)

    def _route_copy(self, row):
        if not row:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(str(row.get("dst", "")))
            self.status.set_state("已复制目标网段", "ok")
        except tk.TclError:
            pass

    def _warn(self, title, msg):
        from tkinter import messagebox
        messagebox.showwarning(title, msg, parent=self)


def os_note(row: dict) -> str:
    return f"{row.get('dev','')} · {row.get('metric','')}"
