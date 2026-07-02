"""British Columbia Recreation Sites and Trails (RSTBC) normalize adapter.

Source: the official RSTBC "Recreation Sites, Reserves, and Interpretive Forests
Details and Closures" point service (2268 points across all project types). We
keep only recreation SITES (PROJECT_TYPE == "SIT - ...") that have at least one
DEFINED_CAMPSITES > 0 - i.e. actual campgrounds (~1147) - dropping trails,
reserves, and interpretive forests. DEFINED_CAMPSITES doubles as capacity.

Live and offline formats are identical GeoJSON. NOTE: this server caps queries
at 1000 records/page, so the registry page_size must stay <= 1000 or pagination
stops after the first page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import common
from . import state_base

OPERATED_BY = "Recreation Sites and Trails BC"


def normalize(raw_path: Path, snapshot: str, source_tag: str) -> list[dict[str, Any]]:
    dataset = raw_path.name
    fc = common.read_geojson(raw_path)
    out: list[dict[str, Any]] = []

    for feat in fc.get("features", []):
        row = feat.get("properties", {})
        if not str(row.get("PROJECT_TYPE") or "").strip().upper().startswith("SIT"):
            continue
        campsites = common.safe_int(row.get("DEFINED_CAMPSITES")) or 0
        if campsites <= 0:
            continue

        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") != "Point" or len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            continue
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue

        name = common.clean_str(row.get("PROJECT_NAME")) or "Unknown"

        props = state_base.canonical_properties(
            source=source_tag,
            site_id=common.clean_str(row.get("FTEN_RPD_SYSID")) or common.clean_str(row.get("OBJECTID")),
            global_id=None,
            name=name,
            public_name=name,
            state="BC",
            # RSTBC sites are rustic, user-maintained forest recreation sites.
            site_subtype="CAMPGROUND",
            development_scale=2,
            development_label="basic",
            reservation_tier=state_base.classify_reservation_tier(
                row.get("SITE_DESCRIPTION"), row.get("PROJECT_NAME")
            ),
            description=common.clean_str(row.get("SITE_DESCRIPTION")),
            directions=common.clean_str(row.get("DRIVING_DIRECTIONS")),
            operated_by=OPERATED_BY,
            source_dataset=dataset,
            source_record={k: common.clean_str(v) for k, v in row.items()},
            snapshot_date=snapshot,
            total_capacity=campsites,
        )
        out.append(common.to_feature(lon, lat, props))

    return out
