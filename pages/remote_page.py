# -*- coding: utf-8 -*-
"""远程管理：SSH 服务配置 / SSH 密钥 / RDP / XRDP / VNC。

为什么单独一页：这几件事原来散在「系统信息」和「文件管理」里 ——
用户想开个 RDP 得自己记住 reg 路径和防火墙规则组名，想改 sshd 得
回忆 `PasswordAuthentication` 怎么拼。这页把它们做成「勾选 + 确认命令」
的形态，命令原文始终给用户看，程序不偷偷改。

改 sshd 有一条铁律：**先 `sshd -t` 再 reload，失败当场回滚**。
我们正是靠 SSH 在操作远端，写错一行就是把自己关在门外。
"""
from __future__ import annotations

import tkinter as tk

from tkinter import ttk

import wbtui as U
from core import remote as R
from ui import forms
from ui.table import Segmented

TABS = (("ssh", "SSH 服务"), ("key", "SSH 密钥"),
        ("rdp", "RDP"), ("xrdp", "XRDP"))

class RemotePage:
    # ============================================================ 构建
    def build_remote(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)
        self._rm_tab = TABS[0][0]
        self.rm_seg = Segmented(pg, list(TABS), command=self._rm_switch)
        self.rm_seg.grid(row=0, column=0, sticky="w")

        holder = U.wframe(pg, "page")
        holder.grid(row=1, column=0, sticky="nsew", pady=(U.px(8), 0))
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self._rm_boxes = {}
        for key in ("ssh", "key", "rdp", "xrdp"):
            box = U.wframe(holder, "page")
            box.grid(row=0, column=0, sticky="nsew")
            self._rm_boxes[key] = box
        self._build_rm_ssh(self._rm_boxes["ssh"])
        self._build_rm_key(self._rm_boxes["key"])
        self._build_rm_rdp(self._rm_boxes["rdp"])
        self._build_rm_xrdp(self._rm_boxes["xrdp"])
        self._rm_boxes["ssh"].tkraise()
        self._rm_cfg: dict = {}
        self._rm_eff: dict = {}

    def _rm_switch(self, key: str):
        self._rm_tab = key
        box = self._rm_boxes.get(key)
        if box is not None:
            box.tkraise()

    # ---- 会话
    def _rm_sess(self):
        s = getattr(self, "session", None)
        if s is None or not getattr(s, "alive", False):
            self.toast("先在主机管理里连接一台设备", "warn")
            return None
        return s

    def _rm_run(self, cmd: str, timeout: int = 60, admin: bool = True):
        """先试提权通道，不行再直连。改系统配置基本都要管理员。"""
        s = self._rm_sess()
        if s is None:
            raise RuntimeError("未连接")
        if admin and hasattr(s, "run_admin"):
            try:
                return s.run_admin(cmd, timeout=timeout)
            except Exception:  # noqa: BLE001
                pass
        return s.run(cmd, timeout=timeout)

    def _rm_target(self) -> str:
        s = getattr(self, "session", None)
        if s is None:
            return "未连接"
        for name in ("label", "host", "hostname"):
            v = getattr(s, name, "")
            if callable(v):
                try:
                    v = v()
                except Exception:  # noqa: BLE001
                    v = ""
            if v:
                return str(v)
        return "当前设备"

    # ============================================================ SSH 服务
    def _build_rm_ssh(self, box):
        box.columnconfigure(0, weight=1)
        c = U.Card(box, title="SSH 服务配置",
                   hint="读的是设备上的 sshd_config，改完会先 sshd -t 校验")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        bar = U.wframe(b, "card")
        bar.pack(fill="x")
        U.RoundButton(bar, "读取当前配置", self.rm_ssh_read, variant="primary",
                      height=30, padx=16).pack(side="left")
        U.RoundButton(bar, "图形化修改", self.rm_ssh_edit, variant="secondary",
                      height=30, padx=14).pack(side="left", padx=(U.px(6), 0))
        U.RoundButton(bar, "一键：仅允许密钥登录", self.rm_ssh_keyonly,
                      variant="secondary", height=30, padx=14).pack(
            side="left", padx=(U.px(6), 0))
        self.rm_ssh_state = U.Pill(bar, "未读取", "mute")
        self.rm_ssh_state.pack(side="right")

        self.rm_ssh_rows = U.wframe(b, "card")
        self.rm_ssh_rows.pack(fill="x", pady=(U.px(10), 0))
        U.wlabel(self.rm_ssh_rows,
                 "点「读取当前配置」看这台设备的 SSH 设置。", "dim",
                 U.FONT_S).pack(anchor="w")
        self.rm_ssh_note = U.wlabel(b, "", "dim", U.FONT_XS, justify="left")
        self.rm_ssh_note.pack(anchor="w", pady=(U.px(8), 0))

        c2 = U.Card(box, title="这台设备上的远程入口",
                    hint="按实际装的显示（sshd / xrdp / vnc）")
        c2.grid(row=1, column=0, sticky="we", pady=(U.px(10), 0))
        b2 = c2.body
        r2 = U.wframe(b2, "card")
        r2.pack(fill="x")
        U.RoundButton(r2, "检测", self.rm_probe, variant="secondary",
                      height=30, padx=16).pack(side="left")
        self.rm_probe_lbl = U.wlabel(r2, "还没检测", "dim", U.FONT_XS)
        self.rm_probe_lbl.pack(side="left", padx=(U.px(10), 0))

    def rm_ssh_read(self):
        if self._rm_sess() is None:
            return
        self.rm_ssh_state.set("读取中…", "mute")
        paths = ["/etc/ssh/sshd_config", "/etc/sshd_config",
                 "/usr/local/etc/ssh/sshd_config",
                 "/etc/openssh/sshd_config"]

        def work():
            for p in paths:
                rc, out, err = self._rm_run(R.sshd_read_cmd(p), timeout=30,
                                            admin=False)
                if rc == 0 and out.strip():
                    return p, out
            return "", ""

        def ok(res):
            path, text = res
            if not text.strip():
                self.rm_ssh_state.set("没读到配置", "err")
                self.rm_ssh_note.configure(
                    text="四个常见路径都没有 sshd_config。"
                         "可能这台设备的 SSH 是 dropbear（OpenWrt），"
                         "它没有 sshd_config，本页的图形化修改用不上。")
                return
            self._rm_path = path
            self._rm_cfg = R.parse_sshd(text)
            self._rm_eff = R.sshd_effective(self._rm_cfg)
            ko = R.key_only(self._rm_cfg)
            self.rm_ssh_state.set("仅密钥登录" if ko else "允许密码登录",
                                  "ok" if ko else "warn")
            self._rm_ssh_render(path)
            self.toast(f"已读取 {path}", "ok")

        self.bg(work, ok=ok, err=lambda m: (
            self.rm_ssh_state.set("读取失败", "err"),
            self.toast(f"读取失败：{m}", "err")))

    def _rm_ssh_render(self, path: str):
        for w in list(self.rm_ssh_rows.winfo_children()):
            w.destroy()
        eff = self._rm_eff
        rows = [
            ("配置文件", path),
            ("端口", eff.get("Port", "22")),
            ("允许密码登录", "是" if str(eff.get(
                "PasswordAuthentication", "yes")).lower() == "yes" else "否"),
            ("允许密钥登录", "是" if str(eff.get(
                "PubkeyAuthentication", "yes")).lower() == "yes" else "否"),
            ("允许 root 登录", eff.get("PermitRootLogin", "prohibit-password")),
            ("最大认证次数", eff.get("MaxAuthTries", "6")),
            ("允许的用户", eff.get("AllowUsers") or "（不限制）"),
            ("X11 转发", eff.get("X11Forwarding", "no")),
        ]
        for i, (k, v) in enumerate(rows):
            U.wlabel(self.rm_ssh_rows, k, "dim", U.FONT_S).grid(
                row=i, column=0, sticky="w", padx=(0, U.px(14)),
                pady=U.px(3))
            color = "card"
            if k.startswith("允许密码") and v == "是":
                color = "warn"
            U.wlabel(self.rm_ssh_rows, str(v), color, U.FONT_S).grid(
                row=i, column=1, sticky="w", pady=U.px(3))
        self.rm_ssh_note.configure(
            text="改这里会先备份 sshd_config、sed 删掉同名行再追加新值，"
                 "然后 sshd -t 校验；校验不过自动还原。需要管理员权限。")

    def rm_ssh_edit(self):
        if not self._rm_cfg:
            return self.toast("先点「读取当前配置」", "warn")
        fields = []
        eff = self._rm_eff
        for key, label, opts, default, note in R.SSHD_FIELDS:
            cur = eff.get(key, default)
            f = {"key": key, "label": label,
                 "default": cur, "width": 26}
            if opts:
                f["kind"] = "select"
                f["options"] = [(o, o) for o in opts]
            if note:
                f["hint"] = note
            fields.append(f)
        data = forms.FormDialog.open(
            self, "图形化修改 sshd_config", fields,
            subtitle=f"目标：{self._rm_target()} · {getattr(self, '_rm_path', '')}\n"
                     "只提交改动过的项；其它行原样保留。",
            submit="保存并生效")
        if not data:
            return
        changed = {k: v for k, v in data.items()
                   if str(v).strip() != str(eff.get(k, "")).strip()
                   and str(v).strip() != ""}
        if not changed:
            return self.toast("没有任何改动", "warn")
        self._rm_apply_sshd(changed, "修改 SSH 配置")

    def rm_ssh_keyonly(self):
        if not self._rm_cfg:
            return self.toast("先点「读取当前配置」", "warn")
        if R.key_only(self._rm_cfg):
            return self.toast("现在已经是「只允许密钥登录」了", "ok")
        changed = dict(R.KEY_ONLY_SET)
        changed["PermitRootLogin"] = "prohibit-password"
        self._rm_apply_sshd(
            changed, "改为仅允许密钥登录",
            extra="⚠ 确认你**已经**能用密钥登录这台设备了，否则改完就再也连不上"
                  "（本窗口的当前连接不会断，但关掉之后需要密钥才能再连）。")

    def _rm_apply_sshd(self, changed: dict, title: str, extra: str = ""):
        try:
            cmds = R.sshd_plan(getattr(self, "_rm_path", "/etc/ssh/sshd_config"),
                               changed, init=getattr(self, "_rm_init", ""))
        except R.RemoteError as e:
            return self.toast(str(e), "err")
        body = "即将执行：\n\n" + "\n\n".join(cmds)
        if extra:
            body += "\n\n" + extra
        if not forms.confirm(self, title, body, danger=True, ok_text="执行"):
            return
        self.toast("正在应用…", "info")

        def work():
            last = (0, "", "")
            for c in cmds:
                last = self._rm_run(c, timeout=90)
                if last[0] != 0:
                    break
            return last

        def ok(res):
            rc, out, err = res
            if rc == 0:
                self.toast("已生效", "ok")
                self.after(400, self.rm_ssh_read)
            else:
                self.toast(f"失败（rc={rc}）：{(err or out).strip()[:200]}", "err")

        self.bg(work, ok=ok, err=lambda m: self.toast(f"执行失败：{m}", "err"))

    def rm_probe(self):
        if self._rm_sess() is None:
            return

        def work():
            return self._rm_run(R.probe_remote_cmd(), timeout=40, admin=False)

        def ok(res):
            rc, out, err = res
            got = R.parse_remote_probe(out)
            self._rm_init = got.get("init") or ""
            bits = []
            if got["sshd"]:
                bits.append("SSH")
            if got["xrdp"]:
                bits.append("XRDP")
            if got["vnc"]:
                bits.append("VNC")
            ports = "、".join(got["ports"][:6])
            self.rm_probe_lbl.configure(
                text=(f"{got['os'] or '未知系统'} · " +
                      ("、".join(bits) if bits else "没有检测到远程服务") +
                      (f" · 监听 {ports}" if ports else "")))

        self.bg(work, ok=ok, err=lambda m: self.toast(f"检测失败：{m}", "err"))

    # ============================================================ SSH 密钥
    def _build_rm_key(self, box):
        box.columnconfigure(0, weight=1)
        c = U.Card(box, title="生成 OpenSSH 密钥对",
                   hint="私钥留在本机 data/keys，公钥随便传")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        r = U.wframe(b, "card")
        r.pack(fill="x")
        self.rm_key_kind = tk.StringVar(value="ed25519")
        ttk.Combobox(r, textvariable=self.rm_key_kind, state="readonly",
                     width=12,
                     values=["ed25519", "rsa"]).pack(side="left")
        U.wlabel(r, "注释", "card", U.FONT_S).pack(side="left", padx=(10, 6))
        self.rm_key_comment = tk.StringVar(value="")
        ttk.Entry(r, textvariable=self.rm_key_comment, width=22).pack(side="left")
        U.RoundButton(r, "生成", self.rm_key_gen, variant="primary",
                      height=30, padx=16).pack(side="left", padx=(10, 0))
        U.RoundButton(r, "导出到文件…", self.rm_key_export,
                      variant="secondary", height=30, padx=14).pack(
            side="left", padx=(6, 0))

        self.rm_key_pub = U.TextBox(b, role="alt", font=U.MONO_S, height=3,
                                    wrap="char")
        self.rm_key_pub.pack(fill="x", pady=(U.px(10), 0))
        self.rm_key_pub.set_text("生成后公钥显示在这里（可以直接复制）。")
        self.rm_key_info = U.wlabel(b, "", "dim", U.FONT_XS)
        self.rm_key_info.pack(anchor="w", pady=(U.px(6), 0))

        c2 = U.Card(box, title="导入公钥（OpenSSH / PuTTY / SSH2）",
                    hint=".ppk、SSH2 公钥、OpenSSH 单行都认")
        c2.grid(row=1, column=0, sticky="we", pady=(U.px(10), 0))
        b2 = c2.body
        self.rm_imp = U.TextBox(b2, role="alt", font=U.MONO_S, height=4,
                                wrap="char")
        self.rm_imp.pack(fill="x")
        self.rm_imp.set_text("把公钥粘在这儿：\n"
                             "· OpenSSH：ssh-ed25519 AAAA… comment\n"
                             "· PuTTY：.ppk 文件内容，或 PuTTYgen 里的 "
                             "「SSH2 public key」整段")
        r2 = U.wframe(b2, "card")
        r2.pack(fill="x", pady=(U.px(8), 0))
        U.RoundButton(r2, "识别", self.rm_imp_parse, variant="secondary",
                      height=30, padx=14).pack(side="left")
        U.RoundButton(r2, "从 .ppk 文件读…", self.rm_imp_file,
                      variant="ghost", height=30, padx=12).pack(
            side="left", padx=(6, 0))
        U.RoundButton(r2, "部署到设备 authorized_keys", self.rm_imp_deploy,
                      variant="primary", height=30, padx=14).pack(side="left",
                                                                 padx=(6, 0))
        self.rm_imp_lbl = U.wlabel(b2, "", "dim", U.FONT_XS, justify="left")
        self.rm_imp_lbl.pack(anchor="w", pady=(U.px(6), 0))

    def _key_dir(self):
        root = getattr(self, "data_dir", None)
        if root is None:
            from pathlib import Path
            root = Path(__file__).resolve().parent.parent / "data"
        d = root / "keys"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def rm_key_gen(self):
        kind = self.rm_key_kind.get() or "ed25519"
        comment = self.rm_key_comment.get().strip() or f"toolbox@{self._rm_target()}"
        try:
            priv, pub = R.gen_keypair(kind, 3072, comment)
        except Exception as e:  # noqa: BLE001
            return self.toast(f"生成失败：{e}", "err")
        import time
        stem = f"id_{kind}_{time.strftime('%Y%m%d-%H%M%S')}"
        d = self._key_dir()
        kp = d / stem
        try:
            kp.write_text(priv, encoding="utf-8")
            import os
            os.chmod(kp, 0o600)
            (d / f"{stem}.pub").write_text(pub + "\n", encoding="utf-8")
        except OSError as e:
            return self.toast(f"写文件失败：{e}", "err")
        self._rm_last_pub = pub
        self.rm_key_pub.set_text(pub)
        self.rm_key_info.configure(
            text=f"已保存 {kp.name}（权限 600）· 指纹 "
                 f"{R.fingerprint_openssh(pub)}")
        self.toast("已生成密钥对", "ok")

    def rm_key_export(self):
        pub = getattr(self, "_rm_last_pub", "")
        if not pub:
            return self.toast("先生成或识别一把公钥", "warn")
        from tkinter import filedialog
        p = filedialog.asksaveasfilename(
            parent=self, title="保存公钥", defaultextension=".pub",
            initialfile="id_key.pub",
            filetypes=[("公钥", "*.pub"), ("全部文件", "*.*")])
        if not p:
            return
        try:
            open(p, "w", encoding="utf-8").write(pub + "\n")
        except OSError as e:
            return self.toast(f"写失败：{e}", "err")
        self.toast(f"已保存 {p}", "ok")

    def rm_imp_parse(self, text: str = "") -> dict | None:
        raw = text or self.rm_imp.get("1.0", "end")
        try:
            info = R.parse_pubkey(raw)
        except R.RemoteError as e:
            self.rm_imp_lbl.configure(text=f"识别失败：{e}")
            return None
        self._rm_imp_info = info
        self._rm_last_pub = R.pub_to_line(info)
        src = {"openssh": "OpenSSH 单行", "ssh2": "SSH2 公钥",
               "ppk": "PuTTY .ppk"}.get(info.get("source"), info.get("source"))
        self.rm_imp_lbl.configure(
            text=f"识别为 {src} · 类型 {info['type']} · 指纹 {info['fingerprint']}"
                 f" · 注释 {info.get('comment') or '（无）'}")
        return info

    def rm_imp_file(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            parent=self, title="选择 PuTTY 密钥或公钥文件",
            filetypes=[("PuTTY / 公钥", "*.ppk *.pub *.txt"), ("全部文件", "*.*")])
        if not p:
            return
        try:
            raw = open(p, "r", encoding="utf-8", errors="replace").read()
        except OSError as e:
            return self.toast(f"读不了：{e}", "err")
        if self.rm_imp_parse(raw):
            self.toast(f"已从 {p} 读出公钥", "ok")

    def rm_imp_deploy(self):
        info = getattr(self, "_rm_imp_info", None)
        if info is None:
            info = self.rm_imp_parse()
            if info is None:
                return
        if self._rm_sess() is None:
            return
        data = forms.FormDialog.open(
            self, "部署公钥",
            [{"key": "user", "label": "目标用户", "required": True,
              "default": "", "width": 20,
              "hint": "留空 = 当前登录用户；要写别人的家目录得用 root",
              "hint_below": True},
             {"key": "check", "label": "先检查一遍（不写入）", "kind": "check",
              "default": True}],
            subtitle=f"公钥类型 {info['type']} · 指纹 {info['fingerprint']}",
            submit="写入")
        if not data:
            return
        user = (data.get("user") or "").strip()
        line = R.pub_to_line(info)
        home = "/root" if user == "root" else (f"/home/{user}" if user else "")
        cmds = R.authorized_keys_append_cmd(line, home=home)
        base = f"/home/{user}" if user else "$HOME"
        if data.get("check"):
            cmds = [f"echo 目标：{user or '当前用户'}",
                    f"test -d {base}/.ssh && echo '.ssh 已存在' "
                    f"|| echo '.ssh 待创建'"] + cmds

        def work():
            last = (0, "", "")
            for c in cmds:
                last = self._rm_run(c, timeout=45)
                if last[0] != 0:
                    break
            return last

        def ok(res):
            rc, out, err = res
            if rc == 0:
                self.toast("公钥已写入 authorized_keys", "ok")
                self.rm_imp_lbl.configure(
                    text=self.rm_imp_lbl.cget("text") + " · 已部署")
            else:
                self.toast(f"写入失败（rc={rc}）：{(err or out).strip()[:200]}",
                           "err")

        self.bg(work, ok=ok, err=lambda m: self.toast(f"写入失败：{m}", "err"))

    # ============================================================ RDP
    def _build_rm_rdp(self, box):
        box.columnconfigure(0, weight=1)
        c = U.Card(box, title="远程桌面（RDP）",
                   hint="Windows 目标 · 改的是注册表 + 防火墙规则")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        r = U.wframe(b, "card")
        r.pack(fill="x")
        U.RoundButton(r, "读取状态", self.rm_rdp_read, variant="primary",
                      height=30, padx=16).pack(side="left")
        U.RoundButton(r, "开启远程桌面", self.rm_rdp_on, variant="secondary",
                      height=30, padx=14).pack(side="left", padx=(6, 0))
        U.RoundButton(r, "关闭", self.rm_rdp_off, variant="ghost",
                      height=30, padx=12).pack(side="left", padx=(6, 0))
        U.RoundButton(r, "改端口…", self.rm_rdp_port, variant="secondary",
                      height=30, padx=14).pack(side="left", padx=(6, 0))
        U.RoundButton(r, "网络级认证(NLA)", self.rm_rdp_nla, variant="ghost",
                      height=30, padx=12).pack(side="left", padx=(6, 0))

        self.rm_rdp_rows = U.wframe(b, "card")
        self.rm_rdp_rows.pack(fill="x", pady=(U.px(10), 0))
        U.wlabel(self.rm_rdp_rows, "点「读取状态」。", "dim",
                 U.FONT_S).pack(anchor="w")
        U.wlabel(b,
                 "RDP 只能用于 Windows 目标（本机 Windows 或通过 WinRM/SSH 连的 "
                 "Windows）。Linux 目标改 XRDP 那一页。",
                 "dim", U.FONT_XS, justify="left").pack(anchor="w",
                                                        pady=(U.px(8), 0))

    def rm_rdp_read(self):
        if self._rm_sess() is None:
            return

        def work():
            return self._rm_run(R.rdp_read_cmd(), timeout=60, admin=False)

        def ok(res):
            rc, out, err = res
            got = R.parse_rdp(out)
            for w in list(self.rm_rdp_rows.winfo_children()):
                w.destroy()
            if "enabled=" not in (out or ""):
                U.wlabel(self.rm_rdp_rows,
                         "读不到 RDP 状态 —— 这台大概不是 Windows，"
                         "或者 PowerShell 不可用。", "dim", U.FONT_S).pack(
                    anchor="w")
                return
            rows = [
                ("远程桌面", "已开启" if got["enabled"] else "已关闭"),
                ("端口", str(got["port"])),
                ("网络级认证 (NLA)",
                 "开启" if got["nla"] else
                 ("关闭" if got["nla"] is False else "未知")),
                ("放行的防火墙规则", f"{got['fw_rules']} 条"),
            ]
            for i, (k, v) in enumerate(rows):
                U.wlabel(self.rm_rdp_rows, k, "dim", U.FONT_S).grid(
                    row=i, column=0, sticky="w", padx=(0, U.px(14)),
                    pady=U.px(3))
                U.wlabel(self.rm_rdp_rows, v, "card", U.FONT_S).grid(
                    row=i, column=1, sticky="w", pady=U.px(3))

        self.bg(work, ok=ok, err=lambda m: self.toast(f"读取失败：{m}", "err"))

    def _rm_rdp_apply(self, plan: list[str], title: str):
        if not plan:
            return self.toast("没有要改的项", "warn")
        if not forms.confirm(self, title,
                             "即将执行：\n\n" + "\n\n".join(plan) +
                             "\n\n需要管理员权限；改完 TermService 会重启，"
                             "已经连着的远程桌面会话会断开。",
                             danger=True, ok_text="执行"):
            return

        def work():
            last = (0, "", "")
            for c in plan:
                last = self._rm_run(c, timeout=120)
                if last[0] != 0:
                    break
            return last

        def ok(res):
            rc, out, err = res
            if rc == 0:
                self.toast("已生效", "ok")
                self.after(500, self.rm_rdp_read)
            else:
                self.toast(f"失败（rc={rc}）：{(err or out).strip()[:200]}", "err")

        self.bg(work, ok=ok, err=lambda m: self.toast(f"执行失败：{m}", "err"))

    def rm_rdp_on(self):
        self._rm_rdp_apply(R.rdp_plan(enable=True), "开启远程桌面")

    def rm_rdp_off(self):
        if not forms.confirm(self, "关闭远程桌面",
                             "关掉之后远程桌面就连不上了（SSH 不受影响）。",
                             danger=True, ok_text="关闭"):
            return
        self._rm_rdp_apply(R.rdp_plan(enable=False), "关闭远程桌面")

    def rm_rdp_port(self):
        data = forms.FormDialog.open(
            self, "修改 RDP 端口",
            [{"key": "port", "label": "新端口", "required": True,
              "default": "3389", "width": 12,
              "hint": "改完客户端要写 地址:端口；防火墙会自动放行新端口",
              "hint_below": True}],
            submit="修改")
        if not data:
            return
        try:
            plan = R.rdp_plan(port=int(data["port"]))
        except (R.RemoteError, ValueError) as e:
            return self.toast(str(e), "err")
        self._rm_rdp_apply(plan, "修改 RDP 端口")

    def rm_rdp_nla(self):
        data = forms.FormDialog.open(
            self, "网络级认证 (NLA)",
            [{"key": "nla", "label": "要求 NLA", "kind": "select",
              "options": [("1", "开启（更安全，老客户端可能连不上）"),
                          ("0", "关闭（兼容老系统，安全性差）")],
              "default": "1"}],
            subtitle="NLA 要求客户端先认证再建会话，能挡掉一部分扫描。",
            submit="应用")
        if not data:
            return
        self._rm_rdp_apply(R.rdp_plan(nla=str(data["nla"]) == "1"),
                           "修改 NLA 设置")

    # ============================================================ XRDP
    def _build_rm_xrdp(self, box):
        box.columnconfigure(0, weight=1)
        c = U.Card(box, title="XRDP（Linux 远程桌面）",
                   hint="改的是 /etc/xrdp/xrdp.ini 的 [Globals] 段")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        r = U.wframe(b, "card")
        r.pack(fill="x")
        U.RoundButton(r, "读取配置", self.rm_xrdp_read, variant="primary",
                      height=30, padx=16).pack(side="left")
        U.RoundButton(r, "修改设置", self.rm_xrdp_edit, variant="secondary",
                      height=30, padx=14).pack(side="left", padx=(6, 0))
        U.RoundButton(r, "安装/启动提示", self.rm_xrdp_help, variant="ghost",
                      height=30, padx=12).pack(side="left", padx=(6, 0))

        self.rm_xrdp_rows = U.wframe(b, "card")
        self.rm_xrdp_rows.pack(fill="x", pady=(U.px(10), 0))
        U.wlabel(self.rm_xrdp_rows, "点「读取配置」。", "dim",
                 U.FONT_S).pack(anchor="w")

        # 别的远程桌面协议：VNC 家族配置各不相同（TigerVNC 走
        # ~/.vnc/xstartup + systemd 实例，x11vnc 全靠命令行参数，
        # vino 是 GNOME 的设置项），硬做一个「统一设置界面」只会
        # 猜错。这里老实说明 + 给启动命令，检测在 SSH 页那个
        # 「远程入口」里已经按实际装的报了。
        c2 = U.Card(box, title="其它远程管理服务",
                    hint="VNC 各家的配置方式差太多，这里给命令不给开关")
        c2.grid(row=1, column=0, sticky="we", pady=(U.px(10), 0))
        U.wlabel(c2.body,
                 "· x11vnc（临时开一个共享屏幕）\n"
                 "    sudo x11vnc -display :0 -auth guess -forever "
                 "-shared -rfbauth ~/.vnc/passwd\n"
                 "· TigerVNC（一人一个独立桌面）\n"
                 "    sudo apt install tigervnc-standalone-server\n"
                 "    vncpasswd && vncserver :1 -geometry 1920x1080\n"
                 "· vino / GNOME 远程桌面：设置 → 共享 → 远程桌面\n"
                 "· Windows 侧还有 WinRM（5985/5986）和 RDP 的 RemoteApp；\n"
                 "  WinRM 在「网络工具」里能直接用，不需要额外开端口。",
                 "dim", U.FONT_XS, justify="left").pack(anchor="w")

    def rm_xrdp_read(self):
        if self._rm_sess() is None:
            return
        paths = ["/etc/xrdp/xrdp.ini", "/usr/local/etc/xrdp/xrdp.ini"]

        def work():
            for p in paths:
                rc, out, _err = self._rm_run(f"cat '{p}' 2>/dev/null",
                                             timeout=30, admin=False)
                if rc == 0 and out.strip():
                    return p, out
            return "", ""

        def ok(res):
            path, text = res
            for w in list(self.rm_xrdp_rows.winfo_children()):
                w.destroy()
            if not text.strip():
                U.wlabel(self.rm_xrdp_rows,
                         "没找到 xrdp.ini —— 这台大概没装 xrdp。"
                         "点「安装/启动提示」看装法。", "dim", U.FONT_S).pack(
                    anchor="w")
                self._rm_xrdp_path = ""
                self._rm_xrdp_cfg = {}
                return
            self._rm_xrdp_path = path
            self._rm_xrdp_cfg = R.parse_ini(text)
            summ = R.xrdp_summary(self._rm_xrdp_cfg)
            rows = [("配置文件", path), ("端口", summ["port"]),
                    ("色深", summ["max_bpp"]),
                    ("安全层", summ["security_layer"]),
                    ("加密级别", summ["crypt_level"])]
            for i, (k, v) in enumerate(rows):
                U.wlabel(self.rm_xrdp_rows, k, "dim", U.FONT_S).grid(
                    row=i, column=0, sticky="w", padx=(0, U.px(14)),
                    pady=U.px(3))
                U.wlabel(self.rm_xrdp_rows, str(v), "card", U.FONT_S).grid(
                    row=i, column=1, sticky="w", pady=U.px(3))

        self.bg(work, ok=ok, err=lambda m: self.toast(f"读取失败：{m}", "err"))

    def rm_xrdp_edit(self):
        cfg = getattr(self, "_rm_xrdp_cfg", {})
        if not cfg:
            return self.toast("先点「读取配置」", "warn")
        summ = R.xrdp_summary(cfg)
        data = forms.FormDialog.open(
            self, "修改 XRDP 设置",
            [{"key": "port", "label": "端口", "default": summ["port"],
              "width": 12, "hint": "默认 3389；和 Windows RDP 同端口，别冲突"},
             {"key": "max_bpp", "label": "色深", "kind": "select",
              "options": ["32", "24", "16", "15"], "default": summ["max_bpp"]},
             {"key": "security_layer", "label": "安全层", "kind": "select",
              "options": [("negotiate", "negotiate（协商，推荐）"),
                          ("rdp", "rdp（老客户端）"),
                          ("tls", "tls（最安全）")],
              "default": summ["security_layer"]},
             {"key": "crypt_level", "label": "加密级别", "kind": "select",
              "options": ["high", "medium", "low", "none"],
              "default": summ["crypt_level"]}],
            subtitle="只改 [Globals] 段里已有的键；改完重启 xrdp。",
            submit="应用")
        if not data:
            return
        changed = {k: v for k, v in data.items()
                   if str(v) != str(summ.get(k, ""))}
        if not changed:
            return self.toast("没有改动", "warn")
        try:
            plan = R.xrdp_plan(getattr(self, "_rm_xrdp_path",
                                       "/etc/xrdp/xrdp.ini"),
                               changed, init=getattr(self, "_rm_init", ""))
        except R.RemoteError as e:
            return self.toast(str(e), "err")
        if not forms.confirm(self, "修改 XRDP 设置",
                             "即将执行：\n\n" + "\n\n".join(plan) +
                             "\n\n重启 xrdp 会断开正在进行的远程桌面会话。",
                             danger=True, ok_text="执行"):
            return

        def work():
            last = (0, "", "")
            for c in plan:
                last = self._rm_run(c, timeout=90)
                if last[0] != 0:
                    break
            return last

        def ok(res):
            rc, out, err = res
            if rc == 0:
                self.toast("已生效", "ok")
                self.after(500, self.rm_xrdp_read)
            else:
                self.toast(f"失败（rc={rc}）：{(err or out).strip()[:200]}", "err")

        self.bg(work, ok=ok, err=lambda m: self.toast(f"执行失败：{m}", "err"))

    def rm_xrdp_help(self):
        forms.InfoDialog.show(
            self, "XRDP 安装与启动",
            "Debian / Ubuntu：\n"
            "  sudo apt install xrdp\n"
            "  sudo systemctl enable --now xrdp\n"
            "  # 用 GNOME 的话还要让它用 xorgxrdp：\n"
            "  sudo apt install xorgxrdp\n"
            "  # 普通用户登录进桌面后要赋权（否则黑屏）：\n"
            "  echo 'allowed_users=anybody' | sudo tee /etc/X11/Xwrapper.config\n\n"
            "RHEL / Rocky / Alma：\n"
            "  sudo dnf install xrdp\n"
            "  sudo systemctl enable --now xrdp\n"
            "  sudo firewall-cmd --add-port=3389/tcp --permanent\n"
            "  sudo firewall-cmd --reload\n\n"
            "Arch：\n"
            "  sudo pacman -S xrdp\n"
            "  sudo systemctl enable --now xrdp\n\n"
            "看不到桌面就检查 ~/.xsession 和 /etc/xrdp/startwm.sh —— "
            "xrdp 不会读你 SSH 登录用的 shell 配置。",
            subtitle="提示", width=780, height=480)
