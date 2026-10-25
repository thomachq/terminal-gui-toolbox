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
MIN_H = 200         # 拖到最矮也要能看见表头 + 一两条
# 默认高度按「看得见几条」倒推，不按窗口比例拍。日志表的行高是 26 逻辑像素
# （ROW_H_DENSE：39 物理），表头 51 物理。实测（面板逻辑像素 → 完整/半截）：
#   250 → 2/1、251 → 3/0、252 → 3/0、253 → 3/1、255 → 3/1
# 用户 2026-10-25 的要求是「能展示 3 条就可以」，取 252：正好表头 + 整 3 行，
# 第 4 条一点都不露（露半行看着像表格被裁坏了）。掉到 250 会变成 2 条，
# 所以这里没有余量可加 —— 改行高时这条要重测（_logdocktest 会红）。
DEFAULT_H = 252
USABLE_H = 210      # 低于这个数等于「没设置过」，按 DEFAULT_H 起
RATIO = 0.72        # 拖动的上限：不超过窗口高度的 72%
# 主区（页签内容）的最低高度：日志再怎么拖，也不许把它压没。
# 用户 2026-10-25 报「升起日志后输入框被覆盖」，一半是终端里 pack 顺序，
# 另一半是这里 —— 日志能拖到窗口的 72%，1080 高的屏幕上主区只剩 36 逻辑像素，
# 连终端头部都塞不进去，这时候输入行摆哪儿都看不见。
# （原来还有一个 FALLBACK_RATIO=0.45 的独立兜底，现在并进 `_log_dock_top()`。）
MAIN_MIN_H = 200


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
        # 「这个高度是不是程序定的」靠存档里的标记判断，不靠数值猜：
        #   · 老存档没这个字段（v1.0.21 及以前）→ None，一律按默认值重置。
        #     用户机器上那份 320 就是这么来的，不重置的话改默认值等于白改。
        #   · 升级后写回 True → 下次仍然跟着默认值走。
        #   · 用户手动拖过 → 拖动结束时写 False，之后永远尊重。
        # 顺带兜住明显不合理的值（太小 / 超过 2000 逻辑像素）。
        auto = pref.get("log_h_auto")
        if auto is not False or saved < USABLE_H or saved > 2000:
            saved = DEFAULT_H
            self._log_dock_auto = True      # 这个高度是「我们定的」，可以按窗口收
        else:
            self._log_dock_auto = False     # 用户自己拖出来的，不许动
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
        # 窗口本身很矮时别把主界面挤没 —— 这里只做兜底，不做常态缩放。
        # 以前这里是「默认值再按窗口高三成收」，结果 1080 高的窗口把 270
        # 压成 216 逻辑（只露 1 条），越是大窗口越看不清日志，方向反了。
        # （winfo_height 在未映射时是 1，那会儿不夹，等下次打开自然就对了）
        top = self._log_dock_top()
        if top:
            h = min(h, top)
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

    def _log_dock_top(self) -> int:
        """日志面板最高能到多少（物理像素）。0 表示窗口尺寸还没出来，不夹。

        两条约束取小：
          · 不超过窗口高的 RATIO；
          · 主区至少留 MAIN_MIN_H。
        `shell` 是 paned 与日志宿主**共同的父**，所以 shell 的高度就是
        「主区 + 日志」能用的全部高度，减去 MAIN_MIN_H 就是日志的上限。
        不能写成 `win - shell_h - log_h` —— 那样等于把日志高度抠了两次，
        fixed 会被算成 0，上限退化成 RATIO，日志照样能把主区压到 36 像素。
        """
        try:
            win = int(self.winfo_height())
            if win <= U.px(MIN_H) * 2:
                return 0                    # 窗口还太小 / 没映射，先别夹
            shell_h = int(self.shell.winfo_height())
            if shell_h <= U.px(MAIN_MIN_H) * 1.5:
                return 0                    # shell 还没铺开，夹了反而算出个极小值
            top = min(int(win * RATIO), shell_h - U.px(MAIN_MIN_H))
            return max(U.px(MIN_H), top)
        except (tk.TclError, AttributeError):
            return 0

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
        top = self._log_dock_top() or max(
            U.px(MIN_H), int(self.winfo_height() * RATIO))
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
        # 用户亲手拖过了 —— 从此这个数就是他自己的，改默认值也不许覆盖
        self._log_dock_auto = False
        self._win_state_save()
