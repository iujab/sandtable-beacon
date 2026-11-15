"""Our board-to-board SS-TWR firmware (hardware/twr_firmware/): its RTT lines, and a robust puck-to-puck baseline.

Line format (one line per event on SEGGER RTT channel 0; header comment of the firmware's app.c):

    BOOT,<role>,<dw_dev_id>,<nrf_device_id>                     both roles, once at start
    ID,<role>,<dw_dev_id>,<nrf_device_id>,<uptime_s>            both roles, every 5 s
    R,<seq>,<dist_m>,<ppm>,<rx_level_dBm>,<preamble_count>      initiator: one successful range
    T,<seq>                                                     initiator: no response before the timeout
    E,<seq>,<status_hex>                                        initiator: receive error
    P,<seq>,<responses>                                         responder: answered a poll
    H,<responses>,<rx_timeouts>,<rx_errors>,<late_tx>           responder: heartbeat, about once a second
    FAIL,<role>,<stage>                                         either: radio bring-up failed (init | config)

`role` is INIT or RESP. `dist_m` is the single-sided two-way-ranging distance in metres with the antenna delays left at
the default 16385 (NOT calibrated: every distance carries an unknown constant offset). `ppm` is the clock offset between
the boards, `rx_level_dBm` an estimate of the received level from the chip's diagnostics (generic formula constant), and
`preamble_count` the accumulated preamble symbols (out of 1024 with the long-range profile). The older 4-field
`R,<seq>,<dist_m>,<ppm>` form is accepted too.

A board-to-board range is NOT a phone range: the host never forwards it as `uwb_puck` (which the server attributes to a
phone). It only feeds the baseline (`--baseline-to`), posted to `POST /api/pucks/baseline` (docs/BEACON_FEED.md A12).

Pure standard library; no I/O.
"""
from __future__ import annotations

import re
import statistics
from collections import deque
from dataclasses import dataclass

__all__ = ["TwrRange", "TwrTimeout", "TwrError", "TwrPoll", "TwrHeartbeat", "TwrIdent", "TwrFail", "parse_twr_line",
           "BaselineEstimator", "MAD_TO_SIGMA", "TWR_SAMPLE"]

MAD_TO_SIGMA = 1.4826        # median absolute deviation -> standard deviation for Gaussian data


@dataclass(frozen=True)
class TwrRange:
    seq: int
    d_m: float
    ppm: float
    rx_dbm: float | None = None
    preamble: int | None = None


@dataclass(frozen=True)
class TwrTimeout:
    seq: int


@dataclass(frozen=True)
class TwrError:
    seq: int
    status: str


@dataclass(frozen=True)
class TwrPoll:
    seq: int
    responses: int


@dataclass(frozen=True)
class TwrHeartbeat:
    responses: int
    rx_timeouts: int
    rx_errors: int
    late_tx: int


@dataclass(frozen=True)
class TwrIdent:
    kind: str                    # "BOOT" or "ID"
    role: str                    # "INIT" or "RESP"
    dw_dev_id: str
    nrf_device_id: str
    uptime_s: int | None = None


@dataclass(frozen=True)
class TwrFail:
    role: str
    stage: str


_NUM = r"-?\d+(?:\.\d+)?"
_INT = r"\d+"
_HEX = r"[0-9A-Fa-f]+"
_ROLE = r"(?:INIT|RESP)"
_PATTERNS = (
    ("R", re.compile(rf"^R,({_INT}),({_NUM}),({_NUM})(?:,({_NUM}),({_INT}))?$")),
    ("T", re.compile(rf"^T,({_INT})$")),
    ("E", re.compile(rf"^E,({_INT}),({_HEX})$")),
    ("P", re.compile(rf"^P,({_INT}),({_INT})$")),
    ("H", re.compile(rf"^H,({_INT}),({_INT}),({_INT}),({_INT})$")),
    ("BOOT", re.compile(rf"^BOOT,({_ROLE}),({_HEX}),({_HEX})$")),
    ("ID", re.compile(rf"^ID,({_ROLE}),({_HEX}),({_HEX}),({_INT})$")),
    ("FAIL", re.compile(rf"^FAIL,({_ROLE}),(\w+)$")),
)

TwrRecord = TwrRange | TwrTimeout | TwrError | TwrPoll | TwrHeartbeat | TwrIdent | TwrFail


def parse_twr_line(line) -> TwrRecord | None:
    """One RTT line of our TWR firmware as a typed record, or None when the line is not one of ours. Never raises."""
    if not isinstance(line, str):
        try:
            line = bytes(line).decode("utf-8", "replace")
        except Exception:        # noqa: BLE001 - garbage in, None out
            return None
    text = line.strip().replace("\x00", "")
    if not text or text[0] not in "RTEPHBIF":
        return None
    for tag, rx in _PATTERNS:
        m = rx.match(text)
        if not m:
            continue
        g = m.groups()
        if tag == "R":
            return TwrRange(int(g[0]), float(g[1]), float(g[2]),
                            None if g[3] is None else float(g[3]), None if g[4] is None else int(g[4]))
        if tag == "T":
            return TwrTimeout(int(g[0]))
        if tag == "E":
            return TwrError(int(g[0]), g[1].upper())
        if tag == "P":
            return TwrPoll(int(g[0]), int(g[1]))
        if tag == "H":
            return TwrHeartbeat(*(int(v) for v in g))
        if tag == "BOOT":
            return TwrIdent("BOOT", g[0], g[1].upper(), g[2].upper())
        if tag == "ID":
            return TwrIdent("ID", g[0], g[1].upper(), g[2].upper(), int(g[3]))
        return TwrFail(g[0], g[1])
    return None


class BaselineEstimator:
    """The spacing of two pucks from a window of board-to-board ranges, robust to outliers.

    - keeps the last `window` ranges no older than `max_age_s`;
    - median m and MAD of the window; a range is an outlier when |d - m| > `reject_k` x max(1.4826 MAD, `sigma_floor_m`)
      (the floor stops a near-constant window from rejecting everything);
    - over the inliers: `d_m` = their median, `sigma_m` = 1.4826 x their MAD (at least `sigma_floor_m`): the spread of a
      SINGLE range, a conservative number (the standard error of the median would be about 1.25 sigma / sqrt(n));
    - no estimate before `min_samples` inliers.

    The antenna delays of the TWR firmware are uncalibrated, so `d_m` carries an unknown constant offset that no amount
    of averaging removes (hardware/twr_firmware/README.md, antenna-delay calibration).
    """

    def __init__(self, window: int = 50, min_samples: int = 20, max_age_s: float = 30.0, reject_k: float = 3.5,
                 sigma_floor_m: float = 0.01, min_m: float = 0.05, max_m: float = 150.0):
        if window < 1 or min_samples < 1 or min_samples > window:
            raise ValueError("need 1 <= min_samples <= window")
        self.window, self.min_samples, self.max_age_s = int(window), int(min_samples), float(max_age_s)
        self.reject_k, self.sigma_floor_m = float(reject_k), float(sigma_floor_m)
        self.min_m, self.max_m = float(min_m), float(max_m)
        self._buf: deque[tuple[float, float]] = deque(maxlen=self.window)
        self.ranges = 0
        self.implausible = 0

    def add(self, d_m: float, t: float) -> bool:
        """Add one range taken at time `t`; False (and not kept) when outside (min_m, max_m)."""
        self.ranges += 1
        if not self.min_m < d_m < self.max_m:
            self.implausible += 1
            return False
        self._buf.append((float(t), float(d_m)))
        return True

    def clear(self) -> None:
        self._buf.clear()

    def estimate(self, now: float) -> dict | None:
        """{"d_m", "sigma_m", "samples", "rejected", "window"} or None while there are too few inliers."""
        vals = [d for t, d in self._buf if now - t <= self.max_age_s]
        if len(vals) < self.min_samples:
            return None
        med = statistics.median(vals)
        mad = statistics.median(abs(v - med) for v in vals)
        cut = self.reject_k * max(MAD_TO_SIGMA * mad, self.sigma_floor_m)
        inl = [v for v in vals if abs(v - med) <= cut]
        if len(inl) < self.min_samples:
            return None
        d = statistics.median(inl)
        sigma = max(MAD_TO_SIGMA * statistics.median(abs(v - d) for v in inl), self.sigma_floor_m)
        return {"d_m": round(d, 3), "sigma_m": round(sigma, 3), "samples": len(inl), "rejected": len(vals) - len(inl),
                "window": len(vals)}


# Lines for --dry-run and the tests, in the firmware's real format. NOT measurements.
TWR_SAMPLE = [
    "BOOT,INIT,DECA0302,1A2B3C4D5E6F7081",
    "R,0,1.571,-3.21,-78.9,977",
    "R,1,1.568,-3.20,-79.1,977",
    "T,2",
    "R,3,1.602,-3.22,-78.8,976",
    "R,4,4.950,-3.19,-80.2,961",
    "E,5,0000A000",
    "R,6,1.559,-3.21,-79.0,977",
    "ID,INIT,DECA0302,1A2B3C4D5E6F7081,5",
]
