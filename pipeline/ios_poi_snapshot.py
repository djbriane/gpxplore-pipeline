"""iOS POI snapshot stage.

Builds gzipped marker/detail payloads from compact POIRecord output. POI marker
records intentionally carry only {i, n, t, y, x}; all optional fields remain in
the detail map so the mobile client can keep the map index lean.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from . import common
from .ios_snapshot import IdAssigner, sanitize_url

MARKER_FIELDS = {"i", "n", "t", "y", "x"}
POI_FILES = (("usfs-pois.json", "usfs"),)
SOFT_CEILING_MB = 10


def _gzip_json(data: Any) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return gzip.compress(payload, compresslevel=9, mtime=0)


def process_source(
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
        marker_records.append({
            "i": rid,
            "n": rec.get("n"),
            "t": rec.get("t"),
            "y": rec.get("y"),
            "x": rec.get("x"),
        })
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
        if detail:
            details[rid] = detail


def build_snapshot(source_records: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    assigner = IdAssigner()
    marker_records: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    total_records = 0
    for _fname, src in POI_FILES:
        records = source_records.get(src, [])
        total_records += len(records)
        process_source(records, src, assigner, marker_records, details)
    return {
        "marker_records": marker_records,
        "details": details,
        "total_records": total_records,
        "disambiguated": assigner.disambiguated,
        "dropped_exact_duplicates": assigner.dropped_exact_duplicates,
    }


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

    source_records: dict[str, list[dict[str, Any]]] = {}
    for fname, src in POI_FILES:
        path = src_dir / fname
        if not path.exists():
            raise FileNotFoundError(f"compact output missing: {path}. Run `make compact` first.")
        with open(path, encoding="utf-8") as f:
            records = json.load(f)
        print(f"  {src}: {len(records)} POI records")
        source_records[src] = records

    result = build_snapshot(source_records)

    marker_payload = {"version": snapshot, "records": result["marker_records"]}
    detail_payload = {"version": snapshot, "details": result["details"]}

    dest_dir = out_base / snapshot
    dest_dir.mkdir(parents=True, exist_ok=True)
    marker_gz = _gzip_json(marker_payload)
    detail_gz = _gzip_json(detail_payload)
    (dest_dir / "poi-marker-index.json.gz").write_bytes(marker_gz)
    (dest_dir / "poi-detail.json.gz").write_bytes(detail_gz)

    total_bytes = len(marker_gz) + len(detail_gz)
    over_budget = total_bytes > SOFT_CEILING_MB * 1024 * 1024
    print(f"  -> poi-marker-index.json.gz: {len(marker_gz) / 1024:.0f} KB gzipped")
    print(f"  -> poi-detail.json.gz: {len(detail_gz) / 1024:.0f} KB gzipped")
    print(f"  ids: {len(result['marker_records'])} unique "
          f"({result['disambiguated']} disambiguated, "
          f"{result['dropped_exact_duplicates']} exact duplicates dropped)")

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
    common.write_json(dest_dir / "poi-snapshot-summary.json", summary, indent=2)
    return summary
