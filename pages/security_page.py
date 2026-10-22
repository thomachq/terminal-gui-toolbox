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

TABS = [("scan", "体检结果"), ("fw", "防火墙"), ("fix", "加固建议")]


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
               "fix": self.sec_fix_box}.get(key)
        if box is not None:
            box.tkraise()
        if key == "fw" and getattr(self, "fw_backend", None) is None:
            self.after(80, self.fw_probe)

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
