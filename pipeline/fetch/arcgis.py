"""Generalized ArcGIS FeatureServer/MapServer offset paginator.

Generalizes campgrounds-pipeline-bundle/scripts/download_co_campgrounds.py into
a reusable, dependency-free client (urllib instead of requests). Any source
whose registry entry declares a confirmed `live` endpoint can be fetched with
this; the output is a GeoJSON FeatureCollection saved under data/raw/<id>/.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

USER_AGENT = "gpxplore-pipeline/1.0 (+https://gpxplore.example)"


def _get_json(url: str, params: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    full = f"{url}?{query}"
    req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def count_only(url: str, where: str = "1=1", timeout: float = 60.0) -> int:
    """Return the server-reported feature count (returnCountOnly=true)."""
    data = _get_json(url, {"where": where, "returnCountOnly": "true", "f": "json"}, timeout)
    return int(data.get("count", 0))


def layer_fields(url: str, timeout: float = 60.0) -> list[str]:
    """Return the layer's field names from its metadata (strips '/query')."""
    return [name for name, _alias in layer_field_map(url, timeout)]


def layer_field_map(url: str, timeout: float = 60.0) -> list[tuple[str, str]]:
    """Return (name, alias) pairs from the layer metadata (strips '/query')."""
    meta_url = url[: -len("/query")] if url.endswith("/query") else url
    data = _get_json(meta_url, {"f": "json"}, timeout)
    return [
        (fld.get("name"), fld.get("alias") or fld.get("name"))
        for fld in data.get("fields", [])
        if fld.get("name")
    ]


def paginate(
    url: str,
    *,
    page_size: int = 1000,
    where: str = "1=1",
    out_format: str = "geojson",
    max_pages: int = 10_000,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """Page through an ArcGIS `/query` endpoint and return all features.

    Mirrors the offset/resultRecordCount loop from download_co_campgrounds.py.
    """
    all_features: list[dict[str, Any]] = []
    offset = 0
    for _ in range(max_pages):
        params = {
            "where": where,
            "outFields": "*",
            "f": out_format,
            "resultRecordCount": page_size,
            "resultOffset": offset,
        }
        data = _get_json(url, params, timeout)
        features = data.get("features", [])
        all_features.extend(features)
        if len(features) < page_size:
            break
        offset += page_size
    return all_features


def fetch_to_geojson(
    url: str,
    *,
    page_size: int = 1000,
    where: str = "1=1",
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Fetch all features and wrap them in a GeoJSON FeatureCollection."""
    features = paginate(url, page_size=page_size, where=where, out_format="geojson", timeout=timeout)
    return {"type": "FeatureCollection", "features": features}
