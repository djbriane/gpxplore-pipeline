"""Washington State Parks campsites normalize adapter.

Source: the "Campsites" point layer of the Washington State Parks service
(services5.arcgis.com/.../Campsites/FeatureServer/78). This is campsite-level
data - one point per individual numbered site (~6k points) - so a straight
pass-through would produce thousands of markers. This adapter rolls the
campsites up into one campground per park (grouping by ParkName), recording the
site count as total_capacity, mirroring the Montana adapter's rollup approach.

Only "active" campsites are kept (the Filter field also carries a handful of
"inactive" and blank rows). Live and offline formats are identical GeoJSON.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import common
from . import state_base

OPERATED_BY = "Washington State Parks"


def _lonlat(feat: dict[str, Any]) -> tuple[float, float] | None:
    geom = feat.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if geom.get("type") != "Point" or len(coords) < 2:
        return None
    lon, lat = coords[0], coords[1]
    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        return None
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    return lon, lat


def normalize(raw_path: Path, snapshot: str, source_tag: str) -> list[dict[str, Any]]:
    dataset = raw_path.name
    fc = common.read_geojson(raw_path)
    out: list[dict[str, Any]] = []

    # Group active campsites by park; each group becomes one campground POI.
    groups: dict[str, dict[str, Any]] = {}
    for feat in fc.get("features", []):
        row = feat.get("properties", {})
        if str(row.get("Filter") or "").strip().lower() != "active":
            continue
        ll = _lonlat(feat)
        if ll is None:
            continue
        park = common.clean_str(row.get("ParkName"))
        code = common.clean_str(row.get("ParkCode"))
        key = code or park
        if not key:
            continue
        grp = groups.get(key)
        if grp is None:
            grp = {"park": park, "code": code, "coords": [], "notes": row.get("SourceNotes")}
            groups[key] = grp
        grp["coords"].append(ll)

    for key, grp in groups.items():
        coords = grp["coords"]
        if not coords:
            continue
        lon, lat = common.centroid(coords)
        name = grp["park"] or "Campground"
        props = state_base.canonical_properties(
            source=source_tag,
            site_id=grp["code"] or key,
            global_id=None,
            name=name,
            public_name=name,
            state="WA",
            site_subtype="CAMPGROUND",
            development_scale=3,
            development_label="moderate",
            reservation_tier=state_base.classify_reservation_tier(grp["notes"]),
            description=None,
            directions=None,
            operated_by=OPERATED_BY,
            source_dataset=dataset,
            source_record={"ParkName": grp["park"], "ParkCode": grp["code"]},
            snapshot_date=snapshot,
            total_capacity=len(coords) or None,
        )
        out.append(common.to_feature(lon, lat, props))

    return out
