"""Unit tests for the shared helpers in pipeline.common."""

import unittest

from pipeline import common


class TextIndicatesAvailableTests(unittest.TestCase):
    """Real strings pulled from raw-sources/Recreation_Sites_INFRA.csv."""

    NEGATIVE = [
        "No water is available",
        "No drinking water is available",
        "No",
        "None",
        "NOT AVAILABLE",
        "No, drinking water is not provided",
        "No restroom available",
        "Non-Potable Water",
        "no",
    ]
    POSITIVE = [
        "Yes, drinking water is available from a hand pump",
        "Yes",
        "Potable water available",
        "Handpump drinking water",
        "Vault toilet(s)",
        "Flush toilet(s)",
        "Outhouse",
    ]
    AMBIGUOUS = [
        "",
        None,
        "Water can be treated or filtered from nearby water sources",
        "Seasonal",
    ]

    def test_negative_strings_are_unavailable(self):
        for s in self.NEGATIVE:
            self.assertIs(common.text_indicates_available(s), False, msg=s)

    def test_positive_strings_are_available(self):
        for s in self.POSITIVE:
            self.assertIs(common.text_indicates_available(s), True, msg=s)

    def test_ambiguous_strings_are_unflagged(self):
        for s in self.AMBIGUOUS:
            self.assertIsNone(common.text_indicates_available(s), msg=repr(s))


class ClassifyByKeywordsTests(unittest.TestCase):
    FCFS = ("first come", "fcfs")
    RES = ("reservable", "reservation")

    def test_nrrs_forces_reservable(self):
        self.assertEqual(
            common.classify_by_keywords(["quiet site"], self.FCFS, self.RES, has_nrrs=True),
            common.TIER_RESERVABLE,
        )

    def test_nrrs_with_fcfs_is_mixed(self):
        self.assertEqual(
            common.classify_by_keywords(["first come first served"], self.FCFS, self.RES, has_nrrs=True),
            common.TIER_MIXED,
        )

    def test_no_signal_is_likely_fcfs(self):
        self.assertEqual(
            common.classify_by_keywords(["nice campground"], self.FCFS, self.RES),
            common.TIER_LIKELY_FCFS,
        )

    def test_both_signals_is_mixed(self):
        self.assertEqual(
            common.classify_by_keywords(["first come, or reservation"], self.FCFS, self.RES),
            common.TIER_MIXED,
        )


class SchemaValidatorTests(unittest.TestCase):
    def test_nullable_required_field_is_ok(self):
        schema = {"required": ["f"], "properties": {"f": {"type": ["integer", "null"]}},
                  "additionalProperties": False}
        self.assertEqual(common.validate_object({"f": None}, schema), [])

    def test_missing_required_key_fails(self):
        schema = {"required": ["f"], "properties": {"f": {"type": ["integer", "null"]}},
                  "additionalProperties": False}
        self.assertTrue(common.validate_object({}, schema))

    def test_additional_property_rejected(self):
        schema = {"properties": {"a": {"type": "string"}}, "additionalProperties": False}
        self.assertTrue(common.validate_object({"a": "x", "b": 1}, schema))

    def test_const_and_maxlength(self):
        schema = {"properties": {"w": {"const": 1}, "n": {"type": "string", "maxLength": 3}},
                  "additionalProperties": False}
        self.assertEqual(common.validate_object({"w": 1, "n": "abc"}, schema), [])
        self.assertTrue(common.validate_object({"w": 2}, schema))
        self.assertTrue(common.validate_object({"n": "abcd"}, schema))


class RollupSitesTests(unittest.TestCase):
    def test_parent_with_sites(self):
        rows = [
            {"type": "cg", "park": "A"},
            {"type": "site", "park": "A"},
            {"type": "site", "park": "A"},
        ]
        groups = common.rollup_sites(
            rows,
            group_key=lambda r: r["park"],
            is_parent=lambda r: r["type"] == "cg",
            is_site=lambda r: r["type"] == "site",
        )
        self.assertEqual(len(groups), 1)
        self.assertIsNotNone(groups[0].parent)
        self.assertEqual(groups[0].site_count, 2)

    def test_sites_without_parent(self):
        rows = [{"type": "site", "park": "B"}, {"type": "site", "park": "B"}]
        groups = common.rollup_sites(
            rows,
            group_key=lambda r: r["park"],
            is_parent=lambda r: r["type"] == "cg",
            is_site=lambda r: r["type"] == "site",
        )
        self.assertEqual(len(groups), 1)
        self.assertIsNone(groups[0].parent)
        self.assertEqual(groups[0].site_count, 2)


class GeometryCentroidTests(unittest.TestCase):
    def test_point_returned_as_is(self):
        self.assertEqual(
            common.geometry_centroid({"type": "Point", "coordinates": [-120.5, 45.0]}),
            (-120.5, 45.0),
        )

    def test_polygon_averages_outer_ring_dropping_closing_dup(self):
        ring = [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]
        self.assertEqual(
            common.geometry_centroid({"type": "Polygon", "coordinates": [ring]}),
            (1.0, 1.0),
        )

    def test_multipolygon_uses_largest_ring(self):
        small = [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]
        big = [[10.0, 10.0], [12.0, 10.0], [12.0, 12.0], [10.0, 12.0], [10.0, 10.0]]
        geom = {"type": "MultiPolygon", "coordinates": [[small], [big]]}
        self.assertEqual(common.geometry_centroid(geom), (11.0, 11.0))

    def test_none_and_unknown_return_none(self):
        self.assertIsNone(common.geometry_centroid(None))
        self.assertIsNone(common.geometry_centroid({"type": "LineString", "coordinates": []}))
        self.assertIsNone(common.geometry_centroid({"type": "Point", "coordinates": []}))


if __name__ == "__main__":
    unittest.main()
