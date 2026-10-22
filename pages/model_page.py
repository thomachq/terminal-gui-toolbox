# -*- coding: utf-8 -*-
"""模型管理 —— 一条配置一个模型，随时切换当前用哪个（对齐 DeepSeek Harness）。

为什么不是「一个表单填到底」：

  运维现场常见的状态就是同时有好几条可用线路 —— 官方的、公司网关的、本机
  Ollama 的、DeepSeek Harness 的。用哪个往往取决于当时有没有网、要不要省钱。
  单表单的写法每换一次就要重填地址和 Key，而且填错就丢了原来那条。

所以这里是一张清单：每条一个模型档案（名称 / 服务商 / 模型名 / 地址 / 上下文 /
温度 / Key / 状态），行上直接「设为当前」「试连」。

几条硬规矩：

  · 「当前」只有一个。切换等于把那条档案写回扁平字段（provider/base/model/…），
    引擎那边一行都不用改。
  · 最后一条删不掉 —— 删空了助手就没模型可用。
  · Key 只显示「已存 / 未填」，永不回显；编辑时留空表示不修改。
  · 状态列会自己看：缺 Key、缺 Harness、就绪。填完不知道对不对，点「试连」。
"""
from __future__ import annotations

import threading
import tkinter as tk

import wbtui as U
from core import aiops
from ui import forms
from ui.table import Column, DataTable

# 列宽加起来要能塞进 980 的对话框，否则最右的「状态」被挤出去 ——
# 那恰是最该看的一列（缺 Key / 缺 Harness / 就绪）。
MODEL_COLS = [
    Column("current", "", 54),
    Column("name", "名称", 128, stretch=True),
    Column("provider", "服务商", 104),
    Column("model", "模型名", 150, stretch=True),
    Column("base", "接口地址", 178, stretch=True),
    Column("ctx", "上下文", 72, anchor="e"),
    Column("temp", "温度", 46, anchor="e"),
    Column("key", "Key", 56),
    Column("state", "状态", 86),
]


class ModelDialog(tk.Toplevel):
    """模型清单。非模态 —— 开着也能回主界面看助手现在到底用哪条。"""

    def __init__(self, app, parent=None):
        tk.Toplevel.__init__(self, app)
        self.app = app
        self.cfg = app.ai_cfg
        self.withdraw()
        self.title("模型管理 — 智能运维助手")
        self.configure(bg=U.T.page)
        self.geometry(f"{U.px(980)}x{U.px(560)}+{U.px(160)}+{U.px(120)}")
        self.minsize(U.px(760), U.px(420))
        self.transient(app)

        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(18), pady=U.px(16))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(2, weight=1)

        # ---- 标题
        head = U.wframe(wrap, "page")
        head.grid(row=0, column=0, sticky="we")
        U.wlabel(head, "模型管理", "page", U.FONT_T).pack(side="left")
        U.wlabel(head, "一条配置一个模型，随时切换当前", "pagedim",
                 U.FONT_S).pack(side="left", padx=(U.px(10), 0))
        U.RoundButton(head, "关闭", self._close, variant="ghost",
                      font=U.FONT_S, height=28, padx=12).pack(side="right")
        U.RoundButton(head, "试连", self.model_probe, variant="ghost",
                      font=U.FONT_S, height=28, padx=10).pack(
            side="right", padx=(0, U.px(6)))

        # ---- 动作条
        bar = U.wframe(wrap, "page")
        bar.grid(row=1, column=0, sticky="we", pady=(U.px(12), U.px(8)))
        U.RoundButton(bar, "＋ 新增模型", self.model_add, variant="primary",
                      font=U.FONT_S, height=30, padx=13).pack(side="left")
        U.RoundButton(bar, "设为当前", self.model_activate, variant="ghost",
                      font=U.FONT_S, height=30, padx=10).pack(
            side="left", padx=(U.px(6), 0))
        U.RoundButton(bar, "编辑", lambda: self.model_edit(self._pick()),
                      variant="ghost", font=U.FONT_S, height=30,
                      padx=10).pack(side="left", padx=(U.px(6), 0))
        U.RoundButton(bar, "删除", self.model_delete, variant="ghost",
                      font=U.FONT_S, height=30, padx=10).pack(
            side="left", padx=(U.px(6), 0))
        self.lbl_sum = U.wlabel(bar, "", "pagedim", U.FONT_XS)
        self.lbl_sum.pack(side="right")

        # ---- 表格
        self.tbl_model = DataTable(
            wrap, MODEL_COLS, height=11,
            search_hint="搜索名称、服务商、模型名",
            empty_title="还没有模型",
            empty_hint="点「＋ 新增模型」填一条；只有一条时会自动设为当前。",
            on_activate=lambda r: self.model_edit((r or {}).get("_raw") or {}))
        self.tbl_model.grid(row=2, column=0, sticky="nsew")
        self.tbl_model.set_menu([
            ("设为当前", self.model_activate, None),
            ("编辑", lambda: self.model_edit(self._pick()), None),
            ("试连这条", self.model_probe, None),
            (None, None, None),
            ("删除", self.model_delete, None),
        ])

        # ---- 底部提示
        self.lbl_hint = U.wlabel(
            wrap, "切换只影响「当前模型」；助手下次调用就用新的那条。",
            "pagedim", U.FONT_XS)
        self.lbl_hint.grid(row=3, column=0, sticky="w", pady=(U.px(8), 0))

        self.model_reload()
        self.deiconify()

    def _close(self) -> None:
        try:
            self.destroy()
        except tk.TclError:
            pass

    # ================================================================ 数据
    def model_reload(self) -> None:
        cfg = self.cfg
        active = cfg.current_id()
        rows = []
        for m in cfg.models():
            v = aiops.model_view(m, active)
            v["_tone"] = v.get("tone") or ""
            rows.append(v)
        self.tbl_model.set_rows(rows)
        cur = cfg.current()
        self.lbl_sum.configure(
            text=f"共 {len(rows)} 个模型 · 当前：{cur.get('name') or '未命名'}")
        self.lbl_hint.configure(
            text="切换只影响「当前模型」；助手下次调用就用新的那条。"
                 f"现在用的是 {cur.get('model') or '（未填模型名）'}")

    def _pick(self) -> dict | None:
        row = self.tbl_model.selected() or {}
        return row.get("_raw")

    def _need_pick(self) -> dict | None:
        rec = self._pick()
        if rec is None:
            self.app.toast("先在列表里选一个模型", "warn")
        return rec

    # ================================================================ 动作
    def model_activate(self) -> None:
        rec = self._need_pick()
        if rec is None:
            return
        mid = str(rec.get("id") or "")
        if mid == self.cfg.current_id():
            self.app.toast("这条已经是当前模型了", "warn")
            return
        if not self.cfg.set_active(mid):
            self.app.toast("切换失败：找不到这条模型", "err")
            return
        self.model_reload()
        self.app.toast(f"已切到「{rec.get('name') or mid}」", "ok")
        try:
            self.app._ai_refresh_dock_state()
        except Exception:  # noqa: BLE001
            pass

    def model_add(self) -> None:
        rec = self._form(None)
        if not rec:
            return
        saved = self.cfg.upsert_model(rec)
        # 只有一条时自动设为当前，省得用户还得再点一次
        if len(self.cfg.models()) == 1:
            self.cfg.set_active(saved["id"])
        self.model_reload()
        self.app.toast("模型已添加", "ok")

    def model_edit(self, rec: dict | None) -> None:
        rec = rec or self._need_pick()
        if not rec:
            return
        data = self._form(rec)
        if not data:
            return
        self.cfg.upsert_model(data)
        self.model_reload()
        self.app.toast("模型已更新", "ok")

    def model_delete(self) -> None:
        rec = self._need_pick()
        if rec is None:
            return
        name = rec.get("name") or rec.get("id")
        if not forms.confirm(self, "删除模型",
                             f"删除「{name}」这条配置？\n"
                             "只是不再用这条线路，主机和会话都不受影响。",
                             danger=True, ok_text="删除"):
            return
        ok, why = self.cfg.remove_model(str(rec.get("id") or ""))
        if not ok:
            self.app.toast(why, "warn")
            return
        self.model_reload()
        self.app.toast("已删除", "ok")

    def model_probe(self) -> None:
        rec = self._need_pick()
        if rec is None:
            return
        name = rec.get("name") or rec.get("id")
        self.lbl_hint.configure(text=f"正在试连「{name}」…（最多 20 秒）")
        self.app.toast(f"正在试连「{name}」…", "warn")

        def work():
            try:
                ok, msg = aiops.probe_model(rec, timeout=20)
            except Exception as e:  # noqa: BLE001
                ok, msg = False, f"{type(e).__name__}: {e}"
            try:
                self.after(0, lambda: self._probe_done(name, ok, msg))
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _probe_done(self, name, ok: bool, msg: str) -> None:
        try:
            self.lbl_hint.configure(text=("✓ " if ok else "✕ ") + msg)
        except tk.TclError:
            return
        self.app.toast(f"「{name}」{msg}", "ok" if ok else "err", ms=4200)

    # ================================================================ 表单
    def _form(self, rec: dict | None) -> dict | None:
        """新增 / 编辑共用一张表单。rec 为空就是新增。"""
        rec = rec or {}
        prov = str(rec.get("provider") or aiops.DEFAULT_PROVIDER)
        if prov not in aiops.PROVIDERS:
            prov = aiops.DEFAULT_PROVIDER
        opts = [(k, v["label"]) for k, v in aiops.PROVIDERS.items()]
        fields = [
            {"key": "name", "label": "名称", "kind": "text",
             "default": rec.get("name") or "",
             "hint": "给自己看的标签，例如「公司网关」「本机 Ollama」"},
            {"key": "provider", "label": "服务商", "kind": "select",
             "options": opts, "default": prov,
             "hint": "选「自定义」可以填任何 OpenAI 兼容接口"},
            {"key": "base", "label": "接口地址", "kind": "text",
             "default": rec.get("base") or "",
             "hint": "例如 https://api.deepseek.com（会自动补 /v1/chat/completions）"},
            {"key": "model", "label": "模型名", "kind": "text",
             "default": rec.get("model") or "",
             "hint": "例如 deepseek-chat、qwen2.5-coder:14b"},
            {"key": "key", "label": "API Key", "kind": "password",
             "default": "",
             "hint": ("已存，留空表示不修改" if rec.get("key_enc")
                      else "本地 Harness / Ollama 一般不需要")},
            {"key": "ctx", "label": "上下文长度", "kind": "text",
             "default": str(rec.get("ctx") or ""),
             "hint": "留空用服务默认；MoE 模型常写 65536"},
            {"key": "temperature", "label": "温度", "kind": "text",
             "default": str(rec.get("temperature", 0.2)),
             "hint": "运维场景建议 0–0.3，越小越按事实说话"},
            {"key": "note", "label": "备注", "kind": "text",
             "default": rec.get("note") or "",
             "hint": "可选：这条线路什么时候用"},
        ]
        data = forms.FormDialog.open(
            self, "编辑模型" if rec else "新增模型", fields,
            subtitle="Key 只存密文、不回显；这条不设为当前也不影响现有配置",
            width=620)
        if not data:
            return None

        out = dict(rec)
        out["name"] = str(data.get("name") or "").strip()
        p = str(data.get("provider") or prov)
        out["provider"] = p if p in aiops.PROVIDERS else aiops.DEFAULT_PROVIDER
        preset = aiops.PROVIDERS[out["provider"]]
        out["base"] = (str(data.get("base") or "").strip() or preset["base"])
        out["model"] = (str(data.get("model") or "").strip() or preset["model"])
        out["note"] = str(data.get("note") or "").strip()
        try:
            out["ctx"] = max(0, int(str(data.get("ctx") or "0").strip() or 0))
        except (TypeError, ValueError):
            out["ctx"] = 0
        try:
            out["temperature"] = max(0.0, min(2.0, float(
                data.get("temperature") or 0.2)))
        except (TypeError, ValueError):
            out["temperature"] = 0.2
        if not out["name"]:
            out["name"] = (aiops.PROVIDERS[out["provider"]]["label"]
                           + " · " + (out["model"] or "未命名"))
        key = str(data.get("key") or "").strip()
        if key:
            out["key_enc"] = self.cfg.seal_key(key)
        return out
