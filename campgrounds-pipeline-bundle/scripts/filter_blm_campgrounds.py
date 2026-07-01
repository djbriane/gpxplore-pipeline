#!/usr/bin/env python3
"""
Filter BLM National Recreation Site Points into a clean campground dataset
for route planning, with structured reservation/fee/development classification.

The BLM dataset encodes reservation status and fee info directly in the
Feature Subtype field (e.g. "Campsite - Developed - Non Reservable - Fee"),
making classification much cleaner than the USFS INFRA dataset.

Usage:
    # Developed + campground sites only (default)
    python filter_blm_campgrounds.py

    # Include primitive and undeveloped sites
    python filter_blm_campgrounds.py --include-primitive

    # Exclude reservable sites (avoid Recreation.gov duplication)
    python filter_blm_campgrounds.py --exclude-reservable

    # Both
    python filter_blm_campgrounds.py --include-primitive --exclude-reservable
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

INPUT_CSV = Path(__file__).parent / "BLM_National_Recreation_Site_Points_-2581447096637901266.csv"
OUTPUT_DIR = Path(__file__).parent / "data" / "processed" / "blm"

# ---------------------------------------------------------------------------
# Subtype taxonomy
#
# BLM subtypes follow the pattern:
#   "Campsite - {Development} - {Reservable} - {Fee}"
# Plus two special cases:
#   "Campground"          — a campground-level POI (not individual site)
#   "Campsite - Undeveloped"
# ---------------------------------------------------------------------------

# All camping-related subtypes
ALL_CAMPING_SUBTYPES = {
    "Campground",
    "Campsite - Developed - Reservable - Fee",
    "Campsite - Developed - Reservable - No Fee",
    "Campsite - Developed - Non Reservable - Fee",
    "Campsite - Developed - Non Reservable - No Fee",
    "Campsite - Primitive - Reservable - Fee",
    "Campsite - Primitive - Reservable - No Fee",
    "Campsite - Primitive - Non Reservable - Fee",
    "Campsite - Primitive - Non Reservable - No Fee",
    "Campsite - Undeveloped",
}

# Developed-tier subtypes (default inclusion set)
DEVELOPED_SUBTYPES = {
    "Campground",
    "Campsite - Developed - Reservable - Fee",
    "Campsite - Developed - Reservable - No Fee",
    "Campsite - Developed - Non Reservable - Fee",
    "Campsite - Developed - Non Reservable - No Fee",
}

# Primitive / undeveloped subtypes (opt-in via --include-primitive)
PRIMITIVE_SUBTYPES = {
    "Campsite - Primitive - Reservable - Fee",
    "Campsite - Primitive - Reservable - No Fee",
    "Campsite - Primitive - Non Reservable - Fee",
    "Campsite - Primitive - Non Reservable - No Fee",
    "Campsite - Undeveloped",
}

# Reservable subtypes (for --exclude-reservable filtering)
RESERVABLE_SUBTYPES = {
    "Campsite - Developed - Reservable - Fee",
    "Campsite - Developed - Reservable - No Fee",
    "Campsite - Primitive - Reservable - Fee",
    "Campsite - Primitive - Reservable - No Fee",
}

# Three-tier classification mirroring the USFS script
TIER_DEFINITE_FCFS = "definite_fcfs"
TIER_LIKELY_FCFS = "likely_fcfs"
TIER_RESERVABLE = "reservable"


# ---------------------------------------------------------------------------
# Subtype parsing
# ---------------------------------------------------------------------------

def parse_subtype(subtype: str) -> dict[str, str | None]:
    """Extract structured fields from BLM Feature Subtype string."""
    if subtype == "Campground":
        return {
            "development": "campground",
            "reservable_raw": None,
            "fee_raw": None,
        }
    if subtype == "Campsite - Undeveloped":
        return {
            "development": "undeveloped",
            "reservable_raw": "non_reservable",
            "fee_raw": None,
        }

    # Pattern: "Campsite - {Dev} - {Reservable} - {Fee}"
    m = re.match(
        r"Campsite\s*-\s*(Developed|Primitive)\s*-\s*(Reservable|Non Reservable)\s*-\s*(Fee|No Fee)",
        subtype,
        re.IGNORECASE,
    )
    if m:
        return {
            "development": m.group(1).lower(),
            "reservable_raw": "reservable" if m.group(2).lower() == "reservable" else "non_reservable",
            "fee_raw": "fee" if m.group(3).lower() == "fee" else "no_fee",
        }

    return {
        "development": None,
        "reservable_raw": None,
        "fee_raw": None,
    }


def classify_reservation_tier(subtype: str) -> str:
    """Map BLM subtype to the three-tier classification."""
    if subtype in RESERVABLE_SUBTYPES:
        return TIER_RESERVABLE
    # "Campground" is ambiguous — could be either, but if it's not
    # explicitly marked reservable, treat it as likely FCFS
    if subtype == "Campground":
        return TIER_LIKELY_FCFS
    if subtype == "Campsite - Undeveloped":
        return TIER_DEFINITE_FCFS
    if "Non Reservable" in subtype:
        return TIER_DEFINITE_FCFS
    return TIER_LIKELY_FCFS


# ---------------------------------------------------------------------------
# Normalized record
# ---------------------------------------------------------------------------

@dataclass
class BLMCampgroundRecord:
    # Identity
    source: str = "blm_recreation"
    object_id: str = ""
    global_id: str = ""

    # Location
    name: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    admin_state: str = ""
    admin_unit_code: str = ""
    unit_name: str | None = None

    # Classification
    feature_type: str = ""
    feature_subtype: str = ""
    development: str | None = None
    reservable: str | None = None
    has_fee: str | None = None
    reservation_tier: str = ""

    # Content
    description: str | None = None
    web_link: str | None = None

    # Metadata
    ingest_hash: str = ""
    snapshot_date: str = ""


def _clean_str(val: str | None) -> str | None:
    if not val or not val.strip():
        return None
    return val.strip()


def _safe_float(val: str | None) -> float | None:
    if not val or not val.strip():
        return None
    try:
        return float(val.strip())
    except ValueError:
        return None


def _make_hash(*parts: Any) -> str:
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def normalize_row(row: dict[str, str], snapshot: str) -> BLMCampgroundRecord | None:
    """Convert a raw CSV row into a BLMCampgroundRecord, or None if invalid."""
    lat = _safe_float(row.get("Latitude"))
    lon = _safe_float(row.get("Longitude"))
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    subtype = row.get("Feature Subtype", "").strip()
    parsed = parse_subtype(subtype)

    object_id = row.get("OBJECTID", "").strip()
    name = row.get("Feature Name", "").strip()

    return BLMCampgroundRecord(
        object_id=object_id,
        global_id=row.get("GlobalID", "").strip(),
        name=name,
        latitude=lat,
        longitude=lon,
        admin_state=row.get("Administrative State", "").strip(),
        admin_unit_code=row.get("Administrative Unit Code", "").strip(),
        unit_name=_clean_str(row.get("UNIT_NAME")),
        feature_type=row.get("Feature Type", "").strip(),
        feature_subtype=subtype,
        development=parsed["development"],
        reservable=parsed["reservable_raw"],
        has_fee=parsed["fee_raw"],
        reservation_tier=classify_reservation_tier(subtype),
        description=_clean_str(row.get("DESCRIPTION")),
        web_link=_clean_str(row.get("WEB_LINK")),
        ingest_hash=_make_hash(object_id, lat, lon, name),
        snapshot_date=snapshot,
    )


# ---------------------------------------------------------------------------
# GeoJSON helpers
# ---------------------------------------------------------------------------

def to_geojson_feature(rec: BLMCampgroundRecord) -> dict[str, Any]:
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
        description="Filter BLM Recreation Sites into a clean campground dataset."
    )
    parser.add_argument(
        "--include-primitive",
        action="store_true",
        help="Include primitive and undeveloped campsites (default: developed + campground only).",
    )
    parser.add_argument(
        "--exclude-reservable",
        action="store_true",
        help="Exclude sites marked as reservable (avoid Recreation.gov duplication).",
    )
    parser.add_argument(
        "--state",
        type=str,
        default=None,
        help="Filter to a single administrative state (e.g. OR, MT, CA).",
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
    allowed_subtypes = set(DEVELOPED_SUBTYPES)
    if args.include_primitive:
        allowed_subtypes |= PRIMITIVE_SUBTYPES

    # --- Pass 1: read and filter ---
    records: list[BLMCampgroundRecord] = []
    stats = {
        "total_source_rows": 0,
        "skipped_not_camping": 0,
        "skipped_bad_coords": 0,
        "skipped_reservable": 0,
        "skipped_wrong_state": 0,
        "kept": 0,
        "by_subtype": {},
        "by_tier": {},
        "by_development": {},
        "by_state": {},
    }

    with open(args.input, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stats["total_source_rows"] += 1
            subtype = row.get("Feature Subtype", "").strip()

            if subtype not in allowed_subtypes:
                stats["skipped_not_camping"] += 1
                continue

            if args.state and row.get("Administrative State", "").strip().upper() != args.state.upper():
                stats["skipped_wrong_state"] += 1
                continue

            rec = normalize_row(row, snapshot)
            if rec is None:
                stats["skipped_bad_coords"] += 1
                continue

            if args.exclude_reservable and rec.reservation_tier == TIER_RESERVABLE:
                stats["skipped_reservable"] += 1
                continue

            records.append(rec)
            stats["kept"] += 1
            stats["by_subtype"][rec.feature_subtype] = stats["by_subtype"].get(rec.feature_subtype, 0) + 1
            stats["by_tier"][rec.reservation_tier] = stats["by_tier"].get(rec.reservation_tier, 0) + 1
            dev = rec.development or "unknown"
            stats["by_development"][dev] = stats["by_development"].get(dev, 0) + 1
            stats["by_state"][rec.admin_state] = stats["by_state"].get(rec.admin_state, 0) + 1

    # --- Write outputs ---
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    # 1. NDJSON
    ndjson_path = out / "blm-campgrounds.ndjson"
    with open(ndjson_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")

    # 2. GeoJSON
    geojson_path = out / "blm-campgrounds.geojson"
    geojson = {
        "type": "FeatureCollection",
        "features": [to_geojson_feature(rec) for rec in records],
    }
    with open(geojson_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)

    # 3. Stats
    stats_path = out / "blm-campgrounds-stats.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    # --- Report ---
    print(f"\n{'='*60}")
    print(f"BLM Campground Filter — {snapshot}")
    print(f"{'='*60}")
    print(f"Source rows scanned:       {stats['total_source_rows']:>8,}")
    print(f"Skipped (not camping):     {stats['skipped_not_camping']:>8,}")
    print(f"Skipped (bad coords):      {stats['skipped_bad_coords']:>8,}")
    print(f"Skipped (reservable):      {stats['skipped_reservable']:>8,}")
    print(f"Skipped (wrong state):     {stats['skipped_wrong_state']:>8,}")
    print(f"{'─'*60}")
    print(f"Records kept:              {stats['kept']:>8,}")
    print()

    print("By subtype:")
    for k, v in sorted(stats["by_subtype"].items(), key=lambda x: -x[1]):
        print(f"  {k:<50} {v:>5,}")
    print()

    print("By reservation tier:")
    for k, v in sorted(stats["by_tier"].items(), key=lambda x: -x[1]):
        print(f"  {k:<25} {v:>5,}")
    print()

    print("By development level:")
    for k, v in sorted(stats["by_development"].items(), key=lambda x: -x[1]):
        print(f"  {k:<25} {v:>5,}")
    print()

    print("By state:")
    for k, v in sorted(stats["by_state"].items(), key=lambda x: -x[1]):
        print(f"  {k:<10} {v:>5,}")
    print()

    print(f"Output:")
    print(f"  {ndjson_path}")
    print(f"  {geojson_path}")
    print(f"  {stats_path}")
    print()


if __name__ == "__main__":
    main()
