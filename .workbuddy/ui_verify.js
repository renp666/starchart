/* 星图趋势面板 UI 验证：用系统 Edge 有头打开 → 点击 🔥 → 截图 */
const path = require("path");
(async () => {
  const { chromium } = require("playwright");
  const browser = await chromium.launch({
    channel: "msedge",          // 复用系统 Edge，不下载浏览器
    headless: false,            // 有头模式，肉眼可见
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
  page.on("console", (m) => { if (m.type() === "error") console.log("[console.error]", m.text()); });
  page.on("pageerror", (e) => console.log("[pageerror]", e.message));

  await page.goto("http://127.0.0.1:6173/", { waitUntil: "networkidle", timeout: 30000 });
  console.log("title:", await page.title());

  await page.click("#btn-trending");
  await page.waitForSelector(".trend-card", { timeout: 60000 });
  await page.waitForTimeout(800);
  const cards = await page.locator(".trend-card").count();
  console.log("trend cards:", cards);
  console.log("modal h3:", await page.locator("#modal-root h3").innerText());
  const first = page.locator(".trend-card").first();
  console.log("first card:", (await first.locator(".tc-name").innerText()).trim());
  console.log("first intro:", (await first.locator(".tc-intro").count()) ? await first.locator(".tc-intro").innerText() : "(无缓存介绍)");

  await page.screenshot({ path: path.join(__dirname, "ui_trend.png") });
  console.log("screenshot saved: ui_trend.png");

  // 顺手验证「本期速览」按钮存在且可点出区块（不真点，避免触发 LLM）
  console.log("digest btn:", await page.locator("#tr-digest").count());
  console.log("lang options:", await page.locator("#tr-lang option").count());
  await browser.close();
  console.log("UI VERIFY PASS");
})().catch((e) => { console.error("VERIFY FAIL:", e.message); process.exit(1); });
