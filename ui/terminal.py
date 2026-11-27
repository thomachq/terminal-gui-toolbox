# -*- coding: utf-8 -*-
"""终端窗口 —— 程序和用户共用一个会话。

和「命令日志」的分工：

  · 命令日志是**审计**：带时间戳、rc、耗时，一条都不少，只读；
  · 终端是**干活**：提示符 + 光标，能就地敲命令，程序执行的命令也回显在这里。

默认不打开（用户明确要求），在主界面点「终端」才出现。做成独立窗口而不是
主界面的一页，是因为命令输出经常很长，窗口能拉大、能挪到第二块屏幕上去。

数据来源有两条，都汇到同一个 Text 里：

  1. 主程序执行的每条命令 → `session.tracer` → `ToolboxApp._term_feed` → `feed()`；
  2. 用户在这里敲的命令 → `ctrl.term_run()` → 同一个 session。

自己敲的命令走 `run_traced()` 时会再触发一次 tracer，造成重复回显，
所以渲染期间用一个 `_own` 标志让旁路跳过这次。

手动输入有两种形态，用户自己选（窗口右上角「行内 / 输入框」）：

  · 行内（默认）：像真终端那样，提示符直接落在正文里，命令和结果混排在
    一条流里。光标被限制在提示符之后，输出插到提示符之前，正在敲的内容
    不会被冲掉。默认它是因为**没有独立输入行**，正文自己会随窗口高低伸缩，
    不存在「输入行被别的东西挤掉」这种可能。
  · 输入框：底部一条独立的输入行 + 提示符标签，输出在大框里滚。
    编辑器行为、不会误改上面的输出，代价是多占一条高度。

底部日志面板升起时，终端区会被压矮。输入行的 pack 顺序必须是
「头部 → 输入行(底部) → 正文(吃剩余)」——反过来的话正文（expand=True）
先按自己的请求高度（Text 默认 24 行）拿走空间，输入行最后一个 pack，
空间不够时第一个被压成 0 高、连 ismapped 都变 0（v1.0.24 用户报的
「升起日志后输入框被覆盖」就是这个）。见 `_build()` 里的 `before=self.wrap`。
"""
from __future__ import annotations

import os
import queue
import time
import shlex
import threading
import time
import tkinter as tk
from tkinter import ttk

import wbtui as U
from core.redact import scrub, scrub_cmd

MAX_LINES = 4000          # 超过就从头砍，长跑一晚也不会把内存吃光
HIST_MAX = 300
# Tab 补全的内建命令候选（与 _help 里那张表保持一致）。
BUILTIN_CMDS = ("clear", "cls", "history", "cd", "help", "exit", "quit")

#: 这些命令后面跟的是路径，Tab 才值得去问设备要目录清单。
#: 不在这里的命令（如 `systemctl`、`docker`）参数千奇百怪，发 `ls` 只会白跑一趟。
PATH_CMDS = frozenset((
    "cd", "ls", "ll", "la", "cat", "less", "more", "head", "tail", "vi", "vim",
    "nano", "emacs", "cp", "mv", "rm", "mkdir", "rmdir", "touch", "chmod",
    "chown", "chgrp", "ln", "stat", "file", "find", "du", "df", "tar", "unzip",
    "zip", "gzip", "gunzip", "scp", "rsync", "md5sum", "sha256sum", "diff",
    "source", "sh", "bash", "python3", "python", "nohup",
))
#: 列目录一次要发一条命令，短时间内重复按 Tab 不该重复发。
PATH_CACHE_TTL = 4.0
PATH_CACHE_MAX = 24


def completion_kind(line: str) -> tuple[str, str, str]:
    """这一次 Tab 该补什么：`(kind, 命令, 行尾词)`，kind ∈ `cmd` / `path` / `none`。

    抽成纯函数是因为 Tab 的绑定必须 `return "break"`，没有窗口就测不了；
    但「这次该问远端要目录、还是补命令名」这条判断完全可以脱离 UI 测。
    """
    word = line.rsplit(" ", 1)[-1] if line else ""
    prefix = word.strip()
    head = line[: len(line) - len(word)].strip()
    cmd = head.split()[0] if head else ""
    if not cmd:
        return "cmd", "", word             # 行首：补命令名
    if prefix.startswith("-"):
        return "none", cmd, word           # `ls -l` 这种开关，不猜
    if cmd in PATH_CMDS or "/" in prefix:
        return "path", cmd, word
    return "cmd", cmd, word


def split_dir_prefix(token: str) -> tuple[str, str]:
    """把行尾词切成「目录前缀 + 名字前缀」。

    `/` 和 `\\` 都得认：本机 Windows 的终端里用户敲的是 `C:\\Us`，
    只认 `/` 的话会把整串当成名字前缀，补出来的候选不带盘符 —— 一补就丢路径。
    """
    token = str(token or "")
    cut = max(token.rfind("/"), token.rfind("\\"))
    if cut < 0:
        return "", token
    return token[: cut + 1], token[cut + 1:]


def _ps_quote(s: str) -> str:
    """PowerShell 单引号字符串（内部单引号翻倍）。"""
    return "'" + str(s).replace("'", "''") + "'"


def path_list_cmd(kind: str, dir_spec: str) -> str:
    """问设备要目录清单的命令。

    两种外壳不能混用：Windows 本机（PowerShell）没有 `ls -1p`，`ls` 只是
    `Get-ChildItem` 的别名，参数一给就报错 —— 于是「本机终端里 Tab 补不了路径」
    而 Linux 上好好的。这里按 `host_kind` 分开生成，两边输出格式统一成
    「一 行 一 个名字，目录带 `/` 后缀」。
    """
    if str(kind or "").lower() == "windows":
        d = dir_spec or "."
        return ("Get-ChildItem -LiteralPath " + _ps_quote(d) + " -Force "
                "-ErrorAction SilentlyContinue | ForEach-Object { "
                "$_.Name + $(if ($_.PSIsContainer) { '/' } else { '' }) }")
    return f"ls -1p -- {shlex.quote(dir_spec or './')} 2>/dev/null"


def parse_ls_listing(token: str, text: str) -> list[str]:
    """把 `ls -1p -- <dir>` 的输出变成「整词候选」。

    抽成纯函数是为了能测：这段逻辑里有三个容易写错的地方 ——
      ① `.` 开头的隐藏文件必须保留（`lstrip("./")` 会把 `.bashrc` 削成 `bashrc`）；
      ② 目录带 `/` 后缀（`-p` 给的），补上之后用户能直接接着 Tab 下一层；
      ③ token 已经带了目录前缀（`cd /var/lo`）时，候选也要带同样的前缀，
         否则补进去的是裸文件名，替换掉整个 token 之后路径就丢了。
    """
    token = str(token or "")
    dir_part, name_part = split_dir_prefix(token)
    out: list[str] = []
    for line in str(text or "").splitlines():
        s = line.strip()
        if not s or s.endswith(":"):        # `ls` 多目录时打的 "xxx:" 头
            continue
        if dir_part:
            if not s.startswith(dir_part):
                continue
            s = s[len(dir_part):]
        elif s.startswith("./"):
            s = s[2:]                       # 只剥一次，不动 `.bashrc`
        if not s.startswith(name_part):
            continue
        word = dir_part + s
        if word not in out:
            out.append(word)
    return out

# 记住用户上次选的输入形态（本进程内有效，重开窗口不跳回去）。
# 默认行内：没有独立输入行，就不会被底部日志面板挤掉（用户 2026-10-25 要求
# 「直接在界面里输入，而不是弄输入框」）。
_LAST_MODE = "inline"

MODES = [("inline", "行内"), ("box", "输入框")]

BANNER = (
    "终端已就绪。命令直接跑在当前会话上，跟主界面用的是同一个连接。\n"
    "输入 help 看内建命令；↑ ↓ 翻历史；Ctrl+L 清屏。\n"
    "私钥/tls-auth 正文（PEM、ta.key、ppk）在这里会自动打码，不会显示出来。\n"
)


class _TermShell:
    """终端的正文与交互，与「宿主容器」无关。

    同一套东西要能跑在两种宿主上：
      · `TerminalWindow` —— 独立窗口（Toplevel）
      · `TerminalPane`   —— 嵌在主页签里（Frame）
    抽出来的原因很实际：拉出去、收回来不该换一套实现，否则两边行为迟早不一致
    （一边能翻历史、一边不能这种）。这里只依赖 Tk 的两个宿主都有的能力，
    Toplevel 专有的（title / geometry / protocol）留在子类里。
    """

    def _init_term(self, ctrl, mode: str | None) -> None:
        global _LAST_MODE
        self.ctrl = ctrl
        self.mode = mode if mode in ("box", "inline") else _LAST_MODE
        _LAST_MODE = self.mode

        self._hist: list[str] = []
        self._hist_at = 0
        self._busy = False
        self._cancel = False
        self._own = False          # 自己正在执行：让 tracer 旁路闭嘴
        # 主程序队列和终端队列各走各的 after(80)，谁先跑到没有保证，
        # 光靠 _own 会漏掉一次重复回显。所以再记一条「刚刚自己敲了什么」，
        # 旁路送来同一条命令时按文本比对吞掉，跟执行顺序无关。
        self._own_cmd = ""
        self._own_ts = 0.0
        #: 远端目录清单缓存：(目录, 机器) → (时间, 候选)。按 Tab 连按不会连发 ls。
        self._path_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}
        self._sync = tk.BooleanVar(value=True)
        self._prompt_str = ""      # 行内模式写在正文里的提示符
        # 命令在后台线程跑，结果不能直接调 after —— 跨线程注册回调会在
        # Tk 还没进 mainloop 时抛 "main thread is not in main loop"。
        # 统一投进队列，由主线程的 _drain 取出来执行。
        self._inbox: queue.Queue = queue.Queue()

        self._build()
        self._apply_mode(quiet=True)
        self._banner()
        self.after(30, self._drain)
        self.after(60, self._focus_input)

    # ------------------------------------------------------------ 线程 → 主线程
    def post(self, fn) -> None:
        self._inbox.put(fn)

    def _drain(self) -> None:
        while True:
            try:
                fn = self._inbox.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.after(30, self._drain)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 骨架
    def _build(self) -> None:
        head = tk.Frame(self, bg=U.T.page, bd=0)
        head.pack(fill="x", padx=U.px(12), pady=(U.px(10), U.px(6)))
        self.head = head

        self.lbl_title = tk.Label(head, text="终端", bg=U.T.page, fg=U.T.text,
                                  font=(U.FONT_FAMILY, 11, "bold"), bd=0)
        self.lbl_title.pack(side="left")
        self.lbl_dev = tk.Label(head, text="", bg=U.T.page, fg=U.T.text_dim,
                                font=U.FONT_S, bd=0, anchor="w")
        self.lbl_dev.pack(side="left", padx=(U.px(10), 0))

        U.RoundButton(head, "关闭", self.close, variant="ghost",
                      font=U.FONT_S, height=26, padx=10).pack(side="right")
        # 独立窗口才有「嵌回」—— 嵌在页签里的那份已经在家了
        if isinstance(self, tk.Toplevel) and callable(
                getattr(self.ctrl, "term_embed", None)):
            U.RoundButton(head, "嵌回主界面",
                          lambda: self.ctrl.term_embed(self),
                          variant="ghost", font=U.FONT_S, height=26,
                          padx=10).pack(side="right", padx=(0, U.px(6)))
        U.RoundButton(head, "清屏", self.clear, variant="ghost",
                      font=U.FONT_S, height=26, padx=10).pack(
            side="right", padx=(0, U.px(6)))
        # 输入形态：输入框（默认）／行内（提示符落在正文里）
        from ui.table import Segmented
        self.seg_mode = Segmented(head, MODES, command=self._set_mode)
        self.seg_mode.select(self.mode, fire=False)
        self.seg_mode.pack(side="right", padx=(0, U.px(10)))
        # 头部控件多，窄的时候先被挤掉的是最左边那几个 —— 所以文案都压到最短，
        # 「输入方式」这种一看分段控件就懂的标签干脆不要。
        # 智能体通过 API 跑的命令单独一个开关：很多人只想看自己敲的，
        # 不想被智能体的命令刷屏；反过来也有只想看智能体在干什么的。
        # 「同步命令」是总线：关掉它，两个都不显示。
        self._sync_api = tk.BooleanVar(value=True)
        self.chk_api = U.Check(head, "同步 API", self._sync_api, font=U.FONT_XS)
        self.chk_api.pack(side="right", padx=(0, U.px(10)))
        self.chk_sync = U.Check(head, "同步命令", self._sync, font=U.FONT_XS)
        self.chk_sync.pack(side="right", padx=(0, U.px(10)))
        # AI 活动流：助手调模型、发命令都往这里写。默认开 —— 这就是
        # 「AI 干了什么，终端里看得见」；嫌吵可以关掉，不影响命令本身。
        self._ai_on = tk.BooleanVar(value=True)
        self.chk_ai = U.Check(head, "AI 活动", self._ai_on, font=U.FONT_XS)
        self.chk_ai.pack(side="right", padx=(0, U.px(10)))

        # 正文
        wrap = tk.Frame(self, bg=U.T.card, bd=0, highlightthickness=1,
                        highlightbackground=U.T.border)
        wrap.pack(fill="both", expand=True, padx=U.px(12))
        self.wrap = wrap

        self.text = tk.Text(wrap, bg=U.T.card, fg=U.T.text, bd=0,
                            highlightthickness=0, wrap="char",
                            font=(U.MONO[0], 10), padx=U.px(10), pady=U.px(8),
                            insertwidth=1, spacing1=0, spacing3=1,
                            selectbackground=U.T.brand_soft2,
                            selectforeground=U.T.text)
        self.text.pack(side="left", fill="both", expand=True)
        self.sb = ttk.Scrollbar(wrap, orient="vertical", command=self.text.yview,
                                style="Card.Vertical.TScrollbar")
        self.text.configure(yscrollcommand=self.sb.set)
        self.sb.pack(side="right", fill="y")

        self.text.tag_configure("out", foreground=U.T.text)
        self.text.tag_configure("err", foreground=U.T.err)
        self.text.tag_configure("cmd", foreground=U.T.text)
        self.text.tag_configure("prompt", foreground=U.T.ok)
        self.text.tag_configure("meta", foreground=U.T.text_mute)
        self.text.tag_configure("echo", foreground=U.T.brand)
        # AI 活动流三档：头部（箭头那行）加重、正文摘录退后、警示用 warn
        self.text.tag_configure("aihead", foreground=U.T.brand,
                                font=(U.MONO[0], 10, "bold"))
        self.text.tag_configure("aidim", foreground=U.T.text_mute)
        self.text.tag_configure("warn", foreground=U.T.warn)
        self.text.configure(state="disabled")

        # 输入行。
        # `side="bottom" + before=self.wrap` 这两样缺一不可：
        #   · side="bottom" 让它贴着底边（切到行内模式时正文才会自然接管整块）；
        #   · before=self.wrap 把它排在正文**前面**分配空间。pack 是按顺序
        #     切的，正文带 expand=True、请求高度又是 Text 默认的 24 行，排在
        #     它后面就只能在剩余空间里捡 —— 日志面板一升起、终端区一矮，
        #     输入行直接拿到 0 高（`winfo_ismapped()` 变 0，但代码里
        #     `winfo_manager()` 仍然是 "pack"，从返回值完全看不出问题）。
        row = tk.Frame(self, bg=U.T.card, bd=0, highlightthickness=1,
                       highlightbackground=U.T.border)
        row.pack(side="bottom", fill="x", padx=U.px(12),
                 pady=(U.px(6), U.px(12)), before=self.wrap)
        self.row = row
        self.lbl_ps1 = tk.Label(row, text="$", bg=U.T.card, fg=U.T.ok,
                                font=(U.MONO[0], 10), bd=0, padx=U.px(8),
                                anchor="w")
        self.lbl_ps1.pack(side="left")
        self.v_in = tk.StringVar()
        self.entry = tk.Entry(row, textvariable=self.v_in, bd=0,
                              bg=U.T.card, fg=U.T.text, insertbackground=U.T.text,
                              font=(U.MONO[0], 10), highlightthickness=0)
        self.entry.pack(side="left", fill="x", expand=True,
                        padx=(0, U.px(8)), pady=U.px(6))
        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<Up>", self._hist_prev)
        self.entry.bind("<Down>", self._hist_next)
        self.entry.bind("<Tab>", self._complete)
        self.entry.bind("<Control-l>", lambda e: (self.clear(), "break")[1])
        self.entry.bind("<Control-c>", self._sigint)

        U.register(self, self._paint)

    def _paint(self) -> None:
        """主题切换时重刷。Text 里的 tag 也要跟着改前景色。"""
        try:
            self.configure(bg=U.T.page)
            self.head.configure(bg=U.T.page)
            self.row.configure(bg=U.T.card, highlightbackground=U.T.border)
            self.lbl_title.configure(bg=U.T.page, fg=U.T.text)
            self.lbl_dev.configure(bg=U.T.page, fg=U.T.text_dim)
            # tk.Frame 没有 -fg，只能分开设；一起设会抛 TclError，
            # 而这里又不能让它冒出去（apply_theme 只吞 TclError），
            # 一个错参数就会让整个重绘变成空操作。
            self.wrap.configure(bg=U.T.card)
            self.text.configure(bg=U.T.card, fg=U.T.text)
            self.lbl_ps1.configure(bg=U.T.card, fg=U.T.ok)
            self.entry.configure(bg=U.T.card, fg=U.T.text,
                                 insertbackground=U.T.text)
            self.text.tag_configure("out", foreground=U.T.text)
            self.text.tag_configure("err", foreground=U.T.err)
            self.text.tag_configure("cmd", foreground=U.T.text)
            self.text.tag_configure("prompt", foreground=U.T.ok)
            self.text.tag_configure("meta", foreground=U.T.text_mute)
            self.text.tag_configure("echo", foreground=U.T.brand)
            self.text.tag_configure("aihead", foreground=U.T.brand,
                                    font=(U.MONO[0], 10, "bold"))
            self.text.tag_configure("aidim", foreground=U.T.text_mute)
            self.text.tag_configure("warn", foreground=U.T.warn)
            self.text.configure(selectbackground=U.T.brand_soft2,
                                selectforeground=U.T.text)
            self.wrap.configure(highlightbackground=U.T.border)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 输入形态
    def _set_mode(self, key: str) -> None:
        """用户在头部切了「输入框 / 行内」。正在敲的内容要跟着搬过去。"""
        if key not in ("box", "inline") or key == self.mode:
            return
        keep = self._get_input()
        self.mode = key
        globals()["_LAST_MODE"] = key
        self._apply_mode()
        self._set_input(keep)
        self._focus_input()

    def _apply_mode(self, quiet: bool = False) -> None:
        """把控件树调整成当前形态该有的样子。"""
        try:
            if self.mode == "inline":
                self.row.pack_forget()
                self.text.configure(state="normal", insertwidth=2,
                                    insertbackground=U.T.text)
                self._bind_inline(True)
                self._ensure_prompt()
                self.lbl_ps1.configure(text=self._prompt_str or "$")
            else:
                self._bind_inline(False)
                self._drop_prompt()
                self.text.configure(state="disabled", insertwidth=1)
                if not self.row.winfo_manager():
                    # 顺序必须跟 _build() 一致：贴底 + 排在正文前面。
                    # 只写 pack(fill="x") 会排到队尾，空间一紧又被挤掉。
                    self.row.pack(side="bottom", fill="x", padx=U.px(12),
                                  pady=(U.px(6), U.px(12)), before=self.wrap)
                self.lbl_ps1.configure(text=self._safe_prompt())
        except tk.TclError:
            pass

    def _safe_prompt(self) -> str:
        try:
            return self.ctrl.term_prompt()
        except Exception:  # noqa: BLE001
            return "$ "

    def _bind_inline(self, on: bool) -> None:
        t = self.text
        seqs = {
            "<Return>": self._submit,
            "<KP_Enter>": self._submit,
            "<Up>": self._hist_prev,
            "<Down>": self._hist_next,
            "<Tab>": self._complete,
            "<Control-l>": lambda e: (self.clear(), "break")[1],
            "<Control-c>": self._sigint,
            "<Key>": self._clamp_key,
            "<BackSpace>": self._clamp_key,
            "<Delete>": self._clamp_key,
            "<ButtonRelease-1>": self._clamp_key,
        }
        for seq, fn in seqs.items():
            try:
                if on:
                    t.bind(seq, fn, add="+")
                else:
                    t.unbind(seq)
            except tk.TclError:
                pass

    def _clamp_key(self, e=None):
        """行内模式：光标和删除都不许越过提示符。

        光把光标拽回末尾是不够的：用户先点在输出区（光标在 input_start
        之前），再按退格，默认处理会删掉「新光标位置的**前一个**字符」——
        输入区为空时那就是提示符自己的最后一个字（`root@h:~$ ` 的空格），
        删几次提示符就没了。所以删除键要单独判，越界就 return "break"。
        """
        keysym = getattr(e, "keysym", "")
        try:
            before = self.text.compare("insert", "<", "input_start")
            at = self.text.compare("insert", "<=", "input_start")
            if keysym == "BackSpace" and at:
                # 退格是删前一个字符，光标贴在 input_start 上就已经越界
                if before:
                    self.text.mark_set("insert", "end-1c")
                return "break"
            if keysym == "Delete":
                if before:
                    self.text.mark_set("insert", "end-1c")
                    return "break"
            if before:
                self.text.mark_set("insert", "end-1c")
        except tk.TclError:
            pass
        return None

    def _ensure_prompt(self) -> None:
        """正文末尾要有一条可输入的提示符行。"""
        try:
            if "prompt" in self.text.mark_names():
                return
        except tk.TclError:
            return
        self._append_prompt()

    def _append_prompt(self) -> None:
        try:
            ps = self._safe_prompt()
            self._prompt_str = ps
            # 正文若不是以换行结尾，先补一个，免得命令接在上一行尾巴上
            if self.text.index("end-1c") != "1.0":
                tail = self.text.get("end-2c", "end-1c")
                if tail != "\n":
                    self.text.insert("end-1c", "\n")
            self.text.mark_set("insert", "end-1c")
            self.text.insert("insert", ps, "prompt")
            # prompt 标记钉在**提示符字符串的起点**，不是行首：
            # 输出若不以换行结尾（比如 feed 里的时间戳），提示符会临时贴在
            # 那一行尾巴上，这时按行首删就会连输出一起删掉。
            self.text.mark_set("prompt", f"insert-{len(ps)}c")
            self.text.mark_gravity("prompt", "right")
            self.text.mark_set("input_start", "insert")
            self.text.mark_gravity("input_start", "left")
            self.text.see("end")
        except tk.TclError:
            pass

    def _reset_inline_prompt(self) -> None:
        """cd 之后提示符里的目录要跟着变，但不许动用户已经敲的字。"""
        if self.mode != "inline":
            return
        try:
            ps = self._safe_prompt()
            self._prompt_str = ps
            if self.text.compare("prompt", "<", "input_start"):
                # prompt 标记平时是右重力（输出插在它前面，它跟着往后走）；
                # 这里要往它前面塞新提示符，得临时掰成左重力，否则标记会被
                # 推到新提示符后面 —— 那之后「删提示符」就会删错位置。
                self.text.mark_gravity("prompt", "left")
                self.text.delete("prompt", "input_start")
                self.text.insert("prompt", ps, "prompt")
                self.text.mark_set("input_start", "insert")
                self.text.mark_gravity("input_start", "left")
                self.text.mark_gravity("prompt", "right")
            else:
                self._append_prompt()
            self.lbl_ps1.configure(text=ps)
        except tk.TclError:
            pass

    def _drop_prompt(self) -> None:
        """切回输入框形态时把正文里的提示符行去掉（用户敲的内容搬进输入框）。"""
        try:
            if "prompt" not in self.text.mark_names():
                return
            self.text.delete("prompt", "end-1c")
        except tk.TclError:
            pass
        for m in ("prompt", "input_start"):
            try:
                self.text.mark_unset(m)
            except tk.TclError:
                pass

    def _get_input(self) -> str:
        try:
            if self.mode == "inline":
                return self.text.get("input_start", "end-1c")
        except tk.TclError:
            return ""
        return self.v_in.get()

    def _set_input(self, s: str) -> None:
        if self.mode == "inline":
            try:
                self.text.delete("input_start", "end-1c")
                self.text.insert("input_start", s)
                self.text.mark_set("insert", "end-1c")
                self.text.see("end")
            except tk.TclError:
                pass
        else:
            self.v_in.set(s)

    def _close_inline(self) -> None:
        """提交时收尾：给敲的那行上色、换行、再补一条新提示符。"""
        try:
            self.text.tag_add("cmd", "input_start", "end-1c")
            self.text.insert("end-1c", "\n")
            self._append_prompt()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 写入
    def _at_bottom(self) -> bool:
        try:
            return self.text.yview()[1] > 0.995
        except tk.TclError:
            return True

    def write(self, text: str, tag: str = "out") -> None:
        if not text:
            return
        # 唯一出口就在这里：旁路 feed()、手敲命令的 _result()、错误提示、
        # 历史回放全都汇到 write()，所以脱敏放在这一层，任何调用者都绕不过。
        text = scrub(text)
        stick = self._at_bottom()
        try:
            if self.mode == "inline" and "prompt" in self.text.mark_names():
                # 输出插在提示符之前：正在敲的字不会被冲掉，提示符被顶到下一行
                self.text.insert("prompt", text, tag)
            else:
                self.text.configure(state="normal")
                self.text.insert("end", text, tag)
                self.text.configure(state="disabled")
            # 超长就砍掉前 1/4，比每行都判断省事
            n = int(self.text.index("end-1c").split(".")[0])
            if n > MAX_LINES:
                self.text.delete("1.0", f"{n - MAX_LINES // 2}.0")
            if stick:
                self.text.see("end")
        except tk.TclError:
            pass

    def clear(self) -> None:
        try:
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            if self.mode != "inline":
                self.text.configure(state="disabled")
            else:
                self.text.mark_unset("prompt")
                self.text.mark_unset("input_start")
                self._append_prompt()
        except tk.TclError:
            pass

    def _banner(self) -> None:
        self.write(BANNER, "meta")
        self._refresh_head()
        if not self.ctrl.term_ready():
            self.write("当前未连接：先在主界面连上设备，这个终端才有地方执行命令。\n", "err")
        if self.mode == "inline":
            self._ensure_prompt()

    def _win_title(self, text: str) -> None:
        """设置窗口标题。嵌入形态没有标题栏，默认什么都不做。"""

    def _refresh_head(self) -> None:
        try:
            self._win_title(f"终端 · {self.ctrl.term_describe()}")
            self.lbl_dev.configure(text=self.ctrl.term_describe())
            if self.mode == "inline":
                self._reset_inline_prompt()
            else:
                self.lbl_ps1.configure(text=self._safe_prompt())
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 旁路
    def feed(self, display: str, rc: int, out: str, err: str, ms: int,
             by_api: bool = False) -> None:
        """主程序 / 智能体执行完一条命令后由 tracer 调进来。

        `by_api=True` 表示这条是智能体通过 HTTP API 发的（tracer 那边已经
        加了 `[API] ` 前缀）。两个开关的关系：
          「同步命令」是总线 —— 关掉它，人和智能体的都不显示；
          「同步 API」只管子集 —— 关掉它，自己敲的照常显示。
        """
        if not self._sync.get():
            return
        if by_api and not self._sync_api.get():
            return
        if self._own or self._is_own(display):
            return
        self.write(f"{time.strftime('%H:%M:%S')} ", "meta")
        # 回显里内联的口令打码；判定「是不是自己敲的」仍用原文比对
        self.write("▸ " + scrub_cmd(display) + "\n", "echo")
        if out.strip():
            self.write(out if out.endswith("\n") else out + "\n", "out")
        if err.strip():
            self.write(err if err.endswith("\n") else err + "\n", "err")
        if rc != 0:
            self.write(f"[rc={rc} {ms}ms]\n", "meta")

    # ------------------------------------------------------------ AI 活动流
    def ai_feed(self, lines) -> None:
        """把 AI 的活动逐行写进正文。

        lines 是 [(文本, 标签)]，由 core.aitrace 拼好，这里只负责落字。
        头部行前面插一个空行做分隔，否则连续两轮对话会粘成一坨分不清。
        用户在头部取消勾选「AI 活动」就整段静默 —— 但命令本身照跑，
        只是不回显，免得以为把 AI 关掉了。
        """
        if not self._ai_on.get():
            return
        rows = list(lines or [])
        if not rows:
            return
        for i, (text, tag) in enumerate(rows):
            if not text:
                continue
            self.write(text if text.endswith("\n") else text + "\n", tag)
            if i == 0:
                # 头部行之后再空一行，让「输入 / 输出 / 命令」三段落分明
                self.write("\n", "meta")

    def _is_own(self, display: str) -> bool:
        """这条旁路是不是自己刚敲的那条（只吞一次）。"""
        if not self._own_cmd:
            return False
        if time.monotonic() - self._own_ts > 8.0:
            self._own_cmd = ""
            return False
        if display.strip().endswith(self._own_cmd.strip()):
            self._own_cmd = ""
            return True
        return False

    # ------------------------------------------------------------ 输入
    def _focus_input(self) -> None:
        try:
            (self.text if self.mode == "inline" else self.entry).focus_set()
        except tk.TclError:
            pass

    def _hist_prev(self, _e=None):
        if not self._hist:
            return "break"
        self._hist_at = max(0, self._hist_at - 1)
        self._set_input(self._hist[self._hist_at])
        if self.mode != "inline":
            self.entry.icursor("end")
        return "break"

    def _hist_next(self, _e=None):
        if not self._hist:
            return "break"
        self._hist_at = min(len(self._hist), self._hist_at + 1)
        self._set_input("" if self._hist_at >= len(self._hist)
                        else self._hist[self._hist_at])
        if self.mode != "inline":
            self.entry.icursor("end")
        return "break"

    # ------------------------------------------------------------ Tab 补全
    def _completion_pool(self) -> list[str]:
        """补全候选：内建命令 + 历史命令（去重、保序）。

        远端文件/目录名要发命令查，补一次敲一回不划算，也不该伪造 —— 这里
        只补「指令」（命令名）。用户敲过的命令最该回来，所以 `_hist` 也算。
        """
        pool: list[str] = []
        seen: set[str] = set()
        for c in (*BUILTIN_CMDS, *self._hist):
            # 历史里存的是整行，取第一个词才是命令名
            word = str(c or "").strip().split()[0] if str(c or "").strip() else ""
            if word and word not in seen:
                seen.add(word)
                pool.append(word)
        return pool

    def _complete(self, _e=None):
        """Tab 补全当前正敲的那个词（行尾 token）。

        三条路，按顺序判：
          ① 第一个词 = 命令名 → 内建命令 + 历史（本地就有，立即出）；
          ② 命令是文件类命令（cd/ls/cat…）或这个词已经带 `/` → **远端路径**，
             发一条 `ls` 去问（异步，回来再填，查不到不伪造）；
          ③ 其它 → 还是本地命令池，不猜。

        · 唯一候选：补全到整个词；
        · 多个候选：补到公共前缀，并把候选列出来让用户再敲一个字；
        · 没候选：原样不动（绝不打断输入）。
        返回 "break" 阻止 Tab 把焦点挪到别的控件上去。
        """
        try:
            line = self._get_input()
        except tk.TclError:
            return "break"
        # 光标前的最后一个词：行尾若有空格，补的是「新词」（空前缀=列出全部）
        word = line.rsplit(" ", 1)[-1] if line else ""
        prefix = word.strip()

        # ② 路径补全：命令吃路径，或这个词本身就是路径
        kind, _cmd, _w = completion_kind(line)
        if kind == "path":
            self._complete_path(line, word)
            return "break"
        if kind == "none":
            return "break"

        # ① ③ 命令名补全（原来那套）
        try:
            pool = self._completion_pool()
        except tk.TclError:
            pool = []
        matches = [c for c in pool if c.startswith(prefix)]
        matches = list(dict.fromkeys(matches))   # 去重保序
        if not matches:
            return "break"
        if len(matches) == 1:
            done = matches[0]
        else:
            done = os.path.commonprefix(matches)
            self.write("  ".join(matches) + "\n", "meta")
        # 把行尾那个词换成补全结果，前面的命令/参数原样保留
        head = line[: len(line) - len(word)]
        self._set_input(head + done)
        if self.mode != "inline":
            self.entry.icursor("end")
        return "break"

    # ------------------------------------------------------------ 远端路径补全
    def _complete_path(self, line: str, word: str) -> None:
        """问设备要目录清单，回来只替换行尾那个词。

        三条规矩（原 `_TODO_20261121.md` 就写死了，别改）：
          · **发命令查**，不猜（本地没有远端目录的任何信息）；
          · **异步回填**，绝不阻塞输入；
          · **查不到就不动** —— 不伪造候选、不清空用户打的字、不弹窗。
        网络设备（交换机）没有 `ls`，直接不补，免得往 console 上扔垃圾命令。
        """
        try:
            ready = bool(self.ctrl.term_ready())
            kind = str(self.ctrl.term_host_kind() or "")
        except Exception:  # noqa: BLE001  套接字断了就当没连
            return
        if not ready or self._busy or kind == "network":
            return
        token = word
        dir_part, _name = split_dir_prefix(token)
        ls_dir = dir_part or "./"
        snapshot = line
        cache_key = (ls_dir, kind)
        hit = self._path_cache.get(cache_key)
        if hit and (time.monotonic() - hit[0]) < PATH_CACHE_TTL:
            self._apply_path(snapshot, token, hit[1])
            return

        def work():
            text = ""
            try:
                rc, out, _err = self.ctrl.term_capture(
                    path_list_cmd(kind, ls_dir), 8)
                if rc == 0:
                    text = out or ""
            except Exception:  # noqa: BLE001
                text = ""
            items = parse_ls_listing(token, text)
            if len(self._path_cache) > PATH_CACHE_MAX:
                self._path_cache.clear()
            self._path_cache[cache_key] = (time.monotonic(), items)
            self.post(lambda: self._apply_path(snapshot, token, items))

        threading.Thread(target=work, daemon=True).start()

    def _apply_path(self, snapshot: str, token: str, items: list[str]) -> None:
        """把候选填进输入框 —— 前提是用户没在这期间接着敲别的。"""
        if not items:
            return
        try:
            if self._get_input() != snapshot:
                return          # 用户已经改了，这次结果过期，丢掉（不抢输入）
        except tk.TclError:
            return
        head = snapshot[: len(snapshot) - len(token)]
        if len(items) == 1:
            done = items[0]
        else:
            done = os.path.commonprefix(items)
            if done == token:                   # 前缀没推进，别装作补全了
                self.write("  ".join(items) + "\n", "meta")
                return
        self._set_input(head + done)
        if self.mode != "inline":
            self.entry.icursor("end")

    def _sigint(self, _e=None):
        if self._busy:
            self._cancel = True
            self.write("^C  已放弃等待（设备上的命令可能还在跑）\n", "meta")
            self._finish()
        return "break"

    def _echo(self, line: str) -> None:
        self.write(self.ctrl.term_prompt(), "prompt")
        self.write(line + "\n", "cmd")

    def _submit(self, _e=None):
        if self._busy:
            return "break"
        if self.mode == "inline":
            # 行内：命令已经在正文里了，取走 + 收行，不再重复回显
            line = self.text.get("input_start", "end-1c").rstrip("\r\n")
            self._close_inline()
        else:
            line = self.v_in.get().rstrip("\r\n")
            self.v_in.set("")
            if not line.strip():
                self._echo("")
                return "break"
            self._echo(line)

        if not line.strip():
            return "break"

        safe = scrub(line)
        if not self._hist or self._hist[-1] != safe:
            self._hist.append(safe)
            if len(self._hist) > HIST_MAX:
                del self._hist[: len(self._hist) - HIST_MAX]
        self._hist_at = len(self._hist)

        low = line.strip().lower()
        if low in ("clear", "cls"):
            self.clear()
            return "break"
        if low in ("exit", "quit"):
            self.close()
            return "break"
        if low in ("help", "?"):
            self._help()
            return "break"
        if low == "history":
            for i, h in enumerate(self._hist, 1):
                self.write(f"{i:>4}  {h}\n", "meta")
            return "break"

        if not self.ctrl.term_ready():
            self.write("未连接：先回主界面连上设备。\n", "err")
            return "break"

        if low.startswith("cd ") or low == "cd":
            self._cd(line.strip()[3:].strip())
            return "break"

        self._busy = True
        self._cancel = False
        self._own = True
        self._own_cmd = line
        self._own_ts = time.monotonic()
        self.lbl_ps1.configure(fg=U.T.text_mute)

        def done(rc, out, err, ms):
            self.post(lambda: self._result(rc, out, err, ms))

        try:
            self.ctrl.term_run(line, done)
        except Exception as e:  # noqa: BLE001
            self._own = False
            self._busy = False
            self.write(f"执行失败：{e}\n", "err")

        return "break"

    def _result(self, rc, out, err, ms) -> None:
        self._own = False
        if self._cancel:
            self._finish()
            return
        if out:
            self.write(out if out.endswith("\n") else out + "\n", "out")
        if err:
            self.write(err if err.endswith("\n") else err + "\n", "err")
        if rc != 0:
            self.write(f"[rc={rc} {ms}ms]\n", "meta")
        self._finish()

    def _finish(self) -> None:
        self._busy = False
        self._own = False
        try:
            self.lbl_ps1.configure(fg=U.T.ok)
            self._refresh_head()
        except tk.TclError:
            pass
        self._focus_input()

    def _cd(self, path: str) -> None:
        if not path:
            self.write(self.ctrl.term_cwd() + "\n", "out")
            return
        ok, msg = self.ctrl.term_chdir(path)
        if ok:
            self._refresh_head()
        else:
            self.write(msg + "\n", "err")

    def _help(self) -> None:
        self.write(
            "内建命令（不发到设备）：\n"
            "  clear / cls   清屏        history   历史\n"
            "  cd <目录>     换目录       help      这条\n"
            "  exit / quit   关闭窗口\n"
            "其它一律原样发到当前会话执行。\n"
            "Ctrl+L 清屏，↑↓ 翻历史，Ctrl+C 放弃等待。\n"
            "Tab 补全：行首补命令（内建 + 历史）；cd/ls/cat 这类命令后面补"
            "**远端路径**\n"
            "（会发一条 ls 去问设备，查不到就不补，不会瞎猜）。\n",
            "meta")

    # ------------------------------------------------------------ 生命周期
    def close(self) -> None:
        try:
            self.ctrl.term_closed(self)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass

    # 主程序可能在别的线程里想推点东西进来
    def push(self, text: str, tag: str = "out") -> None:
        self.post(lambda: self.write(text, tag))


class TerminalWindow(_TermShell, tk.Toplevel):
    """独立终端窗口。

    `ctrl` 是主程序，需要提供四个方法：
        term_ready()           -> bool        当前是否可执行命令
        term_prompt()          -> str         提示符（含设备名 / 目录）
        term_describe()        -> str         窗口标题栏右侧那行字
        term_run(cmd, done)    -> None        异步执行，done(rc, out, err, ms)
    """

    def __init__(self, master, ctrl, mode: str | None = None,
                 title: str = "终端 · Terminal"):
        tk.Toplevel.__init__(self, master)
        self.configure(bg=U.T.page)
        self.title(title)
        self.geometry(f"{U.px(820)}x{U.px(520)}")
        self.minsize(U.px(560), U.px(300))
        self._init_term(ctrl, mode)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _win_title(self, text: str) -> None:
        try:
            self.title(text)
        except tk.TclError:
            pass


class TerminalPane(_TermShell, tk.Frame):
    """嵌在主界面页签里的终端。除了没有窗口边框，行为与独立窗口一致。"""

    def __init__(self, master, ctrl, mode: str | None = None):
        tk.Frame.__init__(self, master, bg=U.T.page, bd=0)
        self._init_term(ctrl, mode)

    def _win_title(self, text: str) -> None:
        # 嵌入形态的标题由页签负责，这里不抢
        pass
