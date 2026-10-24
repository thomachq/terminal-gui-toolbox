# -*- coding: utf-8 -*-
"""MCP 库：登记本机的 MCP 服务器，把它们的工具借给智能运维助手。

和「技能库」的区别（这两个页签挨着，必须一眼分得清）：
  · 技能库 = 文本 SOP，进提示词，模型照着自己做；
  · MCP 库  = 真进程/真接口，提供**可调用的工具**，模型说要调就真的跑。

零安装铁律不受影响：这些进程起在**本机**，被管设备上什么都不装。
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, ttk

import wbtui as U
from core import mcp
from ui import forms

T_LABEL = {"stdio": "stdio（起子进程）", "http": "http（远程/本机端口）"}


class MCPPanel(tk.Frame):
    """左清单 / 右表单。"""

    def __init__(self, master, app, on_close=None):
        super().__init__(master, bg=U.T.page, bd=0)
        self.app = app
        self.store = app.mcp_store
        self.pool = app.mcp_pool
        self.on_close = on_close
        self._rows: list[dict] = []
        self._cur: dict | None = None

        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(14), pady=U.px(12))

        head = U.wframe(wrap, "page")
        head.pack(fill="x")
        U.wlabel(head, "MCP 库", "page", U.FONT_T).pack(side="left")
        self.lbl_info = U.wlabel(head, "", "pagedim", U.FONT_XS)
        self.lbl_info.pack(side="left", padx=(U.px(10), 0))
        if on_close is not None:
            U.RoundButton(head, "关闭", on_close, variant="secondary",
                          height=30, padx=16).pack(side="right")

        bar = U.wframe(wrap, "page")
        bar.pack(fill="x", pady=(U.px(10), 0))
        U.RoundButton(bar, "新建服务器", self.add_new, variant="primary",
                      height=30, padx=14).pack(side="left")
        U.RoundButton(bar, "从 mcp.json 导入…", self.import_file,
                      variant="ghost", height=30, padx=14).pack(
            side="left", padx=(U.px(8), 0))
        U.RoundButton(bar, "刷新", self.reload, variant="ghost",
                      height=30, padx=14).pack(side="left", padx=(U.px(6), 0))
        U.RoundButton(bar, "全部启动", self.start_all, variant="ghost",
                      height=30, padx=14).pack(side="right")

        body = U.wframe(wrap, "page")
        body.pack(fill="both", expand=True, pady=(U.px(10), 0))

        left = tk.Frame(body, bg=U.T.card, bd=0, width=U.px(230),
                        highlightthickness=1, highlightbackground=U.T.border)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        U.wlabel(left, "服务器", "card", U.FONT_XS).pack(
            anchor="w", padx=U.px(12), pady=(U.px(8), U.px(2)))
        self.lst = tk.Listbox(left, bd=0, relief="flat", highlightthickness=0,
                              activestyle="none", exportselection=False,
                              font=U.FONT_S, bg=U.T.card, fg=U.T.text,
                              selectbackground=U.T.brand_soft,
                              selectforeground=U.T.brand_deep)
        self.lst.pack(fill="both", expand=True, padx=U.px(4), pady=(0, U.px(6)))
        self.lst.bind("<<ListboxSelect>>", lambda e: self._pick())

        right = U.wframe(body, "page")
        right.pack(side="left", fill="both", expand=True, padx=(U.px(10), 0))
        self._build_form(right)
        self._build_tools(right)

        self.reload()

    # ================================================================ 表单
    def _build_form(self, parent) -> None:
        card = tk.Frame(parent, bg=U.T.card, bd=0,
                        highlightthickness=1, highlightbackground=U.T.border)
        card.pack(fill="x")
        f = tk.Frame(card, bg=U.T.card)
        f.pack(fill="x", padx=U.px(12), pady=U.px(10))
        f.columnconfigure(1, weight=1)

        r = 0
        U.wlabel(f, "名称", "card", U.FONT_S).grid(row=r, column=0, sticky="w")
        self.v_name = tk.StringVar()
        self.ent_name = ttk.Entry(f, textvariable=self.v_name, font=U.FONT_S)
        self.ent_name.grid(row=r, column=1, sticky="we", padx=(U.px(8), 0))

        r += 1
        U.wlabel(f, "传输", "card", U.FONT_S).grid(row=r, column=0, sticky="w",
                                                  pady=(U.px(6), 0))
        self.v_transport = tk.StringVar(value=T_LABEL["stdio"])
        self.cmb_transport = ttk.Combobox(
            f, textvariable=self.v_transport, state="readonly", font=U.FONT_S,
            values=[T_LABEL["stdio"], T_LABEL["http"]], width=26)
        self.cmb_transport.grid(row=r, column=1, sticky="w",
                                padx=(U.px(8), 0), pady=(U.px(6), 0))
        self.cmb_transport.bind("<<ComboboxSelected>>",
                                lambda e: self._sync_transport())

        r += 1
        self.lbl_cmd = U.wlabel(f, "命令", "card", U.FONT_S)
        self.lbl_cmd.grid(row=r, column=0, sticky="w", pady=(U.px(6), 0))
        self.v_cmd = tk.StringVar()
        self.ent_cmd = ttk.Entry(f, textvariable=self.v_cmd, font=U.FONT_S)
        self.ent_cmd.grid(row=r, column=1, sticky="we", padx=(U.px(8), 0),
                          pady=(U.px(6), 0))
        self.lbl_cmd_hint = U.wlabel(
            f, "例：npx ／ node ／ python（绝对路径更稳）", "mute", U.FONT_XS)
        self.lbl_cmd_hint.grid(row=r, column=2, sticky="w", padx=(U.px(8), 0),
                               pady=(U.px(6), 0))

        r += 1
        U.wlabel(f, "参数", "card", U.FONT_S).grid(row=r, column=0, sticky="w",
                                                  pady=(U.px(6), 0))
        self.v_args = tk.StringVar()
        self.ent_args = ttk.Entry(f, textvariable=self.v_args, font=U.FONT_S)
        self.ent_args.grid(row=r, column=1, sticky="we", padx=(U.px(8), 0),
                           pady=(U.px(6), 0))
        U.wlabel(f, "空格分隔", "mute", U.FONT_XS).grid(
            row=r, column=2, sticky="w", padx=(U.px(8), 0), pady=(U.px(6), 0))

        r += 1
        self.lbl_url = U.wlabel(f, "URL", "card", U.FONT_S)
        self.lbl_url.grid(row=r, column=0, sticky="w", pady=(U.px(6), 0))
        self.v_url = tk.StringVar()
        self.ent_url = ttk.Entry(f, textvariable=self.v_url, font=U.FONT_S)
        self.ent_url.grid(row=r, column=1, sticky="we", padx=(U.px(8), 0),
                          pady=(U.px(6), 0))
        self.lbl_url_hint = U.wlabel(f, "例：http://127.0.0.1:3001/mcp",
                                     "mute", U.FONT_XS)
        self.lbl_url_hint.grid(row=r, column=2, sticky="w",
                               padx=(U.px(8), 0), pady=(U.px(6), 0))

        r += 1
        U.wlabel(f, "环境变量", "card", U.FONT_S).grid(row=r, column=0,
                                                    sticky="nw",
                                                    pady=(U.px(6), 0))
        self.tx_env = tk.Text(f, height=2, font=U.MONO_S, bd=0, relief="flat",
                              highlightthickness=1,
                              highlightbackground=U.T.input_border,
                              highlightcolor=U.T.brand, bg=U.T.input_bg,
                              fg=U.T.text, insertbackground=U.T.text,
                              padx=U.px(6), pady=U.px(4))
        self.tx_env.grid(row=r, column=1, sticky="we", padx=(U.px(8), 0),
                         pady=(U.px(6), 0))
        U.wlabel(f, "每行一个 KEY=VALUE", "mute", U.FONT_XS).grid(
            row=r, column=2, sticky="w", padx=(U.px(8), 0), pady=(U.px(6), 0))

        r += 1
        U.wlabel(f, "超时", "card", U.FONT_S).grid(row=r, column=0, sticky="w",
                                                 pady=(U.px(6), 0))
        self.v_timeout = tk.StringVar(value="20")
        ttk.Entry(f, textvariable=self.v_timeout, width=6, font=U.FONT_S).grid(
            row=r, column=1, sticky="w", padx=(U.px(8), 0), pady=(U.px(6), 0))
        U.wlabel(f, "秒（3–120）", "mute", U.FONT_XS).grid(
            row=r, column=2, sticky="w", padx=(U.px(8), 0), pady=(U.px(6), 0))

        r += 1
        self.v_on = tk.BooleanVar(value=True)
        row2 = tk.Frame(f, bg=U.T.card)
        row2.grid(row=r, column=1, sticky="w", padx=(U.px(8), 0),
                  pady=(U.px(8), 0))
        U.Check(row2, "启用（助手能用这家提供的工具）", self.v_on,
                font=U.FONT_S).pack(side="left")

        r += 1
        acts = tk.Frame(f, bg=U.T.card)
        acts.grid(row=r, column=1, sticky="we", padx=(U.px(8), 0),
                  pady=(U.px(10), 0))
        U.RoundButton(acts, "保存", self.save, variant="primary", height=30,
                      padx=16).pack(side="left")
        U.RoundButton(acts, "试连并列出工具", self.probe, variant="ghost",
                      height=30, padx=14).pack(side="left", padx=(U.px(6), 0))
        U.RoundButton(acts, "启动", self.start_one, variant="ghost",
                      height=30, padx=12).pack(side="left", padx=(U.px(6), 0))
        U.RoundButton(acts, "停止", self.stop_one, variant="ghost",
                      height=30, padx=12).pack(side="left", padx=(U.px(4), 0))
        U.RoundButton(acts, "删除", self.remove, variant="ghost",
                      height=30, padx=12).pack(side="right")

    def _build_tools(self, parent) -> None:
        card = tk.Frame(parent, bg=U.T.card, bd=0,
                        highlightthickness=1, highlightbackground=U.T.border)
        card.pack(fill="both", expand=True, pady=(U.px(10), 0))
        top = tk.Frame(card, bg=U.T.card)
        top.pack(fill="x", padx=U.px(12), pady=(U.px(8), 0))
        U.wlabel(top, "工具与试连结果", "card", U.FONT_S).pack(side="left")
        self.lbl_state = U.wlabel(top, "", "mute", U.FONT_XS)
        self.lbl_state.pack(side="right")
        body = tk.Frame(card, bg=U.T.card)
        body.pack(fill="both", expand=True, padx=U.px(12), pady=U.px(8))
        self.tb = U.TextBox(body, role="console", height=8, readonly=True)
        self.tb.pack(fill="both", expand=True)

    # ================================================================ 数据
    def reload(self) -> None:
        self._rows = self.store.all()
        self.lst.delete(0, "end")
        for r in self._rows:
            tid = r.get("sid") or ""
            mark = "●" if r.get("enabled") else "·"
            n = 0
            s = self.pool._sessions.get(tid)
            if s is not None and s.ready:
                n = len(s.tools)
            tail = f"{n} 个工具" if n else ("已停用" if not r.get("enabled") else "未启动")
            self.lst.insert("end", f"{mark} {r.get('name') or tid}　{tail}")
        self.lbl_info.configure(text=f"{len(self._rows)} 个服务器")
        if self._rows:
            idx = 0
            if self._cur is not None:
                for i, r in enumerate(self._rows):
                    if r.get("sid") == self._cur.get("sid"):
                        idx = i
                        break
            self.lst.selection_clear(0, "end")
            self.lst.selection_set(idx)
            self._load(self._rows[idx])
        else:
            self._load(None)

    def _pick(self) -> None:
        idx = self.lst.curselection()
        if not idx or idx[0] >= len(self._rows):
            return
        self._load(self._rows[idx[0]])

    def _load(self, rec: dict | None) -> None:
        self._cur = dict(rec) if rec else None
        row = rec or {}
        self.v_name.set(row.get("name") or "")
        self.v_transport.set(T_LABEL.get(row.get("transport") or "stdio",
                                         T_LABEL["stdio"]))
        self.v_cmd.set(row.get("command") or "")
        self.v_args.set(" ".join(row.get("args") or []))
        self.v_url.set(row.get("url") or "")
        self.v_timeout.set(str(row.get("timeout") or 20))
        self.v_on.set(bool(row.get("enabled", True)))
        self.tx_env.delete("1.0", "end")
        env = row.get("env") or {}
        self.tx_env.insert("1.0", "\n".join(f"{k}={v}" for k, v in env.items()))
        self._sync_transport()
        if row.get("sid"):
            st = self.pool.status(str(row["sid"]))
            self.lbl_state.configure(text=st)
            self._show_tools(str(row["sid"]))
        else:
            self.lbl_state.configure(text="新的一条")
            self.tb.clear()
            self.tb.txt.insert("1.0", "左边还没有服务器。点「新建服务器」，或者"
                                      "「从 mcp.json 导入…」把别家配好的搬过来。")

    def _show_tools(self, sid: str) -> None:
        s = self.pool._sessions.get(sid)
        self.tb.clear()
        if s is None or not s.ready:
            self.tb.txt.insert("1.0", "还没启动。点「试连并列出工具」可以直接看它有什么。")
            return
        lines = [f"{sid} · 协议 {s.protocol or '未知'} · {len(s.tools)} 个工具", ""]
        for t in s.tools:
            lines.append(f"· {t.get('name')}")
            desc = str(t.get("description") or "").strip()
            if desc:
                lines.append("    " + desc.replace("\n", " ")[:300])
        self.tb.txt.insert("1.0", "\n".join(lines))

    def _sync_transport(self) -> None:
        http = self.v_transport.get() == T_LABEL["http"]
        state_cmd = "disabled" if http else "normal"
        for w in (self.ent_cmd, self.ent_args, self.tx_env):
            try:
                w.configure(state=state_cmd)
            except tk.TclError:
                pass
        try:
            self.ent_url.configure(state="normal" if http else "disabled")
        except tk.TclError:
            pass
        for w in (self.lbl_cmd, self.lbl_cmd_hint):
            w.configure(fg=U.T.text_mute if http else U.T.text)
        for w in (self.lbl_url, self.lbl_url_hint):
            w.configure(fg=U.T.text if http else U.T.text_mute)

    def _form_rec(self) -> dict | None:
        name = self.v_name.get().strip()
        if not name:
            self.app.toast("先给这条服务器起个名字", "warn")
            return None
        http = self.v_transport.get() == T_LABEL["http"]
        env: dict[str, str] = {}
        for line in self.tx_env.get("1.0", "end").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
        try:
            timeout = max(3, min(120, int(str(self.v_timeout.get()).strip() or 20)))
        except ValueError:
            timeout = 20
        return mcp.MCPStore._norm({
            "sid": (self._cur or {}).get("sid") or mcp.slug(name),
            "name": name,
            "transport": "http" if http else "stdio",
            "command": "" if http else self.v_cmd.get().strip(),
            "args": [] if http else self.v_args.get().split(),
            "env": {} if http else env,
            "url": self.v_url.get().strip() if http else "",
            "headers": (self._cur or {}).get("headers") or {},
            "timeout": timeout,
            "enabled": bool(self.v_on.get()),
            "note": (self._cur or {}).get("note") or "",
        })

    # ================================================================ 动作
    def add_new(self) -> None:
        self._cur = None
        self._load(None)
        self.v_name.set("")
        self.v_transport.set(T_LABEL["stdio"])
        self.v_timeout.set("20")
        self.v_on.set(True)
        self.lst.selection_clear(0, "end")
        self.ent_name.focus_set()

    def save(self) -> None:
        rec = self._form_rec()
        if rec is None:
            return
        if rec["transport"] == "stdio" and not rec["command"]:
            self.app.toast("stdio 必须填启动命令", "warn")
            return
        if rec["transport"] == "http" and not rec["url"]:
            self.app.toast("http 必须填 URL", "warn")
            return
        self.store.upsert(rec)
        self._cur = rec
        self.reload()
        self.app.toast(f"已保存「{rec['name']}」", "ok")

    def remove(self) -> None:
        rec = self._cur
        if not rec or not rec.get("sid"):
            self.app.toast("先在左边选一条", "warn")
            return
        if not forms.confirm(self, "删除服务器",
                             f"删掉「{rec.get('name') or rec['sid']}」？\n"
                             "它的工具会从助手的工具清单里消失（不会删对方的任何文件）。",
                             danger=True, ok_text="删除"):
            return
        sid = str(rec["sid"])
        self.pool.stop(sid)
        self.store.remove(sid)
        self._cur = None
        self.reload()
        self.app.toast("已删除", "ok")

    def probe(self) -> None:
        rec = self._form_rec()
        if rec is None:
            return
        self.lbl_state.configure(text="正在试连…")
        self.tb.clear()
        self.tb.txt.insert("1.0", "正在起进程 / 连接口…")

        def work():
            ok, msg, tools = self.pool.probe(rec)
            try:
                self.after(0, lambda: self._probe_done(ok, msg, tools))
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _probe_done(self, ok: bool, msg: str, tools: list) -> None:
        self.lbl_state.configure(text=("✓ " if ok else "✕ ") + msg)
        self.tb.clear()
        if not ok:
            self.tb.txt.insert("1.0", "试连失败：\n" + msg)
            return
        lines = [msg, f"{len(tools)} 个工具", ""]
        for t in tools:
            lines.append(f"· {t.get('name')}")
            desc = str(t.get("description") or "").strip()
            if desc:
                lines.append("    " + desc.replace("\n", " ")[:300])
        self.tb.txt.insert("1.0", "\n".join(lines))

    def start_one(self) -> None:
        rec = self._cur
        if not rec or not rec.get("sid"):
            self.app.toast("先在左边选一条", "warn")
            return
        sid = str(rec["sid"])
        ok, why = self.pool.start(sid)
        self.app.toast(("已启动 " + sid) if ok else ("启动失败：" + why),
                       "ok" if ok else "err")
        self.pool.rebuild_tools()
        self.reload()

    def stop_one(self) -> None:
        rec = self._cur
        if not rec or not rec.get("sid"):
            return
        self.pool.stop(str(rec["sid"]))
        self.reload()

    def start_all(self) -> None:
        def work():
            rows = self.pool.ensure_tools()
            try:
                self.after(0, lambda: self._all_done(len(rows)))
            except tk.TclError:
                pass

        self.app.toast("正在启动全部启用的服务器…", "ok")
        threading.Thread(target=work, daemon=True).start()

    def _all_done(self, n: int) -> None:
        self.reload()
        self.app.toast(f"MCP 工具就绪：{n} 个", "ok")

    def import_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(), title="选择 mcp.json",
            filetypes=[("MCP 配置", "*.json"), ("全部文件", "*.*")])
        if not path:
            return
        n, msg = self.store.import_json(path)
        self.reload()
        self.app.toast(msg, "ok" if n else "warn")
