"""Tests for check_updates.py. All ArcGIS network calls are monkeypatched -
this suite must run fully offline like the rest of the pipeline tests."""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline import check_updates
from pipeline.fetch import arcgis
from pipeline.registry import Source


def _csv_source(*, rows=3, live_confirmed=True) -> Source:
    tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8")
    tmp.write("id,name\n")
    for i in range(rows):
        tmp.write(f"{i},site{i}\n")
    tmp.close()
    fetch = {
        "type": "arcgis",
        "offline_path": tmp.name,
        "offline_format": "csv",
        "offline_snapshot_date": "2026-05-20",
    }
    if live_confirmed:
        fetch["live"] = {"url": "https://example.test/FeatureServer/0/query", "confirmed": True}
    else:
        fetch["live"] = {"confirmed": False, "note": "manual only"}
    return Source(id="test", source_tag="test_tag", adapter="test", label="Test Source", fetch=fetch)


def _geojson_source(*, features=3) -> Source:
    tmp = tempfile.NamedTemporaryFile("w", suffix=".geojson", delete=False, encoding="utf-8")
    json.dump({"type": "FeatureCollection", "features": [{"type": "Feature"} for _ in range(features)]}, tmp)
    tmp.close()
    fetch = {
        "type": "arcgis",
        "offline_path": tmp.name,
        "offline_format": "geojson",
        "offline_snapshot_date": "2026-05-20",
        "live": {"url": "https://example.test/FeatureServer/0/query", "confirmed": True},
    }
    return Source(id="test-geo", source_tag="test_geo_tag", adapter="test", label="Test Geo Source", fetch=fetch)


class OfflineCountTests(unittest.TestCase):
    def test_csv_offline_count(self):
        src = _csv_source(rows=5)
        self.assertEqual(check_updates._offline_record_count(src), 5)

    def test_geojson_offline_count(self):
        src = _geojson_source(features=7)
        self.assertEqual(check_updates._offline_record_count(src), 7)


class CheckSourceTests(unittest.TestCase):
    def test_no_live_endpoint_reports_manual_check(self):
        src = _csv_source(rows=3, live_confirmed=False)
        result = check_updates.check_source(src)
        self.assertEqual(result["status"], check_updates.STATUS_NO_LIVE_CHECK)
        self.assertEqual(result["offline_count"], 3)
        self.assertIn("manual", result["note"])

    def test_unchanged_when_counts_match(self):
        src = _csv_source(rows=100)
        orig_count_only = arcgis.count_only
        orig_meta = arcgis.layer_metadata
        arcgis.count_only = lambda url, **kw: 100
        arcgis.layer_metadata = lambda url, **kw: {}
        try:
            result = check_updates.check_source(src)
        finally:
            arcgis.count_only = orig_count_only
            arcgis.layer_metadata = orig_meta
        self.assertEqual(result["status"], check_updates.STATUS_UNCHANGED)
        self.assertEqual(result["delta"], 0)

    def test_possible_update_when_counts_diverge(self):
        src = _csv_source(rows=100)
        orig_count_only = arcgis.count_only
        orig_meta = arcgis.layer_metadata
        arcgis.count_only = lambda url, **kw: 250
        arcgis.layer_metadata = lambda url, **kw: {
            "editingInfo": {"dataLastEditDate": 1735689600000}
        }
        try:
            result = check_updates.check_source(src)
        finally:
            arcgis.count_only = orig_count_only
            arcgis.layer_metadata = orig_meta
        self.assertEqual(result["status"], check_updates.STATUS_POSSIBLE_UPDATE)
        self.assertEqual(result["delta"], 150)
        self.assertEqual(result["last_edit_date"], "2025-01-01")

    def test_small_drift_is_not_flagged(self):
        # Below both the absolute (25) and percentage (1%) thresholds.
        src = _csv_source(rows=10_000)
        orig_count_only = arcgis.count_only
        arcgis.count_only = lambda url, **kw: 10_010
        try:
            result = check_updates.check_source(src)
        finally:
            arcgis.count_only = orig_count_only
        self.assertEqual(result["status"], check_updates.STATUS_UNCHANGED)

    def test_live_query_failure_is_reported_not_raised(self):
        src = _csv_source(rows=10)

        def _boom(url, **kw):
            raise RuntimeError("network unreachable")

        orig_count_only = arcgis.count_only
        arcgis.count_only = _boom
        try:
            result = check_updates.check_source(src)
        finally:
            arcgis.count_only = orig_count_only
        self.assertEqual(result["status"], check_updates.STATUS_CHECK_FAILED)
        self.assertIn("network unreachable", result["error"])


if __name__ == "__main__":
    unittest.main()
