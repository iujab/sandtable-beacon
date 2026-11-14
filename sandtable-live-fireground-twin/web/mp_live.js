// Matterport Showcase SDK view of the real scan, driven by the command board through postMessage.
//
// Config: GET /api/live/config -> {"matterport": {"mode", "sdk_key", "model_id"}} (filled only when the server runs with
// MATTERPORT_MODE=live) is used first; the URL fragment #m=<model>&key=<sdk key> is the fallback for testing. Nothing is hard-coded.
// SDK: the current bootstrap (@matterport/sdk setupSdk, pinned version, loaded at runtime: its licence is Matterport's own, so it
// is not vendored); the older SDK-for-Embeds bootstrap (MP_SDK.connect) is the fallback.
// The SDK key is restricted to the domain `localhost`: the page must be opened at http://localhost:<port>.
//
// Sweeps: the normalised twin (GET /api/bundle -> twin.sweeps, ids are Matterport sweep ids, positions in the
// building frame) is used to find the sweep nearest a board position, so no axis conversion is guessed here.
//
// postMessage API (parent -> this page):
//   {type: "mp.goto", x, y, floor?}   move the camera to the sweep nearest (x, y) of the building frame
//   {type: "mp.sweep", id}            move to a sweep by id
//   {type: "mp.ping"}
// replies (this page -> parent): {type: "mp.ready", sweeps, model_sweeps} | {type: "mp.moved", ok, sweep, dist_m, error?}
//   | {type: "mp.error", text} | {type: "mp.pong", ready}
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = { sdk: null, sweeps: [], modelSweeps: new Set(), ready: false, current: null };
  const say = (html, err = false) => { $("status").innerHTML = html; $("status").classList.toggle("err", err); };
  const post = (msg) => { try { (window.parent !== window ? window.parent : window).postMessage(msg, "*"); } catch (_) { /* no parent */ } };
  window.__mp = state;                                                          // for tests

  async function config() {
    const frag = new URLSearchParams(location.hash.slice(1));
    try {
      const r = await fetch("/api/live/config", { cache: "no-store" });
      if (r.ok) {
        const body = await r.json();
        const c = (body && body.matterport) || body || {};
        if (c.sdk_key && c.model_id) return { sdk_key: c.sdk_key, model_id: c.model_id, twin_url: "/api/bundle", via: "server" };
      }
    } catch (_) { /* endpoint absent */ }
    if (frag.get("m") && frag.get("key")) return { model_id: frag.get("m"), sdk_key: frag.get("key"), twin_url: frag.get("twin") || "/api/bundle", via: "fragment" };
    return null;
  }

  async function loadTwin(url) {
    try {
      const r = await fetch(url, { cache: "no-store" });
      const b = await r.json();
      const twin = b.twin || b;
      return (twin.sweeps || []).map((s) => ({ id: s.id, floor: s.floor, x: s.position[0], y: s.position[1] }));
    } catch (_) { return []; }
  }

  function loadScript(src) {
    return new Promise((res, rej) => { const s = document.createElement("script"); s.src = src; s.onload = res; s.onerror = () => rej(new Error("could not load " + src)); document.head.appendChild(s); });
  }

  const SDK_URL = "https://cdn.jsdelivr.net/npm/@matterport/sdk@1.6.2/dist/index.umd.js";
  const PARAMS = { play: 1, qs: 1, title: 0, brand: 0, help: 0 };

  async function connectCurrent(iframe, c) {
    // current bootstrap: given an existing iframe, setupSdk() only handshakes with it, so the iframe is pointed at the
    // Showcase (with the application key) first; setupSdk() resolves with the connected SDK
    if (!window.setupSdk) await loadScript(SDK_URL);
    iframe.src = `https://my.matterport.com/show/?m=${encodeURIComponent(c.model_id)}&${new URLSearchParams(PARAMS)}&applicationKey=${encodeURIComponent(c.sdk_key)}`;
    const sdk = await Promise.race([window.setupSdk(c.sdk_key, { iframe, space: c.model_id }),
      new Promise((_, rej) => setTimeout(() => rej(new Error("setupSdk timed out after 45 s")), 45000))]);
    state.bootstrap = "@matterport/sdk 1.6.2 (setupSdk)";
    return sdk;
  }

  function connectLegacy(iframe, c) {
    // fallback: SDK for Embeds 2.x (window.MP_SDK.connect over the embed's postMessage channel)
    return new Promise((resolve, reject) => {
      iframe.addEventListener("load", async () => {
        try {
          if (!window.MP_SDK) await loadScript("https://static.matterport.com/showcase-sdk/latest.js");
          let lastErr;
          for (const ver of ["", "3.10", "3.6"]) {
            try { const sdk = await window.MP_SDK.connect(iframe, c.sdk_key, ver); state.bootstrap = "showcase-sdk latest.js (MP_SDK.connect, fallback)"; return resolve(sdk); } catch (e) { lastErr = e; }
          }
          reject(lastErr || new Error("MP_SDK.connect failed"));
        } catch (e) { reject(e); }
      }, { once: true });
      iframe.src = `https://my.matterport.com/show/?m=${encodeURIComponent(c.model_id)}&${new URLSearchParams(PARAMS)}&applicationKey=${encodeURIComponent(c.sdk_key)}`;
    });
  }

  async function connect(iframe, c) {
    try { return await connectCurrent(iframe, c); } catch (e) { state.bootstrapError = String(e && e.message || e); }
    return connectLegacy(iframe, c);
  }

  async function moveTo(id) {
    if (!state.sdk) throw new Error("not connected");
    const opts = { transition: state.sdk.Sweep.Transition ? state.sdk.Sweep.Transition.FLY : undefined, transitionTime: 900 };
    await state.sdk.Sweep.moveTo(id, opts);
    return id;
  }

  function nearest(x, y, floor) {
    let best = null;
    for (const s of state.sweeps) {
      if (floor && s.floor !== floor) continue;
      if (state.modelSweeps.size && !state.modelSweeps.has(s.id)) continue;
      const d = Math.hypot(s.x - x, s.y - y);
      if (!best || d < best.d) best = { s, d };
    }
    return best;
  }

  window.addEventListener("message", async (ev) => {
    const m = ev.data || {};
    if (m.type === "mp.ping") return post({ type: "mp.pong", ready: state.ready });
    if (m.type !== "mp.goto" && m.type !== "mp.sweep") return;
    try {
      if (m.type === "mp.sweep") {
        await moveTo(m.id);
        return post({ type: "mp.moved", ok: true, sweep: m.id, dist_m: null });
      }
      const b = nearest(+m.x, +m.y, m.floor);
      if (!b) throw new Error("no sweep known (is the twin loaded?)");
      await moveTo(b.s.id);
      say(`<b>Real scan</b> · moved to the sweep nearest (${(+m.x).toFixed(1)}, ${(+m.y).toFixed(1)}), ${b.d.toFixed(1)} m away`);
      post({ type: "mp.moved", ok: true, sweep: b.s.id, dist_m: +b.d.toFixed(2) });
    } catch (e) {
      say(`<b>Real scan</b> · move failed: ${String(e && e.message || e)}`, true);
      post({ type: "mp.moved", ok: false, error: String(e && e.message || e) });
    }
  });

  (async () => {
    const c = await config();
    if (!c) { $("empty").classList.add("on"); say("<b>Real scan</b> · not configured", true); return post({ type: "mp.error", text: "not configured" }); }
    if (location.hostname !== "localhost") say(`<b>Real scan</b> · the SDK key is restricted to <code>localhost</code>; this page is on ${location.hostname} and will probably be refused`, true);
    state.sweeps = await loadTwin(c.twin_url);
    state.via = c.via;
    const iframe = $("showcase");
    {
      try {
        state.sdk = await connect(iframe, c);
        await new Promise((res) => {
          let done = false;
          const finish = () => { if (!done) { done = true; res(); } };
          try {
            state.sdk.Sweep.data.subscribe({ onCollectionUpdated: (col) => { for (const k of Object.keys(col)) state.modelSweeps.add(col[k].sid || col[k].id || k); finish(); } });
          } catch (_) { finish(); }
          setTimeout(finish, 8000);
        });
        try { state.sdk.Sweep.current.subscribe((s) => { state.current = s && (s.sid || s.id); }); } catch (_) { /* optional */ }
        state.ready = true;
        const known = state.sweeps.filter((s) => state.modelSweeps.has(s.id)).length;
        say(`<b>Real scan</b> · Matterport Showcase (live) · ${state.modelSweeps.size} sweeps in the scan, ${known} matched to the twin`);
        post({ type: "mp.ready", sweeps: state.sweeps.length, model_sweeps: state.modelSweeps.size, matched: known });
      } catch (e) {
        say(`<b>Real scan</b> · SDK connect failed: ${String(e && e.message || e)}`, true);
        post({ type: "mp.error", text: String(e && e.message || e) });
      }
    }
  })();
})();
