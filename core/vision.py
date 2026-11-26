# -*- coding: utf-8 -*-
"""把本地图片变成模型能吃的 data URL（视觉通道的原料）。

为什么不用 PIL：打包环境里没有第三方包，也不想为了「读一张图」多一条依赖。
我们只做一件事 —— 把原字节按 base64 塞进 `data:image/png;base64,...`，
尺寸/缩放交给模型侧（它自己会缩），这样零解码、零失真、零依赖。

三条硬限制（都是被真实失败逼出来的）：
  · **只认 OpenAI 兼容端点认识的四种**：png / jpeg / gif / webp。
    BMP 看着像图片，但 OpenAI 兼容实现基本都拒收 —— 与其发出去 400，不如
    在这里就说清楚「bmp 不发，转成 png 再来」。
  · 单张 > 4 MB 不发：base64 会再涨 33%，很容易把请求撑到网关的超时/体积墙，
    而且运维场景的截图没有需要这么大才能看懂的。
  · 一轮最多 6 张：再多就不是「看一眼」而是传相册了，宁可让用户分两轮问。

判类型不看扩展名而看魔数 —— 剪贴板落盘的图经常叫 `clip_1.png` 其实是 JPEG，
按扩展名报 MIME 会被服务端拒。
"""
from __future__ import annotations

from pathlib import Path

# 扩展名 → (MIME, 魔数判定函数)。魔数优先，扩展名只在魔数看不出来时兜底。
MAX_BYTES = 4 * 1024 * 1024      # 单张原图上限
MAX_COUNT = 6                    # 一轮最多发几张
SAFE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _kind(head: bytes) -> str:
    """从文件头判断图片类型。返回 mime，认不出返回空串。"""
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return ""


def sniff(path: str | Path) -> str:
    """返回这张图的 MIME；不是我们支持的图片就返回空串（不抛异常）。"""
    p = Path(path)
    try:
        with p.open("rb") as f:
            head = f.read(16)
    except OSError:
        return ""
    mime = _kind(head)
    if mime:
        return mime
    # 魔数认不出：扩展名是四种之一就按扩展名给，剩下的（含 bmp）一律不发。
    ext = p.suffix.lower()
    if ext in SAFE_EXTS:
        return "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
    return ""


def to_data_url(path: str | Path, max_bytes: int = MAX_BYTES) -> tuple[str, str]:
    """图片 → (data URL, 人话)。发不出去时 URL 为空串，人话说明原因。"""
    import base64

    p = Path(path)
    mime = sniff(p)
    if not mime:
        return "", f"{p.name}：不是 png/jpg/gif/webp，没发（常见是 bmp，先另存为 png）"
    try:
        raw = p.read_bytes()
    except OSError as e:
        return "", f"{p.name}：读不了（{e.__class__.__name__}）"
    if not raw:
        return "", f"{p.name}：空文件"
    if len(raw) > max_bytes:
        return "", (f"{p.name}：{len(raw) / 1048576:.1f} MB，超过单张 "
                    f"{max_bytes / 1048576:.0f} MB 上限，没发")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}", ""


def collect(paths, allow: bool = True,
            max_count: int = MAX_COUNT) -> tuple[list[str], list[str]]:
    """一批路径 → (data URL 列表, 没发出去的原因列表)。

    `allow=False` 表示当前模型没勾「视觉」：只回一句原因，不白读文件。
    """
    urls: list[str] = []
    notes: list[str] = []
    seq = list(paths or [])          # 生成器只能走一次，先落成列表
    if not allow:
        if seq:
            notes.append("当前模型没勾「视觉」能力，图片仍按路径处理"
                         "（在模型设置里勾上视觉才会真发图）")
        return urls, notes
    for raw in seq[:max_count]:
        url, why = to_data_url(raw)
        if url:
            urls.append(url)
        elif why:
            notes.append(why)
    extra = len(seq) - max_count
    if extra > 0:
        notes.append(f"还有 {extra} 张没发：一轮最多 {max_count} 张")
    return urls, notes
