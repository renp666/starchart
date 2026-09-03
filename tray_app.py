# -*- coding: utf-8 -*-
"""星图 StarChart 常驻入口：系统托盘 + 全局热键 + 后台 HTTP 服务。

用法：
    pythonw tray_app.py   无控制台窗口（start.bat 使用）
    python   tray_app.py  保留控制台，便于排障（start-console.bat 使用）

设计取舍：
- 全局热键用 ctypes 直接调 win32 RegisterHotKey，不引入 keyboard 之类
  需要管理员权限的库——新增依赖只有 pystray 一个。
- 未安装 pystray 时自动回落到「无托盘」模式：服务照常运行，只是没有托盘图标。
- 端口已被占用时不报错退出，而是假定已有实例在运行并直接唤起它的界面。
"""
import math
import os
import subprocess
import sys
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import server  # noqa: E402

# 全局热键 Ctrl+Alt+S
HOTKEY_ID = 1
WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_ALT = 0x0001
VK_S = 0x53


def echo(msg):
    """pythonw 下 sys.stdout 为 None，print 会被静默吞掉；统一走这里，失败也不影响运行。"""
    try:
        print(msg)
    except Exception:
        pass


def tray_image():
    """用 Pillow 手绘图标：紫靛渐变圆角方牌 + 白色四芒星火 + 两道流星轨迹。
    高对比、小尺寸清晰，不依赖字体；与网页 SVG / make_icons.py 同一套几何
    （128 坐标系、顺时针旋转 14°、平移 (6,20) 缩放 1.8）。"""
    from PIL import Image, ImageDraw

    size = 256
    radius = int(size * 0.21)

    # 圆角渐变方牌
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    top, bottom = (139, 124, 248), (91, 70, 224)
    base = Image.new("RGB", (size, size))
    gd = ImageDraw.Draw(base)
    for y in range(size):
        t = y / (size - 1)
        gd.line([(0, y), (size, y)],
                fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.paste(base.convert("RGBA"), (0, 0), mask)

    # 顶部高光（alpha_composite 叠加；paste 会直接把底色替换成半透明白）
    ov = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(ov).rounded_rectangle([0, 0, size - 1, int(size * 0.5)],
                                         radius=radius, fill=(255, 255, 255, 38))
    img = Image.alpha_composite(img, ov)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius,
                        outline=(255, 255, 255, 80), width=3)

    # 白色四芒星火（4 外点 + 4 内凹点交替）
    rot = math.radians(14.0)
    rc, rs = math.cos(rot), math.sin(rot)
    q = 11.0 * math.cos(math.radians(45.0))
    local = [(0.0, -36.0), (q, -q), (25.0, 0.0), (q, q),
             (0.0, 36.0), (-q, q), (-25.0, 0.0), (-q, -q)]
    cx, cy, sc = 64.0 * 1.8 + 6, 62.0 * 1.8 + 20, 1.8
    pts = []
    for lx, ly in local:
        rx, ry = lx * rc - ly * rs, lx * rs + ly * rc
        pts.append((cx + rx * sc, cy + ry * sc))

    # 流星轨迹：斜向轨迹画到星体中心，镂空内部可见穿过；两端补圆头
    # （9 点方向水平轨迹与星火左尖角方向重叠，应用图标省略）
    tw = 13.5
    for x1, y1, x2, y2 in ((26, 33, 63, 63),):
        X1, Y1 = x1 * 1.8 + 6, y1 * 1.8 + 20
        X2, Y2 = x2 * 1.8 + 6, y2 * 1.8 + 20
        d.line([(X1, Y1), (X2, Y2)], fill=(255, 255, 255, 255), width=int(tw))
        r = tw / 2.0
        d.ellipse([X1 - r, Y1 - r, X1 + r, Y1 + r], fill=(255, 255, 255, 255))
        d.ellipse([X2 - r, Y2 - r, X2 + r, Y2 + r], fill=(255, 255, 255, 255))

    # 白色镂空星火：闭合描边（round join），不填充
    d.line(pts + [pts[0]], fill=(255, 255, 255, 255), width=int(tw), joint="curve")

    for x, y, rpx, a in ((208, 78, 3, 160), (214, 188, 3, 140), (58, 208, 2.6, 140)):
        d.ellipse([x - rpx, y - rpx, x + rpx, y + rpx], fill=(255, 255, 255, a))
    return img


def acquire_singleton():
    """Windows 命名互斥量：保证同一台机器只跑一个托盘服务实例。

    返回互斥量句柄（进程存活期间需保持引用），返回 None 表示已有实例在运行。
    进程退出时系统自动释放互斥量，不会受崩溃留下的残留文件影响。
    """
    import ctypes

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        return None
    name = "Local\\StarChart_Tray_Singleton"
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return None
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return None
    return handle


def register_hotkey(url):
    """独立线程中运行消息循环，监听全局热键。

    注册失败（热键被别的程序占用）时静默降级，其余功能不受影响。
    唤起界面统一走 server.open_ui（带去重），避免连发弹出多个窗口。
    """
    import ctypes
    from ctypes import wintypes

    try:
        user32 = ctypes.windll.user32
    except Exception as e:
        server.log(f"无法加载 user32，热键不可用：{e}")
        return

    if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_S):
        server.log("全局热键 Ctrl+Alt+S 注册失败，可能已被其他程序占用")
        echo("提示：全局热键 Ctrl+Alt+S 注册失败，可能已被其他程序占用。")
        return

    server.log("全局热键 Ctrl+Alt+S 已注册")
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            server.open_ui(url)
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    user32.UnregisterHotKey(None, HOTKEY_ID)


def main():
    # 单实例锁：本机只允许一个星图托盘进程。已有实例时直接唤起它的界面并退出，
    # 从源头杜绝重复绑定端口、叠加托盘、重复弹窗口。
    singleton = acquire_singleton()
    if singleton is None:
        server.log("检测到已有实例运行，仅唤起界面后退出")
        echo("星图已在运行，正在打开界面…")
        server.open_ui()
        return

    try:
        httpd, url = server.create_server()
    except OSError as e:
        # 端口已被占用：多半是已有星图在跑，直接唤起它的界面，而不是报错退出
        cfg = server.load_config()
        url = f"http://127.0.0.1:{int(cfg.get('port', 6173))}"
        server.log(f"端口无法绑定（{e}），假定已有实例运行于 {url}，直接唤起界面")
        echo(f"星图疑似已在运行，正在打开 {url}")
        server.open_ui(url)
        return

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Thread(target=register_hotkey, args=(url,), daemon=True).start()
    server.log(f"服务已启动 {url}（托盘常驻模式）")
    echo(f"星图 StarChart 已在后台启动：{url}")
    echo("托盘图标已就绪；Ctrl+Alt+S 随时唤起；右键托盘图标可退出。")
    # 首次启动主动打开界面（与 server.py 行为一致），不再要求用户手动去点托盘
    if os.environ.get("STARCHART_NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: server.open_ui(url)).start()

    try:
        import pystray
    except ImportError:
        server.log("未安装 pystray，回落到无托盘模式（服务继续运行）")
        echo("未安装 pystray，托盘不可用；执行 pip install pystray 后可启用。")
        echo("服务仍在运行，按 Ctrl+C 或双击 stop.bat 结束。")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return

    def on_open(icon, item):
        server.open_ui(url)

    def on_rescan(icon, item):
        threading.Thread(target=server.run_scan, daemon=True).start()

    def on_data_dir(icon, item):
        try:
            subprocess.Popen(["explorer", server.DATA_DIR])
        except OSError as e:
            server.log(f"打开数据目录失败：{e}")

    def on_quit(icon, item):
        server.log("通过托盘菜单退出")
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("打开星图", on_open, default=True),
        pystray.MenuItem("重新扫描", on_rescan),
        pystray.MenuItem("打开数据目录", on_data_dir),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_quit),
    )
    icon = pystray.Icon("StarChart", tray_image(), "星图 StarChart", menu)
    icon.run()

    try:
        httpd.shutdown()
    except Exception:
        pass
    server.log("服务已停止")


if __name__ == "__main__":
    main()
