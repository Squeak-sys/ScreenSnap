# -*- coding: utf-8 -*-
"""生成 ScreenSnap 图标：深蓝圆角底 + 白色相机（清晰易懂）。运行一次即可。"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PNG = os.path.join(HERE, "icon.png")
OUT_ICO = os.path.join(HERE, "icon.ico")

SIZE = 256


def make_icon(size=SIZE):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 256.0

    # 圆角背景
    d.rounded_rectangle([8 * s, 8 * s, 248 * s, 248 * s],
                        radius=56 * s, fill=(37, 99, 235, 255))

    # 相机机身（白色圆角矩形）
    d.rounded_rectangle([46 * s, 96 * s, 210 * s, 190 * s],
                        radius=20 * s, fill=(255, 255, 255, 255))

    # 顶部取景器
    d.rounded_rectangle([96 * s, 70 * s, 160 * s, 100 * s],
                        radius=10 * s, fill=(255, 255, 255, 255))

    # 镜头外圈
    d.ellipse([86 * s, 116 * s, 170 * s, 200 * s], fill=(37, 99, 235, 255))
    # 镜头内圈
    d.ellipse([106 * s, 136 * s, 150 * s, 180 * s], fill=(24, 74, 184, 255))
    # 镜头高光
    d.ellipse([112 * s, 142 * s, 130 * s, 160 * s], fill=(255, 255, 255, 160))

    # 快门按钮
    d.ellipse([178 * s, 84 * s, 196 * s, 102 * s], fill=(255, 196, 0, 255))

    return img


if __name__ == "__main__":
    icon = make_icon(SIZE)
    icon.save(OUT_PNG, "PNG")
    # 多尺寸 ico
    icon.resize((64, 64), 3).save(OUT_ICO, "ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("图标已生成：", OUT_PNG)
    print("图标已生成：", OUT_ICO)
