// Shared words, stamps and colours for the command board. Colours live in style.css (custom properties on :root,
// one set per theme); the plan and the 3D twin read them from there through palette(), so the CSS is the one source.

export const $ = (id) => document.getElementById(id);
export const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const mmss = (s) => { s = Math.max(0, Math.round(s || 0)); return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };
export const ago = (s) => (s == null ? "" : s < 1.5 ? "just now" : s < 90 ? `${Math.round(s)} s ago` : `${Math.round(s / 60)} min ago`);
export const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
export const fmt = (v) => (typeof v === "number" ? v.toLocaleString("en-US") : v);
export const REDUCED = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
export const range = (lo, hi) => (lo == null && hi == null ? "?" : lo === hi ? `${lo}` : `${lo ?? "?"} to ${hi ?? "?"}`);

// ---------------------------------------------------------------- source stamps: every layer says where it comes from
// LIVE: sponsor data (private). STAND-IN: open data and a synthetic twin. SIM: simulated phones, pucks, crews, radar.
// REPLAY: recorded agent turns. MEASURED: real boards. SAMPLE: hand-made development data. MODEL: answers from a live model.
export const STAMP = {
  live: ["LIVE", "Live sponsor data: private, do not publish"],
  standin: ["STAND-IN", "Stand-in: public open data and a synthetic twin, not sponsor data"],
  sim: ["SIM", "Simulated: no hardware in the loop"],
  replay: ["REPLAY", "Recorded agent turns, replayed"],
  measured: ["MEASURED", "Measured by real boards"],
  sample: ["SAMPLE", "Hand-made sample for development, not a result"],
  model: ["MODEL", "Answers from a live model"],
  off: ["OFF", "Not connected"],
};
export function stamp(kind, title) {
  const s = STAMP[kind] || [String(kind).toUpperCase(), ""];
  return `<span class="stamp st-${esc(kind)}" title="${esc(title || s[1])}">${s[0]}</span>`;
}
// which stamp a sponsor layer gets
export const layerKind = (labels, k) => (labels?.[k]?.mode === "live" ? "live" : "standin");
export function agentKind(backend) { return backend === "replay" ? "replay" : backend === "anthropic" || backend === "claude-cli" ? "model" : backend === "reflex-fallback" ? "sim" : "replay"; }

// ---------------------------------------------------------------- people: status as a word and a shape, never colour alone
// shape: tri (trapped), plus (injured), dot (can move, sheltering, ok), out (accounted for outside), q (no status)
export function personStatus(r = {}) {
  if (r.safe_outside) return { key: "out", word: "Out", shape: "out" };
  if (r.status === "trapped") return { key: "trapped", word: "Trapped", shape: "tri" };
  if (r.status === "injured") return { key: "injured", word: "Injured", shape: "plus" };
  if (r.can_move === true) return { key: "mobile", word: "Can move", shape: "dot" };
  if (r.status === "sheltering") return { key: "mobile", word: "Sheltering", shape: "dot" };
  if (r.status === "ok") return { key: "mobile", word: "OK", shape: "dot" };
  if (r.can_move === false) return { key: "unknown", word: "Cannot move", shape: "q" };
  return { key: "unknown", word: "No status", shape: "q" };
}
export const STATUS_RANK = { trapped: 0, injured: 1, unknown: 2, mobile: 3, out: 4 };
// a 12 px inline icon for the shape (HTML)
export function shapeIcon(shape) {
  const p = {
    tri: `<path d="M6 1.2 11 10.6H1Z"/>`,
    plus: `<path d="M4.4 1h3.2v3.4H11v3.2H7.6V11H4.4V7.6H1V4.4h3.4Z"/>`,
    dot: `<circle cx="6" cy="6" r="4.4"/>`,
    out: `<path d="M1.5 6.4 4.6 9.4 10.6 2.6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>`,
    q: `<circle cx="6" cy="6" r="4.3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.2 1.6"/>`,
  }[shape] || "";
  return `<svg class="ico" viewBox="0 0 12 12" aria-hidden="true">${p}</svg>`;
}
export function statusChip(r, extra = "") {
  const s = personStatus(r);
  return `<span class="chip ps-${s.key}${extra ? " " + extra : ""}">${shapeIcon(s.shape)}${esc(s.word)}</span>`;
}
// the same shapes as SVG paths centred on 0,0 with radius ~1 (for the plan, in building metres)
export const SHAPE_PATH = {
  tri: "M0 -1 L0.95 0.72 L-0.95 0.72 Z",
  plus: "M-0.3 -0.95 H0.3 V-0.3 H0.95 V0.3 H0.3 V0.95 H-0.3 V0.3 H-0.95 V-0.3 H-0.3 Z",
  dot: "M0.8 0 A0.8 0.8 0 1 1 -0.8 0 A0.8 0.8 0 1 1 0.8 0 Z",
  out: "M-0.8 0.05 L-0.25 0.6 L0.85 -0.6",
  q: "M0.8 0 A0.8 0.8 0 1 1 -0.8 0 A0.8 0.8 0 1 1 0.8 0 Z",
};

// a phone's geometry, exactly as measured, in words
export function geomShort(p) {
  switch (p.geometry) {
    case "fix": return `Located within ${(p.sigma_m ?? 0).toFixed(1)} m`;
    case "candidates": return `${p.candidates?.length || 2} possible places: cannot choose yet`;
    case "ring": return `Ring ${p.ring.r_m.toFixed(1)} m from ${p.ring.puck}: a distance, not a place`;
    case "zone": return `Somewhere in ${p.zone?.room_label || "the room"}`;
    default: return p.report?.safe_outside ? "Says they are out" : "Heard, no position";
  }
}
export function peopleWords(r = {}) {
  const bits = [];
  if (r.people != null) bits.push(plural(r.people, "person", "people"));
  if (r.can_move === false) bits.push("cannot move"); else if (r.can_move === true) bits.push("can move");
  return bits.join(", ");
}

// ---------------------------------------------------------------- pucks: how each position came to be
// view: the Beacon model's view (its `origins` map remembers what this board sent); absent: judge from the puck alone
export function puckOrigin(pk, view) {
  if (view?.origins?.[pk.id]) return view.origins[pk.id];
  if (pk.placed_by) return pk.placed_by;
  if (pk.source === "hand" || pk.placed === "by hand" || view?.hand?.has?.(pk.id)) return "hand";
  if (pk.simulated) return "sim";
  return null;
}
export const ORIGIN_WORD = { hand: "placed by hand", baseline: "spaced by the measured baseline", layout: "from the rig layout", sim: "simulated position" };
// plan metres per measured metre: the exercise scale applies to real (live-source) pucks only, as in the Beacon module
export const rangeScale = (b) => (b?.source === "live" && b?.rig?.scale > 0 ? b.rig.scale : 1);
// the Beacon module's warnings (for example a placed spacing that disagrees with the measured one), in plain words
export function warningWords(w) {
  if (w?.kind === "baseline-mismatch" && Number.isFinite(w.placed_m) && Number.isFinite(w.measured_m))
    return `${(w.pair || []).join(" and ")} are ${w.placed_m.toFixed(2)} m apart on the plan${w.scale && w.scale !== 1 ? " (exercise scale)" : ""}, but the pucks measured ${w.measured_m.toFixed(2)} m (allowed difference ${(w.tolerance_m ?? 0).toFixed(2)} m). One of them is misplaced: move the pucks.`;
  return w?.text || String(w || "");
}
// the measured spacing for a pair of pucks, from the Beacons object's optional `baselines` (absent: null)
export function baselineFor(b, a, c) {
  for (const q of b?.baselines || []) {
    const pr = q.pair || [];
    if ((pr[0] === a && pr[1] === c) || (pr[0] === c && pr[1] === a)) if (Number.isFinite(q.d_m)) return q;
  }
  return null;
}

// ---------------------------------------------------------------- colours for SVG and WebGL, read from the CSS
const PAL_KEYS = ["ground", "paper", "paper-2", "ink", "ink-2", "ink-3", "rule", "rule-2", "danger", "caution", "person", "person-soft", "puck", "puck-ink",
  "e1", "rit", "mobile", "flag", "halo", "m-suite", "m-corridor", "m-stair", "m-lobby", "m-service", "m-wall", "m-wall3d", "m-slab", "m-ground", "m-label", "m-label-hz", "m-hot", "m-lifehaz", "on-danger", "on-caution", "on-person"];
export function palette() {
  const cs = getComputedStyle(document.documentElement), out = {};
  for (const k of PAL_KEYS) out[k.replace(/-([a-z0-9])/g, (_, c) => c.toUpperCase())] = cs.getPropertyValue("--" + k).trim() || "#888888";
  out.theme = document.documentElement.dataset.theme || "day";
  return out;
}
export const UNIT_IDS = ["E1", "RIT"];
export function unitColors(pal) { return { E1: pal.e1, RIT: pal.rit }; }

// ---------------------------------------------------------------- theme
export function initialTheme() {
  try { const t = localStorage.getItem("sandtable-theme"); if (t === "day" || t === "night") return t; } catch { /* storage blocked */ }
  return "day";
}
export function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("sandtable-theme", t); } catch { /* storage blocked */ }
}

// keep keyboard focus and scroll when a container's HTML is replaced (the board re-renders at the state rate)
// (and which <details data-k> the reader opened or closed)
export function setHTML(el, html) {
  if (!el || el._html === html) return false;
  const active = document.activeElement, key = active && el.contains(active) ? active.dataset.k : null, top = el.scrollTop;
  const det = new Map([...el.querySelectorAll("details[data-k]")].map((d) => [d.dataset.k, d.open]));
  el.innerHTML = html; el._html = html;
  for (const d of el.querySelectorAll("details[data-k]")) if (det.has(d.dataset.k)) d.open = det.get(d.dataset.k);
  if (key) el.querySelector(`[data-k="${CSS.escape(key)}"]`)?.focus({ preventScroll: true });
  el.scrollTop = top;
  return true;
}
