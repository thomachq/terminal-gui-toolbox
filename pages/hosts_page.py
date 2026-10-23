# -*- coding: utf-8 -*-
"""主机管理 —— 清单式的资产台账。

布局参考用户给的图：顶部一排动作（添加主机 / 本地终端 / 导入主机），
右侧是分组下拉 + 搜索，下面是主机行。段切换把「连接与设备」原样收进来 ——
不再另开一套平行列表，两处看到的是同一份档案。

连接动作刻意**复用** `do_connect`：填好表单再走原来那条路。主机清单不自己
实现一套连接逻辑，否则「清单里连不上、表单里连得上」这种事迟早发生。
"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox

import wbtui as U
from core import creds as C
from core import hosts as H
from core import proxies as P
from ui import forms
from ui.table import Column, DataTable, Segmented

HOST_COLS = [
    Column("name", "名称", 170, stretch=True),
    Column("addr", "地址", 210, mono=True),
    Column("proto", "协议", 76),
    Column("group", "分组", 110),
    Column("tags", "标签", 140),
    Column("cred", "凭证", 110),
    Column("last_used", "最近连接", 130),
]


def host_fields(rec: dict | None = None, cred_opts=None, proxy_opts=None,
                is_win: bool | None = None) -> list[dict]:
    """「添加/编辑主机」的字段表。

    做成模块级纯函数是为了能单独测：表单本身是模态窗，测试开它要等用户点。
    这里只描述「有哪些字段、什么时候显示」，跟界面怎么画无关。

    两条规矩：

      · 协议选「本机」时，地址 / 端口 / 认证 / 私钥 / 凭证 / 代理全部收起来 ——
        本机连接不需要地址也不需要口令，让用户填这些是纯折磨。
      · 「本机」要问的是另一套：Linux 是 sudo（+ 口令），Windows 是
        管理员（UAC）+ 外壳（cmd / powershell）。两边不混。
    """
    rec = rec or H.blank_record()
    cred_opts = cred_opts if cred_opts is not None else [("", "不使用凭证（手填口令）")]
    proxy_opts = proxy_opts if proxy_opts is not None else [("", "直连（不走代理）")]
    is_win = sys.platform == "win32" if is_win is None else bool(is_win)
    remote = ["ssh", "telnet"]
    local_only = ["local"]

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
        {"key": "auth", "label": "认证", "kind": "select",
         "show_if": {"proto": remote},
         "options": [("key", "私钥"), ("password", "登录密码")],
         "default": str(rec.get("auth") or "key")},
        {"key": "key_path", "label": "私钥路径", "kind": "file",
         "show_if": {"proto": ["ssh"], "auth": ["key"]},
         "default": str(rec.get("key_path") or ""),
         "pick_title": "选择 SSH 私钥",
         "filetypes": [("私钥文件",
                        "id_rsa* id_ed25519* id_ecdsa* id_dsa* *.pem *.key"),
                       ("全部文件", "*.*")],
         "hint": "留空表示用 SSH 默认密钥或已在 agent 里的密钥"},
        {"key": "password", "label": "登录密码", "kind": "password",
         "show_if": {"proto": remote},
         "default": "",
         "hint": "填了会加密存进档案；多台共用建议改到「凭证管理」"},
        # ---- 本机专属：Linux 只有 sudo，Windows 只有 UAC + 外壳
        {"key": "sudo", "label": "使用 sudo 提权（进入后自动 sudo -i）",
         "kind": "check", "show_if": {"proto": local_only},
         "default": bool(rec.get("sudo")),
         "hint": "不勾也能用；需要 root 时程序会弹窗问你要密码"},
        {"key": "sudo_pass", "label": "sudo 密码", "kind": "password",
         "show_if": {"proto": local_only, "sudo": [True]},
         "default": "",
         "hint": "勾了「保存」才会落盘；留空表示每次现问"},
        {"key": "elevate", "label": "以管理员权限运行（UAC 提权）",
         "kind": "check", "show_if": {"proto": local_only},
         "default": bool(rec.get("elevate")),
         "hint": "启动会话时会弹 UAC 让你同意；本机 Windows 没有 sudo"},
        {"key": "shell", "label": "Shell", "kind": "select",
         "show_if": {"proto": local_only},
         "options": [("", "自动（认命令前缀）"), ("powershell", "PowerShell"),
                     ("cmd", "cmd")],
         "default": str(rec.get("shell") or ""),
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
    # 本机提权只有一种形态：把另一端那两个字段直接去掉，
    # 别让人对着一个永远用不上的勾选框发呆。
    drop = {"elevate", "shell"} if not is_win else {"sudo", "sudo_pass"}
    return [f for f in fields if f["key"] not in drop]


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
        U.RoundButton(bar, "本地终端", self.host_local_term, variant="secondary",
                      font=U.FONT_S, height=30, padx=12).pack(side="left",
                                                               padx=(U.px(6), 0))
        U.RoundButton(bar, "导入主机", self.host_import, variant="secondary",
                      font=U.FONT_S, height=30, padx=12).pack(side="left",
                                                               padx=(U.px(6), 0))

        # 右侧：分组下拉 + 刷新
        self.v_host_group = tk.StringVar(value="全部分组")
        self.cb_host_group = tk.OptionMenu(bar, self.v_host_group,
                                           "全部分组",
                                           command=lambda _e: self.hosts_render())
        self._style_optionmenu(self.cb_host_group, width=13)
        self.cb_host_group.pack(side="right")
        U.wlabel(bar, "分组", "pagedim", U.FONT_XS).pack(side="right",
                                                        padx=(0, U.px(6)))
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
            ("编辑", lambda: self.host_edit(self._host_pick()), None),
            ("复制地址", lambda: self.host_copy(self._host_pick()), None),
            ("复制为「新主机」", lambda: self.host_dup(self._host_pick()), None),
            (None, None, None),
            ("删除", lambda: self.host_del(self._host_pick()), None),
        ])
        self.tbl_host.entry_search.bind(
            "<KeyRelease>", lambda _e: self._hosts_apply_filter(), add="+")

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
        out = []
        for rec in self.profiles.profiles:
            out.append(H.host_view(
                rec,
                cred_name=cred_names.get(str(rec.get("cred_id") or ""), ""),
                proxy_name=proxy_names.get(str(rec.get("proxy_id") or ""), "")))
        return out

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
            subtitle="只填能连上的必要信息；口令加密保存，不进日志")
        if not data:
            return None
        pwd = str(data.pop("password", "") or "").strip()
        sudo_pw = str(data.pop("sudo_pass", "") or "").strip()
        merged = H.record_from_form(data, base=rec)
        if pwd:
            merged["password"] = pwd
            merged["remember"] = True
            # 填了密码却没选认证方式，说明用户就是想用密码登 —— 别让他困惑
            if merged.get("auth") == "password" or not merged.get("key_path"):
                merged["auth"] = "password"
        if sudo_pw:
            merged["sudo_password"] = sudo_pw
            merged["remember"] = True
        if merged["proto"] != "local":
            # 切成远程之后，本机那三个字段留着只会误导下一次连接
            merged["sudo"] = False
            merged["elevate"] = False
            merged["shell"] = ""
        errs = H.validate(merged, others=self.profiles.profiles)
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
        self.profiles.remove(str(rec.get("name") or ""))
        self.host_refresh()
        self._refresh_profile_list()
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
