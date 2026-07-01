"""iOS snapshot stage.

Ports gpxplore-web/apps/planner/scripts/build-ios-campground-snapshot.mjs
(retired) to Python, so this pipeline produces the iOS client's gzipped
snapshot itself instead of depending on a checked-out gpxplore-web at publish
time. See README "Publishing to gpxplore-web and gpxplore-ios".

Reads directly from this repo's own compact output (data/compact/<snapshot>/),
not from gpxplore-web's public/data - so the marker/detail snapshot always
describes exactly the same records as the CampRecord files, even before
gpxplore-web has actually been written to (e.g. a publish-downstream dry run).

Ported behavior:
  - tier_for(): dispersed -> p3, reservable ("r" == "res") -> p2, else p1.
    Mirrors packages/route-components/src/lib/campgroundShared.ts::tierFor in
    gpxplore-web, the single source of truth for tier semantics. That function
    predates this pipeline as a manually-kept-in-sync copy (not shared code)
    in the retired .mjs script too, so this doesn't introduce a new sync risk
    - just carries the existing one over. Update by hand if tierFor changes.
  - sanitize_url(): drops malformed source URLs so the iOS client's atomic
    detail-map decode can't be broken by one bad value.
  - detect_permanent_closure(): conservative "(closed)"/"permanently closed"
    detection across the free-text fields, skipping partial (sub-feature)
    closures.
  - IdAssigner: the compact `i` id is only unique per agency file, not
    globally; assigns a stable agency-prefixed id, disambiguating
    same-agency/same-id-different-location collisions with a coordinate hash,
    and dropping exact (agency, id, coordinates) duplicates.

One deliberate change from the retired script: `version` is the pipeline
snapshot date (data/compact/<snapshot>/), not "today" - so re-running the
snapshot build for the same compact output is fully reproducible.
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import common

# Fields that live in the marker index; everything else goes in the detail payload.
MARKER_FIELDS = {"i", "n", "t", "y", "x", "r", "f"}

# Free-text fields that may state a permanent closure. There is no dedicated
# status field in the source data, so we detect a *permanent* closure only
# (high precision) and leave seasonal/temporary closures to the generic
# "verify status" disclaimer.
CLOSURE_TEXT_FIELDS = ("u", "desc", "dir", "cond", "imp", "hrs", "rest", "sea")
_PERMANENT_CLOSURE_RE = re.compile(r"\bpermanent(?:ly)?\b[^.!?\n]*\bclos", re.IGNORECASE)
# A closure sentence that mentions a sub-feature is usually a *partial* closure
# (e.g. "the boat ramp ... is permanently closed"), not the whole site. Skip it.
_SUBFEATURE_RE = re.compile(
    r"\b(road|rd|ramp|spur|loop|trail|bridge|gate|boat|dock|well|pump|spring|"
    r"toilet|restroom|day[-\s]?use)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_CLOSED_NAME_RE = re.compile(r"\(closed\)", re.IGNORECASE)

_WS_RE = re.compile(r"\s+")
_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
_SCHEMELESS_HOST_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(/|$|\?)", re.IGNORECASE)

SOFT_CEILING_MB = 10

AGENCY_FILES = (
    ("usfs-campgrounds.json", "usfs"),
    ("blm-campgrounds.json", "blm"),
    ("state-campgrounds.json", "state"),
)


def tier_for(rec: dict[str, Any]) -> str:
    if rec.get("t") == "dispersed":
        return "p3"
    if rec.get("r") == "res":
        return "p2"
    return "p1"


def sanitize_url(value: Any) -> str | None:
    """Clean a source URL, or return None to omit a malformed one.

    Source data carries some malformed entries: hosts split by stray spaces
    ("https://www. fs. usda. gov/..."), scheme-less hosts
    ("www.fs.usda.gov/..."), and free text ("None"). The iOS client decodes
    the whole detail map atomically, so a single non-URL string would drop
    *every* campground's detail.
    """
    if not isinstance(value, str):
        return None
    s = _WS_RE.sub("", value)
    if not s:
        return None
    if not _SCHEME_RE.match(s):
        if _SCHEMELESS_HOST_RE.match(s):
            s = f"https://{s}"
        else:
            return None  # free text like "None"
    parts = urlsplit(s)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return s


def detect_permanent_closure(rec: dict[str, Any]) -> str | None:
    """Rider-facing closure reason when permanently closed, else None."""
    for key in CLOSURE_TEXT_FIELDS:
        v = rec.get(key)
        if not isinstance(v, str) or not _PERMANENT_CLOSURE_RE.search(v):
            continue
        parts = _SENTENCE_SPLIT_RE.split(v)
        sentence = next((p for p in parts if _PERMANENT_CLOSURE_RE.search(p)), v)
        if _SUBFEATURE_RE.search(sentence):
            continue  # partial closure (a ramp/road/loop), not the whole site
        return _WS_RE.sub(" ", sentence.strip())[:180]
    name = rec.get("n")
    if isinstance(name, str) and _CLOSED_NAME_RE.search(name):
        return "Marked closed in source data."
    return None


_BASE36_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


def _to_base36(n: int) -> str:
    if n == 0:
        return "0"
    digits = []
    while n:
        n, rem = divmod(n, 36)
        digits.append(_BASE36_DIGITS[rem])
    return "".join(reversed(digits))


def _coord_discriminator(y: Any, x: Any) -> str:
    """FNV-1a 32-bit hash of "y,x", base36-encoded."""
    h = 2166136261
    for ch in f"{y},{x}":
        h = (h ^ ord(ch)) & 0xFFFFFFFF
        h = (h * 16777619) & 0xFFFFFFFF
    return _to_base36(h)


class IdAssigner:
    """Assigns globally-unique, stable ids across agencies.

    The compact `i` id is only unique per agency file: distinct campgrounds
    can collide on the same id within an agency (different coordinates) and
    always collide across agencies, and a few rows are exact duplicates.
    """

    def __init__(self) -> None:
        self._used_ids: set[str] = set()
        self._exact_keys: set[str] = set()
        self.disambiguated = 0
        self.dropped_exact_duplicates = 0

    def assign(self, src: str, raw_id: Any, y: Any, x: Any) -> str | None:
        """Returns a unique id for the record, or None if it's an exact duplicate."""
        exact = f"{src}:{raw_id}:{y}:{x}"
        if exact in self._exact_keys:
            self.dropped_exact_duplicates += 1
            return None
        self._exact_keys.add(exact)

        base = f"{src}-{raw_id}"
        if base not in self._used_ids:
            self._used_ids.add(base)
            return base

        self.disambiguated += 1
        candidate = f"{base}-{_coord_discriminator(y, x)}"
        n = 2
        while candidate in self._used_ids:
            candidate = f"{base}-{_coord_discriminator(y, x)}-{n}"
            n += 1
        self._used_ids.add(candidate)
        return candidate


def process_agency(
    records: list[dict[str, Any]],
    src: str,
    assigner: IdAssigner,
    marker_records: list[dict[str, Any]],
    details: dict[str, dict[str, Any]],
) -> None:
    for rec in records:
        raw_id = rec.get("i")
        if not raw_id:
            continue

        rid = assigner.assign(src, raw_id, rec.get("y"), rec.get("x"))
        if rid is None:
            continue

        closure_reason = detect_permanent_closure(rec)

        marker: dict[str, Any] = {
            "i": rid,
            "n": rec.get("n"),
            "t": rec.get("t"),
            "src": src,
            "tier": tier_for(rec),
            "y": rec.get("y"),
            "x": rec.get("x"),
            "r": rec.get("r"),
            "f": rec.get("f"),
        }
        # `cl` flag lives in the marker index so the map can mute closed sites
        # without loading the detail payload. Omitted when open.
        if closure_reason:
            marker["cl"] = True
        marker_records.append(marker)

        detail: dict[str, Any] = {}
        for k, v in rec.items():
            if k in MARKER_FIELDS or v is None:
                continue
            if k == "u":
                clean = sanitize_url(v)
                if clean:
                    detail["u"] = clean
                continue
            detail[k] = v
        # `cl_r` carries the rider-facing closure reason for the detail banner.
        if closure_reason:
            detail["cl_r"] = closure_reason
        if detail:
            details[rid] = detail


def build_snapshot(agency_records: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    assigner = IdAssigner()
    marker_records: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    total_records = 0

    for _fname, src in AGENCY_FILES:
        records = agency_records.get(src, [])
        total_records += len(records)
        process_agency(records, src, assigner, marker_records, details)

    return {
        "marker_records": marker_records,
        "details": details,
        "total_records": total_records,
        "disambiguated": assigner.disambiguated,
        "dropped_exact_duplicates": assigner.dropped_exact_duplicates,
    }


def _gzip_json(data: Any) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return gzip.compress(payload, compresslevel=9, mtime=0)


def run(*, snapshot: str | None = None, compact_dir: Path | None = None,
        out_dir: Path | None = None) -> dict[str, Any]:
    compact_dir = compact_dir or common.COMPACT_DIR
    out_base = out_dir or common.IOS_SNAPSHOT_DIR

    if snapshot:
        src_dir = compact_dir / snapshot
    else:
        src_dir = common.latest_snapshot_dir(compact_dir)
    if src_dir is None or not src_dir.exists():
        raise FileNotFoundError("no compact output found. Run `make compact` first.")
    snapshot = src_dir.name

    agency_records: dict[str, list[dict[str, Any]]] = {}
    for fname, src in AGENCY_FILES:
        path = src_dir / fname
        if not path.exists():
            raise FileNotFoundError(f"compact output missing: {path}. Run `make compact` first.")
        with open(path, encoding="utf-8") as f:
            records = json.load(f)
        print(f"  {src}: {len(records)} records")
        agency_records[src] = records

    result = build_snapshot(agency_records)

    marker_payload = {"version": snapshot, "records": result["marker_records"]}
    detail_payload = {"version": snapshot, "details": result["details"]}

    dest_dir = out_base / snapshot
    dest_dir.mkdir(parents=True, exist_ok=True)

    marker_gz = _gzip_json(marker_payload)
    detail_gz = _gzip_json(detail_payload)
    (dest_dir / "campground-marker-index.json.gz").write_bytes(marker_gz)
    (dest_dir / "campground-detail.json.gz").write_bytes(detail_gz)

    raw_marker_kb = len(json.dumps(marker_payload, ensure_ascii=False)) / 1024
    raw_detail_kb = len(json.dumps(detail_payload, ensure_ascii=False)) / 1024
    print(f"  \u2192 campground-marker-index.json.gz: {raw_marker_kb:.0f} KB raw, "
          f"{len(marker_gz) / 1024:.0f} KB gzipped")
    print(f"  \u2192 campground-detail.json.gz: {raw_detail_kb:.0f} KB raw, "
          f"{len(detail_gz) / 1024:.0f} KB gzipped")
    print(f"  ids: {len(result['marker_records'])} unique "
          f"({result['disambiguated']} disambiguated, "
          f"{result['dropped_exact_duplicates']} exact duplicates dropped)")

    total_bytes = len(marker_gz) + len(detail_gz)
    over_budget = total_bytes > SOFT_CEILING_MB * 1024 * 1024
    status = "OVER SOFT CEILING" if over_budget else "OK"
    print(f"\n[{status}] {len(result['marker_records'])} campgrounds, version {snapshot}, "
          f"{total_bytes / 1024 / 1024:.1f} MB total gzipped (soft ceiling: {SOFT_CEILING_MB} MB)")

    summary = {
        "snapshot_date": snapshot,
        "output_dir": str(dest_dir),
        "record_count": len(result["marker_records"]),
        "total_records_in": result["total_records"],
        "disambiguated_ids": result["disambiguated"],
        "dropped_exact_duplicates": result["dropped_exact_duplicates"],
        "marker_bytes_gz": len(marker_gz),
        "detail_bytes_gz": len(detail_gz),
        "over_soft_ceiling": over_budget,
    }
    common.write_json(dest_dir / "ios-snapshot-summary.json", summary, indent=2)
    return summary
