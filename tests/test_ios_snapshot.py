"""iOS snapshot stage tests: id disambiguation, closure detection, URL
sanitization, and tier assignment - the logic ported from the retired
gpxplore-web build-ios-campground-snapshot.mjs script."""

import gzip
import json
import unittest

from pipeline import ios_snapshot as snap


def _rec(**props):
    base = {"i": "1", "n": "Test Campground", "t": "campground", "y": 46.0, "x": -112.0,
            "r": None, "f": None}
    base.update(props)
    return base


class TierForTests(unittest.TestCase):
    def test_dispersed_is_p3_even_if_reservable(self):
        self.assertEqual(snap.tier_for(_rec(t="dispersed", r="res")), "p3")

    def test_reservable_non_dispersed_is_p2(self):
        self.assertEqual(snap.tier_for(_rec(t="campground", r="res")), "p2")

    def test_mixed_and_fcfs_are_p1_not_p2(self):
        self.assertEqual(snap.tier_for(_rec(t="campground", r="mixed")), "p1")
        self.assertEqual(snap.tier_for(_rec(t="campground", r="fcfs")), "p1")

    def test_no_reservation_info_is_p1(self):
        self.assertEqual(snap.tier_for(_rec(t="campground", r=None)), "p1")


class SanitizeUrlTests(unittest.TestCase):
    def test_valid_https_url_passes_through(self):
        self.assertEqual(snap.sanitize_url("https://www.fs.usda.gov/x"), "https://www.fs.usda.gov/x")

    def test_scheme_less_host_gets_https_prefix(self):
        self.assertEqual(snap.sanitize_url("www.fs.usda.gov/x"), "https://www.fs.usda.gov/x")

    def test_whitespace_split_host_is_repaired(self):
        self.assertEqual(snap.sanitize_url("https://www. fs. usda. gov/x"), "https://www.fs.usda.gov/x")

    def test_free_text_is_rejected(self):
        self.assertIsNone(snap.sanitize_url("None"))

    def test_non_string_is_rejected(self):
        self.assertIsNone(snap.sanitize_url(None))

    def test_non_http_scheme_is_rejected(self):
        self.assertIsNone(snap.sanitize_url("ftp://example.com/x"))


class DetectPermanentClosureTests(unittest.TestCase):
    def test_permanently_closed_text_is_detected(self):
        reason = snap.detect_permanent_closure(_rec(desc="This campground is permanently closed."))
        self.assertEqual(reason, "This campground is permanently closed.")

    def test_subfeature_closure_is_not_a_full_closure(self):
        self.assertIsNone(
            snap.detect_permanent_closure(_rec(desc="The boat ramp is permanently closed."))
        )

    def test_closed_in_name_is_detected(self):
        reason = snap.detect_permanent_closure(_rec(n="Test Campground (Closed)"))
        self.assertEqual(reason, "Marked closed in source data.")

    def test_closed_road_name_is_not_a_closure(self):
        self.assertIsNone(snap.detect_permanent_closure(_rec(n="Closed Rd 11646")))

    def test_open_record_has_no_closure(self):
        self.assertIsNone(snap.detect_permanent_closure(_rec(desc="A lovely campground.")))


class IdAssignerTests(unittest.TestCase):
    def test_unique_ids_pass_through_unchanged(self):
        a = snap.IdAssigner()
        self.assertEqual(a.assign("usfs", "1", 46.0, -112.0), "usfs-1")
        self.assertEqual(a.assign("usfs", "2", 47.0, -113.0), "usfs-2")
        self.assertEqual(a.disambiguated, 0)
        self.assertEqual(a.dropped_exact_duplicates, 0)

    def test_same_id_different_location_is_disambiguated(self):
        a = snap.IdAssigner()
        first = a.assign("usfs", "1", 46.0, -112.0)
        second = a.assign("usfs", "1", 47.0, -113.0)
        self.assertEqual(first, "usfs-1")
        self.assertNotEqual(second, first)
        self.assertTrue(second.startswith("usfs-1-"))
        self.assertEqual(a.disambiguated, 1)

    def test_exact_duplicate_is_dropped(self):
        a = snap.IdAssigner()
        first = a.assign("usfs", "1", 46.0, -112.0)
        second = a.assign("usfs", "1", 46.0, -112.0)
        self.assertEqual(first, "usfs-1")
        self.assertIsNone(second)
        self.assertEqual(a.dropped_exact_duplicates, 1)

    def test_same_id_across_agencies_does_not_collide(self):
        a = snap.IdAssigner()
        usfs_id = a.assign("usfs", "1", 46.0, -112.0)
        blm_id = a.assign("blm", "1", 46.0, -112.0)
        self.assertEqual(usfs_id, "usfs-1")
        self.assertEqual(blm_id, "blm-1")


class ProcessAgencyTests(unittest.TestCase):
    def test_marker_and_detail_split(self):
        records = [_rec(i="1", u="https://example.com", desc="Nice spot")]
        assigner = snap.IdAssigner()
        markers, details = [], {}
        snap.process_agency(records, "usfs", assigner, markers, details)
        self.assertEqual(len(markers), 1)
        marker = markers[0]
        self.assertEqual(marker["i"], "usfs-1")
        self.assertEqual(marker["src"], "usfs")
        self.assertNotIn("desc", marker)  # detail-only field
        self.assertIn("usfs-1", details)
        self.assertEqual(details["usfs-1"]["desc"], "Nice spot")
        self.assertEqual(details["usfs-1"]["u"], "https://example.com")

    def test_malformed_url_is_dropped_from_detail(self):
        records = [_rec(i="1", u="None")]
        assigner = snap.IdAssigner()
        markers, details = [], {}
        snap.process_agency(records, "usfs", assigner, markers, details)
        self.assertNotIn("u", details.get("usfs-1", {}))

    def test_record_without_id_is_skipped(self):
        records = [_rec(i=None)]
        assigner = snap.IdAssigner()
        markers, details = [], {}
        snap.process_agency(records, "usfs", assigner, markers, details)
        self.assertEqual(markers, [])

    def test_closed_record_carries_cl_flag_and_reason(self):
        records = [_rec(i="1", desc="Permanently closed due to flood damage.")]
        assigner = snap.IdAssigner()
        markers, details = [], {}
        snap.process_agency(records, "usfs", assigner, markers, details)
        self.assertTrue(markers[0]["cl"])
        self.assertEqual(details["usfs-1"]["cl_r"], "Permanently closed due to flood damage.")


class RunIntegrationTests(unittest.TestCase):
    """End-to-end: writes real compact-shaped fixtures, runs the stage, and
    decodes the gzipped output to confirm it round-trips correctly."""

    def test_run_produces_valid_gzipped_snapshot(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            compact_dir = Path(tmp) / "compact"
            out_dir = Path(tmp) / "ios-snapshot"
            snapshot_dir = compact_dir / "2026-01-01"
            snapshot_dir.mkdir(parents=True)

            (snapshot_dir / "usfs-campgrounds.json").write_text(
                json.dumps([_rec(i="1", n="Alpha")])
            )
            (snapshot_dir / "blm-campgrounds.json").write_text(
                json.dumps([_rec(i="2", n="Beta", t="dispersed")])
            )
            (snapshot_dir / "state-campgrounds.json").write_text(json.dumps([]))

            summary = snap.run(snapshot="2026-01-01", compact_dir=compact_dir, out_dir=out_dir)

            self.assertEqual(summary["record_count"], 2)
            self.assertFalse(summary["over_soft_ceiling"])

            marker_path = out_dir / "2026-01-01" / "campground-marker-index.json.gz"
            detail_path = out_dir / "2026-01-01" / "campground-detail.json.gz"
            self.assertTrue(marker_path.exists())
            self.assertTrue(detail_path.exists())

            marker_payload = json.loads(gzip.decompress(marker_path.read_bytes()))
            self.assertEqual(marker_payload["version"], "2026-01-01")
            ids = {r["i"] for r in marker_payload["records"]}
            self.assertEqual(ids, {"usfs-1", "blm-2"})

            detail_payload = json.loads(gzip.decompress(detail_path.read_bytes()))
            self.assertIn("version", detail_payload)
            self.assertIn("details", detail_payload)


if __name__ == "__main__":
    unittest.main()
