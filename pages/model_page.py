# -*- coding: utf-8 -*-
"""模型管理 —— 一条配置一个模型，随时切换当前用哪个（对齐 DeepSeek Harness）。

为什么不是「一个表单填到底」：

  运维现场常见的状态就是同时有好几条可用线路 —— 官方的、公司网关的、本机
  Ollama 的、DeepSeek Harness 的。用哪个往往取决于当时有没有网、要不要省钱。
  单表单的写法每换一次就要重填地址和 Key，而且填错就丢了原来那条。

所以这里是一张清单：每条一个模型档案（名称 / 服务商 / 模型名 / 接口 / 三个
token 限制 / 能力 / Key / 状态），行上直接「设为当前」「试连」。

**只管文本模型**（对齐 Cherry Studio 的模型服务，但砍掉多模态那些）：
  这个助手只会发 `chat/completions`，图片/视频/嵌入/重排模型一条都用不上。
  所以类型固定「文本」不给选，同步时也把它们过滤掉 —— 理由在
  `core/modelsync.py` 里有实测依据。

几条硬规矩：

  · 「当前」只有一个。切换等于把那条档案写回扁平字段（provider/base/model/…），
    引擎那边一行都不用改。
  · 最后一条删不掉 —— 删空了助手就没模型可用。
  · Key 只显示「已存 / 未填」，永不回显；编辑时留空表示不修改。
  · Token 限制抓不到就显示「未知」，绝不拿默认值瞎猜（猜了用户会照着它切会话）。
  · 状态列会自己看：缺 Key、缺 Harness、就绪。填完不知道对不对，点「试连」。
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

import wbtui as U
from core import aiops, modelsync
from ui import forms
from ui.table import Column, DataTable

# 左边已经按供应商分好组，表里就不再重复「服务商」和「接口地址」——
# 这两样都属于上面那张供应商卡，同一家下面的每一条都一模一样，
# 一列里重复十遍除了挤掉别的列没有任何信息量。
# 同理「分组」「Key」也不再占列：分组已经是列表里的折叠头（树的父节点），
# Key 是供应商级的（同一家共用一把），单独一列全是「已存」等于噪声。
MODEL_COLS = [
    Column("current", "", 44),
    Column("name", "名称", 128, stretch=True),
    Column("model", "模型名", 156, stretch=True),
    Column("ctx", "上下文", 64, anchor="e"),
    Column("max_in", "最大输入", 64, anchor="e"),
    Column("max_out", "最大输出", 64, anchor="e"),
    Column("caps", "能力", 88),
    Column("state", "状态", 54),
    # 行尾一个 ✕：对齐 Cherry Studio 的「每行可直接删」，省一次右键
    Column("del", "", 30, anchor="center", format=lambda v, r: "✕"),
]

# 左侧供应商列表的顺序：常用的排前面，「自定义」永远在最后当兜底
PROVIDER_ORDER = ("deepseek", "ollama", "lmstudio", "dsh", "custom")

# Token 限制的预设胶囊。1024 进制 —— 主流模型窗口基本都是 2 的幂
# （131072 / 262144 / 1048576）。点一下只是把值填进输入框，手填的值优先。
_CTX_PRESETS = [128, 200, 256, 400, 512, 1024]
_IN_PRESETS = [128, 200, 256, 512, 1024]
_OUT_PRESETS = [16, 32, 64, 128, 256]


def _preset_label(k: int) -> str:
    return "1M" if k >= 1024 else f"{k}K"


def _preset_value(k: int) -> int:
    return k * 1024


class _ProviderList(tk.Frame):
    """左栏供应商列表。

    没用 Listbox：要两行（名字 + 「N 个模型」）并且要跟着主题换色，
    Listbox 做不到单元格级别的自绘。
    """

    def __init__(self, master, on_pick):
        super().__init__(master, bg=U.T.card, bd=0)
        self.on_pick = on_pick
        self._rows: dict[str, tuple[list, tk.Label, tk.Label]] = {}
        self.current = ""
        U.register(self, self._paint)

    def set_items(self, items: list[tuple]) -> None:
        """items: [(pid, 标题, 副标题, 是否当前)]"""
        for w in self.winfo_children():
            w.destroy()
        self._rows.clear()
        for pid, label, sub, is_cur in items:
            row = tk.Frame(self, bg=U.T.card, bd=0, cursor="hand2")
            row.pack(fill="x", padx=U.px(6), pady=U.px(1))
            top = tk.Frame(row, bg=U.T.card)
            top.pack(fill="x", padx=U.px(8), pady=(U.px(7), 0))
            dot = tk.Label(top, text="●" if is_cur else "", bg=U.T.card,
                           fg=U.T.brand, font=U.FONT_XS)
            dot.pack(side="left")
            lb = tk.Label(top, text=label, bg=U.T.card, fg=U.T.text,
                          font=U.FONT_S, anchor="w")
            lb.pack(side="left", padx=(U.px(4), 0))
            sb = tk.Label(row, text=sub, bg=U.T.card, fg=U.T.text_mute,
                          font=U.FONT_XS, anchor="w")
            sb.pack(fill="x", padx=(U.px(20), U.px(8)), pady=(0, U.px(7)))
            for w in (row, top, dot, lb, sb):
                w.bind("<Button-1>", lambda _e, p=pid: self.select(p))
            self._rows[pid] = ([row, top, dot], lb, sb)
        self._paint()

    def select(self, pid: str, fire: bool = True) -> None:
        if pid not in self._rows:
            return
        self.current = pid
        self._paint()
        if fire and self.on_pick:
            self.on_pick(pid)

    def _paint(self) -> None:
        for pid, (frames, lb, sb) in self._rows.items():
            on = pid == self.current
            bg = U.T.brand_soft if on else U.T.card
            for f in frames:
                try:
                    f.configure(bg=bg)
                except tk.TclError:
                    pass
            lb.configure(bg=bg, fg=U.T.text if on else U.T.text_dim,
                         font=((U.FONT_S[0], U.FONT_S[1], "bold") if on
                               else U.FONT_S))
            sb.configure(bg=bg, fg=U.T.text_mute)


class ModelPanel(tk.Frame):
    """模型清单：左边供应商，右边该供应商下的模型与设置。

    为什么分两层（对齐 Cherry Studio 的「模型服务」）：
      同一个供应商下一般会挂好几个模型（快 / 便宜 / 会写代码），
      接口地址和 Key 是这几个模型**共用**的。扁平一张表就得给每条模型
      重复填一遍地址和 Key，改网关要改 N 行，填漏一行就是一条废配置。

    做成 Frame：设置中心把它当内嵌页签（智能体 / 模型 / 技能库 / MCP 库）。
    要独立窗口就用下面的 ModelDialog 包一层。
    """

    def __init__(self, app, master, on_close=None):
        tk.Frame.__init__(self, master, bg=U.T.page, bd=0)
        self.app = app
        self.cfg = app.ai_cfg
        self.on_close = on_close

        self.sel_pid = self.cfg.provider
        if self.sel_pid not in aiops.PROVIDERS:
            self.sel_pid = aiops.DEFAULT_PROVIDER
        self.v_base = tk.StringVar()
        self.v_key = tk.StringVar()

        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(14), pady=U.px(12))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(1, weight=1)

        # ---- 标题
        head = U.wframe(wrap, "page")
        head.grid(row=0, column=0, sticky="we")
        U.wlabel(head, "模型管理", "page", U.FONT_T).pack(side="left")
        U.wlabel(head, "只收文本模型。先选供应商，再管它下面的模型", "pagedim",
                 U.FONT_S).pack(side="left", padx=(U.px(10), 0))
        if on_close is not None:
            U.RoundButton(head, "关闭", on_close, variant="ghost",
                          font=U.FONT_S, height=28, padx=12).pack(side="right")

        # ---- 主体：左供应商 / 右模型
        body = U.wframe(wrap, "page")
        body.grid(row=1, column=0, sticky="nsew", pady=(U.px(12), 0))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=U.T.card, bd=0, width=U.px(206),
                        highlightthickness=1, highlightbackground=U.T.border)
        left.grid(row=0, column=0, sticky="ns", padx=(0, U.px(12)))
        left.grid_propagate(False)
        # 搜索：Cherry Studio 左栏顶上就有一个「搜索模型平台…」，供应商一多
        # （用户自己加到十几条）不搜根本翻不到。
        self.v_search = tk.StringVar()
        box = tk.Frame(left, bg=U.T.card)
        box.pack(fill="x", padx=U.px(8), pady=(U.px(8), U.px(4)))
        ent = ttk.Entry(box, textvariable=self.v_search, font=U.FONT_S)
        ent.pack(fill="x")
        ent.bind("<KeyRelease>",
                 lambda e: self.plist.set_items(self._provider_items()))
        self.plist = _ProviderList(left, self.provider_pick)
        self.plist.pack(fill="both", expand=True, pady=(0, U.px(4)))
        U.RoundButton(left, "＋ 添加服务商", self.provider_add, variant="ghost",
                      font=U.FONT_XS, height=28, padx=10).pack(
            fill="x", padx=U.px(8), pady=(0, U.px(8)))

        right = U.wframe(body, "page")
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self._build_provider_card(right)
        self._build_model_area(right)

        # ---- 底部提示
        self.lbl_hint = U.wlabel(
            wrap, "切换只影响「当前模型」；助手下次调用就用新的那条。",
            "pagedim", U.FONT_XS)
        self.lbl_hint.grid(row=2, column=0, sticky="w", pady=(U.px(8), 0))
        self.lbl_sum = U.wlabel(wrap, "", "pagedim", U.FONT_XS)
        self.lbl_sum.grid(row=2, column=0, sticky="e")

        self.provider_pick(self.sel_pid)

    # ------------------------------------------------------------ 供应商卡
    def _entry(self, parent, var: tk.StringVar, show: str = "") -> tk.Entry:
        e = tk.Entry(parent, textvariable=var, bd=0, relief="flat", show=show,
                     bg=U.T.input_bg, fg=U.T.text, insertbackground=U.T.text,
                     font=U.FONT_S, highlightthickness=1,
                     highlightbackground=U.T.input_border,
                     highlightcolor=U.T.brand)
        U.register(e, lambda: e.configure(
            bg=U.T.input_bg, fg=U.T.text, insertbackground=U.T.text,
            highlightbackground=U.T.input_border, highlightcolor=U.T.brand))
        return e

    def _build_provider_card(self, parent) -> None:
        card = tk.Frame(parent, bg=U.T.card, bd=0,
                        highlightthickness=1, highlightbackground=U.T.border)
        card.grid(row=0, column=0, sticky="we", pady=(0, U.px(10)))
        body = tk.Frame(card, bg=U.T.card)
        body.pack(fill="x", padx=U.px(14), pady=U.px(12))
        body.columnconfigure(1, weight=1)

        self.lbl_prov = tk.Label(body, text="", bg=U.T.card, fg=U.T.text,
                                 font=U.FONT_B, anchor="w")
        self.lbl_prov.grid(row=0, column=0, sticky="w")
        self.lbl_prov_sub = tk.Label(body, text="", bg=U.T.card,
                                     fg=U.T.text_mute, font=U.FONT_XS, anchor="w")
        self.lbl_prov_sub.grid(row=0, column=1, sticky="w", padx=(U.px(10), 0))

        btns = tk.Frame(body, bg=U.T.card)
        btns.grid(row=0, column=3, sticky="e", rowspan=4, padx=(U.px(8), 0))
        self.btn_prov_save = U.RoundButton(
            btns, "保存设置", self.provider_save, variant="primary",
            font=U.FONT_S, height=28, padx=12)
        self.btn_prov_save.pack(side="right")
        self.btn_prov_probe = U.RoundButton(
            btns, "检测", self.provider_probe, variant="ghost",
            font=U.FONT_S, height=28, padx=10)
        self.btn_prov_probe.pack(side="right", padx=(0, U.px(6)))
        self.btn_key_clear = U.RoundButton(
            btns, "清除 Key", self.provider_clear_key, variant="ghost",
            font=U.FONT_S, height=28, padx=10)
        # 默认收起来：没存过 Key 的时候「清除」是纯噪声
        self.btn_key_clear.pack(side="right", padx=(0, U.px(6)))
        self.btn_key_clear.pack_forget()
        U.RoundButton(btns, "删除服务商", self.provider_remove, variant="ghost",
                      font=U.FONT_XS, height=28, padx=10).pack(
            side="right", padx=(0, U.px(6)))

        # 启用开关：关掉这家 = 它的模型不参与「当前模型」轮换，也不进同步。
        # Cherry Studio 这个开关在标题栏右上角，这里放同一行的右边。
        self.v_enabled = tk.BooleanVar(value=True)
        self.chk_enabled = U.Check(btns, "启用", self.v_enabled,
                                   font=U.FONT_XS, command=self._toggle_enabled)
        self.chk_enabled.pack(side="left", padx=(0, U.px(4)))

        lab = dict(bg=U.T.card, fg=U.T.text_dim, font=U.FONT_S)
        tk.Label(body, text="接口地址", **lab).grid(
            row=1, column=0, sticky="w", pady=(U.px(10), 0))
        self.ent_base = self._entry(body, self.v_base)
        self.ent_base.grid(row=1, column=1, sticky="we", padx=(U.px(10), U.px(6)),
                           pady=(U.px(10), 0), ipady=U.px(3))
        # 「端点 ▾」：同一家往往有内网/外网/备用网关好几个地址，
        # 切换是点一下的事，不该逼着人每次重敲一遍 URL。
        self.btn_eps = U.RoundButton(body, "端点 ▾", self._endpoint_menu,
                                     variant="ghost", font=U.FONT_XS,
                                     height=26, padx=8)
        self.btn_eps.grid(row=1, column=2, sticky="w", pady=(U.px(10), 0))

        tk.Label(body, text="API Key", **lab).grid(
            row=2, column=0, sticky="w", pady=(U.px(8), 0))
        self.ent_key = self._entry(body, self.v_key, show="•")
        self.ent_key.grid(row=2, column=1, sticky="we", padx=(U.px(10), U.px(6)),
                          pady=(U.px(8), 0), ipady=U.px(3))
        # 眼睛：Key 默认是掩码，核对「有没有多粘一个空格」时得能看见
        self._key_shown = False
        self.btn_eye = U.RoundButton(body, "👁", self._toggle_key_show,
                                     variant="ghost", font=U.FONT_XS,
                                     height=26, padx=8)
        self.btn_eye.grid(row=2, column=2, sticky="w", pady=(U.px(8), 0))

        self.lbl_key_hint = tk.Label(body, text="", bg=U.T.card,
                                     fg=U.T.text_mute, font=U.FONT_XS, anchor="w")
        self.lbl_key_hint.grid(row=3, column=1, columnspan=2, sticky="w",
                                padx=(U.px(10), 0), pady=(U.px(3), 0))
        self.lbl_base_hint = tk.Label(body, text="", bg=U.T.card,
                                      fg=U.T.text_mute, font=U.FONT_XS, anchor="w")
        self.lbl_base_hint.grid(row=3, column=0, sticky="w", pady=(U.px(3), 0))

    # ------------------------------------------------------------ 模型区
    def _build_model_area(self, parent) -> None:
        host = tk.Frame(parent, bg=U.T.page, bd=0)
        host.grid(row=1, column=0, sticky="nsew")
        self._model_host = host
        self.drawer = None
        self._drawer_job = None
        self.tbl_model = DataTable(
            host, MODEL_COLS, height=9,
            search_hint="搜索名称、模型名",
            empty_title="这个供应商下还没有模型",
            empty_hint="点右上「＋ 新增模型」填一条，或用「同步模型」从接口拉一份。",
            on_activate=lambda r: self.model_edit((r or {}).get("_raw") or {}))
        self.tbl_model.pack(fill="both", expand=True)
        # 按「分组名称」折叠：一组一条可展开的组头，点三角展开/收起。
        # 默认全展开（Cherry Studio 也是展开态），用户收起的组会记住。
        self.tbl_model.set_group("group", empty="默认", expanded=True)
        self.tbl_model.tree.bind("<Button-1>", self._model_row_click, add="+")
        host.bind("<Configure>", self._drawer_later, add="+")
        self.tbl_model.set_menu([
            ("设为当前", self.model_activate, None),
            ("编辑", lambda: self.model_edit(self._pick()), None),
            ("试连这条", self.model_probe, None),
            (None, None, None),
            ("删除", self.model_delete, None),
        ])

        act = self.tbl_model.actions
        U.RoundButton(act, "＋ 新增模型", self.model_add, variant="primary",
                      font=U.FONT_S, height=28, padx=12).pack(side="right")
        for text, cmd in (("删除", self.model_delete),
                          ("编辑", lambda: self.model_edit(self._pick())),
                          ("设为当前", self.model_activate),
                          ("同步模型", self.model_sync),
                          ("收起分组", self.model_fold_toggle)):
            U.RoundButton(act, text, cmd, variant="ghost", font=U.FONT_S,
                          height=28, padx=9).pack(side="right", padx=(0, U.px(5)))

    # ------------------------------------------------------------ 供应商维度
    def model_fold_toggle(self) -> None:
        """一键收起 / 展开所有分组。组多的时候比一个个点三角快。"""
        now = getattr(self, "_fold_closed", False)
        self._fold_closed = not now
        self.tbl_model.set_all_open(not self._fold_closed)
        self.app.toast("已收起所有分组" if self._fold_closed else "已展开所有分组",
                       "ok", ms=1200)

    def _model_row_click(self, event) -> None:
        """行尾的 ✕ 直接删这一条。其余位置交给 Treeview 自己处理。"""
        try:
            col = self.tbl_model.column_at(event.x)
        except Exception:  # noqa: BLE001
            return
        if col != "del":
            return
        rec = (self.tbl_model.row_at(event.y) or {}).get("_raw")
        if rec:
            self._delete_rec(rec)

    def _provider_items(self) -> list[tuple]:
        """左栏：供应商名 + 「N 个模型 / 当前 / 已停用」。

        清单不再写死那几个 —— 用户能自己加（截图里 Cherry Studio 就有
        两个「Llama 网关」）。搜索框只过滤列表，不动选中项。
        """
        cur = self.cfg.current_id()
        q = str(self.v_search.get() or "").strip().lower()
        out = []
        for row in self.cfg.provider_list():
            pid = str(row["pid"])
            name = str(row.get("name") or pid)
            ms = self.cfg.provider_models(pid)
            if q and q not in name.lower() and q not in pid.lower():
                continue
            is_cur = any(str(m.get("id")) == cur for m in ms)
            bits = [f"{len(ms)} 个模型"]
            if is_cur:
                bits.append("当前")
            if not row.get("enabled", True):
                bits.append("已停用")
            out.append((pid, name, "　·　".join(bits), is_cur))
        return out

    def provider_pick(self, pid: str) -> None:
        rows = {str(r["pid"]): r for r in self.cfg.provider_list()}
        if pid not in rows:
            pid = next(iter(rows), aiops.DEFAULT_PROVIDER)
        self.sel_pid = pid
        row = rows.get(pid) or {}
        preset = aiops.PROVIDERS.get(pid) or {"label": row.get("name") or pid,
                                              "kind": row.get("kind") or "openai",
                                              "need_key": True, "hint": ""}
        self.v_base.set(self.cfg.provider_base(pid))
        self.v_key.set("")
        # 换了一家就把眼睛关回去：明文状态跨供应商延续等于把 Key 晾在屏幕上
        if getattr(self, "_key_shown", False):
            self._key_shown = False
            try:
                self.ent_key.configure(show="•")
                self.btn_eye.set_text("👁")
            except tk.TclError:
                pass
        n = len(self.cfg.provider_models(pid))
        self.lbl_prov.configure(text=str(row.get("name") or preset["label"]))
        self.lbl_prov_sub.configure(
            text=f"{n} 个模型　·　{preset['kind']} 接口")
        self.lbl_base_hint.configure(
            text=preset.get("hint") or "")
        try:
            self.v_enabled.set(bool(row.get("enabled", True)))
        except (AttributeError, tk.TclError):
            pass
        has = bool(self.cfg.provider_key_enc(pid))
        if has:
            self.lbl_key_hint.configure(text="已存，留空表示不修改")
            if not self.btn_key_clear.winfo_manager():
                self.btn_key_clear.pack(side="right", padx=(0, U.px(6)))
        else:
            self.lbl_key_hint.configure(
                text="未填" if preset.get("need_key")
                else "这条线路一般不需要 Key")
            if self.btn_key_clear.winfo_manager():
                self.btn_key_clear.pack_forget()
        self.plist.select(pid, fire=False)
        # 换了一家就把抽屉收掉：里面那条模型未必属于新供应商，留着等于给人挖坑
        self._drawer_shut()
        self.model_reload()

    def provider_save(self) -> None:
        pid = self.sel_pid
        base = self.v_base.get().strip()
        typed = self.v_key.get()
        self.cfg.set_provider(pid, base=base,
                              key=(typed if typed.strip() else None),
                              enabled=bool(self.v_enabled.get()))
        self.v_key.set("")
        self.provider_pick(pid)
        self.app.toast("供应商设置已保存，下面的模型跟着更新了", "ok")
        try:
            self.app._ai_refresh_dock_state()
        except Exception:  # noqa: BLE001
            pass

    def provider_add(self) -> None:
        """新增一条供应商。类型只决定默认地址和提示，先按 OpenAI 兼容起步。"""
        from tkinter import simpledialog
        name = simpledialog.askstring(
            "添加服务商", "给这条线路起个名字（例如「Llama 网关 https」）：",
            parent=self.winfo_toplevel())
        if not name or not name.strip():
            return
        pid = self.cfg.add_provider(name.strip())
        self.v_search.set("")
        self.provider_pick(pid)
        self.plist.set_items(self._provider_items())
        self.app.toast(f"已添加「{name.strip()}」，接着填地址和 Key", "ok")

    def _toggle_enabled(self) -> None:
        pid = getattr(self, "sel_pid", "")
        if not pid:
            return
        on = bool(self.v_enabled.get())
        self.cfg.set_provider(pid, enabled=on)
        self.plist.set_items(self._provider_items())
        self.app.toast(("已启用「" if on else "已停用「")
                       + self.cfg.provider_name(pid) + "」", "ok")

    def provider_remove(self) -> None:
        pid = getattr(self, "sel_pid", "")
        if not pid:
            return
        name = self.cfg.provider_name(pid)
        n = len(self.cfg.provider_models(pid))
        if not forms.confirm(self, "删除服务商",
                             f"删掉「{name}」？\n它下面的 {n} 条模型会一起删除。\n"
                             "（不会动对方的任何服务，只是本地不再登记）",
                             danger=True, ok_text="删除"):
            return
        self.cfg.remove_provider(pid)
        self.sel_pid = ""
        self.provider_pick(next(iter([r["pid"] for r in
                                      self.cfg.provider_list()] or [""]), ""))
        self.plist.set_items(self._provider_items())
        self.app.toast(f"已删除「{name}」", "ok")

    def provider_clear_key(self) -> None:
        pid = self.sel_pid
        label = self.cfg.provider_name(pid)
        if not forms.confirm(self, "清除 Key",
                             f"清掉「{label}」保存的 API Key？\n"
                             "下面的模型会一起变成「缺 Key」。",
                             danger=True, ok_text="清除"):
            return
        self.cfg.set_provider(pid, key="")
        self.provider_pick(pid)
        self.app.toast("已清除 Key", "ok")

    def _toggle_key_show(self) -> None:
        """眼睛：掩码 ↔ 明文。核对「是不是多粘了一个空格」要用。"""
        self._key_shown = not bool(getattr(self, "_key_shown", False))
        try:
            self.ent_key.configure(show="" if self._key_shown else "•")
            self.btn_eye.set_text("●" if self._key_shown else "👁")
        except tk.TclError:
            return
        if self._key_shown:
            self.lbl_key_hint.configure(text="明文显示中，核对完记得关掉（截图会带出去）")

    def _endpoint_menu(self) -> None:
        """「端点 ▾」下拉：切地址、加地址、删地址。"""
        pid = getattr(self, "sel_pid", "")
        if not pid:
            return
        eps = self.cfg.provider_endpoints(pid)
        cur = self.v_base.get().strip() or self.cfg.provider_base(pid)
        m = tk.Menu(self, tearoff=0)
        for u in eps:
            m.add_command(label=("● " if u == cur else "　 ") + u,
                          command=lambda uu=u: self._use_endpoint(uu))
        if eps:
            m.add_separator()
        m.add_command(label="＋ 添加端点…", command=self.provider_add_endpoint)
        if len(eps) > 1:
            m.add_command(label="－ 删掉当前端点", command=self.provider_del_endpoint)
        # 菜单被 popup 期间必须有人引用，否则 CPython 立刻回收 → 菜单闪一下就没了
        self._eps_menu = m
        try:
            x = self.btn_eps.winfo_rootx()
            y = self.btn_eps.winfo_rooty() + self.btn_eps.winfo_height()
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _use_endpoint(self, url: str) -> None:
        pid = getattr(self, "sel_pid", "")
        self.v_base.set(url)
        self.cfg.use_endpoint(pid, url)
        self.provider_pick(pid)
        self.app.toast(f"已切到 {url}", "ok")

    def provider_add_endpoint(self) -> None:
        from tkinter import simpledialog
        pid = getattr(self, "sel_pid", "")
        if not pid:
            return
        cur = self.v_base.get().strip() or self.cfg.provider_base(pid)
        url = simpledialog.askstring(
            "添加端点",
            "再登记一个接口地址（内网直连 / 外网中转 / 备用网关）：\n"
            f"当前生效：{cur}",
            parent=self.winfo_toplevel())
        if not url or not url.strip():
            return
        if not self.cfg.add_endpoint(pid, url.strip()):
            self.app.toast("这个地址已经在清单里了", "warn")
            return
        self.provider_pick(pid)
        self.app.toast("端点已添加，点「端点 ▾」可以切过去", "ok")

    def provider_del_endpoint(self) -> None:
        pid = getattr(self, "sel_pid", "")
        if not pid:
            return
        url = self.v_base.get().strip() or self.cfg.provider_base(pid)
        if not url:
            return
        if not forms.confirm(self, "删除端点",
                             f"把「{url}」从这家的地址清单里删掉？\n"
                             "如果它正在生效，会自动切到清单里的下一个。",
                             danger=True, ok_text="删除"):
            return
        if not self.cfg.remove_endpoint(pid, url):
            self.app.toast("最后一条地址删不得 —— 删了这家就没有入口了", "warn")
            return
        self.provider_pick(pid)
        self.app.toast("端点已删除", "ok")

    def provider_probe(self) -> None:
        """试连整个供应商：地址 + Key 对不对，不用挑某一条模型。"""
        pid = self.sel_pid
        base = self.v_base.get().strip() or self.cfg.provider_base(pid)
        ms = self.cfg.provider_models(pid)
        model = str((ms[0].get("model") if ms else "")
                    or (aiops.PROVIDERS.get(pid) or {}).get("model") or "")
        enc = self.cfg.provider_key_enc(pid)
        typed = self.v_key.get().strip()
        if typed:
            try:
                import crypto_store
                enc = crypto_store.protect(typed) or ""
            except Exception:  # noqa: BLE001
                enc = ""
        rec = {"provider": pid, "base": base, "model": model, "key_enc": enc}
        label = self.cfg.provider_name(pid)
        self.lbl_hint.configure(text=f"正在试连「{label}」…（最多 20 秒）")
        self.app.toast(f"正在试连「{label}」…", "warn")

        def work():
            try:
                ok, msg = aiops.probe_model(rec, timeout=20)
            except Exception as e:  # noqa: BLE001
                ok, msg = False, f"{type(e).__name__}: {e}"
            try:
                self.after(0, lambda: self._probe_done(label, ok, msg))
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _close(self) -> None:
        cb = self.on_close
        if cb is not None:
            cb()
            return
        try:
            self.winfo_toplevel().destroy()
        except tk.TclError:
            pass

    # ================================================================ 数据
    def model_reload(self) -> None:
        cfg = self.cfg
        pid = getattr(self, "sel_pid", cfg.provider)
        pname = cfg.provider_name(pid) if pid else "这一家"
        active = cfg.current_id()
        rows = []
        for m in cfg.provider_models(pid):
            v = aiops.model_view(m, active)
            v["_tone"] = v.get("tone") or ""
            v["group"] = str(m.get("group") or "默认")
            v["_raw"] = m
            rows.append(v)
        # 对齐 Cherry Studio：同一分组排在一起（组内按名称）。
        # 折叠头的顺序就取这里的第一出现顺序，所以这一步不能省。
        rows.sort(key=lambda r: (str(r.get("group") or "默认").lower(),
                                 str(r.get("name") or "").lower()))
        self.tbl_model.set_rows(rows)
        self.tbl_model.set_empty(
            f"「{pname}」下还没有模型",
            "点右上「＋ 新增模型」填一条，或用「同步模型」从接口拉一份。")

        self.plist.set_items(self._provider_items())
        all_models = cfg.models()
        cur = cfg.current()
        unknown = sum(1 for m in all_models
                      if not (m.get("context") or m.get("ctx")))
        self.lbl_sum.configure(
            text=f"共 {len(all_models)} 个模型 · 当前：{cur.get('name') or '未命名'}")
        tip = ("切换只影响「当前模型」；助手下次调用就用新的那条。"
               f"现在用的是 {cur.get('model') or '（未填模型名）'}")
        if unknown:
            # 不吓人，但要让人知道「未知」是抓不到，不是填错了
            tip += f"　·　{unknown} 个模型的上下文未知，可点「编辑」手填或点「同步模型」重试"
        self.lbl_hint.configure(text=tip)

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
        # 走抽屉：四个常改的字段在这儿就够了，token/思考那些点「更多参数…」
        self._drawer_open(None)

    def model_edit(self, rec: dict | None) -> None:
        rec = rec or self._need_pick()
        if not rec:
            return
        self._drawer_open(rec)

    def model_delete(self) -> None:
        rec = self._need_pick()
        if rec is None:
            return
        self._delete_rec(rec)

    def _delete_rec(self, rec: dict) -> None:
        """删之前一定问一次 —— 行尾的 ✕ 离鼠标太近，误点概率不低。"""
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

    # ================================================================ 同步
    def model_sync(self) -> None:
        """从服务商的 /models 拉一份列表，只留文本模型。

        顺手抓上下文/最大输入/最大输出 —— 抓不到就是「未知」，不猜。
        """
        rec = self._pick() or self.cfg.current()
        prov = str(rec.get("provider") or getattr(self, "sel_pid", "")
                   or self.cfg.provider)
        preset = aiops.PROVIDERS.get(prov) or aiops.PROVIDERS[aiops.DEFAULT_PROVIDER]
        if preset.get("kind") == "dsh":
            self.app.toast("Harness 线路没有模型列表；换一条 OpenAI 兼容线路再同步", "warn")
            return
        base = str(rec.get("base") or self.cfg.provider_base(prov)
                   or preset.get("base") or "")
        if not base:
            self.app.toast("这家供应商还没填接口地址，先在上面填上", "warn")
            return
        key = (self.cfg.model_key(str(rec.get("id") or ""))
               or self.cfg.provider_key(prov) or self.cfg.get_key())
        self.lbl_hint.configure(text=f"正在从 {base} 同步模型列表…（最多 20 秒）")
        self.app.toast("正在同步模型列表…", "warn")

        def work():
            try:
                rows = modelsync.sync_text_models(base, key, timeout=20)
                err = ""
            except Exception as e:  # noqa: BLE001
                rows, err = [], f"{type(e).__name__}: {e}"
            try:
                self.after(0, lambda: self._sync_done(prov, base, rows, err))
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _sync_done(self, prov: str, base: str, rows: list, err: str) -> None:
        if err:
            try:
                self.lbl_hint.configure(text="✕ 同步失败：" + err)
            except tk.TclError:
                pass
            self.app.toast("同步失败：" + err, "err", ms=5200)
            return
        if not rows:
            msg = "这个接口没返回可用的文本模型"
            try:
                self.lbl_hint.configure(text="✕ " + msg)
            except tk.TclError:
                pass
            self.app.toast(msg, "warn")
            return
        existing = {str(m.get("model") or "") for m in self.cfg.models()
                    if str(m.get("provider")) == prov}
        dlg = _SyncDialog(self, rows, existing)
        try:
            self.wait_window(dlg)
        except tk.TclError:
            return
        picked = dlg.picked or []
        if not picked:
            return
        added = 0
        for row in picked:
            mid = str(row.get("id") or "")
            if not mid:
                continue
            name = (aiops.PROVIDERS.get(prov, {}).get("label") or prov) + " · " + mid
            rec = {
                "name": name,
                "provider": prov if prov in aiops.PROVIDERS else aiops.DEFAULT_PROVIDER,
                "base": base,
                "model": mid,
                "context": int(row.get("context") or 0),
                "max_input": int(row.get("max_input") or 0),
                "max_output": int(row.get("max_output") or 0),
                "modalities": row.get("modalities") or [],
                "caps": modelsync.guess_caps(mid),
            }
            self.cfg.upsert_model(rec)
            added += 1
        self.provider_pick(prov)          # 同步完自动切到那家供应商看结果
        self.app.toast(f"同步完成，新增 {added} 个文本模型", "ok")
        try:
            self.lbl_hint.configure(
                text=f"✓ 从 {base} 拉到 {len(rows)} 个文本模型，已新增 {added} 个")
        except tk.TclError:
            pass

    # ================================================================ 表单
    # ------------------------------------------------------------ 抽屉
    def _drawer_later(self, _e=None) -> None:
        """窗口/分栏拖动时重算抽屉位置。debounce 一下 —— 每像素重排会顿。"""
        if self.drawer is None:
            return
        if self._drawer_job:
            try:
                self.after_cancel(self._drawer_job)
            except Exception:  # noqa: BLE001
                pass
        self._drawer_job = self.after(60, self._drawer_place)

    def _drawer_place(self) -> None:
        self._drawer_job = None
        d = self.drawer
        if d is None:
            return
        try:
            w = int(self._model_host.winfo_width())
            h = int(self._model_host.winfo_height())
            top = int(self.tbl_model.toolbar.winfo_reqheight()) + U.px(8)
            dw = U.px(342)
            d.place(x=max(0, w - dw), y=top, width=dw,
                    height=max(U.px(160), h - top))
            d.lift()
        except tk.TclError:
            pass

    def _drawer_shut(self) -> None:
        d = getattr(self, "drawer", None)
        self.drawer = None
        if d is not None:
            try:
                d.destroy()
            except tk.TclError:
                pass

    def _drawer_open(self, rec: dict | None = None) -> None:
        """打开「编辑模型」抽屉。

        用 place 浮在表格右半边之上，而不是 grid 挤窄表格 ——
        挤窄会让表格每次开抽屉都重排列宽（横向滚动条忽隐忽现），
        浮层不动表格，关掉就恢复原样。
        """
        self._drawer_shut()
        d = _ModelDrawer(self._model_host, self, rec)
        self.drawer = d
        self._drawer_place()

    def _form(self, rec: dict | None, provider: str = "") -> dict | None:
        """新增 / 编辑共用一张表单。rec 为空就是新增。

        `provider` 是新增时的默认供应商 —— 左边点了哪一家，新增就落在哪一家，
        省得每次都要在表单里再选一遍。
        """
        dlg = _ModelForm(self, rec, self.cfg, default_provider=provider)
        try:
            self.wait_window(dlg)
        except tk.TclError:
            return None
        return dlg.result


# ==================================================================== 抽屉
class _ModelDrawer(tk.Frame):
    """右侧「编辑模型」抽屉（对齐 Cherry Studio 的紧凑编辑面板）。

    和 `_ModelForm` 的分工：抽屉只放四个字段 —— 模型 ID / 名称 / 分组 / 用途。
    这四个是日常真会改的（换条线路、把模型归到某个分组）；token 限制、
    思考档位、模态填一次基本不再动，放「更多参数…」里（还是原来的完整表单）。

    为什么是抽屉不是弹窗：挑模型时经常连着改好几条。弹窗每次都要
    「取消 → 找下一行 → 再双击」，抽屉开着就能一路点下去，列表也一直在眼前。
    """

    def __init__(self, master, page, rec: dict | None = None):
        tk.Frame.__init__(self, master, bg=U.T.card, bd=0,
                          highlightthickness=1, highlightbackground=U.T.border)
        self.page = page
        self.cfg = page.cfg
        self.rec = dict(rec or {})
        self.is_new = not rec
        self.pid = str(self.rec.get("provider") or page.sel_pid or "")

        head = tk.Frame(self, bg=U.T.card)
        head.pack(fill="x", padx=U.px(14), pady=(U.px(12), 0))
        tk.Label(head, text=("新增模型" if self.is_new else "编辑模型"),
                 bg=U.T.card, fg=U.T.text, font=U.FONT_B).pack(side="left")
        U.RoundButton(head, "✕", self.close, variant="ghost", font=U.FONT_XS,
                      height=24, padx=8).pack(side="right")
        tk.Label(self, text=self.cfg.provider_name(self.pid), bg=U.T.card,
                 fg=U.T.text_mute, font=U.FONT_XS, anchor="w").pack(
            fill="x", padx=U.px(14), pady=(U.px(2), 0))

        self.v_model = tk.StringVar(value=str(self.rec.get("model") or ""))
        self.v_name = tk.StringVar(value=str(self.rec.get("name") or ""))
        self.v_group = tk.StringVar(value=str(self.rec.get("group") or ""))

        # 页脚先 pack 到底部：字段区再长也压不掉「保存 / 取消」——
        # 窗口小的时候最不能丢的就是这两个按钮。
        foot = tk.Frame(self, bg=U.T.card)
        foot.pack(side="bottom", fill="x", padx=U.px(14), pady=(U.px(4), U.px(12)))
        self.lb_err = tk.Label(self, text="", bg=U.T.card, fg=U.T.err,
                               font=U.FONT_XS, anchor="w", justify="left",
                               wraplength=U.px(310))
        self.lb_err.pack(side="bottom", fill="x", padx=U.px(14))
        btnrow = tk.Frame(foot, bg=U.T.card)
        btnrow.pack(fill="x", pady=(U.px(6), 0))
        U.RoundButton(btnrow, "保存", self.save, variant="primary",
                      font=U.FONT_S, height=30, padx=16).pack(side="right")
        U.RoundButton(btnrow, "取消", self.close, variant="secondary",
                      font=U.FONT_S, height=30, padx=12).pack(
            side="right", padx=(0, U.px(6)))
        U.RoundButton(btnrow, "更多参数…", self.more, variant="ghost",
                      font=U.FONT_XS, height=30, padx=10).pack(side="left")

        # 字段区可滚：抽屉高度跟着窗口走，字段一屏放不下时能滚，不裁字
        self._sc = _Scroll(self, 200, role="card")
        self._sc.pack(fill="both", expand=True, padx=(0, 0), pady=(U.px(8), 0))
        body = self._sc.inner
        body.columnconfigure(0, weight=1)
        r = 0
        for key, label, var, hint in (
                ("model", "模型 ID", self.v_model,
                 "接口里那个名字，例如 deepseek-chat"),
                ("name", "模型名称", self.v_name,
                 "列表里显示的名字；留空就用模型 ID"),
                ("group", "分组名称", self.v_group,
                 "同一组的折叠在一起，例如 qwen3.6-35b")):
            tk.Label(body, text=label, bg=U.T.card, fg=U.T.text_dim,
                     font=U.FONT_XS, anchor="w").grid(row=r, column=0, sticky="w",
                                                      padx=U.px(14))
            ent = page._entry(body, var)
            ent.grid(row=r + 1, column=0, sticky="we", ipady=U.px(3),
                     padx=U.px(14), pady=(U.px(2), 0))
            tk.Label(body, text=hint, bg=U.T.card, fg=U.T.text_mute,
                     font=U.FONT_XS, anchor="w", justify="left",
                     wraplength=U.px(296)).grid(row=r + 2, column=0, sticky="w",
                                                padx=U.px(14),
                                                pady=(U.px(2), U.px(9)))
            setattr(self, "ent_" + key, ent)
            r += 3

        # 模型用途：助手只会发 chat/completions，所以只有「对话」能选。
        # 其余照样列出来（置灰 + 说明），不然用户会以为是自己没找对地方。
        tk.Label(body, text="模型用途", bg=U.T.card, fg=U.T.text_dim,
                 font=U.FONT_XS, anchor="w").grid(row=r, column=0, sticky="w",
                                                  padx=U.px(14))
        use = tk.Frame(body, bg=U.T.card)
        use.grid(row=r + 1, column=0, sticky="w", padx=U.px(14), pady=(U.px(4), 0))
        U.Pill(use, "对话", tone="brand", height=22,
               font=U.FONT_XS).pack(side="left")
        for t in ("嵌入", "重排", "图像", "视频"):
            U.Pill(use, t, tone="mute", height=22,
                   font=U.FONT_XS).pack(side="left", padx=(U.px(6), 0))
        tk.Label(body, text="只有「对话」可用：这个助手只发 chat/completions",
                 bg=U.T.card, fg=U.T.text_mute, font=U.FONT_XS, anchor="w",
                 justify="left", wraplength=U.px(296)).grid(
            row=r + 2, column=0, sticky="w", padx=U.px(14),
            pady=(U.px(3), U.px(12)))

        # 滚轮要能滚：Frame 上的 bind 管不到子控件（bindtags 走的是
        # 控件 → 类 → toplevel），所以逐个挂一遍。
        self._bind_wheel()
        self.bind("<Escape>", lambda e: self.close())
        self.after(30, self._focus)

    def _bind_wheel(self) -> None:
        sc = self._sc

        def on_wheel(e):
            sc._wheel(e)
            return "break"

        stack = [self]
        while stack:
            w = stack.pop()
            # 下拉框的滚轮是「换选项」，抢过来只会让人误操作
            if not isinstance(w, ttk.Combobox):
                try:
                    w.bind("<MouseWheel>", on_wheel, add="+")
                except tk.TclError:
                    pass
            try:
                stack.extend(w.winfo_children())
            except tk.TclError:
                continue

    def _focus(self) -> None:
        try:
            (self.ent_name if (self.is_new and not self.v_model.get())
             else self.ent_model).focus_set()
        except tk.TclError:
            pass

    def _fail(self, msg: str) -> None:
        try:
            self.lb_err.configure(text=msg)
        except tk.TclError:
            pass

    def close(self) -> None:
        self.page._drawer_shut()

    def _collect(self) -> dict:
        out = dict(self.rec)
        out["provider"] = self.pid
        model = self.v_model.get().strip()
        if model:
            out["model"] = model
        out["name"] = (self.v_name.get().strip()
                       or out.get("model") or "未命名模型")
        out["group"] = self.v_group.get().strip()
        return out

    def save(self) -> None:
        if not self.v_model.get().strip() and not self.rec.get("model"):
            return self._fail("模型 ID 不能空 —— 接口就是按它找模型的")
        out = self._collect()
        saved = self.cfg.upsert_model(out)
        # 第一条自动设为当前，省得用户还得再点一次
        if len(self.cfg.models()) == 1:
            self.cfg.set_active(saved["id"])
        self.page.model_reload()
        self.page.app.toast("模型已保存", "ok")
        try:
            self.page.app._ai_refresh_dock_state()
        except Exception:  # noqa: BLE001
            pass
        self.close()

    def more(self) -> None:
        """转去完整表单。抽屉里填的值先带过去，免得用户重填一遍。"""
        rec = self._collect()
        self.close()
        data = self.page._form(rec, provider=self.pid)
        if not data:
            return
        saved = self.cfg.upsert_model(data)
        if len(self.cfg.models()) == 1:
            self.cfg.set_active(saved["id"])
        self.page.model_reload()
        self.page.app.toast("模型已保存", "ok")


# ==================================================================== 表单窗
class _Scroll(tk.Frame):
    """表单内容比屏幕高，必须能滚 —— 150% 缩放下这些字段一屏放不完。"""

    def __init__(self, master, height: int, role: str = "page"):
        bgc = U.T.card if role == "card" else U.T.page
        super().__init__(master, bg=bgc, bd=0)
        self.role = role
        self.cv = tk.Canvas(self, bg=bgc, bd=0, highlightthickness=0,
                            height=max(U.px(200), int(height)),
                            yscrollincrement=U.px(18))
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.cv.yview,
                                style="Card.Vertical.TScrollbar")
        self.cv.configure(yscrollcommand=self.sb.set)
        self.sb.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)
        self.inner = U.wframe(self.cv, role)
        self._win = self.cv.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._sync)
        self.cv.bind("<Configure>", self._widen)
        self.bind("<MouseWheel>", self._wheel)

    def _sync(self, _e=None) -> None:
        try:
            self.cv.configure(scrollregion=self.cv.bbox("all"))
        except tk.TclError:
            pass

    def _widen(self, e) -> None:
        try:
            self.cv.itemconfigure(self._win, width=e.width)
        except tk.TclError:
            pass

    def _wheel(self, e):
        d = getattr(e, "delta", 0) or 0
        if d:
            step = -1 if d > 0 else 1
        else:
            try:
                step = -1 if int(getattr(e, "num", 0)) == 4 else 1
            except (TypeError, ValueError):
                return
        try:
            self.cv.yview_scroll(step * 3, "units")
        except tk.TclError:
            pass


class ModelDialog(tk.Toplevel):
    """独立窗口形态的模型管理（侧栏「⋯ → 模型设置」还用这个）。"""

    def __init__(self, app, parent=None):
        tk.Toplevel.__init__(self, app)
        self.app = app
        self.withdraw()
        self.title("模型管理 — 智能运维助手")
        self.configure(bg=U.T.page)
        self.geometry(f"{U.px(1100)}x{U.px(600)}+{U.px(150)}+{U.px(100)}")
        self.minsize(U.px(900), U.px(470))
        self.transient(app)
        self.panel = ModelPanel(app, self, on_close=self.destroy)
        self.panel.pack(fill="both", expand=True)
        self.deiconify()

    def __getattr__(self, name: str):
        """窗口形态对外还是原来那套接口：方法都转给内嵌面板。

        调用方（侧栏菜单、自测脚本）一直写的是 `dlg.model_add()`、
        `dlg.provider_pick(...)`，包装之后不该逼着它们全改一遍。
        """
        panel = self.__dict__.get("panel")
        if panel is not None:
            return getattr(panel, name)
        raise AttributeError(name)


class _ModelForm(tk.Toplevel):
    """文本模型表单。

    比 `forms.FormDialog` 复杂的地方（所以没复用它）：
      · 勾「思考」才展开档位区，档位是多选 + 一个默认档
      · token 三件套要「预设胶囊 + 手填」，胶囊点了往输入框里写值
      · 字段多到一屏放不下，要能滚
    """

    def __init__(self, parent, rec: dict | None, cfg, default_provider: str = ""):
        super().__init__(parent)
        self.withdraw()
        self.rec = dict(rec or {})
        self.cfg = cfg
        self.result: dict | None = None
        prov = str(self.rec.get("provider") or default_provider
                   or aiops.DEFAULT_PROVIDER)
        if prov not in aiops.PROVIDERS:
            prov = aiops.DEFAULT_PROVIDER
        self._prov = prov
        prov_label = aiops.PROVIDERS[prov]["label"]

        title = "编辑模型" if rec else "新增模型"
        self.title(f"{title} — {prov_label} — 智能运维助手")
        self.configure(bg=U.T.page)
        self.transient(parent)
        self.resizable(False, True)

        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(20), pady=U.px(16))
        # 服务商只在标题里报一次：这个窗口是从某一家下面点进来的，
        # 归属已经定了，再放一个下拉框等于让用户以为自己能换家（换了 Key 就对不上）。
        head = U.wframe(wrap, "page")
        head.pack(fill="x", anchor="w")
        U.wlabel(head, title, "page", U.FONT_T).pack(side="left")
        U.Pill(head, prov_label, tone="brand", height=22,
               font=U.FONT_XS).pack(side="left", padx=(U.px(10), 0))
        U.wlabel(wrap,
                 "只收文本模型。接口地址和 API Key 在左侧这家服务商里统一设置，"
                 "这里只描述「这一条模型」。Token 限制抓不到就留空，"
                 "界面会显示「未知」，不会替你猜一个数。",
                 "pagedim", U.FONT_S, justify="left",
                 wraplength=U.px(680)).pack(anchor="w", pady=(U.px(4), 0))

        screen_h = self.winfo_screenheight()
        body_h = min(U.px(430), int(screen_h * 0.48))
        sc = _Scroll(wrap, body_h)
        sc.pack(fill="both", expand=True, pady=(U.px(12), 0))
        body = sc.inner
        body.columnconfigure(1, weight=1)

        r = 0
        r = self._row_text(body, r, "name", "名称",
                           self.rec.get("name") or "",
                           "给自己看的标签，例如「公司网关」「本机 Ollama」")
        # 接口地址不在这里填：它跟 Key 一样属于服务商，上面那张卡里一次填完，
        # 同一家下面每一条都跟着走。模型层再放一份只是把同一串 URL 抄 N 遍。
        r = self._row_model(body, r)
        r = self._row_text(body, r, "group", "分组名称",
                           self.rec.get("group") or "",
                           "同一家供应商下按它折叠，例如 deepseek、qwen3.6-35b")
        r = self._row_kind(body, r)
        r = self._row_caps(body, r)
        r = self._row_thinking(body, r)
        r = self._row_modalities(body, r)
        r = self._row_tokens(body, r, "context", "上下文窗口",
                             "整个窗口能装多少 token", _CTX_PRESETS)
        r = self._row_tokens(body, r, "max_input", "最大输入",
                             "单次请求最多发多少", _IN_PRESETS)
        r = self._row_tokens(body, r, "max_output", "最大输出",
                             "单次最多回多少", _OUT_PRESETS)
        r = self._row_text(body, r, "temperature", "温度",
                           str(self.rec.get("temperature", 0.2)),
                           "运维场景建议 0–0.3，越小越按事实说话")
        r = self._row_text(body, r, "note", "备注", self.rec.get("note") or "",
                           "可选：这条线路什么时候用")

        # 滚轮事件挂在 Toplevel 上：Tk 的 bindtags 里含所属 toplevel，
        # 所以子控件上的滚轮也会走到这里。挂在 Frame 上只有帧空白处管用。
        self.bind("<MouseWheel>", sc._wheel)

        self.lb_err = U.wlabel(wrap, "", "pageerr", U.FONT_S, justify="left",
                               wraplength=U.px(680))
        self.lb_err.pack(anchor="w", pady=(U.px(6), 0))

        foot = U.wframe(wrap, "page")
        foot.pack(fill="x", pady=(U.px(10), 0))
        U.RoundButton(foot, "保存", self._ok, variant="primary",
                      height=34, padx=22).pack(side="right")
        U.RoundButton(foot, "取消", self._cancel, variant="secondary",
                      height=34, padx=18).pack(side="right", padx=(0, U.px(8)))

        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._sync_thinking_area()

        self.update_idletasks()
        w = max(self.winfo_reqwidth(), U.px(720))
        h = min(self.winfo_reqheight(), int(screen_h * 0.86))
        try:
            px = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
            py = parent.winfo_rooty() + U.px(30)
        except tk.TclError:
            px, py = U.px(160), U.px(90)
        self.geometry(f"{w}x{h}+{max(0, px)}+{max(0, py)}")
        self.deiconify()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        try:
            self.ent_name.focus_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 行
    def _label(self, body, text: str, r: int, hint: str = "") -> tk.Frame:
        box = U.wframe(body, "page")
        box.grid(row=r, column=0, sticky="nw", padx=(0, U.px(14)),
                 pady=(U.px(8), U.px(8)))
        U.wlabel(box, text, "pagedim", U.FONT_S).pack(anchor="w")
        return box

    def _cell(self, body, r: int) -> tk.Frame:
        cell = U.wframe(body, "page")
        cell.grid(row=r, column=1, sticky="ew", pady=(U.px(8), U.px(8)))
        return cell

    def _entry(self, cell, value: str, width: int = 42,
               password: bool = False) -> ttk.Entry:
        ent = ttk.Entry(cell, width=width, font=U.FONT_S,
                        show="•" if password else "")
        ent.insert(0, str(value or ""))
        ent.pack(side="left")
        return ent

    def _hint(self, cell, text: str, width: int = 300) -> None:
        if text:
            U.wlabel(cell, text, "pagemute", U.FONT_XS, justify="left",
                     wraplength=U.px(width)).pack(side="left",
                                                  padx=(U.px(9), 0))

    def _row_text(self, body, r: int, key: str, label: str, value: str,
                  hint: str = "", password: bool = False) -> int:
        self._label(body, label, r)
        cell = self._cell(body, r)
        ent = self._entry(cell, value, password=password)
        self._hint(cell, hint)
        setattr(self, "ent_" + key, ent)
        return r + 1

    def _row_model(self, body, r: int) -> int:
        self._label(body, "模型名", r)
        cell = self._cell(body, r)
        ent = self._entry(cell, self.rec.get("model") or "")
        self.ent_model = ent
        U.RoundButton(cell, "同步…", self._open_sync, variant="ghost",
                      font=U.FONT_XS, height=24, padx=9).pack(
            side="left", padx=(U.px(8), 0))
        self._hint(cell, "例如 deepseek-chat、qwen3-coder:14b")
        return r + 1

    def _row_kind(self, body, r: int) -> int:
        self._label(body, "模型类型", r)
        cell = self._cell(body, r)
        badge = U.Pill(cell, "文本", tone="brand", height=22, font=U.FONT_XS)
        badge.pack(side="left")
        self._hint(cell, "这个助手只发 chat/completions，所以只有文本类型", 340)
        return r + 1

    def _row_caps(self, body, r: int) -> int:
        self._label(body, "能力", r)
        cell = self._cell(body, r)
        caps = self.rec.get("caps") if isinstance(self.rec.get("caps"), dict) else {}
        th = caps.get("thinking") or {}
        self.var_think = tk.BooleanVar(value=bool(th.get("levels")))
        self.var_tools = tk.BooleanVar(value=bool(caps.get("tools")))
        self.chk_think = U.Check(cell, "思考", self.var_think, font=U.FONT_S,
                                 command=self._sync_thinking_area)
        self.chk_think.pack(side="left")
        self.chk_tools = U.Check(cell, "工具", self.var_tools, font=U.FONT_S)
        self.chk_tools.pack(side="left", padx=(U.px(14), 0))
        return r + 1

    def _row_thinking(self, body, r: int) -> int:
        """档位区。不勾「思考」时整块不显示（grid_remove）。"""
        lb = self._label(body, "思考档位", r)
        cell = self._cell(body, r)
        self._think_label = lb
        self._think_cell = cell
        self._think_row = r
        caps = self.rec.get("caps") if isinstance(self.rec.get("caps"), dict) else {}
        th = caps.get("thinking") or {}
        have = [str(x) for x in (th.get("levels") or [])]
        default = str(th.get("default") or "")
        self.chk_levels: dict[str, U.Check] = {}
        self.var_levels: dict[str, tk.BooleanVar] = {}
        for key, label in aiops.THINKING_LEVELS:
            var = tk.BooleanVar(value=(key in have) if have else (key in ("off", "high")))
            ck = U.Check(cell, label, var, font=U.FONT_S,
                         command=self._sync_default_values)
            ck.pack(side="left", padx=(0, U.px(10)))
            self.var_levels[key] = var
            self.chk_levels[key] = ck
        self.var_def = tk.StringVar()
        U.wlabel(cell, "默认", "pagemute", U.FONT_XS).pack(side="left",
                                                           padx=(U.px(12), U.px(5)))
        self.cmb_default = ttk.Combobox(cell, textvariable=self.var_def,
                                        state="readonly", width=8,
                                        font=U.FONT_S)
        self.cmb_default.pack(side="left")
        self._think_default = default
        self._sync_default_values()
        return r + 1

    def _row_modalities(self, body, r: int) -> int:
        self._label(body, "输入模态", r)
        cell = self._cell(body, r)
        have = self.rec.get("modalities") or []
        self.var_mods: dict[str, tk.BooleanVar] = {}
        for key, label in aiops.MODALITIES:
            var = tk.BooleanVar(value=(key in have))
            U.Check(cell, label, var, font=U.FONT_S).pack(side="left",
                                                          padx=(0, U.px(14)))
            self.var_mods[key] = var
        self._hint(cell, "只有这两项；视频/文件不做", 220)
        return r + 1

    def _row_tokens(self, body, r: int, key: str, label: str, hint: str,
                    presets: list[int]) -> int:
        self._label(body, label, r)
        cell = self._cell(body, r)
        ent = ttk.Entry(cell, width=14, font=U.FONT_S)
        val = self.rec.get(key) or (self.rec.get("ctx") if key == "context" else 0) or 0
        if int(val or 0) > 0:
            ent.insert(0, str(int(val)))
        ent.pack(side="left")
        setattr(self, "ent_" + key, ent)
        btns = []
        for k in presets:
            def _fill(_=None, e=ent, v=_preset_value(k)):
                e.delete(0, "end")
                e.insert(0, str(v))
            btn = U.RoundButton(cell, _preset_label(k), _fill, variant="soft",
                                font=U.FONT_XS, height=22, padx=8)
            btn.pack(side="left", padx=(U.px(4), 0))
            btns.append(btn)
        # 留着引用：一来测试要能点，二来主题切换时它们也得在册
        setattr(self, "chips_" + key, btns)
        self._hint(cell, hint + "；留空 = 未知", 150)
        return r + 1

    # ------------------------------------------------------------ 联动
    def _sync_thinking_area(self) -> None:
        on = bool(self.var_think.get())
        try:
            if on:
                self._think_label.grid()
                self._think_cell.grid()
            else:
                self._think_label.grid_remove()
                self._think_cell.grid_remove()
        except tk.TclError:
            pass

    def _sync_default_values(self) -> None:
        """默认档只能从已勾的档位里选 —— 这是「默认必须属于可用」的界面保证。"""
        keys = [k for k in aiops.THINKING_KEYS if self.var_levels[k].get()]
        labels = [aiops.THINKING_LABEL[k] for k in keys]
        try:
            self.cmb_default.configure(values=labels)
        except tk.TclError:
            return
        want = self._think_default or aiops.DEFAULT_THINKING
        idx = keys.index(want) if want in keys else (0 if keys else -1)
        try:
            if idx >= 0:
                self.cmb_default.current(idx)
            else:
                self.var_def.set("")
        except tk.TclError:
            pass

    def _open_sync(self) -> None:
        """表单里的「同步…」：按当前供应商的 base + key 拉一次，把模型名填进来。"""
        # 表单里已经没有接口地址和 Key 输入框了：它们属于供应商那一层。
        base = self.cfg.provider_base(self._prov)
        if not base:
            self._fail("这家服务商还没填接口地址，先在上面那张卡里填")
            return
        key = (self.cfg.provider_key(self._prov)
               or self.cfg.model_key(str(self.rec.get("id") or ""))
               or self.cfg.get_key())
        self._fail("正在同步…")

        def work():
            try:
                rows = modelsync.sync_text_models(base, key, timeout=20)
                err = ""
            except Exception as e:  # noqa: BLE001
                rows, err = [], f"{type(e).__name__}: {e}"
            try:
                self.after(0, lambda: self._sync_pick(rows, err))
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _sync_pick(self, rows: list, err: str) -> None:
        if err:
            self._fail("同步失败：" + err)
            return
        if not rows:
            self._fail("这个接口没返回可用的文本模型")
            return
        dlg = _SyncDialog(self, rows, set())
        try:
            self.wait_window(dlg)
        except tk.TclError:
            return
        picked = dlg.picked or []
        if not picked:
            self._fail("")
            return
        row = picked[0]
        try:
            self.ent_model.delete(0, "end")
            self.ent_model.insert(0, str(row.get("id") or ""))
        except tk.TclError:
            return
        # 顺手把抓到的 token 限制填进三个输入框，抓不到就不动（保持未知）
        for key in ("context", "max_input", "max_output"):
            v = int(row.get(key) or 0)
            if v <= 0:
                continue
            ent = getattr(self, "ent_" + key, None)
            if ent is None:
                continue
            ent.delete(0, "end")
            ent.insert(0, str(v))
        mods = row.get("modalities") or []
        for k, var in self.var_mods.items():
            var.set(k in mods)
        caps = modelsync.guess_caps(str(row.get("id") or ""))
        th = caps.get("thinking") or {}
        if th.get("levels"):
            for k, var in self.var_levels.items():
                var.set(k in th["levels"])
            self._think_default = th.get("default") or ""
            self.var_think.set(True)
            self._sync_thinking_area()
            self._sync_default_values()
        self._fail(f"已填入 {len(picked)} 个候选中选中的那个")

    # ------------------------------------------------------------ 结果
    def _fail(self, msg: str) -> None:
        try:
            self.lb_err.configure(text=msg)
        except tk.TclError:
            pass

    def _cancel(self) -> None:
        self.result = None
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _ok(self) -> None:
        prov = self._prov
        preset = aiops.PROVIDERS[prov]
        name = self.ent_name.get().strip()
        model = self.ent_model.get().strip() or preset["model"]

        # 思考：勾了就必须至少一个档位，默认档必须在档位里
        thinking = None
        if self.var_think.get():
            levels = [k for k in aiops.THINKING_KEYS if self.var_levels[k].get()]
            if not levels:
                return self._fail("勾了「思考」就要至少选一个档位")
            want = self.var_def.get()
            default = next((k for k in levels
                            if aiops.THINKING_LABEL[k] == want),
                           levels[0])
            thinking = {"levels": levels, "default": default}

        # Token 三件套：能认出来才算数，认不出来就报错（不静默改成 0）
        toks = {}
        for key, label in (("context", "上下文窗口"),
                           ("max_input", "最大输入"),
                           ("max_output", "最大输出")):
            raw = getattr(self, "ent_" + key).get().strip()
            if not raw:
                toks[key] = 0
                continue
            n = modelsync.to_int(raw)
            if n is None:
                return self._fail(f"{label} 认不出来：「{raw}」。"
                                  "可写 128000、128K、1M 这类")
            toks[key] = n

        try:
            temp = max(0.0, min(2.0, float(self.ent_temperature.get() or 0.2)))
        except (TypeError, ValueError):
            return self._fail("温度要填数字（0–2）")

        out = dict(self.rec)
        out.update({
            "name": name or (preset["label"] + " · " + (model or "未命名")),
            "provider": prov,
            "group": self.ent_group.get().strip(),
            "model": model,
            "kind": "text",
            "caps": {"thinking": thinking, "tools": bool(self.var_tools.get())},
            "modalities": [k for k, v in self.var_mods.items() if v.get()],
            "context": toks["context"],
            "max_input": toks["max_input"],
            "max_output": toks["max_output"],
            "ctx": toks["context"],
            "temperature": temp,
            "note": self.ent_note.get().strip(),
        })
        # Key 不在这里取：它属于服务商，改一次全下生效。
        self.result = out
        try:
            self.destroy()
        except tk.TclError:
            pass


# ==================================================================== 同步选择
class _SyncDialog(tk.Toplevel):
    """同步出来的候选列表：默认全选，已存在的不选。

    为什么让用户挑：一个网关动辄上百个模型，全塞进清单等于把模型管理变成
    另一个「找不到」的地方。勾几个常用的就够了。
    """

    def __init__(self, parent, rows: list[dict], existing: set[str]):
        super().__init__(parent)
        self.withdraw()
        self.title("同步模型 — 只列文本模型")
        self.configure(bg=U.T.page)
        self.transient(parent)
        self.picked: list[dict] = []
        self._rows = list(rows)
        self._vars: list[tk.BooleanVar] = []

        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(18), pady=U.px(14))
        U.wlabel(wrap, "同步到的文本模型", "page", U.FONT_T).pack(anchor="w")
        known = sum(1 for r in self._rows if str(r.get("id")) in existing)
        U.wlabel(wrap,
                 f"共 {len(self._rows)} 个，其中 {known} 个已经在清单里（默认不勾）。"
                 "空着的 token 限制表示服务商没给，添加后可以手填。",
                 "pagedim", U.FONT_S, justify="left",
                 wraplength=U.px(620)).pack(anchor="w", pady=(U.px(4), 0))

        screen_h = self.winfo_screenheight()
        sc = _Scroll(wrap, min(U.px(360), int(screen_h * 0.42)))
        sc.pack(fill="both", expand=True, pady=(U.px(10), 0))
        box = sc.inner
        box.columnconfigure(2, weight=1)
        for i, row in enumerate(self._rows):
            mid = str(row.get("id") or "")
            var = tk.BooleanVar(value=mid not in existing)
            self._vars.append(var)
            U.Check(box, mid, var, font=U.FONT_S).grid(
                row=i, column=0, sticky="w", pady=U.px(2))
            lim = " · ".join(
                f"{lb} {aiops.fmt_tokens(row.get(_k))}"
                for _k, lb in (("context", "上下文"),
                               ("max_input", "入"),
                               ("max_output", "出")))
            U.wlabel(box, lim, "pagemute", U.FONT_XS).grid(
                row=i, column=2, sticky="w", padx=(U.px(12), 0))
            if mid in existing:
                U.Pill(box, "已存在", tone="mute", height=20,
                       font=U.FONT_XS).grid(row=i, column=3, sticky="e",
                                            padx=(U.px(8), 0))

        foot = U.wframe(wrap, "page")
        foot.pack(fill="x", pady=(U.px(10), 0))
        self.btn_ok = U.RoundButton(foot, "添加勾选的", self._ok,
                                    variant="primary", height=32, padx=18)
        self.btn_ok.pack(side="right")
        U.RoundButton(foot, "取消", self._cancel, variant="secondary",
                      height=32, padx=16).pack(side="right", padx=(0, U.px(8)))
        U.RoundButton(foot, "全选", self._all, variant="ghost",
                      height=32, padx=14).pack(side="left")
        U.RoundButton(foot, "全不选", self._none, variant="ghost",
                      height=32, padx=14).pack(side="left", padx=(U.px(6), 0))

        self.bind("<MouseWheel>", sc._wheel)

        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.geometry(f"{U.px(720)}x{U.px(520)}")
        self.update_idletasks()
        try:
            x = parent.winfo_rootx() + (parent.winfo_width()
                                        - self.winfo_width()) // 2
            y = parent.winfo_rooty() + U.px(40)
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass
        self.deiconify()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _all(self) -> None:
        for v in self._vars:
            v.set(True)

    def _none(self) -> None:
        for v in self._vars:
            v.set(False)

    def _ok(self) -> None:
        self.picked = [r for r, v in zip(self._rows, self._vars) if v.get()]
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _cancel(self) -> None:
        self.picked = []
        try:
            self.destroy()
        except tk.TclError:
            pass
