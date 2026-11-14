// Walks an exercise against a running demo in a headless browser and saves screenshots into figures/.
// Usage: NODE_PATH=<dir containing the playwright package> node scripts/shots.cjs [base-url] [beacon|scout|mayday]
//   beacon (default; "ui" means the same): phones located by the Beacon module  -> figures/ui_beacon_*.png
//   scout: the simulated radar scan                                              -> figures/ui_scout_*.png
//   mayday: the original crews exercise                                          -> figures/ui_mayday_*.png
// Writes results/ui_walk.json (one entry per exercise under "runs"; the top-level fields are the latest run).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.argv[2] || "http://127.0.0.1:1810";
const EX = { ui: "beacon", beacon: "beacon", scout: "scout", mayday: "mayday" }[process.argv[3] || "beacon"] || "beacon";
const MODE = { beacon: "beacon", scout: "scout", mayday: "crews" }[EX];
// Sponsor data never reaches the public tree: when any layer of the server's bundle is live, screenshots and the
// walk's results go to data/live/ (git-ignored) instead of figures/ and results/.
let OUT = path.join(__dirname, "..", "figures");
let RESULTS = path.join(__dirname, "..", "results", "ui_walk.json");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const get = async (p) => (await fetch(BASE + p)).json();
const post = (p, b = {}) => fetch(BASE + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }).then((r) => r.json().catch(() => ({})));
const unit = (s, id) => (s.units || []).find((u) => u.id === id) || {};

async function waitFor(cond, what, timeout = 180000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const st = await get("/api/state");
    if (cond(st)) return st;
    await sleep(250);
  }
  console.log("timed out waiting for", what);
  missed.push(what);
  return get("/api/state");
}
const missed = [];

(async () => {
  const bundle = await get("/api/bundle");
  const live = ["costar", "matterport", "vexcel"].filter((k) => bundle.labels?.[k]?.mode === "live");
  if (live.length) {
    OUT = path.join(__dirname, "..", "data", "live", "figures");
    RESULTS = path.join(__dirname, "..", "data", "live", "ui_walk.json");
    console.log(`live sponsor layers (${live.join(", ")}): writing to data/live/ only`);
  }
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const errors = [];
  const watch = (pg) => {
    pg.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") errors.push(`${m.type()}: ${m.text()}`); });
    pg.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  };
  watch(page);
  const saved = [];
  const shot = async (name, pg = page) => { const f = `ui_${EX}_${name}.png`; await pg.screenshot({ path: path.join(OUT, f) }); saved.push(f); console.log("saved", f); };
  const phone = async (name) => {
    const ph = await browser.newPage({ viewport: { width: 390, height: 844 } });
    watch(ph);
    await ph.goto(BASE + "/crew.html?unit=RIT", { waitUntil: "networkidle" });
    await sleep(2600);
    await shot(name, ph);
    await ph.close();
  };
  // clicks go through the DOM so that an open document panel (the live after-action report) cannot swallow them
  const click = (sel) => page.evaluate((q) => document.querySelector(q)?.click(), sel);
  const doc = async () => { await page.evaluate(() => { const d = document.getElementById("doc"); if (d) d.hidden = true; }); await sleep(300); };
  const view = async (v) => { await doc(); await click(`#view-seg button[data-view="${v}"]`); await sleep(1000); };

  await post("/api/reset");
  await page.goto(`${BASE}/?mode=${MODE}`, { waitUntil: "networkidle" });
  await sleep(2500);
  await shot("01_standby");
  const t0 = Date.now();
  const start = await post("/api/storyboard", { action: "start", script: EX });
  if (start.detail) console.log("storyboard:", start.detail);

  await waitFor((s) => s.phase === "operations", "pre-plan");
  await sleep(700);
  if (EX !== "beacon") await shot("02_preplan");
  if (EX === "mayday") {
    await click("#btn-preplan"); await sleep(500);
    await shot("03_preplan_document");
    await page.evaluate(() => { const h = [...document.querySelectorAll("#doc-body h3")].find((e) => /looked at/i.test(e.textContent)); if (h) h.scrollIntoView(); });
    await sleep(700);
    await shot("03b_preplan_what_the_agent_looked_at");
    await doc();
    await waitFor((s) => unit(s, "E1").status === "interior" && (unit(s, "E1").room || "").startsWith("rc"), "E1 in a corridor");
    await sleep(2500); await shot("04_engine1_inside");
    await waitFor((s) => !!s.mayday, "mayday"); await sleep(900); await shot("05_mayday");
    await waitFor((s) => unit(s, "RIT").route, "rescue route"); await sleep(1200); await shot("06_rescue_route");
    await phone("06b_crew_phone");
    await waitFor((s) => s.hazards.length > 0 && unit(s, "RIT").route && unit(s, "RIT").route.valid, "re-plan");
    await sleep(2500); await shot("07_replan_after_hazard");
    await view("2d"); await shot("08_plan_2d"); await view("3d");
    await waitFor((s) => s.phase === "egress", "contact"); await sleep(1500); await shot("09_egress");
  } else if (EX === "scout") {
    await waitFor((s) => (s.scout?.smoke || 0) > 0.3 || ["thrown", "ranging"].includes(s.scout?.phase), "Engine 1 at the door with smoke");
    await sleep(900); await shot("03_engine1_door_smoke");
    await waitFor((s) => ["ranging", "located", "registered"].includes(s.scout?.phase), "pucks ranging"); await sleep(700); await shot("04_pucks_ranging");
    await waitFor((s) => s.scout?.phase === "done", "scan landed"); await sleep(3000); await shot("05_scan_landed");
    // the smoke view on (the script's own smoke usually has it on already); then back to the script's setting
    await doc();
    const smokeOn = () => page.evaluate(() => document.getElementById("btn-smoke").getAttribute("aria-pressed") === "true");
    const was = await smokeOn();
    if (!was) await click("#btn-smoke");
    await sleep(1500); await shot("06_smoke_view");
    if (!was) { await click("#btn-smoke"); await sleep(400); }
    await waitFor((s) => (s.units || []).some((u) => u.assignment && /^V\d/.test(u.assignment.target || "")), "rescue order to the victim", 90000);
    await sleep(7000); await shot("07_orders_route");
    await phone("07b_crew_phone");
    await waitFor((s) => (s.scout?.victims || []).some((v) => ["reached", "evacuating", "out"].includes(v.status)), "victim reached", 150000);
    await sleep(1200); await doc(); await shot("08_victim_reached");
    await view("2d"); await shot("09_plan_2d"); await view("3d");
  } else {
    // the hands-free beacon storyboard on the replayed recorded agent turns: two pucks placed by hand, two phones
    const phoneOf = (s, id) => (s.phones || []).find((p) => p.id === id) || {};
    const select = async (id) => { await click(`#board .unit-row[data-unit="${id}"]`); await sleep(500); };
    await click("#btn-preplan"); await sleep(700);
    await shot("02_preplan_document");
    await doc();
    await waitFor((s) => s.beacon?.pucks_in, "two pucks placed by hand", 120000);
    await sleep(300); await shot("03_pucks_placed_by_hand");
    await waitFor((s) => (s.beacon?.fixes || 0) > 0, "B1 located: the twin rules out the mirror place", 90000);
    await sleep(1500); await select("B1");
    await shot("04_two_puck_moment_3d");
    await view("2d"); await shot("05_two_puck_moment_plan"); await view("3d");
    await waitFor((s) => phoneOf(s, "B2").geometry === "candidates", "B2: two places", 90000);
    await sleep(900); await select("B2"); await shot("06_b2_cannot_choose");
    await waitFor((s) => unit(s, "E1").assignment?.target === "B2", "the order to search for B2", 90000);
    await sleep(2500); await shot("07_order_to_search");
    const ph = await browser.newPage({ viewport: { width: 390, height: 844 } }); watch(ph);
    await ph.goto(BASE + "/crew.html?unit=E1", { waitUntil: "networkidle" }); await sleep(2600); await shot("07b_crew_phone_engine1", ph); await ph.close();
    await waitFor((s) => /Rescue team goes to B1/.test(s.storyboard?.step || ""), "the rescue team on its way", 90000);
    await sleep(4500); await select("B1"); await shot("08_rescue_route_radio");
    await waitFor((s) => /nobody at the first point/.test(s.storyboard?.step || ""), "the search result", 150000);
    await sleep(1600); await select("B2"); await shot("09_search_result_b2_point");
    await view("2d"); await shot("10_accountability_and_plan"); await view("3d");
  }
  await waitFor((s) => s.phase === "terminated", "all out", 200000);
  for (let i = 0; i < 120; i++) { if (await get("/api/aar")) break; await sleep(500); }
  await sleep(1800);
  const wall = Number(((Date.now() - t0) / 1000).toFixed(1));
  console.log("storyboard wall time (s):", wall);
  if (EX === "beacon") {
    await doc(); await shot("11_all_out");
    await page.evaluate(() => document.getElementById("btn-aar")?.click()); await sleep(700);
    await shot("12_after_action");
    await doc();
    await page.setViewportSize({ width: 1280, height: 720 }); await sleep(900);
    await shot("13_1280x720");
    // the board's control for tape-measured pucks (the storyboard places them server-side): opened, two clicks, not sent
    await page.setViewportSize({ width: 1600, height: 900 }); await sleep(600);
    await click("#btn-place"); await sleep(700);
    const box = await page.locator("#plan").boundingBox();
    await page.mouse.click(box.x + box.width * 0.42, box.y + box.height * 0.6); await sleep(250);
    await page.mouse.click(box.x + box.width * 0.56, box.y + box.height * 0.38); await sleep(500);
    await shot("14_place_pucks_control");
    await click("#pp-cancel");
  } else {
    await page.evaluate(() => document.getElementById("btn-aar")?.click()); await sleep(600);
    await shot("10_after_action");
    await doc(); await shot("11_terminated");
    await page.setViewportSize({ width: 1280, height: 720 }); await sleep(900);
    await shot("12_1280x720");
  }
  const issues = [...new Set(errors)].slice(0, 20);
  console.log(issues.length ? "console issues:\n  " + issues.join("\n  ") : "no console errors");
  let all = {};
  try { all = JSON.parse(fs.readFileSync(RESULTS, "utf8")); } catch { /* first run */ }
  const run = { exercise: EX, base: "localhost", sponsor_layers_live: live, storyboard_wall_s: wall, console_issues: issues, waits_timed_out: missed, figures: saved, at: new Date().toISOString() };
  all = { ...run, runs: { ...(all.runs || {}), [EX]: run } };
  fs.writeFileSync(RESULTS, JSON.stringify(all, null, 1));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
