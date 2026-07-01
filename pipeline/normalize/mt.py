"""Montana FWP State Parks facilities normalize adapter.

NOT a straight port. The reference script kept any row whose Facility
Type/Name/Park Name contained the substring "camp", then emitted one record
per row - so a single developed campground with 45 numbered "Campsite" points
produced 45 map markers, and unrelated infrastructure ("Restroom", "Parking",
"Campers Point") was swept in because a *name* contained "camp".

This adapter instead:
  * classifies rows by an explicit Facility Type allow-list;
  * rolls individual "Campsite" rows up into their parent campground (or a
    synthesized park-level campground when no explicit campground row exists),
    recording the site count as total_capacity;
  * keeps each "Backcountry Camp" row as its own dispersed POI;
  * drops everything else (hosts, restrooms, parking, boat ramps, ...).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .. import common
from . import state_base

# Facility Type values that are campground-level POIs on their own.
CAMPGROUND_TYPES = {
    "Campground (Utilities Available)",
    "Campground (Utilities Unavailable)",
    "Campground (Primitive)",
}
# Individual sites that should roll up into a parent campground.
SITE_TYPES = {"Campsite"}
# Dispersed/backcountry camps: distinct, far-apart, named - keep individual.
BACKCOUNTRY_TYPES = {"Backcountry Camp"}

OPERATED_BY = "Montana State Parks"


def _dev_scale(subtype_upper: str) -> tuple[int, str]:
    if "BACKCOUNTRY" in subtype_upper:
        return 1, "minimal"
    if "UTILITIES UNAVAILABLE" in subtype_upper or "PRIMITIVE" in subtype_upper:
        return 1, "minimal"
    if "CAMPGROUND" in subtype_upper:
        return 3, "moderate"
    return 2, "basic"


def _lonlat(row: dict[str, Any]) -> tuple[float, float] | None:
    x = common.safe_float(row.get("x"))
    y = common.safe_float(row.get("y"))
    if x is None or y is None:
        return None
    return common.web_mercator_to_wgs84(x, y)


def _feature_from_row(
    row: dict[str, Any],
    *,
    snapshot: str,
    source_tag: str,
    dataset: str,
    lon: float,
    lat: float,
    name: str,
    subtype: str,
    total_capacity: int | None,
) -> dict[str, Any]:
    ftype = (row.get("Facility Type") or "").strip()
    fname = (row.get("Facility Name") or "").strip()
    dev_scale, dev_label = _dev_scale(subtype.upper())
    props = state_base.canonical_properties(
        # OBJECTID is unique per facility row; SITEID is a park-level id shared by
        # many facilities, so it cannot be the POI id (would collapse distinct camps).
        source=source_tag,
        site_id=common.clean_str(row.get("OBJECTID")) or common.clean_str(row.get("SITEID")),
        global_id=common.clean_str(row.get("GlobalID")),
        name=name,
        public_name=name,
        state="MT",
        site_subtype=subtype.upper(),
        development_scale=dev_scale,
        development_label=dev_label,
        reservation_tier=state_base.classify_reservation_tier(ftype, fname, row.get("COMMENTS")),
        description=common.clean_str(row.get("COMMENTS")),
        directions=None,
        operated_by=OPERATED_BY,
        source_dataset=dataset,
        source_record={k: common.clean_str(v) for k, v in row.items()},
        snapshot_date=snapshot,
        total_capacity=total_capacity,
    )
    return common.to_feature(lon, lat, props)


def normalize(raw_path: Path, snapshot: str, source_tag: str) -> list[dict[str, Any]]:
    dataset = raw_path.name
    out: list[dict[str, Any]] = []

    with open(raw_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    # 1. Backcountry camps: individual dispersed POIs, no rollup.
    for row in rows:
        ftype = (row.get("Facility Type") or "").strip()
        if ftype not in BACKCOUNTRY_TYPES:
            continue
        ll = _lonlat(row)
        if ll is None:
            continue
        lon, lat = ll
        pname = (row.get("Park Name") or "").strip()
        fname = (row.get("Facility Name") or "").strip()
        name = fname or pname or ftype
        out.append(
            _feature_from_row(
                row,
                snapshot=snapshot,
                source_tag=source_tag,
                dataset=dataset,
                lon=lon,
                lat=lat,
                name=name,
                subtype="BACKCOUNTRY CAMP",
                total_capacity=None,
            )
        )

    # 2. Campgrounds + campsites: group by park, roll sites into campgrounds.
    def group_key(row: dict[str, Any]):
        return (row.get("Park Name") or "").strip()

    def is_parent(row: dict[str, Any]) -> bool:
        return (row.get("Facility Type") or "").strip() in CAMPGROUND_TYPES

    def is_site(row: dict[str, Any]) -> bool:
        return (row.get("Facility Type") or "").strip() in SITE_TYPES

    groups = common.rollup_sites(rows, group_key=group_key, is_parent=is_parent, is_site=is_site)

    for grp in groups:
        # Number of individual sites rolled into this campground (capacity).
        site_coords = [c for c in (_lonlat(r) for r in grp.sites) if c is not None]
        total_capacity = grp.site_count or None

        if grp.parent is not None:
            row = grp.parent
            ll = _lonlat(row)
            if ll is None:
                continue
            lon, lat = ll
            pname = (row.get("Park Name") or "").strip()
            fname = (row.get("Facility Name") or "").strip()
            ftype = (row.get("Facility Type") or "").strip()
            name = fname or pname or ftype
            out.append(
                _feature_from_row(
                    row,
                    snapshot=snapshot,
                    source_tag=source_tag,
                    dataset=dataset,
                    lon=lon,
                    lat=lat,
                    name=name,
                    subtype=(ftype or "CAMPGROUND").upper(),
                    total_capacity=total_capacity,
                )
            )
        else:
            # No explicit campground row: synthesize one from the site cluster.
            if not site_coords:
                continue
            lon, lat = common.centroid(site_coords)
            anchor = grp.sites[0]
            pname = (anchor.get("Park Name") or "").strip()
            name = pname or "Campground"
            # Preserve the park identity but not a single site's record.
            synth = dict(anchor)
            synth["Facility Type"] = "Campground"
            synth["Facility Name"] = ""
            out.append(
                _feature_from_row(
                    synth,
                    snapshot=snapshot,
                    source_tag=source_tag,
                    dataset=dataset,
                    lon=lon,
                    lat=lat,
                    name=name,
                    subtype="CAMPGROUND",
                    total_capacity=total_capacity,
                )
            )

    return out
