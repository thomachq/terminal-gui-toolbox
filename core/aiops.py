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

import copy
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


# ------------------------------------------------------------------ 模型能力
# 这个程序只发 chat/completions，所以模型类型只剩「文本」一种，不给选。
#
# 思考档位取 off / low / high / max —— 不是拍脑袋：DeepSeek Harness GUI 的
# scripts/model-capabilities.json 里 efforts 就是这四个键，那份表是拿 566 个
# 真实模型逐条校对过的，也是本程序唯一验证过 reasoning_effort 能被网关接受的
# 取值集合（实测 6 家全 200）。off 表示「不发这个字段」，不是发 "none"。
THINKING_LEVELS: list[tuple[str, str]] = [
    ("off", "关"), ("low", "低"), ("high", "高"), ("max", "最高"),
]
THINKING_KEYS = tuple(k for k, _ in THINKING_LEVELS)
THINKING_LABEL = dict(THINKING_LEVELS)
DEFAULT_THINKING = "high"

# 输入模态只留这两项。视频/文件不做 —— 这个程序只会把文本发出去，
# 勾了也没地方用。存 "vision"/"audio"（对外），读的时候 "image" 也认。
MODALITIES: list[tuple[str, str]] = [("vision", "视觉"), ("audio", "音频")]
MODALITY_KEYS = tuple(k for k, _ in MODALITIES)
MODALITY_LABEL = dict(MODALITIES)
_MODALITY_ALIAS = {"image": "vision", "img": "vision", "文本": "", "text": ""}


def clean_caps(raw) -> dict:
    """把外部来源的 caps 洗成固定结构。

    `{"thinking": {"levels":[...], "default":"..."} | None, "tools": bool}`
    thinking 为 None 表示这个模型不支持思考。默认档位必须属于可用档位 ——
    不在就退回第一档，宁可显示错的默认值也不要让请求带一个不存在的档位。
    """
    src = raw if isinstance(raw, dict) else {}
    think = src.get("thinking")
    levels: list[str] = []
    default = ""
    if isinstance(think, dict):
        for lv in (think.get("levels") or []):
            s = str(lv or "").strip().lower()
            if s in THINKING_KEYS and s not in levels:
                levels.append(s)
        if levels:
            d = str(think.get("default") or "").strip().lower()
            default = d if d in levels else levels[0]
    elif isinstance(think, (list, tuple)):
        for lv in think:
            s = str(lv or "").strip().lower()
            if s in THINKING_KEYS and s not in levels:
                levels.append(s)
        if levels:
            default = levels[0]
    return {
        "thinking": ({"levels": levels, "default": default} if levels else None),
        "tools": bool(src.get("tools")),
    }


def clean_modalities(raw) -> list[str]:
    out: list[str] = []
    if isinstance(raw, str):
        raw = [raw]
    for m in (raw or []):
        s = str(m or "").strip().lower()
        s = _MODALITY_ALIAS.get(s, s)
        if s in MODALITY_KEYS and s not in out:
            out.append(s)
    return out


def caps_label(m: dict) -> str:
    """能力列的一句话摘要：`思考·高 · 工具 · 视觉`。"""
    caps = m.get("caps") if isinstance(m.get("caps"), dict) else {}
    parts: list[str] = []
    th = caps.get("thinking") or {}
    levels = th.get("levels") or []
    if levels:
        d = th.get("default") or levels[0]
        parts.append(f"思考·{THINKING_LABEL.get(d, d)}")
    if caps.get("tools"):
        parts.append("工具")
    mods = [MODALITY_LABEL.get(x, x) for x in (m.get("modalities") or [])]
    if mods:
        parts.append("＋".join(mods))
    return " · ".join(parts) or "—"


def fmt_tokens(n) -> str:
    """Token 数字显示。空值一律「未知」，绝不显示 0 —— 0 会被当成真值。"""
    try:
        v = int(n or 0)
    except (TypeError, ValueError):
        return "未知"
    return f"{v:,}" if v > 0 else "未知"


# ------------------------------------------------------------------ 风险分级
# 只影响界面上的默认勾选与二次确认，不阻止执行——最终决定权在人。
_DANGER = [
    r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+/(?:\s|$)",
    r"\brm\s+-rf\b",
    r"\bmkfs(\.\w+)?\b", r"\bdd\s+.*of=/dev/", r"\bwipefs\b", r"\bshred\b",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\bpoweroff\b", r"\binit\s+0\b",
    r"\buserdel\b", r"\bgroupdel\b", r"\bpasswd\b", r"\bchpasswd\b",
    r"\biptables\s+(?:-t\s+\S+\s+)?-F\b", r"\bnft\s+flush\b",
    r"\bufw\s+disable\b",
    # 网络层的「一刀切」：清空整张表 / 重置全部规则 / 删默认路由 = 立刻失联
    r"\bip\s+route\s+(?:flush|del|delete)\b.*\bdefault\b",
    r"\bip\s+route\s+(?:replace|change|append|prepend)\b.*\bdefault\b",
    r"\bip\s+-6\s+route\s+(?:flush|del|delete)\b",
    r"\bip\s+route\s+flush\b",
    r"\broute\s+(?:delete|del|change)\s+(?:0\.0\.0\.0|0\.0\.0\.0/0)\b",
    r"\bnetsh\s+advfirewall\s+reset\b",
    r"\bufw\s+reset\b", r"\bfirewall-cmd\s+--panic-on\b",
    r"\biptables\s+(?:-t\s+\S+\s+)?-[XPFZ]\b",
    r"\bip6tables\s+(?:-t\s+\S+\s+)?-[XPFZ]\b",
    r"\bsysctl\s+-w\s+net\.ipv4\.ip_forward",
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
    r"\biptables\s+(?:-t\s+\S+\s+)?-(?:A|I|D|R|N|E|X|F|Z|P)\b",
    r"\bip6tables\s+(?:-t\s+\S+\s+)?-(?:A|I|D|R|N|E|X|F|Z|P)\b",
    r"\bfirewall-cmd\b[^|;]*--(?:add|remove|change|set|new|delete|panic|reload|complete-reload|runtime-to-permanent|permanent)\b",
    r"\bnft\s+(?:-f\s+\S+\s+)?(?:add|insert|delete|flush|create|destroy|replace|rename)\b",
    r"\bufw\s+(allow|deny|reject|limit|delete|insert|add|route|enable|disable|default|reload|app)\b",
    r"\bpfctl\s+-f\b", r"\bipfw\s+(add|delete|flush)\b",
    r"\bnetsh\s+advfirewall\s+(?:firewall\s+)?(?:add|delete|set|reset)\b",
    r"\bip\s+route\s+(add|del|delete|replace|change|append|prepend)\b",
    r"\broute\s+(?:-p\s+|-f\s+)?(add|delete|change)\b",
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
        # 供应商能自定义，所以这里不再限定在内置表里：只要求是个非空 slug。
        p = str(self.data.get("provider") or DEFAULT_PROVIDER).strip()
        return p or DEFAULT_PROVIDER

    def preset(self) -> dict:
        """当前供应商的类型预设。自定义供应商给一份兜底，键名和内置的一致。"""
        p = self.provider
        if p in PROVIDERS:
            return PROVIDERS[p]
        name = self.provider_name(p)
        return {"label": name, "kind": "openai", "base": "", "model": "",
                "need_key": True, "hint": ""}

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

    # ------------------------------------------------------------ 供应商
    # 「供应商」是模型上面的一层：一个供应商一套接口地址与 Key，下面挂若干模型。
    # 老配置没有 providers 这个键，就从该供应商下第一条模型推一个初值 ——
    # 打开界面就有内容，不用用户重填一遍。
    def provider_meta(self) -> dict:
        """每个供应商的元信息：地址 / Key / 名字 / 类型 / 是否启用。

        供应商不再是固定那几个：用户在界面上能加任意多条（同一类型也能加几条，
        截图里就有两个「Llama 网关」）。内置的 5 个只当「类型预设」用，
        真正在用的是 `providers` 这个字典 + 模型里出现过的 pid。
        """
        meta = self.data.get("providers")
        if not isinstance(meta, dict):
            meta = {}
        models = self.models()
        pids: list[str] = list(PROVIDERS)
        for pid in list(meta):
            if pid not in pids:
                pids.append(str(pid))
        for m in models:
            pid = str(m.get("provider") or "")
            if pid and pid not in pids:
                pids.append(pid)
        out = {}
        for pid in pids:
            row = meta.get(pid) if isinstance(meta.get(pid), dict) else {}
            base = str(row.get("base") or "").strip()
            key_enc = str(row.get("key_enc") or "").strip()
            if not base or not key_enc:
                for m in models:
                    if str(m.get("provider") or "") != pid:
                        continue
                    base = base or str(m.get("base") or "").strip()
                    key_enc = key_enc or str(m.get("key_enc") or "").strip()
                    if base and key_enc:
                        break
            preset = PROVIDERS.get(pid) or {}
            out[pid] = {
                "base": base,
                "key_enc": key_enc,
                "name": str(row.get("name") or preset.get("label") or pid),
                "kind": str(row.get("kind") or preset.get("kind") or "openai"),
                "enabled": bool(row.get("enabled", True)),
                # 同一家的备用地址（内网/外网、直连/中转）。Cherry Studio 的
                # 「添加端点」就是这层意思：地址可以有多个，当前生效的只有一个。
                "endpoints": [str(x).strip() for x in (row.get("endpoints") or [])
                              if str(x).strip()],
                "builtin": pid in PROVIDERS,
            }
        return out

    def provider_list(self) -> list[dict]:
        """界面左栏用的有序清单：内置的按原顺序在前，自定义的按创建顺序在后。"""
        meta = self.provider_meta()
        order = list(PROVIDERS) + [p for p in meta if p not in PROVIDERS]
        rows = []
        for pid in order:
            row = meta.get(pid)
            if not row:
                continue
            # 内置的先全列出来（Cherry Studio 也是把服务商全摆出来，用过的排前面），
            # 不做「没用过就藏起来」—— 藏起来等于用户找不到怎么新增这一家。
            rows.append({"pid": pid, **row})
        return rows

    def provider_name(self, pid: str) -> str:
        row = self.provider_meta().get(pid) or {}
        return str(row.get("name") or (PROVIDERS.get(pid) or {}).get("label") or pid)

    def provider_enabled(self, pid: str) -> bool:
        return bool((self.provider_meta().get(pid) or {}).get("enabled", True))

    def add_provider(self, name: str, kind: str = "openai", base: str = "",
                     key: str = "") -> str:
        """新增一条供应商，返回它的 pid。同名的会自动加序号，不会覆盖。"""
        name = str(name or "").strip() or "新供应商"
        meta = self.data.get("providers")
        if not isinstance(meta, dict):
            meta = {}
        base_pid = re.sub(r"[^0-9a-z]+", "", name.lower()) or "prov"
        base_pid = base_pid[:16]
        pid = base_pid
        i = 2
        while pid in PROVIDERS or pid in meta:
            pid = f"{base_pid}{i}"
            i += 1
        meta[pid] = {
            "name": name,
            "kind": kind if kind in ("openai", "dsh") else "openai",
            "base": str(base or "").strip(),
            "key_enc": ((crypto_store.protect(key) or "")
                        if (key and crypto_store is not None) else key or ""),
            "enabled": True,
            "endpoints": ([str(base).strip()] if str(base or "").strip() else []),
        }
        self.data["providers"] = meta
        self.save()
        return pid

    def remove_provider(self, pid: str) -> int:
        """删一条供应商，连同它下面的模型。返回删掉的模型条数。

        内置的那 5 个不真删 —— 删了下次升级又冒出来，只会让人困惑；
        这里只清空它的地址/Key/模型（等于「不用了」）。
        """
        n = 0
        keep = []
        for m in self.models():
            if str(m.get("provider") or "") == pid:
                n += 1
            else:
                keep.append(m)
        self.data["models"] = keep
        meta = self.data.get("providers")
        if isinstance(meta, dict):
            if pid in PROVIDERS:
                meta[pid] = {"base": "", "key_enc": "", "enabled": False}
            else:
                meta.pop(pid, None)
            self.data["providers"] = meta
        self.save()
        return n

    def provider_base(self, pid: str) -> str:
        row = self.provider_meta().get(pid) or {}
        return str(row.get("base") or PROVIDERS.get(pid, {}).get("base") or "")

    # ------------------------------------------------------------ 多端点
    # 同一家供应商往往有好几个入口：内网直连、外网中转、备用网关。
    # 地址只能生效一个，但清单要留着 —— 否则每次换网络都要重新敲一遍 URL。
    def provider_endpoints(self, pid: str) -> list[str]:
        """这家的地址清单，当前生效的那个排第一。"""
        row = self.provider_meta().get(pid) or {}
        out: list[str] = []
        cur = str(row.get("base") or "").strip()
        if cur:
            out.append(cur)
        for u in row.get("endpoints") or []:
            u = str(u or "").strip()
            if u and u not in out:
                out.append(u)
        return out

    def add_endpoint(self, pid: str, url: str) -> bool:
        """加一个备用地址。加完不会自动切过去（用户可能只是想先登记）。"""
        url = str(url or "").strip()
        if not url:
            return False
        if url in self.provider_endpoints(pid):
            return False
        meta = self.data.get("providers")
        if not isinstance(meta, dict):
            meta = {}
        row = meta.get(pid) if isinstance(meta.get(pid), dict) else {}
        eps = [str(x).strip() for x in (row.get("endpoints") or []) if str(x).strip()]
        if url not in eps:
            eps.append(url)
        row["endpoints"] = eps
        row.setdefault("name", self.provider_name(pid))
        row.setdefault("kind", (self.provider_meta().get(pid) or {}).get("kind")
                       or "openai")
        row.setdefault("base", self.provider_base(pid))
        row.setdefault("key_enc", self.provider_key_enc(pid))
        meta[pid] = row
        self.data["providers"] = meta
        self.save()
        return True

    def remove_endpoint(self, pid: str, url: str) -> bool:
        """删一个地址。删的正好是当前生效的那个就切到清单里的下一个。"""
        url = str(url or "").strip()
        meta = self.data.get("providers")
        if not isinstance(meta, dict) or not url:
            return False
        row = meta.get(pid) if isinstance(meta.get(pid), dict) else {}
        eps = [str(x).strip() for x in (row.get("endpoints") or [])
               if str(x).strip() and str(x).strip() != url]
        if not eps:
            # 一条都不剩就没地址可用了，内置家的默认地址还会被 provider_base
            # 兜回来 —— 界面看着像「删了没反应」。直接不许删最后一条。
            return False
        row["endpoints"] = eps
        meta[pid] = row
        self.data["providers"] = meta
        if str(self.provider_base(pid)).strip() == url:
            self.set_provider(pid, base=eps[0])
        else:
            self.save()
        return True

    def use_endpoint(self, pid: str, url: str) -> None:
        """切换当前生效的地址（等价于在地址框里改成它并保存）。"""
        url = str(url or "").strip()
        if not url:
            return
        self.add_endpoint(pid, url)
        self.set_provider(pid, base=url)

    def provider_key_enc(self, pid: str) -> str:
        return str((self.provider_meta().get(pid) or {}).get("key_enc") or "")

    def provider_key(self, pid: str) -> str:
        enc = self.provider_key_enc(pid)
        if not enc or crypto_store is None:
            return ""
        try:
            return crypto_store.unprotect(enc) or ""
        except Exception:  # noqa: BLE001
            return ""

    def provider_models(self, pid: str) -> list[dict]:
        # 走 models() 而不是直接读 data["models"]：老配置第一次打开时
        # 列表还没归一化（甚至还没有 models 这个键），直接读会得到空表，
        # 界面上就是「这家供应商下没有模型」—— 明明有。
        return [m for m in self.models()
                if str(m.get("provider") or "") == pid]

    def set_provider(self, pid: str, base: str | None = None,
                     key: str | None = None, name: str | None = None,
                     enabled: bool | None = None,
                     kind: str | None = None) -> None:
        """改供应商的地址 / Key / 名字 / 启用状态，并把地址与 Key 下发到它下面的模型。

        `base` / `key` 传 None 表示「这一项不改」，传空串表示清空。
        模型自己单独填过的地址不覆盖 —— 同一个供应商下挂两个网关是常见用法。
        自定义供应商（不在内置表里的 pid）同样能改。
        """
        if pid not in PROVIDERS and pid not in (self.provider_meta() or {}):
            return
        old = self.provider_meta().get(pid) or {}
        old_base = str(old.get("base") or "")
        row = {"base": old_base, "key_enc": str(old.get("key_enc") or "")}
        if pid not in PROVIDERS or old.get("name"):
            row["name"] = str(name if name is not None
                             else (old.get("name") or pid))
        elif name is not None:
            row["name"] = str(name)
        if kind is not None:
            row["kind"] = kind if kind in ("openai", "dsh") else "openai"
        elif old.get("kind"):
            row["kind"] = str(old["kind"])
        if enabled is not None:
            row["enabled"] = bool(enabled)
        if base is not None:
            row["base"] = str(base).strip()
        # 端点清单必须原样带过去 —— 这里是整体覆盖 store[pid]，
        # 少抄一个键就等于把用户攒的备用地址全丢了。
        eps = [str(x).strip() for x in (old.get("endpoints") or []) if str(x).strip()]
        if base is not None and row["base"] and row["base"] not in eps:
            eps.append(row["base"])
        if eps:
            row["endpoints"] = eps
        if key is not None:
            if key:
                row["key_enc"] = ((crypto_store.protect(key) or "")
                                  if crypto_store is not None else key)
            else:
                row["key_enc"] = ""
        store = self.data.get("providers")
        if not isinstance(store, dict):
            store = {}
        store[pid] = row
        self.data["providers"] = store

        cur_id = self.current_id()
        for m in (self.data.get("models") or []):
            if not isinstance(m, dict) or str(m.get("provider")) != pid:
                continue
            cur = str(m.get("base") or "").strip()
            if base is not None and (not cur or cur == old_base):
                m["base"] = row["base"]
            if key is not None:
                m["key_enc"] = row["key_enc"]
            if str(m.get("id")) == cur_id:
                flat = str(self.data.get("base") or "").strip()
                if base is not None and (not flat or flat == old_base):
                    self.data["base"] = row["base"]
                if key is not None:
                    self.data["key_enc"] = row["key_enc"]
        self.save()


    # ------------------------------------------------------------ 多模型
    # 扁平字段（provider / base / model / key_enc / temperature）永远是
    # **当前模型**的镜像。这样引擎、界面、旧配置全都不用改一行；
    # 切换模型 = 把另一条档案写到这几个字段上。
    MODEL_FIELDS = ("provider", "base", "model", "temperature", "max_tokens",
                    "key_enc", "ctx", "note", "kind", "caps", "modalities",
                    "context", "max_input", "max_output", "thinking")

    def _model_from_flat(self, name: str = "", mid: str = "") -> dict:
        p = self.preset()
        return {
            "id": mid or new_model_id(),
            "name": name or f"{p['label']} · {self.data.get('model') or p['model']}",
            "provider": self.provider,
            "base": str(self.data.get("base") or p["base"] or ""),
            "model": str(self.data.get("model") or p["model"] or ""),
            "temperature": self.data.get("temperature", 0.2),
            "max_tokens": self.data.get("max_tokens", 1200),
            "key_enc": str(self.data.get("key_enc") or ""),
            "ctx": int(self.data.get("ctx") or 0),
            "note": str(self.data.get("note") or ""),
            "kind": "text",
            "caps": clean_caps(self.data.get("caps")),
            "modalities": clean_modalities(self.data.get("modalities")),
            "context": int(self.data.get("context") or self.data.get("ctx") or 0),
            "max_input": int(self.data.get("max_input") or 0),
            "max_output": int(self.data.get("max_output") or 0),
            "created": str(int(time.time())),
        }

    def ensure_models(self) -> list[dict]:
        """保证 models 里至少有一条。

        老用户的配置只有一份扁平字段 —— 升级后不弹窗、不丢 Key，
        静默变成「默认模型」这一条，然后照常用。
        """
        models = self.data.get("models")
        if not isinstance(models, list) or not models:
            models = [self._model_from_flat(name="默认模型")]
            self.data["models"] = models
            self.data["active"] = models[0]["id"]
            self.save()
            return models
        clean = [self._clean_model(m) for m in models if isinstance(m, dict)]
        if not clean:
            clean = [self._model_from_flat(name="默认模型")]
            self.data["active"] = clean[0]["id"]
        if not self.current_id() or self.find_model(self.current_id()) is None:
            self.data["active"] = clean[0]["id"]
        self.data["models"] = clean
        return clean

    @staticmethod
    def _clean_model(m: dict) -> dict:
        # 自定义供应商的 pid 也是合法值，别把它强行改回默认那家 ——
        # 改回去等于「重开一次配置，模型全跑到 DeepSeek 名下」。
        prov = str(m.get("provider") or DEFAULT_PROVIDER).strip() or DEFAULT_PROVIDER
        try:
            temp = max(0.0, min(2.0, float(m.get("temperature", 0.2))))
        except (TypeError, ValueError):
            temp = 0.2
        try:
            mt = max(64, min(32000, int(m.get("max_tokens") or 1200)))
        except (TypeError, ValueError):
            mt = 1200
        # 三个 token 限制：0 / 缺字段都表示「不知道」，界面显示「未知」。
        # 不拿默认值填坑 —— 填了用户就会照着那个数字去切会话。
        def _tok(*keys) -> int:
            for k in keys:
                try:
                    v = int(m.get(k) or 0)
                except (TypeError, ValueError):
                    v = 0
                if v > 0:
                    return v
            return 0

        ctx = _tok("context", "ctx")
        caps = clean_caps(m.get("caps"))
        # 当前选中的思考档：只有落在「可用档位」里才算数，否则回落成空
        # （空 = 用模型默认档，见 AIConfig.thinking()）
        want = str(m.get("thinking") or "").strip().lower()
        levels = ((caps.get("thinking") or {}).get("levels") or [])
        return {
            "id": str(m.get("id") or new_model_id()),
            "name": str(m.get("name") or "").strip() or "未命名模型",
            "provider": prov,
            "base": str(m.get("base") or "").strip(),
            "model": str(m.get("model") or "").strip(),
            "temperature": temp,
            "max_tokens": mt,
            "key_enc": str(m.get("key_enc") or ""),
            # ctx 是 context 的旧名字，留着给老配置和老测试用，值永远跟 context 走
            "ctx": ctx,
            "context": ctx,
            "max_input": _tok("max_input"),
            "max_output": _tok("max_output"),
            # 类型固定文本：这个程序只会发 chat/completions
            "kind": "text",
            "caps": caps,
            # 分组名称：对齐 Cherry Studio 的「分组」——同一家供应商下按用途/家族
            # 折叠（例如 deepseek、qwen3.6-35b）。空表示「默认」。
            "group": str(m.get("group") or "").strip(),
            "thinking": want if want in levels else "",
            "modalities": clean_modalities(m.get("modalities")),
            "note": str(m.get("note") or ""),
            "created": str(m.get("created") or int(time.time())),
        }

    def models(self) -> list[dict]:
        return self.ensure_models()

    def find_model(self, mid: str) -> dict | None:
        # 直接读原始列表，不走 ensure_models —— 后者要靠 find_model 判活跃项，
        # 互相调用会直接爆栈。
        models = self.data.get("models")
        if not isinstance(models, list):
            return None
        for m in models:
            if isinstance(m, dict) and m.get("id") == mid:
                return m
        return None

    def current_id(self) -> str:
        return str(self.data.get("active") or "")

    def current(self) -> dict:
        m = self.find_model(self.current_id())
        if m is None:
            m = self.models()[0]
            self.data["active"] = m["id"]
        return m

    def set_active(self, mid: str) -> bool:
        """切当前模型：把它写到扁平字段上，然后启用引擎重读。"""
        m = self.find_model(mid)
        if m is None:
            return False
        self.data["active"] = m["id"]
        for k in self.MODEL_FIELDS:
            if k == "provider":
                self.data["provider"] = (m["provider"]
                                         if m["provider"] in PROVIDERS
                                         else DEFAULT_PROVIDER)
            elif k in m:
                v = m[k]
                # caps / modalities 是可变容器：扁平镜像必须拿副本，
                # 否则改扁平那份会顺手改掉 models 里的档案
                if isinstance(v, dict):
                    v = copy.deepcopy(v)
                elif isinstance(v, list):
                    v = list(v)
                self.data[k] = v
        self.data["base"] = (m.get("base")
                             or PROVIDERS[self.provider]["base"])
        self.data["model"] = (m.get("model")
                              or PROVIDERS[self.provider]["model"])
        self.save()
        return True

    def upsert_model(self, rec: dict) -> dict:
        # 模型没单独填地址 / Key 时，继承它所属供应商那一份 ——
        # 「供应商一层填一次，下面所有模型共用」就是这么落地的。
        rec = dict(rec or {})
        pid = str(rec.get("provider") or DEFAULT_PROVIDER)
        # 自定义供应商（用户自己加的）同样要继承 —— 以前只对内置那 5 个生效，
        # 结果在自己加的「Llama 网关」下新增模型时地址是空的。
        if not str(rec.get("base") or "").strip():
            rec["base"] = self.provider_base(pid)
        if not str(rec.get("key_enc") or "").strip():
            rec["key_enc"] = self.provider_key_enc(pid)
        m = self._clean_model(rec)
        models = self.models()
        for i, old in enumerate(models):
            if old.get("id") == m["id"]:
                models[i] = m
                break
        else:
            models.append(m)
        self.data["models"] = models
        self.save()
        # 改的正好是当前模型 → 立刻生效，不用用户再点一次「设为当前」
        if self.current_id() == m["id"]:
            self.set_active(m["id"])
        return m

    def remove_model(self, mid: str) -> tuple[bool, str]:
        """删模型。最后一条删不掉 —— 删空了助手就没模型可用了。"""
        models = self.models()
        if len(models) <= 1:
            return False, "至少要留一个模型"
        keep = [m for m in models if m.get("id") != mid]
        if len(keep) == len(models):
            return False, "没找到这个模型"
        self.data["models"] = keep
        if self.current_id() == mid:
            self.data["active"] = keep[0]["id"]
            self.save()
            self.set_active(keep[0]["id"])
        else:
            self.save()
        return True, ""

    # ------------------------------------------------------------ 思考档位
    def thinking_levels(self) -> list[str]:
        """当前模型支持的思考档位。空列表 = 这个模型不支持思考。"""
        caps = self.current().get("caps") if isinstance(self.current(), dict) else {}
        th = (caps or {}).get("thinking") or {}
        return [str(x) for x in (th.get("levels") or [])]

    def thinking(self) -> str:
        """这一轮该用哪个档位。

        优先级：用户在输入框旁选的 → 模型配的默认档 → 第一档。
        模型不支持思考就返回空串，调用方据此不发 `reasoning_effort`。
        """
        m = self.current()
        levels = self.thinking_levels()
        if not levels:
            return ""
        want = str(m.get("thinking") or "")
        if want in levels:
            return want
        th = ((m.get("caps") or {}).get("thinking") or {})
        dflt = str(th.get("default") or "")
        return dflt if dflt in levels else levels[0]

    def set_thinking(self, level: str) -> bool:
        """选一个档位并落盘。不在可用档位里就拒绝（返回 False）。"""
        m = self.current()
        levels = self.thinking_levels()
        lv = str(level or "").strip().lower()
        if lv and lv not in levels:
            return False
        m["thinking"] = lv
        self.upsert_model(m)
        return True

    def context_limit(self) -> int:
        """当前模型的上下文窗口。0 = 未知（界面上显示「未知」，不编数字）。"""
        m = self.current()
        try:
            return max(0, int(m.get("context") or m.get("ctx") or 0))
        except (TypeError, ValueError):
            return 0

    def model_key(self, mid: str = "") -> str:
        """取某条模型的口令（默认当前）。解不开返回空串。"""
        rec = self.find_model(mid) if mid else self.current()
        enc = str((rec or {}).get("key_enc") or "")
        if not enc or crypto_store is None:
            return ""
        try:
            return crypto_store.unprotect(enc) or ""
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def seal_key(plain: str) -> str:
        if not plain:
            return ""
        if crypto_store is None:
            return plain
        try:
            return crypto_store.protect(plain) or ""
        except Exception:  # noqa: BLE001
            return plain

    def set_model_key(self, mid: str, plain: str) -> None:
        m = self.find_model(mid)
        if m is None:
            return
        m["key_enc"] = self.seal_key(plain)
        self.upsert_model(m)


def new_model_id() -> str:
    import secrets as _s
    return "m" + _s.token_hex(3)


def probe_model(rec: dict, timeout: int = 20) -> tuple[bool, str]:
    """试连一条模型配置。返回 (能不能用, 一句人话)。

    这是「填完怎么知道对不对」的唯一答案 —— 不然用户只能发一句话看它报不报错。
    """
    rec = rec or {}
    prov = str(rec.get("provider") or DEFAULT_PROVIDER)
    preset = PROVIDERS.get(prov) or {"label": prov, "kind": "openai",
                                     "base": "", "model": "",
                                     "need_key": True, "hint": ""}
    if preset.get("kind") == "dsh":
        exe = find_dsh()
        if exe:
            return True, f"找到 Harness：{exe}"
        return False, "没找到 dsh，可设 DSH_CLI 指向 dsh.cmd"
    base = str(rec.get("base") or preset["base"] or "").rstrip("/")
    model = str(rec.get("model") or preset["model"] or "")
    if not base:
        return False, "还没填接口地址"
    if not model:
        return False, "还没填模型名"
    key = ""
    enc = str(rec.get("key_enc") or "")
    if enc and crypto_store is not None:
        try:
            key = crypto_store.unprotect(enc) or ""
        except Exception:  # noqa: BLE001
            key = ""
    if preset.get("need_key") and not key:
        return False, "这条没有 API Key"
    if not base.endswith("/v1") and "/v1" not in base:
        base += "/v1"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "temperature": 0,
        "max_tokens": 8,
        "stream": False,
    }
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        data = _post_json(base + "/chat/completions", payload, headers,
                          max(8, int(timeout)))
    except AIError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"请求失败：{e}"
    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return False, f"返回格式不认识：{str(data)[:160]}"
    return True, text[:60] or "(连通，返回空文本)"


def model_view(m: dict, active_id: str) -> dict:
    """模型管理表的一行。"""
    m = m or {}
    prov = str(m.get("provider") or "")
    preset = PROVIDERS.get(prov) or {}
    has_key = bool(m.get("key_enc"))
    # 自定义供应商没有 preset，那就按「需要 Key」算 —— 这类线路（网关、
    # 中转站）九成都要鉴权，默认「就绪」会让人以为填不填都行。
    need = bool(preset.get("need_key", True))
    if prov == "dsh":
        ok = bool(find_dsh())
        state = "可用" if ok else "缺 Harness"
        tone = "ok" if ok else "err"
    elif need and not has_key:
        state, tone = "缺 Key", "warn"
    else:
        state, tone = "就绪", "ok"
    base = str(m.get("base") or preset.get("base") or "—")
    ctx = m.get("context") or m.get("ctx") or 0
    return {
        "name": str(m.get("name") or "未命名模型"),
        "provider": preset.get("label") or prov or "—",
        "provider_key": prov,
        "model": str(m.get("model") or preset.get("model") or "—"),
        "base": base,
        # ctx 保留旧键名（老测试与老配置都在用），值仍是上下文窗口
        "ctx": fmt_tokens(ctx),
        "max_in": fmt_tokens(m.get("max_input")),
        "max_out": fmt_tokens(m.get("max_output")),
        "caps": caps_label(m),
        "kind": "文本",
        "temp": f"{float(m.get('temperature') or 0):g}",
        "key": "已存" if has_key else ("不需要" if not need else "未填"),
        "state": state,
        "tone": tone,
        "active": str(m.get("id") or "") == active_id,
        "current": "● 当前" if str(m.get("id") or "") == active_id else "",
        "note": str(m.get("note") or ""),
        "created": fmt_ts(m.get("created")),
        "_id": str(m.get("id") or ""),
        "_raw": m,
    }


def fmt_ts(v) -> str:
    try:
        ts = float(v)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    try:
        return time.strftime("%Y/%m/%d %H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return ""


# ------------------------------------------------------------------ 引擎
class AIError(RuntimeError):
    pass


# ---------------------------------------------------------- 模型调用活动上报
# 用户要求「即使只是调 API，终端里也要看得到输入了什么、输出了什么」。
# 引擎是唯一知道请求全文与响应全文的地方，所以钩子挂在这里，而不是界面层。
# 界面注册一个回调，把 (kind, info) 渲染成几行字写进终端；测试则注册一个
# 收集器，直接断言请求内容 —— 两边共用同一条通道。
_TRACE = None      # fn(kind: str, info: dict) -> None
_TRACE_ON = True


def set_trace(fn) -> None:
    """注册活动回调。传 None 取消。"""
    global _TRACE
    _TRACE = fn


def trace_enabled() -> bool:
    return bool(_TRACE) and _TRACE_ON


def _trace(kind: str, info: dict) -> None:
    if not _TRACE or not _TRACE_ON:
        return
    try:
        _TRACE(kind, info)
    except Exception:  # noqa: BLE001
        # 上报是旁路，不能因为它把一次正常的模型调用带崩
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
        # 思考档位。只有模型自己声明了可用档位、且不是"关"才发 ——
        # 给不认识这个字段的网关乱发参数会直接 400。
        # "off" 的语义是「不发」，不是发 "none"（对齐 DSH 的 efforts.off = null）。
        level = self.cfg.thinking()
        if level and level != "off":
            payload["reasoning_effort"] = level
        headers = {"Accept": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        _trace("req", {"base": base, "model": model, "messages": messages,
                       "provider": str(self.cfg.data.get("provider") or ""),
                       "thinking": level})
        t0 = time.time()
        try:
            data = _post_json(base + "/chat/completions", payload, headers,
                              int(self.cfg.data.get("timeout") or 90))
        except AIError as e:
            _trace("resp", {"ok": False, "ms": int((time.time() - t0) * 1000),
                            "err": str(e)})
            raise
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            _trace("resp", {"ok": False, "ms": int((time.time() - t0) * 1000),
                            "err": f"返回格式不认识：{str(data)[:200]}"})
            raise AIError(f"返回格式不认识：{str(data)[:200]}") from e
        usage = data.get("usage") or {}
        _trace("resp", {"ok": True, "ms": int((time.time() - t0) * 1000),
                        "code": 200, "tokens": usage, "text": text})
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
        _trace("req", {"base": "dsh --profile headless", "model": "dsh-headless",
                       "messages": messages, "provider": "DeepSeek Harness"})
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                               encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as e:
            _trace("resp", {"ok": False, "ms": int((time.time() - t0) * 1000),
                            "err": "dsh 运行超时（300 秒）"})
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
                    _trace("resp", {"ok": False,
                                    "ms": int((time.time() - t0) * 1000),
                                    "err": "dsh：" + str(err["message"])[:300]})
                    raise AIError("dsh：" + str(err["message"])[:300])
        if not text:
            tail = (p.stderr or out)[-300:]
            _trace("resp", {"ok": False, "ms": int((time.time() - t0) * 1000),
                            "err": f"dsh 没有返回内容：{tail.strip()}"})
            raise AIError(f"dsh 没有返回内容：{tail.strip()}")
        _trace("resp", {"ok": True, "ms": int((time.time() - t0) * 1000),
                        "code": "dsh", "tokens": {}, "text": text})
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
