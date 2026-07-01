#!/usr/bin/env python3
"""
Build a combined state-managed campground GeoJSON from:
  - Montana State Parks facilities CSV
  - Idaho Parks and Facilities CSV
  - Colorado campgrounds GeoJSON

The output schema is aligned to the USFS campground fields where possible and
retains full source metadata for each feature.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

MT_INPUT = Path(__file__).parent / "FWPLND_STATEPARKS_FACILITIES_PTS_7905798971970118109.csv"
ID_INPUT = Path(__file__).parent / "IDPR_Parks_and_Facilities.csv"
CO_INPUT = Path(__file__).parent / "scripts" / "co_campgrounds.geojson"
OUT_DIR = Path(__file__).parent / "data" / "processed" / "state"

TIER_DEFINITE_FCFS = "definite_fcfs"
TIER_LIKELY_FCFS = "likely_fcfs"
TIER_RESERVABLE = "reservable"
TIER_MIXED = "mixed"

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


def _clean(val: Any) -> Any:
    if not isinstance(val, str):
        return val
    s = val.strip()
    return s if s else None


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(str(val).strip())
    except (TypeError, ValueError):
        return None


def _hash(*parts: Any) -> str:
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def web_mercator_to_wgs84(x: float, y: float) -> tuple[float, float]:
    lon = (x / 20037508.34) * 180.0
    lat = (y / 20037508.34) * 180.0
    lat = 180.0 / math.pi * (2.0 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2.0)
    return lon, lat


def classify_reservation_tier(*fields: Any) -> str:
    blob = " ".join(str(f or "") for f in fields).lower()
    has_fcfs = any(k in blob for k in FCFS_KEYWORDS)
    has_reservable = any(k in blob for k in RESERVABLE_KEYWORDS)
    if has_fcfs and has_reservable:
        return TIER_MIXED
    if has_reservable:
        return TIER_RESERVABLE
    if has_fcfs:
        return TIER_DEFINITE_FCFS
    return TIER_LIKELY_FCFS


def usfs_aligned_properties(
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
) -> dict[str, Any]:
    return {
        # USFS-aligned identity
        "source": source,
        "site_id": site_id,
        "nrrs_id": None,
        "global_id": global_id,
        # USFS-aligned location
        "name": name or "",
        "public_name": public_name or name or "",
        "state": state,
        "closest_towns": None,
        "elevation_ft": None,
        "directions": directions,
        # USFS-aligned classification
        "site_subtype": site_subtype or "CAMPGROUND",
        "development_scale": development_scale,
        "development_label": development_label or "unknown",
        "reservation_tier": reservation_tier,
        # USFS-aligned capacity/access
        "total_capacity": None,
        "fee_charged": None,
        "fee_type": None,
        "fee_description": None,
        # USFS-aligned amenities/content
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
        # USFS-aligned links/meta
        "usda_portal_url": None,
        "last_update": None,
        "ingest_hash": _hash(source, site_id, name, global_id),
        "snapshot_date": snapshot_date,
        # Source-preservation fields
        "source_dataset": source_dataset,
        "source_record": source_record,
    }


def load_mt_features(snapshot: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with open(MT_INPUT, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ftype = (row.get("Facility Type") or "").strip()
            fname = (row.get("Facility Name") or "").strip()
            pname = (row.get("Park Name") or "").strip()
            searchable = f"{ftype} {fname} {pname}".lower()
            # Keep campsite/campground-oriented records from this facility points layer.
            if "camp" not in searchable:
                continue

            x = _safe_float(row.get("x"))
            y = _safe_float(row.get("y"))
            if x is None or y is None:
                continue
            lon, lat = web_mercator_to_wgs84(x, y)

            subtype = (ftype or "Campground").upper()
            if "UTILITIES UNAVAILABLE" in subtype:
                dev_scale, dev_label = 1, "minimal"
            elif "CAMPGROUND" in subtype:
                dev_scale, dev_label = 3, "moderate"
            else:
                dev_scale, dev_label = 2, "basic"

            display_name = fname or f"{pname} {ftype}".strip()
            props = usfs_aligned_properties(
                source="mt_state_parks",
                site_id=_clean(row.get("SITEID")) or _clean(row.get("OBJECTID")),
                global_id=_clean(row.get("GlobalID")),
                name=_clean(display_name),
                public_name=_clean(display_name),
                state="MT",
                site_subtype=subtype,
                development_scale=dev_scale,
                development_label=dev_label,
                reservation_tier=classify_reservation_tier(ftype, fname, row.get("COMMENTS")),
                description=_clean(row.get("COMMENTS")),
                directions=None,
                operated_by="Montana State Parks",
                source_dataset=MT_INPUT.name,
                source_record={k: _clean(v) for k, v in row.items()},
                snapshot_date=snapshot,
            )
            out.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": props,
                }
            )
    return out


def load_id_features(snapshot: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with open(ID_INPUT, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            x = _safe_float(row.get("X"))
            y = _safe_float(row.get("Y"))
            if x is None or y is None:
                continue
            lon, lat = web_mercator_to_wgs84(x, y)

            name = _clean(row.get("name"))
            desc = _clean(row.get("description"))
            props = usfs_aligned_properties(
                source="id_state_parks",
                site_id=_clean(row.get("objectid")),
                global_id=None,
                name=name,
                public_name=name,
                state="ID",
                site_subtype="STATE PARK",
                development_scale=3,
                development_label="moderate",
                reservation_tier=classify_reservation_tier(name, desc),
                description=desc,
                directions=None,
                operated_by="Idaho Department of Parks and Recreation",
                source_dataset=ID_INPUT.name,
                source_record={k: _clean(v) for k, v in row.items()},
                snapshot_date=snapshot,
            )
            props["usda_portal_url"] = _clean(row.get("pic_url"))
            out.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": props,
                }
            )
    return out


def load_co_features(snapshot: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with open(CO_INPUT, encoding="utf-8") as f:
        fc = json.load(f)
    for feat in fc.get("features", []):
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [])
        if geom.get("type") != "Point" or len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            continue
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue

        row = feat.get("properties", {})
        fac_type = str(row.get("FAC_TYPE") or "").upper()
        name = _clean(row.get("FAC_NAME")) or _clean(row.get("PROPNAME")) or "Unknown"
        props = usfs_aligned_properties(
            source="co_state_parks",
            site_id=_clean(row.get("FAC_ID")) or _clean(row.get("OBJECTID")),
            global_id=_clean(row.get("GlobalID")),
            name=name,
            public_name=name,
            state="CO",
            site_subtype=fac_type or "CAMPGROUND",
            development_scale=3 if "CAMPGROUND" in fac_type else 2,
            development_label="moderate" if "CAMPGROUND" in fac_type else "basic",
            reservation_tier=classify_reservation_tier(
                row.get("FAC_TYPE"), row.get("TYPE_DETAIL"), row.get("COMMENTS")
            ),
            description=_clean(row.get("COMMENTS")) or _clean(row.get("TYPE_DETAIL")),
            directions=_clean(row.get("ST_ADDRESS")),
            operated_by=_clean(row.get("MGMT_AUTH")) or "Colorado Parks and Wildlife",
            source_dataset=CO_INPUT.name,
            source_record={k: _clean(v) for k, v in row.items()},
            snapshot_date=snapshot,
        )
        out.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": props,
            }
        )
    return out


def write_outputs(features: list[dict[str, Any]], out_geojson: Path, out_stats: Path) -> None:
    out_geojson.parent.mkdir(parents=True, exist_ok=True)
    payload = {"type": "FeatureCollection", "features": features}
    with open(out_geojson, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    by_source: dict[str, int] = {}
    by_state: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    for feat in features:
        p = feat["properties"]
        by_source[p["source"]] = by_source.get(p["source"], 0) + 1
        state = p.get("state") or "?"
        by_state[state] = by_state.get(state, 0) + 1
        tier = p.get("reservation_tier") or "unknown"
        by_tier[tier] = by_tier.get(tier, 0) + 1

    stats = {
        "generated": date.today().isoformat(),
        "total_features": len(features),
        "output_geojson": str(out_geojson),
        "by_source": by_source,
        "by_state": by_state,
        "by_reservation_tier": by_tier,
    }
    with open(out_stats, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a combined state campground GeoJSON (MT + ID + CO) aligned "
            "to the USFS campground property schema."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUT_DIR,
        help=f"Output directory (default: {OUT_DIR})",
    )
    args = parser.parse_args()

    snapshot = date.today().isoformat()
    mt_features = load_mt_features(snapshot)
    id_features = load_id_features(snapshot)
    co_features = load_co_features(snapshot)
    features = mt_features + id_features + co_features

    out_geojson = args.output_dir / "state-campgrounds.geojson"
    out_stats = args.output_dir / "state-campgrounds-stats.json"
    write_outputs(features, out_geojson, out_stats)

    print("\n============================================================")
    print(f"State Campgrounds Build — {snapshot}")
    print("============================================================")
    print(f"MT kept: {len(mt_features):>6,}")
    print(f"ID kept: {len(id_features):>6,}")
    print(f"CO kept: {len(co_features):>6,}")
    print("------------------------------------------------------------")
    print(f"Total:   {len(features):>6,}")
    print()
    print("Output:")
    print(f"  {out_geojson}")
    print(f"  {out_stats}")
    print()


if __name__ == "__main__":
    main()
