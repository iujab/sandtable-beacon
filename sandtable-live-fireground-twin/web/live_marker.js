// "LIVE SPONSOR DATA, PRIVATE": a banner at the top of a page whenever any sponsor layer of the server's bundle is live,
// so that a screenshot of a live page can never be mistaken for a repository figure. Never shown in fixture mode.
// Used by report.html and mp.html (one script tag); the board (app.js) and crew.html call liveMarker() themselves.
(function () {
  "use strict";
  function liveMarker(isLive, where) {
    const old = document.getElementById("live-private");
    if (!isLive) { if (old) old.remove(); return; }
    if (old) return;
    const el = document.createElement("div");
    el.id = "live-private";
    el.setAttribute("role", "note");
    el.textContent = "LIVE SPONSOR DATA, PRIVATE";
    el.title = "This page shows live CoStar, Matterport or Vexcel data: do not publish screenshots of it";
    el.style.cssText = [
      "background:#c4003c", "color:#fff", "font:700 13px/1 'Barlow Condensed','Arial Narrow',Arial,sans-serif", "letter-spacing:.18em",
      "text-align:center", "padding:5px 8px 4px", "z-index:9999", "-webkit-print-color-adjust:exact", "print-color-adjust:exact",
      where === "overlay" ? "position:fixed;left:0;right:0;top:0" : "position:relative",
    ].join(";");
    document.body.insertBefore(el, document.body.firstChild);
  }
  window.liveMarker = liveMarker;
  if (document.currentScript && document.currentScript.dataset.auto !== undefined) {
    const where = document.currentScript.dataset.auto || "flow";
    fetch("/api/bundle", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).then((b) => {
      const labels = (b && b.labels) || {};
      liveMarker(["costar", "matterport", "vexcel"].some((k) => labels[k] && labels[k].mode === "live"), where);
    }).catch(() => {});
  }
})();
