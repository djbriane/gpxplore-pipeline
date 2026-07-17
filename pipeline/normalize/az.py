"""Arizona parks (non-federal) campground normalize adapter.

Source: the "ParksInArizona" point layer (ParksInArizona/FeatureServer/0), which
covers ~2860 parks across every agency with a per-park CAMPGROUND flag. We keep
only rows with CAMPGROUND == "Y" and drop federally managed sites, because
Forest Service / BLM / NPS / FWS campgrounds are already covered by the national
USFS and BLM sources - including them here would double-map the same sites. That
leaves Arizona State Parks, county/regional, tribal, and AZ Game & Fish
campgrounds (~130 records), all net-new coverage.

Rows are points, so no rollup or centroid math is strictly needed;
common.geometry_centroid also handles the Point case uniformly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import common
from . import state_base

# Agencies already covered by the national USFS / BLM sources (or out of scope
# as federal land) - excluded to avoid duplicate markers.
FEDERAL_AGENCIES = {
    "FOREST SERVICE",
    "BUREAU OF LAND MANAGEMENT",
    "NATIONAL PARK SERVICE",
    "FISH AND WILDLIFE SERVICE",
}


def _fee(val: Any) -> bool | None:
    s = str(val or "").strip().upper()
    if s == "Y":
        return True
    if s == "N":
        return False
    return None


def normalize(raw_path: Path, snapshot: str, source_tag: str) -> list[dict[str, Any]]:
    dataset = raw_path.name
    fc = common.read_geojson(raw_path)
    out: list[dict[str, Any]] = []

    for feat in fc.get("features", []):
        row = feat.get("properties", {})
        if str(row.get("CAMPGROUND") or "").strip().upper() != "Y":
            continue
        agency = str(row.get("MANAGENCY") or "").strip().upper()
        if agency in FEDERAL_AGENCIES:
            continue
        ll = common.geometry_centroid(feat.get("geometry"))
        if ll is None:
            continue
        lon, lat = ll
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue

        name = common.clean_str(row.get("PARKNAME")) or "Unknown"
        city = common.clean_str(row.get("CITY"))
        street = common.clean_str(row.get("STREET"))
        directions = ", ".join(x for x in (street, city) if x) or None

        props = state_base.canonical_properties(
            source=source_tag,
            site_id=common.clean_str(row.get("OBJECTID")),
            global_id=None,
            name=name,
            public_name=name,
            state="AZ",
            site_subtype="CAMPGROUND",
            development_scale=3,
            development_label="moderate",
            reservation_tier=state_base.classify_reservation_tier(row.get("PARKTYPE")),
            description=None,
            directions=directions,
            operated_by=common.clean_str(row.get("MANAGENCY")),
            source_dataset=dataset,
            source_record={k: common.clean_str(v) for k, v in row.items()},
            snapshot_date=snapshot,
        )
        props["fee_charged"] = _fee(row.get("FEE"))
        out.append(common.to_feature(lon, lat, props))

    return out
