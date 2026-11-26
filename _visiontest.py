# -*- coding: utf-8 -*-
"""自测：视觉通道（图片 → data URL → 消息里的 image_url）。

不联网、不读真实照片：全部用临时文件 + 假引擎。
真模型认不认图，是另一件事（那要真 key 真请求，写在这里只会变成一次性的运气）。

    python _visiontest.py

要验的三层：
  1. `core/vision.py` 认得什么、拒收什么、超限怎么说话；
  2. `Agent.build_messages` 带图时把「任务」那条换成 content 数组，
     且**文本不能丢**（这是最容易写错、也最难在真机上发现的地方）；
  3. `Agent.run` 真的把图递到了引擎手上（假引擎拦下来看 messages）。
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import agent as A                     # noqa: E402
from core import aiops                          # noqa: E402
from core import vision as V                    # noqa: E402

OK = 0
BAD = 0


def chk(cond, msg, extra=None):
    global OK, BAD
    if cond:
        OK += 1
        print(f"  [ok] {msg}" + (f"  —— {extra}" if extra not in (None, "") else ""))
    else:
        BAD += 1
        print(f"  [NG] {msg}" + (f"  —— {extra}" if extra not in (None, "") else ""))


def sec(t):
    print(f"\n-- {t}")


# 1x1 的真 PNG（标准最小样本），以及各类型的文件头
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")
JPEG_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
GIF_HEAD = b"GIF89a"
WEBP_HEAD = b"RIFF\x24\x00\x00\x00WEBPVP8 "
BMP_HEAD = b"BM\x36\x00\x00\x00"

tmp = Path(tempfile.mkdtemp(prefix="_vistest_"))
p_png = tmp / "shot.png"
p_png.write_bytes(PNG_1X1)
p_jpg = tmp / "clip_1.png"                      # 扩展名骗人：内容其实是 JPEG
p_jpg.write_bytes(JPEG_HEAD + b"\x00" * 64)
p_gif = tmp / "a.gif"
p_gif.write_bytes(GIF_HEAD + b"\x00" * 32)
p_webp = tmp / "a.webp"
p_webp.write_bytes(WEBP_HEAD + b"\x00" * 32)
p_bmp = tmp / "a.bmp"
p_bmp.write_bytes(BMP_HEAD + b"\x00" * 32)
p_txt = tmp / "note.txt"
p_txt.write_text("hello", encoding="utf-8")
p_big = tmp / "big.png"
p_big.write_bytes(PNG_1X1 + b"\x00" * (V.MAX_BYTES + 8))

sec("1. sniff 按文件头认类型")
chk(V.sniff(p_png) == "image/png", "PNG", V.sniff(p_png))
chk(V.sniff(p_jpg) == "image/jpeg", "扩展名是 .png 但内容是 JPEG，按内容认", V.sniff(p_jpg))
chk(V.sniff(p_gif) == "image/gif", "GIF", V.sniff(p_gif))
chk(V.sniff(p_webp) == "image/webp", "WEBP", V.sniff(p_webp))
chk(V.sniff(p_bmp) == "", "BMP 认不出（OpenAI 兼容端点基本都拒收）", V.sniff(p_bmp) or "<空>")
chk(V.sniff(p_txt) == "", "文本文件不是图片", V.sniff(p_txt) or "<空>")
chk(V.sniff(tmp / "没有这个文件.png") == "", "文件不存在不抛异常", "<空>")

sec("2. to_data_url 往返")
url, why = V.to_data_url(p_png)
chk(url.startswith("data:image/png;base64,"), "前缀是标准 data URL", url[:30] + "…")
back = base64.b64decode(url.split(",", 1)[1])
chk(back == PNG_1X1, "解回来与原字节逐字节相同", f"{len(back)} 字节")
chk(why == "", "成功时不带噪音说明")

url2, why2 = V.to_data_url(p_bmp)
chk(url2 == "" and "bmp" in why2.lower(), "BMP 不发，并说清原因", why2)
url3, why3 = V.to_data_url(p_big)
chk(url3 == "" and "上限" in why3, "超 4 MB 不发，并报出实际大小", why3)

sec("3. collect：开关、上限、跳过坏文件")
urls, notes = V.collect([p_png, p_jpg, p_bmp], allow=True)
chk(len(urls) == 2, "3 个里 2 个能发（bmp 被跳过）", f"{len(urls)} 张")
chk(any("bmp" in n.lower() for n in notes), "跳过原因带回去给界面", notes[0] if notes else "")
urls2, notes2 = V.collect([p_png], allow=False)
chk(urls2 == [] and any("视觉" in n for n in notes2), "模型没勾视觉 → 一张都不发，只说原因")
many = [p_png] * (V.MAX_COUNT + 2)
urls3, notes3 = V.collect(many, allow=True)
chk(len(urls3) == V.MAX_COUNT, f"一轮最多 {V.MAX_COUNT} 张", f"{len(urls3)} 张")
chk(any("最多" in n for n in notes3), "超出部分说明白")

sec("4. build_messages：带图时改成 content 数组，文本不能丢")
cfg = A.AgentConfig(tmp / "agent.json")
llm = aiops.AIConfig(tmp / "ai.json")
ag = A.Agent(cfg, llm)
msgs = ag.build_messages("看看这张图", "设备：本机", [], "", [url])
last = msgs[-2] if len(msgs) >= 2 else msgs[-1]
goal_msg = next(m for m in msgs if m["role"] == "user"
                and isinstance(m.get("content"), list))
parts = goal_msg["content"]
chk(isinstance(parts, list) and len(parts) == 2, "内容是一条 text + 一条 image_url",
    [p.get("type") for p in parts])
chk(parts[0] == {"type": "text", "text": "任务：看看这张图"}, "文本部分完整保留",
    parts[0].get("text"))
chk(parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"] == url,
    "图片部分就是那个 data URL")
chk(all(isinstance(m["content"], str) for m in msgs
        if m["role"] in ("system", "assistant")),
    "system 与历史消息仍是字符串（不能全改成数组）")

msgs_no = ag.build_messages("看看这张图", "设备：本机", [], "")
chk(all(isinstance(m["content"], str) for m in msgs_no), "不带图时一条数组都不出现")

sec("5. run 把图递到引擎手上（假引擎拦截）")
seen: list[list[dict]] = []


class FakeEngine:
    def chat(self, messages):
        seen.append(messages)
        return "看完了：图里是一个白点。", {"total": 12}


_real_make = aiops.make_engine
aiops.make_engine = lambda _cfg: FakeEngine()
try:
    res = ag.run("看看这张图", lambda *_a, **_k: (True, "ok"), context="设备：本机",
                 images=[url])
finally:
    aiops.make_engine = _real_make

chk(len(seen) >= 1, "引擎至少被调了一次（run 没提前退出）", f"{len(seen)} 次")
sent = seen[0] if seen else []
with_img = [m for m in sent if isinstance(m.get("content"), list)
            and any(p.get("type") == "image_url" for p in m["content"])]
chk(len(with_img) == 1, "发出去的 messages 里有且只有一条带图", f"{len(with_img)} 条")
chk(bool(res.get("answer")), "回答落进了结果", str(res.get("answer"))[:24])

sec("6. AI 活动流 / Harness 通道：base64 不能刷屏")
from core import aitrace                      # noqa: E402

lines = aitrace.api_request(
    base="https://example.com/v1", model="m",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "任务：看看这张图"},
        {"type": "image_url", "image_url": {"url": url}}]}])
body = " ".join(t for t, _tag in lines[1:])
chk("base64" not in body, "活动流里没有 base64 正文", body[:60] + "…")
chk("[图片 1 张" in body, "改成报「图片 N 张 + 体积」", body[:80])
chk(len(body) < 400, "一段请求最多几百字符，不会刷屏", f"{len(body)} 字符")

flat = aiops._plain([{"type": "text", "text": "任务：看看这张图"},
                     {"type": "image_url", "image_url": {"url": url}}])
chk("附了 1 张图片" in flat and len(flat) < 200,
    "Harness 命令行通道降级成一句占位（不撞 32K 命令行上限）", flat)

print(f"\n合计 {OK + BAD} 项：通过 {OK}，失败 {BAD}")
sys.exit(1 if BAD else 0)
