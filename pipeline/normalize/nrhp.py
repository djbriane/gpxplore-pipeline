"""NPS National Register of Historic Places normalize adapter.

Maps the public nrhp_locations MapServer point layer into canonical POI
features. The GIS layer carries identity and listing metadata only — no prose
description — so narrative depth is an iOS-side enrichment concern.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import common

STATE_NAME_TO_CODE = {
    "ARIZONA": "AZ",
    "CALIFORNIA": "CA",
    "COLORADO": "CO",
    "IDAHO": "ID",
    "MONTANA": "MT",
    "OREGON": "OR",
    "WASHINGTON": "WA",
    "WYOMING": "WY",
}
TARGET_STATE_CODES = set(STATE_NAME_TO_CODE.values())

# ArcGIS live fetch uses full state names in SQL `where` clauses.
TARGET_STATE_WHERE = (
    "State IN ('ARIZONA','CALIFORNIA','COLORADO','IDAHO',"
    "'MONTANA','OREGON','WASHINGTON','WYOMING')"
)


def _state_code(raw_state: Any) -> str | None:
    if not isinstance(raw_state, str):
        return None
    key = raw_state.strip().upper()
    if len(key) == 2 and key in TARGET_STATE_CODES:
        return key
    return STATE_NAME_TO_CODE.get(key)


def _listing_year(cert_date: Any) -> str | None:
    if not isinstance(cert_date, str):
        return None
    cert_date = cert_date.strip()
    if not cert_date:
        return None
    # CertDate arrives as MM/DD/YY from NPS GIS.
    parts = cert_date.split("/")
    if len(parts) == 3 and len(parts[2]) == 2:
        yy = int(parts[2])
        century = 1900 if yy >= 30 else 2000
        return str(century + yy)
    return cert_date


def _coords_from_feature(feat: dict[str, Any]) -> tuple[float, float] | None:
    geom = feat.get("geometry") or {}
    if geom.get("type") == "Point":
        coords = geom.get("coordinates") or []
        if len(coords) >= 2:
            lon, lat = coords[0], coords[1]
            if isinstance(lon, (int, float)) and isinstance(lat, (int, float)):
                return float(lon), float(lat)
    return None


def _normalize_feature(feat: dict[str, Any], snapshot: str, source_tag: str) -> dict[str, Any] | None:
    props_in = feat.get("properties") or {}
    ll = _coords_from_feature(feat)
    if ll is None:
        return None
    lon, lat = ll
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    state = _state_code(props_in.get("State"))
    if state is None:
        return None

    ref = common.clean_str(props_in.get("NRIS_Refnum"))
    name = common.clean_str(props_in.get("RESNAME")) or ref or "Historic Site"
    raw_id = props_in.get("OBJECTID")
    site_id = (str(raw_id).strip() if raw_id is not None else "") or ref
    if not site_id:
        return None

    res_type = common.clean_str(props_in.get("ResType"))
    county = common.clean_str(props_in.get("County"))
    subtype_raw = res_type.upper() if isinstance(res_type, str) and res_type else None

    props = {
        "source": source_tag,
        "site_id": site_id,
        "name": name,
        "public_name": name,
        "category": "historic",
        "subtype_raw": subtype_raw,
        "state": state,
        "county": county,
        "elevation_ft": None,
        "significant_year": _listing_year(props_in.get("CertDate")),
        "ref_number": ref,
        "commodity": None,
        "dev_status": None,
        "description": None,
        "operated_by": None,
        "url": None,
        "snapshot_date": snapshot,
        "ingest_hash": common.make_hash(source_tag, site_id, ref, lat, lon, name),
    }
    return common.to_feature(lon, lat, props)


def normalize(raw_path: Path, snapshot: str, source_tag: str) -> list[dict[str, Any]]:
    fc = common.read_geojson(raw_path)
    out: list[dict[str, Any]] = []
    for feat in fc.get("features", []):
        normalized = _normalize_feature(feat, snapshot, source_tag)
        if normalized is not None:
            out.append(normalized)
    return out
