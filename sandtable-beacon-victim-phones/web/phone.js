// The simulated occupant phone: the app's screen on the left, the presenter's simulation controls on the right.
// Talks to POST /api/sim/phone and GET /api/sim/phone/{key} (docs/BEACON_FEED.md section 4). One phone per browser tab.
import { $, esc, num, isNum, getJSON, postJSON } from "./common.js";

const NS = "http://www.w3.org/2000/svg";
const el = (name, attrs = {}, text) => { const e = document.createElementNS(NS, name); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text !== undefined) e.textContent = text; return e; };
const store = {
  get(k) { try { return JSON.parse(sessionStorage.getItem(k)); } catch { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode: fine */ } },
};
let key = store.get("beacon.phone.key");
if (!key) { key = "tab-" + Array.from(crypto.getRandomValues(new Uint8Array(4)), (b) => b.toString(16).padStart(2, "0")).join(""); store.set("beacon.phone.key", key); }
const S = Object.assign({ status: null, people: 1, can_move: null, text: "", floor: null, suite: "", opt_in: true, uwb: true, chip: "U1", pointing: false, camera_assist: false, battery: 80, x: null, y: null },
  store.get("beacon.phone.state") || {});
let bundle = null, beacons = null, view = null, simOk = true, lastAck = null, joined = false;

// ---------------------------------------------------------------- sending
let sendTimer = null, sending = false, again = false;
function save() { store.set("beacon.phone.state", S); }
function queueSend(delay = 120) { save(); clearTimeout(sendTimer); sendTimer = setTimeout(send, delay); }
function report() {
  const out = S.status === "out";
  return {
    status: out ? "ok" : S.status || "unknown", safe_outside: out, people: S.people, can_move: S.can_move,
    text: S.text.slice(0, 120), battery_pct: S.battery, floor_reported: isNum(S.floor) ? S.floor : null, home_suite: S.suite || null,
    model: S.uwb ? (S.chip === "U2" ? "iPhone 15" : "iPhone 14") : null, chip: S.uwb ? S.chip : null, platform: "sim", app: "0.1",
  };
}
async function send() {
  if (sending) { again = true; return; }
  if (!isNum(S.x)) return;
  sending = true;
  try {
    const r = report();
    await postJSON("/api/sim/phone", { key, x: +S.x.toFixed(2), y: +S.y.toFixed(2), floor: view ? view.floorId : undefined, opt_in: S.opt_in, uwb: S.uwb, pointing: S.pointing, camera_assist: S.uwb && S.pointing && S.camera_assist, chip: r.chip, model: r.model, report: r });
    simOk = true; joined = true;
    setState(S.opt_in ? "Sent to the simulator." : "Sent: a cold phone (no app), counted as a device only.");
  } catch (e) {
    simOk = e.status !== 409;
    setState(e.status === 409 ? "The server is not running the simulator (it is playing a sample or real hardware), so this phone cannot join. Start the demo with the simulator." : `Could not reach the simulator: ${e.message}`, true);
  } finally {
    sending = false;
    if (again) { again = false; send(); }
  }
}
function setState(text, err) { const s = $("sim-state"); s.textContent = text; s.classList.toggle("err", !!err); }

// ---------------------------------------------------------------- what the app shows back
function occupantGeometry(ph, ack) {
  const g = (ack && ack.geometry) || (ph && ph.geometry);
  const sig = (ack && isNum(ack.sigma_m)) ? ack.sigma_m : ph && ph.sigma_m;
  if (S.status === "out") return "You are marked safe outside. Rescuers will not search for you.";
  if (g === "fix") return `They know where you are to within ${num(sig)} m.`;
  if (g === "candidates") return `They have narrowed it down to ${(ph && ph.candidates || []).length || "a few"} spots, not one yet.`;
  if (g === "ring") return "They know how far you are from one beacon, not yet in which direction.";
  if (g === "zone") return "They know roughly which room you are in.";
  if (g === "none") return "They hear your SOS. No position yet.";
  return "";
}
function renderAck(res, err) {
  const box = $("ack"), main = $("ack-main"), sub = $("ack-sub");
  $("beacon-dot").classList.toggle("on", !!(S.opt_in && joined && simOk));
  if (!simOk) { box.className = "ack none"; main.textContent = "Not connected to the simulator"; sub.textContent = "This page needs the Beacon server running its simulator."; return; }
  if (!S.opt_in) { box.className = "ack cold"; main.textContent = "No app: no SOS is sent"; sub.textContent = "Rescue beacons can only count this phone as one more Bluetooth device. They cannot see your status or find you."; return; }
  if (err === 404 || !res) { box.className = "ack none"; main.textContent = joined ? "This phone left the simulation" : "Starting your SOS beacon…"; sub.textContent = joined ? "The board was reset. Tap anything to send your SOS again." : ""; if (joined && err === 404) joined = false; $("beacon-dot").classList.remove("on"); return; }
  const ack = res.ack || {}, ph = res.phone, n = (ack.heard_by || []).length;
  if (!n && !ph) { box.className = "ack none"; main.textContent = "No rescue beacon hears you yet"; sub.textContent = "Keep the app open. Beacons are thrown into the building as crews arrive."; return; }
  box.className = "ack";
  main.textContent = ack.text || `${n} rescue beacon${n === 1 ? "" : "s"} hear${n === 1 ? "s" : ""} you`;
  // the server's ack already carries the numbers; add only what the occupant can do, or the geometry when the ack has no text
  const g = (ack.geometry || (ph && ph.geometry));
  sub.textContent = !ack.text ? occupantGeometry(ph, ack)
    : S.status === "out" ? "You are marked safe outside. Rescuers will not search for you."
    : (g === "ring" || g === "candidates") && S.uwb && !S.pointing ? "Hold the phone up and point it at the door: it helps them find you."
    : "Keep the app open so they keep hearing you.";
  lastAck = res;
}
async function pollAck() {
  if (!isNum(S.x)) return;
  try { const r = await getJSON(`/api/sim/phone/${encodeURIComponent(key)}`); simOk = true; renderAck(r); }
  catch (e) { if (e.status === 409) simOk = false; renderAck(null, e.status); }
}

// ---------------------------------------------------------------- the app's controls
function renderApp() {
  for (const b of document.querySelectorAll(".st")) b.setAttribute("aria-checked", String(b.dataset.st === S.status));
  $("ppl").textContent = S.people;
  for (const b of document.querySelectorAll("[data-move]")) b.setAttribute("aria-checked", String((b.dataset.move === "yes" && S.can_move === true) || (b.dataset.move === "no" && S.can_move === false)));
  if (document.activeElement !== $("note")) $("note").value = S.text;
  $("note-n").textContent = `${S.text.length} / 120`;
  $("fl").textContent = isNum(S.floor) ? S.floor : "?";
  $("suite").value = S.suite || "";
  // simulation controls
  $("opt-in").checked = S.opt_in; $("uwb").checked = S.uwb; $("pointing").checked = S.pointing; $("camera").checked = S.camera_assist;
  for (const r of document.querySelectorAll('input[name="model"]')) r.checked = r.value === S.chip;
  $("models").classList.toggle("off", !S.uwb);
  $("cam-row").classList.toggle("off", !(S.uwb && S.pointing));
  $("bat").value = S.battery; $("bat-v").textContent = `${S.battery}%`;
  drawMe();
}
function wire() {
  for (const b of document.querySelectorAll(".st")) b.onclick = () => { S.status = b.dataset.st; if (S.status === "out") S.can_move = true; renderApp(); queueSend(0); };
  $("ppl-minus").onclick = () => { S.people = Math.max(1, S.people - 1); renderApp(); queueSend(); };
  $("ppl-plus").onclick = () => { S.people = Math.min(50, S.people + 1); renderApp(); queueSend(); };
  for (const b of document.querySelectorAll("[data-move]")) b.onclick = () => { S.can_move = b.dataset.move === "yes"; renderApp(); queueSend(0); };
  $("note").oninput = (e) => { S.text = e.target.value.slice(0, 120); $("note-n").textContent = `${S.text.length} / 120`; queueSend(500); };
  for (const c of $("chips").querySelectorAll("button")) c.onclick = () => { S.text = (S.text ? S.text.replace(/[.\s]*$/, "") + ". " : "") + c.textContent; S.text = S.text.slice(0, 120); renderApp(); queueSend(); };
  $("fl-minus").onclick = () => { S.floor = Math.max(0, (isNum(S.floor) ? S.floor : 1) - 1); renderApp(); queueSend(); };
  $("fl-plus").onclick = () => { S.floor = Math.min(99, (isNum(S.floor) ? S.floor : 0) + 1); renderApp(); queueSend(); };
  $("suite").onchange = (e) => { S.suite = e.target.value; queueSend(0); };
  $("opt-in").onchange = (e) => { S.opt_in = e.target.checked; renderApp(); queueSend(0); };
  $("uwb").onchange = (e) => { S.uwb = e.target.checked; renderApp(); queueSend(0); };
  for (const r of document.querySelectorAll('input[name="model"]')) r.onchange = () => { S.chip = r.value; renderApp(); queueSend(0); };
  $("pointing").onchange = (e) => { S.pointing = e.target.checked; renderApp(); queueSend(0); };
  $("camera").onchange = (e) => { S.camera_assist = e.target.checked; queueSend(0); };
  $("bat").oninput = (e) => { S.battery = Number(e.target.value); $("bat-v").textContent = `${S.battery}%`; queueSend(300); };
  $("btn-remove").onclick = async () => {
    try { await postJSON("/api/sim/phone", { key, remove: true }); joined = false; setState("Removed from the simulation. Tap anything in the app to send the SOS again."); renderAck(null, 404); }
    catch (e) { setState(`Could not remove: ${e.message}`, true); }
  };
}

// ---------------------------------------------------------------- the presenter's plan
function buildView() {
  const t = bundle.twin;
  const floorId = (beacons && beacons.floor) || (bundle.config && bundle.config.floor) || t.exercise_floor;
  const floor = t.floors.find((f) => f.id === floorId) || t.floors[0];
  const roomId = (beacons && beacons.room) || (bundle.config && bundle.config.room) || null;
  const rooms = t.rooms.filter((r) => r.floor === floor.id);
  const room = rooms.find((r) => r.id === roomId) || null;
  const xs = floor.outline.map((p) => p[0]), ys = floor.outline.map((p) => p[1]);
  const fl = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  let bb = fl;
  if (room) {
    const rx = room.polygon.map((p) => p[0]), ry = room.polygon.map((p) => p[1]), m = 5;
    bb = [Math.max(fl[0] - 0.6, Math.min(...rx) - m), Math.max(fl[1] - 0.6, Math.min(...ry) - m), Math.min(fl[2] + 0.6, Math.max(...rx) + m), Math.min(fl[3] + 0.6, Math.max(...ry) + m)];
  }
  const key2 = `${floor.id}|${room && room.id}`;
  if (view && view.key === key2) return;
  view = { key: key2, floorId: floor.id, floor, room, rooms, fy: fl[3], fl, bb };
  if (!isNum(S.floor)) S.floor = Number(floor.label);
  if (!isNum(S.x)) {
    // a starting spot inside the room, a little different for every tab
    const c = room ? [room.polygon.reduce((s, p) => s + p[0], 0) / room.polygon.length, room.polygon.reduce((s, p) => s + p[1], 0) / room.polygon.length] : [(fl[0] + fl[2]) / 2, (fl[1] + fl[3]) / 2];
    const h = parseInt(key.slice(-4), 16) || 0;
    S.x = c[0] - 2 + (h % 9) * 0.4; S.y = c[1] - 2 + ((h >> 4) % 9) * 0.4;
  }
  drawPlan();
}
const P = (p) => [p[0], view.fy - p[1]];
function drawPlan() {
  const svg = $("mplan"), v = view;
  svg.replaceChildren();
  svg.setAttribute("viewBox", [v.bb[0], v.fy - v.bb[3], v.bb[2] - v.bb[0], v.bb[3] - v.bb[1]].join(" "));
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  const t = bundle.twin;
  for (const r of v.rooms) {
    svg.appendChild(el("polygon", { points: r.polygon.map((q) => P(q).join(",")).join(" "), class: `r ${r.kind}${v.room && r.id === v.room.id ? " focus" : ""}` }));
  }
  for (const w of t.walls.filter((q) => q.floor === v.floor.id)) { const a = P(w.a), b = P(w.b); svg.appendChild(el("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: "wall" })); }
  for (const d of t.doors.filter((q) => q.floor === v.floor.id)) {
    const p = P(d.at), w = (d.width_m || 0.9) / 2, vert = t.walls.some((q) => q.floor === v.floor.id && Math.abs(q.a[0] - q.b[0]) < 0.01 && Math.abs(q.a[0] - d.at[0]) < 0.05 && d.at[1] >= Math.min(q.a[1], q.b[1]) && d.at[1] <= Math.max(q.a[1], q.b[1]));
    svg.appendChild(el("line", vert ? { x1: p[0], y1: p[1] - w, x2: p[0], y2: p[1] + w, class: "door" } : { x1: p[0] - w, y1: p[1], x2: p[0] + w, y2: p[1], class: "door" }));
  }
  for (const r of v.rooms) if (r.kind === "suite") { const c = [r.polygon.reduce((s, p) => s + p[0], 0) / r.polygon.length, r.polygon.reduce((s, p) => s + p[1], 0) / r.polygon.length]; svg.appendChild(el("text", { x: c[0], y: v.fy - c[1], class: "rl" }, r.suite)); }
  if (v.room) svg.appendChild(el("polygon", { points: v.room.polygon.map((q) => P(q).join(",")).join(" "), class: "focus-outline" }));
  v.pucksG = el("g"); svg.appendChild(v.pucksG);
  v.me = el("g", { class: "me" }); v.meH = el("circle", { class: "h" }); v.meB = el("circle", { class: "b" }); v.meT = el("text", {}, "this phone");
  v.me.append(v.meH, v.meB, v.meT); svg.appendChild(v.me);
  sizeUnits(); drawPucks(); drawMe();
}
function sizeUnits() {
  const svg = $("mplan"), r = svg.getBoundingClientRect();
  if (!view || !r.width) return;
  view.u = Math.max((view.bb[2] - view.bb[0]) / r.width, (view.bb[3] - view.bb[1]) / r.height);
  svg.style.setProperty("--u", view.u.toFixed(5));
}
function drawPucks() {
  if (!view || !view.pucksG) return;
  view.pucksG.replaceChildren();
  const u = view.u || 0.05;
  for (const p of (beacons && beacons.pucks) || []) {
    if (!isNum(p.x) || (p.floor && p.floor !== view.floor.id)) continue;
    const q = P([p.x, p.y]), g = el("g", { class: "mpuck" });
    g.append(el("circle", { cx: q[0], cy: q[1], r: 5 * u }), el("text", { x: q[0], y: q[1] - 9 * u }, p.id));
    view.pucksG.appendChild(g);
  }
}
function drawMe() {
  if (!view || !view.me || !isNum(S.x)) return;
  const u = view.u || 0.05, q = P([S.x, S.y]);
  view.me.classList.toggle("cold", !S.opt_in);
  view.meH.setAttribute("cx", q[0]); view.meH.setAttribute("cy", q[1]); view.meH.setAttribute("r", 16 * u);
  view.meB.setAttribute("cx", q[0]); view.meB.setAttribute("cy", q[1]); view.meB.setAttribute("r", 8 * u);
  view.meT.setAttribute("x", q[0]); view.meT.setAttribute("y", q[1] - 20 * u);
}
function wirePlan() {
  const svg = $("mplan");
  let drag = false, lastSend = 0;
  const toB = (ev) => { const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY; const p = pt.matrixTransform(svg.getScreenCTM().inverse()); return [p.x, view.fy - p.y]; };
  const moveTo = (ev, final) => {
    const p = toB(ev), f = view.fl;
    S.x = Math.max(f[0] + 0.2, Math.min(f[2] - 0.2, p[0])); S.y = Math.max(f[1] + 0.2, Math.min(f[3] - 0.2, p[1]));
    drawMe();
    const now = performance.now();
    if (final || now - lastSend > 250) { lastSend = now; queueSend(0); }
  };
  svg.addEventListener("pointerdown", (ev) => { if (!view) return; drag = true; try { svg.setPointerCapture(ev.pointerId); } catch { /* ignore */ } moveTo(ev); ev.preventDefault(); });
  svg.addEventListener("pointermove", (ev) => { if (drag) moveTo(ev); });
  svg.addEventListener("pointerup", (ev) => { if (drag) { drag = false; moveTo(ev, true); } });
  svg.addEventListener("pointercancel", () => { drag = false; });
  new ResizeObserver(() => { sizeUnits(); drawPucks(); drawMe(); }).observe(svg);
}

// ---------------------------------------------------------------- start
async function loadDirectory() {
  const sel = $("suite"), note = $("suite-note");
  try {
    const dir = await getJSON("/api/directory");
    if (!Array.isArray(dir) || !dir.length) { note.textContent = "The building has not shared a suite list."; return; }
    for (const d of dir) sel.add(new Option(d.label || `Suite ${d.suite}`, d.suite));
    note.textContent = "From the building's tenant list.";
  } catch { note.textContent = "The building has not shared a suite list."; }
  sel.value = S.suite || "";
}
async function refreshBeacons() {
  try { beacons = await getJSON("/api/beacons"); buildView(); drawPucks(); } catch { /* keep the last one */ }
}
(async () => {
  $("sim-key").textContent = `· ${key}`;
  wire(); wirePlan();
  try { bundle = await getJSON("/api/bundle"); }
  catch { $("app-sub").textContent = "The Beacon server is not reachable."; setState("No server: open this page from the Beacon server (python -m beacon.demo).", true); return; }
  const p = (bundle.site && bundle.site.property) || {};
  $("app-sub").textContent = p.address ? `${p.address}${p.city ? ", " + p.city : ""}` : "Building on record";
  await Promise.all([loadDirectory(), refreshBeacons()]);
  try { const r = await getJSON(`/api/sim/phone/${encodeURIComponent(key)}`); if (r.truth && isNum(r.truth.x)) { S.x = r.truth.x; S.y = r.truth.y; } joined = true; } catch { /* new phone */ }
  if (!view) buildView();
  renderApp();
  await send();
  pollAck();
  setInterval(pollAck, 1000);
  setInterval(refreshBeacons, 2500);
})();
