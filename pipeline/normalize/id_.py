"""Idaho Parks & Facilities normalize adapter.

NOT a straight port. The reference script emitted a record for every row in
IDPR_Parks_and_Facilities unconditionally, including rows that are not
campgrounds at all: trails ("Ashton-Tetonia Trail"), parkways ("Coeur d'Alene
Parkway"), and a stray "Welcome to Idaho State Parks!" banner.

Known limitation: this source is one row per park with no structured field
indicating which parks actually have camping. We therefore (a) drop obvious
non-park rows by name pattern, and (b) apply a hand-curated deny-list of
day-use-only parks (pipeline/overrides/id_no_camping.json). This is the best
that can be done from this source alone; a properly-typed campground layer
would remove the need for the override (see the id-campground-vs-park note).

There is no per-campsite data in this file, so no rollup is needed here.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from .. import common
from . import state_base

OVERRIDES_PATH = common.REPO_ROOT / "pipeline" / "overrides" / "id_no_camping.json"

# Name patterns that identify non-campground rows in this dataset.
_NON_PARK_RE = re.compile(r"\b(trail|parkway)\b", re.IGNORECASE)

OPERATED_BY = "Idaho Department of Parks and Recreation"


def _load_no_camping() -> set[str]:
    try:
        with open(OVERRIDES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {n.strip() for n in data.get("no_camping", [])}
    except FileNotFoundError:
        return set()


def _is_excluded(name: str, no_camping: set[str]) -> bool:
    if not name:
        return True
    if name.lower().startswith("welcome to"):
        return True
    if _NON_PARK_RE.search(name):
        return True
    if name.strip() in no_camping:
        return True
    return False


def _lonlat_from_csv(row: dict[str, Any]) -> tuple[float, float] | None:
    x = common.safe_float(row.get("X"))
    y = common.safe_float(row.get("Y"))
    if x is None or y is None:
        return None
    return common.web_mercator_to_wgs84(x, y)


def _build_feature(
    row: dict[str, Any], lon: float, lat: float, snapshot: str, source_tag: str, dataset: str
) -> dict[str, Any]:
    name = common.clean_str(row.get("name"))
    desc = common.clean_str(row.get("description"))
    props = state_base.canonical_properties(
        source=source_tag,
        site_id=common.clean_str(row.get("objectid")),
        global_id=None,
        name=name,
        public_name=name,
        state="ID",
        site_subtype="STATE PARK",
        development_scale=3,
        development_label="moderate",
        reservation_tier=state_base.classify_reservation_tier(name, desc),
        description=desc,
        directions=None,
        operated_by=OPERATED_BY,
        source_dataset=dataset,
        source_record={k: common.clean_str(v) for k, v in row.items()},
        snapshot_date=snapshot,
    )
    props["usda_portal_url"] = common.clean_str(row.get("pic_url"))
    return common.to_feature(lon, lat, props)


def normalize(raw_path: Path, snapshot: str, source_tag: str) -> list[dict[str, Any]]:
    dataset = raw_path.name
    no_camping = _load_no_camping()
    out: list[dict[str, Any]] = []

    if raw_path.suffix.lower() == ".geojson":
        fc = common.read_geojson(raw_path)
        for feat in fc.get("features", []):
            row = dict(feat.get("properties", {}))
            name = (row.get("name") or "").strip()
            if _is_excluded(name, no_camping):
                continue
            coords = (feat.get("geometry") or {}).get("coordinates") or []
            if len(coords) < 2:
                continue
            out.append(_build_feature(row, coords[0], coords[1], snapshot, source_tag, dataset))
        return out

    with open(raw_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            if _is_excluded(name, no_camping):
                continue
            ll = _lonlat_from_csv(row)
            if ll is None:
                continue
            out.append(_build_feature(row, ll[0], ll[1], snapshot, source_tag, dataset))
    return out
