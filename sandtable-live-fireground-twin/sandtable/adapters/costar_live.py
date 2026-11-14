"""CoStar live adapter: reads a CoStar Data Extract folder (the CoStar layer is files, not an HTTP API).

The folder is COSTAR_KIT_DIR (on the bench machine, the unpacked kit in the drop zone when the variable is unset;
on the owner's laptop, wherever the extract was unzipped, ideally data/live/costar/). The property is
COSTAR_PROPERTY_ID, else the `property_id`, `model_id` or `address` of data/live/demo_building.json, else
COSTAR_PROPERTY_ADDRESS. All field mapping lives in ONE table, `costar_extract.MAPPING`.

The record is merged with the public context of the same building (footprint, sides, hydrants, fire stations,
street geometry) from scripts/build_site.py: config.site_context_json(). Context that lies more than 300 m from
the CoStar coordinates belongs to another building and is not merged; the site then says what to run.
Indexes and caches are written under data/live/ only. Nothing here prints a value from the extract.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from .. import config
from . import costar_extract as cx


def _dist_m(a: dict, b: dict) -> float:
    try:
        dn = (a["lat"] - b["lat"]) * 111320.0
        de = (a["lon"] - b["lon"]) * 111320.0 * math.cos(math.radians(a["lat"]))
        return math.hypot(dn, de)
    except (KeyError, TypeError):
        return float("inf")


class CoStarLive:
    mode = "live"

    def __init__(self, kit_dir: Path, property_id: str | None = None, address: str | None = None, open_bundle: dict | None = None,
                 model_id: str | None = None, lat: float | None = None, lon: float | None = None):
        self.kit_dir = Path(kit_dir)
        self.is_kit = cx.is_kit(self.kit_dir)
        self.address = address
        self.kit = cx.Kit(self.kit_dir) if self.is_kit else None
        self.property_id = self.kit.find_property_id(property_id, model_id, address, lat, lon) if self.kit else property_id
        if self.kit and not self.property_id:
            from . import AdapterError
            raise AdapterError("COSTAR_MODE=live: the building was not found in the CoStar extract (set COSTAR_PROPERTY_ID, "
                               "or give data/live/demo_building.json a property_id, model_id or address)")
        if open_bundle is None:
            path = config.site_context_json()
            open_bundle = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        self._open = open_bundle
        self._site: dict | None = None
        self.report: dict = {}

    @classmethod
    def from_env(cls) -> "CoStarLive":
        from . import AdapterError
        kit = cx.default_kit_dir()
        if not kit.is_dir() or not cx.discover(kit):
            raise AdapterError(f"COSTAR_MODE=live but there is no CoStar extract folder: set COSTAR_KIT_DIR to the unzipped CoStar Data Extract "
                               f"(expected {kit.name}/ with CSV, JSON or JSON Lines files; see docs/TOOLKIT_SWAP.md)")
        b = config.demo_building() or {}
        pid = os.environ.get("COSTAR_PROPERTY_ID") or b.get("property_id")
        address = os.environ.get("COSTAR_PROPERTY_ADDRESS") or b.get("address")
        if not (pid or address or b.get("model_id")):
            raise AdapterError("COSTAR_MODE=live but no building is selected: set COSTAR_PROPERTY_ID (or COSTAR_PROPERTY_ADDRESS), "
                               "or write data/live/demo_building.json")
        return cls(kit, property_id=str(pid) if pid else None, address=address, model_id=b.get("model_id"), lat=b.get("lat"), lon=b.get("lon"))

    def site(self) -> dict:
        if self._site is not None:
            return self._site
        if self.kit:
            rec, self.report = cx.site_record(self.kit, self.property_id)
            where = f"CoStar data kit ({cx.KIT['property'].split('/')[-1]})"
        else:
            rec, self.report = cx.flat_record(self.kit_dir, self.property_id, self.address)
            where = "CoStar Data Extract"
        prop = rec["property"]
        open_prop = self._open.get("property") or {}
        same_building = _dist_m(prop, open_prop) < 300.0
        unavailable = []
        if prop.get("height_m") is None and same_building and open_prop.get("height_m") is not None:
            prop["height_m"] = open_prop["height_m"]
        field_sources = {k: ({"source": where, "synthetic": False} if prop.get(k) is not None else {"source": "not in the CoStar record", "synthetic": False})
                         for k in list(cx.MAPPING["property"]) + ["height_m"]}
        if prop.get("height_m") is not None and prop.get("height_ft") is None:
            field_sources["height_m"] = (self._open.get("field_sources") or {}).get("height_m", {"source": "open data", "synthetic": False})
        for k in ("owner", "property_manager"):
            if prop.get(k):
                field_sources[k] = {"source": where + "; contact name real, phone fake by design in the kit", "synthetic": False}
        if not same_building:
            unavailable.append("public context (footprint, sides, hydrants, stations) for this building is not built: run "
                               "scripts/build_site.py --address \"<street address>\" --city <la|houston> --out data/live/site/<slug>/")
        cross = []
        if same_building and prop.get("stories") and open_prop.get("height_m"):
            per = open_prop["height_m"] / prop["stories"]
            if not 2.6 <= per <= 6.0:
                cross.append({"id": "height", "layers": ["costar", "vexcel"], "text": f"CoStar says {prop['stories']} storeys; the open-data roof height "
                              f"{open_prop['height_m']} m gives {per:.1f} m per storey. Check the storey count or the footprint match."})
        self._site = {"schema": "sandtable.site/1", "placeholder": False,
                      "source_note": "CoStar record (live, from the CoStar data extract) merged with public context from open data. Private: never committed.",
                      "property": prop, "field_sources": field_sources, "tenants": rec["tenants"], "available_spaces": rec["available_spaces"],
                      "comparables": rec["comparables"],
                      **({k: self._open.get(k) for k in ("footprint", "frame", "sides", "context", "open_data") if k in self._open} if same_building else
                         {"footprint": None, "frame": None, "sides": {}, "context": {"hydrants": [], "fire_stations": [], "apparatus_positions": [], "entrances": [], "permits": []},
                          "open_data": []}),
                      "cross_checks": cross, "unavailable": unavailable}
        return self._site

    def get_property(self) -> dict:
        s = self.site()
        return {**s["property"], "field_sources": s["field_sources"]}

    def list_tenants(self, floor: int | None = None) -> list[dict]:
        return [t for t in self.site()["tenants"] if floor is None or t.get("floor") == floor]

    def list_available_space(self, floor: int | None = None) -> list[dict]:
        return [a for a in self.site()["available_spaces"] if floor is None or a.get("floor") == floor]

    def file_correction(self, field: str, current: Any, proposed: Any, evidence: dict) -> dict:
        """The extract is read-only: corrections are kept in a private local file for CoStar research, never sent."""
        from . import _append
        item = {"id": None, "field": field, "current": current, "proposed": proposed, "evidence": evidence,
                "status": "filed locally (live mode: the CoStar extract is read-only; written to data/live/record_corrections.json for CoStar research, not sent)"}
        _append(config.CORRECTIONS_JSON, item, "corr")
        return item
