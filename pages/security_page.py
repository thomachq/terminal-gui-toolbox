# -*- coding: utf-8 -*-
"""安全中心：把「体检结论」和「防火墙规则」放在同一页。

以前的防火墙是单独一页，用户要先知道「我的问题是不是防火墙」才找得到。
现在合成三档：

  体检结果   一次只读扫描（SSH 配置 / 监听端口 / 账号 / 失败登录 / 防火墙 /
             SELinux / 可疑计划任务），每条给等级与建议
  防火墙     原来的防火墙页，一行没改地搬进来（build_fw 复用）
  加固建议   把所有「需要动手」的项拼成可复制的文本

体检**永远只读**。建议里的命令是给用户自己复制的，程序不代跑 ——
改配置、重启服务属于「AI 运维」页或用户自己的终端。
"""
from __future__ import annotations

import tkinter as tk

import wbtui as U
from core import secscan as S
from core import servercert as SC
from pages.firewall_page import FirewallPage
from ui import forms
from ui.table import Column, DataTable, Segmented

COLS = [
    # 行内已经有中文等级（良好/提示/注意/风险），整行按 _tone 上色，
    # 所以这一列不需要再套格式函数。
    Column("level", "等级", 64),
    Column("title", "项目", 190),
    Column("detail", "现状", 320, stretch=True),
    Column("advice", "建议", 300, stretch=True),
]

TABS = [("scan", "体检结果"), ("fw", "防火墙"), ("cert", "证书管理"),
        ("fix", "加固建议")]

CERT_COLS = [
    Column("name", "名称", 150),
    Column("cn", "CN", 170),
    Column("sans", "SAN（域名 / IP）", 260, stretch=True),
    Column("not_after", "到期", 100),
    Column("days", "剩余", 78, anchor="e"),
    Column("key", "私钥", 56, anchor="center"),
]


class SecurityPage(FirewallPage):
    # ------------------------------------------------------------ 构建
    def build_sec(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(2, weight=1)

        # ---- 顶部：评分
        c = U.Card(pg, title="安全体检", hint="只读扫描 · 不修改任何配置")
        c.grid(row=0, column=0, sticky="we")
        b = c.body

        head = U.wframe(b, "card")
        head.pack(fill="x")
        self.lbl_sec_score = U.wlabel(head, "—", "card", U.FONT_H)
        self.lbl_sec_score.pack(side="left")
        self.lbl_sec_of = U.wlabel(head, " / 100", "mute", U.FONT_S)
        self.lbl_sec_of.pack(side="left", pady=(U.px(6), 0))
        self.badge_sec = U.Pill(head, "未体检", "mute", height=24)
        self.badge_sec.pack(side="left", padx=(U.px(12), 0))
        self.lbl_sec_sum = U.wlabel(head, "点「重新体检」开始。", "dim", U.FONT_S)
        self.lbl_sec_sum.pack(side="left", padx=(U.px(12), 0))

        row = U.wframe(b, "card")
        row.pack(fill="x", pady=(U.px(10), 0))
        U.RoundButton(row, "重新体检", self.sec_scan, variant="primary",
                      height=32, padx=18).pack(side="left")
        U.RoundButton(row, "复制加固建议", self.sec_copy_fix, variant="secondary",
                      height=32, padx=14).pack(side="left", padx=(U.px(8), 0))
        U.RoundButton(row, "查看原始命令", self.sec_show_cmds, variant="ghost",
                      height=32, padx=14).pack(side="left", padx=(U.px(6), 0))
        self.lbl_sec_hint = U.wlabel(
            row, "本机与远端都适用；Windows 只查防火墙 / 监听 / 远程桌面 / 管理员组。",
            "mute", U.FONT_XS)
        self.lbl_sec_hint.pack(side="right")

        # 这条只是补充说明，窗口一窄会被按钮挤成「本机与远端都…」这样的半截话。
        # 挤不下就整条收起来 —— 显示半句话比不显示更像故障。
        def _fit_sec_hint(_e=None):
            try:
                others = sum(w.winfo_reqwidth() for w in row.winfo_children()
                             if w is not self.lbl_sec_hint)
                room = int(row.winfo_width()) - others
                want = U.px(230)
                shown = bool(self.lbl_sec_hint.winfo_manager())
                if room < want and shown:
                    self.lbl_sec_hint.pack_forget()
                elif room >= want and not shown:
                    self.lbl_sec_hint.pack(side="right")
            except tk.TclError:
                pass

        row.bind("<Configure>", _fit_sec_hint, add="+")

        # ---- 页签
        self.seg_sec = Segmented(pg, TABS, command=self._sec_tab)
        self.seg_sec.grid(row=1, column=0, sticky="w", pady=(U.px(10), 0))

        holder = U.wframe(pg, "page")
        holder.grid(row=2, column=0, sticky="nsew", pady=(U.px(8), 0))
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        # 体检结果
        self.sec_scan_box = U.wframe(holder, "page")
        self.sec_scan_box.grid(row=0, column=0, sticky="nsew")
        card = U.Card(self.sec_scan_box, title="检查项",
                      hint="按严重程度排序 · 双击看建议 · 右键复制命令")
        card.pack(fill="both", expand=True)
        self.tbl_sec = DataTable(
            card.body, COLS, height=10, searchable=True,
            empty_title="还没有体检",
            empty_hint="点上面的「重新体检」。只读扫描，不会改任何配置。",
            on_activate=self._sec_detail)
        self.tbl_sec.pack(fill="both", expand=True)
        self.tbl_sec.set_menu([
            ("查看建议与命令", self._sec_detail, lambda r: bool(r)),
            ("复制这一项的修复命令", self._sec_copy_one, lambda r: bool(r)),
        ])

        # 防火墙（原页整体搬进来）
        self.sec_fw_box = U.wframe(holder, "page")
        self.sec_fw_box.grid(row=0, column=0, sticky="nsew")
        self.sec_fw_box.columnconfigure(0, weight=1)
        self.sec_fw_box.rowconfigure(1, weight=1)
        self.build_fw(self.sec_fw_box)

        # 证书管理
        self.sec_cert_box = U.wframe(holder, "page")
        self.sec_cert_box.grid(row=0, column=0, sticky="nsew")
        self.build_srvcert(self.sec_cert_box)

        # 加固建议
        self.sec_fix_box = U.wframe(holder, "page")
        self.sec_fix_box.grid(row=0, column=0, sticky="nsew")
        card3 = U.Card(self.sec_fix_box, title="加固建议",
                       hint="复制走自己执行 · 程序不代跑")
        card3.pack(fill="both", expand=True)
        self.box_sec_fix = U.TextBox(card3.body, role="alt", font=U.MONO_S,
                                     height=12, wrap="word")
        self.box_sec_fix.pack(fill="both", expand=True)
        self.box_sec_fix.set_text("体检完成后，这里会列出需要处理的项与对应命令。")

        self.sec_rep = S.Report()
        self.sec_scan_box.tkraise()

    # ------------------------------------------------------------ 页签
    def _sec_tab(self, key: str):
        box = {"scan": self.sec_scan_box, "fw": self.sec_fw_box,
               "cert": getattr(self, "sec_cert_box", None),
               "fix": self.sec_fix_box}.get(key)
        if box is not None:
            box.tkraise()
        if key == "fw" and getattr(self, "fw_backend", None) is None:
            self.after(80, self.fw_probe)
        if key == "cert":
            self.cert_refresh()

    def _sec_fw_maybe_probe(self):
        """停在防火墙页签时换了设备 —— 必须重探，否则会拿上一台的后端跑命令。"""
        try:
            if getattr(self, "seg_sec", None) is None:
                return
            if getattr(self.seg_sec, "current", "") != "fw":
                return
        except Exception:  # noqa: BLE001
            return
        self.fw_backend = None
        try:
            self.badge_fw.set("未探测", "mute")
        except Exception:  # noqa: BLE001
            pass
        self.after(120, self.fw_probe)

    # ------------------------------------------------------------ 体检
    def sec_scan(self):
        s = self._active_session()
        dev = self._effective_device()
        self.badge_sec.set("体检中", "mute")
        self.lbl_sec_sum.configure(text="正在跑只读检查…", fg=U.T.text_dim)
        self.status.set_state("安全体检…")
        scanner = S.SecurityScanner(s, dev)

        def work():
            return scanner.scan()

        def done(rep):
            self.sec_rep = rep
            self._sec_render(rep)

        def fail(msg):
            self.badge_sec.set("失败", "err")
            self.lbl_sec_sum.configure(text=str(msg)[:120], fg=U.T.err)
            self.status.set_state("体检失败", "err")

        self.bg(work, ok=done, err=fail)

    def _sec_render(self, rep: S.Report):
        score = rep.score
        tone = "ok" if score >= 85 else ("warn" if score >= 60 else "err")
        self.lbl_sec_score.configure(
            text=str(score), fg={"ok": U.T.ok, "warn": U.T.warn,
                                 "err": U.T.err}.get(tone, U.T.text))
        self.badge_sec.set(rep.headline(), tone)
        self.lbl_sec_sum.configure(text=rep.detail, fg=U.T.text_dim)
        self.tbl_sec.set_rows(rep.rows())
        self.tbl_sec.set_empty("没采到检查项",
                               "远端可能没装 ss/netstat，或命令被限制。"
                               "看「查看原始命令」确认跑了什么。")
        self.box_sec_fix.set_text(rep.to_fix_text())
        self.status.set_state(
            f"体检完成 · {score}/100 · 风险 {rep.count(S.BAD)}", tone)
        self._sec_log(rep)

    def _sec_log(self, rep: S.Report) -> None:
        kind = "bad" if rep.count(S.BAD) else (
            "warn" if rep.count(S.WARN) else "ok")
        self._log_raw(kind, f"[安全] 体检 {rep.score}/100 · {rep.headline()}\n")

    def _sec_detail(self, row):
        if not row:
            return
        body = [
            f"等级：{row.get('level')}",
            f"项目：{row.get('title')}",
            f"现状：{row.get('detail') or '—'}",
            f"建议：{row.get('advice') or '—'}",
        ]
        if row.get("fix"):
            body += ["", "参考命令（自己决定跑不跑）：", row["fix"]]
        forms.InfoDialog.show(self, row.get("title") or "检查项",
                              "\n".join(body), subtitle="安全体检",
                              width=720, height=440)

    def _sec_copy_one(self, row):
        if not row:
            return
        text = row.get("fix") or row.get("advice") or ""
        if not text:
            self.toast("这一项没有可直接执行的命令", "warn")
            return
        self.copy_text(text, "修复命令")

    def sec_copy_fix(self):
        self.copy_text(self.sec_rep.to_fix_text(), "加固建议")

    def sec_show_cmds(self):
        cmds = self.sec_rep.commands or [c for _, c in S.POSIX_CMDS]
        body = "体检跑的只读命令：\n\n" + "\n\n".join(cmds)
        body += ("\n\n都是在目标机上执行的查询命令，没有写操作。"
                 "读不到的项（比如需要 root 才能看的 /etc/shadow）会标成「提示」，"
                 "不会伪造成「良好」。")
        forms.InfoDialog.show(self, "体检命令", body, subtitle="只读",
                              width=820, height=520)

    # ============================================================ 证书管理
    def build_srvcert(self, pg):
        """服务器证书：自签名生成 / 导入 / 导出 / 上传到设备。

        跟「隧道服务管理 → 证书」那页不是一回事：那边是给 OpenVPN 客户端
        签证书（要有 CA），这边是给 **Web 服务** 一张能用的自签名证书
        （群晖 DSM、nginx、IIS 都是这个用法）。
        """
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(0, weight=1)

        card = U.Card(pg, title="服务器证书",
                      hint="自签名或导入 · 给群晖 / nginx / IIS 这类 Web 服务用")
        card.grid(row=0, column=0, sticky="nsew")
        b = card.body

        bar = U.wframe(b, "card")
        bar.pack(fill="x", pady=(0, U.px(8)))
        U.RoundButton(bar, "新建自签名", self.cert_new, variant="primary",
                      height=30, padx=16).pack(side="left")
        for text, cmd in (("导入…", self.cert_import),
                          ("导出…", self.cert_export),
                          ("上传到设备", self.cert_upload)):
            U.RoundButton(bar, text, cmd, variant="secondary",
                          height=30, padx=14).pack(side="left", padx=(U.px(6), 0))
        U.RoundButton(bar, "删除", self.cert_delete, variant="ghost",
                      height=30, padx=12).pack(side="left", padx=(U.px(6), 0))
        self.lbl_cert_sum = U.wlabel(bar, "", "dim", U.FONT_XS)
        self.lbl_cert_sum.pack(side="right")

        # 说明放在动作条下面、表格上面：安全中心这块 holder 本身不滚，
        # 放卡片底部会被 expand 的表格挤出可视区（实测整段看不见）。
        self.lbl_cert_tip = U.wlabel(
            b,
            "自签名证书首次访问会提示「不受信任」—— 把 cert.pem 导入客户端"
            "「受信任的根证书颁发机构」即可。证书里的 SAN 才决定哪些地址受信任，"
            "浏览器不看 CN。",
            "dim", U.FONT_XS, justify="left")
        self.lbl_cert_tip.pack(anchor="w", pady=(0, U.px(6)))

        self.tbl_cert = DataTable(
            b, CERT_COLS, height=10, searchable=True,
            empty_title="证书库是空的",
            empty_hint="「新建自签名」给内网服务签一张；"
                       "「导入…」把已有的证书收起来。",
            on_activate=self.cert_detail)
        self.tbl_cert.pack(fill="both", expand=True)
        self.tbl_cert.set_menu([
            ("查看证书内容", self.cert_detail, lambda r: bool(r)),
            ("导出到目录…", lambda r: self.cert_export(r), lambda r: bool(r)),
        ])
        self.cert_refresh()

    # ---- 库
    def cert_store(self) -> SC.Store:
        root = getattr(self, "data_dir", None)
        if root is None:
            from pathlib import Path as _P
            root = _P(__file__).resolve().parent.parent / "data"
        return SC.Store(root / "certs")

    def cert_refresh(self):
        tbl = getattr(self, "tbl_cert", None)
        if tbl is None:
            return
        try:
            rows = self.cert_store().list()
        except Exception as e:  # noqa: BLE001
            tbl.set_empty("读证书库失败", str(e))
            return
        out = []
        for r in rows:
            left = r.get("days_left", 0)
            out.append({
                "name": r.get("label") or r.get("name", ""),
                "cn": r.get("cn", "") or "（读不出来）",
                "sans": ", ".join(r.get("sans") or []) or "—",
                "not_after": r.get("not_after", ""),
                "days": ("已过期" if left < 0 else f"{left} 天"),
                "key": "有" if r.get("has_key") else "无",
                "_raw": r,
            })
        tbl.set_rows(out)
        if out:
            exp = sum(1 for r in rows if r.get("days_left", 0) < 0)
            soon = sum(1 for r in rows
                       if 0 <= r.get("days_left", 0) <= 30)
            msg = f"{len(out)} 张证书"
            if soon:
                msg += f" · {soon} 张 30 天内到期"
            if exp:
                msg += f" · {exp} 张已过期"
            self.lbl_cert_sum.configure(text=msg)
        else:
            self.lbl_cert_sum.configure(text="")

    def _cert_sel(self, row=None) -> dict | None:
        row = row if isinstance(row, dict) and row.get("_raw") else \
            (self.tbl_cert.selected() or {})
        return (row or {}).get("_raw")

    # ---- 新建
    def cert_new(self):
        data = forms.FormDialog.open(
            self, "新建自签名服务器证书",
            [{"key": "name", "label": "库里叫它", "required": True,
              "default": "web", "width": 28,
              "hint": "只是本机文件名，随便起"},
             {"key": "cn", "label": "名称 (CN)", "required": True,
              "default": "nas.local", "width": 28,
              "hint": "一般写域名；IP 也可以"},
             {"key": "sans", "label": "SAN", "kind": "textarea", "lines": 2,
              "default": "nas.local, 192.168.1.10", "width": 34,
              "hint": "用哪些地址访问就写哪些，逗号或换行分隔。"
                      "**浏览器只认这里**，不认 CN。",
              "hint_below": True},
             {"key": "days", "label": "有效期(天)", "default": "825",
              "width": 10, "hint": "825 天是浏览器接受的上限"},
             {"key": "kind", "label": "密钥类型", "kind": "select",
              "options": [("rsa", "RSA（兼容最好）"), ("ec", "ECDSA（更快更小）")],
              "default": "rsa"},
             {"key": "bits", "label": "RSA 长度", "kind": "select",
              "options": ["2048", "3072", "4096"], "default": "2048"}],
            subtitle="私钥只留在本机 data/certs 下，上传要你自己点。",
            submit="生成")
        if not data:
            return
        name = data["name"].strip() or data["cn"].strip()
        cn = data["cn"].strip()
        sans = SC.split_sans(data.get("sans") or cn)
        try:
            days = max(1, int(data.get("days") or 825))
        except ValueError:
            days = 825
        bits = int(data.get("bits") or 2048)
        kind = data.get("kind") or "rsa"
        self.toast("正在生成证书…", "info")

        def work():
            cert, key = SC.make_selfsigned(cn, sans, days=days, bits=bits,
                                            kind=kind)
            return self.cert_store().add(name, cert, key,
                                         meta={"days": days, "kind": kind})

        def ok(row):
            self.cert_refresh()
            self.toast(f"已生成 {row.get('name')}（{len(row.get('sans') or [])} "
                       f"个 SAN，{row.get('not_after')} 到期）", "ok")
            self.tbl_cert.select_row(
                lambda r: r.get("name") == row.get("name"))

        self.bg(work, ok=ok, err=lambda m: self.toast(f"生成失败：{m}", "err"))

    # ---- 导入
    def cert_import(self):
        from tkinter import filedialog
        cert_path = filedialog.askopenfilename(
            parent=self, title="选择证书（PEM / CRT）",
            filetypes=[("证书", "*.pem *.crt *.cer"), ("全部文件", "*.*")])
        if not cert_path:
            return
        try:
            cert_text = open(cert_path, "r", encoding="utf-8",
                             errors="replace").read()
        except OSError as e:
            return self.toast(f"读不了文件：{e}", "err")
        try:
            info = SC.parse_pem(cert_text)
        except SC.CertError as e:
            return self.toast(str(e), "err")
        data = forms.FormDialog.open(
            self, "导入证书",
            [{"key": "name", "label": "库里叫它", "required": True,
              "default": info.get("cn") or "imported", "width": 28},
             {"key": "key", "label": "私钥文件", "kind": "file", "width": 34,
              "hint": "可不填（只导入证书时）", "hint_below": True},
             {"key": "over", "label": "同名覆盖", "kind": "check",
              "default": False}],
            subtitle=f"CN={info.get('cn')} · 到期 {info.get('not_after')}"
                     f" · SAN {', '.join(info.get('sans') or []) or '（无）'}",
            submit="导入")
        if not data:
            return
        key_text = ""
        kp = (data.get("key") or "").strip()
        if kp:
            try:
                key_text = open(kp, "r", encoding="utf-8",
                                errors="replace").read()
            except OSError as e:
                return self.toast(f"读不了私钥：{e}", "err")
        try:
            row = self.cert_store().add(data["name"], cert_text, key_text,
                                        meta={"source": cert_path},
                                        overwrite=bool(data.get("over")))
        except SC.CertError as e:
            return self.toast(str(e), "err")
        self.cert_refresh()
        self.toast(f"已导入 {row.get('name')}（私钥{'有' if key_text else '无'}）",
                   "ok")

    # ---- 导出
    def cert_export(self, row=None):
        rec = self._cert_sel(row)
        if not rec:
            return self.toast("先在列表里选一张证书", "warn")
        from tkinter import filedialog
        d = filedialog.askdirectory(parent=self, title="导出到哪个目录")
        if not d:
            return
        try:
            made = self.cert_store().export(rec["name"], d)
        except (OSError, SC.CertError) as e:
            return self.toast(f"导出失败：{e}", "err")
        self.toast(f"已导出 {len(made)} 个文件到 {d}", "ok")

    # ---- 删除
    def cert_delete(self, row=None):
        rec = self._cert_sel(row)
        if not rec:
            return self.toast("先在列表里选一张证书", "warn")
        if not forms.confirm(
                self, "删除证书",
                f"要删除「{rec['name']}」吗？私钥会一起删掉，删了没法恢复。\n"
                "（已经部署到设备上的那份不受影响）", danger=True,
                ok_text="删除"):
            return
        if self.cert_store().remove(rec["name"]):
            self.cert_refresh()
            self.toast(f"已删除 {rec['name']}", "ok")

    # ---- 查看
    def cert_detail(self, row=None):
        rec = self._cert_sel(row)
        if not rec:
            return
        try:
            cert, key = self.cert_store().read(rec["name"])
        except OSError as e:
            return self.toast(f"读不了：{e}", "err")
        body = [
            f"名称：{rec.get('name')}",
            f"CN：{rec.get('cn')}",
            f"签发者：{rec.get('issuer')}"
            f"{'（自签名）' if rec.get('self_signed') else ''}",
            f"SAN：{', '.join(rec.get('sans') or []) or '（无）'}",
            f"有效期：{rec.get('not_before')} → {rec.get('not_after')}"
            f"（剩 {rec.get('days_left')} 天）",
            f"序列号：{rec.get('serial')}",
            f"SHA256：{rec.get('sha256')}",
            "",
            "—— cert.pem ——",
            cert.strip(),
        ]
        if key:
            body += ["", "—— key.pem（私钥，注意别外传）——", key.strip()]
        forms.InfoDialog.show(self, f"证书 {rec.get('name')}", "\n".join(body),
                              subtitle=SC.deploy_hint(), width=860, height=600)

    # ---- 上传到设备
    def cert_upload(self, row=None):
        rec = self._cert_sel(row)
        if not rec:
            return self.toast("先在列表里选一张证书", "warn")
        sess = getattr(self, "session", None)
        if sess is None or not getattr(sess, "alive", False):
            return self.toast("先连接一台设备（SSH），本机窗口没有上传通道",
                              "warn")
        if not hasattr(sess, "sftp"):
            return self.toast("当前会话不支持 SFTP（Telnet 不行），用 SSH 连接",
                              "warn")
        data = forms.FormDialog.open(
            self, "上传证书到设备",
            [{"key": "dir", "label": "远程目录", "required": True,
              "default": "/tmp", "width": 34,
              "hint": "群晖的证书目录通常是 /usr/syno/etc/certificate/system/default",
              "hint_below": True},
             {"key": "name", "label": "证书文件名", "default": "cert.pem",
              "width": 24},
             {"key": "kname", "label": "私钥文件名", "default": "privkey.pem",
              "width": 24},
             {"key": "withkey", "label": "同时上传私钥（权限 600）",
              "kind": "check", "default": bool(rec.get("has_key"))}],
            subtitle=f"{rec.get('name')} · CN={rec.get('cn')} · "
                     f"{rec.get('not_after')} 到期",
            submit="上传")
        if not data:
            return
        try:
            cert, key = self.cert_store().read(rec["name"])
        except OSError as e:
            return self.toast(f"读不了：{e}", "err")
        rdir = (data.get("dir") or "").strip().rstrip("/") or "/tmp"
        cname = (data.get("name") or "cert.pem").strip()
        kname = (data.get("kname") or "privkey.pem").strip()
        withkey = bool(data.get("withkey")) and bool(key)

        def work():
            sf = sess.sftp()
            made = []
            with sf.open(f"{rdir}/{cname}", "w") as f:
                f.write(cert)
            made.append(f"{rdir}/{cname}")
            if withkey:
                with sf.open(f"{rdir}/{kname}", "w") as f:
                    f.write(key)
                try:
                    sf.chmod(f"{rdir}/{kname}", 0o600)
                except OSError:
                    pass
                made.append(f"{rdir}/{kname}")
            return made

        def ok(made):
            self.toast(f"已上传 {len(made)} 个文件：" + "、".join(made), "ok")
            forms.InfoDialog.show(
                self, "上传完成",
                "已写入：\n" + "\n".join(made) + "\n\n" + SC.deploy_hint(),
                subtitle="证书", width=760, height=360)

        self.bg(work, ok=ok, err=lambda m: self.toast(f"上传失败：{m}", "err"))
