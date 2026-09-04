/* 星图 StarChart 前端逻辑 — UX 审核后重构版 */

const $ = (s) => document.querySelector(s);

let data = { tree: null };
let config = {};
let selectedPath = null;
let searchQuery = "";
let generating = false;
let stopFlag = false;
let selectedNode = null;
const docCache = {};
const lastExplorer = {};
let detectedEditors = [];
let detectedAgents = [];
// 工具 id → 显示名。用于"项目期待但本机未安装"的提示，仅展示用，非全量严格表。
const TOOL_NAMES = {
	claude: "Claude Code",
	codex: "Codex CLI",
	gemini: "Gemini CLI",
	qwen: "Qwen Code",
	trae: "Trae",
	"trae-cn": "Trae CN",
	vscode: "VS Code",
	cursor: "Cursor",
	windsurf: "Windsurf",
	qoder: "Qoder",
	copilot: "Copilot CLI",
	aider: "Aider",
	opencode: "OpenCode",
	codebuddy: "CodeBuddy",
	kiro: "Kiro CLI",
	crush: "Crush",
	iflow: "iFlow CLI",
	workbuddy: "WorkBuddy",
	zcode: "ZCode",
	dsh: "DSH",
	pi: "Pi Agent",
	lingma: "通义灵码",
	minimax: "MiniMax Code",
};
let modalFocusReturn = null; // 弹窗关闭后焦点归还到此元素
let modalDirty = false; // 设置弹窗有未保存修改
let launchPollTimer = null; // 启动状态轮询定时器
let searchTimer = null; // 搜索 debounce 定时器
let openMoreMenu = null; // 当前打开的 ⋯ 菜单元素（配合 init 中的常驻 document 监听）

/* ================ 基础工具 ================ */

/* 断线遮罩：服务不可达时展示一键重连。初始网络失败只尽力展示一次。 */
let _netdownShown = false;
function showNetDown() {
	const ov = $("#netdown");
	if (!ov) return;
	if (!_netdownShown) {
		_netdownShown = true;
		const u = $("#netdown-url");
		if (u) u.textContent = location.origin || "http://localhost";
	}
	ov.classList.remove("hidden");
}
function isNetError(e) {
	return (
		e instanceof TypeError && /fetch|NetworkError|load/i.test(e.message || "")
	);
}

/* 探活：页面能打开 ≠ 服务可用（可能是个残留旧标签页）。
   只有 GET /api/config 也连不上时，才判定为"服务真不可达"。 */
let _probeBusy = false;
async function probeAlive() {
	if (_probeBusy) return false;
	_probeBusy = true;
	try {
		const c = await fetch("/api/config", { method: "GET" });
		const j = await c.json();
		return !!(c.ok && j && j.ok !== false);
	} catch {
		return false;
	} finally {
		_probeBusy = false;
	}
}

async function api(path, body, raw) {
	const opt = body
		? {
				method: "POST",
				body: raw ? body : JSON.stringify(body),
				headers: raw
					? { "Content-Type": "application/zip" }
					: { "Content-Type": "application/json" },
			}
		: {};
	let resp;
	try {
		resp = await fetch(path, opt);
	} catch (e) {
		// 连接层失败（服务没起来 / 连接被重置 / 连接断开）—— 只可能是服务或传输问题，
		// 统一走"不可达"处理，不给用户一堆看不懂的底层异常。
		if (isNetError(e)) {
			console.error("[star] api 网络失败:", path, e);
			probeAlive().then((alive) => {
				if (!alive) showNetDown();
			});
			const err = new Error(
				"请求失败：本地服务暂时没有响应，请点右下角重试或查看服务是否运行",
			);
			err.isNetwork = true;
			throw err;
		}
		throw e;
	}
	// 星图接口都应返回 JSON；万一拿到 HTML/空页（如残留旧标签页、反向代理吞掉了接口、
	// 或服务正被其他响应顶替），继续 JSON.parse 只会抛 $"Unexpected token '<'"——
	// 这类看不懂的错归一化成明确的"服务异常/请重连"提示。
	const text = await resp.text();
	let j;
	try {
		j = JSON.parse(text);
	} catch (parseErr) {
		console.error("[star] api 非 JSON 响应:", path, text.slice(0, 80));
		probeAlive().then((alive) => {
			if (!alive) showNetDown();
		});
		const err = new Error(
			"本地服务返回了异常内容（可能已重启或不是星图的服务），请点右下角重新连接",
		);
		err.isNetwork = true;
		throw err;
	}
	if (j && j.ok === false && j.error) throw new Error(j.error);
	return j;
}

function toast(msg, isError) {
	const t = $("#toast");
	t.textContent = msg;
	t.classList.toggle("error", !!isError);
	t.classList.remove("hidden");
	clearTimeout(t._timer);
	t._timer = setTimeout(() => t.classList.add("hidden"), 4000);
}

/* 带操作按钮的 toast：用于需要"立即反悔"的场景（如排除项目 → 撤销） */
function toastAct(msg, label, fn) {
	const t = $("#toast");
	// pi-lens-ignore: no-inner-html-js
	t.innerHTML = `${esc(msg)} <button class="toast-act">${esc(label)}</button>`;
	t.classList.remove("error");
	t.classList.remove("hidden");
	const btn = t.querySelector(".toast-act");
	if (btn)
		btn.addEventListener("click", (e) => {
			e.stopPropagation();
			fn();
		});
	clearTimeout(t._timer);
	// pi-lens-ignore: no-inner-html-js
	t._timer = setTimeout(() => {
		t.innerHTML = "";
		t.classList.add("hidden");
	}, 8000);
}

function fmtTime(iso) {
	if (!iso) return "—";
	const d = new Date(iso);
	if (isNaN(d)) return iso;
	const p = (n) => String(n).padStart(2, "0");
	return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function getExpanded() {
	try {
		return new Set(
			JSON.parse(localStorage.getItem("starchart-expanded") || "[]"),
		);
	} catch {
		return new Set();
	}
}
function saveExpanded(set) {
	localStorage.setItem("starchart-expanded", JSON.stringify([...set]));
}
const expanded = getExpanded();

/* 按扫描根折叠：多工作区（roots > 1）时每个根显示分组头，可整体收起。
   搜索时忽略折叠，直接展示命中结果。 */
function getCollapsedRoots() {
	try {
		return new Set(
			JSON.parse(localStorage.getItem("starchart-collapsed-roots") || "[]"),
		);
	} catch {
		return new Set();
	}
}
function saveCollapsedRoots(set) {
	localStorage.setItem("starchart-collapsed-roots", JSON.stringify([...set]));
}
const collapsedRoots = getCollapsedRoots();

function renderRootHeader(c, collapsed) {
	const hd = document.createElement("div");
	hd.className = "root-header" + (collapsed ? " collapsed" : "");
	hd.setAttribute("role", "button");
	hd.tabIndex = 0;
	hd.setAttribute("aria-expanded", String(!collapsed));
	// pi-lens-ignore: no-inner-html-js
	hd.innerHTML =
		`<span class="twisty">${collapsed ? "▶" : "▼"}</span>` +
		`<span class="icon">🗂️</span>` +
		`<span class="root-name">${esc(c.name)}</span>` +
		`<span class="root-path">${esc(c.path)}</span>`;
	hd.addEventListener("click", (e) => {
		e.stopPropagation();
		toggleRoot(c.path);
	});
	hd.addEventListener("keydown", (e) => {
		if (e.key === "Enter" || e.key === " ") {
			e.preventDefault();
			toggleRoot(c.path);
		}
	});
	return hd;
}

function toggleRoot(path) {
	if (collapsedRoots.has(path)) collapsedRoots.delete(path);
	else collapsedRoots.add(path);
	saveCollapsedRoots(collapsedRoots);
	render();
}

/* ================ 树渲染 ================ */

function nodeMatches(node, q) {
	if (!q || !node) return true;
	if (node.name && node.name.toLowerCase().includes(q)) return true;
	if (node.path && node.path.toLowerCase().includes(q)) return true;
	if (node.intro && node.intro.toLowerCase().includes(q)) return true;
	if (node.py && node.py.includes(q)) return true;
	if (node.tags && node.tags.some((t) => String(t).toLowerCase().includes(q)))
		return true;
	if (node.note && node.note.toLowerCase().includes(q)) return true;
	return false;
}

function subtreeMatches(node, q) {
	if (nodeMatches(node, q)) return true;
	return (node.children || []).some((c) => subtreeMatches(c, q));
}

function esc(s) {
	return String(s).replace(
		/[&<>"]/g,
		(c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c],
	);
}

// 搜索命中片段用 <mark> 包裹；q 应为已小写的查询串
function highlight(text, q) {
	const s = String(text || "");
	if (!q) return esc(s);
	const lower = s.toLowerCase();
	let out = "",
		i = 0;
	for (;;) {
		const idx = lower.indexOf(q, i);
		if (idx < 0) {
			out += esc(s.slice(i));
			break;
		}
		out +=
			esc(s.slice(i, idx)) +
			"<mark>" +
			esc(s.slice(idx, idx + q.length)) +
			"</mark>";
		i = idx + q.length;
	}
	return out;
}

/* 搜索时命中节点的祖先需要临时展开，否则命中项被折叠的父级藏住看不见。
   searchAutoExpand 只在渲染期生效，不写回 localStorage（退出搜索自动还原）。 */
let searchAutoExpand = new Set();

function isExpanded(path) {
	return expanded.has(path) || searchAutoExpand.has(path);
}

function computeSearchAutoExpand(tree, q) {
	const set = new Set();
	if (!q || !tree) return set;
	(function walk(node, ancestors) {
		if (!node) return;
		if (nodeMatches(node, q)) ancestors.forEach((p) => set.add(p));
		const next = node.path ? ancestors.concat(node.path) : ancestors;
		for (const c of node.children || []) walk(c, next);
	})(tree, []);
	return set;
}

function iconFor(node) {
	if (node.type === "project") return "🛠";
	return isExpanded(node.path) && node.children && node.children.length
		? "📂"
		: "📁";
}

function renderNode(node, q, _isRoot, depth = 1) {
	const div = document.createElement("div");
	div.className = "node";
	// ARIA tree 规范要求 treeitem 必须是 tree 的直接子节点，中间容器用 role=none 跳过
	div.setAttribute("role", "none");
	const row = document.createElement("div");
	const isProject = node.type === "project";
	row.className =
		"node-row" +
		(isProject ? " project" : "") +
		(node.path === selectedPath ? " selected" : "");
	row.dataset.path = node.path;
	// ARIA: 树项角色 + 展开状态
	row.setAttribute("role", "treeitem");
	row.setAttribute("aria-level", String(depth));
	row.setAttribute("tabindex", node.path === selectedPath ? "0" : "-1");
	row.title = node.path; // 超长名称 hover 可见全路径

	const hasKids = node.children && node.children.length > 0;
	const isCollapsed = hasKids && !isExpanded(node.path);
	if (hasKids) row.setAttribute("aria-expanded", !isCollapsed);

	let html = `<span class="twisty">${hasKids ? (isCollapsed ? "▶" : "▼") : ""}</span>`;
	html += `<span class="icon">${iconFor(node)}</span>`;
	html += `<span class="node-main"><span class="name">${highlight(node.name, q)}</span></span>`;
	if (node.marked === "manual")
		html += `<span class="badge manual">手动</span>`;
	if (node.marked === "off") html += `<span class="badge off">排除</span>`;
	if (node.starred) html += `<span class="star-badge" title="已收藏">★</span>`;
	// 搜索时高亮介绍命中
	if (q && node.intro && node.intro.toLowerCase().includes(q)) {
		html += `<span class="intro-line">${highlight(node.intro, q)}</span>`;
	} else if (!q && isProject && node.intro) {
		html += `<span class="intro-line">${esc(node.intro)}</span>`;
	}
	if (node.fileCount > 0 && node.type === "dir") {
		html += `<span class="filecount">+${node.fileCount} 文件</span>`;
	}
	if (node.git) {
		if (node.git.dirty)
			html += `<span class="git-dirty" title="有未提交的更改">●</span>`;
		if (node.git.branch) {
			html += `<span class="git-branch" title="当前分支：${esc(node.git.branch)}">${esc(node.git.branch)}</span>`;
		}
	}
	if (node.stack && node.stack.length) {
		html += `<span class="stack-tag" title="技术栈：${esc(node.stack.join("、"))}">${esc(node.stack[0])}</span>`;
	}
	// pi-lens-ignore: no-inner-html-js
	row.innerHTML = html;

	row.addEventListener("click", (e) => {
		selectedPath = node.path;
		selectedNode = node;
		if (hasKids) {
			const willExpand = isCollapsed;
			if (e.target.closest(".twisty") || willExpand) {
				if (willExpand) expanded.add(node.path);
				else expanded.delete(node.path);
				saveExpanded(expanded);
			}
		}
		render();
		renderDetail(node);
		row.focus();
	});
	row.addEventListener("dblclick", () => openPath(node.path, "explorer"));
	row.addEventListener("contextmenu", (e) => showRowMenu(e, node));

	// 键盘导航：方向键
	row.addEventListener("keydown", (e) => {
		const rows = [...$("#tree").querySelectorAll(".node-row")];
		const idx = rows.indexOf(row);
		if (e.key === "ArrowDown") {
			e.preventDefault();
			rows[idx + 1]?.focus();
		} else if (e.key === "ArrowUp") {
			e.preventDefault();
			rows[idx - 1]?.focus();
		} else if (e.key === "ArrowRight" && hasKids && isCollapsed) {
			e.preventDefault();
			expanded.add(node.path);
			saveExpanded(expanded);
			render();
			row.focus();
		} else if (e.key === "ArrowLeft" && hasKids && !isCollapsed) {
			e.preventDefault();
			expanded.delete(node.path);
			saveExpanded(expanded);
			render();
			row.focus();
		} else if (e.key === "Home") {
			e.preventDefault();
			rows[0]?.focus();
		} else if (e.key === "End") {
			e.preventDefault();
			rows[rows.length - 1]?.focus();
		} else if (e.key === "Enter") {
			e.preventDefault();
			row.click();
		}
	});

	div.appendChild(row);

	const kids = document.createElement("div");
	kids.className = "node-children" + (isCollapsed ? " collapsed" : "");
	kids.setAttribute("role", "group");
	const visible = (
		q
			? (node.children || []).filter((c) => subtreeMatches(c, q))
			: node.children || []
	).filter((c) => c.marked !== "off"); // 排除的不显示
	for (const c of visible) kids.appendChild(renderNode(c, q, false, depth + 1));
	div.appendChild(kids);
	return div;
}

/* ================ 视图：目录树 / 常用 ================ */

let viewMode = "tree"; // "tree" | "frequent"

// frecency：使用次数为主，越久没用衰减越多（24 小时衰减一半权重）
function frecency(u) {
	const last = u && u.last ? new Date(u.last).getTime() : 0;
	if (!last) return 0;
	const hours = Math.max(0, (Date.now() - last) / 3600000);
	return (u.count || 0) / (1 + hours / 24);
}

let freqSort = (function () {
	try {
		return localStorage.getItem("starchart-freq-sort") || "usage";
	} catch (e) {
		return "usage";
	}
})();
function saveFreqSort(v) {
	try {
		localStorage.setItem("starchart-freq-sort", v);
	} catch (e) {}
}
function relTime(iso) {
	if (!iso) return "";
	const d = new Date(iso);
	if (isNaN(d)) return "";
	const sec = (Date.now() - d.getTime()) / 1000;
	if (sec < 0) return "刚刚";
	if (sec < 3600) return Math.max(1, Math.floor(sec / 60)) + " 分钟前";
	if (sec < 86400) return Math.floor(sec / 3600) + " 小时前";
	if (sec < 86400 * 30) return Math.floor(sec / 86400) + " 天前";
	return fmtTime(iso).slice(0, 10);
}

function renderFrequent() {
	const treeEl = $("#tree");
	// pi-lens-ignore: no-inner-html-js
	treeEl.innerHTML = "";
	$("#empty-state").classList.add("hidden");

	// 工具栏：在「使用频率 / 最近修改」之间切换（记忆选择）
	const bar = document.createElement("div");
	bar.className = "freq-bar";
	const mk = (label, value) => {
		const b = document.createElement("button");
		b.className = "seg" + (freqSort === value ? " on" : "");
		b.textContent = label;
		b.addEventListener("click", () => {
			if (freqSort === value) return;
			freqSort = value;
			saveFreqSort(value);
			render();
		});
		return b;
	};
	bar.appendChild(mk("★ 使用频率", "usage"));
	bar.appendChild(mk("🕒 最近修改", "mtime"));
	treeEl.appendChild(bar);

	const usage = data.usage || {};
	const items = [];
	(function walk(n) {
		if (!n) return;
		if (n.type === "project" && n.marked !== "off") {
			if (freqSort === "usage" ? usage[n.path] : true) items.push(n);
		}
		for (const c of n.children || []) walk(c);
	})(data.tree);

	if (!items.length) {
		treeEl.innerHTML =
			freqSort === "usage"
				? `<div class="no-results">还没有使用记录。<br>` +
				  `用星图打开或启动几个项目后，这里会按使用频率排出最常用的项目。</div>`
				: `<div class="no-results">没有可展示的项目</div>`;
		return;
	}

	// 收藏永远在前，其余按所选模式排序
	items.sort((a, b) => {
		const sa = a.starred ? 1 : 0,
			sb = b.starred ? 1 : 0;
		if (sa !== sb) return sb - sa;
		if (freqSort === "mtime") {
			const ba = b.mtime || "",
				aa = a.mtime || "";
			if (ba !== aa) return ba > aa ? -1 : 1; // 较新的在前
		}
		return frecency(usage[b.path]) - frecency(usage[a.path]);
	});
	for (const n of items.slice(0, 50)) {
		const u = usage[n.path] || {};
		const row = document.createElement("div");
		row.className = "node-row project" + (n.path === selectedPath ? " selected" : "");
		row.dataset.path = n.path;
		row.setAttribute("role", "treeitem");
		row.setAttribute("tabindex", "-1");
		row.title = n.path;
		const mark =
			freqSort === "mtime"
				? `<span class="filecount" title="最近修改">${esc(relTime(n.mtime))}</span>`
				: `<span class="filecount" title="使用次数">${u.count || 0} 次</span>`;
		// pi-lens-ignore: no-inner-html-js
		row.innerHTML =
			`<span class="twisty"></span>` +
			`<span class="icon">🛠</span>` +
			`<span class="node-main"><span class="name">${esc(n.name)}</span></span>` +
			(n.intro ? `<span class="intro-line">${esc(n.intro)}</span>` : "") +
			mark;
		row.addEventListener("click", () => {
			selectedPath = n.path;
			selectedNode = n;
			render();
			renderDetail(n);
		});
		row.addEventListener("dblclick", () => openPath(n.path, "explorer"));
		row.addEventListener("contextmenu", (e) => showRowMenu(e, n));
		treeEl.appendChild(row);
	}
}

/* URL 深链：把当前选中/视图/搜索写进 location.hash，刷新或分享后按原状态恢复。
   replaceState 只改地址、不入浏览历史，避免每次输入都塞满前进/后退。 */
function syncHash() {
	const parts = [];
	if (selectedPath) parts.push("path=" + encodeURIComponent(selectedPath));
	if (viewMode === "frequent") parts.push("view=frequent");
	if (searchQuery.trim())
		parts.push("q=" + encodeURIComponent(searchQuery.trim()));
	const h = "#" + parts.join("&");
	try {
		if (location.hash !== h) history.replaceState(null, "", h);
	} catch (e) {}
}

/* 从 URL hash 还原视图/搜索/选中。selectedNode 需在数据就绪后解析，这里只还原 raw 状态。 */
function applyHash() {
	try {
		const h = location.hash.slice(1);
		if (!h) return;
		const p = new URLSearchParams(h);
		const q = p.get("q");
		if (q) {
			searchQuery = q;
			const si = $("#search");
			if (si) si.value = q;
			const cb = $("#search-clear");
			if (cb) cb.style.display = "block";
		}
		if (p.get("view") === "frequent") viewMode = "frequent";
		const path = p.get("path");
		if (path) {
			selectedPath = path;
			selectedNode = null;
		}
	} catch (e) {}
}

function render() {
	renderTree();
	syncHash();
	updateExcludedCount();
}

function renderTree() {
	const treeEl = $("#tree");
	// pi-lens-ignore: no-inner-html-js
	treeEl.innerHTML = "";
	const q = searchQuery.trim().toLowerCase();
	searchAutoExpand = new Set();
	const empty = $("#empty-state");

	// 搜索时一律回到树视图：常用列表本身就是结果集，再过滤没有意义
	if (viewMode === "frequent" && !q) {
		renderFrequent();
		updateGenButton();
		return;
	}
	if (!data.tree || !data.tree.children || !data.tree.children.length) {
		empty.classList.remove("hidden");
		return;
	}
	empty.classList.add("hidden");

	searchAutoExpand = computeSearchAutoExpand(data.tree, q);
	const roots = (
		q
			? data.tree.children.filter((c) => subtreeMatches(c, q))
			: data.tree.children
	).filter((c) => c.marked !== "off"); // 排除的不显示
	if (q && roots.length === 0) {
		// pi-lens-ignore: no-inner-html-js
		treeEl.innerHTML =
			`<div class="no-results">没有匹配「${esc(searchQuery)}」的项目<br>` +
			`<button id="clear-search" class="link-btn">清除搜索</button></div>`;
		const clearBtn = $("#clear-search");
		if (clearBtn)
			clearBtn.addEventListener("click", () => {
				searchQuery = "";
				$("#search").value = "";
				$("#search-clear").style.display = "none";
				render();
				$("#search").focus();
			});
		return;
	}
	const groupRoots = !q && roots.length > 1; // 多工作区时显示按根分组；搜索时直接平铺结果
	for (const c of roots) {
		if (groupRoots) {
			const folded = collapsedRoots.has(c.path);
			treeEl.appendChild(renderRootHeader(c, folded));
			if (folded) continue; // 整根收起，不渲染该根的子树
		}
		treeEl.appendChild(renderNode(c, q, true));
	}

	// 搜索后选中行滚动到可视区
	if (selectedPath) {
		const sel = treeEl.querySelector(".node-row.selected");
		if (sel) sel.scrollIntoView({ block: "nearest" });
	}
	updateGenButton();
}

function collectPending(node, out) {
	if (!node) return;
	if (node.marked === "off") return; // 排除的整棵子树不参与
	if (node.type === "project") {
		// 与后端 run_scan 判定一致：只统计"还没有任何介绍"的项目。
		// 生成成功（非空介绍）即不再计入；不把"静态介绍待 AI 升级 / 内容变化"
		// 计入——否则这类项目在 AI 升级失败时会反复挂着"(1)"，出现
		// "提示全部完成却仍显示还有 N 个待生成"的假象。
		if (!node.intro) out.push(node.path);
	}
	for (const c of node.children || []) collectPending(c, out);
}

function updateGenButton() {
	const pending = [];
	collectPending(data.tree, pending);
	$("#btn-gen").classList.toggle("hidden", pending.length === 0 || generating);
	$("#gen-count").textContent = pending.length ? `(${pending.length})` : "";
}

/* ================ 详情面板（操作卡重构） ================ */

function closeOpenMoreMenu() {
	if (openMoreMenu) {
		openMoreMenu.classList.add("hidden");
		openMoreMenu = null;
	}
}

function renderDetail(node) {
	stopLaunchPoll();
	// 切换项目时收起上一个详情的 ⋯ 菜单（旧元素随 innerHTML 重建被丢弃）
	openMoreMenu = null;
	const d = $("#detail");
	if (!node) {
		d.classList.add("hidden");
		return;
	}
	d.classList.remove("hidden");
	// pi-lens-ignore: no-inner-html-js
	d.innerHTML = "";

	// ---- 项目名 + 介绍（紧凑） ----
	const h = document.createElement("h2");
	// pi-lens-ignore: no-inner-html-js
	h.innerHTML = `${node.type === "project" ? "🛠" : "📁"} ${highlight(node.name, searchQuery.trim().toLowerCase())}`;
	d.appendChild(h);

	const meta = document.createElement("div");
	meta.className = "meta";
	// pi-lens-ignore: no-inner-html-js
	meta.innerHTML = `
    路径：<code title="点击复制">${esc(node.path)}</code><br>
    修改：${fmtTime(node.mtime)}${node.fileCount ? `　·　${node.fileCount} 个文件` : ""}${
			node.introSource
				? `　·　${{ ai: "AI 生成", static: "README 提取", manual: "手动编辑" }[node.introSource] || node.introSource}`
				: ""
		}`;
	d.appendChild(meta);

	// ---- Git 状态（展示属性：拿不到就整块不显示，不影响项目本身） ----
	if (node.git) {
		const g = node.git;
		const git = document.createElement("div");
		git.className = "git-box";
		const bits = [];
		if (g.branch) bits.push(`<b>${esc(g.branch)}</b>`);
		if (g.ahead) bits.push(`↑ ${g.ahead} 未推送`);
		if (g.behind) bits.push(`↓ ${g.behind} 未拉取`);
		bits.push(
			g.dirty
				? `<span class="dirty">● 有未提交更改</span>`
				: `<span class="clean">✓ 工作区干净</span>`,
		);
		if (g.lastCommit) bits.push(`最后提交 ${fmtTime(g.lastCommit)}`);
		// pi-lens-ignore: no-inner-html-js
		git.innerHTML = bits.join("　·　");
		d.appendChild(git);
	}

	// ---- 技术栈 ----
	if (node.stack && node.stack.length) {
		const sw = document.createElement("div");
		sw.className = "tags-row";
		for (const s of node.stack) {
			const pill = document.createElement("span");
			pill.className = "tag-pill stack-pill";
			pill.textContent = s;
			sw.appendChild(pill);
		}
		d.appendChild(sw);
	}

	// ---- 标签与备注 ----
	if (node.type === "project") {
		const tagsWrap = document.createElement("div");
		tagsWrap.className = "tags-row";
		for (const t of node.tags || []) {
			const pill = document.createElement("span");
			pill.className = "tag-pill";
			pill.textContent = t;
			tagsWrap.appendChild(pill);
		}
		if (node.note) {
			const nt = document.createElement("div");
			nt.className = "note-line";
			nt.textContent = node.note;
			tagsWrap.appendChild(nt);
		}
		const editBtn = document.createElement("button");
		editBtn.className = "link-btn";
		editBtn.textContent =
			(node.tags && node.tags.length) || node.note
				? "✎ 编辑标签 / 备注"
				: "🏷 添加标签 / 备注";
		editBtn.addEventListener("click", () => editMeta(node));
		tagsWrap.appendChild(editBtn);
		d.appendChild(tagsWrap);
	}

	// ---- 介绍区 ----
	if (node.type === "project") {
		const box = document.createElement("div");
		box.className = "intro-box" + (node.intro ? "" : " empty");
		box.textContent =
			node.intro || "暂无介绍 —— 点击编辑，或用顶栏「生成介绍」";
		box.title = "点击编辑介绍";
		box.addEventListener("click", () => editIntro(node));
		d.appendChild(box);
	}

	// ---- 3 张操作卡 ----
	const cards = document.createElement("div");
	cards.className = "action-cards";
	d.appendChild(cards);

	// 卡 1: 启动
	const launchers = node.launchers || [];
	const launchCard = document.createElement("div");
	launchCard.className = "action-card" + (launchers.length ? "" : " disabled");
	if (launchers.length === 1) {
		const l = launchers[0];
		// pi-lens-ignore: no-inner-html-js
		launchCard.innerHTML = `<div class="card-icon">▶</div><div class="card-label">${esc(l.label)}</div><div class="card-sub">本地启动</div>`;
		launchCard.title = "在新终端启动";
		launchCard.addEventListener("click", () => doLaunch(node, l));
	} else if (launchers.length > 1) {
		// pi-lens-ignore: no-inner-html-js
		launchCard.innerHTML = `<div class="card-icon">▶</div><div class="card-label">启动项目</div><div class="card-sub">${launchers.length} 个入口</div>`;
		launchCard.title = "选择启动入口";
		launchCard.addEventListener("click", () =>
			showLaunchMenu(launchCard, node, launchers),
		);
	} else {
		// pi-lens-ignore: no-inner-html-js
		launchCard.innerHTML = `<div class="card-icon">▶</div><div class="card-label">无启动入口</div><div class="card-sub">未检测到</div>`;
	}
	cards.appendChild(launchCard);

	// 卡 2: 打开方式（编辑器 + Agent 合并，副标题写清动作与来源）
	// 自适应：项目根目录有某工具证据（CLAUDE.md/.cursor 等）→ 只列这些工具的本机安装；
	// 无证据 → 列本机全部已装。项目期待但本机没装的工具单独提示。
	const hints = node.agentHints || [];
	const specificHints = hints.filter((h) => h !== "generic");
	const useAll = !specificHints.length;
	const edList = useAll
		? detectedEditors
		: detectedEditors.filter((e) => specificHints.includes(e.id));
	const agList = useAll
		? detectedAgents
		: detectedAgents.filter((a) => specificHints.includes(a.id));
	const ways = [];
	edList.forEach((ed) =>
		ways.push({
			icon: "✎",
			mode: "editor",
			editor: ed,
			agent: null,
			name: ed.name,
			action: `用 ${ed.name} 打开`,
			src: useAll ? "all" : "hint",
		}),
	);
	agList.forEach((ag) =>
		ways.push({
			icon: "🤖",
			mode: "agent",
			editor: null,
			agent: ag,
			name: ag.name,
			action: `在终端启动 ${ag.name}`,
			src: useAll ? "all" : "hint",
		}),
	);
	const detectedIds = new Set([
		...detectedEditors.map((e) => e.id),
		...detectedAgents.map((a) => a.id),
	]);
	const missing = specificHints
		.filter((h) => !detectedIds.has(h))
		.map((h) => TOOL_NAMES[h] || h);
	const openCard = document.createElement("div");
	openCard.className = "action-card" + (ways.length ? "" : " disabled");
	let cardSub, cardLabel;
	// 单项：标签直接写动作（「用 Trae 打开」），副标题写来源；多项：标签写分类
	if (ways.length === 1) {
		cardLabel = ways[0].action;
		cardSub = specificHints.length ? "项目配置匹配" : "本机已装";
	} else if (ways.length > 1) {
		cardLabel = "编辑器 / AI";
		cardSub = specificHints.length
			? `${ways.length} 项 · 项目配置匹配`
			: `${ways.length} 项 · 本机已装`;
	} else {
		cardLabel = "无可用工具";
		cardSub = missing.length
			? `缺 ${missing.length} 个（未安装）`
			: "未检测到编辑器 / Agent";
	}
	// pi-lens-ignore: no-inner-html-js
	openCard.innerHTML =
		`<div class="card-icon">${ways.length === 1 ? ways[0].icon : "⇱"}</div>` +
		`<div class="card-label">${esc(cardLabel)}</div><div class="card-sub">${esc(cardSub)}</div>`;
	openCard.title = missing.length
		? "项目期待但未安装：" + missing.join("、")
		: ways.length === 1
			? ways[0].action
			: "选择用哪个工具打开 / 启动";
	if (ways.length === 1) {
		const w = ways[0];
		openCard.addEventListener("click", () =>
			openPath(node.path, w.mode, w.editor, w.agent),
		);
	} else if (ways.length > 1) {
		openCard.addEventListener("click", () =>
			showOpenMenu(openCard, node, ways, missing),
		);
	}
	cards.appendChild(openCard);

	// ---- 次级操作菜单（⋯） ----
	const moreWrap = document.createElement("div");
	moreWrap.className = "more-menu-wrap";
	moreWrap.style.marginBottom = "16px";
	const moreBtn = document.createElement("button");
	moreBtn.textContent = "⋯ 更多操作";
	moreBtn.title = "资源管理器 / 复制路径 / 终端 / 编辑介绍 / 排除项目";
	moreWrap.appendChild(moreBtn);
	const menu = document.createElement("div");
	menu.className = "more-menu hidden";
	moreWrap.appendChild(menu);

	const mi = (label, fn) => {
		const b = document.createElement("button");
		b.textContent = label;
		b.addEventListener("click", (ev) => {
			ev.stopPropagation();
			closeOpenMoreMenu();
			fn();
		});
		menu.appendChild(b);
	};
	mi("📂 资源管理器", () => {
		const now = Date.now();
		if (now - (lastExplorer[node.path] || 0) < 1500) {
			toast("资源管理器刚已打开");
			return;
		}
		lastExplorer[node.path] = now;
		openPath(node.path, "explorer");
	});
	mi("⧉ 复制路径", () => {
		navigator.clipboard
			.writeText(node.path)
			.then(() => toast("已复制路径"))
			.catch(() => toast("复制失败", true));
	});
	mi("▶ 终端", () => openPath(node.path, "terminal"));
	mi("✏ 编辑介绍", () => editIntro(node));
	mi(node.starred ? "☆ 取消收藏" : "★ 收藏", () => doStar(node, !node.starred));
	mi("🏷 标签 / 备注", () => editMeta(node));
	if (node.type === "project") {
		mi("✖ 排除此项目", () => confirmExclude(node));
	} else {
		mi("📌 标为项目", () => doMark(node, "manual"));
	}
	mi("🗑 已排除的项目…", openExcluded);

	// ⋯ 按钮：开/关由模块级 openMoreMenu 状态与 init 中的常驻 document 监听协同处理
	// （⋯ 按钮 stopPropagation，打开的那次点击不会冒泡到 document 误触关闭）
	moreBtn.addEventListener("click", (e) => {
		e.stopPropagation();
		if (openMoreMenu === menu) {
			closeOpenMoreMenu();
		} else {
			closeOpenMoreMenu();
			menu.classList.remove("hidden");
			openMoreMenu = menu;
		}
	});

	d.appendChild(moreWrap);

	// ---- 运行状态区 ----
	if (node.type === "project") {
		const runDiv = document.createElement("div");
		runDiv.id = "run-state-area";
		d.appendChild(runDiv);
		refreshLaunch(node, runDiv);
	}

	// ---- 项目内查找文件 ----
	if (node.type === "project") {
		d.appendChild(sectionTitle("查找文件"));
		const fsBox = document.createElement("div");
		fsBox.className = "filesearch";
		const input = document.createElement("input");
		input.type = "text";
		input.className = "file-search-input";
		input.placeholder = "按文件名查找（如 app.py）";
		const list = document.createElement("div");
		list.className = "file-list";
		fsBox.appendChild(input);
		fsBox.appendChild(list);

		let ftimer = null;
		input.addEventListener("input", () => {
			clearTimeout(ftimer);
			const q = input.value.trim();
			// pi-lens-ignore: no-inner-html-js
			if (!q) {
				list.innerHTML = "";
				return;
			}
			ftimer = setTimeout(async () => {
				// pi-lens-ignore: no-inner-html-js
				list.innerHTML = `<span class="hint-line">搜索中…</span>`;
				try {
					const r = await api(
						`/api/files?path=${encodeURIComponent(node.path)}` +
							`&q=${encodeURIComponent(q)}`,
					);
					// pi-lens-ignore: no-inner-html-js
					list.innerHTML = "";
					if (!r.files.length) {
						// pi-lens-ignore: no-inner-html-js
						list.innerHTML = `<span class="hint-line">没有匹配的文件</span>`;
						return;
					}
					for (const rel of r.files) {
						const row = document.createElement("div");
						row.className = "file-row";
						const nm = document.createElement("span");
						nm.className = "file-path";
						nm.textContent = rel;
						nm.title = "点击在资源管理器中定位该文件所在目录";
						nm.addEventListener("click", () => {
							// 资源管理器只能定位目录，截取该文件所在的文件夹
							const dir = /[\\/]/.test(rel)
								? node.path + "\\" + rel.replace(/[\\/][^\\/]*$/, "")
								: node.path;
							openPath(dir, "explorer");
						});
						const cp = document.createElement("button");
						cp.className = "link-btn";
						cp.textContent = "复制路径";
						cp.addEventListener("click", () => {
							navigator.clipboard
								.writeText(node.path + "\\" + rel)
								.then(() => toast("已复制路径"))
								.catch(() => toast("复制失败", true));
						});
						row.appendChild(nm);
						row.appendChild(cp);
						list.appendChild(row);
					}
				} catch (e) {
					// pi-lens-ignore: no-inner-html-js
					list.innerHTML = `<span class="hint-line">搜索失败：${esc(e.message)}</span>`;
				}
			}, 250);
		});
		d.appendChild(fsBox);
	}

	// ---- 使用说明 ----
	if (node.type === "project") {
		d.appendChild(sectionTitle("使用说明"));
		const docBtn = document.createElement("button");
		docBtn.textContent = "📖 查看使用说明";
		const docBox = document.createElement("div");
		docBox.className = "doc-box hidden";
		docBtn.addEventListener("click", async () => {
			if (!docBox.classList.contains("hidden")) {
				docBox.classList.add("hidden");
				docBtn.textContent = "📖 查看使用说明";
				return;
			}
			docBtn.textContent = "📖 收起说明";
			docBox.classList.remove("hidden");
			if (!docCache[node.path]) {
				// pi-lens-ignore: no-inner-html-js
				docBox.innerHTML = `<span class="hint-line">加载中…</span>`;
				try {
					const r = await api("/api/doc?path=" + encodeURIComponent(node.path));
					docCache[node.path] = r.content
						? { file: r.file, html: mdToHtml(r.content) }
						: {
								file: "",
								html: `<span class="hint-line">未找到说明文档</span>`,
							};
				} catch (e) {
					docCache[node.path] = {
						file: "",
						html: `<span class="hint-line">读取失败：${esc(e.message)}</span>`,
					};
				}
			}
			const c = docCache[node.path];
			// pi-lens-ignore: no-inner-html-js
			docBox.innerHTML =
				(c.file ? `<div class="doc-file">来源：${esc(c.file)}</div>` : "") +
				c.html;
		});
		d.appendChild(docBtn);
		d.appendChild(docBox);
	}
}

function sectionTitle(text) {
	const t = document.createElement("div");
	t.className = "section-title";
	t.textContent = text;
	return t;
}

/* ---- 下拉菜单（启动/编辑器/Agent 多选） ---- */

function showLaunchMenu(anchor, node, launchers) {
	showMenu(
		anchor,
		launchers.map((l) => ({
			label: "▶ " + l.label,
			fn: () => doLaunch(node, l),
		})),
	);
}
function showOpenMenu(anchor, node, ways, missing) {
	const items = [];
	// 项目配置有证据的工具放前面（"推荐"），其余本机已装工具放后面
	const hint = ways.filter((w) => w.src === "hint");
	const rest = ways.filter((w) => w.src !== "hint");
	if (hint.length) {
		items.push({ header: "项目配置匹配" });
		hint.forEach((w) =>
			items.push({
				label: `${w.icon} ${w.action}`,
				fn: () => openPath(node.path, w.mode, w.editor, w.agent),
			}),
		);
	}
	if (rest.length) {
		items.push({ header: "本机已装" });
		rest.forEach((w) =>
			items.push({
				label: `${w.icon} ${w.action}`,
				fn: () => openPath(node.path, w.mode, w.editor, w.agent),
			}),
		);
	}
	if (missing && missing.length) {
		items.push({ header: "项目期待·未安装" });
		missing.forEach((name) =>
			items.push({ note: `${name}（未检测到，请先安装）` }),
		);
	}
	showMenu(anchor, items);
}
function showMenu(anchor, items) {
	// 移除已有菜单
	document.querySelectorAll(".more-menu.popup").forEach((m) => m.remove());
	const menu = document.createElement("div");
	menu.className = "more-menu popup";
	for (const it of items) {
		if (it.header) {
			const hd = document.createElement("div");
			hd.className = "popup-header";
			hd.textContent = it.header;
			menu.appendChild(hd);
			continue;
		}
		if (it.note) {
			const nt = document.createElement("div");
			nt.className = "popup-note";
			nt.textContent = it.note;
			menu.appendChild(nt);
			continue;
		}
		const b = document.createElement("button");
		b.textContent = it.label;
		b.addEventListener("click", () => {
			menu.remove();
			it.fn();
		});
		menu.appendChild(b);
	}
	anchor.appendChild(menu);
	menu.classList.remove("hidden");
	setTimeout(() => {
		document.addEventListener(
			"click",
			function close() {
				menu.remove();
				document.removeEventListener("click", close);
			},
			{ once: true },
		);
	}, 0);
}

/* ---- 树行右键菜单 ---- */
function closeRowMenu() {
	document.querySelectorAll(".ctx-menu").forEach((m) => m.remove());
}

function showRowMenu(e, node) {
	e.preventDefault();
	e.stopPropagation();
	// 与单击一致：先选中该节点并刷新详情
	selectedPath = node.path;
	selectedNode = node;
	render();
	renderDetail(node);

	closeRowMenu();
	const menu = document.createElement("div");
	menu.className = "ctx-menu";
	const sep = () => {
		const s = document.createElement("div");
		s.className = "ctx-sep";
		menu.appendChild(s);
	};
	const add = (label, fn, common) => {
		const b = document.createElement("button");
		b.textContent = label;
		if (common) b.classList.add("common");
		b.addEventListener("click", () => {
			closeRowMenu();
			fn();
		});
		menu.appendChild(b);
	};

	// 常用三件置顶突出
	add(
		"📂 资源管理器",
		() => {
			const now = Date.now();
			if (now - (lastExplorer[node.path] || 0) < 1500) {
				toast("资源管理器刚已打开");
				return;
			}
			lastExplorer[node.path] = now;
			openPath(node.path, "explorer");
		},
		true,
	);
	add(
		"⧉ 复制路径",
		() => {
			navigator.clipboard
				.writeText(node.path)
				.then(() => toast("已复制路径"))
				.catch(() => toast("复制失败", true));
		},
		true,
	);
	add("▶ 终端", () => openPath(node.path, "terminal"), true);

	sep();
	add("✏ 编辑介绍", () => editIntro(node));
	add(node.starred ? "☆ 取消收藏" : "★ 收藏", () =>
		doStar(node, !node.starred),
	);
	add("🏷 标签 / 备注", () => editMeta(node));
	if (node.type === "project") {
		add("✖ 排除此项目", () => confirmExclude(node));
	} else {
		add("📌 标为项目", () => doMark(node, "manual"));
	}
	sep();
	add("🗑 已排除的项目…", openExcluded);

	document.body.appendChild(menu);
	const pad = 10;
	const rect = menu.getBoundingClientRect();
	let x = e.clientX,
		y = e.clientY;
	if (x + rect.width + pad > window.innerWidth)
		x = window.innerWidth - rect.width - pad;
	if (y + rect.height + pad > window.innerHeight)
		y = window.innerHeight - rect.height - pad;
	if (x < pad) x = pad;
	if (y < pad) y = pad;
	menu.style.left = x + "px";
	menu.style.top = y + "px";
	menu.classList.remove("hidden");
}

/* ---- 启动与运行状态 ---- */

async function doLaunch(node, launcher) {
	try {
		const r = await api("/api/launch", { path: node.path, launcher });
		if (r.ok) toast(`已启动：${r.cmd}（PID ${r.pid}）`);
	} catch (e) {
		toast(e.message, true);
	}
	// 立即刷新运行状态 + 启动轮询
	const runDiv = $("#run-state-area");
	if (runDiv) {
		await refreshLaunch(node, runDiv);
		startLaunchPoll(node);
	}
}

async function refreshLaunch(node, container) {
	if (!container) return;
	container.querySelectorAll(".run-state").forEach((e) => e.remove());
	try {
		const r = await api("/api/running");
		const mine = (r.running || []).filter((x) => x.path === node.path);
		if (!mine.length) return;
		for (const m of mine) {
			const st = document.createElement("div");
			st.className = "run-state";
			// pi-lens-ignore: no-inner-html-js
			st.innerHTML = `<span class="run-dot">● 运行中</span> <span class="pid">PID ${m.pid}</span>`;
			const stop = document.createElement("button");
			stop.textContent = "■ 停止";
			stop.className = "danger";
			stop.addEventListener("click", async () => {
				try {
					await api("/api/stop", { path: node.path });
					toast("已停止");
				} catch (e) {
					toast(e.message, true);
				}
				await refreshLaunch(node, container);
			});
			st.appendChild(stop);
			container.appendChild(st);
		}
	} catch {
		/* 服务重启后状态清零，忽略 */
	}
}

function startLaunchPoll(node) {
	stopLaunchPoll();
	launchPollTimer = setInterval(async () => {
		const runDiv = $("#run-state-area");
		if (!runDiv || !document.body.contains(runDiv)) {
			stopLaunchPoll();
			return;
		}
		await refreshLaunch(node, runDiv);
	}, 4000);
}
function stopLaunchPoll() {
	if (launchPollTimer) {
		clearInterval(launchPollTimer);
		launchPollTimer = null;
	}
}

/* ---- Markdown 渲染 ---- */

function mdToHtml(md) {
	const lines = String(md).replace(/\r/g, "").split("\n");
	let html = "",
		inCode = 0,
		inQuote = false,
		inUl = false,
		inOl = false,
		i = 0;
	const closeLists = () => {
		if (inUl) {
			html += "</ul>";
			inUl = false;
		}
		if (inOl) {
			html += "</ol>";
			inOl = false;
		}
	};
	const closeQuote = () => {
		if (inQuote) {
			html += "</blockquote>";
			inQuote = false;
		}
	};
	const inline = (s) =>
		esc(s)
			.replace(/!\[[^\]]*\]\([^)]*\)/g, "")
			.replace(/\[([^\]]*)\]\(([^)]*)\)/g, "$1")
			.replace(/`([^`]+)`/g, "<code>$1</code>")
			.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");

	const sepRe = /^\s*\|?[\s\-:|]+\|?\s*$/;
	const isTableSep = (s) => sepRe.test(s) && s.includes("-");

	while (i < lines.length) {
		const line = lines[i];

		if (line.trim().startsWith("```")) {
			closeLists();
			closeQuote();
			html += inCode ? "</pre>" : "<pre>";
			inCode ^= 1;
			i++;
			continue;
		}
		if (inCode) {
			html += esc(line) + "\n";
			i++;
			continue;
		}

		// 表格：当前行含 | 且下一行是 --- 分隔线
		if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
			closeLists();
			closeQuote();
			const rows = [];
			let j = i;
			while (j < lines.length && lines[j].includes("|")) {
				if (j !== i && isTableSep(lines[j])) {
					j++;
					continue;
				}
				rows.push(lines[j]);
				j++;
			}
			const cells = (r) =>
				r
					.trim()
					.replace(/^\|/, "")
					.replace(/\|$/, "")
					.split("|")
					.map((c) => c.trim());
			html +=
				"<table><thead><tr>" +
				cells(rows[0]).map((c) => `<th>${inline(c)}</th>`).join("") +
				"</tr></thead>";
			if (rows.length > 1) {
				html += "<tbody>";
				for (let r = 1; r < rows.length; r++)
					html +=
						"<tr>" +
						cells(rows[r]).map((c) => `<td>${inline(c)}</td>`).join("") +
						"</tr>";
				html += "</tbody>";
			}
			html += "</table>";
			i = j;
			continue;
		}

		// 引用
		const q = line.match(/^\s*>\s?(.*)/);
		if (q) {
			closeLists();
			if (!inQuote) {
				html += "<blockquote>";
				inQuote = true;
			}
			html += `<p>${inline(q[1])}</p>`;
			i++;
			continue;
		}
		closeQuote();

		if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
			closeLists();
			html += "<hr>";
			i++;
			continue;
		}

		const h = line.match(/^(#{1,4})\s+(.*)/);
		if (h) {
			closeLists();
			html += `<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`;
			i++;
			continue;
		}

		const ul = line.match(/^\s*[-*+]\s+(.*)/);
		const ol = line.match(/^\s*\d+\.\s+(.*)/);
		if (ul) {
			if (inOl) {
				html += "</ol>";
				inOl = false;
			}
			if (!inUl) {
				html += "<ul>";
				inUl = true;
			}
			html += `<li>${inline(ul[1])}</li>`;
			i++;
			continue;
		}
		if (ol) {
			if (inUl) {
				html += "</ul>";
				inUl = false;
			}
			if (!inOl) {
				html += "<ol>";
				inOl = true;
			}
			html += `<li>${inline(ol[1])}</li>`;
			i++;
			continue;
		}
		closeLists();
		if (!line.trim()) {
			i++;
			continue;
		}
		html += `<p>${inline(line)}</p>`;
		i++;
	}
	if (inCode) html += "</pre>";
	if (inQuote) html += "</blockquote>";
	if (inUl) html += "</ul>";
	if (inOl) html += "</ol>";
	return html;
}

/* ---- 介绍编辑 ---- */

function editIntro(node) {
	const box = $("#detail .intro-box");
	if (!box || box.querySelector("textarea")) return;
	box.classList.remove("empty");
	// pi-lens-ignore: no-inner-html-js
	box.innerHTML = "";
	const ta = document.createElement("textarea");
	ta.value = node.intro || "";
	ta.placeholder = "一句话介绍这个项目（30 字内最佳）";
	box.appendChild(ta);
	const acts = document.createElement("div");
	acts.className = "edit-actions";
	const save = document.createElement("button");
	save.className = "primary";
	save.textContent = "保存中…";
	save.disabled = true;
	// 启用保存按钮（textarea 有内容时）
	ta.addEventListener("input", () => {
		save.disabled = false;
		save.textContent = "保存";
	});
	save.addEventListener("click", async () => {
		save.disabled = true;
		save.textContent = "保存中…";
		try {
			const r = await api("/api/intro/manual", {
				path: node.path,
				text: ta.value,
			});
			Object.assign(node, r.node);
			toast("介绍已保存");
			render();
			renderDetail(node);
		} catch (e) {
			toast("保存失败：" + e.message, true);
			save.disabled = false;
			save.textContent = "保存";
		}
	});
	const cancel = document.createElement("button");
	cancel.textContent = "取消";
	cancel.addEventListener("click", () => renderDetail(node));
	acts.appendChild(save);
	acts.appendChild(cancel);
	box.appendChild(acts);
	ta.focus();
	save.disabled = false;
	save.textContent = "保存";
}

/* ---- 收藏 / 标签 / 备注 ---- */

async function doStar(node, starred) {
	try {
		const r = await api("/api/meta", { path: node.path, starred });
		Object.assign(node, r.node);
		toast(starred ? "已收藏" : "已取消收藏");
		render();
		renderDetail(node);
	} catch (e) {
		toast(e.message, true);
	}
}

function editMeta(node) {
	const m = openModal(`
    <h3>🏷 标签与备注</h3>
    <div class="field"><label>标签（逗号分隔，最多 10 个）</label>
      <input type="text" id="meta-tags" value="${esc((node.tags || []).join(", "))}"
             placeholder="如：工作, 前端, 待重构">
    </div>
    <div class="field"><label>备注（最多 500 字）</label>
      <textarea id="meta-note" rows="4"
                placeholder="随手记下这个项目的状态、注意事项…">${esc(node.note || "")}</textarea>
    </div>
    <div class="field">
      <label><input type="checkbox" id="meta-star" ${node.starred ? "checked" : ""}>
        ★ 收藏（在「常用」视图里置顶）</label>
    </div>
    <div class="hint">标签与备注都会被搜索到。</div>
    <div class="footer">
      <button id="meta-cancel">取消</button>
      <button id="meta-save" class="primary">保存</button>
    </div>
  `);
	m.querySelector("#meta-cancel").addEventListener("click", closeModal);
	m.querySelector("#meta-save").addEventListener("click", async () => {
		const tags = m
			.querySelector("#meta-tags")
			.value.split(/[,，]/)
			.map((s) => s.trim())
			.filter(Boolean);
		try {
			const r = await api("/api/meta", {
				path: node.path,
				tags,
				note: m.querySelector("#meta-note").value.trim(),
				starred: m.querySelector("#meta-star").checked,
			});
			Object.assign(node, r.node);
			toast("已保存");
			closeModal();
			render();
			renderDetail(node);
		} catch (e) {
			toast("保存失败：" + e.message, true);
		}
	});
}

/* ---- 排除/标记 ---- */

async function confirmExclude(node) {
	if (
		!confirm(
			`确定排除「${node.name}」？\n排除后不再作为项目显示；可点下面提示里的「撤销」，或在 设置 → 已排除的项目 中恢复。`,
		)
	)
		return;
	await doMark(node, "off");
}
async function undoExclude(path) {
	try {
		const r = await api("/api/mark", { path, mark: "auto" });
		const suffix = r.needRescan ? "（重新扫描后完全生效）" : "";
		toast("已撤销排除" + suffix);
		await reloadTree();
	} catch (e) {
		toast(e.message, true);
	}
}
async function doMark(node, mark) {
	try {
		const r = await api("/api/mark", { path: node.path, mark });
		// 节点是"项目"还是"普通目录"由扫描判定，标记变更需重新扫描才完全生效
		const suffix = r.needRescan ? "（重新扫描后完全生效）" : "";
		if (mark === "off") {
			// 排除后项目立即从树里消失，给一个"撤销"入口便于反悔
			toastAct(`已排除「${node.name}」`, "撤销", () => undoExclude(node.path));
		} else {
			toast(`已标记为项目${suffix}`);
		}
		await reloadTree();
	} catch (e) {
		toast(e.message, true);
	}
}

/* ================ 动作 ================ */

async function openPath(path, mode, editor, agent) {
	try {
		const r = await api("/api/open", { path, mode, editor, agent });
		if (!r.ok) toast(r.error || "打开失败", true);
		else if (r.dedup) toast("该目录窗口刚已打开");
	} catch (e) {
		toast(e.message, true);
	}
}

async function reloadTree() {
	try {
		const r = await api("/api/tree");
		data = r.data || { tree: null };
		render();
		if (selectedPath) {
			const n = findLocal(selectedPath);
			renderDetail(n);
		}
		updateStaleBar();
	} catch (e) {
		toast(e.message, true);
	}
}

function findLocal(path, node = data.tree) {
	if (!node) return null;
	if (node.path === path) return node;
	for (const c of node.children || []) {
		const r = findLocal(path, c);
		if (r) return r;
	}
	return null;
}

/* ================ 索引过期提醒 ================ */
// 索引生成超过这个时长就提示"可能已过期"，但仍由用户决定是否扫描——
// 星图不做定时自动扫描（见 CONTEXT.md「扫描」）。
const INDEX_STALE_HOURS = 24;
let staleDismissed = false;

function updateStaleBar() {
	const bar = $("#stale-bar");
	if (!bar) return;
	// 扫描 / 生成进行中时不叠加提示，避免与进度条重复
	if (staleDismissed || scanning || generating) {
		bar.classList.add("hidden");
		return;
	}
	const ts = data.generatedAt ? new Date(data.generatedAt).getTime() : 0;
	if (!ts) {
		bar.classList.add("hidden");
		return;
	}
	const hours = (Date.now() - ts) / 3600000;
	if (hours < INDEX_STALE_HOURS) {
		bar.classList.add("hidden");
		return;
	}
	// pi-lens-ignore: no-inner-html-js
	$("#stale-text").innerHTML =
		`<span class="stale-icon">⚠</span>索引生成于 ${fmtTime(data.generatedAt)}，` +
		`距今已 ${Math.floor(hours)} 小时，工作区可能已有变动`;
	bar.classList.remove("hidden");
}

let scanning = false; // 扫描进行中（屏蔽其他操作）

/* 扫描请求：网络层瞬时失败（快速连点 / 连接被重置）时自动重试一次，
   把底层的"暂时没响应"自愈掉，只有服务真的挂了才交给断线重连。 */
async function scanWithRetry() {
	let lastErr;
	for (let attempt = 0; attempt <= 1; attempt++) {
		try {
			return await api("/api/scan", {});
		} catch (e) {
			lastErr = e;
			if (e && e.isNetwork && attempt === 0) {
				await new Promise((res) => setTimeout(res, 700));
				continue;
			}
			throw e;
		}
	}
	throw lastErr;
}

async function doScan() {
	if (scanning) return;
	scanning = true;
	updateStaleBar(); // 扫描期间收起过期提示，避免与进度条重复
	const btn = $("#btn-scan");
	const bar = $("#progressbar");
	const fill = $("#progress-fill");
	const text = $("#progress-text");
	const stopBtn = $("#btn-stopgen");
	const disabledBtns = ["#btn-gen", "#btn-settings", "#btn-backup"]
		.map((s) => $(s))
		.filter(Boolean);
	btn.disabled = true;
	btn.textContent = "扫描中…";
	disabledBtns.forEach((b) => {
		b.disabled = true;
	});
	bar.classList.remove("hidden");
	stopBtn.classList.add("hidden"); // 扫描没有"停止"概念
	fill.classList.add("indeterminate");
	text.textContent = "正在扫描工作区…";
	try {
		const r = await scanWithRetry();
		data.tree = r.tree;
		// 后端才是生成时间的权威来源；不更新的话扫描完过期提示不会消失
		if (r.generatedAt) data.generatedAt = r.generatedAt;
		render();
		let msg =
			`扫描完成：项目 ${r.projects} 个（新增 ${r.new}，移除 ${r.removed}）` +
			(r.pendingIntro ? `，待生成介绍 ${r.pendingIntro} 个` : "");
		if (r.errors && r.errors.length) {
			msg += `；${r.errors.length} 个扫描根失败（已保留上次结果）`;
			console.warn("扫描失败的根：", r.errors);
		}
		if (r.truncated) msg += `；${r.truncated} 处目录因层级过深未继续展开`;
		toast(msg);
	} catch (e) {
		toast("扫描失败：" + e.message, true);
	} finally {
		scanning = false;
		btn.disabled = false;
		btn.textContent = "⟳ 扫描";
		disabledBtns.forEach((b) => {
			b.disabled = false;
		});
		fill.classList.remove("indeterminate");
		fill.style.transform = "scaleX(0)";
		bar.classList.add("hidden");
		stopBtn.classList.toggle("hidden", !generating);
		updateStaleBar(); // 扫描结束（无论成败）后重新评估是否过期
	}
}

async function doGenerate() {
	const pending = [];
	collectPending(data.tree, pending);
	if (!pending.length) return;
	generating = true;
	stopFlag = false;
	$("#btn-gen").classList.add("hidden");
	$("#btn-stopgen").classList.remove("hidden");
	const bar = $("#progressbar");
	bar.classList.remove("hidden");
	const fill = $("#progress-fill");
	const text = $("#progress-text");
	// 交给后端并发执行（默认 3 路），前端只轮询进度——
	// 不再逐个串行 await，100 个项目不再需要等上几十分钟
	let total = pending.length,
		done = 0,
		fail = 0,
		shown = -1;
	text.textContent = `0/${total}`;
	try {
		const r = await api("/api/intro/batch", { paths: pending });
		const tid = r.taskId;
		total = r.total || total;
		for (;;) {
			let s;
			try {
				s = await api(`/api/intro/batch?taskId=${encodeURIComponent(tid)}`);
			} catch {
				break; // 任务被清理等边缘情况，直接收尾
			}
			done = s.done;
			fail = s.fail;
			const finished = done + fail;
			fill.style.transform = `scaleX(${finished / total})`;
			text.textContent = `${finished}/${total}`;
			if (finished !== shown) {
				shown = finished;
				await reloadTree(); // 有进展就刷新，让介绍一条条出现
			}
			if (!s.running) break;
			if (stopFlag)
				await api("/api/intro/batch/stop", { taskId: tid }).catch(() => {});
			await new Promise((res) => setTimeout(res, 600));
		}
		toast(
			stopFlag
				? `已停止（完成 ${done}，失败 ${fail}）`
				: fail
					? `生成完成，${fail} 个失败`
					: "全部介绍生成完成",
		);
	} catch (e) {
		toast("生成失败：" + e.message, true);
	} finally {
		generating = false;
		bar.classList.add("hidden");
		$("#btn-stopgen").classList.add("hidden");
		fill.style.transform = "scaleX(0)";
		await reloadTree();
		updateGenButton();
		updateStaleBar();
	}
}

/* ================ 弹窗（焦点陷阱 + Esc + 脏数据确认） ================ */

/* 弹窗的键盘与遮罩监听：具名模块级函数，关闭时解绑。
   旧实现每次 openModal 都在 #modal-root 上叠加匿名监听且无法移除，
   开关 N 次后按一次 Esc 会连续弹 N 次确认框。 */
function onModalKeydown(e) {
	const root = $("#modal-root");
	const modal = root.firstElementChild;
	if (e.key === "Escape") {
		e.stopPropagation();
		if (modalDirty && !confirm("有未保存的修改，确定关闭？")) return;
		closeModal();
	} else if (e.key === "Tab" && modal) {
		// 焦点陷阱
		const focusable = [
			...modal.querySelectorAll(
				"input, select, textarea, button:not([disabled])",
			),
		];
		if (focusable.length === 0) return;
		const first = focusable[0],
			last = focusable[focusable.length - 1];
		if (e.shiftKey && document.activeElement === first) {
			e.preventDefault();
			last.focus();
		} else if (!e.shiftKey && document.activeElement === last) {
			e.preventDefault();
			first.focus();
		}
	}
}

function onModalBackdropClick(e) {
	if (e.target !== $("#modal-root")) return;
	if (modalDirty && !confirm("有未保存的修改，确定关闭？")) return;
	closeModal();
}

function openModal(html, returnFocus) {
	modalFocusReturn = returnFocus || document.activeElement;
	modalDirty = false;
	const root = $("#modal-root");
	root.classList.remove("hidden");
	// pi-lens-ignore: no-inner-html-js
	root.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
	const modal = root.firstElementChild;
	// 焦点陷阱
	setTimeout(() => {
		const first = modal.querySelector("input, select, textarea, button");
		if (first) first.focus();
	}, 0);
	// Esc 关闭 / Tab 陷阱 / 遮罩点击关闭（均有脏数据确认）
	root.removeEventListener("keydown", onModalKeydown);
	root.removeEventListener("click", onModalBackdropClick);
	root.addEventListener("keydown", onModalKeydown);
	root.addEventListener("click", onModalBackdropClick);
	return modal;
}
function closeModal() {
	const root = $("#modal-root");
	root.removeEventListener("keydown", onModalKeydown);
	root.removeEventListener("click", onModalBackdropClick);
	root.classList.add("hidden");
	// pi-lens-ignore: no-inner-html-js
	root.innerHTML = "";
	if (modalFocusReturn) {
		modalFocusReturn.focus();
		modalFocusReturn = null;
	}
}

/* ================ 设置 ================ */

const PROVIDERS = {
	zhipu: {
		label: "智谱（glm-4-flash 免费）",
		baseUrl: "https://open.bigmodel.cn/api/paas/v4",
		model: "glm-4-flash",
	},
	siliconflow: {
		label: "硅基流动（Qwen2.5-7B 免费）",
		baseUrl: "https://api.siliconflow.cn/v1",
		model: "Qwen/Qwen2.5-7B-Instruct",
	},
	custom: { label: "自定义 OpenAI 兼容", baseUrl: "", model: "" },
};

function openSettings() {
	const api_ = config.api || {};
	const oldPort = config.port || 6173;
	const bl = [...(config.blacklist || [])];
	const settingRoots = [...(config.roots || [])];
	const m = openModal(
		`
    <h3>⚙ 设置</h3>
    <div class="field"><label>界面主题</label>
      <select id="set-theme">
        <option value="dark">暗色</option>
        <option value="light">亮色</option>
        <option value="auto">跟随系统</option>
      </select>
    </div>
    <div class="field"><label>扫描根目录</label>
      <div class="taglist" id="set-roots-tags"></div>
      <button id="set-roots-pick" type="button" style="flex:0 0 auto">📁 选择目录…</button>
      <div class="hint">可添加多个根目录，点目录上的 ✕ 移除。
        选择窗口由星图服务在你的桌面上弹出——浏览器出于安全不提供本地路径，无法在网页里选。</div>
    </div>
    <div class="field">
      <label><input type="checkbox" id="set-git"> 扫描时读取 Git 状态（分支 / 未提交 / 最后提交时间）</label>
      <div class="hint">未检测到 git 时自动跳过，不影响其余功能</div>
    </div>
    <div class="field">
      <label><input type="checkbox" id="set-autoscan"> 启动时自动扫描一次，保持索引最新</label>
      <div class="hint">服务每次启动后后台自动更新索引（不阻塞使用）；刚扫过 1 小时内不重复扫。</div>
    </div>
    <div class="field">
      <label><input type="checkbox" id="set-autostart"> 开机自动启动（登录 Windows 后常驻后台）</label>
      <div class="hint">写入当前用户的启动项，无需管理员权限；取消勾选即移除。</div>
    </div>
    <div class="field"><label>黑名单（目录名，支持 * 通配符）</label>
      <div class="taglist" id="set-bl-tags"></div>
      <div class="row">
        <input type="text" id="set-bl-input" placeholder="输入名称或通配符">
        <button id="set-bl-add" style="flex:0 0 auto">添加</button>
      </div>
    </div>
    <div class="field"><label>已排除的项目（点「恢复」重新纳入）</label>
      <div id="set-excluded" class="excluded-list"></div>
    </div>
    <hr>
    <div class="field"><label>AI 服务商</label>
      <select id="set-provider">
        ${Object.entries(PROVIDERS)
					.map(([k, v]) => `<option value="${k}">${v.label}</option>`)
					.join("")}
      </select>
    </div>
    <div class="field"><label>API 地址 (baseUrl)</label>
      <input type="text" id="set-baseurl" value="${esc(api_.baseUrl || "")}">
    </div>
    <div class="field"><label>API Key</label>
      <input type="password" id="set-apikey" value="${esc(api_.apiKey || "")}">
    </div>
    <div class="field"><label>模型名</label>
      <input type="text" id="set-model" value="${esc(api_.model || "")}">
    </div>
    <div class="row">
      <button id="set-test">测试连接</button>
      <span id="set-test-result" class="hint"></span>
    </div>
    <hr>
    <div class="field"><label>GitHub 镜像 / 加速前缀（可选）</label>
      <input type="text" id="set-ghmirror" value="${esc(config.ghMirror || "")}" placeholder="https://gh-proxy.com">
      <div class="hint">github.com / raw.githubusercontent 被墙或拉不到时，直连失败会自动按此前缀重试（前缀拼在完整 GitHub URL 前，形如 ghproxy 类加速站）。不写死具体镜像，由你自填最稳。</div>
    </div>
    <hr>
    <div class="field"><label>常用备份目录</label>
      <input type="text" id="set-backupdir" value="${esc(config.backupDir || "")}" placeholder="U 盘或网盘同步文件夹">
    </div>
    <div class="field"><label>服务端口（修改后需重启）</label>
      <input type="text" id="set-port" value="${config.port || 6173}">
    </div>
    <details class="advanced">
      <summary>高级 · 备用编辑器（一般无需设置）</summary>
      <div class="field"><label>编辑器名称</label>
        <input type="text" id="set-edname" value="${esc(config.editor?.name || "")}">
      </div>
      <div class="field"><label>编辑器命令模板（{path} 为占位符）</label>
        <input type="text" id="set-edcmd" value="${esc(config.editor?.cmd || "")}" placeholder='trae "{path}"'>
      </div>
      <div class="hint">仅当自动检测不到本机已装编辑器时兜底；{path} 会被替换为项目路径。</div>
    </details>
    <details class="advanced" id="set-log-dd">
      <summary>📜 服务日志（跳转 / 启动 / 停止 / 报错）</summary>
      <div class="field">
        <pre id="set-log" class="log-view">加载中…</pre>
        <button id="set-log-refresh" type="button">刷新</button>
      </div>
    </details>
    <div class="footer">
      <button id="set-cancel">取消</button>
      <button id="set-save" class="primary">保存</button>
    </div>
  `,
		$("#btn-settings"),
	);

	// 服务日志面板：点击展开时拉取，可手动刷新
	(async () => {
		const dd = m.querySelector("#set-log-dd");
		const pre = m.querySelector("#set-log");
		const loadLog = async () => {
			pre.textContent = "加载中…";
			try {
				const r = await api("/api/log");
				pre.textContent = r.log || "（暂无日志）";
				pre.scrollTop = pre.scrollHeight;
			} catch (e) {
				pre.textContent = "读取失败：" + e.message;
			}
		};
		m.querySelector("#set-log-refresh").addEventListener("click", loadLog);
		dd.addEventListener("toggle", () => {
			if (dd.open) loadLog();
		});
	})();

	// 标记脏数据
	m.addEventListener("input", () => {
		modalDirty = true;
	});
	m.addEventListener("change", () => {
		modalDirty = true;
	});

	const themeSel = m.querySelector("#set-theme");
	themeSel.value = config.theme || "dark";
	m.querySelector("#set-git").checked = config.gitStatus !== false;
	m.querySelector("#set-autoscan").checked = config.autoScan !== false;
	m.querySelector("#set-autostart").checked = config.autostart === true;
	m.querySelector("#set-ghmirror").value = config.ghMirror || "";
	const provSel = m.querySelector("#set-provider");
	provSel.value = api_.provider || "zhipu";
	provSel.addEventListener("change", () => {
		const p = PROVIDERS[provSel.value];
		if (p) {
			m.querySelector("#set-baseurl").value = p.baseUrl;
			m.querySelector("#set-model").value = p.model;
		}
	});

	// 黑名单标签
	const tagsEl = m.querySelector("#set-bl-tags");
	const renderTags = () => {
		// pi-lens-ignore: no-inner-html-js
		tagsEl.innerHTML = "";
		bl.forEach((t, i) => {
			const tag = document.createElement("span");
			tag.className = "tag";
			// pi-lens-ignore: no-inner-html-js
			tag.innerHTML = `${esc(t)} <span class="x" title="移除">✕</span>`;
			tag.querySelector(".x").addEventListener("click", () => {
				bl.splice(i, 1);
				modalDirty = true;
				renderTags();
			});
			tagsEl.appendChild(tag);
		});
	};
	renderTags();

	// 扫描根目录：多选，路径由后端在本机弹出文件夹对话框后回传
	const rootsEl = m.querySelector("#set-roots-tags");
	const renderRoots = () => {
		// pi-lens-ignore: no-inner-html-js
		rootsEl.innerHTML = "";
		if (!settingRoots.length) {
			// pi-lens-ignore: no-inner-html-js
			rootsEl.innerHTML = `<span class="hint">尚未选择目录</span>`;
			return;
		}
		settingRoots.forEach((p, i) => {
			const tag = document.createElement("span");
			tag.className = "tag";
			tag.title = p;
			// pi-lens-ignore: no-inner-html-js
			tag.innerHTML = `${esc(p)} <span class="x" title="移除">✕</span>`;
			tag.querySelector(".x").addEventListener("click", () => {
				settingRoots.splice(i, 1);
				modalDirty = true;
				renderRoots();
			});
			rootsEl.appendChild(tag);
		});
	};
	renderRoots();

	const pickBtn = m.querySelector("#set-roots-pick");
	pickBtn.addEventListener("click", async () => {
		pickBtn.disabled = true;
		const label = pickBtn.textContent;
		pickBtn.textContent = "请在弹出的窗口中选择…";
		try {
			const r = await api("/api/pick-dir", {});
			if (r.cancelled || !r.path) return;
			if (settingRoots.includes(r.path)) {
				toast("该目录已在列表中");
				return;
			}
			settingRoots.push(r.path);
			modalDirty = true;
			renderRoots();
		} catch (e) {
			toast(e.message, true);
		} finally {
			pickBtn.disabled = false;
			pickBtn.textContent = label;
		}
	});

	const blInput = m.querySelector("#set-bl-input");
	const addBl = () => {
		const v = blInput.value.trim();
		if (v && !bl.includes(v)) {
			bl.push(v);
			modalDirty = true;
			renderTags();
		}
		blInput.value = "";
	};
	m.querySelector("#set-bl-add").addEventListener("click", addBl);
	blInput.addEventListener("keydown", (e) => {
		if (e.key === "Enter") {
			e.preventDefault();
			addBl();
		}
	});

	// 已排除的项目列表 + 恢复
	const excludedEl = m.querySelector("#set-excluded");
	const renderExcluded = () => {
		const list = [];
		(function walk(n) {
			if (!n) return;
			if (n.marked === "off" && n.path)
				list.push({ name: n.name, path: n.path });
			for (const c of n.children || []) walk(c);
		})(data.tree);
		// pi-lens-ignore: no-inner-html-js
		excludedEl.innerHTML = "";
		if (!list.length) {
			// pi-lens-ignore: no-inner-html-js
			excludedEl.innerHTML = `<span class="hint">无</span>`;
			return;
		}
		for (const item of list) {
			const row = document.createElement("div");
			row.className = "excluded-row";
			const name = document.createElement("span");
			name.textContent = item.name;
			name.title = item.path;
			const btnRestore = document.createElement("button");
			btnRestore.textContent = "恢复";
			btnRestore.addEventListener("click", async () => {
				btnRestore.disabled = true;
				btnRestore.textContent = "…";
				try {
					await api("/api/mark", { path: item.path, mark: "auto" });
					await reloadTree();
					toast(`已恢复「${item.name}」`);
					renderExcluded();
				} catch (e) {
					toast(e.message, true);
					btnRestore.disabled = false;
					btnRestore.textContent = "恢复";
				}
			});
			row.appendChild(name);
			row.appendChild(btnRestore);
			excludedEl.appendChild(row);
		}
	};
	renderExcluded();

	// 测试连接
	m.querySelector("#set-test").addEventListener("click", async () => {
		const out = m.querySelector("#set-test-result");
		out.textContent = "测试中…";
		try {
			const r = await api("/api/config/test", {
				baseUrl: m.querySelector("#set-baseurl").value.trim(),
				apiKey: m.querySelector("#set-apikey").value,
				model: m.querySelector("#set-model").value.trim(),
			});
			out.textContent = r.ok ? `✓ 连接成功` : "✗ " + r.error;
		} catch (e) {
			out.textContent = "✗ " + e.message;
		}
	});

	m.querySelector("#set-cancel").addEventListener("click", () => {
		if (modalDirty && !confirm("有未保存的修改，确定关闭？")) return;
		closeModal();
	});
	m.querySelector("#set-save").addEventListener("click", async () => {
		const cfg = {
			theme: themeSel.value,
			roots: settingRoots,
			blacklist: bl,
			api: {
				provider: provSel.value,
				baseUrl: m.querySelector("#set-baseurl").value.trim(),
				apiKey: m.querySelector("#set-apikey").value,
				model: m.querySelector("#set-model").value.trim(),
			},
			editor: {
				name: m.querySelector("#set-edname").value.trim() || "编辑器",
				cmd: m.querySelector("#set-edcmd").value.trim(),
			},
			backupDir: m.querySelector("#set-backupdir").value.trim(),
			gitStatus: m.querySelector("#set-git").checked,
			autoScan: m.querySelector("#set-autoscan").checked,
			autostart: m.querySelector("#set-autostart").checked,
			ghMirror: m.querySelector("#set-ghmirror").value.trim(),
			port: parseInt(m.querySelector("#set-port").value, 10) || 6173,
		};
		try {
			const r = await api("/api/config", { config: cfg });
			config = r.config;
			applyTheme();
			modalDirty = false;
			closeModal();
			const autoMsg = r.autostartError
				? "（开机自启设置失败：" + r.autostartError + "）"
				: "";
			toast(
				"设置已保存" +
					(cfg.port !== oldPort ? "（端口修改需重启生效）" : "") +
					autoMsg,
			);
		} catch (e) {
			toast("保存失败：" + e.message, true);
		}
	});
}

/* ================ 已排除项目管理页 ================ */
// 排除 = 把节点 marked 置 "off"（数据里保留、只是不显示）。本页列出入库的
// 被排除节点并逐个/全部恢复 —— 是被藏起来的项目的"回收站"。

function collectExcluded(list) {
	(function walk(n) {
		if (!n) return;
		if (n.marked === "off" && n.path) list.push(n);
		for (const c of n.children || []) walk(c);
	})(data.tree);
}

function updateExcludedCount() {
	const el = $("#excluded-count");
	if (!el) return;
	const list = [];
	collectExcluded(list);
	el.textContent = list.length ? " " + list.length : "";
	el.title = list.length ? `有 ${list.length} 个被排除的项目，点击恢复` : "";
}

async function restoreExcluded(path, btn) {
	if (btn) btn.disabled = true;
	try {
		const r = await api("/api/mark", { path, mark: "auto" });
		toast("已恢复" + (r.needRescan ? "（重新扫描后完全生效）" : ""));
		await reloadTree();
	} catch (e) {
		toast(e.message, true);
		if (btn) btn.disabled = false;
	}
}

function openExcluded() {
	const m = openModal(
		`
    <h3>🗑 已排除的项目</h3>
    <div class="hint">被排除的项目不会显示在树里，但数据仍保留（此即"回收站"）。点「恢复」重新纳入；"重新扫描后完全生效"指它重新被识别为项目。</div>
    <div class="row" style="margin:10px 0">
      <input type="text" id="ex-search" placeholder="按名称 / 路径过滤">
      <button id="ex-restore-all" class="primary" style="flex:0 0 auto">全部恢复</button>
    </div>
    <div id="ex-list" class="excluded-list-2"></div>
    <div class="footer"><button id="ex-close">关闭</button></div>
  `,
		$("#btn-excluded"),
	);

	const listEl = m.querySelector("#ex-list");
	const searchEl = m.querySelector("#ex-search");
	// 恢复"off"后需重扫才重新识别为项目，统一在提示里说明
	const needRescan = true;
	let items = [];
	const render = () => {
		const q = searchEl.value.trim().toLowerCase();
		const shown = items.filter(
			(n) => !q || (n.name + " " + n.path).toLowerCase().includes(q),
		);
		// pi-lens-ignore: no-inner-html-js
		listEl.innerHTML = "";
		if (!shown.length) {
			// pi-lens-ignore: no-inner-html-js
			listEl.innerHTML = `<span class="hint">${items.length ? "没有匹配项" : "没有被排除的项目"}</span>`;
			return;
		}
		for (const n of shown) {
			const row = document.createElement("div");
			row.className = "excluded-row";
			const info = document.createElement("span");
			// pi-lens-ignore: no-inner-html-js
			info.innerHTML = `<span class="ex-name">${esc(n.name)}</span> <span class="ex-path">${esc(n.path)}</span>`;
			info.title = n.path;
			const btn = document.createElement("button");
			btn.textContent = "恢复";
			btn.addEventListener("click", async () => {
				btn.disabled = true;
				btn.textContent = "…";
				try {
					await api("/api/mark", { path: n.path, mark: "auto" });
					toast(
						`已恢复「${n.name}」${needRescan ? "（重新扫描后完全生效）" : ""}`,
					);
					items = items.filter((x) => x.path !== n.path);
					render();
					updateExcludedCount();
				} catch (e) {
					toast(e.message, true);
					btn.disabled = false;
					btn.textContent = "恢复";
				}
			});
			row.appendChild(info);
			row.appendChild(btn);
			listEl.appendChild(row);
		}
	};
	searchEl.addEventListener("input", render);
	collectExcluded(items);
	render();
	m.querySelector("#ex-close").addEventListener("click", closeModal);
	m.querySelector("#ex-restore-all").addEventListener("click", async () => {
		if (!items.length) return;
		if (!confirm(`确定恢复全部 ${items.length} 个被排除的项目？`)) return;
		for (const n of items) {
			try {
				await api("/api/mark", { path: n.path, mark: "auto" });
			} catch (e) {}
		}
		items = [];
		render();
		updateExcludedCount();
		await reloadTree();
		toast("已全部恢复（重新扫描后完全生效）");
	});
}

/* ================ GitHub 趋势 · 中文解读 ================ */
/* 榜单来自 github.com/trending；中文一句话介绍与中文导读复用设置里的 LLM，
   生成结果由后端长期缓存，重复查看不再消耗额度。 */

const TREND_SINCE = [
	["daily", "今日"],
	["weekly", "本周"],
	["monthly", "本月"],
];
const trend = {
	since: "daily",
	lang: "",
	items: [],
	intros: {},
	guides: {},
	digest: "",
	langs: [],
	fetchedAt: 0,
	filter: "",
	busy: false,
};
let trendPoll = null; // 批量生成进度轮询
let trendTaskId = null;
let trendReqSeq = 0; // 榜单拉取序号：快速切时段/语言时，只认最后一次请求的响应

function fmtStar(n) {
	n = Number(n) || 0;
	return n >= 1000
		? (n / 1000).toFixed(1).replace(/\.0$/, "") + "k"
		: String(n);
}

/* 把 AI 输出的纯文本（一句话 + '- ' 要点 + 适合：/上手：/推荐：）渲染成结构化 HTML */
function renderAIBlock(text) {
	const escHtml = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
	const lines = String(text || "")
		.split("\n")
		.map((s) => s.trim())
		.filter(Boolean);
	let html = "";
	let inList = false;
	for (let i = 0; i < lines.length; i++) {
		const ln = lines[i];
		const bullet = ln.startsWith("- ");
		if (bullet && !inList) {
			html += "<ul class='ai-ul'>";
			inList = true;
		}
		if (!bullet && inList) {
			html += "</ul>";
			inList = false;
		}
		if (bullet) {
			html += `<li>${escHtml(ln.slice(2))}</li>`;
		} else if (ln.startsWith("适合：") || ln.startsWith("上手：")) {
			const [k, ...rest] = ln.split("：");
			html += `<div class='ai-kv'><span class='ai-k'>${esc(k)}</span>${escHtml(rest.join("："))}</div>`;
		} else if (ln.startsWith("推荐：")) {
			html += `<div class='ai-rec'>⭐ ${escHtml(ln.slice(3))}</div>`;
		} else if (ln.startsWith("本期风向：")) {
			html += `<div class='ai-wind'>${escHtml(ln.slice(5))}</div>`;
		} else {
			html += `<div class='ai-one'>${escHtml(ln)}</div>`;
		}
	}
	if (inList) html += "</ul>";
	return html;
}

async function trendLoad(force) {
	const listEl = $("#tr-list");
	const metaEl = $("#tr-meta");
	const seq = ++trendReqSeq; // 只认最后一次请求
	// pi-lens-ignore: no-inner-html-js
	if (listEl)
		listEl.innerHTML = `<div class="hint">正在拉取 GitHub 趋势…</div>`;
	try {
		const q =
			`?since=${encodeURIComponent(trend.since)}&lang=${encodeURIComponent(trend.lang)}` +
			(force ? "&refresh=1" : "");
		const r = await api("/api/trending" + q);
		// 期间又切了时段/语言/刷新：丢弃这个过期响应，否则新条件下会显示旧数据
		if (seq !== trendReqSeq) return;
		trend.items = r.items || [];
		trend.intros = r.intros || {};
		trend.guides = r.guides || {};
		trend.langs = r.langs || [];
		trend.fetchedAt = r.fetchedAt || 0;
		if (r.error) toast(r.error, true);
		trendRender();
		// 语言下拉只回填一次（防并发加载时重复 append option）
		const langSel = $("#tr-lang");
		if (langSel && !trend.langsFilled && trend.langs.length) {
			trend.langsFilled = true;
			trend.langs.forEach((lb) => {
				if (lb === "全部语言") return;
				const o = document.createElement("option");
				o.value = lb;
				o.textContent = lb;
				if (lb === trend.lang) o.selected = true;
				langSel.appendChild(o);
			});
		}
		if (metaEl) {
			const when = trend.fetchedAt
				? new Date(trend.fetchedAt * 1000).toLocaleString("zh-CN")
				: "—";
			metaEl.textContent =
				`共 ${trend.items.length} 条 · 更新于 ${when}` +
				(r.cached
					? r.stale
						? "（网络失败，展示旧缓存）"
						: "（本地缓存）"
					: "（刚拉取）");
		}
	} catch (e) {
		if (seq !== trendReqSeq) return; // 过期请求的错误不打扰用户
		// pi-lens-ignore: no-inner-html-js
		if (listEl)
			listEl.innerHTML = `<div class="hint">拉取失败：${esc(e.message)}</div>`;
		toast(e.message, true);
	}
}

function trendCardHTML(it) {
	const intro = trend.intros[it.fullName];
	const hasGuide = !!trend.guides[it.fullName];
	const sinceLabel = (TREND_SINCE.find((s) => s[0] === trend.since) || [
		"",
		"",
	])[1];
	return `<div class="trend-card" data-name="${esc(it.fullName)}" data-desc="${esc(it.desc || "")}">
    <div class="tc-head">
      <span class="tc-rank">${it.rank}</span>
      <a class="tc-name" href="${esc(it.url)}" target="_blank" rel="noopener"
         title="${esc(it.url)}">${esc(it.owner)} / <b>${esc(it.name)}</b></a>
    </div>
    <div class="tc-meta">
      ${it.lang ? `<span class="tc-lang"><i style="background:${esc(it.langColor || "#8b949e")}"></i>${esc(it.lang)}</span>` : ""}
      <span class="tc-star">★ ${fmtStar(it.stars)}</span>
      <span class="tc-today">${sinceLabel} +${fmtStar(it.today)}</span>
    </div>
    <div class="tc-desc">${esc(it.desc || "（无描述）")}</div>
    ${intro ? `<div class="tc-intro">🇨🇳 ${esc(intro)}</div>` : ""}
    <div class="tc-acts">
      <button data-act="intro">${intro ? "🔄 重生成" : "✨ 中文介绍"}</button>
      <button data-act="guide">${hasGuide ? "📖 看导读" : "📖 中文导读"}</button>
      <button data-act="copy" title="复制仓库地址">⧉ 地址</button>
    </div>
  </div>`;
}

function trendRender() {
	const listEl = $("#tr-list");
	if (!listEl) return;
	const q = (trend.filter || "").trim().toLowerCase();
	const shown = trend.items.filter(
		(it) =>
			!q ||
			(it.fullName + " " + (it.desc || "") + " " + (it.lang || ""))
				.toLowerCase()
				.includes(q),
	);
	if (!shown.length) {
		// pi-lens-ignore: no-inner-html-js
		listEl.innerHTML = `<div class="hint">${trend.items.length ? "没有匹配的仓库" : "暂无数据"}</div>`;
		return;
	}
	// pi-lens-ignore: no-inner-html-js
	listEl.innerHTML = shown.map(trendCardHTML).join("");
}

/* 只重绘某一张卡（改 label/介绍等状态），不动整表 —— 保留列表滚动与键盘焦点 */
function trendRefreshCard(fullName) {
	const card = document.querySelector(
		`#tr-list .trend-card[data-name="${CSS.escape(fullName)}"]`,
	);
	if (!card) return;
	const item = trend.items.find((it) => it.fullName === fullName);
	if (!item) return;
	// 记录焦点按钮（若在卡内），替换后还给它
	const focusedAct = card.contains(document.activeElement)
		? document.activeElement.dataset.act
		: null;
	// pi-lens-ignore: no-inner-html-js
	card.outerHTML = trendCardHTML(item);
	if (focusedAct) {
		const b = document.querySelector(
			`#tr-list .trend-card[data-name="${CSS.escape(fullName)}"] button[data-act="${focusedAct}"]`,
		);
		if (b) b.focus();
	}
}

async function trendOneIntro(name, desc, btn) {
	if (btn) {
		btn.disabled = true;
		btn.textContent = "…";
	}
	try {
		const r = await api("/api/trending/intro", {
			fullName: name,
			desc: desc || "",
		});
		trend.intros[name] = r.text;
		trendRefreshCard(name); // 重绘单卡，按钮 label 由状态自然更新为「🔄 重生成」
	} catch (e) {
		toast(e.message, true);
		if (btn) {
			btn.disabled = false;
			btn.textContent = "✨ 中文介绍";
		}
	}
}

async function trendOneGuide(name, desc, btn) {
	if (btn) {
		btn.disabled = true;
		btn.textContent = "…";
	}
	try {
		const r = await api("/api/trending/guide", {
			fullName: name,
			desc: desc || "",
		});
		trend.guides[name] = r.text;
		showGuide(name, r.text, r.cached);
		trendRefreshCard(name);
	} catch (e) {
		toast(e.message, true);
		if (btn) {
			btn.disabled = false;
			btn.textContent = "📖 中文导读";
		}
	}
}

/* 阅读栏辅助：只要有一个结果面板可见就给 #tr-rail 挂 .open（滑入抽屉）并显示遮罩 */
function trendRailOpen() {
	const rail = $("#tr-rail");
	if (!rail) return;
	const anyOpen =
		!$("#tr-guide-box").classList.contains("hidden") ||
		!$("#tr-digest-box").classList.contains("hidden");
	rail.classList.toggle("open", anyOpen);
	const mask = $("#tr-drawer-mask");
	if (mask) mask.classList.toggle("hidden", !anyOpen);
}

/* 把某面板滚进阅读栏可视区（宽屏右栏内滚动；窄屏整栏位于列表上方已可见） */
function trendScrollPanel(box) {
	// 真正的滚动容器是 .tr-drawer-body（overflow-y:auto），#tr-rail 不可滚动
	const scroller = box.closest(".tr-drawer-body") || $("#tr-rail");
	if (!scroller) return;
	const target =
		box.getBoundingClientRect().top -
		scroller.getBoundingClientRect().top +
		scroller.scrollTop -
		4;
	scroller.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
}

/* 导读展示：写入右阅读栏 #tr-guide-box，可收起；不重建列表、不移动原列表焦点 */
function showGuide(name, text, cached) {
	const box = $("#tr-guide-box");
	if (!box) return;
	box.classList.remove("hidden");
	trendRailOpen();
	// pi-lens-ignore: no-inner-html-js
	box.innerHTML = `
    <div class="gb-head"><strong>📖 中文导读 · ${esc(name)}</strong>
      <button id="tr-guide-close" title="收起导读" aria-label="收起导读">✕</button></div>
    <div class="guide-box">${renderAIBlock(text)}</div>
    <div class="hint">由设置中的 LLM 依据 README 生成${cached ? "（缓存）" : ""}，仅供参考；细节以仓库原文为准。</div>`;
	const close = $("#tr-guide-close");
	if (close)
		close.addEventListener("click", () => {
			box.classList.add("hidden");
			trendRailOpen();
		});
	requestAnimationFrame(() => trendScrollPanel(box));
}

async function trendBatch() {
	if (trendTaskId) {
		toast("批量生成进行中，可点「停止」后再试");
		return;
	}
	const targets = trend.items.filter((it) => !trend.intros[it.fullName]);
	if (!targets.length) {
		toast("榜单里的项目都已经有中文介绍了");
		return;
	}
	const bar = $("#tr-progress");
	bar.classList.remove("hidden");
	bar.setAttribute("aria-valuenow", "0");
	$("#tr-progress-text").textContent = `0 / ${targets.length}`;
	try {
		const r = await api("/api/trending/intros", {
			items: targets.map((it) => ({ fullName: it.fullName, desc: it.desc })),
		});
		trendTaskId = r.taskId;
		let fails = 0; // 轮询连续失败计数：网络闪断退避重试，避免进度条永久卡死
		const tick = async () => {
			try {
				const st = await api(
					`/api/trending/intros?taskId=${encodeURIComponent(trendTaskId)}`,
				);
				fails = 0;
				const pct = Math.round(((st.done + st.fail) / st.total) * 100);
				$("#tr-progress-text").textContent =
					`${st.done + st.fail} / ${st.total}（失败 ${st.fail}）`;
				$("#tr-progress-fill").style.width = pct + "%";
				bar.setAttribute("aria-valuenow", String(pct));
				if (st.running) {
					trendPoll = setTimeout(tick, 800);
					return;
				}
				trendPoll = null;
				bar.classList.add("hidden");
				trendTaskId = null;
				await trendLoad(false); // 拉一次缓存，把新生成的介绍带回来
				toast(`中文介绍生成完成：成功 ${st.done}，失败 ${st.fail}`);
			} catch (e) {
				fails += 1;
				if (fails <= 5) {
					trendPoll = setTimeout(tick, 1500 * fails);
					return;
				}
				trendPoll = null;
				trendTaskId = null;
				$("#tr-progress-text").textContent = "进度查询失败：" + e.message;
				toast("批量进度查询失败，已停止轮询", true);
			}
		};
		tick();
	} catch (e) {
		bar.classList.add("hidden");
		toast(e.message, true);
	}
}

async function trendDigest() {
	if (trend.digestBusy) return; // 一次 LLM 调用最长 45s，防连点并发请求
	trend.digestBusy = true;
	const btn = $("#tr-digest");
	if (btn) btn.disabled = true;
	const box = $("#tr-digest-box");
	const sinceLabel = (TREND_SINCE.find((s) => s[0] === trend.since) || [
		"",
		"",
	])[1];
	const scopeLabel = trend.lang ? sinceLabel + " · " + trend.lang : sinceLabel;
	// pi-lens-ignore: no-inner-html-js
	box.innerHTML = `
    <div class="gb-head"><strong>📋 本期速览 · ${esc(scopeLabel)}</strong>
      <button id="tr-digest-close" title="收起速览" aria-label="收起速览">✕</button></div>
    <div class="digest-body"><div class="hint">正在让 AI 通读榜单并总结…（约需十几秒）</div></div>`;
	box.classList.remove("hidden");
	trendRailOpen();
	const close = $("#tr-digest-close");
	if (close)
		close.addEventListener("click", () => {
			box.classList.add("hidden");
			trendRailOpen();
		});
	try {
		const r = await api("/api/trending/digest", {
			since: trend.since,
			lang: trend.lang,
		});
		trend.digest = r.text;
		const body = box.querySelector(".digest-body");
		// pi-lens-ignore: no-inner-html-js
		if (body) body.innerHTML = renderAIBlock(r.text);
		requestAnimationFrame(() => trendScrollPanel(box));
	} catch (e) {
		const body = box.querySelector(".digest-body");
		// pi-lens-ignore: no-inner-html-js
		if (body) body.innerHTML = `<div class="hint">${esc(e.message)}</div>`;
		toast(e.message, true);
	} finally {
		trend.digestBusy = false;
		if (btn) btn.disabled = false;
	}
}

async function trendAdHoc() {
	const el = $("#tr-adhoc");
	let raw = (el.value || "").trim();
	if (!raw) return;
	if (trend.adhocBusy) return; // 一次导读最长 45s，防连点并发请求
	trend.adhocBusy = true;
	const go = $("#tr-adhoc-go");
	if (go) {
		go.disabled = true;
		go.textContent = "…";
	}
	// 支持直接粘贴 GitHub 链接（含 /tree/main、/blob/... 等深链，取前两段）
	raw = raw
		.replace(/^https?:\/\/(www\.)?github\.com\//i, "")
		.replace(/\/+$/, "");
	raw = raw.replace(/\.git$/i, "");
	const segs = raw.split("/").filter(Boolean);
	if (segs.length > 2) raw = segs[0] + "/" + segs[1];
	if (!/^[^/\s]+\/[^/\s]+$/.test(raw)) {
		trend.adhocBusy = false;
		if (go) {
			go.disabled = false;
			go.textContent = "📖 中文导读";
		}
		toast("请填 owner/repo 或 GitHub 仓库链接", true);
		return;
	}
	try {
		const r = await api("/api/trending/guide", { fullName: raw });
		trend.guides[raw] = r.text;
		showGuide(raw, r.text, r.cached);
	} catch (e) {
		toast(e.message, true);
	} finally {
		trend.adhocBusy = false;
		if (go) {
			go.disabled = false;
			go.textContent = "📖 中文导读";
		}
	}
}

/* ---- 主视图切换：树 / 常用 / 趋势 三个页签自由互切 ---- */
let trendBound = false; // 趋势视图为静态 HTML，事件只绑一次

function isTrendOn() {
	return !$("#trend-view").classList.contains("hidden");
}

function currentView() {
	return isTrendOn() ? "trend" : viewMode;
}

function syncViewTabs() {
	const cur = currentView();
	for (const [id, v] of [
		["#view-tab-tree", "tree"],
		["#view-tab-frequent", "frequent"],
		["#view-tab-trend", "trend"],
	]) {
		const t = $(id);
		if (!t) continue;
		const on = cur === v;
		t.classList.toggle("on", on);
		t.setAttribute("aria-selected", String(on));
	}
}

function resetTrendPanels() {
	// 收起阅读栏（批量任务不中断，轮询照常）
	$("#tr-guide-box")?.classList.add("hidden");
	$("#tr-digest-box")?.classList.add("hidden");
	trendRailOpen();
}

function showMainView(v) {
	if (v === "trend") {
		if (!isTrendOn()) {
			$("#tree-panel").classList.add("hidden");
			$("#detail").classList.add("hidden");
			$("#trend-view").classList.remove("hidden");
			syncTrendUI();
			bindTrendEvents();
			trendLoad(false);
		}
	} else if (v === "tree" || v === "frequent") {
		if (isTrendOn()) {
			$("#trend-view").classList.add("hidden");
			resetTrendPanels();
		}
		viewMode = v;
		searchQuery = "";
		$("#search").value = "";
		$("#search-clear").style.display = "none";
		$("#tree-panel").classList.remove("hidden");
		if (selectedNode) $("#detail").classList.remove("hidden");
		render();
	}
	syncViewTabs();
}

function syncTrendUI() {
	document.querySelectorAll("#tr-since button").forEach((b) => {
		const on = b.dataset.since === trend.since;
		b.classList.toggle("on", on);
		b.setAttribute("aria-pressed", String(on));
	});
	const langSel = $("#tr-lang");
	if (langSel) langSel.value = trend.lang;
	const filter = $("#tr-filter");
	if (filter && filter.value !== (trend.filter || ""))
		filter.value = trend.filter || "";
}

function bindTrendEvents() {
	if (trendBound) return;
	trendBound = true;

	// 阅读栏收起：✕ 按钮或点击遮罩
	const closeDrawer = () => resetTrendPanels();
	const dc = $("#tr-drawer-close");
	if (dc) dc.addEventListener("click", closeDrawer);
	const mask = $("#tr-drawer-mask");
	if (mask) mask.addEventListener("click", closeDrawer);

	document.querySelector("#tr-since").addEventListener("click", (e) => {
		const b = e.target.closest("button[data-since]");
		if (!b) return;
		trend.since = b.dataset.since;
		[...document.querySelectorAll("#tr-since button")].forEach((x) => {
			const on = x === b;
			x.classList.toggle("on", on);
			x.setAttribute("aria-pressed", String(on));
		});
		trend.digest = "";
		$("#tr-digest-box").classList.add("hidden");
		trendRailOpen();
		trendLoad(false);
	});
	$("#tr-lang").addEventListener("change", (e) => {
		trend.lang = e.target.value;
		trend.digest = "";
		$("#tr-digest-box").classList.add("hidden");
		trendRailOpen();
		trendLoad(false);
	});
	$("#tr-refresh").addEventListener("click", () => trendLoad(true));
	$("#tr-filter").addEventListener("input", (e) => {
		trend.filter = e.target.value;
		trendRender();
	});
	$("#tr-filter").addEventListener("keydown", (e) => {
		if (e.key === "Escape" && trend.filter) {
			trend.filter = "";
			e.target.value = "";
			trendRender();
		}
	});
	$("#tr-all").addEventListener("click", trendBatch);
	$("#tr-digest").addEventListener("click", trendDigest);
	$("#tr-adhoc-go").addEventListener("click", trendAdHoc);
	$("#tr-adhoc").addEventListener("keydown", (e) => {
		if (e.key === "Enter") trendAdHoc();
	});
	$("#tr-stop").addEventListener("click", async () => {
		if (!trendTaskId) return;
		try {
			await api("/api/trending/intros/stop", { taskId: trendTaskId });
		} catch (e) {}
		toast("已请求停止，正在收尾");
	});

	$("#tr-list").addEventListener("click", (e) => {
		const btn = e.target.closest("button[data-act]");
		if (!btn) return;
		const card = btn.closest(".trend-card");
		const name = card.dataset.name;
		const desc = card.dataset.desc || "";
		const act = btn.dataset.act;
		if (act === "intro") trendOneIntro(name, desc, btn);
		else if (act === "guide") trendOneGuide(name, desc, btn); else if (act === "copy") {
			navigator.clipboard
				.writeText("https://github.com/" + name)
				.then(() => toast("已复制仓库地址"))
				.catch(() => toast("复制失败", true));
		}
	});

	// Esc 返回项目树：弹窗打开时让位给弹窗处理，焦点在输入框时让位给清空逻辑
	document.addEventListener("keydown", (e) => {
		if (e.key !== "Escape") return;
		if (!$("#modal-root").classList.contains("hidden")) return;
		if ($("#trend-view").classList.contains("hidden")) return;
		if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || ""))
			return;
		showMainView(viewMode); // Esc：趋势 → 回到之前（树/常用）
	});
}

/* ================ 备份 ================ */

function openBackup() {
	const m = openModal(
		`
    <h3>📤 备份与恢复</h3>
    <div class="field"><label>导出到（目录；留空使用设置中的常用备份目录）</label>
      <input type="text" id="bk-dest" value="${esc(config.backupDir || "")}" placeholder="如 E:\\ 或 D:\\NutstoreFiles">
    </div>
    <div class="footer">
      <button id="bk-export" class="primary">导出备份 (zip)</button>
    </div>
    <hr>
    <div class="field"><label>从备份恢复（选择 starchart-YYYYMMDD.zip）</label>
      <input type="file" id="bk-file" accept=".zip">
      <div class="hint">恢复会覆盖当前数据（覆盖前自动生成 .bak 备份）</div>
    </div>
    <div class="footer">
      <button id="bk-close">关闭</button>
    </div>
  `,
		$("#btn-backup"),
	);
	m.querySelector("#bk-close").addEventListener("click", closeModal);
	m.querySelector("#bk-export").addEventListener("click", async () => {
		try {
			const r = await api("/api/backup/export", {
				dest: m.querySelector("#bk-dest").value.trim(),
			});
			if (r.ok) toast("已导出：" + r.path);
			else toast(r.error, true);
		} catch (e) {
			toast(e.message, true);
		}
	});
	m.querySelector("#bk-file").addEventListener("change", async (e) => {
		const file = e.target.files[0];
		if (!file) return;
		if (!confirm("确定从该备份恢复？当前数据将被覆盖。")) return;
		try {
			const r = await api("/api/backup/import", file, true);
			if (r.ok) {
				toast("恢复成功");
				closeModal();
				await reloadConfig();
				await reloadTree();
				applyTheme();
			} else toast(r.error || "恢复失败", true);
		} catch (err) {
			toast(err.message, true);
		}
	});
}

/* ================ 初始化 ================ */

const themeMedia = window.matchMedia("(prefers-color-scheme: dark)");

function applyTheme() {
	// auto = 跟随系统；dark/light 为固定主题
	const t =
		config.theme === "auto"
			? themeMedia.matches
				? "dark"
				: "light"
			: config.theme === "light"
				? "light"
				: "dark";
	document.body.classList.toggle("light", t === "light");
	document.body.classList.toggle("dark", t !== "light");
	// 同步一份到 localStorage，供首屏内联脚本提前上色（消除主题闪烁）
	try {
		localStorage.setItem("starchart-theme", t);
	} catch (e) {}
}

async function reloadConfig() {
	try {
		config = (await api("/api/config")).config;
		applyTheme();
	} catch (e) {
		toast(e.message, true);
	}
}

/* 启动自动扫描跟随：服务在后台重建索引时，等它扫完再刷新界面并提示。
   仅在设置开启了 autoScan 时运行，最多观察 25s，避免常驻轮询。 */
function watchStartupScan() {
	if (config.autoScan === false) return;
	let seenScan = false;
	let ended = false;
	const stop = () => {
		clearInterval(tid);
		clearTimeout(timer);
		ended = true;
	};
	const timer = setTimeout(stop, 25000);
	const tid = setInterval(async () => {
		try {
			if (ended) return;
			const r = await api("/api/status");
			if (r && r.scanning) {
				seenScan = true;
			} else if (seenScan) {
				stop();
				await reloadTree();
				toast("索引已自动更新");
			}
		} catch (e) {}
	}, 1200);
}

async function init() {
	// 断线重连：服务起来后一键刷新回到首页。必须放在可能提前 return 的网络检查之前，
	// 否则首次加载就连不上时按钮没有绑定，点了没反应。
	const retryBtn = $("#netdown-reload");
	if (retryBtn) {
		retryBtn.addEventListener("click", () => {
			_netdownShown = false;
			location.reload();
		});
	}

	// 先还原 URL 深链状态（选中/视图/搜索），再加载数据渲染，让刷新后回到原状态
	applyHash();

	// 首次加载连不上服务时，页面也能正常弹"断线重连"遮罩，而不是 init 中断导致整页无响应。
	// 同样先探活，只有服务真不可达（残留旧标签页）才全屏遮罩。
	try {
		await reloadConfig();
		await reloadTree();
	} catch (e) {
		if (isNetError(e)) {
			probeAlive().then((alive) => {
				if (!alive) showNetDown();
			});
			return;
		}
		throw e;
	}

	// 数据就绪后，把深链指向的路径解析成节点并打开详情
	if (selectedPath) {
		selectedNode = findLocal(selectedPath);
		if (selectedNode) renderDetail(selectedNode);
	}

	// 系统主题切换时，"跟随系统"模式实时生效
	const onSysTheme = () => {
		if (config.theme === "auto") applyTheme();
	};
	if (themeMedia.addEventListener)
		themeMedia.addEventListener("change", onSysTheme);
	else themeMedia.addListener(onSysTheme); // 旧版浏览器

	// 检测本机编辑器与 Agent
	api("/api/editors")
		.then((r) => {
			detectedEditors = r.editors || [];
			detectedAgents = r.agents || [];
			if (selectedNode) renderDetail(selectedNode);
		})
		.catch(() => {});

	$("#btn-scan").addEventListener("click", doScan);
	const emptyScan = $("#empty-scan");
	if (emptyScan) emptyScan.addEventListener("click", doScan);
	$("#stale-rescan").addEventListener("click", doScan);
	// 主视图页签：树 / 常用 / 趋势 自由互切
	const trTab = $("#view-tab-trend");
	const treeTab = $("#view-tab-tree");
	const freqTab = $("#view-tab-frequent");
	if (treeTab) treeTab.addEventListener("click", () => showMainView("tree"));
	if (freqTab)
		freqTab.addEventListener("click", () => showMainView("frequent"));
	if (trTab)
		trTab.addEventListener("click", () => {
			if (currentView() === "trend")
				showMainView(viewMode); // 已开：再点收起
			else showMainView("trend");
		});
	syncViewTabs();
	$("#stale-dismiss").addEventListener("click", () => {
		staleDismissed = true; // 仅本次会话内忽略，刷新页面或重新扫描后重新评估
		updateStaleBar();
	});
	$("#btn-gen").addEventListener("click", doGenerate);
	$("#btn-stopgen").addEventListener("click", () => {
		stopFlag = true;
	});
	$("#btn-settings").addEventListener("click", openSettings);
	$("#btn-backup").addEventListener("click", openBackup);
	$("#btn-excluded").addEventListener("click", openExcluded);

	// 搜索：debounce 150ms + ✕ 清除 + Ctrl+K 聚焦
	const search = $("#search");
	const clearBtn = $("#search-clear");
	search.addEventListener("input", () => {
		clearBtn.style.display = search.value ? "block" : "none";
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			searchQuery = search.value;
			render();
		}, 150);
	});
	search.addEventListener("keydown", (e) => {
		if (e.key === "Escape") {
			search.value = "";
			searchQuery = "";
			clearBtn.style.display = "none";
			render();
		}
		if (e.key === "Enter") {
			const first = $("#tree .node-row");
			if (first) first.click();
		}
	});
	clearBtn.addEventListener("click", () => {
		search.value = "";
		searchQuery = "";
		clearBtn.style.display = "none";
		render();
		search.focus();
	});

	// 全局快捷键：Ctrl+K 或 / 聚焦搜索
	document.addEventListener("keydown", (e) => {
		if ((e.ctrlKey || e.metaKey) && e.key === "k") {
			e.preventDefault();
			search.focus();
		}
		if (e.key === "/" && document.activeElement !== search) {
			if (!$("#modal-root").classList.contains("hidden")) return;
			e.preventDefault();
			search.focus();
		}
	});

	// ⋯ 菜单外围点击关闭：常驻监听，点击目标在打开的菜单外即收起
	document.addEventListener("click", (ev) => {
		if (!openMoreMenu) return;
		if (openMoreMenu.contains(ev.target)) return; // 菜单内部点击交给菜单项
		closeOpenMoreMenu();
	});

	// 行级右键菜单：点击其它处 / 在非树行处右键 时关闭
	document.addEventListener("click", () => closeRowMenu());
	document.addEventListener("contextmenu", (ev) => {
		if (!ev.target.closest(".node-row")) closeRowMenu();
	});

	// 详情路径点击复制
	$("#detail").addEventListener("click", (e) => {
		if (e.target.tagName === "CODE") {
			navigator.clipboard
				.writeText(e.target.textContent)
				.then(() => toast("已复制路径"))
				.catch(() => toast("复制失败", true));
		}
	});

	// 启动后跟随后台自动扫描：扫完自动刷新索引
	watchStartupScan();
}

init();
