// Step 3 of the story, "Command": the crews and their orders, the radio, the command agent's runs (newest first),
// and the documents (pre-plan, after-action). The radio and the agent log grow by appending; the crews re-render.
import { esc, mmss, plural, stamp, agentKind } from "./common.js";

const LAYER_OF_TOOL = {
  get_building_record: "Record", list_tenants: "Record", list_vacant_space: "Record", file_record_correction: "Order",
  outside_context: "Aerial", list_aerial_imagery: "Aerial", look_at_aerial: "Aerial", measure_outside: "Aerial",
  describe_floor: "Twin", route: "Twin", nearest_exit: "Twin", hose_stretch: "Twin",
  locate_unit: "Tags", plan_anchor_drops: "Tags", get_scan: "Pucks", radio: "Order", assign: "Order", mark_hazard: "Order", request_resource: "Order", add_observation: "Order",
};
export const RUN_TITLE = { preplan: "Pre-plan", mayday: "Mayday", hazard: "Re-plan: part of the floor is lost", contact: "Contact made: the way out", aar: "After-action report",
  scan: "Scan landed: a person in the room", victim_reached: "Person reached: the way out", phone: "Phones answered", phone_reached: "Phone reached: the way out" };
const TASK = { stage: "Stage", search: "Search", rescue: "Rescue", rit_rescue: "Rescue the crew", exit: "Withdraw", hold: "Hold", attack: "Attack", ventilate: "Ventilate" };
// radio parties in words: call signs stay short, "ALL" and "COMMAND" read as people say them
export function party(x, units) {
  const u = (units || []).find((q) => q.id === x);
  return u ? u.label : ({ ALL: "all units", COMMAND: "Command" }[String(x).toUpperCase()] || x);
}
const STATUS = { staged: "staged", interior: "inside", mayday: "mayday", exiting: "leaving", out: "out" };

export function taskWords(u) {
  const a = u.assignment; if (!a) return "";
  const t = TASK[a.task] || String(a.task).replace("_", " ");
  return `${t}${a.target_label ? `: ${a.target_label}` : ""}${u.route ? `, ${Math.round(u.route.length_m)} m` : ""}`;
}

export class Command {
  constructor() { this.runs = new Map(); this.radio = []; this.tab = "radio"; this.lastAgent = ""; }
  reset() { this.runs.clear(); this.radio = []; this.lastAgent = ""; }
  onAgent(ev) {
    let run = this.runs.get(ev.run);
    if (!run) { run = { id: ev.run, kind: ev.run.split("#")[0], backend: ev.backend, events: [], t: Date.now() }; this.runs.set(ev.run, run); }
    if (ev.kind === "start") return run;
    if (ev.backend && (ev.kind === "final" || ev.backend === "reflex-fallback")) run.backend = ev.backend;
    run.events.push(ev);
    if (ev.kind === "final") run.final = ev.text;
    this.lastAgent = ev.kind === "tool_call" ? `${LAYER_OF_TOOL[ev.tool] || "Tool"}: ${ev.tool.replace(/_/g, " ")}` : ev.text || "";
    return run;
  }
  onRadio(m) { this.radio.push(m); if (this.radio.length > 300) this.radio.shift(); }

  html(k) {
    const st = k.state, out = [];
    if (st?.mayday) {
      const u = st.units.find((q) => q.id === st.mayday.unit);
      out.push(`<section class="sec"><div class="callout" style="border-color:var(--danger)"><h3 style="color:var(--danger)">Mayday: ${esc(u?.label || st.mayday.unit)}</h3><p class="lead">${esc(st.mayday.room_label || "")}, ${esc({ no_motion: "no movement", button: "tag button pressed", manual: "declared by command" }[st.mayday.reason] || st.mayday.reason)}, ${mmss(st.mayday.elapsed_s)} ago.</p></div></section>`);
    }
    if (st?.phase === "terminated" && k.closing) out.push(`<p class="closing">${esc(k.closing)}</p>`);
    out.push(`<section class="sec"><h3 class="sec-h">Crews <small>${esc(parLine(st))}</small> ${stamp(k.simulated ? "sim" : "measured", k.simulated ? "Crew positions from the uwb-kit simulator" : "Crew positions from real UWB boards")}</h3><ul class="crews">${(st?.units || []).map((u) => crewRow(u, k.selected)).join("")}</ul>` +
      `<p class="note">Select a crew to send it, hold it or withdraw it. With a guided exercise, drag Engine 1 on the plan.</p></section>`);
    const runs = [...this.runs.values()].reverse();
    const units = st?.units || [];
    out.push(`<section class="sec"><div class="seg subtabs" role="tablist"><button role="tab" data-act="cmdtab" data-tab="radio" aria-selected="${this.tab === "radio"}">Radio <small>${this.radio.length || ""}</small></button>` +
      `<button role="tab" data-act="cmdtab" data-tab="agent" aria-selected="${this.tab === "agent"}">Command agent <small>${runs.length || ""}</small></button></div>` +
      (this.tab === "radio" ? radioHTML(this.radio, units) : agentHTML(runs, k)) + `</section>`);
    out.push(`<section class="sec"><h3 class="sec-h">Documents</h3><div class="actions" style="margin-top:0">` +
      `<button class="btn small" data-act="doc-preplan"${k.preplan ? "" : " disabled"}>Pre-plan</button><button class="btn small" data-act="doc-aar"${k.aar ? "" : " disabled"}>After-action report</button>` +
      `<a class="btn small" href="/report.html" target="_blank" rel="noopener">Incident report</a></div>` +
      `<p class="note">The incident report is a printable page: people found, accountability, timeline.</p></section>`);
    return out.join("");
  }
}

export function parLine(st) {
  if (!st || st.phase === "standby") return "";
  if (st.phase === "terminated") return "all out";
  const inside = st.units.filter((u) => ["interior", "mayday", "exiting"].includes(u.status)).length;
  return `${plural(inside, "crew")} inside`;
}
function crewRow(u, selected) {
  const may = u.status === "mayday";
  return `<li><div class="crew-row${may ? " mayday" : ""}${u.id === selected ? " sel" : ""}" data-act="pick" data-id="${esc(u.id)}" role="button" tabindex="0" data-k="u-${esc(u.id)}">` +
    `<span class="cs u-${esc(u.id)}${may ? " mayday" : ""}">${esc(u.id)}</span>` +
    `<span style="min-width:0"><span class="loc">${esc(u.room_label || "no fix")}</span><span class="st">${esc(STATUS[u.status] || u.status)}${u.physical ? "" : ", no tag"}</span>` +
    `<span class="task" style="display:block">${esc(taskWords(u) || u.label)}${u.route && !u.route.valid ? ` <span class="lost">route lost</span>` : ""}</span></span>` +
    `<span class="nums">${u.time_in_s ? `in ${mmss(u.time_in_s)}<br>air ${Math.round(u.air_pct)}%` : u.sigma_m != null ? `within ${u.sigma_m.toFixed(1)} m` : ""}</span></div></li>`;
}
function radioHTML(list, units) {
  if (!list.length) return `<p class="empty">No radio traffic yet.</p>`;
  return `<ul class="radio">${list.slice().reverse().map((m) => `<li class="rx ${esc(m.priority)}"><span class="to">${esc(party(m.from, units))} to ${esc(party(m.to, units))}${m.priority === "emergency" ? ", emergency" : m.priority === "urgent" ? ", urgent" : ""}</span>${esc(m.text)}</li>`).join("")}</ul>`;
}
function agentHTML(runs, k) {
  if (!runs.length) return `<p class="empty">${k.state?.phase === "standby" ? "Dispatch the alarm: the agent reads the record, the aerial and the twin, and writes the pre-plan." : "No agent turn yet."}</p>`;
  return (k.state?.agent_busy ? `<p class="thinking">The agent is working: ${esc(k.lastAgent || "")}</p>` : "") + runs.map((r, i) => {
    const title = RUN_TITLE[r.kind] || r.kind.replace(/_/g, " ");
    const final = r.final || [...r.events].reverse().find((e) => e.kind === "action" || e.kind === "thought")?.text || "working";
    return `<details class="run ${esc(r.kind)}"${i === 0 ? " open" : ""} data-k="run-${esc(r.id)}"><summary><span class="rt">${esc(title)}</span><span>${stamp(agentKind(r.backend), `Agent backend: ${r.backend}`)}</span><span class="rf">${esc(final)}</span></summary>` +
      r.events.filter((e) => e.kind !== "final").map(evHTML).join("") + `</details>`;
  }).join("");
}
function evHTML(ev) {
  if (ev.kind === "tool_call" || ev.kind === "action") {
    const layer = LAYER_OF_TOOL[ev.tool] || "Order";
    const text = ev.kind === "action" ? ev.text : (ev.tool === "look_at_aerial" ? ev.text.replace(/^Look at the aerial \([^)]*\): /, "") : Object.entries(ev.args || {}).map(([a, v]) => `${a}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(", "));
    return `<div class="ev ${esc(ev.kind)}"><span class="tool${layer === "Order" ? " action" : ""}" title="${esc(ev.tool)}">${esc(layer)}</span><span class="arg" title="${esc(text)}">${esc(ev.kind === "action" ? text : `${ev.tool.replace(/_/g, " ")}${text ? `: ${text}` : ""}`)}</span></div>`;
  }
  return `<div class="ev ${esc(ev.kind)}">${esc(ev.text)}</div>`;
}

// ---------------------------------------------------------------- documents in the drawer
const itemRow = (i) => `<div class="item"><span>${esc(i.text)}</span>${i.synthetic ? stamp("standin", "Synthetic, inferred or a stand-in") : "<span></span>"}</div>`;
export function preplanDoc(p, twin) {
  const t = p.tactical || {}, hs = t.hose_stretch, stairs = Object.fromEntries(twin.stairs.map((s) => [s.id, s.label]));
  return `<p class="lead">${esc(p.address)}</p><p>${esc(p.summary || "")}</p><p>Written by the command agent ${stamp(agentKind(p.backend), `Backend: ${p.backend}`)}</p>` +
    `<h3>Tactical</h3><div class="kvd"><span class="k">Attack stair</span><span>${esc(stairs[t.attack_stair] || t.attack_stair || "not set")}</span><span class="k">Evacuation stair</span><span>${esc(stairs[t.evac_stair] || t.evac_stair || "not set")}</span>` +
    `<span class="k">Staging</span><span>${esc(t.staging || "not set")}</span>` +
    (hs ? `<span class="k">Hose stretch</span><span>${esc(hs.target)} from ${esc(stairs[hs.stair] || hs.stair)}: ${hs.total_m} m (${hs.lengths_50ft} lengths) = inside ${hs.inside_m} m (twin) + stair ${hs.vertical_m} m; supply leg outside ${hs.outside_m} m (aerial)</span>` : "") +
    (t.anchor_drops ? `<span class="k">UWB anchor drops</span><span>${t.anchor_drops.map((a) => `${esc(a.id)} ${esc(a.near)}`).join("; ")} (3 or more anchors within one partition wall on ${t.anchor_coverage_pct}% of the floor graph; strict line of sight ${t.anchor_line_of_sight_pct ?? "?"}%)</span>` : "") +
    `<span class="k">Life hazards</span><span>${(t.life_hazards || []).map((h) => `Suite ${esc(h.suite)}: ${esc(h.why)}`).join("<br>") || "none listed"}</span></div>` +
    (p.sections || []).map((s) => `<h3>${esc(s.title || s.id)}</h3>${(s.items || []).map(itemRow).join("")}`).join("") +
    (p.vision || []).map((v) => `<h3>What the agent looked at</h3><img class="vision" src="/api/vision/${esc(v.image)}.png" alt="The aerial image sent to the model, with the footprint, side letters, hydrants and the engine drawn on it">` +
      `<p class="qa"><b>Asked:</b> ${esc(v.question)}</p><p class="qa"><b>The model answered:</b> ${esc(v.answer)}</p><p class="qa"><small>Image: ${esc(v.source)}. Overlays drawn by Sandtable before sending.</small></p>`).join("") +
    `<h3>Verify on site</h3>${(p.verify_on_site || []).map((v) => `<div class="item"><span>${esc(v.text)}</span><small>${esc((v.layers || []).join(" and "))}</small></div>`).join("") || "<p class='empty'>Nothing flagged.</p>"}`;
}
export function aarDoc(a, simulated) {
  const m = a.metrics || {};
  const list = (xs) => (xs || []).map((x) => `<div class="item"><span>${esc(x)}</span><span></span></div>`).join("") || "<p class='empty'>None.</p>";
  return `<p class="lead">${esc(a.summary || "")}</p><p>Written by the command agent ${stamp(agentKind(a.backend), `Backend: ${a.backend}`)}</p>` +
    `<h3>Measured${simulated ? " (simulated run)" : ""}</h3><div class="kvd">` +
    Object.entries({ mayday_to_assign_s: "Mayday to rescue order", mayday_to_contact_s: "Mayday to contact", mayday_to_all_out_s: "Mayday to all out", replans: "Re-plans", hazards: "Hazards reported" })
      .filter(([kk]) => m[kk] !== undefined).map(([kk, l]) => `<span class="k">${l}</span><span>${m[kk]}${kk.endsWith("_s") ? " s" : ""}</span>`).join("") + `</div>` +
    `<h3>Went well</h3>${list(a.went_well)}<h3>Improve</h3>${list(a.improve)}<h3>Follow up</h3>${list(a.follow_up)}` +
    `<h3>Pinned in the twin</h3>${(a.observations || []).map((o) => `<div class="item"><span>${esc(o.description || o.label)}</span><small>${esc(o.id)}</small></div>`).join("") || "<p class='empty'>None.</p>"}` +
    `<h3>Record corrections filed</h3>${(a.corrections || []).map((c) => `<div class="item"><span><b>${esc(c.field)}</b>: ${esc(typeof c.proposed === "object" ? JSON.stringify(c.proposed) : c.proposed)}<br><small>${esc(c.status || "")}</small></span><small>${esc(c.id || "")}</small></div>`).join("") || "<p class='empty'>None.</p>"}` +
    `<h3>Timeline</h3>${(a.timeline || []).map((e) => `<div class="tl ${esc(e.kind)}"><span class="t">+${mmss(e.t_rel)}</span><span class="tx">${esc(e.text)}</span></div>`).join("")}`;
}
