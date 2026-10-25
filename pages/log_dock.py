# -*- coding: utf-8 -*-
"""底部日志面板（IDE 那种底边栏）。

形态参考 IDE 的底部工具窗：一条可拖高度的面板，不属于任何页签，切到终端、
切到设置都还在。开关放在状态栏最右那个「日志」按钮上 —— 日志是「随时想看
一眼」的东西，塞进左侧导航就得先切页签，切完还得切回来。

内容不另写一套：直接把 `pages/log_page.py::build_log` 建在这里，
所以筛选、搜索、分页、导出、详情、口令打码全都跟原来一致。
"""
from __future__ import annotations

import tkinter as tk

import wbtui as U

GRIP_H = 5          # 顶部抓手的高度（逻辑像素）
MIN_H = 200         # 拖到最矮也要能看见表头 + 三五行（原来 140，只剩个表头）
DEFAULT_H = 280     # 默认高度：能看五六条 + 筛选条 + 分页（原来实际只有 160 太扁）
USABLE_H = 240      # 低于这个数等于「没设置过」，按 DEFAULT_H 起
RATIO = 0.72        # 最高不超过窗口高度的 72%，否则主界面就没地方了
# 默认高度还要跟窗口高度挂钩。定死 280 在 1747×1136（逻辑 1165×757）的窗口上
# 要吃掉 37%：左侧导航被挤到只剩 5 项（「运维工具」以下全都点不到），主机表格
# 只剩 2 行。用户 2026-10-25 报的「界面显示不完整」里就有这一条。
# 用户自己拖过的高度不受影响，只有「没设置过 / 老存档」才按比例算。
AUTO_RATIO = 0.30


class LogDock:
    """混进 ToolboxApp 用。只负责「面板本体 + 开关 + 拖动 + 记忆」。"""

    # ================================================================ 初始化
    def log_dock_setup(self) -> None:
        """建一个空的宿主，先不 pack —— 首次打开时才真正构建内容。"""
        self._log_dock_on = False
        self._log_dock_built = False
        self._log_drag: tuple[int, int] | None = None
        pref = getattr(self, "_win_pref", {}) or {}
        # 上次存的是**物理像素**，除以缩放比才是逻辑像素。
        # 老版本默认只有 240（缩放 1.5 的机器上实际显示 ≈ 160 逻辑像素），
        # 面板里只剩一行表头和分页 —— 用户的原话是「显示都不完整」。
        # 所以低于 USABLE_H 的一律当「没设置过」，直接用新的默认高度；
        # 用户自己拖过、且拖到可用高度的，尊重他存的数。
        try:
            saved = int(pref.get("log_h") or 0) / max(1.0, float(U.S))
        except (TypeError, ValueError):
            saved = 0
        if saved < USABLE_H or saved > 2000:
            saved = DEFAULT_H
            self._log_dock_auto = True      # 这个高度是「我们定的」，可以按窗口收
        else:
            self._log_dock_auto = False     # 用户自己拖的，不许动
        self._log_dock_h = U.px(max(MIN_H, int(saved)))
        self.log_dock_host = tk.Frame(self.shell, bg=U.T.page, bd=0)
        self._log_want_open = bool(pref.get("log_open"))

    # ================================================================ 开关
    def log_dock_toggle(self, force=None) -> None:
        want = (not self._log_dock_on) if force is None else bool(force)
        if want == self._log_dock_on:
            return
        if want:
            self._log_dock_build()
            try:
                # before=paned：先占住底部这条，主区再拿剩下的。
                # 顺序反了会被 paned 的 expand 抢光，面板高度变 0。
                self.log_dock_host.pack(side="bottom", fill="x",
                                        before=self.paned,
                                        pady=(0, U.px(6)))
            except tk.TclError:
                return
            self._log_dock_on = True
            if not getattr(self, "_audit_ready", False):
                self._audit_ready = True
                self.after(60, self.audit_reload)
            else:
                self.after(60, self.audit_reload)
        else:
            try:
                self.log_dock_host.pack_forget()
            except tk.TclError:
                pass
            self._log_dock_on = False
        try:
            self.status.set_log_on(self._log_dock_on)
        except AttributeError:
            pass
        self._win_state_save()

    def log_dock_open(self) -> None:
        self.log_dock_toggle(True)

    # ================================================================ 构建
    def _log_dock_build(self) -> None:
        if getattr(self, "_log_dock_built", False):
            return
        self._log_dock_built = True
        host = self.log_dock_host
        host.pack_propagate(False)
        h = self._log_dock_h
        # 窗口本身很矮时别把主界面挤没 —— 按 RATIO 收一收（winfo_height 在
        # 未映射时是 1，那会儿不夹，等下次打开自然就对了）
        try:
            win_h = int(self.winfo_height())
            if win_h > U.px(MIN_H) * 2:
                h = min(h, max(U.px(MIN_H), int(win_h * RATIO)))
                # 用默认值的（不是用户拖的）再按窗口高三成收一次：
                # 高度越大的窗口越不该让底栏按死数占地方。
                if getattr(self, "_log_dock_auto", False):
                    h = max(U.px(MIN_H), min(h, int(win_h * AUTO_RATIO)))
        except tk.TclError:
            pass
        self._log_dock_h = h
        host.configure(height=h)

        # 抓手的光标用「手」而不是上下双箭头：这条只有 5px 高，鼠标扫过
        # 日志面板顶部就会闪一下 ↕，看着像界面在提示什么却不知道提示什么。
        # 手型表达的是「这里能抓住拖」，语义一样但不刺眼。
        grip = tk.Frame(host, bg=U.T.border, height=U.px(GRIP_H),
                        bd=0, cursor="hand2")
        grip.pack(fill="x")
        U.register(grip, lambda: grip.configure(bg=U.T.border))
        grip.bind("<Button-1>", self._log_drag_start)
        grip.bind("<B1-Motion>", self._log_drag_move)
        grip.bind("<ButtonRelease-1>", self._log_drag_done)

        head = U.wframe(host, "page")
        head.pack(fill="x", padx=U.px(10))
        lb = tk.Label(head, text="日志", bg=U.T.page, fg=U.T.text,
                      font=U.FONT_B, bd=0)
        lb.pack(side="left", pady=(U.px(2), U.px(2)))
        U.register(lb, lambda: lb.configure(bg=U.T.page, fg=U.T.text))
        hint = tk.Label(head, text="命令与输出、审批、连接、增删主机都记在这里；"
                                   "口令与私钥已打码",
                        bg=U.T.page, fg=U.T.text_mute, font=U.FONT_XS, bd=0)
        hint.pack(side="left", padx=(U.px(8), 0))
        U.register(hint, lambda: hint.configure(bg=U.T.page, fg=U.T.text_mute))

        btn = U.RoundButton(head, "收起", lambda: self.log_dock_toggle(False),
                            variant="ghost", font=U.FONT_XS, height=24, padx=10)
        btn.pack(side="right")

        body = U.wframe(host, "page")
        body.pack(fill="both", expand=True, padx=U.px(10),
                  pady=(0, U.px(6)))
        self.log_dock_body = body
        self.build_log(body)

    # ================================================================ 拖动
    def _log_drag_start(self, event) -> None:
        try:
            self._log_drag = (event.y_root, self.log_dock_host.winfo_height())
        except tk.TclError:
            self._log_drag = None

    def _log_drag_move(self, event) -> None:
        if not self._log_drag:
            return
        y0, h0 = self._log_drag
        want = h0 - (event.y_root - y0)
        top = max(U.px(MIN_H), int(self.winfo_height() * RATIO))
        want = max(U.px(MIN_H), min(want, top))
        try:
            self.log_dock_host.configure(height=want)
        except tk.TclError:
            pass

    def _log_drag_done(self, _e=None) -> None:
        self._log_drag = None
        try:
            self._log_dock_h = self.log_dock_host.winfo_height()
        except tk.TclError:
            return
        self._win_state_save()
