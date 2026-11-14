"""CoStar Data Extract reader: the CoStar layer is a folder of files, not an HTTP API.

Two shapes are read:
  * the CoStar data kit given to hackathon teams: JSON Lines datasets under `data/` (commercial-property,
    location-occupancy, company-detail, commercial-sale-comp, commercial-listing-summary), integer enums decoded
    with `schemas/enums.json`, and `commercial-properties.csv` (PropertyId -> MatterportModelId);
  * any other extract folder (CSV, JSON, JSON Lines, Excel with openpyxl, Parquet with pyarrow): tables are
    discovered, classified and their columns matched fuzzily against the same mapping table.

Everything goes through ONE mapping table, MAPPING below. Per section (property, tenant, space, sale_comp,
lease_comp), our field -> a list of candidates tried in order. A candidate is a dotted path into a record (kit
JSONL, nested JSON) or a column name of a flat file (matched ignoring case, spaces and punctuation, then by
similarity), optionally followed by `|converter` (see CONVERTERS; `enum:<Name>` decodes a kit enum). Edit the
right-hand side; nothing else needs to change. `inspect` prints what was and was not mapped.

Privacy: the kit is sponsor data. Indexes and per-property caches are written only under the private root
(config.LIVE_DIR). The command line prints file names, counts, ids of nothing and column NAMES only, never
row values; `find` writes its matches to a file under data/live/ and prints the count and the path.

    python -m sandtable.adapters.costar_extract inspect [DIR]
    python -m sandtable.adapters.costar_extract find [--city la|houston] [--type office] [--has-model] [--min-stories N]
    python -m sandtable.adapters.costar_extract record [--property-id ID]     counts only; the record stays in memory
    python -m sandtable.adapters.costar_extract unzip ZIP [--out FOLDER]   outside the repo, or data/live/costar; password from COSTAR_KIT_ZIP_PASSWORD
    python -m sandtable.adapters.costar_extract denylist [PROPERTY_ID]   writes data/live/denylist_building.txt for the publish check
"""
from __future__ import annotations

import csv
import difflib
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator

from .. import config

# ---- the one mapping table -------------------------------------------------------------------------------------
MAPPING: dict[str, dict[str, list[str]]] = {
    "property": {
        "property_id": ["id", "PropertyId", "Property ID", "CoStar Property ID", "PropID"],
        "name": ["buildingName", "Building Name", "Property Name"],
        "address": ["primaryAddress|street", "Property Address", "Building Address", "Street Address", "Address"],
        "city": ["primaryAddress.city", "City"],
        "state": ["primaryAddress.state", "State"],
        "zip": ["primaryAddress.postcode", "Zip", "Postal Code", "Zip Code"],
        "lat": ["primaryAddress.latitude|float", "Latitude", "Lat"],
        "lon": ["primaryAddress.longitude|float", "Longitude", "Lon", "Long"],
        "property_type": ["propertyType|enum:PropertyType", "Property Type", "PropertyType"],
        "property_subtype": ["propertySubtype|enum:PropertySubtype", "Secondary Type", "Property Subtype"],
        "building_class": ["buildingClass|enum:BuildingClass", "Building Class", "Class"],
        "rba_sf": ["rentableBuildingAreaSquareFeet|int", "RBA", "Rentable Building Area", "Building SF", "RBA SF"],
        "gba_sf": ["grossBuildingAreaSquareFeet|int", "GBA", "Gross Building Area"],
        "typical_floor_sf": ["grossTypicalFloorSquareFeet|int", "Typical Floor Size", "Typical Floor SF"],
        "stories": ["numStoriesAboveGrade|int", "Number Of Stories", "Stories", "Floors"],
        "year_built": ["constructionEvents|year:ConstructionComplete", "Year Built"],
        "year_renovated": ["constructionEvents|year:RenovationFinished", "Year Renovated"],
        "construction": ["buildingConstructionMaterial|enum:BuildingConstructionMaterialType", "Construction Material", "Construction"],
        "sprinklers": ["|sprinklers", "Sprinklers", "Sprinkler Type"],
        "elevators": ["|elevators", "Number Of Elevators", "Elevators"],
        "parking_spaces": ["parking|parking_total", "Number Of Parking Spaces", "Parking Spaces"],
        "parking_ratio": ["parkingRatio|float", "Parking Ratio"],
        "percent_leased": ["Percent Leased", "% Leased"],
        "height_ft": ["buildingHeightInFeet|float", "Building Height", "Height (ft)"],
        "owner": ["|contact:TrueOwner,RecordedOwner", "True Owner", "Owner Name", "Recorded Owner"],
        "property_manager": ["|contact:PropertyManager", "Property Manager", "Property Manager Name"],
        "matterport_model_ids": ["|matterport_models", "Matterport Model Id", "MatterportModelId"],
    },
    "tenant": {
        "tenant_id": ["id", "Tenant Id", "TenantId", "Location Occupancy Id"],
        "name": ["company.name", "Tenant Name", "Tenant", "Company Name", "Occupant"],
        "suite": ["space.suite", "company.suite", "Suite", "Suite Number", "Unit"],
        "floor": ["_floor|int", "Floor", "Floor Number"],
        "sf": ["space.squareFeetOccupied|int", "SF Occupied", "Square Feet Occupied", "Occupied SF", "Size"],
        "employees": ["company.numEmployees|int", "Number Of Employees", "Employees", "Headcount"],
        "industry": ["industryType|enum:IndustryType", "company.profile.industryType|enum:IndustryType", "Industry", "Industry Type", "Tenant Industry"],
        "naics": ["NAICS", "NAICS Code", "Naics Code"],
        "lease_expiration": ["leaseDeal.expirationDate|date", "Lease Expiration", "Expiration Date", "Lease Expiration Date"],
        "move_in": ["moveInDate|date", "fromDate|date", "Move In Date", "Commencement Date"],
    },
    "space": {
        "suite": ["suite", "Suite", "Suite Number", "Unit"],
        "floor": ["_floor|int", "Floor", "Floor Number"],
        "sf": ["squareFeetMax|int", "squareFeetMin|int", "SF Available", "Available SF", "Square Feet Available", "Size"],
        "status": ["_status", "Space Status", "Status", "Availability"],
        "available_on": ["availableOnDate|date", "Date Available", "Available Date"],
        "space_use": ["spaceUse|enum:SpaceUse", "Space Use", "Use"],
    },
    "sale_comp": {
        "sale_id": ["id", "Sale Id", "Comp Id"],
        "sold_date": ["soldDate|date", "Sale Date", "Sold Date"],
        "price": ["soldForPrice|int", "Sale Price", "Price"],
        "price_status": ["salePriceStatus|enum:SalePriceStatus", "Sale Price Status"],
        "sf": ["_entity.totalSquarefeet|int", "Building SF", "Square Feet"],
        "percent_leased": ["_entity.percentLeased|float", "Percent Leased"],
        "sale_type": ["saleType|enum:SaleType", "Sale Type"],
    },
    "lease_comp": {
        "deal_id": ["leaseDeal.id", "Deal Id", "Lease Id"],
        "signed": ["leaseDeal.signDate|date", "Sign Date", "Lease Sign Date"],
        "sf": ["_sf|int", "Square Feet Leased", "SF Leased"],
        "rate_per_year": ["leaseDeal.rateActualPerYear|float", "leaseDeal.askingRatePerYear|float", "Rent", "Rate", "Asking Rent"],
        "term_months": ["leaseDeal.leaseTerm|int", "Lease Term", "Term"],
        "lease_type": ["leaseDeal.leaseDealType|enum:LeaseDealType", "Lease Type", "Deal Type"],
        "expiration": ["leaseDeal.expirationDate|date", "Expiration Date"],
    },
}

# hints that classify a table of an unseen extract (file name, then columns)
TABLE_HINTS = {"property": ("propert", "building"), "tenant": ("tenant", "occupan", "occupier"), "space": ("avail", "space", "listing"),
               "sale_comp": ("sale", "comp"), "lease_comp": ("lease",)}

# Life-hazard rule table, shared with scripts/build_site.py (the fixture). Inferred from the industry, never observed.
# (NAICS prefix, words in the industry text, life hazard, hazmat)
HAZARD_RULES = [
    ("6220", ("hospital",), "hospital outpatient department: patients may be non-ambulatory or under treatment", "medical gases, sharps and biohazard waste likely"),
    ("62121", ("dentist", "dental"), "dental patients may be sedated or mid-procedure", "medical oxygen and nitrous oxide cylinders likely"),
    ("62151", ("laborator",), "few occupants; staff only", "laboratory reagents, biological samples, compressed gases likely"),
    ("62134", ("physical therap",), "therapy patients with limited mobility", None),
    ("62139", (), "patients with limited mobility possible", None),
    ("62132", ("optometr",), "patients with dilated pupils or impaired vision possible", None),
    ("6219", ("ambulatory",), "ambulatory care: patients may need assistance", "medical gases possible"),
    ("6211", ("physician", "medical office", "clinic"), "medical patients, some with limited mobility", "medical oxygen possible"),
    ("6244", ("child day care", "child care", "daycare"), "young children who need assisted evacuation", None),
    ("6233", ("assisted living", "nursing", "elderly"), "elderly residents who need assisted evacuation", None),
    (None, ("healthcareandsocialassistance", "health care and social assistance"), "health care or social assistance: occupants may need assistance", "medical gases possible"),
    (None, ("educationalservices", "educational services"), "students; classes may need staff to evacuate them", None),
    (None, ("accommodationandfoodservices", "accommodation and food services"), "guests or diners unfamiliar with the exits", "cooking gas possible"),
    (None, ("manufacturing",), None, "process chemicals or machinery possible"),
    (None, ("utilities", "miningquarryingandoilandgasextraction"), None, "fuel, gas or electrical hazards possible"),
]


def infer_hazard(naics: str | None, industry: str | None) -> tuple[str | None, str | None, str | None]:
    """(life_hazard, hazmat, basis) from the NAICS code or the industry text, by the rule table."""
    code = str(naics or "")
    text = re.sub(r"[^a-z ]", "", str(industry or "").lower())
    for prefix, words, life, hazmat in HAZARD_RULES:
        if prefix and code.startswith(prefix):
            return life, hazmat, "inferred from the NAICS category by a rule table, not observed"
    for prefix, words, life, hazmat in HAZARD_RULES:
        if any(w in text or w.replace(" ", "") in text.replace(" ", "") for w in words):
            return life, hazmat, "inferred from the industry by a rule table, not observed"
    return None, None, None


# ---- converters -------------------------------------------------------------------------------------------------
_ORD = {w: i for i, w in enumerate("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth "
                                   "sixteenth seventeenth eighteenth nineteenth".split(), 1)}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_TENS_ORD = {"twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50, "sixtieth": 60, "seventieth": 70, "eightieth": 80, "ninetieth": 90}


def floor_number(name: Any) -> int | None:
    """'ThirdFloor' -> 3, 'TwentyFirstFloor' -> 21, 'Ground'/'Lobby'/'Plaza' -> 1, '3', '3rd', 'Floor 3' -> 3, basements negative."""
    if name is None:
        return None
    if isinstance(name, (int, float)):
        return int(name)
    s = str(name).strip()
    m = re.search(r"-?\d+", s)
    if m:
        return int(m.group())
    low = re.sub(r"(?<!^)(?=[A-Z])", " ", s).lower().replace("floor", "").strip()
    if low in ("ground", "lobby", "plaza", "ground garden", "principal"):
        return 1
    if low in ("basement", "lower level", "concourse"):
        return -1
    total = 0
    for w in low.replace("one hundred", "onehundred").split():
        if w == "onehundred":
            total += 100
        elif w in _TENS:
            total += _TENS[w]
        elif w in _TENS_ORD:
            total += _TENS_ORD[w]
        elif w in _ORD:
            total += _ORD[w]
        elif w == "hundredth":
            total += 100
        else:
            return None
    return total or None


def _to_int(v):
    try:
        return int(round(float(str(v).replace(",", "").replace("$", "").strip()))) if v not in (None, "") else None
    except ValueError:
        return None


def _to_float(v):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip()) if v not in (None, "") else None
    except ValueError:
        return None


class Enums:
    def __init__(self, path: Path | None):
        self.table: dict = {}
        if path and path.is_file():
            self.table = json.loads(path.read_text(encoding="utf-8"))

    def name(self, enum: str, value: Any) -> Any:
        if value is None:
            return None
        names = self.table.get(enum)
        if not names:
            return value
        n = names.get(str(value), value) if isinstance(names, dict) else value
        return None if n in ("Unknown", "None", "UnknownFloor") else n

    def value(self, enum: str, name: str) -> int | None:
        for k, v in (self.table.get(enum) or {}).items():
            if v == name:
                return int(k)
        return None


def convert(conv: str, value: Any, record: dict, ctx: "Context") -> Any:
    if not conv:
        return value
    if conv == "int":
        return _to_int(value)
    if conv == "float":
        return _to_float(value)
    if conv == "date":
        return str(value)[:10] if value else None
    if conv.startswith("enum:"):
        return ctx.enums.name(conv[5:], value) if isinstance(value, int) else value
    if conv.startswith("year:"):
        want = ctx.enums.value("ConstructionEventType", conv[5:])
        years = [e.get("year") for e in (value or []) if isinstance(e, dict) and e.get("eventType") == want and e.get("year")]
        return max(years) if years else None
    if conv == "street":
        a = value or {}
        parts = [a.get("streetNumber"), a.get("streetDirection"), a.get("streetName"), a.get("streetType"), a.get("streetSuffix")]
        return " ".join(str(p) for p in parts if p) or None
    if conv == "sprinklers":
        has, kind = record.get("hasSprinklers"), ctx.enums.name("SprinklerType", record.get("sprinklerType"))
        if has is None and not kind:
            return None
        return ("Yes" + (f" ({kind})" if kind else "")) if has or kind else "No"
    if conv == "elevators":
        out = []
        for block in ("propertyOffice", "propertyRetail", "propertyIndustrial", "propertyFlex", "propertyHospitality"):
            for e in (((record.get(block) or {}).get("structural") or {}).get("elevators") or []):
                if e.get("elevatorState") == 2 or not e.get("numElevators"):
                    continue
                out.append(f"{e['numElevators']} {(ctx.enums.name('ElevatorType', e.get('elevatorType')) or 'unspecified').lower()}")
        return ", ".join(out) or None
    if conv == "parking_total":
        total = sum(int(p.get("totalSpaces") or 0) for p in (value or []) if isinstance(p, dict))
        return total or None
    if conv.startswith("contact:"):
        roles = {ctx.enums.value("ContactRole", r) for r in conv[8:].split(",")}
        for c in record.get("contacts") or []:
            if c.get("contactRole") in roles and not c.get("endDate"):
                company = ctx.companies.get(c.get("locationId")) or {}
                return {"name": company.get("name"), "phone": c.get("contactPhoneNumber"),
                        "role": ctx.enums.name("ContactRole", c.get("contactRole")),
                        "note": "name from the kit's company record; phone numbers in the CoStar kit are fake by design"}
        return None
    if conv == "matterport_models":
        ids = []
        for a in record.get("attachments") or []:
            if a.get("videoExternalReferenceType") in (36, 234) and a.get("videoKey"):
                ids.append(a["videoKey"])
        return sorted(set(ids) | set(ctx.models.get(str(record.get("id")), []))) or None
    raise KeyError(f"unknown converter {conv!r}")


def dig(obj: Any, dotted: str) -> tuple[bool, Any]:
    if dotted == "":
        return True, obj
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return False, None
        obj = obj[part]
    return True, obj


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def match_column(columns: Iterable[str], candidates: list[str], cutoff: float = 0.86) -> str | None:
    """The column of a flat table that best matches one of the candidates (exact after normalising, then similarity)."""
    cols = {norm(c): c for c in columns}
    for cand in candidates:
        path = cand.split("|", 1)[0]
        if path and norm(path) in cols:
            return cols[norm(path)]
    for cand in candidates:
        path = cand.split("|", 1)[0]
        if not path or "." in path:
            continue
        close = difflib.get_close_matches(norm(path), list(cols), n=1, cutoff=cutoff)
        if close:
            return cols[close[0]]
    return None


class Context:
    def __init__(self, enums: Enums, companies: dict | None = None, models: dict | None = None):
        self.enums, self.companies, self.models = enums, companies or {}, models or {}


def map_record(section: str, record: dict, ctx: Context, flat_columns: dict[str, str] | None = None) -> tuple[dict, dict]:
    """Our fields from one record through MAPPING[section]. Returns (fields, where): `where[field]` is the path or column used."""
    out, where = {}, {}
    for field, cands in MAPPING[section].items():
        out[field], where[field] = None, None
        if flat_columns is not None:                         # a flat file: the column was matched once per table
            col = flat_columns.get(field)
            if col is not None:
                # a flat column holds plain text: apply the field's plain type (int, float, date) if it has one
                conv = next((c.split("|", 1)[1] for c in cands if c.endswith(("|int", "|float", "|date"))), "")
                out[field], where[field] = convert(conv, record.get(col), record, ctx), col
                if field == "floor":
                    out[field] = floor_number(record.get(col))
            continue
        for cand in cands:
            path, _, conv = cand.partition("|")
            found, value = dig(record, path)
            if not found:
                continue
            value = convert(conv, value, record, ctx)
            if value is not None or path:
                out[field], where[field] = value, cand
                if value is not None:
                    break
    return out, where


# ---- discovery ----------------------------------------------------------------------------------------------------
TABLE_SUFFIXES = (".csv", ".tsv", ".txt", ".json", ".jsonl", ".ndjson", ".xlsx", ".xlsm", ".parquet")


def discover(root: Path) -> list[Path]:
    root = Path(root)
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).parts
        if not p.is_file() or any(part.startswith(("_", ".")) for part in rel) or "records" in rel:
            continue
        if p.suffix.lower() in TABLE_SUFFIXES and not p.name.endswith(".schema.json") and p.name != "enums.json":
            out.append(p)
    return out


def _open_text(path: Path) -> io.TextIOBase:
    raw = path.read_bytes()[:4096]
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            raw.decode(enc)
            return open(path, encoding=enc, newline="")
        except UnicodeDecodeError:
            continue
    return open(path, encoding="latin-1", newline="")


def iter_rows(path: Path) -> Iterator[dict]:
    """Rows of one table as dicts (a JSONL/JSON record, a CSV row, an Excel row)."""
    suf = path.suffix.lower()
    if suf in (".jsonl", ".ndjson"):
        with open(path, "rb") as f:
            for line in f:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        yield rec
    elif suf == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            data = next((v for k, v in data.items() if isinstance(v, list) and v and isinstance(v[0], dict)), [data])
        yield from (r for r in data if isinstance(r, dict))
    elif suf in (".csv", ".tsv", ".txt"):
        with _open_text(path) as f:
            sample = f.read(8192)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
            except csv.Error:
                dialect = csv.excel_tab if suf == ".tsv" else csv.excel
            yield from csv.DictReader(f, dialect=dialect)
    elif suf in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError as exc:
            raise RuntimeError(f"{path.name}: install openpyxl to read Excel files (pip install openpyxl)") from exc
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            rows = ws.iter_rows(values_only=True)
            header = next(rows, None)
            if not header:
                continue
            names = [str(h) if h is not None else f"col{i}" for i, h in enumerate(header)]
            for r in rows:
                if any(v is not None for v in r):
                    yield {**dict(zip(names, r)), "_sheet": ws.title}
    elif suf == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError(f"{path.name}: install pyarrow to read Parquet files") from exc
        yield from pq.read_table(path).to_pylist()


def columns_and_count(path: Path, sample: int = 300) -> tuple[list[str], int]:
    cols: dict[str, None] = {}
    n = 0
    for row in iter_rows(path):
        if n < sample:
            for k in row:
                cols.setdefault(str(k), None)
        n += 1
    return list(cols), n


def classify(path: Path, columns: list[str]) -> tuple[str | None, dict[str, str]]:
    """The section a flat table most likely holds, and its column match per field."""
    best = (0.0, None, {})
    for section, fields in MAPPING.items():
        matched = {f: c for f, cands in fields.items() if (c := match_column(columns, cands))}
        score = len(matched) / max(1, len(fields)) + (0.5 if any(h in path.stem.lower() for h in TABLE_HINTS[section]) else 0.0)
        if score > best[0]:
            best = (score, section, matched)
    return (best[1], best[2]) if best[0] >= 0.3 else (None, {})


# ---- the kit --------------------------------------------------------------------------------------------------------
KIT = {"property": "data/commercial-property.jsonl", "occupancy": "data/location-occupancy.jsonl", "company": "data/company-detail.jsonl",
       "sale_comp": "data/commercial-sale-comp.jsonl", "listing": "data/commercial-listing-summary.jsonl",
       "models_csv": "commercial-properties.csv", "enums": "schemas/enums.json"}
CITIES = {"la": 20, "los-angeles": 20, "houston": 14}          # research market ids named in the kit's index


def default_kit_dir() -> Path:
    """COSTAR_KIT_DIR, else the bench drop zone's unpacked kit when it exists, else data/live/costar."""
    if os.environ.get("COSTAR_KIT_DIR"):
        return Path(os.environ["COSTAR_KIT_DIR"])
    bench = config.ROOT.parent / ".secrets" / "costar-kit"
    return bench if bench.is_dir() else config.LIVE_DIR / "costar"


def is_kit(root: Path) -> bool:
    return (Path(root) / KIT["property"]).is_file()


def _cache_dir() -> Path:
    d = config.LIVE_DIR / "costar" / "_index"
    config.guard_write(d / "x")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stream(path: Path, needle: re.Pattern | None = None) -> Iterator[dict]:
    with open(path, "rb") as f:
        for line in f:
            if needle is not None and not needle.search(line):
                continue
            line = line.strip()
            if line:
                yield json.loads(line)


class Kit:
    """The CoStar data kit, indexed once into data/live/costar/_index/ (never 400 MB per request)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        if not is_kit(self.root):
            raise FileNotFoundError(f"not a CoStar data kit (no {KIT['property']}) in {self.root}")
        self.enums = Enums(self.root / KIT["enums"])
        self._models: dict[str, list[str]] | None = None

    def models(self) -> dict[str, list[str]]:
        """PropertyId -> Matterport model ids, from commercial-properties.csv."""
        if self._models is None:
            self._models = {}
            path = self.root / KIT["models_csv"]
            if path.is_file():
                with _open_text(path) as f:
                    for row in csv.DictReader(f):
                        pid, mid = (row.get("PropertyId") or "").strip(), (row.get("MatterportModelId") or "").strip()
                        if pid:
                            self._models.setdefault(pid, [])
                            if mid:
                                self._models[pid].append(mid)
        return self._models

    def index(self) -> list[dict]:
        """One small row per property (id, market, type, stories, coordinates, model count): built once, then cached."""
        cache = _cache_dir() / "properties.json"
        src = self.root / KIT["property"]
        if cache.is_file() and cache.stat().st_mtime >= src.stat().st_mtime:
            return json.loads(cache.read_text(encoding="utf-8"))
        rows, models = [], self.models()
        for rec in _stream(src):
            a = rec.get("primaryAddress") or {}
            rows.append({"id": rec.get("id"), "market": a.get("researchMarketId"), "type": self.enums.name("PropertyType", rec.get("propertyType")),
                         "stories": rec.get("numStoriesAboveGrade"), "rba_sf": rec.get("rentableBuildingAreaSquareFeet"),
                         "lat": a.get("latitude"), "lon": a.get("longitude"), "street": convert("street", a, rec, Context(self.enums)),
                         "models": len(models.get(str(rec.get("id")), []))})
        config.guard_write(cache)
        cache.write_text(json.dumps(rows), encoding="utf-8")
        return rows

    def find_property_id(self, property_id: str | None = None, model_id: str | None = None, address: str | None = None,
                         lat: float | None = None, lon: float | None = None) -> str | None:
        if property_id:
            return str(property_id)
        if model_id:
            for pid, mids in self.models().items():
                if model_id in mids:
                    return pid
        rows = self.index()
        if address:
            key = address_key(address)
            hits = [r for r in rows if r.get("street") and address_key(r["street"]) == key]
            if lat is not None and lon is not None and len(hits) > 1:
                hits.sort(key=lambda r: _dist2(r, lat, lon))
            if hits:
                return str(hits[0]["id"])
        if lat is not None and lon is not None:
            near = min((r for r in rows if r.get("lat") is not None), key=lambda r: _dist2(r, lat, lon), default=None)
            if near and _dist2(near, lat, lon) ** 0.5 * 111320 < 60:
                return str(near["id"])
        return None

    def raw(self, property_id: str) -> dict:
        """The property's records from every dataset, cached per property under data/live/costar/_index/."""
        pid = int(property_id)
        enums = _cache_dir() / "enums.json"                # the private index then works without the kit (a laptop with the bundle)
        if not enums.is_file() and (self.root / KIT["enums"]).is_file():
            config.guard_write(enums)
            enums.write_text((self.root / KIT["enums"]).read_text(encoding="utf-8"), encoding="utf-8")
        cache = _cache_dir() / f"property_{pid}.json"
        if cache.is_file() and cache.stat().st_mtime >= (self.root / KIT["property"]).stat().st_mtime:
            return json.loads(cache.read_text(encoding="utf-8"))
        pid_re = re.compile(rb'"(?:propertyId|id)":\s*%d[,}\s]' % pid)
        prop = next((r for r in _stream(self.root / KIT["property"], pid_re) if r.get("id") == pid), None)
        if prop is None:
            raise KeyError(f"property id {pid} is not in the kit")
        occ = [r for r in _stream(self.root / KIT["occupancy"], pid_re) if r.get("propertyId") == pid]
        listings = [r for r in _stream(self.root / KIT["listing"], pid_re)
                    if r.get("propertyId") == pid or any(s.get("propertyId") == pid for s in ((r.get("forLeaseSummary") or {}).get("spaces") or []))]
        sales = [r for r in _stream(self.root / KIT["sale_comp"], pid_re) if any(e.get("propertyId") == pid for e in r.get("entities") or [])]
        want = {r["tenantLocationId"] for r in occ if r.get("tenantLocationId")} | {c["locationId"] for c in prop.get("contacts") or [] if c.get("locationId")}
        companies = {}
        if want:
            id_re = re.compile(rb'"id":\s*(?:%s)[,}\s]' % b"|".join(str(i).encode() for i in sorted(want)))
            companies = {r["id"]: r for r in _stream(self.root / KIT["company"], id_re) if r.get("id") in want}
        raw = {"property": prop, "occupancies": occ, "listings": listings, "sales": sales, "companies": {str(k): v for k, v in companies.items()},
               "models": self.models().get(str(pid), [])}
        config.guard_write(cache)
        cache.write_text(json.dumps(raw), encoding="utf-8")
        return raw


class CachedKit(Kit):
    """The private index alone (data/live/costar/_index/: per-property records and the enums), when the kit is not on this machine."""

    def __init__(self, index_dir: Path | None = None):
        self.root = Path(index_dir or config.LIVE_DIR / "costar" / "_index")
        if not (self.root / "enums.json").is_file() or not list(self.root.glob("property_*.json")):
            raise FileNotFoundError("no private CoStar index (data/live/costar/_index/ with enums.json and a property record)")
        self.enums = Enums(self.root / "enums.json")
        self._models = None

    def models(self) -> dict[str, list[str]]:
        if self._models is None:
            self._models = {}
            for f in self.root.glob("property_*.json"):
                raw = json.loads(f.read_text(encoding="utf-8"))
                self._models[str(raw["property"]["id"])] = list(raw.get("models") or [])
        return self._models

    def index(self) -> list[dict]:
        return [{"id": int(pid), "street": None, "lat": None, "lon": None} for pid in self.models()]

    def raw(self, property_id: str) -> dict:
        path = self.root / f"property_{int(property_id)}.json"
        if not path.is_file():
            raise KeyError(f"property id {property_id} is not in the private index and the kit is not on this machine")
        return json.loads(path.read_text(encoding="utf-8"))


def _dist2(r: dict, lat: float, lon: float) -> float:
    import math
    return (r["lat"] - lat) ** 2 + ((r["lon"] - lon) * math.cos(math.radians(lat))) ** 2


_ABBR = {"street": "st", "avenue": "ave", "av": "ave", "drive": "dr", "boulevard": "blvd", "road": "rd", "lane": "ln", "place": "pl", "court": "ct",
         "parkway": "pkwy", "highway": "hwy", "freeway": "fwy", "suite": "ste", "north": "n", "south": "s", "east": "e", "west": "w", "circle": "cir",
         "terrace": "ter", "square": "sq", "plaza": "plz", "center": "ctr", "centre": "ctr"}


def address_key(address: str) -> str:
    """'1234 N. Main Street, Suite 300' -> '1234 n main st' (house number + street, normalised; city and unit dropped)."""
    s = str(address).split(",")[0].lower()
    s = re.sub(r"\b(ste|suite|unit|#)\s*\S+", "", s)
    words = [_ABBR.get(w, w) for w in re.sub(r"[^a-z0-9 ]", " ", s).split()]
    return " ".join(words)


def site_record(kit: Kit, property_id: str) -> tuple[dict, dict]:
    """(record, report): the sandtable.site/1 record fields from the kit, and what mapped and what did not."""
    raw = kit.raw(property_id)
    ctx = Context(kit.enums, {int(k): v for k, v in raw["companies"].items()}, {str(raw["property"]["id"]): raw.get("models") or []})
    prop_raw = raw["property"]
    prop, where = map_record("property", prop_raw, ctx)
    prop["property_id"] = str(prop["property_id"]) if prop["property_id"] is not None else None
    prop["height_m"] = round(prop["height_ft"] * 0.3048, 1) if prop.get("height_ft") else None
    floors = {f.get("propertyFloorId"): floor_number(kit.enums.name("FloorName", f.get("floorName"))) for f in prop_raw.get("floors") or []}
    prop["floors_on_record"] = sorted({n for n in floors.values() if n is not None}) or None
    prop["contacts"] = [{"role": kit.enums.name("ContactRole", c.get("contactRole")), "name": (ctx.companies.get(c.get("locationId")) or {}).get("name"),
                         "phone": c.get("contactPhoneNumber"), "phone_note": "fake in the CoStar kit (by design)"}
                        for c in prop_raw.get("contacts") or [] if not c.get("endDate")]
    tenants, lease_comps, t_where = [], [], {}
    for occ in raw["occupancies"]:
        company = ctx.companies.get(occ.get("tenantLocationId")) or {}
        spaces = occ.get("spaces") or [{}]
        current = not occ.get("toDate")
        for i, space in enumerate(spaces):
            row = {**occ, "company": company, "space": space, "_floor": floors.get(space.get("propertyFloorId"))}
            if occ.get("leaseDeal") and i == 0:
                lc, _ = map_record("lease_comp", {**row, "_sf": sum(int(s.get("squareFeetOccupied") or 0) for s in spaces) or None}, ctx)
                lease_comps.append({**lc, "source": "CoStar data kit (location-occupancy lease deal)", "synthetic": False})
            if not current:
                continue
            t, t_where = map_record("tenant", row, ctx)
            t["tenant_id"] = f"{t['tenant_id']}-{i + 1}" if len(spaces) > 1 else str(t["tenant_id"])
            if t["floor"] is None and space.get("propertyFloorId") is None and t.get("suite"):
                t["floor"] = floor_number(str(t["suite"])[:1]) if str(t["suite"])[:1].isdigit() and len(str(t["suite"])) >= 3 else None
            t["suite"] = str(t["suite"]) if t["suite"] is not None else None
            life, hazmat, basis = infer_hazard(t.get("naics"), t.get("industry"))
            t.update({"life_hazard": life, "hazmat": hazmat, "hazard_basis": basis, "source": "CoStar data kit (location-occupancy + company-detail)", "synthetic": False})
            tenants.append(t)
    spaces_out, s_where = [], {}
    for lst in raw["listings"]:
        status = kit.enums.name("ListingStatus", lst.get("listingStatus"))
        for sp in (lst.get("forLeaseSummary") or {}).get("spaces") or []:
            if sp.get("propertyId") not in (None, int(property_id)):
                continue
            row = {**sp, "_floor": floors.get(sp.get("propertyFloorId")) or floor_number(kit.enums.name("FloorName", sp.get("floorName"))),
                   "_status": " / ".join(x for x in (status, kit.enums.name("AvailabilityType", sp.get("availabilityType"))) if x) or None}
            s, s_where = map_record("space", row, ctx)
            s["suite"] = str(s["suite"]) if s["suite"] is not None else None
            spaces_out.append({**s, "source": "CoStar data kit (commercial-listing-summary)", "synthetic": False})
    sales = []
    for sc in raw["sales"]:
        ent = next((e for e in sc.get("entities") or [] if e.get("propertyId") == int(property_id)), {})
        s, _ = map_record("sale_comp", {**sc, "_entity": ent}, ctx)
        sales.append({**s, "source": "CoStar data kit (commercial-sale-comp)", "synthetic": False})
    record = {"property": prop, "tenants": tenants, "available_spaces": spaces_out, "comparables": {"sales": sales, "leases": lease_comps},
              "field_where": where}
    report = {"property_fields_mapped": sorted(k for k, v in prop.items() if v is not None and k in MAPPING["property"]),
              "property_fields_empty": sorted(k for k in MAPPING["property"] if prop.get(k) is None),
              "tenants_current": len(tenants), "occupancies": len(raw["occupancies"]), "available_spaces": len(spaces_out),
              "sale_comps": len(sales), "lease_comps": len(lease_comps), "companies": len(raw["companies"]),
              "matterport_models": len(raw.get("models") or []),
              "tenant_fields_empty_everywhere": sorted(f for f in MAPPING["tenant"] if tenants and all(t.get(f) is None for t in tenants))}
    return record, report


# ---- any other extract folder (flat files) ---------------------------------------------------------------------------
def flat_record(root: Path, property_id: str | None = None, address: str | None = None) -> tuple[dict, dict]:
    """The record from an unseen extract of flat tables: classify each table, match columns, filter to the property."""
    tables: dict[str, list[tuple[Path, dict[str, str]]]] = {}
    unclassified = []
    for path in discover(root):
        cols, _n = columns_and_count(path, sample=50)
        section, matched = classify(path, cols)
        if section is None:
            unclassified.append(path.name)
        else:
            tables.setdefault(section, []).append((path, matched))
    ctx = Context(Enums(None))
    key = address_key(address) if address else None

    def belongs(row: dict, matched: dict) -> bool:
        pid_col = match_column(row.keys(), MAPPING["property"]["property_id"])
        if property_id and pid_col and str(row.get(pid_col)).strip() == str(property_id):
            return True
        addr_col = match_column(row.keys(), MAPPING["property"]["address"])
        if key and addr_col and row.get(addr_col) and address_key(str(row[addr_col])) == key:
            return True
        return not property_id and not key or (pid_col is None and addr_col is None)

    prop, where = {}, {}
    for path, matched in tables.get("property", []):
        for row in iter_rows(path):
            if belongs(row, matched):
                prop, where = map_record("property", row, ctx, matched)
                break
        if prop:
            break
    out = {"property": prop, "tenants": [], "available_spaces": [], "comparables": {"sales": [], "leases": []}, "field_where": where}
    for section, dest in (("tenant", out["tenants"]), ("space", out["available_spaces"]), ("sale_comp", out["comparables"]["sales"]), ("lease_comp", out["comparables"]["leases"])):
        for path, matched in tables.get(section, []):
            for row in iter_rows(path):
                if belongs(row, matched):
                    rec, _ = map_record(section, row, ctx, matched)
                    if section == "tenant":
                        life, hazmat, basis = infer_hazard(rec.get("naics"), rec.get("industry"))
                        rec.update({"life_hazard": life, "hazmat": hazmat, "hazard_basis": basis})
                    dest.append({**rec, "source": f"CoStar Data Extract ({path.name})", "synthetic": False})
    report = {"tables": {s: [p.name for p, _ in v] for s, v in tables.items()}, "unclassified_files": unclassified,
              "unmapped_fields": {s: sorted(set(MAPPING[s]) - set().union(*[set(m) for _, m in v])) for s, v in tables.items()},
              "property_found": bool(prop), "tenants": len(out["tenants"]), "available_spaces": len(out["available_spaces"])}
    return out, report


# ---- command line -------------------------------------------------------------------------------------------------------
def _cmd_inspect(root: Path) -> int:
    root = Path(root)
    files = discover(root)
    print(f"CoStar extract at {root.name}/: {len(files)} table files" + (" (the CoStar data kit layout)" if is_kit(root) else ""))
    for path in files:
        try:
            cols, n = columns_and_count(path)
        except Exception as exc:
            print(f"  {path.relative_to(root)}: unreadable ({type(exc).__name__}: {str(exc)[:80]})")
            continue
        section, matched = classify(path, cols)
        print(f"  {path.relative_to(root)}: {n} rows, {len(cols)} columns")
        print("    columns: " + ", ".join(cols))
        if section and not is_kit(root):
            print(f"    looks like: {section}; mapped: " + ", ".join(f"{f}<-{c}" for f, c in matched.items()))
            print(f"    NOT mapped ({section}): " + (", ".join(sorted(set(MAPPING[section]) - set(matched))) or "nothing missing"))
        elif not is_kit(root):
            print("    looks like: nothing in MAPPING (add its column names to a section)")
    if is_kit(root):
        print("  kit layout: mapped through the dotted paths in MAPPING; run `record` to see which fields come out empty")
    return 0


def _cmd_find(root: Path, args: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="costar_extract find")
    ap.add_argument("--city", choices=sorted(CITIES))
    ap.add_argument("--type", help="property type, e.g. office, healthcare, multifamily")
    ap.add_argument("--has-model", action="store_true")
    ap.add_argument("--min-stories", type=int, default=0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(args)
    kit = Kit(root)
    rows = [r for r in kit.index() if (not a.city or r["market"] == CITIES[a.city]) and (not a.type or norm(a.type) == norm(r["type"] or ""))
            and (not a.has_model or r["models"]) and (r["stories"] or 0) >= a.min_stories]
    out = Path(a.out) if a.out else config.LIVE_DIR / "costar" / f"find_{a.city or 'all'}_{norm(a.type or 'any')}{'_model' if a.has_model else ''}_{a.min_stories}.json"
    config.guard_write(out) if config.live_active() else None
    if not config.is_private(out):
        print("refusing to write the matches outside a 'live' directory")
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([{k: r[k] for k in ("id", "type", "stories", "rba_sf", "models", "lat", "lon")} for r in rows], indent=1), encoding="utf-8")
    print(f"{len(rows)} properties match; written to {out.relative_to(config.ROOT) if out.is_relative_to(config.ROOT) else out}")
    return 0


def _cmd_record(root: Path, args: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="costar_extract record")
    ap.add_argument("--property-id")
    a = ap.parse_args(args)
    b = config.demo_building() or {}
    if is_kit(root):
        kit = Kit(root)
        pid = kit.find_property_id(a.property_id or os.environ.get("COSTAR_PROPERTY_ID") or b.get("property_id"), b.get("model_id"),
                                   os.environ.get("COSTAR_PROPERTY_ADDRESS") or b.get("address"), b.get("lat"), b.get("lon"))
        if not pid:
            print("no property selected: set COSTAR_PROPERTY_ID, or write data/live/demo_building.json with property_id, model_id or address")
            return 1
        _rec, report = site_record(kit, pid)
    else:
        _rec, report = flat_record(root, a.property_id or os.environ.get("COSTAR_PROPERTY_ID"), os.environ.get("COSTAR_PROPERTY_ADDRESS") or b.get("address"))
    for k, v in report.items():
        print(f"  {k}: {v}")
    return 0


def _cmd_denylist(root: Path, args: list[str]) -> int:
    """data/live/denylist_building.txt: the building's id, address, names, every tenant and contact name, its model ids.
    scripts/check_publishable.sh refuses any publishable file that contains one of these lines. Prints the count only."""
    b = config.demo_building() or {}
    kit = Kit(root)
    pid = kit.find_property_id(args[0] if args else os.environ.get("COSTAR_PROPERTY_ID") or b.get("property_id"), b.get("model_id"),
                               os.environ.get("COSTAR_PROPERTY_ADDRESS") or b.get("address"), b.get("lat"), b.get("lon"))
    if not pid:
        print("no property selected")
        return 1
    raw = kit.raw(pid)
    prop = raw["property"]
    ctx = Context(kit.enums)
    ids = {str(pid), b.get("model_id")} | set(raw.get("models") or []) | set(convert("matterport_models", None, prop, Context(kit.enums, {}, {})) or [])
    places = {b.get("address"), str((b.get("address") or "").split(",")[0]).strip(), convert("street", prop.get("primaryAddress") or {}, prop, ctx)}
    places |= {convert("street", a, prop, ctx) for a in prop.get("addresses") or []}
    building = {b.get("building_name"), b.get("name"), b.get("model_name"), prop.get("buildingName")} | set(prop.get("aliases") or [])
    names = set()
    for c in raw["companies"].values():
        names |= {c.get("name"), (c.get("profile") or {}).get("companyName")}
    for alt in b.get("alternates") or []:
        if isinstance(alt, dict):
            ids |= {alt.get("model_id"), alt.get("property_id")}
            places.add(alt.get("address"))
    keep, dropped = set(), []
    for kind, group in (("id", ids), ("address", places), ("building name", building), ("tenant or contact name", names)):
        for t in group:
            t = str(t or "").strip()
            if not t:
                continue
            words = len(t.split())
            if kind == "id" and len(t) >= 4:
                keep.add(t)
            elif len(t) < 4:
                dropped.append((t, kind, "shorter than 4 characters"))
            elif kind != "id" and words < 2 and len(t) < 10:
                dropped.append((t, kind, "a single word under 10 characters: too likely to be an ordinary word or a brand"))
            else:
                keep.add(t)
    terms = sorted(keep)
    out = config.LIVE_DIR / "denylist_building.txt"
    config.guard_write(out) if config.live_active() else None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(terms) + "\n", encoding="utf-8")
    (config.LIVE_DIR / "denylist_dropped.txt").write_text("".join(f"{t}\t{kind}\t{why}\n" for t, kind, why in sorted(set(dropped))), encoding="utf-8")
    print(f"{len(terms)} terms written to data/live/denylist_building.txt, {len(set(dropped))} dropped to data/live/denylist_dropped.txt "
          "(private; read by scripts/check_publishable.sh)")
    return 0


def _cmd_unzip(args: list[str]) -> int:
    """Unpack a password-protected extract into a private folder; the password comes from COSTAR_KIT_ZIP_PASSWORD."""
    import argparse
    import zipfile
    ap = argparse.ArgumentParser(prog="costar_extract unzip")
    ap.add_argument("zip")
    ap.add_argument("--out", default=str(config.LIVE_DIR / "costar"))
    a = ap.parse_args(args)
    out = Path(a.out).expanduser().resolve()
    if out.is_relative_to(config.ROOT.resolve()) and not config.is_private(out / "x"):
        print("refusing: inside the repository the extract may only go under a directory named 'live' (e.g. data/live/costar); "
              "outside the repository any folder is fine")
        return 2
    pwd = os.environ.get("COSTAR_KIT_ZIP_PASSWORD", "").encode() or None
    try:
        import pyzipper
        zf = pyzipper.AESZipFile(a.zip)
    except ImportError:
        zf = zipfile.ZipFile(a.zip)
    n = 0
    with zf:
        if pwd:
            zf.setpassword(pwd)
        for info in zf.infolist():
            target = (out / info.filename).resolve()
            if not target.is_relative_to(out):
                print(f"skipped an entry outside the target folder ({info.filename[:40]})")
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            n += 1
    print(f"unpacked {n} files into {out.name}/ (private)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("inspect", "find", "record", "unzip", "denylist"):
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "unzip":
        return _cmd_unzip(rest)
    root = default_kit_dir()
    if rest and not rest[0].startswith("-"):
        root, rest = Path(rest[0]), rest[1:]
    if not Path(root).is_dir():
        print(f"no extract folder at {root} (set COSTAR_KIT_DIR)")
        return 1
    return {"inspect": lambda: _cmd_inspect(root), "find": lambda: _cmd_find(root, rest), "record": lambda: _cmd_record(root, rest),
            "denylist": lambda: _cmd_denylist(root, rest)}[cmd]()


if __name__ == "__main__":
    sys.exit(main())
