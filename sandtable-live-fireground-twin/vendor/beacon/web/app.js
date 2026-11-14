// Sandtable · Beacon board: consumes the Beacon feed (docs/BEACON_FEED.md) over the WebSocket, falls back to polling,
// or reads a static sample with ?sample=1 (marked SAMPLE). Draws only what the feed publishes.
import { $, esc, num, isNum, getJSON, postJSON, statusOf, peopleWords, methodWords, geometryWords, chipWords, bearingWords, floorWords, stillWords, clockOf, noiseLine, GEOMETRY_SHORT } from "./common.js";
import { Plan } from "./plan.js";

const params = new URLSearchParams(location.search);
const S = { mode: params.get("sample") ? "sample" : "ws", hello: null, beacons: null, bundle: null, events: [], conn: "connecting", selected: null, geomSeen: {}, subject: null, placing: null, rig: null };
const MAX_PUCKS = 5;
let plan = null;

// the ± after a fix is the locator's model, not a measurement of error: say so on hover
const sigmaNote = () => `1 sigma, from the locator's model${simulated() ? "; simulated" : ""}`;
// set innerHTML only when it changed, so hover, focus and running animations survive the 4-per-second updates
function setHTML(el, html) { if (el._html !== html) { el.innerHTML = html; el._html = html; return true; } return false; }
const liveFix = (p) => p.geometry === "fix" && !p.stale && !(p.report && p.report.safe_outside);
function toast(text) { const t = $("toast"); t.textContent = text; t.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 4200); }
const isSample = () => S.mode === "sample" || !!(S.beacons && S.beacons.sample) || !!(S.hello && S.hello.sample) || (S.beacons && S.beacons.source === "fake") || (S.hello && S.hello.source === "fake");
const simulated = () => (S.beacons ? S.beacons.simulated !== false : S.hello ? S.hello.simulated !== false : true);

// ---------------------------------------------------------------- bundle and static text
async function loadBundle() {
  try { return await getJSON("/api/bundle"); }
  catch {
    // a plain static server (development, ?sample=1): read the building fixtures directly
    const [twin, site] = await Promise.all([getJSON("/fixtures/building/twin.json"), getJSON("/fixtures/building/site.json").catch(() => ({ tenants: [] }))]);
    return { twin, site, labels: null, config: {}, _static: true };
  }
}
function renderStatic() {
  const { site, twin } = S.bundle;
  const p = (site && site.property) || {};
  $("address").textContent = [p.address, p.city, p.state].filter(Boolean).join(", ") + (p.name ? ` · ${p.name}` : "");
  const fl = twin.floors.find((f) => f.id === (S.beacons?.floor || twin.exercise_floor));
  $("plan-src").textContent = `floor ${fl ? fl.label : "?"} · ${twin.source?.synthetic ? "synthetic twin fitted to the real footprint (not a Matterport scan)" : "twin"}`;
  renderFooter();
}
function renderFooter() {
  const labels = (S.hello && S.hello.labels) || (S.bundle && S.bundle.labels) || null;
  const parts = [];
  if (labels) {
    for (const [k, v] of Object.entries(labels)) if (v && typeof v === "object" && !Array.isArray(v) && v.text) parts.push(esc(v.text));
    const od = (labels.open_data || []).map((o) => o.name).filter(Boolean);
    if (od.length) parts.push(`<span title="${esc(od.join("; "))}" style="text-decoration: underline dotted; cursor: help">${od.length} named open sources</span>`);
  } else parts.push("Building record and twin: fixtures from this repository; the server's honesty labels are not available from this source");
  const b = S.beacons;
  parts.push(simulated() ? "Pucks and phones: simulated" : "Pucks and phones: live");
  parts.push("Cold counts are devices, not people");
  if (b && b.privacy && b.privacy.text) parts.push(`<span class="priv">Privacy: ${esc(b.privacy.text)}</span>`);
  $("foot").innerHTML = `<b>What is real here.</b> ` + parts.join(`<span class="sep">|</span>`);
}

// ---------------------------------------------------------------- top bar
function renderTop() {
  const b = S.beacons, h = S.hello;
  const badges = [];
  if (simulated()) badges.push(`<span class="badge sim" title="${esc(noiseLine(h && h.noise) ? "Stated noise model: " + noiseLine(h.noise) : "Simulated: no hardware in the loop")}">SIM</span>`);
  else if ((b && b.source) === "replay") badges.push(`<span class="badge live" title="A replay of a recording made with real devices">REPLAY</span>`);
  else badges.push(`<span class="badge live" title="Real phones reporting through the occupant app">LIVE</span>`);
  if (isSample()) badges.push(`<span class="badge sample" title="A hand-made sample of the feed, not a locator result">SAMPLE</span>`);
  const cn = { up: ["up", "connected"], poll: ["up", "polling"], down: ["down", "disconnected: retrying"], connecting: ["", "connecting"], static: ["", "static sample file"] }[S.conn] || ["", S.conn];
  badges.push(`<span class="badge conn ${cn[0]}" title="Connection to the Beacon server">${esc(cn[1])}</span>`);
  const src = (b && b.source) || (h && h.source);
  if (src) badges.push(`<span class="badge" title="Where the observations come from">source: ${esc(src)}</span>`);
  const scale = Number((b && b.rig && b.rig.scale) ?? (h && h.rig && h.rig.scale) ?? (S.rig && S.rig.scale) ?? 1);
  if (isNum(scale) && Math.abs(scale - 1) > 1e-6) badges.push(`<span class="badge scale" title="Building metres per metre of the physical demo area">exercise scale 1 : ${esc(+scale.toFixed(2))}</span>`);
  $("badges").innerHTML = badges.join("");
  const sel = $("scale-sel");
  if (document.activeElement !== sel) {
    const v = String(+scale.toFixed(2));
    if (![...sel.options].some((o) => o.value === v)) sel.add(new Option(`1 : ${v}`, v));
    sel.value = v;
  }
  $("modes-note").textContent = isSample()
    ? "A hand-made sample of the feed is playing: shapes by example, not a locator result."
    : simulated()
      ? `Pucks and phones are simulated: nothing on this screen comes from a real device.${noiseLine(h && h.noise) ? " Noise model: " + noiseLine(h.noise) + "." : ""}`
      : "Ranges come from real phones reporting through the occupant app.";
  const ph = $("phase");
  ph.textContent = b && b.room_label ? b.room_label.toUpperCase() : "NO PUCKS";
  ph.classList.toggle("idle", !(b && b.room_label));
  $("clock").textContent = clockOf(b && b.t);
  const n = (b && b.pucks || []).length;
  const sampleOnly = S.mode === "sample" || isSample();
  $("btn-place2").disabled = sampleOnly;
  const add = $("btn-add");
  add.disabled = n >= MAX_PUCKS || sampleOnly;
  add.textContent = n >= MAX_PUCKS ? `All ${MAX_PUCKS} pucks in` : n ? `Add a puck (${n + 1})` : "Add a puck";
  add.title = simulated() ? `One more simulated puck (POST /api/pucks {"n": ${n + 1}})` : "Live: place real pucks from the tray on the plan";
  $("btn-search").disabled = !(b && b.room) || sampleOnly;
  const sr = b && b.search_room;
  $("btn-search").setAttribute("aria-pressed", sr && sr.room ? "true" : "false");
  $("btn-story").disabled = S.mode === "sample" || !simulated();
  $("btn-reset").disabled = S.mode === "sample";
}

// ---------------------------------------------------------------- plan overlays: caption, empty states, tray
function pickSubject(phones) {
  const changed = [];
  for (const p of phones) {
    const prev = S.geomSeen[p.id];
    if (prev !== p.geometry) {
      if (p.geometry !== "none" && !(p.report && p.report.safe_outside)) changed.push(p);
      S.geomSeen[p.id] = p.geometry;
      const card = document.querySelector(`.pcard[data-id="${CSS.escape(p.id)}"]`);
      if (card && prev !== undefined) { card.classList.remove("flash"); void card.offsetWidth; card.classList.add("flash"); }
    }
  }
  if (changed.length) {
    changed.sort((a, c) => (GEO_RANK[a.geometry] ?? 5) - (GEO_RANK[c.geometry] ?? 5) || (SEV[a.report?.status] ?? 3) - (SEV[c.report?.status] ?? 3));
    S.subject = changed[0].id;
  }
  const live = phones.filter((p) => !(p.report && p.report.safe_outside) && p.geometry !== "none" && !p.stale);
  if (S.selected && live.some((p) => p.id === S.selected)) return live.find((p) => p.id === S.selected);
  return live.find((p) => p.id === S.subject) || live[0] || null;
}
function renderCaption(phones) {
  const p = pickSubject(phones), cap = $("caption"), was = cap.hidden;
  if (!p) { cap.hidden = true; if (!was) plan.setFocusMode(plan.focusMode); return; }
  const st = statusOf(p.report);
  let x = "";
  const g = p.geometry;
  if (g === "ring" && p.ring) {
    const pruned = p.ring.arcs && !(p.ring.arcs.length === 1 && Math.abs(p.ring.arcs[0][1] - p.ring.arcs[0][0]) >= 359);
    x = p.ring.kind === "ble"
      ? `Bluetooth signal strength only: a band metres wide around ${esc(p.ring.puck)}. No point can honestly be drawn.`
      : `One puck hears it: ${num(p.ring.r_m)} m from ${esc(p.ring.puck)}, direction unknown.${pruned ? " The twin rules out the dashed part of the ring (outside the building or inside a wall)." : ""}`;
  } else if (g === "candidates") {
    const n = (p.candidates || []).length, pr = (p.pruned || []).length;
    x = `Cannot choose: ${n} possible spots${n === 2 ? ", mirror images across the line between the pucks" : ""}.${pr ? ` ${pr} more ruled out.` : ""} A puck slid to a wall, or another puck, picks one.`;
  } else if (g === "fix") {
    const nP = (p.ranges || []).length;
    x = `Located${p.room_label ? ` in ${esc(p.room_label)}` : ""} to ±${num(p.sigma_m)} m: ${p.method === "uwb-twr+bearing" ? "a range plus the phone's own direction to the puck" : p.method === "ble-rssi" ? "Bluetooth signal strength from several pucks" : nP ? `UWB ranges from ${nP} pucks` : "UWB ranges"}.`;
  } else if (g === "zone" && p.zone) {
    x = `Bluetooth only: probably in ${esc(p.zone.room_label || "this room")} (${esc(p.zone.band)} signal band). No point is drawn.`;
  }
  if (p.stale) x += ` Not heard for ${num(p.track && p.track.last_seen_s, 0)} s: the last known shape, growing until the phone is heard again.`;
  const before = cap.offsetHeight;
  if ((p.assumptions || []).length) x += ` <span style="color:#ffcf8a">Assumption: ${esc(plan.assumptionTag(p.assumptions[0]))}.</span>`;
  if (p.conflict) x += ` <span style="color:#ffb4ae">${esc(typeof p.conflict === "string" ? p.conflict : p.conflict.text || "")}</span>`;
  cap.title = g === "fix" ? `±${num(p.sigma_m)} m: ${sigmaNote()}` : "";
  cap.innerHTML = `<div class="t"><span style="color:var(--phone-soft)">${esc(p.id)}</span><span style="color:${st.color}">${esc(st.word.toUpperCase())}</span><span style="font-family:var(--sans);font-weight:500;letter-spacing:0;font-size:14px">${esc(peopleWords(p.report))}</span></div><div class="x">${x}</div>`;
  cap.hidden = false;
  if (was || Math.abs(cap.offsetHeight - before) > 12) plan.setFocusMode(plan.focusMode);
}
function renderCue(b) {
  const cue = $("cue");
  if (!b) {
    cue.className = "cue"; cue.hidden = false;
    cue.innerHTML = `<h3>Connecting to the Beacon server</h3><p>The plan fills in as soon as the first message arrives.</p>`;
    return;
  }
  const pucks = b.pucks || [], phones = (b.phones || []).filter((p) => !(p.report && p.report.safe_outside));
  const heard = (b.presence || []).some((q) => (q.count || 0) > 0 || Object.values(q.zones || {}).some((v) => v > 0));
  if (!pucks.length) {
    cue.className = "cue"; cue.hidden = false;
    cue.innerHTML = `<h3>No pucks in the building yet</h3><p>Put two pucks into the fire room. They listen for phones running the occupant app, and count the Bluetooth devices around them without identifying any. Where they go decides whether two pucks give one answer: “Where to put 2 pucks” shows it.</p>` +
      `<div class="acts">${S.mode === "sample" ? "" : `<button class="primary" data-act="place2">Place 2 pucks</button><button data-act="story">Run walk-through</button>`}<a class="btn" href="phone.html" target="_blank" rel="noopener">Open simulated phone</a></div>` +
      ((b.unplaced_pucks || []).length ? `<p class="fine">Pucks are heard but not placed: pick one in the tray, then click the plan where it lies.</p>` : "");
  } else if (!phones.length && !heard) {
    const ids = pucks.map((p) => p.id).join(", ");
    cue.className = "cue low"; cue.hidden = false;
    cue.innerHTML = `<h3>${esc(ids)} listening${b.room_label ? ` in ${esc(b.room_label)}` : ""}</h3><p>Nothing heard yet: no app beacon and no Bluetooth device in range. Silence is weak evidence of an empty room, never proof.</p>` +
      `<div class="acts"><a class="btn" href="phone.html" target="_blank" rel="noopener">Open simulated phone</a></div>`;
  } else cue.hidden = true;
  for (const btn of cue.querySelectorAll("[data-act]")) btn.onclick = () => (btn.dataset.act === "place2" ? placeTwo() : runStory());
}
function renderTray(b) {
  const list = (b && b.unplaced_pucks) || [], tray = $("tray");
  if (!list.length) { tray.hidden = true; if (S.placing) { S.placing = null; plan.setPlacing(false); } return; }
  if (S.placing && !list.some((q) => q.accessory === S.placing.accessory)) { S.placing = null; plan.setPlacing(false); }
  tray.hidden = false;
  tray.innerHTML = `<div class="k">Pucks heard, not placed yet</div>` + list.map((q) =>
    `<button data-acc="${esc(q.accessory)}" class="${S.placing && S.placing.accessory === q.accessory ? "on" : ""}" title="Heard by ${esc(q.heard_by)} phone(s), ${num(q.last_seen_s)} s ago"><span>${esc(q.accessory)}</span><span>→ ${esc(q.suggested_id || "?")}</span></button>`).join("") +
    `<div class="hint">${S.placing ? `Click the plan where ${esc(S.placing.suggested_id || "the puck")} lies. Esc cancels.` : "Pick one, then click the plan where it lies."}</div>`;
  for (const btn of tray.querySelectorAll("button")) btn.onclick = () => {
    const q = list.find((r) => r.accessory === btn.dataset.acc);
    S.placing = S.placing && S.placing.accessory === q.accessory ? null : q;
    plan.setPlacing(!!S.placing);
    renderTray(S.beacons);
  };
}

// ---------------------------------------------------------------- phone cards
const SEV = { trapped: 0, injured: 1, sheltering: 2, unknown: 3, ok: 4 };
const GEO_RANK = { fix: 0, candidates: 1, ring: 2, zone: 3, none: 4 };
function cardHtml(p) {
  const r = p.report || {};
  const bat = isNum(r.battery_pct) ? `<span class="pc-bat" title="Battery"><span class="bat${r.battery_pct <= 15 ? " low" : ""}"><i style="width:${Math.max(0, Math.min(100, r.battery_pct)) * 0.18}px"></i></span>${Math.round(r.battery_pct)}%</span>` : "";
  const top = `<div class="pc-top"><span class="pc-id">${esc(p.id)}</span><span class="pc-st">${esc(statusOf(r).word)}</span><span class="pc-ppl">${esc(peopleWords(r))}</span>${bat}</div>`;
  if (p.stale) {
    // not heard: no position is claimed; say where it was last located and how long ago
    const lk = p.last_known || (p.geometry && p.geometry !== "none" ? { geometry: p.geometry, room_label: p.room_label || (p.zone && p.zone.room_label) } : {});
    const age = p.track && isNum(p.track.last_seen_s) ? p.track.last_seen_s : lk.age_s;   // time since it was last heard
    const where = lk.room_label ? `last located in ${lk.room_label}${lk.geometry && lk.geometry !== "fix" ? ` (${lk.geometry === "candidates" ? "two possible spots" : lk.geometry === "ring" ? "range only" : lk.geometry})` : ""}` : "no position before it went quiet";
    return top + `<div class="pc-stale">Not heard for ${num(age, 0)} s; ${esc(where)}</div>` + (r.text ? `<div class="pc-quote">“${esc(r.text)}”</div>` : "");
  }
  const lastHeard = isNum(p.track?.last_seen_s) ? (p.track.last_seen_s < 1.5 ? "heard just now" : `heard ${Math.round(p.track.last_seen_s)} s ago`) : null;
  const meta = [floorWords(p), stillWords(r), chipWords(r), p.method && p.method.startsWith("uwb") || r.chip ? bearingWords(p) : null,
    r.home_suite ? `says: suite ${r.home_suite}` : null, lastHeard].filter(Boolean);
  const chip = (q) => `<span class="rg ${q.kind}" title="${q.kind === "uwb" ? "UWB two-way ranging" : "Bluetooth signal strength"} to ${esc(q.puck)}: ${num(q.d_m, 2)} m, model 1σ ${num(q.sigma_m, 2)} m${simulated() ? " (simulated)" : ""}${isNum(q.rssi) ? `, ${q.rssi} dBm` : ""}${isNum(q.los) ? `, line of sight ${num(q.los)}` : ""}">${esc(q.puck)} ${num(q.d_m)} m <i>±${num(q.sigma_m, q.kind === "uwb" ? 2 : 1)}</i></span>`;
  const byPuck = (a, c) => String(a.puck).localeCompare(String(c.puck));
  const rU = (p.ranges || []).filter((q) => q.kind === "uwb").sort(byPuck), rB = (p.ranges || []).filter((q) => q.kind !== "uwb").sort(byPuck);
  const ranges = (rU.length ? `<span class="rk">UWB</span>${rU.map(chip).join("")}` : "") + (rB.length ? `<span class="rk">Bluetooth</span>${rB.map(chip).join("")}` : "");
  const stated = (q) => q.by === "occupant-statement" || q.by === "search-room";
  const prunedList = [...(p.pruned || [])].sort((a, c) => (stated(c) - stated(a)) || (a.x - c.x) || (a.y - c.y));
  const pruned = prunedList.map((q) => stated(q) ? `<div class="pc-dismissed">Dismissed on a statement: ${esc(q.reason || "")}</div>` : `<div class="pc-pruned">Ruled out by the twin: ${esc(q.reason || "removed by the twin")}</div>`).join("");
  const choose = p.geometry === "candidates" ? `<div class="pc-choose">${esc(p.text || `Two possible spots: cannot choose between them yet`)}</div>` : "";
  const assume = (p.assumptions || []).map((a) => `<div class="pc-assume" title="${esc(a.text || "")}"><b>Assumption</b>${esc(a.text || plan.assumptionTag(a))}</div>`).join("");
  const conflict = p.conflict ? `<div class="pc-conflict">${esc(typeof p.conflict === "string" ? p.conflict : p.conflict.text || JSON.stringify(p.conflict))}</div>` : "";
  const geoTitle = p.geometry === "fix" ? ` title="±${num(p.sigma_m)} m: ${esc(sigmaNote())}"` : "";
  return top +
    `<div class="pc-geo"${geoTitle}>${esc(geometryWords(p))} <span class="how">· ${esc(methodWords(p.method))} · ${esc(GEOMETRY_SHORT[p.geometry] || p.geometry)}</span></div>` +
    choose + assume + conflict +
    (r.text ? `<div class="pc-quote">“${esc(r.text)}”</div>` : "") +
    `<div class="pc-meta">${meta.map((m) => `<span>${esc(m)}</span>`).join("")}</div>` +
    (ranges ? `<div class="pc-ranges">${ranges}</div>` : "") + pruned +
    (p.text && S.selected === p.id && p.geometry !== "candidates" ? `<div class="pc-line">${esc(p.text)}</div>` : "");
}
function renderPhones(b) {
  const all = (b && b.phones) || [], box = $("phones");
  const inside = all.filter((p) => !(p.report && p.report.safe_outside));
  const out = all.filter((p) => p.report && p.report.safe_outside);
  // a stable order: heard phones before stale ones, then the reported status, then the id; never the geometry, which changes
  inside.sort((a, c) => (!!a.stale - !!c.stale) || (SEV[a.report?.status] ?? 3) - (SEV[c.report?.status] ?? 3) || String(a.id).localeCompare(String(c.id), undefined, { numeric: true }));
  const heard = inside.filter((p) => !p.stale), ppl = heard.reduce((s, q) => s + (isNum(q.report?.people) ? q.report.people : 0), 0);
  $("phones-par").textContent = inside.length ? `${heard.length} heard · ${ppl} people reported${inside.length > heard.length ? ` · ${inside.length - heard.length} not heard` : ""}` : "";
  if (!all.length) {
    setHTML(box, `<div class="empty"><b>No opt-in phone heard</b>A phone appears here only when its owner runs the occupant app and sends an SOS beacon. Phones without the app are counted on the plan as devices, never listed.</div>`);
    return;
  }
  if (box.querySelector(":scope > .empty")) { box.innerHTML = ""; box._html = null; }
  box._html = null;
  const existing = new Map([...box.querySelectorAll(":scope > .pcard")].map((e) => [e.dataset.id, e]));
  let prev = null;
  for (const p of inside) {
    let e = existing.get(p.id);
    if (!e) { e = document.createElement("div"); e.dataset.id = p.id; e.onclick = () => { selectPhone(p.id === S.selected ? null : p.id); renderPhones(S.beacons); }; }
    existing.delete(p.id);
    setHTML(e, cardHtml(p));
    e.classList.add("pcard"); e.classList.toggle("stale", !!p.stale); e.classList.toggle("sel", S.selected === p.id);
    e.style.setProperty("--st", p.stale ? "#5d6875" : statusOf(p.report).color);
    const next = prev ? prev.nextSibling : box.firstChild;
    if (next !== e) box.insertBefore(e, next);
    prev = e;
  }
  for (const e of existing.values()) e.remove();
  let outEl = box.querySelector(":scope > .out-list");
  if (out.length) {
    if (!outEl) { outEl = document.createElement("div"); outEl.className = "out-list"; }
    setHTML(outEl, `<b>Safe outside:</b> ${out.map((p) => `${esc(p.id)}${isNum(p.report?.people) ? ` (${p.report.people})` : ""}${p.report?.home_suite ? `, suite ${esc(p.report.home_suite)}` : ""}`).join("; ")} <small>pressed “I am out”: accounted for</small>`);
    box.appendChild(outEl);
  } else if (outEl) outEl.remove();
}
function selectPhone(id, scroll) {
  S.selected = id;
  plan.select(id);
  for (const c of document.querySelectorAll(".pcard")) c.classList.toggle("sel", c.dataset.id === id);
  if (id && scroll) document.querySelector(`.pcard[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  if (S.beacons) renderCaption(S.beacons.phones || []);
}

// ---------------------------------------------------------------- accountability
function renderAccountability(b) {
  const a = b && b.accountability, body = $("acct-body");
  if (!a) {
    plan.setNextPuck(null);
    setHTML(body, `<div class="empty"><b>No accountability yet</b>When the server compares the building record's tenant roster with the phones the pucks hear, each suite appears here: how many people the record suggests, how many reported, how many are still unaccounted for.</div>`);
    $("acct-floor").textContent = "";
    return;
  }
  const fl = S.bundle.twin.floors.find((f) => f.id === a.floor);
  $("acct-floor").textContent = fl ? `floor ${fl.label}` : "";
  const t = a.totals || {};
  // "located" counts phones with a fix now: a stale phone is not located, whatever it was before
  const fixes = (b.phones || []).filter(liveFix);
  const locatedIn = (s) => fixes.filter((p) => p.room && (p.room === s.room || (s.rooms || []).includes(p.room))).length;
  const tot = [["Expected", t.expected_people, "", "People the building record suggests on this floor: an estimate (hover the rows for each basis)"], ["Reported", t.people_reported, "ppl", "People reported by opt-in phones"], ["Located", fixes.length, "ppl", "Opt-in phones with a fix now (phones not heard recently are not counted)"], ["Devices", t.cold_devices, "dev", "Bluetooth devices heard with no app: devices, not people"], ["Unscanned", t.suites_unscanned, "", "Suites with no puck in range"]];
  const pr = (a.priorities || []).slice(0, 3).map((q) => `<div class="pr" title="${esc(q.text || "")}"><span class="rk">${esc(q.rank)}</span><span>${esc(q.text || `Suite ${q.suite}`)}${q.target ? `<span class="tg">${esc(q.target)}</span>` : ""}</span></div>`).join("");
  const suites = [...(a.suites || [])].sort((x, y) => (x.priority ?? 999) - (y.priority ?? 999) || String(x.suite).localeCompare(String(y.suite)));
  const rows = suites.map((s) => {
    const e = s.expected || {}, un = s.unaccounted || {}, cr = s.cold_range;
    const fire = b.room && s.room === b.room;
    const ten = s.vacant ? "vacant on record" : esc(s.tenant || s.industry || "tenant not on record");
    const app = s.app_registered ? ` · <span title="Occupants who registered this suite in the app; how many pressed I am out">${s.app_registered} app user${s.app_registered === 1 ? "" : "s"}${s.safe_outside ? `, <span style="color:var(--ok)">${s.safe_outside} safe outside</span>` : ""}</span>` : "";
    const line2 = `<span class="ten">${ten}</span>${app}${s.life_hazard ? ` · <span style="color:#ffb4ae">${esc(s.life_hazard)}</span>` : ""}${s.why ? ` — ${esc(s.why)}` : ""}`;
    const loc = locatedIn(s);
    const inside = (s.people_reported || s.phones_opt_in) ? `<span class="ppl">${esc(s.people_reported ?? 0)}</span><br><small>${esc(s.phones_opt_in ?? 0)} ph${loc ? ` · ${loc} fix` : ""}</small>` : `<small>—</small>`;
    return `<tr class="r1${fire ? " fire" : ""}" data-room="${esc(s.room || "")}">` +
      `<td class="ste">${isNum(s.priority) ? `<span class="rk">${esc(s.priority)}</span>` : ""}${esc(s.suite)}</td>` +
      `<td class="num"><span class="basis" title="${esc(e.basis ? `Estimate. ${e.basis}` : "Estimate")}">${isNum(e.people) ? "~" + e.people : "?"}</span>${isNum(e.low) ? `<br><small>${e.low}–${e.high}</small>` : ""}</td>` +
      `<td class="num" title="People reported inside by opt-in phones heard in this suite">${inside}</td>` +
      `<td class="num dev" title="Bluetooth devices heard with no app: devices, not people">${esc(s.cold_devices ?? 0)}${cr ? `<br><small>${cr[0]}–${cr[1]}</small>` : ""}</td>` +
      `<td class="num" title="Expected minus everybody accounted for, as a band">${isNum(un.low) ? `${un.low}–${un.high}` : "?"}</td>` +
      `<td class="l"><span class="pill ${esc(s.state)}">${esc(String(s.state || "").replace(/-/g, " "))}</span></td></tr>` +
      `<tr class="r2${fire ? " fire" : ""}" data-room="${esc(s.room || "")}"><td colspan="6">${line2}</td></tr>`;
  }).join("");
  const changed = setHTML(body, `<div class="totals">${tot.map(([k, v, c, tt], i) => `<div class="tot" title="${esc(tt)}"><div class="k">${esc(k)}</div><div class="v ${c}">${isNum(v) ? (i === 0 ? "~" + v : v) : "—"}</div></div>`).join("")}</div>` +
    (pr ? `<div class="prio"><div class="k">Search priorities</div>${pr}</div>` : "") +
    `<div class="tbl"><table class="acct"><thead><tr><th>Suite</th><th title="Expected people from the building record: an estimate, hover for its basis">Expected</th><th title="People reported inside">Inside</th><th title="Devices heard with no app">Devices</th><th title="Unaccounted for">Unacc.</th><th class="l">State</th></tr></thead><tbody>${rows}</tbody></table></div>` +
    checksHtml(a) +
    assumptionsHtml(a.assumptions));
  const src = a.source || {};
  $("acct-sub").textContent = src.real_sponsor_data ? "building record: CoStar (live)" : "building record: fixture, not CoStar";
  $("acct-sub").title = src.text || "";
  plan.setNextPuck(a.next_puck || null);
  if (!changed) return;
  for (const tr of body.querySelectorAll("tr[data-room]")) {
    tr.onmouseenter = () => { plan.highlightRoom(tr.dataset.room, true); for (const q of body.querySelectorAll(`tr[data-room="${CSS.escape(tr.dataset.room)}"]`)) q.classList.add("hl"); };
    tr.onmouseleave = () => { plan.highlightRoom(tr.dataset.room, false); for (const q of body.querySelectorAll("tr.hl")) q.classList.remove("hl"); };
  }
}

function assumptionsHtml(as) {
  if (!as) return "";
  if (Array.isArray(as)) return as.length ? `<div class="src-note">Assumptions: ${as.map((q) => esc(typeof q === "string" ? q : q.text || "")).join("; ")}</div>` : "";
  const detail = Object.entries(as).filter(([k]) => k !== "note").map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? JSON.stringify(v).replace(/[{}"]/g, "").replace(/,/g, ", ").replace(/:/g, ": ") : v}`).join("\n");
  return `<div class="src-note" title="${esc(detail)}">${esc(as.note || "Expected people are estimates")}${detail ? ` <span style="text-decoration: underline dotted; cursor: help">(assumptions)</span>` : ""}</div>`;
}
const npWhy = (np) => (np.why || np.text || "").replace(/^Next puck:\s*/i, "");
function checksHtml(a) {
  const list = [...(a.record_checks || []), ...((S.agent && S.agent.plan && S.agent.plan.record_checks) || [])];
  const seen = new Set(), rows = [];
  for (const c of list) { const k = `${c.suite}|${c.text}`; if (seen.has(k)) continue; seen.add(k); rows.push(c); }
  const np = a.next_puck;
  return (np ? `<div class="checks"><div class="nextp" title="${esc(npWhy(np))}"><b>Next puck:</b> ${esc(npWhy(np) || (np.suite ? "Suite " + np.suite : np.room || ""))}</div></div>` : "") +
    (rows.length ? `<div class="checks"><div class="k">Record disagrees</div>${rows.map((c) => `<div class="c">${c.suite ? `<b>${esc(c.suite)}</b>` : ""}${esc(c.text)}</div>`).join("")}</div>` : "");
}

// ---------------------------------------------------------------- capability ladder (feed section 6, v1 additions)
const RUNGS = [
  { key: "cold", title: "Cold presence", phone: "nothing installed", puck: "a Bluetooth scan", gives: "a device count band and a signal-strength zone per puck; no identity", uses: (b) => (b.presence || []).length },
  { key: "ble", title: "SOS over Bluetooth", phone: "the opt-in occupant app", puck: "Bluetooth", gives: "status, people, battery, floor; a fat ring, a zone, or a coarse fix from several pucks", uses: (b) => (b.phones || []).filter((p) => /^ble/.test(p.method || "")).length },
  { key: "uwb", title: "UWB ranging", phone: "the iOS app on an iPhone 11 or later", puck: "a DWM3001CDK with Qorvo's stock Nearby Interaction firmware", gives: "thin rings; two candidates from two pucks; a fix from three, or from one plus the phone's direction", uses: (b) => (b.phones || []).filter((p) => /^uwb/.test(p.method || "")).length },
];
function renderLadder(b) {
  const rungs = (b && b.rungs) || (S.hello && S.hello.rungs) || {};
  $("rungs").innerHTML = RUNGS.map((r) => {
    const avail = rungs[r.key] === true, n = b ? r.uses(b) : 0;
    const what = r.key === "cold" ? `heard by ${n} puck${n === 1 ? "" : "s"}` : `${n} phone${n === 1 ? "" : "s"}`;
    let state = n ? `in use: ${what}` : avail ? "available" : rungs[r.key] === false ? "not from this source" : "unknown";
    if (r.key === "uwb" && b && (b.phones || []).some((q) => q.bearing)) state += " · with a phone's direction";
    return `<div class="rung ${n ? "inuse" : avail ? "avail" : "off"}" title="Gives: ${esc(r.gives)}"><span class="lt"></span><span class="rh">${esc(r.title)}</span><span class="stt">${esc(state)}</span>` +
      `<div class="nd"><b>Phone:</b> ${esc(r.phone)} · <b>Puck:</b> ${esc(r.puck)}</div><div class="gv">${esc(r.gives)}</div></div>`;
  }).join("");
}

// ---------------------------------------------------------------- timeline and feeds
function addEvent(m) {
  S.events.unshift(m);
  S.events.length = Math.min(S.events.length, 120);
  renderTimeline();
}
function renderTimeline() {
  const box = $("timeline");
  $("tl-n").textContent = S.events.length ? String(S.events.length) : "";
  if (!S.events.length) { box.innerHTML = `<div class="empty">Events appear here: pucks landing, phones heard, fixes, status changes.</div>`; return; }
  box.innerHTML = S.events.map((e) => `<div class="tl ${esc(e.name)}"><span class="tm">${esc(clockOf(e.t))}</span><span><span class="nm">${esc(String(e.name || "").replace(/_/g, " "))}</span>${esc(e.text || "")}</span></div>`).join("");
}
async function pollFeeds() {
  if ($("tab-feeds").hidden) return;
  try {
    const gj = await getJSON("/feed/beacons.geojson");
    const counts = {};
    for (const f of gj.features || []) { const k = f.properties?.kind || "?"; counts[k] = (counts[k] || 0) + 1; }
    $("feed-geo-n").textContent = `${(gj.features || []).length} features${Object.keys(counts).length ? ": " + Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(", ") : ""}${gj.simulated ? " · simulated" : ""}`;
  } catch (e) { $("feed-geo-n").textContent = `not served by this source (${e.status ? "HTTP " + e.status : "offline"})`; }
  try {
    const r = await fetch("/feed/cot.xml", { cache: "no-store" });
    if (!r.ok) throw Object.assign(new Error(), { status: r.status });
    const doc = new DOMParser().parseFromString(await r.text(), "application/xml");
    const evs = [...doc.getElementsByTagName("event")];
    const lines = evs.slice(0, 8).map((ev) => {
      const uid = (ev.getAttribute("uid") || "").replace(/^sandtable\.beacon\./, ""), type = ev.getAttribute("type") || "";
      const pt = ev.getElementsByTagName("point")[0], ce = pt ? Number(pt.getAttribute("ce")) : NaN;
      const cs = (ev.getElementsByTagName("contact")[0] || { getAttribute: () => "" }).getAttribute("callsign") || "";
      const rm = (ev.getElementsByTagName("remarks")[0] || {}).textContent || "";
      // a ring is sent as the puck's position with a circle that contains it: not a position of the phone
      const rangeOnly = /range only/i.test(cs) || /range only/i.test(rm);
      return `${uid.padEnd(10)} ${type.padEnd(10)} ${isNum(ce) ? `ce ${ce.toFixed(1)} m` : ""}${rangeOnly ? "  RANGE ONLY" : ""}${cs && !/range only/i.test(cs) ? `  ${cs}` : ""}`;
    });
    $("feed-cot-pre").textContent = `${evs.length} event${evs.length === 1 ? "" : "s"}${evs.length > 8 ? " (first 8)" : ""}:\n` + lines.join("\n");
  } catch (e) { $("feed-cot-pre").textContent = `not served by this source (${e.status ? "HTTP " + e.status : "offline"})`; }
}

// ---------------------------------------------------------------- command agent (CONTRACTS 3.7; log style as Sandtable's board)
const BACKEND_WORDS = { replay: "recorded real model run", "reflex-fallback": "deterministic fallback, not a model", "claude-cli": "model, through the Claude CLI", anthropic: "model, through the Anthropic API" };
const backendWords = (b) => BACKEND_WORDS[b] || b || "";
function renderAgent() {
  const A = S.agent;
  if (!A) { $("agent-panel").hidden = true; return; }
  $("agent-panel").hidden = false;
  const busy = !!A.busy;
  $("btn-agent").disabled = busy || S.mode === "sample";
  $("btn-agent").textContent = busy ? "Thinking…" : "Ask the command agent";
  const meta = S.agentMeta || {}, asOf = meta.as_of_t ?? (A.plan && (A.plan.as_of_t ?? A.plan.t));
  const stale = !!meta.stale && !!A.plan && !busy;
  $("agent-state").textContent = busy ? "· thinking…" : [A.plan && isNum(asOf) ? `plan from ${clockOf(asOf)}` : null, A.backend ? backendWords(A.backend) : null].filter(Boolean).map((x) => "· " + x).join(" ");
  $("btn-agent").classList.toggle("primary", stale);
  const p = A.plan, parts = [];
  if (stale) parts.push(`<div class="agent-stale">The situation has changed since this plan${isNum(asOf) ? ` (${clockOf(asOf)})` : ""}: ask again.</div>`);
  if (p) {
    parts.push(`<div class="plan-card${stale ? " old" : ""}"><div class="run-head"><span>Plan${isNum(asOf) ? ` <small>from ${clockOf(asOf)}</small>` : ""}</span><span class="bk ${esc(p.backend)}" title="${esc(backendWords(p.backend))}">${esc(p.backend || "")}</span></div>` +
      (p.summary ? `<div class="sum">${esc(p.summary)}</div>` : "") +
      ((p.orders || []).length ? `<div class="k">Radio orders</div>${p.orders.map((o) => `<div class="rx ${esc(o.priority)}"><span class="to">→ ${esc(o.to)}</span>${esc(o.text)}${o.target ? ` <span class="tg" style="font:400 11px var(--mono);color:var(--phone-soft)">${esc(o.target)}</span>` : ""}</div>`).join("")}` : "") +
      (p.next_puck ? `<div class="k">Next puck</div><div class="nextp"><b>${esc(p.next_puck.suite ? "Suite " + p.next_puck.suite : p.next_puck.room || "")}</b>${p.next_puck.why ? ` — ${esc(p.next_puck.why)}` : ""}</div>` : "") +
      ((p.limits_acknowledged || []).length ? `<div class="k">Limits it acknowledged</div><div style="font-size:12px;color:var(--muted)">${p.limits_acknowledged.map(esc).join("; ")}</div>` : "") + `</div>`);
  }
  const log = A.log || [];
  if (log.length) {
    const run = log[0].run || "run", bk = (log.find((e) => e.kind === "final") || log[log.length - 1]).backend || A.backend;
    const rows = log.filter((e) => e.kind !== "start").map((ev) => {
      if (ev.kind === "tool_call" || ev.kind === "action") {
        const text = ev.kind === "action" ? ev.text : Object.entries(ev.args || {}).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ") || ev.text || "";
        return `<div class="ev ${ev.kind}"><span class="tool${ev.kind === "action" ? " action" : ""}">${esc(ev.tool || ev.kind)}</span><span class="${ev.kind === "action" ? "txt" : "arg"}" title="${esc(text)}">${esc(text)}</span></div>`;
      }
      return `<div class="ev ${esc(ev.kind)}">${esc(ev.text || "")}</div>`;
    }).join("");
    parts.push(`<div class="run"><div class="run-head"><span>${esc(run.split("#")[0] === "phones" ? "Phones situation" : run)}</span><span class="bk ${esc(bk)}" title="${esc(backendWords(bk))}">${esc(bk || "")}</span></div>${rows}</div>`);
  }
  setHTML($("agent-log"), parts.join("") || `<div class="empty">Ask the command agent: it reads the phones and the building record, then writes search priorities and radio orders.</div>`);
}
function onAgentMessage(m) {
  if (!S.agent) S.agent = { busy: false, backend: null, plan: null, log: [] };   // a run arrived: the agent exists
  const A = S.agent;
  if (m.type === "agent") {
    if (m.kind === "start" || (A.log[0] && A.log[0].run !== m.run)) { A.log = []; A.busy = true; }
    A.log.push(m);
    if (m.backend) A.backend = m.backend;
    if (m.kind === "final" || m.kind === "error") A.busy = false;
  } else if (m.type === "agent_plan") { A.plan = m; A.busy = false; if (m.backend) A.backend = m.backend; }
  renderAgent();
  if (S.beacons && S.beacons.accountability) renderAccountability(S.beacons);
  const box = $("agent-log"); box.scrollTop = m.type === "agent_plan" ? 0 : box.scrollHeight;
}
async function loadAgent(keep) {
  try {
    const a = await getJSON("/api/agent");
    if (a.available === false && !keep) S.agent = null;
    else S.agent = { busy: !!a.busy, backend: a.backend || (S.agent && S.agent.backend) || null, plan: a.plan || (S.agent && S.agent.plan) || null, log: (a.log && a.log.length ? a.log : (S.agent && S.agent.log)) || [] };
  } catch { if (!keep) S.agent = null; }
  renderAgent();
}
function askAgent() {
  if (S.agent) { S.agent.busy = true; renderAgent(); }
  act(() => postJSON("/api/agent/run", {}), "The command agent is reading the phones and the record");
}

// ---------------------------------------------------------------- render
function render() {
  const b = S.beacons;
  renderTop();
  if (b) {
    if (b.agent) {
      S.agentMeta = b.agent;
      if (!S.agent) { S.agent = { busy: !!b.agent.busy, backend: b.agent.backend || null, plan: null, log: [] }; loadAgent(true); }
      S.agent.busy = !!b.agent.busy; if (b.agent.backend) S.agent.backend = b.agent.backend; renderAgent();
    }
    plan.update(b);
    renderCaption(b.phones || []);
    renderPhones(b);
    renderAccountability(b);
    renderTray(b);
    renderSearchBanner(b);
    if (S.layoutOn) loadLayout();
  }
  renderCue(b);
  renderLadder(b);
  renderFooter();
}

// ---------------------------------------------------------------- actions
async function act(fn, okText) {
  try { await fn(); if (okText) toast(okText); }
  catch (e) { toast(e.status === 409 ? "Not available: the source is real hardware, not the simulator." : e.status === 404 ? "This server does not offer that yet." : `Failed: ${e.message}`); }
}
function placeTwo() { act(() => postJSON("/api/pucks", { n: 2 }), "Two pucks placed (recommended layout)"); }
function addPuck() {
  const n = ((S.beacons && S.beacons.pucks) || []).length;
  if (n >= MAX_PUCKS) return;
  act(() => postJSON("/api/pucks", { n: n + 1 }), `Puck ${n + 1} added`);
}
function toggleSearch() {
  const b = S.beacons; if (!b || !b.room) return;
  const on = !!(b.search_room && b.search_room.room);
  act(() => postJSON("/api/search_room", { room: on ? null : b.room }), on ? "Search room off: candidates stand on the measurements alone" : `Command is searching ${b.room_label || b.room}`);
}
async function loadLayout() {
  const room = (S.beacons && S.beacons.room) || (S.bundle.config && S.bundle.config.room);
  if (!S.layoutOn || !room) { plan.setLayout(null); $("layout-why").hidden = true; return; }
  if (S.layout && S.layout.room === room) return;
  try {
    const lay = await getJSON(`/api/layout?room=${encodeURIComponent(room)}&n=2`);
    S.layout = { ...lay, room: lay.room || room };
    plan.setLayout(S.layout);
    const first = String(lay.why || "").split(/(?<=\.)\s/)[0];
    $("layout-why").innerHTML = `<b>Where to put two pucks</b>${esc(first)}${lay.mirror_falls ? `<div class="mf">Mirror images fall in ${esc(lay.mirror_falls)}.</div>` : ""}`;
    $("layout-why").title = lay.why || "";
    $("layout-why").hidden = false;
  } catch (e) {
    S.layoutOn = false; $("btn-layout").setAttribute("aria-pressed", "false");
    toast(e.status === 404 ? "This server does not offer a recommended layout yet." : `Layout failed: ${e.message}`);
  }
}
function renderSearchBanner(b) {
  const sr = b && b.search_room, ban = $("search-banner");
  if (sr && sr.room) { ban.textContent = `Command is searching ${sr.room_label || sr.room}: dismiss spots outside it`; ban.hidden = false; }
  else ban.hidden = true;
}
function runStory() { act(() => postJSON("/api/sim/story", { action: "start", script: "suite-310" }), "Walk-through started: pucks land one by one"); }
function resetAll() { act(() => postJSON("/api/reset", {}), "Reset: no pucks, no phones"); }

// ---------------------------------------------------------------- connection
function onMessage(m) {
  if (!m || typeof m !== "object") return;
  if (m.type === "hello") { S.hello = m; if (m.rig) S.rig = m.rig; render(); }
  else if (m.type === "beacons") { S.beacons = m; if (!S._static) { S._static = true; renderStatic(); } render(); }
  else if (m.type === "beacon_event") addEvent(m);
  else if (m.type === "agent" || m.type === "agent_plan") onAgentMessage(m);
  else if (m.type === "reset") { S.events = []; S.geomSeen = {}; S.subject = null; renderTimeline(); }
}
let ws = null, pollTimer = null;
function startPoll() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    if (ws && ws.readyState === 1) return;
    try { const b = await getJSON("/api/beacons"); S.conn = "poll"; onMessage({ ...b, type: b.type || "beacons" }); }
    catch { S.conn = "down"; renderTop(); }
  }, 1500);
}
function connect() {
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
  try { ws = new WebSocket(url); } catch { S.conn = "down"; renderTop(); setTimeout(connect, 2000); return; }
  ws.onopen = () => { S.conn = "up"; renderTop(); };
  ws.onmessage = (e) => { if (e.data === "pong") return; try { onMessage(JSON.parse(e.data)); } catch { /* not JSON */ } };
  ws.onclose = () => { S.conn = S.conn === "poll" ? "poll" : "down"; renderTop(); setTimeout(connect, 1500); };
  ws.onerror = () => { try { ws.close(); } catch { /* ignore */ } };
}
setInterval(() => { if (ws && ws.readyState === 1) ws.send("ping"); }, 20000);

async function loadSample() {
  S.conn = "static";
  for (const path of ["/fixtures/beacon/sample_beacons.json"]) {
    try { const b = await getJSON(path); onMessage({ ...b, type: "beacons", sample: true }); return; } catch { /* next */ }
  }
  toast("No sample file found (fixtures/beacon/sample_beacons.json)");
}

// ---------------------------------------------------------------- wiring
function wire() {
  $("btn-place2").onclick = placeTwo;
  $("btn-add").onclick = addPuck;
  $("btn-search").onclick = toggleSearch;
  $("btn-layout").onclick = () => {
    S.layoutOn = !S.layoutOn; S.layout = null;
    $("btn-layout").setAttribute("aria-pressed", S.layoutOn ? "true" : "false");
    loadLayout();
  };
  $("btn-connect").onclick = (e) => {
    e.stopPropagation();
    const pop = $("connect-pop"), open = pop.hidden;
    pop.hidden = !open; $("btn-connect").setAttribute("aria-expanded", String(open));
    if (open && !$("qr").getAttribute("src")) {
      $("qr").onerror = () => { $("qr").hidden = true; $("qr-none").hidden = false; };
      $("qr").src = `/api/app_link.svg?url=${encodeURIComponent(location.origin)}`;
    }
    $("addr").textContent = location.origin;
  };
  $("connect-pop").onclick = (e) => e.stopPropagation();
  document.addEventListener("click", () => { $("connect-pop").hidden = true; $("btn-connect").setAttribute("aria-expanded", "false"); });
  $("btn-story").onclick = runStory;
  $("btn-reset").onclick = resetAll;
  $("btn-agent").onclick = askAgent;
  for (const b of $("focus-seg").querySelectorAll("button")) b.onclick = () => {
    for (const q of $("focus-seg").querySelectorAll("button")) { q.classList.toggle("on", q === b); q.setAttribute("aria-pressed", q === b ? "true" : "false"); }
    plan.setFocusMode(b.dataset.focus);
  };
  for (const t of document.querySelectorAll(".tab")) t.onclick = () => {
    for (const q of document.querySelectorAll(".tab")) { q.classList.toggle("on", q === t); q.setAttribute("aria-selected", q === t ? "true" : "false"); }
    for (const k of ["ladder", "timeline", "feeds"]) $(`tab-${k}`).hidden = t.dataset.tab !== k;
    if (t.dataset.tab === "feeds") pollFeeds();
  };
  setInterval(pollFeeds, 3000);
  $("scale-sel").onchange = (e) => {
    const v = Number(e.target.value);
    act(async () => { S.rig = await postJSON("/api/rig", { scale: v }) || { scale: v }; renderTop(); }, `Exercise scale set to 1 : ${v}`);
  };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && S.placing) { S.placing = null; plan.setPlacing(false); renderTray(S.beacons); } });
}

(async () => {
  wire();
  try { S.bundle = await loadBundle(); }
  catch (e) { $("cue").hidden = false; $("cue").innerHTML = `<h3>The building could not be loaded</h3><p>${esc(e.message)}. Is the Beacon server running?</p>`; return; }
  plan = new Plan($("plan"), S.bundle, {
    onPick: (id) => selectPhone(S.selected === id ? null : id, true),
    topReserve: () => ($("caption").hidden ? 0 : $("caption").offsetHeight + 16),
    canDragPuck: () => S.mode !== "sample" && !isSample(),
    onPuckMove: (id, x, y, p) => act(() => postJSON("/api/pucks", { pucks: [{ id, x: +x.toFixed(2), y: +y.toFixed(2), ...(p && p.accessory ? { accessory: p.accessory } : {}) }], merge: true }), `${id} moved`),
    onPlace: (x, y) => {
      const q = S.placing; if (!q) return;
      S.placing = null; plan.setPlacing(false);
      act(() => postJSON("/api/pucks", { pucks: [{ id: q.suggested_id, x: +x.toFixed(2), y: +y.toFixed(2), accessory: q.accessory }], merge: true }), `${q.suggested_id} placed`);
    },
  });
  renderStatic();
  render();
  if (S.mode === "sample") { await loadSample(); return; }
  getJSON("/api/rig").then((r) => { S.rig = r; renderTop(); }).catch(() => {});
  loadAgent();
  connect();
  startPoll();
})();
