# -*- coding: utf-8 -*-
"""星图 StarChart —— 本地项目导航工具后端
扫描工作区、生成中文介绍、路径跳转、备份导入导出。
仅监听 127.0.0.1。
"""

import atexit
import base64
import io
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import fnmatch
import hashlib
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlparse, parse_qs, quote
from html import unescape as html_unescape

try:
    from pypinyin import lazy_pinyin

    def pinyin_of(name):
        parts = [p for p in lazy_pinyin(name) if p]
        initials = "".join(p[0] for p in parts)
        full = "".join(parts)
        return (initials + " " + full).lower()
except ImportError:  # 未安装 pypinyin 时仅支持名称/路径搜索

    def pinyin_of(name):
        return ""


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
# 数据目录：默认与代码同级（starchart\.starchart\），工具自包含——
# 拷走整个 starchart 文件夹即为完整迁移；可用 STARCHART_HOME 覆盖到任意位置。
DATA_DIR = os.environ.get("STARCHART_HOME") or os.path.join(BASE_DIR, ".starchart")
DATA_FILE = os.path.join(DATA_DIR, "data.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
LOG_FILE = os.path.join(DATA_DIR, "server.log")

PROJECT_MARKERS = [
    ".git",
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "README.md",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "app.py",
]
HEAVY_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}

MAX_SCAN_DEPTH = 12  # 目录递归最大深度，防止超深目录栈溢出/异常结构
MAX_LOG_BYTES = 1024 * 1024  # 日志超过 1MB 轮转一份旧日志
GIT_TIMEOUT = 2.0  # 单个 git 命令超时（秒），超时即跳过，不拖慢整体扫描
GIT_WORKERS = 6  # 并发查询 Git 状态的线程数
GEN_WORKERS = 3  # 并发生成介绍的线程数（AI 接口通常有限流，不宜过高）
FILE_SEARCH_LIMIT = 300  # 项目内文件名搜索的最大结果数
FILE_SEARCH_DEPTH = 6  # 项目内文件名搜索的最大层级

LOCK = threading.RLock()
_log_lock = threading.Lock()
_config = None
_data = None
_migrated = False


# ---------------------------------------------------------------- 日志


def log(msg):
    """服务日志：记录会执行外部进程的操作，供事后追溯。写失败不影响主流程。"""
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    try:
        with _log_lock:
            try:
                if (
                    os.path.isfile(LOG_FILE)
                    and os.path.getsize(LOG_FILE) > MAX_LOG_BYTES
                ):
                    os.replace(LOG_FILE, LOG_FILE + ".1")
            except OSError:
                pass
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        pass


def _quarantine(path):
    """JSON 损坏时改名保留，避免被静默覆盖。"""
    try:
        os.replace(path, path + ".corrupt-" + datetime.now().strftime("%Y%m%d%H%M%S"))
    except OSError:
        pass


def _legacy_data_dirs():
    """改名/旧版本留下的数据目录候选（按优先级）：
    1) 改名前的同级 .starchart（StarChart 时代）
    2) v1.2 之前的「server.py 往上两级」.starchart
    """
    return [
        os.path.join(BASE_DIR, ".starchart"),
        os.path.join(os.path.dirname(os.path.dirname(BASE_DIR)), ".starchart"),
    ]


def _migrate_data_home():
    """把旧目录的数据搬进工具目录，使「拷走 starchart 文件夹」即为完整迁移。

    只复制、不删除旧目录；已存在同名文件时不覆盖，避免来回切换目录造成覆盖。
    """
    global _migrated
    if _migrated:
        return
    _migrated = True
    if os.environ.get("STARCHART_HOME"):
        return  # 显式指定了目录时不自动迁移
    migrated_from = []
    for legacy in _legacy_data_dirs():
        if os.path.normcase(os.path.abspath(legacy)) == os.path.normcase(
            os.path.abspath(DATA_DIR)
        ):
            continue
        if not os.path.isdir(legacy):
            continue
        os.makedirs(DATA_DIR, exist_ok=True)
        moved = []
        for name in ("data.json", "config.json"):
            src, dst = os.path.join(legacy, name), os.path.join(DATA_DIR, name)
            if os.path.isfile(src) and not os.path.isfile(dst):
                try:
                    shutil.copy2(src, dst)
                    moved.append(name)
                except OSError as e:
                    log(f"迁移 {name} 失败：{e}")
        if moved:
            migrated_from.append(legacy)
            log(f"已从旧目录 {legacy} 迁移：{'、'.join(moved)}")
    if migrated_from:
        msg = (
            f"已把数据从旧目录迁移到 {DATA_DIR}。旧目录保留未删，"
            f"确认新目录数据无误后可自行清理。"
        )
        log(msg)
        print(msg)


# ---------------------------------------------------------------- 配置与数据


def default_config():
    workspace = os.path.dirname(os.path.dirname(BASE_DIR))
    return {
        "port": 6173,
        "roots": [workspace],
        "blacklist": [
            "05zip",
            "node_modules",
            ".git",
            ".trae",
            "新建文件夹",
            ".agents",
            ".starchart",
            ".vscode",
            ".idea",
            "__pycache__",
            ".venv",
        ],
        "api": {"provider": "zhipu", "baseUrl": "", "apiKey": "", "model": ""},
        "editor": {"name": "VS Code", "cmd": 'code "{path}"'},
        "backupDir": "",
        "theme": "auto",
        "gitStatus": True,  # 扫描时读取 Git 状态（分支 / 未提交 / 最后提交）
        "autoScan": True,  # 服务启动时后台自动扫描一次，保持索引最新（可关）
        "ghToken": "",  # GitHub Token（可选）：填了可提高 api.github.com 限流额度
        "ghMirror": "",  # GitHub 镜像/加速前缀（可选）：ghproxy 类加速站，直连失败时用它重试；被墙 / raw 取不到 README 时填入
    }


def load_config():
    global _config
    with LOCK:
        if _config is None:
            _migrate_data_home()
            # pi-lens-ignore: unchecked-throwing-call-python
            os.makedirs(DATA_DIR, exist_ok=True)
            if os.path.isfile(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, encoding="utf-8") as f:
                        _config = json.load(f)
                except Exception:
                    _config = None
                    _quarantine(CONFIG_FILE)
                    log("配置文件损坏，已备份为 .corrupt-* 并使用默认配置")
            if _config is None:
                _config = default_config()
                save_config()
            else:
                # 旧默认编辑器（Trae）一次性迁移为更通用的 VS Code——
                # 仅当值与旧默认完全一致（即用户从未改过）时才替换
                ed = _config.get("editor")
                if (
                    isinstance(ed, dict)
                    and ed.get("name") == "Trae"
                    and ed.get("cmd") == 'trae "{path}"'
                ):
                    _config["editor"] = {"name": "VS Code", "cmd": 'code "{path}"'}
                    save_config()
                # 结构兜底：旧版/手改过的 config 里 api/editor 可能缺失或非对象，
                # 而设置保存路径会直接写 cfg["api"][...]，缺了这个 dict 会整页报错。
                if not isinstance(_config.get("api"), dict):
                    _config["api"] = {"provider": "zhipu", "baseUrl": "", "apiKey": "", "model": ""}
                if not isinstance(_config.get("editor"), dict):
                    _config["editor"] = {"name": "VS Code", "cmd": 'code "{path}"'}
                if _config.get("theme") not in ("dark", "light", "auto"):
                    _config["theme"] = "auto"
        return _config


def save_config():
    with LOCK:
        # pi-lens-ignore: unchecked-throwing-call-python
        os.makedirs(DATA_DIR, exist_ok=True)
        # 与 data.json 一致采用原子写，避免写入中断产生损坏的配置
        tmp = CONFIG_FILE + ".tmp"
        # pi-lens-ignore: unchecked-throwing-call-python
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_config, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_FILE)


DPAPI_PREFIX = "dpapi:"


def _dpapi_encrypt(text):
    """Windows DPAPI（当前用户级，DATA_PROTECTION_LOCAL_USER）加密文本，返回 base64。

    失败（非 Windows / ctypes 异常）返回 None，调用方回落到明文存储。
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
            ]

        raw = text.encode("utf-8")
        buf = ctypes.create_string_buffer(raw)
        blob = _BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        out = _BLOB()
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(out)
        ):
            return None
        ct = ctypes.string_at(out.pbData, out.cbData)
        ctypes.windll.kernel32.LocalFree(out.pbData)
        return base64.b64encode(ct).decode("ascii")
    except Exception:
        return None


def _dpapi_decrypt(b64):
    """DPAPI 解密密文，返回原文；失败（密文来自其他机器/用户）返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes

        class _BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
            ]

        enc = base64.b64decode(b64)
        buf = ctypes.create_string_buffer(enc)
        blob = _BLOB(len(enc), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        out = _BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(out)
        ):
            return None
        ct = ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
        ctypes.windll.kernel32.LocalFree(out.pbData)
        return ct
    except Exception:
        return None


def encrypt_api_key(key):
    """保存 API key 时加密：DPAPI 可用则存 `dpapi:base64`，否则回落明文。"""
    if not key or key.startswith(DPAPI_PREFIX):
        return key  # 已是密文（或为空），不改写
    enc = _dpapi_encrypt(key)
    if enc is None:
        log("DPAPI 不可用，API key 以明文保存到 config.json")
        return key
    return DPAPI_PREFIX + enc


def api_key(cfg=None):
    """返回解密后的 API key；兼容未升级前存的明文旧格式。解密失败（如备份跨机）返回空串。"""
    cfg = cfg or load_config()
    key = (cfg.get("api") or {}).get("apiKey") or ""
    if key.startswith(DPAPI_PREFIX):
        dec = _dpapi_decrypt(key[len(DPAPI_PREFIX) :])
        if dec is None:
            log("API key 解密失败（可能来自其他电脑/用户），请到设置中重新填写")
            return ""
        return dec
    return key


def load_data():
    global _data
    with LOCK:
        if _data is None:
            if os.path.isfile(DATA_FILE):
                try:
                    with open(DATA_FILE, encoding="utf-8") as f:
                        _data = json.load(f)
                except Exception:
                    _data = None
                    _quarantine(DATA_FILE)
                    log("数据文件损坏，已备份为 .corrupt-* ，请重新扫描或从备份恢复")
            if _data is None:
                _data = {"version": 1, "generatedAt": "", "tree": None}
        return _data


def save_data():
    with LOCK:
        # pi-lens-ignore: unchecked-throwing-call-python
        os.makedirs(
            DATA_DIR, exist_ok=True
        )  # 使用记录等早期写入路径可能先于 load_config 触达
        # 原子写：先写临时文件再替换，避免并发/中断产生损坏的 JSON
        tmp = DATA_FILE + ".tmp"
        # pi-lens-ignore: unchecked-throwing-call-python
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)


_saved_pending = False


def save_data_debounced(delay=0.35):
    """合并高频写（如批量生成介绍逐条写）：窗口内多次修改只落盘一次。

    复用 save_data 的同步原子写；由后台线程兜底，进程退出 / 备份导出等
    关键时刻用 flush_data 强制落盘防丢。
    """
    global _saved_pending
    with LOCK:
        if _saved_pending:
            return  # 已有一次即将落盘，本次修改累积到 _data 里即可
        _saved_pending = True
    threading.Thread(target=_debounced_worker, args=(delay,), daemon=True).start()


def _debounced_worker(delay):
    time.sleep(delay)
    with LOCK:
        global _saved_pending
        if not _saved_pending:
            return
        _saved_pending = False
        save_data()


def flush_data():
    """强制立即落盘（备份导出/导入、进程退出前调用）。"""
    with LOCK:
        global _saved_pending
        if _saved_pending:
            _saved_pending = False
            save_data()


atexit.register(flush_data)


# ---------------------------------------------------------------- 扫描


def is_blacklisted(name, blacklist):
    low = name.lower()
    for pat in blacklist:
        pat = pat.strip().lower()
        if pat and (low == pat or fnmatch.fnmatch(low, pat)):
            return True
    return False


def has_project_markers(path, entries):
    for m in PROJECT_MARKERS:
        if m in entries:
            return True
    return any(e.lower().endswith(".sln") for e in entries)


def fingerprint(path):
    cnt, mx = 0, 0.0
    for root, dirs, files in os.walk(path):
        depth = root[len(path) :].count(os.sep)
        if depth >= 2:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in HEAVY_DIRS]
        for f in files:
            cnt += 1
            # pi-lens-ignore: SIM105
            try:
                mx = max(mx, os.path.getmtime(os.path.join(root, f)))
            except OSError:
                pass
    return hashlib.md5(f"{cnt}:{mx}".encode()).hexdigest()[:8]


def detect_launchers(path, entries):
    """检测项目可启动入口，返回 [{kind, label, ...}]，最多 6 个。"""
    launchers = []
    lower = {e.lower(): e for e in entries}
    pj = lower.get("package.json")
    if pj:
        try:
            with open(os.path.join(path, pj), encoding="utf-8", errors="ignore") as f:
                scripts = json.load(f).get("scripts") or {}
            for s in ("dev", "start", "serve"):
                if s in scripts:
                    launchers.append(
                        {
                            "kind": "npm",
                            "script": s,
                            "label": "npm start" if s == "start" else f"npm run {s}",
                        }
                    )
        except Exception:
            pass
    for fname, args in (
        ("app.py", ""),
        ("main.py", ""),
        ("run.py", ""),
        ("manage.py", "runserver"),
    ):
        real = lower.get(fname)
        if real:
            cmd = f"python {real}" + (f" {args}" if args else "")
            launchers.append({"kind": "python", "file": real, "label": cmd})
    for fname in ("run.bat", "start.bat", "run.cmd", "start.cmd"):
        real = lower.get(fname)
        if real:
            launchers.append({"kind": "bat", "file": real, "label": real})
    if any(
        k in lower
        for k in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml")
    ):
        launchers.append({"kind": "docker", "label": "docker compose up"})
    mk = lower.get("makefile")
    if mk:
        try:
            with open(os.path.join(path, mk), encoding="utf-8", errors="ignore") as f:
                content = f.read().lower()
            if "\nrun:" in content or content.startswith("run:"):
                launchers.append({"kind": "make", "label": "make run"})
        except OSError:
            pass
    return launchers[:6]


def detect_stack(path, entries):
    """根据标志文件与依赖推断技术栈，最多 3 个标签。

    仅用于展示；推断不出就返回空列表，不影响项目本身。
    """
    lower = {e.lower() for e in entries}
    stack = []

    if "package.json" in lower:
        real = next((e for e in entries if e.lower() == "package.json"), "package.json")
        deps = ""
        try:
            with open(os.path.join(path, real), encoding="utf-8", errors="ignore") as f:
                pj = json.load(f)
            deps = " ".join(
                list((pj.get("dependencies") or {}).keys())
                + list((pj.get("devDependencies") or {}).keys())
            ).lower()
        except Exception:
            pass
        for key, label in (
            ("next", "Next.js"),
            ("nuxt", "Nuxt"),
            ("svelte", "Svelte"),
            ("vue", "Vue"),
            ("react", "React"),
            ("electron", "Electron"),
            ("express", "Express"),
            ("vite", "Vite"),
        ):
            if key in deps and label not in stack:
                stack.append(label)
        stack.append("Node.js")

    for marker, label in (
        ("go.mod", "Go"),
        ("cargo.toml", "Rust"),
        ("requirements.txt", "Python"),
        ("pyproject.toml", "Python"),
        ("setup.py", "Python"),
        ("pom.xml", "Java"),
        ("build.gradle", "Java"),
        ("composer.json", "PHP"),
        ("cmakelists.txt", "C/C++"),
    ):
        if marker in lower and label not in stack:
            stack.append(label)

    if ".NET" not in stack and any(k.endswith((".sln", ".csproj")) for k in lower):
        stack.append(".NET")

    return stack[:3]


# pi-lens-ignore: E741
def launcher_cmd(l):
    """仅接受扫描时生成的安全参数（字母/数字/点/横线），杜绝命令注入。"""
    safe = re.compile(r"^[\w][\w.\-]*$")
    k = l.get("kind")
    if k == "npm":
        s = l.get("script") or ""
        if s == "start":
            return "npm start"
        return f"npm run {s}" if safe.match(s) else ""
    if k == "python":
        f = l.get("file") or ""
        return f"python {f}" if safe.match(f) else ""
    if k == "bat":
        f = l.get("file") or ""
        return f"cmd /c {f}" if safe.match(f) else ""
    if k == "docker":
        return "docker compose up"
    if k == "make":
        return "make run"
    return ""


def ps_quote(s):
    """PowerShell 单引号字符串转义：' → ''"""
    return str(s).replace("'", "''")


RUNNING = {}  # path -> [{pid, cmd}]


def _persist_running():
    """把运行状态写回 data.json 顶层 running 字段。

    调用方需已持有 LOCK（或处于 do_POST 的处理流程中，接口均为串行处理
    except 由 _gen_lock 等并发场景保护——这里统一由 save_data 的 RLock 兜底）。
    """
    with LOCK:
        load_data()["running"] = RUNNING
        save_data()


def restore_running():
    """服务启动时从 data.json 恢复运行状态，只保留仍然存活的进程。

    目的：星图重启后，之前启动的项目不会变成孤儿进程——
    「运行中 / 停止」按钮在重启后依然可用。
    """
    global RUNNING
    with LOCK:
        data = load_data()
        raw = data.get("running") or {}
        RUNNING = {}
        for k, entries in raw.items():
            if not os.path.isdir(k):
                continue  # 项目目录已被删除/移动，丢弃记录
            alive = [e for e in entries if e.get("pid") and pid_alive(e["pid"])]
            if alive:
                RUNNING[k] = alive
        # pi-lens-ignore: SIM300
        if RUNNING != raw:
            data["running"] = RUNNING
            save_data()
        if RUNNING:
            log(f"已恢复 {sum(len(v) for v in RUNNING.values())} 个运行中进程")


def dir_mtime(path):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).isoformat(
            timespec="seconds"
        )
    except OSError:
        return ""


def build_tree(path, blacklist, old_idx, stats, depth=0):
    name = os.path.basename(path) or path
    old = old_idx.get(path, {})
    marked = old.get("marked", "auto")
    try:
        entries = os.listdir(path)
    except OSError:
        entries = []

    is_project = marked == "manual" or (
        marked != "off" and has_project_markers(path, entries)
    )
    too_deep = depth >= MAX_SCAN_DEPTH

    node = {
        "name": name,
        "path": path,
        "type": "project" if is_project else "dir",
        "mtime": dir_mtime(path),
        "children": [],
    }
    node["py"] = pinyin_of(name)

    if is_project:
        node["fingerprint"] = fingerprint(path)
        node["fileCount"] = sum(
            1 for e in entries if os.path.isfile(os.path.join(path, e))
        )
        node["launchers"] = detect_launchers(path, entries)
        node["agentHints"] = detect_agent_hints(path, entries)
        node["stack"] = detect_stack(path, entries)
        if old:
            node["intro"] = old.get("intro", "")
            node["introSource"] = old.get("introSource", "")
            node["marked"] = old.get("marked", "auto")
            node["fpChanged"] = old.get("fingerprint") != node["fingerprint"]
        else:
            node["intro"] = ""
            node["introSource"] = ""
            node["marked"] = "auto" if marked == "auto" else marked
            node["fpChanged"] = False
        stats["projects"] += 1
        return node

    # 普通目录：递归
    subdirs = []
    files = []
    for e in entries:
        full = os.path.join(path, e)
        if os.path.isdir(full):
            subdirs.append(e)
        else:
            files.append(e)
    node["fileCount"] = len(files)
    # 排除/手动标记在目录分支也要回填，否则重扫后标记丢失（排除的又显示出来）
    node["marked"] = old.get("marked", "auto") if old else "auto"
    if too_deep:
        # 深度到顶：不再向下递归，仅统计本层（防止超深目录击穿递归栈）
        stats["truncated"] += 1
        return node
    for d in sorted(subdirs, key=str.lower):
        if is_blacklisted(d, blacklist):
            continue
        full = os.path.join(path, d)
        if os.path.islink(full):
            continue  # 符号链接 / junction：不跟随，避免循环引用与重复统计
        try:
            node["children"].append(
                build_tree(full, blacklist, old_idx, stats, depth + 1)
            )
        except OSError:
            continue  # 扫描瞬间目录被移动/删除，跳过即可
    node["children"].sort(key=lambda n: (n["type"] != "project", n["name"].lower()))
    return node


def index_old(node, idx):
    idx[node["path"]] = node
    for c in node.get("children", []):
        index_old(c, idx)


# ---------------------------------------------------------------- Git 状态
# Git 状态是项目的「展示属性」，不是判定依据：拿不到就整块不显示，
# 绝不影响项目本身进入索引（见 CONTEXT.md「Git 状态」）。

_git_available = None


def git_available():
    """git 是否在 PATH 中。只探测一次并缓存结果。"""
    global _git_available
    if _git_available is None:
        _git_available = bool(shutil.which("git"))
        if not _git_available:
            log("未检测到 git，跳过 Git 状态读取（不影响其余功能）")
    return _git_available


def git_status(path):
    """读取一个项目的 Git 状态；非仓库 / 超时 / 任何异常都返回 None。"""
    if not git_available():
        return None
    if not os.path.isdir(os.path.join(path, ".git")):
        return None

    def run(args):
        try:
            r = subprocess.run(
                ["git", "-C", path] + args,
                capture_output=True,
                timeout=GIT_TIMEOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return (
                r.stdout.decode("utf-8", errors="ignore").strip()
                if r.returncode == 0
                else ""
            )
        except (OSError, subprocess.SubprocessError):
            return ""

    sb = run(["status", "--porcelain", "-sb", "--untracked-files=normal"])
    if not sb:
        return None

    lines = sb.splitlines()
    branch, ahead, behind = "", 0, 0
    if lines and lines[0].startswith("## "):
        head = lines[0][3:]
        m = re.search(r"\[(.*?)\]", head)
        if m:
            for part in m.group(1).split(","):
                bits = part.strip().split()
                if len(bits) == 2 and bits[1].isdigit():
                    if bits[0] == "ahead":
                        # pi-lens-ignore: unchecked-throwing-call-python
                        ahead = int(bits[1])
                    elif bits[0] == "behind":
                        # pi-lens-ignore: unchecked-throwing-call-python
                        behind = int(bits[1])
            head = head[: m.start()].strip()
        branch = head.split("...")[0].strip()
        if branch.startswith("No commits yet on "):
            branch = branch[len("No commits yet on ") :].strip()
        if branch.startswith("HEAD"):
            branch = "分离 HEAD"

    ts = run(["log", "-1", "--format=%ct"])
    last = ""
    if ts.isdigit():
        try:
            last = datetime.fromtimestamp(int(ts)).isoformat(timespec="seconds")
        except (OSError, OverflowError, ValueError):
            last = ""

    return {
        "branch": branch,
        "dirty": len(lines) > 1,
        "ahead": ahead,
        "behind": behind,
        "lastCommit": last,
    }


def refresh_git_status(tree, enabled=True):
    """并发刷新树中所有项目的 Git 状态，返回成功读取到的数量。"""
    if not enabled or not git_available():
        return 0
    targets = []

    def walk(node):
        if node.get("type") == "project":
            targets.append(node)
        for c in node.get("children", []):
            walk(c)

    walk(tree)
    if not targets:
        return 0

    def work(node):
        st = git_status(node["path"])
        if st:
            node["git"] = st

    with ThreadPoolExecutor(max_workers=GIT_WORKERS) as ex:
        list(ex.map(work, targets))
    return sum(1 for n in targets if n.get("git"))


_scanning = False


def _index_fresh(max_age_seconds=3600):
    """索引生成时间距今 < max_age_seconds 视为"刚扫过"，可跳过启动自动扫描。"""
    try:
        ts = load_data().get("generatedAt") or ""
        if not ts:
            return False
        return (
            datetime.now() - datetime.fromisoformat(ts)
        ).total_seconds() < max_age_seconds
    except Exception:
        return False


def _safe_auto_scan():
    """启动自动扫描的运行壳：失败只记日志，不影响服务本身。"""
    try:
        r = run_scan()
        log(
            f"启动自动扫描结束：项目 {r.get('projects', 0)} 个，待生成介绍 {r.get('pendingIntro', 0)} 个"
            if r and r.get("ok")
            else "启动自动扫描未完成"
        )
    except Exception as e:
        log(f"启动自动扫描失败：{e}")


def run_scan():
    """扫描入口：同一时刻只允许一次扫描，避免并发扫描与介绍生成产生竞态。"""
    global _scanning
    if _scanning:
        return {"ok": False, "error": "已有扫描正在进行，请等待完成"}
    _scanning = True
    log("开始扫描")
    try:
        return _run_scan()
    finally:
        _scanning = False


def _run_scan():
    cfg = load_config()
    data = load_data()
    old_idx = {}
    if data.get("tree"):
        index_old(data["tree"], old_idx)
    old_projects = {p for p, n in old_idx.items() if n.get("type") == "project"}

    stats = {
        "new": 0,
        "projects": 0,
        "removed": 0,
        "changed": 0,
        "truncated": 0,
        "errors": [],
    }
    tree = {
        "name": "工作区",
        "path": "",
        "type": "dir",
        "mtime": "",
        "py": "",
        "children": [],
    }
    for root in cfg["roots"]:
        root = os.path.normpath(root)
        if os.path.isdir(root):
            try:
                tree["children"].append(
                    build_tree(root, cfg["blacklist"], old_idx, stats)
                )
            except Exception as e:
                # 单根失败时保留该根上一次的分支，避免整个工作区从树中"消失"
                stats["errors"].append(f"{root}：{e}")
                log(f"扫描根失败 {root}：{e}")
                old_root = old_idx.get(root)
                if old_root:
                    tree["children"].append(old_root)
    stats["new"] = 0

    with LOCK:
        data["tree"] = tree
        data["generatedAt"] = datetime.now().isoformat(timespec="seconds")
        save_data()

    # 汇总：待生成 = 没有介绍的项目。不做"静态介绍升级 AI / 内容变化刷新"这类
    # 判定——这些在 AI 升级失败时会反复计入 pending，造成"前端提示全部完成
    # 却仍显示 N 个待生成"，且永远清不掉。生成出一个非空介绍即视为已解决。
    pending, changed = [], 0
    new_projects_set = set()

    def walk(node):
        if node.get("type") == "project":
            new_projects_set.add(node["path"])
            if not node.get("intro"):
                pending.append(node["path"])
        for c in node.get("children", []):
            walk(c)

    walk(tree)
    # 清理已移除项目的使用记录（扫描顺带），避免 data.json 因历史项目无限膨胀。
    # new_projects_set 刚由 walk 填好，只留当前仍存在的项目。
    if data.get("usage") and new_projects_set:
        gone = {p for p in data["usage"] if p not in new_projects_set}
        if gone:
            with LOCK:
                for k in gone:
                    data["usage"].pop(k, None)
                save_data()
            log(f"清理 {len(gone)} 个已移除项目的使用记录")
    removed = len(old_projects - new_projects_set)
    git_count = refresh_git_status(tree, cfg.get("gitStatus", True))
    log(
        f"扫描完成：项目 {len(new_projects_set)} 个，待生成介绍 {len(pending)} 个"
        + (f"，Git 状态 {git_count} 个" if git_count else "")
        + (f"，截断 {stats['truncated']} 处" if stats["truncated"] else "")
    )
    return {
        "ok": True,
        "tree": tree,
        "projects": len(new_projects_set),
        "new": len(new_projects_set - old_projects),
        "removed": removed,
        "changed": changed,
        "pendingIntro": len(pending),
        "pendingPaths": pending,
        "truncated": stats["truncated"],
        "errors": stats["errors"],
        "git": git_count,
        "generatedAt": data.get("generatedAt", ""),
    }


def find_node(node, path):
    if node.get("path") == path:
        return node
    for c in node.get("children", []):
        r = find_node(c, path)
        if r:
            return r
    return None


# ---------------------------------------------------------------- 介绍生成

STATIC_INTRO_SOURCES = [
    "README.md",
    "readme.md",
    "README.txt",
    "README",
    "readme.txt",
    "CLAUDE.md",
    "需求文档.md",
]


def read_static_intro(path, project_name=""):
    for cand in STATIC_INTRO_SOURCES:
        p = os.path.join(path, cand)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for line in lines:
            s = line.strip()
            if not s:
                continue
            low = s.lower()
            # 跳过徽章、HTML、图片、链接行
            if "shields.io" in low or "](http" in low:
                continue
            if re.search(r"<[a-zA-Z/][^>]*>", s) or s.startswith(("![",)):
                continue
            text = re.sub(r"^[#\s>*\-`]+", "", s)
            text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
            text = re.sub(r"[\[\]`*_|]", "", text).strip()
            if not text or len(text) < 6:
                continue
            # 跳过与项目名重复的标题行
            if project_name and text.lower().replace(" ", "").replace(
                "-", ""
            ) == project_name.lower().replace(" ", "").replace("-", ""):
                continue
            return text[:50]
    return ""


def project_context(path):
    readme = ""
    for cand in STATIC_INTRO_SOURCES[:5]:
        p = os.path.join(path, cand)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    readme = f.read(3000)
            except OSError:
                pass
            break
    listing = []
    for root, dirs, files in os.walk(path):
        depth = root[len(path) :].count(os.sep)
        if depth >= 2:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in HEAVY_DIRS][:30]
        listing.extend(dirs)
        listing.extend(files[:40])
        if len(listing) > 120:
            break
    return readme, "\n".join(sorted(set(listing))[:120])


def ai_intro(path, cfg):
    readme, listing = project_context(path)
    user = f"项目名：{os.path.basename(path)}\n\nREADME 内容：\n{readme or '（无）'}\n\n目录结构：\n{listing or '（空）'}"
    try:
        text = llm_chat(
            "你是一个项目索引助手。根据项目的 README 和目录结构，用不超过30字的中文一句话说明该项目是做什么的。"
            "只输出这句话本身，不要任何前缀、标点修饰或引号。",
            user,
            max_tokens=100,
        )
        return text[:60] if text else None
    except Exception as e:
        log(f"AI 介绍请求失败 {path}：{e}")
        return None


def generate_intro(path):
    node = find_node(load_data().get("tree") or {}, path)
    if not node or node.get("type") != "project":
        return {"ok": False, "error": "找不到项目节点"}
    cfg = load_config()
    intro = ai_intro(path, cfg)
    source = "ai"
    if not intro:
        intro = read_static_intro(path, node.get("name", ""))
        source = "static"
    # AI 调用耗时数秒，期间可能发生扫描替换了整棵树——写回前重新定位节点引用
    with LOCK:
        node = find_node(load_data().get("tree") or {}, path)
        if not node or node.get("type") != "project":
            return {"ok": False, "error": "项目节点已不存在（可能被重新扫描移除）"}
        node["intro"] = intro
        node["introSource"] = source if intro else ""
        # 批量生成时逐条调用，用合写（窗口内只落盘一次）避免 N 次全量写
        save_data_debounced()
    return {
        "ok": True,
        "intro": intro,
        "introSource": node["introSource"],
        "node": node,
    }


# ---------------------------------------------------------------- 批量生成介绍
# 批量生成跑在后台线程里，前端轮询进度并可随时停止——
# 不用长连接挂起请求，避免浏览器超时、也让"停止"真正生效。

_gen_tasks = {}
_gen_lock = threading.Lock()


def _batch_worker(task):
    def work(path):
        if task["stop"]:
            return
        try:
            r = generate_intro(path)
            ok = bool(r.get("ok") and r.get("intro"))
        except Exception as e:
            log(f"批量生成失败 {path}：{e}")
            ok = False
        if task["stop"]:
            return
        with task["lock"]:
            if ok:
                task["done"] += 1
            else:
                task["fail"] += 1

    with ThreadPoolExecutor(max_workers=GEN_WORKERS) as ex:
        list(ex.map(work, task["paths"]))
    task["running"] = False
    log(f"批量生成结束：成功 {task['done']}，失败 {task['fail']}")


def start_batch(paths):
    tid = datetime.now().strftime("%Y%m%d%H%M%S%f")
    task = {
        "paths": paths,
        "total": len(paths),
        "done": 0,
        "fail": 0,
        "stop": False,
        "running": True,
        "lock": threading.Lock(),
    }
    with _gen_lock:
        # 只保留最近几个已结束的任务，避免长期运行后无限累积
        finished = [k for k, v in _gen_tasks.items() if not v["running"]]
        for old in finished[:-4]:
            _gen_tasks.pop(old, None)
        _gen_tasks[tid] = task
    threading.Thread(target=_batch_worker, args=(task,), daemon=True).start()
    log(f"开始批量生成介绍：{len(paths)} 个，并发 {GEN_WORKERS}")
    return tid, task


def batch_status(tid):
    with _gen_lock:
        task = _gen_tasks.get(tid)
    if not task:
        return None
    return {
        "ok": True,
        "running": task["running"],
        "done": task["done"],
        "fail": task["fail"],
        "total": task["total"],
    }


# ---------------------------------------------------------------- 打开/跳转


def path_allowed(path):
    cfg = load_config()
    norm = os.path.normpath(path)
    if not os.path.isdir(norm):
        return None
    for root in cfg["roots"]:
        try:
            if (
                os.path.commonpath([norm.lower(), os.path.normpath(root).lower()])
                == os.path.normpath(root).lower()
            ):
                return norm
        except ValueError:
            continue
    return None


# cmd / Windows shell 的元字符：路径一旦含这些字符，经 shell 拼接的执行分支（如
# 自定义编辑器命令模板）会成为注入面。凡仍走 shell=True 的分支都要先过这个拦截。
CMD_SHELL_META = re.compile(r"[&|^<>%]")


def _has_shell_meta(s):
    return bool(CMD_SHELL_META.search(str(s)))


def open_path(path, mode, editor=None, agent=None):
    if '"' in path or "\n" in path or "\r" in path:
        return {"ok": False, "error": "路径包含非法字符"}
    cfg = load_config()
    if mode == "explorer":
        # 2 秒内同一目录只开一次，避免重复点击弹出一堆窗口
        key = os.path.normcase(os.path.normpath(path))
        now = time.time()
        if now - _last_explorer.get(key, 0) < 2.0:
            return {"ok": True, "dedup": True}
        _last_explorer[key] = now
        # 用 argv 直接传参，不走 cmd 解释，路径里的 & | ^ 等字符不再是注入面
        subprocess.Popen(["explorer", "/select," + path])
    elif mode == "terminal":
        subprocess.Popen(
            [
                "powershell",
                "-NoExit",
                "-Command",
                f"Set-Location -LiteralPath '{ps_quote(path)}'",
            ],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    elif mode == "editor":
        ed = editor or {}
        if ed.get("exe"):
            exe = str(ed["exe"])
            if not exe_allowed(exe):
                return {
                    "ok": False,
                    "error": "未识别的编辑器，请在设置中配置编辑器命令",
                }
            if exe.lower().endswith((".cmd", ".bat")):
                # cmd.exe 对带空格路径的 .cmd 有引号剥离问题，改经 PowerShell 调用（隐藏辅助窗口）
                subprocess.Popen(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        f"& '{ps_quote(exe)}' '{ps_quote(path)}'",
                    ],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                return {"ok": True}
            # 探测到的主程序 exe：直接以 argv 传路径，避免 shell 解释路径里的特殊字符
            subprocess.Popen([exe, path])
            return {"ok": True}
        tpl = cfg.get("editor", {}).get("cmd", "")
        if not tpl:
            return {
                "ok": False,
                "error": "未检测到可用编辑器，请在设置中配置编辑器命令",
            }
        # 命令模板是用户自定义的 shell 字符串，只能经 shell 执行；
        # 路径里的 shell 元字符会变成注入面，先拦截再放行。
        if _has_shell_meta(path):
            return {
                "ok": False,
                "error": "路径包含特殊字符，无法通过命令模板安全打开，请改用已检测到的编辑器",
            }
        subprocess.Popen(tpl.replace("{path}", path), shell=True)
    elif mode == "agent":
        ag = agent or {}
        exe = ag.get("exe")
        if not exe_allowed(exe):
            return {
                "ok": False,
                "error": "未识别的 Agent，请确认已安装并在本机可检测到",
            }
        exe = str(exe)
        if ag.get("gui"):
            # 桌面版 Agent 应用：直接启动主程序，工作目录设为项目
            subprocess.Popen([exe], cwd=path)
            return {"ok": True}
        full = f"Set-Location -LiteralPath '{ps_quote(path)}'; & '{ps_quote(exe)}'"
        subprocess.Popen(
            ["powershell", "-NoExit", "-Command", full],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        return {"ok": False, "error": "未知跳转模式"}
    return {"ok": True}


_last_explorer = {}


# ---------------------------------------------------------------- 使用记录


def record_usage(path):
    """记录一次项目访问，供「常用」视图按 frecency 排序。

    只在真正打开/启动项目时记录（而非每次点选），既保证信号真实，也避免频繁写盘。
    """
    if not path:
        return
    with LOCK:
        data = load_data()
        usage = data.setdefault("usage", {})
        item = usage.get(path) or {}
        # pi-lens-ignore: unchecked-throwing-call-python
        item["count"] = int(item.get("count", 0)) + 1
        item["last"] = datetime.now().isoformat(timespec="seconds")
        usage[path] = item
        save_data()


try:
    import winreg
except ImportError:
    winreg = None

# 主流 AI 编辑器/Agent CLI：命令名 -> (id, 显示名)。id 用于匹配项目内配置文件证据
EDITOR_WHICH = [
    ("trae", "trae", "Trae"),
    ("trae-cn", "trae", "Trae CN"),
    ("trae-solo", "trae", "TRAE SOLO"),
    ("trae-solo-cn", "trae", "TRAE SOLO CN"),
    ("code", "vscode", "VS Code"),
    ("cursor", "cursor", "Cursor"),
    ("windsurf", "windsurf", "Windsurf"),
    ("qoder", "qoder", "Qoder"),
]
EDITOR_NAME_PAT = re.compile(
    r"trae|cursor|visual studio code|windsurf|qoder|lingma", re.I
)
# 注册表 bin\*.cmd 文件名 -> (id, 显示名)
_EDITOR_STEM_MAP = {
    "trae": ("trae", "Trae"),
    "trae-cn": ("trae", "Trae CN"),
    "trae-solo": ("trae", "TRAE SOLO"),
    "trae-solo-cn": ("trae", "TRAE SOLO CN"),
    "code": ("vscode", "VS Code"),
    "cursor": ("cursor", "Cursor"),
    "windsurf": ("windsurf", "Windsurf"),
    "qoder": ("qoder", "Qoder"),
    "buddycn": ("codebuddy", "CodeBuddy CN"),
    "lingma": ("lingma", "通义灵码"),
}

# 主流终端 Agent CLI：命令名 -> (id, 显示名)
AGENT_WHICH = [
    ("claude", "claude", "Claude Code"),
    ("codex", "codex", "Codex CLI"),
    ("gemini", "gemini", "Gemini CLI"),
    ("qwen", "qwen", "Qwen Code"),
    ("iflow", "iflow", "iFlow CLI"),
    ("opencode", "opencode", "OpenCode"),
    ("crush", "crush", "Crush"),
    ("aider", "aider", "Aider"),
    ("copilot", "copilot", "Copilot CLI"),
    ("codebuddy", "codebuddy", "CodeBuddy"),
    ("kiro-cli", "kiro", "Kiro CLI"),
    ("qodercli", "qoder", "Qoder CLI"),
    ("workbuddy", "workbuddy", "WorkBuddy"),
    ("wb", "workbuddy", "WorkBuddy"),
    ("zcode", "zcode", "ZCode"),
    ("dsh", "dsh", "DSH"),
    ("pi", "pi", "Pi Agent"),
]

# 项目根目录配置文件证据：id -> 根目录条目（"a/b" 表示 a 目录下的 b 文件）
TOOL_MARKERS = {
    "claude": [".claude", "CLAUDE.md"],
    "codex": [".codex", "AGENTS.codex.md"],
    "gemini": [".gemini", "GEMINI.md"],
    "qwen": [".qwen", "QWEN.md"],
    "copilot": [".github/copilot-instructions.md"],
    "aider": [".aider.conf.yml", ".aider", ".aiderignore"],
    "opencode": ["opencode.json", ".opencode"],
    "codebuddy": ["CODEBUDDY.md", ".codebuddy"],
    "kiro": [".kiro"],
    "crush": [".crush", "CRUSH.md"],
    "iflow": [".iflow"],
    "qoder": [".qoder", "QODER.md"],
    "trae": [".trae"],
    "vscode": [".vscode"],
    "cursor": [".cursor", ".cursorrules"],
    "windsurf": [".windsurf", ".windsurfrules"],
    "workbuddy": ["WORKBUDDY.md", ".workbuddy"],
    "zcode": ["ZCODE.md", ".zcode"],
    "dsh": ["DSH.md", ".dsh"],
    "pi": [".pi"],
    "lingma": [".lingma"],
}

# 通用配置文件（多家 agent 共用，无法归属具体哪家）
GENERIC_MARKERS = ["agents.md", ".agents"]


def detect_agent_hints(path, entries):
    """扫描项目根目录的 agent/编辑器配置文件，返回证据 id 列表。
    通用文件（AGENTS.md 等）记为 "generic"，前端据此显示全部已安装供用户选择。
    """
    lower = {e.lower() for e in entries}
    hints = []
    for key, markers in TOOL_MARKERS.items():
        for m in markers:
            if "/" in m:
                d, f = m.split("/", 1)
                if d in lower and os.path.isfile(os.path.join(path, d, f)):
                    hints.append(key)
                    break
            elif m in lower:
                hints.append(key)
                break
    # 通用配置文件（多家共用，无法归属具体哪家）
    for m in GENERIC_MARKERS:
        if m in lower:
            hints.append("generic")
            break
    return hints


# 注册表 Uninstall 中常见的 Agent 桌面应用：DisplayName 匹配 -> (id, 显示名, 主程序 exe)
AGENT_APP_PATTERNS = [
    (re.compile(r"^workbuddy", re.I), "workbuddy", "WorkBuddy", "WorkBuddy.exe"),
    (re.compile(r"^zcode", re.I), "zcode", "ZCode", "ZCode.exe"),
    (re.compile(r"^dsh", re.I), "dsh", "DSH", "DSH Desktop.exe"),
    (re.compile(r"minimax", re.I), "minimax", "MiniMax Code", "MiniMax Code.exe"),
    (re.compile(r"codex\+\+", re.I), "codex", "Codex++", "codex-plus-plus.exe"),
    (re.compile(r"codebuddy", re.I), "codebuddy", "CodeBuddy", None),
]


def _uninstall_dir(sk):
    """从注册表卸载项推断安装目录：InstallLocation > DisplayIcon > UninstallString。"""
    try:
        # pi-lens-ignore: reportOptionalMemberAccess
        loc = winreg.QueryValueEx(sk, "InstallLocation")[0] or ""
    except OSError:
        loc = ""
    if loc and os.path.isdir(loc):
        return loc
    for val in ("DisplayIcon", "UninstallString"):
        try:
            # pi-lens-ignore: reportOptionalMemberAccess
            s = winreg.QueryValueEx(sk, val)[0] or ""
        except OSError:
            continue
        s = s.split(",")[0].strip().strip('"')
        d = os.path.dirname(s)
        if d and os.path.isdir(d):
            return d
    return ""


def detect_agents():
    """检测本机可用的 Agent：先 PATH 查 CLI，再注册表找桌面版 Agent 应用。"""
    found, seen = [], set()

    def add(tid, name, exe, gui=False):
        key = os.path.normcase(os.path.normpath(exe))
        if key in seen:
            return
        seen.add(key)
        entry = {"id": tid, "name": name, "exe": exe}
        if gui:
            entry["gui"] = True
        found.append(entry)

    for cli, tid, name in AGENT_WHICH:
        p = shutil.which(cli)
        if p:
            add(tid, name, p)

    if winreg:
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for sub_path in (
                "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
                "SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
            ):
                try:
                    key = winreg.OpenKey(root, sub_path)
                except OSError:
                    continue
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, i)
                        i += 1
                    except OSError:
                        break
                    try:
                        sk = winreg.OpenKey(key, sub)
                        disp = winreg.QueryValueEx(sk, "DisplayName")[0]
                    except OSError:
                        continue
                    for pat, tid, name, exe_name in AGENT_APP_PATTERNS:
                        if not pat.search(disp):
                            continue
                        d = _uninstall_dir(sk)
                        if d:
                            # bin 下的 CLI 优先，其次桌面主程序（GUI）
                            exe = ""
                            bin_dir = os.path.join(d, "bin")
                            if os.path.isdir(bin_dir):
                                # pi-lens-ignore: unchecked-throwing-call-python
                                for f in os.listdir(bin_dir):
                                    if f.lower().endswith((".cmd", ".bat")):
                                        exe = os.path.join(bin_dir, f)
                                        break
                            gui = False
                            if (
                                not exe
                                and exe_name
                                and os.path.isfile(os.path.join(d, exe_name))
                            ):
                                exe = os.path.join(d, exe_name)
                                gui = True
                            if exe:
                                add(tid, name, exe, gui)
                        break
    return found


def detect_editors():
    """检测本机已安装的编辑器：PATH 命令 + 注册表 Uninstall 扫描。"""
    found, seen = [], set()

    def add(tid, name, exe):
        key = os.path.normcase(os.path.normpath(exe))
        if key in seen:
            return
        seen.add(key)
        found.append({"id": tid, "name": name, "exe": exe})

    for cli, tid, name in EDITOR_WHICH:
        p = shutil.which(cli)
        if p:
            add(tid, name, p)

    if winreg:
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for sub_path in (
                "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
                "SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
            ):
                try:
                    key = winreg.OpenKey(root, sub_path)
                except OSError:
                    continue
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, i)
                        i += 1
                    except OSError:
                        break
                    try:
                        sk = winreg.OpenKey(key, sub)
                        disp = winreg.QueryValueEx(sk, "DisplayName")[0]
                    except OSError:
                        continue
                    try:
                        loc = winreg.QueryValueEx(sk, "InstallLocation")[0] or ""
                    except OSError:
                        loc = ""
                    if not (
                        EDITOR_NAME_PAT.search(disp) and loc and os.path.isdir(loc)
                    ):
                        continue
                    bin_dir = os.path.join(loc, "bin")
                    if not os.path.isdir(bin_dir):
                        continue
                    # pi-lens-ignore: unchecked-throwing-call-python
                    for f in os.listdir(bin_dir):
                        if f.lower().endswith(".cmd"):
                            stem = os.path.splitext(f)[0].lower()
                            tid, name = _EDITOR_STEM_MAP.get(stem, (stem, disp))
                            add(tid, name, os.path.join(bin_dir, f))
                            break
    return found[:8]


# ---------------------------------------------------------------- 探测缓存与白名单

_detect_cache = {"ts": 0.0, "editors": [], "agents": []}
DETECT_TTL = 600.0  # 注册表全表扫描较慢，10 分钟内复用结果


def detect_all():
    """带缓存的编辑器 / Agent 探测（注册表扫描开销大，不每次请求都跑）。"""
    now = time.time()
    if _detect_cache["ts"] and now - _detect_cache["ts"] < DETECT_TTL:
        return _detect_cache["editors"], _detect_cache["agents"]
    editors, agents = detect_editors(), detect_agents()
    _detect_cache.update(ts=now, editors=editors, agents=agents)
    return editors, agents


def exe_allowed(exe):
    """/api/open 只允许启动本机探测到的编辑器 / Agent，拒绝请求体传入的任意路径。"""
    if not exe or re.search(r"['\"\r\n]", str(exe)):
        return False
    if not _detect_cache["ts"]:
        detect_all()
    target = os.path.normcase(os.path.normpath(str(exe)))
    for item in _detect_cache["editors"] + _detect_cache["agents"]:
        cand = item.get("exe")
        if cand and os.path.normcase(os.path.normpath(cand)) == target:
            return True
    return False


# ---------------------------------------------------------------- 备份


def backup_export(dest):
    cfg = load_config()
    dest = dest or cfg.get("backupDir", "")
    if not dest or not os.path.isdir(dest):
        return {"ok": False, "error": "导出目录不存在，请先在设置或表单中指定"}
    name = "starchart-" + datetime.now().strftime("%Y%m%d")
    out = os.path.join(dest, name + ".zip")
    i = 1
    while os.path.exists(out):
        out = os.path.join(dest, f"{name}-{i:02d}.zip")
        i += 1
    flush_data()  # 先把窗口内合写的改动落盘，导出才不丢最新数据
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in (DATA_FILE, CONFIG_FILE):
            if os.path.isfile(f):
                z.write(f, os.path.basename(f))
    return {"ok": True, "path": out}


def backup_import(body):
    try:
        zf = zipfile.ZipFile(io.BytesIO(body))
        names = zf.namelist()
        if "data.json" not in names:
            return {"ok": False, "error": "备份包中缺少 data.json"}
        payload = json.loads(zf.read("data.json").decode("utf-8"))
        if "version" not in payload or "tree" not in payload:
            return {"ok": False, "error": "数据结构校验失败"}
        os.makedirs(DATA_DIR, exist_ok=True)
        for fname, target in (("data.json", DATA_FILE), ("config.json", CONFIG_FILE)):
            if fname in names and os.path.isfile(target):
                bak = target + ".bak"
                with open(target, "rb") as src, open(bak, "wb") as dst:
                    dst.write(src.read())
        with open(DATA_FILE, "wb") as f:
            f.write(zf.read("data.json"))
        if "config.json" in names:
            with open(CONFIG_FILE, "wb") as f:
                f.write(zf.read("config.json"))
        global _data, _config, _saved_pending
        with LOCK:
            _saved_pending = False  # 取消可能尚未落盘的旧改动，避免覆盖刚导入的文件
            _data, _config = None, None
        load_config()
        load_data()
        return {"ok": True}
    # pi-lens-ignore: no-bare-except
    except zipfile.BadZipFile:
        return {"ok": False, "error": "不是有效的 zip 文件"}
    except Exception as e:
        return {"ok": False, "error": f"导入失败：{e}"}


# ---------------------------------------------------------------- 使用说明文档

DOC_SOURCES = [
    "README.md",
    "readme.md",
    "README.txt",
    "使用说明.md",
    "说明.md",
    "需求文档.md",
    "CLAUDE.md",
    "AGENTS.md",
    "PRODUCT.md",
]


def read_doc(path):
    for cand in DOC_SOURCES:
        fp = os.path.join(path, cand)
        if os.path.isfile(fp):
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    return cand, f.read(30000)
            except OSError:
                continue
    return "", ""


def search_files(path, q, limit=FILE_SEARCH_LIMIT):
    """在项目内按文件名搜索，返回相对路径列表（跳过重量级目录）。"""
    q = (q or "").strip().lower()
    if not q:
        return []
    hits = []
    for root, dirs, files in os.walk(path):
        depth = root[len(path) :].count(os.sep)
        if depth >= FILE_SEARCH_DEPTH:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in HEAVY_DIRS]
        for f in files:
            if q in f.lower():
                hits.append(os.path.relpath(os.path.join(root, f), path))
                if len(hits) >= limit:
                    return hits
    return hits


# ---------------------------------------------------------------- GitHub 趋势 / 中文导读
# 数据来源：github.com/trending（趋势榜）+ raw.githubusercontent.com（README）+ api.github.com（元信息）。
# 全部走标准库 urllib，成功率与本地网络一致；结果落盘到 .starchart\trending.json 长期缓存。
# 中文一行介绍与中文导读复用「设置」里已配好的 LLM（OpenAI 兼容），没配也能看榜单原文。

GH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TREND_FILE = os.path.join(DATA_DIR, "trending.json")
TREND_TTL = 30 * 60  # 榜单缓存 30 分钟，避免每次开面板都打一次 GitHub
TREND_TIMEOUT = 25  # 单次网络请求超时（秒）
TREND_README_LIMIT = 4000  # 送进 LLM 的 README 截断长度
GH_NAME_RE = re.compile(
    r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$"
)  # 只认 owner/repo，防任意 URL 拼接

# 语言下拉：label → GitHub trending 的 URL slug
TREND_LANGS = [
    ("全部语言", ""),
    ("Python", "python"),
    ("TypeScript", "typescript"),
    ("JavaScript", "javascript"),
    ("Go", "go"),
    ("Rust", "rust"),
    ("C++", "c++"),
    ("C", "c"),
    ("C#", "c#"),
    ("Java", "java"),
    ("Shell", "shell"),
    ("HTML", "html"),
    ("CSS", "css"),
    ("Vue", "vue"),
    ("Kotlin", "kotlin"),
    ("Swift", "swift"),
    ("PHP", "php"),
    ("Ruby", "ruby"),
    ("Jupyter Notebook", "jupyter-notebook"),
]

_trend_lock = threading.RLock()


def _trend_load():
    if not os.path.isfile(TREND_FILE):
        return {"lists": {}, "intros": {}, "guides": {}, "digests": {}}
    try:
        with open(TREND_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {"lists": {}, "intros": {}, "guides": {}, "digests": {}}
    for k in ("lists", "intros", "guides", "digests"):
        d.setdefault(k, {})
    return d


def _trend_save(d):
    # pi-lens-ignore: unchecked-throwing-call-python
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = TREND_FILE + ".tmp"
    # pi-lens-ignore: unchecked-throwing-call-python
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, TREND_FILE)


_tls = threading.local()  # 每线程最近一次 GitHub 失败分类，供 _gh_err_message 读取


def _gh_err_kind(e):
    """把 exceptions 归类为可提示的失败类型：dns / timeout / http / other。"""
    reason = getattr(e, "reason", e)
    if isinstance(reason, socket.gaierror):
        return "dns"
    if (
        isinstance(reason, (TimeoutError, socket.timeout))
        or "timed out" in str(reason).lower()
    ):
        return "timeout"
    if isinstance(e, HTTPError):
        return "http"
    return "other"


def _gh_mirror_url(url):
    """按设置里的 GitHub 镜像/加速前缀生成重试 URL；未配置返回 None。"""
    prefix = (load_config().get("ghMirror") or "").strip().rstrip("/")
    if not prefix:
        return None
    return prefix + "/" + url


def _gh_request(url, headers, timeout):
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _gh_get(url, accept="application/vnd.github+json", timeout=TREND_TIMEOUT):
    """带 UA 与可选 Token 的 GET。直连失败后按设置里的镜像前缀重试一次；全失败返回 None。"""
    headers = {"User-Agent": GH_UA, "Accept": accept}
    token = (load_config().get("ghToken") or "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token
    kind = "other"
    for link in (url, _gh_mirror_url(url)):
        if not link:
            continue
        try:
            return _gh_request(link, headers, timeout=timeout)
        except Exception as e:
            kind = _gh_err_kind(e)
            log(f"GitHub 请求失败 {link}：{e}")
    _tls.gh_err = kind
    return None


def _gh_err_message():
    """把本线程最近一次失败分类转成可操作的提示文案。"""
    k = getattr(_tls, "gh_err", "other")
    return {
        "dns": "无法解析 github.com —— 网络被墙或未走代理；可在「设置 → GitHub 镜像前缀」填加速站，或为 urllib 配置 HTTPS_PROXY",
        "timeout": "GitHub 响应超时 —— 网络较慢或被墙；可在「设置 → GitHub 镜像前缀」填加速站或配置代理",
        "http": "GitHub 返回异常状态码；可稍后重试，或到「设置 → GitHub 镜像前缀」填加速站",
    }.get(
        k,
        "拉取 GitHub 趋势失败，请检查网络；被墙/限速时可在「设置 → GitHub 镜像前缀」填加速站",
    )


def _tag_text(s):
    return html_unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _count_of(s):
    """把 '2,989' / '1.2k' 这类的计数文本转成整数。"""
    raw = (s or "").strip()
    m = re.search(r"([\d.]+)", raw.replace(",", ""))
    if not m:
        return 0
    try:
        v = float(m.group(1))
    except ValueError:
        return 0
    if re.search(r"k\b", raw, re.I):
        v *= 1000
    # pi-lens-ignore: unchecked-throwing-call-python
    return int(v)


def parse_trending(page):
    """解析 github.com/trending 的 HTML。改版时这里的正则可能需要跟着调。"""
    items = []
    for rank, chunk in enumerate(
        re.split(r'<article class="Box-row">', page or "")[1:], 1
    ):
        chunk = chunk.split("</article>")[0]
        m = re.search(r'<h2[^>]*>.*?href="/([^/"]+/[^/"]+)"', chunk, re.S)
        if not m:
            continue
        full = m.group(1)
        owner, _, name = full.partition("/")
        desc = ""
        dm = re.search(r'<p class="col-9[^"]*">(.*?)</p>', chunk, re.S)
        if not dm:
            dm = re.search(
                r'<p class="[^"]*color-fg-muted[^"]*">(.*?)</p>', chunk, re.S
            )
        if dm:
            desc = _tag_text(dm.group(1))
        lang = ""
        lm = re.search(r'itemprop="programmingLanguage">([^<]+)<', chunk)
        if lm:
            lang = html_unescape(lm.group(1)).strip()
        stars = forks = today = 0
        sm = re.search(r'/stargazers"[^>]*>(.*?)</a>', chunk, re.S)
        if sm:
            stars = _count_of(_tag_text(sm.group(1)))
        fm = re.search(r'/forks"[^>]*>(.*?)</a>', chunk, re.S)
        if fm:
            forks = _count_of(_tag_text(fm.group(1)))
        tm = re.search(r"float-sm-right[^>]*>(.*?)</span>", chunk, re.S)
        if tm:
            today = _count_of(_tag_text(tm.group(1)))
        lang_color = ""
        cm = re.search(r'repo-language-color"[^>]*background-color:\s*([^;"]+)', chunk)
        if cm:
            lang_color = cm.group(1).strip()
        items.append(
            {
                "rank": rank,
                "fullName": full,
                "owner": owner,
                "name": name,
                "desc": desc,
                "lang": lang,
                "langColor": lang_color,
                "stars": stars,
                "forks": forks,
                "today": today,
                "url": "https://github.com/" + full,
                "zreadUrl": "https://zread.ai/" + full,
            }
        )
    return items


def fetch_trending(since="daily", lang="", force=False):
    """取趋势榜。先读 30 分钟内的缓存，force 或过期才真正发请求。"""
    since = since if since in ("daily", "weekly", "monthly") else "daily"
    slug = next(
        (s for lb, s in TREND_LANGS if lb.lower() == (lang or "").lower()), lang or ""
    )
    key = f"{since}|{slug}"
    with _trend_lock:
        store = _trend_load()
        cached = store["lists"].get(key)
        if cached and not force:
            # pi-lens-ignore: unchecked-throwing-call-python
            age = time.time() - float(cached.get("fetchedAt", 0))
            if age < TREND_TTL:
                return {
                    "ok": True,
                    "items": cached.get("items", []),
                    "cached": True,
                    "fetchedAt": cached.get("fetchedAt"),
                    "since": since,
                    "lang": slug,
                    # pi-lens-ignore: unchecked-throwing-call-python
                    "age": int(age),
                }
    base = "https://github.com/trending"
    if slug:
        base += "/" + quote(slug, safe="")
    url = base + ("?since=" + since if since != "daily" else "")
    page = _gh_get(url, accept="text/html")
    if not page:
        if cached:
            # 网络不通时退回旧榜单，至少界面不是空的
            return {
                "ok": True,
                "items": cached.get("items", []),
                "cached": True,
                "stale": True,
                "fetchedAt": cached.get("fetchedAt"),
                "since": since,
                "lang": slug,
                "error": _gh_err_message() + "；展示的是上一次缓存",
            }
        return {
            "ok": False,
            "error": _gh_err_message(),
        }
    items = parse_trending(page)
    if not items:
        return {
            "ok": False,
            "error": "页面已拉取但没解析到条目，GitHub 页面结构可能已改版",
        }
    now = time.time()
    with _trend_lock:
        store = _trend_load()
        store["lists"][key] = {"fetchedAt": now, "items": items}
        _trend_save(store)
    log(f"拉取 GitHub 趋势 since={since} lang={slug or 'all'} 共 {len(items)} 条")
    return {
        "ok": True,
        "items": items,
        "cached": False,
        "fetchedAt": now,
        "since": since,
        "lang": slug,
    }


def gh_repo_meta(full_name):
    """仓库元信息（默认分支 / 简介 / 语言 / star）。失败返回空 dict。"""
    page = _gh_get("https://api.github.com/repos/" + full_name)
    if not page:
        return {}
    try:
        d = json.loads(page)
    except Exception:
        return {}
    return {
        "defaultBranch": d.get("default_branch") or "main",
        "desc": d.get("description") or "",
        "lang": d.get("language") or "",
        "stars": d.get("stargazers_count") or 0,
        "topics": d.get("topics") or [],
        "homepage": d.get("homepage") or "",
    }


def gh_readme(full_name, limit=TREND_README_LIMIT):
    """抓 README：先查默认分支（一次 API），再取 raw 文件；都失败则返回空串。"""
    meta = gh_repo_meta(full_name)
    branch = meta.get("defaultBranch") or "main"
    for name in ("README.md", "readme.md", "README", "README.rst", "README.txt"):
        page = _gh_get(
            # pi-lens-ignore: UP031
            "https://raw.githubusercontent.com/%s/%s/%s" % (full_name, branch, name),
            accept="text/plain",
        )
        if page and not page.lstrip().lower().startswith("404"):
            return page[:limit], meta
    return "", meta


def llm_chat(system, user, max_tokens=200, temperature=0.3):
    """复用设置里的 LLM 配置发一条对话；没配置或失败返回 None。"""
    cfg = load_config()
    api = cfg.get("api") or {}
    if not (api.get("baseUrl") and api.get("model")):
        return None
    body = {
        "model": api["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {"Content-Type": "application/json"}
    if api.get("apiKey"):
        headers["Authorization"] = "Bearer " + api_key(cfg)
    try:
        req = Request(
            api["baseUrl"].rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
        )
        with urlopen(req, timeout=45) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        text = (result["choices"][0]["message"]["content"] or "").strip().strip('"“”')
        return text or None
    except Exception as e:
        log(f"LLM 调用失败：{e}")
        return None


def trend_intro(full_name, desc=""):
    """给榜单条目生成中文一句话介绍（≤30 字），结果长期缓存。"""
    with _trend_lock:
        store = _trend_load()
        hit = store["intros"].get(full_name)
        if hit and hit.get("text"):
            return {
                "ok": True,
                "text": hit["text"],
                "cached": True,
                "source": hit.get("source", "ai"),
            }
    readme, meta = gh_readme(full_name)
    if not readme and not desc:
        desc = meta.get("desc", "")
    # pi-lens-ignore: UP031
    user = "项目：%s\n简介：%s\n\nREADME 内容：\n%s" % (
        full_name,
        desc or meta.get("desc") or "（无）",
        readme or "（未取到 README）",
    )
    text = llm_chat(
        "你是一个开源项目索引助手。根据项目简介和 README，用 15~30 字中文一句话说明这个项目是什么、解决什么问题。"
        "只输出这句话本身，不要任何前缀、编号、标点修饰或引号。",
        user,
        max_tokens=100,
    )
    if not text:
        return {
            "ok": False,
            "error": "AI 生成失败：请先在 ⚙ 设置里配置并测试 LLM（或检查网络）",
        }
    text = text[:60]
    with _trend_lock:
        store = _trend_load()
        store["intros"][full_name] = {"text": text, "at": time.time(), "source": "ai"}
        _trend_save(store)
    return {"ok": True, "text": text, "cached": False, "source": "ai"}


# 批量生成榜单中文介绍：与本地项目批量生成同一套模式——后台线程 + taskId 轮询 + 可停止
_trend_tasks = {}
_trend_task_lock = threading.Lock()


def _trend_batch_worker(task):
    def work(item):
        if task["stop"]:
            return
        try:
            r = trend_intro(item["fullName"], item.get("desc", ""))
            ok = bool(r.get("ok"))
        except Exception as e:
            log(f"榜单介绍生成失败 {item.get('fullName')}：{e}")
            ok = False
        if task["stop"]:
            return
        with task["lock"]:
            if ok:
                task["done"] += 1
            else:
                task["fail"] += 1

    with ThreadPoolExecutor(max_workers=GEN_WORKERS) as ex:
        list(ex.map(work, task["items"]))
    task["running"] = False
    log(f"榜单介绍批量生成结束：成功 {task['done']}，失败 {task['fail']}")


def start_trend_batch(items):
    tid = "t" + datetime.now().strftime("%Y%m%d%H%M%S%f")
    task = {
        "items": items,
        "total": len(items),
        "done": 0,
        "fail": 0,
        "stop": False,
        "running": True,
        "lock": threading.Lock(),
    }
    with _trend_task_lock:
        finished = [k for k, v in _trend_tasks.items() if not v["running"]]
        for old in finished[:-4]:
            _trend_tasks.pop(old, None)
        _trend_tasks[tid] = task
    threading.Thread(target=_trend_batch_worker, args=(task,), daemon=True).start()
    log(f"开始批量生成榜单中文介绍：{len(items)} 个")
    return tid, task


def trend_batch_status(tid):
    with _trend_task_lock:
        task = _trend_tasks.get(tid)
    if not task:
        return None
    return {
        "ok": True,
        "running": task["running"],
        "done": task["done"],
        "fail": task["fail"],
        "total": task["total"],
    }


def _clean_guide(text):
    """清洗导读文本：去掉模型偶尔加的编号前缀与重复破折号，保持行结构干净。"""
    lines = []
    for ln in (text or "").splitlines():
        s = ln.rstrip()
        s = re.sub(r"^\s*\d+\s*[)）.、]\s*", "", s)  # 1) / 2、 之类编号
        s = re.sub(r"^\s*[-•]\s*[-•]\s*", "- ", s)  # '- - ' 重复
        s = re.sub(r"^[-•]\s*(?=\S)", "- ", s)
        if s.strip():
            lines.append(s)
    return "\n".join(lines)


def trend_guide(full_name, desc=""):
    """中文导读（探索）：一句话 + 核心要点 + 适合谁 + 上手建议。结果长期缓存。"""
    with _trend_lock:
        store = _trend_load()
        hit = store["guides"].get(full_name)
        if hit and hit.get("text"):
            return {"ok": True, "text": hit["text"], "cached": True}
    readme, meta = gh_readme(full_name)
    topics = meta.get("topics") or []
    user = (
        # pi-lens-ignore: UP031
        "项目：%s\n简介：%s\n主题标签：%s\n主语言：%s\nStar：%s\n\nREADME 内容：\n%s"
        % (
            full_name,
            desc or meta.get("desc") or "（无）",
            "、".join(topics[:8]) or "（无）",
            meta.get("lang") or "（未知）",
            meta.get("stars") or "（未知）",
            readme or "（未取到 README）",
        )
    )
    text = llm_chat(
        "你是一个中文开源项目导读助手。严格按下述纯文本格式输出，不要 Markdown 标题符号、不要编号、"
        "不要客套话、不要重复项目名：\n"
        "第 1 行：用一句话说清这个项目是什么、解决什么问题（≤30 字，不加任何前缀）。\n"
        "第 2-4 行：恰好 3 个核心要点，每行以 '- ' 开头，每条 ≤25 字，只说干货事实，不要凑数。\n"
        "第 5 行：以「适合：」开头，说明适合谁用（≤30 字）。\n"
        "第 6 行：以「上手：」开头，给出最省事的上手/安装方式（≤45 字）；其中任何命令或包名一律用反引号包裹，如 `pip install x`。\n"
        "严格只输出这 6 行。",
        user,
        max_tokens=500,
    )
    if not text:
        return {
            "ok": False,
            "error": "AI 生成失败：请先在 ⚙ 设置里配置并测试 LLM（或检查网络）",
        }
    text = _clean_guide(text)
    with _trend_lock:
        store = _trend_load()
        store["guides"][full_name] = {"text": text, "at": time.time()}
        _trend_save(store)
    return {"ok": True, "text": text, "cached": False}


def trend_digest(since="weekly", lang="", items=None):
    """本期速览：把榜单整体交给 LLM，产出中文趋势小结（每天每榜缓存一份）。"""
    since = since if since in ("daily", "weekly", "monthly") else "weekly"
    key = f"{since}|{lang}|{datetime.now().strftime('%Y-%m-%d')}"
    with _trend_lock:
        store = _trend_load()
        hit = store["digests"].get(key)
        if hit and hit.get("text"):
            return {"ok": True, "text": hit["text"], "cached": True}
    if not items:
        r = fetch_trending(since, lang)
        items = r.get("items") or []
    if not items:
        return {"ok": False, "error": "没有可分析的榜单数据"}
    brief = "\n".join(
        # pi-lens-ignore: UP031
        "%d. %s（%s，★%s，本期 +%s）：%s"
        % (
            it.get("rank", 0),
            it.get("fullName", ""),
            it.get("lang") or "未知语言",
            it.get("stars", 0),
            it.get("today", 0),
            (it.get("desc") or "")[:120],
        )
        for it in items[:25]
    )
    text = llm_chat(
        "你是开源趋势观察员。下面是一份 GitHub Trending 榜单，请用中文写「本期速览」，严格按格式：\n"
        "第 1 行：以「本期风向：」开头，一句话总括（≤40 字）。\n"
        "第 2-5 行：恰好 4 条观察，每行以 '- ' 开头，每条 ≤30 字，"
        "写「出现了哪一类项目 / 什么技术主题」，禁止逐个仓库罗列。\n"
        "第 6-8 行：恰好 3 条推荐，每行格式为「推荐：owner/repo —— 原因（≤15 字）」。\n"
        "只输出这 8 行，不要标题、不要客套、不要重复榜单数据。",
        brief,
        max_tokens=600,
        temperature=0.5,
    )
    if not text:
        return {"ok": False, "error": "AI 生成失败：请先在 ⚙ 设置里配置并测试 LLM"}
    text = "\n".join(
        re.sub(r"^\s*[-•]\s*(?=推荐：)", "", ln)
        for ln in _clean_guide(text).splitlines()
    )
    with _trend_lock:
        store = _trend_load()
        store["digests"][key] = {"text": text, "at": time.time()}
        store["digests"] = dict(list(store["digests"].items())[-30:])  # 只留最近 30 份
        _trend_save(store)
    return {"ok": True, "text": text, "cached": False}


def trend_store_public():
    """给前端的已缓存中文结果（榜单接口会一并带上，前端无需再取一次）。"""
    with _trend_lock:
        store = _trend_load()
    return {
        "intros": {k: v.get("text", "") for k, v in store["intros"].items()},
        "guides": {k: v.get("text", "") for k, v in store["guides"].items()},
    }


# ---------------------------------------------------------------- HTTP

MASK = "********"
PORT = 6173  # main() 启动时回填，用于 Origin 校验

_last_ui_open = 0.0


# ---------------------------------------------------------------- 目录选择
# 浏览器出于安全不暴露本地完整路径，<input type="file"> 也拿不到目录绝对路径，
# 因此扫描根目录的选择由后端在本机弹出原生文件夹对话框，再把路径回传给页面。
# 提示语用英文：PowerShell 经 subprocess 传参时中文会受控制台代码页影响。
_PICK_PS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.WindowState = 'Minimized'
$owner.ShowInTaskbar = $false
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = 'Select a folder to scan'
$d.ShowNewFolderButton = $true
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output $d.SelectedPath
}
$d.Dispose()
$owner.Dispose()
"""
_pick_lock = threading.Lock()


def pick_dir(timeout=300):
    """弹出文件夹选择框，返回 (path, err)。

    选中 → (绝对路径, None)；取消 → (None, None)；失败 → (None, 原因)。
    会阻塞调用线程直到用户确认或超时，因此只应在独立请求线程里调用。
    """
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", _PICK_PS],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return None, "选择超时：窗口打开后 5 分钟内没有确认"
    except Exception as e:
        return None, "无法打开文件夹选择框：%s" % e
    if p.returncode != 0:
        return None, "文件夹选择框启动失败"
    lines = (p.stdout or b"").decode("utf-8", "replace").strip().splitlines()
    if not lines:
        return None, None          # 用户点了取消
    return lines[0].strip(), None


def _port_occupant(port):
    """查占用 TCP 端口的进程，返回 "PID xxx（进程名）"；查不到返回 None。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            stdout=subprocess.PIPE,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.decode("gbk", "replace")
    except Exception:
        return None
    suffix = ":%d" % port
    pids = set()
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(suffix):
            pids.add(parts[4])
    for pid in sorted(pids):
        try:
            t = subprocess.run(
                ["tasklist", "/FI", "PID eq %s" % pid, "/FO", "CSV", "/NH"],
                stdout=subprocess.PIPE,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.decode("gbk", "replace")
            first = (t.strip().splitlines() or [""])[0]
            name = first.split('","')[0].strip('"').strip()
        except Exception:
            name = ""
        return "PID %s（%s）" % (pid, name or "未知进程")
    return None


def open_ui(url=None, min_interval=1.0):
    """打开（或唤起）星图界面，带去重：短时间内的多次唤起合并为一次。

    托盘模式 + 热键 + 端口兜底都可能触发打开浏览器，去重可避免一按弹两个窗口。
    返回 True 表示本次真正打开了浏览器。
    """
    global _last_ui_open
    if url is None:
        url = f"http://127.0.0.1:{PORT}"
    now = time.time()
    if now - _last_ui_open < min_interval:
        return False
    _last_ui_open = now
    webbrowser.open(url)
    return True


def mask_config(cfg):
    # pi-lens-ignore: unchecked-throwing-call-python
    out = json.loads(json.dumps(cfg))
    if out.get("api", {}).get("apiKey"):
        out["api"]["apiKey"] = MASK
    return out


# ---------------------------------------------------------------- 开机自启
# 写 HKCU Run（当前用户），免管理员；删键即撤销，不改系统全局设置。
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "StarChart"


def autostart_status():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as k:
            winreg.QueryValueEx(k, AUTOSTART_NAME)
            return True
    except OSError:
        return False


def _autostart_cmd():
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.isfile(pyw) else sys.executable
    app = os.path.join(BASE_DIR, "tray_app.py")
    return f'"{exe}" "{app}" --autostart'


def set_autostart(enable):
    if winreg is None:
        return {"ok": False, "error": "非 Windows 环境，无法设置开机自启"}
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as k:
            if enable:
                winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, _autostart_cmd())
            else:
                try:
                    winreg.DeleteValue(k, AUTOSTART_NAME)
                except OSError:
                    pass
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------- 日志尾部

def read_log_tail(max_lines=200):
    """读取 server.log 末尾若干行，供界面就地查看。"""
    try:
        with open(LOG_FILE, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return "".join(lines[-max_lines:]) or "（暂无日志）"
    except OSError:
        return ""


def pid_alive(pid):
    """Windows 下检测进程是否仍存活（STILL_ACTIVE）。检测失败时保守视为存活。"""
    try:
        import ctypes

        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    except Exception:
        return True


def prune_running():
    """清理已退出进程的运行记录，避免详情页显示僵尸'运行中'。

    仅在有变化时写回 data.json（前端每 4 秒轮询，不变化不写盘）。
    """
    with LOCK:
        new = {}
        changed = False
        for k, entries in RUNNING.items():
            alive = [e for e in entries if e.get("pid") and pid_alive(e["pid"])]
            if alive:
                new[k] = alive
            if len(alive) != len(entries):
                changed = True
        if set(new) != set(RUNNING):
            changed = True
        RUNNING.clear()
        RUNNING.update(new)
        if changed:
            data = load_data()
            data["running"] = RUNNING
            save_data()


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 + keep-alive：让浏览器复用同一条 TCP 连接。
    # 默认 HTTP/1.0 是每请求一连接，扫描时前端并发多个请求会反复开新连接，
    # 短连接 + TIME_WAIT 堆积到峰值时个别连接建立（握手）失败，
    # 表现为「服务端已成功处理、前端却报 Failed to fetch / 本地服务无响应」。
    # 所有响应均带正确 Content-Length，切到 keep-alive 安全。
    protocol_version = "HTTP/1.1"

    # pi-lens-ignore: reportIncompatibleMethodOverride
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self):
        # pi-lens-ignore: unchecked-throwing-call-python
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        # pi-lens-ignore: unchecked-throwing-call-python
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _body_bytes(self):
        # pi-lens-ignore: unchecked-throwing-call-python
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _origin_ok(self):
        """跨站请求防护：浏览器发起的跨域 POST 必带 Origin 头，非本机来源一律拒绝。
        无 Origin（本页 fetch / 本机脚本）放行。"""
        o = self.headers.get("Origin", "")
        if not o:
            return True
        return o in (f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}")

    def do_GET(self):
        try:
            self._do_get()
        except Exception as e:
            log(f"处理失败 {self.path.split('?')[0]}：{e}\n{traceback.format_exc().rstrip()}")
            try:
                self._json({"ok": False, "error": "服务器内部错误，详情见日志"}, 500)
            except Exception:
                pass

    def _do_get(self):
        path = self.path.split("?")[0]
        if path == "/api/tree":
            data = load_data()
            return self._json({"ok": True, "data": data})
        if path == "/api/intro/batch":
            qs = parse_qs(urlparse(self.path).query)
            st = batch_status((qs.get("taskId") or [""])[0])
            if not st:
                return self._json({"ok": False, "error": "任务不存在或已清理"}, 404)
            return self._json(st)
        if path == "/api/config":
            c = mask_config(load_config())
            c["autostart"] = autostart_status()
            return self._json({"ok": True, "config": c})
        if path == "/api/doc":
            qs = parse_qs(urlparse(self.path).query)
            real = path_allowed(qs.get("path", [""])[0])
            if not real:
                return self._json({"ok": False, "error": "路径不在扫描根内或不存在"})
            fname, content = read_doc(real)
            return self._json({"ok": True, "file": fname, "content": content})
        if path == "/api/files":
            qs = parse_qs(urlparse(self.path).query)
            real = path_allowed(qs.get("path", [""])[0])
            if not real:
                return self._json({"ok": False, "error": "路径不在扫描根内或不存在"})
            return self._json(
                {"ok": True, "files": search_files(real, qs.get("q", [""])[0])}
            )
        if path == "/api/running":
            prune_running()
            items = [
                {"path": k, "pid": e["pid"], "cmd": e["cmd"]}
                for k, v in RUNNING.items()
                for e in v
            ]
            return self._json({"ok": True, "running": items})
        if path == "/api/editors":
            editors, agents = detect_all()
            return self._json({"ok": True, "editors": editors, "agents": agents})
        if path == "/api/status":
            return self._json({"ok": True, "scanning": _scanning})
        if path == "/api/log":
            return self._json({"ok": True, "log": read_log_tail()})
        if path == "/api/trending":
            qs = parse_qs(urlparse(self.path).query)
            r = fetch_trending(
                (qs.get("since") or ["daily"])[0],
                (qs.get("lang") or [""])[0],
                (qs.get("refresh") or [""])[0] == "1",
            )
            if r.get("ok"):
                r.update(trend_store_public())
                r["langs"] = [lb for lb, _ in TREND_LANGS]
            return self._json(r)
        if path == "/api/trending/intros":
            qs = parse_qs(urlparse(self.path).query)
            st = trend_batch_status((qs.get("taskId") or [""])[0])
            if not st:
                return self._json({"ok": False, "error": "任务不存在或已清理"}, 404)
            return self._json(st)
        return self._static(path)

    TYPES = {
        ".html": "text/html",
        ".js": "text/javascript",
        ".css": "text/css",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
    }

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        fname = os.path.normpath(path.lstrip("/"))
        if ".." in fname:
            return self._json({"ok": False, "error": "forbidden"}, 403)
        full = os.path.join(WEB_DIR, fname)
        if not os.path.isfile(full):
            return self._json({"ok": False, "error": "not found"}, 404)
        ext = os.path.splitext(full)[1]
        # pi-lens-ignore: unchecked-throwing-call-python
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header(
            "Content-Type",
            self.TYPES.get(ext, "application/octet-stream") + "; charset=utf-8"
            if ext in self.TYPES
            else "application/octet-stream",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._origin_ok():
            log(f"拒绝跨站请求 {path} Origin={self.headers.get('Origin', '(无)')}")
            return self._json({"ok": False, "error": "拒绝跨站请求"}, 403)
        try:
            if path == "/api/scan":
                return self._json(run_scan())
            if path == "/api/intro":
                body = self._body_json()
                return self._json(generate_intro(body.get("path", "")))
            if path == "/api/intro/batch":
                body = self._body_json()
                tree = load_data().get("tree") or {}
                # 只接受树中真实存在的项目，避免传入任意路径
                paths = [
                    p
                    for p in (body.get("paths") or [])
                    if isinstance(p, str) and find_node(tree, p)
                ]
                if not paths:
                    return self._json({"ok": False, "error": "没有可生成介绍的项目"})
                tid, task = start_batch(paths)
                return self._json({"ok": True, "taskId": tid, "total": task["total"]})
            if path == "/api/intro/batch/stop":
                body = self._body_json()
                with _gen_lock:
                    task = _gen_tasks.get(str(body.get("taskId", "")))
                if task:
                    task["stop"] = True
                    log("批量生成已请求停止")
                return self._json({"ok": True})
            if path == "/api/intro/manual":
                body = self._body_json()
                with LOCK:
                    node = find_node(
                        load_data().get("tree") or {}, body.get("path", "")
                    )
                    if not node:
                        return self._json({"ok": False, "error": "找不到节点"})
                    node["intro"] = (body.get("text") or "").strip()[:100]
                    node["introSource"] = "manual" if node["intro"] else ""
                    save_data()
                return self._json({"ok": True, "node": node})
            if path == "/api/mark":
                body = self._body_json()
                with LOCK:
                    node = find_node(
                        load_data().get("tree") or {}, body.get("path", "")
                    )
                    if not node:
                        return self._json({"ok": False, "error": "找不到节点"})
                    mark = body.get("mark")
                    if mark in ("manual", "off", "auto"):
                        node["marked"] = mark
                        save_data()
                return self._json({"ok": True, "node": node, "needRescan": True})
            if path == "/api/meta":
                # 收藏 / 标签 / 备注：用户随手维护的轻量元数据
                body = self._body_json()
                with LOCK:
                    node = find_node(
                        load_data().get("tree") or {}, body.get("path", "")
                    )
                    if not node:
                        return self._json({"ok": False, "error": "找不到节点"})
                    if "starred" in body:
                        node["starred"] = bool(body["starred"])
                    if "note" in body:
                        node["note"] = str(body.get("note") or "").strip()[:500]
                    if "tags" in body:
                        raw = body.get("tags") or []
                        if isinstance(raw, str):
                            raw = re.split(r"[,，]", raw)
                        node["tags"] = [
                            str(t).strip()[:20] for t in raw if str(t).strip()
                        ][:10]
                    save_data()
                return self._json({"ok": True, "node": node})
            if path == "/api/open":
                body = self._body_json()
                real = path_allowed(body.get("path", ""))
                if not real:
                    return self._json(
                        {"ok": False, "error": "路径不在扫描根内或不存在"}
                    )
                mode = body.get("mode", "explorer")
                log(f"跳转 {real} mode={mode}")
                r = open_path(real, mode, body.get("editor"), body.get("agent"))
                if r.get("ok"):
                    try:
                        record_usage(real)
                    except Exception as e:
                        log(f"记录使用失败：{e}")
                return self._json(r)
            if path == "/api/launch":
                body = self._body_json()
                real = path_allowed(body.get("path", ""))
                if not real:
                    return self._json(
                        {"ok": False, "error": "路径不在扫描根内或不存在"}
                    )
                cmd = launcher_cmd(body.get("launcher") or {})
                if not cmd:
                    return self._json({"ok": False, "error": "无效的启动入口"})
                full = f"Set-Location -LiteralPath '{ps_quote(real)}'; {cmd}"
                proc = subprocess.Popen(
                    ["powershell", "-NoExit", "-Command", full],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
                # RUNNING 会被 GET /api/running 的 prune_running 并发读取（持锁迭代），
                # 这里的增也需持锁，避免 dict 在迭代中途被改大小抛异常。
                with LOCK:
                    RUNNING.setdefault(real, []).append({"pid": proc.pid, "cmd": cmd})
                _persist_running()
                log(f"启动 {real} cmd={cmd} pid={proc.pid}")
                try:
                    record_usage(real)
                except Exception as e:
                    log(f"记录使用失败：{e}")
                return self._json({"ok": True, "pid": proc.pid, "cmd": cmd})
            if path == "/api/stop":
                body = self._body_json()
                target = os.path.normpath(body.get("path", ""))
                killed = []
                with LOCK:
                    removed = RUNNING.pop(target, [])
                for entry in removed:
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(entry["pid"]), "/T", "/F"],
                            capture_output=True,
                        )
                        killed.append(entry["pid"])
                    except OSError:
                        pass
                if removed:
                    _persist_running()
                log(f"停止 {target} killed={killed}")
                return self._json({"ok": True, "killed": killed})
            if path.startswith("/api/trending/"):
                # 榜单相关：只接受 owner/repo 形式的仓库名，杜绝拼进 URL 的任意输入
                sub = path[len("/api/trending/") :]
                body = self._body_json()
                if sub == "intro":
                    name = str(body.get("fullName", "")).strip()
                    if not GH_NAME_RE.match(name):
                        return self._json(
                            {"ok": False, "error": "仓库名格式应为 owner/repo"}
                        )
                    return self._json(trend_intro(name, str(body.get("desc") or "")))
                if sub == "guide":
                    name = str(body.get("fullName", "")).strip()
                    if not GH_NAME_RE.match(name):
                        return self._json(
                            {"ok": False, "error": "仓库名格式应为 owner/repo"}
                        )
                    return self._json(trend_guide(name, str(body.get("desc") or "")))
                if sub == "intros":
                    raw = body.get("items") or []
                    items = []
                    for it in raw:
                        n = (
                            str(it.get("fullName", "")).strip()
                            if isinstance(it, dict)
                            else ""
                        )
                        if GH_NAME_RE.match(n):
                            items.append(
                                {"fullName": n, "desc": str(it.get("desc") or "")}
                            )
                    if not items:
                        return self._json({"ok": False, "error": "没有可生成的仓库"})
                    tid, task = start_trend_batch(items)
                    return self._json(
                        {"ok": True, "taskId": tid, "total": task["total"]}
                    )
                if sub == "intros/stop":
                    with _trend_task_lock:
                        task = _trend_tasks.get(str(body.get("taskId", "")))
                    if task:
                        task["stop"] = True
                    return self._json({"ok": True})
                if sub == "digest":
                    return self._json(
                        trend_digest(
                            str(body.get("since") or "weekly"),
                            str(body.get("lang") or ""),
                        )
                    )
            if path == "/api/pick-dir":
                # 同一时刻只允许一个选择窗口，避免连点弹出多个
                if not _pick_lock.acquire(blocking=False):
                    return self._json(
                        {"ok": False, "error": "已有一个选择窗口打开，请先完成选择"})
                try:
                    picked, err = pick_dir()
                finally:
                    _pick_lock.release()
                # 该线程为弹选择框阻塞了十几秒甚至更久，期间浏览器可能已把它
                # 所在的 keep-alive 连接判定为超时关闭。这里显式关掉连接，
                # 避免旧的半开连接被浏览器复用 → 下一次请求被 RST → 前端误报
                # "服务未启动"（实际服务正常，重试即好）。
                self.close_connection = True
                if err:
                    return self._json({"ok": False, "error": err})
                if not picked:
                    return self._json({"ok": True, "cancelled": True})
                return self._json({"ok": True, "path": picked})
            if path == "/api/config":
                body = self._body_json().get("config", {})
                cfg = load_config()
                with LOCK:
                    for key in (
                        "port",
                        "roots",
                        "blacklist",
                        "editor",
                        "backupDir",
                        "gitStatus",
                        "autoScan",
                        "ghToken",
                        "ghMirror",
                    ):
                        if key in body:
                            cfg[key] = body[key]
                    if body.get("theme") in ("dark", "light", "auto"):
                        cfg["theme"] = body["theme"]
                    if "api" in body:
                        newapi = body["api"]
                        for k in ("provider", "baseUrl", "model"):
                            if k in newapi:
                                cfg["api"][k] = newapi[k]
                        key = newapi.get("apiKey", "")
                        if key and key != MASK:
                            cfg["api"]["apiKey"] = encrypt_api_key(key)
                    save_config()
                autostart_err = None
                if "autostart" in body:
                    ar = set_autostart(bool(body["autostart"]))
                    if not ar.get("ok"):
                        autostart_err = ar.get("error")
                resp = mask_config(cfg)
                resp["autostart"] = autostart_status()
                return self._json({"ok": True, "config": resp, "autostartError": autostart_err})
            if path == "/api/config/test":
                body = self._body_json()
                api = dict(body)
                if api.get("apiKey") == MASK:
                    api["apiKey"] = api_key()
                if not (api.get("baseUrl") and api.get("model")):
                    return self._json({"ok": False, "error": "请填写 baseUrl 和模型名"})
                text = _test_api(api)
                return self._json(
                    {
                        "ok": bool(text),
                        "reply": text,
                        "error": None if text else "请求失败，请检查地址/key/模型名",
                    }
                )
            if path == "/api/backup/export":
                r = backup_export(self._body_json().get("dest", ""))
                log(f"导出备份 ok={r.get('ok')} path={r.get('path', '')}")
                return self._json(r)
            if path == "/api/backup/import":
                r = backup_import(self._body_bytes())
                log(f"导入备份 ok={r.get('ok')} {r.get('error', '')}")
                return self._json(r)
            return self._json({"ok": False, "error": "unknown api"}, 404)
        except Exception as e:
            log(f"处理失败 {path}：{e}\n{traceback.format_exc().rstrip()}")
            return self._json({"ok": False, "error": str(e)}, 500)


def _test_api(api):
    url = api["baseUrl"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api.get("apiKey"):
        headers["Authorization"] = "Bearer " + api["apiKey"]
    body = {
        "model": api["model"],
        "messages": [{"role": "user", "content": "回复OK"}],
        "max_tokens": 5,
    }
    try:
        req = Request(url, data=json.dumps(body).encode(), headers=headers)
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def create_server():
    """创建 HTTP 服务实例，返回 (server, url)。端口被占用时抛 OSError。

    控制台模式（server.py）与托盘模式（tray_app.py）共用。
    """
    global PORT
    cfg = load_config()
    # pi-lens-ignore: unchecked-throwing-call-python
    port = int(cfg.get("port", 6173))
    PORT = port
    restore_running()  # 服务启动（控制台/托盘共用）时恢复上次运行中的进程
    # 启动时"维持最新"：旧索引立即可用，若已较旧则后台异步重建索引（不阻塞首屏）。
    # 刚扫过（1 小时内）不重复扫；可用 config.autoScan 关闭。
    if cfg.get("autoScan", True) and not _index_fresh():
        threading.Timer(
            1.5, lambda: threading.Thread(target=_safe_auto_scan, daemon=True).start()
        ).start()
    # 后台预热编辑器 / Agent 探测（注册表全表扫描较慢），首次点击不再卡顿
    threading.Thread(target=detect_all, daemon=True).start()
    # 界面地址统一用 127.0.0.1 而非 localhost：服务只绑定 IPv4 loopback。
    # localhost 在部分环境（DNS、系统代理、localhost 解析到 ::1 等）下不确定，
    # 会出现「页面能打开、fetch 接口却连不上」。用 IP 直连是确定性连接目标。
    # Windows 的 SO_REUSEADDR 允许第二个进程静默绑定已被监听的端口，导致两个
    # 实例共存、请求被随机截胡（表现为「改了代码没生效 / unknown api」）。
    # 因此绑定前先用一个不带 SO_REUSEADDR 的裸 socket 做真实占用检测。
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        who = _port_occupant(port) or "未知进程"
        raise OSError(
            f"端口 {port} 已被 {who} 占用。"
            "若是残留的旧实例，请结束它（taskkill /PID <pid> /F）或双击 stop.bat"
        ) from None
    finally:
        probe.close()
    return ThreadingHTTPServer(("127.0.0.1", port), Handler), f"http://127.0.0.1:{port}"


def main():
    try:
        server, url = create_server()
    except OSError as e:
        print(f"启动失败：端口 {PORT} 无法绑定（{e}）")
        print("可能已有星图在运行。请先双击 stop.bat，或在设置中更换端口后重启。")
        log(f"启动失败，端口 {PORT} 被占用：{e}")
        sys.exit(1)
    print(f"星图 StarChart 已启动：{url}  （数据目录：{DATA_DIR}）")
    print("按 Ctrl+C 停止；或双击 stop.bat")
    if os.environ.get("STARCHART_NO_BROWSER") != "1":
        threading.Timer(0.8, lambda: open_ui(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
