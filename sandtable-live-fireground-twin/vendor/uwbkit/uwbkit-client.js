/*! uwbkit-client.js — single file, no dependencies. Browser (<script src> gives a global `UwbKit`)
 *  and Node >= 22 (`const UwbKit = require("./uwbkit-client.js")`, or `import UwbKit from ...`).
 *
 *    const kit = new UwbKit("ws://localhost:1800/stream");
 *    kit.on("hello", h => showSimBadge(h.simulated));
 *    kit.on("pos", p => draw(p));
 *
 *  Wire format: uwb-kit INTERFACE.md v1. Unknown message types and fields are passed through.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
    module.exports.UwbKit = module.exports;
    module.exports.default = module.exports;
  } else {
    root.UwbKit = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var DEFAULT_URL = "ws://127.0.0.1:1800/stream";
  var isBrowser = typeof window !== "undefined" && typeof document !== "undefined";

  function defaultUrl() {
    if (isBrowser) {
      if (window.UWBKIT_URL) return String(window.UWBKIT_URL);
      var loc = window.location;
      var secure = loc.protocol === "https:";
      var meta = document.querySelector('meta[name="uwbkit-hub"]');
      if (meta && meta.content === "same-origin") return (secure ? "wss://" : "ws://") + loc.host + "/stream";
      if (loc.hostname) return (secure ? "wss://" : "ws://") + loc.hostname + ":1800/stream";
      return DEFAULT_URL;
    }
    if (typeof process !== "undefined" && process.env && process.env.UWBKIT_URL) return process.env.UWBKIT_URL;
    return DEFAULT_URL;
  }

  /** Normalise "host:port", "http://host:port", "ws://host:port" to a ws(s)://.../stream URL. */
  function normalise(url) {
    url = String(url || defaultUrl()).trim();
    if (url.indexOf("://") < 0) url = "ws://" + url;
    url = url.replace(/^http(s?):\/\//i, "ws$1://");
    var m = /^(wss?:\/\/[^\/?#]+)([^?#]*)(.*)$/i.exec(url);
    if (!m) return url;
    var path = m[2];
    if (path === "" || path === "/") path = "/stream";
    return m[1] + path + m[3];
  }

  function httpBase(wsUrl) {
    var m = /^ws(s?):\/\/([^\/?#]+)/i.exec(wsUrl);
    return m ? "http" + m[1] + "://" + m[2] : "http://127.0.0.1:1800";
  }

  function UwbKit(url, opts) {
    if (!(this instanceof UwbKit)) return new UwbKit(url, opts);
    opts = opts || {};
    this.url = normalise(url);
    this.http = opts.http || httpBase(this.url);
    this.reconnect = opts.reconnect !== false;
    this.hello = null;
    this.anchors = null;
    this.simulated = null;
    this.status = null;
    this.pos = {};
    this.ranges = {};
    this.connected = false;
    this.transport = null;
    this._handlers = {};
    this._closed = false;
    this._backoff = 200;
    this._fails = 0;
    this._everOpen = false;
    this._ws = null;
    this._es = null;
    this._timer = null;
    this._WS = opts.WebSocket || (typeof WebSocket !== "undefined" ? WebSocket : null);
    this._forceSse = opts.transport === "sse";
    if (opts.autoConnect !== false) this.connect();
  }

  UwbKit.DEFAULT_URL = DEFAULT_URL;
  UwbKit.version = "1.0.0";

  UwbKit.prototype.on = function (type, fn) {
    (this._handlers[type] = this._handlers[type] || []).push(fn);
    if (type === "hello" && this.hello) { try { fn(this.hello); } catch (e) { console.error(e); } }
    if (type === "anchors" && this.anchors) { try { fn(this.anchors); } catch (e) { console.error(e); } }
    return this;
  };

  UwbKit.prototype.off = function (type, fn) {
    var list = this._handlers[type];
    if (!list) return this;
    this._handlers[type] = fn ? list.filter(function (f) { return f !== fn; }) : [];
    return this;
  };

  UwbKit.prototype._emit = function (type, arg) {
    var list = this._handlers[type];
    if (!list) return;
    for (var i = 0; i < list.length; i++) {
      try { list[i](arg); } catch (e) { if (typeof console !== "undefined") console.error("uwbkit handler error", e); }
    }
  };

  UwbKit.prototype._handle = function (text) {
    var msg;
    try { msg = JSON.parse(text); } catch (e) { return; }
    if (!msg || typeof msg !== "object" || typeof msg.type !== "string") return;
    switch (msg.type) {
      case "hello": this.hello = msg; this.simulated = !!msg.simulated; this.pos = {}; this.ranges = {}; break;
      case "anchors": this.anchors = msg; break;
      case "pos": this.pos[msg.id] = msg; break;
      case "range": this.ranges[msg.a + "-" + msg.b] = msg; break;
      case "status": this.status = msg; break;
    }
    this._emit(msg.type, msg);
    this._emit("*", msg);
  };

  UwbKit.prototype.connect = function () {
    var self = this;
    if (this._closed) return this;
    var canSse = typeof EventSource !== "undefined";
    if ((this._forceSse || !this._WS || (this._fails >= 2 && !this._everOpen)) && canSse) return this._connectSse();
    if (!this._WS) {
      this._emit("error", new Error("no WebSocket implementation (Node >= 22 or pass opts.WebSocket)"));
      return this;
    }
    var ws;
    try { ws = new this._WS(this.url); } catch (e) { this._emit("error", e); this._retry(); return this; }
    this._ws = ws;
    this.transport = "ws";
    ws.onopen = function () {
      self.connected = true; self._everOpen = true; self._fails = 0; self._backoff = 200;
      self._emit("open", { transport: "ws", url: self.url });
    };
    ws.onmessage = function (ev) { if (typeof ev.data === "string") self._handle(ev.data); };
    ws.onerror = function (ev) { self._emit("error", ev); };
    ws.onclose = function () {
      if (self._ws !== ws) return;
      var was = self.connected;
      self.connected = false; self._ws = null;
      if (!was) self._fails++;
      self._emit("close", { transport: "ws" });
      self._retry();
    };
    return this;
  };

  UwbKit.prototype._connectSse = function () {
    var self = this;
    var es = new EventSource(this.http + "/events");
    this._es = es;
    this.transport = "sse";
    es.onopen = function () {
      self.connected = true; self._backoff = 200;
      self._emit("open", { transport: "sse", url: self.http + "/events" });
    };
    es.onmessage = function (ev) { self._handle(ev.data); };
    es.onerror = function (ev) {
      if (self._es !== es) return;
      es.close();
      self._es = null;
      var was = self.connected;
      self.connected = false;
      self._emit("error", ev);
      if (was) self._emit("close", { transport: "sse" });
      self._fails = 0;            // give WebSocket another chance next time
      self._everOpen = false;
      self._retry();
    };
    return this;
  };

  UwbKit.prototype._retry = function () {
    var self = this;
    if (this._closed || !this.reconnect) return;
    clearTimeout(this._timer);
    this._timer = setTimeout(function () { self.connect(); }, this._backoff);
    this._backoff = Math.min(2000, Math.round(this._backoff * 1.8));
  };

  UwbKit.prototype.close = function () {
    this._closed = true;
    clearTimeout(this._timer);
    if (this._ws) { var ws = this._ws; this._ws = null; try { ws.close(); } catch (e) { /* ignore */ } }
    if (this._es) { this._es.close(); this._es = null; }
    if (this.connected) { this.connected = false; this._emit("close", { transport: this.transport }); }
  };

  // ---- REST helpers (promises) -------------------------------------------------
  UwbKit.prototype._fetch = function (method, path, body) {
    var init = { method: method };
    if (body !== undefined) { init.headers = { "Content-Type": "application/json" }; init.body = JSON.stringify(body); }
    return fetch(this.http + path, init).then(function (r) {
      return r.text().then(function (t) {
        var data = null;
        try { data = t ? JSON.parse(t) : null; } catch (e) { data = t; }
        if (!r.ok) {
          var err = new Error("uwbkit " + method + " " + path + " -> HTTP " + r.status + ": " +
            (data && data.error ? data.error : t));
          err.status = r.status; err.body = data;
          throw err;
        }
        return data;
      });
    });
  };
  UwbKit.prototype.get = function (path) { return this._fetch("GET", path); };
  UwbKit.prototype.post = function (path, body) { return this._fetch("POST", path, body === undefined ? {} : body); };
  UwbKit.prototype.state = function () { return this.get("/state"); };
  UwbKit.prototype.diag = function () { return this.get("/diag"); };
  UwbKit.prototype.scenarios = function () {
    return this.get("/sim/scenarios").then(function (r) { return r && r.scenarios ? r.scenarios : r; });
  };
  UwbKit.prototype.setScenario = function (nameOrObject) {
    return this.post("/sim/scenario", typeof nameOrObject === "string" ? { name: nameOrObject } : { scenario: nameOrObject });
  };
  UwbKit.prototype.simControl = function (cmd, value) {
    var b = { cmd: cmd }; if (value !== undefined) b.value = value;
    return this.post("/sim/control", b);
  };
  UwbKit.prototype.teleport = function (id, x, y, z) {
    var b = { id: id, x: x, y: y }; if (z !== undefined) b.z = z;
    return this.post("/sim/teleport", b);
  };
  UwbKit.prototype.release = function (id) { return this.post("/sim/teleport", { id: id, release: true }); };
  UwbKit.prototype.record = function (cmd, path) {
    var b = { cmd: cmd }; if (path) b.path = path;
    return this.post("/record", b);
  };
  UwbKit.prototype.calibrate = function (options) { return this.post("/calibrate", options || {}); };
  UwbKit.prototype.setAnchors = function (anchors) {
    return this.post("/anchors", anchors && anchors.anchors ? anchors
      : { type: "anchors", frame: "site", anchors: anchors, calibrated: "manual" });
  };

  // ---- SIM badge: every UI that shows kit data must show one while hello.simulated is true ----
  /** Create (or reuse) a visible SIM badge and return a function `show(simulated)`. */
  UwbKit.simBadge = function (element) {
    if (!isBrowser) return function () {};
    var el = element || document.getElementById("uwbkit-sim-badge");
    if (!el) {
      el = document.createElement("div");
      el.id = "uwbkit-sim-badge";
      el.textContent = "SIM";
      el.title = "Simulated UWB data (uwb-kit simulator), not real boards";
      el.setAttribute("role", "status");
      el.style.cssText = "position:fixed;top:10px;right:10px;z-index:99999;padding:3px 10px;border-radius:6px;" +
        "background:#f5a524;color:#1a1200;font:700 13px/1.4 system-ui,sans-serif;letter-spacing:.08em;" +
        "box-shadow:0 1px 4px rgba(0,0,0,.35);display:none";
      (document.body || document.documentElement).appendChild(el);
    }
    return function (simulated) { el.style.display = simulated ? "" : "none"; };
  };
  /** Wire a SIM badge to this connection: shown while hello.simulated is true. */
  UwbKit.prototype.simBadge = function (element) {
    var show = UwbKit.simBadge(element);
    this.on("hello", function (h) { show(!!h.simulated); });
    return this;
  };

  return UwbKit;
});
