"""Wyoming State Parks camping normalize adapter.

Source: the "Camping" point layer of the Wyoming State Parks, Historic Sites &
Trails points service (WYSPHST_pts/MapServer/1). Rows are already at park
granularity - one point per state park that offers camping (Camping == "YES"),
each carrying amenity boolean-ish flags - so no rollup is needed.

The live and offline formats are identical GeoJSON (the offline snapshot is a
saved copy of the live `/query` output), so this adapter only reads GeoJSON.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import common
from . import state_base

OPERATED_BY = "Wyoming State Parks"


def normalize(raw_path: Path, snapshot: str, source_tag: str) -> list[dict[str, Any]]:
    dataset = raw_path.name
    fc = common.read_geojson(raw_path)
    out: list[dict[str, Any]] = []

    for feat in fc.get("features", []):
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") != "Point" or len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            continue
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue

        row = feat.get("properties", {})
        # This is the dedicated Camping layer; keep only parks that flag camping.
        if str(row.get("Camping") or "").strip().upper() != "YES":
            continue

        name = common.clean_str(row.get("NAME")) or "Unknown"

        props = state_base.canonical_properties(
            source=source_tag,
            # OBJECTID is unique per row; CODE is a stable site code fallback.
            site_id=common.clean_str(row.get("OBJECTID")) or common.clean_str(row.get("CODE")),
            global_id=None,
            name=name,
            public_name=name,
            state="WY",
            # Site_Type is a park designation ("State Park"), not a campground
            # subtype; these are all park campgrounds.
            site_subtype="CAMPGROUND",
            development_scale=3,
            development_label="moderate",
            reservation_tier=state_base.classify_reservation_tier(
                row.get("FEATURE"), row.get("Site_Type")
            ),
            description=None,
            directions=None,
            operated_by=OPERATED_BY,
            source_dataset=dataset,
            source_record={k: common.clean_str(v) for k, v in row.items()},
            snapshot_date=snapshot,
        )
        out.append(common.to_feature(lon, lat, props))

    return out
