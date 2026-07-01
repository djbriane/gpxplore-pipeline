"""Shared, dependency-free helpers for every pipeline stage.

Ported out of the three original reference scripts
(campgrounds-pipeline-bundle/scripts/*.py) so all adapters classify, hash,
and reproject identically. Two genuinely new helpers live here too:

  * rollup_sites()            - collapse individual-site rows into one
                                campground-level record (fixes MT/ID over-counting)
  * text_indicates_available()- semantically read free-text amenity fields
                                (fixes the "shown available with 'no water' subtext" bug)

No third-party imports: everything is Python stdlib.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

# ---------------------------------------------------------------------------
# Paths / layout
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
MERGED_DIR = DATA_DIR / "merged"
REPORTS_DIR = DATA_DIR / "reports"
COMPACT_DIR = DATA_DIR / "compact"
PUBLISH_DIR = DATA_DIR / "publish"
SCHEMA_DIR = REPO_ROOT / "schema"


def today_str() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    """Current local timestamp, ISO 8601 (second precision)."""
    return datetime.now().isoformat(timespec="seconds")


def resolve_path(path_str: str) -> Path:
    """Resolve a registry path (relative to the repo root) to an absolute Path."""
    p = Path(path_str)
    return p if p.is_absolute() else (REPO_ROOT / p)


def latest_snapshot_dir(base: Path) -> Path | None:
    """Most recent YYYY-MM-DD subdirectory under base, or None."""
    dirs = _snapshot_dirs(base)
    return dirs[-1] if dirs else None


def previous_snapshot_dir(base: Path, current: Path | None = None) -> Path | None:
    """Second-most-recent snapshot dir (relative to current, if given)."""
    dirs = _snapshot_dirs(base)
    if current is not None:
        dirs = [d for d in dirs if d.name < current.name]
        return dirs[-1] if dirs else None
    return dirs[-2] if len(dirs) >= 2 else None


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _snapshot_dirs(base: Path) -> list[Path]:
    if not base.exists():
        return []
    dirs = [d for d in base.iterdir() if d.is_dir() and _DATE_RE.match(d.name)]
    return sorted(dirs, key=lambda d: d.name)


# ---------------------------------------------------------------------------
# Safe scalar conversions (identical semantics to the original scripts)
# ---------------------------------------------------------------------------

def safe_int(val: Any) -> int | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def safe_float(val: Any) -> float | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def clean_str(val: Any) -> Any:
    """Trim whitespace; empty -> None. Non-strings pass through unchanged."""
    if not isinstance(val, str):
        return val
    s = val.strip()
    return s if s else None


def make_hash(*parts: Any) -> str:
    """Stable 16-char content hash for dedupe / change-detection."""
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Geospatial
# ---------------------------------------------------------------------------

def web_mercator_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """EPSG:3857 -> EPSG:4326. Returns (lon, lat)."""
    lon = (x / 20037508.34) * 180.0
    lat = (y / 20037508.34) * 180.0
    lat = 180.0 / math.pi * (2.0 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2.0)
    return lon, lat


def round5(v: float) -> float:
    """Round a coordinate to 5 decimal places (matches build-campgrounds.mjs)."""
    return round(float(v), 5)


# ---------------------------------------------------------------------------
# Reservation-tier taxonomy (shared across every adapter)
# ---------------------------------------------------------------------------

TIER_DEFINITE_FCFS = "definite_fcfs"
TIER_LIKELY_FCFS = "likely_fcfs"
TIER_RESERVABLE = "reservable"
TIER_MIXED = "mixed"


def classify_by_keywords(
    fields: Iterable[Any],
    fcfs_keywords: Sequence[str],
    reservable_keywords: Sequence[str],
    *,
    has_nrrs: bool = False,
) -> str:
    """Generic FCFS / reservable / mixed scanner.

    Each adapter passes its own keyword lists so the *exact* per-source
    classification behavior of the original scripts is preserved while the
    control flow is shared. `has_nrrs=True` reproduces the USFS rule where a
    Recreation.gov id forces reservable/mixed regardless of free text.
    """
    blob = " ".join(str(f or "") for f in fields).lower()
    has_fcfs = any(k in blob for k in fcfs_keywords)
    has_reservable = any(k in blob for k in reservable_keywords)

    if has_nrrs:
        return TIER_MIXED if has_fcfs else TIER_RESERVABLE
    if has_fcfs and has_reservable:
        return TIER_MIXED
    if has_reservable:
        return TIER_RESERVABLE
    if has_fcfs:
        return TIER_DEFINITE_FCFS
    return TIER_LIKELY_FCFS


# ---------------------------------------------------------------------------
# Amenity free-text classifier (fixes the USFS water/restroom bug)
# ---------------------------------------------------------------------------

# Phrases that mean "not available" wherever they appear in the value.
_UNAVAILABLE_PHRASES = (
    "not available",
    "not provided",
    "not for drinking",
    "non-potable",
    "non potable",
    "no potable",
    "no public water",
    "no water",
    "no drinking",
    "no restroom",
    "no toilet",
)

# Positive nouns/phrases that confirm the amenity is present on site.
_AVAILABLE_NOUNS = (
    "potable water",
    "drinking water",
    "hand pump",
    "handpump",
    "faucet",
    "spigot",
    "well water",
    "water is available",
    "water available",
    "vault toilet",
    "flush toilet",
    "pit toilet",
    "composting toilet",
    "portable toilet",
    "outhouse",
    "toilet",
)

# A leading no/none/not (as a whole word) means "not available".
_NEGATIVE_PREFIX_RE = re.compile(r"^\s*(no|none|not)\b")


def text_indicates_available(text: str | None) -> bool | None:
    """Interpret a free-text amenity field.

    Returns:
        True  - the text affirmatively says the amenity is available
        False - the text says it is NOT available
        None  - empty, or too ambiguous to confidently flag (treated as "not
                a confirmed amenity" by callers, so no presence flag is set)

    This exists because the raw USFS `water_availability` / `restroom_availability`
    fields are free text: ~68% of non-empty water values are negative
    ("No water is available", "No", "NOT AVAILABLE", ...). A naive truthy
    check on the field therefore flags most sites as having water when they do
    not. Negatives are checked before positives so "No restroom available"
    resolves to False even though it contains the word "restroom".
    """
    if not text:
        return None
    t = text.strip().lower()
    if not t:
        return None

    # 1. Negative signals win.
    if _NEGATIVE_PREFIX_RE.match(t):
        return False
    if any(phrase in t for phrase in _UNAVAILABLE_PHRASES):
        return False

    # 2. Affirmative signals.
    if t.startswith("yes"):
        return True
    if any(noun in t for noun in _AVAILABLE_NOUNS):
        return True

    # 3. Ambiguous (e.g. "Water can be treated or filtered from nearby
    #    sources") - do not claim the amenity.
    return None


# ---------------------------------------------------------------------------
# Site rollup (fixes MT/ID campground-vs-campsite over-counting)
# ---------------------------------------------------------------------------

class SiteGroup:
    """A campground and the individual sites rolled up under it."""

    def __init__(self, key: Any) -> None:
        self.key = key
        self.parent: dict[str, Any] | None = None  # explicit campground-level row
        self.sites: list[dict[str, Any]] = []      # individual campsite rows

    @property
    def site_count(self) -> int:
        return len(self.sites)


def rollup_sites(
    rows: Iterable[dict[str, Any]],
    *,
    group_key,
    is_parent,
    is_site,
) -> list[SiteGroup]:
    """Group facility-point rows into campground-level SiteGroups.

    Args:
        rows:      iterable of raw source rows (dicts).
        group_key: fn(row) -> hashable grouping key (e.g. park name). Rows with
                   a falsy key are grouped individually (never merged together).
        is_parent: fn(row) -> bool, True for a campground-level row.
        is_site:   fn(row) -> bool, True for an individual campsite row.

    Rows that are neither parent nor site are ignored (callers filter those
    out beforehand; this is a safety net). A group with a parent row keeps that
    row as the POI and attaches its sites as capacity. A group with only site
    rows is synthesized into a single campground from the cluster.
    """
    groups: dict[Any, SiteGroup] = {}
    orphan_counter = 0
    for row in rows:
        parent = is_parent(row)
        site = is_site(row)
        if not parent and not site:
            continue
        key = group_key(row)
        if not key:
            # No grouping key: keep this row on its own so unrelated rows never
            # merge. Parents still act as parents; lone sites become their own
            # single-site group.
            key = ("__orphan__", orphan_counter)
            orphan_counter += 1
        grp = groups.get(key)
        if grp is None:
            grp = SiteGroup(key)
            groups[key] = grp
        if parent:
            # First explicit parent wins as the POI anchor; extra parents in the
            # same park become their own groups so we don't silently merge two
            # distinct campgrounds.
            if grp.parent is None:
                grp.parent = row
            else:
                orphan_counter += 1
                extra = SiteGroup(("__extra_parent__", orphan_counter))
                extra.parent = row
                groups[extra.key] = extra
        elif site:
            grp.sites.append(row)
    return list(groups.values())


def centroid(coords: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Mean (lon, lat) of a list of (lon, lat) pairs."""
    n = len(coords)
    if n == 0:
        raise ValueError("centroid of empty coordinate list")
    sx = sum(c[0] for c in coords)
    sy = sum(c[1] for c in coords)
    return sx / n, sy / n


# ---------------------------------------------------------------------------
# GeoJSON helpers
# ---------------------------------------------------------------------------

def to_feature(lon: float, lat: float, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": properties,
    }


def feature_collection(features: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    fc: dict[str, Any] = {"type": "FeatureCollection", "features": features}
    fc.update(extra)
    return fc


def read_geojson(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any, *, indent: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)


def feature_id(props: dict[str, Any]) -> str | None:
    """Best-effort stable id for a canonical feature (varies by source)."""
    return props.get("site_id") or props.get("object_id") or props.get("global_id")


# ---------------------------------------------------------------------------
# Hand-rolled JSON-Schema validator (subset sufficient for our two schemas)
# ---------------------------------------------------------------------------

_DATE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_schema(name: str) -> dict[str, Any]:
    with open(SCHEMA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _type_ok(value: Any, types: Any) -> bool:
    if isinstance(types, str):
        types = [types]
    for t in types:
        if t == "null" and value is None:
            return True
        if t == "string" and isinstance(value, str):
            return True
        if t == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if t == "boolean" and isinstance(value, bool):
            return True
        if t == "object" and isinstance(value, dict):
            return True
        if t == "array" and isinstance(value, list):
            return True
    return False


def validate_object(obj: dict[str, Any], schema: dict[str, Any], *, path: str = "") -> list[str]:
    """Return a list of human-readable validation errors (empty == valid).

    Supports: type, required, enum, const, minimum, maximum, maxLength,
    additionalProperties(false), and a light 'date' format check - which is all
    canonical-campground.schema.json and camp-record.schema.json actually use.
    """
    errors: list[str] = []
    props = schema.get("properties", {})

    # JSON-Schema "required" only mandates key presence; a null value is fine
    # when the field's declared type permits null (e.g. CampRecord.f / .r).
    for req in schema.get("required", []):
        if req not in obj:
            errors.append(f"{path}: missing required field '{req}'")

    if schema.get("additionalProperties") is False:
        for key in obj:
            if key not in props:
                errors.append(f"{path}: unexpected field '{key}'")

    for key, value in obj.items():
        spec = props.get(key)
        if spec is None:
            continue
        loc = f"{path}.{key}" if path else key
        errors.extend(_validate_value(value, spec, loc))

    return errors


def _validate_value(value: Any, spec: dict[str, Any], loc: str) -> list[str]:
    errors: list[str] = []

    if "const" in spec:
        if value != spec["const"]:
            errors.append(f"{loc}: expected const {spec['const']!r}, got {value!r}")
        return errors

    if "type" in spec and not _type_ok(value, spec["type"]):
        errors.append(f"{loc}: type {value!r} is not {spec['type']}")
        return errors

    if "enum" in spec and value not in spec["enum"]:
        errors.append(f"{loc}: {value!r} not in enum {spec['enum']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in spec and value < spec["minimum"]:
            errors.append(f"{loc}: {value} < minimum {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            errors.append(f"{loc}: {value} > maximum {spec['maximum']}")

    if isinstance(value, str):
        if "maxLength" in spec and len(value) > spec["maxLength"]:
            errors.append(f"{loc}: length {len(value)} > maxLength {spec['maxLength']}")
        if spec.get("format") == "date" and value and not _DATE_ISO_RE.match(value):
            errors.append(f"{loc}: {value!r} is not an ISO date")

    return errors
