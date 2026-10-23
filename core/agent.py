# -*- coding: utf-8 -*-
"""智能运维助手 —— 一个有「描述 / 提示词 / 技能 / 工具」四件套的运维智能体。

与 core/aiops.py 的分工：

  · aiops —— 模型接入（OpenAI 兼容 / dsh）、风险分级、JSON 方案解析。
  · agent —— 智能体本身：叫什么、干什么、用什么语气、能开哪些技能、能碰哪些工具，
             以及「模型 → 工具调用 → 真实结果回灌 → 再决策」这一层循环。

为什么不把工具直接交给模型的原生 function calling：这个工具箱连的是远端设备，
模型的原生工具默认跑在**本机**上，一不小心就在错的机器上执行了。所以工具一律
用 JSON 契约描述，由上层（pages/ai_dock.py）落地执行 —— 命令回到我们的会话通道，
界面动作回到主线程。这样「模型说」和「程序做」之间永远隔着一层可审计的边界。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from core import aiops

AGENT_NAME = "智能运维助手"


# ------------------------------------------------------------------ 工具注册表
@dataclass(frozen=True)
class ToolSpec:
    id: str
    label: str
    group: str
    desc: str
    arg_keys: tuple = ()
    risk: str = "read"          # read / write / danger（page_action 由页面注册表覆盖）
    sample: str = ""            # 给模型看的调用样例
    opt_keys: tuple = ()        # 可选参数，缺了也能跑

    @property
    def args(self) -> str:
        need = "、".join(self.arg_keys)
        if self.opt_keys:
            opt = "、".join(f"{k}(可选)" for k in self.opt_keys)
            return f"{need}；{opt}" if need else opt
        return need


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        "exec", "执行命令", "设备",
        "在目标设备上执行一条命令，返回真实 stdout / stderr / 退出码。"
        "命令按当前设备方言写：Linux 用 bash，Windows 用 cmd 或 PowerShell。"
        "不填 device 就在当前活动设备上跑；填了就跑到另一台已连接的设备上 —— "
        "跨机搬运时「读源机」这一步靠它。",
        ("cmd",), "read",
        '{"tool": "exec", "cmd": "tar czf - -C /etc nginx | base64 -w0",'
        ' "device": "web-2"}',
        ("device",)),
    ToolSpec(
        "device", "读取设备上下文", "设备",
        "取当前连接的设备名、协议、系统版本、是否提权、以及最近的命令记录。"
        "动手前不确定对面是什么机器时，先调它。",
        (), "read", '{"tool": "device"}'),
    ToolSpec(
        "relay", "跨设备中转文件", "设备",
        "把一台设备上的文件搬到另一台：源机读出来 → 经本机内存 → 写进目标机。"
        "两台机器不需要互相能连，只要本机两边都连着。"
        "适合配置、证书这类小文件，单文件上限 4 MB；两端都写绝对路径。",
        ("src", "src_path", "dst", "dst_path"), "write",
        '{"tool": "relay", "src": "web-1", "src_path": "/etc/nginx/nginx.conf",'
        ' "dst": "web-2", "dst_path": "/etc/nginx/nginx.conf"}'),
    ToolSpec(
        "pages", "列出页面与动作", "界面",
        "列出主界面所有选项卡，以及每页可以触发的动作 id。"
        "想操作界面时先调这个，不要凭印象猜动作名。",
        (), "read", '{"tool": "pages"}'),
    ToolSpec(
        "open_page", "切换页面", "界面",
        "把主界面切到某个选项卡，人就能看到你正在看的那一页。",
        ("page",), "read", '{"tool": "open_page", "page": "sec"}'),
    ToolSpec(
        "page_action", "触发页面动作", "界面",
        "在某个选项卡上「点一下按钮」，例如让安全中心跑一次体检、让数据库页重新探测实例。"
        "动作 id 由 pages 工具给出。",
        ("page", "action"), "write",
        '{"tool": "page_action", "page": "sec", "action": "scan"}'),
    ToolSpec(
        "page_read", "读取页面数据", "界面",
        "把某个选项卡当前表格里的行读成文本，用来核对界面上的真实结果。"
        "页面还没加载过的话，先 open_page 或 page_action 让它加载。",
        ("page",), "read", '{"tool": "page_read", "page": "cron"}'),
]

TOOLS: dict[str, ToolSpec] = {t.id: t for t in TOOL_SPECS}
GROUPS: tuple = ("设备", "界面")
DEFAULT_TOOLS: list[str] = [t.id for t in TOOL_SPECS]


# ------------------------------------------------------------------ 默认提示词
# 用占位符替换（不是 str.format），所以正文里的 JSON 大括号照原样写单花括号就行。
DEFAULT_PROMPT = """你是「{name}」，一个跑在终端工具箱里的运维智能体。
{desc}

你能用的工具（只能通过它们与外界交互，不要假装调用没列出来的工具）：
{tools}

当前设备上下文（程序注入，是真实现状，不要怀疑它）：
{context}

工作方式：
1. 一次只做一小步：先只读取证，再动手改；改完再看一眼结果。
2. 要看设备上的东西就 exec 跑只读命令；要看界面上的东西就 open_page / page_read。
3. 命令必须是目标设备上能直接执行的一行，不带 sudo 前缀（提权由程序处理）。
4. 删除、重启、清空、覆盖这类回滚成本高的动作，risk 标 danger，why 里写清后果。
5. 用户只是问概念、不需要动作时，直接给结论，calls 返回空数组。
6. 不确定就先查，不要编造命令输出；拿到真实输出再下结论。

只输出一个 JSON 对象，不要代码围栏、不要前后废话：
{"analysis": "不超过 120 字的判断或下一步理由",
 "done": false,
 "calls": [{"tool": "exec", "cmd": "df -h", "why": "先看磁盘水位"}]}
done 为 true 表示任务已完成（这时 calls 必须是空数组）。"""

DEFAULT_DESC = ("能直接操作这台设备：读系统状态、查日志、改配置、跑体检，"
                "也能替你翻到对应选项卡。危险动作一律先问过你。")


def tool_catalog(enabled: list[str] | None = None) -> str:
    """把启用的工具拼成给模型看的清单。enabled 为 None 表示全开。"""
    ids = DEFAULT_TOOLS if enabled is None else [i for i in enabled if i in TOOLS]
    if not ids:
        return "（本轮没有启用任何工具，只能用已有知识回答，不要提出任何操作）"
    out: list[str] = []
    for g in GROUPS:
        rows = [TOOLS[i] for i in ids if TOOLS[i].group == g]
        if not rows:
            continue
        out.append(f"【{g}】")
        for t in rows:
            arg = f"　参数：{t.args}" if t.arg_keys else "　无参数"
            out.append(f"- {t.id}（{t.label}）{t.desc}{arg}")
            if t.sample:
                out.append(f"  例：{t.sample}")
    return "\n".join(out)


def known_pages_hint(pages: list[str]) -> str:
    """页面清单塞进提示词，省得模型每次都要先调 pages 工具。"""
    return "、".join(pages) if pages else "（主界面暂未就绪）"


# ------------------------------------------------------------------ 配置
DEFAULTS: dict = {
    "name": AGENT_NAME,
    "desc": DEFAULT_DESC,
    "prompt": DEFAULT_PROMPT,
    "skills": [],          # 空 = 自动按关键词挑
    "tools": list(DEFAULT_TOOLS),
    "mode": "auto",
    "max_steps": 8,
    "dock_open": False,
    "dock_width": 400,
}


class AgentConfig:
    """data/agent.json。只有配置，不含任何密钥（Key 在 ai.json 里）。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = json.loads(json.dumps(DEFAULTS))  # 深拷贝，别共享列表
        self.load()

    # -- 落盘
    def load(self) -> None:
        try:
            if self.path.is_file():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if k in DEFAULTS:
                            self.data[k] = v
        except (OSError, ValueError):
            pass
        self._clean()

    def save(self) -> None:
        self._clean()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def _clean(self) -> None:
        """把外部改坏的字段收拾成能用的值 —— 配置坏了不该让助手打不开。"""
        name = str(self.data.get("name") or "").strip()
        self.data["name"] = name or AGENT_NAME
        if not str(self.data.get("desc") or "").strip():
            self.data["desc"] = DEFAULT_DESC
        if not str(self.data.get("prompt") or "").strip():
            self.data["prompt"] = DEFAULT_PROMPT
        tools = self.data.get("tools")
        if not isinstance(tools, list):
            tools = list(DEFAULT_TOOLS)
        self.data["tools"] = [t for t in tools if t in TOOLS]
        skills = self.data.get("skills")
        self.data["skills"] = [str(s) for s in skills] if isinstance(skills, list) else []
        if self.data.get("mode") not in ("auto", "confirm", "readonly"):
            self.data["mode"] = "auto"
        try:
            steps = int(self.data.get("max_steps") or 8)
        except (TypeError, ValueError):
            steps = 8
        self.data["max_steps"] = max(1, min(30, steps))
        try:
            w = int(self.data.get("dock_width") or 400)
        except (TypeError, ValueError):
            w = 400
        self.data["dock_width"] = max(320, min(760, w))
        self.data["dock_open"] = bool(self.data.get("dock_open"))

    # -- 便捷
    @property
    def name(self) -> str:
        return str(self.data.get("name") or AGENT_NAME)

    @property
    def desc(self) -> str:
        return str(self.data.get("desc") or "")

    @property
    def tools(self) -> list[str]:
        return list(self.data.get("tools") or [])

    @property
    def skills(self) -> list[str]:
        return list(self.data.get("skills") or [])

    @property
    def mode(self) -> str:
        return str(self.data.get("mode") or "auto")

    @property
    def max_steps(self) -> int:
        return int(self.data.get("max_steps") or 8)

    def tool_on(self, tid: str) -> bool:
        return tid in self.tools


# ------------------------------------------------------------------ 步骤解析
def _arg_value(item: dict, key: str) -> str:
    v = item.get(key)
    if v is None:
        alias = {"cmd": ("command", "shell"), "page": ("tab", "page_key"),
                 "action": ("act", "op")}.get(key, ())
        for a in alias:
            if item.get(a) is not None:
                v = item.get(a)
                break
    return str(v).strip() if v is not None else ""


def normalize_calls(items, allowed: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """把模型给的 calls 收拾成 [{'tool','args','why','risk'}]，返不出来的记进 bad。"""
    ok_ids = set(allowed) if allowed is not None else set(TOOLS)
    calls: list[dict] = []
    bad: list[str] = []
    for item in (items or [])[:8]:
        if isinstance(item, str):
            item = {"tool": "exec", "cmd": item}
        if not isinstance(item, dict):
            bad.append(str(item)[:60])
            continue
        tid = str(item.get("tool") or item.get("name") or "").strip()
        if not tid and _arg_value(item, "cmd"):
            tid = "exec"          # 老格式 {"cmd": ...} 当作 exec
        if tid not in TOOLS:
            bad.append(tid or "未指定工具")
            continue
        if tid not in ok_ids:
            bad.append(f"{tid}（本轮未启用）")
            continue
        spec = TOOLS[tid]
        args: dict = {}
        for k in spec.arg_keys:
            v = _arg_value(item, k)
            if v:
                args[k] = v
        for k in spec.opt_keys:
            v = _arg_value(item, k)
            if v:
                args[k] = v
        missing = [k for k in spec.arg_keys if k not in args]
        if missing:
            bad.append(f"{tid} 缺参数 {'/'.join(missing)}")
            continue
        calls.append({
            "tool": tid,
            "args": args,
            "why": str(item.get("why") or item.get("reason") or "").strip()[:200],
            "risk": str(item.get("risk") or "").strip().lower(),
        })
    return calls, bad


def default_risk(call: dict) -> str:
    """没有页面注册表时按工具自身估计风险。exec 走命令规则。"""
    tid = call.get("tool")
    if tid == "exec":
        return aiops.classify_risk(call.get("args", {}).get("cmd", ""))
    spec = TOOLS.get(tid or "")
    return spec.risk if spec else "read"


def parse_step(text: str, allowed: list[str] | None = None) -> dict:
    """解析模型一步输出：analysis + done + calls。

    解析不出来就把整段当结论，calls 留空 —— 绝不猜命令。
    """
    raw = (text or "").strip()
    if not raw:
        return {"analysis": "", "calls": [], "done": False, "raw": "", "bad": [],
                "note": "模型返回为空"}
    body = raw
    m = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if m:
        body = m.group(1).strip()
    obj = aiops._first_json_object(body)
    if not isinstance(obj, dict):
        return {"analysis": raw[:2000], "calls": [], "done": False, "raw": raw,
                "bad": [], "note": "没解析出 JSON，整段当结论"}
    analysis = str(obj.get("analysis") or obj.get("answer") or "").strip()
    done = bool(obj.get("done") or obj.get("finished") or False)
    payload = obj.get("calls")
    if payload is None:
        payload = obj.get("tools")
    if payload is None:
        payload = obj.get("commands")      # 兼容旧格式
    calls, bad = normalize_calls(payload or [], allowed)
    note = ""
    if bad:
        note = "丢弃的调用：" + "、".join(bad[:4])
    return {"analysis": analysis, "calls": calls, "done": done,
            "raw": raw, "bad": bad, "note": note}


# ------------------------------------------------------------------ 智能体
class Agent:
    """模型 + 工具 = 一个能自己往前走的运维助手。执行权始终在界面层。"""

    def __init__(self, cfg: AgentConfig, llm: aiops.AIConfig):
        self.cfg = cfg
        self.llm = llm
        self.history: list[dict] = []
        self.tokens = 0
        self.last_usage: dict = {}
        self.last_answer = ""

    # -- 提示词
    def system_prompt(self, context: str = "", skill_text: str = "") -> str:
        tpl = str(self.cfg.data.get("prompt") or DEFAULT_PROMPT)
        text = (tpl.replace("{name}", self.cfg.name)
                   .replace("{desc}", self.cfg.desc)
                   .replace("{tools}", tool_catalog(self.cfg.tools))
                   .replace("{context}", context or "未连接（本机）"))
        if skill_text:
            text += "\n\n本轮套用的技能（照它的步骤走）：\n" + skill_text
        return text

    def build_messages(self, goal: str, context: str, transcript: list[str],
                       skill_text: str = "") -> list[dict]:
        msgs = [{"role": "system",
                 "content": self.system_prompt(context, skill_text)}]
        for h in self.history[-8:]:
            role = h.get("role")
            if role in ("user", "assistant") and h.get("content"):
                msgs.append({"role": role, "content": str(h["content"])[:4000]})
        msgs.append({"role": "user", "content": f"任务：{goal}"})
        if transcript:
            msgs.append({"role": "user",
                         "content": "你已经执行过的动作与真实结果：\n"
                                    + "\n".join(transcript[-14:])})
        else:
            msgs.append({"role": "user",
                         "content": "还没有执行任何动作，请给出第一步。"})
        return msgs

    def reset(self) -> None:
        self.history.clear()
        self.tokens = 0
        self.last_usage = {}
        self.last_answer = ""

    # -- 主循环
    def run(self, goal: str, execute, *, context: str = "", skill_text: str = "",
            confirm=None, on_event=None, should_stop=None,
            risk_of=None, out_limit: int = 2400,
            poll_inject=None) -> dict:
        """跑一轮任务。

        execute(tool_id, args) -> (ok: bool, text: str)   由界面层提供，负责真正落地
        confirm(calls: list[dict]) -> bool                 需要人点头时调用
        on_event(kind, payload)                            见下方 emit
        risk_of(call) -> str                              页面动作的真实风险由界面给
        poll_inject() -> list[str]                         中途插话：每步开始前取一次

        插话走的是**同一轮**：取到的句子作为一条 user 消息追加到 history，
        下一步的 messages 里自然就带上了。绝不允许并发跑第二个 run ——
        transcript 是这一轮的现场，另起一轮等于把它扔掉。
        """
        emit = on_event or (lambda *_a, **_k: None)
        stop = should_stop or (lambda: False)
        risk_fn = risk_of or default_risk
        steps = 0
        ran: list[str] = []
        refused: list[str] = []
        skipped: list[str] = []
        injected: list[str] = []
        transcript: list[str] = []
        stopped = False
        answer = ""
        max_steps = max(1, self.cfg.max_steps)
        allowed = self.cfg.tools
        mode = self.cfg.mode

        for step in range(1, max_steps + 1):
            if stop():
                stopped = True
                emit("stopped", {"step": step})
                break
            # 用户在这期间补的话：先进 history，再让模型看到
            if poll_inject is not None:
                try:
                    extra = poll_inject() or []
                except Exception:  # noqa: BLE001  队列坏了不该毁掉这一轮
                    extra = []
                for text in extra:
                    t = str(text or "").strip()
                    if not t or t in injected:
                        continue
                    injected.append(t)
                    self.history.append(
                        {"role": "user",
                         "content": f"（用户在你执行期间补充）{t}"})
                    emit("inject", {"text": t, "step": step})
            steps = step
            try:
                text, usage = aiops.make_engine(self.llm).chat(
                    self.build_messages(goal, context, transcript, skill_text))
            except Exception as e:  # noqa: BLE001  网络/配置错都收在这里
                msg = str(e) if isinstance(e, aiops.AIError) else f"{type(e).__name__}: {e}"
                emit("error", {"step": step, "msg": msg})
                return {"ok": False, "error": msg, "steps": step, "ran": ran,
                        "refused": refused, "skipped": skipped,
                        "injected": injected, "answer": answer}
            self.tokens += int(usage.get("total") or 0)
            self.last_usage = usage

            st = parse_step(text, allowed)
            answer = st.get("analysis") or answer
            emit("plan", {"step": step, "analysis": st.get("analysis", ""),
                          "done": bool(st.get("done")), "note": st.get("note", ""),
                          "usage": usage})
            calls = st.get("calls") or []
            if not calls:
                emit("finish", {"step": step,
                                "reason": "模型没有给出动作" if not st.get("done")
                                          else "模型判断已完成",
                                "analysis": st.get("analysis", "")})
                break

            for call in calls:
                if stop():
                    stopped = True
                    emit("stopped", {"step": step})
                    break
                risk = call.get("risk") or risk_fn(call)
                if risk not in aiops.RISK_LABEL:
                    risk = risk_fn(call)
                else:
                    # 模型自报只读但规则更严时，以严的为准
                    auto = risk_fn(call)
                    order = {"read": 0, "write": 1, "danger": 2}
                    if order.get(auto, 0) > order.get(risk, 0):
                        risk = auto
                call["risk"] = risk

                if mode == "readonly" and risk != "read":
                    skipped.append(_call_brief(call))
                    emit("skip", {"call": call, "why": "只读模式下不执行写操作"})
                    continue
                need_ask = risk == "danger" or (mode == "confirm" and risk != "read")
                if need_ask:
                    emit("need_confirm", {"call": call})
                    if not (confirm(call) if confirm else False):
                        refused.append(_call_brief(call))
                        emit("refused", {"call": call})
                        continue

                emit("call", {"call": call, "step": step})
                try:
                    ok, out = execute(call["tool"], dict(call.get("args") or {}))
                except Exception as e:  # noqa: BLE001
                    ok, out = False, f"{type(e).__name__}: {e}"
                text_out = (out or "").strip() or "(无输出)"
                ran.append(_call_brief(call))
                emit("result", {"call": call, "ok": bool(ok),
                                "text": _clip(text_out, out_limit)})
                transcript.append(_transcript_line(call, ok, text_out, out_limit))

            if stopped:
                break
            if st.get("done"):
                emit("finish", {"step": step, "reason": "模型判断已完成",
                                "analysis": st.get("analysis", "")})
                break
        else:
            emit("finish", {"step": steps, "reason": "达到步数上限", "analysis": ""})

        self.last_answer = answer
        self.history.append({"role": "user", "content": goal})
        self.history.append({"role": "assistant", "content": answer[:1200]})
        return {"ok": True, "steps": steps, "ran": ran, "refused": refused,
                "skipped": skipped, "stopped": stopped, "injected": injected,
                "answer": answer}


def _call_brief(call: dict) -> str:
    tid = call.get("tool") or "?"
    args = call.get("args") or {}
    spec = TOOLS.get(tid)
    label = spec.label if spec else tid
    if tid == "exec":
        return f"{label}：{args.get('cmd', '')}"
    if args:
        return f"{label}：{'/'.join(str(v) for v in args.values())}"
    return label


def _transcript_line(call: dict, ok: bool, out: str, limit: int) -> str:
    """回灌给模型的单行记录。带工具名与成败，方便它判断下一步。"""
    tid = call.get("tool") or "?"
    args = call.get("args") or {}
    head = _call_brief(call)
    tag = "ok" if ok else "FAILED"
    body = _clip(out, limit)
    if tid == "exec":
        return f"$ {args.get('cmd', '')}  [{tag}]\n{body}"
    return f"[{tid}] {args}  [{tag}]\n{body}"


def _clip(text: str, limit: int) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t or "(无输出)"
    head = t[: int(limit * 0.7)]
    tail = t[-int(limit * 0.25):]
    return f"{head}\n…（省略 {len(t) - len(head) - len(tail)} 字）…\n{tail}"
