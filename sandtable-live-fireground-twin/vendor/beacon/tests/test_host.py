"""Tests for the puck host (beacon/host). Offline, localhost only. No test imports bleak or touches a radio, a serial
port or a J-Link: the cold aggregator is fed by a fake scanner, the RTT reader by a local TCP socket, the server is a
tiny HTTP stub on 127.0.0.1:1891 that each test stops."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from beacon.host import __version__
from beacon.host import cold as cold_mod
from beacon.host.cold import ColdAggregator, band_of
from beacon.host.core import Host, Poster, RangeThrottle, heartbeat_obs, uwb_puck_obs
from beacon.host.parse import LEARN_SAMPLE, parse_line
from beacon.host.sources import FileFollowSource, RttTelnetSource, _LineSplitter, parse_hostport

ROOT = Path(__file__).resolve().parent.parent
STUB_PORT = 1891          # W8's test port (docs/CONTRACTS.md section 1)
DEAD_PORT = 1991          # W8's second port; nothing listens on it during the tests


# --- parser ---------------------------------------------------------------------------------------------------------

def _d(line, **kw):
    r = parse_line(line, **kw)
    return [x.d_m for x in r.readings]


def test_research_example_err_status_is_dropped():
    r = parse_line('{"TWR": {"R":6613,"a16":"0x0001","S":"ERR","D cm":0}}')
    assert r.readings == []
    assert any("ERR" in why for why in r.dropped)


def test_research_example_twr_ok_in_centimetres():
    r = parse_line('{"TWR": {"R":6614,"a16":"0x0001","S":"OK","D cm":231}}')
    assert [x.d_m for x in r.readings] == [2.31]
    assert r.readings[0].peer == "0x0001"          # "R" is a round counter, never a range


def test_research_example_cli_fragment_and_full_json():
    assert _d('"Addr":"0x0000","Status":"Ok","D_cm":61') == [0.61]
    full = '{"Block":3,"results":[{"Addr":"0x0000","Status":"Ok","D_cm":61,"LPDoA_deg":0.0,"LAoA_deg":0.0},' \
           '{"Addr":"0x0001","Status":"RX_TIMEOUT"}]}'
    r = parse_line(full)
    assert [x.d_m for x in r.readings] == [0.61]
    assert any("RX_TIMEOUT" in why for why in r.dropped)


def test_cli_failure_without_distance():
    r = parse_line('"Addr":"0x0001","Status":"RX_TIMEOUT"')
    assert r.readings == [] and "RX_TIMEOUT" in r.explain()


def test_json_with_a_log_prefix():
    assert _d('00> [00:00:12.345] <inf> app: {"TWR": {"R":1,"a16":"0x0002","S":"OK","D cm":150}}') == [1.5]


@pytest.mark.parametrize("line,expected", [
    ("distance: 1.23 m", 1.23),
    ("distance: 1.23 meters", 1.23),
    ("Distance = 2.5", 2.5),            # unit-less decimal: metres
    ("range=245cm status=OK", 2.45),
    ("dist 1230 mm", 1.23),
    ("id=3 range_m=4.2", 4.2),
    ('{"d_m": 2.29}', 2.29),
])
def test_plain_text_forms(line, expected):
    assert _d(line) == [expected]


def test_unitless_integer_is_ambiguous_unless_told():
    assert _d("distance: 123") == []
    assert _d("distance: 123", unitless="cm") == [1.23]


def test_bad_status_in_plain_text():
    assert _d("distance 3.1m status: ERR") == []


@pytest.mark.parametrize("line", [
    "", "   ", "\x00\x00", "ACCESSORY_RANGING_ROLE: Initiator", "SEGGER J-Link V7.94 - Real time terminal output",
    "rssi: -71 dBm", "{not json", "{}", "[1,2,3]", '{"D cm": "abc"}', '{"D cm": true}', '{"D cm": null}',
    "\x1b[0m<inf> garbage \xff\xfe 12:34:56 ###", "x" * 20000, "R=6613 a16=0x0001", "LAoA_deg: 12.5",
])
def test_garbage_gives_no_range_and_never_raises(line):
    assert parse_line(line).readings == []


def test_bytes_and_random_noise_never_raise():
    import random
    rng = random.Random(7)
    for _ in range(300):
        junk = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 120)))
        parse_line(junk)
        parse_line(junk.decode("latin-1"))


def test_implausible_values_dropped():
    assert _d("range=987654cm") == []
    assert _d('{"S":"OK","D cm":-12}') == []
    assert _d('{"S":"OK","D cm":0}') == []
    assert _d("distance: 31 m") == []
    assert _d("distance: 31 m", max_m=40) == [31.0]


def test_learn_sample_yields_only_the_expected_ranges():
    got = [d for line in LEARN_SAMPLE for d in _d(line)]
    assert got == [2.31, 0.61, 1.23]


# --- payloads -------------------------------------------------------------------------------------------------------

def test_heartbeat_shape_matches_feed_a7():
    hb = heartbeat_obs("P1", "uno-q", 312.7, rtt=True, cold_scan=False, now=1794679200.5)
    for k, typ in {"kind": str, "t": float, "puck": str, "host": str, "uptime_s": int, "rtt": bool, "cold_scan": bool,
                   "version": str}.items():
        assert isinstance(hb[k], typ), k
    assert hb["kind"] == "host" and hb["puck"] == "P1" and hb["host"] == "uno-q" and hb["uptime_s"] == 312
    assert hb["version"] == __version__
    assert "dry_run" not in hb
    json.dumps(hb)
    assert heartbeat_obs("P1", "x", 0, rtt=False, cold_scan=False, dry_run=True)["dry_run"] is True


def test_uwb_puck_shape():
    o = uwb_puck_obs("P2", 2.2949, now=1.0)
    assert o == {"kind": "uwb_puck", "t": 1.0, "puck": "P2", "d_m": 2.295}


def test_throttle_at_most_five_per_second_and_median():
    th = RangeThrottle(5.0)
    sent = []
    t = 1000.0
    for i in range(100):                    # 100 readings in one second
        d = th.offer(None, 2.0 + (0.5 if i % 10 == 0 else 0.0), t)
        if d is not None:
            sent.append(d)
        sent.extend(v for _, v in th.due(t))
        t += 0.01
    assert 4 <= len(sent) <= 6
    assert all(abs(v - 2.0) < 1e-9 for v in sent[1:])     # outliers die in the pooled median


def test_host_routes_lines_to_poster_without_network():
    poster = Poster("http://127.0.0.1:9", post=False)
    h = Host(server="x", puck="P4", name="test", poster=poster, log=lambda m: None)
    for line in LEARN_SAMPLE:
        h.on_line(line, "test")
    items = []
    while not poster.q.empty():
        items.append(poster.q.get_nowait())
    assert [o["d_m"] for o in items] == [2.31, 0.61, 1.23]
    assert all(o["kind"] == "uwb_puck" and o["puck"] == "P4" for o in items)
    hb = h.heartbeat()
    assert hb["lines_seen"] == len(LEARN_SAMPLE) and hb["ranges_sent"] == 3 and hb["rtt"] is False


# --- cold aggregator privacy ----------------------------------------------------------------------------------------

FAKE_ADDRESSES = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03", "4F:12:9A:00:7C:11", "6E:6E:6E:6E:6E:6E"]


def _fake_scan(agg):
    """A fake scanner: the same advertisers heard several times with varying strength."""
    for _ in range(5):
        agg.add(FAKE_ADDRESSES[0], -50)     # near
        agg.add(FAKE_ADDRESSES[1], -58)     # near
        agg.add(FAKE_ADDRESSES[2], -70)     # mid
        agg.add(FAKE_ADDRESSES[3], -90)     # far
        agg.add(FAKE_ADDRESSES[4], -82)     # far ...
    agg.add(FAKE_ADDRESSES[4], -55)         # ... but its strongest packet in the window is near
    agg.add("garbage", "not-a-number")      # ignored


def test_cold_payload_contains_only_counts():
    agg = ColdAggregator(10.0)
    _fake_scan(agg)
    obs = agg.close_window("P1", now=time.time() + 10)
    assert set(obs) == {"kind", "t", "puck", "window_s", "near", "mid", "far"}
    assert (obs["near"], obs["mid"], obs["far"]) == (3, 1, 1)
    text = json.dumps(obs) + repr(agg)
    for a in FAKE_ADDRESSES:
        assert a not in text and a.replace(":", "") not in text and a.lower() not in text
    # the window is forgotten
    assert agg.close_window("P1")["near"] == 0


def test_cold_aggregator_exposes_no_addresses_or_hashes():
    agg = ColdAggregator(10.0)
    assert not hasattr(agg, "__dict__")                   # __slots__: no hidden attributes can appear
    public = [n for n in dir(agg) if not n.startswith("_")]
    assert sorted(public) == ["add", "close_window", "window_s"]
    _fake_scan(agg)
    assert repr(agg) == "ColdAggregator(window_s=10.0)"


def test_cold_salt_is_random_per_run():
    a, b = ColdAggregator(), ColdAggregator()
    assert a._salt != b._salt and len(a._salt) == 16


def test_cold_bands_documented_thresholds():
    assert band_of(-60) == "near" and band_of(-60.5) == "mid" and band_of(-75) == "mid" and band_of(-75.1) == "far"


def test_cold_module_never_writes_or_logs_addresses():
    src = Path(cold_mod.__file__).read_text(encoding="utf-8")
    assert "open(" not in src and "write(" not in src
    assert "log(device" not in src and "print(device" not in src and "device.name" not in src


def test_cold_scan_without_bleak_does_nothing(monkeypatch):
    monkeypatch.setattr(cold_mod, "bleak_available", lambda: False)
    said, emitted = [], []
    cold_mod.run_cold_scan("P1", emitted.append, threading.Event(), 0.1, said.append)
    assert emitted == [] and "bleak is not installed" in said[0]


# --- sources --------------------------------------------------------------------------------------------------------

def test_line_splitter_handles_telnet_iac_crlf_and_partial_lines():
    s = _LineSplitter()
    out = s.feed(b"\xff\xfb\x01SEGGER J-Link\r\n" + b'{"TWR": {"S":"OK","D c')
    out += s.feed(b'm":120}}\n\x00\nnext')
    assert out == ["SEGGER J-Link", '{"TWR": {"S":"OK","D cm":120}}']
    assert s.feed(b"\r") == ["next"]


def test_parse_hostport():
    assert parse_hostport("127.0.0.1:19021") == ("127.0.0.1", 19021)
    assert parse_hostport("localhost") == ("localhost", 19021)
    assert parse_hostport(":19022") == ("127.0.0.1", 19022)


def test_rtt_telnet_reader_against_local_socket():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        conn.sendall(b"SEGGER J-Link V7.94 - Real time terminal output\r\n")
        conn.sendall(b'{"TWR": {"R":7,"a16":"0x0001","S":"OK","D cm":199}}\r\n')
        time.sleep(0.3)
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    got, stop = [], threading.Event()
    src = RttTelnetSource("127.0.0.1", port, got.append, stop, log=lambda m: None)
    src.start()
    deadline = time.time() + 5
    while len(got) < 2 and time.time() < deadline:
        time.sleep(0.05)
    stop.set()
    src.join(3)
    srv.close()
    assert got[1] == '{"TWR": {"R":7,"a16":"0x0001","S":"OK","D cm":199}}'
    assert _d(got[1]) == [1.99]


def test_file_follow_reads_only_new_lines(tmp_path):
    f = tmp_path / "rtt.log"
    f.write_text("old line distance: 9.9 m\n", encoding="utf-8")
    got, stop = [], threading.Event()
    src = FileFollowSource(str(f), got.append, stop, log=lambda m: None)
    src.start()
    time.sleep(0.4)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write("distance: 1.50 m\n")
    deadline = time.time() + 5
    while not got and time.time() < deadline:
        time.sleep(0.05)
    stop.set()
    src.join(3)
    assert got == ["distance: 1.50 m"]


# --- end to end: --dry-run against a stub server --------------------------------------------------------------------

class _Stub(BaseHTTPRequestHandler):
    bodies: list = []

    def do_POST(self):          # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        _Stub.bodies.append((self.path, json.loads(self.rfile.read(n))))
        out = json.dumps({"ok": True, "accepted": 1}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):  # quiet
        pass


@pytest.fixture
def stub_server():
    _Stub.bodies = []
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(("127.0.0.1", STUB_PORT), _Stub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield _Stub.bodies
    finally:
        srv.shutdown()
        srv.server_close()


def _run_host(args, timeout=30):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, "-m", "beacon.host", *args], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=timeout)


def test_dry_run_end_to_end_against_stub(stub_server):
    p = _run_host(["--server", f"http://127.0.0.1:{STUB_PORT}", "--puck", "P3", "--name", "test-host",
                   "--dry-run", "--seconds", "2.5"])
    assert p.returncode == 0, p.stderr
    assert stub_server, "nothing was posted"
    assert all(path == "/api/ingest" and isinstance(body, list) for path, body in stub_server)
    obs = [o for _, body in stub_server for o in body]
    hbs = [o for o in obs if o["kind"] == "host"]
    ranges = [o for o in obs if o["kind"] == "uwb_puck"]
    assert hbs and hbs[0]["puck"] == "P3" and hbs[0]["host"] == "test-host" and hbs[0]["dry_run"] is True
    assert ranges and {o["d_m"] for o in ranges} <= {2.31, 0.61, 1.23}
    assert all(o["dry_run"] is True and o["puck"] == "P3" for o in ranges)
    assert {o["kind"] for o in obs} <= {"host", "uwb_puck"}           # no cold counts unless asked
    assert "DRY RUN" in p.stderr


def test_server_away_is_survived_quietly():
    p = _run_host(["--server", f"http://127.0.0.1:{DEAD_PORT}", "--puck", "P1", "--dry-run", "--seconds", "1.5"])
    assert p.returncode == 0, p.stderr
    assert p.stderr.count("unreachable") == 1                         # said once, not per batch
    assert "Traceback" not in p.stderr


def test_help_and_bad_arguments():
    p = _run_host(["--help"])
    assert p.returncode == 0 and "EXPERIMENTAL" in p.stdout and "NEVER RUN BY ITS AUTHORS" in p.stdout
    assert _run_host(["--dry-run"]).returncode == 2                      # --puck is required
    assert _run_host(["--puck", "P1", "--dry-run", "--serial", "auto"]).returncode == 2


def test_no_test_imported_bleak():
    assert "bleak" not in sys.modules


# --- the layout numbers quoted in hardware/BRINGUP.md and hardware/LAYOUT.md ----------------------------------------

def test_layout_table_numbers_match_the_documents():
    p = subprocess.run([sys.executable, str(ROOT / "hardware" / "layout_table.py"), "--scale", "3"], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    out = p.stdout
    assert "3.17 m x 2.88 m" in out
    assert "| P1 | 9.15, 6.70 | 3.05, 2.23 |" in out and "| P2 | 9.15, 2.70 | 3.05, 0.90 |" in out
    assert "0.12 m on the tape" in out and "1.33 m on the tape" in out and "(3.17, 2.40), 0.30 m wide" in out
    docs = (ROOT / "hardware" / "BRINGUP.md").read_text(encoding="utf-8") + (ROOT / "hardware" / "LAYOUT.md").read_text(encoding="utf-8")
    for n in ("3.17 m by 2.88 m", "2.23 m", "0.90 m", "1.33 m", "0.12 m", "2.40 m"):
        assert n in docs, n


# --- board to board: our SS-TWR firmware's RTT lines and the baseline (docs/BEACON_FEED.md A12) ----------------------

from beacon.host.twr import (TWR_SAMPLE, BaselineEstimator, TwrError, TwrHeartbeat, TwrIdent, TwrPoll, TwrRange,  # noqa: E402
                             TwrTimeout, TwrFail, parse_twr_line)


def test_twr_lines_in_the_firmware_format():
    assert parse_twr_line("R,12,1.572,-3.21,-78.9,977") == TwrRange(12, 1.572, -3.21, -78.9, 977)
    assert parse_twr_line("R,7,0.237,1.05") == TwrRange(7, 0.237, 1.05)            # the 4-field form of the old header
    assert parse_twr_line("T,13") == TwrTimeout(13)
    assert parse_twr_line("E,14,0000a000") == TwrError(14, "0000A000")
    assert parse_twr_line("P,200,7522") == TwrPoll(200, 7522)
    assert parse_twr_line("H,7522,11,0,0") == TwrHeartbeat(7522, 11, 0, 0)
    assert parse_twr_line("BOOT,RESP,DECA0302,1A2B3C4D5E6F7081") == TwrIdent("BOOT", "RESP", "DECA0302", "1A2B3C4D5E6F7081")
    assert parse_twr_line("ID,INIT,DECA0302,1A2B3C4D5E6F7081,35") == TwrIdent("ID", "INIT", "DECA0302", "1A2B3C4D5E6F7081", 35)
    assert parse_twr_line("FAIL,INIT,config") == TwrFail("INIT", "config")
    assert parse_twr_line(b"R,1,2.000,0.00,-80.0,1000\r\n") == TwrRange(1, 2.0, 0.0, -80.0, 1000)


@pytest.mark.parametrize("line", ["", "R,12", "R,x,1.5,0", "R,1,1.5,0,-80", "Range 1.5 m", "T,", "H,1,2,3",
                                  '{"TWR": {"R":6614,"a16":"0x0001","S":"OK","D cm":231}}', "distance: 1.23 m",
                                  "BOOT,TAG,DECA,00", "\x00\xff garbage"])
def test_twr_parser_ignores_everything_else(line):
    assert parse_twr_line(line) is None


def test_research_lines_are_not_twr_and_twr_lines_are_not_phone_ranges():
    for line in LEARN_SAMPLE:
        assert parse_twr_line(line) is None
    for line in TWR_SAMPLE:
        assert parse_line(line).readings == []


def _noisy(n, mean=1.572, sd=0.04, seed=3):
    import random
    rng = random.Random(seed)
    return [rng.gauss(mean, sd) for _ in range(n)]


def test_baseline_estimator_is_robust():
    est = BaselineEstimator(window=60, min_samples=20)
    vals = _noisy(50) + [4.95, 0.31, 9.9]                       # three wild multipath readings
    for i, v in enumerate(vals):
        est.add(v, 100.0 + i * 0.2)
    e = est.estimate(100.0 + len(vals) * 0.2)
    assert abs(e["d_m"] - 1.572) < 0.02 and 0.025 < e["sigma_m"] < 0.06
    assert e["rejected"] >= 3 and e["samples"] == e["window"] - e["rejected"]


def test_baseline_estimator_needs_enough_fresh_samples():
    est = BaselineEstimator(window=50, min_samples=20, max_age_s=30.0)
    for i, v in enumerate(_noisy(19)):
        est.add(v, 100.0 + i)
    assert est.estimate(120.0) is None                           # 19 < 20
    est.add(1.57, 119.0)
    assert est.estimate(120.0)["samples"] == 20
    assert est.estimate(160.0) is None                           # all older than 30 s
    assert est.add(0.01, 161.0) is False and est.add(200.0, 161.0) is False and est.implausible == 2
    with pytest.raises(ValueError):
        BaselineEstimator(window=10, min_samples=11)


def test_baseline_estimator_constant_window_keeps_everything():
    est = BaselineEstimator(window=30, min_samples=10)
    for i in range(30):
        est.add(2.0, float(i))
    e = est.estimate(30.0)
    assert e == {"d_m": 2.0, "sigma_m": 0.01, "samples": 30, "rejected": 0, "window": 30}


def test_host_never_sends_a_board_to_board_range_as_a_phone_range():
    poster = Poster("http://127.0.0.1:9", post=False)
    h = Host(server="x", puck="P1", name="test", poster=poster, log=lambda m: None, baseline_to="P2")
    for line in TWR_SAMPLE * 3:
        h.on_line(line, "rtt")
    h.tick(time.time() + 10)
    kinds = []
    while not poster.q.empty():
        kinds.append(poster.q.get_nowait()["kind"])
    assert "uwb_puck" not in kinds
    hb = h.heartbeat()
    assert hb["twr"]["role"] == "INIT" and hb["twr"]["ranges"] == 15 and hb["twr"]["timeouts"] == 3
    assert hb["ranges_sent"] == 0 and hb["lines_seen"] == len(TWR_SAMPLE) * 3


def test_host_posts_the_baseline_to_the_server(stub_server):
    from beacon.baseline import make_baseline
    poster = Poster(f"http://127.0.0.1:{STUB_PORT}", post=True)
    logs = []
    h = Host(server=f"http://127.0.0.1:{STUB_PORT}", puck="P1", name="test", poster=poster, log=logs.append,
             baseline_to="P2", estimator=BaselineEstimator(window=50, min_samples=20))
    t0 = time.time()
    for i, v in enumerate(_noisy(40)):
        h.on_line(f"R,{i},{v:.3f},-3.21,-78.9,977", "rtt")
    sent = h.baseline_tick(t0 + 1)
    assert sent is not None and h.baselines_sent == 1
    assert h.baseline_tick(t0 + 4) is None                       # unchanged and not yet a minute: not re-posted
    paths = [p for p, _ in stub_server]
    assert paths == ["/api/pucks/baseline"]
    body = stub_server[0][1]
    assert body["pair"] == ["P1", "P2"] and body["source"] == "twr" and body["samples"] >= 20
    assert abs(body["d_m"] - 1.572) < 0.03
    make_baseline(body["pair"], body["d_m"], sigma_m=body["sigma_m"], samples=body["samples"], source=body["source"], t=t0)


def test_dry_run_baseline_is_printed_never_posted(stub_server):
    p = _run_host(["--server", f"http://127.0.0.1:{STUB_PORT}", "--puck", "P1", "--baseline-to", "P2", "--baseline-min", "3",
                   "--dry-run", "--seconds", "4.5"])
    assert p.returncode == 0, p.stderr
    assert all(path == "/api/ingest" for path, _ in stub_server)    # never /api/pucks/baseline
    assert "DRY RUN baseline P1-P2" in p.stderr and "not posted" in p.stderr
    assert not [o for _, body in stub_server for o in body if o["kind"] == "uwb_puck" and not o.get("dry_run")]


def test_baseline_arguments_are_checked():
    assert _run_host(["--puck", "P1", "--baseline-to", "P1", "--dry-run", "--no-post"]).returncode == 2
    assert _run_host(["--puck", "uno", "--baseline-to", "P2", "--dry-run", "--no-post"]).returncode == 2
    assert _run_host(["--puck", "P1", "--baseline-to", "P9", "--dry-run", "--no-post"]).returncode == 2
    assert _run_host(["--puck", "P1", "--baseline-to", "P2", "--baseline-min", "60", "--dry-run", "--no-post"]).returncode == 2
    p = _run_host(["--puck", "P1", "--rtt", "127.0.0.1:jlink", "--no-post", "--seconds", "1"])
    assert p.returncode == 2 and "Traceback" not in p.stderr and "HOST:PORT" in p.stderr


def test_twr_offset_is_subtracted_before_the_baseline():
    poster = Poster("http://127.0.0.1:9", post=False, echo=lambda b: None)
    h = Host(server="x", puck="P1", name="test", poster=poster, log=lambda m: None, baseline_to="P2",
             estimator=BaselineEstimator(window=30, min_samples=10), twr_offset_m=0.05)
    for i in range(20):
        h.on_line(f"R,{i},1.620,0.00,-80.0,977", "rtt")
    body = h.baseline_tick(time.time())
    assert body["d_m"] == 1.57 and body["pair"] == ["P1", "P2"]
