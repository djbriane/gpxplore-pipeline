"""Oregon State Parks normalize adapter.

Source: the Oregon State Parks boundary layer (Oregon_State_Parks/
FeatureServer/0). This is a polygon layer of *all* 422 state park units with no
campsite points, but each unit carries a USE_TYPE - so we keep only the 64 units
flagged USE_TYPE == "Camping" and emit one marker at each park's polygon
centroid (via common.geometry_centroid).

Marker positions are therefore park-boundary centroids, not exact campground
locations - an inherent limitation of a boundaries-only source.

The offline snapshot is stored with simplified geometry (maxAllowableOffset)
since only the centroid is used; live fetches return full-resolution polygons
that centroid to essentially the same point.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import common
from . import state_base

OPERATED_BY = "Oregon Parks and Recreation Department"


def normalize(raw_path: Path, snapshot: str, source_tag: str) -> list[dict[str, Any]]:
    dataset = raw_path.name
    fc = common.read_geojson(raw_path)
    out: list[dict[str, Any]] = []

    for feat in fc.get("features", []):
        row = feat.get("properties", {})
        if str(row.get("USE_TYPE") or "").strip().lower() != "camping":
            continue
        ll = common.geometry_centroid(feat.get("geometry"))
        if ll is None:
            continue
        lon, lat = ll
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue

        name = common.clean_str(row.get("FULL_NAME")) or common.clean_str(row.get("NAME")) or "Unknown"
        designation = common.clean_str(row.get("DESIGNATION"))

        props = state_base.canonical_properties(
            source=source_tag,
            site_id=common.clean_str(row.get("OBJECTID")),
            global_id=common.clean_str(row.get("GlobalID")),
            name=name,
            public_name=name,
            state="OR",
            site_subtype="CAMPGROUND",
            development_scale=3,
            development_label="moderate",
            reservation_tier=state_base.classify_reservation_tier(designation, row.get("USE_TYPE")),
            description=designation,
            directions=None,
            operated_by=OPERATED_BY,
            source_dataset=dataset,
            source_record={k: common.clean_str(v) for k, v in row.items()},
            snapshot_date=snapshot,
        )
        out.append(common.to_feature(lon, lat, props))

    return out
