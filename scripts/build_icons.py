# -*- coding: utf-8 -*-
"""构建桌面快捷方式图标套装（深色玻璃渐变风）。

流程：内置 SVG 源稿（256 网格）→ Playwright 驱动系统 Edge 光栅化为 256px PNG
     → Pillow 合成 256/128/64/48/32/16 多尺寸 ICO。
产物：web/icons/shortcuts/*.ico（随仓库分发，运行时由 lnk 的 IconLocation 引用）。
重跑本脚本即可在改稿后一键重建，无需任何第三方图像库（Pillow + Edge 除外）。
"""
import os
import sys

from PIL import Image

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_DIR = os.path.join(BASE, "web", "icons", "shortcuts")
SRC_DIR = os.path.join(ICON_DIR, "src")
PNG_SIZE = 256

# ---------- 统一视觉语言 ----------
# 深色玻璃方块：垂直暗渐变 + 顶部品牌紫晕 + 顶部斜面高光 + 发丝描边。
FRAME = """<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#2d2d36"/>
    <stop offset="0.55" stop-color="#1d1d24"/>
    <stop offset="1" stop-color="#141419"/>
  </linearGradient>
  <radialGradient id="glow" cx="0.5" cy="-0.12" r="0.95">
    <stop offset="0" stop-color="#8b7cf8" stop-opacity="0.42"/>
    <stop offset="0.55" stop-color="#8b7cf8" stop-opacity="0.08"/>
    <stop offset="1" stop-color="#8b7cf8" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#ffffff" stop-opacity="0.13"/>
    <stop offset="0.6" stop-color="#ffffff" stop-opacity="0.02"/>
    <stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>
  <clipPath id="clip"><rect x="8" y="8" width="240" height="240" rx="56"/></clipPath>
</defs>
<g clip-path="url(#clip)">
  <rect x="8" y="8" width="240" height="240" rx="56" fill="url(#bg)"/>
  <rect x="8" y="8" width="240" height="240" fill="url(#glow)"/>
  <rect x="8" y="8" width="240" height="150" fill="url(#sheen)"/>
</g>
<!-- 无描边框：圆角外保持全透明，桌面底色自然透出 -->
"""

WHITE = "#f4f5ff"
ACCENT = "#9a8cf8"
SW = 11  # 主描边粗细（粗，保证 16px 下不糊）

# ---------- 8 枚符号 ----------
# 1) 星图：logo 同源北极星 + 星座连线 + 小星（连线在星后方、不穿星）
SYM_STARCHART = f"""
<g stroke="{WHITE}" stroke-width="5" stroke-linecap="round" opacity="0.5" fill="none">
  <line x1="58" y1="74" x2="92" y2="100"/>
  <line x1="168" y1="172" x2="184" y2="190"/>
</g>
<circle cx="56" cy="72" r="6" fill="{ACCENT}" stroke="none"/>
<circle cx="188" cy="194" r="5" fill="{WHITE}" stroke="none" opacity="0.8"/>
<g transform="translate(-19 -11) scale(9.2)">
  <polygon points="18.2,6.8 18.4,14.1 22.1,17.0 17.4,17.9 13.8,24.2 13.6,16.9 9.9,14.0 14.6,13.1"
           fill="{WHITE}"/>
</g>
"""

# 2) Node：六边形节点（外描边 + 紫芯）
SYM_NODE = f"""
<polygon points="128,56 192,93 192,167 128,204 64,167 64,93"
         fill="none" stroke="{WHITE}" stroke-width="{SW}" stroke-linejoin="round"/>
<polygon points="128,96 156,112 156,148 128,164 100,148 100,112"
         fill="{ACCENT}"/>
"""

# 3) Python：双蛇咬合的极简抽象（上白填 / 下白描，孔位用紫/底点）
SYM_PYTHON = f"""
<rect x="88" y="64" width="80" height="54" rx="25" fill="{WHITE}"/>
<circle cx="150" cy="84" r="7.5" fill="#1d1d24"/>
<rect x="88" y="142" width="80" height="54" rx="25" fill="none"
      stroke="{WHITE}" stroke-width="{SW}"/>
<circle cx="106" cy="176" r="7.5" fill="{ACCENT}"/>
"""

# 4) Go：倾斜速度轨道 + 端头箭头
SYM_GO = f"""
<g transform="rotate(-18 128 132)">
  <ellipse cx="128" cy="132" rx="76" ry="42" fill="none"
           stroke="{WHITE}" stroke-width="{SW}"/>
</g>
<polygon points="216,104 194,90 198,115" fill="{WHITE}"/>
<circle cx="118" cy="126" r="11" fill="{ACCENT}"/>
"""

# 5) Docker：集装箱 + 弧形船背
SYM_DOCKER = f"""
<g fill="{WHITE}">
  <rect x="86" y="140" width="27" height="27" rx="5"/>
  <rect x="117" y="140" width="27" height="27" rx="5"/>
  <rect x="148" y="140" width="27" height="27" rx="5"/>
  <rect x="102" y="109" width="27" height="27" rx="5"/>
  <rect x="133" y="109" width="27" height="27" rx="5"/>
</g>
<path d="M58 184 q70 26 140 0" fill="none" stroke="{WHITE}"
      stroke-width="{SW}" stroke-linecap="round"/>
"""

# 6) CMD：终端窗口 + 提示符
SYM_CMD = f"""
<rect x="58" y="78" width="140" height="100" rx="14" fill="none"
      stroke="{WHITE}" stroke-width="{SW}"/>
<polyline points="92,114 112,130 92,146" fill="none" stroke="{ACCENT}"
      stroke-width="{SW+1}" stroke-linecap="round" stroke-linejoin="round"/>
<line x1="120" y1="148" x2="158" y2="148" stroke="{WHITE}"
      stroke-width="{SW+1}" stroke-linecap="round"/>
"""

# 7) Make/通用任务：播放圆牌
SYM_MAKE = f"""
<circle cx="128" cy="130" r="72" fill="none" stroke="{WHITE}" stroke-width="{SW}"/>
<polygon points="118,102 118,158 166,130" fill="{ACCENT}" stroke-linejoin="round"/>
"""

# 8) 通用项目：文件夹 + 小星
SYM_GENERIC = f"""
<path d="M58 100 h46 l15 17 h79 v77 H58 Z" fill="none"
      stroke="{WHITE}" stroke-width="{SW}" stroke-linejoin="round" stroke-linecap="round"/>
<path d="M172 62 l4.6 10.4 11.4 1.2-8.5 7.8 2.4 11.2-9.9-5.8-9.9 5.8 2.4-11.2-8.5-7.8 11.4-1.2 Z"
      fill="{ACCENT}"/>
"""

ICONS = {
    "starchart": SYM_STARCHART,
    "node": SYM_NODE,
    "python": SYM_PYTHON,
    "go": SYM_GO,
    "docker": SYM_DOCKER,
    "cmd": SYM_CMD,
    "make": SYM_MAKE,
    "generic": SYM_GENERIC,
}

SVG_TPL = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
    "{frame}{sym}</svg>"
)


def write_svgs():
    os.makedirs(SRC_DIR, exist_ok=True)
    for name, sym in ICONS.items():
        p = os.path.join(SRC_DIR, f"{name}.svg")
        with open(p, "w", encoding="utf-8") as f:
            f.write(SVG_TPL.format(frame=FRAME, sym=sym))
        print("svg:", p)


def render_pngs():
    """用系统 Edge 离屏渲染 SVG（与 favicon/前端同一渲染引擎，零色差）。"""
    from playwright.sync_api import sync_playwright

    pngs = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": PNG_SIZE, "height": PNG_SIZE})
        page.set_content(
            "<!doctype html><style>*{margin:0;padding:0}</style>"
            f"<svg xmlns='http://www.w3.org/2000/svg' width='{PNG_SIZE}' "
            f"height='{PNG_SIZE}' viewBox='0 0 256 256'>{{body}}</svg>"
        )
        for name, sym in ICONS.items():
            # 直接把完整 SVG 塞进页面（file:// 在部分机器上被策略拦截，data-URI 更稳）
            svg = SVG_TPL.format(frame=FRAME, sym=sym)
            page.set_content(
                "<!doctype html><meta charset='utf-8'>"
                "<style>*{margin:0;padding:0}html,body{background:transparent}</style>"
                f"<div style='width:{PNG_SIZE}px;height:{PNG_SIZE}px'>{svg}</div>"
            )
            png_path = os.path.join(SRC_DIR, f"{name}.png")
            # omit_background：圆角外保持透明，绝不能烘成白底（桌面小图会显白框）
            page.locator("svg").screenshot(path=png_path, omit_background=True)
            pngs[name] = png_path
        browser.close()
    return pngs


def build_icos(pngs):
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    os.makedirs(ICON_DIR, exist_ok=True)
    for name, png_path in pngs.items():
        img = Image.open(png_path).convert("RGBA")
        ico_path = os.path.join(ICON_DIR, f"{name}.ico")
        img.save(ico_path, format="ICO", sizes=sizes)
        print("ico:", ico_path, os.path.getsize(ico_path), "bytes")


def main():
    write_svgs()
    pngs = render_pngs()
    build_icos(pngs)
    print("DONE", len(ICONS), "icons ->", ICON_DIR)


if __name__ == "__main__":
    sys.exit(main())
