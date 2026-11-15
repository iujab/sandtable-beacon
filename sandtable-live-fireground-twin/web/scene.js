// Step 2 of the story, "Beacon on scene": where the beacon landed, every phone that answered (geometry exactly as measured),
// devices heard, and the record against the phones per floor. Scout and Crews modes put their own content here.
import { esc, plural, ago, stamp, statusChip, personStatus, STATUS_RANK, geomShort, peopleWords, puckOrigin, ORIGIN_WORD, baselineFor, range, rangeScale, warningWords } from "./common.js";
import { mirrorCaption, STATE_WORD } from "./beacon.js";
import { PHASES, PHASE_WORD, RUNG_WORD, victimState, regQuality, meshSvg } from "./scout.js";
import { planFloorNo } from "./know.js";

const OPTIN = `<p class="optin"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm.9 10.6H7.1V7h1.8v4.6ZM8 5.9a1 1 0 1 1 0-2 1 1 0 0 1 0 2Z"/></svg><span>Only phones running the opt-in app are found and placed. Phones without the app are not detected; at most the pucks count advertising devices, which are not people.</span></p>`;

export function beaconSource(beacon) {
  const b = beacon.b, link = beacon.link || {};
  if (beacon.dev) return [stamp("sample", "Hand-made sample story on the board (?devbeacons=1)"), "Hand-made sample, not the Beacon module"];
  if (!b) return ["", ""];
  if (b.sample) return [stamp("sample", "The server relays a hand-made sample Beacons object"), "Sample phones, not a result"];
  if (link.source === "replay" || b.replay) return [stamp("sim", "Replay of a recorded run of the simulated Beacon module"), "Replay of a recorded simulated run"];
  return b.simulated !== false ? [stamp("sim", `Simulated phones and pucks (${b.source || "sim"})`), "Simulated phones and pucks"] : [stamp("measured", "Real pucks and phones"), "Real pucks"];
}

// people inside first, by urgency; then those out
export function sortedPhones(beacon) {
  return beacon.phones().slice().sort((a, b) => (STATUS_RANK[personStatus(a.report).key] ?? 2) - (STATUS_RANK[personStatus(b.report).key] ?? 2) || a.id.localeCompare(b.id));
}

export function sceneHTML(k) {
  if (k.mode === "scout") return scoutHTML(k);
  if (k.mode === "crews") return crewsHTML(k);
  return beaconHTML(k);
}

// ---------------------------------------------------------------- beacon
function beaconHTML(k) {
  const { beacon, bundle, active } = k, b = beacon.b, out = [];
  const [src, srcWords] = beaconSource(beacon);
  if (!beacon.connected && !b && !beacon.link?.sample) {
    out.push(`<section class="sec"><div class="callout"><h3>Beacon module not connected</h3><p class="lead">No phone data reaches this board. The record, the twin, the aerial and the crews still work.</p><p class="note">Start the Beacon server, or run the board with SANDTABLE_BEACON=sample for the labelled sample.</p></div></section>`);
    out.push(`<section class="sec">${accHTML(k)}</section>`);
    return out.join("") + OPTIN;
  }
  if (b && beacon.foreign) {
    out.push(`<section class="sec"><div class="callout"><h3>The Beacon module is set up for another building</h3><p class="lead">Its phones and suites (floor ${esc(b.floor)}) are not in this twin, so none are drawn here. Restart the Beacon module with this building's bundle.</p></div></section>`);
    return out.join("");
  }
  // ---- where did the beacon land?
  const pucks = b?.pucks || [];
  if (!pucks.length) {
    out.push(`<section class="sec"><div class="callout"><h3>Where did the beacon land?</h3><ol><li>Pick the floor</li><li>Tap where P1 landed</li><li>Tap toward P2</li></ol>` +
      `<div class="actions"><button class="btn primary" data-act="place">Place the pucks</button>${active && (beacon.connected || beacon.link?.sample || beacon.dev) ? `<button class="btn" data-act="throw">Throw pucks (simulated)</button>` : ""}</div>` +
      `${active ? "" : `<p class="note">Dispatch the alarm first: the board sends the pucks to the Beacon module only during an incident.</p>`}</div></section>`);
  } else {
    const floorNo = bundle.twin.floors.find((f) => f.id === (b.floor || bundle.twin.exercise_floor))?.label ?? planFloorNo(bundle);
    const hand = beacon.view();
    // A puck whose UWB distance dropped while Bluetooth stayed up leaves the phone with one ring: say so, and offer to restart
    // that puck's board (POST /api/beacon/puck_restart), which makes the phone redo its UWB handshake in about 10 s.
    const liveLink = b.simulated === false && !b.replay && !b.sample && !!b.link?.connected;
    const kinds = (id) => { const k = new Set(); for (const ph of b.phones || []) for (const r of ph.ranges || []) if (r.puck === id) k.add(r.kind); return k; };
    const uwbLost = (id) => { const k = kinds(id); return k.size > 0 && !k.has("uwb"); };
    const uwbNote = (id) => { const k = kinds(id); return !k.size ? "" : k.has("uwb") ? `, <span class="uwb-ok">UWB distance live</span>` : `, <span class="uwb-lost">Bluetooth only: no UWB distance</span>`; };
    let spacing = "";
    if (pucks.length >= 2) {
      const [a, c] = pucks, bl = baselineFor(b, a.id, c.id), d = Math.hypot(a.x - c.x, a.y - c.y) / rangeScale(b);
      const oa = puckOrigin(a, hand), oc = puckOrigin(c, hand);
      spacing = bl ? `Spacing measured by the pucks: ${bl.d_m.toFixed(2)} m ±${(bl.sigma_m ?? 0).toFixed(2)}${bl.samples ? ` (${bl.samples} samples)` : ""}`
        : oa === "hand" && oc === "hand" ? `Spacing set by hand: ${d.toFixed(2)} m` : `Spacing ${d.toFixed(2)} m${oa === "sim" ? " (simulated positions)" : ""}`;
    }
    out.push(`<section class="sec"><h3 class="sec-h">Where the beacon landed ${src}</h3><div class="landed"><div class="where">Floor ${esc(floorNo)}, ${esc(b.room_label || "room unknown")}</div>` +
      `<ul class="pk-list">${pucks.map((q) => { const o = puckOrigin(q, hand); return `<li><span class="pk">${esc(q.id)}</span><span>${esc(ORIGIN_WORD[o] || "position from the Beacon module")}${q.sigma_m != null && o !== "hand" ? `, ±${q.sigma_m} m` : ""}${liveLink ? uwbNote(q.id) : ""}</span>` +
        `${liveLink ? `<button class="btn small pk-restart${uwbLost(q.id) ? " urgent" : ""}" data-act="restart-puck" data-puck="${esc(q.id)}" title="Reboot ${esc(q.id)}'s board so phones redo their UWB handshake (about 10 s)">Restart ${esc(q.id)}</button>` : ""}</li>`; }).join("")}</ul>` +
      (spacing ? `<p class="spacing">${esc(spacing)}</p>` : "") +
      (b.warnings || []).map((w) => `<p class="warnline" title="${esc(w.text || "")}">${esc(warningWords(w))}</p>`).join("") + `<p class="note">${esc(srcWords)}${b.link?.note && !/^replay/i.test(srcWords) ? `. ${esc(b.link.note)}` : ""}.${(b.unplaced_pucks || []).length ? ` Not placed yet: ${esc(b.unplaced_pucks.map((q) => q.id || q).join(", "))}.` : ""}</p>` +
      `<div class="actions"><button class="btn small" data-act="place">Move the pucks</button></div></div></section>`);
  }
  // ---- people: the phones that answered
  const phones = sortedPhones(beacon);
  const inside = phones.filter((p) => !p.report?.safe_outside), ppl = inside.reduce((s, p) => s + (p.report?.people || 0), 0);
  out.push(`<section class="sec"><h3 class="sec-h">Phones with the app <small>${phones.length ? `${inside.length} inside, ${plural(ppl, "person", "people")} reported, ${inside.filter((p) => p.geometry === "fix").length} located to a point` : ""}</small></h3>` +
    (phones.length ? `<ul class="people">${phones.map((p) => personRow(p, k)).join("")}</ul>` : `<p class="empty">${pucks.length ? "No phone has answered yet." : "Phones answer once the pucks are in a room."}</p>`) +
    coldHTML(b) + OPTIN + `</section>`);
  // ---- the record against the phones
  out.push(`<section class="sec">${accHTML(k)}</section>`);
  const lim = b?.limits || [];
  if (lim.length) out.push(`<details class="limits" data-k="lim"><summary>What the phones cannot tell</summary><ul>${lim.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></details>`);
  return out.join("");
}

function personRow(p, k) {
  const r = p.report || {}, out = r.safe_outside, st = k.state || {};
  const inc = (st.phones || []).find((q) => q.id === p.id) || {};
  const asg = (st.units || []).find((u) => u.id === inc.rescuer) || (st.units || []).find((u) => u.assignment && u.assignment.target === p.id);
  const crew = asg ? `${esc(asg.label)} ${inc.status === "out" ? "brought them out" : ["reached", "evacuating"].includes(inc.status) ? "is with them" : "sent"}` : "";
  const room = k.beacon.foreign ? "on another building's plan" : out ? "Outside the building" : p.room_label || p.zone?.room_label ||
    (p.geometry === "ring" ? `Near puck ${p.ring.puck}` : p.geometry === "candidates" ? [...new Set(p.candidates.map((c) => c.room_label || "?"))].join(" or ") : "Where unknown");
  const cap = mirrorCaption(p);
  return `<li><button class="prow${p.id === k.selected ? " sel" : ""}${p.stale ? " stale" : ""}" data-act="pick" data-id="${esc(p.id)}" data-k="ph-${esc(p.id)}">` +
    `<span class="cs ph">${esc(p.id)}</span>` +
    `<span><span class="l1">${statusChip(r)}<span>${esc(peopleWords(r))}</span></span><span class="room" style="display:block;margin-top:4px">${esc(room)}</span>` +
    `<span class="g${p.geometry === "fix" ? " fix" : ""}" style="display:block">${esc(geomShort(p))}</span>${cap ? `<span class="m" style="display:block">${esc(cap)}</span>` : ""}</span>` +
    `<span class="r">${esc(ago(p.track?.last_seen_s ?? r.age_s))}${r.battery_pct != null ? `<br>battery ${r.battery_pct}%` : ""}${crew ? `<br><span class="crew">${crew}</span>` : ""}</span></button></li>`;
}

function coldHTML(b) {
  const pres = (b?.presence || []).filter((c) => (c.count || 0) > 0 || (c.count_range?.[1] || 0) > 0);
  if (!pres.length) return "";
  return pres.map((c) => { const n = c.count_range ? (c.count_range[0] === c.count_range[1] ? c.count_range[0] : `${c.count_range[0]} to ${c.count_range[1]}`) : c.count; return `<p class="cold" title="${esc(c.text || "")}">${n} ${n === 1 ? "device" : "devices"} heard near ${esc(c.puck || "a puck")}${c.room_label ? ` in ${esc(c.room_label)}` : ""}: devices, not people, no identities kept.</p>`; }).join("");
}

// ---------------------------------------------------------------- accountability: the record against the phones, per floor
export function mergeSuites(rows) {
  const by = new Map();
  for (const r of rows) {
    const key = r.suite || r.room, m = by.get(key);
    if (!m) { by.set(key, { ...r, crews: [...(r.crews || [])] }); continue; }
    for (const f of ["phones_opt_in", "people_reported", "located", "cold_devices"]) m[f] = (m[f] || 0) + (r[f] || 0);
    if (r.expected && m.expected && r.room !== m.room) m.expected = { ...m.expected, people: (m.expected.people || 0) + (r.expected.people || 0), low: (m.expected.low || 0) + (r.expected.low || 0), high: (m.expected.high || 0) + (r.expected.high || 0), basis: "sum of the rooms' estimates" };
    if (r.unaccounted && m.unaccounted) m.unaccounted = { low: (m.unaccounted.low || 0) + (r.unaccounted.low || 0), high: (m.unaccounted.high || 0) + (r.unaccounted.high || 0) };
    m.priority = Math.min(m.priority ?? 9, r.priority ?? 9);
    m.crews = [...new Set([...m.crews, ...(r.crews || [])])];
    m.assigned = m.assigned || r.assigned; m.reached = m.reached || r.reached; m.out = m.out || r.out;
    const rank = { "people-reported": 0, "devices-heard": 1, silent: 2, unscanned: 3, "vacant-on-record": 4 };
    if ((rank[r.state] ?? 5) < (rank[m.state] ?? 5)) m.state = r.state;
  }
  return [...by.values()];
}
export function accSummary(beacon, bundle) {
  const all = beacon.rows(), merged = all.some((r) => Array.isArray(r.rooms) || r.scope) ? all : mergeSuites(all);
  const t = beacon.totals() || {}, floorNo = planFloorNo(bundle);
  const lo = t.expected_low ?? merged.reduce((a, r) => a + (r.expected?.low || 0), 0), hi = t.expected_high ?? merged.reduce((a, r) => a + (r.expected?.high || 0), 0);
  const rep = t.people_reported ?? merged.reduce((a, r) => a + (r.people_reported || 0), 0);
  const nc = t.not_covered_suites ?? t.suites_unscanned ?? merged.filter((r) => r.state === "unscanned" || r.covered === false).length;
  return { merged, floorNo, lo, hi, rep, nc, t };
}
function accHTML(k) {
  const { beacon, bundle } = k;
  const { merged, floorNo, lo, hi, rep, nc, t } = accSummary(beacon, bundle);
  const head = `<h3 class="sec-h">Accountability, floor ${esc(floorNo)} ${stamp(bundle.labels.costar.mode === "live" ? "live" : "standin", bundle.labels.costar.text)}</h3>`;
  if (!merged.length) return head + `<p class="empty">${beacon.foreign ? "The Beacon module's suites are not in this twin." : "The record's suites appear here against the phones heard, once the Beacon module answers."}</p>`;
  const line = `<p class="acc-line">The record expects about <b>${lo}</b> to <b>${hi}</b> on this floor. Phones report <b>${rep}</b>${t.safe_outside ? `, ${t.safe_outside} of them outside` : ""}. <b>${nc}</b> ${nc === 1 ? "suite is" : "suites are"} not covered by a puck.</p>`;
  const extra = beacon.acc?.headline && /record (lists|does not|has no)/i.test(beacon.acc.headline) ? `<p class="note">${esc(beacon.acc.headline)}</p>` : "";
  const order = (xs) => xs.slice().sort((a, b) => (a.priority ?? 9) - (b.priority ?? 9) || String(a.suite).localeCompare(String(b.suite), undefined, { numeric: true }));
  const rows = merged.filter((r) => r.scope !== "storey"), storey = merged.filter((r) => r.scope === "storey");
  const sl = beacon.acc?.storey || {};
  const recordWords = (rec) => rec.text || ({ available: "available space, no tenant", vacant: "no current tenant" }[rec.status] || `${plural(rec.tenants || 1, "tenant")}${rec.employees != null ? `, ${rec.employees} staff` : ""}`);
  const phoneWords = (r) => (r.phones_opt_in ? `${plural(r.people_reported || 0, "person", "people")} reported by ${plural(r.phones_opt_in, "phone")}${r.located ? `, ${r.located} located` : ""}` : r.cold_devices ? `${plural(r.cold_devices, "device")} heard, no app` : r.covered === false || r.state === "unscanned" ? "no puck here" : "no phone heard");
  const row = (r) => {
    const crew = r.reached ? `${esc(r.assigned || (r.crews || [])[0] || "crew")} reached` : r.assigned ? `${esc(r.assigned)} sent` : (r.crews || []).length ? `${esc(r.crews.join(", "))} inside` : "";
    return `<li class="acc-row st-${esc(r.state)}" data-act="suite" data-suite="${esc(r.suite)}" title="${esc(r.expected?.basis ? `Expected: ${r.expected.basis}` : "")}${r.why ? `\n${esc(r.why)}` : ""}">` +
      `<div><div class="su">${esc(r.suite)}</div>${r.priority != null && r.priority <= 3 ? `<span class="prio" title="Priority from the record and the phones">Priority ${r.priority}</span>` : ""}</div>` +
      `<div style="min-width:0"><div class="tnm">${esc(r.tenant || r.industry || (r.vacant ? "vacant on record" : "no tenant on record"))}${r.rooms?.length > 1 ? ` <small>(${r.rooms.length} rooms)</small>` : ""}</div>` +
      (r.life_hazard ? `<div class="hz" style="font-size:13px"><span class="hz-ico"></span>${esc(r.life_hazard)}</div>` : "") +
      `<div class="vs">${r.record ? `<b>Record:</b> ${esc(recordWords(r.record))}. ` : r.expected ? `<b>Record:</b> expects ${r.expected.people == null ? "an unknown number" : range(r.expected.low, r.expected.high)}. ` : ""}<b>Phones:</b> ${esc(phoneWords(r))}.</div></div>` +
      `<div class="rt"><div class="un">${r.unaccounted ? range(r.unaccounted.low, r.unaccounted.high) : "?"}</div><div class="ul">unaccounted</div><div class="stw">${esc(STATE_WORD[r.state] || r.state || "")}</div>${crew ? `<div class="stw"><b>${crew}</b>${r.out ? ", out" : ""}</div>` : ""}</div></li>`;
  };
  const sLo = sl.expected_low ?? storey.reduce((a, r) => a + (r.expected?.low || 0), 0), sHi = sl.expected_high ?? storey.reduce((a, r) => a + (r.expected?.high || 0), 0);
  return head + line + extra + `<ul class="acc-rows">${order(rows).map(row).join("")}</ul>` +
    (storey.length ? `<details class="more-rows" data-k="storey"><summary>Same floor, outside the plan: ${plural(sl.not_covered ?? storey.length, "tenancy", "tenancies")}, about ${sLo} to ${sHi} people on record</summary><ul class="acc-rows">${order(storey).map(row).join("")}</ul></details>` : "") +
    `<p class="note">Unaccounted is a range: the record's expected occupancy is an estimate, never a census. A silent suite is weak evidence of empty, never proof.</p>`;
}

// ---------------------------------------------------------------- scout (simulated radar)
function scoutHTML(k) {
  const { scout, active } = k, view = scout.view(), s = scout.scan;
  const head = `<section class="sec"><h3 class="sec-h">Radar scan ${stamp(s ? (s.simulated ? "sim" : "measured") : "sim", "Simulated radar: the boards lack the echo firmware")}${scout.dev ? stamp("sample", "Hand-made sample scan (?devscan=1)") : scout.sample ? stamp("sample", "The server replays the hand-made sample scan") : ""}</h3>`;
  if (!view) {
    return head + `<p class="lead">A crew throws a handful of UWB pucks into a room from its door. The pucks range each other, work out where they lie, register into the twin and listen as a radar for a person who breathes or moves, through smoke.</p>` +
      `<p class="note">${scout.dev ? "Sample mode: press Throw pucks or N." : !scout.supported ? "This server does not offer scout mode yet." : active ? "" : "Start an incident first."}</p>` +
      `<div class="actions"><button class="btn primary" data-act="throw"${(scout.dev || (scout.supported && active && !scout.running)) ? "" : " disabled"}>Throw pucks</button></div></section>`;
  }
  const idx = PHASES.indexOf(scout.phase), failed = scout.phase === "failed";
  const steps = PHASES.map((p, i) => `<li class="${failed ? "" : i < idx ? "done" : i === idx ? "now" : ""}">${esc(PHASE_WORD[p])}</li>`).join("");
  const rung = RUNG_WORD[s?.rung] || (s ? [s.rung, ""] : null), reg = s?.registration;
  const used = s ? (s.pucks || []).length : view.ids.length, links = view.links, usedLinks = links.filter((l) => l.used !== false).length;
  const room = scout.room();
  const facts = [
    ["Room", esc(room?.label || scout.roomId || "the room")],
    ["Method", rung ? `${esc(rung[0])}: ${esc(rung[1])}` : "radar (pucks listening to each other's echoes)"],
    ["Pucks", `${view.n} thrown${s ? `, ${used} placed${s.pucks?.[0]?.sigma_m != null ? ` to ±${s.pucks[0].sigma_m} m` : ""}` : ""}; ${usedLinks} of ${links.length} links used`],
    ["Registered", reg ? esc(regQuality(reg)) : idx >= 3 ? esc(scout.regText || scout.text) : "not yet"],
  ];
  const vics = (s?.victims || []).map((v) => `<li><button class="prow${v.id === k.selected ? " sel" : ""}" data-act="pick" data-id="${esc(v.id)}" data-k="v-${esc(v.id)}"><span class="cs ph">${esc(v.id)}</span><span><span class="room" style="display:block">${esc(victimState(v))}</span><span class="m" style="display:block">${esc(v.text || "")}${v.x == null ? " (position unknown with two pucks)" : ""}</span></span><span class="r">within ${(v.sigma_m ?? 0).toFixed(1)} m<br>${Math.round((v.confidence ?? 0) * 100)}% sure</span></button></li>`).join("");
  const flags = (s?.mismatches || []).map((m) => `<li><button class="prow" data-act="flag" data-id="${esc(m.id)}"><span class="cs" style="background:var(--flag)">${esc(m.id)}</span><span><span style="display:block;font-weight:600">Differs from the twin</span><span class="m" style="display:block">${esc(m.text)}</span></span><span class="r">${Math.round((m.confidence ?? 0) * 100)}%</span></button></li>`).join("");
  const res = s?.resolution;
  return head + `<ol class="sc-steps${failed ? " failed" : ""}">${steps}</ol><p class="lead">${esc(failed ? `Failed: ${scout.text}` : scout.text || (s ? `Scan of ${s.room_label} landed` : ""))}</p>` +
    `<div class="sc-grid">${meshSvg(view, performance.now())}<div class="kv">${facts.map(([a, b]) => `<span class="k">${a}</span><span>${b}</span>`).join("")}</div></div></section>` +
    (s ? `<section class="sec"><h3 class="sec-h">Found</h3>${vics ? `<ul class="people">${vics}</ul>` : `<p class="empty">No one who moves or breathes. A still, non-breathing person would not show.</p>`}</section>` +
      (flags ? `<section class="sec"><h3 class="sec-h">Where the room differs from the twin</h3><ul class="people">${flags}</ul></section>` : "") +
      `<details class="limits" open data-k="sclim"><summary>What this scan cannot tell</summary><ul>${(s.limits || []).map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` +
      (res ? `<p class="note" style="margin:0 0 10px">Range resolution ${res.range_m} m from ${Math.round(res.bandwidth_mhz)} MHz of bandwidth at ${(res.carrier_mhz / 1000).toFixed(1)} GHz${s.simulated ? "; simulated" : ""}. Outline: ${s.outline?.source === "box-fit" ? "a coarse box fitted to echoes (no twin)" : "the twin's room"}.</p>` : "") + `</details>` : "");
}

// ---------------------------------------------------------------- crews (simulated tags on firefighters)
function crewsHTML(k) {
  const st = k.state, sim = k.simulated;
  const units = (st?.units || []).filter((u) => u.physical);
  return `<section class="sec"><h3 class="sec-h">Crew tags ${stamp(sim ? "sim" : "measured", sim ? "Crew positions from the uwb-kit simulator" : "Crew positions from real UWB boards")}</h3>` +
    `<p class="lead">Each crew carries a UWB tag; anchors at the doors locate it. A crew that stops moving for ${k.noMotion ?? "?"} s raises a mayday on its own.</p>` +
    `<ul class="list">${units.map((u) => `<li><span class="cs u-${esc(u.id)}">${esc(u.id)}</span> ${esc(u.room_label || "no fix")}: tag ${esc(u.tag || "?")}${u.sigma_m != null ? `, within ${u.sigma_m.toFixed(1)} m` : ""}, ${u.still_s >= 3 ? `still for ${Math.round(u.still_s)} s` : "moving"}</li>`).join("") || "<li>No tag bound to a crew.</li>"}</ul>` +
    `<p class="note">${plural((st?.anchors || []).length, "anchor")} on the plan. The mayday drill is in the exercise menu.</p></section>`;
}
