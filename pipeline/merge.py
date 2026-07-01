"""Merge stage.

Concatenates every registered source's latest normalized GeoJSON into one
dated canonical snapshot at data/merged/<snapshot>/merged.geojson, tagged with
snapshot_date and per-source counts. This snapshot is the single input to both
validate and compact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import common
from .registry import load_registry


def run(*, snapshot: str | None = None, registry_path: Path | None = None,
        processed_dir: Path | None = None, merged_dir: Path | None = None) -> dict[str, Any]:
    snapshot = snapshot or common.today_str()
    processed_dir = processed_dir or common.PROCESSED_DIR
    merged_dir = merged_dir or common.MERGED_DIR

    sources = load_registry(registry_path)
    features: list[dict[str, Any]] = []
    by_source: dict[str, int] = {}
    missing: list[str] = []

    for src in sources:
        path = processed_dir / src.id / f"{src.id}.geojson"
        if not path.exists():
            missing.append(src.id)
            continue
        fc = common.read_geojson(path)
        feats = fc.get("features", [])
        features.extend(feats)
        by_source[src.source_tag] = len(feats)

    if missing:
        raise FileNotFoundError(
            f"missing normalized output for: {', '.join(missing)}. Run `make normalize` first."
        )

    out_dir = merged_dir / snapshot
    out_path = out_dir / "merged.geojson"
    payload = common.feature_collection(
        features, snapshot_date=snapshot, sources=by_source, total=len(features)
    )
    common.write_json(out_path, payload)

    summary = {
        "snapshot_date": snapshot,
        "total": len(features),
        "by_source": by_source,
        "output": str(out_path),
    }
    common.write_json(out_dir / "merge-summary.json", summary, indent=2)
    print(f"  merge: {len(features)} features -> {out_path}")
    for tag, n in by_source.items():
        print(f"    {tag}: {n}")
    return summary
