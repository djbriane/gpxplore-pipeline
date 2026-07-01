"""Shared helpers for the state-park adapters (MT / ID / CO).

Ported from campgrounds-pipeline-bundle/scripts/build_state_campgrounds_geojson.py:
the USFS-aligned canonical property builder and the state reservation-tier
keyword scanner (kept as-is; documented known limitation - it is cruder than
the USFS/BLM logic because state sources carry little structured text).
"""

from __future__ import annotations

from typing import Any

from .. import common

# State keyword sets - preserved verbatim from the reference script (they
# differ slightly from the USFS sets, so they stay separate).
FCFS_KEYWORDS = (
    "first come",
    "first-come",
    "first served",
    "first-served",
    "fcfs",
    "non reservable",
    "non-reservable",
    "walk-in only",
    "walk in only",
    "no reservation",
    "no reservations",
)

RESERVABLE_KEYWORDS = (
    "reservable",
    "reservation",
    "book online",
    "book ahead",
    "book in advance",
    "reserve",
)


def classify_reservation_tier(*fields: Any) -> str:
    return common.classify_by_keywords(fields, FCFS_KEYWORDS, RESERVABLE_KEYWORDS)


def _str_id(val: Any) -> str | None:
    """Coerce a source id (which may arrive as an int, e.g. CO FAC_ID) to str."""
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def canonical_properties(
    *,
    source: str,
    site_id: str | None,
    global_id: str | None,
    name: str | None,
    public_name: str | None,
    state: str | None,
    site_subtype: str | None,
    development_scale: int | None,
    development_label: str | None,
    reservation_tier: str,
    description: str | None,
    directions: str | None,
    operated_by: str | None,
    source_dataset: str,
    source_record: dict[str, Any],
    snapshot_date: str,
    total_capacity: int | None = None,
) -> dict[str, Any]:
    """USFS-aligned canonical property dict for a state-park record."""
    return {
        "source": source,
        "site_id": _str_id(site_id),
        "nrrs_id": None,
        "global_id": _str_id(global_id),
        "name": name or "",
        "public_name": public_name or name or "",
        "state": state,
        "closest_towns": None,
        "elevation_ft": None,
        "directions": directions,
        "site_subtype": site_subtype or "CAMPGROUND",
        "development_scale": development_scale,
        "development_label": development_label or "unknown",
        "reservation_tier": reservation_tier,
        "total_capacity": total_capacity,
        "fee_charged": None,
        "fee_type": None,
        "fee_description": None,
        "water_availability": None,
        "restroom_availability": None,
        "pack_in_out": None,
        "description": description,
        "important_info": None,
        "restrictions": None,
        "current_conditions": None,
        "open_season": None,
        "operational_hours": None,
        "operated_by": operated_by,
        "usda_portal_url": None,
        "last_update": None,
        "ingest_hash": common.make_hash(source, site_id, name, global_id),
        "snapshot_date": snapshot_date,
        "source_dataset": source_dataset,
        "source_record": source_record,
    }
