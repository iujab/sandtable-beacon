"""Cold scan: count Bluetooth advertisers near a puck, per signal-strength band. OPTIONAL, OFF by default.

NEVER RUN BY ITS AUTHORS. No radio was ever opened by this code on the machine it was written on; the tests feed the
aggregator from a fake scanner and never import `bleak`.

What it does (`--cold-scan`): listen passively to Bluetooth advertisements for a 10 s window with `bleak` (if it is
installed), then post ONE observation per window:

    {"kind": "cold", "t", "puck": "P1", "window_s": 10.0, "near": 2, "mid": 1, "far": 4}

That is all that ever leaves this process: three counts. Devices, not people (watches, earbuds and laptops advertise
too; some phones do not; a phone's random address can rotate inside a window and count twice). No identity.

Privacy by construction:
- An address is turned into a salted hash the moment it arrives (`ColdAggregator.add`) and only the hash is kept, in
  memory, for the current window. The salt is 16 random bytes drawn when the process starts and never stored,
  printed or sent, so a hash cannot be matched across runs or against an address list.
- At the end of each window the set of hashes is reduced to three integers and thrown away.
- Nothing is written to disk. Nothing but the three counts is sent.
- There is no code path that logs, prints, stores or returns an address or a hash: `ColdAggregator` has no method that
  exposes them, and the scanner callback passes the address straight into `add` and drops it.
- Advertisement contents (names, manufacturer data, service UUIDs) are never read.

Bands: `beacon.models.cold_band` when it can be imported (it needs numpy), else the same documented thresholds
(CONTRACTS section 2): near >= -60 dBm, mid -60..-75 dBm, far < -75 dBm.

Research (docs/research/puck_hardware.md section 3): the UNO Q's radio is a Qualcomm WCN3980 (Bluetooth 5.1); a
Qualcomm project runs a bleak scanner on its Debian side over SSH. Not confirmed: that BlueZ passive scanning works on
the WCN3980 firmware. If passive mode fails, this module says so and does nothing else (it does not fall back to an
active scan, which would send scan requests).
"""
from __future__ import annotations

import hashlib
import os
import threading
import time

__all__ = ["ColdAggregator", "band_of", "BANDS_DBM", "run_cold_scan", "bleak_available"]

BANDS_DBM = {"near": -60.0, "mid": -75.0}       # documented fallback; same values as beacon.models.COLD_BANDS_DBM

try:                                             # numpy is not needed for anything else on a host
    from beacon.models import cold_band as _models_cold_band     # type: ignore
except Exception:                                # noqa: BLE001 - any import problem means "use the documented thresholds"
    _models_cold_band = None


def band_of(rssi_dbm: float) -> str:
    """'near' | 'mid' | 'far' for one advertisement's signal strength."""
    if _models_cold_band is not None:
        return _models_cold_band(rssi_dbm)
    r = float(rssi_dbm)
    if r >= BANDS_DBM["near"]:
        return "near"
    if r >= BANDS_DBM["mid"]:
        return "mid"
    return "far"


class ColdAggregator:
    """Distinct advertisers per band over one window. Holds only salted hashes, only in memory, only until `close_window`."""

    __slots__ = ("_salt", "_best", "_lock", "window_s", "_t0")

    def __init__(self, window_s: float = 10.0, salt: bytes | None = None):
        self._salt = salt if salt is not None else os.urandom(16)
        self._best: dict[bytes, float] = {}      # salted hash -> strongest RSSI heard in this window
        self._lock = threading.Lock()
        self.window_s = float(window_s)
        self._t0 = time.time()

    def add(self, address, rssi_dbm) -> None:
        """One advertisement. The address is hashed here and never kept or exposed."""
        try:
            r = float(rssi_dbm)
        except (TypeError, ValueError):
            return
        h = hashlib.blake2b(str(address).encode("utf-8", "replace"), key=self._salt, digest_size=16).digest()
        with self._lock:
            prev = self._best.get(h)
            if prev is None or r > prev:
                self._best[h] = r

    def close_window(self, puck: str, now: float | None = None) -> dict:
        """End the window: return the `cold` observation (counts only) and forget every hash."""
        now = time.time() if now is None else now
        with self._lock:
            rssis = list(self._best.values())
            self._best.clear()
        counts = {"near": 0, "mid": 0, "far": 0}
        for r in rssis:
            counts[band_of(r)] += 1
        window = round(max(0.0, now - self._t0), 1) or self.window_s
        self._t0 = now
        return {"kind": "cold", "t": round(now, 3), "puck": puck, "window_s": window,
                "near": counts["near"], "mid": counts["mid"], "far": counts["far"]}

    def __repr__(self) -> str:                   # never shows hashes or the salt
        return f"ColdAggregator(window_s={self.window_s})"


def bleak_available() -> bool:
    """True when bleak can be imported (checked without importing it)."""
    import importlib.util
    return importlib.util.find_spec("bleak") is not None


def run_cold_scan(puck: str, emit, stop: threading.Event, window_s: float = 10.0, log=print) -> None:
    """Blocking: scan in windows of `window_s` until `stop` is set; call `emit(obs)` with each `cold` observation.

    Uses bleak (BlueZ on Linux, CoreBluetooth on macOS, WinRT on Windows). NEVER RUN BY ITS AUTHORS.
    """
    if not bleak_available():
        log("cold scan: bleak is not installed (pip install bleak); cold scan disabled")
        return
    import asyncio

    async def _scan() -> None:
        from bleak import BleakScanner          # imported only here, only when --cold-scan was asked for

        agg = ColdAggregator(window_s)

        def _on_adv(device, adv) -> None:       # the address goes into the hash and is dropped
            agg.add(device.address, getattr(adv, "rssi", None))

        try:
            scanner = BleakScanner(detection_callback=_on_adv, scanning_mode="passive")
        except Exception as e:                  # noqa: BLE001
            log(f"cold scan: passive scanning is not available here ({type(e).__name__}); cold scan disabled")
            return
        try:
            await scanner.start()
        except Exception as e:                  # noqa: BLE001
            log(f"cold scan: could not start a passive scan ({type(e).__name__}: {e}); cold scan disabled")
            return
        log(f"cold scan: passive, {window_s:.0f} s windows; posting counts only")
        try:
            while not stop.is_set():
                t_end = time.time() + window_s
                while time.time() < t_end and not stop.is_set():
                    await asyncio.sleep(0.2)
                emit(agg.close_window(puck))
        finally:
            try:
                await scanner.stop()
            except Exception:                   # noqa: BLE001
                pass

    asyncio.run(_scan())
