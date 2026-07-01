"""Validate stage.

Runs hard checks (schema, coordinate bounds, duplicate ids) and a soft
diff-vs-previous-snapshot report. Hard failures make the stage exit non-zero so
bad data cannot be silently published; the diff is informational but flags a
large per-source count drop as a warning.

Public API:
    validate_features(features, previous=None, ...) -> report dict
    run(...) -> report dict (CLI turns report['ok']==False into a non-zero exit)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import common
from .registry import load_registry

# Generous continental-US + AK/HI bounding box. Meant to catch gross errors
# (null island, transposed lat/lon, wrong hemisphere), not to geofence tightly.
LAT_MIN, LAT_MAX = 15.0, 72.0
LON_MIN, LON_MAX = -180.0, -60.0

MAX_ERROR_SAMPLE = 25
NEAR_ZERO_DROP_DEFAULT = 0.5  # warn if a source loses >50% of its records


def _bounds_ok(lon: float, lat: float) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


def _index_by_key(features: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    idx: dict[tuple, dict[str, Any]] = {}
    for feat in features:
        p = feat.get("properties", {})
        key = (p.get("source"), common.feature_id(p))
        idx[key] = feat
    return idx


def validate_features(
    features: list[dict[str, Any]],
    *,
    previous: list[dict[str, Any]] | None = None,
    near_zero_drop: float = NEAR_ZERO_DROP_DEFAULT,
) -> dict[str, Any]:
    schema = common.load_schema("canonical-campground.schema.json")

    schema_errors: list[str] = []
    bounds_errors: list[str] = []
    exact_seen: dict[tuple, int] = {}   # (source, ingest_hash) -> count
    id_hashes: dict[tuple, set] = {}    # (source, id) -> set of distinct ingest_hashes
    by_source: dict[str, int] = {}

    for i, feat in enumerate(features):
        props = feat.get("properties", {})
        source = props.get("source", "?")
        by_source[source] = by_source.get(source, 0) + 1

        errs = common.validate_object(props, schema, path=f"feature[{i}]")
        if errs:
            schema_errors.extend(errs)

        coords = (feat.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2 or not all(isinstance(c, (int, float)) for c in coords[:2]):
            bounds_errors.append(f"feature[{i}] ({source}): missing/invalid coordinates {coords!r}")
        else:
            lon, lat = coords[0], coords[1]
            if not _bounds_ok(lon, lat):
                fid = common.feature_id(props)
                bounds_errors.append(f"feature[{i}] ({source}/{fid}): out-of-bounds lon={lon}, lat={lat}")

        fid = common.feature_id(props)
        ihash = props.get("ingest_hash")
        exact_seen[(source, ihash)] = exact_seen.get((source, ihash), 0) + 1
        if fid is not None:
            id_hashes.setdefault((source, fid), set()).add(ihash)

    # Exact duplicates == identical record (same content hash) ingested more than
    # once -> genuine error -> HARD fail.
    exact_dup_errors = [
        f"exact duplicate record (hash {ihash!r}) in source {src!r} ({count}x)"
        for (src, ihash), count in exact_seen.items()
        if count > 1
    ]
    # Id reuse == one source id mapping to multiple distinct records. A known
    # USFS INFRA quirk (campground ids reused for camp units / adjacent dispersed
    # sites). Not blocking here; compact guarantees per-agency `i` uniqueness.
    id_reuse_warnings = [
        f"id {sid!r} in source {src!r} maps to {len(hashes)} distinct records"
        for (src, sid), hashes in id_hashes.items()
        if len(hashes) > 1
    ]

    diff = _diff(features, previous, by_source, near_zero_drop) if previous is not None else None

    hard_count = len(schema_errors) + len(bounds_errors) + len(exact_dup_errors)

    report: dict[str, Any] = {
        "total": len(features),
        "by_source": by_source,
        "hard_failures": {
            "schema_errors": len(schema_errors),
            "bounds_errors": len(bounds_errors),
            "exact_duplicate_ids": len(exact_dup_errors),
        },
        "warnings": {
            "id_reuse": len(id_reuse_warnings),
        },
        "samples": {
            "schema_errors": schema_errors[:MAX_ERROR_SAMPLE],
            "bounds_errors": bounds_errors[:MAX_ERROR_SAMPLE],
            "exact_duplicate_ids": exact_dup_errors[:MAX_ERROR_SAMPLE],
            "id_reuse": id_reuse_warnings[:MAX_ERROR_SAMPLE],
        },
        "diff": diff,
        "ok": hard_count == 0,
    }
    return report


def _diff(
    features: list[dict[str, Any]],
    previous: list[dict[str, Any]],
    by_source: dict[str, int],
    near_zero_drop: float,
) -> dict[str, Any]:
    prev_idx = _index_by_key(previous)
    curr_idx = _index_by_key(features)

    prev_by_source: dict[str, int] = {}
    for feat in previous:
        s = feat.get("properties", {}).get("source", "?")
        prev_by_source[s] = prev_by_source.get(s, 0) + 1

    per_source = {}
    warnings: list[str] = []
    all_sources = set(by_source) | set(prev_by_source)
    for s in sorted(all_sources):
        now = by_source.get(s, 0)
        before = prev_by_source.get(s, 0)
        per_source[s] = {"previous": before, "current": now, "delta": now - before}
        if before > 0 and now < before * (1 - near_zero_drop):
            warnings.append(
                f"source '{s}' dropped {before - now} records "
                f"({before} -> {now}, > {int(near_zero_drop*100)}% loss)"
            )

    new_keys = [k for k in curr_idx if k not in prev_idx]
    gone_keys = [k for k in prev_idx if k not in curr_idx]

    return {
        "per_source": per_source,
        "added": len(new_keys),
        "removed": len(gone_keys),
        "added_sample": [f"{s}/{i}" for (s, i) in new_keys[:MAX_ERROR_SAMPLE]],
        "removed_sample": [f"{s}/{i}" for (s, i) in gone_keys[:MAX_ERROR_SAMPLE]],
        "warnings": warnings,
    }


def _render_markdown(report: dict[str, Any], snapshot: str) -> str:
    lines = [f"# Validation report - {snapshot}", ""]
    lines.append(f"- Total features: **{report['total']}**")
    lines.append(f"- Result: **{'PASS' if report['ok'] else 'FAIL'}**")
    lines.append("")
    lines.append("## Counts by source")
    for s, n in sorted(report["by_source"].items()):
        lines.append(f"- `{s}`: {n}")
    lines.append("")
    lines.append("## Hard checks")
    hf = report["hard_failures"]
    lines.append(f"- Schema errors: {hf['schema_errors']}")
    lines.append(f"- Out-of-bounds coordinates: {hf['bounds_errors']}")
    lines.append(f"- Exact duplicate ids: {hf['exact_duplicate_ids']}")
    lines.append("")
    lines.append("## Warnings (non-blocking)")
    lines.append(f"- Id reuse (same id, different locations - upstream quirk): {report['warnings']['id_reuse']}")
    for cat in ("schema_errors", "bounds_errors", "exact_duplicate_ids", "id_reuse"):
        sample = report["samples"][cat]
        if sample:
            lines.append(f"\n<details><summary>{cat} sample</summary>\n")
            for e in sample:
                lines.append(f"- {e}")
            lines.append("\n</details>")
    if report.get("diff"):
        d = report["diff"]
        lines.append("\n## Diff vs previous snapshot")
        lines.append(f"- Added: {d['added']}, Removed: {d['removed']}")
        lines.append("\n| source | previous | current | delta |")
        lines.append("|---|---|---|---|")
        for s, v in d["per_source"].items():
            lines.append(f"| {s} | {v['previous']} | {v['current']} | {v['delta']:+d} |")
        if d["warnings"]:
            lines.append("\n### Warnings")
            for w in d["warnings"]:
                lines.append(f"- {w}")
    else:
        lines.append("\n## Diff vs previous snapshot\n- No previous snapshot to compare against.")
    return "\n".join(lines) + "\n"


def run(*, snapshot: str | None = None, registry_path: Path | None = None,
        merged_dir: Path | None = None, reports_dir: Path | None = None,
        near_zero_drop: float = NEAR_ZERO_DROP_DEFAULT) -> dict[str, Any]:
    merged_dir = merged_dir or common.MERGED_DIR
    reports_dir = reports_dir or common.REPORTS_DIR

    if snapshot:
        current_dir = merged_dir / snapshot
    else:
        current_dir = common.latest_snapshot_dir(merged_dir)
    if current_dir is None or not (current_dir / "merged.geojson").exists():
        raise FileNotFoundError("no merged snapshot found. Run `make merge` first.")
    snapshot = current_dir.name

    features = common.read_geojson(current_dir / "merged.geojson").get("features", [])

    prev_dir = common.previous_snapshot_dir(merged_dir, current_dir)
    previous = None
    if prev_dir is not None and (prev_dir / "merged.geojson").exists():
        previous = common.read_geojson(prev_dir / "merged.geojson").get("features", [])

    report = validate_features(features, previous=previous, near_zero_drop=near_zero_drop)
    report["snapshot_date"] = snapshot
    report["previous_snapshot"] = prev_dir.name if prev_dir else None

    out_dir = reports_dir / snapshot
    common.write_json(out_dir / "validation.json", report, indent=2)
    (out_dir / "validation.md").write_text(_render_markdown(report, snapshot), encoding="utf-8")

    hf = report["hard_failures"]
    print(f"  validate [{snapshot}]: {'PASS' if report['ok'] else 'FAIL'} "
          f"(schema={hf['schema_errors']}, bounds={hf['bounds_errors']}, "
          f"exact_dupes={hf['exact_duplicate_ids']}, id_reuse_warn={report['warnings']['id_reuse']})")
    if report.get("diff") and report["diff"]["warnings"]:
        for w in report["diff"]["warnings"]:
            print(f"    WARNING: {w}")
    print(f"    report: {out_dir / 'validation.md'}")
    return report
