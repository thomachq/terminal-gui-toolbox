# -*- coding: utf-8 -*-
"""AI 运维引擎：把自然语言变成「待确认的命令」，执行权留在人手里。

两件事要分清楚：

  1. 模型本身 —— 走 OpenAI 兼容接口（DeepSeek 官方 / Ollama / LM Studio / 任何自建网关），
     或者走本机的 DeepSeek Harness（`dsh --profile headless`）。配置存在 data/ai.json，
     API Key 用 crypto_store 加密后落盘，不写明文。
  2. 命令执行 —— **不交给模型**。模型只输出 JSON 方案（分析 + 命令列表 + 风险等级），
     由界面逐条「批准 / 拒绝」，批准后才用现有会话通道（SSH/Telnet/本机）跑。

为什么不直接把工具交给模型：这个工具箱连的是远端设备，模型的工具默认跑在**本机**上，
一不小心就在错误的机器上执行了。所以命令必须回到我们的 session 层跑。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import crypto_store  # 与口令保存同一套加密
except Exception:  # noqa: BLE001  单独跑测试时可能没有
    crypto_store = None  # type: ignore[assignment]


# ------------------------------------------------------------------ 服务商预设
PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek 官方",
        "kind": "openai",
        "base": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "need_key": True,
        "hint": "api.deepseek.com，Key 在官网申请",
    },
    "dsh": {
        "label": "本机 DeepSeek Harness",
        "kind": "dsh",
        "base": "",
        "model": "dsh-headless",
        "need_key": False,
        "hint": "调本机 dsh --profile headless，用 Harness 自己配好的模型",
    },
    "ollama": {
        "label": "Ollama（本机）",
        "kind": "openai",
        "base": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5-coder:14b",
        "need_key": False,
        "hint": "本机 Ollama 默认端口 11434",
    },
    "lmstudio": {
        "label": "LM Studio（本机）",
        "kind": "openai",
        "base": "http://127.0.0.1:1234/v1",
        "model": "local-model",
        "need_key": False,
        "hint": "LM Studio 打开 Local Server 后可用",
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "kind": "openai",
        "base": "",
        "model": "",
        "need_key": True,
        "hint": "任何 OpenAI 兼容网关：填 base（含 /v1）、模型名、Key",
    },
}

DEFAULT_PROVIDER = "deepseek"


# ------------------------------------------------------------------ 风险分级
# 只影响界面上的默认勾选与二次确认，不阻止执行——最终决定权在人。
_DANGER = [
    r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+/(?:\s|$)",
    r"\brm\s+-rf\b",
    r"\bmkfs(\.\w+)?\b", r"\bdd\s+.*of=/dev/", r"\bwipefs\b", r"\bshred\b",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\bpoweroff\b", r"\binit\s+0\b",
    r"\buserdel\b", r"\bgroupdel\b", r"\bpasswd\b", r"\bchpasswd\b",
    r"\biptables\s+-F\b", r"\bnft\s+flush\b", r"\bufw\s+disable\b",
    r">\s*/etc/", r"\btruncate\b.*-s\s*0", r"\bchmod\s+-R\s+777\b",
    r"\bkill\s+-9\s+1\b", r":\(\)\s*\{", r"\bcurl\b[^|]*\|\s*(ba|z)?sh\b",
    r"\bformat\s+[a-zA-Z]:", r"\bdiskpart\b", r"\bbcdedit\b",
    r"\bRemove-Item\b.*-Recurse.*-Force",
]
_WRITE = [
    r"\b(apt|apt-get|yum|dnf|pacman|zypper|apk)\s+(install|remove|purge|upgrade|update)\b",
    r"\bpip3?\s+install\b", r"\bnpm\s+(i|install|uninstall)\b",
    r"\bsystemctl\s+(start|stop|restart|enable|disable|mask)\b",
    r"\bservice\s+\S+\s+(start|stop|restart)\b",
    r"\bdocker\s+(run|rm|rmi|stop|start|restart|prune|exec)\b",
    r"\b(?:sed|perl)\s+-i\b", r"\btee\b", r"\bmv\b", r"\bcp\b", r"\brm\b",
    r"\bmkdir\b", r"\btouch\b", r"\bchmod\b", r"\bchown\b", r"\bln\b",
    r"\buseradd\b", r"\busermod\b", r"\bcrontab\b", r"\bsetfacl\b",
    r"\biptables\b", r"\bfirewall-cmd\b", r"\bnft\b",
    r"\bsc\s+(config|start|stop|delete)\b", r"\bnet\s+(start|stop)\b",
    r"\bschtasks\b", r"\breg\s+add\b", r"\bSet-Service\b", r"\bRestart-Service\b",
]
_DANGER_RE = [re.compile(p, re.I) for p in _DANGER]
_WRITE_RE = [re.compile(p, re.I) for p in _WRITE]


def classify_risk(cmd: str) -> str:
    """read / write / danger。宁可错判成危险，也不要错判成只读。"""
    c = (cmd or "").strip()
    if not c:
        return "read"
    for r in _DANGER_RE:
        if r.search(c):
            return "danger"
    for r in _WRITE_RE:
        if r.search(c):
            return "write"
    return "read"


RISK_LABEL = {"read": "只读", "write": "会改动", "danger": "高风险"}
RISK_TONE = {"read": "ok", "write": "warn", "danger": "err"}


# ------------------------------------------------------------------ 提示词
SYSTEM_PROMPT = """你是终端运维助手，帮助用户在远端设备上排查和操作。
你只能**提出命令**，不能执行；用户会逐条确认后由程序代为执行。

当前设备上下文（程序注入，是真实现状）：
{context}

输出要求（严格遵守）：
1. 只输出一个 JSON 对象，不要加代码围栏、不要加解释性前后缀。
2. 结构：
{{"analysis": "不超过 120 字的判断", "commands": [{{"cmd": "单行命令", "why": "为什么这样查/改", "risk": "read|write|danger"}}, ...]}}
3. 命令必须是**目标设备上可直接执行**的单条命令，按设备方言写（Linux 用 bash，
   Windows 用 cmd/PowerShell，网络设备用厂商 CLI）。不要写 sudo 前缀，提权由程序处理。
4. 只读排查优先：先给最小代价的只读命令，不要一上来就改配置。
5. 涉及删除、重启、清空、覆盖的回滚成本高的操作，risk 标 danger，并在 why 里写清后果。
6. 如果用户的请求不需要执行命令（只是问概念），commands 返回空数组，把答案写在 analysis。
"""


SYSTEM_PROMPT_TASK = """你是终端运维助手，正在**连续执行**一个任务：你提出命令，程序在目标设备上执行，
然后把真实输出回给你，你据此决定下一步，直到任务完成。

当前设备上下文（程序注入，是真实现状）：
{context}

{skill}

输出要求（严格遵守）：
1. 只输出一个 JSON 对象，不要代码围栏、不要解释性前后缀。
2. 结构：
{{"analysis": "不超过 120 字的判断或下一步理由",
 "done": false,
 "commands": [{{"cmd": "单行命令", "why": "为什么", "risk": "read|write|danger"}}]}}
3. `done` 为 true 表示任务已完成或无法继续（这时 commands 必须为空数组）。
4. 每轮最多 6 条命令，宁少勿多 —— 你要看输出再决定下一步。
5. 命令必须是目标设备上可直接执行**单行**命令（不含 sudo 前缀，提权由程序处理）。
6. 先只读查证再动手；不要假设上一条命令的结果，等程序回给你真实输出。
7. 不要重复已经执行过的同一条命令；输出为空就换个角度查。
8. 涉及删除、重启、清空、覆盖的，risk 标 danger，why 里写清后果。
"""


def build_context(device: dict | None = None, recent: list[str] | None = None) -> str:
    d = device or {}
    lines = []
    name = d.get("name") or d.get("host") or "未连接（本机）"
    lines.append(f"设备：{name}")
    if d.get("proto"):
        lines.append(f"连接方式：{d.get('proto')}")
    if d.get("os"):
        lines.append(f"系统：{d.get('os')}")
    if d.get("host_kind"):
        lines.append(f"设备类别：{d.get('host_kind')}")
    if d.get("vendor"):
        lines.append(f"厂商/方言：{d.get('vendor')}")
    if d.get("admin"):
        lines.append(f"提权：{d.get('admin')}")
    if recent:
        tail = [x for x in recent if x.strip()][-8:]
        if tail:
            lines.append("最近的命令与输出片段：")
            for t in tail:
                lines.append("  " + t.replace("\n", " ")[:220])
    return "\n".join(lines) if lines else "未连接（本机）"


def build_messages(question: str, context: str,
                   history: list[dict] | None = None) -> list[dict]:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT.format(context=context)}]
    for h in (history or [])[-8:]:
        role = h.get("role")
        if role in ("user", "assistant") and h.get("content"):
            msgs.append({"role": role, "content": str(h["content"])[:4000]})
    msgs.append({"role": "user", "content": question})
    return msgs


# ------------------------------------------------------------------ 方案解析
def parse_plan(text: str) -> dict:
    """把模型输出解成 {'analysis': str, 'commands': [...]}。

    模型经常加代码围栏或前后废话，所以先剥围栏，再按括号配对找第一个 JSON 对象。
    解析不出来就整段当 analysis，命令列表为空——绝不猜命令。
    """
    raw = (text or "").strip()
    if not raw:
        return {"analysis": "", "commands": [], "raw": "", "done": False,
                "shorthand": "未给出命令"}

    body = raw
    m = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if m:
        body = m.group(1).strip()

    obj = _first_json_object(body)
    if obj is None:
        return {"analysis": raw[:2000], "commands": [], "raw": raw,
                "done": False, "shorthand": "未给出命令"}

    analysis = str(obj.get("analysis") or obj.get("answer") or "").strip()
    done = bool(obj.get("done") or obj.get("finished") or False)
    cmds = []
    for item in (obj.get("commands") or [])[:12]:
        if isinstance(item, str):
            cmd, why, risk = item, "", ""
        elif isinstance(item, dict):
            cmd = str(item.get("cmd") or item.get("command") or "").strip()
            why = str(item.get("why") or item.get("reason") or "").strip()
            risk = str(item.get("risk") or "").strip().lower()
        else:
            continue
        cmd = cmd.strip().strip("`").strip()
        if not cmd:
            continue
        auto = classify_risk(cmd)
        if risk not in RISK_LABEL:
            risk = auto
        # 模型说只读但规则判定更危险时，以规则为准
        order = {"read": 0, "write": 1, "danger": 2}
        if order.get(auto, 0) > order.get(risk, 0):
            risk = auto
        cmds.append({"cmd": cmd, "why": why, "risk": risk})
    out = {"analysis": analysis, "commands": cmds, "raw": raw, "done": done}
    out["shorthand"] = _shorthand(out)
    return out


def _first_json_object(s: str):
    start = s.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except ValueError:
                        break
        start = s.find("{", start + 1)
    return None


# ------------------------------------------------------------------ 配置
class AIConfig:
    """data/ai.json。Key 单独加密存放，json 里只有密文。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {
            "provider": DEFAULT_PROVIDER,
            "base": PROVIDERS[DEFAULT_PROVIDER]["base"],
            "model": PROVIDERS[DEFAULT_PROVIDER]["model"],
            "key_enc": "",
            "temperature": 0.2,
            "max_tokens": 1200,
            "timeout": 90,
            "auto_read": True,     # 只读命令可以一键全批
        }
        self.load()

    def load(self) -> None:
        try:
            if self.path.is_file():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.data.update({k: v for k, v in raw.items()})
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    # -- 便捷
    @property
    def provider(self) -> str:
        p = str(self.data.get("provider") or DEFAULT_PROVIDER)
        return p if p in PROVIDERS else DEFAULT_PROVIDER

    def preset(self) -> dict:
        return PROVIDERS[self.provider]

    def get_key(self) -> str:
        enc = str(self.data.get("key_enc") or "")
        if not enc:
            return ""
        if crypto_store is None:
            return ""
        try:
            return crypto_store.unprotect(enc) or ""
        except Exception:  # noqa: BLE001
            return ""

    def set_key(self, plain: str) -> None:
        if not plain:
            self.data["key_enc"] = ""
        elif crypto_store is not None:
            self.data["key_enc"] = crypto_store.protect(plain) or ""
        else:
            self.data["key_enc"] = plain  # 极少数情况下退化为明文，界面会提示
        self.save()

    def describe(self) -> str:
        p = self.preset()
        base = str(self.data.get("base") or p["base"] or "—")
        model = str(self.data.get("model") or p["model"] or "—")
        return f"{p['label']} · {model} · {base}"


# ------------------------------------------------------------------ 引擎
class AIError(RuntimeError):
    pass


def _post_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403):
            raise AIError(f"认证失败（{e.code}）：请检查 API Key。{detail}") from e
        if e.code == 404:
            raise AIError(f"接口不存在（404）：检查 base 地址是否需要带 /v1。{detail}") from e
        raise AIError(f"HTTP {e.code}：{detail}") from e
    except urllib.error.URLError as e:
        raise AIError(f"连不上模型服务：{e.reason}") from e
    except TimeoutError as e:
        raise AIError("请求超时") from e


class OpenAIEngine:
    """任何 OpenAI 兼容的 /chat/completions。"""

    def __init__(self, cfg: AIConfig):
        self.cfg = cfg

    def chat(self, messages: list[dict]) -> tuple[str, dict]:
        base = str(self.cfg.data.get("base") or self.cfg.preset()["base"]).rstrip("/")
        if not base:
            raise AIError("还没填 base 地址")
        if not base.endswith("/v1") and "/v1" not in base:
            base += "/v1"
        model = str(self.cfg.data.get("model") or self.cfg.preset()["model"])
        if not model:
            raise AIError("还没填模型名")
        key = self.cfg.get_key()
        if self.cfg.preset().get("need_key") and not key:
            raise AIError("还没填 API Key")

        payload = {
            "model": model,
            "messages": messages,
            "temperature": float(self.cfg.data.get("temperature") or 0.2),
            "max_tokens": int(self.cfg.data.get("max_tokens") or 1200),
            "stream": False,
        }
        headers = {"Accept": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        data = _post_json(base + "/chat/completions", payload, headers,
                          int(self.cfg.data.get("timeout") or 90))
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise AIError(f"返回格式不认识：{str(data)[:200]}") from e
        usage = data.get("usage") or {}
        return text, {"prompt": usage.get("prompt_tokens"),
                      "completion": usage.get("completion_tokens"),
                      "total": usage.get("total_tokens"),
                      "model": model}


# ---------------------------------------------------------------- dsh 运行时
def find_dsh() -> str:
    """找 dsh 命令行：环境变量 → PATH → Harness GUI 的默认安装位置。"""
    env = os.environ.get("DSH_CLI")
    if env and Path(env).is_file():
        return env
    found = shutil.which("dsh")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA") or ""
    for cand in (
        Path(local) / "Programs/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd",
        Path.home() / "AppData/Local/Programs/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd",
    ):
        if cand.is_file():
            return str(cand)
    return ""


class DshEngine:
    """本机 DeepSeek Harness：`dsh --profile headless --json <task>`。

    只取它的最终文本，不让它用工具——命令必须回到我们的会话上执行。
    """

    def __init__(self, cfg: AIConfig):
        self.cfg = cfg

    def chat(self, messages: list[dict]) -> tuple[str, dict]:
        exe = find_dsh()
        if not exe:
            raise AIError("没找到 dsh：装 DeepSeek Harness，或设 DSH_CLI 指向 dsh.cmd")
        prompt = _flatten(messages)
        args = ["--profile", "headless", "--json", prompt]
        if exe.lower().endswith((".cmd", ".bat")):
            cmd = ["cmd.exe", "/c", exe] + args
        else:
            cmd = [exe] + args
        t0 = time.time()
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                               encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as e:
            raise AIError("dsh 运行超时（300 秒）") from e
        out = p.stdout or ""
        text = ""
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "final" and isinstance(ev.get("text"), str):
                text = ev["text"]
            elif ev.get("type") == "turn_end":
                err = ((ev.get("reason") or {}).get("error") or {})
                if err.get("message"):
                    raise AIError("dsh：" + str(err["message"])[:300])
        if not text:
            tail = (p.stderr or out)[-300:]
            raise AIError(f"dsh 没有返回内容：{tail.strip()}")
        return text, {"prompt": None, "completion": len(text) // 3,
                      "total": len(text) // 3, "model": "dsh-headless",
                      "elapsed": round(time.time() - t0, 1)}


def _flatten(messages: list[dict]) -> str:
    parts = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            parts.append("【规则】\n" + str(m.get("content")))
        elif role == "assistant":
            parts.append("【你上次的回答】\n" + str(m.get("content"))[:2000])
        else:
            parts.append("【用户】\n" + str(m.get("content")))
    parts.append("【提醒】只输出一个 JSON 对象，不要使用任何工具，不要执行命令。")
    return "\n\n".join(parts)


def make_engine(cfg: AIConfig):
    if cfg.preset().get("kind") == "dsh":
        return DshEngine(cfg)
    return OpenAIEngine(cfg)


# ------------------------------------------------------------------ 对外门面
class Assistant:
    """一问一答：给问题 + 设备上下文 → 分析 + 待确认命令。"""

    def __init__(self, cfg: AIConfig):
        self.cfg = cfg
        self.history: list[dict] = []
        self.tokens = 0
        self.last_usage: dict = {}

    def ask(self, question: str, device: dict | None = None,
            recent: list[str] | None = None) -> dict:
        ctx = build_context(device, recent)
        msgs = build_messages(question, ctx, self.history)
        text, usage = make_engine(self.cfg).chat(msgs)
        plan = parse_plan(text)
        self.history.append({"role": "user", "content": question})
        brief = plan.get("analysis") or text[:400]
        self.history.append({"role": "assistant", "content": brief})
        self.tokens += int(usage.get("total") or 0)
        self.last_usage = usage
        plan["usage"] = usage
        plan["shorthand"] = _shorthand(plan)
        return plan

    def reset(self) -> None:
        self.history.clear()
        self.tokens = 0

    # ------------------------------------------------------------ 自驱循环
    def _task_messages(self, goal: str, context: str, transcript: list[str],
                       skill_text: str = ""):
        system = SYSTEM_PROMPT_TASK.format(
            context=context, skill=skill_text or "（本轮没有套用技能，按通用排查顺序："
                                                "只读查证 → 定位 → 最小改动修复）")
        msgs = [{"role": "system", "content": system}]
        msgs.append({"role": "user", "content": f"任务：{goal}"})
        if transcript:
            msgs.append({"role": "user",
                         "content": "到目前为止你提出并已经执行过的命令与真实输出：\n"
                                    + "\n".join(transcript[-12:])})
        else:
            msgs.append({"role": "user",
                         "content": "还没有执行任何命令，请给出第一步。"})
        return msgs

    def run_task(self, goal: str, runner, *, device: dict | None = None,
                 mode: str = "auto", confirm=None, on_event=None,
                 should_stop=None, max_steps: int = 8,
                 out_limit: int = 3000, skill_text: str = "") -> dict:
        """生成 → 远端执行 → 读回输出 → 再生成，直到完成或步数用完。

        runner(cmd) -> (rc, out, err)
        confirm(list_of_cmds) -> bool   # 需要人点头时调用，None 表示一律当作拒绝
        on_event(kind, payload)         # 界面用，kind 见下方
        should_stop() -> bool           # 用户点了停止

        模式：
          auto     —— 只读与一般写直接跑，danger 必问（对应宝塔的 assist_approval）
          confirm  —— 只读直接跑，写与 danger 必问（对应 request_approval）
          readonly —— 只跑只读，其余跳过并记账
        """
        emit = on_event or (lambda *_a, **_k: None)
        stop = should_stop or (lambda: False)
        ctx = build_context(device)
        transcript: list[str] = []
        ran: list[str] = []
        refused: list[str] = []
        skipped: list[str] = []
        steps = 0
        stopped = False

        for step in range(1, max_steps + 1):
            if stop():
                stopped = True
                emit("stopped", {"step": step})
                break
            steps = step
            try:
                text, usage = make_engine(self.cfg).chat(
                    self._task_messages(goal, ctx, transcript, skill_text))
            except AIError as e:
                emit("error", {"step": step, "msg": str(e)})
                return {"ok": False, "error": str(e), "steps": step,
                        "ran": ran, "refused": refused, "skipped": skipped}
            self.tokens += int(usage.get("total") or 0)
            self.last_usage = usage
            plan = parse_plan(text)
            emit("plan", {"step": step, "analysis": plan.get("analysis", ""),
                          "done": bool(plan.get("done")),
                          "usage": usage})
            cmds = plan.get("commands") or []
            if not cmds:
                emit("finish", {"step": step, "reason": "模型没有给出命令",
                                "analysis": plan.get("analysis", "")})
                break

            # 先按模式分拣：哪些直接跑、哪些要问、哪些不跑
            direct, ask = [], []
            for c in cmds:
                risk = c.get("risk", "read")
                if mode == "readonly" and risk != "read":
                    skipped.append(c["cmd"])
                    emit("skip", {"cmd": c["cmd"], "risk": risk,
                                  "why": "只读模式下不执行"})
                    continue
                need_ask = risk == "danger" or (mode == "confirm" and risk != "read")
                (ask if need_ask else direct).append(c)

            if ask:
                emit("need_confirm", {"cmds": ask})
                ok = bool(confirm(ask)) if confirm else False
                if ok:
                    direct.extend(ask)
                else:
                    for c in ask:
                        refused.append(c["cmd"])
                        emit("refused", {"cmd": c["cmd"], "risk": c["risk"]})

            if not direct:
                # 本轮全都被人拒了或跳过，别拿空结果再问一遍模型
                emit("finish", {"step": step,
                                "reason": "没有可执行的命令",
                                "analysis": plan.get("analysis", "")})
                break

            for c in direct:
                if stop():
                    stopped = True
                    emit("stopped", {"step": step})
                    break
                cmd = c["cmd"]
                emit("run", {"cmd": cmd, "risk": c.get("risk"), "why": c.get("why", "")})
                try:
                    rc, out, err = runner(cmd)
                except Exception as e:  # noqa: BLE001
                    rc, out, err = 1, "", f"{type(e).__name__}: {e}"
                ran.append(cmd)
                body = (out or "") + (("\n[stderr] " + err) if err else "")
                emit("result", {"cmd": cmd, "rc": rc, "out": out or "",
                                "err": err or ""})
                transcript.append(
                    f"$ {cmd}\nrc={rc}\n{_clip(body, out_limit)}")

            if stopped:
                break
            if plan.get("done") and not ask:
                emit("finish", {"step": step, "reason": "模型判断已完成",
                                "analysis": plan.get("analysis", "")})
                break
        else:
            emit("finish", {"step": steps, "reason": "达到步数上限",
                            "analysis": ""})

        return {"ok": True, "steps": steps, "ran": ran,
                "refused": refused, "skipped": skipped, "stopped": stopped}


def _clip(text: str, limit: int) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t or "(无输出)"
    head = t[: int(limit * 0.7)]
    tail = t[-int(limit * 0.25):]
    return f"{head}\n…（省略 {len(t) - len(head) - len(tail)} 字）…\n{tail}"


def _shorthand(plan: dict) -> str:
    cmds = plan.get("commands") or []
    if not cmds:
        return "未给出命令"
    worst = "read"
    for c in cmds:
        if c.get("risk") == "danger":
            worst = "danger"
            break
        if c.get("risk") == "write":
            worst = "write"
    return f"{len(cmds)} 条命令 · 最高 {RISK_LABEL[worst]}"


def quote_cmd(cmd: str) -> str:
    """仅用于日志展示前的归一，不做任何改写。"""
    try:
        return " ".join(shlex.split(cmd))
    except ValueError:
        return cmd
