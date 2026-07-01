"""Colorado campgrounds normalize adapter.

Functionally equivalent to the CO branch of build_state_campgrounds_geojson.py.
CO rows are already at the correct campground/loop granularity (each with its
own site count), so no rollup is needed.

Incidental fix (noted in the plan): the raw export's FAC_TYPE is a numeric
domain code (e.g. "1070"), not decoded text, so the reference script's
`"CAMPGROUND" in fac_type` test never matched and every record was labelled
development_scale 2 / "basic". We decode the known code(s) so campgrounds are
labelled correctly, falling back to the reference behavior for unknown codes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import common
from . import state_base

OPERATED_BY_DEFAULT = "Colorado Parks and Wildlife"

# FAC_TYPE domain-code decode. The 2026 snapshot uses a single code (1070) for
# all rows; decode it explicitly and treat unknown codes as the raw string.
FAC_TYPE_CODES = {
    "1070": "CAMPGROUND",
}


def _decode_fac_type(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    return FAC_TYPE_CODES.get(s, s)


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
        fac_type = _decode_fac_type(row.get("FAC_TYPE"))
        is_campground = "CAMPGROUND" in fac_type
        name = common.clean_str(row.get("FAC_NAME")) or common.clean_str(row.get("PROPNAME")) or "Unknown"

        props = state_base.canonical_properties(
            source=source_tag,
            # OBJECTID is unique per row; FAC_ID (e.g. "BONCMP") is shared across
            # distinct campgrounds, so prefer OBJECTID for a unique POI id.
            site_id=common.clean_str(row.get("OBJECTID")) or common.clean_str(row.get("FAC_ID")),
            global_id=common.clean_str(row.get("GlobalID")),
            name=name,
            public_name=name,
            state="CO",
            site_subtype=fac_type or "CAMPGROUND",
            development_scale=3 if is_campground else 2,
            development_label="moderate" if is_campground else "basic",
            reservation_tier=state_base.classify_reservation_tier(
                row.get("FAC_TYPE"), row.get("TYPE_DETAIL"), row.get("COMMENTS")
            ),
            description=common.clean_str(row.get("COMMENTS")) or common.clean_str(row.get("TYPE_DETAIL")),
            directions=common.clean_str(row.get("ST_ADDRESS")),
            operated_by=common.clean_str(row.get("MGMT_AUTH")) or OPERATED_BY_DEFAULT,
            source_dataset=dataset,
            source_record={k: common.clean_str(v) for k, v in row.items()},
            snapshot_date=snapshot,
            total_capacity=common.safe_int(row.get("SITE_COUNT")),
        )
        out.append(common.to_feature(lon, lat, props))

    return out
