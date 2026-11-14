"""Scout hardware path, SIMULATION-ONLY (no real board this weekend; the owner's boards are DWM3001CDK), offline: rung 2 (link-shadow locator) on synthetic links and on a recorded fake-board session,
the kit source, and the CIR side channel (L08 binary frames) over a byte stream and UDP loopback.

Everything here is SIMULATED or synthetic; nothing touches a real board or a running hub."""
import json
import math
import socket
import time
from pathlib import Path

import numpy as np
import pytest

from sandtable.scout import shadow, source_kit

ROOT = Path(__file__).resolve().parent.parent
REC = ROOT / "hardware" / "recordings" / "scout_mesh5_fakeboard.ndjson"
SCEN = ROOT / "hardware" / "scenarios" / "scout_pucks_mesh5.json"
PUCKS = {"P1": [6.2, 7.6], "P2": [8.6, 6.0], "P3": [7.9, 3.6], "P4": [5.0, 4.4], "P5": [4.6, 6.8]}


def _segd(a, b, p):
    ex, ey = b[0] - a[0], b[1] - a[1]
    s = max(0.0, min(1.0, ((p[0] - a[0]) * ex + (p[1] - a[1]) * ey) / (ex * ex + ey * ey)))
    return math.hypot(p[0] - a[0] - s * ex, p[1] - a[1] - s * ey)


def synth(person, t0=0.0, secs=4.0, seed=1, fields=("fp", "rssi", "los")):
    """Synthetic kit `range` messages at 10 Hz: free-space power with a fixed per-link offset (antenna orientation),
    1.5 dB noise, and a body (0.25 m half-width) that costs 5 dB total / 9 dB first-path power and 0.15 m of range."""
    rng, off = np.random.default_rng(seed), np.random.default_rng(7)
    ids = sorted(PUCKS)
    offs = {(a, b): off.normal(0, 1.5) for i, a in enumerate(ids) for b in ids[i + 1:]}
    out = []
    for k in range(int(secs * 10)):
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                d = math.dist(PUCKS[a], PUCKS[b])
                sh = person is not None and _segd(PUCKS[a], PUCKS[b], person) < 0.25
                m = {"type": "range", "t": t0 + k * 0.1, "a": a, "b": b,
                     "d": round(d + rng.normal(0, 0.05) + (0.15 if sh else 0.0), 3)}
                rssi = -68 - 20 * math.log10(d) + offs[(a, b)] + rng.normal(0, 1.5) - (5 if sh else 0)
                gap = 2 + 2 * abs(rng.normal()) + (4 if sh else 0)
                if "rssi" in fields:
                    m["rssi"] = round(rssi, 1)
                if "fp" in fields:
                    m["fp"] = round(rssi - gap, 1)
                if "los" in fields:
                    m["los"] = round(1 / (1 + math.exp((gap - 8) / 1.2)), 2)
                out.append(m)
    return out


def _crossing(a, b, c, d):
    a, b, c, d = map(np.array, (PUCKS[a], PUCKS[b], PUCKS[c], PUCKS[d]))
    s, _ = np.linalg.solve(np.array([b - a, c - d]).T, c - a)
    return list(a + s * (b - a))


BASE = shadow.baseline(synth(None, 0.0, 3.0, seed=2))


@pytest.mark.parametrize("use_base", [True, False])
def test_nobody(use_base):
    r = shadow.locate(synth(None, 3.0, 4.0, seed=3), PUCKS, baseline=BASE if use_base else None)
    assert r["present"] is False and r["links_down"] == [] and r["n_links"] == 10


@pytest.mark.parametrize("use_base", [True, False])
def test_person_on_one_link(use_base):
    person = [7.4, 6.8]                                    # middle of P1-P2, on no other link
    r = shadow.locate(synth(person, 3.0, 4.0, seed=3), PUCKS, baseline=BASE if use_base else None)
    assert r["present"] and [(d["a"], d["b"]) for d in r["links_down"]] == [("P1", "P2")]
    assert _segd(PUCKS["P1"], PUCKS["P2"], [r["x"], r["y"]]) < 0.3     # somewhere along that line ...
    assert r["sigma_m"] >= 0.5 and r["confidence"] < 0.6               # ... and it says it is unsure
    assert r["method"].startswith("link-shadow/")


def test_person_at_a_crossing():
    person = _crossing("P1", "P3", "P2", "P4")
    r = shadow.locate(synth(person, 3.0, 4.0, seed=4), PUCKS, baseline=BASE)
    assert {(d["a"], d["b"]) for d in r["links_down"]} == {("P1", "P3"), ("P2", "P4")}
    assert math.dist([r["x"], r["y"]], person) < 0.3
    assert r["confidence"] > 0.6 and r["sigma_m"] >= 0.5


def test_outside_the_hull_is_not_seen():
    assert not shadow.in_hull(PUCKS, 2.0, 2.0)
    r = shadow.locate(synth([2.0, 2.0], 3.0, 4.0, seed=5), PUCKS, baseline=BASE)
    assert r["present"] is False
    parts = shadow.to_scan_parts(r)
    assert parts["victims"] == [] and any("convex hull" in x for x in parts["limits"])


def test_missing_optional_fields():
    person = [7.4, 6.8]
    for fields in (("fp",), ("rssi",), ("los",), ("rssi", "los")):          # MaUWB / vendor firmware drop some
        r = shadow.locate(synth(person, 3.0, 4.0, seed=6, fields=fields), PUCKS,
                          baseline=shadow.baseline(synth(None, 0.0, 3.0, seed=2, fields=fields)))
        assert r["present"] and ("P1", "P2") in {(d["a"], d["b"]) for d in r["links_down"]}, fields
    # nothing but `d`: only the range excess against a baseline is left
    r = shadow.locate(synth(person, 3.0, 4.0, seed=6, fields=()), PUCKS,
                      baseline=shadow.baseline(synth(None, 0.0, 3.0, seed=2, fields=())))
    assert r["present"] and r["links_down"][0]["evidence"] == ["range"]
    r = shadow.locate(synth(person, 3.0, 4.0, seed=6, fields=()), PUCKS)
    assert r["present"] is False and r["method"] == "link-shadow/unavailable" and r["why"]


def test_moving_person_and_window():
    a = synth([7.4, 6.8], 3.0, 2.0, seed=8)                 # on P1-P2 for 2 s ...
    b = synth(_crossing("P1", "P3", "P2", "P4"), 5.0, 2.0, seed=9)    # ... then at the crossing
    r = shadow.locate(a + b, PUCKS, baseline=BASE)
    assert r["moving"] is True
    r = shadow.locate(a + b, PUCKS, baseline=BASE, window_s=1.5)     # the live view: only the last 1.5 s
    assert {(d["a"], d["b"]) for d in r["links_down"]} == {("P1", "P3"), ("P2", "P4")}


def test_scan_parts_shape():
    r = shadow.locate(synth(_crossing("P1", "P3", "P2", "P4"), 3.0, 4.0, seed=4), PUCKS, baseline=BASE)
    parts = shadow.to_scan_parts(r, PUCKS)
    assert parts["rung"] == "link-shadow"
    v = parts["victims"][0]
    for k in ("x", "y", "sigma_m", "cov", "confidence", "n_links", "state", "moving", "breathing_bpm", "breathing_conf"):
        assert k in v
    assert v["breathing_bpm"] is None and v["state"] in ("none", "moving")
    assert len(parts["per_link"]) == 10 and {"tx", "rx", "excess_m", "snr_db", "used"} <= set(parts["per_link"][0])
    assert sum(l["shadowed"] for l in parts["links"]) == 2
    em = parts["echo_map"]
    assert em["kind"] == "shadow" and len(em["values"]) == em["nx"] * em["ny"]
    json.dumps(parts)                                        # JSON-serialisable


# ---- the kit source against a recorded mesh5 session (kit fake-board emulator -> UDP -> hub) -----------------------
def _twin_transform(rec):
    sc = json.loads(SCEN.read_text())
    twin = {f"P{k[1]}": v["at"] for k, v in sc["nodes"].items()}
    ids = sorted(twin)
    A, B = np.array([rec["pucks_rel"][i] for i in ids]), np.array([twin[i] for i in ids])
    best = None
    for mir in (1, -1):
        A2 = A * np.array([1, mir])
        ca, cb = A2.mean(0), B.mean(0)
        U, _, Vt = np.linalg.svd((A2 - ca).T @ (B - cb))
        R = Vt.T @ U.T
        if np.linalg.det(R) < 0:
            continue
        res = float(np.linalg.norm((A2 - ca) @ R.T + cb - B, axis=1).mean())
        if best is None or res < best[0]:
            best = (res, lambda p, m=mir, R=R, ca=ca, cb=cb: (np.array(p) * np.array([1, m]) - ca) @ R.T + cb)
    return best


def test_source_from_recorded_mesh5_session():
    msgs = source_kit.load_ndjson(REC)
    rec = source_kit.from_messages(msgs)
    assert rec["meta"]["simulated"] is True and rec["meta"]["profile"] == "mesh5"      # fake boards keep the SIM badge
    assert rec["pucks"] == ["P1", "P2", "P3", "P4", "P5"] and rec["id_map"]["N3"] == "P3"
    assert rec["meta"]["n_links"] == 10 and rec["frames"] == {} and rec["truth"] is None
    assert rec["pucks_rel"]["P1"] == [0.0, 0.0] and abs(rec["pucks_rel"]["P2"][1]) < 0.05   # kit mesh frame
    resid, to_twin = _twin_transform(rec)
    assert resid < 0.15                                                   # self-location matches the scenario
    # MDS fallback when the hub publishes no pos
    rec2 = source_kit.from_messages([m for m in msgs if m.get("type") != "pos"])
    assert rec2["meta"]["self_location"] == "mds-from-ranges"
    assert _twin_transform(rec2)[0] < 0.15
    # rung 2 on it: empty first 3.5 s as the baseline, the person (stand-in) appears at 4 s on links N1-N4 and N2-N5
    t0 = min(r["t"] for r in rec["ranges"])
    base = shadow.baseline(rec["ranges"], seconds=3.5)
    early = [r for r in rec["ranges"] if r["t"] < t0 + 3.5]
    late = [r for r in rec["ranges"] if r["t"] >= t0 + 5.0]
    assert shadow.locate(early, rec["pucks_rel"], baseline=base)["present"] is False
    for b in (base, None):
        res = shadow.locate(late, rec["pucks_rel"], baseline=b)
        assert {(d["a"], d["b"]) for d in res["links_down"]} == {("P1", "P4"), ("P2", "P5")}
        assert math.dist(to_twin([res["x"], res["y"]]), [6.81, 5.91]) < 0.5
    assert "SIMULATED" in source_kit.report(rec)


# ---- CIR side channel (L08 binary frames) -----------------------------------------------------------------------------
def _cir_msg(tx, rx, t, fp=8.3, seq=1):
    k = np.arange(64)
    h = 3000 * np.exp(-0.5 * ((k - fp) / 0.6) ** 2) * np.exp(1j * 0.7) + 400 * np.exp(-0.5 * ((k - 20) / 0.6) ** 2)
    return {"type": "cir", "t": t, "seq": seq, "tx": tx, "rx": rx, "fp": fp, "pacc": 118, "rxp": -80.0, "fpp": -83.5,
            "_iq": h.astype(np.complex64)}


def test_cir_frames_in_a_mixed_serial_byte_stream():
    stream = (b"\x00\xffets Jun  8 2016 boot noise\r\n#UWBKIT fw=0.3.0 chip=DW3000 role=mesh id=N2\n"
              + source_kit.encode_frame(_cir_msg("N1", "N2", 1.0)) + b"R,1,N1,N3,2.410,2.410,-75.1,-78.0,0.97\n"
              + source_kit.encode_frame(_cir_msg("N4", "N2", 1.05))[:-1] + b"\x00"         # corrupted CRC: dropped
              + source_kit.encode_frame(_cir_msg("N5", "N2", 1.1)))
    p = source_kit.FrameParser()
    out = []
    for i in range(0, len(stream), 37):                   # arrives in arbitrary chunks, as from a UART
        out += p.feed(stream[i:i + 37], now=100.0)
    cir = [m for m in out if m["type"] == "cir"]
    assert [(m["tx"], m["rx"]) for m in cir] == [("N1", "N2"), ("N5", "N2")]
    assert p.stats["bad_crc"] >= 1 and cir[0]["synthetic"] is True
    assert abs(cir[0]["fp"] - 8.3) < 0.02 and int(np.argmax(np.abs(cir[0]["_iq"]))) == 8
    assert any(m.get("banner") and m.get("id") == "N2" for m in out)


def test_cir_udp_receiver_and_recording_shape():
    with source_kit.CirUdpReceiver("127.0.0.1", 0) as rx:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for k in range(20):
            for tx, r in (("N1", "N2"), ("N3", "N2")):
                s.sendto(source_kit.encode_frame(_cir_msg(tx, r, 10 + k * 0.05, seq=k)), ("127.0.0.1", rx.port))
        deadline = time.time() + 3.0
        while len(rx.frames) < 40 and time.time() < deadline:
            time.sleep(0.02)
        frames = rx.take()
    assert len(frames) == 40
    msgs = source_kit.load_ndjson(REC)
    rec = source_kit.from_messages(msgs, frames)
    f = rec["frames"][("P1", "P2")]
    assert f["cir"].shape == (20, 64) and f["cir"].dtype == np.complex64 and f["fp_index"].shape == (20,)
    assert rec["meta"]["cir"] and rec["meta"]["synthetic_cir"] and rec["meta"]["simulated"]
    assert "first-path index" in source_kit.report(rec)


def test_esp32_reference_is_labelled():
    ref = ROOT / "hardware" / "firmware" / "esp32-reference"
    assert "Not applicable to the owner's boards" in (ref / "README.md").read_text()
    assert (ref / "uwbkit_tagless" / "uwbkit_tagless.ino").exists() and not (ROOT / "hardware" / "firmware" / "STATUS.json").exists()
    assert "exit=0" in (ROOT / "hardware" / "build_logs" / "uwbkit_tagless__esp32_esp32_esp32.txt").read_text()


# ---- against builder R1's scout simulator (independent body model: knife-edge loss past a lying body) ---------------
def test_against_the_scout_simulator():
    sim = pytest.importorskip("sandtable.scout.sim")
    scene = pytest.importorskip("sandtable.scout.scene")
    from sandtable import config
    from sandtable.spatial import Twin
    twin = Twin(json.loads(config.TWIN_JSON.read_text()))
    sc = scene.build_scene(twin, "r310", seed=313, victim=True)
    P = {p["id"]: [p["x"], p["y"]] for p in sc["pucks"]}
    as_thrown = shadow.locate(sim.simulate(sc)["ranges"], P, bounds=sc["polygon"])
    if not shadow.in_hull(P, sc["victim"]["x"], sc["victim"]["y"]):
        assert as_thrown["present"] is False                      # beyond the pucks: rung 2 cannot see it, and says so
    c = np.mean(np.array(list(P.values())), 0)
    sc["victim"]["x"], sc["victim"]["y"] = float(c[0]), float(c[1])
    r = shadow.locate(sim.simulate(sc)["ranges"], P, bounds=sc["polygon"])
    assert r["present"] and math.dist([r["x"], r["y"]], c) < 1.0


def test_simulated_if_any_hello_says_so():
    # a hub sends its first hello before it notices the fake-board emulator; the stream is still simulated
    msgs = [{"type": "hello", "simulated": False, "profile": "mesh5", "nodes": {"N1": {}, "N2": {}, "N3": {}}},
            {"type": "hello", "simulated": True, "profile": "mesh5", "nodes": {"N1": {}, "N2": {}, "N3": {}}}]
    msgs += [{"type": "range", "t": 1.0 + k * 0.1, "a": a, "b": b, "d": d}
             for k in range(5) for a, b, d in (("N1", "N2", 2.0), ("N1", "N3", 2.0), ("N2", "N3", 2.0))]
    rec = source_kit.from_messages(msgs)
    assert rec["meta"]["simulated"] is True and rec["meta"]["self_location"] == "mds-from-ranges"
    assert source_kit.load_ndjson(REC)[0]["simulated"] is True              # the fixture's first hello says SIM too
