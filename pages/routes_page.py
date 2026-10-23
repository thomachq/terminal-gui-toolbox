# -*- coding: utf-8 -*-
"""路由表页（DSM 风格：列表 + 工具条 + 表单弹窗改配置）。

能读就能改：新增 / 编辑 / 删除 / 设为默认，四个动作都翻译成当前系统的原生命令
（Linux `ip route add|del|replace`、Windows `route add|delete -p`、
BSD `route -n add|delete|change`、网络设备 `ip route` / `ip route-static`）。

写操作前把路由表原文备份到 `data/route_backup/`，跑完进审计，
并留一条「撤销上一步」——撤销命令同样先给人看再跑。
"""
from __future__ import annotations

import tkinter as tk

import wbtui as U
from core import firewall as fw
from core import routes
from ui import forms
from ui.table import Column, DataTable, KVGrid

COLS = [
    Column("dst", "目标网段", 180, mono=True, stretch=True),
    Column("gateway", "网关", 140, mono=True),
    Column("dev", "接口 / 出站", 130, mono=True),
    Column("metric", "跃点", 64, anchor="center"),
    Column("proto", "来源", 90),
    Column("kind", "类型", 80),
]


class RoutesPage:
    # ------------------------------------------------------------ 构建
    def build_routes(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        c = U.Card(pg, title="路由表",
                   hint="按探测到的系统选择 ip route / route / netstat")
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
        self.btn_route_add = U.RoundButton(row, "新增路由", self.route_add,
                                           variant="primary", height=32, padx=16)
        self.btn_route_add.pack(side="left", padx=(U.px(8), 0))
        self.btn_route_edit = U.RoundButton(row, "编辑", self.route_edit,
                                            variant="secondary", height=32,
                                            padx=14)
        self.btn_route_edit.pack(side="left", padx=(U.px(8), 0))
        self.btn_route_del = U.RoundButton(row, "删除选中", self.route_del,
                                           variant="danger", height=32, padx=14)
        self.btn_route_del.pack(side="left", padx=(U.px(8), 0))
        self.btn_route_def = U.RoundButton(row, "设为默认", self.route_default,
                                           variant="secondary", height=32,
                                           padx=14)
        self.btn_route_def.pack(side="left", padx=(U.px(8), 0))
        self.btn_route_undo = U.RoundButton(row, "撤销上一步", self.route_undo,
                                            variant="ghost", height=32, padx=12)
        self.btn_route_undo.pack(side="right")

        c2 = U.Card(pg, title="当前路由", hint="双击编辑 · 右键更多 · 写操作前自动备份")
        c2.grid(row=1, column=0, sticky="nsew", pady=(U.px(10), 0))
        self.tbl_route = DataTable(
            c2.body, COLS, height=10, searchable=True,
            empty_title="还没有读取路由表",
            empty_hint="点上面的「刷新」，或先连接设备再刷新。",
            on_activate=self.route_edit,
        )
        self.tbl_route.pack(fill="both", expand=True)
        self.tbl_route.set_menu([
            ("编辑这条路由", self.route_edit, lambda r: bool(r)),
            ("设为默认路由", self.route_default, lambda r: bool(r)),
            (None, None, None),
            ("查看原始行", self._route_detail, lambda r: bool(r)),
            ("复制目标网段", self._route_copy, lambda r: bool(r)),
            (None, None, None),
            ("删除这条路由", self.route_del, lambda r: bool(r)),
        ])
        self._routes_raw = ""
        self._routes_undo = None
        self._route_set_writable(False)

    def _route_set_writable(self, ok: bool) -> None:
        for btn in (self.btn_route_add, self.btn_route_edit,
                    self.btn_route_del, self.btn_route_def):
            try:
                btn.set_enabled(bool(ok))
            except Exception:  # noqa: BLE001
                pass
        try:
            self.btn_route_undo.set_enabled(bool(ok and self._routes_undo))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 读
    def routes_refresh(self):
        s = self._active_session()
        be = routes.backend_of(self._effective_device(), s)
        self.lbl_route.configure(text=f"后端：{be.label} · 正在读取…",
                                 fg=U.T.text_dim)
        self.status.set_state("读取路由表…")

        def work():
            rc, out, err = s.run_traced(be.read_cmd(), timeout=40)
            return be, rc, out, err

        def ok(res):
            backend, rc, out, err = res
            if rc != 0 and not out.strip():
                return bad((err or "读取路由表失败").strip())
            self._routes_raw = out or ""
            rows = backend.parse(out)
            for r in rows:
                r["kind"] = "默认" if routes.is_default(r) else (
                    "直连" if not r.get("gateway") else "静态")
                r["_tone"] = r.get("_tone") or (
                    "brand" if routes.is_default(r) else "")
            self.tbl_route.set_rows(rows)
            self.tbl_route.set_empty(
                "没有读到路由",
                f"后端 {backend.label} 返回为空。原始输出见右侧日志。")
            can, why = self._can_write(backend)
            self._route_set_writable(can)
            self.kk_route.set_items([
                ("后端", backend.label, "brand"),
                ("路由条数", str(len(rows)), None),
                ("默认路由", next((r["gateway"] for r in rows
                                   if routes.is_default(r)), "无"), None),
                ("可写", "是" if can else why, "ok" if can else "warn"),
                ("持久化", backend.persist_note()[:60], None),
            ])
            self.lbl_route.configure(
                text=f"后端：{backend.label} · {len(rows)} 条路由 · "
                     f"{backend.persist_note()}", fg=U.T.text_dim)
            self.status.set_state(f"路由表已读取（{len(rows)} 条）", "ok")

        def bad(m):
            self.lbl_route.configure(text="读取失败：" + str(m), fg=U.T.err)
            self.tbl_route.set_empty("读取路由表失败", str(m))
            self.status.set_state(str(m), "err")

        self.bg(work, ok=ok, err=bad)

    def _can_write(self, backend):
        if not backend.can_write:
            return False, "该后端只读"
        s = self._active_session()
        try:
            if s is None:
                return False, "没有会话"
            return s.can_admin()
        except Exception:  # noqa: BLE001
            return False, "无法提权"

    # ------------------------------------------------------------ 表单
    def _route_fields(self, be, row=None) -> list[dict]:
        def d(k, default=""):
            if row is None:
                return default
            v = row.get(k)
            return default if v is None else v

        win = "Windows" in be.label
        fields = [
            {"key": "dst", "label": "目标网段", "required": True,
             "default": d("dst") or "0.0.0.0/0",
             "hint": "192.168.1.0/24 · 10.0.0.5/32 · default",
             "pattern": (r"^(default|[\d.]+(/\d+)?)$",
                         "写法：192.168.1.0/24 或 default")},
            {"key": "gateway", "label": "网关", "required": True,
             "default": d("gateway"), "hint": "下一跳 IP，例如 192.168.1.1"},
            {"key": "dev", "label": "接口", "default": d("dev"),
             "hint": "Linux 网卡名（eth0），Windows 可留空或填接口号"},
            {"key": "metric", "label": "跃点", "default": d("metric"),
             "kind": "int", "min": 0, "max": 9999,
             "hint": "越小优先级越高，留空用系统默认"},
        ]
        if win:
            fields.append({"key": "persistent", "label": "重启后保留（route -p）",
                           "kind": "check",
                           "default": bool(d("persistent", True))})
        return fields

    # ------------------------------------------------------------ 写
    def route_add(self):
        be = routes.backend_of(self._effective_device(), self._active_session())
        if not be.can_write:
            return self._warn("不可写", f"{be.label} 不支持在界面里改路由。")
        data = forms.FormDialog.open(
            self, "新增路由", self._route_fields(be),
            subtitle=f"将执行：{be.label} 的添加命令，需要管理员 / sudo 权限")
        if not data:
            return
        data["dst"] = routes.clean_cidr(data.get("dst", ""))
        try:
            preview = routes.plan(be, "add", data)
        except Exception as e:  # noqa: BLE001
            return self._warn("命令拼装失败", str(e))
        if not forms.confirm(
                self, "确认新增路由",
                "即将执行：\n\n" + "\n".join(preview) +
                f"\n\n{be.persist_note()}\n\n继续吗？"):
            return
        self._route_apply(be, "add", data, "已新增路由")

    def route_edit(self, row=None):
        row = row if isinstance(row, dict) and row.get("dst") else \
            self.tbl_route.selected()
        if not row:
            return self._warn("没有选中", "先在列表里选一条路由。")
        be = routes.backend_of(self._effective_device(), self._active_session())
        if not be.can_write:
            return self._warn("不可写", f"{be.label} 不支持在界面里改路由。")
        ok, why = self._can_write(be)
        if not ok:
            return self._warn("不可写", f"{be.label}：{why}")
        data = forms.FormDialog.open(
            self, "编辑路由", self._route_fields(be, row),
            values={"dst": row.get("dst"), "gateway": row.get("gateway"),
                    "dev": row.get("dev"), "metric": row.get("metric")},
            subtitle=f"后端：{be.label}。改一条路由 = 删旧的 + 加新的，"
                     "断开瞬间这条路由不生效。",
            submit="保存")
        if not data:
            return
        data["dst"] = routes.clean_cidr(data.get("dst", ""))
        try:
            cmds = routes.plan(be, "edit", data, old=row)
        except Exception as e:  # noqa: BLE001
            return self._warn("命令拼装失败", str(e))
        danger = routes.is_default(row) or routes.is_default(data)
        if not forms.confirm(
                self, "确认修改路由",
                f"原：{row.get('dst')} → {row.get('gateway') or '直连'}\n"
                f"新：{data.get('dst')} → {data.get('gateway')}\n\n"
                "即将执行：\n" + "\n".join(cmds) +
                f"\n\n{be.persist_note()}\n\n继续吗？",
                danger=danger, ok_text="保存"):
            return
        self._route_apply(be, "edit", data, "路由已修改", old=row)

    def route_del(self, row=None):
        row = row if isinstance(row, dict) and row.get("dst") else \
            self.tbl_route.selected()
        if not row:
            return self._warn("没有选中", "先在列表里选一条路由。")
        be = routes.backend_of(self._effective_device(), self._active_session())
        if not be.can_write:
            return self._warn("不可写", f"{be.label} 不支持在界面里改路由。")
        try:
            cmds = routes.plan(be, "del", row)
        except Exception as e:  # noqa: BLE001
            return self._warn("无法删除", str(e))
        if not forms.confirm(
                self, "确认删除路由",
                f"目标网段：{row.get('dst')}\n网关：{row.get('gateway') or '直连'}\n\n"
                "即将执行：\n" + "\n".join(cmds) + "\n\n删除可能立刻断掉正在用的连接。",
                danger=True, ok_text="删除"):
            return
        self._route_apply(be, "del", row, "已删除路由", old=row)

    def route_default(self, row=None):
        row = row if isinstance(row, dict) and row.get("dst") else \
            self.tbl_route.selected()
        if not row:
            return self._warn("没有选中", "先选一条有网关的路由。")
        be = routes.backend_of(self._effective_device(), self._active_session())
        if not be.can_write:
            return self._warn("不可写", f"{be.label} 不支持在界面里改路由。")
        try:
            cmds = routes.plan(be, "default", row)
        except Exception as e:  # noqa: BLE001
            return self._warn("无法设为默认", str(e))
        if not forms.confirm(
                self, "设为默认路由",
                f"把 {row.get('gateway')} 设为默认网关（原来那条会被替换）。\n\n"
                "即将执行：\n" + "\n".join(cmds) +
                "\n\n换错默认网关会立刻断网。",
                danger=True, ok_text="设为默认"):
            return
        self._route_apply(be, "default", {**row, "dst": "0.0.0.0/0"},
                          "已设为默认路由", old=row)

    # ------------------------------------------------------------ 执行
    def _route_backup(self, be) -> str:
        """写之前备份路由表原文。跑在后台线程里，失败只能抛，不能弹窗。"""
        root = getattr(self, "data_dir", None)
        if not root:
            return ""
        try:
            p = fw.save_backup(root, be.key, self._routes_raw or "", tag="route")
        except fw.FirewallError as e:
            raise fw.FirewallError(f"{e}（没有备份就不动路由表）") from e
        return str(p)

    def _route_apply(self, backend, action, payload, ok_text, old=None):
        s = self._active_session()
        if s is None:
            return
        self.status.set_state("正在修改路由…")

        def work():
            bpath = self._route_backup(backend)
            rc, out, err = routes.apply(s, backend, action, payload, old=old)
            return rc, out, err, bpath

        def done(res):
            rc, out, err, bpath = res
            self._audit("route.change", "ok" if rc == 0 else "fail",
                        f"{backend.label} · {action} · "
                        f"{str(payload.get('dst') or '')[:60]}"
                        + (f" · 备份 {bpath}" if bpath else ""))
            if rc == 0:
                self.status.set_state(ok_text, "ok")
                self._routes_undo = (backend, action, old, dict(payload))
                try:
                    self.btn_route_undo.set_enabled(True)
                except Exception:  # noqa: BLE001
                    pass
                self.routes_refresh()
                self._route_highlight(payload)
            else:
                self.status.set_state(f"命令失败 rc={rc}", "err")
                self._warn("修改失败", (err or out or f"rc={rc}").strip()[:600])

        self.bg(work, ok=done, err=lambda m: (
            self.status.set_state(m, "err"), self._warn("修改失败", m)))

    def _route_highlight(self, row):
        dst = str(row.get("dst") or "")
        if not dst:
            return
        try:
            self.tbl_route.select_row({"dst": dst})
        except Exception:  # noqa: BLE001
            pass

    def route_undo(self):
        if not self._routes_undo:
            return self._warn("没有可撤销的", "还没改过任何路由。")
        backend, action, old, new = self._routes_undo
        try:
            cmds, why = routes.undo_cmds(backend, action, old, new)
        except Exception as e:  # noqa: BLE001
            cmds, why = [], str(e)
        if not cmds:
            return self._warn("撤不回来", f"{why}\n\n改动前的路由表已备份在 "
                              "data/route_backup/。")
        if not forms.confirm(self, "撤销上一步",
                             f"{why}\n\n即将执行：\n" + "\n".join(cmds) +
                             "\n\n撤销本身也是一次改动，会再备份一次。",
                             danger=True, ok_text="撤销"):
            return
        s = self._active_session()

        def work():
            bpath = self._route_backup(backend)
            rc, out, err = fw.run_cmds(s, cmds)
            return rc, out, err, bpath

        def done(res):
            rc, out, err, bpath = res
            self._audit("route.change", "ok" if rc == 0 else "fail",
                        f"{backend.label} · undo · {why}"
                        + (f" · 备份 {bpath}" if bpath else ""))
            self._routes_undo = None
            try:
                self.btn_route_undo.set_enabled(False)
            except Exception:  # noqa: BLE001
                pass
            if rc == 0:
                self.status.set_state("已撤销上一步", "ok")
                self.routes_refresh()
            else:
                self._warn("撤销失败", (err or out or f"rc={rc}").strip()[:600])

        self.bg(work, ok=done, err=lambda m: self._warn("撤销失败", m))

    # ------------------------------------------------------------ 小动作
    def _route_detail(self, row):
        if not row:
            return
        body = (f"目标网段 : {row.get('dst', '')}\n"
                f"网关     : {row.get('gateway') or '直连'}\n"
                f"接口     : {row.get('dev', '')}\n"
                f"跃点     : {row.get('metric', '')}\n"
                f"来源     : {row.get('proto', '')}\n"
                f"标志     : {row.get('flags', '')}\n"
                f"源地址   : {row.get('src', '')}\n\n"
                f"原始行   :\n{row.get('raw') or row.get('dst', '')}")
        forms.InfoDialog.show(self, "路由详情", body,
                              subtitle=f"{row.get('dev', '')} · 跃点 "
                                       f"{row.get('metric', '')}",
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
    return f"{row.get('dev', '')} · {row.get('metric', '')}"
