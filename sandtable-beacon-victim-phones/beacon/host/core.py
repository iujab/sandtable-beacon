"""The puck host's moving parts: the HTTP poster, the range throttle, the heartbeat and the run loop.

Standard library only (urllib, threading, json). Talks to the Beacon server's `POST /api/ingest` with a LIST of
observations (docs/BEACON_FEED.md, addition A7):

    {"kind": "host", "t", "puck", "host", "uptime_s", "rtt", "cold_scan", "version", ...}   every 5 s
    {"kind": "uwb_puck", "t", "puck", "d_m"}                                                  at most 5 per second (experimental)
    {"kind": "cold", "t", "puck", "window_s", "near", "mid", "far"}                           every cold window (optional)

Extra fields this host adds (additive; servers ignore unknown fields): on `host`: `reader`, `reader_ok`, `lines_seen`,
`ranges_sent`, `serial`, `twr` (our TWR firmware's counters, when its lines are seen); on `uwb_puck`: `peer` (when the
line names one); on everything during `--dry-run`: `"dry_run": true`.

Board-to-board mode (`--baseline-to P2`, docs/BEACON_FEED.md A12): lines of our SS-TWR firmware (beacon/host/twr.py) feed
a robust baseline estimator, and the spacing is posted to `POST /api/pucks/baseline` when it is first ready, when it
changes, and once a minute. A board-to-board range is never sent as `uwb_puck` (that would be taken for a phone's range).
In `--dry-run` the baseline is printed and never posted (sample lines are not a measurement).
"""
from __future__ import annotations

import json
import queue
import statistics
import threading
import time
import urllib.error
import urllib.request

from . import __version__
from .parse import parse_line
from .twr import BaselineEstimator, TwrError, TwrIdent, TwrPoll, TwrRange, TwrTimeout, parse_twr_line

__all__ = ["Poster", "RangeThrottle", "heartbeat_obs", "uwb_puck_obs", "baseline_body", "post_json", "Host"]

USER_AGENT = f"beacon-host/{__version__}"


def post_json(url: str, body, timeout_s: float = 3.0) -> tuple[bool, str | None]:
    """POST `body` as JSON. (True, None) on a 2xx answer, else (False, a short reason). Never raises on network trouble."""
    data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            resp.read(65536)
            if 200 <= resp.status < 300:
                return True, None
            return False, f"server answered HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        text = ""
        try:
            text = e.read(300).decode("utf-8", "replace")
        except Exception:       # noqa: BLE001
            pass
        return False, f"server refused it: HTTP {e.code} {text[:200]}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, f"server {url} unreachable ({getattr(e, 'reason', e)})"


def baseline_body(puck: str, other: str, est: dict) -> dict:
    """The POST /api/pucks/baseline body for an estimate of BaselineEstimator."""
    return {"pair": [puck, other], "d_m": est["d_m"], "sigma_m": est["sigma_m"], "samples": est["samples"], "source": "twr"}


def heartbeat_obs(puck: str, host: str, uptime_s: float, *, rtt: bool, cold_scan: bool, now: float | None = None,
                  reader: str | None = None, reader_ok: bool = False, lines_seen: int = 0, ranges_sent: int = 0,
                  serial: bool = False, dry_run: bool = False) -> dict:
    """The A7 heartbeat (`kind: host`)."""
    obs = {"kind": "host", "t": round(time.time() if now is None else now, 3), "puck": puck, "host": host,
           "uptime_s": int(uptime_s), "rtt": bool(rtt), "cold_scan": bool(cold_scan), "version": __version__,
           "reader": reader, "reader_ok": bool(reader_ok), "lines_seen": int(lines_seen), "ranges_sent": int(ranges_sent),
           "serial": bool(serial)}
    if dry_run:
        obs["dry_run"] = True
    return obs


def uwb_puck_obs(puck: str, d_m: float, *, now: float | None = None, peer: str | None = None, dry_run: bool = False) -> dict:
    """A range read on the PUCK side (A7 `uwb_puck`); no phone id."""
    obs = {"kind": "uwb_puck", "t": round(time.time() if now is None else now, 3), "puck": puck, "d_m": round(float(d_m), 3)}
    if peer:
        obs["peer"] = peer
    if dry_run:
        obs["dry_run"] = True
    return obs


class RangeThrottle:
    """At most `max_hz` ranges per peer: readings that arrive between two slots are pooled and their median is sent."""

    def __init__(self, max_hz: float = 5.0):
        self.min_dt = 1.0 / max_hz if max_hz > 0 else 0.0
        self._last: dict[str, float] = {}
        self._pool: dict[str, list[float]] = {}

    def offer(self, peer: str | None, d_m: float, now: float) -> float | None:
        """Add a reading; return a value to send now, or None (pooled for the next slot)."""
        key = peer or ""
        self._pool.setdefault(key, []).append(d_m)
        if now - self._last.get(key, -1e9) >= self.min_dt:
            return self._release(key, now)
        return None

    def due(self, now: float) -> list[tuple[str | None, float]]:
        """Pooled readings whose slot has come (called from the loop so a burst's tail is not lost)."""
        out = []
        for key in list(self._pool):
            if self._pool[key] and now - self._last.get(key, -1e9) >= self.min_dt:
                out.append((key or None, self._release(key, now)))
        return out

    def _release(self, key: str, now: float) -> float:
        vals = self._pool.pop(key)
        self._last[key] = now
        return float(statistics.median(vals))


class Poster(threading.Thread):
    """Batches observations and POSTs them as a JSON list to <server>/api/ingest. Quiet retries when the server is away:
    one line when it goes away, one when it is back; ranges that cannot be delivered are dropped (they would be stale)."""

    def __init__(self, server: str, log=print, *, post: bool = True, echo=None, timeout_s: float = 3.0, batch_s: float = 0.2):
        super().__init__(daemon=True, name="beacon-host-poster")
        self.url = server.rstrip("/") + "/api/ingest"
        self.log = log
        self.do_post = post
        self.echo = echo                    # callable(list) to print every batch (dry run, --no-post), or None
        self.timeout_s = timeout_s
        self.batch_s = batch_s
        self.q: queue.Queue = queue.Queue(maxsize=2000)
        self.stop_event = threading.Event()
        self.server_up: bool | None = None
        self.posted = 0
        self.failed = 0
        self.dropped = 0
        self._next_try = 0.0

    def put(self, obs: dict) -> None:
        try:
            self.q.put_nowait(obs)
        except queue.Full:
            self.dropped += 1

    def _send(self, batch: list[dict]) -> bool:
        ok, why = post_json(self.url, batch, self.timeout_s)
        if not ok and self.server_up is not False:          # say it once per outage, not per batch
            self.log(why + ("; retrying quietly every few seconds" if "unreachable" in why else ""))
        return ok

    def flush_once(self, block_s: float | None = None) -> int:
        """Collect what is queued (waiting up to block_s for the first item) and post it. Returns the batch size."""
        batch: list[dict] = []
        try:
            batch.append(self.q.get(timeout=self.batch_s if block_s is None else block_s))
        except queue.Empty:
            return 0
        while True:
            try:
                batch.append(self.q.get_nowait())
            except queue.Empty:
                break
        if not self.do_post:
            if self.echo is not None:
                self.echo(batch)
            return len(batch)
        now = time.time()
        if self.server_up is False and now < self._next_try:
            # server away: keep only the newest heartbeat and cold counts for the next try; ranges would be stale
            keep = [o for o in batch if o.get("kind") in ("host", "cold")][-2:]
            self.dropped += len(batch) - len(keep)
            for o in keep:
                self.put(o)
            self.stop_event.wait(min(0.5, self._next_try - now))
            return 0
        if self.echo is not None:
            self.echo(batch)
        if self._send(batch):
            if self.server_up is False:
                self.log("server is back")
            self.server_up = True
            self.posted += len(batch)
        else:
            self.server_up = False
            self.failed += len(batch)
            self._next_try = time.time() + 2.0
        return len(batch)

    def run(self) -> None:
        while not self.stop_event.is_set():
            self.flush_once()
        # last chance for anything queued at shutdown
        while self.flush_once(block_s=0.05):
            pass

    def stop(self) -> None:
        self.stop_event.set()


class Host:
    """Wires sources -> parser -> throttle -> poster, plus the heartbeat, the optional cold scan and, with `baseline_to`,
    the board-to-board baseline of our TWR firmware."""

    def __init__(self, *, server: str, puck: str, name: str, poster: Poster, log=print, learn_out=None,
                 dry_run: bool = False, heartbeat_s: float = 5.0, max_hz: float = 5.0,
                 min_m: float = 0.05, max_m: float = 30.0, unitless: str = "auto", cold_scan: bool = False,
                 baseline_to: str | None = None, estimator: BaselineEstimator | None = None,
                 baseline_every_s: float = 2.0, baseline_refresh_s: float = 60.0, twr_offset_m: float = 0.0):
        self.server, self.puck, self.name = server, puck, name
        self.poster = poster
        self.log = log
        self.learn_out = learn_out          # callable(str) for --learn, or None
        self.dry_run = dry_run
        self.heartbeat_s = heartbeat_s
        self.throttle = RangeThrottle(max_hz)
        self.min_m, self.max_m, self.unitless = min_m, max_m, unitless
        self.cold_scan = cold_scan
        self.sources: list = []
        self.t0 = time.time()
        self.lines_seen = 0
        self.ranges_sent = 0
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        # board-to-board (our TWR firmware)
        self.baseline_to = baseline_to
        self.estimator = estimator if estimator is not None else BaselineEstimator()
        self.baseline_every_s, self.baseline_refresh_s = baseline_every_s, baseline_refresh_s
        self.twr_offset_m = float(twr_offset_m)         # a measured constant bias (uncalibrated antenna delays), or 0
        self.twr = {"role": None, "ranges": 0, "timeouts": 0, "errors": 0, "polls": 0}
        self.baseline_last: dict | None = None          # the last baseline sent (or printed in a dry run)
        self.baselines_sent = 0
        self._baseline_last_t = 0.0
        self._baseline_next_check = 0.0

    # --- lines -> ranges --------------------------------------------------------------------------------------------
    def on_line(self, text: str, source: str = "") -> None:
        rec = parse_twr_line(text)
        if rec is not None:
            self._on_twr(rec, text, source)
            return
        res = parse_line(text, min_m=self.min_m, max_m=self.max_m, unitless=self.unitless)
        with self._lock:
            self.lines_seen += 1
        if self.learn_out is not None:
            self.learn_out(f"[{source or 'line'}] {text!r}\n    -> {res.explain()}")
        now = time.time()
        for r in res.readings:
            with self._lock:
                d = self.throttle.offer(r.peer, r.d_m, now)
            if d is not None:
                self._send_range(d, r.peer, now)

    def _on_twr(self, rec, text: str, source: str) -> None:
        """A line of our board-to-board firmware: counted, and with `baseline_to` a range feeds the baseline. Never a
        `uwb_puck` observation."""
        now = time.time()
        with self._lock:
            self.lines_seen += 1
            if isinstance(rec, TwrRange):
                self.twr["ranges"] += 1
                if self.baseline_to:
                    self.estimator.add(rec.d_m - self.twr_offset_m, now)
            elif isinstance(rec, TwrTimeout):
                self.twr["timeouts"] += 1
            elif isinstance(rec, TwrError):
                self.twr["errors"] += 1
            elif isinstance(rec, TwrPoll):
                self.twr["polls"] += 1
            elif isinstance(rec, TwrIdent):
                self.twr["role"] = rec.role
        if self.learn_out is not None:
            if isinstance(rec, TwrRange):
                what = (f"TWR board-to-board range {rec.d_m:.3f} m (seq {rec.seq}); not a phone range"
                        + ("; feeds the baseline" if self.baseline_to else "; add --baseline-to P2 to post the spacing"))
            else:
                what = f"TWR firmware line: {type(rec).__name__[3:]}"
            self.learn_out(f"[{source or 'line'}] {text!r}\n    -> {what}")

    def baseline_tick(self, now: float) -> dict | None:
        """Every `baseline_every_s`: post the estimate when it is first ready, when it moved by more than
        max(2 cm, half its sigma), and every `baseline_refresh_s`. Returns the body sent (or printed), else None."""
        if not self.baseline_to or now < self._baseline_next_check:
            return None
        self._baseline_next_check = now + self.baseline_every_s
        with self._lock:
            est = self.estimator.estimate(now)
        if est is None:
            return None
        last = self.baseline_last
        moved = last is None or abs(est["d_m"] - last["d_m"]) > max(0.02, (last.get("sigma_m") or 0.0) / 2)
        if not moved and now - self._baseline_last_t < self.baseline_refresh_s:
            return None
        body = baseline_body(self.puck, self.baseline_to, est)
        self.baseline_last, self._baseline_last_t = body, now
        line = (f"baseline {self.puck}-{self.baseline_to}: {est['d_m']:.3f} m, sigma {est['sigma_m']:.3f} m, "
                f"{est['samples']} ranges ({est['rejected']} outliers dropped)")
        if self.dry_run:
            self.log("DRY RUN " + line + ": sample lines, not a measurement; not posted")
            return body
        if not self.poster.do_post:
            if self.poster.echo is not None:
                self.poster.echo([body])
            return body
        ok, why = post_json(self.server.rstrip("/") + "/api/pucks/baseline", body, self.poster.timeout_s)
        if ok:
            self.baselines_sent += 1
            self.log(line + " -> posted")
        else:
            self.baseline_last = last                      # try again at the next check
            self.log(f"{line}: not posted ({why})")
        return body if ok else None

    def _send_range(self, d_m: float, peer: str | None, now: float) -> None:
        self.poster.put(uwb_puck_obs(self.puck, d_m, now=now, peer=peer, dry_run=self.dry_run))
        with self._lock:
            self.ranges_sent += 1

    def tick(self, now: float) -> None:
        with self._lock:
            due = self.throttle.due(now)
        for peer, d in due:
            self._send_range(d, peer, now)

    # --- heartbeat ----------------------------------------------------------------------------------------------------
    def heartbeat(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        rtt_src = [s for s in self.sources if s.kind in ("rtt", "rtt-log")]
        ser_src = [s for s in self.sources if s.kind == "serial"]
        reader = self.sources[0].kind if self.sources else None
        hb = heartbeat_obs(self.puck, self.name, now - self.t0, now=now,
                           rtt=any(s.connected for s in rtt_src), cold_scan=self.cold_scan,
                           reader=reader, reader_ok=any(s.connected for s in self.sources),
                           lines_seen=self.lines_seen, ranges_sent=self.ranges_sent,
                           serial=any(s.connected for s in ser_src), dry_run=self.dry_run)
        if self.twr["role"] is not None or any(self.twr[k] for k in ("ranges", "timeouts", "errors", "polls")):
            with self._lock:
                hb["twr"] = dict(self.twr, baselines_sent=self.baselines_sent)
        return hb

    def add_source(self, src) -> None:
        self.sources.append(src)

    def line_handler(self, source_kind: str):
        return lambda text: self.on_line(text, source_kind)

    def run(self, seconds: float = 0.0) -> None:
        """Blocking main loop: heartbeat every `heartbeat_s`, throttle flush, until Ctrl-C or `seconds` elapse."""
        self.poster.start()
        for s in self.sources:
            s.start()
        next_hb = time.time()
        t_end = time.time() + seconds if seconds and seconds > 0 else None
        try:
            while not self.stop_event.is_set():
                now = time.time()
                if now >= next_hb:
                    self.poster.put(self.heartbeat(now))
                    next_hb = now + self.heartbeat_s
                self.tick(now)
                self.baseline_tick(now)
                if t_end is not None and now >= t_end:
                    break
                self.stop_event.wait(0.1)
        except KeyboardInterrupt:
            self.log("stopping (Ctrl-C)")
        finally:
            self.stop_event.set()
            for s in self.sources:
                s.stop_event.set()
            self.poster.stop()
            self.poster.join(timeout=5.0)
