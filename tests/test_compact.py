"""Compact-stage tests: the water/restroom semantic fix + a golden-file test."""

import json
import unittest

from pipeline import common, compact

BUNDLE = common.REPO_ROOT / "campgrounds-pipeline-bundle" / "samples"


def _usfs_feature(**props):
    base = {
        "source": "usfs_infra",
        "site_id": "X1",
        "name": "TEST CAMPGROUND",
        "public_name": "Test Campground",
        "site_subtype": "CAMPGROUND",
        "reservation_tier": "likely_fcfs",
        "fee_charged": True,
    }
    base.update(props)
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-112.0, 46.0]}, "properties": base}


class WaterRestroomFlagTests(unittest.TestCase):
    def test_negative_water_text_sets_no_flag_but_keeps_detail(self):
        feat = _usfs_feature(water_availability="No water is available")
        rec = compact.build_usfs(feat["properties"], -112.0, 46.0, _counters())
        self.assertNotIn("w", rec)
        self.assertEqual(rec["w_d"], "No water is available")

    def test_positive_water_text_sets_flag(self):
        feat = _usfs_feature(water_availability="Yes, drinking water is available from a hand pump")
        rec = compact.build_usfs(feat["properties"], -112.0, 46.0, _counters())
        self.assertEqual(rec["w"], 1)
        self.assertIn("w_d", rec)

    def test_negative_restroom_text_sets_no_flag(self):
        feat = _usfs_feature(restroom_availability="No restroom available")
        rec = compact.build_usfs(feat["properties"], -112.0, 46.0, _counters())
        self.assertNotIn("rt", rec)
        self.assertEqual(rec["rt_d"], "No restroom available")

    def test_positive_restroom_text_sets_flag(self):
        feat = _usfs_feature(restroom_availability="Vault toilet(s)")
        rec = compact.build_usfs(feat["properties"], -112.0, 46.0, _counters())
        self.assertEqual(rec["rt"], 1)


class CampUnitDropTests(unittest.TestCase):
    def test_camp_unit_is_dropped(self):
        feat = _usfs_feature(site_subtype="CAMP UNIT")
        counters = _counters()
        rec = compact.build_usfs(feat["properties"], -112.0, 46.0, counters)
        self.assertIsNone(rec)
        self.assertEqual(counters["dropped_camp_units"], 1)


class GoldenFileTests(unittest.TestCase):
    """samples/normalized-sample.geojson -> samples/camp-record-sample.json.

    The bundled camp-record sample is an abbreviated illustration (it omits the
    richer detail fields compact also emits), so we assert each expected record
    is a subset of the actual compacted record for that id.
    """

    def test_compacted_records_match_sample(self):
        normalized = json.loads((BUNDLE / "normalized-sample.geojson").read_text())
        expected = json.loads((BUNDLE / "camp-record-sample.json").read_text())

        result = compact.compact_features(normalized["features"])
        actual_by_id = {}
        for records in result["files"].values():
            for rec in records:
                actual_by_id[rec["i"]] = rec

        self.assertEqual(len(expected), 4)
        for want in expected:
            got = actual_by_id.get(want["i"])
            self.assertIsNotNone(got, f"missing compacted record for id {want['i']}")
            for key, value in want.items():
                self.assertIn(key, got, f"{want['i']}: expected key {key!r}")
                self.assertEqual(got[key], value, f"{want['i']}: key {key!r}")


def _counters():
    return {
        "dropped_camp_units": 0, "water_present": 0, "water_flagged": 0,
        "restroom_present": 0, "restroom_flagged": 0,
    }


if __name__ == "__main__":
    unittest.main()
