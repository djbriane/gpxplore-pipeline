"""Freshness check: is an upstream source likely to have changed since the
snapshot currently pinned in this repo?

This is read-only and does not fetch, normalize, or write pipeline data. For
each registered source it:

- Reads the *offline* snapshot's own record count directly off disk (the
  data actually powering the app until someone deliberately re-fetches).
- For sources with a confirmed live ArcGIS endpoint (see registry.json
  `fetch.live.confirmed`), makes one lightweight metadata call
  (`returnCountOnly` + layer JSON) to get the upstream's current record
  count and, when the service exposes it, its `editingInfo.lastEditDate`.
- Reports the delta so you can decide whether a `make fetch-live` +
  `make pipeline` re-run (and a look at the validate-stage diff report) is
  worth doing.

Record-count drift is a proxy for "something changed", not proof of it —
a source can rewrite existing rows without changing the count. Sources
without a confirmed live endpoint (MT, as of this writing) can't be checked
automatically at all; see SOURCES.md for the portal to check by hand.

Run via `make check-updates` or `python3 -m pipeline.cli check-updates`.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from typing import Any

from .fetch import arcgis
from .registry import Source, load_registry

# A live/offline record-count delta smaller than both of these thresholds is
# treated as routine upstream housekeeping (a handful of sites added or
# retired) rather than a signal that a re-fetch is worth doing.
DRIFT_THRESHOLD_ABS = 25
DRIFT_THRESHOLD_PCT = 1.0

STATUS_UNCHANGED = "unchanged"
STATUS_POSSIBLE_UPDATE = "possible_update"
STATUS_NO_LIVE_CHECK = "no_live_check"
STATUS_CHECK_FAILED = "check_failed"


def _offline_record_count(src: Source) -> int:
    path = src.offline_path
    if src.offline_format == "geojson":
        with open(path, encoding="utf-8") as f:
            return len(json.load(f).get("features", []))
    with open(path, encoding="utf-8-sig", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def _last_edit_date(url: str) -> str | None:
    try:
        meta = arcgis.layer_metadata(url)
    except Exception:
        return None
    editing = meta.get("editingInfo") or {}
    ms = editing.get("dataLastEditDate") or editing.get("lastEditDate")
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def check_source(src: Source) -> dict[str, Any]:
    """Check one registered source. Never raises; failures land in `error`."""
    result: dict[str, Any] = {
        "id": src.id,
        "label": src.label,
        "offline_snapshot_date": src.offline_snapshot_date,
    }

    try:
        result["offline_count"] = _offline_record_count(src)
    except Exception as exc:
        result["status"] = STATUS_CHECK_FAILED
        result["error"] = f"could not read offline snapshot: {exc}"
        return result

    if not src.live_confirmed:
        result["status"] = STATUS_NO_LIVE_CHECK
        result["note"] = src.live.get(
            "note", "no confirmed live endpoint for this source; check its portal manually (see SOURCES.md)"
        )
        return result

    url = src.live["url"]
    try:
        result["live_count"] = arcgis.count_only(url)
    except Exception as exc:
        result["status"] = STATUS_CHECK_FAILED
        result["error"] = f"live endpoint query failed: {exc}"
        return result

    result["last_edit_date"] = _last_edit_date(url)

    offline_count = result["offline_count"]
    delta = result["live_count"] - offline_count
    pct = (abs(delta) / offline_count * 100) if offline_count else 0.0
    result["delta"] = delta
    result["delta_pct"] = round(pct, 2)
    result["status"] = (
        STATUS_POSSIBLE_UPDATE
        if abs(delta) > DRIFT_THRESHOLD_ABS and pct > DRIFT_THRESHOLD_PCT
        else STATUS_UNCHANGED
    )
    return result


def run(source_id: str | None = None, *, registry_path=None) -> list[dict[str, Any]]:
    sources = load_registry(registry_path)
    if source_id:
        sources = [s for s in sources if s.id == source_id]
        if not sources:
            raise KeyError(f"no source with id '{source_id}'")

    results = []
    any_updates = False
    for src in sources:
        result = check_source(src)
        results.append(result)
        _print_result(result)
        if result["status"] == STATUS_POSSIBLE_UPDATE:
            any_updates = True

    print()
    if any_updates:
        print("=> at least one source may have changed upstream - consider `make fetch-live` for it, then re-run the pipeline.")
    else:
        print("=> no source shows a meaningful change; offline snapshots still look current.")
    return results


def _print_result(r: dict[str, Any]) -> None:
    label = f"[{r['id']}] {r['label']}"
    status = r.get("status")

    if status == STATUS_CHECK_FAILED:
        print(f"{label}: CHECK FAILED - {r['error']}")
        return

    if status == STATUS_NO_LIVE_CHECK:
        print(f"{label}: no automated check available ({r['note']})")
        print(f"    offline snapshot: {r['offline_count']} records, pulled {r['offline_snapshot_date']}")
        return

    marker = "POSSIBLE UPDATE" if status == STATUS_POSSIBLE_UPDATE else "unchanged"
    edit_note = f", upstream last edited {r['last_edit_date']}" if r.get("last_edit_date") else ""
    print(
        f"{label}: {marker} - live {r['live_count']} vs offline {r['offline_count']} "
        f"({r['delta']:+d}, {r['delta_pct']}%){edit_note}"
    )
    print(f"    offline snapshot pulled {r['offline_snapshot_date']}")
