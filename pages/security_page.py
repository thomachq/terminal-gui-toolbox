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

import time
import tkinter as tk

import wbtui as U
from core import devicecert as DC
from core import iplib
from core import region as RG
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

REGION_COLS = [
    Column("pick", "", 32, anchor="center"),
    Column("name", "区域", 190),
    Column("group", "分组", 88),
    Column("count", "网段数", 84, anchor="e"),
    Column("note", "说明", 300, stretch=True),
]

CERT_COLS = [
    # 列宽总和压到 ~960 逻辑像素：右边常驻 AI 面板，再宽就会把「用途」列
    # 挤出可视区（实测 1128 时最后一列整列看不见）。名称列会截断，
    # 双击看详情有全名。
    Column("name", "名称", 190),
    # 「来源」是这一页最容易误解的一列：本机库只是工具箱自己的一份收藏，
    # 设备上那份（DSM 控制面板 / fnOS 系统设置）才是真正在用的。
    Column("src", "来源", 76),
    Column("cn", "CN", 130),
    Column("sans", "SAN（域名 / IP）", 200, stretch=True),
    Column("not_after", "到期", 88),
    Column("days", "剩余", 62, anchor="e"),
    Column("key", "私钥", 48, anchor="center"),
    Column("usage", "用途 / 绑定的服务", 166, stretch=True),
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
        #
        # 「区域策略」是防火墙的一部分（按国家/省拒绝），所以**不单独占页签** ——
        # 单独一个顶级页签会让人以为防火墙被替换掉了（用户当场就问
        # 「防火墙功能被你弄没了？」）。这里在防火墙页顶部挂一个入口，
        # 点进去是同一个 holder 里的另一个视图，返回按钮回规则列表。
        self.sec_fw_box = U.wframe(holder, "page")
        self.sec_fw_box.grid(row=0, column=0, sticky="nsew")
        self.sec_fw_box.columnconfigure(0, weight=1)
        self.sec_fw_box.rowconfigure(0, weight=1)
        strip = U.wframe(self.sec_fw_box, "page")
        strip.grid(row=0, column=0, sticky="we")
        self.lbl_fw_sub = U.wlabel(strip, "规则列表", "pagedim", U.FONT_XS)
        self.lbl_fw_sub.pack(side="left")
        U.RoundButton(strip, "区域策略（按国家 / 省）…", self.region_open,
                      variant="secondary", height=28,
                      padx=12).pack(side="right")
        self.fw_host = U.wframe(self.sec_fw_box, "page")
        self.fw_host.grid(row=1, column=0, sticky="nsew")
        self.sec_fw_box.rowconfigure(1, weight=1)
        self.fw_host.columnconfigure(0, weight=1)
        self.fw_host.rowconfigure(1, weight=1)
        self.build_fw(self.fw_host)

        # 证书管理
        self.sec_cert_box = U.wframe(holder, "page")
        self.sec_cert_box.grid(row=0, column=0, sticky="nsew")
        self.build_srvcert(self.sec_cert_box)

        # 区域策略（防火墙的子视图，不占页签）
        self.sec_region_box = U.wframe(holder, "page")
        self.sec_region_box.grid(row=0, column=0, sticky="nsew")
        self.build_region(self.sec_region_box)

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
        self._region_shown = False        # 区域策略是防火墙的子视图
        self.sec_scan_box.tkraise()

    # ------------------------------------------------------------ 页签
    def _sec_tab(self, key: str):
        box = {"scan": self.sec_scan_box, "fw": self.sec_fw_box,
               "cert": getattr(self, "sec_cert_box", None),
               "fix": self.sec_fix_box}.get(key)
        if box is not None:
            box.tkraise()
        if key == "fw":
            # 从别的页签切回「防火墙」= 回到规则列表（区域策略是它的子视图）
            self._region_shown = False
            if getattr(self, "fw_backend", None) is None:
                self.after(80, self.fw_probe)
        if key == "cert":
            self.cert_refresh()
            # 切到这一页就连带读一次设备自己的证书清单 —— 用户报的就是
            # 「页面上看不到设备上明明装着的证书」，不该让他再点一次按钮。
            self.cert_maybe_sync()

    # ---- 防火墙 → 区域策略（子视图）
    def region_open(self):
        if getattr(self, "sec_region_box", None) is None:
            return
        self._region_shown = True
        self.sec_region_box.tkraise()
        if getattr(self, "_reg_caps", None) is None:
            self.after(80, self.region_probe)

    def region_back(self):
        self._region_shown = False
        try:
            self.sec_fw_box.tkraise()
        except Exception:  # noqa: BLE001
            pass

    def _sec_fw_maybe_probe(self):
        """换了设备后把这一页的数据重取一遍。

        防火墙页签要重探后端（否则会拿上一台的后端跑命令）；证书页签要重读
        设备自己的清单（换设备后还显示上一台的证书 = 张冠李戴）。
        """
        try:
            cur = getattr(getattr(self, "seg_sec", None), "current", "") or ""
        except Exception:  # noqa: BLE001
            cur = ""
        if cur == "cert":
            # 设备变了，旧缓存一律作废，强制重读
            self._devcert = {}
            self.after(120, lambda: self.cert_maybe_sync(force=True))
            return
        if cur == "fw" and getattr(self, "_region_shown", False):
            # 人正在区域策略这个子视图里换了设备：能力与「已启用几条」都得重探，
            # 否则会拿上一台的结果去指导这一台，甚至点「撤销」删错对象。
            self._reg_caps = None
            self._reg_state = {}
            self.after(120, self.region_probe)
            return
        if cur != "fw":
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
                      hint="本机库 + 设备清单 · 群晖 DSM / 飞牛 fnOS / nginx / IIS")
        card.grid(row=0, column=0, sticky="nsew")
        b = card.body

        bar = U.wframe(b, "card")
        bar.pack(fill="x", pady=(0, U.px(8)))
        U.RoundButton(bar, "新建自签名", self.cert_new, variant="primary",
                      height=30, padx=16).pack(side="left")
        for text, cmd in (("读取设备证书", self.cert_sync),
                          ("导入…", self.cert_import),
                          ("导出…", self.cert_export),
                          ("上传到设备", self.cert_upload)):
            U.RoundButton(bar, text, cmd, variant="secondary",
                          height=30, padx=14).pack(side="left", padx=(U.px(6), 0))
        U.RoundButton(bar, "删除", self.cert_delete, variant="ghost",
                      height=30, padx=12).pack(side="left", padx=(U.px(6), 0))
        self.lbl_cert_sum = U.wlabel(bar, "", "dim", U.FONT_XS)
        self.lbl_cert_sum.pack(side="right")

        # 说明压在**一行**：这一页上面还有体检卡片和页签，日志面板一开
        # 纵向就只剩一百多像素，说明多一行表格就少一行（实测只剩表头）。
        self.lbl_cert_tip = U.wlabel(
            b,
            "「本机库」是工具箱收藏的证书；连上设备自动带出它自己的清单"
            "（只读、私钥不下发）。自签名首次访问会提示不受信任："
            "把 cert.pem 导入客户端「受信任的根证书颁发机构」—— 浏览器只认 SAN。",
            "dim", U.FONT_XS, justify="left")
        self.lbl_cert_tip.pack(anchor="w", pady=(0, U.px(6)))

        self.tbl_cert = DataTable(
            b, CERT_COLS, height=10, searchable=True,
            empty_title="证书库是空的",
            empty_hint="「新建自签名」给内网服务签一张；"
                       "「导入…」把已有的证书收起来；"
                       "连着设备时点「读取设备证书」读它自己的清单。",
            on_activate=self.cert_detail)
        self.tbl_cert.pack(fill="both", expand=True)
        self.tbl_cert.set_menu([
            ("查看证书内容", self.cert_detail, lambda r: bool(r)),
            ("保存到本机库", self.cert_save_local, self._is_dev_row),
            ("导出到目录…", lambda r: self.cert_export(r), self._is_local_row),
            ("上传到设备…", lambda r: self.cert_upload(r), self._is_local_row),
            ("删除", lambda r: self.cert_delete(r), self._is_local_row),
        ])
        self.cert_refresh()

    # ---- 库
    def cert_store(self) -> SC.Store:
        root = getattr(self, "data_dir", None)
        if root is None:
            from pathlib import Path as _P
            root = _P(__file__).resolve().parent.parent / "data"
        return SC.Store(root / "certs")

    # ---- 渲染
    def cert_refresh(self):
        tbl = getattr(self, "tbl_cert", None)
        if tbl is None:
            return
        local, why = [], ""
        try:
            local = self.cert_store().list()
        except Exception as e:  # noqa: BLE001
            why = f"读本机证书库失败：{e}"
        out = []
        for r in local:
            left = int(r.get("days_left") or 0)
            out.append({
                "name": r.get("label") or r.get("name", ""),
                "src": "本机库",
                "cn": r.get("cn", "") or "（读不出来）",
                "sans": ", ".join(r.get("sans") or []) or "—",
                "not_after": r.get("not_after", ""),
                "days": ("已过期" if left < 0 else f"{left} 天"),
                "key": "有" if r.get("has_key") else "无",
                "usage": str(r.get("usage") or "—"),
                "_raw": {**r, "source": "local"},
            })
        st = getattr(self, "_devcert", None) or {}
        dev_rows = st.get("rows") or []
        for r in dev_rows:
            out.append(self._dev_row(r))
        tbl.set_rows(out)
        if not out and why:
            tbl.set_empty("读证书库失败", why[:300])
        self._cert_sum(local, dev_rows, st.get("why") or why)

    def _dev_row(self, r: dict) -> dict:
        """设备证书 → 表格行。`_raw` 里保留 pem（详情/保存要用）。"""
        left = int(r.get("days_left") or 0)
        svc = [str(s) for s in (r.get("services") or []) if str(s).strip()]
        if len(svc) > 2:
            usage = "、".join(svc[:2]) + f" 等 {len(svc)} 项"
        else:
            usage = "、".join(svc) or "—"
        name = str(r.get("name") or "（无名）")
        if r.get("used"):
            name += "（默认）"
        return {
            "name": name,
            "src": DC.KIND_LABEL.get(str(r.get("kind") or ""), "设备"),
            "cn": r.get("cn") or "（读不出来）",
            "sans": ", ".join(r.get("sans") or []) or "—",
            "not_after": r.get("not_after", ""),
            "days": ("已过期" if left < 0 else f"{left} 天"),
            "key": "有" if r.get("has_key") else "无",
            "usage": usage,
            "_raw": {**r, "source": "device"},
        }

    def _cert_sum(self, local: list[dict], dev_rows: list[dict],
                  why: str = "") -> None:
        lbl = getattr(self, "lbl_cert_sum", None)
        if lbl is None:
            return
        bits = []
        if local:
            bits.append(f"本机库 {len(local)} 张")
        if dev_rows:
            kind = DC.KIND_LABEL.get(str(dev_rows[0].get("kind") or ""), "设备")
            extra = []
            exp = sum(1 for r in dev_rows if int(r.get("days_left") or 0) < 0)
            soon = sum(1 for r in dev_rows
                       if 0 <= int(r.get("days_left") or 0) <= 30)
            if soon:
                extra.append(f"{soon} 张 30 天内到期")
            if exp:
                extra.append(f"{exp} 张已过期")
            bits.append(f"{kind} {len(dev_rows)} 张"
                        + (f"（{'、'.join(extra)}）" if extra else ""))
        elif why:
            one = str(why).splitlines()[0].strip()
            bits.append("设备：读不到（" + (one[:40] + "…" if len(one) > 40
                                          else one) + "）")
        lbl.configure(text=" · ".join(bits))

    def _cert_sel(self, row=None) -> dict | None:
        row = row if isinstance(row, dict) and row.get("_raw") else \
            (self.tbl_cert.selected() or {})
        return (row or {}).get("_raw")

    @staticmethod
    def _raw_of(row) -> dict:
        return (row or {}).get("_raw") or {}

    def _is_dev_row(self, row) -> bool:
        return self._raw_of(row).get("source") == "device"

    def _is_local_row(self, row) -> bool:
        return self._raw_of(row).get("source") == "local"

    # ---------------------------------------------------- 设备证书（只读）
    def _dev_key(self) -> str:
        """会话指纹：换设备就必须重读，同一台别反复读。"""
        s = getattr(self, "session", None)
        if s is None or not getattr(s, "alive", False):
            return ""
        return "{}:{}:{}".format(getattr(s, "host", "") or "",
                                 getattr(s, "port", "") or "",
                                 getattr(s, "user", "") or "")

    def cert_maybe_sync(self, force: bool = False) -> None:
        """切到证书页签时调 —— 有设备就连带读一次，没连就只显示本机库。"""
        key = self._dev_key()
        st = getattr(self, "_devcert", None) or {}
        if not key:
            if st:
                self._devcert = {}
                self.cert_refresh()
            return
        if not force:
            if st.get("key") == key:
                return
            # 同一条会话 8 秒内只试一次：即使上次卡在提权弹窗 / 失败，
            # 也不会切来切去反复连，但过一会儿仍能重试。
            last = getattr(self, "_devcert_try", (None, 0.0))
            if last[0] == key and (time.time() - last[1]) < 8.0:
                return
        self.cert_sync(silent=True, key=key)

    def cert_sync(self, silent: bool = False, key: str = "") -> None:
        """从设备读证书清单（群晖 DSM / 飞牛 fnOS）。只读，不下载私钥。"""
        s = getattr(self, "session", None)
        if s is None or not getattr(s, "alive", False):
            if not silent:
                self.toast("先连一台设备（SSH），才能读它上面的证书", "warn")
            self._devcert = {}
            self.cert_refresh()
            return
        key = key or self._dev_key()
        # 只有 Linux 系设备才有这两套证书模块；连的是 Windows/macOS 时
        # 别去跑 `test -d /usr/syno/...` 再报一句「读不到」——那是噪音。
        try:
            fam = getattr(self._effective_device(), "os_family", "") or ""
        except Exception:  # noqa: BLE001
            fam = "linux"
        if fam and fam != "linux":
            self._devcert = {}
            self.cert_refresh()
            if not silent:
                self.toast("这台设备不是 Linux，暂无证书清单可读"
                           "（目前支持群晖 DSM / 飞牛 fnOS）", "warn")
            return
        self._devcert_try = (key, time.time())
        lbl = getattr(self, "lbl_cert_sum", None)
        if lbl is not None:
            lbl.configure(text="正在读取设备证书…")
        try:
            self.status.set_state("读取设备证书…")
        except Exception:  # noqa: BLE001
            pass

        def work():
            try:
                return {"rows": DC.list_device(s), "why": ""}
            except DC.DeviceCertError as e:
                why = str(e) + (f" —— {e.hint}" if e.hint else "")
                return {"rows": [], "why": why}

        def ok(res):
            rows = res.get("rows") or []
            self._devcert = {"key": key, "rows": rows,
                             "why": res.get("why") or ""}
            self.cert_refresh()
            try:
                if rows:
                    self.status.set_state(f"设备证书已读取（{len(rows)} 张）", "ok")
                else:
                    self.status.set_state("设备证书读不到", "warn")
            except Exception:  # noqa: BLE001
                pass
            if not silent:
                if rows:
                    self.toast(f"已读取 {len(rows)} 张设备证书", "ok")
                else:
                    self.toast(str(res.get("why") or "设备上没读到证书")[:140],
                               "warn")

        def err(m):
            # 提权弹窗被取消时不会走到这里（app 自己处理），这条只管真失败。
            self._devcert = {"key": key, "rows": [], "why": str(m)}
            self.cert_refresh()
            if not silent:
                self.toast(f"读取设备证书失败：{str(m)[:120]}", "err")

        self.bg(work, ok=ok, err=err)

    def cert_save_local(self, row=None) -> None:
        """把设备上那张证书存进本机库（**只存证书，不碰私钥**）。"""
        rec = self._cert_sel(row)
        if not rec or rec.get("source") != "device":
            return self.toast("先在列表里选一张「来源＝群晖 / 飞牛」的证书", "warn")
        pem = str(rec.get("pem") or "")
        if not pem:
            return self.toast("这张证书的内容没读到："
                              + str(rec.get("error") or "权限不足"), "err")
        kind = DC.KIND_LABEL.get(str(rec.get("kind") or ""), "设备")
        stem = str(rec.get("name") or "cert")
        data = forms.FormDialog.open(
            self, "保存到本机库",
            [{"key": "name", "label": "库里叫它", "required": True,
              "width": 30,
              "default": f"{kind}-{stem}",
              "hint": "存的是证书文件（cert.pem）。设备上的私钥不会被下载，"
                      "需要私钥请到设备自己的管理界面导出。",
              "hint_below": True}],
            subtitle=f"{kind} · {stem} · {rec.get('not_after')} 到期",
            submit="保存")
        if not data:
            return
        name = str(data.get("name") or "").strip() or stem
        try:
            rec2 = self.cert_store().add(
                name, pem, "", meta={"from_device": kind,
                                     "device_path": rec.get("where") or ""})
        except SC.CertError as e:
            return self.toast(str(e), "err")
        except OSError as e:
            return self.toast(f"写不进本机库：{e}", "err")
        self.cert_refresh()
        self.toast(f"已存入本机库：{rec2.get('name')}（只有证书，无私钥）", "ok")

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
        if rec.get("source") == "device":
            pem = str(rec.get("pem") or "")
            if not pem:
                return self.toast("这张证书的内容没读到："
                                  + str(rec.get("error") or "权限不足"), "err")
            from pathlib import Path as _P
            out = _P(d) / f"{SC._safe_name(rec.get('name') or 'cert')}.crt"
            try:
                out.write_text(pem, encoding="utf-8")
            except OSError as e:
                return self.toast(f"导出失败：{e}", "err")
            return self.toast(f"已导出证书到 {out}（私钥留在设备上，没下载）",
                              "ok")
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
        if rec.get("source") == "device":
            return self.toast(
                "这是设备上的证书，这里只读 —— 要删请在设备自己的"
                "「证书」界面删（群晖：控制面板 → 安全性 → 证书）", "warn")
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
        dev = rec.get("source") == "device"
        if dev:
            cert = str(rec.get("pem") or "")
            key = ""
            if not cert:
                return self.toast("这张证书的内容没读到："
                                  + str(rec.get("error") or "权限不足"), "err")
        else:
            try:
                cert, key = self.cert_store().read(rec["name"])
            except OSError as e:
                return self.toast(f"读不了：{e}", "err")
        body = []
        if dev:
            body.append(
                "来源：设备（" + DC.KIND_LABEL.get(str(rec.get("kind")), "设备")
                + " · 只读）" + ("　【当前默认证书】" if rec.get("used") else ""))
        else:
            body.append("来源：本机库")
        body += [
            f"名称：{rec.get('name')}",
            f"CN：{rec.get('cn')}",
            f"签发者：{rec.get('issuer')}"
            f"{'（自签名）' if rec.get('self_signed') else ''}",
            f"SAN：{', '.join(rec.get('sans') or []) or '（无）'}",
            f"有效期：{rec.get('not_before')} → {rec.get('not_after')}"
            f"（剩 {rec.get('days_left')} 天）",
            f"序列号：{rec.get('serial')}",
            f"SHA256：{rec.get('sha256')}",
        ]
        if dev:
            if rec.get("where"):
                body.append(f"设备上的位置：{rec.get('where')}")
            if rec.get("services"):
                body.append("绑定的服务：" + "、".join(rec.get("services") or []))
            body.append("私钥：留在设备上（本工具只读证书，不下载私钥）")
        body += ["", "—— cert.pem ——", cert.strip()]
        if key:
            body += ["", "—— key.pem（私钥，注意别外传）——", key.strip()]
        forms.InfoDialog.show(self, f"证书 {rec.get('name')}", "\n".join(body),
                              subtitle=SC.deploy_hint(), width=860, height=600)

    # ---- 上传到设备
    def cert_upload(self, row=None):
        rec = self._cert_sel(row)
        if not rec:
            return self.toast("先在列表里选一张证书", "warn")
        if rec.get("source") == "device":
            return self.toast(
                "这张本来就在设备上 —— 要从本机往设备装，请先"
                "「保存到本机库」再选本机那张上传", "warn")
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

    # ============================================================ 区域策略
    def build_region(self, pg):
        """区域策略：把「某个省 / 国家」做成一张 nftables 独立表。

        为什么单独一页而不是塞进防火墙表单：一个省就是两万多条网段，
        逐条下发既慢又装不下（群晖每个配置只允许 100 条规则）。nft 的
        interval set 能把它们压成一条规则、一次事务生效，值得单独一块地方。
        详见 core/region.py 文件头。
        """
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(0, weight=1)

        card = U.Card(pg, title="区域策略",
                      hint="防火墙的一部分 · nftables 独立表 · 一条规则顶几万条网段")
        card.grid(row=0, column=0, sticky="nsew")
        b = card.body

        bar = U.wframe(b, "card")
        bar.pack(fill="x", pady=(0, U.px(8)))
        U.RoundButton(bar, "← 返回规则列表", self.region_back, variant="ghost",
                      height=30, padx=10).pack(side="left", padx=(0, U.px(8)))
        U.RoundButton(bar, "检测设备", self.region_probe, variant="secondary",
                      height=30, padx=14).pack(side="left")
        U.RoundButton(bar, "应用策略", self.region_apply, variant="primary",
                      height=30, padx=16).pack(side="left", padx=(U.px(6), 0))
        U.RoundButton(bar, "撤销（删表）", self.region_clear, variant="ghost",
                      height=30, padx=12).pack(side="left", padx=(U.px(6), 0))
        U.RoundButton(bar, "预览脚本", self.region_preview, variant="ghost",
                      height=30, padx=12).pack(side="left", padx=(U.px(6), 0))
        U.RoundButton(bar, "导出脚本…", self.region_export, variant="ghost",
                      height=30, padx=12).pack(side="left", padx=(U.px(6), 0))
        self.badge_reg = U.Pill(bar, "未检测", "mute", height=24)
        self.badge_reg.pack(side="right")
        self.lbl_reg_sum = U.wlabel(bar, "", "dim", U.FONT_XS)
        self.lbl_reg_sum.pack(side="right", padx=(0, U.px(10)))

        # 只做「拒绝」这件事必须写在脸上：accept 在 priority -10 这一层不是
        # 终局，系统自己的链照样能把包丢掉 —— 写成「放行」就是骗人。
        self.lbl_reg_tip = U.wlabel(
            b,
            "只做「拒绝」：勾选区域 → 点「应用策略」，来自这些区域的包直接丢。"
            "「放行」在这里做不出真效果（本表 accept 不是终局，系统链仍可拒绝），"
            "所以不提供。只动 tb_region 这一张表，系统规则一个字节都不改。",
            "dim", U.FONT_XS, justify="left")
        self.lbl_reg_tip.pack(anchor="w", pady=(0, U.px(6)))

        self.tbl_region = DataTable(
            b, REGION_COLS, height=10, searchable=True,
            search_hint="搜省份 / 国家…",
            empty_title="地址库是空的",
            empty_hint="到「防火墙 → 地址库」点一次更新（约 3 分钟），"
                       "就有省级与国家数据了。")
        self.tbl_region.pack(fill="both", expand=True)
        self.tbl_region.tree.bind("<Button-1>", self._region_click, add="+")

        self._reg_picked = {}      # key(group:code) -> {"item","group"}
        self._reg_caps = None
        self._reg_state = {}
        self.region_render()
    # ---- 列表
    def _region_items(self) -> list[tuple]:
        out = [(it, "province", "中国省级") for it in iplib.province_items()]
        out += [(it, "country", "国家/地区") for it in iplib.country_items()]
        return out

    def region_render(self):
        tbl = getattr(self, "tbl_region", None)
        if tbl is None:
            return
        rows = []
        for it, group, glabel in self._region_items():
            key = f"{group}:{it.key}"
            rows.append({
                "pick": "✓" if key in self._reg_picked else "",
                "name": it.name,
                "group": glabel,
                "count": f"{it.count:,}" if it.cidrs else "—",
                "note": it.note or ("没有网段数据，先更新地址库"
                                    if not it.cidrs else ""),
                "_key": key,
                "_item": it,
                "_group": group,
            })
        tbl.set_rows(rows)
        self._region_sum()

    def _region_sum(self):
        n = len(self._reg_picked)
        if not n:
            self.lbl_reg_sum.configure(text="点左边的勾选列选区域，可多选")
            return
        raw = sum(int(v["item"].count) for v in self._reg_picked.values())
        self.lbl_reg_sum.configure(
            text=f"已选 {n} 个区域 · 原始 {raw:,} 条网段（下发前会合并）")

    def _region_click(self, event):
        col = self.tbl_region.column_at(event.x)
        if col != "pick":
            return None
        row = self.tbl_region.row_at(event.y)
        if not row or not row.get("_key"):
            return None
        key = str(row["_key"])
        it = row.get("_item")
        if key in self._reg_picked:
            self._reg_picked.pop(key, None)
        else:
            if it is not None and not it.cidrs:
                self.toast(f"「{it.name}」还没有网段数据，先更新地址库", "warn")
                return "break"
            self._reg_picked[key] = {"item": it,
                                     "group": str(row.get("_group") or "")}
        self.region_render()
        return "break"

    def _region_picks(self) -> list[dict]:
        return [{"key": v["item"].key, "name": v["item"].name,
                 "group": v["group"]}
                for v in self._reg_picked.values()]

    # ---- 设备
    def _region_session(self):
        s = getattr(self, "session", None)
        if s is None or not getattr(s, "alive", False):
            self.toast("先在「主机管理」连一台设备（SSH），本机没有 nft 可管",
                       "warn")
            return None
        return s

    def region_probe(self):
        s = self._region_session()
        if s is None:
            self.badge_reg.set("未连接", "mute")
            return
        self.badge_reg.set("检测中…", "mute")

        def work():
            caps = RG.probe(s)
            st = RG.state(s, caps) if caps.nft else {}
            return caps, st

        def ok(res):
            caps, st = res
            self._reg_caps, self._reg_state = caps, st
            self.badge_reg.set(caps.label, "ok" if caps.ok else "warn")
            self.lbl_reg_tip.configure(
                text=caps.summary() + "　当前：" + RG.describe(st) +
                "　（只动 tb_region 这张表）")

        self.bg(work, ok=ok,
                err=lambda m: self.toast(f"检测失败：{m}", "err"))

    # ---- 应用
    def region_apply(self):
        s = self._region_session()
        if s is None:
            return
        if not self._reg_picked:
            return self.toast("先勾选要拒绝的区域", "warn")
        caps = self._reg_caps or RG.probe(s)
        if not caps.ok:
            return self.toast("这台设备不能自动下发：" +
                              (caps.why or "未知原因") + " —— 可以「导出脚本」",
                              "warn")
        try:
            plan = RG.make_plan(self._region_picks())
        except RG.RegionError as e:
            return self.toast(str(e) + ("：" + e.hint if e.hint else ""), "err")

        # 防自锁：这条策略会不会把当前连接一起封掉
        try:
            addrs = RG.peer_addrs(s)
        except Exception:  # noqa: BLE001
            addrs = [str(getattr(s, "host", "") or "")]
        bad = RG.lockout(plan, addrs)
        if bad:
            forms.InfoDialog.show(
                self, "已阻止：这条策略会把自己关在门外",
                bad + "\n\n更安全的做法：\n"
                      "· 先在一台能物理接触的设备上试；\n"
                      "· 或者换成不含自己出口 IP 的区域（例如只封某个国家，"
                      "但你的出口不在其中）；\n"
                      "· 只想看命令的话用「预览脚本」，自己挑时间跑。",
                subtitle="没有下发任何东西", width=720, height=380)
            return

        if not forms.confirm(
                self, "下发区域策略",
                f"将对这台设备生效：\n\n{plan.summary()}\n\n"
                f"动作：{RG.VERDICT_LABEL.get(plan.verdict, plan.verdict)}\n"
                "只改 tb_region 这张表，系统其它防火墙规则不动；"
                "已建立的连接保持不断。\n\n确定下发吗？",
                danger=True, ok_text="下发"):
            return

        self.toast(f"正在下发 {plan.total:,} 条网段…")

        def work():
            return RG.apply(s, plan, caps)

        def ok(res):
            rc, body, err = res
            if rc != 0:
                self.toast(f"下发失败：{(err or body)[:200]}", "err")
                return
            self.toast(f"已生效：{plan.summary()}", "ok")
            self._audit("region.apply", "ok", plan.summary(), s.label)
            self.region_probe()

        self.bg(work, ok=ok,
                err=lambda m: self.toast(f"下发失败：{m}", "err"))

    # ---- 撤销
    def region_clear(self):
        s = self._region_session()
        if s is None:
            return
        caps = self._reg_caps or RG.probe(s)
        if not caps.ok:
            return self.toast("这台设备不能自动改：" + (caps.why or ""), "warn")
        if not forms.confirm(
                self, "撤销区域策略",
                "把 tb_region 整张表删掉 —— 机器上没有别的规则会被动。\n"
                "（删完之后本页显示的所有拒绝策略立即失效。）",
                danger=True, ok_text="删表"):
            return

        def work():
            return RG.clear(s, caps)

        def ok(res):
            rc, body, err = res
            if rc != 0:
                self.toast(f"撤销失败：{(err or body)[:200]}", "err")
                return
            self.toast("已撤销（tb_region 表已删除）", "ok")
            self._audit("region.clear", "ok", "删除 tb_region 表", s.label)
            self.region_probe()

        self.bg(work, ok=ok,
                err=lambda m: self.toast(f"撤销失败：{m}", "err"))

    # ---- 预览 / 导出
    def _region_plan_for_view(self):
        if not self._reg_picked:
            self.toast("先勾选区域", "warn")
            return None
        try:
            return RG.make_plan(self._region_picks())
        except RG.RegionError as e:
            self.toast(str(e) + ("：" + e.hint if e.hint else ""), "err")
            return None

    def region_preview(self):
        plan = self._region_plan_for_view()
        if plan is None:
            return
        caps = self._reg_caps
        try:
            if caps is not None and caps.nft:
                text = RG.nft_script(plan, exists=bool(
                    (self._reg_state or {}).get("exists")))
                tip = ("这是要交给 `nft -f` 的整表脚本。整批是一个事务："
                       "任何一行不合法，一个字节都不生效。\n"
                       "只出现 tb_region，不会 flush ruleset。")
            else:
                text = RG.plain_script(plan)
                tip = ("这台设备没有 nft（或没提权），退回 iptables 脚本："
                       "命令很多，中途断了没有回滚。")
        except RG.RegionError as e:
            return self.toast(str(e), "err")
        forms.InfoDialog.show(self, "区域策略脚本",
                              tip + "\n\n" + text,
                              subtitle=plan.summary(),
                              width=880, height=600)

    def region_export(self):
        plan = self._region_plan_for_view()
        if plan is None:
            return
        from tkinter import filedialog
        caps = self._reg_caps
        use_nft = bool(caps is not None and caps.nft)
        name = "region-" + "-".join(
            str(p.get("key")) for p in plan.picks)[:40] + (
            ".nft" if use_nft else ".sh")
        path = filedialog.asksaveasfilename(
            parent=self, title="导出区域策略脚本", initialfile=name,
            defaultextension=".nft" if use_nft else ".sh",
            filetypes=[("nft 脚本", "*.nft"), ("Shell 脚本", "*.sh"),
                       ("全部文件", "*.*")])
        if not path:
            return
        try:
            text = (RG.nft_script(plan, exists=bool(
                (self._reg_state or {}).get("exists"))) if use_nft
                else RG.plain_script(plan))
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except (OSError, RG.RegionError) as e:
            return self.toast(f"导出失败：{e}", "err")
        self.toast(f"已导出 {len(text.splitlines())} 行到 {path}", "ok")
