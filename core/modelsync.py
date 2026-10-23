# -*- coding: utf-8 -*-
"""模型同步与 Token 限制解析。

两件事，都跟「界面不该瞎猜」有关：

**一、只留文本模型。**
  这个程序只会发 `chat/completions`。服务商的 `/models` 里混着 embedding、
  rerank、TTS、ASR、图像、视频、3D、音乐 —— 这些**永远不可能回答一轮对话**，
  留在模型选择器里只会让人误选，然后收到一个莫名其妙的 400。
  过滤规则照抄 DeepSeek Harness GUI 的 `model-capabilities.json`（那份是拿
  566 个真实模型逐条校对过的），顺序也是照抄的：**非对话规则必须排在视觉规则
  之前**，否则 `kling-image-v3-omni`、`kling-video-v3-omni` 会因为名字里带
  `omni` 被当成多模态对话模型留下。

**二、Token 限制三个值：能抓到就填，抓不到就 None。**
  `context` / `max_input` / `max_output`。OpenAI 官方 `/models` 只回
  `id/created/object/owned_by`，一个都不给；但兼容网关（LiteLLM、OpenRouter、
  vLLM、Ollama、各类中转）常常会给，而且**字段名各家不一样**。
  所以这里对着一堆已知写法挨个找，找不到就返回 `None`。

  关键：**绝不用默认值瞎猜**。返回 0 或者 128000 都会让界面显示一个假的数字，
  用户按这个数字去切会话，切爆了都不知道是谁的错。None 在界面上显示「未知」。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

# --------------------------------------------------------------------- 文本过滤
# 顺序即优先级，第一条命中就定案。
# 非对话路由排最前（含 omni 的那些图像/视频模型），其次是 embedding/rerank，
# 最后才是「看得懂图但能对话」的视觉家族 —— 它们**要留下**。
_NON_CHAT = re.compile(
    r"cosyvoice|paraformer|sambert|whisper|tts|^wanx|(^|/)wan2|kling|"
    r"(^|/)z-image|(^|/)qwen-image$|ocr-guard|3d-|3d$|image-v|image-q|"
    r"vega-image|vega-video|video-|seedream|dubbing|speech|voice-(design|clone)|"
    r"(^|-)asr|mt2|world2|rodin|mureka|music|youtu-vita|hy-role|polygen|"
    r"stable-diffusion|sdxl|dall-e|gpt-image|flux|imagen|midjourney|"
    r"realtime|moderation",
    re.I,
)
_EMBED = re.compile(r"embedding|embed|reranker|(^|/)bge|(^|/)gte-|colbert|"
                    r"(^|/)e5-|m3e|jina-embed|paraphrase", re.I)

# 视觉，但确实能对话 —— 反过来用：命中不改结论，只用来标 modalities
_VISION = re.compile(
    r"-vl-|-vl$|(^|/)vl-|vision|omni|internvl|llava|pixtral|minicpm-v|"
    r"glm-4\.[0-9]+v|glm-5v|kimi-vl|step-3|step-1v|molmo|idefics|"
    r"gpt-4o|gpt-4\.1|claude-3|claude-4|gemini|qwen2\.5-vl|qwen3-vl",
    re.I,
)
_AUDIO_IN = re.compile(r"audio|speech-in|omni", re.I)


def is_text_model(model_id: str) -> bool:
    """这个名字的模型能不能拿来跑一轮对话。"""
    mid = str(model_id or "").strip()
    if not mid:
        return False
    if _NON_CHAT.search(mid):
        return False
    if _EMBED.search(mid):
        return False
    return True


def modality_guess(model_id: str) -> list[str]:
    """从名字猜输入模态。只在同步时用来预勾选，用户随时能改。

    猜错了代价很小（两个复选框），猜对了省一次点击。
    """
    mid = str(model_id or "")
    out = []
    if _VISION.search(mid):
        out.append("vision")
    if _AUDIO_IN.search(mid) and not _NON_CHAT.search(mid):
        out.append("audio")
    return out


def filter_text_models(ids) -> list[str]:
    """过滤 + 去重 + 保持原顺序。"""
    seen = set()
    out = []
    for i in ids or []:
        mid = str(i or "").strip()
        if not mid or mid in seen:
            continue
        if not is_text_model(mid):
            continue
        seen.add(mid)
        out.append(mid)
    return out


# --------------------------------------------------------------------- 能力猜测
# 档位集合 off/low/high/max 与判定规则同样来自 DSH 的 model-capabilities.json
# （那份表拿 566 个真实模型校对过）。这里只用来**预填**，用户随时能改。
_REASONING = re.compile(
    r"deepseek-v4|deepseek-flash|deepseek-v3\.[12]|deepseek-r1|qwen3|glm-4\.7|"
    r"glm-5|kimi-k[2-9]|step-5|ernie-4\.5|ernie-5|minimax-m|mimo-v|hunyuan|"
    r"(^|/)hy[0-9]|o[134](-|$)",
    re.I,
)
_CODER = re.compile(r"qwen.*coder|coder-", re.I)


def guess_caps(model_id: str) -> dict:
    """按名字猜这份模型支持哪些能力。猜不准也不要紧，表单里能改。"""
    mid = str(model_id or "")
    thinking = None
    # Coder 系不带思考；其余命中推理家族的开全档
    if mid and not _CODER.search(mid) and _REASONING.search(mid):
        thinking = {"levels": ["off", "low", "high", "max"], "default": "high"}
    return {"thinking": thinking, "tools": False}


# --------------------------------------------------------------------- 数值解析
# 带单位的手写值用 1024 进制 —— 和界面上的预设胶囊口径一致：
# 胶囊写「128K」填进输入框的就是 131072，那手打「128K」也得是同一个数，
# 否则同一行里两个含义不同的值，谁也说不清到底哪个生效。
_SUFFIX = {"k": 1024, "m": 1024 * 1024, "b": 1024 ** 3}


def to_int(v) -> int | None:
    """把 128000 / "128000" / "128k" / "1M" / "128,000" 统一成整数。

    认不出来就 None —— 不要 0，0 会被当成「已知是 0」。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        n = int(v)
        return n if n > 0 else None
    s = str(v).strip().lower().replace(",", "").replace("_", "")
    if not s:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmb])?", s)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    mul = _SUFFIX.get(m.group(2) or "", 1)
    n = int(n * mul)
    return n if n > 0 else None


# 上下文窗口：先看扁平的常见写法，再看嵌套
_CTX_FLAT = ("context_length", "max_context_length", "context_window",
             "max_model_len", "max_seq_len", "n_ctx", "num_ctx",
             "max_position_embeddings", "context_size", "model_context_length")
_CTX_NEST = (("top_provider", "context_length"),
             ("limits", "context_window"),
             ("limits", "context_length"),
             ("capabilities", "context_length"))

_IN_FLAT = ("max_input_tokens", "input_token_limit", "max_prompt_tokens",
            "max_input", "prompt_token_limit")
_IN_NEST = (("limits", "max_input_tokens"),
            ("limits", "max_prompt_tokens"),)

_OUT_FLAT = ("max_output_tokens", "max_completion_tokens", "output_token_limit",
             "max_tokens", "completion_token_limit", "max_output")
_OUT_NEST = (("top_provider", "max_completion_tokens"),
             ("limits", "max_output_tokens"),
             ("limits", "max_completion_tokens"))


def _pick(src: dict, flat: tuple, nest: tuple = ()) -> int | None:
    for k in flat:
        n = to_int(src.get(k))
        if n:
            return n
    for outer, inner in nest:
        sub = src.get(outer)
        if isinstance(sub, dict):
            n = to_int(sub.get(inner))
            if n:
                return n
    return None


UNKNOWN = {"context": None, "max_input": None, "max_output": None}


def parse_limits(entry) -> dict:
    """从一个 `/models` 条目里抠出三个 token 限制。

    拿不到的键就是 None。**三个键永远都在**，调用方不用 get 判空。
    """
    if isinstance(entry, str):
        return dict(UNKNOWN)
    if not isinstance(entry, dict):
        return dict(UNKNOWN)
    return {
        "context": _pick(entry, _CTX_FLAT, _CTX_NEST),
        "max_input": _pick(entry, _IN_FLAT, _IN_NEST),
        "max_output": _pick(entry, _OUT_FLAT, _OUT_NEST),
    }


def entry_id(entry) -> str:
    """条目里模型名的位置各家不同（OpenAI 是 id，Ollama 是 name/model）。"""
    if isinstance(entry, str):
        return entry.strip()
    if not isinstance(entry, dict):
        return ""
    for k in ("id", "name", "model", "model_name"):
        v = entry.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


# --------------------------------------------------------------------- 拉取
class SyncError(RuntimeError):
    pass


def normalize_base(base: str) -> str:
    b = str(base or "").strip().rstrip("/")
    if not b:
        return ""
    if not b.endswith("/v1") and "/v1" not in b:
        b += "/v1"
    return b


def fetch_models(base: str, key: str = "", timeout: int = 20) -> list:
    """GET {base}/models，原样返回 data 列表（不在这里过滤）。"""
    url = normalize_base(base)
    if not url:
        raise SyncError("还没填接口地址")
    url += "/models"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=max(5, int(timeout))) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:240]
        if e.code in (401, 403):
            raise SyncError(f"认证失败（{e.code}）：检查 API Key。{detail}") from e
        if e.code == 404:
            raise SyncError(f"没有 /models 接口（404）：检查 base 是否要带 /v1。"
                            f"{detail}") from e
        raise SyncError(f"HTTP {e.code}：{detail}") from e
    except urllib.error.URLError as e:
        raise SyncError(f"连不上服务：{e.reason}") from e
    except TimeoutError as e:
        raise SyncError("请求超时") from e
    try:
        doc = json.loads(raw)
    except ValueError as e:
        raise SyncError(f"返回的不是 JSON：{raw[:160]}") from e
    if isinstance(doc, dict):
        for k in ("data", "models", "items", "result"):
            v = doc.get(k)
            if isinstance(v, list):
                return v
        return []
    if isinstance(doc, list):
        return doc
    return []


def sync_text_models(base: str, key: str = "", timeout: int = 20) -> list[dict]:
    """同步入口：拉列表 → 只留文本模型 → 带上能抓到的三个限制。

    返回 `[{"id","context","max_input","max_output","modalities"}]`，
    按 id 排序，`context` 等键可能是 None（未知）。
    """
    entries = fetch_models(base, key, timeout)
    out: dict[str, dict] = {}
    for e in entries:
        mid = entry_id(e)
        if not mid or not is_text_model(mid):
            continue
        if mid in out:
            continue
        lim = parse_limits(e)
        lim["id"] = mid
        lim["modalities"] = modality_guess(mid)
        out[mid] = lim
    return [out[k] for k in sorted(out)]
