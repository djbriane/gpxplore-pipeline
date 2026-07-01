"""BLM National Recreation Site Points normalize adapter.

Functionally equivalent to campgrounds-pipeline-bundle/scripts/filter_blm_campgrounds.py.
BLM encodes reservation/fee/development directly in the Feature Subtype string
(e.g. "Campsite - Developed - Non Reservable - Fee"), so classification is a
structured parse rather than a free-text scan. Supports both the CSV snapshot
and a live GeoJSON FeatureCollection (attributes carry the same field names).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from .. import common

DEVELOPED_SUBTYPES = {
    "Campground",
    "Campsite - Developed - Reservable - Fee",
    "Campsite - Developed - Reservable - No Fee",
    "Campsite - Developed - Non Reservable - Fee",
    "Campsite - Developed - Non Reservable - No Fee",
}
PRIMITIVE_SUBTYPES = {
    "Campsite - Primitive - Reservable - Fee",
    "Campsite - Primitive - Reservable - No Fee",
    "Campsite - Primitive - Non Reservable - Fee",
    "Campsite - Primitive - Non Reservable - No Fee",
    "Campsite - Undeveloped",
}
# Default inclusion set == reference script default (developed + campground only;
# primitive/undeveloped are intentionally left out to stay behavior-compatible).
ALLOWED_SUBTYPES = set(DEVELOPED_SUBTYPES)

RESERVABLE_SUBTYPES = {
    "Campsite - Developed - Reservable - Fee",
    "Campsite - Developed - Reservable - No Fee",
    "Campsite - Primitive - Reservable - Fee",
    "Campsite - Primitive - Reservable - No Fee",
}

_SUBTYPE_RE = re.compile(
    r"Campsite\s*-\s*(Developed|Primitive)\s*-\s*(Reservable|Non Reservable)\s*-\s*(Fee|No Fee)",
    re.IGNORECASE,
)


def parse_subtype(subtype: str) -> dict[str, str | None]:
    if subtype == "Campground":
        return {"development": "campground", "reservable_raw": None, "fee_raw": None}
    if subtype == "Campsite - Undeveloped":
        return {"development": "undeveloped", "reservable_raw": "non_reservable", "fee_raw": None}
    m = _SUBTYPE_RE.match(subtype)
    if m:
        return {
            "development": m.group(1).lower(),
            "reservable_raw": "reservable" if m.group(2).lower() == "reservable" else "non_reservable",
            "fee_raw": "fee" if m.group(3).lower() == "fee" else "no_fee",
        }
    return {"development": None, "reservable_raw": None, "fee_raw": None}


def classify_reservation_tier(subtype: str) -> str:
    if subtype in RESERVABLE_SUBTYPES:
        return common.TIER_RESERVABLE
    if subtype == "Campground":
        return common.TIER_LIKELY_FCFS
    if subtype == "Campsite - Undeveloped":
        return common.TIER_DEFINITE_FCFS
    if "Non Reservable" in subtype:
        return common.TIER_DEFINITE_FCFS
    return common.TIER_LIKELY_FCFS


def _normalize_row(row: dict[str, Any], snapshot: str, source_tag: str) -> dict[str, Any] | None:
    lat = common.safe_float(row.get("Latitude"))
    lon = common.safe_float(row.get("Longitude"))
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    # Drop null-island points: the BLM export has a handful of (0, 0) rows that
    # the reference script's loose bounds check let through.
    if lat == 0 and lon == 0:
        return None

    subtype = (row.get("Feature Subtype") or "").strip()
    parsed = parse_subtype(subtype)
    object_id = (str(row.get("OBJECTID") or "")).strip()
    name = (row.get("Feature Name") or "").strip()

    props = {
        "source": source_tag,
        "object_id": object_id,
        "global_id": (row.get("GlobalID") or "").strip(),
        "name": name,
        "admin_state": (row.get("Administrative State") or "").strip(),
        "admin_unit_code": (row.get("Administrative Unit Code") or "").strip(),
        "unit_name": common.clean_str(row.get("UNIT_NAME")),
        "feature_type": (str(row.get("Feature Type") or "")).strip(),
        "feature_subtype": subtype,
        "development": parsed["development"],
        "reservable": parsed["reservable_raw"],
        "has_fee": parsed["fee_raw"],
        "reservation_tier": classify_reservation_tier(subtype),
        "description": common.clean_str(row.get("DESCRIPTION")),
        "web_link": common.clean_str(row.get("WEB_LINK")),
        "ingest_hash": common.make_hash(object_id, lat, lon, name),
        "snapshot_date": snapshot,
    }
    return common.to_feature(lon, lat, props)


# Live geojson uses machine field NAMES; the offline CSV headers are the field
# ALIASES the rest of this adapter reads. Confirmed via `make blm-verify` against
# BLM_Natl_Recreation_Offline/MapServer/2.
LIVE_FIELD_MAP = {
    "FET_TYPE": "Feature Type",
    "FET_SUBTYPE": "Feature Subtype",
    "FET_NAME": "Feature Name",
    "ADM_UNIT_CD": "Administrative Unit Code",
    "ADMIN_ST": "Administrative State",
    "LAT": "Latitude",
    "LONG": "Longitude",
}


def _iter_rows(raw_path: Path) -> list[dict[str, Any]]:
    """Yield attribute rows from either the CSV snapshot or a live GeoJSON file."""
    if raw_path.suffix.lower() == ".geojson":
        fc = common.read_geojson(raw_path)
        rows = []
        for feat in fc.get("features", []):
            src = dict(feat.get("properties", {}))
            attrs = dict(src)
            # Alias any machine field names to the alias-based names used below.
            for machine, alias in LIVE_FIELD_MAP.items():
                if machine in src and alias not in attrs:
                    attrs[alias] = src[machine]
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or []
            # Prefer explicit Latitude/Longitude attributes; fall back to geometry.
            if not attrs.get("Latitude") and len(coords) >= 2:
                attrs["Longitude"], attrs["Latitude"] = coords[0], coords[1]
            rows.append(attrs)
        return rows
    with open(raw_path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def normalize(raw_path: Path, snapshot: str, source_tag: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _iter_rows(raw_path):
        subtype = (row.get("Feature Subtype") or "").strip()
        if subtype not in ALLOWED_SUBTYPES:
            continue
        feat = _normalize_row(row, snapshot, source_tag)
        if feat is not None:
            out.append(feat)
    return out
