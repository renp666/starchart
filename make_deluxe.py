# -*- coding: utf-8 -*-
"""Hybrid deluxe icon: AI-generated gradient tile + exact logo.svg line-art mark."""
import math
from PIL import Image, ImageDraw

SRC = "web/icon-tile-ai.png"  # gpt-image-2 生成的空方牌底图
OUT = "web/icon-deluxe-1024.png"

# 1) locate tile edge (use left/top glow-free bounds; ignore watermark at bottom-right)
im = Image.open(SRC).convert("RGB")
W, H = im.size
px = im.load()
minx, miny = W, H
for y in range(0, H, 2):
    for x in range(0, W // 2, 2):
        r, g, b = px[x, y]
        if max(r, g, b) > 60 and x < minx:
            minx = x
for y in range(0, H // 2, 2):
    for x in range(0, W, 2):
        r, g, b = px[x, y]
        if max(r, g, b) > 60 and y < miny:
            miny = y

S = 1024
L = min(W - 2 * minx, H - 2 * miny)
x0 = (W - L) // 2
y0 = (H - L) // 2
# take the inner body (no glass edge frame / glow), then re-fill full canvas
m = int(L * 0.115)
inner = im.crop((x0 + m, y0 + m, x0 + L - m, y0 + L - m))
tile = inner.resize((S, S), Image.LANCZOS).convert("RGBA")

# brand tint: pull dark navy bottom-right back toward #5B46E0
ov = Image.new("RGBA", (S, S), (0, 0, 0, 0))
od = ImageDraw.Draw(ov)
for i in range(S):
    t = i / S
    a = int(110 * max(0.0, (t - 0.35) / 0.65))
    od.line([(0, i), (S, i)], fill=(91, 70, 224, a))
tile = Image.alpha_composite(tile, ov)

# clean rounded-square edge (applied after overlays)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
tile.putalpha(mask)

# 2) draw exact logo.svg mark (128-space geometry)
img = tile
d = ImageDraw.Draw(img)
STAR = [(72.7, 27.1), (73.4, 56.3), (88.3, 68.0), (69.7, 71.4),
        (55.3, 96.9), (54.6, 67.7), (39.7, 56.0), (58.3, 52.6)]
TRAILS = [(26, 33, 63, 63)]
bbox = [min(p[0] for p in STAR + [(26, 33)]),
        min(p[1] for p in STAR),
        max(p[0] for p in STAR + [(63, 63)]),
        max(p[1] for p in STAR)]
cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
k = 8.4
ox, oy = S / 2 - cx * k, S / 2 - cy * k

def T(x, y):
    return (ox + x * k, oy + y * k)

lw = int(round(7.5 * k * 0.95))
white = (255, 255, 255, 255)
pts = [T(*p) for p in STAR]
d.line(pts + [pts[0]], fill=white, width=lw, joint="curve")
r = lw / 2
for x1, y1, x2, y2 in TRAILS:
    p1, p2 = T(x1, y1), T(x2, y2)
    d.line([p1, p2], fill=white, width=lw)
    for (X, Y) in (p1, p2):
        d.ellipse([X - r, Y - r, X + r, Y + r], fill=white)
for dx, dy in ((0.8125, 0.281), (0.844, 0.719), (0.188, 0.812)):
    X, Y = dx * S, dy * S
    rr = 0.019 * S
    d.ellipse([X - rr, Y - rr, X + rr, Y + rr], fill=white)

img.save(OUT)
img.resize((512, 512), Image.LANCZOS).save("web/icon-deluxe-512.png")
img.resize((512, 512), Image.LANCZOS).save("web/icon-512.png")
print("saved", OUT, "tile origin", (x0, y0, L))
