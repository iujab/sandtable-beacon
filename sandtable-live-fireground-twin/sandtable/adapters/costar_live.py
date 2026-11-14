"""CoStar live adapter.

WRITTEN BLIND: CoStar has no public API, and the event toolkit (SDKs, MCP servers, OpenAPI
services) was not on this machine. This adapter is therefore deliberately thin, so that
plugging the toolkit in is a matter of filling in one table and (if needed) one function:

  1. COSTAR_FIELD_MAP below: our field name -> the toolkit's field name (dotted paths allowed).
  2. COSTAR_PATHS: the three calls we make (property by id or address, tenants, available space).
  3. `transport`: by default a JSON-over-HTTPS GET with the key in a header. If the toolkit is an
     SDK or an MCP server instead, pass a function with the same signature (see docs/TOOLKIT_SWAP.md).

Everything the rest of Sandtable reads comes out in the `sandtable.site/1` shape, so nothing
else changes. Public context that CoStar does not hold (hydrants, fire stations, the footprint)
keeps coming from the open-data bundle and is merged in here.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

from .. import config

# our field            -> toolkit field (edit the right-hand side at the event)
COSTAR_FIELD_MAP = {
    "property_id": "propertyId",
    "name": "propertyName",
    "address": "address.street",
    "city": "address.city",
    "state": "address.state",
    "zip": "address.postalCode",
    "lat": "location.latitude",
    "lon": "location.longitude",
    "property_type": "propertyType",
    "building_class": "buildingClass",
    "rba_sf": "rentableBuildingArea",
    "typical_floor_sf": "typicalFloorSize",
    "stories": "numberOfStories",
    "year_built": "yearBuilt",
    "year_renovated": "yearRenovated",
    "construction": "constructionMaterial",
    "sprinklers": "sprinklers",
    "elevators": "passengerElevators",
    "parking_spaces": "parkingSpaces",
    "parking_ratio": "parkingRatio",
    "percent_leased": "percentLeased",
    "owner": "trueOwner",
    "property_manager": "propertyManager",
}
TENANT_FIELD_MAP = {"tenant_id": "tenantId", "name": "tenantName", "suite": "suite", "floor": "floor", "sf": "squareFeetOccupied",
                    "industry": "industry", "naics": "naicsCode", "lease_expiration": "leaseExpirationDate"}
SPACE_FIELD_MAP = {"suite": "suite", "floor": "floor", "sf": "squareFeetAvailable", "status": "spaceStatus"}
COSTAR_PATHS = {"property": "/properties/{property_id}", "tenants": "/properties/{property_id}/tenants", "spaces": "/properties/{property_id}/available-spaces",
                "corrections": "/properties/{property_id}/research-requests"}


def _dig(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


class CoStarLive:
    mode = "live"

    def __init__(self, api_key: str, base_url: str, property_id: str, transport: Callable | None = None, open_bundle: dict | None = None):
        self.key, self.base, self.property_id = api_key, base_url.rstrip("/"), property_id
        self._transport = transport or self._http
        self._open = open_bundle if open_bundle is not None else (json.loads(config.SITE_JSON.read_text(encoding="utf-8")) if config.SITE_JSON.exists() else {})
        self._site: dict | None = None

    @classmethod
    def from_env(cls) -> "CoStarLive":
        missing = [k for k in ("COSTAR_API_KEY", "COSTAR_BASE_URL", "COSTAR_PROPERTY_ID") if not os.environ.get(k)]
        if missing:
            from . import AdapterError
            raise AdapterError("COSTAR_MODE=live but these are not set: " + ", ".join(missing) + " (see docs/TOOLKIT_SWAP.md)")
        return cls(os.environ["COSTAR_API_KEY"], os.environ["COSTAR_BASE_URL"], os.environ["COSTAR_PROPERTY_ID"])

    def _http(self, method: str, path: str, body: dict | None = None):
        import httpx
        r = httpx.request(method, self.base + path, json=body, headers={"X-API-KEY": self.key, "Authorization": f"Bearer {self.key}"}, timeout=30.0)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _rows(payload) -> list[dict]:
        if isinstance(payload, list):
            return payload
        for key in ("items", "results", "data", "tenants", "spaces"):
            if isinstance(payload, dict) and isinstance(payload.get(key), list):
                return payload[key]
        return []

    def site(self) -> dict:
        if self._site is None:
            pid = self.property_id
            raw = self._transport("GET", COSTAR_PATHS["property"].format(property_id=pid))
            prop = {ours: _dig(raw, theirs) for ours, theirs in COSTAR_FIELD_MAP.items()}
            prop.setdefault("height_m", (self._open.get("property") or {}).get("height_m"))
            tenants = []
            for row in self._rows(self._transport("GET", COSTAR_PATHS["tenants"].format(property_id=pid))):
                t = {ours: _dig(row, theirs) for ours, theirs in TENANT_FIELD_MAP.items()}
                t.update({"life_hazard": None, "hazmat": None, "source": "CoStar", "synthetic": False})
                tenants.append(t)
            spaces = [{**{ours: _dig(row, theirs) for ours, theirs in SPACE_FIELD_MAP.items()}, "source": "CoStar", "synthetic": False}
                      for row in self._rows(self._transport("GET", COSTAR_PATHS["spaces"].format(property_id=pid)))]
            self._site = {"schema": "sandtable.site/1", "placeholder": False, "source_note": "CoStar record (live) merged with public context from the open-data bundle.",
                          "property": prop, "field_sources": {k: {"source": "CoStar", "synthetic": False} for k in COSTAR_FIELD_MAP},
                          "tenants": tenants, "available_spaces": spaces,
                          **{k: self._open.get(k) for k in ("footprint", "frame", "sides", "context", "open_data") if k in self._open},
                          "cross_checks": []}
        return self._site

    def get_property(self) -> dict:
        s = self.site()
        return {**s["property"], "field_sources": s["field_sources"]}

    def list_tenants(self, floor: int | None = None) -> list[dict]:
        return [t for t in self.site()["tenants"] if floor is None or t.get("floor") == floor]

    def list_available_space(self, floor: int | None = None) -> list[dict]:
        return [a for a in self.site()["available_spaces"] if floor is None or a.get("floor") == floor]

    def file_correction(self, field: str, current: Any, proposed: Any, evidence: dict) -> dict:
        out = self._transport("POST", COSTAR_PATHS["corrections"].format(property_id=self.property_id), {"field": field, "current": current, "proposed": proposed, "evidence": evidence})
        return {"id": (out or {}).get("id"), "field": field, "current": current, "proposed": proposed, "evidence": evidence, "status": "sent to CoStar (live)"}
