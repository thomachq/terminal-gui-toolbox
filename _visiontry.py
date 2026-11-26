# -*- coding: utf-8 -*-
"""真机试视觉：把一张图真的发给模型，看它能不能说出图里有什么。

`_visiontest.py` 验的是「我们把图正确塞进了请求」（离线、假引擎）；
这个脚本验的是「模型真的看懂了」—— 那必须有真 Key、真端点。

**它走的是产品代码**（`aiops.make_engine(cfg).chat()`），不是自己拼 urllib：
预算抬高、空回答提示、思考 token 统计这些都在引擎里，脚本另写一份就成了
第二套口径（第一版就是这么写的，结果 max_tokens=400 被思考吃光，看到「空回答」
还以为模型不支持图）。

用法：

    # ① 用「能解开 Key」的那份配置（自动挑：TTB_DATA → 安装目录 → 仓库树）
    python _visiontry.py

    # ② 只跑一次能力探针（1×1 图，几十 token，不花大钱）
    python _visiontry.py --probe

    # ③ 临时指定端点 / 模型 / Key（不动配置）
    python _visiontry.py --base https://api.deepseek.com/v1 \
        --model deepseek-flash --key sk-xxxx

    # ④ 换一张图（默认 screenshots 里最新一张，内容已知才好判对错）
    python _visiontry.py --image D:\\some\\shot.png

判定：回答里出现与图内容相关的词（截图 → 终端/命令行/窗口/按钮…），
且不是「我看不到图片」这类话；两者都会直接打出来。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
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


def short(base: str) -> str:
    """只显示主机名，别把带凭据的完整 URL 打出来。"""
    try:
        from urllib.parse import urlsplit
        u = urlsplit(base)
        return f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")
    except Exception:  # noqa: BLE001
        return base


def main() -> int:
    ap = argparse.ArgumentParser(description="把一张图真发给模型，验证视觉通道")
    ap.add_argument("--base", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--key", default="")
    ap.add_argument("--image", default="")
    ap.add_argument("--data", default="", help="data 目录（默认自动挑 key 能解开的）")
    ap.add_argument("--probe", action="store_true", help="只跑能力探针（1×1 图）")
    ap.add_argument("--prompt", default="这张图里是什么？用两三句话说清你看到的内容。")
    a = ap.parse_args()

    # 一站式入口：连密钥目录一起指对。别自己拼 AIConfig(path) ——
    # crypto_store 的密钥目录是进程级全局，配错了 get_key() 只会安静地返回空，
    # 表现成「项目没配 Key」（2026-11-26 就这么误判过一次）。
    cfg, meta = aiops.open_config(a.data or None)
    print(f"配置：{meta['data_dir']}")
    print(f"      key：{'可解开' if meta['key_ok'] else '不可用 —— ' + meta['key_error']}")

    for k, v in (("base", a.base), ("model", a.model)):
        if v:
            cfg.data[k] = v
    if a.key:
        cfg.set_key(a.key) if hasattr(cfg, "set_key") else None

    base = str(cfg.data.get("base") or "").rstrip("/")
    model = str(cfg.data.get("model") or "")
    src = cfg.cap_src("vision")
    origin = src.get("from") or ("user" if "vision" in (cfg.data.get("modalities") or []) else "未知")
    print(f"模型：{model} | 视觉判定：{'会发图' if cfg.vision_ok() else '不发图'}"
          f" | 来源：{origin}")

    if a.probe:
        print("\n== 探针（1×1 红图，几十 token）")
        t0 = time.time()
        ok, why = cfg.probe_vision(remember=not (a.base or a.model or a.key))
        print(f"  {'通过' if ok else '失败'}（{int((time.time() - t0) * 1000)} ms）：{why}")
        return 0 if ok else 1

    img = pick_image(a.image)
    if img is None or not img.is_file():
        print("找不到可用的图片：给一个 --image 路径，或让 screenshots/ 里有 png")
        return 2
    url, why_bad = V.to_data_url(img)
    if not url:
        print("这张图发不出去：", why_bad)
        return 2
    print(f"图：{img.name}（{img.stat().st_size / 1024:.1f} KB）"
          f" → data URL {len(url) / 1024:.1f} KB")

    if not base or not model:
        print("没有可用的端点/模型名：先在「设置 → 模型」里配一条，或用 --base/--model")
        return 2
    if not cfg.get_key() and not cfg.preset().get("kind") == "dsh":
        print("\n没有 Key：这条路径需要真凭据。三种给法——")
        print("  1) 程序里配好的模型（设置 → 模型 → 填 Key）后直接重跑")
        print("  2) 环境变量：set TTB_VISION_KEY=sk-xxx（本脚本也认）")
        print("  3) 临时：python _visiontry.py --key sk-xxx")
        return 2

    print(f"请求：POST {short(base)}/chat/completions  ·  {model}")
    messages = [{"role": "user", "content": [
        {"type": "text", "text": a.prompt},
        {"type": "image_url", "image_url": {"url": url}},
    ]}]
    t0 = time.time()
    try:
        text, usage = aiops.make_engine(cfg).chat(messages)
    except Exception as e:  # noqa: BLE001
        print(f"\n请求失败：{type(e).__name__}: {e}")
        print("常见原因：模型不支持图片输入 / Key 无效 / 网关只收纯文本")
        return 1
    ms = int((time.time() - t0) * 1000)

    print(f"\n== 模型回答（{ms} ms，tokens {usage.get('total') or '?'}"
          f"，思考 {usage.get('reasoning') or 0}，预算 {usage.get('budget') or '?'}）")
    print(text or "(空)")
    if usage.get("note"):
        print("\n引擎提示：", usage["note"])
    hit = [h for h in HINTS if h.lower() in str(text).lower()]
    print("\n== 判定")
    print("  看图迹象：", ("有 —— " + "、".join(hit[:5])) if hit
          else "没有（回答里找不到任何与图相关的词，可能它没真的看图）")
    if not str(text or "").strip():
        print("  回答是空的：先看上面的「引擎提示」——多半是思考把预算吃光了，"
              "或模型拒收图片（可以 python _visiontry.py --probe 单独探一次）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
