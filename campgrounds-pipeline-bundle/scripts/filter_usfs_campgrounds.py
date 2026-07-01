#!/usr/bin/env python3
"""
Filter USFS Recreation Sites INFRA dataset into a clean campground dataset
for route planning, with three-tier FCFS classification.

Usage:
    # Developed campgrounds only (default)
    python filter_usfs_campgrounds.py

    # Include dispersed camping areas
    python filter_usfs_campgrounds.py --include-dispersed

    # Exclude sites linked to Recreation.gov
    python filter_usfs_campgrounds.py --exclude-reservable

    # Both
    python filter_usfs_campgrounds.py --include-dispersed --exclude-reservable
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

INPUT_CSV = Path(__file__).parent / "Recreation_Sites_INFRA.csv"
OUTPUT_DIR = Path(__file__).parent / "data" / "processed" / "usfs"

CAMPGROUND_SUBTYPES = {"CAMPGROUND", "GROUP CAMPGROUND", "HORSE CAMP"}
DISPERSED_SUBTYPES = {"CAMPING AREA", "CAMP UNIT", "CAMP UNIT - TENT"}

FCFS_KEYWORDS = [
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
]

RESERVABLE_KEYWORDS = [
    "reserv",          # catches reservation, reservable, reserve, reserved
    "recreation.gov",
    "rec.gov",
    "book online",
    "book ahead",
    "book in advance",
]

# Text fields to scan for FCFS / reservable signals
TEXT_FIELDS = [
    "recarea_description",
    "fee_description",
    "important_info",
    "restrictions",
    "operational_hours",
    "current_conditions",
]

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

TIER_DEFINITE_FCFS = "definite_fcfs"
TIER_LIKELY_FCFS = "likely_fcfs"
TIER_RESERVABLE = "reservable"
TIER_MIXED = "mixed"  # text mentions both FCFS and reservable


def _scan_text(row: dict[str, str]) -> tuple[bool, bool]:
    """Return (has_fcfs_signal, has_reservable_signal) from freetext fields."""
    blob = " ".join(row.get(f, "") for f in TEXT_FIELDS).lower()
    has_fcfs = any(kw in blob for kw in FCFS_KEYWORDS)
    has_reservable = any(kw in blob for kw in RESERVABLE_KEYWORDS)
    return has_fcfs, has_reservable


def classify_reservation_tier(row: dict[str, str]) -> str:
    """Three-tier + mixed classification."""
    has_nrrs = bool(row.get("nrrs_id", "").strip())
    has_fcfs, has_reservable = _scan_text(row)

    # If the site has a Recreation.gov ID, it is reservable regardless of text
    if has_nrrs:
        if has_fcfs:
            return TIER_MIXED   # some sites/loops FCFS, others reservable
        return TIER_RESERVABLE

    if has_fcfs and has_reservable:
        return TIER_MIXED
    if has_reservable:
        return TIER_RESERVABLE
    if has_fcfs:
        return TIER_DEFINITE_FCFS

    # No signal at all → likely FCFS (most USFS sites without reservation
    # infrastructure are first-come-first-served)
    return TIER_LIKELY_FCFS


# ---------------------------------------------------------------------------
# Development scale labels
# ---------------------------------------------------------------------------

DEVELOPMENT_SCALE_LABELS = {
    "0": "undeveloped",
    "1": "minimal",
    "2": "basic",
    "3": "moderate",
    "4": "developed",
    "5": "fully_developed",
}


# ---------------------------------------------------------------------------
# Normalized record
# ---------------------------------------------------------------------------

@dataclass
class CampgroundRecord:
    # Identity
    source: str = "usfs_infra"
    site_id: str = ""
    nrrs_id: str | None = None
    global_id: str = ""

    # Location
    name: str = ""
    public_name: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    state: str | None = None
    closest_towns: str | None = None
    elevation_ft: str | None = None
    directions: str | None = None

    # Classification
    site_subtype: str = ""
    development_scale: int | None = None
    development_label: str = ""
    reservation_tier: str = ""

    # Capacity & access
    total_capacity: int | None = None
    fee_charged: bool = False
    fee_type: str | None = None
    fee_description: str | None = None

    # Amenities
    water_availability: str | None = None
    restroom_availability: str | None = None
    pack_in_out: bool = False

    # Descriptions
    description: str | None = None
    important_info: str | None = None
    restrictions: str | None = None
    current_conditions: str | None = None
    open_season: str | None = None
    operational_hours: str | None = None
    operated_by: str | None = None

    # Links
    usda_portal_url: str | None = None

    # Metadata
    last_update: str | None = None
    ingest_hash: str = ""
    snapshot_date: str = ""


def _safe_int(val: str | None) -> int | None:
    if not val or not val.strip():
        return None
    try:
        return int(val.strip())
    except ValueError:
        return None


def _safe_float(val: str | None) -> float | None:
    if not val or not val.strip():
        return None
    try:
        return float(val.strip())
    except ValueError:
        return None


def _clean_str(val: str | None) -> str | None:
    if not val or not val.strip():
        return None
    return val.strip()


def _make_hash(*parts: Any) -> str:
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def normalize_row(row: dict[str, str], snapshot: str) -> CampgroundRecord | None:
    """Convert a raw CSV row into a CampgroundRecord, or None if invalid."""
    lat = _safe_float(row.get("latitude"))
    lon = _safe_float(row.get("longitude"))
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    scale_raw = row.get("development_scale", "").strip()
    scale_int = _safe_int(scale_raw)
    scale_label = DEVELOPMENT_SCALE_LABELS.get(scale_raw, "unknown")

    nrrs = _clean_str(row.get("nrrs_id"))
    site_id = row.get("site_id", "").strip()

    rec = CampgroundRecord(
        site_id=site_id,
        nrrs_id=nrrs,
        global_id=row.get("globalid", "").strip(),
        name=row.get("site_name", "").strip(),
        public_name=_clean_str(row.get("public_site_name")) or row.get("site_name", "").strip(),
        latitude=lat,
        longitude=lon,
        state=_clean_str(row.get("address_state")),
        closest_towns=_clean_str(row.get("closest_towns")),
        elevation_ft=_clean_str(row.get("maximum_elevation")) or _clean_str(row.get("minimum_elevation")),
        directions=_clean_str(row.get("directions")),
        site_subtype=row.get("site_subtype", "").strip().upper(),
        development_scale=scale_int,
        development_label=scale_label,
        reservation_tier=classify_reservation_tier(row),
        total_capacity=_safe_int(row.get("total_capacity")),
        fee_charged=row.get("fee_charged", "").strip().upper() == "Y",
        fee_type=_clean_str(row.get("fee_type")),
        fee_description=_clean_str(row.get("fee_description")),
        water_availability=_clean_str(row.get("water_availability")),
        restroom_availability=_clean_str(row.get("restroom_availability")),
        pack_in_out=row.get("pack_in_out", "").strip().upper() == "Y",
        description=_clean_str(row.get("recarea_description")),
        important_info=_clean_str(row.get("important_info")),
        restrictions=_clean_str(row.get("restrictions")),
        current_conditions=_clean_str(row.get("current_conditions")),
        open_season=_clean_str(row.get("open_season")),
        operational_hours=_clean_str(row.get("operational_hours")),
        operated_by=_clean_str(row.get("operated_by")),
        usda_portal_url=_clean_str(row.get("usda_portal_url")),
        last_update=_clean_str(row.get("last_update")),
        ingest_hash=_make_hash(site_id, lat, lon, row.get("site_name", "")),
        snapshot_date=snapshot,
    )
    return rec


# ---------------------------------------------------------------------------
# GeoJSON helpers
# ---------------------------------------------------------------------------

def to_geojson_feature(rec: CampgroundRecord) -> dict[str, Any]:
    props = asdict(rec)
    props.pop("latitude")
    props.pop("longitude")
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [rec.longitude, rec.latitude],
        },
        "properties": props,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filter USFS INFRA campgrounds into a clean route-planning dataset."
    )
    parser.add_argument(
        "--include-dispersed",
        action="store_true",
        help="Include CAMPING AREA, CAMP UNIT, and CAMP UNIT - TENT subtypes.",
    )
    parser.add_argument(
        "--exclude-reservable",
        action="store_true",
        help="Exclude sites classified as 'reservable' (has nrrs_id or reservation text).",
    )
    parser.add_argument(
        "--min-scale",
        type=int,
        default=None,
        help="Minimum development_scale to include (0=undeveloped … 5=fully developed).",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=INPUT_CSV,
        help=f"Path to source CSV (default: {INPUT_CSV})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help=f"Output directory (default: {OUTPUT_DIR})",
    )
    args = parser.parse_args()

    snapshot = date.today().isoformat()
    allowed_subtypes = set(CAMPGROUND_SUBTYPES)
    if args.include_dispersed:
        allowed_subtypes |= DISPERSED_SUBTYPES

    # --- Pass 1: read and filter ---
    records: list[CampgroundRecord] = []
    stats = {
        "total_source_rows": 0,
        "skipped_wrong_subtype": 0,
        "skipped_bad_coords": 0,
        "skipped_reservable": 0,
        "skipped_below_min_scale": 0,
        "kept": 0,
        "by_subtype": {},
        "by_tier": {},
        "by_scale": {},
    }

    with open(args.input, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stats["total_source_rows"] += 1
            subtype = row.get("site_subtype", "").strip().upper()

            if subtype not in allowed_subtypes:
                stats["skipped_wrong_subtype"] += 1
                continue

            rec = normalize_row(row, snapshot)
            if rec is None:
                stats["skipped_bad_coords"] += 1
                continue

            if args.min_scale is not None and (rec.development_scale or 0) < args.min_scale:
                stats["skipped_below_min_scale"] += 1
                continue

            if args.exclude_reservable and rec.reservation_tier == TIER_RESERVABLE:
                stats["skipped_reservable"] += 1
                continue

            records.append(rec)
            stats["kept"] += 1
            stats["by_subtype"][rec.site_subtype] = stats["by_subtype"].get(rec.site_subtype, 0) + 1
            stats["by_tier"][rec.reservation_tier] = stats["by_tier"].get(rec.reservation_tier, 0) + 1
            sl = str(rec.development_scale) if rec.development_scale is not None else "?"
            stats["by_scale"][sl] = stats["by_scale"].get(sl, 0) + 1

    # --- Write outputs ---
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    # 1. NDJSON
    ndjson_path = out / "usfs-campgrounds.ndjson"
    with open(ndjson_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")

    # 2. GeoJSON
    geojson_path = out / "usfs-campgrounds.geojson"
    geojson = {
        "type": "FeatureCollection",
        "features": [to_geojson_feature(rec) for rec in records],
    }
    with open(geojson_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)

    # 3. Stats
    stats_path = out / "usfs-campgrounds-stats.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    # --- Report ---
    print(f"\n{'='*60}")
    print(f"USFS Campground Filter — {snapshot}")
    print(f"{'='*60}")
    print(f"Source rows scanned:       {stats['total_source_rows']:>8,}")
    print(f"Skipped (wrong subtype):   {stats['skipped_wrong_subtype']:>8,}")
    print(f"Skipped (bad coords):      {stats['skipped_bad_coords']:>8,}")
    print(f"Skipped (reservable):      {stats['skipped_reservable']:>8,}")
    print(f"Skipped (below min scale): {stats['skipped_below_min_scale']:>8,}")
    print(f"{'─'*60}")
    print(f"Records kept:              {stats['kept']:>8,}")
    print()

    print("By subtype:")
    for k, v in sorted(stats["by_subtype"].items(), key=lambda x: -x[1]):
        print(f"  {k:<25} {v:>6,}")
    print()

    print("By reservation tier:")
    for k, v in sorted(stats["by_tier"].items(), key=lambda x: -x[1]):
        print(f"  {k:<25} {v:>6,}")
    print()

    print("By development scale:")
    for k, v in sorted(stats["by_scale"].items()):
        label = DEVELOPMENT_SCALE_LABELS.get(k, "")
        print(f"  {k} ({label:<16}) {v:>6,}")
    print()

    print(f"Output:")
    print(f"  {ndjson_path}")
    print(f"  {geojson_path}")
    print(f"  {stats_path}")
    print()


if __name__ == "__main__":
    main()
