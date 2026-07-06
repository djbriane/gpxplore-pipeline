"""iOS POI snapshot tests."""

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from pipeline import ios_poi_snapshot as snap


def _rec(**props):
    base = {
        "i": "1",
        "n": "Test Lookout",
        "t": "lookout",
        "y": 46.0,
        "x": -112.0,
        "src": "usfs_infra",
        "sub": "LOOKOUT/CABIN",
    }
    base.update(props)
    return base


class ProcessSourceTests(unittest.TestCase):
    def test_marker_and_detail_split(self):
        records = [_rec(u="https://example.com", desc="Wide views.")]
        assigner = snap.IdAssigner()
        markers, details = [], {}
        snap.process_source(records, "usfs", assigner, markers, details)

        self.assertEqual(markers, [{
            "i": "usfs-1",
            "n": "Test Lookout",
            "t": "lookout",
            "y": 46.0,
            "x": -112.0,
        }])
        self.assertEqual(details["usfs-1"]["src"], "usfs_infra")
        self.assertEqual(details["usfs-1"]["sub"], "LOOKOUT/CABIN")
        self.assertEqual(details["usfs-1"]["u"], "https://example.com")

    def test_malformed_url_is_dropped_from_detail(self):
        assigner = snap.IdAssigner()
        markers, details = [], {}
        snap.process_source([_rec(u="None")], "usfs", assigner, markers, details)
        self.assertNotIn("u", details["usfs-1"])


class RunIntegrationTests(unittest.TestCase):
    def test_run_produces_valid_gzipped_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            compact_dir = Path(tmp) / "compact"
            out_dir = Path(tmp) / "ios-snapshot"
            snapshot_dir = compact_dir / "2026-01-01"
            snapshot_dir.mkdir(parents=True)
            (snapshot_dir / "usfs-pois.json").write_text(json.dumps([_rec(i="LO1")]))

            summary = snap.run(snapshot="2026-01-01", compact_dir=compact_dir, out_dir=out_dir)

            self.assertEqual(summary["record_count"], 1)
            self.assertFalse(summary["over_soft_ceiling"])

            marker_path = out_dir / "2026-01-01" / "poi-marker-index.json.gz"
            detail_path = out_dir / "2026-01-01" / "poi-detail.json.gz"
            marker_payload = json.loads(gzip.decompress(marker_path.read_bytes()))
            detail_payload = json.loads(gzip.decompress(detail_path.read_bytes()))
            self.assertEqual(marker_payload["records"][0]["i"], "usfs-LO1")
            self.assertIn("usfs-LO1", detail_payload["details"])


if __name__ == "__main__":
    unittest.main()
