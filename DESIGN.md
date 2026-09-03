# 星图 StarChart 设计文档

> 本地项目导航工具「星图 StarChart」：把工作区各项目绘成一张星图——
> 扫描 `d:\work` 工作区，为每个项目生成中文一句话介绍，
> 树状展示 + 搜索 + 路径跳转 + 备份导出，解决"项目多了记不住、找不到"的问题。
> 领域术语见 `d:\work\CONTEXT.md`。

## 1. 技术选型

| 项 | 决定 |
|---|---|
| 后端 | Python 3 标准库（`http.server` + `urllib`），单服务进程 |
| 前端 | 原生 HTML/JS/CSS 单页，无构建步骤，由后端静态托管 |
| 第三方依赖 | `pypinyin`（拼音首字母搜索，纯 Python）+ `pystray`（托盘常驻），两者缺失均可降级 |
| AI 接口 | OpenAI 兼容 `/v1/chat/completions`，baseUrl/key/model 可配置 |
| 数据存储 | 工具目录下 `starchart\.starchart\` 的两个 JSON 文件（可用 `STARCHART_HOME` 改位置） |

免费 AI API 候选（OpenAI 兼容，国内直连）：
- 智谱 `https://open.bigmodel.cn/api/paas/v4`，model `glm-4-flash`（免费）
- 硅基流动 `https://api.siliconflow.cn/v1`，model `Qwen/Qwen2.5-7B-Instruct`（免费档）

## 2. 目录布局

```
d:\work\02toolsMy\starchart\       ← 工具代码（住址）
├── DESIGN.md          本文档
├── start.bat          双击启动：托盘常驻，后台无窗口 + 全局热键 Ctrl+Alt+S
├── start-console.bat  保留控制台窗口的启动方式（排障用）
├── stop.bat           结束服务
├── server.py          后端（扫描/AI/跳转/备份/静态托管）
├── tray_app.py        常驻入口（系统托盘 + 全局热键 + 后台服务）
├── requirements.txt   pypinyin、pystray
├── .starchart\        ← 数据（v1.2 起随工具走，拷走整个文件夹即完整迁移）
│   ├── data.json      树 + 项目元数据 + 介绍
│   └── config.json    扫描根/黑名单/API key/编辑器/端口/主题
└── web\
    ├── index.html
    ├── app.js
    └── style.css
```

## 3. 数据结构

### config.json

```json
{
  "port": 6173,
  "roots": ["d:\\work", "e:\\projects"],
  "blacklist": ["05zip", "node_modules", ".git", ".trae", "新建文件夹"],
  "api": { "provider": "zhipu", "baseUrl": "", "apiKey": "", "model": "" },
  "editor": { "name": "Trae", "cmd": "trae \"{path}\"" },
  "backupDir": "",
  "theme": "dark",
  "gitStatus": True
}
```

`roots` 可配置多个扫描根，界面会按根分组展示；`api.apiKey` 保存时经 Windows DPAPI 加密后以
`dpapi:base64` 前缀写入，本机当前用户可解密；`gitStatus` 控制扫描时是否读取 Git 状态。

黑名单匹配规则：**目录名**精确匹配或 `fnmatch` 通配符（如 `*.tmp`），命中则整个子树跳过。界面可视化增删。

### data.json

```json
{
  "version": 1,
  "generatedAt": "2026-09-01T10:00:00",
  "tree": {
    "name": "02toolsMy",
    "path": "d:\\work\\02toolsMy",
    "type": "dir",
    "mtime": "2026-08-30T18:00:00",
    "fileCount": 0,
    "children": [
      {
        "name": "winClean",
        "path": "d:\\work\\02toolsMy\\winClean",
        "type": "project",
        "intro": "Windows 清理脚本集，含清洁/迁移两个 PowerShell 入口",
        "introSource": "ai",
        "py": "winClean",
        "fingerprint": "a1b2c3",
        "marked": "auto",
        "mtime": "...",
        "fileCount": 3,
        "children": []
      }
    ]
  }
}
```

字段说明：
- `type`：`dir` 普通目录（继续递归）| `project` 项目（叶子节点，不再深入）
- `introSource`：`ai` | `static` | `manual`（manual 扫描永不覆盖）
- `py`：项目名的拼音首字母小写串，扫描时用 pypinyin 预计算
- `fingerprint`：项目内（2 层深度）文件数 + 最大 mtime 的哈希，用于增量判断
- `marked`：`auto` 自动识别 | `manual` 手动标记 | `off` 手动排除（目录本身有标志物但用户不想当项目）
- 排除语义（v1.1 修订）：被排除的节点**从树中隐藏**（不显示为普通文件夹）；重扫时 `marked` 在目录分支同样回填，排除状态永久保留；恢复入口在 ⚙ 设置 →「已排除的项目」列表，点「恢复」即重新纳入；介绍生成与待办统计跳过被排除的整棵子树
- 散文件不进树，数量记在父节点 `fileCount`，界面显示 "+N 文件"
- `git`：Git 状态，**仅当该目录是 Git 仓库且读取成功时才存在**——
  `branch` 分支名、`dirty` 是否有未提交更改、`ahead` / `behind` 领先 / 落后远程的提交数、
  `lastCommit` 最后提交时间。它只是展示属性，拿不到就整块不显示，
  不影响项目进入索引（见 `CONTEXT.md`「Git 状态」）
- `stack`：技术栈标签（最多 3 个），扫描时根据标志文件与依赖推断，仅用于展示
- `starred` / `tags` / `note`：收藏、标签（≤10 个）、备注（≤500 字），
  用户在详情面板维护，可被搜索，重扫不丢失
- `data.json` 顶层 `usage`：使用记录 `{path: {count, last}}`，
  每次通过星图真实打开 / 启动项目时累加，是「常用」视图 frecency 排序的数据源
- `data.json` 顶层 `running`：运行中的启动项 `{path: [{pid, cmd}]}`，
  随本地启动写盘，服务重启后据此恢复运行状态（见 4.9）

### 项目识别规则（领域定义见 CONTEXT.md）

1. 目录含标志物之一 → 项目：`.git`、`package.json`、`requirements.txt`、`pyproject.toml`、`README.md`、`go.mod`、`Cargo.toml`、`pom.xml`、`*.sln`、`app.py`
2. 手动 `marked=manual` → 项目（即使无标志物）；手动 `marked=off` → 非项目
3. 确定为项目的目录不再向下递归
4. 压缩包（.zip/.rar/.7z）不是项目，仅在父节点文件计数中体现

## 4. 功能规格

### 4.1 扫描（手动触发）

- 用户点"扫描"按钮 → 后端遍历 `roots`（跳过黑名单）→ 重建树
- **合并旧数据**：新树与旧 `data.json` 按路径对齐——
  - 保留：intro / introSource / py / marked / 展开状态
  - 新目录 → `marked=auto` 待定，intro 为空
  - 消失的目录 → 从树中移除
- **增量 AI 策略**：仅 `intro` 为空 或 `fingerprint` 变化且 `introSource != manual` 的项目进入"待生成"队列
- 扫描完成后界面提示"待生成介绍 N 个"，用户点"批量生成" → 后端**3 路并发**执行
  （`POST /api/intro/batch`，返回 taskId），前端轮询 `GET /api/intro/batch?taskId=`
  驱动进度条实时刷新；「停止」调 `POST /api/intro/batch/stop` 立即生效

### 4.2 介绍生成

优先级：`manual > ai > static`

- **AI 生成**：输入 = README 内容（截 3000 字符）+ 项目 2 层目录清单 + 关键文件名；system prompt 要求输出 ≤30 字中文一句话，只输出介绍本身。失败（无 key / 网络错误）自动降级 static
- **静态提取**：README 第一个非空行，剥除 Markdown 语法，截 50 字；无 README 则留空
- **手动编辑**：详情面板介绍区点击即编辑，保存后 `introSource=manual`

### 4.3 树展示

- 只显示目录与项目节点；目录可展开/折叠，展开状态存 localStorage，扫描后不重置
- 项目节点带 🛠 徽标；节点行显示介绍（灰色小字，截断）
- 单击选中 → 右侧详情；双击 → 打开资源管理器
- **多扫描根分组**：`roots` 多于 1 个时，每个根上方渲染分组头（粗体根名 + 根路径），
  点击可整根收起/展开，折叠状态存 localStorage；搜索时忽略折叠，直接平铺命中结果

### 4.4 搜索

顶栏搜索框，实时过滤：
- 匹配：目录名 / 项目名 / 介绍文本 / 完整路径 / 拼音首字母（`zhjy` → "智慧教育课程自动观看工具"）
- 结果模式下树只显示命中节点及其祖先链，高亮命中文本
- 回车跳到首个结果并选中；Esc 清空恢复完整树

### 4.5 路径跳转

四类跳转按钮：

| 按钮 | 实现 | 防重复 |
|---|---|---|
| 📂 资源管理器 | `explorer /select,"路径"` | 前端 1.5s + 后端 2s 同路径去重 |
| ⧉ 复制路径 | 剪贴板 | - |
| ▶ 终端 | PowerShell 新控制台窗口（`CREATE_NEW_CONSOLE`） | - |
| ✎ 编辑器 ×N | 按本机实际检测结果渲染多个按钮 | - |
| 🤖 Agent ×N | 按本机实际检测结果渲染（Claude Code 等） | - |

**编辑器检测**（`/api/editors`）：① PATH 中查找 `trae` / `trae-cn` / `trae-solo` / `code` / `cursor` / `windsurf` / `qoder` 等 CLI；② 注册表 Uninstall 扫描（HKCU/HKLM），匹配 Trae/Cursor/VSCode/Windsurf/Qoder 安装目录下的 `bin\*.cmd` CLI——覆盖自定义安装路径。检测到多个则全部显示；一个都没检测到时回落到设置中的自定义编辑器命令。

**终端 Agent 检测**：PATH 查找 `claude`(Claude Code) / `codex` / `gemini` / `qwen`(Qwen Code) / `iflow` / `opencode` / `crush` / `aider` / `copilot` / `codebuddy` / `kiro-cli` / `qodercli` / `workbuddy`(`wb`) / `zcode` / `dsh`。点击 🤖 按钮 → 新控制台窗口 cd 到项目目录并启动该 Agent。

**项目内配置文件证据**（扫描时写入 `node.agentHints`）：检测项目根目录的 19 类专属标记（TOOL_MARKERS，含 `.claude`/`CLAUDE.md`、`.codex`、`.gemini`/`GEMINI.md`、`.qwen`/`QWEN.md`、`.cursor`/`.cursorrules`、`.windsurf`、`.trae`、`.vscode`、`.codebuddy`/`CODEBUDDY.md`、`.kiro`、`opencode.json`、`.workbuddy`/`WORKBUDDY.md`、`.zcode`/`ZCODE.md`、`.dsh`/`DSH.md` 等）+ 通用标记（GENERIC_MARKERS：`AGENTS.md`/`.agents`）。详情面板过滤规则：**项目有专属证据 → 只显示"有证据且本机已安装"的按钮；只有通用证据或无证据 → 显示全部已安装供用户选择**。按钮 tooltip 注明证据来源与类型。

其余按钮：

| 按钮 | 行为 | 实现 |
|---|---|---|
| ✏ 编辑介绍 | 行内编辑 | — |

### 4.6 黑名单管理

设置弹窗内列表增删：输入目录名或通配符 → 立即写入 config → 提示"重新扫描生效"。预置：`05zip`、`node_modules`、`.git`、`.trae`、`新建文件夹`。

### 4.7 备份与恢复

- **导出**：仅打包 `data.json + config.json` 为 `starchart-YYYYMMDD.zip`（代码不入包，备份数据即可）；备份目录留空则弹保存框，配置了 `backupDir`（U 盘/网盘同步文件夹）则一键直达
- **导入**：选 zip → 校验 version 与 JSON 结构 → 覆盖前自动把现有两个 JSON 备份为 `.bak` → 写入并重新加载
- API key 属敏感信息，导出包中以 **DPAPI 密文**保留（不再出现明文）；故备份跨机器 / 跨用户恢复后
  key 无法解密，需在设置中重新填写（见安全说明）

### 4.8 设置

端口、扫描根（可加多个）、黑名单、编辑器命令模板、常用备份目录、主题（暗/亮）。

**LLM 配置（全部在工具界面内完成，无需改文件）**：
- 服务商预设下拉：智谱（自动填 `https://open.bigmodel.cn/api/paas/v4` + `glm-4-flash`）、硅基流动（自动填 + `Qwen/Qwen2.5-7B-Instruct`）、自定义 OpenAI 兼容地址（手填 baseUrl/model）
- API key 输入框掩码显示；保存时经 Windows DPAPI 加密后写入 config.json（本机当前用户可解），
  备份包内含的也是密文
- 「测试连接」按钮（`POST /api/config/test`）：发一条最小请求，实时显示成功/失败原因

### 4.9 本地启动

- **入口检测**（扫描时自动进行，存入节点 `launchers` 字段，最多 6 个）：
  - `package.json` scripts 中的 `dev` / `start` / `serve` → `npm run xxx`
  - Python 入口：`app.py` / `main.py` / `run.py`（`python xxx.py`）、`manage.py`（`python manage.py runserver`）
  - 启动脚本：`run.bat` / `start.bat` / `run.cmd` / `start.cmd`
  - `docker-compose.yml` → `docker compose up`
  - `Makefile` 含 `run:` 目标 → `make run`
- **行为**：点启动 → 弹出独立 PowerShell 窗口执行（cd 到项目目录），窗口内可直接看日志、Ctrl+C 或关窗停止；同时记录 PID，详情面板显示"● 运行中 (PID)"与「■ 停止」按钮（`taskkill /T /F` 结束进程树）
- 运行状态（PID）持久化到 data.json 顶层 `running` 字段（启动写入、停止/进程退出清理）；
  服务重启时自动校验进程存活并恢复——之前启动的项目不会变成孤儿，
  「● 运行中 / ■ 停止」在重启后依然有效

### 4.10 使用说明

详情面板「📖 使用说明」按钮：读取项目内说明文档（优先级 README.md > 使用说明.md > 说明.md > 需求文档.md > CLAUDE.md > AGENTS.md），前端内置轻量 Markdown 渲染（标题/列表/代码块/粗体/行内码，忽略图片徽章），滚动区展示；内容缓存，切换项目不重复拉取。

### 4.11 Git 状态（扫描时读取）

- 设置中可开关（默认开启）；关闭后扫描不再读取。
- 读取内容：分支名、是否有未提交更改、领先 / 落后远程的提交数、最后提交时间。
- 只对含 `.git` 的项目发起，6 线程并发；单个命令超时 2 秒即跳过。
- git 不在 PATH 时整块跳过，并在日志里记录一次。

**边界**：Git 状态是展示属性，不是项目判定依据——读取失败或不是仓库的项目照常进入索引，
只是详情面板里没有 Git 这一块。

## 5. 后端 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tree` | 返回完整 data.json |
| POST | `/api/scan` | 重新扫描，返回 `{new, changed, removed, pendingIntro}` |
| POST | `/api/intro` | `{path}` 生成单个介绍（AI→static 降级），返回更新后的节点 |
| POST | `/api/intro/manual` | `{path, text}` 保存手动介绍 |
| POST | `/api/intro/batch` | `{paths}` 启动批量生成（后端 3 路并发），返回 `{taskId, total}` |
| GET | `/api/intro/batch?taskId=` | 批量进度轮询 `{running, done, fail, total}` |
| POST | `/api/intro/batch/stop` | `{taskId}` 停止批量生成 |
| POST | `/api/mark` | `{path, mark: "manual"|"off"|"auto"}` |
| POST | `/api/meta` | `{path, starred?, tags?, note?}` 更新收藏 / 标签 / 备注 |
| GET | `/api/editors` | 本机探测到的编辑器 / Agent，返回 `{editors, agents}` |
| GET | `/api/files?path=&q=` | 项目内按文件名搜索，返回 `{files}` |
| POST | `/api/open` | `{path, mode: "explorer"|"terminal"|"editor"|"agent"}` |
| GET | `/api/running` | 当前运行中的启动项 `{path, pid, cmd}` |
| POST | `/api/launch` | `{path, launcher}` 新终端窗口启动项目 |
| POST | `/api/stop` | `{path}` 结束该项目的启动进程树 |
| GET | `/api/doc?path=` | 读取项目说明文档原始 Markdown |
| GET | `/api/config` | 读配置（key 打码） |
| POST | `/api/config` | 保存配置（key 走 DPAPI 加密） |
| POST | `/api/config/test` | 用提交的 API 配置发最小请求，返回成功 / 原因 |
| POST | `/api/backup/export` | `{dest?}` 打包导出 |
| POST | `/api/backup/import` | 上传 zip 恢复 |
| GET | `/*` | 静态托管 web\ 目录 |

安全边界：仅监听 `127.0.0.1`；`/api/open` 只允许跳转到 roots 之内解析后的真实目录。
API key 以 Windows DPAPI 密文存储（仅本机当前用户可解），不再出现明文。

## 6. 界面规格

```
┌──────────────────────────────────────────────────────────┐
│ 🔍 搜索（拼音/名称/介绍/路径）        [扫描] [⚙设置] [📤备份] │
├────────────────────────────┬─────────────────────────────┤
│ 树（可展开/折叠，展开态记忆） │ 项目名 + 🛠徽标              │
│  📁 02toolsMy               │ 介绍（点击行内编辑）          │
│   ├─ 🛠 winClean 介绍…      │ ──────────────────────      │
│   ├─ 🛠 智慧教育… 介绍…      │ 路径　d:\work\02toolsMy\…    │
│  📁 03clones                │ 修改　2026-08-30　+N 文件    │
│   └─ 🛠 react-bits 介绍…    │ [📂][⧉][▶][✎][✏] 按钮组     │
└────────────────────────────┴─────────────────────────────┘
```

- 暗色默认（深灰底 #1e1e1e、蓝紫强调），设置可切亮色
- 树悬停高亮、选中态左侧强调条；介绍文字灰色小号
- 批量生成时顶栏出现进度条（N/M）+ 停止按钮
- 空状态：首次启动引导"点扫描建立索引"

## 7. 实现顺序

1. `server.py`：静态托管 + 扫描 + data.json 读写
2. 前端树 + 详情 + 跳转
3. 搜索（含 pypinyin）
4. 黑名单 + 设置 + 手动标记/介绍编辑
5. AI 介绍（含批量队列）+ 降级
6. 备份导出/导入
7. start.bat / stop.bat 收尾
