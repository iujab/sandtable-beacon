// Shared helpers for the Beacon board and the simulated phone: plain words for the feed's codes, fetch helpers.
// Every number shown comes from the feed; the words here only name what the codes mean (docs/BEACON_FEED.md).

export const $ = (id) => document.getElementById(id);
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const num = (v, d = 1) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "?" : Number(v).toFixed(d));
export const pct = (v) => (v === null || v === undefined ? "?" : `${Math.round(Number(v) * 100)}%`);
export const isNum = (v) => typeof v === "number" && Number.isFinite(v);

export async function getJSON(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) { const e = new Error(`${path}: HTTP ${r.status}`); e.status = r.status; throw e; }
  return r.json();
}
export async function postJSON(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
  let data = null;
  try { data = await r.json(); } catch { /* empty body */ }
  if (!r.ok) {
    const detail = data && (data.detail || data.error || data.text);
    const e = new Error(typeof detail === "string" ? detail : `${path}: HTTP ${r.status}`); e.status = r.status; throw e;
  }
  return data;
}

export const STATUS = {
  trapped: { word: "Trapped", color: "#ff3b30" },
  injured: { word: "Injured", color: "#ff9f0a" },
  sheltering: { word: "Sheltering", color: "#ffc247" },
  ok: { word: "OK", color: "#3fcf8e" },
  unknown: { word: "No status", color: "#8d99a6" },
};
export function statusOf(report) {
  const r = report || {};
  if (r.safe_outside) return { word: "Safe outside", color: "#3fcf8e", key: "out" };
  const s = STATUS[r.status] || STATUS.unknown;
  return { ...s, key: r.status || "unknown" };
}
export function peopleWords(report) {
  const r = report || {};
  const n = isNum(r.people) ? r.people : null;
  const who = n === null ? "people not said" : `${n} ${n === 1 ? "person" : "people"}`;
  const mv = r.can_move === true ? "can move" : r.can_move === false ? "cannot move" : null;
  return mv ? `${who}, ${mv}` : who;
}

export const METHOD = {
  "uwb-twr": "UWB ranging",
  "uwb-twr+bearing": "UWB ranging + the phone's direction",
  "ble-rssi": "Bluetooth signal strength",
  "ble-presence": "Bluetooth, heard only",
};
export const methodWords = (m) => METHOD[m] || m || "unknown method";

// What the published geometry says, in words (never more than the geometry supports).
export function geometryWords(p) {
  if (!p) return "";
  const g = p.geometry;
  if (p.report && p.report.safe_outside) return "Pressed “I am out”: accounted for, not searched for";
  if (g === "fix") return `Located${p.room_label ? ` in ${p.room_label}` : ""} to ±${num(p.sigma_m)} m`;
  if (g === "candidates") {
    const c = p.candidates || [];
    const rooms = [...new Set(c.map((q) => q.room_label).filter(Boolean))];
    return `${c.length} possible spots${rooms.length === 1 ? ` in ${rooms[0]}` : rooms.length ? ` (${rooms.join(", ")})` : ""}: not one point yet`;
  }
  if (g === "ring" && p.ring) {
    const r = p.ring;
    return r.kind === "ble"
      ? `Bluetooth only: about ${num(r.r_m)} m from ${r.puck}, give or take ${num(r.sigma_m)} m`
      : `Range only: ${num(r.r_m)} m from ${r.puck}, direction unknown`;
  }
  if (g === "zone" && p.zone) return `Probably in ${p.zone.room_label || "this room"} (${p.zone.band} signal band, ${pct(p.zone.prob)})`;
  if (g === "none") return "Heard, no position";
  return g || "";
}
export const GEOMETRY_SHORT = { fix: "fix", candidates: "candidates", ring: "ring", zone: "zone", none: "heard" };

// The phone hardware line (v1 additions A5): model, UWB chip and what the chip can give.
export function chipWords(report) {
  const r = report || {};
  // iPhone 14 and later report no direction to an accessory without camera assistance, whatever the chip (docs/research/ios_ni.md).
  const needsCamera = r.chip === "U2" || /iPhone (1[4-9]|Air)/.test(r.model || "");
  const chipText = r.chip ? `${r.chip}: ${needsCamera ? "distance; direction only with the camera" : "distance and direction"}` : undefined;
  const parts = [];
  if (r.model) parts.push(r.model);
  if (chipText) parts.push(chipText);
  else if (r.chip === null && r.model) parts.push("no UWB chip");
  return parts.join(" · ");
}
export function bearingWords(p) {
  const b = p && p.bearing;
  if (!b) return "no direction now";
  return `direction from ${b.puck} now, ±${Math.round(b.sigma_deg)}°`;
}
export function floorWords(p) {
  const r = (p && p.report) || {};
  const src = { barometer: "barometer", "self-report": "typed in the app", puck: "assumed: the pucks' floor" }[p && p.floor_source] || p?.floor_source;
  const f = isNum(r.floor_reported) ? `floor ${r.floor_reported}` : p && p.floor ? `floor ${String(p.floor).replace(/^f/, "")}` : "floor unknown";
  return src ? `${f} (${src})` : f;
}
export function stillWords(report) {
  const r = report || {};
  if (r.still === true) return isNum(r.still_s) ? `still for ${Math.round(r.still_s)} s` : "still";
  if (r.still === false) return "moving";
  return null;
}
export const clockOf = (t) => {
  if (!isNum(t)) return "--:--:--";
  const d = new Date(t * 1000);
  return d.toTimeString().slice(0, 8);
};
// The noise model in one line, from the hello message (numbers from the server, not from this file).
export function noiseLine(noise) {
  if (!noise) return "";
  const u = noise.uwb || {}, b = noise.ble || {}, parts = [];
  if (isNum(u.sigma_los_m)) parts.push(`UWB σ ${u.sigma_los_m} m in line of sight${isNum(u.wall_bias_m) ? `, +${u.wall_bias_m} m bias per wall` : ""}`);
  if (isNum(b.shadow_sigma_db)) parts.push(`Bluetooth shadowing σ ${b.shadow_sigma_db} dB${isNum(b.wall_loss_db) ? `, ${b.wall_loss_db} dB per wall` : ""}`);
  return parts.join("; ");
}
