"""Behavior tests for the NUC enrichment helper's pure domain transform."""

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER_ROOT = ROOT / "deploy" / "valhalla-unraid"
FIXTURES = ROOT / "prototypes" / "bdr-mapmatch" / "fixtures"
sys.path.insert(0, str(HELPER_ROOT))

from enrichment_helper import normalize_surface  # noqa: E402


def load_fixture(name):
    with (FIXTURES / name).open(encoding="utf-8") as handle:
        return json.load(handle)


class NormalizeSurfaceTests(unittest.TestCase):
    def setUp(self):
        recorded = load_fixture("bdr-surface-excerpt.trace.json")
        self.result = normalize_surface(
            recorded["chunks"],
            load_fixture("bdr-surface-excerpt.sidecar.json"),
            load_fixture("tile-manifest.json"),
            point_count=recorded["point_count"],
        )

    def test_tagged_surface_uses_sidecar_and_taxonomy(self):
        self.assertEqual(
            self.result["segments"][0],
            {
                "startM": 0.0,
                "endM": 250.0,
                "lengthM": 250.0,
                "surfaceClass": "paved",
                "riderSurfaceClass": "paved",
                "roughness": 0.0,
                "isTrack": False,
                "roadClass": "tertiary",
                "valhallaSurface": "paved_smooth",
                "confidence": "tagged",
                "osmTags": {
                    "surface": "asphalt",
                    "smoothness": "good",
                },
            },
        )

    def test_untagged_backcountry_way_is_inferred(self):
        segment = self.result["segments"][1]
        self.assertEqual(segment["surfaceClass"], "gravel")
        self.assertEqual(segment["roughness"], 0.4)
        self.assertEqual(segment["confidence"], "inferred")
        self.assertNotIn("osmTags", segment)

    def test_zero_way_id_is_unknown_with_road_class_fallback(self):
        segment = next(
            item for item in self.result["segments"] if item["confidence"] == "unknown"
        )
        self.assertEqual(segment["surfaceClass"], "unknown")
        self.assertEqual(segment["roughness"], 0.6)
        self.assertTrue(segment["isTrack"])
        self.assertEqual(segment["roadClass"], "service_other")

    def test_chunk_boundary_edge_is_deduplicated_with_continuous_distances(self):
        self.assertEqual(len(self.result["segments"]), 4)
        self.assertEqual(
            [
                (segment["startM"], segment["endM"])
                for segment in self.result["segments"]
            ],
            [
                (0.0, 250.0),
                (250.0, 750.0),
                (750.0, 1050.0),
                (1050.0, 1250.0),
            ],
        )

    def test_point_roughness_aligns_with_input_and_fills_unmatched_from_route(self):
        self.assertEqual(
            self.result["pointRoughness"],
            [0.0, 0.4, 0.4, 0.352, 0.3],
        )

    def test_summary_is_length_weighted_and_provenance_honest(self):
        self.assertEqual(
            self.result["summary"],
            {
                "byClassM": {
                    "paved": 250.0,
                    "compacted": 200.0,
                    "dirt": 0.0,
                    "gravel": 500.0,
                    "rough": 0.0,
                    "unknown": 300.0,
                },
                "dominantClass": "gravel",
                "byRiderClassM": {
                    "paved": 250.0,
                    "hardpack": 200.0,
                    "dirt_road": 0.0,
                    "primitive_road": 0.0,
                    "loose_gravel": 500.0,
                    "rough_trail": 0.0,
                    "unknown": 300.0,
                },
                "dominantRiderSurfaceClass": "loose_gravel",
                "pavedPct": 20.0,
                "unpavedPct": 56.0,
                "trackPct": 40.0,
                "roughnessMean": 0.352,
                "taggedPct": 36.0,
                "inferredPct": 40.0,
                "unknownPct": 24.0,
                "engineVersion": "surface-normalization/2",
                "dataVersion": "us-west-260716",
            },
        )

    def test_full_valhalla_surface_enum_maps_and_impassable_is_excluded(self):
        surfaces = [
            "paved_smooth",
            "paved",
            "paved_rough",
            "compacted",
            "dirt",
            "gravel",
            "path",
            "impassable",
        ]
        edges = [
            {
                "id": index,
                "way_id": 8000 + index,
                "surface": surface,
                "road_class": "unclassified",
                "use": "road",
                "length": 0.001,
            }
            for index, surface in enumerate(surfaces)
        ]
        result = normalize_surface(
            [{"start_index": 0, "response": {"edges": edges, "matched_points": []}}],
            {},
            {"data_version": "test"},
            point_count=0,
        )
        self.assertEqual(
            [
                (segment["surfaceClass"], segment["roughness"])
                for segment in result["segments"]
            ],
            [
                ("paved", 0.0),
                ("paved", 0.0),
                ("paved", 0.0),
                ("compacted", 0.1),
                ("dirt", 0.35),
                ("gravel", 0.4),
                ("rough", 1.0),
            ],
        )

    def test_rider_surface_classes_refine_machine_classes_and_sum_route_meters(self):
        cases = [
            ("paved_smooth", "road", "paved"),
            ("compacted", "track", "hardpack"),
            ("dirt", "road", "dirt_road"),
            ("dirt", "track", "primitive_road"),
            ("gravel", "track", "loose_gravel"),
            ("path", "road", "rough_trail"),
            ("mystery", "road", "unknown"),
            ("impassable", "road", None),
        ]
        edges = [
            {
                "id": index,
                "way_id": 10_000 + index,
                "surface": surface,
                "road_class": "unclassified",
                "use": use,
                "length": 0.001,
            }
            for index, (surface, use, _) in enumerate(cases)
        ]

        result = normalize_surface(
            [{"start_index": 0, "response": {"edges": edges, "matched_points": []}}],
            {},
            {"data_version": "test"},
            point_count=0,
        )

        expected_classes = [expected for _, _, expected in cases if expected is not None]
        self.assertEqual(
            [segment["riderSurfaceClass"] for segment in result["segments"]],
            expected_classes,
        )
        self.assertEqual(
            result["summary"]["byRiderClassM"],
            {
                "paved": 1.0,
                "hardpack": 1.0,
                "dirt_road": 1.0,
                "primitive_road": 1.0,
                "loose_gravel": 1.0,
                "rough_trail": 1.0,
                "unknown": 1.0,
            },
        )
        self.assertEqual(result["summary"]["dominantRiderSurfaceClass"], "paved")
        self.assertEqual(sum(result["summary"]["byRiderClassM"].values()), 7.0)
        self.assertEqual(result["summary"]["engineVersion"], "surface-normalization/2")

    def test_unknown_without_road_class_uses_known_route_mean(self):
        edges = [
            {
                "id": 201,
                "way_id": 9001,
                "surface": "gravel",
                "road_class": "unclassified",
                "use": "road",
                "length": 0.1,
            },
            {
                "id": 202,
                "way_id": 0,
                "surface": "paved_smooth",
                "use": "road",
                "length": 0.1,
            },
        ]
        result = normalize_surface(
            [{"start_index": 0, "response": {"edges": edges, "matched_points": []}}],
            {},
            {"data_version": "test"},
            point_count=0,
        )
        self.assertEqual(result["segments"][1]["surfaceClass"], "unknown")
        self.assertEqual(result["segments"][1]["roughness"], 0.4)
        self.assertEqual(result["summary"]["roughnessMean"], 0.4)


if __name__ == "__main__":
    unittest.main()
