// Step 1 of the story, "Know before you go": the building before anyone enters. Built only from the bundle
// (the record, the aerial, the twin) and, once the alarm is in, the command agent's pre-plan.
import { esc, fmt, plural, stamp, layerKind, agentKind } from "./common.js";
import { prettyIndustry } from "./plan2d.js";

const nul = (v) => v === null || v === undefined || v === "";
const tenantName = (t, live) => (live && t.name ? t.name : prettyIndustry(t.industry) || t.name || "tenant, industry not on record");

// the storeys the record knows, top first, with what is on each
export function storeys(bundle) {
  const { site, twin } = bundle, p = site.property;
  const nums = new Set();
  for (let i = 1; i <= (Number(p.stories) || 0); i++) nums.add(i);
  for (const t of site.tenants || []) if (Number.isFinite(t.floor)) nums.add(t.floor);
  for (const a of site.available_spaces || []) if (Number.isFinite(a.floor)) nums.add(a.floor);
  for (const f of p.floors_on_record || []) if (Number.isFinite(f)) nums.add(f);
  const planOf = {};
  for (const f of twin.floors) if (twin.rooms.some((r) => r.floor === f.id) && Number.isFinite(Number(f.label))) { planOf[Number(f.label)] = f; nums.add(Number(f.label)); }
  return [...nums].sort((a, b) => b - a).map((n) => {
    const ts = (site.tenants || []).filter((t) => t.floor === n), av = (site.available_spaces || []).filter((a) => a.floor === n);
    const emp = ts.filter((t) => t.employees != null);
    return { n, tenants: ts, available: av, hazards: ts.filter((t) => t.life_hazard || t.hazmat), employees: emp.length ? emp.reduce((s, t) => s + t.employees, 0) : null, plan: planOf[n] || null };
  });
}
export function planFloorNo(bundle) { const t = bundle.twin; return Number(t.floors.find((f) => f.id === t.exercise_floor)?.label); }

// which fire-service side an oblique shows, from the face it looks at and the building's side bearings
export function obliqueSides(bundle) {
  const obl = (bundle.aerial.images || []).filter((i) => i.kind === "oblique" && (i.url || i.path));
  const sides = bundle.site.sides || {}, FACE = { north: 0, east: 90, south: 180, west: 270 };
  const letterOf = (im) => {
    const face = im.shows_face in FACE ? FACE[im.shows_face] : ((im.heading_deg ?? 0) + 180) % 360;
    let best = null;
    for (const [k, s] of Object.entries(sides)) { const d = Math.abs(((s.bearing_deg - face) % 360 + 540) % 360 - 180); if (!best || d < best[1]) best = [k, d]; }
    return best ? best[0] : "?";
  };
  return obl.map((im) => ({ im, letter: letterOf(im) })).sort((a, b) => a.letter.localeCompare(b.letter));
}

export function knowHTML(k) {
  const { bundle, preplan, acc, phase, agentBusy, openFloor } = k;
  const { site, twin, labels } = bundle, p = site.property, fs = site.field_sources || {};
  const live = labels.costar.mode === "live", recK = layerKind(labels, "costar"), twinK = layerKind(labels, "matterport"), aerK = layerKind(labels, "vexcel");
  const out = [];

  // ---- the agent's pre-plan (once the alarm is in)
  if (preplan) {
    const t = preplan.tactical || {}, hs = t.hose_stretch, stairs = Object.fromEntries(twin.stairs.map((s) => [s.id, s.label]));
    out.push(`<section class="sec"><h3 class="sec-h">Pre-plan ${stamp(agentKind(preplan.backend), `Written by the command agent (${preplan.backend})`)}</h3>` +
      `<p class="lead">${esc(preplan.summary || "")}</p><dl class="facts">` +
      fact("Attack", stairs[t.attack_stair] || t.attack_stair) + fact("Evacuation", stairs[t.evac_stair] || t.evac_stair) +
      fact("Staging", t.staging, true) + (hs ? fact("Hose stretch", `${hs.total_m} m (${hs.lengths_50ft} lengths of 50 ft) to ${hs.target} from ${stairs[hs.stair] || hs.stair}`, true) : "") +
      `</dl><div class="actions"><button class="btn small" data-act="doc-preplan">Read the full pre-plan</button></div></section>`);
  } else if (phase === "preplan" || agentBusy) {
    out.push(`<section class="sec"><div class="callout"><h3>Writing the pre-plan</h3><p class="lead">The command agent is reading the record, the aerial and the twin.</p></div></section>`);
  }

  // ---- the building
  const sub = p.property_subtype ? String(p.property_subtype).split("_").pop().replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase() : "";
  const kh = p.property_manager?.name || null;
  const facts = [
    ["Type", [p.property_type, sub && sub !== String(p.property_type).toLowerCase() ? sub : ""].filter(Boolean).join(", "), "property_type", true],
    ["Storeys", p.stories, "stories"], ["Height", p.height_m && `${p.height_m} m`, "height_m"],
    ["Construction", p.construction, "construction", true],
    ["Sprinklers", p.sprinklers, "sprinklers"], ["Elevators", p.elevators, "elevators"],
    ["Class", p.building_class, "building_class"], ["Year built", p.year_built && `${p.year_built}${p.year_renovated ? `, renovated ${p.year_renovated}` : ""}`, "year_built"],
    ["Area", p.rba_sf && `${fmt(p.rba_sf)} sq ft`, "rba_sf"], ["Key holder", kh, "property_manager"],
  ];
  const contacts = live ? (p.contacts || []).filter((c) => c && (c.name || c.phone)) : [];
  out.push(`<section class="sec"><h3 class="sec-h">The building ${stamp(recK, labels.costar.text)}</h3><dl class="facts">` +
    facts.map(([kk, v, f, wide]) => {
      const src = fs[f]?.source || "", empty = nul(v);
      return `<div${wide ? ' class="wide"' : ""} title="${esc(empty ? (live ? "Not in this building's record" : "Only the sponsor record has this field") : src)}"><dt>${kk}</dt><dd${empty ? ' class="null"' : ""}>${empty ? "not in the record" : esc(v)}${fs[f]?.synthetic ? " <small>(inferred)</small>" : ""}</dd></div>`;
    }).join("") + `</dl>` +
    (contacts.length ? `<details class="limits mt" data-k="contacts"><summary>${plural(contacts.length, "contact")} on record</summary><ul>${contacts.map((c) => `<li>${esc([c.role, c.name, c.phone].filter(Boolean).join(", "))}</li>`).join("")}</ul></details>` : "") + `</section>`);

  // ---- who is inside: the stack, top floor first
  const floors = storeys(bundle), planNo = planFloorNo(bundle);
  const tot = acc?.totals, accLine = tot && tot.expected_high != null ? `Expect about ${tot.expected_low ?? "?"} to ${tot.expected_high} people on floor ${planNo} during open hours: an estimate from the record, not a count.` : "";
  const allTen = site.tenants || [], hz = allTen.filter((t) => t.life_hazard).length;
  out.push(`<section class="sec"><h3 class="sec-h">Who is inside ${stamp(recK, labels.costar.text)}</h3>` +
    `<p class="note" style="margin:0 0 8px">${plural(allTen.length, "tenancy", "tenancies")} on ${plural(floors.filter((f) => f.tenants.length).length, "floor")}${hz ? `, ${plural(hz, "life hazard")}` : ""}${(site.available_spaces || []).length ? `, ${plural(site.available_spaces.length, "space")} available` : ""}.${live ? "" : " Tenant names withheld."}</p>` +
    `<div class="stack-key"><span><i></i>tenancy</span><span><i class="h"></i>life hazard</span><span><i class="a"></i>available space</span></div>` +
    `<div class="stack" role="list">` + floors.map((f) => {
      const open = openFloor === f.n;
      const blocks = f.tenants.map((t) => `<i class="b${t.life_hazard ? " h" : ""}" title="${esc(`Suite ${t.suite}: ${tenantName(t, live)}${t.life_hazard ? `. Life hazard: ${t.life_hazard}` : ""}`)}"></i>`).join("") +
        f.available.map((a) => `<i class="b a" title="${esc(`Suite ${a.suite}: available${a.sf ? `, ${fmt(a.sf)} sq ft` : ""}`)}"></i>`).join("");
      const nh = f.tenants.filter((t) => t.life_hazard).length;
      const sum = f.tenants.length ? `${plural(f.tenants.length, "tenancy", "tenancies")}${f.employees != null ? `, ${fmt(f.employees)} staff` : ""}${nh ? `, ${nh} hazard${nh === 1 ? "" : "s"}` : ""}` : f.available.length ? "available space" : "nothing on record";
      return `<div role="listitem"><button class="fl-row" data-act="floor" data-floor="${f.n}" aria-expanded="${open}" data-k="fl${f.n}"><span class="fn">${f.n}</span><span class="blocks">${blocks || ""}</span><span class="fs">${esc(sum)}${f.plan ? `<span class="plan-tag" title="This floor has an interior plan">Plan</span>` : ""}</span></button>` +
        (open ? floorDetail(f, live, f.n === planNo ? accLine : "") : "") + `</div>`;
    }).join("") + `</div></section>`);

  // ---- getting there: sides, entrances, hydrants, stations, obliques
  const ctx = site.context || {}, sides = site.sides || {};
  const hyd = (ctx.hydrants || []).slice().sort((a, b) => a.distance_m - b.distance_m);
  const st = (ctx.fire_stations || []).slice().sort((a, b) => a.distance_m - b.distance_m);
  const obl = obliqueSides(bundle);
  out.push(`<section class="sec"><h3 class="sec-h">Getting there ${stamp(aerK, labels.vexcel.text)}</h3>` +
    `<div class="sides-tbl">${Object.entries(sides).map(([kk, s]) => `<span class="side-l">${esc(kk)}</span><span title="${esc(s.note || "")}">${esc(s.street || "no street on record")}${/address/i.test(s.note || "") ? " <small>address side</small>" : ""}</span>`).join("")}</div>` +
    `<ul class="list mt">` +
    (ctx.entrances || []).map((e) => `<li>Entrance: ${esc(e.label)}${e.synthetic ? " <small>(inferred, not surveyed)</small>" : ""}</li>`).join("") +
    (hyd.length ? `<li>${plural(hyd.length, "hydrant")} nearby. Nearest: ${hyd.slice(0, 3).map((h) => `${h.distance_m} m${h.size ? ` (${esc(h.size)})` : ""}`).join(", ")}</li>` : `<li>No hydrants on record</li>`) +
    (st.length ? `<li>Nearest stations: ${st.slice(0, 2).map((s) => `${esc(s.name)}, ${(s.distance_m / 1000).toFixed(1)} km`).join("; ")}</li>` : "") +
    (ctx.apparatus_positions || []).map((a) => `<li>${esc(a.label)} staged on side ${esc(a.side || "?")}${a.synthetic ? " <small>(inferred)</small>" : ""}</li>`).join("") +
    `</ul>` +
    (obl.length ? `<p class="note">Oblique views, one per side:</p><div class="thumbs mt">${obl.map(({ im, letter }, i) => `<button class="thumb" data-act="oblique" data-i="${i}" title="Side ${esc(letter)}: the ${esc(im.shows_face || "")} face"><img src="${esc(im.url || "/" + im.path)}" alt="Side ${esc(letter)}" loading="lazy"><span class="lt">${esc(letter)}</span></button>`).join("")}</div>` : "") +
    `<div class="actions"><button class="btn small" data-act="view" data-view="aerial">Show the aerial</button></div></section>`);

  // ---- inside: the twin
  const fid = twin.exercise_floor, rooms = twin.rooms.filter((r) => r.floor === fid);
  const suites = rooms.filter((r) => r.kind === "suite").length;
  out.push(`<section class="sec"><h3 class="sec-h">Inside, floor ${planNo} ${stamp(twinK, labels.matterport.text)}</h3>` +
    `<p class="note" style="margin:0 0 6px">${esc(twin.source.synthetic ? "Synthetic interior fitted to the real footprint, numbered with the real suites. Not a scan." : "Scanned interior (Matterport).")}</p>` +
    `<p class="pp-sum">${plural(rooms.length, "room")} (${plural(suites, "suite")}), ${plural(twin.doors.filter((d) => d.floor === fid).length, "door")}, ${plural(twin.walls.filter((w) => w.floor === fid).length, "wall segment")}.</p>` +
    `<ul class="list">${twin.stairs.map((s) => `<li><b>${esc(s.label)}</b>: ${[s.standpipe ? "standpipe" : "", s.roof_access ? "roof access" : "", s.discharge ? `discharges to ${s.discharge}` : ""].filter(Boolean).map(esc).join(", ") || "no details"}</li>`).join("") || "<li>No stair in the scan: the suite entrance is the way in and out</li>"}</ul>` +
    `<div class="actions"><button class="btn small" data-act="view" data-view="3d">3D twin</button><button class="btn small" data-act="view" data-view="2d">Plan</button>${k.scanAvailable ? `<button class="btn small" data-act="view" data-view="scan">Matterport scan</button>` : ""}</div></section>`);

  // ---- check on arrival: where the layers disagree, and what the agent asks to verify
  const checks = [...(site.cross_checks || []).map((c) => c.text), ...((preplan?.verify_on_site || []).map((v) => v.text))];
  if (checks.length) out.push(`<section class="sec"><h3 class="sec-h">Check on arrival</h3><ul class="list">${checks.slice(0, 8).map((c) => `<li>${esc(c)}</li>`).join("")}</ul></section>`);

  if (!preplan && phase === "standby") out.push(`<section class="sec"><div class="callout"><h3>Dispatch the alarm</h3><p class="lead">The command agent reads the record, the aerial and the twin, and writes the pre-plan for the first-in crew.</p><div class="actions"><button class="btn primary" data-act="alarm">Dispatch alarm</button></div></div></section>`);
  return out.join("");
}

function fact(k, v, wide) { const e = nul(v); return `<div${wide ? ' class="wide"' : ""}><dt>${esc(k)}</dt><dd${e ? ' class="null"' : ""}>${e ? "not set" : esc(v)}</dd></div>`; }

function floorDetail(f, live, accLine) {
  const rows = f.tenants.slice().sort((a, b) => String(a.suite).localeCompare(String(b.suite), undefined, { numeric: true })).map((t) =>
    `<div class="tn" data-suite="${esc(t.suite)}"><div class="su">${esc(t.suite || "?")}</div><div><div>${esc(tenantName(t, live))}</div>` +
    `<div class="meta">${t.employees != null ? `${fmt(t.employees)} staff on record` : "head count not in the record"}${t.sf ? `, ${fmt(Math.round(t.sf))} sq ft` : ""}</div>` +
    (t.life_hazard ? `<div class="hz"><span class="hz-ico"></span>${esc(t.life_hazard)}</div>` : "") + (t.hazmat ? `<div class="hz"><span class="hz-ico"></span>Hazmat: ${esc(t.hazmat)}</div>` : "") + `</div></div>`).join("");
  const av = f.available.map((a) => `<div class="tn"><div class="su">${esc(a.suite || "?")}</div><div>Available${a.sf ? `, ${fmt(a.sf)} sq ft` : ""}${a.space_use ? ` <small>${esc(a.space_use)}</small>` : ""}</div></div>`).join("");
  return `<div class="fl-detail">${accLine ? `<p class="acc-line" style="font-size:14.5px">${esc(accLine)}</p>` : ""}${rows || av ? rows + av : `<p class="empty">Nothing on record for this floor.</p>`}</div>`;
}
