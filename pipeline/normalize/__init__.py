"""Normalize stage.

Each adapter module exposes:
    normalize(raw_path: Path, snapshot: str, source_tag: str) -> list[dict]
returning canonical GeoJSON Feature dicts (schema/canonical-campground.schema.json).

This package also orchestrates the stage: it dispatches each registered source
to its adapter and writes data/processed/<id>/<id>.geojson + stats.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType
from typing import Any

from .. import common
from ..registry import Source, load_registry
from . import az, bc, blm, ca, co, id_ as id_adapter, mt, nrhp, or_ as or_adapter, usfs, usfs_poi, wa, wy

ADAPTERS: dict[str, ModuleType] = {
    "usfs": usfs,
    "blm": blm,
    "mt": mt,
    "id": id_adapter,
    "co": co,
    "wy": wy,
    "wa": wa,
    "ca": ca,
    "or": or_adapter,
    "az": az,
    "bc": bc,
    "usfs_poi": usfs_poi,
    "nrhp": nrhp,
}


def _raw_path_for(src: Source, raw_dir: Path) -> Path:
    """Locate the fetched raw file: prefer the fetch manifest, else the offline snapshot."""
    manifest_path = raw_dir / src.id / "fetch-manifest.json"
    if manifest_path.exists():
        with open(manifest_path, encoding="utf-8") as f:
            raw_file = json.load(f).get("raw_file")
        if raw_file and Path(raw_file).exists():
            return Path(raw_file)
    if src.offline_path.exists():
        return src.offline_path
    raise FileNotFoundError(
        f"[{src.id}] no raw file found; run `make fetch` (or fetch this source) first."
    )


def normalize_source(
    src: Source, *, snapshot: str | None = None, raw_dir: Path | None = None,
    processed_dir: Path | None = None,
) -> dict[str, Any]:
    snapshot = snapshot or common.today_str()
    raw_dir = raw_dir or common.RAW_DIR
    processed_dir = processed_dir or common.PROCESSED_DIR

    adapter = ADAPTERS.get(src.adapter)
    if adapter is None:
        raise KeyError(f"[{src.id}] no adapter module named '{src.adapter}'")

    raw_path = _raw_path_for(src, raw_dir)
    features = adapter.normalize(raw_path, snapshot, src.source_tag)

    out_dir = processed_dir / src.id
    out_geojson = out_dir / f"{src.id}.geojson"
    out_stats = out_dir / "stats.json"
    common.write_json(out_geojson, common.feature_collection(features, source=src.source_tag,
                                                             snapshot_date=snapshot))

    stats = _stats(features, src, snapshot, raw_path)
    common.write_json(out_stats, stats, indent=2)
    stats["output"] = str(out_geojson)
    return stats


def _stats(features: list[dict], src: Source, snapshot: str, raw_path: Path) -> dict[str, Any]:
    by_subtype: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    for feat in features:
        p = feat["properties"]
        st = p.get("site_subtype") or p.get("feature_subtype") or p.get("subtype_raw") or "?"
        by_subtype[st] = by_subtype.get(st, 0) + 1
        tier = p.get("reservation_tier") or p.get("category") or "unknown"
        by_tier[tier] = by_tier.get(tier, 0) + 1
    return {
        "source": src.source_tag,
        "source_id": src.id,
        "snapshot_date": snapshot,
        "raw_file": str(raw_path),
        "kept": len(features),
        "by_subtype": dict(sorted(by_subtype.items(), key=lambda kv: -kv[1])),
        "by_reservation_tier": dict(sorted(by_tier.items(), key=lambda kv: -kv[1])),
    }


def run(source_id: str | None = None, *, snapshot: str | None = None,
        registry_path: Path | None = None) -> list[dict[str, Any]]:
    sources = load_registry(registry_path)
    if source_id:
        sources = [s for s in sources if s.id == source_id]
        if not sources:
            raise KeyError(f"no source with id '{source_id}'")
    results = []
    for src in sources:
        stats = normalize_source(src, snapshot=snapshot)
        results.append(stats)
        print(f"  normalize [{src.id}]: {stats['kept']} records -> {stats['output']}")
    return results
