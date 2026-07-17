"""USFS INFRA point-of-interest normalize adapter.

Re-filters the same Recreation Sites INFRA CSV used by normalize/usfs.py into
rider POI categories. The INFRA `LOOKOUT/CABIN` subtype is broad, so this
adapter only keeps rows whose name/text actually indicates a lookout or fire
tower instead of publishing every cabin/yurt as a lookout.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from .. import common

TARGET_STATE_BOUNDS = {
    "AZ": (-114.9, 31.2, -109.0, 37.1),
    "CA": (-124.6, 32.4, -114.1, 42.1),
    "CO": (-109.1, 36.9, -102.0, 41.1),
    "ID": (-117.3, 42.0, -111.0, 49.1),
    "MT": (-116.1, 44.3, -104.0, 49.1),
    "OR": (-124.8, 42.0, -116.4, 46.4),
    "WA": (-124.9, 45.5, -116.9, 49.1),
    "WY": (-111.1, 40.9, -104.0, 45.1),
}

SUBTYPE_CATEGORY = {
    "HISTORIC SITE": "historic",
    "DOCUMENTARY SITE": "historic",
    "INTERPRETIVE SITE": "interpretive",
    "INTERPRETIVE SITE (ADMIN)": "interpretive",
    "INTERPRETIVE VISITOR CENTER (MAJOR)": "interpretive",
    "INTERPRETIVE VISITOR CENTER (MINOR)": "interpretive",
}

LOOKOUT_SUBTYPE = "LOOKOUT/CABIN"
_LOOKOUT_RE = re.compile(r"\b(lookout|look out|fire\s+tower|firetower|observation\s+tower|l\.?o\.?)\b", re.I)
_HISTORIC_RE = re.compile(r"\b(historic|heritage|battlefield|cemeter|ccc|ranger\s+station)\b", re.I)
# Interpretive rows tagged as overlooks/scenic pullouts — low narrative value.
_OVERLOOK_NAME_RE = re.compile(r"\b(overlook|vista|viewpoint|scenic\s+view)\b", re.I)


def _clean_state(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    state = value.strip().upper()
    if len(state) == 2 and state in TARGET_STATE_BOUNDS:
        return state
    return None


def _in_target_footprint(lon: float, lat: float) -> bool:
    for _state, (min_lon, min_lat, max_lon, max_lat) in TARGET_STATE_BOUNDS.items():
        if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat:
            return True
    return False


def _text_blob(row: dict[str, str]) -> str:
    fields = (
        "site_name",
        "public_site_name",
        "alias_name",
        "alternative_name",
        "official_designation",
        "recarea_name",
        "recarea_description",
    )
    return " ".join(row.get(f, "") or "" for f in fields)


def _lookout_blob(row: dict[str, str]) -> str:
    fields = (
        "site_name",
        "public_site_name",
        "alias_name",
        "alternative_name",
        "official_designation",
    )
    return " ".join(row.get(f, "") or "" for f in fields)


INTERPRETIVE_MIN_DESC = 80


def _has_url(row: dict[str, str]) -> bool:
    return bool((row.get("usda_portal_url") or "").strip()) or bool(
        (row.get("rec1stop_url") or "").strip()
    )


def _is_low_value_interpretive(row: dict[str, str]) -> bool:
    """Drop interpretive sites that are really scenic pullouts with thin prose."""
    if not _OVERLOOK_NAME_RE.search(_lookout_blob(row)):
        return False
    desc = (row.get("recarea_description") or "").strip()
    if len(desc) >= 120:
        return False
    name = (row.get("site_name") or row.get("public_site_name") or "").strip().upper()
    if name and desc.upper().startswith(name[: min(len(name), 24)]):
        return True
    return len(desc) < 80


def _category_for(row: dict[str, str]) -> str | None:
    subtype = (row.get("site_subtype") or "").strip().upper()
    if subtype == LOOKOUT_SUBTYPE:
        return "lookout" if _LOOKOUT_RE.search(_lookout_blob(row)) else None
    category = SUBTYPE_CATEGORY.get(subtype)
    if category == "interpretive" and _HISTORIC_RE.search(_text_blob(row)):
        return "historic"
    if category == "interpretive" and _is_low_value_interpretive(row):
        return None
    return category


def _normalize_row(row: dict[str, str], snapshot: str, source_tag: str) -> dict[str, Any] | None:
    lat = common.safe_float(row.get("latitude"))
    lon = common.safe_float(row.get("longitude"))
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    state = _clean_state(row.get("address_state")) or _clean_state(row.get("states_spanned"))
    if state is None and not _in_target_footprint(lon, lat):
        return None

    category = _category_for(row)
    if category is None:
        return None
    if category == "interpretive":
        desc_len = len((row.get("recarea_description") or "").strip())
        if desc_len < INTERPRETIVE_MIN_DESC and not _has_url(row):
            return None

    site_id = (row.get("site_id") or row.get("objectid") or "").strip()
    name = (row.get("site_name") or "").strip()
    public_name = common.clean_str(row.get("public_site_name")) or name
    subtype = (row.get("site_subtype") or "").strip().upper()

    props = {
        "source": source_tag,
        "site_id": site_id,
        "name": name,
        "public_name": public_name,
        "category": category,
        "subtype_raw": subtype,
        "state": state,
        "county": None,
        "elevation_ft": common.clean_str(row.get("maximum_elevation"))
        or common.clean_str(row.get("minimum_elevation")),
        "significant_year": None,
        "ref_number": None,
        "commodity": None,
        "dev_status": None,
        "description": common.clean_str(row.get("recarea_description")),
        "operated_by": common.clean_str(row.get("operated_by")),
        "url": common.clean_str(row.get("usda_portal_url"))
        or common.clean_str(row.get("rec1stop_url")),
        "snapshot_date": snapshot,
        "ingest_hash": common.make_hash(source_tag, site_id, lat, lon, public_name, category),
    }
    return common.to_feature(lon, lat, props)


def normalize(raw_path: Path, snapshot: str, source_tag: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with open(raw_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            feat = _normalize_row(row, snapshot, source_tag)
            if feat is not None:
                out.append(feat)
    return out
