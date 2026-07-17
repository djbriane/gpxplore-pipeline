"""Fetch stage: obtain the raw source file (offline snapshot or live ArcGIS).

Offline mode (default) copies a checksummed local snapshot; live mode pages a
confirmed ArcGIS endpoint. Either way a fetch-manifest.json is written next to
the raw file recording origin, timestamp, sha256, and record count.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import common
from ..registry import Source, load_registry
from . import arcgis, manual_file


def fetch_source(src: Source, *, live: bool = False, raw_dir: Path | None = None) -> dict[str, Any]:
    raw_dir = raw_dir or common.RAW_DIR

    if live:
        if src.fetch.get("type") != "arcgis" or not src.live_confirmed:
            raise RuntimeError(
                f"[{src.id}] live fetch requested but no confirmed ArcGIS endpoint "
                f"(fetch.live.confirmed must be true). Use offline mode."
            )
        manifest = _fetch_live(src, raw_dir)
    else:
        manifest = manual_file.fetch_offline(src, raw_dir)

    manifest_path = raw_dir / src.id / "fetch-manifest.json"
    common.write_json(manifest_path, manifest, indent=2)
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def _fetch_live(src: Source, raw_dir: Path) -> dict[str, Any]:
    live = src.live
    url = live["url"]
    page_size = int(live.get("page_size", 1000))
    where = live.get("where", "1=1")
    fc = arcgis.fetch_to_geojson(url, page_size=page_size, where=where)

    dest_dir = raw_dir / src.id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{src.id}.geojson"
    common.write_json(dest, fc)

    return {
        "source": src.source_tag,
        "source_id": src.id,
        "mode": "live",
        "origin": url,
        "raw_file": str(dest),
        "format": "geojson",
        "sha256": common.sha256_file(dest),
        "expected_sha256": None,
        "checksum_ok": True,
        "fetched_at": common.now_iso(),
        "feature_count": len(fc.get("features", [])),
    }


def run(source_id: str | None = None, *, live: bool = False, registry_path: Path | None = None) -> list[dict[str, Any]]:
    sources = load_registry(registry_path)
    if source_id:
        sources = [s for s in sources if s.id == source_id]
        if not sources:
            raise KeyError(f"no source with id '{source_id}'")
    results = []
    for src in sources:
        manifest = fetch_source(src, live=live)
        results.append(manifest)
        status = "ok" if manifest.get("checksum_ok", True) else "CHECKSUM MISMATCH"
        detail = manifest.get("feature_count", manifest.get("byte_size"))
        print(f"  fetch [{src.id}] {manifest['mode']}: {status} ({detail})")
    return results
