"""Compact stage.

Ports campgrounds-pipeline-bundle/scripts/build-campgrounds.mjs to Python 1:1
(cleanName / trim / trimMulti / USFS_TYPE / RES_MAP / RES_CONFIDENCE) with two
deliberate changes, both documented in the plan:

  1. The USFS `w` / `rt` availability flags use common.text_indicates_available()
     instead of a truthy check, so "No water is available" no longer sets w=1.
     The detail text (w_d / rt_d) is still emitted verbatim either way.
  2. A new state-campgrounds.json builder (MT / ID / CO), which the original
     Node script never produced.

BLM's `c` (capacity) is new too: normalize/blm.py rolls individual developed
campsite points up into their parent campground (see that module's
_rollup_developed_sites), and the number of rolled-up sites is surfaced here
as `c` - the original reference script had no rollup and no capacity field
for BLM at all.

Output records are deduped by `i` per agency file (guaranteeing the CampRecord
"unique within an agency file" contract) and validated against
schema/camp-record.schema.json before writing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import common

# ---------------------------------------------------------------------------
# Ported helpers (verbatim behavior from build-campgrounds.mjs)
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"\w\S*")
_WS_RE = re.compile(r"\s+")
_LETTERS_RE = re.compile(r"[^A-Za-z]")


def title_case(s: str) -> str:
    return _WORD_RE.sub(lambda m: m.group()[0].upper() + m.group()[1:].lower(), s)


def clean_name(n: str | None) -> str:
    if not n:
        return ""
    letters = _LETTERS_RE.sub("", n)
    if len(letters) > 2 and letters == letters.upper():
        return title_case(n)
    return n


def trim(s: Any, max_len: int = 1200) -> str | None:
    if not s or not isinstance(s, str):
        return None
    cleaned = _WS_RE.sub(" ", s.strip())
    if not cleaned:
        return None
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "\u2026"


def trim_multi(s: Any, max_len: int = 800) -> str | None:
    if not s or not isinstance(s, str):
        return None
    cleaned = s.replace("\r\n", "\n")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned).strip()
    if not cleaned:
        return None
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "\u2026"


USFS_TYPE = {
    "CAMPGROUND": "campground",
    "GROUP CAMPGROUND": "group",
    "CAMPING AREA": "dispersed",
    "HORSE CAMP": "horse",
}
RES_MAP = {
    "reservable": "res",
    "mixed": "mixed",
    "likely_fcfs": "fcfs",
    "definite_fcfs": "fcfs",
}
RES_CONFIDENCE = {
    "definite_fcfs": "def",
    "likely_fcfs": "likely",
}

USFS_DROP_SUBTYPES = {"CAMP UNIT", "CAMP UNIT - TENT"}


# ---------------------------------------------------------------------------
# Per-source record builders
# ---------------------------------------------------------------------------

def _fee_flag(fee_charged: Any) -> int | None:
    if fee_charged is True:
        return 1
    if fee_charged is False:
        return 0
    return None


def build_usfs(p: dict[str, Any], x: float, y: float, counters: dict[str, int]) -> dict[str, Any] | None:
    sub = p.get("site_subtype")
    if sub in USFS_DROP_SUBTYPES:
        counters["dropped_camp_units"] += 1
        return None
    t = USFS_TYPE.get(sub, "other")
    rec: dict[str, Any] = {
        "i": p.get("site_id"),
        "n": clean_name(p.get("public_name") or p.get("name")),
        "t": t,
        "y": y,
        "x": x,
        "r": RES_MAP.get(p.get("reservation_tier")),
        "f": _fee_flag(p.get("fee_charged")),
    }
    if RES_CONFIDENCE.get(p.get("reservation_tier")):
        rec["rc"] = RES_CONFIDENCE[p["reservation_tier"]]

    if p.get("total_capacity"):
        rec["c"] = p["total_capacity"]

    # --- water / restroom: semantic classification (see module docstring) ---
    if common.text_indicates_available(p.get("water_availability")) is True:
        rec["w"] = 1
        counters["water_flagged"] += 1
    if p.get("water_availability"):
        counters["water_present"] += 1
    if common.text_indicates_available(p.get("restroom_availability")) is True:
        rec["rt"] = 1
        counters["restroom_flagged"] += 1
    if p.get("restroom_availability"):
        counters["restroom_present"] += 1

    if p.get("usda_portal_url"):
        rec["u"] = p["usda_portal_url"]
    if p.get("elevation_ft") and p["elevation_ft"] != "feet":
        rec["el"] = p["elevation_ft"]
    if p.get("closest_towns"):
        rec["tn"] = p["closest_towns"]
    if p.get("development_label"):
        rec["d"] = p["development_label"]
    if isinstance(p.get("development_scale"), int):
        rec["ds"] = p["development_scale"]

    desc = trim(p.get("description"), 1400)
    if desc:
        rec["desc"] = desc
    op = trim(p.get("operated_by"), 100)
    if op:
        rec["op"] = op
    fee_d = trim_multi(p.get("fee_description"), 800)
    if fee_d:
        rec["fee_d"] = fee_d
    ft = trim(p.get("fee_type"), 80)
    if ft:
        rec["ft"] = ft
    wd = trim(p.get("water_availability"), 160)
    if wd:
        rec["w_d"] = wd
    rtd = trim(p.get("restroom_availability"), 160)
    if rtd:
        rec["rt_d"] = rtd
    dirs = trim(p.get("directions"), 600)
    if dirs:
        rec["dir"] = dirs
    rest = trim(p.get("restrictions"), 400)
    if rest:
        rec["rest"] = rest
    cond = trim(p.get("current_conditions"), 400)
    if cond:
        rec["cond"] = cond
    season = trim(p.get("open_season"), 200)
    if season:
        rec["sea"] = season
    hours = trim(p.get("operational_hours"), 200)
    if hours:
        rec["hrs"] = hours
    imp = trim(p.get("important_info"), 400)
    if imp:
        rec["imp"] = imp
    return rec


def build_blm(p: dict[str, Any], x: float, y: float) -> dict[str, Any]:
    t = "campground" if p.get("development") == "campground" else "developed"
    reservable = p.get("reservable")
    r = RES_MAP.get(p.get("reservation_tier"))
    if r is None:
        r = "res" if reservable == "reservable" else "fcfs" if reservable == "non_reservable" else None
    rec: dict[str, Any] = {
        "i": p.get("object_id"),
        "n": clean_name(p.get("name")),
        "t": t,
        "y": y,
        "x": x,
        "r": r,
        "f": 1 if p.get("has_fee") == "fee" else 0 if p.get("has_fee") == "no_fee" else None,
    }
    if RES_CONFIDENCE.get(p.get("reservation_tier")):
        rec["rc"] = RES_CONFIDENCE[p["reservation_tier"]]
    if p.get("total_capacity"):
        rec["c"] = p["total_capacity"]
    if p.get("web_link"):
        rec["u"] = p["web_link"]
    if p.get("admin_state"):
        rec["st"] = p["admin_state"]
    if p.get("unit_name"):
        rec["tn"] = p["unit_name"]
    if p.get("feature_subtype"):
        rec["sub"] = p["feature_subtype"]
    desc = trim(p.get("description"), 1400)
    if desc:
        rec["desc"] = desc
    return rec


def _state_type(subtype: str | None) -> str:
    s = (subtype or "").upper()
    if "BACKCOUNTRY" in s:
        return "dispersed"
    if "GROUP" in s:
        return "group"
    return "campground"


def _state_id(p: dict[str, Any]) -> str:
    """Namespace state ids by state code.

    state-campgrounds.json combines MT/ID/CO, each with its own independent
    OBJECTID integer space, so a bare id can collide across states. Prefixing
    with the state code keeps `i` unique within this combined agency file.
    """
    sid = p.get("site_id")
    st = p.get("state")
    if st and sid:
        return f"{st}-{sid}"
    return sid


def build_state(p: dict[str, Any], x: float, y: float) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "i": _state_id(p),
        "n": clean_name(p.get("public_name") or p.get("name")),
        "t": _state_type(p.get("site_subtype")),
        "y": y,
        "x": x,
        "r": RES_MAP.get(p.get("reservation_tier")),
        "f": _fee_flag(p.get("fee_charged")),
    }
    if RES_CONFIDENCE.get(p.get("reservation_tier")):
        rec["rc"] = RES_CONFIDENCE[p["reservation_tier"]]
    if p.get("total_capacity"):
        rec["c"] = p["total_capacity"]
    if p.get("state"):
        rec["st"] = p["state"]
    if p.get("development_label"):
        rec["d"] = p["development_label"]
    if isinstance(p.get("development_scale"), int):
        rec["ds"] = p["development_scale"]
    if p.get("site_subtype"):
        rec["sub"] = p["site_subtype"]
    if p.get("usda_portal_url"):
        rec["u"] = p["usda_portal_url"]
    op = trim(p.get("operated_by"), 100)
    if op:
        rec["op"] = op
    desc = trim(p.get("description"), 1400)
    if desc:
        rec["desc"] = desc
    dirs = trim(p.get("directions"), 600)
    if dirs:
        rec["dir"] = dirs
    return rec


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

STATE_SOURCES = {
    "mt_state_parks",
    "id_state_parks",
    "co_state_parks",
    "wy_state_parks",
    "wa_state_parks",
    "ca_state_parks",
    "or_state_parks",
    "az_parks",
    "bc_rec_sites",
}

POI_OUTPUT: dict[str, tuple[str, str]] = {
    "usfs_infra_poi": ("usfs-pois.json", "usfs_infra"),
    "nrhp": ("nrhp-pois.json", "nrhp"),
}
POI_SOURCE_TAGS = {source: tag for source, (_fname, tag) in POI_OUTPUT.items()}
POI_FILENAMES = {fname for fname, _tag in POI_OUTPUT.values()}


def _dedupe_by_id(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Keep first record per `i` (guarantees per-file id uniqueness)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    collisions = 0
    for rec in records:
        rid = rec.get("i")
        if rid in seen:
            collisions += 1
            continue
        seen.add(rid)
        out.append(rec)
    return out, collisions


def build_poi(p: dict[str, Any], x: float, y: float) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "i": p.get("site_id"),
        "n": clean_name(p.get("public_name") or p.get("name")),
        "t": p.get("category"),
        "y": y,
        "x": x,
        "src": POI_SOURCE_TAGS.get(p.get("source"), p.get("source")),
    }
    if p.get("subtype_raw"):
        rec["sub"] = p["subtype_raw"]
    if p.get("url"):
        rec["u"] = p["url"]
    if p.get("elevation_ft") and p["elevation_ft"] != "feet":
        rec["el"] = p["elevation_ft"]
    if p.get("state"):
        rec["st"] = p["state"]
    if p.get("significant_year"):
        rec["yr"] = p["significant_year"]
    if p.get("ref_number"):
        rec["ref"] = p["ref_number"]
    if p.get("commodity"):
        rec["com"] = p["commodity"]
    desc = trim(p.get("description"), 1400)
    if desc:
        rec["desc"] = desc
    op = trim(p.get("operated_by"), 100)
    if op:
        rec["op"] = op
    return rec


def compact_features(features: list[dict[str, Any]]) -> dict[str, Any]:
    counters = {
        "dropped_camp_units": 0,
        "water_present": 0,
        "water_flagged": 0,
        "restroom_present": 0,
        "restroom_flagged": 0,
    }
    usfs: list[dict[str, Any]] = []
    blm: list[dict[str, Any]] = []
    state: list[dict[str, Any]] = []
    poi_files: dict[str, list[dict[str, Any]]] = {fname: [] for fname in POI_FILENAMES}

    for feat in features:
        p = feat.get("properties", {})
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2 or not isinstance(coords[0], (int, float)) or not isinstance(coords[1], (int, float)):
            continue
        x = common.round5(coords[0])
        y = common.round5(coords[1])
        source = p.get("source")

        if source == "usfs_infra":
            rec = build_usfs(p, x, y, counters)
            if rec is not None:
                usfs.append(rec)
        elif source == "blm_recreation":
            blm.append(build_blm(p, x, y))
        elif source in STATE_SOURCES:
            state.append(build_state(p, x, y))
        elif source in POI_SOURCE_TAGS:
            fname = POI_OUTPUT[source][0]
            poi_files[fname].append(build_poi(p, x, y))

    usfs, usfs_dupes = _dedupe_by_id(usfs)
    blm, blm_dupes = _dedupe_by_id(blm)
    state, state_dupes = _dedupe_by_id(state)
    poi_dupes: dict[str, int] = {}
    for fname in POI_FILENAMES:
        poi_files[fname], poi_dupes[fname] = _dedupe_by_id(poi_files[fname])

    # Amenity-flag stats computed on the final (deduped) USFS file. w_d/rt_d are
    # present whenever the raw field had any text (== the old truthy behavior),
    # so they let us report the before/after impact of the semantic fix.
    counters["water_present"] = sum(1 for r in usfs if "w_d" in r)
    counters["water_flagged"] = sum(1 for r in usfs if r.get("w") == 1)
    counters["restroom_present"] = sum(1 for r in usfs if "rt_d" in r)
    counters["restroom_flagged"] = sum(1 for r in usfs if r.get("rt") == 1)

    files = {
        "usfs-campgrounds.json": usfs,
        "blm-campgrounds.json": blm,
        "state-campgrounds.json": state,
    }
    files.update(poi_files)

    return {
        "files": files,
        "counters": counters,
        "id_collisions": {
            "usfs": usfs_dupes,
            "blm": blm_dupes,
            "state": state_dupes,
            **{f"poi:{fname}": poi_dupes[fname] for fname in POI_FILENAMES},
        },
    }


def _validate_records(records: list[dict[str, Any]], schema: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    for i, rec in enumerate(records):
        errs = common.validate_object(rec, schema, path=f"{label}[{i}]")
        errors.extend(errs)
    return errors


def run(*, snapshot: str | None = None, merged_dir: Path | None = None,
        compact_dir: Path | None = None) -> dict[str, Any]:
    merged_dir = merged_dir or common.MERGED_DIR
    compact_dir = compact_dir or common.COMPACT_DIR

    if snapshot:
        src_dir = merged_dir / snapshot
    else:
        src_dir = common.latest_snapshot_dir(merged_dir)
    if src_dir is None or not (src_dir / "merged.geojson").exists():
        raise FileNotFoundError("no merged snapshot found. Run `make merge` first.")
    snapshot = src_dir.name

    features = common.read_geojson(src_dir / "merged.geojson").get("features", [])
    result = compact_features(features)

    # Validate every output record against the frozen app-facing schema.
    camp_schema = common.load_schema("camp-record.schema.json")
    poi_schema = common.load_schema("poi-record.schema.json")
    all_errors: list[str] = []
    for fname, records in result["files"].items():
        schema = poi_schema if fname in POI_FILENAMES else camp_schema
        all_errors.extend(_validate_records(records, schema, fname))
    if all_errors:
        preview = "\n".join(all_errors[:20])
        raise ValueError(f"compact output failed schema validation:\n{preview}")

    out_dir = compact_dir / snapshot
    for fname, records in result["files"].items():
        common.write_json(out_dir / fname, records)

    counters = result["counters"]
    summary = {
        "snapshot_date": snapshot,
        "counts": {k: len(v) for k, v in result["files"].items()},
        "dropped_camp_units": counters["dropped_camp_units"],
        "id_collisions_dropped": result["id_collisions"],
        "water_flags": {
            "usfs_records_with_water_text": counters["water_present"],
            "usfs_records_flagged_available_new": counters["water_flagged"],
            "would_have_been_flagged_old_truthy_check": counters["water_present"],
        },
        "restroom_flags": {
            "usfs_records_with_restroom_text": counters["restroom_present"],
            "usfs_records_flagged_available_new": counters["restroom_flagged"],
            "would_have_been_flagged_old_truthy_check": counters["restroom_present"],
        },
        "output_dir": str(out_dir),
    }
    common.write_json(out_dir / "compact-summary.json", summary, indent=2)

    print(f"  compact [{snapshot}]:")
    for fname, records in result["files"].items():
        print(f"    {fname}: {len(records)} records")
    print(f"    dropped USFS camp-units: {counters['dropped_camp_units']}")
    print(f"    water flag (new semantic vs old truthy): "
          f"{counters['water_flagged']} vs {counters['water_present']} "
          f"(-{counters['water_present'] - counters['water_flagged']})")
    print(f"    restroom flag (new semantic vs old truthy): "
          f"{counters['restroom_flagged']} vs {counters['restroom_present']} "
          f"(-{counters['restroom_present'] - counters['restroom_flagged']})")
    return summary
