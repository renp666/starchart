# -*- coding: utf-8 -*-
"""生成星图 StarChart 的位图图标（纯几何绘制，不依赖任何字体文件）。

图形与 web/icon.svg / web/logo.svg 同源：倾斜 14° 的四芒星火 + 斜向流星轨迹
（轨迹穿过星体中部；9 点方向水平轨迹与星火左尖角方向重叠，应用图标省略）。
小尺寸场景衬在紫靛渐变圆角方牌上，星火用白色镂空描边——与 logo 图形标同一
视觉语言，16px 下依然清晰。产物（均为小尺寸场景，扁平高对比最清晰）：
    web/favicon.ico    浏览器标签图标（16/32/48 多尺寸）
    web/tray-64.png    托盘图标（Windows 托盘推荐 64×64）

大尺寸图标（关于页 / README 用 web/icon-512.png）为 gpt-image-2 生成的
精致版方牌 + 同一套几何描边星火，由 make_deluxe.py 合成（master 见
web/icon-deluxe-1024.png），不在本脚本生成。

用法：python make_icons.py
"""
import math
import os

from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")

# 紫靛渐变（与 logo.svg / 网页内联 SVG 同色）
PURPLE_TOP = (139, 124, 248)
PURPLE_BOT = (91, 70, 224)
WHITE = (255, 255, 255, 255)

# ---- 星火几何：128 坐标系，顺时针旋转 14°（与 web/icon.svg 完全同源）----
ROT = math.radians(14.0)
RV, RH, RI = 36.0, 25.0, 11.0          # 外长轴 / 外短轴 / 内凹半径
# 轨迹与 icon.svg 一致：斜向轨迹一直画到星体中心附近，镂空处可见穿过；
# 9 点方向水平轨迹与星火左尖角方向重叠，应用图标中省略
TRAILS = ((26.0, 33.0, 63.0, 63.0),)   # 斜向轨迹
# 星点：相对坐标（0~1）、相对半径、不透明度，避开星火主体
DOTS = ((0.812, 0.305, 0.012, 160),
        (0.836, 0.734, 0.012, 140),
        (0.227, 0.812, 0.010, 140))

# 128 坐标系 → 256 基准画布：平移 (6,20)、缩放 1.8
SHIFT_X, SHIFT_Y, SCALE = 6.0, 20.0, 1.8


def sparkle_points(cx, cy, scale):
    """四芒星火的 8 个顶点（4 外点 + 4 内凹点交替），已旋转平移。"""
    q = RI * math.cos(math.radians(45.0))
    local = [(0.0, -RV), (q, -q), (RH, 0.0), (q, q),
             (0.0, RV), (-q, q), (-RH, 0.0), (-q, -q)]
    c, s = math.cos(ROT), math.sin(ROT)
    pts = []
    for x, y in local:
        rx, ry = x * c - y * s, x * s + y * c
        pts.append((cx + rx * scale, cy + ry * scale))
    return pts


def rounded_badge(size):
    """紫靛渐变圆角方牌 + 顶部高光 + 细边框。"""
    radius = size * 0.21
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=255)
    base = Image.new("RGB", (size, size))
    gd = ImageDraw.Draw(base)
    for y in range(size):
        t = y / (size - 1)
        gd.line([(0, y), (size, y)],
                fill=tuple(int(PURPLE_TOP[i] + (PURPLE_BOT[i] - PURPLE_TOP[i]) * t)
                           for i in range(3)))
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.paste(base.convert("RGBA"), (0, 0), mask)
    # 顶部高光：alpha_composite 叠加（paste 会直接替换掉底色色值）
    ov = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(ov).rounded_rectangle(
        [0, 0, size - 1, int(size * 0.5)], radius=radius,
        fill=(255, 255, 255, 38))
    img = Image.alpha_composite(img, ov)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius,
                        outline=(255, 255, 255, 70),
                        width=max(1, int(size * 0.012)))
    return img


def make_icon(size):
    img = rounded_badge(size)
    d = ImageDraw.Draw(img)
    k = size / 256.0
    cx = (64.0 * SCALE + SHIFT_X) * k
    cy = (62.0 * SCALE + SHIFT_Y) * k
    sc = SCALE * k

    # 流星轨迹：与 logo 相同画到星体中心，镂空内部可见穿过；两端补圆头
    tw = max(1.0, 13.5 * k)
    for x1, y1, x2, y2 in TRAILS:
        X1, Y1 = (x1 * SCALE + SHIFT_X) * k, (y1 * SCALE + SHIFT_Y) * k
        X2, Y2 = (x2 * SCALE + SHIFT_X) * k, (y2 * SCALE + SHIFT_Y) * k
        d.line([(X1, Y1), (X2, Y2)], fill=WHITE, width=int(tw))
        r = tw / 2.0
        d.ellipse([X1 - r, Y1 - r, X1 + r, Y1 + r], fill=WHITE)
        d.ellipse([X2 - r, Y2 - r, X2 + r, Y2 + r], fill=WHITE)

    # 白色镂空星火：闭合描边（round join），不填充——与 logo 图形标同一语言
    star = sparkle_points(cx, cy, sc)
    d.line(star + [star[0]], fill=WHITE, width=int(tw), joint="curve")

    for px, py, pr, a in DOTS:
        x, y, r = px * size, py * size, pr * size
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, a))
    return img


def main():
    os.makedirs(WEB_DIR, exist_ok=True)

    big = make_icon(512)
    big.save(os.path.join(WEB_DIR, "icon-512.png"))

    tray = make_icon(64)
    tray.save(os.path.join(WEB_DIR, "tray-64.png"))

    # favicon：一份文件内含 16/32/48 三档，浏览器按场景自动选用
    fav = make_icon(256)
    fav.save(os.path.join(WEB_DIR, "favicon.ico"),
             format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])

    for name in ("icon-512.png", "tray-64.png", "favicon.ico"):
        p = os.path.join(WEB_DIR, name)
        print(f"已生成 {name}  {os.path.getsize(p)} 字节")


if __name__ == "__main__":
    main()
