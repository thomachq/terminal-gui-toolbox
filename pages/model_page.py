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

# 列宽加起来要能塞进 1080 的对话框，否则最右的「状态」被挤出去 ——
# 那恰是最该看的一列（缺 Key / 缺 Harness / 就绪）。
MODEL_COLS = [
    Column("current", "", 52),
    Column("name", "名称", 116, stretch=True),
    Column("provider", "服务商", 92),
    Column("model", "模型名", 136, stretch=True),
    Column("base", "接口地址", 146, stretch=True),
    Column("ctx", "上下文", 64, anchor="e"),
    Column("max_in", "最大输入", 64, anchor="e"),
    Column("max_out", "最大输出", 64, anchor="e"),
    Column("caps", "能力", 104),
    Column("key", "Key", 48),
    Column("state", "状态", 74),
]

# Token 限制的预设胶囊。1024 进制 —— 主流模型窗口基本都是 2 的幂
# （131072 / 262144 / 1048576）。点一下只是把值填进输入框，手填的值优先。
_CTX_PRESETS = [128, 200, 256, 400, 512, 1024]
_IN_PRESETS = [128, 200, 256, 512, 1024]
_OUT_PRESETS = [16, 32, 64, 128, 256]


def _preset_label(k: int) -> str:
    return "1M" if k >= 1024 else f"{k}K"


def _preset_value(k: int) -> int:
    return k * 1024


class ModelDialog(tk.Toplevel):
    """模型清单。非模态 —— 开着也能回主界面看助手现在到底用哪条。"""

    def __init__(self, app, parent=None):
        tk.Toplevel.__init__(self, app)
        self.app = app
        self.cfg = app.ai_cfg
        self.withdraw()
        self.title("模型管理 — 智能运维助手")
        self.configure(bg=U.T.page)
        self.geometry(f"{U.px(1080)}x{U.px(580)}+{U.px(150)}+{U.px(110)}")
        self.minsize(U.px(820), U.px(440))
        self.transient(app)

        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(18), pady=U.px(16))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(2, weight=1)

        # ---- 标题
        head = U.wframe(wrap, "page")
        head.grid(row=0, column=0, sticky="we")
        U.wlabel(head, "模型管理", "page", U.FONT_T).pack(side="left")
        U.wlabel(head, "只收文本模型，一条配置一个模型，随时切换当前", "pagedim",
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
        U.RoundButton(bar, "同步模型", self.model_sync, variant="ghost",
                      font=U.FONT_S, height=30, padx=10).pack(
            side="left", padx=(U.px(6), 0))
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
        unknown = sum(1 for m in cfg.models()
                      if not (m.get("context") or m.get("ctx")))
        self.lbl_sum.configure(
            text=f"共 {len(rows)} 个模型 · 当前：{cur.get('name') or '未命名'}")
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

    # ================================================================ 同步
    def model_sync(self) -> None:
        """从服务商的 /models 拉一份列表，只留文本模型。

        顺手抓上下文/最大输入/最大输出 —— 抓不到就是「未知」，不猜。
        """
        rec = self._pick() or self.cfg.current()
        prov = str(rec.get("provider") or self.cfg.provider)
        preset = aiops.PROVIDERS.get(prov) or aiops.PROVIDERS[aiops.DEFAULT_PROVIDER]
        if preset.get("kind") == "dsh":
            self.app.toast("Harness 线路没有模型列表；换一条 OpenAI 兼容线路再同步", "warn")
            return
        base = str(rec.get("base") or preset.get("base") or "")
        if not base:
            self.app.toast("这条模型还没填接口地址，先编辑填上", "warn")
            return
        key = self.cfg.model_key(str(rec.get("id") or "")) or self.cfg.get_key()
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
        self.model_reload()
        self.app.toast(f"同步完成，新增 {added} 个文本模型", "ok")
        try:
            self.lbl_hint.configure(
                text=f"✓ 从 {base} 拉到 {len(rows)} 个文本模型，已新增 {added} 个")
        except tk.TclError:
            pass

    # ================================================================ 表单
    def _form(self, rec: dict | None) -> dict | None:
        """新增 / 编辑共用一张表单。rec 为空就是新增。"""
        dlg = _ModelForm(self, rec, self.cfg)
        try:
            self.wait_window(dlg)
        except tk.TclError:
            return None
        return dlg.result


# ==================================================================== 表单窗
class _Scroll(tk.Frame):
    """表单内容比屏幕高，必须能滚 —— 150% 缩放下这些字段一屏放不完。"""

    def __init__(self, master, height: int):
        super().__init__(master, bg=U.T.page, bd=0)
        self.cv = tk.Canvas(self, bg=U.T.page, bd=0, highlightthickness=0,
                            height=max(U.px(200), int(height)),
                            yscrollincrement=U.px(18))
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.cv.yview,
                                style="Card.Vertical.TScrollbar")
        self.cv.configure(yscrollcommand=self.sb.set)
        self.sb.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)
        self.inner = U.wframe(self.cv, "page")
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


class _ModelForm(tk.Toplevel):
    """文本模型表单。

    比 `forms.FormDialog` 复杂的地方（所以没复用它）：
      · 勾「思考」才展开档位区，档位是多选 + 一个默认档
      · token 三件套要「预设胶囊 + 手填」，胶囊点了往输入框里写值
      · 字段多到一屏放不下，要能滚
    """

    def __init__(self, parent, rec: dict | None, cfg):
        super().__init__(parent)
        self.withdraw()
        self.rec = dict(rec or {})
        self.cfg = cfg
        self.result: dict | None = None
        title = "编辑模型" if rec else "新增模型"
        self.title(title + " — 智能运维助手")
        self.configure(bg=U.T.page)
        self.transient(parent)
        self.resizable(False, True)

        prov = str(self.rec.get("provider") or aiops.DEFAULT_PROVIDER)
        if prov not in aiops.PROVIDERS:
            prov = aiops.DEFAULT_PROVIDER
        self._prov = prov

        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(20), pady=U.px(16))
        U.wlabel(wrap, title, "page", U.FONT_T).pack(anchor="w")
        U.wlabel(wrap,
                 "只收文本模型。Token 限制抓不到就留空，界面会显示「未知」，"
                 "不会替你猜一个数。", "pagedim", U.FONT_S, justify="left",
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
        r = self._row_provider(body, r)
        r = self._row_text(body, r, "base", "接口地址",
                           self.rec.get("base") or "",
                           "例如 https://api.deepseek.com（会自动补 /v1/chat/completions）")
        r = self._row_model(body, r)
        r = self._row_text(body, r, "key", "API Key", "",
                           "已存，留空表示不修改" if self.rec.get("key_enc")
                           else "本地 Harness / Ollama 一般不需要", password=True)
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

    def _row_provider(self, body, r: int) -> int:
        self._label(body, "服务商", r)
        cell = self._cell(body, r)
        self._prov_keys = list(aiops.PROVIDERS.keys())
        labels = [aiops.PROVIDERS[k]["label"] for k in self._prov_keys]
        var = tk.StringVar()
        cb = ttk.Combobox(cell, textvariable=var, values=labels, state="readonly",
                          width=32, font=U.FONT_S)
        cb.pack(side="left")
        try:
            cb.current(self._prov_keys.index(self._prov))
        except ValueError:
            cb.current(0)
        self.var_prov = var
        self.cmb_provider = cb
        self._hint(cell, "选「自定义」可以填任何 OpenAI 兼容接口")
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
        """表单里的「同步…」：按当前填的 base + key 拉一次，把模型名填进来。"""
        base = self.ent_base.get().strip()
        if not base:
            self._fail("先填接口地址再同步")
            return
        key = self.ent_key.get().strip() or self.cfg.model_key(
            str(self.rec.get("id") or "")) or self.cfg.get_key()
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

    def _pick_provider(self) -> str:
        label = self.var_prov.get()
        for k in self._prov_keys:
            if aiops.PROVIDERS[k]["label"] == label:
                return k
        return self._prov

    def _ok(self) -> None:
        prov = self._pick_provider()
        preset = aiops.PROVIDERS[prov]
        name = self.ent_name.get().strip()
        base = self.ent_base.get().strip() or preset["base"]
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
            "base": base,
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
        key = self.ent_key.get().strip()
        if key:
            out["key_enc"] = self.cfg.seal_key(key)
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
