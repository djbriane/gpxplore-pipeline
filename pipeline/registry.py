"""Load and validate the source registry (registry.json)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import common

DEFAULT_REGISTRY = common.REPO_ROOT / "registry.json"


@dataclass
class Source:
    id: str
    source_tag: str
    adapter: str
    label: str
    fetch: dict[str, Any] = field(default_factory=dict)

    @property
    def offline_path(self) -> Path:
        return common.resolve_path(self.fetch["offline_path"])

    @property
    def offline_format(self) -> str:
        return self.fetch.get("offline_format", "csv")

    @property
    def offline_sha256(self) -> str | None:
        return self.fetch.get("offline_sha256")

    @property
    def offline_snapshot_date(self) -> str | None:
        return self.fetch.get("offline_snapshot_date")

    @property
    def live(self) -> dict[str, Any]:
        return self.fetch.get("live", {})

    @property
    def live_confirmed(self) -> bool:
        return bool(self.live.get("confirmed")) and bool(self.live.get("url"))


def load_registry(path: Path | None = None) -> list[Source]:
    path = path or DEFAULT_REGISTRY
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    sources: list[Source] = []
    seen_ids: set[str] = set()
    seen_tags: set[str] = set()
    for entry in data.get("sources", []):
        for required in ("id", "source_tag", "adapter", "fetch"):
            if required not in entry:
                raise ValueError(f"registry entry missing '{required}': {entry!r}")
        if entry["id"] in seen_ids:
            raise ValueError(f"duplicate source id in registry: {entry['id']}")
        if entry["source_tag"] in seen_tags:
            raise ValueError(f"duplicate source_tag in registry: {entry['source_tag']}")
        seen_ids.add(entry["id"])
        seen_tags.add(entry["source_tag"])
        if "offline_path" not in entry["fetch"]:
            raise ValueError(f"source '{entry['id']}' fetch missing 'offline_path'")
        sources.append(
            Source(
                id=entry["id"],
                source_tag=entry["source_tag"],
                adapter=entry["adapter"],
                label=entry.get("label", entry["id"]),
                fetch=entry["fetch"],
            )
        )
    if not sources:
        raise ValueError("registry contains no sources")
    return sources


def get_source(source_id: str, path: Path | None = None) -> Source:
    for src in load_registry(path):
        if src.id == source_id:
            return src
    raise KeyError(f"no source with id '{source_id}' in registry")
