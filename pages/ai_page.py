# -*- coding: utf-8 -*-
"""AI 运维页：对话 → 生成命令 → 在远端执行 → 读回输出 → 再生成。

与「智能体接入」页的分工：
  · 智能体接入 = 把我们的连接**借给别的智能体**（对外 HTTP API）
  · AI 运维     = **我们自己的** AI，命令走现有会话通道，输出回显在页面上

一条硬规矩（照宝塔终端抄的）：命令必须在界面上看得见，不做静默执行。
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, ttk

import wbtui as U
from core import ai_skills, aiops
from ui import forms

MODES = [
    ("auto", "自动执行 · 只读与改动直接跑，高风险必问"),
    ("confirm", "每步确认 · 有改动的命令都先问"),
    ("readonly", "只读 · 只执行查询，不改任何东西"),
]
MODE_LABEL = {k: v for k, v in MODES}
MODE_BY_LABEL = {v: k for k, v in MODES}

AUTO_LABEL = "自动判断（按问题挑技能）"


def _open_in_shell(path: str) -> None:
    """用系统文件管理器打开目录（Windows 走 explorer，其它平台按各自命令）。"""
    import os
    import subprocess
    import sys
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class AiPage:
    # ------------------------------------------------------------ 构建
    def build_ai(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        # ---- 模型与状态
        c = U.Card(pg, title="AI 运维",
                   hint="生成命令 → 在远端执行 → 读回输出 → 再生成")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        head = U.wframe(b, "card")
        head.pack(fill="x")
        self.lbl_ai_model = U.wlabel(head, "未配置模型", "card", U.FONT_B)
        self.lbl_ai_model.pack(side="left")
        self.badge_ai = U.Pill(head, "待配置", "mute", height=24)
        self.badge_ai.pack(side="left", padx=(U.px(10), 0))
        self.lbl_ai_tokens = U.wlabel(head, "", "mute", U.FONT_XS)
        self.lbl_ai_tokens.pack(side="right")

        U.wlabel(
            b, "AI 只输出命令方案，真正的执行交给当前会话通道（就是「连接与设备」里那条），"
               "每条命令与输出都显示在下面 —— 不做后台静默执行。"
               "高风险操作（删除、清空、重启设备）不管哪种模式都会先问你。",
            "dim", U.FONT_S, justify="left", wraplength=U.px(760)).pack(
            anchor="w", pady=(U.px(6), 0))

        row = U.wframe(b, "card")
        row.pack(fill="x", pady=(U.px(10), 0))
        U.RoundButton(row, "模型设置", self.ai_settings, variant="primary",
                      height=32, padx=16).pack(side="left")
        U.RoundButton(row, "清空会话", self.ai_reset, variant="ghost",
                      height=32, padx=14).pack(side="left", padx=(U.px(8), 0))
        U.RoundButton(row, "复制对话", self.ai_copy_chat, variant="ghost",
                      height=32, padx=14).pack(side="left", padx=(U.px(6), 0))

        # ---- 对话区
        c2 = U.Card(pg, title="对话", hint="命令卡片标了风险等级，执行前必看")
        c2.grid(row=1, column=0, sticky="nsew", pady=(U.px(10), 0))
        self.tb_ai_chat = U.TextBox(c2.body, role="console", height=14,
                                    readonly=True, wrap="word")
        self.tb_ai_chat.pack(fill="both", expand=True)
        self._ai_paint_tags()
        U.register(self, self._ai_paint_tags)

        # ---- 输入
        c3 = U.Card(pg, title="说你要做什么", hint="Ctrl+Enter 发送")
        c3.grid(row=2, column=0, sticky="we", pady=(U.px(10), 0))
        b3 = c3.body
        bar = U.wframe(b3, "card")
        bar.pack(fill="x")
        U.wlabel(bar, "模式", "dim", U.FONT_S).pack(side="left")
        self.v_ai_mode = tk.StringVar(value=MODE_LABEL.get(
            str(getattr(self.ai_cfg, "data", {}).get("exec_mode") or "auto"),
            MODE_LABEL["auto"]))
        cb = ttk.Combobox(bar, textvariable=self.v_ai_mode, values=[v for _, v in MODES],
                          state="readonly", width=44)
        cb.pack(side="left", padx=(U.px(6), 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self._ai_mode_changed())

        self.btn_ai_send = U.RoundButton(bar, "发送", self.ai_send,
                                         variant="primary", height=32, padx=18)
        self.btn_ai_send.pack(side="right")
        self.btn_ai_stop = U.RoundButton(bar, "停止", self.ai_stop,
                                         variant="ghost", height=32, padx=14)
        self.btn_ai_stop.pack(side="right", padx=(0, U.px(6)))

        # ---- 技能：把宝塔终端那 8 份 SOP 搬过来，也可以自己装
        srow = U.wframe(b3, "card")
        srow.pack(fill="x", pady=(U.px(8), 0))
        U.wlabel(srow, "技能", "dim", U.FONT_S).pack(side="left")
        self._ai_skill_labels: dict[str, str] = {}
        self.v_ai_skill = tk.StringVar(value=AUTO_LABEL)
        self.cb_ai_skill = ttk.Combobox(srow, textvariable=self.v_ai_skill,
                                        state="readonly", width=34,
                                        values=[AUTO_LABEL])
        self.cb_ai_skill.pack(side="left", padx=(U.px(6), 0))
        self.cb_ai_skill.bind("<<ComboboxSelected>>",
                              lambda e: self._ai_skill_changed())
        U.RoundButton(srow, "技能库", self.ai_skill_dialog, variant="ghost",
                      height=30, padx=14).pack(side="left", padx=(U.px(8), 0))
        self.lbl_ai_skill = U.wlabel(srow, "", "mute", U.FONT_XS)
        self.lbl_ai_skill.pack(side="left", padx=(U.px(10), 0))

        self.tb_ai_input = U.TextBox(b3, role="alt", height=3, readonly=False)
        self.tb_ai_input.pack(fill="x", pady=(U.px(8), 0))
        self.tb_ai_input.txt.bind("<Control-Return>", self._ai_hotkey)

        self._ai_ready()

    def _ai_hotkey(self, _e=None):
        self.ai_send()
        return "break"

    def _ai_paint_tags(self):
        t = self.tb_ai_chat.txt
        t.tag_configure("user", foreground=U.T.console_cmd, font=U.FONT_B)
        t.tag_configure("ai", foreground=U.T.console_ok, font=U.FONT_B)
        t.tag_configure("mute", foreground=U.T.console_meta)
        t.tag_configure("cmd", foreground=U.T.console_cmd, font=U.MONO)
        t.tag_configure("read", foreground=U.T.console_cmd)
        t.tag_configure("write", foreground=U.T.warn)
        t.tag_configure("danger", foreground=U.T.err)
        t.tag_configure("ok", foreground=U.T.console_ok)
        t.tag_configure("out", foreground=U.T.console_fg, font=U.MONO_S)

    # ------------------------------------------------------------ 状态
    def _ai_ready(self):
        cfg = self.ai_cfg
        self.lbl_ai_model.configure(text=cfg.describe())
        if cfg.preset().get("kind") == "dsh":
            ok = bool(aiops.find_dsh())
        else:
            ok = bool(cfg.get_key())
        self.badge_ai.set("已就绪" if ok else "缺 API Key",
                          "ok" if ok else "warn")
        self.lbl_ai_tokens.configure(
            text=(f"已用 {self.ai_assistant.tokens} tokens"
                  if self.ai_assistant.tokens else ""))
        self._ai_refresh_skills()

    def _ai_mode(self) -> str:
        return MODE_BY_LABEL.get(self.v_ai_mode.get(), "auto")

    def _ai_mode_changed(self):
        self.ai_cfg.data["exec_mode"] = self._ai_mode()
        self.ai_cfg.save()

    # ------------------------------------------------------------ 技能
    def _ai_skills(self) -> list:
        try:
            return self.ai_skill_store.all()
        except Exception:  # noqa: BLE001  技能库坏了不该拖垮整页
            return []

    def _ai_refresh_skills(self):
        skills = self._ai_skills()
        labels = [AUTO_LABEL]
        self._ai_skill_labels = {}
        for s in skills:
            lab = f"{s.name} · {s.source}"
            labels.append(lab)
            self._ai_skill_labels[lab] = s.slug
        self.cb_ai_skill.configure(values=labels)
        want = str(self.ai_cfg.data.get("skill") or "")
        back = next((lab for lab, slug in self._ai_skill_labels.items()
                     if slug == want), AUTO_LABEL)
        self.v_ai_skill.set(back)
        if self.lbl_ai_skill.winfo_exists():
            self.lbl_ai_skill.configure(
                text=f"{len(skills)} 个可用" if skills else "技能库为空，点右边导入")

    def _ai_skill_slug(self) -> str:
        lab = self.v_ai_skill.get()
        return self._ai_skill_labels.get(lab, "")

    def _ai_skill_changed(self):
        self.ai_cfg.data["skill"] = self._ai_skill_slug()
        self.ai_cfg.save()

    def _ai_pick_skill(self, goal: str):
        """返回 (skill, 说明)。手动选了就用选的，否则按关键词挑。"""
        skills = self._ai_skills()
        if not skills:
            return None, "技能库为空"
        slug, why = ai_skills.preferred_slug(goal, skills, self._ai_skill_slug())
        sk = next((x for x in skills if x.slug == slug), None)
        return (sk, why) if sk else (None, why)

    def ai_skill_dialog(self):
        SkillDialog(self, self)

    # ------------------------------------------------------------ 日志
    def _ai_write(self, text: str, *tags):
        t = self.tb_ai_chat.txt
        t.configure(state="normal")
        t.insert("end", text if text.endswith("\n") else text + "\n",
                 tags or ("mute",))
        t.see("end")

    def _ai_clear_log(self):
        self.tb_ai_chat.clear()

    def ai_copy_chat(self):
        body = self.tb_ai_chat.get("1.0", "end-1c")
        if not body.strip():
            self.toast("对话还是空的", "warn")
            return
        self.copy_text(body, "对话")

    def ai_reset(self):
        if self.ai_busy:
            self.toast("正在执行，先停止再清空", "warn")
            return
        if not forms.confirm(self, "清空会话", "清空对话与上下文？"
                                               "（已经执行过的命令不会撤销）"):
            return
        self.ai_assistant.reset()
        self._ai_clear_log()
        self._ai_ready()
        self.toast("会话已清空", "ok")

    # ------------------------------------------------------------ 设置
    def ai_settings(self):
        cfg = self.ai_cfg
        opts = [(k, f"{v['label']}") for k, v in aiops.PROVIDERS.items()]
        fields = [
            {"key": "provider", "label": "服务商", "kind": "select",
             "options": opts, "default": cfg.provider,
             "hint": "选「自定义」就可以填任何 OpenAI 兼容接口"},
            {"key": "base", "label": "接口地址", "kind": "text",
             "default": cfg.data.get("base") or "",
             "hint": "例如 https://api.deepseek.com（程序会自动补 /v1/chat/completions）"},
            {"key": "model", "label": "模型", "kind": "text",
             "default": cfg.data.get("model") or "",
             "hint": "例如 deepseek-chat、qwen2.5-72b-instruct"},
            {"key": "key", "label": "API Key", "kind": "password",
             "default": "", "hint": "加密保存在 data/ai.json，不回显；留空表示不修改"},
            {"key": "temperature", "label": "温度", "kind": "text",
             "default": str(cfg.data.get("temperature", 0.2)),
             "hint": "运维场景建议 0–0.3，越小越按事实说话"},
            {"key": "max_steps", "label": "最多步数", "kind": "int",
             "default": int(cfg.data.get("max_steps") or 8), "min": 1, "max": 30,
             "unit": "步", "hint": "一个任务最多来回几轮，防止绕圈"},
        ]
        data = forms.FormDialog.open(
            self, "模型设置", fields,
            subtitle="支持官方 API、自建网关、本地 Harness；Key 只存密文")
        if not data:
            return
        prov = str(data.get("provider") or cfg.provider)
        if prov not in aiops.PROVIDERS:
            prov = aiops.DEFAULT_PROVIDER
        cfg.data["provider"] = prov
        preset = aiops.PROVIDERS[prov]
        cfg.data["base"] = (str(data.get("base") or "").strip()
                            or preset["base"])
        cfg.data["model"] = (str(data.get("model") or "").strip()
                             or preset["model"])
        try:
            cfg.data["temperature"] = max(0.0, min(2.0, float(
                data.get("temperature") or 0.2)))
        except (TypeError, ValueError):
            cfg.data["temperature"] = 0.2
        cfg.data["max_steps"] = int(data.get("max_steps") or 8)
        key = str(data.get("key") or "").strip()
        cfg.save()
        if key:
            cfg.set_key(key)
        self._ai_ready()
        self.toast("模型设置已保存", "ok")

    # ------------------------------------------------------------ 发送
    def ai_send(self):
        if self.ai_busy:
            self.toast("上一条还在跑，先停止或等它结束", "warn")
            return
        goal = self.tb_ai_input.get("1.0", "end-1c").strip()
        if not goal:
            return
        cfg = self.ai_cfg
        if cfg.preset().get("kind") == "dsh":
            if not aiops.find_dsh():
                forms.InfoDialog.show(
                    self, "找不到 dsh",
                    "选了本地 Harness 通道，但没找到 dsh 命令。\n"
                    "请先装 DeepSeek Harness，或改用其它服务商 + API Key。",
                    width=680, height=240)
                return
        elif not cfg.get_key():
            self.toast("还没填 API Key，点「模型设置」填一个", "warn")
            return

        self.tb_ai_input.clear()
        self.ai_busy = True
        self._ai_stop.clear()
        self.btn_ai_send.set_text("执行中…")
        self._ai_write(f"\n你 > {goal}", "user")

        skill, why = self._ai_pick_skill(goal)
        skill_text = ai_skills.render(skill) if skill is not None else ""
        if skill is not None:
            self._ai_write(f"· 技能：{skill.name}（{why}）", "mute")
        elif why:
            self._ai_write(f"· 没套用技能：{why}", "mute")

        session = self._active_session()
        device = self._ai_device()
        mode = self._ai_mode()
        steps = int(cfg.data.get("max_steps") or 8)
        threading.Thread(target=self._ai_worker,
                         args=(goal, session, device, mode, steps, skill_text),
                         daemon=True).start()

    def ai_stop(self):
        if not self.ai_busy:
            return
        self._ai_stop.set()
        self._ai_write("· 已请求停止，等当前这条命令跑完", "mute")

    def _ai_device(self) -> dict:
        d = self._effective_device()
        s = self.session
        kind = ""
        if s is not None:
            kind = str(getattr(s, "host_kind", "") or "")
        return {
            "name": (getattr(s, "name", None) or getattr(s, "host", None)
                     or "本机"),
            "proto": getattr(s, "proto", "") or "local",
            "os": getattr(d, "os_name", "") or "",
            "host_kind": kind or ("windows" if getattr(d, "os_family", "") == "windows"
                                  else "unix"),
            "vendor": str(getattr(s, "vendor", "") or ""),
            "admin": "已提权" if getattr(s, "is_admin", False) else "",
        }

    # ------------------------------------------------------------ 工作线程
    def _ai_worker(self, goal, session, device, mode, max_steps, skill_text=""):
        def runner(cmd):
            try:
                rc, out, err = session.run(cmd, timeout=120)
                return rc, out, err
            except Exception as e:  # noqa: BLE001
                name = type(e).__name__
                return 1, "", f"{name}: {e}"

        try:
            res = self.ai_assistant.run_task(
                goal, runner, device=device, mode=mode,
                confirm=self._ai_confirm_blocking,
                on_event=lambda k, p: self.post(self._ai_event, k, p),
                should_stop=lambda: self._ai_stop.is_set(),
                max_steps=max_steps, skill_text=skill_text)
        except Exception as e:  # noqa: BLE001
            self.post(self._ai_event, "error",
                      {"msg": f"{type(e).__name__}: {e}"})
            res = {"ok": False}
        self.post(self._ai_done, res)

    def _ai_confirm_blocking(self, cmds) -> bool:
        """在工作线程里等主线程弹窗的结果。超时当拒绝。"""
        box: dict = {}
        ev = threading.Event()
        self.post(self._ai_ask, cmds, box, ev)
        if not ev.wait(timeout=600):
            return False
        return bool(box.get("ok"))

    def _ai_ask(self, cmds, box, ev):
        lines = []
        for c in cmds:
            risk = aiops.RISK_LABEL.get(c.get("risk"), c.get("risk"))
            lines.append(f"[{risk}] {c.get('cmd')}")
            if c.get("why"):
                lines.append(f"    理由：{c.get('why')}")
        text = ("下面这些命令会改动设备或风险较高，需要你确认：\n\n"
                + "\n".join(lines)
                + "\n\n确认执行？拒绝的话这一轮就停下来。")
        try:
            box["ok"] = bool(forms.confirm(self, "确认执行", text,
                                           danger=any(
                                               c.get("risk") == "danger"
                                               for c in cmds),
                                           ok_text="执行"))
        except Exception:  # noqa: BLE001
            box["ok"] = False
        finally:
            ev.set()

    # ------------------------------------------------------------ 事件渲染
    def _ai_event(self, kind, p):
        if kind == "plan":
            a = (p.get("analysis") or "").strip()
            if a:
                self._ai_write(f"AI · 第 {p.get('step')} 步 > {a}", "ai")
            if p.get("usage"):
                self.lbl_ai_tokens.configure(
                    text=f"已用 {self.ai_assistant.tokens} tokens")
        elif kind == "need_confirm":
            self._ai_write("· 有命令需要你确认…", "mute")
        elif kind == "run":
            risk = p.get("risk") or "read"
            tag = risk if risk in ("read", "write", "danger") else "cmd"
            label = aiops.RISK_LABEL.get(risk, risk)
            self._ai_write(f"  ⏵ [{label}] {p.get('cmd')}", tag)
            if p.get("why"):
                self._ai_write(f"      {p.get('why')}", "mute")
        elif kind == "result":
            out = (p.get("out") or "").rstrip()
            err = (p.get("err") or "").rstrip()
            body = out if out else (err or "(无输出)")
            lines = body.splitlines()
            head = lines[:6]
            for ln in head:
                self._ai_write(f"    {ln}", "out")
            if len(lines) > 6:
                self._ai_write(f"    …（共 {len(lines)} 行，完整输出见日志栏）",
                               "mute")
            if err and out:
                self._ai_write(f"    [stderr] {err.splitlines()[0][:200]}", "write")
            self._ai_write(f"    ← rc={p.get('rc')}", "mute")
            self._log_raw("ok" if p.get("rc") == 0 else "bad",
                          f"[AI] $ {p.get('cmd')}\n{body[:4000]}\n")
        elif kind == "refused":
            self._ai_write(f"  ✕ 已拒绝：{p.get('cmd')}", "danger")
        elif kind == "skip":
            self._ai_write(f"  – 只读模式跳过：{p.get('cmd')}", "mute")
        elif kind == "finish":
            self._ai_write(f"— {p.get('reason') or '结束'} —", "mute")
        elif kind == "stopped":
            self._ai_write("— 已停止 —", "mute")
        elif kind == "error":
            self._ai_write(f"出错了：{p.get('msg')}", "danger")
            self.toast("AI 调用失败", "err")

    def _ai_done(self, res):
        self.ai_busy = False
        self.btn_ai_send.set_text("发送")
        self._ai_ready()
        if isinstance(res, dict) and not res.get("ok"):
            return
        if isinstance(res, dict):
            n = len(res.get("ran") or [])
            if n:
                self.toast(f"这一轮执行了 {n} 条命令", "ok")


class SkillPanel(tk.Frame):
    """技能库：看正文、装第三方技能、打开目录。

    技能只是文本 —— 这个面板里所有按钮都不执行技能目录里的任何脚本。

    做成 Frame 而不是 Toplevel：设置中心要把它当成一个内嵌页签
    （「智能体 / 模型 / 技能库 / MCP 库」），另开一个窗口会让人来回找。
    需要独立窗口时用下面的 SkillDialog 包一层。
    """

    def __init__(self, master, page, on_close=None):
        super().__init__(master, bg=U.T.page, bd=0)
        self.page = page
        self.store = page.ai_skill_store
        self.on_close = on_close
        self._items: list = []

        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(14), pady=U.px(12))

        head = U.wframe(wrap, "page")
        head.pack(fill="x")
        U.wlabel(head, "技能库", "page", U.FONT_T).pack(side="left")
        self.lbl_info = U.wlabel(head, "", "pagedim", U.FONT_XS)
        self.lbl_info.pack(side="left", padx=(U.px(10), 0))
        if on_close is not None:
            U.RoundButton(head, "关闭", on_close, variant="secondary",
                          height=30, padx=16).pack(side="right")

        bar = U.wframe(wrap, "page")
        bar.pack(fill="x", pady=(U.px(10), 0))
        U.RoundButton(bar, "导入技能…", self._import, variant="primary",
                      height=30, padx=14).pack(side="left")
        U.RoundButton(bar, "打开技能目录", self._open_dir, variant="ghost",
                      height=30, padx=14).pack(side="left", padx=(U.px(8), 0))
        U.RoundButton(bar, "刷新", self._reload, variant="ghost",
                      height=30, padx=14).pack(side="left", padx=(U.px(6), 0))
        self.btn_use = U.RoundButton(bar, "用这个技能", self._use, variant="ghost",
                                     height=30, padx=14)
        self.btn_use.pack(side="right")

        body = U.wframe(wrap, "page")
        body.pack(fill="both", expand=True, pady=(U.px(10), 0))

        left = U.Card(body)
        left.pack(side="left", fill="y")
        self.lst = tk.Listbox(left.body, width=30, height=14, bd=0,
                              relief="flat", highlightthickness=0,
                              activestyle="none", exportselection=False,
                              font=U.FONT_S, bg=U.T.card, fg=U.T.text,
                              selectbackground=U.T.brand_soft,
                              selectforeground=U.T.brand_deep)
        self.lst.pack(fill="both", expand=True, padx=U.px(2), pady=U.px(2))
        self.lst.bind("<<ListboxSelect>>", lambda e: self._preview())

        right = U.Card(body, title="正文")
        right.pack(side="left", fill="both", expand=True, padx=(U.px(10), 0))
        self.tb = U.TextBox(right.body, role="console", height=14, readonly=True)
        self.tb.pack(fill="both", expand=True)

        self._reload()

    # -- 数据
    def _reload(self):
        self._items = self.store.all() if hasattr(self.store, "all") else []
        self.lst.delete(0, "end")
        for s in self._items:
            self.lst.insert("end", f"[{s.source}] {s.name}")
        bad = getattr(self.store, "errors", [])
        txt = f"{len(self._items)} 个技能"
        if bad:
            txt += f" · {len(bad)} 个坏文件已跳过"
        self.lbl_info.configure(text=txt)
        if self._items:
            self.lst.selection_set(0)
            self._preview()

    def _sel(self):
        idx = self.lst.curselection()
        if not idx or idx[0] >= len(self._items):
            return None
        return self._items[idx[0]]

    def _preview(self):
        s = self._sel()
        if s is None:
            return
        head = (f"{s.name}　（{s.slug} · {s.source}）\n"
                f"{s.description or '—'}\n"
                f"标签：{('、'.join(s.tags) if s.tags else '—')}\n"
                f"文件：{s.path}\n"
                + "-" * 60 + "\n\n")
        self.tb.clear()
        self.tb.txt.insert("1.0", head + (s.body or "（正文为空）"))

    # -- 动作
    def _use(self):
        s = self._sel()
        if s is None:
            return
        page = self.page
        page.ai_cfg.data["skill"] = s.slug
        page.ai_cfg.save()
        page._ai_refresh_skills()
        page.v_ai_skill.set(next((lab for lab, slug in page._ai_skill_labels.items()
                                  if slug == s.slug), AUTO_LABEL))
        page.toast(f"已选用「{s.name}」", "ok")

    def _import(self):
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(), title="选择技能包",
            filetypes=[("技能包", "*.md *.zip"), ("Markdown", "*.md"),
                       ("Zip", "*.zip"), ("全部文件", "*.*")])
        if not path:
            return
        ok, msg = self.store.install(path)
        self.page.toast(msg, "ok" if ok else "err")
        if ok:
            self._reload()
            self.page._ai_refresh_skills()
        else:
            forms.InfoDialog.show(self, "装不上", msg + "\n\n"
                                  "技能包的入口文件必须是 SKILL.md，"
                                  "frontmatter 要有 name 字段。", width=720, height=280)

    def _open_dir(self):
        try:
            self.store.dir.mkdir(parents=True, exist_ok=True)
            _open_in_shell(str(self.store.dir))
        except Exception as e:  # noqa: BLE001
            self.page.toast(f"打不开目录：{e}", "err")


class SkillDialog(tk.Toplevel):
    """独立窗口形态的技能库（侧栏「技能库…」还用这个）。"""

    def __init__(self, parent, page):
        super().__init__(parent)
        self.title("技能库")
        self.configure(bg=U.T.page)
        self.transient(parent)
        self.panel = SkillPanel(self, page, on_close=self.destroy)
        self.panel.pack(fill="both", expand=True)
        self.geometry(f"{U.px(980)}x{U.px(600)}")
        self.minsize(U.px(720), U.px(420))
        self.bind("<Escape>", lambda e: self.destroy())
