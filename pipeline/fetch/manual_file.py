"""Offline / manual-file fetch adapter.

Copies a pinned local snapshot into data/raw/<id>/, verifying its SHA-256
against the checksum recorded in the registry (drift detection, per
ARCHITECTURE.md 4.2). This is the default fetch mode for every source so the
whole pipeline can be built and tested with no network access.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .. import common
from ..registry import Source


def fetch_offline(src: Source, raw_dir: Path) -> dict[str, Any]:
    offline = src.offline_path
    if not offline.exists():
        raise FileNotFoundError(f"[{src.id}] offline snapshot not found: {offline}")

    actual_sha = common.sha256_file(offline)
    expected_sha = src.offline_sha256
    checksum_ok = expected_sha is None or actual_sha == expected_sha

    dest_dir = raw_dir / src.id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / offline.name
    shutil.copy2(offline, dest)

    manifest = {
        "source": src.source_tag,
        "source_id": src.id,
        "mode": "offline",
        "origin": str(offline),
        "raw_file": str(dest),
        "format": src.offline_format,
        "sha256": actual_sha,
        "expected_sha256": expected_sha,
        "checksum_ok": checksum_ok,
        "fetched_at": common.now_iso(),
        "byte_size": offline.stat().st_size,
    }
    return manifest
