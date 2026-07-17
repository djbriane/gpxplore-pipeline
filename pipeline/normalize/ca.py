"""California State Parks campgrounds normalize adapter.

Source: the California State Parks "Campgrounds" point layer
(services2.arcgis.com/.../Campgrounds/FeatureServer/0). Rows are already at
campground-area granularity - one point per developed/primitive camp area, each
with its own TYPE - so no rollup is needed.

LICENSE NOTE: this layer is COPYRIGHT California State Parks and the portal
requests a review of allowed uses (contact geodata@parks.ca.gov) before
commercial use. See the registry `note` for this source.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import common
from . import state_base

OPERATED_BY = "California State Parks"


def _dev_scale(type_upper: str) -> tuple[int, str]:
    if "DEVELOPED" in type_upper:
        return 3, "moderate"
    if "PRIMITIVE" in type_upper or "ENVIRONMENTAL" in type_upper:
        return 1, "minimal"
    if any(k in type_upper for k in ("HIKE", "BIKE", "WALK-IN", "TRAIL", "ENROUTE", "HORSE")):
        return 1, "minimal"
    return 2, "basic"


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
        camp_type = (common.clean_str(row.get("TYPE")) or "").strip()
        name = (
            common.clean_str(row.get("Campground"))
            or common.clean_str(row.get("DETAIL"))
            or "Campground"
        )
        dev_scale, dev_label = _dev_scale(camp_type.upper())

        props = state_base.canonical_properties(
            source=source_tag,
            # GISID (e.g. "GIS0006398") is unique per camp area; FID is the fallback.
            site_id=common.clean_str(row.get("GISID")) or common.clean_str(row.get("FID")),
            global_id=common.clean_str(row.get("GlobalID")),
            name=name,
            public_name=name,
            state="CA",
            site_subtype=(camp_type.upper() or "CAMPGROUND"),
            development_scale=dev_scale,
            development_label=dev_label,
            reservation_tier=state_base.classify_reservation_tier(camp_type, row.get("SUBTYPE")),
            description=common.clean_str(row.get("DETAIL")),
            directions=None,
            operated_by=OPERATED_BY,
            source_dataset=dataset,
            source_record={k: common.clean_str(v) for k, v in row.items()},
            snapshot_date=snapshot,
        )
        out.append(common.to_feature(lon, lat, props))

    return out
