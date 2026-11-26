# -*- coding: utf-8 -*-
"""真机试视觉：把一张图真的发给模型，看它能不能说出图里有什么。

`_visiontest.py` 验的是「我们把图正确塞进了请求」（离线、假引擎）；
这个脚本验的是「模型真的看懂了」—— 那必须有真 Key、真端点。

用法（三种来源，按手上有什么选）：

    # ① 用程序里已经配好的模型（data/ai.json 的当前模型，勾没勾视觉都发）
    python _visiontry.py

    # ② 临时指定端点与 Key（不动配置，Key 只进这一次请求）
    python _visiontry.py --base https://api.deepseek.com/v1 \
        --model deepseek-chat --key sk-xxxx

    # ③ 换一张图（默认用 screenshots 里最新一张应用截图，内容已知才好判对错）
    python _visiontry.py --image D:\\some\\shot.png

判定标准：回答里出现与图内容相关的词（截图 → 终端/命令行/窗口/按钮），
且不是「我看不到图片」这类话。脚本会把这两点直接打出来，不用自己猜。

注意：Key 走命令行会被 `ps` / 历史记录看见，正式用建议只放环境变量
`TTB_VISION_KEY`，或用程序里已配置的那条。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import aiops                       # noqa: E402
from core import vision as V                 # noqa: E402

#: 回答里出现这些词，说明它至少看到了「这是个界面截图」
HINTS = ("终端", "命令行", "窗口", "界面", "按钮", "菜单", "标签", "输入", "文本",
         "terminal", "console", "window", "button", "menu", "text", "screen",
         "tab", "line")


def pick_image(given: str) -> Path | None:
    if given:
        return Path(given)
    shots = sorted((HERE / "screenshots").glob("*.png"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return shots[0] if shots else None


def main() -> int:
    ap = argparse.ArgumentParser(description="把一张图真发给模型，验证视觉通道")
    ap.add_argument("--base", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--key", default="")
    ap.add_argument("--image", default="")
    ap.add_argument("--prompt", default="这张图里是什么？用两三句话说清你看到的内容。")
    a = ap.parse_args()

    cfg = aiops.AIConfig(HERE / "data" / "ai.json")
    base = (a.base or str(cfg.data.get("base") or "")).rstrip("/")
    model = a.model or str(cfg.data.get("model") or "")
    key = a.key or os.environ.get("TTB_VISION_KEY") or ""
    if not key:
        try:
            key = cfg.get_key() or ""
        except Exception:  # noqa: BLE001
            key = ""

    img = pick_image(a.image)
    if img is None or not img.is_file():
        print("找不到可用的图片：给一个 --image 路径，或让 screenshots/ 里有 png")
        return 2
    print(f"图：{img}（{img.stat().st_size / 1024:.1f} KB）")

    url, why = V.to_data_url(img)
    if not url:
        print("这张图发不出去：", why)
        return 2
    print(f"编码：data URL {len(url) / 1024:.1f} KB"
          f"（原图 {img.stat().st_size / 1024:.1f} KB，base64 膨胀 ~33%）")

    if not base or not model:
        print("没有可用的端点/模型名：先在程序「设置 → 模型」里配一条，"
              "或用 --base/--model 指定")
        return 2
    if not key:
        print("\n没有 Key：这条路径需要真凭据。三种给法——")
        print("  1) 程序里配好的模型（设置 → 模型 → 填 Key）后直接重跑本脚本")
        print("  2) 环境变量：set TTB_VISION_KEY=sk-xxx")
        print("  3) 临时：python _visiontry.py --key sk-xxx")
        print("\n（下面这些都不用重试，配置没变就还是没 Key）")
        return 2

    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": a.prompt},
            {"type": "image_url", "image_url": {"url": url}},
        ]}],
        "temperature": 0,
        "max_tokens": 400,
        "stream": False,
    }
    if not base.endswith("/v1") and "/v1" not in base:
        base += "/v1"
    endpoint = base + "/chat/completions"
    print(f"请求：POST {aiops_short(base)}  ·  {model}")

    t0 = time.time()
    req = urllib.request.Request(
        endpoint, data=json.dumps(body, ensure_ascii=False).encode(),
        method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        detail = ""
        if hasattr(e, "read"):
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                detail = ""
        print(f"\n请求失败：{type(e).__name__}: {e}")
        if detail:
            print("服务端说：", detail)
        print("\n常见原因：模型不支持图片输入（换成带视觉的模型）/ Key 无效 / "
              "网关拒收 image_url（只支持纯文本）")
        return 1
    ms = int((time.time() - t0) * 1000)

    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        print("\n返回格式不认识：", json.dumps(data, ensure_ascii=False)[:400])
        return 1

    usage = data.get("usage") or {}
    print(f"\n== 模型回答（{ms} ms，tokens {usage.get('total_tokens', '?')}）")
    print(text or "(空)")
    hit = [h for h in HINTS if h.lower() in text.lower()]
    print("\n== 判定")
    print("  看图迹象：", ("有 —— " + "、".join(hit[:5])) if hit
          else "没有（回答里找不到任何与图相关的词，可能它没真的看图）")
    if not text:
        print("  回答是空的：多半模型拒收图片，或 max_tokens 太小")
        return 1
    return 0


def aiops_short(base: str) -> str:
    """只显示主机名，别把带凭据的完整 URL 打出来。"""
    try:
        from urllib.parse import urlsplit
        u = urlsplit(base)
        return f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")
    except Exception:  # noqa: BLE001
        return base


if __name__ == "__main__":
    sys.exit(main())
