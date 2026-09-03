# 技术方案 · GitHub 趋势榜 & 仓库中文导读（星图 StarChart）

| 项 | 内容 |
|---|---|
| 版本 | v1.0（2026-09-03） |
| 对应 PRD | `docs/PRD-GitHub趋势与中文导读.md` |
| 代码基线 | `server.py`（趋势部分约 L1394–L1770）、`web/app.js`（约 L1769–L2060）、`web/style.css`（趋势面板样式段）、静态资源版本 `?v=20260903a` |
| 架构约束 | 纯 Python 3 标准库后端（`http.server.ThreadingHTTPServer`）+ 原生 JS 前端，**禁止引入第三方运行时依赖** |

---

## 1. 架构总览

```
浏览器（web/index.html + app.js，原生 JS）
   │  fetch /api/trending*（GET/POST，同源，POST 校验 Origin）
   ▼
server.py（ThreadingHTTPServer，端口 6173，可配置）
   │
   ├─ fetch_trending ──► github.com/trending（HTML 抓取，正则解析）
   │        └─ 缓存 trending.json → lists["{since}|{lang}"]，TTL 30min，失败降级旧缓存
   ├─ gh_repo_meta ────► api.github.com/repos/{owner}/{repo}（默认分支/描述/topics/star）
   ├─ gh_readme ───────► raw.githubusercontent.com/{repo}/{branch}/README*（≤4000 字）
   ├─ llm_chat ────────► 设置里配置的 OpenAI 兼容接口（baseUrl+/chat/completions）
   │        ├─ trend_intro （一句话介绍，长期缓存 intros）
   │        ├─ trend_guide （四段式导读，长期缓存 guides）
   │        └─ trend_digest（整榜速览，按天缓存 digests，留 30 份）
   └─ start_trend_batch（后台线程池 3 并发 + taskId 轮询 + 可停止）
```

无数据库，所有持久化落在一个 JSON 文件：`{DATA_DIR}/trending.json`。

## 2. 缓存文件结构（trending.json）

```json
{
  "lists":   { "daily|python": { "fetchedAt": 1756870000, "items": [ /* 卡片对象 */ ] } },
  "intros":  { "fmtlib/fmt":    { "text": "…≤30字…", "at": 1756870123, "source": "ai" } },
  "guides":  { "fmtlib/fmt":    { "text": "…四段式…", "at": 1756870456 } },
  "digests": { "weekly||2026-09-03": { "text": "…", "at": 1756870789 } }
}
```

写盘策略：`tmp + os.replace` 原子替换；所有读改写持 `_trend_lock`（RLock）。

**榜单卡片对象**（`parse_trending` 产出，前端直接消费）：

```json
{ "rank": 1, "fullName": "fmtlib/fmt", "owner": "fmtlib", "name": "fmt",
  "desc": "A modern formatting library", "lang": "C++",
  "langColor": "#f34b7d", "stars": 24352, "forks": 3011, "today": 14,
  "url": "https://github.com/fmtlib/fmt", "zreadUrl": "https://zread.ai/fmtlib/fmt" }
```

## 3. 后端实现要点（server.py）

### 3.1 抓取与解析

- `_gh_get(url, accept)`：统一带 UA（伪装 Chrome）与可选 `ghToken`（⚙ 设置可配，缓解 API 60 次/h 限流）；返回文本或 None。
- `parse_trending(page)`：按 `<article class="Box-row">` 切块，正则提取仓库名、描述、语言、星数/fork/本期新增、语言色点。**已知脆弱点**：GitHub 曾把描述 class 改为临时类 `tmp-pr-4`，解析器已做多 class 回退（`col-9` → `color-fg-muted`），改版仍可能失效——PRD O4。
- **trending 页不含 topics**，topics 只能经 `gh_repo_meta`（REST API）获得。
- `fetch_trending(since, lang, force)`：合法 since ∈ {daily, weekly, monthly}；lang 用 label→slug 表 `TREND_LANGS`（19 种）；缓存键 `f"{since}|{slug}"`；网络失败且有旧缓存时返回 `stale: true` + 旧数据。

### 3.2 LLM 封装与三个生成器

- `llm_chat(system, user, max_tokens, temperature)`：读设置 `api.{baseUrl,model,apiKey}`（key 经 `api_key(cfg)` 解密），OpenAI 兼容 `/chat/completions`，45s 超时，失败返回 None。
- `trend_intro`：README + 简介 → 15~30 字一句话；输出截 60 字符；缓存键 = fullName。
- `trend_guide`：README + 简介 + topics(前8) + 主语言 + star → 四段式纯文本（格式见 prompt 常量）；经 `_clean_guide` 清洗（去 `1)` 编号、`- -` 重复破折号）；缓存键 = fullName。
- `trend_digest`：榜单前 25 条拼简介 → 严格 8 行输出（本期风向 1 行 + 4 条观察 + 3 条推荐）；键 `"{since}|{lang}|{YYYY-MM-DD}"`，字典截尾留 30 份。
- 提示词都要求「纯文本、无 Markdown 标题、无编号、无客套」，配合前端 `renderAIBlock` 的行级解析。

### 3.3 批量任务模型（与本地项目批量介绍同一模式）

```
POST /api/trending/intros {items:[{fullName,desc}]}
  → start_trend_batch → tid，后台 ThreadPoolExecutor(GEN_WORKERS=3)
GET  /api/trending/intros?taskId=xx
  → {running, done, fail, total}，前端 800ms 轮询
POST /api/trending/intros/stop {taskId} → task["stop"]=True（协作式停止）
```

已完成任务在 `_trend_tasks` 里只保留最近 4 个，防泄漏。

### 3.4 HTTP API 契约

| Method | Path | 入参 | 返回（关键字段） |
|---|---|---|---|
| GET | `/api/trending` | query: `since`(`daily`默认), `lang`(label 或 slug), `refresh=1` 绕缓存 | `{ok, items[], cached, stale?, fetchedAt, langs[19], intros{repo:text}, guides{repo:text}, error?}` |
| GET | `/api/trending/intros` | query: `taskId` | `{ok, running, done, fail, total}`；任务不存在 404 |
| POST | `/api/trending/intro` | `{fullName, desc?}` | `{ok, text, cached, source}` |
| POST | `/api/trending/guide` | `{fullName, desc?}` | `{ok, text, cached}` |
| POST | `/api/trending/intros` | `{items:[{fullName,desc}]}` | `{ok, taskId, total}` |
| POST | `/api/trending/intros/stop` | `{taskId}` | `{ok}` |
| POST | `/api/trending/digest` | `{since, lang}` | `{ok, text, cached}` |

**安全约束**：`intro/guide/intros` 的 fullName 必须过 `GH_NAME_RE`（`^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$`），拒绝任意 URL 片段；所有 POST 走 `_origin_ok()` 同源校验。

## 4. 前端实现要点（web/app.js）

### 4.1 状态与入口

```js
const TREND_SINCE = [["daily","今日"],["weekly","本周"],["monthly","本月"]];
const trend = { since:"daily", lang:"", items:[], intros:{}, guides:{}, langs:[], fetchedAt:0, filter:"", digest:"" };
let trendPoll = null, trendTaskId = null;   // 批量轮询句柄
```

入口：顶栏 `#btn-trending` → `openTrending()` 渲染 modal（复用通用 `openModal(html, returnFocus)`）。

### 4.2 函数清单

| 函数 | 职责 |
|---|---|
| `trendLoad(force)` | 拉 `/api/trending`，渲染列表与 meta；**数据回来后回填语言下拉**（修复：首次打开时选项早于数据） |
| `trendRender()` | 按 `trend.filter` 过滤后渲染 `.trend-card` 列表（模板字符串） |
| `trendOneIntro / trendOneGuide` | 单条生成；guide 成功后直接弹导读 modal |
| `trendBatch` | 批量介绍：只提交**尚无 intro** 的条目，轮询进度条，结束后重拉缓存 |
| `trendDigest` | 速览：渲染进 `#tr-digest-box` |
| `trendAdHoc` | 任意仓库导读：清洗输入（去 `github.com/` 前缀、`.git` 后缀）后复用 guide 接口 |
| `renderAIBlock(text)` | 行级解析 AI 纯文本：`- ` → `<ul>`；`适合：/上手：` → kv；`推荐：` → ⭐行；`本期风向：` → 高亮行 |
| `openGuideModal` | 导读弹窗：结构化渲染 + Zread 外链 + 缓存标识 |

### 4.3 样式（style.css 趋势段）

主题走 CSS 变量（`--bg/--accent/--text-dim` 等，自动适配暗/亮主题）；关键类：`.trend-card`、`.tc-head/-rank/-name/-lang/-star/-today/-desc/-intro/-acts`、`.seg`（时段分段控件）、`.tp-bar`（批量进度条）、`.digest-box`、`.guide-box`、`.ai-*`（AI 文本结构化渲染）。

## 5. 已验证结论与实现过程中的坑（交接必读）

1. **GitHub trending 页无公开 JSON API**：数据是服务端渲染进 HTML 的，只能抓取解析；描述 class 曾改名为临时类 `tmp-pr-4`，解析必须做多 class 回退。
2. **trending 页不含 topics**：topics 需对每个仓库单独调 REST API（未认证 60 次/h，配置 ghToken 后 5000 次/h）。
3. **curl（Windows schannel）证书吊销检查会误报失败**：验证连通性用 Python urllib 或 node，勿以 curl 失败下结论。
4. **Windows 同端口双实例陷阱**：`ThreadingHTTPServer` 的 `allow_reuse_address` 在 Windows 上允许两个进程同时 LISTEN 同一端口，旧实例会截胡请求（表现为「改了代码没生效/404」）。升级代码后务必 `netstat -ano | grep :6173` 检查并 taskkill 旧 `pythonw.exe` 托盘实例。
5. **zread.ai 结论**：站点为 SSR + 无公开 API + 对自动化流量间歇 504，其「探索」底层数据不可获取；星图以「trending 抓取 + 任意仓库导读」等价实现（这也是 PRD O2 分类探索页的由来）。
6. **LLM 输出漂移**：模型会自作主张加编号/重复破折号/罗列榜单，靠「严格行数+行首约定」的 prompt + `_clean_guide` 后处理双保险；若换供应商建议改 JSON mode（PRD O6）。

## 6. 本地验证方法（回归测试清单）

```bash
# 1) 语法
python -c "import ast; ast.parse(open('server.py',encoding='utf-8').read())"
node --check web/app.js

# 2) 模块级单测（不启服务）
python -c "import server; r=server.fetch_trending('daily',''); print(r['ok'], len(r['items']))"
python -c "import server; print(server.trend_guide('fmtlib/fmt','modern formatting library')['text'])"

# 3) API 端到端
curl "http://127.0.0.1:6173/api/trending?since=daily"     # 期望 ok=true, items=19

# 4) UI 有头验证（agent-browser 不稳时的 Playwright 兜底，复用系统 Edge 零下载）
#    脚本模板见 .workbuddy/ui_verify.js；运行：
NODE_PATH="C:/Users/Administrator/.workbuddy/binaries/node/workspace/node_modules" \
  "C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-2/node.exe" ui_verify.js
#    关键断言：.trend-card 数量、#tr-lang option 数=19、无 pageerror、截图比对
```

**注意**：跑 UI 验证前确认只有一个实例在监听 6173（见 §5.4）。

## 7. 目录与配置速查

| 项 | 位置 |
|---|---|
| 缓存数据 | `{DATA_DIR}/trending.json`（DATA_DIR 由 `STARCHART_HOME` 环境变量或默认数据目录决定） |
| ghToken / LLM 配置 | ⚙ 设置 → `config.json` 的 `ghToken`、`api.{provider,baseUrl,model,apiKey}`（key 加密存储） |
| 并发数 | `GEN_WORKERS = 3`（server.py L59） |
| 前端缓存版本 | `web/index.html` 中 `?v=20260903a`（改 JS/CSS 后必须 bump） |

## 8. 附录 · zread.ai 探索功能调研记录（2026-09-03）

- 抓取方式：WebFetch（静态）+ agent-browser / curl（动态）均尝试。
- 发现：`zread.ai/` 与 `/trending` 内容服务端渲染直出 HTML；未观测到任何前端调用的公开 JSON API 端点；站点对非浏览器/自动化流量间歇返回 504，HAR 捕获不稳定。
- 结论：底层数据逻辑不可公开获取，不具备直接复用条件 → 转自研等价方案（本方案 F1/F2），并保留「Zread 解读」外链作为深读互补。
