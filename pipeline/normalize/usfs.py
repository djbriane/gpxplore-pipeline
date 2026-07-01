"""USFS National Recreation Sites (INFRA) normalize adapter.

Functionally equivalent to campgrounds-pipeline-bundle/scripts/filter_usfs_campgrounds.py:
same subtype filter, same three-tier+mixed reservation classification (via the
shared common.classify_by_keywords scanner), same field mapping. Dispersed
subtypes are included so downstream compact can emit them; compact itself drops
CAMP UNIT / CAMP UNIT - TENT (matching build-campgrounds.mjs).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .. import common

CAMPGROUND_SUBTYPES = {"CAMPGROUND", "GROUP CAMPGROUND", "HORSE CAMP"}
DISPERSED_SUBTYPES = {"CAMPING AREA", "CAMP UNIT", "CAMP UNIT - TENT"}
ALLOWED_SUBTYPES = CAMPGROUND_SUBTYPES | DISPERSED_SUBTYPES

FCFS_KEYWORDS = (
    "first come",
    "first-come",
    "first served",
    "first-served",
    "fcfs",
    "no reservation",
    "no reservations",
    "walk-in only",
    "walk in only",
    "non-reservable",
    "nonreservable",
)

RESERVABLE_KEYWORDS = (
    "reserv",  # reservation, reservable, reserve, reserved
    "recreation.gov",
    "rec.gov",
    "book online",
    "book ahead",
    "book in advance",
)

# Free-text fields scanned for FCFS / reservable signals.
TEXT_FIELDS = (
    "recarea_description",
    "fee_description",
    "important_info",
    "restrictions",
    "operational_hours",
    "current_conditions",
)

DEVELOPMENT_SCALE_LABELS = {
    "0": "undeveloped",
    "1": "minimal",
    "2": "basic",
    "3": "moderate",
    "4": "developed",
    "5": "fully_developed",
}


def _classify(row: dict[str, str]) -> str:
    has_nrrs = bool((row.get("nrrs_id") or "").strip())
    fields = [row.get(f, "") for f in TEXT_FIELDS]
    return common.classify_by_keywords(
        fields, FCFS_KEYWORDS, RESERVABLE_KEYWORDS, has_nrrs=has_nrrs
    )


def _normalize_row(row: dict[str, str], snapshot: str, source_tag: str) -> dict[str, Any] | None:
    lat = common.safe_float(row.get("latitude"))
    lon = common.safe_float(row.get("longitude"))
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    scale_raw = (row.get("development_scale") or "").strip()
    scale_int = common.safe_int(scale_raw)
    scale_label = DEVELOPMENT_SCALE_LABELS.get(scale_raw, "unknown")

    site_id = (row.get("site_id") or "").strip()
    name = (row.get("site_name") or "").strip()

    props = {
        "source": source_tag,
        "site_id": site_id,
        "nrrs_id": common.clean_str(row.get("nrrs_id")),
        "global_id": (row.get("globalid") or "").strip(),
        "name": name,
        "public_name": common.clean_str(row.get("public_site_name")) or name,
        "state": common.clean_str(row.get("address_state")),
        "closest_towns": common.clean_str(row.get("closest_towns")),
        "elevation_ft": common.clean_str(row.get("maximum_elevation"))
        or common.clean_str(row.get("minimum_elevation")),
        "directions": common.clean_str(row.get("directions")),
        "site_subtype": (row.get("site_subtype") or "").strip().upper(),
        "development_scale": scale_int,
        "development_label": scale_label,
        "reservation_tier": _classify(row),
        "total_capacity": common.safe_int(row.get("total_capacity")),
        "fee_charged": (row.get("fee_charged") or "").strip().upper() == "Y",
        "fee_type": common.clean_str(row.get("fee_type")),
        "fee_description": common.clean_str(row.get("fee_description")),
        "water_availability": common.clean_str(row.get("water_availability")),
        "restroom_availability": common.clean_str(row.get("restroom_availability")),
        "pack_in_out": (row.get("pack_in_out") or "").strip().upper() == "Y",
        "description": common.clean_str(row.get("recarea_description")),
        "important_info": common.clean_str(row.get("important_info")),
        "restrictions": common.clean_str(row.get("restrictions")),
        "current_conditions": common.clean_str(row.get("current_conditions")),
        "open_season": common.clean_str(row.get("open_season")),
        "operational_hours": common.clean_str(row.get("operational_hours")),
        "operated_by": common.clean_str(row.get("operated_by")),
        "usda_portal_url": common.clean_str(row.get("usda_portal_url")),
        "last_update": common.clean_str(row.get("last_update")),
        "ingest_hash": common.make_hash(site_id, lat, lon, name),
        "snapshot_date": snapshot,
    }
    return common.to_feature(lon, lat, props)


def normalize(raw_path: Path, snapshot: str, source_tag: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with open(raw_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            subtype = (row.get("site_subtype") or "").strip().upper()
            if subtype not in ALLOWED_SUBTYPES:
                continue
            feat = _normalize_row(row, snapshot, source_tag)
            if feat is not None:
                out.append(feat)
    return out
