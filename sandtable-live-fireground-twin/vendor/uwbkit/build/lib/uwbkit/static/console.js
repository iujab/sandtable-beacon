/* uwb-kit web console. Uses only the public stream (INTERFACE.md v1) through uwbkit-client.js.
 * Never reads /sim/truth: what you see is what the solver produced from the ranges. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var kit = new UwbKit(location.origin);
  var COLORS = ["#35d0ba", "#f472b6", "#facc15", "#60a5fa", "#a78bfa", "#fb923c", "#4ade80", "#e879f9"];
  var S = {
    hello: null, anchors: {}, calibrated: null, room: {}, mesh: false, dims: 2, canTeleport: false,
    pos: {}, trails: {}, ranges: {}, events: [], status: null, diag: null,
    held: {}, drag: null, mode: "lines", showTrails: true, bounds: null, lastStatus: 0,
    color: {}, dirty: true, scenarios: null
  };
  var canvas = $("map"), ctx = canvas.getContext("2d"), wrap = $("mapwrap");
  var dpr = 1, CW = 0, CH = 0, T = { k: 50, ox: 0, oy: 0, miny: 0, maxy: 1, minx: 0 };

  function now() { return Date.now() / 1000; }
  function fmt(v, n) { return typeof v === "number" && isFinite(v) ? v.toFixed(n) : "-"; }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function nonAnchors() {
    if (!S.hello) return [];
    return Object.keys(S.hello.nodes).filter(function (id) { return S.hello.nodes[id].role !== "anchor"; });
  }
  function colorOf(id) {
    if (!S.color[id]) S.color[id] = COLORS[Object.keys(S.color).length % COLORS.length];
    return S.color[id];
  }

  // ------------------------------------------------------------------ stream handlers
  kit.on("open", function () { setBadge(); });
  kit.on("close", function () { setBadge(); });
  kit.on("hello", function (h) {
    S.hello = h; S.room = h.room || {}; S.dims = h.dims || 2;
    S.mesh = /^mesh/i.test(h.profile || "") || h.frame === "mesh";
    S.isSim = !!h.simulated && /^sim:/.test(h.source || "");
    S.canTeleport = S.isSim && !S.mesh;      // mesh positions are in a relative frame: no dragging there
    S.pos = {}; S.trails = {}; S.ranges = {}; S.held = {}; S.drag = null; S.bounds = null; S.color = {};
    nonAnchors().forEach(colorOf);
    $("c-source").textContent = h.source || "-";
    $("c-profile").textContent = h.profile || "-";
    $("sec-sim").classList.toggle("hide", !S.isSim);
    $("cards").textContent = "";
    $("hint").textContent = S.canTeleport ? "drag a tag to teleport it" : "";
    canvas.classList.toggle("grab", S.canTeleport);
    setBadge();
    if (S.isSim) loadScenarios(h);
    buildAnchorForm();
    S.dirty = true;
  });
  kit.on("anchors", function (a) {
    S.anchors = a.anchors || {}; S.calibrated = a.calibrated; S.bounds = null;
    $("cal-state").textContent = S.mesh ? "mesh: none" : (a.calibrated || "");
    buildAnchorForm();
    updateOverlay();
    S.dirty = true;
  });
  kit.on("pos", function (p) {
    p._rx = now();
    S.pos[p.id] = p;
    var tr = S.trails[p.id] || (S.trails[p.id] = []);
    tr.push([p.x, p.y, p._rx]);
    if (tr.length > 240) tr.splice(0, tr.length - 240);
    S.dirty = true;
  });
  kit.on("range", function (r) { r._rx = now(); S.ranges[r.a + "-" + r.b] = r; S.dirty = true; });
  kit.on("status", function (s) { S.status = s; S.lastStatus = now(); renderHeader(); });
  kit.on("event", function (e) {
    S.events.unshift(e);
    if (S.events.length > 40) S.events.length = 40;
    renderEvents();
    if (e.name === "calibration") {
      var d = e.data || {};
      note("cal-note", d.ok ? "solved, residual " + fmt((d.rms_m || 0) * 100, 1) + " cm rms" : "failed: " + (d.error || "?"), d.ok ? "ok" : "err");
    }
  });

  function setBadge() {
    var b = $("badge");
    if (!kit.connected) { b.className = "badge off"; b.textContent = S.hello ? "RECONNECTING" : "CONNECTING"; return; }
    if (!S.hello) return;
    if (S.hello.simulated) { b.className = "badge sim"; b.textContent = "SIM"; b.title = "Simulated data: " + (S.hello.source || ""); }
    else { b.className = "badge live"; b.textContent = "LIVE BOARDS"; b.title = "Real boards: " + (S.hello.source || ""); }
  }

  function note(id, text, cls) {
    var n = $(id);
    n.textContent = text || "";
    n.className = "note" + (cls ? " " + cls : "");
    if (id === "cal-note") n.style.margin = "0";
    n.classList.toggle("hide", !text);
  }

  // ----------------------------------------------------------------------- header
  function renderHeader() {
    var s = S.status || {}, h = S.hello || {};
    var total = h.nodes ? Object.keys(h.nodes).length : 0, on = (s.nodes_online || []).length;
    $("c-rate").textContent = fmt(s.rate_hz, 1) + " Hz";
    $("chip-rate").className = "chip" + (h.rate_hz && s.rate_hz < 0.6 * h.rate_hz ? " warn" : "");
    $("c-online").textContent = on + "/" + total;
    $("chip-online").className = "chip" + (on < total ? " bad" : "");
    $("c-drop").textContent = s.dropped != null ? s.dropped : "-";
    $("c-clients").textContent = s.clients != null ? s.clients : "-";
    var bat = s.battery || {}, ids = Object.keys(bat);
    $("chip-batt").classList.toggle("hide", !ids.length);
    $("c-batt").textContent = ids.map(function (k) { return k + " " + fmt(bat[k], 2) + " V"; }).join("  ");
    $("chip-batt").className = "chip" + (ids.some(function (k) { return bat[k] < 3.5; }) ? " warn" : "") + (ids.length ? "" : " hide");
    var rec = $("rec");
    rec.classList.toggle("on", !!s.recording);
    rec.innerHTML = s.recording ? "&#9632; Stop recording" : "&#9679; Record";
    if (s.sim) {
      $("pause").textContent = s.sim.paused ? "Resume" : "Pause";
      $("pause").classList.toggle("on", !!s.sim.paused);
      document.querySelectorAll(".spd").forEach(function (b) { b.classList.toggle("on", Math.abs(parseFloat(b.dataset.v) - s.sim.speed) < 1e-6); });
    }
  }

  // ------------------------------------------------------------------------ cards
  function renderCards() {
    var box = $("cards"), ids = nonAnchors(), t = now(), fixes = 0;
    if (box.children.length !== ids.length || ids.some(function (id, i) { return box.children[i].dataset.id !== id; })) {
      box.textContent = "";
      ids.forEach(function (id) {
        var c = el("div", "card"); c.dataset.id = id;
        var top = el("div", "top");
        var dot = el("span", "dot"); dot.style.background = colorOf(id);
        top.appendChild(dot); top.appendChild(el("span", "id", id));
        top.appendChild(el("span", "label", (S.hello.nodes[id] || {}).label || ""));
        var rel = el("button", "small hide", "release"); rel.dataset.rel = id; top.appendChild(rel);
        top.appendChild(el("span", "src", "-"));
        c.appendChild(top);
        var q = el("div", "qbar"); q.appendChild(el("i")); c.appendChild(q);
        var kv = el("div", "kv");
        ["quality", "ranges", "speed", "age", "x", "y", "z", "sigma"].forEach(function (k) {
          var d = el("div"); d.appendChild(el("span", "", k)); var b = el("b", "", "-"); b.dataset.k = k; d.appendChild(b); kv.appendChild(d);
        });
        c.appendChild(kv); box.appendChild(c);
      });
    }
    ids.forEach(function (id, i) {
      var c = box.children[i], p = S.pos[id], set = function (k, v) { c.querySelector('[data-k="' + k + '"]').textContent = v; };
      var age = p ? t - p._rx : null, stale = !p || age > 1.5;
      c.classList.toggle("stale", stale);
      c.querySelector("[data-rel]").classList.toggle("hide", !S.held[id]);
      if (!p) { c.querySelector(".src").textContent = "no fix"; c.querySelector(".qbar i").style.width = "0%"; return; }
      if (!stale) fixes++;
      c.querySelector(".src").textContent = p.src || "-";
      var qi = c.querySelector(".qbar i");
      qi.style.width = Math.round(Math.max(0, Math.min(1, p.q || 0)) * 100) + "%";
      qi.style.background = p.q < 0.3 ? "var(--bad)" : p.q < 0.55 ? "var(--warn)" : "var(--accent)";
      set("quality", fmt(p.q, 2)); set("ranges", p.n != null ? p.n : "-");
      set("speed", fmt(p.speed, 2) + " m/s"); set("age", fmt(age, 1) + " s");
      set("x", fmt(p.x, 2)); set("y", fmt(p.y, 2)); set("z", fmt(p.z, 2));
      var cov = p.cov || [];
      set("sigma", cov.length === 3 ? fmt(Math.sqrt(Math.max(0, (cov[0] + cov[2]) / 2)) * 100, 0) + " cm" : "-");
    });
    $("n-fix").textContent = ids.length ? fixes + "/" + ids.length : "";
  }

  // ------------------------------------------------------------------------ links
  function renderLinks() {
    var keys = Object.keys(S.ranges).sort(), t = now(), tb = $("links"), d = (S.diag && S.diag.links) || {};
    if (tb.children.length !== keys.length) {
      tb.textContent = "";
      keys.forEach(function () { var tr = el("tr"); for (var i = 0; i < 8; i++) tr.appendChild(el("td")); tb.appendChild(tr); });
    }
    keys.forEach(function (k, i) {
      var r = S.ranges[k], tr = tb.children[i], td = tr.children, g = d[k] || {}, age = t - r._rx;
      tr.className = age > 1.5 ? "stale" : "";
      td[0].textContent = k; td[1].textContent = fmt(r.d, 2);
      td[2].textContent = fmt(g.rate_hz, 1); td[3].textContent = fmt(g.d_std, 2);
      td[4].textContent = fmt(r.rssi, 0); td[5].textContent = fmt(r.fp, 0);
      td[6].textContent = fmt(r.los, 2); td[6].className = r.los != null && r.los < 0.4 ? "nlos" : "";
      td[7].textContent = fmt(age, 1); td[7].className = age > 1.5 ? "bad" : "";
    });
    $("n-links").textContent = keys.length || "";
  }

  function renderEvents() {
    var box = $("events");
    box.textContent = "";
    if (!S.events.length) { box.appendChild(el("div", "", "none yet")); return; }
    S.events.forEach(function (e) {
      var row = el("div");
      var d = new Date((e.t || 0) * 1000);
      row.appendChild(el("span", "t", d.toTimeString().slice(0, 8)));
      row.appendChild(el("span", "", e.id || ""));
      row.appendChild(el("span", "n", e.name || ""));
      var data = e.data && Object.keys(e.data).length ? JSON.stringify(e.data) : "";
      row.appendChild(el("span", "d", data));
      box.appendChild(row);
    });
  }

  // ---------------------------------------------------------------------- anchors
  function anchorIds() {
    var ids = S.hello ? Object.keys(S.hello.nodes).filter(function (id) { return S.hello.nodes[id].role === "anchor"; }) : [];
    Object.keys(S.anchors).forEach(function (id) { if (ids.indexOf(id) < 0) ids.push(id); });
    return ids.sort();
  }
  function buildAnchorForm() {
    var g = $("agrid"), ids = anchorIds();
    if (document.activeElement && g.contains(document.activeElement)) return;   // do not fight the user's typing
    g.textContent = "";
    ["", "x", "y", "z"].forEach(function (h) { g.appendChild(el("span", "hd", h)); });
    ids.forEach(function (id) {
      g.appendChild(el("label", "", id));
      ["x", "y", "z"].forEach(function (ax) {
        var inp = el("input"); inp.inputMode = "decimal"; inp.dataset.id = id; inp.dataset.ax = ax;
        inp.setAttribute("aria-label", id + " " + ax);
        var a = S.anchors[id];
        inp.value = a ? String(a[ax]) : "";
        g.appendChild(inp);
      });
    });
    $("sec-anchors").parentNode.classList.toggle("hide", S.mesh);
  }
  function updateOverlay() {
    var o = $("overlay");
    var need = S.hello && !S.mesh && !Object.keys(S.anchors).length;
    o.classList.toggle("hide", !need);
    if (need) {
      o.textContent = "";
      o.appendChild(el("h3", "", "Anchors are not placed yet"));
      o.appendChild(el("p", "", "Ranges are arriving, but the hub cannot solve positions until it knows where the anchors are."));
      var b = el("button", "primary", "Set anchor positions");
      b.onclick = function () { $("sec-anchors").open = true; $("sec-anchors").scrollIntoView({ behavior: "smooth", block: "center" }); };
      o.appendChild(b);
      $("sec-anchors").open = true;
    }
  }
  $("rfill").onclick = function () {
    var w = parseFloat($("rw").value), d = parseFloat($("rd").value), z = parseFloat($("rz").value);
    if (!isFinite(w) || !isFinite(d)) { note("cal-note", "enter width and depth in metres", "err"); return; }
    if (!isFinite(z)) z = 2.0;
    var rect = { A1: [0, 0], A2: [w, 0], A3: [w, d], A4: [0, d] };
    document.querySelectorAll("#agrid input").forEach(function (inp) {
      var r = rect[inp.dataset.id];
      if (r) inp.value = inp.dataset.ax === "x" ? r[0] : inp.dataset.ax === "y" ? r[1] : z;
    });
  };
  $("aset").onclick = function () {
    var out = {}, bad = false;
    document.querySelectorAll("#agrid input").forEach(function (inp) {
      var v = parseFloat(inp.value);
      if (inp.value.trim() === "" && inp.dataset.ax !== "z") { bad = true; return; }
      (out[inp.dataset.id] = out[inp.dataset.id] || {})[inp.dataset.ax] = isFinite(v) ? v : 0;
    });
    if (bad || !Object.keys(out).length) { note("cal-note", "fill x and y for every anchor", "err"); return; }
    kit.setAnchors(out).then(function () { note("cal-note", "coordinates applied", "ok"); },
      function (e) { note("cal-note", e.message, "err"); });
  };
  $("autocal").onclick = function () {
    note("cal-note", "anchors are ranging each other ...", "");
    kit.calibrate({}).catch(function (e) { note("cal-note", e.message, "err"); });
  };

  // -------------------------------------------------------------------- simulator
  function loadScenarios(h) {
    kit.get("/sim/scenarios").then(function (r) {
      S.scenarios = r;
      var sel = $("scenario"), cur = (h.sim && h.sim.scenario) || r.current;
      sel.textContent = "";
      (r.scenarios || []).forEach(function (n) {
        var o = el("option", "", n + "  (" + ((r.details[n] || {}).profile || "") + ")"); o.value = n; sel.appendChild(o);
      });
      if (cur && (r.scenarios || []).indexOf(cur) < 0) { var o = el("option", "", cur + "  (custom)"); o.value = cur; sel.appendChild(o); }
      sel.value = cur || "";
      $("scen-desc").textContent = (h.sim && h.sim.description) || ((r.details[cur] || {}).description) || "";
    }).catch(function () {});
  }
  $("scenario").onchange = function () {
    kit.setScenario(this.value).catch(function (e) { $("scen-desc").textContent = e.message; });
  };
  $("pause").onclick = function () {
    var paused = S.status && S.status.sim && S.status.sim.paused;
    kit.simControl(paused ? "resume" : "pause").then(function () { if (S.status && S.status.sim) { S.status.sim.paused = !paused; renderHeader(); } });
  };
  $("restart").onclick = function () { S.trails = {}; S.held = {}; kit.simControl("restart"); };
  document.querySelectorAll(".spd").forEach(function (b) {
    b.onclick = function () { kit.simControl("speed", parseFloat(b.dataset.v)); };
  });
  $("cards").addEventListener("click", function (ev) {
    var id = ev.target && ev.target.dataset && ev.target.dataset.rel;
    if (id) { kit.release(id); delete S.held[id]; }
  });

  // ----------------------------------------------------------------------- record
  $("rec").onclick = function () {
    var recording = S.status && S.status.recording;
    if (recording) {
      kit.record("stop").then(function (r) {
        note("rec-note", "saved " + r.messages + " messages to " + r.path, "ok");
      }, function (e) { note("rec-note", e.message, "err"); });
    } else {
      var d = new Date(), p = function (n) { return (n < 10 ? "0" : "") + n; };
      var name = "console-" + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + "-" + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds()) + ".ndjson";
      kit.record("start", name).then(function (r) {
        note("rec-note", "recording to " + r.path + "  (replay: uwbkit hub --source replay:<file>)", "");
        if (S.status) { S.status.recording = r.path; renderHeader(); }
      }, function (e) { note("rec-note", e.message, "err"); });
    }
  };

  // ------------------------------------------------------------------------- map
  $("mode").onclick = function () {
    S.mode = S.mode === "lines" ? "circles" : S.mode === "circles" ? "off" : "lines";
    this.textContent = "Ranges: " + S.mode; S.dirty = true;
  };
  $("trails").onclick = function () { S.showTrails = !S.showTrails; this.textContent = "Trails: " + (S.showTrails ? "on" : "off"); S.dirty = true; };

  function resize() {
    var r = wrap.getBoundingClientRect();
    dpr = Math.min(2.5, window.devicePixelRatio || 1);
    CW = Math.max(50, r.width); CH = Math.max(50, r.height);
    canvas.width = Math.round(CW * dpr); canvas.height = Math.round(CH * dpr);
    S.dirty = true;
  }
  if (window.ResizeObserver) new ResizeObserver(resize).observe(wrap); else window.addEventListener("resize", resize);
  resize();

  function targetBounds() {
    var pts = [];
    if (!S.mesh) (S.room.outline || []).forEach(function (p) { pts.push(p); });
    Object.keys(S.anchors).forEach(function (k) { pts.push([S.anchors[k].x, S.anchors[k].y]); });
    Object.keys(S.pos).forEach(function (k) { pts.push([S.pos[k].x, S.pos[k].y]); });
    if (S.drag) pts.push([S.drag.x, S.drag.y]);
    if (!pts.length) return { minx: -1, maxx: 5, miny: -1, maxy: 4 };
    var b = { minx: Infinity, maxx: -Infinity, miny: Infinity, maxy: -Infinity };
    pts.forEach(function (p) { b.minx = Math.min(b.minx, p[0]); b.maxx = Math.max(b.maxx, p[0]); b.miny = Math.min(b.miny, p[1]); b.maxy = Math.max(b.maxy, p[1]); });
    var m = Math.max(0.35, 0.08 * Math.max(b.maxx - b.minx, b.maxy - b.miny));
    if (b.maxx - b.minx < 1) { b.minx -= 0.5; b.maxx += 0.5; }
    if (b.maxy - b.miny < 1) { b.miny -= 0.5; b.maxy += 0.5; }
    return { minx: b.minx - m, maxx: b.maxx + m, miny: b.miny - m, maxy: b.maxy + m };
  }
  function updateTransform() {
    var t = targetBounds(), b = S.bounds;
    if (!b) b = S.bounds = t;
    else {
      // grow at once when something leaves the view, shrink slowly (no jitter in mesh mode)
      ["minx", "miny"].forEach(function (k) { b[k] = t[k] < b[k] ? t[k] : b[k] + (t[k] - b[k]) * 0.02; });
      ["maxx", "maxy"].forEach(function (k) { b[k] = t[k] > b[k] ? t[k] : b[k] + (t[k] - b[k]) * 0.02; });
    }
    var padX = CW < 520 ? 22 : 46, padTop = 52, padBot = 40;
    var k = Math.min((CW - 2 * padX) / (b.maxx - b.minx), (CH - padTop - padBot) / (b.maxy - b.miny));
    T.k = k; T.minx = b.minx; T.maxy = b.maxy;
    T.ox = (CW - k * (b.maxx - b.minx)) / 2;
    T.oy = padTop + ((CH - padTop - padBot) - k * (b.maxy - b.miny)) / 2;
  }
  function X(x) { return T.ox + (x - T.minx) * T.k; }
  function Y(y) { return T.oy + (T.maxy - y) * T.k; }
  function invX(sx) { return T.minx + (sx - T.ox) / T.k; }
  function invY(sy) { return T.maxy - (sy - T.oy) / T.k; }

  function losColor(los, alpha) {
    if (los == null || los >= 0.7) return "rgba(53,208,186," + alpha + ")";
    if (los >= 0.4) return "rgba(245,165,36," + alpha + ")";
    return "rgba(255,107,107," + alpha + ")";
  }

  function draw() {
    updateTransform();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, CW, CH);
    var b = S.bounds, t = now(), i, k;
    // grid (1 m; 0.1 m on table-sized scenes)
    var span = Math.max(b.maxx - b.minx, b.maxy - b.miny), step = span < 2.5 ? 0.1 : span > 30 ? 5 : 1;
    ctx.lineWidth = 1; ctx.strokeStyle = "rgba(120,150,180,0.11)"; ctx.beginPath();
    for (var gx = Math.ceil(b.minx / step) * step; gx <= b.maxx; gx += step) { ctx.moveTo(Math.round(X(gx)) + 0.5, 0); ctx.lineTo(Math.round(X(gx)) + 0.5, CH); }
    for (var gy = Math.ceil(b.miny / step) * step; gy <= b.maxy; gy += step) { ctx.moveTo(0, Math.round(Y(gy)) + 0.5); ctx.lineTo(CW, Math.round(Y(gy)) + 0.5); }
    ctx.stroke();
    // room (scenario coordinates: not drawn in the relative mesh frame)
    var room = S.mesh ? {} : S.room, out = room.outline || [];
    if (out.length > 2) {
      ctx.beginPath();
      out.forEach(function (p, j) { if (j) ctx.lineTo(X(p[0]), Y(p[1])); else ctx.moveTo(X(p[0]), Y(p[1])); });
      ctx.closePath(); ctx.fillStyle = "rgba(130,160,190,0.045)"; ctx.fill();
      ctx.strokeStyle = "rgba(160,185,210,0.55)"; ctx.lineWidth = 1.5; ctx.stroke();
    }
    (room.walls || []).forEach(function (w) {
      ctx.beginPath(); ctx.moveTo(X(w.a[0]), Y(w.a[1])); ctx.lineTo(X(w.b[0]), Y(w.b[1]));
      ctx.strokeStyle = "rgba(200,215,230,0.8)"; ctx.lineWidth = 4; ctx.lineCap = "round"; ctx.stroke();
    });
    ctx.font = "11px " + getComputedStyle(document.body).getPropertyValue("--sans");
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    (room.labels || []).forEach(function (l) {
      if (!l.at) return;
      ctx.fillStyle = "rgba(127,142,160,0.75)"; ctx.fillText(String(l.name || ""), X(l.at[0]), Y(l.at[1]));
    });
    var mono = "11px " + getComputedStyle(document.body).getPropertyValue("--mono");

    // ranges
    if (S.mode !== "off") {
      Object.keys(S.ranges).forEach(function (key) {
        var r = S.ranges[key], age = t - r._rx;
        if (age > 1.5) return;
        var fade = Math.max(0, 1 - age / 1.5);
        var pa = S.pos[r.a] || S.anchors[r.a], pb = S.pos[r.b] || S.anchors[r.b];
        var an = S.anchors[r.b] ? r.b : S.anchors[r.a] ? r.a : null;
        if (S.mode === "circles" && an) {
          var other = an === r.b ? r.a : r.b, A = S.anchors[an], hz = (S.pos[other] && S.pos[other].z != null) ? S.pos[other].z : 1.0;
          var dz = (A.z || 0) - hz, horiz = Math.sqrt(Math.max(0, r.d * r.d - dz * dz));
          ctx.beginPath(); ctx.arc(X(A.x), Y(A.y), horiz * T.k, 0, 6.2832);
          ctx.strokeStyle = losColor(r.los, 0.34 * fade); ctx.lineWidth = 1.2;
          ctx.setLineDash(r.los != null && r.los < 0.4 ? [5, 5] : []); ctx.stroke(); ctx.setLineDash([]);
        } else if (pa && pb) {
          ctx.beginPath(); ctx.moveTo(X(pa.x), Y(pa.y)); ctx.lineTo(X(pb.x), Y(pb.y));
          ctx.strokeStyle = losColor(r.los, (S.mesh ? 0.5 : 0.42) * fade); ctx.lineWidth = 1.2;
          ctx.setLineDash(r.los != null && r.los < 0.4 ? [5, 5] : []); ctx.stroke(); ctx.setLineDash([]);
          if (CW > 520 && T.k * r.d > 70) {
            ctx.font = mono; ctx.fillStyle = losColor(r.los, 0.8 * fade);
            ctx.fillText(r.d.toFixed(2), (X(pa.x) + X(pb.x)) / 2, (Y(pa.y) + Y(pb.y)) / 2 - 7);
          }
        }
      });
    }
    // anchors
    Object.keys(S.anchors).forEach(function (id) {
      var a = S.anchors[id], x = X(a.x), y = Y(a.y);
      var online = !S.status || (S.status.nodes_online || []).indexOf(id) >= 0;
      ctx.save(); ctx.translate(x, y); ctx.rotate(Math.PI / 4);
      ctx.fillStyle = online ? "#dbe4ee" : "#56657a"; ctx.fillRect(-5, -5, 10, 10);
      ctx.strokeStyle = "#0a0e13"; ctx.lineWidth = 2; ctx.strokeRect(-5, -5, 10, 10); ctx.restore();
      ctx.font = "600 " + mono; ctx.fillStyle = online ? "#dbe4ee" : "#56657a";
      var lx = x + (x > CW / 2 ? -1 : 1) * 16, ly = y + (y > CH / 2 ? 1 : -1) * 14;
      ctx.fillText(id + (S.dims === 3 ? " z" + fmt(a.z, 1) : ""), lx, ly);
    });
    // trails
    if (S.showTrails) {
      Object.keys(S.trails).forEach(function (id) {
        var tr = S.trails[id], c = colorOf(id);
        while (tr.length && t - tr[0][2] > 14) tr.shift();
        ctx.lineWidth = 2; ctx.lineCap = "round"; ctx.strokeStyle = c;
        for (i = 1; i < tr.length; i++) {
          var al = Math.max(0, 1 - (t - tr[i][2]) / 14);
          if (Math.hypot(tr[i][0] - tr[i - 1][0], tr[i][1] - tr[i - 1][1]) > 1.2) continue;   // teleport: no streak
          ctx.globalAlpha = 0.55 * al * al;
          ctx.beginPath(); ctx.moveTo(X(tr[i - 1][0]), Y(tr[i - 1][1])); ctx.lineTo(X(tr[i][0]), Y(tr[i][1])); ctx.stroke();
        }
        ctx.globalAlpha = 1;
      });
    }
    // nodes
    nonAnchors().forEach(function (id) {
      var p = S.pos[id];
      if (!p) return;
      var x = X(p.x), y = Y(p.y), c = colorOf(id), age = t - p._rx, stale = age > 1.5;
      ctx.globalAlpha = stale ? 0.35 : 1;
      var cov = p.cov;
      if (cov && cov.length === 3 && !stale) {
        var tr2 = (cov[0] + cov[2]) / 2, det = Math.sqrt(Math.max(0, tr2 * tr2 - (cov[0] * cov[2] - cov[1] * cov[1])));
        var l1 = tr2 + det, l2 = Math.max(0, tr2 - det);
        var ang = Math.abs(cov[1]) < 1e-9 ? (cov[0] >= cov[2] ? 0 : Math.PI / 2) : Math.atan2(l1 - cov[0], cov[1]);
        ctx.beginPath();
        ctx.ellipse(x, y, Math.max(3, 3 * Math.sqrt(l1) * T.k), Math.max(3, 3 * Math.sqrt(l2) * T.k), -ang, 0, 6.2832);
        ctx.fillStyle = c; ctx.globalAlpha = 0.13; ctx.fill(); ctx.globalAlpha = 0.55; ctx.strokeStyle = c; ctx.lineWidth = 1; ctx.stroke();
        ctx.globalAlpha = 1;
      }
      if (p.speed > 0.15 && p.heading != null && !stale) {
        var len = Math.min(46, 12 + p.speed * 22), hx = Math.cos(p.heading), hy = -Math.sin(p.heading);
        ctx.strokeStyle = c; ctx.lineWidth = 2; ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(x + hx * 9, y + hy * 9); ctx.lineTo(x + hx * len, y + hy * len); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x + hx * len, y + hy * len);
        ctx.lineTo(x + hx * (len - 7) - hy * 4, y + hy * (len - 7) + hx * 4);
        ctx.lineTo(x + hx * (len - 7) + hy * 4, y + hy * (len - 7) - hx * 4); ctx.closePath(); ctx.fillStyle = c; ctx.fill();
      }
      ctx.beginPath(); ctx.arc(x, y, 7, 0, 6.2832); ctx.fillStyle = c; ctx.fill();
      ctx.lineWidth = 2.5; ctx.strokeStyle = "#0a0e13"; ctx.stroke();
      if (S.held[id]) { ctx.beginPath(); ctx.arc(x, y, 12, 0, 6.2832); ctx.setLineDash([3, 3]); ctx.strokeStyle = c; ctx.lineWidth = 1.5; ctx.stroke(); ctx.setLineDash([]); }
      ctx.font = "600 12px " + getComputedStyle(document.body).getPropertyValue("--mono"); ctx.fillStyle = c;
      var label = id + (S.dims === 3 ? "  z " + fmt(p.z, 2) : "");
      ctx.fillText(label, x, y - 17);
      ctx.globalAlpha = 1;
    });
    if (S.drag) {
      ctx.beginPath(); ctx.arc(X(S.drag.x), Y(S.drag.y), 15, 0, 6.2832);
      ctx.strokeStyle = colorOf(S.drag.id); ctx.lineWidth = 2; ctx.stroke();
    }
    // scale bar
    var bar = span < 2.5 ? 0.25 : span > 30 ? 5 : 1, bx = CW - (CW < 520 ? 16 : 24) - bar * T.k, by = CH - 22;
    if (bar * T.k > 24 && bar * T.k < CW * 0.6) {
      ctx.strokeStyle = "rgba(219,228,238,0.7)"; ctx.lineWidth = 1.5; ctx.beginPath();
      ctx.moveTo(bx, by - 4); ctx.lineTo(bx, by); ctx.lineTo(bx + bar * T.k, by); ctx.lineTo(bx + bar * T.k, by - 4); ctx.stroke();
      ctx.font = mono; ctx.fillStyle = "rgba(219,228,238,0.7)"; ctx.fillText(bar + " m", bx + bar * T.k / 2, by - 10);
    }
    if (S.mesh) {
      ctx.font = mono; ctx.textAlign = "left"; ctx.fillStyle = "rgba(127,142,160,0.9)";
      ctx.fillText("relative frame: N1 at the origin, N2 on +x", CW < 520 ? 12 : 16, CH - 16); ctx.textAlign = "center";
    }
  }

  // drag to teleport (sim only)
  function pointerPos(ev) { var r = canvas.getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; }
  var lastSend = 0;
  canvas.addEventListener("pointerdown", function (ev) {
    if (!S.canTeleport) return;
    var p = pointerPos(ev), best = null, bd = 30;
    nonAnchors().forEach(function (id) {
      var q = S.pos[id]; if (!q) return;
      var d = Math.hypot(X(q.x) - p[0], Y(q.y) - p[1]);
      if (d < bd) { bd = d; best = id; }
    });
    if (!best) return;
    ev.preventDefault();
    canvas.setPointerCapture(ev.pointerId);
    canvas.classList.add("grabbing");
    S.drag = { id: best, x: invX(p[0]), y: invY(p[1]) };
    S.held[best] = true;
  });
  canvas.addEventListener("pointermove", function (ev) {
    if (!S.drag) return;
    var p = pointerPos(ev);
    S.drag.x = invX(p[0]); S.drag.y = invY(p[1]); S.dirty = true;
    var t = Date.now();
    if (t - lastSend > 66) { lastSend = t; kit.teleport(S.drag.id, +S.drag.x.toFixed(3), +S.drag.y.toFixed(3)).catch(function () {}); }
  });
  function endDrag(ev) {
    if (!S.drag) return;
    var d = S.drag; S.drag = null; canvas.classList.remove("grabbing");
    kit.teleport(d.id, +d.x.toFixed(3), +d.y.toFixed(3)).catch(function () {});
    S.dirty = true;
  }
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);

  // ------------------------------------------------------------------------ loops
  var lastDraw = 0;
  function frame(ts) {
    requestAnimationFrame(frame);
    if (ts - lastDraw < 33) return;
    lastDraw = ts;
    try { draw(); } catch (e) { if (window.console) console.error(e); }
  }
  requestAnimationFrame(frame);
  setInterval(function () { renderCards(); renderLinks(); setBadge(); }, 250);
  function pollDiag() {
    if (!kit.connected) return;
    kit.diag().then(function (d) {
      S.diag = d;
      var logs = (d.logs || []).map(function (l) { return l.text; });
      $("log").textContent = logs.length ? logs.slice(-30).join("\n") : "no lines yet";
    }).catch(function () {});
  }
  setInterval(pollDiag, 2000);
  setTimeout(pollDiag, 600);
  renderEvents();
  window.uwbConsole = { state: S, kit: kit };   // for debugging from the browser console
})();
