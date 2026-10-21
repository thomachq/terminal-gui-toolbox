# -*- coding: utf-8 -*-
"""生成应用图标 assets/app.ico —— Terminal GUI Toolbox。

构成：品牌蓝渐变圆角底 + 深色终端窗口 + `>` 提示符与光标 + 右下角齿轮（工具箱）。
4 倍超采样后缩放，保证 16px 下仍能看出"终端"这个主体。
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUT = HERE / "assets"
OUT.mkdir(exist_ok=True)

BRAND = (0x3A, 0x66, 0xAC, 255)
BRAND_DEEP = (0x22, 0x40, 0x6E, 255)
TERM_BG = (0x0D, 0x1A, 0x2B, 255)
TERM_BAR = (0x1B, 0x30, 0x4C, 255)
WHITE = (255, 255, 255, 255)
ACCENT = (0x5A, 0xD8, 0xC8, 255)      # 光标用的青绿，呼应终端感

S = 1024          # 超采样画布
K = S / 256.0     # 相对 256 设计稿


def sc(v: float) -> float:
    return v * K


def gear(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
         teeth: int, fill) -> None:
    """实心齿轮：轮体 + 辐射齿。"""
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
    tw = r * 0.40          # 齿宽（半）
    th = r * 0.60          # 齿高（超出轮体）
    for i in range(teeth):
        a = 2 * math.pi * i / teeth
        ca, sa = math.cos(a), math.sin(a)
        p1 = (cx + (r - 1) * ca - tw * sa, cy + (r - 1) * sa + tw * ca)
        p2 = (cx + (r - 1) * ca + tw * sa, cy + (r - 1) * sa - tw * ca)
        p3 = (cx + (r + th) * ca + tw * sa, cy + (r + th) * sa - tw * ca)
        p4 = (cx + (r + th) * ca - tw * sa, cy + (r + th) * sa + tw * ca)
        d.polygon([p1, p2, p3, p4], fill=fill)


def draw(size: int, with_gear: bool = True) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ---- 圆角底：竖向渐变
    grad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(size):
        t = y / max(1, size - 1)
        c = tuple(int(BRAND_DEEP[i] + (BRAND[i] - BRAND_DEEP[i]) * t) for i in range(3))
        gd.line([(0, y), (size, y)], fill=(c[0], c[1], c[2], 255))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [sc(4), sc(4), sc(252), sc(252)], radius=sc(54), fill=255)
    img.paste(grad, (0, 0), mask)

    # ---- 终端窗口（不带齿轮的小尺寸版本放大一点，填满画面）
    if with_gear:
        x1, y1, x2, y2 = sc(38), sc(56), sc(214), sc(196)
    else:
        x1, y1, x2, y2 = sc(30), sc(46), sc(226), sc(208)
    d.rounded_rectangle([x1, y1, x2, y2], radius=sc(16), fill=TERM_BG)
    d.rounded_rectangle([x1, y1, x2, y1 + sc(26)], radius=sc(16), fill=TERM_BAR)
    d.rectangle([x1, y1 + sc(14), x2, y1 + sc(26)], fill=TERM_BAR)
    for i in range(3):
        cx = x1 + sc(15) + i * sc(13)
        d.ellipse([cx - sc(3.2), y1 + sc(9.4), cx + sc(3.2), y1 + sc(15.8)],
                  fill=(255, 255, 255, 90))

    # ---- `>` 提示符
    lw = int(sc(9))
    if with_gear:
        ax, ay, bx, by = sc(64), sc(110), sc(92), sc(134)
        cy2 = sc(158)
        cx1, cx2, cy1b, cy2b = sc(104), sc(136), sc(150), sc(161)
    else:
        ax, ay, bx, by = sc(58), sc(104), sc(90), sc(132)
        cy2 = sc(160)
        cx1, cx2, cy1b, cy2b = sc(104), sc(142), sc(150), sc(162)
    d.line([ax, ay, bx, by], fill=WHITE, width=lw, joint="curve")
    d.line([bx, by, ax, cy2], fill=WHITE, width=lw, joint="curve")

    # ---- 光标 `_`
    d.rounded_rectangle([cx1, cy1b, cx2, cy2b], radius=sc(4), fill=ACCENT)

    # ---- 右下角齿轮（工具箱）；小尺寸会糊成一团，故省略
    if with_gear:
        gx, gy, gr = sc(189), sc(185), sc(19)
        d.ellipse([gx - gr - sc(7), gy - gr - sc(7),
                   gx + gr + sc(7), gy + gr + sc(7)], fill=BRAND_DEEP)
        gear(d, gx, gy, gr, 8, WHITE)
        d.ellipse([gx - sc(6.5), gy - sc(6.5), gx + sc(6.5), gy + sc(6.5)],
                  fill=(0x2C, 0x4E, 0x86, 255))

    return img


master = draw(S)
sizes = [16, 24, 32, 48, 64, 128, 256]
frames = [master.resize((s, s), Image.LANCZOS) if s >= 32
          else draw(S, with_gear=False).resize((s, s), Image.LANCZOS)
          for s in sizes]
ico = OUT / "app.ico"
frames[-1].save(ico, format="ICO", sizes=[(s, s) for s in sizes])
frames[-1].save(OUT / "app.png")

# 小尺寸并排预览，肉眼核对 16/32/48 的辨识度
strip = Image.new("RGBA", (16 + 32 + 48 + 8 * 4, 56), (245, 249, 255, 255))
xx = 8
for s in (16, 32, 48):
    f = master.resize((s, s), Image.LANCZOS)
    strip.paste(f, (xx, (56 - s) // 2), f)
    xx += s + 8
strip.save(OUT / "preview_small.png")

print("wrote", ico, ico.stat().st_size, "bytes")
print("wrote", OUT / "app.png")
print("wrote", OUT / "preview_small.png")
