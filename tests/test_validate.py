"""Validate-stage tests: schema violation, out-of-range coords, duplicate id, count drop."""

import unittest

from pipeline import common, validate


def _feature(source="usfs_infra", site_id="1", lon=-112.0, lat=46.0, tier="likely_fcfs",
             name="Test", ingest_hash=None, **extra):
    props = {
        "source": source,
        "site_id": site_id,
        "name": name,
        "reservation_tier": tier,
        "snapshot_date": "2026-05-20",
        "ingest_hash": ingest_hash or common.make_hash(source, site_id, lon, lat),
    }
    props.update(extra)
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


class HardCheckTests(unittest.TestCase):
    def test_clean_input_passes(self):
        report = validate.validate_features([_feature(site_id="1"), _feature(site_id="2")])
        self.assertTrue(report["ok"])

    def test_schema_violation_fails(self):
        bad = _feature()
        bad["properties"]["reservation_tier"] = "totally_bogus"  # not in enum
        report = validate.validate_features([bad])
        self.assertFalse(report["ok"])
        self.assertGreater(report["hard_failures"]["schema_errors"], 0)

    def test_out_of_range_coords_fails(self):
        report = validate.validate_features([_feature(lon=0.0, lat=0.0)])
        self.assertFalse(report["ok"])
        self.assertGreater(report["hard_failures"]["bounds_errors"], 0)

    def test_exact_duplicate_fails(self):
        dup = _feature(site_id="9", ingest_hash="samehash")
        dup2 = _feature(site_id="9", ingest_hash="samehash")
        report = validate.validate_features([dup, dup2])
        self.assertFalse(report["ok"])
        self.assertGreater(report["hard_failures"]["exact_duplicate_ids"], 0)

    def test_id_reuse_is_warning_not_failure(self):
        # Same id, different records (different hashes) -> warning, still ok.
        a = _feature(site_id="7", lon=-112.0, ingest_hash="h1")
        b = _feature(site_id="7", lon=-113.0, ingest_hash="h2")
        report = validate.validate_features([a, b])
        self.assertTrue(report["ok"])
        self.assertGreater(report["warnings"]["id_reuse"], 0)


class DiffTests(unittest.TestCase):
    def test_count_drop_warning(self):
        previous = [_feature(site_id=str(i)) for i in range(100)]
        current = [_feature(site_id=str(i)) for i in range(10)]
        report = validate.validate_features(current, previous=previous, near_zero_drop=0.5)
        self.assertIsNotNone(report["diff"])
        self.assertTrue(report["diff"]["warnings"])
        self.assertEqual(report["diff"]["per_source"]["usfs_infra"]["previous"], 100)
        self.assertEqual(report["diff"]["per_source"]["usfs_infra"]["current"], 10)

    def test_no_previous_snapshot(self):
        report = validate.validate_features([_feature()])
        self.assertIsNone(report["diff"])


if __name__ == "__main__":
    unittest.main()
