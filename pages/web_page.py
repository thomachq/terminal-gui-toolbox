# -*- coding: utf-8 -*-
"""Web 服务管理页。

一台机器上可能同时挂着 Nginx、Apache、Tomcat、Caddy、Lighttpd，甚至 IIS。
所以这里不是「假设只有一个 Web 服务」，而是先探测出几个，再**用下拉列表切换**，
每个服务各管各的配置与证书：

  服务下拉   探测到的服务（类型 + 版本 + 状态），切换即换一整套详情
  服务详情   单元名 / 监听端口 / 配置文件 / 站点根 / 运行用户
  站点清单   这个服务下挂的每个站点：域名、别名、端口、站点根、是否上 TLS
  域名绑定   改 server_name / ServerName+ServerAlias，备份 → 写 → 配置检查 → 重载
  TLS 证书   证书文件、私钥文件、主题、颁发者、SAN、有效期、剩余天数、是否一对
  证书操作   重新读取 / 导入并部署（自动备份 + 配置检查 + 重载 + 失败回滚）/ 恢复备份
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

import wbtui as U
from core import webstack as ws
from ui import forms
from ui.table import Column, DataTable, KVGrid

STATE_TONE = {"运行中": "ok", "未运行": "mute"}

SITE_COLS = [
    Column("name", "主域名", 190, mono=True, stretch=True),
    Column("aliases", "别名", 190, stretch=True),
    Column("listen", "监听", 110, mono=True),
    Column("root", "站点根", 210, mono=True, stretch=True),
    Column("ssl", "TLS", 50),
    Column("file", "配置文件", 210, mono=True, stretch=True),
]
SITE_KINDS = ("nginx", "apache", "iis")


class WebPage:
    # ------------------------------------------------------------ 构建
    def build_web(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(4, weight=1)

        # ---- 服务
        c = U.Card(pg, title="Web 服务", hint="探测到的每个服务单独管理，下拉切换")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        head = U.wframe(b, "card")
        head.pack(fill="x")
        self.lbl_web_title = U.wlabel(head, "正在探测 Web 服务…", "card", U.FONT_B)
        self.lbl_web_title.pack(side="left")
        self.badge_web = U.Pill(head, "探测中", "mute", height=24)
        self.badge_web.pack(side="left", padx=(U.px(10), 0))
        self.lbl_web_ver = U.wlabel(head, "", "mute", U.FONT_XS)
        self.lbl_web_ver.pack(side="right")

        self.lbl_web_hint = U.wlabel(
            b, "未连接设备时探测的是本机。Nginx / Apache / Tomcat / Caddy / "
               "Lighttpd / IIS 都会认，靠进程、监听端口与配置文件三方对照。",
            "dim", U.FONT_S, justify="left", wraplength=U.px(760))
        self.lbl_web_hint.pack(anchor="w", pady=(U.px(6), 0))

        row = U.wframe(b, "card")
        row.pack(fill="x", pady=(U.px(12), 0))
        U.RoundButton(row, "重新探测", self.web_probe, variant="secondary",
                      height=32, padx=16).pack(side="left")
        U.wlabel(row, "服务", "card", U.FONT_S).pack(side="left", padx=(U.px(16), 6))
        self.v_web_svc = tk.StringVar()
        self.cb_web_svc = ttk.Combobox(row, textvariable=self.v_web_svc, width=34,
                                       state="readonly")
        self.cb_web_svc.pack(side="left")
        self.cb_web_svc.bind("<<ComboboxSelected>>", lambda e: self.web_select())
        self.btn_web_test = U.RoundButton(row, "配置检查", self.web_test,
                                          variant="ghost", height=32, padx=12)
        self.btn_web_test.pack(side="left", padx=(U.px(12), 0))
        self.btn_web_reload = U.RoundButton(row, "重载服务", self.web_reload,
                                            variant="ghost", height=32, padx=12)
        self.btn_web_reload.pack(side="left", padx=(U.px(4), 0))
        self.btn_web_conf = U.RoundButton(row, "查看配置", self.web_show_config,
                                          variant="ghost", height=32, padx=12)
        self.btn_web_conf.pack(side="left", padx=(U.px(4), 0))

        # ---- 详情
        c2 = U.Card(pg, title="服务详情", hint="切换下拉即换这一块的内容")
        c2.grid(row=1, column=0, sticky="we", pady=(U.px(10), 0))
        self.kk_web = KVGrid(c2.body, columns=3)
        self.kk_web.pack(fill="x")

        # ---- 站点与域名
        csite = U.Card(pg, title="站点与域名",
                       hint="这个服务下挂的每个站点 · 域名绑定带备份与回滚")
        csite.grid(row=2, column=0, sticky="we", pady=(U.px(10), 0))
        bs = csite.body
        srow = U.wframe(bs, "card")
        srow.pack(fill="x")
        self.lbl_site = U.wlabel(srow, "切换服务后自动读取站点。", "dim", U.FONT_S)
        self.lbl_site.pack(side="left")
        self.badge_site = U.Pill(srow, "未读取", "mute", height=24)
        self.badge_site.pack(side="left", padx=(U.px(10), 0))
        self.btn_site_bind = U.RoundButton(srow, "绑定域名…", self.web_bind_domains,
                                           variant="primary", height=30, padx=14)
        self.btn_site_bind.pack(side="right")
        self.btn_site_roll = U.RoundButton(srow, "还原备份", self.web_site_restore,
                                           variant="ghost", height=30, padx=12)
        self.btn_site_roll.pack(side="right", padx=(0, U.px(6)))
        self.btn_site_reload = U.RoundButton(srow, "刷新站点", self.web_sites_load,
                                             variant="secondary", height=30, padx=12)
        self.btn_site_reload.pack(side="right", padx=(0, U.px(6)))

        self.tbl_site = DataTable(
            bs, SITE_COLS, height=6, searchable=True,
            empty_title="这个服务没有可解析的站点",
            empty_hint="nginx / Apache / IIS 才有站点清单；"
                       "Tomcat 看 webapps，Caddy / Lighttpd 按配置文件管理。",
            on_activate=self._site_detail)
        self.tbl_site.pack(fill="both", expand=True, pady=(U.px(8), 0))
        self.tbl_site.set_menu([
            ("查看站点详情", self._site_detail, lambda r: bool(r)),
            ("绑定 / 修改域名…", self.web_bind_domains, lambda r: bool(r)),
            ("复制域名", lambda r: self.copy_text(
                ", ".join([r.get("name", "")] + [a for a in
                          (r.get("aliases") or "").split(", ") if a != "—"]),
                "域名"), lambda r: bool(r)),
            (None, None, None),
            ("还原这个文件的备份", self.web_site_restore, lambda r: bool(r)),
        ])

        # ---- TLS
        c3 = U.Card(pg, title="服务器 TLS 证书", hint="读取 → 校验 → 部署（失败自动回滚）")
        c3.grid(row=3, column=0, sticky="we", pady=(U.px(10), 0))
        b3 = c3.body
        line = U.wframe(b3, "card")
        line.pack(fill="x")
        U.wlabel(line, "证书", "card", U.FONT_S).pack(side="left")
        self.v_web_cert = tk.StringVar()
        self.cb_web_cert = ttk.Combobox(line, textvariable=self.v_web_cert, width=52,
                                        state="readonly")
        self.cb_web_cert.pack(side="left", padx=(6, 0))
        self.cb_web_cert.bind("<<ComboboxSelected>>", lambda e: self.web_read_tls())
        self.badge_tls = U.Pill(line, "未读取", "mute", height=24)
        self.badge_tls.pack(side="left", padx=(U.px(10), 0))

        self.kk_tls = KVGrid(b3, columns=3)
        self.kk_tls.pack(fill="x", pady=(U.px(8), 0))

        act = U.wframe(b3, "card")
        act.pack(fill="x", pady=(U.px(10), 0))
        U.RoundButton(act, "重新读取", self.web_read_tls, variant="secondary",
                      height=32, padx=14).pack(side="left")
        U.RoundButton(act, "导入证书并部署", self.web_deploy, variant="primary",
                      height=32, padx=16).pack(side="left", padx=(U.px(8), 0))
        U.RoundButton(act, "恢复上一次备份", self.web_restore, variant="ghost",
                      height=32, padx=14).pack(side="left", padx=(U.px(6), 0))
        U.RoundButton(act, "复制证书路径", self.web_copy_paths, variant="ghost",
                      height=32, padx=14).pack(side="left", padx=(U.px(6), 0))

        # ---- 操作记录
        c4 = U.Card(pg, title="操作记录", hint="探测命令与部署步骤，逐步落在这里")
        c4.grid(row=4, column=0, sticky="nsew", pady=(U.px(10), 0))
        self.box_web = U.TextBox(c4.body, role="alt", font=U.MONO_S, height=7,
                                 wrap="none")
        self.box_web.pack(fill="both", expand=True)

        self.web_res: dict = {}
        self.web_cur: str = ""
        self.web_tls: dict = {}
        self.web_sites: list = []
        self._web_site_svc: str = ""
        self._web_set_enabled(False)

    # ------------------------------------------------------------ 状态
    def _web_set_enabled(self, on: bool):
        for btn in (self.btn_web_test, self.btn_web_reload, self.btn_web_conf):
            btn.set_enabled(on)
        for btn in (self.btn_site_reload, self.btn_site_bind, self.btn_site_roll):
            btn.set_enabled(on)

    def _web_log(self, text: str) -> None:
        try:
            self.box_web.txt.configure(state="normal")
            self.box_web.txt.insert("end", text.rstrip() + "\n")
            self.box_web.txt.see("end")
        except tk.TclError:
            pass

    def _web_clear_log(self) -> None:
        try:
            self.box_web.txt.configure(state="normal")
            self.box_web.clear()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 探测
    def web_probe(self):
        s = self._active_session()
        dev = self._effective_device()
        self.lbl_web_title.configure(text="正在探测 Web 服务…", fg=U.T.text)
        self.badge_web.set("探测中", "mute")
        self.status.set_state("探测 Web 服务…")
        self._web_clear_log()
        scanner = ws.WebScanner(s, dev)

        def work():
            return scanner.scan()

        def done(res):
            self._web_apply(res)

        self.bg(work, ok=done, err=lambda m: self._web_apply(
            {"status": "error", "detail": m, "count": 0, "services": [],
             "commands": []}))

    def _web_apply(self, res: dict):
        self.web_res = res or {}
        svcs = self.web_res.get("services") or []
        status = self.web_res.get("status")
        if status == ws.STATUS_UNSUPPORTED:
            self.lbl_web_title.configure(text="这台设备没有可管理的 Web 服务",
                                         fg=U.T.text_dim)
            self.badge_web.set("不适用", "mute")
        elif status == ws.STATUS_EMPTY:
            self.lbl_web_title.configure(text="没有发现 Web 服务", fg=U.T.text_dim)
            self.badge_web.set("0 个", "mute")
        elif status == "error":
            self.lbl_web_title.configure(text="探测失败", fg=U.T.err)
            self.badge_web.set("失败", "err")
            self._web_log(f"[失败] {self.web_res.get('detail', '')}")
        else:
            self.lbl_web_title.configure(
                text=ws.summarize(svcs), fg=U.T.text)
            self.badge_web.set(f"{len(svcs)} 个服务", "ok" if svcs else "mute")
        self.lbl_web_ver.configure(text=self.web_res.get("detail") or "")

        for cmd in (self.web_res.get("commands") or [])[-14:]:
            self._web_log(f"$ {cmd}")

        self.cb_web_svc.configure(values=[
            f"{s.get('title') or s.get('label')} · {s.get('state')}"
            f"{'  ' + ','.join(s.get('listen') or []) if s.get('listen') else ''}"
            for s in svcs])
        if svcs:
            self.cb_web_svc.current(0)
            self._web_set_enabled(True)
            self.web_select()
        else:
            self.cb_web_svc.set("")
            self.v_web_svc.set("")
            self._web_set_enabled(False)
            self.web_cur = ""
            self.web_tls = {}
            self.cb_web_cert.configure(values=[])
            self.cb_web_cert.set("")
            self._web_render()
            self._tls_render({})

    # ------------------------------------------------------------ 选择
    def _web_service(self) -> dict:
        svcs = (self.web_res or {}).get("services") or []
        idx = self.cb_web_svc.current()
        if idx < 0 or idx >= len(svcs):
            return {}
        return svcs[idx]

    def web_select(self):
        svc = self._web_service()
        self.web_cur = svc.get("key", "")
        certs = svc.get("certs") or []
        self.cb_web_cert.configure(values=[(c.get("cert") or "") for c in certs])
        if certs:
            self.cb_web_cert.current(0)
        else:
            self.cb_web_cert.set("")
        self._web_render()
        if certs:
            self.web_read_tls()
        else:
            self.web_tls = {}
            self._tls_render({})
        # 站点清单跟着服务走：换一个服务就该换一套站点
        self.after(60, self.web_sites_load)

    def _web_render(self):
        svc = self._web_service()
        if not svc:
            self.kk_web.set_items([("服务", "—", None)])
            return
        ports = ", ".join(svc.get("listen") or []) or "—"
        certs = svc.get("certs") or []
        self.kk_web.set_items([
            ("类型", svc.get("label") or svc.get("kind") or "—", "brand"),
            ("版本", svc.get("version") or "—", None),
            ("状态", svc.get("state") or "—",
             STATE_TONE.get(svc.get("state", ""), "mute")),
            ("监听端口", ports, None),
            ("服务单元", svc.get("unit") or "—（非 systemd 管理）", None),
            ("运行用户", svc.get("user") or "—", None),
            ("配置文件", svc.get("config") or "—（未找到）", None),
            ("站点根目录", svc.get("docroot") or "—", None),
            ("证书条目", f"{len(certs)} 处" if certs else "配置里没有 TLS 指令",
             "ok" if certs else "mute"),
        ])

    # ------------------------------------------------------------ TLS 读取
    def _web_cert_pair(self) -> dict:
        svc = self._web_service()
        certs = svc.get("certs") or []
        idx = self.cb_web_cert.current()
        if 0 <= idx < len(certs):
            return certs[idx]
        return {}

    def web_read_tls(self):
        pair = self._web_cert_pair()
        if not pair:
            self._tls_render({})
            return
        s = self._active_session()
        dev = self._effective_device()
        self.badge_tls.set("读取中", "mute")
        scanner = ws.WebScanner(s, dev)

        def work():
            cert_text = scanner._read(pair.get("cert", ""))
            key_text = scanner._read(pair.get("key", "")) if pair.get("key") else ""
            rep = ws.cert_report(cert_text) if cert_text else {
                "ok": False, "error": "读不到证书文件（可能需要提权）"}
            rep["cert_path"] = pair.get("cert", "")
            rep["key_path"] = pair.get("key", "") or ""
            rep["key_match"] = ws.key_matches(cert_text, key_text) if key_text else None
            rep["key_present"] = bool(key_text)
            rep["chain_path"] = pair.get("chain", "")
            return rep

        def done(rep):
            self.web_tls = rep
            self._tls_render(rep)
            if rep.get("ok"):
                self.badge_tls.set("已读取", "ok")
            else:
                self.badge_tls.set("读取失败", "err")
                self._web_log(f"[证书] {rep.get('error', '')}  {pair.get('cert', '')}")

        def fail(msg):
            self.badge_tls.set("读取失败", "err")
            self._web_log(f"[证书] {msg}")

        self.bg(work, ok=done, err=fail)

    def _tls_render(self, rep: dict):
        if not rep:
            self.kk_tls.set_items([
                ("证书文件", "—", None),
                ("说明", "该服务在配置里没有找到 TLS 证书指令", "mute"),
            ])
            return
        if not rep.get("ok"):
            self.kk_tls.set_items([
                ("证书文件", rep.get("cert_path") or "—", None),
                ("读取结果", rep.get("error") or "无法解析", "err"),
            ])
            return
        days = int(rep.get("days_left") or 0)
        tone = "err" if days < 0 else ("warn" if days <= 30 else "ok")
        left = "已过期" if days < 0 else f"{days} 天"
        match = rep.get("key_match")
        match_txt = ("匹配" if match else "不匹配") if match is not None else \
            ("未读到私钥" if not rep.get("key_present") else "未比对")
        san = ", ".join(rep.get("san") or []) or "—"
        if len(san) > 90:
            san = san[:88] + "…"
        self.kk_tls.set_items([
            ("证书文件", rep.get("cert_path") or "—", None),
            ("私钥文件", rep.get("key_path") or "—（未在配置里指定）", None),
            ("主题", rep.get("subject") or "—", None),
            ("颁发者", rep.get("issuer") or "—", None),
            ("到期", rep.get("not_after") or "—", None),
            ("剩余", left, tone),
            ("自签", "是" if rep.get("self_signed") else "否", None),
            ("证书链", f"{rep.get('chain', 0)} 张" if rep.get("chain") else "无", None),
            ("密钥", f"{rep.get('key_type') or '—'} · {match_txt}",
             "ok" if match else ("err" if match is False else "mute")),
            ("SAN", san, None),
        ])

    # ------------------------------------------------------------ TLS 部署
    def web_deploy(self):
        svc = self._web_service()
        if not svc:
            return
        if svc.get("kind") == "iis":
            forms.confirm(
                self, "IIS 的证书不在这里换",
                "IIS 的证书是 http.sys 的端口绑定，不是配置文件里的路径。\n\n"
                "请在 IIS 管理器里导入 PFX 并重新绑定 https，"
                "或用 netsh http update sslcert 指定新指纹。\n\n"
                "这里可以查看当前 https 绑定的证书主题与到期时间。")
            return

        cert_file = filedialog.askopenfilename(
            parent=self, title="选择证书文件（PEM / fullchain）",
            filetypes=[("证书", "*.pem *.crt *.cer *.chain"), ("全部", "*.*")])
        if not cert_file:
            return
        key_file = filedialog.askopenfilename(
            parent=self, title="选择私钥文件（PEM）",
            filetypes=[("私钥", "*.key *.pem"), ("全部", "*.*")])
        if not key_file:
            return

        cert_text = Path(cert_file).read_text(encoding="utf-8", errors="replace")
        key_text = Path(key_file).read_text(encoding="utf-8", errors="replace")
        if not ws.is_cert_pem(cert_text):
            forms.confirm(self, "这不是证书", f"{cert_file}\n\n里面没有 PEM 证书块。")
            return
        if not ws.is_private_key_pem(key_text):
            forms.confirm(self, "这不是私钥", f"{key_file}\n\n里面没有 PEM 私钥块。")
            return
        rep = ws.cert_report(cert_text)
        if not ws.key_matches(cert_text, key_text):
            forms.confirm(
                self, "证书与私钥不是一对",
                "这两份文件的公钥对不上，装上去服务会起不来。\n\n"
                "请确认私钥与证书是同一次签发出来的。")
            return

        pair = self._web_cert_pair() or {}
        default_cert = pair.get("cert", "")
        default_key = pair.get("key", "")
        fields = [
            {"key": "cert", "label": "写到证书路径", "default": default_cert,
             "hint": "设备上的目标文件，旧文件会自动备份为 .bak-时间戳"},
            {"key": "key", "label": "写到私钥路径", "default": default_key,
             "hint": "权限会设成 600"},
            {"key": "reload", "label": "部署后检查并重载", "kind": "check",
             "default": True,
             "hint": "取消勾选就只写文件，不碰服务"},
        ]
        data = forms.FormDialog.open(
            self, "部署证书",
            fields, subtitle=f"{svc.get('label')} · {rep.get('subject', '')[:60]}")
        if not data:
            return
        cert_path = str(data.get("cert") or "").strip()
        key_path = str(data.get("key") or "").strip()
        if not cert_path or not key_path:
            forms.confirm(self, "路径不能为空", "证书和私钥都要有目标路径。")
            return

        tip = (f"服务：{svc.get('label')}\n证书：{cert_path}\n私钥：{key_path}\n"
               f"主题：{rep.get('subject', '')}\n到期：{rep.get('not_after', '')}"
               f"（{rep.get('days_left')} 天）\n\n"
               "旧文件会备份成 .bak-时间戳；配置检查或重载失败会自动还原。")
        if not forms.confirm(self, "确认部署", tip, ok_text="部署"):
            return

        deployer = ws.TLSDeployer(self._active_session(), svc)
        self.badge_tls.set("部署中", "mute")
        self._web_log(f"[部署] {cert_path}")

        def work():
            return deployer.deploy(cert_text, key_text, cert_path, key_path,
                                   reload=bool(data.get("reload")))

        def done(res):
            for st in res.get("steps") or []:
                mark = "ok " if st.get("rc") == 0 else "ERR"
                self._web_log(f"  [{mark}] {st.get('name')}: {st.get('cmd')[:110]}")
                extra = (st.get("err") or st.get("out") or "").strip()
                if extra and st.get("rc") != 0:
                    self._web_log(f"        {extra[:160]}")
            if res.get("ok"):
                self.badge_tls.set("已部署", "ok")
                self.toast("证书已部署并重载", "ok")
                self._log_raw("ok", f"[Web] 部署证书 · {svc.get('label')} · {cert_path}\n")
            else:
                self.badge_tls.set("部署失败", "err")
                self._log_raw("bad", f"[Web] 部署证书失败 · {res.get('detail', '')}\n")
                forms.confirm(self, "部署没有成功", res.get("detail") or "未知原因")
            self.web_read_tls()

        self.bg(work, ok=done, err=lambda m: self.badge_tls.set("部署失败", "err"))

    def web_restore(self):
        pair = self._web_cert_pair()
        svc = self._web_service()
        if not pair or not svc:
            return
        if not forms.confirm(
                self, "恢复上一次备份",
                f"把 {pair.get('cert')}\n和 {pair.get('key') or '（无私钥）'}\n"
                "退回最近一次的 .bak-* 版本，然后重载服务。继续？",
                danger=True, ok_text="恢复"):
            return
        deployer = ws.TLSDeployer(self._active_session(), svc)

        def work():
            return deployer.restore_latest_backup(
                pair.get("cert", ""), pair.get("key", ""))

        def done(res):
            self._web_log(f"[恢复] {res.get('detail', '')}")
            self.toast(res.get("detail", ""), "ok" if res.get("ok") else "err")
            self.web_read_tls()

        self.bg(work, ok=done, err=lambda m: self.toast(str(m), "err"))

    # ------------------------------------------------------------ 服务操作
    def web_test(self):
        svc = self._web_service()
        if not svc:
            return
        deployer = ws.TLSDeployer(self._active_session(), svc)
        self.status.set_state("检查配置…")

        def work():
            return deployer._test_config()

        def done(res):
            rc, out, err = res
            text = ((out or "") + (err or "")).strip() or ("通过" if rc == 0 else "")
            self._web_log(f"[配置检查] rc={rc} {text[:300]}")
            self.toast(f"配置检查{'通过' if rc == 0 else '未通过'}", 
                       "ok" if rc == 0 else "err")

        self.bg(work, ok=done, err=lambda m: self.toast(str(m), "err"))

    def web_reload(self):
        svc = self._web_service()
        if not svc:
            return
        if not forms.confirm(self, "重载服务",
                             f"重载 {svc.get('label')}（{svc.get('unit') or '进程内命令'}）？\n"
                             "重载不会断开现有连接。", ok_text="重载"):
            return
        deployer = ws.TLSDeployer(self._active_session(), svc)

        def work():
            return deployer._reload()

        def done(res):
            rc, out, err = res
            text = ((out or "") + (err or "")).strip()
            self._web_log(f"[重载] rc={rc} {text[:300]}")
            self.toast("已重载" if rc == 0 else "重载失败", "ok" if rc == 0 else "err")

        self.bg(work, ok=done, err=lambda m: self.toast(str(m), "err"))

    def web_show_config(self):
        svc = self._web_service()
        if not svc:
            return
        path = svc.get("config") or ""
        if not path:
            forms.confirm(self, "没有配置文件", "这个服务的配置路径没找到。")
            return
        scanner = ws.WebScanner(self._active_session(), self._effective_device())

        def work():
            return scanner._read(path, timeout=30) or "（读不到内容，可能需要提权）"

        def done(text):
            forms.InfoDialog.show(self, "配置文件", text, subtitle=path,
                                  width=900, height=620)

        self.bg(work, ok=done, err=lambda m: self.toast(str(m), "err"))

    def web_copy_paths(self):
        pair = self._web_cert_pair()
        if not pair:
            self.toast("这个服务没有证书路径可复制", "warn")
            return
        text = "\n".join(x for x in (pair.get("cert"), pair.get("key"),
                                     pair.get("chain")) if x)
        self.copy_text(text, "证书路径")

    # ------------------------------------------------------------ 站点
    def _web_kind(self) -> str:
        return (self._web_service() or {}).get("kind", "")

    def web_sites_load(self):
        """读站点清单。nginx 用 `nginx -T`，apache 直接 cat 站点文件。

        这两条命令都把「文件名」写在输出里（`# configuration file x:`），
        所以列出来的每一条站点都能反查到具体文件与行号 —— 改的时候才知道改哪。
        """
        svc = self._web_service()
        kind = svc.get("kind", "")
        if kind not in SITE_KINDS:
            self.web_sites = []
            self.tbl_site.set_rows([])
            self.tbl_site.set_empty(
                "这个服务没有站点清单",
                "nginx / Apache / IIS 才有；Tomcat 的站点在 webapps 下，"
                "Caddy / Lighttpd 直接按配置文件管。")
            self.lbl_site.configure(
                text=f"{svc.get('label') or kind} 不支持站点清单",
                fg=U.T.text_dim)
            self.badge_site.set("不适用", "mute")
            self.btn_site_bind.set_enabled(False)
            self.btn_site_roll.set_enabled(False)
            return

        cmd = ws.site_dump_cmd(kind)
        s = self._active_session()
        self.badge_site.set("读取中", "mute")
        self.lbl_site.configure(text="正在读取站点配置…", fg=U.T.text_dim)
        self._web_site_svc = svc.get("key", "")

        def work():
            if s is None:
                return ""
            runner = getattr(s, "run_traced", None) or s.run
            try:
                rc, out, err = runner(cmd, timeout=45)
            except Exception:  # noqa: BLE001
                return ""
            return out or ""

        def done(text):
            self._web_log(f"$ {cmd}")
            self.web_sites = ws.parse_sites(kind, text)
            self._web_sites_render()

        def fail(msg):
            self.web_sites = []
            self._web_sites_render()
            self._web_log(f"[站点] {msg}")

        self.bg(work, ok=done, err=fail)

    def _web_sites_render(self):
        rows = [s.as_row() for s in self.web_sites]
        self.tbl_site.set_rows(rows)
        kind = self._web_kind()
        if kind == "iis":
            self.lbl_site.configure(
                text="IIS 站点由 http.sys 绑定管理，这里只读", fg=U.T.text_dim)
        elif self.web_sites:
            self.lbl_site.configure(text=ws.summarize_sites(self.web_sites),
                                    fg=U.T.text_dim)
        else:
            self.lbl_site.configure(
                text="没有解析到站点（可能没有站点文件，或需要提权读取）",
                fg=U.T.text_dim)
        self.badge_site.set(f"{len(rows)} 个站点" if rows else "0 个",
                            "ok" if rows else "mute")
        self.btn_site_bind.set_enabled(bool(rows))
        self.btn_site_roll.set_enabled(bool(rows))

    def _site_pick(self, row=None):
        row = row or self.tbl_site.selected()
        if not row:
            return None
        for s in self.web_sites:
            if s.file == row.get("_file") and s.index == row.get("_index"):
                return s
        return None

    def _site_detail(self, row=None):
        site = self._site_pick(row)
        if site is None:
            return
        body = [
            f"类型：{site.kind}",
            f"文件：{site.file or '—'}",
            f"位置：第 {site.index + 1} 个块（第 {site.line or '?'} 行）",
            f"域名：{' '.join(site.names) or '（未绑定）'}",
            f"监听：{', '.join(site.listen) or '—'}",
            f"站点根：{site.root or '—'}",
            f"证书：{site.ssl or '—'}",
        ]
        if site.note:
            body += ["", site.note]
        forms.InfoDialog.show(self, site.primary or "站点", "\n".join(body),
                              subtitle=site.file, width=760, height=420)

    def web_bind_domains(self, row=None):
        site = self._site_pick(row)
        if site is None:
            self.toast("先选一个站点", "warn")
            return
        if site.kind == "iis":
            forms.confirm(
                self, "IIS 的绑定不在这里改",
                f"{site.primary}\n\nIIS 站点绑定由 http.sys 管理，不在配置文件里。\n\n"
                "请在 IIS 管理器里「编辑绑定」，或用：\n"
                "  appcmd set site /site.name:\"站点名\" /+bindings."
                "[protocol='http',bindingInformation='*:80:域名']")
            return
        fields = [
            {"key": "names", "label": "域名", "default": " ".join(site.names),
             "width": 52, "required": True,
             "hint": "空格分隔；第一个是主域名，其余作为别名"},
        ]
        data = forms.FormDialog.open(
            self, "绑定域名", fields,
            subtitle=f"{site.kind} · {site.file} 第 {site.index + 1} 个块")
        if not data:
            return
        raw = str(data.get("names") or "")
        for sep in (",", "，", "、", ";", "\t"):
            raw = raw.replace(sep, " ")
        names = [n.strip() for n in raw.split() if n.strip()]
        if not names:
            return self.toast("域名不能为空", "warn")
        bad = [n for n in names if not _valid_host(n)]
        if bad:
            return forms.confirm(
                self, "域名格式看着不对",
                "这些不像域名：\n" + "、".join(bad) +
                "\n\n通配符请写成 *.example.com；下划线在 nginx 里能过，"
                "但证书和浏览器可能不认。")

        preview = _diff_line(site, names)
        if not forms.confirm(
                self, "确认修改域名",
                f"{site.file}\n\n{preview}\n\n"
                "会先备份成 .bak-时间戳，写完做一次配置检查并重载；"
                "检查不过就自动还原。继续？", ok_text="写入"):
            return

        binder = ws.SiteBinder(self._active_session(), site)
        self.badge_site.set("写入中", "mute")
        self._web_log(f"[域名] {site.primary} → {' '.join(names)}")

        def work():
            return binder.update(names)

        def done(res):
            for st in res.get("steps") or []:
                mark = "ok " if st.get("rc") == 0 else "ERR"
                self._web_log(f"  [{mark}] {st.get('name')}: {st.get('cmd', '')[:110]}")
                extra = (st.get("err") or st.get("out") or "").strip()
                if extra and st.get("rc") != 0:
                    self._web_log(f"        {extra[:180]}")
            if res.get("ok"):
                self.badge_site.set("已写入", "ok")
                self.toast("域名已更新", "ok")
                self._log_raw("ok", f"[Web] 域名 {site.file} → {' '.join(names)}\n")
            else:
                self.badge_site.set("写入失败", "err")
                self._log_raw("bad", f"[Web] 域名写入失败 · {res.get('detail', '')}\n")
                forms.confirm(self, "没有改成", res.get("detail") or "未知原因")
            self.after(300, self.web_sites_load)

        self.bg(work, ok=done, err=lambda m: (
            self.badge_site.set("写入失败", "err"), self._web_log(f"[域名] {m}")))

    def web_site_restore(self, row=None):
        site = self._site_pick(row)
        if site is None:
            return
        if site.kind == "iis" or not site.file:
            return self.toast("这条没有可还原的文件", "warn")
        if not forms.confirm(
                self, "还原配置文件",
                f"把 {site.file} 退回到最近一次的 .bak-* 版本，并重载服务。\n"
                "注意：这会丢弃这个文件在备份之后的**所有**改动，不只是域名。\n\n继续？",
                danger=True, ok_text="还原"):
            return
        binder = ws.SiteBinder(self._active_session(), site)

        def work():
            return binder.rollback()

        def done(res):
            self._web_log(f"[站点还原] {res.get('detail', '')}")
            self.toast(res.get("detail", ""), "ok" if res.get("ok") else "err")
            self.after(300, self.web_sites_load)

        self.bg(work, ok=done, err=lambda m: self.toast(str(m), "err"))


def _valid_host(name: str) -> bool:
    """域名 / 通配符 / IP / `_` 都放行；明显不是域名的拦一下。"""
    if name in ("_", "localhost"):
        return True
    if name.startswith("*."):
        name = name[2:]
    if not name or len(name) > 253:
        return False
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9_]([A-Za-z0-9_.-]*[A-Za-z0-9])?", name)) \
        or bool(re.fullmatch(r"[0-9a-fA-F:.]+", name))


def _diff_line(site, names: list[str]) -> str:
    old = " ".join(site.names) or "（未绑定）"
    new = " ".join(names)
    if site.kind == "apache":
        return (f"现在：ServerName {site.names[0] if site.names else '（无）'}"
                f"{(' / ServerAlias ' + ' '.join(site.names[1:])) if len(site.names) > 1 else ''}\n"
                f"改成：ServerName {names[0]}"
                f"{(' / ServerAlias ' + ' '.join(names[1:])) if len(names) > 1 else ''}")
    return f"现在：server_name {old}\n改成：server_name {new}"
