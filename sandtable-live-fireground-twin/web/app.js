// Sandtable command board. Static ES modules, no build step, no network beyond this origin.
// The story reads top to bottom: 1 Know before you go (the building), 2 Beacon on scene (the phones), 3 Command (orders).
// The table on the left (plan, 3D twin, aerial, scan) is the hero; the story column on the right follows the incident.
import { Plan } from "./plan2d.js";
import { Aerial } from "./aerial.js";
import { BeaconModel, MODES, mirrorCaption } from "./beacon.js";
import { ScoutModel, victimState, sigmaEllipse, wallDistances } from "./scout.js";
import { $, esc, mmss, ago, plural, stamp, layerKind, agentKind, statusChip, geomShort, peopleWords, setHTML, palette, unitColors, initialTheme, applyTheme, SHAPE_PATH } from "./common.js";
import { knowHTML, storeys, planFloorNo, obliqueSides } from "./know.js";
import { sceneHTML, sortedPhones, beaconSource } from "./scene.js";
import { Command, preplanDoc, aarDoc, parLine, taskWords, party } from "./command.js";
import { Placer } from "./place.js";

const S = { bundle: null, hello: null, state: null, preplan: null, aar: null, selected: null, sound: false, inject: false, view: "3d", script: "beacon", mode: "beacon", guided: false,
  step: "know", focus: "room", smokeForce: null, closing: null, scan: null, openFloor: null, keyOpen: null, prevPhase: null, prevPucks: 0, prevPhones: 0, tenantsBySuite: {} };
let plan, aerial, twin3d = null, scout, beacon, cmd, placer, pal;

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { toast((await r.json().catch(() => ({}))).detail || `${path}: ${r.status}`); throw new Error(path); }
  return r.json();
}
function toast(text) { const t = $("toast"); t.textContent = text; t.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 4200); }
const active = () => { const ph = S.state?.phase; return !!ph && !["standby", "terminated"].includes(ph); };
const live = () => ["costar", "matterport", "vexcel"].some((k) => S.bundle?.labels[k]?.mode === "live");
const isUnit = (id) => !!S.state?.units?.some((u) => u.id === id);

// ---------------------------------------------------------------- header
const PHASE_WORD = { standby: "Standby", preplan: "Pre-plan", operations: "Operations", mayday: "Mayday", rescue: "Rescue", egress: "Withdrawing", terminated: "Ended" };
function renderHeader() {
  const st = S.state, ph = st?.phase || "standby";
  const el = $("phase"); el.textContent = PHASE_WORD[ph] || ph; el.className = `phase p-${ph}`;
  $("clock").textContent = mmss(st?.elapsed_s);
  const b = $("btn-primary"), sb = st?.storyboard;
  let act = null;
  if (ph === "standby") act = ["alarm", "Dispatch alarm"];
  else if (ph === "terminated") act = ["report", "Open the report"];
  else if (!sb?.running && S.mode === "beacon" && !(beacon.b?.pucks || []).length && !placer.on) act = ["place", "Place pucks"];
  else if (!sb?.running && S.mode === "scout" && !scout.onScreen && scout.supported) act = ["throw", "Throw pucks"];
  b.hidden = !act; if (act) { b.dataset.act = act[0]; b.textContent = act[1]; }
  $("btn-story").textContent = sb?.running ? "Stop exercise" : `${S.guided || !S.hello?.drivable ? "Guided" : "Run"} ${{ mayday: "mayday", scout: "scout", beacon: "beacon" }[S.script]} exercise`;
  const mu = isUnit(S.selected) ? S.selected : "E1", uLabel = st?.units?.find((u) => u.id === mu)?.label || mu;
  const m = $("btn-mayday"); m.disabled = !active() || !!st?.mayday; m.title = `Declare a mayday for ${uLabel}`;
  const bar = $("mayday-bar");
  if (st?.mayday) {
    const u = st.units.find((q) => q.id === st.mayday.unit);
    bar.hidden = false;
    $("mayday-text").textContent = `${u?.label || st.mayday.unit}, ${st.mayday.room_label || "position unknown"}: ${{ no_motion: "no movement", button: "tag button pressed", manual: "declared by command" }[st.mayday.reason] || st.mayday.reason}`;
    $("mayday-timer").textContent = mmss(st.mayday.elapsed_s);
  } else bar.hidden = true;
  const cue = $("cue"), yours = sb?.running && /^YOUR MOVE/.test(sb.step || "");
  cue.hidden = !yours; if (yours) cue.textContent = sb.step.replace(/^YOUR MOVE:\s*/, "Your move: ");
  const ex = $("exstep");
  const exHTML = sb?.running && !yours ? `<b>${sb.mode === "assist" ? "Guided exercise:" : "Exercise:"}</b> ${esc(sb.step)}` : "";
  setHTML(ex, exHTML); ex.title = sb?.running ? `${sb.step}${sb.playing ? ` (${sb.playing})` : ""}` : "";
}

// ---------------------------------------------------------------- the story column
const STEPS = ["know", "scene", "command"];
function setStep(step, auto) {
  if (!STEPS.includes(step)) return;
  S.step = step;
  if (auto) {
    if (step === "scene") setView(S.mode === "scout" ? (twin3d ? "3d" : "2d") : "2d");
    if (step === "command" && S.view !== "2d" && S.view !== "3d") setView("2d");
  }
  renderStory(true);
}
function renderStory(force) {
  const st = S.state;
  for (const s of STEPS) {
    const sec = document.querySelector(`.step[data-step="${s}"]`), open = S.step === s;
    sec.classList.toggle("open", open);
    sec.querySelector(".step-btn").setAttribute("aria-expanded", String(open));
    $(`step-${s}`).hidden = !open;
  }
  // which step the incident is in (a quiet marker, separate from the one the reader opened)
  const now = !st || st.phase === "standby" || st.phase === "preplan" ? "know" : st.mayday || st.phase === "terminated" ? "command" : (beacon?.b?.pucks || []).length || scout?.onScreen ? "scene" : "know";
  for (const s of STEPS) document.querySelector(`.step[data-step="${s}"]`).classList.toggle("now", s === now);
  // summaries
  const fl = storeys(S.bundle), planNo = planFloorNo(S.bundle), hz = S.bundle.site.tenants.filter((t) => t.life_hazard).length;
  $("sum-know").textContent = S.preplan ? `Pre-plan ready. ${plural(fl.length, "storey")}, ${plural(hz, "life hazard")}` : `${plural(fl.length, "storey")}, ${plural(S.bundle.site.tenants.length, "tenancy", "tenancies")}, ${plural(hz, "life hazard")}`;
  $("title-scene").textContent = S.mode === "scout" ? "Scout scan" : S.mode === "crews" ? "Crew tags" : "Beacon on scene";
  $("sum-scene").textContent = sceneSummary();
  $("sum-command").textContent = st?.mayday ? `Mayday: ${st.units.find((u) => u.id === st.mayday.unit)?.label || st.mayday.unit}` : [parLine(st), cmd.radio.length ? plural(cmd.radio.length, "radio message") : ""].filter(Boolean).join(", ") || "Crews, radio, the agent";
  renderStepBody(force);
}
function sceneSummary() {
  if (S.mode === "scout") return scout.onScreen ? (scout.scan ? `Scan landed: ${plural((scout.scan.victims || []).length, "person", "people")} found` : scout.text || "Scanning") : "No scan yet";
  if (S.mode === "crews") return "Simulated tags on the crews";
  const b = beacon.b;
  if (!b && !beacon.connected) return "Beacon module not connected";
  if (!(b?.pucks || []).length) return `Where did the beacon land? Floor ${planFloorNo(S.bundle)}`;
  if ((b.warnings || []).length) return "Puck spacing disagrees with the measurement: move the pucks";
  const ph = beacon.phones(), inside = ph.filter((p) => !p.report?.safe_outside), ppl = inside.reduce((s, p) => s + (p.report?.people || 0), 0);
  return `${b.room_label || "Pucks in"}: ${plural(inside.length, "phone")}, ${plural(ppl, "person", "people")}${ph.length > inside.length ? `, ${ph.length - inside.length} out` : ""}`;
}
function renderStepBody() {
  const k = { bundle: S.bundle, preplan: S.preplan, aar: S.aar, acc: beacon.acc || beacon.b?.accountability, phase: S.state?.phase || "standby", agentBusy: S.state?.agent_busy,
    openFloor: S.openFloor, scanAvailable: !!S.scan, mode: S.mode, beacon, scout, state: S.state, selected: S.selected, active: active(),
    simulated: S.hello ? !!S.hello.simulated : true, noMotion: S.hello?.no_motion_s, closing: S.closing, lastAgent: cmd.lastAgent };
  if (S.step === "know") setHTML($("step-know"), knowHTML(k));
  else if (S.step === "scene") setHTML($("step-scene"), sceneHTML(k));
  else setHTML($("step-command"), cmd.html(k));
}

// ---------------------------------------------------------------- the table: views, toolbar, overlays
function setView(v) {
  if (v === "3d" && !twin3d) v = "2d";
  if (v === "scan" && !S.scan) v = twin3d ? "3d" : "2d";
  S.view = v;
  $("twin3d").hidden = v !== "3d"; $("plan").toggleAttribute("hidden", v !== "2d"); $("scanview").hidden = v !== "scan"; $("aerialview").hidden = v !== "aerial";
  for (const b of $("view-seg").querySelectorAll("button")) b.setAttribute("aria-selected", String(b.dataset.view === v));
  if (v === "scan" && !$("scanview").firstChild) openScan();
  if (v === "3d") twin3d.resize();
  if (scout) applySmoke();
  renderToolbar();
}
function renderToolbar() {
  const { labels, twin } = S.bundle, n = planFloorNo(S.bundle), total = storeys(S.bundle).length;
  const lay = S.view === "aerial" ? stamp(layerKind(labels, "vexcel"), labels.vexcel.text) : S.view === "scan" ? stamp("live", labels.matterport.text) : stamp(layerKind(labels, "matterport"), labels.matterport.text);
  setHTML($("tb-floor"), `<span class="fl">Floor ${esc(n)}</span><span class="fl-sub">${S.view === "aerial" ? "the site from above" : `of ${total}, ${twin.source.synthetic ? "stand-in interior" : "scanned interior"}`}</span>${lay}`);
  $("btn-place").hidden = S.mode !== "beacon"; $("btn-place").setAttribute("aria-pressed", String(placer.on));
  $("btn-place").textContent = (beacon?.b?.pucks || []).length ? "Move pucks" : "Place pucks";
  $("btn-inject").setAttribute("aria-pressed", String(S.inject)); $("btn-inject").disabled = !active();
  $("btn-smoke").hidden = !(S.mode === "scout" && S.view === "3d");
}
function applySmoke() {
  const lvl = S.smokeForce === true ? Math.max(0.85, scout.smoke) : S.smokeForce === false ? 0 : scout.smoke;
  const on = lvl > 0.01;
  twin3d?.setSmoke(S.view === "3d" ? lvl : 0);
  $("btn-smoke").setAttribute("aria-pressed", String(on));
  $("btn-smoke").textContent = on ? `Smoke view${S.smokeForce == null ? ` ${Math.round(lvl * 100)}%` : ""}` : "Smoke view";
  $("smoke-claim").hidden = !(on && S.view === "3d");
  if (!(on && S.view === "3d")) $("smoke-inset").hidden = true;
}
function onInsetRect(r) {
  const el = $("smoke-inset");
  if (!r || S.view !== "3d") { el.hidden = true; return; }
  el.hidden = false;
  Object.assign(el.style, { left: r.x + "px", bottom: r.y + "px", width: r.w + "px", height: r.h + "px" });
  Object.assign($("smoke-claim").style, { right: "10px", left: "auto", top: "10px", bottom: "auto", width: Math.min(360, Math.round(r.W * 0.4)) + "px" });
}
function beaconBox() {
  const b = beacon.b; if (!b || !b.room || !(b.pucks || []).length) return null;
  const r = S.bundle.twin.rooms.find((q) => q.id === b.room); if (!r) return null;
  const box = [Math.min(...r.polygon.map((p) => p[0])), Math.min(...r.polygon.map((p) => p[1])), Math.max(...r.polygon.map((p) => p[0])), Math.max(...r.polygon.map((p) => p[1]))];
  // the puck room fills the view; candidates and ruled-out mirror places just outside it stay in the frame
  for (const p of b.phones || []) for (const q of [...(p.candidates || []), ...(p.pruned || [])]) {
    if (q.x < box[0] - 6 || q.x > box[2] + 6 || q.y < box[1] - 6 || q.y > box[3] + 6) continue;
    box[0] = Math.min(box[0], q.x - 1); box[1] = Math.min(box[1], q.y - 1.4); box[2] = Math.max(box[2], q.x + 1); box[3] = Math.max(box[3], q.y + 1);
  }
  return box;
}
// a mayday: the downed crew and the rescue team's route to it
function maydayBox() {
  const st = S.state, m = st?.mayday; if (!m) return null;
  const u = st.units.find((q) => q.id === m.unit); if (!u || u.x == null) return null;
  const box = [u.x - 4, u.y - 4, u.x + 4, u.y + 4];
  for (const r of st.units) if (r.id !== u.id && r.route && !r.route.done && r.assignment?.target === u.id) for (const p of r.route.polyline) { box[0] = Math.min(box[0], p[0] - 1); box[1] = Math.min(box[1], p[1] - 1); box[2] = Math.max(box[2], p[0] + 1); box[3] = Math.max(box[3], p[1] + 1); }
  // the frame only grows during one mayday, so the view does not drift as the rescue team walks
  const o = S._mbox && S._mbox.unit === u.id ? S._mbox.box : null;
  const out = (o ? [Math.min(o[0], box[0]), Math.min(o[1], box[1]), Math.max(o[2], box[2]), Math.max(o[3], box[3])] : box).map((v, i) => (i < 2 ? Math.floor(v / 2) * 2 : Math.ceil(v / 2) * 2));
  S._mbox = { unit: u.id, box: out };
  return out;
}
function applyFocus() {
  const view = S.mode === "scout" ? scout.view() : null, bbox = S.mode === "beacon" && beacon && !placer.on ? beaconBox() : maydayBox();
  $("focus-seg").hidden = !view && !bbox;
  for (const b of $("focus-seg").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.focus === S.focus));
  let box = null;
  if (bbox && S.focus === "room") box = bbox.map((v) => Math.round(v * 2) / 2);
  if (view && S.focus === "room") {
    box = [...view.room.bbox];
    // after a few seconds on the person, widen to the crews sent to them, with their routes
    const ids = new Set((scout.scan?.victims || []).map((v) => v.id)), rescuers = new Set(Object.values(scout.vstatus || {}).map((v) => v.rescuer).filter(Boolean));
    const settled = scout.phase === "done" && performance.now() - scout.phaseAt > 6000;
    for (const u of settled ? S.state?.units || [] : []) {
      if (!u.route?.polyline || u.route.done || !(rescuers.has(u.id) || (u.assignment && ids.has(u.assignment.target)))) continue;
      for (const p of u.route.polyline) { box[0] = Math.min(box[0], p[0]); box[1] = Math.min(box[1], p[1]); box[2] = Math.max(box[2], p[0]); box[3] = Math.max(box[3], p[1]); }
    }
    box = box.map((v) => Math.round(v * 2) / 2);
  }
  // placing pucks: the whole floor until P1 is down, then P1's room, so P2 can be placed precisely
  if (placer.on) {
    const r = placer.p.room, ol = S.bundle.twin.floors.find((f) => f.id === S.bundle.twin.exercise_floor).outline;
    const bb = (poly) => [Math.min(...poly.map((p) => p[0])), Math.min(...poly.map((p) => p[1])), Math.max(...poly.map((p) => p[0])), Math.max(...poly.map((p) => p[1]))];
    box = r ? bb(r.polygon).map((v, i) => v + (i < 2 ? -2.5 : 2.5)) : bb(ol);
  }
  // overlays on a wide stage: the selection card (lower left) and the place guide (right); widen the box so the plan stays clear
  const stage = $("stage"), W = stage.clientWidth, card = $("sel"), guide = $("place");
  if (box && W > 760) {
    const w = box[2] - box[0];
    const fl = !card.hidden ? Math.min(0.45, (card.offsetWidth + 24) / W) : 0, fr = !guide.hidden ? Math.min(0.45, (guide.offsetWidth + 24) / W) : 0;
    const k = 1 / Math.max(0.3, 1 - fl - fr);
    box = [box[0] - w * fl * k, box[1], box[2] + w * fr * k, box[3]];
  }
  twin3d?.setFocus(box); plan.setFocus(box);
}

// the key: what the marks on the table mean (shapes and words, not colour alone)
function renderKey() {
  const phones = S.mode === "beacon" && (beacon?.b?.phones || []).length, scan = S.mode === "scout" && scout?.scan;
  const sv = (inner, vb = "0 0 26 16") => `<svg viewBox="${vb}" aria-hidden="true">${inner}</svg>`;
  const items = [];
  if (phones) {
    items.push([sv(`<ellipse cx="13" cy="8" rx="7" ry="5" fill="var(--person)" fill-opacity=".3" stroke="var(--person)" stroke-width="1.5"/><circle cx="13" cy="8" r="2" fill="var(--person)"/>`), "Located: a point, within its error"]);
    items.push([sv(`<circle cx="13" cy="8" r="6.5" fill="none" stroke="var(--person)" stroke-width="2"/>`), "Ring: a distance from one puck, not a place"]);
    items.push([sv(`<circle cx="6" cy="8" r="4" fill="var(--person)" fill-opacity=".45"/><circle cx="20" cy="8" r="4" fill="var(--person)" fill-opacity=".45"/><line x1="10" y1="8" x2="16" y2="8" stroke="var(--person)" stroke-dasharray="2 2"/>`), "One of two places: cannot choose"]);
    items.push([sv(`<circle cx="13" cy="8" r="6" fill="none" stroke="var(--ink-3)" stroke-dasharray="2 2"/><path d="M9 4l8 8M17 4l-8 8" stroke="var(--ink-2)" stroke-width="2"/>`), "Ruled out"]);
    items.push([sv(`<rect x="8" y="3" width="10" height="10" fill="var(--puck)" stroke="var(--puck-ink)" stroke-width="1.4"/>`), "Puck placed by hand"]);
    items.push([sv(["tri", "plus", "dot", "q"].map((s, i) => `<path d="${SHAPE_PATH[s]}" transform="translate(${3.5 + i * 6.5} 8) scale(2.8)" ${s === "q" ? 'fill="none" stroke="var(--ink-2)" stroke-width=".35"' : 'fill="var(--ink)"'}/>`).join("")), "Trapped, injured, can move, no status"]);
  }
  if (scan) items.push([sv(`<ellipse cx="13" cy="8" rx="7" ry="5" fill="var(--person)" fill-opacity=".3" stroke="var(--person)"/>`), "Person found by the radar (simulated)"]);
  items.push([sv(`<circle cx="7" cy="8" r="4.5" fill="var(--e1)"/><circle cx="19" cy="8" r="4.5" fill="var(--rit)"/>`), "Engine 1, rescue team"]);
  items.push([sv(`<rect x="3" y="3" width="20" height="10" fill="var(--m-stair)"/><path d="M3 5.5h20M3 8h20M3 10.5h20" stroke="var(--ink-3)"/>`), "Stairs"]);
  items.push([sv(`<path d="M13 3l4 5-4 5-4-5Z" fill="var(--danger)"/>`), "Life hazard on record"]);
  items.push([sv(`<rect x="3" y="3" width="20" height="10" fill="none" stroke="var(--danger)" stroke-width="1.5"/><path d="M5 13l8-10M11 13l8-10M17 13l5-6" stroke="var(--danger)"/>`), "Lost: do not route through"]);
  const open = S.keyOpen ?? !!phones;
  setHTML($("key"), `<details data-k="key"${open ? " open" : ""}><summary>Key</summary><ul>${items.map(([i, t]) => `<li>${i}<span>${esc(t)}</span></li>`).join("")}${phones ? `<li class="k-note">Phones without the app are not detected.</li>` : ""}</ul></details>`);
}

// ---------------------------------------------------------------- the selection card
function nearestSide(x, y) {
  const { twin, site } = S.bundle;
  const f = twin.floors.find((q) => q.id === twin.exercise_floor).outline;
  const cx = (Math.min(...f.map((p) => p[0])) + Math.max(...f.map((p) => p[0]))) / 2, cy = (Math.min(...f.map((p) => p[1])) + Math.max(...f.map((p) => p[1]))) / 2;
  const bearing = ((twin.georef.bearing_deg - Math.atan2(y - cy, x - cx) * 180 / Math.PI) % 360 + 360) % 360;
  let best = null;
  for (const [k, v] of Object.entries(site.sides || {})) { const d = Math.min(Math.abs(v.bearing_deg - bearing), 360 - Math.abs(v.bearing_deg - bearing)); if (!best || d < best[1]) best = [k, d]; }
  return best ? best[0] : null;
}
const row = (k, v) => (v ? `<div class="row"><span>${esc(k)}</span><span>${v}</span></div>` : "");
function exitsWords(node) {
  const ex = node ? plan.exitDistances(node, S.state?.hazards || []) : [];
  return ex.map((e) => `${esc(e.label)} ${e.m == null ? `<b class="warn">cut off</b>` : `${e.m} m`}`).join(", ");
}
function recordWords(room) {
  const ts = (room?.suite && S.tenantsBySuite[room.suite]) || [];
  if (!ts.length) return room?.suite ? "No current tenant in the record" : "Common area";
  return ts.map((t) => esc((live() && t.name) || t.industry || "tenant") + (t.life_hazard ? `<br><span class="warn">${esc(t.life_hazard)}</span>` : "")).join("<br>");
}
function renderSel() {
  const el = $("sel"), id = S.selected;
  let html = "";
  const v = id && scout?.victim(id), ph = id && beacon?.phone(id), u = id && S.state?.units?.find((q) => q.id === id);
  if (ph) html = selPhone(ph); else if (v) html = selVictim(v); else if (u && u.x != null) html = selUnit(u);
  const was = el.hidden;
  el.hidden = !html;
  setHTML(el, html);
  if (was !== el.hidden) applyFocus();
}
function selPhone(p) {
  const { twin } = S.bundle, r = p.report || {};
  const roomId = p.room || p.zone?.room || p.candidates?.[0]?.room || (p.ring && twin.rooms.find((q) => q.id === beacon.b?.room)?.id);
  const room = twin.rooms.find((q) => q.id === roomId);
  const pt = p.geometry === "fix" ? [p.x, p.y] : p.geometry === "candidates" ? [p.candidates[0].x, p.candidates[0].y] : room ? [room.polygon.reduce((s, q) => s + q[0], 0) / room.polygon.length, room.polygon.reduce((s, q) => s + q[1], 0) / room.polygon.length] : null;
  const node = p.node || (pt ? plan.nearestNode(pt[0], pt[1], room?.id) : null);
  const where = p.geometry === "fix" && room ? wallDistances(twin, room, p.x, p.y) : p.geometry === "ring" ? `Search the arc ${p.ring.r_m.toFixed(1)} m from puck ${p.ring.puck}` : p.geometry === "candidates" ? `Check both places, ${Math.round(p.candidates[0].weight * 100)}% first` : p.geometry === "zone" ? "Search the room" : "";
  const cap = mirrorCaption(p);
  const inc = (S.state?.phones || []).find((q) => q.id === p.id) || {};
  const asg = (S.state?.units || []).find((x) => x.id === inc.rescuer);
  const [src] = beaconSource(beacon);
  const geo = p.geometry === "fix" ? (p.geo || (() => { const g = aerial.geo(p.x, p.y); return { lat: g[0], lon: g[1] }; })()) : null;
  const side = pt ? nearestSide(pt[0], pt[1]) : null;
  const can = active() && !r.safe_outside && !["out"].includes(inc.status);
  return `<div class="sel-h"><div class="who"><div class="t1"><span class="cs ph">${esc(p.id)}</span>${statusChip(r)}<span>${esc(peopleWords(r))}</span></div>` +
    `<div class="where">${esc(r.safe_outside ? "Outside the building" : room?.label || "Where unknown")} ${src}</div><div class="what geo">${esc(geomShort(p))}</div></div><button class="sel-x" data-act="sel-close" aria-label="Close">×</button></div>` +
    `<div class="sel-b">` + row("Where", esc(where) + (cap ? `<br><small>${esc(cap)}</small>` : "")) + row("They said", r.text ? `<span class="said">“${esc(r.text)}”</span>` : "") +
    row("Crew", asg ? `${esc(asg.label)} ${inc.status === "out" ? "brought them out" : ["reached", "evacuating"].includes(inc.status) ? "is with them" : `sent${asg.route ? `, route ${Math.round(asg.route.length_m)} m` : ""}`}` : inc.status === "out" ? "Brought out" : "") +
    `<details class="sel-more" data-k="more"><summary>Way out, record, outside</summary>` +
    row("Heard", `${esc(ago(p.track?.last_seen_s ?? r.age_s))}${r.battery_pct != null ? `, battery ${r.battery_pct}%` : ""}${p.stale ? `, <span class="warn">not heard recently</span>` : ""}`) +
    row("Way out", exitsWords(node)) + row("Record", recordWords(room)) +
    row("Outside", side ? `Behind side ${esc(side)}${geo ? `, ${geo.lat.toFixed(5)}, ${geo.lon.toFixed(5)}` : ""}` : "") + `</details></div>` +
    `<div class="sel-a">${can ? `<button class="btn small" data-act="assign" data-unit="RIT" data-task="rescue" data-target="${esc(p.id)}">Send the rescue team</button><button class="btn small" data-act="assign" data-unit="E1" data-task="search" data-target="${esc(p.id)}">Send Engine 1</button>` : ""}` +
    `${S.scan && pt && !r.safe_outside ? `<button class="btn small" data-act="look" data-x="${pt[0].toFixed(2)}" data-y="${pt[1].toFixed(2)}">Look here in the scan</button>` : ""}</div>`;
}
function selVictim(v) {
  const { twin } = S.bundle, s = scout.scan;
  const room = twin.rooms.find((r) => r.id === (v.room || s.room));
  const node = v.node || plan.nearestNode(v.x, v.y, room?.id);
  const side = nearestSide(v.x, v.y);
  return `<div class="sel-h"><div class="who"><div class="t1"><span class="cs ph">${esc(v.id)}</span><span>A person, not tagged</span></div>` +
    `<div class="where">${esc(room?.label || s.room_label || "Where unknown")} ${stamp(s.simulated ? "sim" : "measured")}</div><div class="what geo">${esc(victimState(v))}, within ${(v.sigma_m ?? sigmaEllipse(v).a).toFixed(1)} m</div></div><button class="sel-x" data-act="sel-close" aria-label="Close">×</button></div>` +
    `<div class="sel-b">` + row("Where", esc(room ? wallDistances(twin, room, v.x, v.y) : "")) + row("Way out", exitsWords(node)) + row("Record", recordWords(room)) +
    row("Outside", side ? `Behind side ${esc(side)}` : "") + row("How sure", `${v.n_links ?? "?"} links agree, confidence ${Math.round((v.confidence ?? 0) * 100)}%. Still and not breathing would look like furniture.`) + `</div>` +
    `<div class="sel-a">${active() ? `<button class="btn small" data-act="assign" data-unit="RIT" data-task="rescue" data-target="${esc(v.id)}">Send the rescue team</button>` : ""}${S.scan ? `<button class="btn small" data-act="look" data-x="${v.x.toFixed(2)}" data-y="${v.y.toFixed(2)}">Look here in the scan</button>` : ""}</div>`;
}
function selUnit(u) {
  const room = S.bundle.twin.rooms.find((r) => r.id === u.room);
  const side = nearestSide(u.x, u.y), may = u.status === "mayday";
  return `<div class="sel-h"><div class="who"><div class="t1"><span class="cs u-${esc(u.id)}${may ? " mayday" : ""}">${esc(u.id)}</span><b>${esc(u.label)}</b><span>${esc(u.status)}${u.physical ? "" : ", no tag"}</span></div>` +
    `<div class="where">${esc(u.room_label || "No fix")}</div><div class="what">${esc(taskWords(u) || "No order")}${u.route && !u.route.valid ? `, <span class="warn">route lost</span>` : ""}</div></div><button class="sel-x" data-act="sel-close" aria-label="Close">×</button></div>` +
    `<div class="sel-b">` + row("Way out", exitsWords(u.node)) + row("Air", u.time_in_s ? `${Math.round(u.air_pct)}%, inside for ${mmss(u.time_in_s)}` : "") +
    `<details class="sel-more" data-k="more"><summary>Fix, record, outside</summary>` +
    row("Fix", u.physical ? `Tag ${esc(u.tag)}${u.sigma_m != null ? `, within ${u.sigma_m.toFixed(1)} m` : ""}, ${u.still_s >= 3 ? `still for ${Math.round(u.still_s)} s` : "moving"}` : "No tag: moved by the server") +
    row("Record", recordWords(room)) + row("Outside", side ? `Behind side ${esc(side)}${u.geo ? `, ${u.geo.lat.toFixed(5)}, ${u.geo.lon.toFixed(5)}` : ""}` : "") + `</details></div>` +
    `<div class="sel-a">${active() ? `<button class="btn small" data-act="assign" data-unit="${esc(u.id)}" data-task="exit">Withdraw</button><button class="btn small" data-act="assign" data-unit="${esc(u.id)}" data-task="hold">Hold here</button>${!S.state?.mayday ? `<button class="btn small danger" data-act="mayday" data-unit="${esc(u.id)}">Mayday for ${esc(u.id)}</button>` : ""}` : ""}` +
    `${S.scan ? `<button class="btn small" data-act="look" data-x="${u.x.toFixed(2)}" data-y="${u.y.toFixed(2)}">Look here in the scan</button>` : ""}</div>`;
}

// ---------------------------------------------------------------- footer: what is real here; the sources popover
function renderFoot() {
  const { labels } = S.bundle, h = S.hello;
  const items = [["Record", layerKind(labels, "costar"), labels.costar.text], ["Twin", layerKind(labels, "matterport"), labels.matterport.text], ["Aerial", layerKind(labels, "vexcel"), labels.vexcel.text]];
  const [bs] = beaconSource(beacon);
  const phones = bs || (beacon.connected ? stamp("sim", "Beacon module connected, no phones yet") : stamp("off", "Beacon module not connected"));
  const hubOk = S.state ? S.state.hub_connected : h?.hub?.connected;
  const crews = !h ? "" : !hubOk ? stamp("off", "UWB hub disconnected") : stamp(h.simulated ? "sim" : "measured", h.simulated ? `Crew positions from the uwb-kit simulator (${h.hub?.source || "sim"})` : "Crew positions from real UWB boards");
  setHTML($("foot"), `<span>What is real here:</span>` + items.map(([w, k, t]) => `<span class="fi"><b>${w}</b>${stamp(k, t)}</span>`).join("") +
    `<span class="fi"><b>Phones and pucks</b>${phones}</span>${crews ? `<span class="fi"><b>Crews</b>${crews}</span>` : ""}` +
    `<span class="fi"><b>Agent</b>${stamp(agentKind(h?.modes?.llm || S.bundle.config.llm_backend), `Command agent backend: ${h?.modes?.llm || S.bundle.config.llm_backend}`)}</span>` +
    `<button class="btn" data-act="sources">Details</button>`);
}
const FEEDS = [
  { path: "/feed/scan.geojson", name: "GeoJSON", what: "pucks, people, flags, the room outline and every crew, WGS84" },
  { path: "/feed/cot.xml", name: "Cursor-on-Target", what: "the same as CoT 2.0 events (the format TAK clients read; not tested against a TAK server)" },
  { path: "/feed/cot/stream", name: "CoT stream", what: "Server-Sent Events, one event per change" },
];
async function openSources() {
  const pop = $("sources-pop"); pop.hidden = false;
  const { labels } = S.bundle, h = S.hello || {};
  const counts = {};
  try { const g = await (await fetch(FEEDS[0].path)).json(); counts[0] = plural((g.features || []).length, "feature"); } catch { counts[0] = "not available"; }
  try { const x = await (await fetch(FEEDS[1].path)).text(); counts[1] = plural((x.match(/<event[\s>]/g) || []).length, "event"); } catch { counts[1] = "not available"; }
  const bl = beacon.link || {};
  pop.innerHTML = `<h3>What is real here</h3><ul class="srcs">` +
    [["Record", layerKind(labels, "costar"), labels.costar.text], ["Twin", layerKind(labels, "matterport"), labels.matterport.text], ["Aerial", layerKind(labels, "vexcel"), labels.vexcel.text]]
      .map(([w, k, t]) => `<li><span><b>${w}</b> ${stamp(k)}</span><span>${esc(t)}</span></li>`).join("") +
    `<li><span><b>Phones</b> ${beaconSource(beacon)[0] || stamp("off")}</span><span>${esc(bl.note || beaconSource(beacon)[1] || (beacon.connected ? "Beacon module connected" : "Beacon module not connected"))}. Only phones with the opt-in app are located; phones without it are not detected.</span></li>` +
    `<li><span><b>Crews</b> ${stamp(h.simulated ? "sim" : "measured")}</span><span>UWB hub ${esc(h.hub?.source || "?")}${h.hub?.profile ? `, ${esc(h.hub.profile)}` : ""}${h.registration?.scale > 1.5 ? `; exercise scale 1:${h.registration.scale.toFixed(1)} (building metres per rig metre)` : ""}. A crew that does not move for ${h.no_motion_s ?? "?"} s raises a mayday.</span></li>` +
    `<li><span><b>Agent</b> ${stamp(agentKind(h.modes?.llm))}</span><span>Command agent backend: ${esc(h.modes?.llm || "?")}${h.modes?.llm === "replay" ? " (recorded turns of a real model run, replayed)" : ""}.</span></li></ul>` +
    ((labels.open_data || []).length ? `<h3>Open data used</h3><ul class="srcs one">${labels.open_data.map((o) => `<li><b>${esc(o.name)}</b>${o.what ? `<small>${esc(o.what)}</small>` : ""}</li>`).join("")}</ul>` : "") +
    ((S.bundle.site.unavailable || []).length ? `<h3>Could not load</h3><ul class="srcs one">${S.bundle.site.unavailable.map((u) => `<li>${esc(u)}</li>`).join("")}</ul>` : "") +
    `<h3>Feeds for other tools</h3>${FEEDS.map((f, i) => `<div class="feed"><b>${esc(f.name)}</b> <code>${esc(f.path)}</code><div class="note" style="margin:2px 0 0">${esc(f.what)}</div><div class="fa"><span class="cnt">${esc(counts[i] || "")}</span><button class="btn small" data-act="copy" data-path="${esc(f.path)}">Copy link</button>${i < 2 ? `<a class="btn small" href="${esc(f.path)}" target="_blank" rel="noopener">Open</a>` : ""}</div></div>`).join("")}` +
    `<div class="actions"><button class="btn small" data-act="sources-close">Close</button></div>`;
  pop.tabIndex = -1; pop.scrollTop = 0; pop.focus({ preventScroll: true });
}
function copyText(t) {
  const done = () => toast(`Copied ${t}`);
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(t).then(done, fallback); else fallback();
  function fallback() { const i = document.createElement("textarea"); i.value = t; document.body.appendChild(i); i.select(); try { document.execCommand("copy"); done(); } catch { toast(t); } i.remove(); }
}

// ---------------------------------------------------------------- state from the server
function renderState() {
  const st = S.state; if (!st) return;
  // the incident's own events move the story forward; the reader can always open another step
  if (st.phase !== S.prevPhase) {
    if (st.phase === "preplan" || (S.prevPhase === "terminated" && st.phase === "standby")) setStep("know", true);
    if (st.phase === "terminated") setStep("command", true);
    S.prevPhase = st.phase;
  }
  const sb = st.storyboard;
  if (sb?.running && sb.script !== S._sbScript) { S._sbScript = sb.script; if (MODE_OF_SCRIPT[sb.script] && MODE_OF_SCRIPT[sb.script] !== S.mode) setMode(MODE_OF_SCRIPT[sb.script], true); }
  if (!sb?.running) S._sbScript = null;
  if (st.mayday && !S._mayday) { S._mayday = true; if (!S._pinned) S.selected = st.mayday.unit; setStep("command", true); }
  if (!st.mayday) { S._mayday = false; S._mbox = null; }
  if (scout.onState(st.scout)) applySmoke();
  if (!beacon.dev && beacon.onState(st.beacon)) renderFoot();
  renderHeader();
  plan.update(st, S.selected);
  aerial.update(st);
  twin3d?.update(st, S.selected);
  applyFocus(); renderToolbar(); renderSel(); renderStory();
}
function onBeaconChange() {
  // each mode draws its own layer: phones in Beacon, the radar scan in Scout (both keep their data)
  const view = S.mode === "beacon" ? beacon.view() : null;
  twin3d?.setBeacons(view, S.selected); plan.setBeacons(view, S.selected); aerial.setBeacons?.(view);
  const np = (beacon.b?.pucks || []).length, nph = beacon.phones().length;
  if (S.mode === "beacon" && ((np && !S.prevPucks) || (nph && !S.prevPhones))) {
    setStep("scene", true);
    if (nph && !S._pinned && !beacon.phone(S.selected)) {
      const first = sortedPhones(beacon).find((p) => !p.report?.safe_outside && p.geometry !== "none");
      if (first) S.selected = first.id;
    }
  }
  S.prevPucks = np; S.prevPhones = nph;
  if (S.selected && /^B\d/.test(S.selected) && !beacon.phone(S.selected)) S.selected = null;
  renderKey(); applyFocus(); renderFoot(); renderToolbar();
  if (S.state) renderState(); else { renderSel(); renderStory(); }
}
function onScoutChange() {
  const view = S.mode === "scout" ? scout.view() : null;
  twin3d?.setScout(view); plan.setScout(view);
  aerial.setVictim?.(scout.mainVictim(), scout.scan);
  if (scout.onScreen && scout.animate && S.mode !== "scout" && scout.phase === "thrown") setMode("scout", true);
  if (scout.onScreen && scout.phase === "thrown" && !S._scoutStep) { S._scoutStep = true; setStep("scene", true); }
  if (!scout.onScreen) { S._scoutStep = false; if (S.selected && /^V\d/.test(S.selected)) S.selected = null; }
  renderKey(); renderDevbar(); applyFocus(); applySmoke();
  clearInterval(S._meshTimer);
  if (scout.phase === "ranging" || scout.phase === "thrown") S._meshTimer = setInterval(() => { if (S.step === "scene") renderStepBody(); }, 200);
  if (S.state) renderState(); else { renderSel(); renderStory(); }
}
function onPick(id) {
  if (/^B\d/.test(id) || /^V/.test(id) || isUnit(id)) { S.selected = id; S._pinned = true; onBeaconChange(); return; }
  const m = (scout.scan?.mismatches || []).find((q) => q.id === id);
  if (m) toast(`${m.id}: ${m.text}`);
}
function renderDevbar() {
  const el = $("devbar");
  if (beacon?.dev) { el.hidden = false; el.innerHTML = `<b>Sample</b> hand-made phones, not the Beacon module <button class="btn" data-act="dev-next">Next (N)</button>`; return; }
  if (!scout?.dev) { el.hidden = true; return; }
  el.hidden = false; el.innerHTML = `<b>Sample</b> hand-made scan, not a radar result <button class="btn" data-act="dev-next">Next phase (N)</button>`;
}

// ---------------------------------------------------------------- modes
const SCRIPT_OF_MODE = { beacon: "beacon", scout: "scout", crews: "mayday" }, MODE_OF_SCRIPT = { beacon: "beacon", scout: "scout", mayday: "crews" };
function setMode(m, quiet) {
  S.mode = m; S.script = SCRIPT_OF_MODE[m];
  for (const b of $("mode-seg").querySelectorAll("button")) b.setAttribute("aria-checked", String(b.dataset.mode === m));
  for (const r of document.querySelectorAll('#story-menu input[name="script"]')) r.checked = r.value === S.script;
  if (m !== "beacon" && placer.on) placer.stop();
  const sv = m === "scout" ? scout.view() : null; twin3d?.setScout(sv); plan.setScout(sv);
  onBeaconChange();
  renderHeader(); renderKey(); applyFocus(); renderToolbar();
  if (!quiet) renderStory(true);
}

// ---------------------------------------------------------------- the Matterport scan (live only): look here in the scan
async function initScan() {
  if (S.bundle.labels.matterport?.mode !== "live") return;      // fixture mode never asks for the live config
  try {
    const r = await fetch("/api/live/config", { cache: "no-store" });
    if (!r.ok) return;
    const c = await r.json();
    if (!(c && (c.matterport?.live || (c.sdk_key && c.model_id) || c.matterport?.model_id))) return;
  } catch { return; }
  S.scan = { frame: null, ready: false, queue: [] };
  $("btn-scan").hidden = false;
  addEventListener("message", (ev) => {
    const m = ev.data || {};
    if (!S.scan || !String(m.type || "").startsWith("mp.")) return;
    if (m.type === "mp.ready") { S.scan.ready = true; clearTimeout(S.scan.timer); document.querySelector("#scanview .scan-note.over")?.remove(); for (const q of S.scan.queue.splice(0)) S.scan.frame?.contentWindow?.postMessage(q, "*"); }
    else if (m.type === "mp.moved") toast(m.ok ? `Scan: moved to the nearest scan position (${m.dist_m != null ? m.dist_m.toFixed(1) + " m away" : "nearest"})` : `Scan: ${m.error || "could not move"}`);
    else if (m.type === "mp.error") toast(`Scan: ${m.text}`);
  });
  renderStory(true); renderSel();
}
function openScan() {
  // the Showcase SDK key is restricted to the host name "localhost": anywhere else, say so instead of trying
  if (location.hostname !== "localhost") {
    const url = `http://localhost:${location.port || 80}/`;
    $("scanview").innerHTML = `<div class="scan-note"><p>The Matterport scan view loads only at <a href="${esc(url)}">${esc(url)}</a> on the machine that runs the board (the SDK key is restricted to localhost).</p><p>The plan and the 3D twin here are built from the same scan.</p></div>`;
    return;
  }
  const f = document.createElement("iframe");
  f.src = "/mp.html"; f.title = "Matterport scan (Showcase SDK)"; f.allow = "fullscreen; xr-spatial-tracking";
  $("scanview").appendChild(f); S.scan.frame = f;
  S.scan.timer = setTimeout(() => {
    if (S.scan.ready) return;
    $("scanview").insertAdjacentHTML("beforeend", `<div class="scan-note over"><p>The Matterport scan did not connect within 15 seconds: the Matterport service may be out of reach from this machine, or it refused the key.</p><p>The plan and the 3D twin are built from the same scan.</p></div>`);
  }, 15000);
}
function lookInScan(x, y) {
  if (!S.scan) return;
  setView("scan");
  const msg = { type: "mp.goto", x, y, floor: S.bundle.twin.exercise_floor };
  if (S.scan.ready) S.scan.frame?.contentWindow?.postMessage(msg, "*"); else S.scan.queue = [msg];
  if (location.hostname !== "localhost") toast("The scan view needs the board opened at localhost; the plan and the twin show the same place");
}

// ---------------------------------------------------------------- documents
function showDoc(title, html) { $("doc-title").textContent = title; $("doc-body").innerHTML = html; $("doc").hidden = false; $("doc-close").focus(); }
function showOblique(i) {
  const it = obliqueSides(S.bundle)[i]; if (!it) return;
  const { im, letter } = it, s = (S.bundle.site.sides || {})[letter] || {};
  showDoc(`Side ${letter}`, `<img class="oblique-big" src="${esc(im.url || "/" + im.path)}" alt="Oblique view of side ${esc(letter)}">` +
    `<p class="qa">The ${esc(im.shows_face || "")} face of the building${s.street ? `, on ${esc(s.street)}` : ""}: camera looking ${esc(im.look || "")}${im.capture_date ? `, captured ${esc(im.capture_date)}` : ""}${im.gsd_m ? `, about ${im.gsd_m} m per pixel` : ""}.</p>` +
    `<p class="qa"><small>${esc(im.source || "")}. An oblique has no map projection here, so nothing is drawn on it; the side letter comes from the face it shows and the building's sides.</small></p>`);
}

// ---------------------------------------------------------------- actions (one delegated handler)
function setInject(on) { S.inject = on; $("inject-hint").hidden = !on; plan.setInject(on); if (on) setView("2d"); renderToolbar(); }
function startPlacing() { if (S.mode !== "beacon") setMode("beacon"); setStep("scene"); setView("2d"); S.selected = null; renderSel(); placer.start(); }
function closePops() {
  for (const [m, b] of [["story-menu", "btn-story-menu"], ["more-menu", "btn-more"]]) { $(m).hidden = true; $(b).setAttribute("aria-expanded", "false"); }
  $("sources-pop").hidden = true;
}
function toggleMenu(m, b) { const open = $(m).hidden; closePops(); $(m).hidden = !open; $(b).setAttribute("aria-expanded", String(open)); }
async function act(a, el) {
  switch (a) {
    case "alarm": api("/api/alarm", {}).catch(() => {}); break;
    case "report": window.open("/report.html", "_blank", "noopener"); break;
    case "place": startPlacing(); break;
    case "throw":
      if (S.mode === "beacon") { if (beacon.dev) beacon.devPlay(true); else api("/api/beacon/pucks", { n: 3 }).catch(() => {}); }
      else if (scout.dev) scout.devPlay(true); else api("/api/scout/deploy", {}).catch(() => {});
      break;
    case "pick": onPick(el.dataset.id); break;
    case "flag": onPick(el.dataset.id); break;
    case "sel-close": S.selected = null; S._pinned = true; onBeaconChange(); break;
    case "assign": {
      const body = { unit: el.dataset.unit, task: el.dataset.task, ...(el.dataset.target ? { target: el.dataset.target } : {}) };
      api("/api/unit/assign", body).then((r) => toast(r.ok === false ? `No order: ${r.reason}` : `${el.textContent.trim()}: ordered`)).catch(() => {});
      break;
    }
    case "mayday": api("/api/mayday", { unit: el.dataset.unit || (isUnit(S.selected) ? S.selected : "E1") }).catch(() => {}); break;
    case "look": lookInScan(Number(el.dataset.x), Number(el.dataset.y)); break;
    case "view": setView(el.dataset.view); break;
    case "floor": S.openFloor = S.openFloor === Number(el.dataset.floor) ? null : Number(el.dataset.floor); renderStepBody(); break;
    case "oblique": showOblique(Number(el.dataset.i)); break;
    case "doc-preplan": if (S.preplan) showDoc("Pre-incident plan", preplanDoc(S.preplan, S.bundle.twin)); break;
    case "doc-aar": if (S.aar) showDoc("After-action report", aarDoc(S.aar, S.hello?.simulated)); break;
    case "cmdtab": cmd.tab = el.dataset.tab; renderStepBody(); break;
    case "suite": {
      const rs = S.bundle.twin.rooms.filter((q) => q.suite === el.dataset.suite).map((q) => q.id);
      const p = beacon.phones().find((q) => rs.includes(q.room || q.zone?.room));
      if (p) onPick(p.id);
      break;
    }
    case "sources": openSources(); break;
    case "sources-close": $("sources-pop").hidden = true; break;
    case "copy": copyText(location.origin + el.dataset.path); break;
    case "dev-next": if (beacon.dev) beacon.devNext(); else if (scout.devStep < 0) scout.devPlay(false); else scout.devNext(); break;
    default: if (a.startsWith("pl-")) placer.act(a, el);
  }
}

function resetUi() {
  cmd.reset(); S.preplan = null; S.aar = null; S._pinned = false; S.selected = null; S.smokeForce = null; S.focus = "room"; S.closing = null; S.prevPucks = 0; S.prevPhones = 0; S._scoutStep = false;
  scout.reset(); applySmoke(); if (!beacon.dev) beacon.reset();
  twin3d?.setFocus(null); plan.setFocus(null);
  $("doc").hidden = true; $("ticker").hidden = true;
  plan.setPreplan(null); twin3d?.setPreplan?.(null);
  setStep("know");
}
function ticker(m) {
  const t = $("ticker");
  t.className = `ticker ${m.priority || ""}`;
  t.innerHTML = `<b>${esc(party(m.from, S.state?.units))} to ${esc(party(m.to, S.state?.units))}</b>${esc(m.text)}`;
  t.hidden = false; clearTimeout(ticker._t); ticker._t = setTimeout(() => (t.hidden = true), 14000);
}
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  let fresh = false;
  setTimeout(() => (fresh = true), 1500);
  ws.onmessage = (e) => {
    let m; try { m = JSON.parse(e.data); } catch { return; }
    switch (m.type) {
      case "hello": S.hello = m; renderFoot(); renderToolbar(); break;
      case "state": S.state = m; renderState(); break;
      case "agent": cmd.onAgent(m); if (S.step === "command") renderStepBody(); break;
      case "radio":
        cmd.onRadio(m);
        if (fresh) { ticker(m); if (S.sound && "speechSynthesis" in window) { const u = new SpeechSynthesisUtterance(m.text.replace(/\bRIT\b/g, "Rit").replace(/\bE1\b/g, "Engine one")); u.rate = 1.08; speechSynthesis.speak(u); } }
        renderStory(); break;
      case "preplan": S.preplan = m; plan.setPreplan(m); twin3d?.setPreplan?.(m); renderStory(); break;
      case "aar": S.aar = m; if (fresh) showDoc("After-action report", aarDoc(m, S.hello?.simulated)); renderStory(); break;
      case "tag": plan.addTag(m.tag); twin3d?.addTag?.(m.tag); break;
      case "alert": if (m.level !== "mayday") toast(m.text); break;
      case "reset": resetUi(); break;
      case "timeline": if (m.entry?.kind === "par") { S.closing = m.entry.text; renderStory(); } break;
      case "scout": scout.onScout(m, fresh); break;
      case "beacons": if (!beacon.dev) beacon.onBeacons(m, fresh); break;
      case "accountability": if (!beacon.dev) beacon.onAccountability(m); break;
      case "scan": scout.onScan(m, fresh); if (fresh && !S._pinned && scout.mainVictim()) { S.selected = scout.mainVictim().id; renderSel(); } break;
      default: break;
    }
  };
  ws.onclose = () => {
    if (S.hello) S.hello = { ...S.hello, hub: { ...S.hello.hub, connected: false } };
    toast("Server disconnected: retrying"); cmd.reset(); setTimeout(connect, 1200);
  };
}

function applyColours() {
  pal = palette();
  const colors = unitColors(pal);
  plan?.setColors(colors);
  if (aerial) { aerial.colors = colors; aerial.layer?.replaceChildren(); aerial.units = {}; if (S.state) aerial.update(S.state); }
  twin3d?.setTheme(pal, colors);
  $("theme-state").textContent = document.documentElement.dataset.theme === "night" ? "On: back to day colours" : "For a dark cab";
  $("btn-theme").firstChild.textContent = document.documentElement.dataset.theme === "night" ? "Day colours" : "Night colours";
  if (scout && beacon) { const sv = S.mode === "scout" ? scout.view() : null; twin3d?.setScout(sv); onBeaconChange(); }
}

async function main() {
  applyTheme(initialTheme());
  pal = palette();
  S.bundle = await api("/api/bundle");
  const colors = unitColors(pal);
  for (const t of S.bundle.site.tenants.filter((q) => q.floor === planFloorNo(S.bundle))) (S.tenantsBySuite[t.suite] ||= []).push(t);
  cmd = new Command();
  plan = new Plan($("plan"), S.bundle, {
    color: colors,
    onMove: (unit, x, y) => api("/api/unit/move", { unit, x, y }).catch(() => {}),
    onInject: (ref) => { setInject(false); api("/api/inject", { ref, kind: "blocked", note: "reported lost by command" }).catch(() => {}); },
    onSelect: (unit) => { S.selected = unit; S._pinned = true; renderSel(); renderStory(); },
    canDrag: () => !!S.hello?.drivable,
  });
  aerial = new Aerial($("aerial-img"), $("aerial-svg"), $("aerial-seg"), $("aerial-src"), S.bundle, colors, {});
  const p = S.bundle.site.property;
  $("addr").textContent = p.address || p.name || "Building";
  $("bld-sub").textContent = [p.city, p.property_type, p.stories ? `${p.stories} storeys` : ""].filter(Boolean).join(", ");
  document.title = `${p.address || "Building"}: Sandtable Beacon`;
  window.liveMarker?.(live());
  scout = new ScoutModel(S.bundle, onScoutChange);
  beacon = new BeaconModel(S.bundle, onBeaconChange);
  placer = new Placer({ bundle: S.bundle, plan, beacon, api, toast, onRoom: () => applyFocus(),
    onChange: () => { const was = $("place").hidden; setHTML($("place"), placer.html(active())); $("place").hidden = !placer.on; if (!placer.on && !was) S.focus = "room"; renderToolbar(); renderHeader(); applyFocus(); } });
  plan.setOutside(S.bundle.aerial.images.find((i) => i.id === "ortho") || S.bundle.aerial.images[0]);
  plan.opts.onPick = onPick; plan.opts.onPlace = (...a) => placer.onPlace(...a);
  try {
    const mod = await import("./twin3d.js");
    twin3d = new mod.Twin3D($("twin3d"), S.bundle, colors, { onPick, onInsetRect, pal });
  } catch (err) { console.warn("3D view unavailable, using the plan:", err); twin3d = null; }
  setView(twin3d ? "3d" : "2d");
  initScan();

  // wiring
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-act]");
    if (el && !el.disabled) { act(el.dataset.act, el); return; }
    if (!e.target.closest(".menu, .pop, #btn-story-menu, #btn-more, [data-act='sources']")) closePops();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.matches?.("[role='button'][data-act]")) { e.preventDefault(); act(e.target.dataset.act, e.target); }
    if (e.key === "Escape") { setInject(false); $("doc").hidden = true; closePops(); if (placer.on) placer.stop(); }
    if ((e.key === "n" || e.key === "N") && !e.target.closest?.("input, textarea") && !placer.on) { if (S.mode === "beacon" && beacon.dev) beacon.devNext(); else if (scout.dev) scout.devNext(); }
  });
  document.addEventListener("input", (e) => { if (e.target.closest("#place")) placer.onInput(e.target); });
  document.addEventListener("toggle", (e) => { if (e.target.closest?.("#key")) S.keyOpen = e.target.open; }, true);
  for (const b of document.querySelectorAll("[data-step-btn]")) b.onclick = () => setStep(b.dataset.stepBtn);
  for (const b of $("view-seg").querySelectorAll("button")) b.onclick = () => setView(b.dataset.view);
  for (const b of $("mode-seg").querySelectorAll("button")) b.onclick = () => setMode(b.dataset.mode);
  for (const b of $("focus-seg").querySelectorAll("button")) b.onclick = () => { S.focus = b.dataset.focus; applyFocus(); };
  $("btn-story-menu").onclick = (e) => { e.stopPropagation(); toggleMenu("story-menu", "btn-story-menu"); };
  $("btn-more").onclick = (e) => { e.stopPropagation(); toggleMenu("more-menu", "btn-more"); };
  for (const r of document.querySelectorAll('#story-menu input[name="script"]')) r.onchange = () => setMode(MODE_OF_SCRIPT[r.value]);
  $("opt-guided").onchange = (e) => { S.guided = e.target.checked; renderHeader(); };
  $("btn-story").onclick = (e) => api("/api/storyboard", S.state?.storyboard?.running ? { action: "stop" } : { action: "start", script: S.script, mode: (!S.hello?.drivable || S.guided || e.shiftKey) ? "assist" : "auto" }).catch(() => {});
  $("btn-mayday").onclick = () => act("mayday", { dataset: {} });
  $("btn-inject").onclick = () => setInject(!S.inject);
  $("btn-place").onclick = () => (placer.on ? placer.stop() : startPlacing());
  $("btn-smoke").onclick = () => { const on = $("btn-smoke").getAttribute("aria-pressed") === "true"; S.smokeForce = !on; applySmoke(); };
  $("btn-reset").onclick = () => { closePops(); api("/api/reset", {}).catch(() => {}); };
  $("btn-sound").onclick = () => { S.sound = !S.sound; $("btn-sound").setAttribute("aria-pressed", String(S.sound)); $("sound-state").textContent = S.sound ? "On" : "Off"; if (!S.sound) speechSynthesis?.cancel(); };
  $("btn-theme").onclick = () => { closePops(); applyTheme(document.documentElement.dataset.theme === "night" ? "day" : "night"); applyColours(); };
  $("btn-sources").onclick = (e) => { e.stopPropagation(); closePops(); openSources(); };
  $("doc-close").onclick = () => ($("doc").hidden = true);
  $("ticker").onclick = () => { cmd.tab = "radio"; setStep("command"); };
  addEventListener("resize", () => twin3d?.resize());
  // hovering a suite in the story lights it on the table
  document.addEventListener("mouseover", (e) => { const s = e.target.closest?.("[data-suite]"); if (s && s !== S._hl) { if (S._hl) hl(S._hl.dataset.suite, false); S._hl = s; hl(s.dataset.suite, true); } else if (!s && S._hl) { hl(S._hl.dataset.suite, false); S._hl = null; } });
  const hl = (suite, on) => { plan.highlightSuite(suite, on); twin3d?.highlightSuite?.(suite, on); };

  const qm = new URLSearchParams(location.search).get("mode");
  setMode(MODES[qm] ? qm : beacon.dev || !scout.dev ? "beacon" : "scout", true);
  applyColours();
  renderHeader(); renderKey(); renderFoot(); renderDevbar(); renderStory(true);
  connect();
}
main().catch((err) => { console.error(err); document.body.insertAdjacentHTML("beforeend", `<div class="toast">Could not load the board: ${esc(err.message)}</div>`); });
