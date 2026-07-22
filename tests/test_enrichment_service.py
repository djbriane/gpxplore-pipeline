"""Public-boundary tests for the NUC-side enrichment service."""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy" / "valhalla-unraid"))

from enrichment_helper.service import EnrichmentService  # noqa: E402


class RecordedTraceClient:
    def __init__(self):
        self.calls = []
        self.responses = [
            {
                "edges": [
                    {
                        "id": 101,
                        "way_id": 7001,
                        "surface": "paved_smooth",
                        "road_class": "tertiary",
                        "use": "road",
                        "length": 0.25,
                    },
                    {
                        "id": 102,
                        "way_id": 7002,
                        "surface": "gravel",
                        "road_class": "unclassified",
                        "use": "road",
                        "length": 0.5,
                    },
                ],
                "matched_points": [
                    {
                        "type": "matched",
                        "distance_from_trace_point": 1.2,
                        "edge_index": 0,
                    },
                    {
                        "type": "matched",
                        "distance_from_trace_point": 2.5,
                        "edge_index": 1,
                    },
                    {
                        "type": "matched",
                        "distance_from_trace_point": 3.1,
                        "edge_index": 1,
                    },
                ],
            },
            {
                "edges": [
                    {
                        "id": 102,
                        "way_id": 7002,
                        "surface": "gravel",
                        "road_class": "unclassified",
                        "use": "road",
                        "length": 0.5,
                    },
                    {
                        "id": 104,
                        "way_id": 7004,
                        "surface": "compacted",
                        "road_class": "service_other",
                        "use": "track",
                        "length": 0.2,
                    },
                ],
                "matched_points": [
                    {
                        "type": "matched",
                        "distance_from_trace_point": 3.1,
                        "edge_index": 0,
                    },
                    {
                        "type": "matched",
                        "distance_from_trace_point": 4.4,
                        "edge_index": 1,
                    },
                ],
            },
        ]

    def trace_attributes(self, points, profile):
        self.calls.append((points, profile))
        return self.responses[len(self.calls) - 1]


class RecordedManifestStore:
    def read(self):
        return {
            "data_version": "us-west-260716",
            "valhalla_version": "3.8.2",
            "extract_date": "260716",
            "region": "us-west",
        }


class RecordedSidecarStore:
    def __init__(self):
        self.calls = []

    def rows_for(self, way_ids, expected_data_version):
        self.calls.append((way_ids, expected_data_version))
        return {
            7001: {"surface": "asphalt", "smoothness": "good"},
            7004: {"tracktype": "grade1"},
        }


class EnrichmentServiceTests(unittest.TestCase):
    def test_enriches_a_multi_chunk_route_through_the_public_service(self):
        trace_client = RecordedTraceClient()
        sidecar_store = RecordedSidecarStore()
        service = EnrichmentService(
            trace_client,
            sidecar_store,
            RecordedManifestStore(),
        )

        status_code, result = service.enrich(
            {
                "points": [
                    {"lat": 0, "lon": 0},
                    {"lat": 0, "lon": 0.9},
                    {"lat": 0, "lon": 1.62},
                    {"lat": 0, "lon": 2.52},
                ],
                "profile": "adv_balanced",
                "geometry": "none",
            }
        )

        self.assertEqual(status_code, 200)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["profile"], "adv_balanced")
        self.assertEqual(len(trace_client.calls), 2)
        self.assertEqual(
            [(chunk["start_index"], chunk["end_index"]) for chunk in result["chunks"]],
            [(0, 2), (2, 3)],
        )
        self.assertEqual(len(result["segments"]), 3)
        self.assertEqual(
            [segment["surfaceClass"] for segment in result["segments"]],
            ["paved", "gravel", "compacted"],
        )
        self.assertEqual(
            [segment["riderSurfaceClass"] for segment in result["segments"]],
            ["paved", "loose_gravel", "hardpack"],
        )
        self.assertEqual(
            result["summary"]["byRiderClassM"],
            {
                "paved": 250.0,
                "hardpack": 200.0,
                "dirt_road": 0.0,
                "primitive_road": 0.0,
                "loose_gravel": 500.0,
                "rough_trail": 0.0,
                "unknown": 0.0,
            },
        )
        self.assertEqual(
            result["summary"]["dominantRiderSurfaceClass"], "loose_gravel"
        )
        self.assertEqual(len(result["pointRoughness"]), 4)
        self.assertEqual(result["summary"]["dataVersion"], "us-west-260716")
        self.assertEqual(result["match"]["match_rate"], 1.0)
        self.assertEqual(sidecar_store.calls[0][1], "us-west-260716")

    def test_polyline6_is_an_opt_in_echo_of_the_authoritative_input(self):
        trace_client = RecordedTraceClient()
        service = EnrichmentService(
            trace_client,
            RecordedSidecarStore(),
            RecordedManifestStore(),
        )

        _, result = service.enrich(
            {
                "points": [
                    {"lat": 38.5, "lon": -120.2},
                    {"lat": 38.6, "lon": -120.3},
                ],
                "geometry": "polyline6",
            }
        )

        self.assertEqual(result["geometry"]["encoding"], "polyline6")
        self.assertIsInstance(result["geometry"]["shape"], str)
        self.assertTrue(result["geometry"]["shape"])


if __name__ == "__main__":
    unittest.main()
