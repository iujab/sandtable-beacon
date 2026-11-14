// Sandtable Beacon reporting centre: renders GET /api/report (sandtable.report/1) as a document a command post keeps.
// ?sample=1 renders web/dev/sample_report.json instead (invented incident data, marked SAMPLE on screen and on every page).
// Every section tolerates a missing or partial field: it says "none recorded", it never throws.

const Q = new URLSearchParams(location.search);
const SAMPLE = Q.has("sample") && Q.get("sample") !== "0";
const SRC = SAMPLE ? "dev/sample_report.json" : "/api/report";
const LIVE_MS = 4000, IDLE_MS = 15000;
const ENDED = new Set(["terminated", "standby", "ended", "closed"]);

const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const has = (v) => v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && !v.length);
const arr = (v) => (Array.isArray(v) ? v : []);
const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
const NONE = (what = "None recorded.") => `<p class="none">${esc(what)}</p>`;

function clock(s) {
  s = num(s); if (s === null) return "";
  const neg = s < 0; s = Math.abs(Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (neg ? "-" : "") + (h ? `${h}:${String(m).padStart(2, "0")}` : String(m).padStart(2, "0")) + ":" + String(x).padStart(2, "0");
}
const trel = (s) => (num(s) === null ? `<span class="dim">–</span>` : `<span class="t">T+${clock(s)}</span>`);
function wallTime(t, withDate = true) {
  t = num(t); if (t === null) return null;
  const d = new Date(t * 1000);
  const opt = { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZoneName: "short" };
  if (withDate) Object.assign(opt, { year: "numeric", month: "short", day: "numeric" });
  return d.toLocaleString("en-US", opt);
}
const cap = (s) => (s ? String(s).charAt(0).toUpperCase() + String(s).slice(1) : "");
const words = (s) => cap(String(s ?? "").replace(/[-_]/g, " "));

// ------------------------------------------------------------------ data access that tolerates shapes
function rangeOf(v) {
  if (v === null || v === undefined) return null;
  if (typeof v === "number") return { low: v, high: v };
  if (Array.isArray(v)) return { low: num(v[0]), high: num(v[1] ?? v[0]) };
  const low = num(v.low ?? v.min), high = num(v.high ?? v.max ?? v.people);
  if (low === null && high === null) return num(v.people) !== null ? { low: v.people, high: v.people } : null;
  return { low: low ?? high, high: high ?? low };
}
// The contract names both `status` and `state` on a person without saying which is which; accept either order.
const OCC = new Set(["trapped", "injured", "sheltering", "ok", "unknown"]);
function norm(p) {
  const occ = [p.report?.status, p.status, p.state].find((v) => OCC.has(String(v))) ?? p.report?.status ?? null;
  let prog = [p.state, p.status].find((v) => v && !OCC.has(String(v))) ?? null;
  if (!prog) prog = num(p.out_t_rel) !== null ? "out" : num(p.reached_t_rel) !== null ? "reached" : p.assigned ? "assigned" : "located";
  return { occ, prog };
}
const isOut = (p) => ["out", "safe-outside"].includes(norm(p).prog) || num(p.out_t_rel) !== null;
// Live reports may carry only the geometry's name: take the shape from the Beacon object the server relays (/api/beacons).
function phoneFromBeacons(p) { return p.source === "phone" ? arr(aux.beacons?.phones).find((q) => q.id === p.id) || null : null; }
function personGeom(p) {
  let g = p.geometry;
  if (g && typeof g === "object") return { ...g, kind: g.kind || g.type || "none" };
  const kind = typeof g === "string" ? g : null;
  const ph = phoneFromBeacons(p);
  if (ph && !(num(p.x) !== null || p.ring || p.candidates || p.zone)) {
    const k = ph.geometry || kind, from = "/api/beacons";
    if (k === "fix" && num(ph.x) !== null) return { kind: "fix", x: ph.x, y: ph.y, sigma_m: ph.sigma_m, pruned: ph.pruned, from };
    if (k === "ring" && ph.ring) return { ...ph.ring, kind: "ring", ring_kind: ph.ring.kind, pruned: ph.pruned, from };
    if (k === "candidates" && arr(ph.candidates).length) return { kind: "candidates", candidates: ph.candidates, pruned: ph.pruned, from };
    if (k === "zone" && ph.zone) return { ...ph.zone, kind: "zone", from };
  }
  if (kind === "zone" && !p.zone && p.room) return { kind: "zone", room: p.room, room_label: p.room_label, inferred: true };
  // the Beacon phone shape: geometry is a word and the shapes sit beside it
  if (kind === "fix" || (!kind && num(p.x) !== null)) return { kind: "fix", x: p.x, y: p.y, sigma_m: p.sigma_m, pruned: p.pruned };
  if (kind === "ring" && p.ring) return { ...p.ring, kind: "ring", ring_kind: p.ring.kind, pruned: p.pruned };
  if (kind === "candidates" && p.candidates) return { kind: "candidates", candidates: p.candidates, pruned: p.pruned };
  if (kind === "zone" && p.zone) return { ...p.zone, kind: "zone" };
  if (kind === "point" && num(p.x) !== null) return { kind: "point", x: p.x, y: p.y };
  return { kind: kind || "none" };
}
function reportOf(p) {
  const ph = phoneFromBeacons(p)?.report || {};
  let can = p.report?.can_move ?? p.can_move ?? ph.can_move;
  const t = String(p.text || "");
  if (can === undefined && /cannot move/.test(t)) can = false; else if (can === undefined && /can move/.test(t)) can = true;
  return { ...ph, ...(p.report || {}), status: norm(p).occ, people: p.report?.people ?? p.people ?? ph.people, can_move: can, text: p.report?.text ?? ph.text ?? null };
}

const BACKEND = {
  replay: ["replay", "Replay of a recorded real model run"],
  anthropic: ["live", "Live model (Anthropic API)"],
  "claude-cli": ["live", "Live model (Claude CLI)"],
  live: ["live", "Live model"],
  "reflex-fallback": ["fallback", "Fallback policy: deterministic code, no model"],
  fallback: ["fallback", "Fallback policy: deterministic code, no model"],
  sample: ["sample", "Sample text written for page development, not model output"],
};
const srcBadge = (b) => {
  if (!b) return `<span class="src">Source not stated</span>`;
  const [cls, txt] = BACKEND[b] || ["", `Source: ${b}`];
  return `<span class="src ${cls}">${esc(txt)}</span>`;
};

// ------------------------------------------------------------------ state
let bundle = null, bundleTried = false, last = null, lastKey = null, lastOk = null, timer = null, lastErr = null;
const aux = { beacons: null, state: null }; // live only: what the report itself does not carry (positions)

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) { const e = new Error(`${url} answered ${r.status}`); e.status = r.status; throw e; }
  return r.json();
}

async function tick() {
  clearTimeout(timer);
  if (!bundleTried) { bundleTried = true; try { bundle = await getJSON("/api/bundle"); } catch { bundle = null; } }
  try {
    const rep = await getJSON(SRC);
    lastOk = Date.now(); lastErr = null;
    if (!SAMPLE) {
      const [b, s] = await Promise.allSettled([getJSON("/api/beacons"), getJSON("/api/state")]);
      aux.beacons = b.status === "fulfilled" ? b.value : null; aux.state = s.status === "fulfilled" ? s.value : null;
    }
    const key = JSON.stringify([rep, aux.beacons?.phones, aux.beacons?.pucks, arr(aux.state?.units).map((u) => [u.id, Math.round(u.x || 0), Math.round(u.y || 0)])]);
    if (key !== lastKey) { lastKey = key; last = rep; render(rep); }
  } catch (e) {
    lastErr = e;
    if (!last) renderFatal(e);
  }
  status();
  if (!SAMPLE) timer = setTimeout(tick, last && !ENDED.has(String(last?.incident?.phase || "")) ? LIVE_MS : IDLE_MS);
}

function status() {
  const el = $("#bar-status");
  const at = lastOk ? new Date(lastOk).toLocaleTimeString("en-GB", { hour12: false }) : null;
  if (SAMPLE) { el.innerHTML = `<span class="samp">SAMPLE</span>Invented incident data from <code>web/dev/sample_report.json</code>, not a live incident`; return; }
  if (lastErr && last) { el.innerHTML = `<span class="warn">Server not answering</span>: showing the report as of ${esc(at)}`; return; }
  if (lastErr) { el.innerHTML = `<span class="warn">No report</span>: ${esc(lastErr.message)}`; return; }
  const ph = String(last?.incident?.phase || "");
  el.innerHTML = ENDED.has(ph)
    ? `Incident ${ph === "standby" ? "not started" : "ended"}. Report as of ${esc(at)}; checking every ${IDLE_MS / 1000} s`
    : `<span class="live">Live</span>: updated ${esc(at)}, refreshing every ${LIVE_MS / 1000} s`;
}

function renderFatal(e) {
  const missing = e.status === 404;
  $("#report").innerHTML = `<div class="fatal"><h1>${missing ? "This server does not serve the report yet" : "The report could not be loaded"}</h1>
    <p>${missing ? "<code>GET /api/report</code> answered 404. The page will pick it up as soon as the server provides it." : esc(e.message)}</p>
    <p><a href="?sample=1">Open the sample report</a> (invented incident data, marked SAMPLE) or go back to the <a href="/">command board</a>.</p></div>`;
}

// ------------------------------------------------------------------ the document
function render(r) {
  const parts = [header, summary, people, accountability, plan, crewsAndHazards, timeline, agent, limits, signoff, foot];
  const html = parts.map((f) => { try { return f(r); } catch (e) { console.error(e); return NONE("A section could not be drawn from the report data."); } });
  $("#report").innerHTML = html.join("");
  pageBoxes(r);
  feeds();
  document.title = `Incident report, ${addrLine(r).main || "Sandtable Beacon"}`;
}

function blk(no, title, aside, body, cls = "") {
  return `<section class="blk ${cls}" id="b${no}"><div class="blk-no" aria-hidden="true">${no}</div><div>
    <div class="blk-h"><h2>${esc(title)}</h2>${aside ? `<span class="aside">${aside}</span>` : ""}</div>${body}</div></section>`;
}
const safe = (f) => { try { return f(); } catch (e) { console.error(e); return NONE("This section could not be drawn from the report data."); } };

function addrLine(r) {
  const inc = r.incident || {}, b = r.building || {}, p = bundle?.site?.property || {};
  const main = b.address || p.address || (inc.address ? String(inc.address).split(",")[0] : "");
  const rest = [b.city || p.city, [b.state || p.state, b.zip || p.zip].filter(Boolean).join(" ")].filter(Boolean).join(", ");
  return { main: main || "Address not recorded", rest, full: inc.address || [main, rest].filter(Boolean).join(", ") };
}
function isSample(r) { return SAMPLE || r.sample === true || r.provenance?.beacon?.sample === true; }
function isSim(r) { return r.simulated === true || Object.values(r.provenance || {}).some((v) => v && (v.status === "simulated" || v.simulated === true)); }

const PHASES = { standby: ["Standby", "ended"], preplan: ["Pre-plan", "active"], operations: ["Operations", "active"], mayday: ["Mayday", "alarm"], rescue: ["Rescue", "alarm"],
  egress: ["Egress", "egress"], terminated: ["All out, ended", "ended"] };

function header(r) {
  const inc = r.incident || {}, a = addrLine(r), b = r.building || {};
  const ph = PHASES[inc.phase] || [words(inc.phase) || "Not recorded", "active"];
  const floorLabel = inc.floor_label || r.accountability?.floor_label || (bundle?.twin?.floors || []).find((f) => f.id === (inc.floor || r.accountability?.floor))?.label;
  const stories = b.stories || bundle?.site?.property?.stories;
  const sub = [a.rest, floorLabel ? `Floor ${floorLabel}${stories ? ` of ${stories}` : ""}` : null, b.property_type || bundle?.site?.property?.property_type].filter(Boolean).join("  ·  ");
  const sb = inc.storyboard || {};
  const mode = inc.mode ? words(inc.mode) + (inc.script ? `, ${inc.script} script` : "") : sb.script ? `Exercise, ${sb.script} script${sb.mode === "auto" ? " (hands-free)" : ""}` : bundle?.config?.exercise ? "Exercise" : "Not recorded";
  const started = wallTime(inc.started_t), gen = wallTime(r.generated_t);
  const marks = [isSample(r) ? `<span class="stamp sample">Sample</span>` : "", isSim(r) ? `<span class="stamp">Simulated</span>` : "", !isSample(r) && !isSim(r) ? `<span class="stamp live">Live data</span>` : ""].join("");
  return `<header class="head">
    <div class="head-top"><div class="prod"><span class="mark" aria-hidden="true"></span>Sandtable Beacon</div><div class="doc-kind">Incident report${inc.id ? `, ${esc(inc.id)}` : ""}</div><div class="marks">${marks}</div></div>
    <h1 class="addr">${esc(a.main)}</h1>
    <div class="addr-sub">${esc(sub)}</div>
    <dl class="meta">
      <div><dt>Incident clock</dt><dd class="clock">${num(inc.elapsed_s) !== null ? clock(inc.elapsed_s) : "–"}</dd></div>
      <div><dt>Phase</dt><dd><span class="ph ${ph[1]}">${esc(ph[0])}</span></dd></div>
      <div><dt>Alarm received</dt><dd>${esc(started || "Not recorded")}</dd></div>
      <div><dt>Report generated</dt><dd>${esc(gen || "Not recorded")}</dd></div>
      <div><dt>Mode</dt><dd>${esc(mode)}</dd></div>
    </dl>
    ${record(r)}
    ${honesty(r)}
  </header>`;
}

// the building record facts the report used, and the ones the record does not have
function record(r) {
  try {
    const b = r.building || {}, p = bundle?.site?.property || {};
    let facts = arr(b.facts).map((f) => [f.key, f.value]);
    if (!facts.length) {
      const v = (k) => b[k] ?? p[k];
      facts = [["Use", v("property_type")], ["Construction", v("construction")], ["Stories", v("stories")], ["Year built", v("year_built")],
        ["Building area", num(v("rba_sf")) !== null ? `${v("rba_sf").toLocaleString("en-US")} sf` : null], ["Roof height", num(v("height_m")) !== null ? `${v("height_m")} m` : null]];
    }
    facts = facts.filter(([k, v]) => has(v) && k !== "Use");
    const miss = arr(b.missing).map((m) => words(m).toLowerCase());
    if (!facts.length && !miss.length) return "";
    return `<div class="rec"><div class="hk">Building record</div><div><dl>${facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>${miss.length ? `<p class="miss">Not in the record: ${esc(miss.join(", "))}.</p>` : ""}</div></div>`;
  } catch { return ""; }
}

const PROV_NAMES = { building_record: "Building record", record: "Building record", costar: "Building record", aerial: "Aerial", vexcel: "Aerial", twin: "Interior twin", matterport: "Interior twin",
  crews: "Crew positions", uwb: "Crew positions", beacon: "Phones (Beacon)", phones: "Phones (Beacon)", scout: "Radar (Scout)", agent: "Command agent", llm: "Command agent" };
function honesty(r) {
  const groups = { real: [], fixture: [], simulated: [], sample: [], agent: [], other: [] };
  const prov = r.provenance && typeof r.provenance === "object" ? r.provenance : null;
  for (const [k, v0] of Object.entries(prov || {})) {
    if (v0 === null || v0 === undefined) continue;
    const v = typeof v0 === "string" ? { status: v0 } : v0;
    let st = String(v.status || v.mode || "").toLowerCase();
    if (v.sample || st === "sample") st = "sample";
    else if (v.simulated === true && !["real", "fixture"].includes(st)) st = "simulated";
    else if (st === "live" || v.real_sponsor_data === true) st = "real";
    else if (k === "agent" || k === "llm" || ["replay", "fallback", "reflex-fallback", "anthropic", "claude-cli"].includes(st)) st = "agent";
    const name = PROV_NAMES[k] || words(k);
    let text = v.text;
    if (!text && (k === "agent" || k === "llm")) text = BACKEND[v.backend || st]?.[1] || (v.backend ? `backend ${v.backend}` : null);
    if (!text && v.source) text = `source ${v.source}${v.connected === false ? ", Beacon server not connected" : ""}`;
    // the text often starts with its own label ("Aerial: ..."); do not say it twice
    const line = text ? (/^[^:]{2,32}:\s/.test(text) ? text : `${name}: ${text}`) : name;
    (groups[st] || groups.other).push(/[.)]$/.test(line) ? line : line + ".");
  }
  const L = { real: "Real", fixture: "Fixture", simulated: "Simulated", sample: "Sample", agent: "Agent", other: "Other" };
  const rows = Object.entries(groups).filter(([, v]) => v.length).map(([k, v]) => `<li><b>${L[k]}.</b>${v.map(esc).join(" ")}</li>`).join("");
  const note = r.sample_note ? `<div class="note">${esc(r.sample_note)}</div>` : "";
  return `<div class="honesty"><div class="hk">What is real</div>${rows ? `<ul>${rows}</ul>` : `<ul><li>The report does not state where its data came from (no <code>provenance</code>). Treat every position as unverified.</li></ul>`}${note}</div>`;
}

// ------------------------------------------------------------------ 1 summary
function summary(r) {
  return safe(() => {
    const ppl = arr(r.people), acc = r.accountability, tot = acc?.totals || {};
    const n = (p) => num(reportOf(p).people) ?? 1;
    const found = ppl.filter((p) => norm(p).prog !== "safe-outside"), outside = ppl.filter((p) => norm(p).prog === "safe-outside");
    const out = found.filter(isOut);
    const open = found.filter((p) => !isOut(p));
    const sum = (xs) => xs.reduce((s, p) => s + n(p), 0);
    const un = rangeOf(tot.unaccounted ?? (tot.unaccounted_low !== undefined ? { low: tot.unaccounted_low, high: tot.unaccounted_high } : null)) || sumRows(acc);
    let text = r.summary || null;
    if (!text) {
      const a = addrLine(r), inc = r.incident || {};
      const bits = [];
      bits.push(`${(PHASES[inc.phase] || [words(inc.phase) || "Incident"])[0]} at ${a.main}${num(inc.elapsed_s) !== null ? `, ${clock(inc.elapsed_s)} after the alarm` : ""}.`);
      if (ppl.length) {
        bits.push(`${sum(found)} ${sum(found) === 1 ? "person was" : "people were"} found in ${found.length} ${found.length === 1 ? "report" : "reports"}${found.length ? ` (${countBy(found, srcWord)})` : ""}; ${sum(out)} ${sum(out) === 1 ? "is" : "are"} out${open.length ? `, ${sum(open)} still inside` : ""}${outside.length ? `, and ${sum(outside)} reported ${sum(outside) === 1 ? "themself" : "themselves"} safe outside` : ""}.`);
      } else bits.push("Nobody has been reported or located.");
      if (acc && un) bits.push(`Against the building record, ${un.low === un.high ? un.low : `between ${un.low} and ${un.high}`} ${un.high === 1 ? "person is" : "people are"} unaccounted for${num(tot.suites_unscanned) ? ` and ${tot.suites_unscanned} ${tot.suites_unscanned === 1 ? "suite was" : "suites were"} never searched` : ""}: an estimate from floor area, not a head count.`);
      else if (!acc) bits.push("The Beacon module was not connected, so there is no accountability against the building record.");
      text = bits.join(" ");
    }
    const fig = (n_, l, cls = "") => `<div class="fig ${cls}"><div class="n">${n_}</div><div class="l">${esc(l)}</div></div>`;
    const figs = `<div class="figs">${fig(sum(found), "people found inside")}${fig(sum(out) + sum(outside), "out or safe outside")}${fig(sum(open), "found, still inside")}${fig(arr(r.crews).length || "–", "crews committed")}
      ${un ? fig(`${un.low === un.high ? un.low : `${un.low}–${un.high}`}<small>people</small>`, "unaccounted for on this floor: a range from the building record, not a count", "range") : fig("–", "unaccounted for: no accountability (Beacon module not connected)", "range")}</div>`;
    return blk(1, "Summary", "", `<div class="summary"><p>${esc(text)}</p>${figs}</div>`);
  });
}
function sumRows(acc) {
  const rows = arr(acc?.rows || acc?.suites); if (!rows.length) return null;
  let lo = 0, hi = 0; for (const x of rows) { const u = rangeOf(x.unaccounted); if (u) { lo += u.low || 0; hi += u.high || 0; } }
  return { low: lo, high: hi };
}
const srcWord = (p) => ({ phone: "phone", radar: "radar", crew: "crew search" }[p.source] || p.source || "unknown");
function countBy(xs, f) { const m = {}; for (const x of xs) m[f(x)] = (m[f(x)] || 0) + 1; return Object.entries(m).map(([k, v]) => `${v} by ${k}`).join(", "); }

// ------------------------------------------------------------------ 2 people
const METHOD = { "uwb-twr": "UWB ranging", "uwb-twr+bearing": "UWB ranging and the phone's bearing", "ble-rssi": "Bluetooth signal strength", "ble-presence": "Bluetooth, heard only", "cir-radar": "radar echo", "app self-report": "the app's own report", "crew search": "crew search" };
function foundBy(p) {
  const s = { phone: "Phone", radar: "Radar", crew: "Crew" }[p.source] || words(p.source) || "Unknown";
  const m = p.method ? METHOD[p.method] || p.method : null;
  return m ? `${s}: ${m}` : s;
}
function geomWords(p) {
  const g = personGeom(p);
  const s = (v) => (num(v) !== null ? `±${(+v).toFixed(1)} m` : "");
  switch (g.kind) {
    case "fix": return [`Fix ${s(g.sigma_m)}`, "one point"];
    case "candidates": { const c = arr(g.candidates); return [`${c.length} candidates`, c.map((q) => `${q.room_label || "?"} ${num(q.weight) !== null ? Math.round(q.weight * 100) + " %" : ""}`).join(" or ")]; }
    case "ring": return [`Ring ${num(g.r_m) !== null ? (+g.r_m).toFixed(1) + " m" : ""} ${s(g.sigma_m)}`.trim(), `range from puck ${g.puck || "?"}${g.ring_kind === "ble" ? ", Bluetooth (wide)" : ""}; not a position`];
    case "zone": return ["Zone", `${g.room_label || "room"}${num(g.prob) !== null ? `, ${Math.round(g.prob * 100)} %` : ""}; room level only`];
    case "point": return ["Point", g.basis || "where the crew reported it"];
    case "none": return ["None", norm(p).prog === "safe-outside" ? "outside, self-reported" : "heard, nothing spatial"];
    default: return [words(g.kind), ""];
  }
}
const STATE = { out: ["Out", "out"], "safe-outside": ["Safe outside", "safe-outside"], reached: ["Reached", "open"], assigned: ["Crew assigned", "open"], located: ["No crew yet", "open"], reported: ["Reported only", "open"] };
function people(r) {
  return safe(() => {
    const ps = arr(r.people);
    if (!ps.length) return blk(2, "People found", "", NONE("None recorded: no phone, radar or crew has reported a person."));
    const rows = ps.map((p) => {
      const rep = reportOf(p), g = geomWords(p), w = p.where || {};
      const where = typeof w === "string" ? w : w.text || [w.room_label, w.suite ? `suite ${w.suite}` : null].filter(Boolean).join(", ");
      const st = STATE[norm(p).prog] || [words(norm(p).prog) || "Not recorded", "open"];
      const people_ = num(rep.people) !== null ? `${rep.people} ${rep.people === 1 ? "person" : "people"}` : "number unknown";
      const move = rep.can_move === true ? "can move" : rep.can_move === false ? "cannot move" : "mobility unknown";
      return `<tr>
        <td><span class="id">${esc(p.id || "?")}</span><span class="by">${esc(foundBy(p))}</span></td>
        <td>${rep.status ? `<span class="st ${esc(rep.status)}">${esc(rep.status)}</span> ` : ""}${esc(people_)}, ${esc(move)}${rep.text ? `<span class="quote">${esc(rep.text)}</span>` : ""}</td>
        <td>${esc(where || "Not recorded")}${p.note ? `<span class="geo-sub">${esc(p.note)}</span>` : ""}</td>
        <td><span class="geo">${esc(g[0])}</span><span class="geo-sub">${esc(g[1])}</span></td>
        <td>${trel(p.located_t_rel)}</td>
        <td>${p.assigned ? `<span class="cs">${esc(p.assigned)}</span>${num(p.assigned_t_rel) !== null ? trel(p.assigned_t_rel) : ""}` : `<span class="dim">none</span>`}</td>
        <td>${trel(p.reached_t_rel)}</td>
        <td>${trel(p.out_t_rel)}</td>
        <td><span class="st ${st[1]}">${esc(st[0])}</span></td></tr>`;
    }).join("");
    const head = `<tr><th>Who, found by</th><th>Their own report</th><th>Where</th><th>What the measurement supports</th><th>Located</th><th>Crew sent</th><th>Reached</th><th>Out</th><th>State</th></tr>`;
    return blk(2, "People found", "One row per phone, radar contact or crew find. Times are incident clock (T+ minutes:seconds after the alarm).",
      `<div class="tbl-scroll"><table class="tbl people-tbl"><thead>${head}</thead><tbody>${rows}</tbody></table></div>`);
  });
}

// ------------------------------------------------------------------ 3 accountability
const ACC_STATE = { "people-reported": "people reported", "devices-heard": "devices heard only", silent: "silent (not proof of empty)", "vacant-on-record": "vacant on record", unscanned: "not searched" };
function accountability(r) {
  return safe(() => {
    const acc = r.accountability;
    const note = `<p class="range-note">Unaccounted is a range, not a count: the building record's expected occupancy (an estimate from floor area) minus the people reported inside or safe outside. Devices are not people: watches and laptops advertise too, and some phones do not.</p>`;
    if (!acc) return blk(3, "Accountability", "", `<div class="acc-off"><b>Beacon module not connected.</b> There is no accountability against the building record in this report: no phones were heard, so nobody can say how many people may still be inside. Use the radio roll call and the crews' searches.</div>`);
    const rows = arr(acc.rows || acc.suites);
    if (!rows.length) return blk(3, "Accountability", "", NONE("None recorded: the accountability table has no suites.") + note);
    const maxHi = Math.max(1, ...rows.map((x) => rangeOf(x.expected)?.high || 0), ...rows.map((x) => rangeOf(x.unaccounted)?.high || 0));
    const bar = (u) => {
      if (!u) return `<span class="dim">–</span>`;
      const W = 100, x0 = (u.low / maxHi) * W, x1 = (u.high / maxHi) * W;
      const txt = u.low === u.high ? `${u.low}` : `${u.low}–${u.high}`;
      return `<div class="rng"><span class="txt${u.high === 0 ? " zero" : ""}">${txt}</span><svg viewBox="0 0 ${W} 6" preserveAspectRatio="none" aria-hidden="true"><rect class="track" x="0" y="1.5" width="${W}" height="3"/>${u.high > 0 ? `<rect class="span" x="${x0.toFixed(1)}" y="0" width="${Math.max(1.2, x1 - x0).toFixed(1)}" height="6"/>` : ""}</svg></div>`;
    };
    // the basis of each estimate is long and repeats: footnote it once
    const bases = [];
    const fn = (b) => { if (!b) return ""; let i = bases.indexOf(b); if (i < 0) { bases.push(b); i = bases.length - 1; } return `<sup class="fn">${String.fromCharCode(97 + i)}</sup>`; };
    const body = rows.map((x) => {
      const e = rangeOf(x.expected), u = rangeOf(x.unaccounted), basis = x.expected?.basis;
      const crews = arr(x.crews).length ? arr(x.crews).join(", ") : x.assigned || "";
      const outcome = x.outcome || [x.reached ? "reached" : null, x.out ? "out" : null].filter(Boolean).join(", ") || x.why || (x.state === "unscanned" ? "not searched" : "");
      const unsc = x.state === "unscanned";
      const phones = unsc && !x.phones_opt_in && !x.cold_devices ? `<span class="dim">no puck in range</span>` : [num(x.phones_opt_in) !== null ? `${x.phones_opt_in} with the app` : null, num(x.cold_devices) !== null ? `${x.cold_devices}${x.cold_range ? ` (${x.cold_range[0]}–${x.cold_range[1]})` : ""} devices` : null].filter(Boolean).join("<br>");
      const exp = e ? (num(x.expected?.people) !== null && e.low !== e.high ? `${x.expected.people} <span class="dim">(${e.low}–${e.high})</span>` : e.low === e.high ? `${e.low}` : `${e.low}–${e.high}`) : "–";
      return `<tr class="${esc(x.state || "")}">
        <td><span class="suite">${esc(x.suite || x.room_label || "?")}</span></td>
        <td><span class="ten">${esc(x.tenant || (x.state === "vacant-on-record" ? "vacant on record" : "no tenant on record"))}</span>${x.life_hazard ? `<span class="lh">${esc(x.life_hazard)}</span>` : ""}</td>
        <td class="nw">${exp}${fn(basis)}</td>
        <td>${phones || `<span class="dim">none heard</span>`}</td>
        <td class="num">${num(x.people_reported) ?? "–"}</td>
        <td class="num">${num(x.located) ?? "–"}</td>
        <td>${bar(u)}<span class="basis">${esc(ACC_STATE[x.state] || words(x.state))}</span></td>
        <td class="num">${num(x.priority) !== null ? `<span class="pri p${x.priority}">${x.priority}</span>` : `<span class="dim">–</span>`}</td>
        <td>${crews ? `<span class="cs">${esc(crews)}</span>` : `<span class="dim">none</span>`}</td>
        <td>${esc(outcome)}</td></tr>`;
    }).join("");
    const t = acc.totals || {};
    const tu = rangeOf(t.unaccounted ?? (t.unaccounted_low !== undefined ? { low: t.unaccounted_low, high: t.unaccounted_high } : null)) || sumRows(acc);
    const te = rangeOf(t.expected ?? (t.expected_low !== undefined ? { low: t.expected_low, high: t.expected_high } : t.expected_people));
    const foot = `<tfoot><tr><td>Floor</td><td>${rows.length} suites</td><td>${fmtR(te)}</td><td>${num(t.phones_opt_in) ?? "–"} with the app${num(t.cold_devices) !== null ? `<br>${t.cold_devices} devices` : ""}</td><td class="num">${num(t.people_reported) ?? "–"}</td><td class="num">${num(t.located) ?? "–"}</td><td>${fmtR(tu)}</td><td></td><td></td><td>${num(t.safe_outside) ? `${t.safe_outside} safe outside (self-reported)` : ""}${num(t.found_by_crews) ? `${num(t.safe_outside) ? "; " : ""}${t.found_by_crews} found by crews outside any suite` : ""}</td></tr></tfoot>`;
    const head = `<tr><th>Suite</th><th>Tenant on record, life hazard</th><th>Expected</th><th>Phones heard</th><th class="num">People reported</th><th class="num">Located</th><th>Unaccounted</th><th class="num">Priority</th><th>Crew</th><th>Outcome</th></tr>`;
    const src = acc.source?.text ? esc(acc.source.text) : "";
    const notes = bases.length ? `<ol class="bases">${bases.map((b, i) => `<li><sup class="fn">${String.fromCharCode(97 + i)}</sup>${esc(b)}</li>`).join("")}</ol>` : "";
    return blk(3, "Accountability", `Floor ${esc(acc.floor_label || floorLabelOf(acc.floor) || "?")}, suite by suite. ${src}`,
      `<div class="tbl-scroll"><table class="tbl acc-tbl"><thead>${head}</thead><tbody>${body}</tbody>${foot}</table></div>${note}${notes}`);
  });
}
const fmtR = (u) => (!u ? "–" : u.low === u.high ? `${u.low}` : `${u.low}–${u.high}`);
const floorLabelOf = (id) => (bundle?.twin?.floors || []).find((f) => f.id === id)?.label;

// ------------------------------------------------------------------ 4 plan
function plan(r) {
  return safe(() => {
    const tw = bundle?.twin;
    if (!tw) return blk(4, "Plan of the floor", "", NONE("Plan unavailable: the floor geometry comes from /api/bundle, which did not answer."), "plan-blk");
    const fid = r.incident?.floor || r.accountability?.floor || tw.exercise_floor;
    const floor = tw.floors.find((f) => f.id === fid) || tw.floors.find((f) => f.id === tw.exercise_floor);
    const { svg, drawn, notDrawn } = planSVG(r, tw, floor);
    const sw = (inner) => `<svg viewBox="-1.5 -1 3 2">${inner}</svg>`;
    const legend = [
      [sw(`<circle cx="0" cy="0" r=".55" class="pl-pers-ring" stroke-width=".12" stroke-dasharray=".25 .15"/><circle cx="0" cy="0" r=".26" class="pl-pers"/>`), "<b>Fix</b>: one point, circle = 1σ uncertainty"],
      [sw(`<circle cx="-.8" cy="0" r=".4" class="pl-cand"/><circle cx=".8" cy="0" r=".4" class="pl-cand"/><line x1="-.4" y1="0" x2=".4" y2="0" class="pl-cand-link"/>`), "<b>Candidates</b>: two possible places with their weights; the phone is at one, not between"],
      [sw(`<path d="M-1.3 .7 A1.6 1.6 0 0 1 1.3 .7" class="pl-ring-band" stroke-width=".55"/><path d="M-1.3 .7 A1.6 1.6 0 0 1 1.3 .7" class="pl-ring-line"/>`), "<b>Ring</b>: a range from one puck; the person is somewhere on the band. Dashed: ruled out by the building"],
      [sw(`<rect x="-1.2" y="-.7" width="2.4" height="1.4" class="pl-zone"/>`), "<b>Zone</b>: room level only"],
      [sw(`<path d="M0 -.6 L.6 .5 L-.6 .5 Z" class="pl-pers"/>`), "<b>Crew find</b>: where the crew reported the person"],
      [sw(`<path d="M-.4 -.4 L.4 .4 M-.4 .4 L.4 -.4" class="pl-ghost"/>`), "Position ruled out (pruned)"],
      [sw(`<path d="M0 -.45 L.45 0 L0 .45 L-.45 0 Z" class="pl-puck"/>`), "Puck thrown into the room"],
      [sw(`<circle cx="0" cy="0" r=".55" fill="var(--e1)" class="pl-crew"/>`), "Crew: last known position; dashed line = path walked"],
      [sw(`<rect x="-1.2" y="-.7" width="2.4" height="1.4" class="pl-haz-room"/>`), "Hazard: fire or smoke reported"],
      [sw(`<rect x="-1.2" y="-.7" width="2.4" height="1.4" class="pl-diff"/>`), "Differs from the twin or the record"],
      [sw(`<rect x="-1.2" y="-.7" width="2.4" height="1.4" fill="var(--p-suite)" stroke="var(--p-wall)" stroke-width=".06"/><rect x="-1.2" y="-.7" width="2.4" height="1.4" class="pl-uns"/>`), "Suite not searched (no puck in range)"],
      [sw(`<circle cx="0" cy="0" r=".6" fill="#ffdd00"/><text x="0" y=".05" font-size=".8" text-anchor="middle" dominant-baseline="central" font-weight="700" fill="#111">A</text>`), "Building side (A = address side, then clockwise)"],
    ].map(([s, t]) => `<li>${s}<span>${t}</span></li>`).join("");
    const drawnList = drawn.length ? `<ul>${drawn.map((d) => `<li><span class="cs" style="color:var(--person)">${esc(d.id)}</span><span>${esc(d.text)}</span></li>`).join("")}</ul>` : NONE("No person has a position to draw.");
    const nd = notDrawn.length ? `<p class="notdrawn">Not drawn: ${notDrawn.map((d) => `${esc(d.id)} (${esc(d.why)})`).join("; ")}.</p>` : "";
    const twinNote = tw.source?.synthetic ? "Interior: synthetic twin fitted to the real footprint, not a survey of this floor." : "";
    return blk(4, "Plan of the floor", `Floor ${esc(floor.label)}, everything at the position the report gives it. ${esc(twinNote)}`,
      `<div class="plan-wrap"><figure class="plan-fig">${svg}<figcaption>North is up; scale bar 10 m. Positions in the building frame of the twin.</figcaption></figure>
       <div><div class="key"><h3>People on this plan</h3>${drawnList}${nd}</div><div class="key"><h3>Key</h3><ul>${legend}</ul></div></div></div>`, "plan-blk");
  });
}

function planSVG(r, tw, floor) {
  const fid = floor.id;
  const xs = floor.outline.map((p) => p[0]), ys = floor.outline.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = 4.2;
  // rotate the drawing so north is up: the twin's +x axis has compass bearing b
  const b = (tw.georef?.bearing_deg ?? 90) * Math.PI / 180;
  const rot = Math.PI / 2 - b; // angle to rotate building frame so that north points to +y (up)
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  const R = ([x, y]) => { const dx = x - cx, dy = y - cy; return [cx + dx * Math.cos(rot) - dy * Math.sin(rot), cy + dx * Math.sin(rot) + dy * Math.cos(rot)]; };
  // bounds after rotation
  const corners = floor.outline.map(R);
  const X0 = Math.min(...corners.map((p) => p[0])) - pad, X1 = Math.max(...corners.map((p) => p[0])) + pad;
  const Y0 = Math.min(...corners.map((p) => p[1])) - pad, Y1 = Math.max(...corners.map((p) => p[1])) + pad + 1.6;
  const P = (p) => { const q = R(p); return [+(q[0]).toFixed(3), +(Y1 + Y0 - q[1]).toFixed(3)]; }; // flip y for SVG
  const pts = (poly) => poly.map((q) => P(q).join(",")).join(" ");
  const f = (v) => (+v).toFixed(2);
  const o = [];
  const site = bundle?.site || {};
  const floorNo = Number(floor.label);
  const tenants = arr(site.tenants).filter((t) => t.floor === floorNo);
  const accRows = arr(r.accountability?.rows || r.accountability?.suites);
  const unscanned = new Set(accRows.filter((x) => x.state === "unscanned").map((x) => x.room || tw.rooms.find((q) => q.suite === x.suite)?.id));
  o.push(`<defs>
    <pattern id="pl-hatch-uns" width="1" height="1" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="1" stroke="var(--p-unscanned)" stroke-width=".42"/></pattern>
    <pattern id="pl-hatch-haz" width=".9" height=".9" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)"><line x1="0" y1="0" x2="0" y2=".9" stroke="var(--alarm)" stroke-opacity=".45" stroke-width=".22"/></pattern>
    <clipPath id="pl-clip-floor"><polygon points="${pts(floor.outline)}"/></clipPath>
    <pattern id="pl-hatch-person" width=".8" height=".8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2=".8" stroke="var(--person)" stroke-opacity=".5" stroke-width=".16"/></pattern>
  </defs>`);
  const rooms = tw.rooms.filter((q) => q.floor === fid);
  for (const rm of rooms) o.push(`<polygon points="${pts(rm.polygon)}" class="pl-r ${esc(rm.kind)}"/>`);
  for (const rm of rooms) if (unscanned.has(rm.id)) o.push(`<polygon points="${pts(rm.polygon)}" class="pl-uns"/>`);
  // hazards (under walls)
  for (const h of arr(r.hazards)) {
    const g = h.geometry || {};
    if (g.kind === "room" && g.polygon) o.push(`<polygon points="${pts(g.polygon)}" class="pl-haz-room"><title>${esc(h.label)}: ${esc(h.kind)}</title></polygon>`);
  }
  for (const w of arr(tw.walls).filter((q) => q.floor === fid)) { const a = P(w.a), c = P(w.b); o.push(`<line x1="${a[0]}" y1="${a[1]}" x2="${c[0]}" y2="${c[1]}" class="pl-wall"/>`); }
  for (const d of arr(tw.doors).filter((q) => q.floor === fid)) {
    const dir = wallDir(tw, d.at), w = (d.width_m || 0.9) / 2, a = P([d.at[0] - dir[0] * w, d.at[1] - dir[1] * w]), c = P([d.at[0] + dir[0] * w, d.at[1] + dir[1] * w]);
    o.push(`<line x1="${a[0]}" y1="${a[1]}" x2="${c[0]}" y2="${c[1]}" class="pl-door" stroke="var(--p-${d.rooms?.some((id) => rooms.find((q) => q.id === id)?.kind === "corridor") ? "corr" : "suite"})"/>`);
  }
  o.push(`<polygon points="${pts(floor.outline)}" class="pl-out"/>`);
  // labels; a suite with people, pucks or crews drawn in it keeps only its number, in the corner farthest from them
  const centre = (poly) => [poly.reduce((s, p) => s + p[0], 0) / poly.length, poly.reduce((s, p) => s + p[1], 0) / poly.length];
  const inside = (pt, poly) => { let c = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const [xi, yi] = poly[i], [xj, yj] = poly[j]; if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) c = !c; } return c; };
  const items = [];
  for (const p of arr(r.people)) {
    const g = personGeom(p);
    if ((g.kind === "fix" || g.kind === "point") && num(g.x) !== null) items.push([g.x, g.y], [g.x, g.y + 1.0]);
    if (g.kind === "candidates") for (const q of arr(g.candidates)) if (num(q.x) !== null) items.push([q.x, q.y], [q.x, q.y + 1.0], [q.x, q.y - 1.0]);
    if (g.kind === "zone") { const zr = rooms.find((q) => q.id === g.room); if (zr) items.push(centre(zr.polygon)); }
  }
  for (const d of arr(r.differences)) if (d.at) items.push(d.at); else if (d.room) { const dr = rooms.find((q) => q.id === d.room); if (dr) items.push(dLabelAt(dr)); }
  // labels for rings and zones go where nothing else is drawn
  const labelAt = {};
  for (const p of arr(r.people)) {
    const g = personGeom(p);
    if (g.kind === "ring" && num(g.r_m) !== null) {
      const cands = []; for (const [a0, a1] of (arr(g.arcs).length ? g.arcs : [[0, 360]])) for (let a = a0 + 8; a <= a1 - 8; a += 6) { const t = a * Math.PI / 180, q = [(g.cx ?? g.x) + g.r_m * Math.cos(t), (g.cy ?? g.y) + g.r_m * Math.sin(t)]; if (inside(q, floor.outline)) cands.push(q); }
      labelAt[p.id] = freeSpot(cands.length ? cands : [ringLabelAt(g)], items); items.push(labelAt[p.id]);
    } else if (g.kind === "zone") {
      const zr = rooms.find((q) => q.id === g.room); const poly = g.polygon || zr?.polygon; if (!poly) continue;
      const zx = poly.map((q) => q[0]), zy = poly.map((q) => q[1]), cands = [];
      for (let x = Math.min(...zx) + 1.6; x <= Math.max(...zx) - 1.6; x += 0.5) for (let y = Math.min(...zy) + 1; y <= Math.max(...zy) - 1; y += 0.5) cands.push([x, y]);
      labelAt[p.id] = freeSpot(cands.length ? cands : [centre(poly)], items); items.push(labelAt[p.id]);
    }
  }
  for (const pk of pucksOf(r)) if (num(pk.x) !== null) items.push([pk.x, pk.y]);
  for (const c of arr(r.crews)) { const l = crewPos(c); if (l) items.push(l); }
  for (const rm of rooms) {
    const c = P(centre(rm.polygon));
    if (rm.kind === "suite") {
      const ten = tenants.filter((t) => t.suite === rm.suite);
      const cat = ten.length ? shortCat(ten[0].industry) : (rm.registered === false ? "no registration" : "");
      const lh = ten.some((t) => t.life_hazard && !/staff only/.test(t.life_hazard));
      const here = items.filter((q) => inside(q, rm.polygon));
      if (here.length) {
        const rx = rm.polygon.map((q) => q[0]), ry = rm.polygon.map((q) => q[1]), ins = 1.5;
        const [ax, bx, ay, by] = [Math.min(...rx) + ins, Math.max(...rx) - ins, Math.min(...ry) + ins, Math.max(...ry) - ins];
        const best = freeSpot([[ax, by], [bx, by], [ax, ay], [bx, ay], [(ax + bx) / 2, by], [(ax + bx) / 2, ay], [ax, (ay + by) / 2], [bx, (ay + by) / 2]], items);
        const a = P(best);
        o.push(`<text x="${f(a[0])}" y="${f(a[1] + 0.5)}" class="pl-sn" style="font-size:1.3px">${esc(rm.suite)}</text>`);
        continue;
      }
      o.push(`<text x="${f(c[0])}" y="${f(c[1] - 0.6)}" class="pl-sn">${esc(rm.suite)}</text>`);
      if (cat) o.push(`<text x="${f(c[0])}" y="${f(c[1] + 0.55)}" class="pl-ten">${esc(cat)}</text>`);
      if (lh) o.push(`<text x="${f(c[0])}" y="${f(c[1] + 1.5)}" class="pl-lh">life hazard</text>`);
    } else if (rm.kind === "stair") {
      const st = arr(tw.stairs).find((s) => s.room === rm.id);
      o.push(`<text x="${f(c[0])}" y="${f(c[1] - 0.1)}" class="pl-core" style="font-weight:700">${esc(rm.label)}</text>`);
      if (st) o.push(`<text x="${f(c[0])}" y="${f(c[1] + 0.9)}" class="pl-core" style="font-weight:500">${esc([st.standpipe ? "standpipe" : null, st.discharge ? `to ${st.discharge.replace(/\s*\(.*\)/, "")}` : null].filter(Boolean).join(", "))}</text>`);
    } else if (rm.kind !== "corridor") o.push(`<text x="${f(c[0])}" y="${f(c[1] + 0.3)}" class="pl-core">${esc(rm.label)}</text>`);
  }
  // hazards on edges and points
  for (const h of arr(r.hazards)) {
    const g = h.geometry || {};
    if (g.kind === "edge" && g.polyline?.length >= 2) {
      const a = P(g.polyline[0]), c = P(g.polyline[g.polyline.length - 1]), m = [(a[0] + c[0]) / 2, (a[1] + c[1]) / 2];
      o.push(`<g><line x1="${a[0]}" y1="${a[1]}" x2="${c[0]}" y2="${c[1]}" class="pl-haz-edge"/><path d="M${f(m[0] - .55)} ${f(m[1] - .55)} L${f(m[0] + .55)} ${f(m[1] + .55)} M${f(m[0] - .55)} ${f(m[1] + .55)} L${f(m[0] + .55)} ${f(m[1] - .55)}" class="pl-haz-x" stroke="var(--p-halo)" stroke-width=".55"/><path d="M${f(m[0] - .55)} ${f(m[1] - .55)} L${f(m[0] + .55)} ${f(m[1] + .55)} M${f(m[0] - .55)} ${f(m[1] + .55)} L${f(m[0] + .55)} ${f(m[1] - .55)}" class="pl-haz-x"/><title>${esc(h.label)}: ${esc(h.kind)}</title></g>`);
    } else if (g.at || g.kind === "point") { const p = P(g.at || [g.x, g.y]); o.push(`<circle cx="${p[0]}" cy="${p[1]}" r="1" class="pl-haz-room"/>`); }
  }
  // differences from the twin or record
  for (const d of arr(r.differences)) {
    if (d.room) { const rm = rooms.find((q) => q.id === d.room); if (rm) { const c = P(dLabelAt(rm)); o.push(`<polygon points="${pts(rm.polygon)}" class="pl-diff"/><text x="${f(c[0])}" y="${f(c[1] + .3)}" class="pl-diff-t" text-anchor="middle">${esc(d.id)}</text>`); } }
    else if (d.at) { const p = P(d.at); o.push(`<path d="M${f(p[0])} ${f(p[1] - .55)} L${f(p[0] + .55)} ${f(p[1])} L${f(p[0])} ${f(p[1] + .55)} L${f(p[0] - .55)} ${f(p[1])} Z" class="pl-diff-m"/><text x="${f(p[0] + .8)}" y="${f(p[1] + .3)}" class="pl-diff-t">${esc(d.id)}</text>`); }
  }
  // crews: track then last position
  const crewColor = (c) => (c.id === "E1" || c.kind === "engine" ? "var(--e1)" : c.id === "RIT" || c.kind === "rit" ? "var(--rit)" : "var(--p-ink)");
  for (const c of arr(r.crews)) if (arr(c.track).length > 1) o.push(`<polyline points="${pts(c.track)}" class="pl-track" stroke="${crewColor(c)}"/>`);
  // pucks
  for (const pk of pucksOf(r)) {
    if (num(pk.x) === null) continue; const p = P([pk.x, pk.y]);
    o.push(`<path d="M${f(p[0])} ${f(p[1] - .42)} L${f(p[0] + .42)} ${f(p[1])} L${f(p[0])} ${f(p[1] + .42)} L${f(p[0] - .42)} ${f(p[1])} Z" class="pl-puck"/><text x="${f(p[0] + .55)}" y="${f(p[1] + .75)}" class="pl-puck-t">${esc(pk.id)}</text>`);
  }
  // people
  const drawn = [], notDrawn = [];
  const label = (p, at, dy = -1.0) => {
    const rep = reportOf(p), n = num(rep.people);
    o.push(`<text x="${f(at[0])}" y="${f(at[1] + dy)}" class="pl-pt" text-anchor="middle">${esc(p.id)}${n !== null ? ` · ${n}` : ""}</text>`);
  };
  for (const p of arr(r.people)) {
    const g = personGeom(p);
    const ghost = (list) => { for (const q of arr(list)) { if (num(q.x) === null) continue; const a = P([q.x, q.y]); o.push(`<path d="M${f(a[0] - .4)} ${f(a[1] - .4)} L${f(a[0] + .4)} ${f(a[1] + .4)} M${f(a[0] - .4)} ${f(a[1] + .4)} L${f(a[0] + .4)} ${f(a[1] - .4)}" class="pl-ghost"/><text x="${f(a[0])}" y="${f(a[1] + 1.05)}" class="pl-gt" text-anchor="middle">${esc(p.id)} ruled out</text>`); } };
    if (g.kind === "fix" && num(g.x) !== null) {
      const a = P([g.x, g.y]), s = Math.max(0.35, num(g.sigma_m) ?? 0.5);
      ghost(g.pruned);
      o.push(`<circle cx="${a[0]}" cy="${a[1]}" r="${f(s)}" class="pl-pers-ring" stroke-width=".1" stroke-dasharray=".25 .15"/><circle cx="${a[0]}" cy="${a[1]}" r=".28" class="pl-pers"/>`);
      label(p, a, -s - 0.35);
      drawn.push({ id: p.id, text: `fix, ±${(num(g.sigma_m) ?? 0).toFixed(1)} m${g.from ? `; position from ${g.from}` : ""}` });
    } else if (g.kind === "candidates" && arr(g.candidates).length) {
      const cs = arr(g.candidates).filter((q) => num(q.x) !== null);
      ghost(g.pruned);
      for (let i = 1; i < cs.length; i++) { const a = P([cs[0].x, cs[0].y]), c = P([cs[i].x, cs[i].y]); o.push(`<line x1="${a[0]}" y1="${a[1]}" x2="${c[0]}" y2="${c[1]}" class="pl-cand-link"/>`); }
      cs.forEach((q, i) => {
        const a = P([q.x, q.y]), s = Math.max(0.4, num(q.sigma_m) ?? 0.4);
        o.push(`<circle cx="${a[0]}" cy="${a[1]}" r="${f(s)}" class="pl-cand"/><circle cx="${a[0]}" cy="${a[1]}" r=".12" class="pl-pers"/>`);
        o.push(`<text x="${f(a[0])}" y="${f(a[1] - s - 0.35)}" class="pl-pt" text-anchor="middle">${esc(p.id)}${i === 0 ? ` · ${num(reportOf(p).people) ?? "?"}` : ""}</text>`);
        if (num(q.weight) !== null) o.push(`<text x="${f(a[0])}" y="${f(a[1] + s + 0.75)}" class="pl-ps" text-anchor="middle">${Math.round(q.weight * 100)} %</text>`);
      });
      drawn.push({ id: p.id, text: `${cs.length} candidates (${cs.map((q) => `${q.room_label || "?"} ${num(q.weight) !== null ? Math.round(q.weight * 100) + " %" : ""}`).join(" or ")})${g.from ? `; from ${g.from}` : ""}` });
    } else if (g.kind === "ring" && num(g.r_m) !== null) {
      const cxr = g.cx ?? g.x, cyr = g.cy ?? g.y, rr = g.r_m, sg = num(g.sigma_m) ?? 0.1;
      const arcs = arr(g.arcs).length ? g.arcs : [[0, 360]];
      const arcPts = (a0, a1) => { const n = Math.max(2, Math.ceil(Math.abs(a1 - a0) / 3)); const out = []; for (let i = 0; i <= n; i++) { const t = (a0 + (a1 - a0) * i / n) * Math.PI / 180; out.push(P([cxr + rr * Math.cos(t), cyr + rr * Math.sin(t)])); } return out.map((q) => q.join(",")).join(" "); };
      // the excluded part of the circle, faint dashed
      const sorted = [...arcs].sort((u, v) => u[0] - v[0]); let prev = sorted[sorted.length - 1][1] - 360;
      for (const [a0, a1] of sorted) { if (a0 - prev > 0.5) o.push(`<polyline points="${arcPts(prev, a0)}" class="pl-ring-off"/>`); prev = a1; }
      // the band is clipped to the building: the twin says nobody is outside it
      for (const [a0, a1] of arcs) o.push(`<g clip-path="url(#pl-clip-floor)"><polyline points="${arcPts(a0, a1)}" class="pl-ring-band" stroke-width="${f(Math.max(0.25, 2 * sg))}"/></g><polyline points="${arcPts(a0, a1)}" class="pl-ring-line"/>`);
      const at = P(labelAt[p.id] || ringLabelAt(g));
      o.push(`<text x="${f(at[0])}" y="${f(at[1] - 0.2)}" class="pl-pt" text-anchor="middle">${esc(p.id)} · ${num(reportOf(p).people) ?? "?"}</text><text x="${f(at[0])}" y="${f(at[1] + 0.75)}" class="pl-ps" text-anchor="middle">somewhere on this ring</text>`);
      drawn.push({ id: p.id, text: `ring ${(+rr).toFixed(1)} m ±${sg.toFixed(1)} m from ${g.puck || "a puck"}${g.ring_kind === "ble" ? " (Bluetooth)" : ""}; not a position${g.from ? `; shape from ${g.from}` : ""}` });
    } else if (g.kind === "zone") {
      const rm = g.polygon ? null : rooms.find((q) => q.id === g.room);
      const poly = g.polygon || rm?.polygon;
      if (poly) { o.push(`<polygon points="${pts(poly)}" class="pl-zone"/>`); const c = P(labelAt[p.id] || centre(poly)); label(p, c, 0.35); drawn.push({ id: p.id, text: `zone: ${g.room_label || rm?.label || "room"}, room level only${g.inferred ? " (room from the report)" : ""}` }); }
      else notDrawn.push({ id: p.id, why: "zone without a room" });
    } else if (g.kind === "point" && num(g.x) !== null) {
      const a = P([g.x, g.y]);
      o.push(`<path d="M${f(a[0])} ${f(a[1] - .55)} L${f(a[0] + .5)} ${f(a[1] + .4)} L${f(a[0] - .5)} ${f(a[1] + .4)} Z" class="pl-pers"/>`);
      label(p, a, -0.85);
      drawn.push({ id: p.id, text: `crew find, ${g.basis || "reported position"}` });
    } else notDrawn.push({ id: p.id, why: p.state === "safe-outside" ? "safe outside, self-reported" : "no position in the report" });
  }
  const placed = [];
  for (const c of arr(r.crews)) {
    const l = crewPos(c); if (!l) continue; const a = P(l);
    const near = placed.filter((q) => Math.hypot(q[0] - a[0], q[1] - a[1]) < 2.2).length;
    const lx = a[0] + (near ? 1.1 : -1.1), anchor = near ? "start" : "end";
    o.push(`<circle cx="${a[0]}" cy="${a[1]}" r=".62" fill="${crewColor(c)}" class="pl-crew"/><text x="${f(lx)}" y="${f(a[1] + .35)}" class="pl-crew-t" fill="${crewColor(c)}" text-anchor="${anchor}">${esc(c.id)}</text>`);
    placed.push(a);
  }
  // sides A-D and north
  const dirOf = (brg) => { const t = brg * Math.PI / 180, e = Math.sin(t), n = Math.cos(t); return [e * Math.sin(b) + n * Math.cos(b), -e * Math.cos(b) + n * Math.sin(b)]; };
  for (const [k, s] of Object.entries(site.sides || {})) {
    const d = dirOf(s.bearing_deg), hx = (x1 - x0) / 2, hy = (y1 - y0) / 2;
    const t = Math.min(Math.abs(d[0]) > 1e-3 ? hx / Math.abs(d[0]) : 1e9, Math.abs(d[1]) > 1e-3 ? hy / Math.abs(d[1]) : 1e9) + 1.7;
    const at = P([cx + d[0] * t, cy + d[1] * t]);
    o.push(`<g class="pl-side"><circle cx="${f(at[0])}" cy="${f(at[1])}" r=".95"/><text x="${f(at[0])}" y="${f(at[1] + .05)}">${esc(k)}</text></g>`);
    if (s.street) { const t2 = t + 1.9; const st = P([cx + d[0] * t2, cy + d[1] * t2]); o.push(`<text x="${f(st[0])}" y="${f(st[1] + .3)}" class="pl-street">${esc(s.street)}</text>`); }
  }
  const nx = X0 + 1.6, ny = Y0 + 2.6;
  o.push(`<g class="pl-north" transform="translate(${f(nx)} ${f(ny)})"><path d="M0 -1.2 L.6 .75 L0 .38 L-.6 .75 Z"/><text x="0" y="-1.65">N</text></g>`);
  const sx = X0 + 1.0, sy = Y1 - 1.1;
  o.push(`<g class="pl-scale"><rect x="${f(sx)}" y="${f(sy - .18)}" width="5" height=".36"/><rect x="${f(sx + 5)}" y="${f(sy - .18)}" width="5" height=".36" fill="none" stroke="var(--p-ink)" stroke-width=".08"/><text x="${f(sx)}" y="${f(sy - .55)}">0</text><text x="${f(sx + 10)}" y="${f(sy - .55)}" text-anchor="end">10 m</text></g>`);
  const svg = `<svg viewBox="${f(X0)} ${f(Y0)} ${f(X1 - X0)} ${f(Y1 - Y0)}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Plan of floor ${esc(floor.label)} with the people, crews and hazards of the report" preserveAspectRatio="xMidYMid meet">${o.join("")}</svg>`;
  return { svg, drawn, notDrawn };
}
// the candidate spot farthest from everything already drawn (x weighted: labels are wider than tall)
function freeSpot(cands, avoid) {
  let best = cands[0], bd = -1;
  for (const c of cands) { const d = Math.min(1e9, ...avoid.map((q) => Math.hypot((q[0] - c[0]) / 1.8, q[1] - c[1]))); if (d > bd) { bd = d; best = c; } }
  return best;
}
const dLabelAt = (rm) => { const rx = rm.polygon.map((q) => q[0]), ry = rm.polygon.map((q) => q[1]); return [Math.min(...rx) + 1.0, Math.max(...ry) - 0.9]; };
function ringLabelAt(g) {
  const arcs = arr(g.arcs).length ? g.arcs : [[0, 360]];
  const big = [...arcs].sort((u, v) => (v[1] - v[0]) - (u[1] - u[0]))[0], mid = ((big[0] + big[1]) / 2) * Math.PI / 180;
  return [(g.cx ?? g.x) + g.r_m * Math.cos(mid), (g.cy ?? g.y) + g.r_m * Math.sin(mid)];
}
const pucksOf = (r) => arr(r.pucks).length ? arr(r.pucks) : arr(r.beacon?.pucks).length ? r.beacon.pucks : arr(aux.beacons?.pucks);
// a crew's last position: from the report, else from the board's state (live only)
function crewPos(c) {
  const l = c.last || {};
  if (num(l.x) !== null) return [l.x, l.y];
  if (num(c.x) !== null) return [c.x, c.y];
  const u = arr(aux.state?.units).find((q) => q.id === c.id);
  return u && num(u.x) !== null ? [u.x, u.y] : null;
}
const CAT = [[/therapist/i, "therapists"], [/laborator/i, "medical laboratory"], [/accounting/i, "accounting"], [/hospital/i, "hospital outpatient"], [/ambulatory/i, "ambulatory care"]];
function shortCat(ind) {
  for (const [re, w] of CAT) if (re.test(ind)) return w;
  const s = String(ind).replace(/^Offices of /i, "").replace(/\s*\(.*?\)/g, "").replace(/^(All other miscellaneous|Other) /i, "");
  return s.length > 22 ? s.split(/,| & /)[0].slice(0, 22) : s;
}
function wallDir(tw, at) {
  let best = null;
  for (const w of arr(tw.walls)) {
    const dx = w.b[0] - w.a[0], dy = w.b[1] - w.a[1], len = Math.hypot(dx, dy); if (len < 0.01) continue;
    const t = Math.max(0, Math.min(1, ((at[0] - w.a[0]) * dx + (at[1] - w.a[1]) * dy) / (len * len)));
    const d = Math.hypot(at[0] - (w.a[0] + t * dx), at[1] - (w.a[1] + t * dy));
    if (!best || d < best[0]) best = [d, [dx / len, dy / len]];
  }
  return best ? best[1] : [1, 0];
}

// ------------------------------------------------------------------ 5 crews, 6 hazards and differences
function crewsAndHazards(r) {
  const crews = safe(() => {
    const cs = arr(r.crews);
    if (!cs.length) return NONE();
    const rows = cs.map((c) => {
      const air = num(c.air_pct), l = c.last || {};
      const asg = c.assignment ? [words(c.assignment.task), c.assignment.target_label].filter(Boolean).join(": ") : "";
      return `<tr><td><span class="cs">${esc(c.id)}</span><span class="by">${esc(c.label || "")}${c.tag ? `, tag ${esc(c.tag)}` : c.physical === false ? ", virtual" : ""}</span></td>
        <td><span class="st ${c.status === "out" ? "out" : c.status === "mayday" ? "trapped" : ""}">${esc(c.status || "?")}</span></td>
        <td class="t">${num(c.time_in_s) !== null ? clock(c.time_in_s) : "–"}</td>
        <td>${air !== null ? `<div class="air"><span>${Math.round(air)} %</span><div class="bar-o"><div class="bar-i ${air < 25 ? "crit" : air < 50 ? "low" : ""}" style="width:${Math.max(0, Math.min(100, air))}%"></div></div></div>` : "–"}</td>
        <td>${esc(l.room_label || c.room_label || "unknown")}${num(l.t_rel) !== null ? ` <span class="t">T+${clock(l.t_rel)}</span>` : ""}${asg ? `<span class="by">${esc(asg)}</span>` : ""}</td></tr>`;
    }).join("");
    return `<table class="tbl"><thead><tr><th>Crew</th><th>Status</th><th>Time in</th><th>Air (estimate)</th><th>Last location, order</th></tr></thead><tbody>${rows}</tbody></table>`;
  });
  const hz = safe(() => {
    const hs = arr(r.hazards), ds = arr(r.differences || r.twin_differences);
    const h = hs.length ? `<ul class="items">${hs.map((x) => `<li><span class="k haz">${esc(x.id || "")}</span><div>${esc(words(x.kind))}: ${esc(x.label || x.ref || "")}${x.note ? `, ${esc(x.note)}` : ""}<small>${esc([x.source ? `reported by ${x.source}` : null, num(x.t_rel) !== null ? `T+${clock(x.t_rel)}` : null].filter(Boolean).join(", "))}</small></div></li>`).join("")}</ul>` : NONE("No hazards recorded.");
    const d = ds.length ? `<ul class="items">${ds.map((x) => `<li><span class="k diff">${esc(x.id || "")}</span><div>${esc(x.text || x.label || "")}<small>${esc([x.kind === "twin" ? "against the twin" : x.kind === "record" ? "against the building record" : x.kind, x.source ? `source: ${x.source}` : null, num(x.t_rel) !== null ? `T+${clock(x.t_rel)}` : null].filter(Boolean).join(", "))}</small></div></li>`).join("")}</ul>` : NONE("No differences from the twin or the record recorded.");
    return `<h3 class="sub-h">Hazards</h3>${h}<h3 class="sub-h" style="margin-top:12px">Differences from the twin and the record</h3>${d}`;
  });
  return blk(5, "Crews, hazards and differences", "Crew positions and air are as the board last knew them.", `<div class="cols"><div><h3 class="sub-h">Crews</h3>${crews}</div><div>${hz}</div></div>`);
}

// ------------------------------------------------------------------ 6 timeline
function timeline(r) {
  return safe(() => {
    const tl = arr(r.timeline);
    if (!tl.length) return blk(6, "Timeline", "", NONE());
    const li = tl.map((e) => `<li class="k-${esc(e.kind)}"><span class="t">T+${clock(e.t_rel ?? 0)}</span><span class="kind">${esc(words(e.kind))}</span><span>${esc(e.text)}</span></li>`).join("");
    return blk(6, "Timeline", `${tl.length} entries, incident clock.`, `<ol class="tl">${li}</ol>`);
  });
}

// ------------------------------------------------------------------ 7 agent
function agent(r) {
  return safe(() => {
    const a = r.agent || {};
    const ppText = typeof a.preplan_summary === "object" && a.preplan_summary ? a.preplan_summary.summary || a.preplan_summary.text : a.preplan_summary;
    const ppBk = a.preplan_backend || a.preplan_summary?.backend || a.backend || r.provenance?.agent?.backend;
    const pp = ppText ? `<div class="srcline">${srcBadge(ppBk)}</div><p>${esc(ppText)}</p>` : NONE("No pre-plan recorded.");
    const aar = a.aar;
    let ar;
    if (!aar) ar = NONE(ENDED.has(String(r.incident?.phase)) ? "No after-action report recorded." : "None yet: the agent writes it when the incident ends.");
    else if (typeof aar === "string") ar = `<div class="srcline">${srcBadge(a.aar_backend || a.backend)}</div><p>${esc(aar)}</p>`;
    else {
      const lst = (t, xs) => (arr(xs).length ? `<h4>${esc(t)}</h4><ul>${arr(xs).map((x) => `<li>${esc(typeof x === "string" ? x : x.text || x.label || JSON.stringify(x))}</li>`).join("")}</ul>` : "");
      ar = `<div class="srcline">${srcBadge(aar.backend || a.aar_backend || a.backend)}</div>${aar.summary ? `<p>${esc(aar.summary)}</p>` : ""}${lst("Went well", aar.went_well)}${lst("To improve", aar.improve)}${lst("Follow up", aar.follow_up)}${lst("Corrections filed against the record", aar.corrections)}`;
    }
    return blk(7, "Command agent", "What the agent planned before entry and wrote afterwards, with where each text came from.", `<div class="agent"><div><h3 class="sub-h">Pre-plan summary</h3>${pp}</div><div><h3 class="sub-h">After-action</h3>${ar}</div></div>`);
  });
}

// ------------------------------------------------------------------ 8 limits, 9 sign-off
function limits(r) {
  return safe(() => {
    const seen = new Set(), xs = [];
    for (const x of [...arr(r.limits), ...arr(r.accountability?.limits)]) { const t = typeof x === "string" ? x : x?.text; if (t && !seen.has(t)) { seen.add(t); xs.push(t); } }
    return blk(8, "Limits of this report", "", xs.length ? `<ul class="limits">${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : NONE());
  });
}
function signoff() {
  const fld = (l) => `<div><div class="f"></div><div class="fl">${l}</div></div>`;
  return blk(9, "Sign-off", "", `<div class="sign">${fld("Incident commander, name and unit")}${fld("Signature")}${fld("Date and time")}${fld("Received by (next commander, investigator or owner)")}${fld("Signature")}${fld("Date and time")}
    <p class="sign-note">Left blank for a person. Nothing in this report has been reviewed by a commander until this block is signed.</p></div>`, "sign-blk");
}
function foot(r) {
  const a = addrLine(r);
  return `<footer class="foot"><span>Sandtable Beacon incident report</span><span>${esc(a.full)}</span><span>Source: <code>${esc(SRC)}</code></span></footer>`;
}

// ------------------------------------------------------------------ paper: running header and footer on every page
function pageBoxes(r) {
  const css = (s) => `"${String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, " ")}"`;
  const marks = [isSample(r) ? "SAMPLE" : null, isSim(r) ? "SIMULATED" : null].filter(Boolean).join("  ·  ") || "LIVE DATA";
  const a = addrLine(r);
  $("#page-boxes").textContent = `@page { size: letter; margin: 15mm 13mm 16mm 13mm;
    @top-left { content: ${css("Sandtable Beacon incident report")}; font: 600 8pt Barlow, Arial, sans-serif; color: #333; }
    @top-right { content: ${css(marks)}; font: 700 9.5pt "Barlow Condensed", Arial, sans-serif; letter-spacing: .14em; color: #000; }
    @bottom-left { content: ${css(a.full)}; font: 400 8pt Barlow, Arial, sans-serif; color: #333; }
    @bottom-right { content: "Page " counter(page) " of " counter(pages); font: 400 8pt Barlow, Arial, sans-serif; color: #333; } }`;
}

function feeds() {
  const list = [["/api/report", "this report, JSON"], ["/api/report.md", "this report, Markdown"], ["/api/accountability", "the accountability table"], ["/api/beacons", "the latest Beacon object (phones)"],
    ["/feed/scan.geojson", "GeoJSON: crews, scan, phones"], ["/feed/cot.xml", "Cursor-on-Target events"], ["/api/state", "the board's state"]];
  $("#feeds-list").innerHTML = (SAMPLE ? [["dev/sample_report.json", "the sample shown here"]] : []).concat(list).map(([u, w]) => `<li><span><code>${esc(u)}</code><small>${esc(w)}</small></span><a href="${esc(u)}" target="_blank" rel="noopener">Open</a></li>`).join("");
}

$("#btn-print").addEventListener("click", () => window.print());
document.addEventListener("visibilitychange", () => { if (!document.hidden && !SAMPLE) tick(); });
feeds();
tick();
