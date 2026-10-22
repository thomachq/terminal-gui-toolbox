# -*- coding: utf-8 -*-
"""AI 活动在终端里的回显格式。

用户的原话是两句：

  「AI 执行的命令会实时显示在终端，同时终端还能看到输出」
  「即使是智能体调用 API，终端也能看到智能体输入了什么、输出了什么」

所以终端里要出现的不只是远端命令，还有模型这一层的请求与回复。
分三类行：

  API  ⇢  发出去的请求（地址、模型、几条消息、每条的首行）
  API  ⇠  回来的响应（状态码、耗时、token、正文首段）
  CMD  ▶  落在设备上的命令；随后是真实输出；最后一行 rc

为什么单独抽一个模块：这里是**纯字符串拼装**，不碰 Tk、不碰网络、不碰会话。
界面那层只负责把 (文本, 颜色标签) 塞进 Text；格式错了在这一层就能测出来，
不用起窗口、不用连机器。

长度一律按字截断，不做列对齐 —— 终端里中英文混排，按字符数补空格对不齐，
宁可每段之间只留两个空格。
"""
from __future__ import annotations

import re
import time

# 一行里最多留多少字。超了打断，避免一条 system prompt 刷满整屏
HEAD_MAX = 96          # 请求里每条消息的首行
BODY_MAX = 320         # 响应正文首段
ERR_MAX = 200          # 报错正文

TAG_HEAD = "aihead"    # 时间 + 方向箭头：强调
TAG_BODY = "aidim"     # 正文摘录：退后一层
TAG_CMD = "echo"       # 命令回显（与主程序命令同一个颜色）
TAG_META = "meta"
TAG_OUT = "out"
TAG_ERR = "err"


def ts(t: float | None = None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t if t else time.time()))


def _one_line(s: str) -> str:
    """把多行压成一行：换行折成 ⏎，连续空白折成一个空格。"""
    s = str(s or "").replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\n", " ⏎ ")
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def clip(s: str, n: int) -> str:
    t = _one_line(s)
    if len(t) <= n:
        return t
    return t[: max(0, n - 1)] + "…"


def human_size(n: int) -> str:
    """消息体积。给人看的，不追求精确到字节。"""
    n = int(n or 0)
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / 1024 / 1024:.1f}MB"


def human_ms(ms) -> str:
    try:
        v = float(ms)
    except (TypeError, ValueError):
        return "?"
    if v < 1000:
        return f"{int(v)}ms"
    return f"{v / 1000:.1f}s"


def short_host(base: str) -> str:
    """把 base 地址剥成 `域名/路径`，否则一行里光 URL 就占满了。"""
    b = str(base or "").strip()
    b = re.sub(r"^[a-zA-Z]+://", "", b)
    b = b.rstrip("/")
    return b or "(未配置地址)"


def _tokens_total(tokens) -> int:
    """各家字段名不一样：OpenAI 系是 total_tokens，我们自己的 usage 用 total，
    dsh 干脆没有。取不到就加出来，加不出来就不显示 —— 不编数字。"""
    if isinstance(tokens, dict):
        for k in ("total", "total_tokens"):
            try:
                if tokens.get(k):
                    return int(tokens[k])
            except (TypeError, ValueError):
                pass
        try:
            return int(tokens.get("prompt_tokens") or 0) + int(
                tokens.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            return 0
    try:
        return int(tokens or 0)
    except (TypeError, ValueError):
        return 0


def api_request(*, base: str = "", model: str = "", messages=None,
                provider: str = "", t: float | None = None) -> list[tuple[str, str]]:
    """一次模型调用的「输入」。messages 是 OpenAI 格式的 list[dict]。"""
    msgs = list(messages or [])
    chars = sum(len(str(m.get("content") or "")) for m in msgs)
    head = (f"[{ts(t)}] AI ⇢ POST {short_host(base)}/chat/completions"
            f"  ·  {model or '(无模型名)'}"
            f"  ·  {len(msgs)} 条消息 {human_size(chars * 2)}")
    lines = [(head, TAG_HEAD)]
    for m in msgs:
        role = str(m.get("role") or "?")
        body = clip(m.get("content") or "", HEAD_MAX)
        if body:
            lines.append((f"        {role:<9} {body}", TAG_BODY))
    if provider:
        lines.append((f"        provider {provider}", TAG_BODY))
    return lines


def api_response(*, ok: bool, ms=None, code: int | str = "", tokens=None,
                 text: str = "", err: str = "", t: float | None = None
                 ) -> list[tuple[str, str]]:
    """一次模型调用的「输出」。成功给正文摘录，失败给原因。"""
    if ok:
        tk = ""
        total = _tokens_total(tokens)
        if total:
            tk = f"  ·  {total} tokens"
        n = len(str(text or ""))
        head = (f"[{ts(t)}] AI ⇠ {code or 200}  {human_ms(ms)}{tk}"
                f"  ·  回复 {n} 字")
        lines = [(head, TAG_HEAD)]
        if str(text or "").strip():
            lines.append((f"        {clip(text, BODY_MAX)}", TAG_BODY))
    else:
        head = f"[{ts(t)}] AI ⇠ 失败  {human_ms(ms)}"
        lines = [(head, TAG_ERR)]
        msg = clip(err or "没有返回内容", ERR_MAX)
        if msg:
            lines.append((f"        {msg}", TAG_ERR))
    return lines


def cmd(*, host: str = "", command: str = "", risk: str = "",
        t: float | None = None) -> list[tuple[str, str]]:
    """落到设备上的一条命令。执行前就写，让用户立刻看到 AI 在干什么。"""
    who = host or "本机"
    flag = " ⚠" if risk == "danger" else ""
    return [(f"[{ts(t)}] AI ▶ {who} $ {command}{flag}", TAG_CMD)]


def result(*, rc: int = 0, ms=None, out: str = "", err: str = "",
           t: float | None = None) -> list[tuple[str, str]]:
    """命令输出。文本原样给，末行补 rc —— 输出自带颜色标签由调用方分段写。"""
    lines = [(f"[{ts(t)}] AI ■ rc={rc}  {human_ms(ms)}"
              + (f"  ·  {len((out or '').strip().splitlines())} 行" if out else ""),
              TAG_META if rc == 0 else TAG_ERR)]
    return lines


def note(text: str, *, tone: str = "meta", t: float | None = None
         ) -> list[tuple[str, str]]:
    """杂项：等确认、被拒、出错、收尾。"""
    tag = {"ok": TAG_META, "err": TAG_ERR, "warn": "warn"}.get(tone, TAG_META)
    return [(f"[{ts(t)}] AI · {clip(text, BODY_MAX)}", tag)]


def banner() -> list[tuple[str, str]]:
    return [
        ("", TAG_META),
        ("── AI 活动流 ─────────────────────────────────────────────", TAG_HEAD),
        ("AI 在这里做的事都会逐条落到这个终端里：", TAG_BODY),
        ("  AI ⇢  发给模型的内容（地址 / 模型 / 每条消息首行）", TAG_BODY),
        ("  AI ⇠  模型回来的内容（状态 / 耗时 / token / 回复首段）", TAG_BODY),
        ("  AI ▶  落到设备上的命令，下面紧跟真实输出与 rc", TAG_BODY),
        ("不需要的话，取消勾选顶部「AI 活动」即可。", TAG_BODY),
    ]


def summarize(events: list[dict]) -> str:
    """把一串事件压成一行字，测试与状态栏用。"""
    n_api = sum(1 for e in events if e.get("kind") == "api")
    n_cmd = sum(1 for e in events if e.get("kind") == "cmd")
    n_bad = sum(1 for e in events if e.get("ok") is False)
    return f"{n_api} 次模型调用 · {n_cmd} 条命令 · {n_bad} 个失败"
