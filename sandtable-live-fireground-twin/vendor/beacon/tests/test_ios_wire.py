"""The iOS app's wire format against the real server in live mode (BEACON_FEED A2, A3, A6, A8).

fixtures/beacon/ios_batch_examples.json holds batches derived by hand from the app's Swift source (synthetic, no iPhone
involved). This test checks, without a Mac:
  - every key in those batches is a property the app's Codable structs declare, and nothing is null (the app omits nil);
  - the live server accepts each batch, counts a reading re-sent with its original `t` once, and answers with an
    `ack` the app's `IngestResponse` / `WireAck` structs decode (types checked against Models.swift, including nulls);
  - one puck's range gives a ring, both give two candidates or a fix, a rotated eid stays the same phone, "I am out"
    is acknowledged, and `bye` drops the phone.
"""
from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "beacon" / "ios_batch_examples.json"
MODELS_SWIFT = ROOT / "apps" / "ios" / "SandtableBeacon" / "Models.swift"


# ---------------------------------------------------------------------------- the app's structs, read from Models.swift
def swift_struct(name: str) -> dict[str, tuple[str, bool]]:
    """{json key: (swift type, optional)} for the stored `var`s of one struct in Models.swift."""
    src = MODELS_SWIFT.read_text(encoding="utf-8")
    m = re.search(r"struct " + name + r"\b[^{]*\{(.*?)\n\}", src, flags=re.S)
    assert m, f"struct {name} not found in Models.swift"
    fields = {}
    for line in m.group(1).splitlines():
        f = re.match(r"\s+var (\w+): ([\w\[\]]+)(\?)?\s*$", line)
        if f:
            fields[f.group(1)] = (f.group(2), bool(f.group(3)))
    assert fields, f"no stored properties parsed for {name}"
    return fields


def json_matches(value, swift_type: str) -> bool:
    """Would Swift's JSONDecoder decode this JSON value as `swift_type` (non-optional)?"""
    if swift_type == "String":
        return isinstance(value, str)
    if swift_type == "Bool":
        return isinstance(value, bool)
    if swift_type == "Int":
        return isinstance(value, int) and not isinstance(value, bool)
    if swift_type == "Double":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if swift_type.startswith("[") and swift_type.endswith("]"):
        return isinstance(value, list) and all(json_matches(v, swift_type[1:-1]) for v in value)
    if swift_type in ("WireAck", "WireReport", "WireDevice"):
        return isinstance(value, dict) and decodes(value, swift_type)
    raise AssertionError(f"no rule for Swift type {swift_type}")


def decodes(obj: dict, struct: str) -> bool:
    """JSONDecoder semantics for a synthesized Decodable: unknown keys ignored, an Optional may be missing or null,
    a non-Optional must be present, non-null and of the right type."""
    for key, (typ, optional) in swift_struct(struct).items():
        if key not in obj or obj[key] is None:
            if not optional:
                return False
            continue
        if not json_matches(obj[key], typ):
            return False
    return True


def assert_app_could_send(body: dict) -> None:
    """Keys are declared Swift properties, required ones are present, and no value is null (nil is omitted)."""
    def no_nulls(v, path):
        if v is None:
            raise AssertionError(f"{path} is null: the app omits nil optionals")
        if isinstance(v, dict):
            for k, x in v.items():
                no_nulls(x, f"{path}.{k}")
        elif isinstance(v, list):
            for i, x in enumerate(v):
                no_nulls(x, f"{path}[{i}]")

    no_nulls(body, "body")
    assert set(body) == {"device", "obs"}
    for struct, obj in [("WireDevice", body["device"])] + [("WireObservation", o) for o in body["obs"]] + \
            [("WireReport", o["report"]) for o in body["obs"] if "report" in o]:
        fields = swift_struct(struct)
        assert set(obj) <= set(fields), (struct, set(obj) - set(fields))
        for key, (typ, optional) in fields.items():
            if not optional:
                assert key in obj, f"{struct}.{key} is not optional but missing"
            if key in obj and typ not in ("WireReport",):
                assert json_matches(obj[key], typ), (struct, key, obj[key], typ)
    dev_eid = body["device"]["eid"]
    assert re.fullmatch(r"[0-9a-f]{8}", dev_eid)
    for o in body["obs"]:
        assert o["eid"] == dev_eid, "every observation carries the device's current eid"


def assert_reply_decodes(reply: dict) -> dict:
    assert decodes(reply, "IngestResponse"), reply
    ack = reply["ack"]
    assert isinstance(ack, dict) and decodes(ack, "WireAck"), ack
    assert isinstance(ack.get("text"), str) and ack["text"], "the app shows ack.text large"
    return ack


# ---------------------------------------------------------------------------- helpers
def _wait(fn, timeout=6.0, every=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return fn()


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def shifted(batches: list[dict]) -> list[dict]:
    """The same batches with every `t` moved by one constant so the newest is 0.3 s ago (the server replaces times more
    than 120 s off its clock). Equal times stay equal, so re-sent readings stay re-sent readings."""
    latest = max(o["t"] for b in batches for o in b["body"]["obs"])
    delta = time.time() - 0.3 - latest
    out = copy.deepcopy(batches)
    for b in out:
        for o in b["body"]["obs"]:
            o["t"] = o["t"] + delta
    return out


def next_tick(body: dict, dt: float = 0.5) -> dict:
    """What the app sends 0.5 s later when no new reading arrived: the same readings, a new report time."""
    b = copy.deepcopy(body)
    for o in b["obs"]:
        if o["kind"] == "report":
            o["t"] += dt
    return b


def phone(c, eid: str) -> dict | None:
    return next((p for p in c.get("/api/beacons").json()["phones"] if p["eid"] == eid), None)


# ---------------------------------------------------------------------------- tests
def test_fixture_is_what_the_app_can_send(fixture):
    assert fixture["synthetic"] is True
    names = [b["name"] for b in fixture["batches"]]
    assert len(names) == len(set(names)) >= 4
    for b in fixture["batches"]:
        assert_app_could_send(b["body"])
    # prev_eid only in the first report after a rotation; bye alone in its batch; "I am out" is ok + safe_outside
    reports = [o for b in fixture["batches"] for o in b["body"]["obs"] if o["kind"] == "report"]
    assert sum("prev_eid" in r for r in reports) == 1
    out = [r for r in reports if r["report"]["safe_outside"]]
    assert out and all(r["report"]["status"] == "ok" for r in out)
    bye = fixture["batches"][-1]["body"]["obs"]
    assert [o["kind"] for o in bye] == ["bye"] and set(bye[0]) == {"kind", "t", "eid"}


def test_swift_ack_struct_matches_make_ack():
    """Every key make_ack returns that the app reads is declared in WireAck, with a compatible type, for each shape."""
    from beacon.session import make_ack
    shapes = [make_ack(None),
              make_ack({"ranges": [{"puck": "P1", "age_s": 1.0}], "geometry": "ring", "ring": {"r_m": 6.3}, "report": {}}),
              make_ack({"ranges": [{"puck": "P1", "age_s": 1.0}, {"puck": "P2", "age_s": 2.0}], "geometry": "fix",
                        "sigma_m": 0.4, "report": {}}),
              make_ack({"ranges": [], "geometry": "none", "report": {"safe_outside": True}})]
    for ack in shapes:
        assert decodes(ack, "WireAck"), ack
        assert set(swift_struct("WireAck")) <= set(ack), "the app reads every WireAck field; the server should send all"
    assert shapes[0]["geometry"] is None and shapes[0]["sigma_m"] is None      # nulls: Optional in Swift, decodes to nil


def test_live_server_takes_the_app_batches(make_client, fixture):
    c = make_client("live")
    batches = {b["name"]: b for b in shifted(fixture["batches"])}
    pl = fixture["placement"]
    r = c.post("/api/pucks", json={"pucks": pl["pucks"], "room": pl["room"]})
    assert r.status_code == 200, r.text

    def post(body):
        r = c.post("/api/ingest", json=body)
        assert r.status_code == 200, r.text
        reply = r.json()
        assert reply["ok"] is True and reply["unknown_accessories"] == []
        return reply, assert_reply_decodes(reply)

    def expect(reply, b):
        assert (reply["accepted"], reply["skipped"]) == (b["expect"]["accepted"], b["expect"]["skipped"]), (b["name"], reply)

    # 1. one board: the phone is new, so the ack has nulls (geometry, sigma_m) the app must decode; then a ring
    b1 = batches["b1_one_board"]
    reply, ack = post(b1["body"])
    expect(reply, b1)
    assert ack["heard_by"] == [] and ack["geometry"] is None and ack["sigma_m"] is None
    ph = _wait(lambda: (lambda p: p if p and p["geometry"] == "ring" else None)(phone(c, "a3f19c02")))
    assert ph, phone(c, "a3f19c02")
    # (the locator's range model shifts r_m a little from the raw d_m, e.g. 6.341 -> 6.18)
    assert ph["ring"]["puck"] == "P1" and abs(ph["ring"]["r_m"] - 6.341) < 0.5 and ph["x"] is None
    assert ph["report"]["status"] == "trapped" and ph["report"]["home_suite"] == "310" and ph["report"]["model"] == "iPhone 14"
    phone_id = ph["id"]

    # the next tick re-sends the same (accessory, t) readings: counted once, nothing breaks
    reply, ack = post(next_tick(b1["body"]))
    assert (reply["accepted"], reply["skipped"]) == (1, 2), reply
    assert ack["heard_by"] == ["P1"] and ack["geometry"] == "ring" and ack["sigma_m"] is None
    assert len(c.get("/api/beacons").json()["phones"]) == 1

    # 2. taking turns: P1's reading repeated with its original t, P2 new: two candidates or a fix
    b2 = batches["b2_take_turns_second_board"]
    reply, ack = post(b2["body"])
    expect(reply, b2)
    ph = _wait(lambda: (lambda p: p if p and p["geometry"] in ("candidates", "fix") else None)(phone(c, "a3f19c02")))
    assert ph, phone(c, "a3f19c02")
    assert {r["puck"] for r in ph["ranges"] if r["kind"] == "uwb"} == {"P1", "P2"}
    if ph["geometry"] == "fix":
        assert ph["x"] is not None and ((ph["x"] - 4.0) ** 2 + (ph["y"] - 3.0) ** 2) ** 0.5 < 1.0
    else:
        assert ph["x"] is None and len(ph["candidates"]) >= 2
    reply, ack = post(next_tick(b2["body"]))
    assert (reply["accepted"], reply["skipped"]) == (1, 4), reply
    assert ack["heard_by"] == ["P1", "P2"] and ack["geometry"] == ph["geometry"]
    assert (ack["sigma_m"] is None) == (ph["geometry"] != "fix")

    # 3. the random id rotated: same phone (prev_eid), the bearing is accepted
    b3 = batches["b3_eid_rotated_with_bearing"]
    reply, ack = post(b3["body"])
    expect(reply, b3)
    ph = _wait(lambda: phone(c, "5c7e21d0"))
    assert ph and ph["id"] == phone_id
    assert phone(c, "a3f19c02") is None and len(c.get("/api/beacons").json()["phones"]) == 1
    assert _wait(lambda: phone(c, "5c7e21d0")["report"]["status"] == "injured")

    # 4. "I am out": status ok + safe_outside, no position; the ack says so
    b4 = batches["b4_i_am_out"]
    reply, ack = post(b4["body"])
    expect(reply, b4)
    ph = _wait(lambda: (lambda p: p if p and p["report"].get("safe_outside") else None)(phone(c, "5c7e21d0")))
    assert ph and ph["geometry"] == "none" and ph["x"] is None and ph["report"]["status"] == "ok"
    reply, ack = post(next_tick(b4["body"]))
    assert "safe outside" in ack["text"]

    # 5. bye: the phone is dropped at once
    b5 = batches["b5_bye"]
    reply, ack = post(b5["body"])
    expect(reply, b5)
    assert _wait(lambda: not c.get("/api/beacons").json()["phones"])


def test_one_bad_value_does_not_silence_the_phone(make_client):
    """The app clamps d_m to >= 0.01 and drops RSSI outside -130..10 (Reporter.buildBatch). The server is tolerant as well:
    an observation that fails validation is dropped and reported, the rest of the batch (the status report) is used, and
    d_m = 0 (a phone lying on a puck) is accepted. Only a batch with nothing usable in it gets a 400."""
    c = make_client("live")
    t = time.time()
    body = {"device": {"eid": "a3f19c02"},
            "obs": [{"kind": "sos", "t": t, "eid": "a3f19c02", "accessory": "X#0001", "rssi": 127},
                    {"kind": "report", "t": t, "eid": "a3f19c02", "report": {"status": "trapped"}}]}
    r = c.post("/api/ingest", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j["accepted"] == 1 and j["rejected"] == 1 and isinstance(j["errors"][0], str)
    zero = {"device": {"eid": "a3f19c02"},
            "obs": [{"kind": "uwb", "t": t, "eid": "a3f19c02", "accessory": "X#0001", "d_m": 0.0}]}
    assert c.post("/api/ingest", json=zero).status_code == 200
    only_bad = {"device": {"eid": "a3f19c02"},
                "obs": [{"kind": "sos", "t": t, "eid": "a3f19c02", "accessory": "X#0001", "rssi": 127}]}
    r = c.post("/api/ingest", json=only_bad)
    assert r.status_code == 400
    assert isinstance(r.json().get("detail"), str)     # Reporter shows this text (ServerErrorReply.detail)
    ok = {"device": {"eid": "a3f19c02"},
          "obs": [{"kind": "uwb", "t": t, "eid": "a3f19c02", "accessory": "X#0001", "d_m": 0.01}]}
    assert c.post("/api/ingest", json=ok).status_code == 200
