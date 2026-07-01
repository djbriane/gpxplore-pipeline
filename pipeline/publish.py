"""Publish stage.

Review-gated by default. The compact output is first written to a local,
reviewable artifact directory (data/publish/<snapshot>/). Copying into an
external target (e.g. a checked-out gpx-route-planner/apps/planner/public/data/)
only happens with an explicit --confirm; without it, publish performs a dry run
and reports what *would* change.

Publishers are swappable behind a tiny interface so a future Cloudflare-KV
edge publisher can be added without touching compact. This module deliberately
does NOT implement the KV/edge-tiling API (that is a separate, spec'd effort).
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from . import common

PUBLISH_FILES = (
    "usfs-campgrounds.json",
    "blm-campgrounds.json",
    "state-campgrounds.json",
)


class Publisher(ABC):
    """Swappable publish target. Implement publish() for new backends (e.g. KV)."""

    @abstractmethod
    def publish(self, files: dict[str, Path], snapshot: str) -> dict[str, Any]:
        ...


class LocalFilePublisher(Publisher):
    """Writes the reviewable artifact to data/publish/<snapshot>/."""

    def __init__(self, publish_dir: Path | None = None) -> None:
        self.publish_dir = publish_dir or common.PUBLISH_DIR

    def publish(self, files: dict[str, Path], snapshot: str) -> dict[str, Any]:
        out_dir = self.publish_dir / snapshot
        out_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for name, src in files.items():
            dest = out_dir / name
            shutil.copy2(src, dest)
            written.append(str(dest))
        return {"target": str(out_dir), "written": written}


class ExternalDirPublisher(Publisher):
    """Copies into an external directory (the app's public/data), review-gated.

    Without confirm=True this is a dry run: it reports per-file byte-size deltas
    against whatever is currently at the target, and writes nothing.
    """

    def __init__(self, target_dir: Path, *, confirm: bool = False) -> None:
        self.target_dir = target_dir
        self.confirm = confirm

    def publish(self, files: dict[str, Path], snapshot: str) -> dict[str, Any]:
        plan = []
        for name, src in files.items():
            dest = self.target_dir / name
            old = dest.stat().st_size if dest.exists() else None
            new = src.stat().st_size
            plan.append({
                "file": name,
                "target": str(dest),
                "old_bytes": old,
                "new_bytes": new,
                "status": "new" if old is None else ("unchanged" if old == new else "changed"),
            })

        if not self.confirm:
            return {"target": str(self.target_dir), "dry_run": True, "plan": plan}

        self.target_dir.mkdir(parents=True, exist_ok=True)
        for name, src in files.items():
            shutil.copy2(src, self.target_dir / name)
        return {"target": str(self.target_dir), "dry_run": False, "plan": plan}


def _source_files(snapshot: str, compact_dir: Path) -> dict[str, Path]:
    src_dir = compact_dir / snapshot
    files: dict[str, Path] = {}
    for name in PUBLISH_FILES:
        path = src_dir / name
        if not path.exists():
            raise FileNotFoundError(f"compact output missing: {path}. Run `make compact` first.")
        files[name] = path
    return files


def run(*, snapshot: str | None = None, target: str | Path | None = None,
        confirm: bool = False, compact_dir: Path | None = None,
        publish_dir: Path | None = None) -> dict[str, Any]:
    compact_dir = compact_dir or common.COMPACT_DIR

    if snapshot:
        src_dir = compact_dir / snapshot
    else:
        src_dir = common.latest_snapshot_dir(compact_dir)
    if src_dir is None or not src_dir.exists():
        raise FileNotFoundError("no compact output found. Run `make compact` first.")
    snapshot = src_dir.name

    files = _source_files(snapshot, compact_dir)

    # 1. Always produce the local reviewable artifact.
    local = LocalFilePublisher(publish_dir).publish(files, snapshot)
    print(f"  publish [{snapshot}]: local artifact -> {local['target']}")

    result: dict[str, Any] = {"snapshot_date": snapshot, "local": local}

    # 2. Optionally stage to an external target (review-gated).
    if target is not None:
        ext = ExternalDirPublisher(Path(target), confirm=confirm)
        ext_result = ext.publish(files, snapshot)
        result["external"] = ext_result
        if ext_result.get("dry_run"):
            print(f"    DRY RUN (no --confirm): would publish to {ext_result['target']}")
            for item in ext_result["plan"]:
                print(f"      {item['status']:10} {item['file']} "
                      f"({item['old_bytes']} -> {item['new_bytes']} bytes)")
            print("    Re-run with --confirm to write these files.")
        else:
            print(f"    CONFIRMED: published to {ext_result['target']}")

    return result
