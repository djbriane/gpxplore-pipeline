"""BLM National Recreation Site Points normalize adapter.

Functionally equivalent to campgrounds-pipeline-bundle/scripts/filter_blm_campgrounds.py,
plus a campground-vs-campsite rollup fix (see _rollup_developed_sites below).
BLM encodes reservation/fee/development directly in the Feature Subtype string
(e.g. "Campsite - Developed - Non Reservable - Fee"), so classification is a
structured parse rather than a free-text scan. Supports both the CSV snapshot
and a live GeoJSON FeatureCollection (attributes carry the same field names).
"""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Any

from .. import common

DEVELOPED_SUBTYPES = {
    "Campground",
    "Campsite - Developed - Reservable - Fee",
    "Campsite - Developed - Reservable - No Fee",
    "Campsite - Developed - Non Reservable - Fee",
    "Campsite - Developed - Non Reservable - No Fee",
}
PRIMITIVE_SUBTYPES = {
    "Campsite - Primitive - Reservable - Fee",
    "Campsite - Primitive - Reservable - No Fee",
    "Campsite - Primitive - Non Reservable - Fee",
    "Campsite - Primitive - Non Reservable - No Fee",
    "Campsite - Undeveloped",
}
# Default inclusion set == reference script default (developed + campground only;
# primitive/undeveloped are intentionally left out to stay behavior-compatible).
ALLOWED_SUBTYPES = set(DEVELOPED_SUBTYPES)

# Individual developed-campsite points (as opposed to the "Campground" POI
# itself) that should roll up rather than each becoming their own map marker.
SITE_SUBTYPES = DEVELOPED_SUBTYPES - {"Campground"}

RESERVABLE_SUBTYPES = {
    "Campsite - Developed - Reservable - Fee",
    "Campsite - Developed - Reservable - No Fee",
    "Campsite - Primitive - Reservable - Fee",
    "Campsite - Primitive - Reservable - No Fee",
}

_SUBTYPE_RE = re.compile(
    r"Campsite\s*-\s*(Developed|Primitive)\s*-\s*(Reservable|Non Reservable)\s*-\s*(Fee|No Fee)",
    re.IGNORECASE,
)


def parse_subtype(subtype: str) -> dict[str, str | None]:
    if subtype == "Campground":
        return {"development": "campground", "reservable_raw": None, "fee_raw": None}
    if subtype == "Campsite - Undeveloped":
        return {"development": "undeveloped", "reservable_raw": "non_reservable", "fee_raw": None}
    m = _SUBTYPE_RE.match(subtype)
    if m:
        return {
            "development": m.group(1).lower(),
            "reservable_raw": "reservable" if m.group(2).lower() == "reservable" else "non_reservable",
            "fee_raw": "fee" if m.group(3).lower() == "fee" else "no_fee",
        }
    return {"development": None, "reservable_raw": None, "fee_raw": None}


def classify_reservation_tier(subtype: str) -> str:
    if subtype in RESERVABLE_SUBTYPES:
        return common.TIER_RESERVABLE
    if subtype == "Campground":
        return common.TIER_LIKELY_FCFS
    if subtype == "Campsite - Undeveloped":
        return common.TIER_DEFINITE_FCFS
    if "Non Reservable" in subtype:
        return common.TIER_DEFINITE_FCFS
    return common.TIER_LIKELY_FCFS


def _row_latlon(row: dict[str, Any]) -> tuple[float, float] | None:
    lat = common.safe_float(row.get("Latitude"))
    lon = common.safe_float(row.get("Longitude"))
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    # Drop null-island points: the BLM export has a handful of (0, 0) rows that
    # the reference script's loose bounds check let through.
    if lat == 0 and lon == 0:
        return None
    return lat, lon


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 6371.0088 * 2 * math.asin(math.sqrt(h))


# Distance within which a developed-campsite row is folded into a nearby
# explicit "Campground" row's site count instead of becoming its own POI.
# Chosen from the data: the vast majority of true site->campground distances
# are under 0.5km (a single facility footprint); the gap before the next
# cluster of unrelated points doesn't start until ~5-10km.
ANCHOR_ATTACH_KM = 2.0
# Distance within which developed-campsite rows with no nearby "Campground"
# row are clustered with *each other* into one synthesized campground POI
# (BLM has no "Campground" row at all for some facilities - e.g. boat-in site
# clusters like "Patos Island Campsite 1..8" - so those numbered sites are all
# each other has to group by).
ORPHAN_CLUSTER_KM = 0.5

_TRAILING_SITE_NUM_RE = re.compile(r"\s*(?:campsite|site)?\s*#?\s*\d+\s*$", re.IGNORECASE)


def _cluster_name(rows: list[dict[str, Any]]) -> str:
    """A shared name for a synthesized cluster: the common prefix once a
    trailing "Campsite N" / "Site N" / bare number is stripped (e.g. "Green
    River Access Site 9 Campsite 1/2/3" -> "Green River Access Site 9"),
    falling back to the shortest raw name if the rows don't share one."""
    raw_names = [(row.get("Feature Name") or "").strip() for row in rows]
    stripped = {_TRAILING_SITE_NUM_RE.sub("", n).strip() for n in raw_names if n}
    stripped.discard("")
    if len(stripped) == 1:
        return next(iter(stripped))
    non_empty = [n for n in raw_names if n]
    return min(non_empty, key=len) if non_empty else "Campground"


def _mode_subtype(rows: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        s = (row.get("Feature Subtype") or "").strip()
        counts[s] = counts.get(s, 0) + 1
    return max(counts, key=lambda s: counts[s])


class _UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, i: int) -> int:
        while self._parent[i] != i:
            self._parent[i] = self._parent[self._parent[i]]
            i = self._parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self._parent[ri] = rj


def _rollup_developed_sites(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[int, int]]:
    """Fold individual developed-campsite rows into a campground-level POI.

    Unlike MT/ID (which group by a shared Park Name field), BLM's site points
    carry no field linking them to a parent campground - "Site 1".."Site 8"
    near "Thibodeau Campground" share nothing but proximity. So this groups by
    distance instead:

      1. A developed-campsite row within ANCHOR_ATTACH_KM of an explicit
         "Campground" row is folded into that campground's site count.
      2. Remaining ("orphan") developed-campsite rows - facilities with no
         explicit "Campground" row at all - are clustered with each other
         (union-find over pairwise distance) within ORPHAN_CLUSTER_KM, and
         each multi-row cluster becomes one synthesized campground POI at the
         cluster's centroid, keeping the cluster's most common subtype (for
         accurate fee/reservation classification) and a derived shared name.
      3. A true singleton (no campground nearby, no sibling site nearby) is
         left as its own POI, unchanged - it may genuinely be a standalone
         developed site.

    Returns (output_rows, site_counts), where site_counts maps id(row) (for
    a row in output_rows) -> number of individual sites folded into it. Rows
    absent from site_counts have no attached sites.
    """
    campgrounds: list[tuple[dict[str, Any], tuple[float, float]]] = []
    sites: list[tuple[dict[str, Any], tuple[float, float]]] = []
    for row in rows:
        ll = _row_latlon(row)
        if ll is None:
            continue
        subtype = (row.get("Feature Subtype") or "").strip()
        if subtype == "Campground":
            campgrounds.append((row, ll))
        elif subtype in SITE_SUBTYPES:
            sites.append((row, ll))

    site_counts: dict[int, int] = {}
    unattached: list[tuple[dict[str, Any], tuple[float, float]]] = []

    for site_row, site_ll in sites:
        nearest = None
        if campgrounds:
            nearest_row, nearest_ll = min(campgrounds, key=lambda c: _haversine_km(site_ll, c[1]))
            if _haversine_km(site_ll, nearest_ll) <= ANCHOR_ATTACH_KM:
                nearest = nearest_row
        if nearest is not None:
            site_counts[id(nearest)] = site_counts.get(id(nearest), 0) + 1
        else:
            unattached.append((site_row, site_ll))

    uf = _UnionFind(len(unattached))
    for i in range(len(unattached)):
        for j in range(i + 1, len(unattached)):
            if _haversine_km(unattached[i][1], unattached[j][1]) <= ORPHAN_CLUSTER_KM:
                uf.union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(len(unattached)):
        clusters.setdefault(uf.find(i), []).append(i)

    synthesized: list[dict[str, Any]] = []
    for members in clusters.values():
        member_rows = [unattached[i][0] for i in members]
        if len(members) == 1:
            # Genuinely standalone site - keep it as-is, no rollup to apply.
            synthesized.append(member_rows[0])
            continue
        member_coords = [unattached[i][1] for i in members]  # (lat, lon) pairs
        lon, lat = common.centroid([(lo, la) for la, lo in member_coords])
        anchor = dict(member_rows[0])
        anchor["Feature Subtype"] = _mode_subtype(member_rows)
        anchor["Feature Name"] = _cluster_name(member_rows)
        anchor["Latitude"] = str(lat)
        anchor["Longitude"] = str(lon)
        site_counts[id(anchor)] = len(members)
        synthesized.append(anchor)

    return [r for r, _ll in campgrounds] + synthesized, site_counts


def _normalize_row(
    row: dict[str, Any], snapshot: str, source_tag: str, *, total_sites: int | None = None,
) -> dict[str, Any] | None:
    ll = _row_latlon(row)
    if ll is None:
        return None
    lat, lon = ll

    subtype = (row.get("Feature Subtype") or "").strip()
    parsed = parse_subtype(subtype)
    object_id = (str(row.get("OBJECTID") or "")).strip()
    name = (row.get("Feature Name") or "").strip()

    props = {
        "source": source_tag,
        "object_id": object_id,
        "global_id": (row.get("GlobalID") or "").strip(),
        "name": name,
        "admin_state": (row.get("Administrative State") or "").strip(),
        "admin_unit_code": (row.get("Administrative Unit Code") or "").strip(),
        "unit_name": common.clean_str(row.get("UNIT_NAME")),
        "feature_type": (str(row.get("Feature Type") or "")).strip(),
        "feature_subtype": subtype,
        "development": parsed["development"],
        "reservable": parsed["reservable_raw"],
        "has_fee": parsed["fee_raw"],
        "reservation_tier": classify_reservation_tier(subtype),
        "description": common.clean_str(row.get("DESCRIPTION")),
        "web_link": common.clean_str(row.get("WEB_LINK")),
        "total_capacity": total_sites,
        "ingest_hash": common.make_hash(object_id, lat, lon, name),
        "snapshot_date": snapshot,
    }
    return common.to_feature(lon, lat, props)


# Live geojson uses machine field NAMES; the offline CSV headers are the field
# ALIASES the rest of this adapter reads. Confirmed via `make blm-verify` against
# BLM_Natl_Recreation_Offline/MapServer/2.
LIVE_FIELD_MAP = {
    "FET_TYPE": "Feature Type",
    "FET_SUBTYPE": "Feature Subtype",
    "FET_NAME": "Feature Name",
    "ADM_UNIT_CD": "Administrative Unit Code",
    "ADMIN_ST": "Administrative State",
    "LAT": "Latitude",
    "LONG": "Longitude",
}


def _iter_rows(raw_path: Path) -> list[dict[str, Any]]:
    """Yield attribute rows from either the CSV snapshot or a live GeoJSON file."""
    if raw_path.suffix.lower() == ".geojson":
        fc = common.read_geojson(raw_path)
        rows = []
        for feat in fc.get("features", []):
            src = dict(feat.get("properties", {}))
            attrs = dict(src)
            # Alias any machine field names to the alias-based names used below.
            for machine, alias in LIVE_FIELD_MAP.items():
                if machine in src and alias not in attrs:
                    attrs[alias] = src[machine]
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or []
            # Prefer explicit Latitude/Longitude attributes; fall back to geometry.
            if not attrs.get("Latitude") and len(coords) >= 2:
                attrs["Longitude"], attrs["Latitude"] = coords[0], coords[1]
            rows.append(attrs)
        return rows
    with open(raw_path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def normalize(raw_path: Path, snapshot: str, source_tag: str) -> list[dict[str, Any]]:
    candidate_rows = [
        row for row in _iter_rows(raw_path)
        if (row.get("Feature Subtype") or "").strip() in ALLOWED_SUBTYPES
    ]
    rolled_rows, site_counts = _rollup_developed_sites(candidate_rows)

    out: list[dict[str, Any]] = []
    for row in rolled_rows:
        feat = _normalize_row(row, snapshot, source_tag, total_sites=site_counts.get(id(row)))
        if feat is not None:
            out.append(feat)
    return out
