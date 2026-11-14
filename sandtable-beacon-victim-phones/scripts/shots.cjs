// Takes the UI screenshots in figures/ against a running Beacon server, walking ring -> candidates -> fix.
//
//   node scripts/shots.cjs [base-url]            (default http://127.0.0.1:1885)
//
// Needs the `playwright` package and its Chromium. If `require("playwright")` fails, set NODE_PATH to a directory
// that contains it (for example the one `npx playwright --version` installs into). The script also tries the
// npx cache by itself. With the simulator (`--source sim`) it places pucks and a simulated phone itself, derived
// from the twin (no hard-coded coordinates); with any other source it only takes the board as it is.
const fs = require("fs");
const os = require("os");
const path = require("path");

function loadPlaywright() {
  try { return [require("playwright")]; } catch { /* look in the npx cache */ }
  const out = [], root = path.join(os.homedir(), ".npm", "_npx");
  try { for (const d of fs.readdirSync(root)) { const p = path.join(root, d, "node_modules", "playwright"); if (fs.existsSync(p)) out.push(require(p)); } } catch { /* none */ }
  return out;
}
const BASE = (process.argv[2] || "http://127.0.0.1:1885").replace(/\/$/, "");
const OUT = path.join(__dirname, "..", "figures");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const get = async (p) => { const r = await fetch(BASE + p); if (!r.ok) throw new Error(`${p}: HTTP ${r.status}`); return r.json(); };
const post = async (p, b) => { const r = await fetch(BASE + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b || {}) }); const t = await r.text(); if (!r.ok) throw new Error(`${p}: HTTP ${r.status} ${t.slice(0, 200)}`); return t ? JSON.parse(t) : {}; };

async function waitFor(cond, what, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const b = await get("/api/beacons");
    if (cond(b)) return b;
    await sleep(300);
  }
  console.log(`  (timed out waiting for ${what})`);
  return get("/api/beacons");
}
const phoneOf = (b, key) => (b.phones || []).find((p) => p.id === key) || null;

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  let browser = null;
  for (const pw of loadPlaywright()) { try { browser = await pw.chromium.launch(); break; } catch (e) { /* try the next copy */ } }
  if (!browser) { console.error("Playwright with a Chromium build was not found: npx playwright install chromium, then set NODE_PATH."); process.exit(2); }
  const health = await get("/healthz");
  const sim = health.source === "sim";
  console.log(`source: ${health.source}${health.sample ? " (SAMPLE)" : ""}${health.simulated ? ", simulated" : ""}`);
  const errors = [];
  const watch = (page) => {
    page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
    page.on("console", (m) => { if (m.type() === "error" && !/404/.test(m.text())) errors.push(m.text()); });
  };
  const board = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  watch(board);
  const shot = async (page, name) => { await page.screenshot({ path: path.join(OUT, `ui_${name}.png`) }); console.log("saved", `figures/ui_${name}.png`); };

  if (sim) {
    await post("/api/reset");
    await board.goto(BASE + "/", { waitUntil: "load" });
    await sleep(2500);
    await shot(board, "00_no_pucks");

    // the two-puck walk-through (beacon/story.py): ring -> careless second puck, cannot choose -> puck slid to the wall, fix
    await post("/api/sim/story", { action: "start", script: "suite-310" });
    const story = async () => ((await get("/api/sim/story")).story || {});
    const stepAnd = async (step, geometry, what, timeout = 40000) => {
      const end = Date.now() + timeout;
      while (Date.now() < end) {
        const st = await story();
        if (st.error) { console.log("  story error:", st.error); return false; }
        if (st.step > step) { console.log(`  (missed: ${what})`); return false; }
        if (st.step === step) {
          const b = await get("/api/beacons");
          if (!geometry || (b.phones || []).some((p) => p.geometry === geometry)) return true;
        }
        await sleep(250);
      }
      console.log(`  (timed out waiting for ${what})`);
      return false;
    };
    if (await stepAnd(2, "ring", "a ring")) { await sleep(2200); await shot(board, "01_ring"); }
    if (await stepAnd(3, "candidates", "two candidates")) { await sleep(2200); await shot(board, "02_cannot_choose"); }
    if (await stepAnd(4, "fix", "a fix from two pucks")) { await sleep(2600); await shot(board, "03_fix_two_pucks"); }
    if (await stepAnd(7, null, "the accountability step", 60000)) {
      await sleep(3000);
      await board.hover("table.acct tbody tr.r1").catch(() => {});
      await shot(board, "04_accountability");
    }
    const end = Date.now() + 100000;
    while (Date.now() < end) { const st = await story(); if (st.done || !st.running) break; await sleep(1000); }
    const agent = await get("/api/agent").catch(() => null);
    if (agent && agent.available !== false && agent.plan) { await sleep(1500); await shot(board, "06_agent"); }
    await board.click('#focus-seg button[data-focus="floor"]');
    await sleep(1500);
    await shot(board, "07_floor");
    await board.click('#focus-seg button[data-focus="room"]');
    await board.click("#btn-layout");
    await sleep(2000);
    await shot(board, "09_layout");
    await board.click("#btn-layout");
  } else {
    await board.goto(BASE + "/", { waitUntil: "load" });
    await sleep(4000);
    await shot(board, "01_board");
  }

  // the projector check
  const small = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  watch(small);
  await small.goto(BASE + "/", { waitUntil: "load" });
  await sleep(3000);
  await shot(small, "08_1280x720");

  // the simulated phone at phone width
  if (sim) {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    const phone = await ctx.newPage();
    watch(phone);
    await phone.goto(BASE + "/phone.html", { waitUntil: "load" });
    await sleep(1500);
    await phone.click('.st[data-st="trapped"]');
    await phone.click("#ppl-plus");
    await phone.click('[data-move="no"]');
    await phone.click("#chips button");
    await sleep(4000);
    await shot(phone, "05_phone");
    await phone.screenshot({ path: path.join(OUT, "ui_05b_phone_full.png"), fullPage: true });
    console.log("saved figures/ui_05b_phone_full.png");
    await post("/api/sim/phone", { key: await phone.evaluate(() => JSON.parse(sessionStorage.getItem("beacon.phone.key"))), remove: true }).catch(() => {});
  }
  await browser.close();
  if (errors.length) { console.log("page errors:"); for (const e of errors) console.log("  " + e); }
})().catch((e) => { console.error(e); process.exit(1); });
