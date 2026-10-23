# -*- coding: utf-8 -*-
"""设置中心 —— 一个入口管掉外观、工作区、助手、安全、备份。

为什么把「锁」和「主题」从顶栏收进来：

  顶栏那一排按钮原本是「锁 / 主题 / 终端」，三个都在抢右上角那一小块，
  而且都不是高频操作 —— 真正高频的是终端和助手。所以顶栏只留两个动作
  按钮，其余全部归到「设置」里，跟宝塔那种「左侧分类 + 右侧内容」的
  设置页对齐。

一条自己给自己定的规矩：**只做真能生效的开关**。
系统休眠锁屏、切换用户锁屏这类需要挂 Windows 会话事件的功能，Tk 层面没有
可靠钩子，与其摆一个永远为「关」的假开关，不如在页面上写清楚没做。
"""

from __future__ import annotations

import os
import shutil
import sys
import tkinter as tk
import zipfile
from pathlib import Path

import wbtui as U
from ui import forms
from ui.table import Segmented

# 主题配色：只换「品牌色」这一族，其余底色沿用 LIGHT / DARK 的既有关系，
# 这样 10 套配色 × 明暗两套底 = 20 种组合，不会出现某套配色在深色下字看不见。
ACCENTS: list[tuple[str, str]] = [
    ("经典蓝", "#3A66AC"),
    ("宝塔绿", "#2E9E5B"),
    ("翡翠绿", "#12A594"),
    ("紫罗兰", "#7C5CD6"),
    ("琥珀橙", "#C98315"),
    ("玫瑰红", "#D93B34"),
    ("石板灰", "#5B7093"),
    ("海洋蓝", "#1E7FB8"),
    ("森林绿", "#2F7D32"),
    ("兰花紫", "#B053A8"),
]
DEFAULT_ACCENT = "经典蓝"

THEMES = [("light", "浅色"), ("dark", "深色"), ("system", "跟随系统")]

SECTIONS = [("look", "外观"), ("work", "工作区"), ("agent", "智能运维助手"),
            ("safe", "安全"), ("backup", "备份"), ("about", "关于")]


# ------------------------------------------------------------------ 颜色工具
def _rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(c))) for c in rgb)


def mix(a: str, b: str, t: float) -> str:
    """a 与 b 按 t 混合（t=0 得 a，t=1 得 b）。"""
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return _hex((ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t))


def palette_for(base, accent_hex: str):
    """在一套明/暗底上换品牌色，得到新的 Palette。"""
    dark = base.name == "dark"
    deep_to = "#FFFFFF" if dark else "#000000"
    soft_t = 0.78 if dark else 0.88
    soft2_t = 0.66 if dark else 0.80
    fields = {k: v for k, v in base.__dict__.items() if k != "name"}
    return U.Palette(
        base.name,
        **{**fields,
           "brand": accent_hex,
           "brand_deep": mix(accent_hex, deep_to, 0.45),
           "brand_hover": mix(accent_hex, "#FFFFFF", 0.15),
           "brand_soft": mix(accent_hex, base.page, soft_t),
           "brand_soft2": mix(accent_hex, base.page, soft2_t)},
    )


def system_is_dark() -> bool:
    """Windows 上读注册表判断系统是深色还是浅色；其它平台先按浅色。"""
    if not sys.platform.startswith("win"):
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return int(val) == 0
    except OSError:
        return False


class SettingsCenter:
    """设置中心（混入主窗口）。"""

    #: 设置页签的键名。跟终端（app.TERM_TAB）一样，一个页签一个键。
    SETTINGS_TAB = "settings"

    # ================================================================ 入口
    def _settings_reg(self) -> dict:
        """设置页签的登记表。和终端一样懒建，不占 app.__init__ 的顺序。"""
        reg = getattr(self, "_settings_panes", None)
        if reg is None:
            reg = {}
            self._settings_panes = reg
        return reg

    def open_settings(self, section: str | None = None) -> None:
        """打开设置。默认嵌成页签 —— 要并排看，点页签上的 ⧉ 拉成独立窗口。

        三种形态按优先级找一遍：已在页签里 → 切过去；已拉成独立窗口 → 抬起来；
        都没有 → 新开一个页签。反过来（页签被关掉、窗口被关掉）也在各自回调里清理，
        所以这里只处理「还活着」的情况。

        `section=None` 表示「别动它现在停在哪一页」：从顶栏再点一次「设置」，
        不该把用户正在看的那一页弹回外观页。
        """
        reg = self._settings_reg()
        pane = reg.get(self.SETTINGS_TAB)
        if pane is not None:
            try:
                if pane.winfo_exists():
                    self.tabbar.select(self.SETTINGS_TAB)
                    if section:
                        pane.show(section)
                    return
            except tk.TclError:
                pass
            reg.pop(self.SETTINGS_TAB, None)
        win = getattr(self, "_settings_win", None)
        if win is not None:
            try:
                if win.winfo_exists():
                    win.focus_set()
                    if section:
                        win.show(section)
                    return
            except tk.TclError:
                pass
            self._settings_win = None
        self._settings_add_tab(section or SECTIONS[0][0])

    def _settings_add_tab(self, section: str = "look"):
        """在工作区里新建一个设置页签，返回它的 SettingsPane。"""
        holder = U.wframe(self.workspace, "page")
        holder.grid(row=0, column=0, sticky="nsew")
        pane = SettingsPane(holder, self, section=section)
        pane.pack(fill="both", expand=True)
        self._settings_reg()[self.SETTINGS_TAB] = pane
        self.tabbar.add(self.SETTINGS_TAB, "设置", closable=True,
                        on_popout=self.settings_popout)
        self.tabbar.select(self.SETTINGS_TAB)
        return pane

    def settings_popout(self) -> None:
        """设置页签 → 独立窗口。停在哪一页就跟到哪一页，收回来也一样。"""
        reg = self._settings_reg()
        pane = reg.pop(self.SETTINGS_TAB, None)
        section = SECTIONS[0][0]
        if pane is not None:
            try:
                section = pane.section
            except AttributeError:
                pass
            holder = pane.master
            try:
                pane.destroy()
                holder.destroy()
            except tk.TclError:
                pass
        self.tabbar.remove(self.SETTINGS_TAB)
        win = SettingsWindow(self, section=section)
        self._settings_win = win
        win.lift()
        win.focus_force()
        self.status.set_state(
            "设置已拉出为独立窗口 · 想收回点窗口里的「嵌回主界面」", "ok")

    def settings_embed(self, w=None):
        """独立窗口 → 页签。"""
        w = w if w is not None else getattr(self, "_settings_win", None)
        section = SECTIONS[0][0]
        if w is not None:
            try:
                section = w.section
            except AttributeError:
                pass
        if getattr(self, "_settings_win", None) is w:
            self._settings_win = None
        if w is not None:
            try:
                w.destroy()
            except tk.TclError:
                pass
        if self.SETTINGS_TAB in self.tabbar.tabs:
            self.tabbar.remove(self.SETTINGS_TAB)
        pane = self._settings_add_tab(section)
        self.status.set_state("设置已嵌回主界面", "ok")
        return pane

    def settings_closed(self, w) -> None:
        """独立窗口被关掉（点 × 或调 close）。"""
        if getattr(self, "_settings_win", None) is w:
            self._settings_win = None

    def _settings_sync(self) -> None:
        """锁状态变了之后回头刷新设置页上的那行字（没开着就什么也不做）。

        页签和独立窗口都可能开着，两边都刷一遍 —— 但同一个时刻只会有一边。
        """
        for w in (self._settings_reg().get(self.SETTINGS_TAB),
                  getattr(self, "_settings_win", None)):
            if w is None:
                continue
            try:
                if w.winfo_exists():
                    w.show(w.section)
            except tk.TclError:
                continue

    # ================================================================ 外观
    def _set_theme(self, key: str) -> None:
        self.settings.data["theme"] = key
        self.settings.save()
        self._apply_theme_pref()

    def _set_accent(self, name: str) -> None:
        self.settings.data["accent"] = name
        self.settings.save()
        self._apply_theme_pref()

    def _apply_theme_pref(self) -> None:
        """按「主题偏好 + 配色」重算调色板。system 每次切换时重新读一次系统设置。"""
        pref = str(self.settings.data.get("theme")
                   or ("dark" if U.T.name == "dark" else "light"))
        if pref == "system":
            dark = system_is_dark()
        else:
            dark = pref == "dark"
        base = U.DARK if dark else U.LIGHT
        accent = self._accent_hex()
        U.apply_theme(palette_for(base, accent) if accent
                      else (U.DARK if dark else U.LIGHT))

    def _accent_hex(self) -> str:
        want = str(self.settings.data.get("accent") or DEFAULT_ACCENT)
        for name, hex_ in ACCENTS:
            if name == want:
                return hex_
        return ""

    def _restore_look(self) -> None:
        self.settings.data["theme"] = "dark" if U.T.name == "dark" else "light"
        self.settings.data["accent"] = DEFAULT_ACCENT
        self.settings.save()
        self._apply_theme_pref()
        self._settings_sync()
        self.toast("已恢复默认外观", "ok")

    # ================================================================ 数据目录
    def settings_data_dir(self) -> Path:
        return Path(self.settings.path).parent

    def settings_backup(self) -> None:
        """把 data/ 打成一个 zip。凭据是密文，换机器要重新输入 API Key。"""
        src = self.settings_data_dir()
        name = f"TerminalToolbox-backup-{_stamp()}.zip"
        from tkinter import filedialog
        out = filedialog.asksaveasfilename(
            parent=self, title="导出备份", defaultextension=".zip",
            initialfile=name, filetypes=[("Zip", "*.zip")])
        if not out:
            return
        try:
            n = 0
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                for p in sorted(src.rglob("*")):
                    if p.is_file() and not p.name.endswith((".tmp", ".lock")):
                        z.write(p, p.relative_to(src))
                        n += 1
        except OSError as e:
            self.toast(f"导出失败：{e}", "err")
            return
        self.toast(f"已导出 {n} 个文件", "ok")

    def settings_open_data(self) -> None:
        p = str(self.settings_data_dir())
        try:
            if sys.platform.startswith("win"):
                os.startfile(p)  # noqa: S606
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", p])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", p])
        except OSError as e:
            self.toast(f"打不开目录：{e}", "err")


def _stamp() -> str:
    import time
    return time.strftime("%Y%m%d-%H%M%S")


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class _SettingsShell:
    """设置的正文与交互，与「宿主容器」无关。

    同一套东西要能跑在两种宿主上：
      · `SettingsPane`   —— 嵌在主页签里（Frame）
      · `SettingsWindow` —— 独立窗口（Toplevel）

    抽出来的理由跟终端（ui/terminal.py 的 `_TermShell`）一样：拉出去、收回来
    不该换一套实现，否则两边迟早不一致（一边能改配色、一边不能这种）。
    这里只用两个宿主都有的能力，Toplevel 专有的（title / geometry / protocol）
    留在子类里。

    还有一条硬规矩：**智能运维助手永远只做侧边栏，不参与这套页签机制**。
    助手要的是「一边看终端一边说话」，做成页签反而互相挡住。
    """

    def _init_settings(self, app, section: str | None = None) -> None:
        self.app = app
        keys = [k for k, _ in SECTIONS]
        self.section = section if section in keys else SECTIONS[0][0]
        wrap = U.wframe(self, "page")
        wrap.pack(fill="both", expand=True, padx=U.px(14), pady=U.px(12))
        self._head(wrap)
        body = U.wframe(wrap, "page")
        body.pack(fill="both", expand=True, pady=(U.px(12), 0))

        self.nav = tk.Frame(body, bg=U.T.page, width=U.px(180))
        self.nav.pack(side="left", fill="y")
        self.nav.pack_propagate(False)
        self._nav_btns: dict[str, tk.Label] = {}
        for key, label in SECTIONS:
            lb = tk.Label(self.nav, text=label, bg=U.T.page, fg=U.T.text_dim,
                          font=U.FONT_S, anchor="w", padx=U.px(12),
                          pady=U.px(8), cursor="hand2")
            lb.pack(fill="x", pady=U.px(1))
            lb.bind("<Button-1>", lambda e, k=key: self.show(k))
            self._nav_btns[key] = lb

        self.panel = tk.Frame(body, bg=U.T.page)
        self.panel.pack(side="left", fill="both", expand=True,
                        padx=(U.px(14), 0))
        self.show(self.section)

    # ------------------------------------------------------------ 头部
    def _head(self, parent) -> None:
        head = U.wframe(parent, "page")
        head.pack(fill="x")
        U.wlabel(head, "设置", "page", U.FONT_T).pack(side="left")
        U.wlabel(head, "外观 · 工作区 · 助手 · 安全 · 备份 · 关于", "pagedim",
                 U.FONT_XS).pack(side="left", padx=(U.px(10), 0))
        # 只有独立窗口才需要这两个按钮：嵌在页签里的那份已经有 × 和 ⧉ 了
        if isinstance(self, tk.Toplevel):
            U.RoundButton(head, "关闭", self.close, variant="secondary",
                          height=30, padx=16).pack(side="right")
            if callable(getattr(self.app, "settings_embed", None)):
                U.RoundButton(head, "嵌回主界面",
                              lambda: self.app.settings_embed(self),
                              variant="ghost", height=30,
                              padx=14).pack(side="right", padx=(0, U.px(6)))

    # -- 切换
    def show(self, key: str) -> None:
        self.section = key
        for k, lb in self._nav_btns.items():
            on = k == key
            try:
                lb.configure(bg=U.T.brand_soft if on else U.T.page,
                             fg=U.T.brand_deep if on else U.T.text_dim)
            except tk.TclError:
                return
        for w in self.panel.winfo_children():
            w.destroy()
        scroll = _Scroll(self.panel, U.T.page)
        scroll.pack(fill="both", expand=True)
        box = scroll.inner
        builder = {
            "look": self._sec_look, "work": self._sec_work,
            "agent": self._sec_agent, "safe": self._sec_safe,
            "backup": self._sec_backup, "about": self._sec_about,
        }.get(key)
        if builder:
            builder(box)

    # ================================================================ 外观
    def _sec_look(self, box) -> None:
        app = self.app
        card = U.Card(box, title="界面主题", hint="浅色、深色，或跟着系统走")
        card.pack(fill="x")
        row = U.wframe(card.body, "card")
        row.pack(fill="x")
        pref = str(app.settings.data.get("theme")
                   or ("dark" if U.T.name == "dark" else "light"))
        seg = Segmented(row, [(k, v) for k, v in THEMES],
                        command=lambda k: (app._set_theme(k),
                                           self.show("look")))
        seg.pack(side="left")
        try:
            seg.select(pref, fire=False)
        except Exception:  # noqa: BLE001
            pass
        U.wlabel(card.body,
                 "「跟随系统」读的是 Windows 的 AppsUseLightTheme。系统主题在运行期间"
                 "被改掉不会自动跟着变 —— 重新进一次这一页就会重新读。",
                 "mute", U.FONT_XS, justify="left",
                 wraplength=U.px(620)).pack(anchor="w", pady=(U.px(8), 0))

        c2 = U.Card(box, title="主题配色",
                    hint="只换品牌色，明暗两套底各自适配")
        c2.pack(fill="x", pady=(U.px(12), 0))
        bar = U.wframe(c2.body, "card")
        bar.pack(fill="x", pady=(0, U.px(8)))
        U.RoundButton(bar, "↺ 恢复默认", app._restore_look, variant="ghost",
                      font=U.FONT_XS, height=26, padx=12).pack(side="left")

        grid = U.wframe(c2.body, "card")
        grid.pack(fill="x")
        cur = str(app.settings.data.get("accent") or DEFAULT_ACCENT)
        for i, (name, hex_) in enumerate(ACCENTS):
            cell = tk.Frame(grid, bg=U.T.card, bd=0, highlightthickness=2,
                            highlightbackground=(U.T.brand if name == cur
                                                 else U.T.border),
                            cursor="hand2")
            cell.grid(row=i // 5, column=i % 5, padx=U.px(5), pady=U.px(5),
                      sticky="nsew")
            cv = tk.Canvas(cell, width=U.px(112), height=U.px(46),
                           bg=U.T.card, highlightthickness=0, bd=0,
                           cursor="hand2")
            cv.pack(padx=U.px(4), pady=(U.px(6), 0))
            cv.create_rectangle(U.px(6), U.px(8), U.px(74), U.px(15),
                                fill=U.T.text, outline="")
            cv.create_rectangle(U.px(6), U.px(21), U.px(56), U.px(26),
                                fill=U.T.text_mute, outline="")
            cv.create_oval(U.px(88), U.px(12), U.px(104), U.px(28),
                           fill=hex_, outline="")
            if name == cur:
                cv.create_text(U.px(84), U.px(40), text="✓", fill=hex_,
                               font=U.FONT_XS)
            lb = tk.Label(cell, text=name, bg=U.T.card,
                          fg=U.T.text if name == cur else U.T.text_dim,
                          font=U.FONT_XS, anchor="w")
            lb.pack(fill="x", padx=U.px(8), pady=(0, U.px(5)))
            for w in (cell, cv, lb):
                w.bind("<Button-1>",
                       lambda e, n=name: (app._set_accent(n),
                                          self.show("look")))
                w.bind("<Enter>", lambda e, c=cell: c.configure(
                    highlightbackground=U.T.brand_hover))
                w.bind("<Leave>", lambda e, c=cell, n=name: c.configure(
                    highlightbackground=(U.T.brand if n == str(
                        app.settings.data.get("accent") or DEFAULT_ACCENT)
                        else U.T.border)))

    # ================================================================ 工作区
    def _sec_work(self, box) -> None:
        app = self.app
        card = U.Card(box, title="窗口与日志", hint="这些是立刻生效的开关")
        card.pack(fill="x")
        b = card.body

        v_tray = tk.BooleanVar(value=bool(getattr(app, "_close_to_tray", True)))

        def on_tray():
            app._close_to_tray = bool(v_tray.get())
            app.settings.data["close_to_tray"] = app._close_to_tray
            app.settings.save()

        row = U.wframe(b, "card")
        row.pack(fill="x", pady=(0, U.px(8)))
        U.Check(row, "", variable=v_tray, command=on_tray).pack(side="left")
        cell = U.wframe(row, "card")
        cell.pack(side="left", fill="x", expand=True, padx=(U.px(4), 0))
        U.wlabel(cell, "关闭窗口时收到托盘", "card", U.FONT_S).pack(anchor="w")
        U.wlabel(cell, "关掉这个，点 × 就直接退出（终端里的会话会断）", "mute",
                 U.FONT_XS).pack(anchor="w")

        row2 = U.wframe(b, "card")
        row2.pack(fill="x", pady=(0, U.px(8)))
        U.wlabel(row2, "命令日志栏", "card", U.FONT_S).pack(side="left")

        def toggle_log(show: bool):
            app._toggle_log(show)
            self.after(120, lambda: self.show("work"))

        U.RoundButton(row2, "展开", lambda: toggle_log(True), variant="ghost",
                      font=U.FONT_XS, height=26, padx=12).pack(
            side="right", padx=(U.px(4), 0))
        U.RoundButton(row2, "收起", lambda: toggle_log(False), variant="ghost",
                      font=U.FONT_XS, height=26, padx=12).pack(side="right")

        state = "停靠" if getattr(app, "_log_visible", True) else "已收起"
        U.wlabel(b, f"当前：{state}", "mute", U.FONT_XS).pack(anchor="w")

        c2 = U.Card(box, title="多设备", hint="一次连几台，上方会多出设备标签")
        c2.pack(fill="x", pady=(U.px(12), 0))
        n = len(app.sessions)
        names = "、".join((s.label or s.sid) for s in app.sessions) or "（还没有）"
        U.wlabel(c2.body, f"当前 {n} 台：{names}", "dim", U.FONT_S,
                 justify="left", wraplength=U.px(620)).pack(anchor="w")

    # ================================================================ 助手
    def _sec_agent(self, box) -> None:
        app = self.app
        cfg = app.agent_cfg
        card = U.Card(box, title=cfg.name, hint="描述 · 提示词 · 技能 · 工具")
        card.pack(fill="x")
        b = card.body
        U.wlabel(b, cfg.desc, "dim", U.FONT_S, justify="left",
                 wraplength=U.px(620)).pack(anchor="w")
        tools = "、".join(t for t in cfg.tools) or "（一个都没启用）"
        U.wlabel(b, f"启用工具：{tools}", "mute", U.FONT_XS, justify="left",
                 wraplength=U.px(620)).pack(anchor="w", pady=(U.px(6), 0))
        U.wlabel(b, f"执行模式：{cfg.mode} · 最多 {cfg.max_steps} 步", "mute",
                 U.FONT_XS).pack(anchor="w")

        row = U.wframe(b, "card")
        row.pack(fill="x", pady=(U.px(12), 0))
        U.RoundButton(row, "智能体设置…", app.ai_agent_settings, variant="primary",
                      height=30, padx=14).pack(side="left")
        U.RoundButton(row, "模型设置…", app.ai_model_settings, variant="ghost",
                      height=30, padx=14).pack(side="left", padx=(U.px(8), 0))
        U.RoundButton(row, "技能库…", app._ai_open_skill_lib, variant="ghost",
                      height=30, padx=14).pack(side="left", padx=(U.px(6), 0))

        c2 = U.Card(box, title="侧边栏", hint="助手常驻在右侧，不占选项卡")
        c2.pack(fill="x", pady=(U.px(12), 0))
        r2 = U.wframe(c2.body, "card")
        r2.pack(fill="x")
        on = bool(getattr(app, "_ai_dock_on", False))
        U.RoundButton(r2, "收起侧栏" if on else "打开侧栏", app.ai_dock_toggle,
                      variant="secondary", height=30,
                      padx=14).pack(side="left")
        U.RoundButton(r2, "清空会话", app.ai_dock_clear, variant="ghost",
                      height=30, padx=14).pack(side="left", padx=(U.px(8), 0))

    # ================================================================ 安全
    def _sec_safe(self, box) -> None:
        app = self.app
        lock = app.lock
        on = lock.enabled()

        card = U.Card(box, title="应用锁屏",
                      hint="闲置到时间就锁住界面，回来后要输口令")
        card.pack(fill="x")
        b = card.body
        row = U.wframe(b, "card")
        row.pack(fill="x")
        U.Pill(row, "已启用" if on else "未启用", "ok" if on else "mute",
               height=22).pack(side="left")
        U.wlabel(row, lock.describe(), "dim", U.FONT_XS).pack(
            side="left", padx=(U.px(10), 0))
        U.RoundButton(row, "修改 / 关闭…" if on else "启用应用锁",
                      app._open_lock_window, variant="primary", height=28,
                      padx=14).pack(side="right")
        if on:
            U.RoundButton(row, "立即锁定", lambda: app._show_lock("已手动锁定"),
                          variant="ghost", height=28, padx=12).pack(
                side="right", padx=(0, U.px(6)))

        c2 = U.Card(box, title="闲置自动锁定", hint="0 表示永不自动锁")
        c2.pack(fill="x", pady=(U.px(12), 0))
        b2 = c2.body
        r2 = U.wframe(b2, "card")
        r2.pack(fill="x")
        U.wlabel(r2, "闲置", "card", U.FONT_S).pack(side="left")
        v_min = tk.StringVar(value=str(max(0, lock.timeout() // 60)))
        ent = tk.Entry(r2, textvariable=v_min, width=6, font=U.FONT_S,
                       bd=0, relief="flat", bg=U.T.input_bg, fg=U.T.text,
                       insertbackground=U.T.text)
        ent.pack(side="left", padx=U.px(6), ipady=U.px(3))
        U.wlabel(r2, "分钟后锁定", "card", U.FONT_S).pack(side="left")

        v_rem = tk.BooleanVar(value=lock.auto_login())
        r3 = U.wframe(b2, "card")
        r3.pack(fill="x", pady=(U.px(10), 0))
        U.Check(r3, "记住口令（本机加密保存，启动自动解锁）", v_rem).pack(
            side="left")

        msg = U.wlabel(b2, "", "err", U.FONT_XS)
        msg.pack(anchor="w", pady=(U.px(6), 0))

        def save():
            try:
                mins = int(v_min.get() or "0")
            except ValueError:
                msg.configure(text="分钟数必须是整数")
                return
            if mins < 0:
                msg.configure(text="不能是负数")
                return
            lock.set_options(timeout=0 if mins == 0 else mins * 60,
                             remember=bool(v_rem.get()))
            msg.configure(text="")
            app.toast("已保存", "ok")
            app._settings_sync()

        U.RoundButton(b2, "保存", save, variant="primary", height=28,
                      padx=16).pack(anchor="w", pady=(U.px(10), 0))

        c3 = U.Card(box, title="本机安全", hint="这一页只做真能生效的东西")
        c3.pack(fill="x", pady=(U.px(12), 0))
        b3 = c3.body
        for title, desc, ok in (
                ("口令与会话凭据加密落盘", "crypto_store（Windows DPAPI / 本地密钥）", True),
                ("命令日志脱敏", "日志里的口令、私钥、Token 会被打码后再写入", True),
                ("外部链接打开前确认", "Tk 没有拦截系统浏览器的可靠钩子", False),
                ("系统休眠 / 切换用户时锁定", "需要挂 Windows 会话事件，当前未实现", False),
        ):
            row = U.wframe(b3, "card")
            row.pack(fill="x", pady=U.px(4))
            U.Pill(row, "已具备" if ok else "未支持", "ok" if ok else "mute",
                   height=20).pack(side="left")
            cell = U.wframe(row, "card")
            cell.pack(side="left", fill="x", expand=True, padx=(U.px(8), 0))
            U.wlabel(cell, title, "card", U.FONT_S).pack(anchor="w")
            U.wlabel(cell, desc, "mute", U.FONT_XS).pack(anchor="w")

    # ================================================================ 备份
    def _sec_backup(self, box) -> None:
        app = self.app
        src = app.settings_data_dir()
        card = U.Card(box, title="数据目录", hint="配置、技能、凭据密文都在这里")
        card.pack(fill="x")
        b = card.body
        U.wlabel(b, str(src), "dim", U.FONT_S, justify="left",
                 wraplength=U.px(620)).pack(anchor="w")
        U.wlabel(b, f"占用 {_human(_dir_size(src))}", "mute", U.FONT_XS).pack(
            anchor="w", pady=(U.px(4), 0))
        row = U.wframe(b, "card")
        row.pack(fill="x", pady=(U.px(10), 0))
        U.RoundButton(row, "导出备份…", app.settings_backup, variant="primary",
                      height=30, padx=14).pack(side="left")
        U.RoundButton(row, "打开目录", app.settings_open_data, variant="ghost",
                      height=30, padx=14).pack(side="left", padx=(U.px(8), 0))

        c2 = U.Card(box, title="换机器怎么搬", hint="拷 data/ 还是导 zip 都行")
        c2.pack(fill="x", pady=(U.px(12), 0))
        U.wlabel(c2.body,
                 "1. 关掉程序，把整个 data/ 目录（或导出的 zip）拷到新机器同一位置。\n"
                 "2. 配置、技能、智能体人设、设备档案都会跟着走。\n"
                 "3. API Key 是绑定本机用户的密文，换了机器要重新填一次；"
                 "设备口令同理。\n"
                 "4. 站点配置、数据库、计划任务这些在远端设备上，不在 data/ 里 —— "
                 "那部分交给智能运维助手去搬。",
                 "dim", U.FONT_S, justify="left",
                 wraplength=U.px(620)).pack(anchor="w")

    # ================================================================ 关于
    def _sec_about(self, box) -> None:
        app = self.app
        card = U.Card(box, title=getattr(app, "app_name", "Terminal GUI Toolbox"))
        card.pack(fill="x")
        b = card.body
        rows = [
            ("版本", getattr(app, "app_ver", "—")),
            ("运行方式", "打包 exe" if getattr(sys, "frozen", False) else "Python 源码"),
            ("Python", sys.version.split()[0]),
            ("Tk", str(tk.TkVersion)),
            ("数据目录", str(app.settings_data_dir())),
            ("许可", "见 LICENSE / THIRD-PARTY-NOTICES.md"),
        ]
        for k, v in rows:
            row = U.wframe(b, "card")
            row.pack(fill="x", pady=U.px(3))
            U.wlabel(row, k, "mute", U.FONT_XS).pack(side="left")
            U.wlabel(row, str(v), "dim", U.FONT_S).pack(side="left",
                                                        padx=(U.px(12), 0))


class _Scroll(tk.Frame):
    """设置页用的滚动容器（和助手侧栏同款，避免两处各写一份）。"""

    def __init__(self, master, bg: str):
        super().__init__(master, bg=bg, bd=0)
        self.cv = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vs = tk.Scrollbar(self, orient="vertical", command=self.cv.yview,
                               width=U.px(10), bd=0, relief="flat")
        self.cv.configure(yscrollcommand=self.vs.set)
        self.cv.pack(side="left", fill="both", expand=True)
        self.vs.pack(side="right", fill="y")
        self.inner = tk.Frame(self.cv, bg=bg, bd=0)
        self._win = self.cv.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.cv.configure(
            scrollregion=self.cv.bbox("all") or (0, 0, 0, 0)))
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfigure(
            self._win, width=e.width))
        self.cv.bind("<Enter>", lambda e: self.cv.bind_all("<MouseWheel>",
                                                           self._wheel))
        self.cv.bind("<Leave>", lambda e: self.cv.unbind_all("<MouseWheel>"))
        self.inner.bind("<Enter>", lambda e: self.cv.bind_all("<MouseWheel>",
                                                              self._wheel))
        self.inner.bind("<Leave>", lambda e: self.cv.unbind_all("<MouseWheel>"))

    def _wheel(self, e):
        self.cv.yview_scroll(-3 if e.delta > 0 else 3, "units")
        return "break"


class SettingsPane(_SettingsShell, tk.Frame):
    """嵌在主界面页签里的设置页。除了没有窗口边框，行为与独立窗口一致。"""

    def __init__(self, master, app, section: str | None = None):
        tk.Frame.__init__(self, master, bg=U.T.page, bd=0)
        self._init_settings(app, section)


class SettingsWindow(_SettingsShell, tk.Toplevel):
    """拉出来的独立设置窗口，尺寸对齐桌面设置的常见比例。"""

    def __init__(self, app, section: str | None = None,
                 master=None):
        tk.Toplevel.__init__(self, master if master is not None else app)
        self.configure(bg=U.T.page)
        self.title("设置 · Settings")
        self.geometry(f"{U.px(900)}x{U.px(660)}")
        self.minsize(U.px(760), U.px(520))
        try:
            self.transient(app)
        except tk.TclError:
            pass
        self._init_settings(app, section)
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)

    def close(self) -> None:
        """关窗要顺手把主程序手上的引用清掉，否则下次 open_settings 会去抬一个死窗口。"""
        try:
            cb = getattr(self.app, "settings_closed", None)
            if callable(cb):
                cb(self)
        finally:
            try:
                self.destroy()
            except tk.TclError:
                pass
