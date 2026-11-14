// Sandtable command board. Static ES modules, no build step, no network beyond this origin.
import { Plan } from "./plan2d.js";
import { Aerial } from "./aerial.js";
import { BeaconModel, PHONE_COLOR, STATUS_WORD, STATE_WORD, MODES, geomWords, reportWords, ago } from "./beacon.js";
import { ScoutModel, PHASES, PHASE_WORD, RUNG_WORD, METHOD_WORD, VICTIM_COLOR, victimState, sigmaEllipse, wallDistances, regQuality, meshSvg } from "./scout.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const UNIT_COLOR = { E1: "#ffc247", RIT: "#4fd1ff" };
const LAYER_OF_TOOL = {
  get_building_record: "costar", list_tenants: "costar", list_vacant_space: "costar", file_record_correction: "action",
  outside_context: "vexcel", list_aerial_imagery: "vexcel", look_at_aerial: "vexcel", measure_outside: "vexcel",
  describe_floor: "matterport", route: "matterport", nearest_exit: "matterport", hose_stretch: "matterport",
  locate_unit: "uwb", plan_anchor_drops: "uwb", radio: "action", assign: "action", mark_hazard: "action", request_resource: "action", add_observation: "action",
};
LAYER_OF_TOOL.get_scan = "uwb";
const RUN_TITLE = { preplan: "Pre-incident plan", mayday: "Mayday", hazard: "Re-plan: part of the floor is lost", contact: "Contact made: way out", aar: "After-action report", scan: "Scan landed: a person in the room", victim_reached: "Victim reached: way out" };

const S = { bundle: null, hello: null, state: null, preplan: null, aar: null, selected: "E1", sound: false, inject: false, view: "3d", runs: new Map(), tenantsBySuite: {},
  script: "beacon", mode: "beacon", guided: false, tab: "acc", focus: "room", smokeForce: null };
let plan, aerial, twin3d = null, scout, beacon;

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { toast((await r.json().catch(() => ({}))).detail || `${path}: ${r.status}`); throw new Error(path); }
  return r.json();
}
function toast(text) { const t = $("toast"); t.textContent = text; t.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 3500); }
const mmss = (s) => { s = Math.max(0, Math.round(s || 0)); return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };

// ---------------------------------------------------------------- static panels
function renderRecord() {
  const { site, twin, labels } = S.bundle;
  const p = site.property, fs = site.field_sources || {};
  $("address").textContent = `${p.address}, ${p.city}, ${p.state} · ${p.name || ""}`;
  $("record-src").textContent = labels.costar.mode === "live" ? "CoStar (live)" : "fixture: public open data, not CoStar";
  const fmt = (v) => (typeof v === "number" ? v.toLocaleString("en-US") : v);
  const rows = [
    ["Type", p.property_type, "property_type", true], ["Construction", p.construction, "construction", true],
    ["Floors", p.stories, "stories"], ["Year built", p.year_built, "year_built"],
    ["Area", p.rba_sf && `${fmt(p.rba_sf)} sq ft`, "rba_sf"], ["Height", p.height_m && `${p.height_m} m`, "height_m"],
    ["Sprinklers", p.sprinklers, "sprinklers"], ["Elevators", p.elevators, "elevators"],
    ["Key holder", p.property_manager && (p.property_manager.name || null), "property_manager"], ["Parking", p.parking_spaces, "parking_spaces"],
  ];
  $("record").innerHTML = rows.map(([k, v, f, wide]) => {
    const src = fs[f] || {};
    const layer = /LARIAC|footprint|aerial|Vexcel/i.test(src.source || "") ? "vexcel" : "costar";
    const empty = v === null || v === undefined || v === "";
    const title = empty ? "Only the CoStar record has this field: empty in fixture mode" : (src.source || "");
    return `<div class="fact${wide ? " wide" : ""}" title="${esc(title)}"><div class="k"><span class="dot ${layer}"></span>${k}${src.synthetic ? " · synthetic" : ""}</div>` +
      `<div class="v${empty ? " null" : ""}" title="${esc(empty ? title : v)}">${empty ? "CoStar field" : esc(v)}</div></div>`;
  }).join("");
  const floorNo = Number(twin.floors.find((f) => f.id === twin.exercise_floor).label);
  $("floor-no").textContent = floorNo;
  const tenants = site.tenants.filter((t) => t.floor === floorNo);
  S.tenantsBySuite = {};
  for (const t of tenants) (S.tenantsBySuite[t.suite] ||= []).push(t);
  $("tenants").innerHTML = tenants.map((t) =>
    `<div class="tenant" data-suite="${esc(t.suite)}"><div class="suite">${esc(t.suite)}</div><div><div class="cat">${esc(t.industry)}</div>` +
    (t.life_hazard ? `<div class="hz">Life hazard: ${esc(t.life_hazard)}</div>` : "") + (t.hazmat ? `<div class="hm">Hazmat: ${esc(t.hazmat)}</div>` : "") + `</div></div>`).join("") ||
    `<div class="empty">No tenants in the record for this floor.</div>`;
  for (const el of $("tenants").querySelectorAll(".tenant")) {
    el.addEventListener("mouseenter", () => highlightSuite(el.dataset.suite, true));
    el.addEventListener("mouseleave", () => highlightSuite(el.dataset.suite, false));
  }
  $("twin-src").textContent = twin.source.synthetic ? `floor ${floorNo} · synthetic twin fitted to the real footprint (not a Matterport scan)` : `floor ${floorNo} · Matterport twin`;
  const fx = (k, live, fixture) => (labels[k].mode === "live" ? live : fixture);
  const sources = (labels.open_data || []).map((o) => o.name).join("; ");
  $("foot").innerHTML = `<b>What is real here.</b> ` + [
    fx("costar", "Record: CoStar (live)", "Record: real public open data (assessor parcel, city business registrations; tenant names withheld), CoStar-only fields empty. Not CoStar data"),
    fx("matterport", "Twin: Matterport (live)", "Twin: synthetic interior fitted to the real footprint. Not a Matterport scan"),
    fx("vexcel", "Aerial: Vexcel (live)", "Aerial: real USDA NAIP imagery. Not Vexcel"),
  ].map(esc).join(`<span class="sep">|</span>`) + `<span class="sep">|</span><span title="${esc(sources)}">Open sources: ${esc(sources)}</span>`;
}
function highlightSuite(suite, on) {
  plan.highlightSuite(suite, on);
  twin3d?.highlightSuite?.(suite, on);
}

function renderBadges() {
  const h = S.hello; if (!h) return;
  const b = [];
  const hubOk = S.state ? S.state.hub_connected : h.hub.connected;
  if (!hubOk) b.push(`<span class="badge off">UWB hub disconnected</span>`);
  else if (h.simulated) b.push(`<span class="badge sim" title="Crew positions come from the uwb-kit simulator (${esc(h.hub.source)}), not from real boards">Crews SIM · ${esc(h.hub.profile || "")}</span>`);
  else b.push(`<span class="badge live">UWB live · ${esc(h.hub.profile || "")}</span>`);
  const sc = h.registration?.scale;
  if (sc) b.push(`<span class="badge" title="Metres of building per metre of rig">${sc > 1.5 ? `Exercise 1:${sc.toFixed(1)}` : "Live 1:1"}</span>`);
  b.push(`<span class="badge" title="Where the command agent's model answers come from">LLM ${esc(h.modes.llm)}</span>`);
  const lay = ["costar", "matterport", "vexcel"], nm = { costar: "CoStar", matterport: "Matterport", vexcel: "Vexcel" };
  const allFx = lay.every((k) => h.modes[k] === "fixture");
  b.push(`<span class="badge" title="Sponsor layers: fixture = labelled stand-in data (see the footer), live = sponsor API">${allFx ? "Sponsor data: fixture" : esc(lay.map((k) => `${nm[k]} ${h.modes[k]}`).join(" · "))}</span>`);
  b.push(`<span class="badge" title="A crew that does not move for this long raises a mayday. Real PASS devices alarm at about 30 s; exercises compress it.">No-motion ${h.no_motion_s}s</span>`);
  const bl = S.state?.beacon || beacon?.link;
  if (beacon) b.unshift(beacon.dev ? `<span class="badge dev">Beacon: dev sample</span>` : bl?.connected ? `<span class="badge ${bl.simulated !== false ? "sim" : "live"}" title="Beacon module: ${esc(bl.source || "")}">Beacon ${bl.simulated !== false ? "SIM" : "live"}${bl.sample ? " · sample" : ""}</span>` : bl?.source === "replay" ? `<span class="badge sim" title="The Beacon module is not connected live; phones come from a replayed, simulated Beacon feed">Beacon replay · SIM</span>` : `<span class="badge off" title="No phone data reaches this board">Beacon not connected</span>`);
  $("badges").innerHTML = b.join("");
  $("modes-note").title = $("modes-note").textContent = `${MODES[S.mode]?.word}: ${MODES[S.mode]?.line} ` + (!hubOk ? "No crew position stream." : h.simulated ? "Crew positions are simulated (uwb-kit simulator)." : "Crew positions come from real UWB boards.");
  $("btn-story").title = h.drivable ? "SIM: plays the two-minute exercise hands-free (shift-click: guided, you move Engine 1)" : "Guided exercise: you move the Engine 1 token, the agent and the script do the rest";
}

// ---------------------------------------------------------------- dynamic panels
function renderState() {
  const st = S.state; if (!st) return;
  const ph = $("phase");
  ph.textContent = st.phase.toUpperCase(); ph.className = `phase ${st.phase}`;
  $("clock").textContent = mmss(st.elapsed_s);
  $("btn-alarm").disabled = st.phase !== "standby";
  $("btn-mayday").disabled = ["standby", "terminated"].includes(st.phase) || !!st.mayday;
  $("btn-story").textContent = st.storyboard?.running ? "Stop exercise" : `${S.guided || !S.hello?.drivable ? "Guided" : "Run"} ${{ mayday: "mayday", scout: "scout", beacon: "beacon" }[S.script]} drill`;
  if (scout.onState(st.scout)) applySmoke();
  if (!beacon.dev && beacon.onState(st.beacon)) { renderBeaconHead(); renderAcc(); renderBadges(); }
  renderThrow();
  $("agent-state").textContent = st.storyboard?.running ? `· ${st.storyboard.mode === "assist" ? "guided" : "autopilot"}: ${st.storyboard.step}` : st.agent_busy ? "· thinking…" : "";
  const cue = $("cue"), yours = st.storyboard?.running && /^YOUR MOVE/.test(st.storyboard.step || "");
  cue.hidden = !yours; if (yours) cue.textContent = st.storyboard.step;
  const bar = $("mayday-bar");
  if (st.mayday) {
    const u = st.units.find((q) => q.id === st.mayday.unit);
    bar.hidden = false;
    $("mayday-text").textContent = `${u?.label || st.mayday.unit} · ${st.mayday.room_label || ""} · ${{ no_motion: "no movement", button: "tag button", manual: "declared by command" }[st.mayday.reason] || st.mayday.reason}`;
    $("mayday-timer").textContent = mmss(st.mayday.elapsed_s);
    if (S.selected !== st.mayday.unit && !S._pinned) S.selected = st.mayday.unit;
  } else bar.hidden = true;
  const inside = st.units.filter((u) => ["interior", "mayday", "exiting"].includes(u.status)).length;
  $("par").textContent = st.phase === "standby" ? "" : st.phase === "terminated" ? "PAR complete: all out" : `${inside} crew${inside === 1 ? "" : "s"} on the floor`;
  $("board").innerHTML = st.units.map((u) => {
    const a = u.assignment;
    return `<div class="unit-row ${u.status === "mayday" ? "mayday" : ""} ${u.id === S.selected ? "sel" : ""}" data-unit="${u.id}">` +
      `<div class="cs" style="color:${UNIT_COLOR[u.id] || "#fff"}">${esc(u.id)}</div>` +
      `<div style="min-width:0"><div class="loc">${esc(u.room_label || "no fix")}<span class="pill ${u.status}">${esc(u.status)}</span>${u.physical ? "" : `<span class="pill virtual" title="No tag bound: moved by the server">virtual</span>`}</div>` +
      `<div class="asg">${a ? `${esc(a.task.replace("_", " "))}${a.target_label ? " → " + esc(a.target_label) : ""}${u.route ? ` · ${Math.round(u.route.length_m)} m${u.route.valid ? "" : " · ROUTE LOST"}` : ""}` : esc(u.label)}</div></div>` +
      `<div class="nums">${u.time_in_s ? `in ${mmss(u.time_in_s)}<br>air ${Math.round(u.air_pct)}%` : (u.sigma_m != null ? `±${u.sigma_m.toFixed(1)} m` : "")}</div></div>`;
  }).join("") + victimRows(st);
  $("board").insertAdjacentHTML("afterbegin", phoneRows());
  for (const el of $("board").querySelectorAll(".unit-row")) el.onclick = () => { S.selected = el.dataset.unit; S._pinned = true; renderState(); };
  renderHere();
  plan.update(st, S.selected);
  aerial.update(st);
  twin3d?.update(st, S.selected);
  applyFocus();
}
function victimRows(st) {
  return (scout.scan?.victims || []).filter((v) => v.x != null).map((v) => {
    const vs = scout.vstatus[v.id] || {}, by = st.units.find((u) => u.id === vs.rescuer) || st.units.find((u) => u.assignment && u.assignment.target === v.id);
    const status = vs.status && vs.status !== "located" ? vs.status : "not tagged";
    return `<div class="unit-row victim ${v.id === S.selected ? "sel" : ""}" data-unit="${esc(v.id)}">` +
      `<div class="cs" style="color:${VICTIM_COLOR}">${esc(v.id)}</div>` +
      `<div style="min-width:0"><div class="loc">${esc(v.room_label || scout.scan.room_label || "")}<span class="pill victim">${esc(status)}</span></div>` +
      `<div class="asg">${esc(victimState(v))}${by ? ` · ${esc(by.label)}${vs.status === "reached" || vs.status === "evacuating" || vs.status === "out" ? " with them" : " sent"}` : " · located by the pucks"}</div></div>` +
      `<div class="nums">±${(v.sigma_m ?? sigmaEllipse(v).a).toFixed(1)} m<br>${scout.scan.simulated ? "simulated" : "measured"}</div></div>`;
  }).join("");
}

function nearestSide(u) {
  const { twin, site } = S.bundle;
  const f = twin.floors.find((q) => q.id === twin.exercise_floor).outline;
  const cx = (Math.min(...f.map((p) => p[0])) + Math.max(...f.map((p) => p[0]))) / 2, cy = (Math.min(...f.map((p) => p[1])) + Math.max(...f.map((p) => p[1]))) / 2;
  const bearing = ((twin.georef.bearing_deg - Math.atan2(u.y - cy, u.x - cx) * 180 / Math.PI) % 360 + 360) % 360;
  let best = null;
  for (const [k, v] of Object.entries(site.sides || {})) {
    const d = Math.min(Math.abs(v.bearing_deg - bearing), 360 - Math.abs(v.bearing_deg - bearing));
    if (!best || d < best[1]) best = [k, d];
  }
  return best ? best[0] : null;
}
function renderHereVictim(v) {
  const el = $("here"), { twin } = S.bundle, s = scout.scan;
  const room = twin.rooms.find((r) => r.id === (v.room || s.room));
  const node = v.node || plan.nearestNode(v.x, v.y, room?.id);
  const exits = plan.exitDistances(node, S.state?.hazards || []);
  const tenants = (room?.suite && S.tenantsBySuite[room.suite]) || [];
  const side = nearestSide(v);
  const geo = v.geo || (() => { const g = aerial.geo(v.x, v.y); return { lat: g[0], lon: g[1] }; })();
  const rec = tenants.length
    ? tenants.map((t) => `<div>${esc(t.industry)}</div>` + (t.life_hazard ? `<div class="hzline">${esc(t.life_hazard)}</div>` : "")).join("")
    : (room?.suite ? "no active tenant in the record for this suite" : "common area, no tenant");
  el.innerHTML = `<div><div class="who"><span class="dot" style="background:${VICTIM_COLOR}"></span>${esc(v.id)}: a person, not tagged</div><div class="where">${esc(room?.label || s.room_label || "—")}</div><div class="sub">${esc(victimState(v))}</div></div>` +
    `<div><div class="k"><span class="dot matterport"></span>Twin: where in the room</div><div>${esc(room ? wallDistances(twin, room, v.x, v.y) : "")}</div>${exits.map((e) => `<div>${esc(e.label)}: ${e.m == null ? "<b class='hzline'>cut off</b>" : e.m + " m"}</div>`).join("")}</div>` +
    `<div><div class="k"><span class="dot costar"></span>Record: who is usually in there</div>${rec}</div>` +
    `<div><div class="k"><span class="dot vexcel"></span>Aerial: where outside</div><div>${side ? `behind side ${side}` : ""}</div><div>${geo.lat.toFixed(5)}, ${geo.lon.toFixed(5)}</div></div>` +
    `<div><div class="k"><span class="dot scan"></span>Radar: how sure${s.simulated ? " (simulated)" : ""}</div><div>±${(v.sigma_m ?? sigmaEllipse(v).a).toFixed(1)} m, ${v.n_links ?? "?"} links agree</div><div>confidence ${Math.round((v.confidence ?? 0) * 100)}%${v.breathing_conf != null ? `, breath ${Math.round(v.breathing_conf * 100)}%` : ""}</div><div class="faintline">coarse: still and not breathing would look like furniture</div></div>`;
}
function renderHere() {
  const st = S.state, el = $("here");
  const vic = scout?.victim(S.selected);
  if (vic) { renderHereVictim(vic); return; }
  const ph = beacon?.phone(S.selected);
  if (ph) { renderHerePhone(ph); return; }
  const u = st?.units.find((q) => q.id === S.selected);
  if (!u || u.x == null) { el.innerHTML = `<div><div class="who">No position yet</div><div class="where">—</div></div><div></div><div></div><div></div><div></div>`; return; }
  const tenants = (u.suite && S.tenantsBySuite[u.suite]) || [];
  const exits = plan.exitDistances(u.node, st.hazards);
  const side = nearestSide(u);
  const rec = tenants.length
    ? tenants.map((t) => `<div>${esc(t.industry)}</div>` + (t.life_hazard ? `<div class="hzline">${esc(t.life_hazard)}</div>` : "") + (t.hazmat ? `<div class="hzline">Hazmat: ${esc(t.hazmat)}</div>` : "")).join("")
    : (u.suite ? "no active tenant in the record for this suite" : "common area, no tenant");
  el.innerHTML = `<div><div class="who"><span class="dot" style="background:${UNIT_COLOR[u.id]}"></span>${esc(u.label)} is here${u.physical ? "" : " (virtual)"}</div><div class="where">${esc(u.room_label || "—")}</div></div>` +
    `<div><div class="k"><span class="dot matterport"></span>Twin: way out</div>${exits.map((e) => `<div>${esc(e.label)}: ${e.m == null ? "<b class='hzline'>cut off</b>" : e.m + " m"}</div>`).join("")}</div>` +
    `<div><div class="k"><span class="dot costar"></span>Record: who is in there</div>${rec}</div>` +
    `<div><div class="k"><span class="dot vexcel"></span>Aerial: where outside</div><div>${side ? `behind side ${side}` : ""}</div><div>${u.geo ? `${u.geo.lat.toFixed(5)}, ${u.geo.lon.toFixed(5)}` : ""}</div></div>` +
    `<div><div class="k"><span class="dot uwb"></span>UWB: the fix</div>${u.physical ? `<div>tag ${esc(u.tag)}${u.sigma_m != null ? ` · ±${u.sigma_m.toFixed(1)} m in the building` : ""}</div><div>${u.still_s >= 3 ? `still for ${Math.round(u.still_s)} s` : "moving"}</div>` : "<div>no tag: moved by the server</div>"}</div>`;
  for (const t of $("tenants").querySelectorAll(".tenant")) t.classList.toggle("at", !!u.suite && t.dataset.suite === u.suite);
}

// ---------------------------------------------------------------- agent log, radio
function onAgent(ev) {
  const log = $("agent-log");
  if (!S.runs.size) log.innerHTML = "";
  let run = S.runs.get(ev.run);
  if (!run) {
    const kind = ev.run.split("#")[0];
    const el = document.createElement("div");
    el.className = `run ${kind}`;
    el.innerHTML = `<div class="run-head"><span>${esc(RUN_TITLE[kind] || ev.run)}</span><span class="bk ${esc(ev.backend)}">${esc(ev.backend)}</span></div>`;
    log.appendChild(el);
    run = { el, bk: el.querySelector(".bk") };
    S.runs.set(ev.run, run);
  }
  if (ev.kind === "start") return;
  if (ev.backend && run.bk.textContent !== ev.backend && (ev.kind === "final" || ev.backend === "reflex-fallback")) { run.bk.textContent = ev.backend; run.bk.className = `bk ${ev.backend}`; }
  const d = document.createElement("div");
  d.className = `ev ${ev.kind}`;
  if (ev.kind === "tool_call" || ev.kind === "action") {
    const layer = LAYER_OF_TOOL[ev.tool] || "action";
    const text = ev.kind === "action" ? ev.text : (ev.tool === "look_at_aerial" ? ev.text.replace(/^Look at the aerial \([^)]*\): /, "") : Object.entries(ev.args || {}).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(", "));
    d.innerHTML = `<span class="tool ${layer}">${esc(ev.tool)}</span><span class="${ev.kind === "action" ? "txt" : "arg"}" title="${esc(text)}">${esc(text)}</span>`;
  } else d.textContent = ev.text;
  run.el.appendChild(d);
  log.scrollTop = log.scrollHeight;
}
function onRadio(m, live) {
  const box = $("radio");
  if (box.querySelector(".empty")) box.innerHTML = "";
  const d = document.createElement("div");
  d.className = `rx ${m.priority}`;
  d.innerHTML = `<span class="to">${esc(m.from)} → ${esc(m.to)}</span>${esc(m.text)}`;
  box.appendChild(d); box.scrollTop = box.scrollHeight;
  if (live && S.sound && "speechSynthesis" in window) {
    const u = new SpeechSynthesisUtterance(m.text.replace(/\bRIT\b/g, "Rit").replace(/\bE1\b/g, "Engine one"));
    u.rate = 1.08; speechSynthesis.speak(u);
  }
}

// ---------------------------------------------------------------- documents
const itemRow = (i) => `<div class="item"><span class="dot ${esc(i.layer || "costar")}" title="${esc(i.layer || "")}"></span><span>${esc(i.text)}</span>${i.synthetic ? `<span class="syn" title="synthetic, inferred or a fixture stand-in">synthetic</span>` : "<span></span>"}</div>`;
function showPreplan() {
  const p = S.preplan; if (!p) return;
  const t = p.tactical || {}, hs = t.hose_stretch;
  $("doc-title").textContent = "Pre-incident plan";
  $("doc-body").innerHTML = `<p class="lead">${esc(p.address)}</p><p>${esc(p.summary || "")}</p><p><span class="bk ${esc(p.backend)}">written by: ${esc(p.backend)}</span></p>` +
    `<h3>Tactical</h3><div class="kv"><div class="k">Attack stair</div><div>${esc(t.attack_stair || "—")}</div><div class="k">Evacuation stair</div><div>${esc(t.evac_stair || "—")}</div>` +
    `<div class="k">Staging</div><div>${esc(t.staging || "—")}</div>` +
    (hs ? `<div class="k">Hose stretch</div><div>${esc(hs.target)} from ${esc(hs.stair)}: ${hs.total_m} m (${hs.lengths_50ft} lengths) = inside ${hs.inside_m} m (twin) + stair ${hs.vertical_m} m; supply leg outside ${hs.outside_m} m (aerial)</div>` : "") +
    (t.anchor_drops ? `<div class="k">UWB anchor drops</div><div>${t.anchor_drops.map((a) => `${esc(a.id)} ${esc(a.near)}`).join(" · ")} (3+ anchors within one partition wall on ${t.anchor_coverage_pct}% of the floor graph; strict line of sight ${t.anchor_line_of_sight_pct ?? "?"}%)</div>` : "") +
    `<div class="k">Life hazards</div><div>${(t.life_hazards || []).map((h) => `Suite ${esc(h.suite)}: ${esc(h.why)}`).join("<br>") || "—"}</div></div>` +
    (p.sections || []).map((s) => `<h3>${esc(s.title || s.id)}</h3>${(s.items || []).map(itemRow).join("")}`).join("") +
    (p.vision || []).map((v) => `<h3>What the agent looked at</h3><img class="vision" src="/api/vision/${esc(v.image)}.png" alt="The aerial image sent to the model, with the footprint, side letters, hydrants and the engine drawn on it">` +
      `<div class="qa"><b>Asked:</b> ${esc(v.question)}</div><div class="qa"><b>The model answered:</b> ${esc(v.answer)}</div><div class="qa"><small>Image: ${esc(v.source)}. Overlays drawn by Sandtable before sending.</small></div>`).join("") +
    `<h3>Verify on site</h3>${(p.verify_on_site || []).map((v) => `<div class="item"><span class="dot open-data"></span><span>${esc(v.text)}</span><span class="syn" style="color:var(--muted);border-color:var(--line-2)">${esc((v.layers || []).join(" + "))}</span></div>`).join("") || "<div class='empty'>Nothing flagged.</div>"}`;
  $("doc").hidden = false;
}
function showAar() {
  const a = S.aar; if (!a) return;
  const m = a.metrics || {};
  const list = (xs) => (xs || []).map((x) => `<div class="item"><span></span><span>${esc(x)}</span><span></span></div>`).join("") || "<div class='empty'>—</div>";
  $("doc-title").textContent = "After-action report";
  $("doc-body").innerHTML = `<p class="lead">${esc(a.summary || "")}</p><p><span class="bk ${esc(a.backend)}">written by: ${esc(a.backend)}</span></p>` +
    `<h3>Measured${S.hello?.simulated ? " (simulated run)" : ""}</h3><div class="kv">` +
    Object.entries({ mayday_to_assign_s: "Mayday to rescue order", mayday_to_contact_s: "Mayday to contact", mayday_to_all_out_s: "Mayday to all out", replans: "Re-plans", hazards: "Hazards reported" })
      .filter(([k]) => m[k] !== undefined).map(([k, l]) => `<div class="k">${l}</div><div>${m[k]}${k.endsWith("_s") ? " s" : ""}</div>`).join("") + `</div>` +
    `<h3>Went well</h3>${list(a.went_well)}<h3>Improve</h3>${list(a.improve)}<h3>Follow up</h3>${list(a.follow_up)}` +
    `<h3>Pinned in the twin</h3>${(a.observations || []).map((o) => `<div class="item"><span class="dot matterport"></span><span>${esc(o.description || o.label)}</span><span class="syn" style="color:var(--muted);border-color:var(--line-2)">${esc(o.id)}</span></div>`).join("") || "<div class='empty'>None.</div>"}` +
    `<h3>Record corrections filed</h3>${(a.corrections || []).map((c) => `<div class="item"><span class="dot costar"></span><span><b>${esc(c.field)}</b>: ${esc(typeof c.proposed === "object" ? JSON.stringify(c.proposed) : c.proposed)}<br><small>${esc(c.status || "")}</small></span><span class="syn" style="color:var(--muted);border-color:var(--line-2)">${esc(c.id || "")}</span></div>`).join("") || "<div class='empty'>None.</div>"}` +
    `<h3>Timeline</h3>${(a.timeline || []).map((e) => `<div class="tl ${esc(e.kind)}"><span>+${mmss(e.t_rel)}</span><span class="tx">${esc(e.text)}</span></div>`).join("")}`;
  $("doc").hidden = false;
}

// ---------------------------------------------------------------- scout: scan layer, card, smoke, feeds
function onScoutChange() {
  const view = S.mode === "scout" ? scout.view() : null;
  twin3d?.setScout(view); plan.setScout(view);
  aerial.setVictim?.(scout.mainVictim(), scout.scan);
  if (scout.onScreen && scout.animate && S.mode !== "scout" && scout.phase === "thrown") setMode("scout", true);
  if (scout.onScreen && !S._tabPinned && S.tab !== "scout" && S.mode === "scout") setTab("scout");
  $("scout-dot").hidden = !scout.onScreen;
  if (!scout.onScreen && S.selected && /^V\d/.test(S.selected)) S.selected = "E1";
  renderScoutCard(); renderScanHead(); renderLegend(); renderThrow(); renderDevbar(); applyFocus(); applySmoke();
  if (S.state) renderState(); else renderHere();
  clearInterval(S._meshTimer);
  if (scout.phase === "ranging" || scout.phase === "thrown") S._meshTimer = setInterval(renderScoutCard, 200);
  if (scout.phase === "done" && $("feeds-pop").hidden === false) refreshFeeds();
}
function onPick(id) {
  if (/^B\d/.test(id)) { S.selected = id; S._pinned = true; onBeaconChange(); return; }
  if (/^V/.test(id)) { S.selected = id; S._pinned = true; if (S.state) renderState(); else renderHere(); return; }
  const m = (scout.scan?.mismatches || []).find((q) => q.id === id);
  if (m) toast(`${m.id}: ${m.text}`);
}
function renderThrow() {
  const b = $("btn-throw"), ph = S.state?.phase;
  const incident = ph && !["standby", "terminated"].includes(ph);
  if (S.mode === "beacon") {
    if (beacon.dev) { b.disabled = false; b.title = "DEV SAMPLE: plays the hand-made phone story (N steps one frame)"; b.classList.add("ready"); return; }
    b.disabled = !incident || !(beacon.connected || beacon.link?.sample); b.classList.toggle("ready", !b.disabled);
    b.title = !beacon.connected ? "Beacon module not connected" : !incident ? "Start an incident first" : "Engine 1 throws Beacon pucks into the fire room: phones with the app answer";
    return;
  }
  if (scout.dev) { b.disabled = scout.running; b.title = "DEV SAMPLE: plays the hand-made sample scan through the phases (N steps one phase)"; return; }
  b.disabled = !scout.supported || !incident || scout.running;
  b.title = !scout.supported ? "This server does not offer scout mode yet" : !incident ? "Start an incident first: pucks are thrown by a crew at a room's door" : scout.running ? "A scan is running" : "Engine 1 throws the pucks into the fire room from its door";
  b.classList.toggle("ready", !b.disabled);
}
function applySmoke() {
  const lvl = S.smokeForce === true ? Math.max(0.85, scout.smoke) : S.smokeForce === false ? 0 : scout.smoke;
  const on = lvl > 0.01;
  twin3d?.setSmoke(S.view === "3d" ? lvl : 0);
  $("btn-smoke").setAttribute("aria-pressed", on);
  $("btn-smoke").textContent = on ? `Smoke view${S.smokeForce == null ? ` ${Math.round(lvl * 100)}%` : ""}` : "Smoke view";
  $("smoke-claim").hidden = !(on && S.view === "3d");
  if (!(on && S.view === "3d")) $("smoke-inset").hidden = true;
}
function onInsetRect(r) {
  const el = $("smoke-inset");
  if (!r || S.view !== "3d") { el.hidden = true; return; }
  el.hidden = false;
  Object.assign(el.style, { left: r.x + "px", bottom: r.y + "px", width: r.w + "px", height: r.h + "px" });
  Object.assign($("smoke-claim").style, { left: "10px", top: "10px", bottom: "auto", width: Math.min(380, Math.round(r.W * 0.5)) + "px" });
}
function applyFocus() {
  const view = S.mode === "scout" ? scout.view() : null, bbox = S.mode === "beacon" && beacon ? beaconBox() : null;
  $("focus-seg").hidden = !view && !bbox;
  for (const b of $("focus-seg").querySelectorAll("button")) b.classList.toggle("on", b.dataset.focus === S.focus);
  let box = null;
  if (bbox && S.focus === "room") box = bbox.map((v) => Math.round(v * 2) / 2);
  if (view && S.focus === "room") {
    box = [...view.room.bbox];
    // after a few seconds on the person, widen to the crews sent to them (or bringing them out), with their routes
    const ids = new Set((scout.scan?.victims || []).map((v) => v.id)), rescuers = new Set(Object.values(scout.vstatus || {}).map((v) => v.rescuer).filter(Boolean));
    const settled = scout.phase === "done" && performance.now() - scout.phaseAt > 6000;
    for (const u of settled ? S.state?.units || [] : []) {
      if (!u.route?.polyline || u.route.done || !(rescuers.has(u.id) || (u.assignment && ids.has(u.assignment.target)))) continue;
      for (const p of u.route.polyline) { box[0] = Math.min(box[0], p[0]); box[1] = Math.min(box[1], p[1]); box[2] = Math.max(box[2], p[0]); box[3] = Math.max(box[3], p[1]); }
    }
    box = box.map((v) => Math.round(v * 2) / 2);
  }
  twin3d?.setFocus(box); plan.setFocus(box);
}
function renderLegend() {
  const base = `<span><i style="background:${UNIT_COLOR.E1}"></i>Engine 1</span><span><i style="background:${UNIT_COLOR.RIT}"></i>Rescue team</span><span><i style="background:#3fcf8e"></i>stairs</span><span><i style="background:#ff3b30"></i>lost / hazard</span><span><i style="background:#35d0ba"></i>twin tags</span>`;
  const sc = scout?.onScreen ? `<span class="lg-sep"></span><span><i class="round" style="background:#ffe3a3"></i>puck</span>` +
    (scout.scan ? `<span><i style="background:${VICTIM_COLOR}"></i>person, ±1σ / 2σ</span><span><i class="cells"></i>coarse echo map</span>${(scout.scan.mismatches || []).length ? `<span><i style="background:#ff9f0a"></i>differs from the twin</span>` : ""}` : "") : "";
  const bc = S.mode === "beacon" && beacon?.b?.phones?.length ? `<span><i class="round" style="background:${PHONE_COLOR}"></i>phone located to a point</span><span><i class="ringi"></i>ring: a distance, not a place</span><span><i class="candi"></i>one of two places</span><span><i style="background:#6f8aa8"></i>devices heard: a count, not people</span>` : "";
  $("legend").innerHTML = bc || base + (S.mode === "scout" ? sc : "");
  $("legend").classList.toggle("scan", !!(S.mode === "scout" && sc));
}
function renderScanHead() {
  const el = $("scan-head"), s = scout.scan;
  if (!scout.onScreen || S.mode !== "scout") { el.hidden = true; return; }
  el.hidden = false;
  const room = scout.room();
  const marks = [(s ? s.simulated : (S.hello?.simulated ?? true)) ? `<span class="badge sim" title="Simulated radar: no boards attached">Simulated</span>` : `<span class="badge live">Measured</span>`];
  if (scout.dev) marks.push(`<span class="badge dev" title="The hand-made sample in fixtures/scout/sample_scan.json, loaded with ?devscan=1">DEV SAMPLE</span>`);
  else if (scout.sample) marks.push(`<span class="badge dev" title="The server replays the hand-made sample scan (fake engine), not a radar result">Sample scan</span>`);
  const reg = s?.registration;
  const lim = (s?.limits || []);
  const summary = s && scout.phase === "done" ? (reg ? `Registered: ${regQuality(reg)}` : "") : scout.text;
  el.innerHTML = `<div class="st1"><span class="sh-phase ${scout.phase}">${esc(PHASE_WORD[scout.phase] || scout.phase)}</span><b>Simulated radar scan of ${esc(room?.label || scout.roomId || "the room")}</b>${marks.join("")}<span class="sh-text" title="${esc(summary)}">${esc(summary)}</span></div>` +
    (lim.length ? `<div class="st2" title="${esc(lim.join("; "))}"><b>Cannot tell:</b> ${lim.map(esc).join("; ")}</div>` : "");
}
function renderDevbar() {
  const el = $("devbar");
  if (beacon?.dev) { el.hidden = false; el.innerHTML = `<b>DEV SAMPLE</b> hand-made phones, not the Beacon module · <button id="dev-next">Next (N)</button>`; $("dev-next").onclick = () => beacon.devNext(); return; }
  if (!scout?.dev) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `<b>DEV SAMPLE</b> hand-made scan, not a radar result · <button id="dev-next">Next phase (N)</button>`;
  $("dev-next").onclick = () => (scout.devStep < 0 ? scout.devPlay(false) : scout.devNext());
}
function renderScoutCard() {
  const el = $("tab-scout"), view = scout.view(), s = scout.scan;
  if (!view) {
    el.innerHTML = `<div class="sc-empty"><p>A crew throws a handful of UWB pucks into a room from its door. The pucks range each other, work out where they lie, register into the twin and listen as a radar for a person who breathes or moves, through smoke.</p>` +
      `<p>${scout.dev ? "DEV SAMPLE mode: press <b>Throw pucks</b> or N." : !scout.supported ? "This server does not offer scout mode yet." : "Start an incident, then <b>Throw pucks</b>, or run the scout drill."}</p></div>`;
    return;
  }
  const idx = PHASES.indexOf(scout.phase), failed = scout.phase === "failed";
  const steps = PHASES.map((p, i) => `<li class="${failed ? "" : i < idx ? "done" : i === idx ? "now" : ""}">${esc(PHASE_WORD[p])}</li>`).join("");
  const rung = RUNG_WORD[s?.rung] || (s ? [s.rung, ""] : null);
  const reg = s?.registration;
  const used = s ? (s.pucks || []).length : view.ids.length;
  const links = view.links, usedLinks = links.filter((l) => l.used !== false).length;
  const facts = [
    ["Method", rung ? `<b>${esc(rung[0])}</b>: ${esc(rung[1])}` : "radar (pucks listening to each other's echoes)"],
    ["Pucks", `${view.n} thrown${s ? `, ${used} placed${s.pucks?.[0]?.sigma_m != null ? ` to ±${s.pucks[0].sigma_m} m` : ""}` : ""}; ${usedLinks} of ${links.length} links used`],
    ["Registration", reg ? esc(regQuality(reg)) : idx >= 3 ? esc(scout.regText || scout.text) : "not yet"],
  ];
  const vics = (s?.victims || []).map((v) => `<button class="sc-row vic${v.id === S.selected ? " sel" : ""}" data-pick="${esc(v.id)}"><span class="sw" style="background:${VICTIM_COLOR}"></span><span><b>${esc(v.id)} · ${esc(victimState(v))}</b><small>${esc(v.text || "")}${v.x == null ? " (position unknown with two pucks)" : ""}</small></span><span class="num">±${(v.sigma_m ?? 0).toFixed(1)} m<br>${Math.round((v.confidence ?? 0) * 100)}%</span></button>`).join("");
  const flags = (s?.mismatches || []).map((m) => `<button class="sc-row flag" data-pick="${esc(m.id)}" title="${esc(m.text)}"><span class="sw" style="background:#ff9f0a"></span><span><b>${esc(m.id)} · differs from the twin</b><small>${esc(m.text)}</small></span><span class="num">${Math.round((m.confidence ?? 0) * 100)}%</span></button>`).join("");
  const res = s?.resolution;
  el.innerHTML = `<ol class="sc-steps${failed ? " failed" : ""}">${steps}</ol>` +
    `<div class="sc-now${failed ? " failed" : ""}">${esc(failed ? `Failed: ${scout.text}` : scout.text || (s ? `Scan of ${s.room_label} landed` : ""))}</div>` +
    `<div class="sc-grid">${meshSvg(view, performance.now())}<div class="kv2">${facts.map(([k, v]) => `<div class="k">${k}</div><div>${v}</div>`).join("")}</div></div>` +
    (s ? `<div class="sc-sec"><div class="k">Found</div>${vics || `<div class="sc-none">No one who moves or breathes. A still, non-breathing person would not show.</div>`}</div>` +
      `<div class="sc-sec limits"><div class="k">What this scan cannot tell</div><ul>${(s.limits || []).map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` +
      (res ? `<div class="sc-res">Range resolution ${res.range_m} m from ${Math.round(res.bandwidth_mhz)} MHz of bandwidth at ${(res.carrier_mhz / 1000).toFixed(1)} GHz${s.simulated ? "; simulated" : ""}. Outline: ${s.outline?.source === "box-fit" ? "a coarse box fitted to echoes (no twin)" : "the twin's room"}.</div>` : "") + `</div>` +
      (flags ? `<div class="sc-sec"><div class="k">Where the room differs from the twin</div>${flags}</div>` : "") : "");
  for (const b of el.querySelectorAll("[data-pick]")) b.onclick = () => onPick(b.dataset.pick);
}
function setTab(t) {
  S.tab = t;
  for (const b of document.querySelectorAll(".tabs .tab")) b.classList.toggle("on", b.dataset.tab === t);
  $("tab-record").hidden = t !== "record"; $("tab-scout").hidden = t !== "scout"; $("tab-acc").hidden = t !== "acc";
  $("record-src").hidden = t !== "record";
}
function closePops() {
  $("story-menu").hidden = true; $("btn-story-menu").setAttribute("aria-expanded", "false");
  if (!$("feeds-pop").hidden) { $("feeds-pop").hidden = true; $("btn-feeds").setAttribute("aria-expanded", "false"); S._es?.close(); S._es = null; clearInterval(S._feedTimer); }
}
const FEEDS = [
  { path: "/feed/scan.geojson", name: "GeoJSON", what: "pucks, the person, flags, the room outline and every crew, WGS84" },
  { path: "/feed/cot.xml", name: "Cursor-on-Target", what: "the same as CoT 2.0 events" },
  { path: "/feed/cot/stream", name: "CoT stream", what: "Server-Sent Events, one event per change" },
];
async function refreshFeeds() {
  if (!scout.supported) { $("feeds-list").innerHTML = `<div class="feed off">This server does not publish the scan feeds yet.</div>`; return; }
  const counts = {};
  try { const g = await (await fetch(FEEDS[0].path)).json(); const by = {}; for (const f of g.features || []) { const k = f.properties?.kind || f.geometry?.type || "feature"; by[k] = (by[k] || 0) + 1; } counts[0] = `${(g.features || []).length} features` + (Object.keys(by).length ? ` (${Object.entries(by).map(([k, n]) => `${n} ${k}`).join(", ")})` : ""); } catch { counts[0] = "not available"; }
  try { const x = await (await fetch(FEEDS[1].path)).text(); counts[1] = `${(x.match(/<event[\s>]/g) || []).length} events`; } catch { counts[1] = "not available"; }
  counts[2] = `${S._esN || 0} events since opened`;
  $("feeds-n").textContent = counts[0] && /^\d/.test(counts[0]) ? counts[0].split(" ")[0] : "";
  $("feeds-list").innerHTML = FEEDS.map((f, i) => `<div class="feed"><div class="fh"><b>${esc(f.name)}</b><code>${esc(f.path)}</code></div><div class="fw">${esc(f.what)}</div>` +
    `<div class="fa"><span class="cnt" id="feed-c${i}">${esc(counts[i])}</span><button class="mini" data-copy="${esc(f.path)}">Copy URL</button>${i < 2 ? `<a class="mini" href="${esc(f.path)}" target="_blank" rel="noopener">Open</a>` : ""}</div></div>`).join("");
  for (const b of $("feeds-list").querySelectorAll("[data-copy]")) b.onclick = () => copyText(location.origin + b.dataset.copy);
}
function copyText(t) {
  const done = () => toast(`Copied ${t}`);
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(t).then(done, () => fallback());
  else fallback();
  function fallback() { const i = document.createElement("textarea"); i.value = t; document.body.appendChild(i); i.select(); try { document.execCommand("copy"); done(); } catch { toast(t); } i.remove(); }
}
function wireScoutControls() {
  $("btn-throw").onclick = () => {
    if (S.mode === "beacon") { if (beacon.dev) beacon.devPlay(true); else api("/api/beacon/pucks", { n: 3 }).catch(() => {}); return; }
    if (scout.dev) { scout.devPlay(true); return; }
    api("/api/scout/deploy", {}).catch(() => {});
  };
  $("btn-story-menu").onclick = (e) => { e.stopPropagation(); const m = $("story-menu"); m.hidden = !m.hidden; $("btn-story-menu").setAttribute("aria-expanded", String(!m.hidden)); };
  for (const r of document.querySelectorAll('#story-menu input[name="script"]')) r.onchange = () => setMode(MODE_OF_SCRIPT[r.value]);
  for (const b of $("mode-seg").querySelectorAll("button")) b.onclick = () => { S._tabPinned = false; setMode(b.dataset.mode); };
  $("opt-guided").onchange = (e) => { S.guided = e.target.checked; renderState(); };
  $("story-menu").onclick = (e) => e.stopPropagation();
  $("btn-place").onclick = () => (S.place ? stopPlacing() : startPlacing());
  $("btn-smoke").onclick = () => { const on = $("btn-smoke").getAttribute("aria-pressed") === "true"; S.smokeForce = !on; applySmoke(); };
  for (const b of $("focus-seg").querySelectorAll("button")) b.onclick = () => { S.focus = b.dataset.focus; applyFocus(); };
  for (const b of document.querySelectorAll(".tabs .tab")) b.onclick = () => { S._tabPinned = true; setTab(b.dataset.tab); };
  $("btn-feeds").onclick = (e) => {
    e.stopPropagation();
    const p = $("feeds-pop"); if (!p.hidden) { closePops(); return; }
    p.hidden = false; $("btn-feeds").setAttribute("aria-expanded", "true");
    S._esN = 0; refreshFeeds();
    if (scout.supported && "EventSource" in window) {
      S._es = new EventSource(FEEDS[2].path);
      S._es.onmessage = () => { S._esN++; const c = $("feed-c2"); if (c) c.textContent = `${S._esN} events since opened`; };
      S._es.onerror = () => { S._es?.close(); S._es = null; const c = $("feed-c2"); if (c) c.textContent = "stream closed"; };
    }
    S._feedTimer = setInterval(refreshFeeds, 3000);
  };
  $("feeds-pop").onclick = (e) => e.stopPropagation();
  addEventListener("click", closePops);
}

// ---------------------------------------------------------------- beacon: phones, accountability, mode
const SCRIPT_OF_MODE = { beacon: "beacon", scout: "scout", crews: "mayday" };
const MODE_OF_SCRIPT = { beacon: "beacon", scout: "scout", mayday: "crews" };
function setMode(m, quiet) {
  S.mode = m; S.script = SCRIPT_OF_MODE[m];
  for (const b of $("mode-seg").querySelectorAll("button")) b.classList.toggle("on", b.dataset.mode === m);
  for (const r of document.querySelectorAll('#story-menu input[name="script"]')) r.checked = r.value === S.script;
  if (!S._tabPinned) setTab(m === "beacon" ? "acc" : m === "scout" ? (scout.onScreen ? "scout" : "record") : "record");
  $("btn-place").hidden = m !== "beacon"; if (m !== "beacon" && S.place) stopPlacing();
  if (scout && beacon) { const sv = m === "scout" ? scout.view() : null; twin3d?.setScout(sv); plan.setScout(sv); onBeaconChange(); }
  renderBadges(); renderBeaconHead(); renderScanHead(); renderThrow(); renderLegend(); applyFocus();
  if (S.state && !quiet) renderState();
}
function onBeaconChange() {
  // each mode draws its own layer: phones in Beacon, the radar scan in Scout (both keep their data)
  const view = S.mode === "beacon" ? beacon.view() : null;
  twin3d?.setBeacons(view, S.selected); plan.setBeacons(view, S.selected); aerial.setBeacons?.(view);
  $("acc-dot").hidden = !beacon.b;
  if (!S._pinned && S.mode === "beacon" && !beacon.phone(S.selected) && !S.state?.mayday) {
    const rank = { trapped: 0, injured: 1, unknown: 2, sheltering: 3 };
    const first = beacon.phones().filter((p) => !p.report?.safe_outside && p.geometry !== "none").sort((a, b) => (rank[a.report?.status] ?? 2) - (rank[b.report?.status] ?? 2) || (a.geometry === "fix" ? -1 : 1))[0];
    if (first) S.selected = first.id;
  }
  if (!scout.onScreen && S.selected && /^B\d/.test(S.selected) && !beacon.phone(S.selected)) S.selected = "E1";
  renderAcc(); renderBeaconHead(); renderLegend(); renderThrow(); applyFocus();
  if (S.state) renderState(); else renderHere();
}
function phoneRows() {
  const order = { trapped: 0, injured: 1, unknown: 2, sheltering: 3, ok: 4 };
  return beacon.phones().slice().sort((a, b) => (order[a.report?.status] ?? 2) - (order[b.report?.status] ?? 2) || a.id.localeCompare(b.id)).map((p) => {
    const r = p.report || {}, out = r.safe_outside;
    const short = { fix: `fix ±${(p.sigma_m ?? 0).toFixed(1)} m`, candidates: `${p.candidates?.length} places: cannot choose`, ring: p.ring?.kind === "ble" ? "BT ring" : "UWB ring", zone: "room only", none: out ? "outside" : "no position" }[p.geometry] || p.geometry;
    const inc = (S.state?.phones || []).find((q) => q.id === p.id) || {};
    const asg = (S.state?.units || []).find((u) => u.id === inc.rescuer) || (S.state?.units || []).find((u) => u.assignment && u.assignment.target === p.id);
    const withThem = ["reached", "evacuating", "out"].includes(inc.status);
    return `<div class="unit-row phone ${p.id === S.selected ? "sel" : ""}${p.stale ? " stale" : ""}" data-unit="${esc(p.id)}">` +
      `<div class="cs" style="color:${PHONE_COLOR}">${esc(p.id)}</div>` +
      `<div style="min-width:0"><div class="loc">${esc(out ? "outside the building" : p.room_label || p.zone?.room_label || (p.geometry === "ring" ? `near puck ${p.ring.puck}` : p.geometry === "candidates" ? [...new Set(p.candidates.map((c) => c.room_label || "?"))].join(" or ") : "where unknown"))}<span class="pill st-${esc(r.status || "unknown")}">${esc(STATUS_WORD[r.status] || "no status")}</span></div>` +
      `<div class="asg">${esc(reportWords(r))}${asg ? ` · ${esc(asg.label)} ${withThem ? (inc.status === "out" ? "brought them out" : "is with them") : "sent"}` : ""}</div></div>` +
      `<div class="nums">${esc(short)}<br>${r.battery_pct != null ? `${r.battery_pct}% · ` : ""}${esc(ago(p.track?.last_seen_s ?? r.age_s))}</div></div>`;
  }).join("");
}
function renderHerePhone(p) {
  const el = $("here"), { twin } = S.bundle, r = p.report || {};
  const roomId = p.room || p.zone?.room || p.candidates?.[0]?.room || (p.ring && twin.rooms.find((q) => q.id === beacon.b?.room)?.id);
  const room = twin.rooms.find((q) => q.id === roomId);
  const pt = p.geometry === "fix" ? [p.x, p.y] : p.geometry === "candidates" ? [p.candidates[0].x, p.candidates[0].y] : room ? [room.polygon.reduce((s, q) => s + q[0], 0) / room.polygon.length, room.polygon.reduce((s, q) => s + q[1], 0) / room.polygon.length] : null;
  const node = p.node || (pt ? plan.nearestNode(pt[0], pt[1], room?.id) : null);
  const exits = node ? plan.exitDistances(node, S.state?.hazards || []) : [];
  const tenants = (room?.suite && S.tenantsBySuite[room.suite]) || [];
  const side = pt ? nearestSide({ x: pt[0], y: pt[1] }) : null;
  const geo = p.geometry === "fix" ? (p.geo || (() => { const g = aerial.geo(p.x, p.y); return { lat: g[0], lon: g[1] }; })()) : null;
  const where = p.geometry === "fix" && room ? wallDistances(twin, room, p.x, p.y) + ((p.pruned || []).length ? `; the mirror place is ruled out (${p.pruned[0].reason})` : "") : p.geometry === "ring" ? `search the arc ${p.ring.r_m.toFixed(1)} m from puck ${p.ring.puck}` : p.geometry === "candidates" ? `two mirror places; the twin cannot choose: check both, ${Math.round(p.candidates[0].weight * 100)}% first` : p.geometry === "zone" ? "search the room" : "";
  const rec = tenants.length ? tenants.map((t) => `<div>${esc(t.industry)}</div>` + (t.life_hazard ? `<div class="hzline">${esc(t.life_hazard)}</div>` : "")).join("") : (room?.suite ? "no active tenant in the record for this suite" : "—");
  el.innerHTML = `<div><div class="who"><span class="dot" style="background:${PHONE_COLOR}"></span>${esc(p.id)}: phone${r.people != null ? `, ${r.people} ${r.people === 1 ? "person" : "people"}` : ""}</div><div class="where">${esc(r.safe_outside ? "Outside" : room?.label || "—")}</div><div class="sub st-${esc(r.status || "unknown")}">${esc(reportWords(r))}</div></div>` +
    `<div><div class="k"><span class="dot matterport"></span>Twin: where</div><div>${esc(geomWords(p))}</div><div>${esc(where)}</div>${exits.map((e) => `<div>${esc(e.label)}: ${e.m == null ? "<b class='hzline'>cut off</b>" : e.m + " m"}</div>`).join("")}</div>` +
    `<div><div class="k"><span class="dot costar"></span>Record: who is usually in there</div>${rec}</div>` +
    `<div><div class="k"><span class="dot vexcel"></span>Aerial: where outside</div><div>${side ? `behind side ${side}` : ""}</div><div>${geo ? `${geo.lat.toFixed(5)}, ${geo.lon.toFixed(5)}` : "no point: not a fix"}</div></div>` +
    `<div><div class="k"><span class="dot scan"></span>Phone: what it says${p.simulated ? " (simulated)" : ""}</div>${r.text ? `<div>“${esc(r.text)}”</div>` : ""}<div>${r.battery_pct != null ? `battery ${r.battery_pct}%, ` : ""}heard ${esc(ago(p.track?.last_seen_s ?? r.age_s))}</div><div class="faintline">${esc(p.method)}, confidence ${Math.round((p.confidence ?? 0) * 100)}%${p.stale ? ", not heard recently" : ""}</div></div>`;
}
function renderBeaconHead() {
  const el = $("beacon-head");
  if (S.mode !== "beacon") { el.hidden = true; return; }
  el.hidden = false;
  const b = beacon.b;
  if (!beacon.connected && !b && !beacon.link?.sample) {
    el.className = "scan-head beacon-head off";
    el.innerHTML = `<div class="st1"><span class="sh-phase failed">Not connected</span><b>Beacon module not connected</b><span class="sh-text">No phone data reaches this board; crews, the record, the twin and the aerial still work. Start the Beacon server (port 1885) or run with SANDTABLE_BEACON=sample.</span></div>`;
    return;
  }
  el.className = "scan-head beacon-head";
  if (!b) { el.innerHTML = `<div class="st1"><span class="sh-phase">Waiting</span><b>Beacon module connected</b><span class="sh-text">No pucks in a room yet: Throw pucks, or run the beacon drill.</span></div>`; return; }
  const ph = b.phones || [], ppl = ph.reduce((s, p) => s + (p.report?.safe_outside ? 0 : p.report?.people || 0), 0), dev = (b.presence || []).reduce((s, c) => s + (c.count || 0), 0);
  const marks = [b.simulated ? `<span class="badge sim" title="Simulated phones and pucks (${esc(b.source)})">Simulated</span>` : `<span class="badge live">Real pucks</span>`];
  if (beacon.dev) marks.push(`<span class="badge dev" title="Hand-made sample story built on the board with ?devbeacons=1">DEV SAMPLE</span>`);
  else if (beacon.sample) marks.push(`<span class="badge dev" title="The server replays a hand-made sample Beacons object">Sample phones</span>`);
  if (!beacon.dev && !beacon.link?.connected) marks.push(beacon.link?.source === "replay" ? `<span class="badge off" title="The Beacon module is not connected live; these phones come from a replayed Beacon feed">Replay, module not live</span>` : `<span class="badge off" title="The Beacon server does not answer; what is shown is the labelled sample">Beacon module not connected</span>`);
  const nIn = ph.filter((p) => !p.report?.safe_outside).length, nFix = ph.filter((p) => p.geometry === "fix").length;
  const summary = `${nIn} phone${nIn === 1 ? "" : "s"} with the app inside, ${ppl} ${ppl === 1 ? "person" : "people"} reported, ${nFix} located to a point${dev ? `; ${dev} device${dev === 1 ? "" : "s"} heard (devices, not people)` : ""}`;
  const lim = b.limits || [];
  el.innerHTML = `<div class="st1"><span class="sh-phase done">Phones</span><b>${b.room_label ? `Pucks in ${esc(b.room_label)}` : "No pucks thrown yet"}</b>${marks.join("")}<span class="sh-text" title="${esc(summary)}">${esc(summary)}</span></div>` +
    (lim.length ? `<div class="st2" title="${esc(lim.join("; "))}"><b>Cannot tell:</b> ${lim.map(esc).join("; ")}</div>` : "");
}
function renderAcc() {
  const el = $("tab-acc"), rows = beacon.rows(), tot = beacon.totals();
  if (!rows.length) {
    el.innerHTML = `<div class="sc-empty"><p>The building record's suites against the phones heard: who the record expects, who reported in, who is still unaccounted for.</p><p>${beacon.connected ? "No pucks in a room yet." : "<b>Beacon module not connected.</b> No phone data; the record is in the Record tab."}</p></div>`;
    return;
  }
  const rungs = beacon.b?.rungs || {};
  const rw = beacon.b ? `<div class="acc-rungs" title="What the current Beacon source can deliver">${[["cold", "device count"], ["ble", "Bluetooth"], ["uwb", "UWB"], ["bearing", "bearing"]].map(([k, w]) => `<span class="rung ${rungs[k] ? "on" : "off"}">${w}</span>`).join("")}</div>` : "";
  const order = rows.slice().sort((a, b) => (a.priority ?? 9) - (b.priority ?? 9) || String(a.suite).localeCompare(String(b.suite)));
  const range = (r) => (r ? (r.low === r.high ? `${r.low}` : `${r.low}–${r.high}`) : "?");
  el.innerHTML = rw + (tot ? `<div class="acc-tot"><div><b>${tot.people_reported ?? 0}</b> ${tot.people_reported === 1 ? "person" : "people"} reported by <b>${tot.phones_opt_in ?? 0}</b> phone${tot.phones_opt_in === 1 ? "" : "s"}, <b>${tot.located ?? 0}</b> located to a point</div><div>${tot.expected_people != null ? `about ${tot.expected_people} expected on the floor (an estimate from the record)` : "no occupancy estimate without the Beacon module"}${tot.cold_devices ? `; ${tot.cold_devices} devices heard (not people)` : ""}${tot.suites_unscanned ? `; ${tot.suites_unscanned} suites not scanned` : ""}</div></div>` : "") +
    `<div class="acc-rows">` + order.map((r) => {
      const crew = r.reached ? `${esc(r.assigned || (r.crews || [])[0] || "crew")} reached` : r.assigned ? `${esc(r.assigned)} sent` : (r.crews || []).length ? `${esc(r.crews.join(", "))} inside` : "";
      return `<div class="acc-row st-${esc(r.state)}${r.out ? " out" : ""}" data-suite="${esc(r.suite)}" title="${esc(r.expected?.basis ? `Expected: ${r.expected.basis}` : "")}${r.why ? `\n${esc(r.why)}` : ""}">` +
        `<div class="su">${esc(r.suite)}${r.priority != null && r.priority <= 2 ? `<span class="prio">P${r.priority}</span>` : ""}</div>` +
        `<div class="mid"><div class="tn">${esc(r.tenant || r.industry || (r.vacant ? "vacant on record" : "no tenant on record"))}</div>${r.life_hazard ? `<div class="hz">${esc(r.life_hazard)}</div>` : ""}` +
        `<div class="nn">${r.expected ? `<span>expects ${range(r.expected)}</span>` : ""}${r.phones_opt_in ? `<span class="ph">${r.phones_opt_in} phone${r.phones_opt_in > 1 ? "s" : ""}, ${r.people_reported} reported${r.located ? `, ${r.located} located` : ""}</span>` : ""}${r.cold_devices ? `<span>${r.cold_devices} devices</span>` : ""}</div></div>` +
        `<div class="rt"><div class="un">${range(r.unaccounted)}</div><div class="ul">unaccounted</div><div class="stw">${esc(STATE_WORD[r.state] || r.state || "")}</div>${crew ? `<div class="crew">${crew}${r.out ? ", out" : ""}</div>` : ""}</div></div>`;
    }).join("") + `</div>` + ((beacon.acc?.limits || beacon.b?.limits || []).length ? `<div class="acc-lim">${(beacon.acc?.limits || []).concat(["Unaccounted is a range: the record's expected occupancy is an estimate"]).slice(0, 3).map(esc).join(". ")}.</div>` : "");
  for (const row of el.querySelectorAll(".acc-row")) {
    row.onmouseenter = () => highlightSuite(row.dataset.suite, true);
    row.onmouseleave = () => highlightSuite(row.dataset.suite, false);
    row.onclick = () => { const r = S.bundle.twin.rooms.find((q) => q.suite === row.dataset.suite); const p = beacon.phones().find((q) => (q.room || q.zone?.room) === r?.id); if (p) onPick(p.id); };
  }
}
// ---------------------------------------------------------------- placing pucks by hand (tape-measured positions)
function roomAtPoint(x, y) {
  const { twin } = S.bundle;
  const inside = (poly) => { let c = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const [xi, yi] = poly[i], [xj, yj] = poly[j]; if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c; } return c; };
  return twin.rooms.find((r) => r.floor === twin.exercise_floor && r.kind !== "corridor" && inside(r.polygon)) || twin.rooms.find((r) => r.floor === twin.exercise_floor && inside(r.polygon)) || null;
}
function startPlacing() {
  S.place = { pucks: [], n: 2, room: null };
  setView("2d"); plan.setPlacing(S.place); renderPlace();
}
function stopPlacing() { S.place = null; plan.setPlacing(null); renderPlace(); }
function onPlace(kind, id, x, y) {
  const P = S.place; if (!P) return;
  if (kind === "add") {
    if (P.pucks.length >= P.n) return toast(`${P.n} pucks placed: drag one to move it, or add a puck`);
    const room = roomAtPoint(x, y); if (!P.room && room) P.room = room;
    const used = new Set(P.pucks.map((q) => q.id)); let k = 1; while (used.has(`P${k}`)) k++;
    P.pucks.push({ id: `P${k}`, x: +x.toFixed(2), y: +y.toFixed(2) });
  } else if (kind === "move") { const q = P.pucks.find((q) => q.id === id); if (q) { q.x = +x.toFixed(2); q.y = +y.toFixed(2); } }
  plan.setPlacing(P); renderPlace();
}
function renderPlace() {
  const el = $("place-panel"), P = S.place;
  $("btn-place").classList.toggle("on", !!P);
  if (!P) { el.hidden = true; return; }
  el.hidden = false;
  const r = P.room, ox = r ? Math.min(...r.polygon.map((p) => p[0])) : 0, oy = r ? Math.min(...r.polygon.map((p) => p[1])) : 0;
  const next = P.pucks.length < P.n ? `Click the plan where P${P.pucks.length + 1} lies.` : "Drag a puck to adjust, or type the tape measurements.";
  el.innerHTML = `<div class="pp-h">Place pucks by hand</div><div class="pp-hint">${next} Positions are tape-measured, not located by the system.</div>` +
    `<div class="pp-rows">${P.pucks.map((q) => `<div class="pp-row" data-id="${q.id}"><b>${q.id}</b><label>from west wall <input type="number" step="0.05" data-k="x" value="${(q.x - ox).toFixed(2)}"> m</label><label>from south wall <input type="number" step="0.05" data-k="y" value="${(q.y - oy).toFixed(2)}"> m</label><button class="mini" data-del="${q.id}" aria-label="Remove ${q.id}">×</button></div>`).join("")}</div>` +
    `<div class="pp-room">${r ? `Walls of ${esc(r.label)}` : "Measured from the floor's south-west corner"}</div>` +
    `<div class="pp-act"><button class="mini" id="pp-add">Add a puck</button><button class="mini" id="pp-cancel">Cancel</button><button id="pp-send" class="primary"${P.pucks.length ? "" : " disabled"}>Send ${P.pucks.length || ""} to Beacon</button></div>`;
  for (const inp of el.querySelectorAll("input")) inp.onchange = () => {
    const q = P.pucks.find((z) => z.id === inp.closest(".pp-row").dataset.id); const v = Number(inp.value); if (!q || !isFinite(v)) return;
    if (inp.dataset.k === "x") q.x = +(ox + v).toFixed(2); else q.y = +(oy + v).toFixed(2);
    plan.setPlacing(P);
  };
  for (const b of el.querySelectorAll("[data-del]")) b.onclick = () => { P.pucks = P.pucks.filter((q) => q.id !== b.dataset.del); plan.setPlacing(P); renderPlace(); };
  $("pp-add").onclick = () => { P.n = Math.min(5, P.n + 1); renderPlace(); };
  $("pp-cancel").onclick = stopPlacing;
  $("pp-send").onclick = async () => {
    const pucks = P.pucks.map((q) => ({ id: q.id, x: q.x, y: q.y, sigma_m: 0.05, source: "hand" }));
    beacon.hand = Object.fromEntries(pucks.map((q) => [q.id, [q.x, q.y]]));
    if (beacon.dev) { toast("DEV SAMPLE: pucks kept on the board, not sent"); stopPlacing(); return; }
    try { await api("/api/beacon/pucks", { pucks, room: P.room?.id, floor: S.bundle.twin.exercise_floor }); toast(`Sent ${pucks.map((q) => q.id).join(", ")} to the Beacon module (placed by hand)`); stopPlacing(); onBeaconChange(); } catch { /* the toast says why */ }
  };
}
function beaconBox() {
  const b = beacon.b; if (!b || !b.room || !(b.pucks || []).length) return null;
  const r = S.bundle.twin.rooms.find((q) => q.id === b.room); if (!r) return null;
  const box = [Math.min(...r.polygon.map((p) => p[0])), Math.min(...r.polygon.map((p) => p[1])), Math.max(...r.polygon.map((p) => p[0])), Math.max(...r.polygon.map((p) => p[1]))];
  // the puck room fills the panel; candidates and ruled-out mirror places just outside it stay in the frame
  for (const p of b.phones || []) for (const q of [...(p.candidates || []), ...(p.pruned || [])]) {
    if (q.x < box[0] - 6 || q.x > box[2] + 6 || q.y < box[1] - 6 || q.y > box[3] + 6) continue;
    box[0] = Math.min(box[0], q.x - 1); box[1] = Math.min(box[1], q.y - 1.4); box[2] = Math.max(box[2], q.x + 1); box[3] = Math.max(box[3], q.y + 1);
  }
  return box;
}

// ---------------------------------------------------------------- wiring
function setView(v) {
  S.view = v;
  if (v === "3d" && !twin3d) v = S.view = "2d";
  $("twin3d").hidden = v !== "3d"; $("plan").toggleAttribute("hidden", v !== "2d");
  for (const b of $("view-seg").querySelectorAll("button")) b.classList.toggle("on", b.dataset.view === v);
  if (v === "3d") twin3d.resize();
  if (scout) applySmoke();
}
function setInject(on) {
  S.inject = on; $("inject-hint").hidden = !on; $("btn-inject").classList.toggle("on", on);
  plan.setInject(on);
  if (on) setView("2d");
}
function resetUi() {
  S.runs.clear(); S.preplan = null; S.aar = null; S._pinned = false; S.selected = "E1"; S.smokeForce = null; S.focus = "room";
  scout.reset(); applySmoke(); if (!beacon.dev) beacon.reset();
  twin3d?.setFocus(null); plan.setFocus(null);
  $("agent-log").innerHTML = `<div class="empty">Dispatch the alarm: the agent reads the record, the aerial and the twin, and writes the pre-plan.</div>`;
  $("radio").innerHTML = `<div class="empty">No traffic.</div>`;
  $("btn-preplan").disabled = true; $("btn-aar").disabled = true; $("doc").hidden = true;
  plan.setPreplan(null); twin3d?.setPreplan?.(null);
}
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  let live = false;
  setTimeout(() => (live = true), 1500);
  ws.onmessage = (e) => {
    let m; try { m = JSON.parse(e.data); } catch { return; }
    switch (m.type) {
      case "hello": S.hello = m; renderBadges(); break;
      case "state": S.state = m; renderState(); renderBadges(); break;
      case "agent": onAgent(m); break;
      case "radio": onRadio(m, live); break;
      case "preplan": S.preplan = m; $("btn-preplan").disabled = false; plan.setPreplan(m); twin3d?.setPreplan?.(m); break;
      case "aar": S.aar = m; $("btn-aar").disabled = false; if (live) showAar(); break;
      case "tag": plan.addTag(m.tag); twin3d?.addTag?.(m.tag); break;
      case "alert": if (m.level !== "mayday") toast(m.text); break;
      case "reset": resetUi(); break;
      case "scout": scout.onScout(m, live); break;
      case "beacons": if (!beacon.dev) beacon.onBeacons(m, live); break;
      case "accountability": if (!beacon.dev) beacon.onAccountability(m); break;
      case "scan": scout.onScan(m, live); if (live && !S._pinned && scout.mainVictim()) { S.selected = scout.mainVictim().id; renderState(); } break;
      default: break;
    }
  };
  ws.onclose = () => { if (S.hello) { S.hello = { ...S.hello, hub: { ...S.hello.hub, connected: false } }; } $("badges").innerHTML = `<span class="badge off">server disconnected: retrying</span>`; S.runs.clear(); $("agent-log").innerHTML = ""; $("radio").innerHTML = ""; setTimeout(connect, 1200); };
}

async function main() {
  S.bundle = await api("/api/bundle");
  plan = new Plan($("plan"), S.bundle, {
    color: UNIT_COLOR,
    onMove: (unit, x, y) => api("/api/unit/move", { unit, x, y }).catch(() => {}),
    onInject: (ref) => { setInject(false); api("/api/inject", { ref, kind: "blocked", note: "reported lost by command" }).catch(() => {}); },
    onSelect: (unit) => { S.selected = unit; S._pinned = true; renderHere(); },
    canDrag: () => !!S.hello?.drivable,
  });
  aerial = new Aerial($("aerial-img"), $("aerial-svg"), $("aerial-seg"), $("aerial-src"), S.bundle, UNIT_COLOR);
  renderRecord();
  scout = new ScoutModel(S.bundle, onScoutChange);
  beacon = new BeaconModel(S.bundle, onBeaconChange);
  plan.setOutside(S.bundle.aerial.images.find((i) => i.id === "ortho") || S.bundle.aerial.images[0]);
  plan.opts.onPick = onPick; plan.opts.onPlace = onPlace;
  renderLegend();
  try {
    const mod = await import("./twin3d.js");
    twin3d = new mod.Twin3D($("twin3d"), S.bundle, UNIT_COLOR, { onPick, onInsetRect });
  } catch (err) { console.warn("3D view unavailable, using the plan:", err); twin3d = null; }
  setView(twin3d ? "3d" : "2d");
  for (const b of $("view-seg").querySelectorAll("button")) b.onclick = () => setView(b.dataset.view);
  $("btn-alarm").onclick = () => api("/api/alarm", {});
  $("btn-story").onclick = (e) => api("/api/storyboard", S.state?.storyboard?.running ? { action: "stop" } : { action: "start", script: S.script, mode: (!S.hello?.drivable || S.guided || e.shiftKey) ? "assist" : "auto" }).catch(() => {});
  wireScoutControls();
  $("btn-inject").onclick = () => setInject(!S.inject);
  $("btn-mayday").onclick = () => api("/api/mayday", { unit: S.selected || "E1" });
  $("btn-reset").onclick = () => api("/api/reset", {});
  $("btn-sound").onclick = (e) => { S.sound = !S.sound; e.target.textContent = S.sound ? "Sound on" : "Sound off"; e.target.setAttribute("aria-pressed", S.sound); if (!S.sound) speechSynthesis?.cancel(); };
  $("btn-preplan").onclick = showPreplan; $("btn-aar").onclick = showAar;
  $("doc-close").onclick = () => ($("doc").hidden = true);
  addEventListener("keydown", (e) => {
    if (e.key === "Escape") { setInject(false); $("doc").hidden = true; closePops(); if (S.place) stopPlacing(); }
    if ((e.key === "n" || e.key === "N") && !e.target.closest?.("input, textarea") && !S.place) { if (S.mode === "beacon" && beacon.dev) beacon.devNext(); else if (scout.dev) scout.devNext(); }
  });
  addEventListener("resize", () => twin3d?.resize());
  const qm = new URLSearchParams(location.search).get("mode");
  setMode(MODES[qm] ? qm : beacon.dev || !scout.dev ? "beacon" : "scout", true);
  renderScoutCard(); renderDevbar(); renderAcc();
  connect();
}
main().catch((err) => { console.error(err); document.body.insertAdjacentHTML("beforeend", `<div class="toast">Could not load the board: ${esc(err.message)}</div>`); });
