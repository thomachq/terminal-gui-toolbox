# -*- coding: utf-8 -*-
"""地址库选择框：常用网段 / 中国省级（含港澳台）/ 国家地区，支持多选。

为什么要有这个框：一个省就是几千条网段，手打不可能；少打一位数字
防火墙就把自己关在门外。这里用的是随程序打包的真实数据
（`assets/iplib/`），也能从公开源更新到 `data/iplib/`（更新后优先）。

对外只有一个入口：`IpPickDialog.open(parent, initial=...)` → 返回
换行分隔的 CIDR 文本（用户取消返回 None）。
"""

from __future__ import annotations

import threading
import tkinter as tk

import wbtui as U
from core import ipaddr, iplib
from ui import table as tbl

# 展开条数保护：一条规则塞几千个网段会把设备打挂（iptables 逐条 -A，
# 群晖那种老内核一万条要跑几十秒），所以分三档。
_WARN_AT = 200
_REFUSE_AT = 2000


class IpPickDialog(tk.Toplevel):
    """三组页签 + 勾选 + 「加入地址框」。"""

    def __init__(self, parent, initial: str = "", title: str = "地址库",
                 width: int = 820, height: int = 620, allowed=None):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.configure(bg=U.T.page)
        self.transient(parent)
        self.result: str | None = None
        # allowed = 只列这几组（后端说了算）。群晖的位置库只有国家级，
        # 把「中国省级」摆出来就是让用户白选一场 —— 选了也下发不了。
        self._groups = [g for g in iplib.GROUPS
                        if not allowed or g[0] in tuple(allowed)]
        if not self._groups:
            self._groups = list(iplib.GROUPS)
        self._group = self._groups[0][0]
        self._picked: dict[str, iplib.Item] = {}
        self._items: list[iplib.Item] = []
        self._busy = False
        self._width = width
        self._height = height

        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(18), pady=U.px(16))

        head = U.wframe(wrap, "page")
        head.pack(fill="x")
        U.wlabel(head, title, "page", U.FONT_T).pack(side="left")
        self.lb_src = U.wlabel(head, "", "pagedim", U.FONT_XS)
        self.lb_src.pack(side="left", padx=(U.px(10), 0))
        U.RoundButton(head, "取消", self._cancel, variant="secondary",
                      height=30, padx=16).pack(side="right")
        U.RoundButton(head, "加入", self._ok, variant="primary",
                      height=30, padx=20).pack(side="right", padx=(0, U.px(8)))

        note = ("勾选的条目会展开成具体网段。IPv4 / IPv6 都有；省级含"
                "中国香港、中国澳门、中国台湾。")
        if allowed and "province" not in tuple(allowed):
            note = ("当前后端的「位置」只有国家/地区级数据（没有省级），"
                    "所以这里只列国家/地区与常用网段 —— 选省级也下发不了。")
        U.wlabel(
            wrap, note,
            "pagedim", U.FONT_XS, justify="left",
            wraplength=U.px(width - 60)).pack(anchor="w", pady=(U.px(4), 0))

        card = U.Card(wrap)
        card.pack(fill="both", expand=True, pady=(U.px(10), 0))

        bar = U.wframe(card.body, "card")
        bar.pack(fill="x", pady=(0, U.px(8)))
        self.seg = tbl.Segmented(bar, [(k, lab) for k, lab, _fn in self._groups],
                                 command=self._switch)
        self.seg.pack(side="left")
        self.btn_update = U.RoundButton(bar, "更新地址库", self._update,
                                        variant="ghost", height=28, padx=14)
        self.btn_update.pack(side="right")
        U.RoundButton(bar, "清空", self._clear, variant="ghost",
                      height=28, padx=12).pack(side="right", padx=(0, U.px(6)))
        U.RoundButton(bar, "全选本组", self._pick_all, variant="ghost",
                      height=28, padx=12).pack(side="right", padx=(0, U.px(6)))

        self.tb = tbl.DataTable(
            card.body,
            [tbl.Column("pick", "", 34, anchor="center"),
             tbl.Column("name", "名称", 200),
             tbl.Column("note", "说明", 150),
             tbl.Column("entry", "条目", 150, mono=True),
             tbl.Column("count", "网段数", 80, anchor="e")],
            height=10, search_hint="搜省份 / 国家 / 网段…",
            empty_title="这一组还没有数据",
            empty_hint="点右上「更新地址库」从公开源拉一次（约 3 分钟）。")
        self.tb.pack(fill="both", expand=True)
        self.tb.tree.bind("<Button-1>", self._click, add="+")

        foot = U.wframe(wrap, "page")
        foot.pack(fill="x", pady=(U.px(10), 0))
        self.lb_sum = U.wlabel(foot, "", "pagedim", U.FONT_S)
        self.lb_sum.pack(side="left")
        self.lb_err = U.wlabel(foot, "", "pageerr", U.FONT_XS)
        self.lb_err.pack(side="left", padx=(U.px(12), 0))

        # 手打的网段也允许原样保留：用户可能只要一条 10.0.0.0/8，
        # 让他先在地址框里写好再进来加几条，不能把原来的抹掉。
        keep = [c for c in ipaddr.split(initial or "")[0] if c and c != "any"]
        self._initial = keep
        if keep:
            self.lb_err.configure(text=f"地址框里已有 {len(keep)} 条，"
                                       "选中会追加在后面")

        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self._switch(self._group, fire=False)
        self.update_idletasks()
        self.geometry(f"{U.px(width)}x{U.px(height)}")
        self.minsize(U.px(620), U.px(420))
        self._center(parent)
        self.deiconify()
        self.grab_set()

    # ------------------------------------------------------------ 列表
    def _switch(self, key: str, fire: bool = True) -> None:
        self._group = key
        self._items = iplib.search(key)
        self._render()

    def _render(self) -> None:
        rows = []
        for it in self._items:
            got = it.key in self._picked
            # 「条目」列看的是**样例**：只写「4186 个网段」跟右边那列重复，
            # 人根本看不出这一组是什么地址段。给第一条 + 省略号更有信息量。
            entry = "（未安装）"
            if it.cidrs:
                entry = it.cidrs[0] + ("  …" if it.count > 1 else "")
            rows.append({
                "pick": "✓" if got else "",
                "name": it.name,
                "note": it.note,
                "entry": entry,
                "count": str(it.count) if it.cidrs else "—",
                "_key": it.key,
                "_item": it,
            })
        self.tb.set_rows(rows)
        st = iplib.installed()
        if self._group == "common":
            self.lb_src.configure(text=f"内置 {st['common']} 条常用网段")
        elif self._group == "province":
            self.lb_src.configure(
                text=f"已装 {st['province']}/{st['province_total']} 个省级")
        else:
            self.lb_src.configure(
                text=f"已装 {st['country']}/{st['country_total']} 个国家地区")
        self._paint_sum()

    def _paint_sum(self) -> None:
        n = len(self._picked)
        if not n:
            self.lb_sum.configure(text="还没选。点左边那一列勾选，可以多选。")
            return
        cidrs = iplib.expand_items(self._picked.values())
        v4 = sum(1 for c in cidrs if ipaddr.family(c) == 4)
        v6 = len(cidrs) - v4
        self.lb_sum.configure(
            text=f"已选 {n} 项 → 展开 {len(cidrs)} 条网段"
                 f"（IPv4 {v4} / IPv6 {v6}）")

    def _click(self, event):
        col = self.tb.column_at(event.x)
        if col != "pick":
            return None
        row = self.tb.row_at(event.y)
        if not row or not row.get("_key"):
            return None
        key = str(row["_key"])
        it = row.get("_item")
        if key in self._picked:
            self._picked.pop(key, None)
        else:
            if it is not None and not it.cidrs:
                self.lb_err.configure(
                    text=f"{it.name} 没有数据，先点「更新地址库」")
                return "break"
            if it is not None:
                self._picked[key] = it
        self.lb_err.configure(text="")
        self._render()
        return "break"

    def _pick_all(self) -> None:
        """全选当前这一组（只选有数据的）。"""
        for it in self._items:
            if it.cidrs:
                self._picked[it.key] = it
        self._render()

    def _clear(self) -> None:
        self._picked.clear()
        self.lb_err.configure(text="")
        self._render()

    # ------------------------------------------------------------ 更新
    def _update(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_update.set_enabled(False)
        self.btn_update.set_text("更新中…")
        self.lb_err.configure(text="正在从公开源拉取，约 3 分钟，请勿关闭本窗口")

        def work():
            try:
                res = iplib.update(province=True, country=True)
            except Exception as e:  # noqa: BLE001
                res = {"error": f"{type(e).__name__}: {e}"}
            try:
                self.after(0, lambda: self._done_update(res))
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _done_update(self, res: dict) -> None:
        self._busy = False
        if not self.winfo_exists():
            return
        self.btn_update.set_enabled(True)
        self.btn_update.set_text("更新地址库")
        if res.get("error"):
            self.lb_err.configure(text=f"更新失败：{res['error']}")
        else:
            self.lb_err.configure(
                text=f"已更新：省级 {res.get('province', 0)} 个 / "
                     f"国家 {res.get('country', 0)} 个，共 "
                     f"{res.get('cidrs', 0):,} 条网段")
        self._switch(self._group, fire=False)

    # ------------------------------------------------------------ 收尾
    def _ok(self) -> None:
        if not self._picked:
            self.lb_err.configure(text="先勾选至少一项。")
            return
        cidrs = iplib.expand_items(self._picked.values())
        if not cidrs:
            self.lb_err.configure(text="选中的条目没有网段数据。")
            return
        if len(cidrs) > _REFUSE_AT:
            self.lb_err.configure(
                text=f"展开后有 {len(cidrs)} 条，太多了（上限 {_REFUSE_AT}）。"
                     "少选几项，或者分几条规则下发。")
            return
        if len(cidrs) > _WARN_AT:
            from tkinter import messagebox
            if not messagebox.askokcancel(
                    "确认要下发这么多？",
                    f"展开后有 {len(cidrs)} 条网段，会生成同样多的规则命令。\n\n"
                    "设备上逐条执行会比较慢，建议按用途拆分。继续吗？",
                    parent=self):
                return
        out = list(self._initial)
        for c in cidrs:
            if c not in out:
                out.append(c)
        self.result = "\n".join(out)
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def _center(self, parent) -> None:
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
            y = parent.winfo_rooty() + max(U.px(20),
                                           (parent.winfo_height() - self.winfo_height()) // 6)
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 入口
    @classmethod
    def open(cls, parent, initial: str = "", title: str = "地址库",
             allowed=None) -> str | None:
        dlg = cls(parent, initial=initial, title=title, allowed=allowed)
        try:
            parent.wait_window(dlg)
        except tk.TclError:
            pass
        return dlg.result


__all__ = ["IpPickDialog"]
