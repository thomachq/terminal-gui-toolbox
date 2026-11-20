# -*- coding: utf-8 -*-
"""生成主机清单行首的按钮组图（开发时跑一次，产物入库）。

运行时只读 PNG（tk.PhotoImage），不依赖 Pillow —— 打包环境没有它。

一行图片里并排两个按钮：左「连接/断开」+ 右「编辑 ✎」。ttk.Treeview 的
data 列放不了按钮，只有 #0 树列支持 image=，所以主机表用 DataTable 的
icon_col 把这一整组按钮画在行首，点击时按左右半分发到不同动作。

DPI：按 96 DPI 基准出图，SS=3 超采样存高分辨率图；运行时按 wbtui.S
缩放回当前 DPI，字号与表格文字一致。
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "assets"
FONT = "C:/Windows/Fonts/msyhbd.ttc"
SS = 3                              # 超采样倍数

# 96 DPI 基准逻辑像素
H = 24                              # 按钮高
CONN_W = 52                         # 连接/断开宽
EDIT_W = 44                         # 编辑宽
GAP = 6                             # 两按钮间距
TOTAL_W = CONN_W + GAP + EDIT_W
R = 6                               # 圆角
FONTPT = 11                         # 基准字号（表格文字 10pt，按钮略大一点点）

# 与 wbtui 亮色主题一致
BRAND, BRAND_HOVER = "#3A66AC", "#4E79BE"
ERR, ERR_HOVER = "#D93B34", "#E4544D"
SOFT, SOFT_FG = "#E8EEF9", "#3A66AC"   # 编辑按钮：浅蓝底 + 蓝字（secondary）


def _button(d, x0, w, label, fill, fg):
    # x0/w 都是 96 DPI 基准逻辑像素，先乘 SS 换成出图像素
    x0, w = x0 * SS, w * SS
    d.rounded_rectangle([x0 + 1, 1, x0 + w - 1, H * SS - 1],
                        radius=R * SS, fill=fill)
    font = ImageFont.truetype(FONT, FONTPT * SS)
    bb = d.textbbox((0, 0), label, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    x = x0 + (w - tw) / 2 - bb[0]
    y = (H * SS - th) / 2 - bb[1] - SS
    d.text((x, y), label, font=font, fill=fg)


def draw(conn_label, conn_fill):
    img = Image.new("RGBA", (TOTAL_W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _button(d, 0, CONN_W, conn_label, conn_fill, "#FFFFFF")
    _button(d, CONN_W + GAP, EDIT_W, "编辑", SOFT, SOFT_FG)
    return img


def main():
    for key, label, fill in (("conn", "连接", BRAND),
                             ("disc", "断开", ERR)):
        img = draw(label, fill)
        p = OUT / f"btns_{key}.png"
        img.save(p)
        print("wrote", p.name, img.size)
    print("基准尺寸", TOTAL_W, "x", H, "| 连接宽", CONN_W, "| 编辑起点", CONN_W + GAP)


if __name__ == "__main__":
    main()
