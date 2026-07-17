"""Adapter tests with small hand-built fixtures -> expected canonical output."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from pipeline import common
from pipeline.normalize import blm as blm_adapter
from pipeline.normalize import ca as ca_adapter
from pipeline.normalize import id_ as id_adapter
from pipeline.normalize import mt as mt_adapter
from pipeline.normalize import usfs as usfs_adapter
from pipeline.normalize import usfs_poi as usfs_poi_adapter
from pipeline.normalize import nrhp as nrhp_adapter
from pipeline.normalize import az as az_adapter
from pipeline.normalize import bc as bc_adapter
from pipeline.normalize import or_ as or_adapter
from pipeline.normalize import wa as wa_adapter
from pipeline.normalize import wy as wy_adapter


def _polygon(ring, props) -> dict:
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": props}


def _write_csv(rows, fieldnames) -> Path:
    tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8")
    writer = csv.DictWriter(tmp, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    tmp.close()
    return Path(tmp.name)


def _point(lon, lat, props) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


def _write_geojson(features) -> Path:
    tmp = tempfile.NamedTemporaryFile("w", suffix=".geojson", delete=False, encoding="utf-8")
    json.dump({"type": "FeatureCollection", "features": features}, tmp)
    tmp.close()
    return Path(tmp.name)


class UsfsAdapterTests(unittest.TestCase):
    FIELDS = [
        "latitude", "longitude", "site_id", "globalid", "site_name", "public_site_name",
        "site_subtype", "development_scale", "nrrs_id", "total_capacity", "fee_charged",
        "water_availability", "restroom_availability", "closest_towns", "recarea_description",
    ]

    def test_campground_row_normalized_and_wrong_subtype_dropped(self):
        rows = [
            {
                "latitude": "47.07833", "longitude": "-112.61944", "site_id": "CG1",
                "globalid": "{G1}", "site_name": "COPPER CREEK CAMPGROUND",
                "public_site_name": "Copper Creek Campground", "site_subtype": "Campground",
                "development_scale": "3", "nrrs_id": "", "total_capacity": "100",
                "fee_charged": "Y", "water_availability": "No water is available",
                "restroom_availability": "Vault toilet(s)", "closest_towns": "Lincoln, MT",
                "recarea_description": "A nice campground.",
            },
            {  # wrong subtype -> filtered out
                "latitude": "47.0", "longitude": "-112.0", "site_id": "TH1", "globalid": "",
                "site_name": "SOME TRAILHEAD", "public_site_name": "", "site_subtype": "TRAILHEAD",
                "development_scale": "1", "nrrs_id": "", "total_capacity": "",
                "fee_charged": "N", "water_availability": "", "restroom_availability": "",
                "closest_towns": "", "recarea_description": "",
            },
        ]
        path = _write_csv(rows, self.FIELDS)
        try:
            feats = usfs_adapter.normalize(path, "2026-05-20", "usfs_infra")
        finally:
            path.unlink()

        self.assertEqual(len(feats), 1)
        p = feats[0]["properties"]
        self.assertEqual(p["source"], "usfs_infra")
        self.assertEqual(p["site_id"], "CG1")
        self.assertEqual(p["name"], "COPPER CREEK CAMPGROUND")
        self.assertEqual(p["site_subtype"], "CAMPGROUND")
        self.assertEqual(p["development_label"], "moderate")
        self.assertEqual(p["total_capacity"], 100)
        self.assertTrue(p["fee_charged"])
        self.assertEqual(p["reservation_tier"], common.TIER_LIKELY_FCFS)
        # Raw amenity text is carried through untouched (the semantic read is
        # deferred to the compact stage).
        self.assertEqual(p["water_availability"], "No water is available")
        self.assertEqual(feats[0]["geometry"]["coordinates"], [-112.61944, 47.07833])


class UsfsPoiAdapterTests(unittest.TestCase):
    FIELDS = [
        "latitude", "longitude", "site_id", "objectid", "site_name", "public_site_name",
        "site_subtype", "address_state", "states_spanned", "maximum_elevation",
        "minimum_elevation", "recarea_description", "operated_by", "usda_portal_url",
        "rec1stop_url", "official_designation", "alias_name", "alternative_name",
        "recarea_name",
    ]

    def test_lookout_and_historic_rows_normalize_guard_station_dropped(self):
        rows = [
            {
                "latitude": "48.7000", "longitude": "-114.8500", "site_id": "LO1",
                "objectid": "1", "site_name": "STAHL PEAK LOOKOUT",
                "public_site_name": "", "site_subtype": "Lookout/Cabin", "address_state": "MT",
                "states_spanned": "", "maximum_elevation": "7300", "minimum_elevation": "",
                "recarea_description": "A fire lookout with wide views.", "operated_by": "USFS",
                "usda_portal_url": "https://www.fs.usda.gov/recarea/test", "rec1stop_url": "",
                "official_designation": "", "alias_name": "", "alternative_name": "",
                "recarea_name": "",
            },
            {
                "latitude": "31.8500", "longitude": "-109.3500", "site_id": "H1",
                "objectid": "2", "site_name": "CAMP RUCKER HISTORIC SITE",
                "public_site_name": "Camp Rucker Historic Site", "site_subtype": "Interpretive Site",
                "address_state": "AZ", "states_spanned": "", "maximum_elevation": "",
                "minimum_elevation": "", "recarea_description": "Historic cavalry camp.",
                "operated_by": "", "usda_portal_url": "", "rec1stop_url": "",
                "official_designation": "", "alias_name": "", "alternative_name": "",
                "recarea_name": "",
            },
            {
                "latitude": "47.0", "longitude": "-113.0", "site_id": "CABIN1",
                "objectid": "3", "site_name": "SOME GUARD STATION",
                "public_site_name": "", "site_subtype": "Lookout/Cabin", "address_state": "MT",
                "states_spanned": "", "maximum_elevation": "", "minimum_elevation": "",
                "recarea_description": "SOME GUARD STATION (Lookout/Cabin)",
                "operated_by": "", "usda_portal_url": "",
                "rec1stop_url": "", "official_designation": "", "alias_name": "",
                "alternative_name": "", "recarea_name": "",
            },
        ]
        path = _write_csv(rows, self.FIELDS)
        try:
            feats = usfs_poi_adapter.normalize(path, "2026-05-20", "usfs_infra_poi")
        finally:
            path.unlink()

        self.assertEqual(len(feats), 2)
        by_id = {f["properties"]["site_id"]: f["properties"] for f in feats}
        self.assertEqual(by_id["LO1"]["category"], "lookout")
        self.assertEqual(by_id["LO1"]["state"], "MT")
        self.assertEqual(by_id["LO1"]["subtype_raw"], "LOOKOUT/CABIN")
        self.assertEqual(by_id["H1"]["category"], "historic")
        self.assertEqual(by_id["H1"]["state"], "AZ")

    def test_observation_site_and_thin_interpretive_overlook_dropped(self):
        rows = [
            {
                "latitude": "44.2898", "longitude": "-121.7632", "site_id": "OV1",
                "objectid": "10", "site_name": "WINDY POINT OVERLOOK",
                "public_site_name": "", "site_subtype": "Observation Site", "address_state": "OR",
                "states_spanned": "", "maximum_elevation": "", "minimum_elevation": "",
                "recarea_description": "Interpretive site overlooking lava flow on the McKenzie Pass Scenic Byway.",
                "operated_by": "", "usda_portal_url": "", "rec1stop_url": "",
                "official_designation": "", "alias_name": "", "alternative_name": "",
                "recarea_name": "",
            },
            {
                "latitude": "42.5000", "longitude": "-122.0000", "site_id": "IO1",
                "objectid": "11", "site_name": "LAKE GRANBY OVERLOOK",
                "public_site_name": "", "site_subtype": "Interpretive Site", "address_state": "CO",
                "states_spanned": "", "maximum_elevation": "", "minimum_elevation": "",
                "recarea_description": "LAKE GRANBY OVERLOOK (Interpretive Site)",
                "operated_by": "", "usda_portal_url": "", "rec1stop_url": "",
                "official_designation": "", "alias_name": "", "alternative_name": "",
                "recarea_name": "",
            },
            {
                "latitude": "31.8500", "longitude": "-109.3500", "site_id": "H2",
                "objectid": "12", "site_name": "CAMP RUCKER HISTORIC SITE",
                "public_site_name": "Camp Rucker Historic Site", "site_subtype": "Interpretive Site",
                "address_state": "AZ", "states_spanned": "", "maximum_elevation": "",
                "minimum_elevation": "", "recarea_description": "Historic cavalry camp.",
                "operated_by": "", "usda_portal_url": "", "rec1stop_url": "",
                "official_designation": "", "alias_name": "", "alternative_name": "",
                "recarea_name": "",
            },
        ]
        path = _write_csv(rows, self.FIELDS)
        try:
            feats = usfs_poi_adapter.normalize(path, "2026-05-20", "usfs_infra_poi")
        finally:
            path.unlink()

        self.assertEqual(len(feats), 1)
        self.assertEqual(feats[0]["properties"]["site_id"], "H2")

    def test_thin_interpretive_without_url_dropped(self):
        rows = [
            {
                "latitude": "44.0", "longitude": "-114.0", "site_id": "K1",
                "objectid": "20", "site_name": "SOME KIOSK",
                "public_site_name": "", "site_subtype": "Interpretive Site", "address_state": "ID",
                "states_spanned": "", "maximum_elevation": "", "minimum_elevation": "",
                "recarea_description": "Short blurb.",
                "operated_by": "", "usda_portal_url": "", "rec1stop_url": "",
                "official_designation": "", "alias_name": "", "alternative_name": "",
                "recarea_name": "",
            },
            {
                "latitude": "44.1", "longitude": "-114.1", "site_id": "K2",
                "objectid": "21", "site_name": "INYO CRATERS",
                "public_site_name": "", "site_subtype": "Interpretive Site", "address_state": "CA",
                "states_spanned": "", "maximum_elevation": "", "minimum_elevation": "",
                "recarea_description": "A" * 120,
                "operated_by": "", "usda_portal_url": "", "rec1stop_url": "",
                "official_designation": "", "alias_name": "", "alternative_name": "",
                "recarea_name": "",
            },
        ]
        path = _write_csv(rows, self.FIELDS)
        try:
            feats = usfs_poi_adapter.normalize(path, "2026-05-20", "usfs_infra_poi")
        finally:
            path.unlink()
        self.assertEqual(len(feats), 1)
        self.assertEqual(feats[0]["properties"]["site_id"], "K2")


class NrhpAdapterTests(unittest.TestCase):
    def test_maps_listed_site_to_historic_poi(self):
        fc = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-112.0, 46.0]},
                "properties": {
                    "OBJECTID": 123,
                    "RESNAME": "Test Historic Bridge",
                    "NRIS_Refnum": "66000424",
                    "CertDate": "10/15/66",
                    "ResType": "structure",
                    "State": "MONTANA",
                    "County": "Gallatin",
                },
            }],
        }
        path = Path(__file__).parent / "_tmp_nrhp.geojson"
        path.write_text(json.dumps(fc), encoding="utf-8")
        try:
            feats = nrhp_adapter.normalize(path, "2026-07-06", "nrhp")
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(len(feats), 1)
        props = feats[0]["properties"]
        self.assertEqual(props["category"], "historic")
        self.assertEqual(props["state"], "MT")
        self.assertEqual(props["ref_number"], "66000424")
        self.assertEqual(props["significant_year"], "1966")
        self.assertEqual(props["subtype_raw"], "STRUCTURE")

    def test_out_of_footprint_state_dropped(self):
        fc = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-90.0, 30.0]},
                "properties": {
                    "OBJECTID": 1,
                    "RESNAME": "Louisiana Site",
                    "NRIS_Refnum": "99009999",
                    "CertDate": "01/01/90",
                    "ResType": "building",
                    "State": "LOUISIANA",
                    "County": "Orleans",
                },
            }],
        }
        path = Path(__file__).parent / "_tmp_nrhp2.geojson"
        path.write_text(json.dumps(fc), encoding="utf-8")
        try:
            feats = nrhp_adapter.normalize(path, "2026-07-06", "nrhp")
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(feats, [])


class MtRollupTests(unittest.TestCase):
    FIELDS = ["Facility Type", "Facility Name", "Park Name", "x", "y", "SITEID", "OBJECTID", "GlobalID", "COMMENTS"]

    def test_campsites_roll_into_campground_and_infra_dropped(self):
        # Mercator-ish coords near western MT; exact values don't matter here.
        base_x, base_y = -12700000.0, 6000000.0
        rows = [
            {"Facility Type": "Campground (Utilities Available)", "Facility Name": "",
             "Park Name": "Logan", "x": base_x, "y": base_y, "SITEID": "100", "OBJECTID": "1",
             "GlobalID": "{A}", "COMMENTS": ""},
            {"Facility Type": "Campsite", "Facility Name": "A1", "Park Name": "Logan",
             "x": base_x + 10, "y": base_y + 10, "SITEID": "100", "OBJECTID": "2", "GlobalID": "{B}", "COMMENTS": ""},
            {"Facility Type": "Campsite", "Facility Name": "A2", "Park Name": "Logan",
             "x": base_x + 20, "y": base_y + 20, "SITEID": "100", "OBJECTID": "3", "GlobalID": "{C}", "COMMENTS": ""},
            {"Facility Type": "Campsite", "Facility Name": "A3", "Park Name": "Logan",
             "x": base_x + 30, "y": base_y + 30, "SITEID": "100", "OBJECTID": "4", "GlobalID": "{D}", "COMMENTS": ""},
            {"Facility Type": "Restroom (Vault)", "Facility Name": "Camp Restroom", "Park Name": "Logan",
             "x": base_x, "y": base_y, "SITEID": "100", "OBJECTID": "5", "GlobalID": "{E}", "COMMENTS": ""},
        ]
        path = _write_csv(rows, self.FIELDS)
        try:
            feats = mt_adapter.normalize(path, "2026-05-20", "mt_state_parks")
        finally:
            path.unlink()

        self.assertEqual(len(feats), 1, "3 campsites + 1 campground + 1 restroom -> one campground POI")
        p = feats[0]["properties"]
        self.assertEqual(p["total_capacity"], 3)
        self.assertEqual(p["name"], "Logan")
        self.assertEqual(p["state"], "MT")
        self.assertEqual(p["site_id"], "1")  # OBJECTID of the campground row

    def test_synthesized_campground_when_no_parent(self):
        base_x, base_y = -12700000.0, 6000000.0
        rows = [
            {"Facility Type": "Campsite", "Facility Name": "1", "Park Name": "West Shore",
             "x": base_x, "y": base_y, "SITEID": "200", "OBJECTID": "10", "GlobalID": "{A}", "COMMENTS": ""},
            {"Facility Type": "Campsite", "Facility Name": "2", "Park Name": "West Shore",
             "x": base_x + 100, "y": base_y + 100, "SITEID": "200", "OBJECTID": "11", "GlobalID": "{B}", "COMMENTS": ""},
        ]
        path = _write_csv(rows, self.FIELDS)
        try:
            feats = mt_adapter.normalize(path, "2026-05-20", "mt_state_parks")
        finally:
            path.unlink()
        self.assertEqual(len(feats), 1)
        self.assertEqual(feats[0]["properties"]["total_capacity"], 2)
        self.assertEqual(feats[0]["properties"]["name"], "West Shore")


class BlmRollupTests(unittest.TestCase):
    FIELDS = ["OBJECTID", "Feature Type", "Feature Subtype", "Feature Name",
              "Administrative Unit Code", "Administrative State", "DESCRIPTION",
              "WEB_LINK", "UNIT_NAME", "GlobalID", "Latitude", "Longitude"]

    def _row(self, object_id, subtype, name, lat, lon):
        return {"OBJECTID": object_id, "Feature Type": "Campsite", "Feature Subtype": subtype,
                "Feature Name": name, "Administrative Unit Code": "MTB01000",
                "Administrative State": "MT", "DESCRIPTION": "", "WEB_LINK": "",
                "UNIT_NAME": "", "GlobalID": f"{{{object_id}}}", "Latitude": str(lat), "Longitude": str(lon)}

    def test_sites_near_explicit_campground_roll_up_and_disappear(self):
        # Mirrors the real Thibodeau Campground / "Site 1".."Site N" case:
        # individual developed-campsite rows ~100-200m from an explicit
        # "Campground" row should fold into it, not show as their own markers.
        rows = [self._row("1", "Campground", "Thibodeau Campground", 46.9504, -113.6073)]
        for i, (dlat, dlon) in enumerate([(0.0005, 0.0002), (-0.0003, 0.0004), (0.0002, -0.0003)], start=2):
            rows.append(self._row(
                str(i), "Campsite - Developed - Non Reservable - Fee", f"Site {i}",
                46.9504 + dlat, -113.6073 + dlon,
            ))
        path = _write_csv(rows, self.FIELDS)
        try:
            feats = blm_adapter.normalize(path, "2026-05-20", "blm_recreation")
        finally:
            path.unlink()

        self.assertEqual(len(feats), 1, "1 campground + 3 nearby sites -> one POI")
        p = feats[0]["properties"]
        self.assertEqual(p["name"], "Thibodeau Campground")
        self.assertEqual(p["object_id"], "1")
        self.assertEqual(p["total_capacity"], 3)

    def test_orphan_site_cluster_synthesizes_one_campground(self):
        # No "Campground" row at all - mirrors "Patos Island Campsite 1..N":
        # numbered sites with nothing but proximity linking them.
        rows = [
            self._row("10", "Campsite - Developed - Non Reservable - Fee",
                       "Patos Island Campsite 1", 48.7860, -122.9670),
            self._row("11", "Campsite - Developed - Non Reservable - Fee",
                       "Patos Island Campsite 2", 48.7861, -122.9671),
            self._row("12", "Campsite - Developed - Non Reservable - Fee",
                       "Patos Island Campsite 3", 48.7862, -122.9669),
        ]
        path = _write_csv(rows, self.FIELDS)
        try:
            feats = blm_adapter.normalize(path, "2026-05-20", "blm_recreation")
        finally:
            path.unlink()

        self.assertEqual(len(feats), 1)
        p = feats[0]["properties"]
        self.assertEqual(p["name"], "Patos Island")
        self.assertEqual(p["total_capacity"], 3)
        self.assertEqual(p["reservation_tier"], common.TIER_DEFINITE_FCFS)

    def test_true_standalone_site_is_kept_unchanged(self):
        rows = [self._row("20", "Campsite - Developed - Reservable - Fee", "Lonesome Site", 40.0, -105.0)]
        path = _write_csv(rows, self.FIELDS)
        try:
            feats = blm_adapter.normalize(path, "2026-05-20", "blm_recreation")
        finally:
            path.unlink()

        self.assertEqual(len(feats), 1)
        p = feats[0]["properties"]
        self.assertEqual(p["name"], "Lonesome Site")
        self.assertIsNone(p["total_capacity"])

    def test_distant_site_does_not_attach_to_unrelated_campground(self):
        rows = [
            self._row("30", "Campground", "Far Campground", 40.0, -105.0),
            # ~5.5km away - beyond ANCHOR_ATTACH_KM, and no sibling to cluster with.
            self._row("31", "Campsite - Developed - Non Reservable - Fee", "Unrelated Site", 40.05, -105.0),
        ]
        path = _write_csv(rows, self.FIELDS)
        try:
            feats = blm_adapter.normalize(path, "2026-05-20", "blm_recreation")
        finally:
            path.unlink()

        names = {f["properties"]["name"] for f in feats}
        self.assertEqual(names, {"Far Campground", "Unrelated Site"})


class IdFilterTests(unittest.TestCase):
    FIELDS = ["X", "Y", "name", "description", "objectid", "pic_url"]

    def test_trail_and_banner_excluded_park_kept(self):
        base_x, base_y = -13000000.0, 6200000.0
        rows = [
            {"X": base_x, "Y": base_y, "name": "Farragut State Park", "description": "camping",
             "objectid": "1", "pic_url": ""},
            {"X": base_x, "Y": base_y, "name": "Ashton-Tetonia Trail", "description": "",
             "objectid": "2", "pic_url": ""},
            {"X": base_x, "Y": base_y, "name": "Welcome to Idaho State Parks!", "description": "",
             "objectid": "3", "pic_url": ""},
            {"X": base_x, "Y": base_y, "name": "Coeur d'Alene Parkway", "description": "",
             "objectid": "4", "pic_url": ""},
        ]
        path = _write_csv(rows, self.FIELDS)
        try:
            feats = id_adapter.normalize(path, "2026-05-20", "id_state_parks")
        finally:
            path.unlink()
        names = [f["properties"]["name"] for f in feats]
        self.assertEqual(names, ["Farragut State Park"])

    def test_override_deny_list_excludes_day_use_parks(self):
        base_x, base_y = -13000000.0, 6200000.0
        rows = [
            {"X": base_x, "Y": base_y, "name": "Eagle Island State Park", "description": "",
             "objectid": "5", "pic_url": ""},
        ]
        path = _write_csv(rows, self.FIELDS)
        try:
            feats = id_adapter.normalize(path, "2026-05-20", "id_state_parks")
        finally:
            path.unlink()
        self.assertEqual(feats, [])


class WyAdapterTests(unittest.TestCase):
    def test_camping_park_kept_noncamping_dropped(self):
        feats = self._normalize([
            _point(-108.17, 43.42, {"NAME": "Boysen State Park\r\n", "Site_Type": "State Park",
                                     "Camping": "YES", "FEATURE": "reservoir", "OBJECTID": 1, "CODE": 56013}),
            _point(-105.0, 42.0, {"NAME": "Day Use Only", "Site_Type": "State Park",
                                   "Camping": "NO", "FEATURE": "park", "OBJECTID": 2, "CODE": 56099}),
        ])
        self.assertEqual(len(feats), 1)
        p = feats[0]["properties"]
        self.assertEqual(p["name"], "Boysen State Park")  # trailing whitespace stripped
        self.assertEqual(p["state"], "WY")
        self.assertEqual(p["site_subtype"], "CAMPGROUND")
        self.assertEqual(p["site_id"], "1")

    def _normalize(self, features):
        path = _write_geojson(features)
        try:
            return wy_adapter.normalize(path, "2026-07-01", "wy_state_parks")
        finally:
            path.unlink()


class WaRollupTests(unittest.TestCase):
    def test_active_campsites_roll_up_to_one_campground_per_park(self):
        feats = self._normalize([
            _point(-123.15, 47.36, {"ParkName": "Potlatch", "ParkCode": 42502, "Filter": "active",
                                     "SourceNotes": "online reservation system map"}),
            _point(-123.16, 47.37, {"ParkName": "Potlatch", "ParkCode": 42502, "Filter": "active",
                                     "SourceNotes": "online reservation system map"}),
            _point(-123.14, 47.35, {"ParkName": "Potlatch", "ParkCode": 42502, "Filter": "inactive",
                                     "SourceNotes": ""}),  # inactive dropped
        ])
        self.assertEqual(len(feats), 1, "2 active + 1 inactive -> one campground POI")
        p = feats[0]["properties"]
        self.assertEqual(p["name"], "Potlatch")
        self.assertEqual(p["state"], "WA")
        self.assertEqual(p["site_id"], "42502")
        self.assertEqual(p["total_capacity"], 2)  # only active sites counted
        self.assertEqual(p["reservation_tier"], common.TIER_RESERVABLE)  # SourceNotes -> reservable

    def _normalize(self, features):
        path = _write_geojson(features)
        try:
            return wa_adapter.normalize(path, "2026-07-01", "wa_state_parks")
        finally:
            path.unlink()


class CaAdapterTests(unittest.TestCase):
    def test_type_drives_development_and_group_subtype(self):
        feats = self._normalize([
            _point(-121.7, 38.1, {"Campground": "North Grove Campground", "TYPE": "Developed Family Camp Area",
                                   "SUBTYPE": "Not Defined", "GISID": "GIS0006400", "GlobalID": "{A}",
                                   "DETAIL": "North Grove Campground"}),
            _point(-119.5, 37.5, {"Campground": "Backpack Camp", "TYPE": "Primitive Family Camp Area",
                                   "SUBTYPE": "Walk-in", "GISID": "GIS0006401", "GlobalID": "{B}",
                                   "DETAIL": "Backpack Camp"}),
        ])
        self.assertEqual(len(feats), 2)
        developed = feats[0]["properties"]
        self.assertEqual(developed["name"], "North Grove Campground")
        self.assertEqual(developed["state"], "CA")
        self.assertEqual(developed["site_id"], "GIS0006400")
        self.assertEqual(developed["development_label"], "moderate")  # Developed -> moderate
        primitive = feats[1]["properties"]
        self.assertEqual(primitive["development_label"], "minimal")  # Primitive -> minimal

    def _normalize(self, features):
        path = _write_geojson(features)
        try:
            return ca_adapter.normalize(path, "2026-07-01", "ca_state_parks")
        finally:
            path.unlink()


class OrAdapterTests(unittest.TestCase):
    def test_camping_units_kept_as_centroids_others_dropped(self):
        # A unit square ring -> centroid (0.5, 0.5); only USE_TYPE Camping kept.
        ring = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
        feats = self._normalize([
            _polygon(ring, {"USE_TYPE": "Camping", "FULL_NAME": "Cape Lookout State Park",
                            "NAME": "Cape Lookout", "DESIGNATION": "State Park",
                            "OBJECTID": 7, "GlobalID": "{A}"}),
            _polygon(ring, {"USE_TYPE": "Day Use", "FULL_NAME": "Some Wayside",
                            "DESIGNATION": "State Wayside", "OBJECTID": 8, "GlobalID": "{B}"}),
        ])
        self.assertEqual(len(feats), 1)
        p = feats[0]["properties"]
        self.assertEqual(p["name"], "Cape Lookout State Park")
        self.assertEqual(p["state"], "OR")
        self.assertEqual(p["site_id"], "7")
        self.assertEqual(feats[0]["geometry"]["coordinates"], [0.5, 0.5])

    def _normalize(self, features):
        path = _write_geojson(features)
        try:
            return or_adapter.normalize(path, "2026-07-01", "or_state_parks")
        finally:
            path.unlink()


class AzAdapterTests(unittest.TestCase):
    def test_nonfederal_campground_kept_federal_and_noncamp_dropped(self):
        feats = self._normalize([
            _point(-112.15, 33.80, {"PARKNAME": "Ben Avery", "MANAGENCY": "ARIZONA STATE PARKS & TRAILS",
                                     "CAMPGROUND": "Y", "FEE": "Y", "OBJECTID": 44,
                                     "STREET": "4044 W Black Canyon Blvd", "CITY": "PHOENIX", "PARKTYPE": "REGIONAL PARK"}),
            _point(-111.0, 34.0, {"PARKNAME": "USFS Campground", "MANAGENCY": "FOREST SERVICE",
                                   "CAMPGROUND": "Y", "FEE": "Y", "OBJECTID": 45}),  # federal -> dropped
            _point(-111.5, 33.5, {"PARKNAME": "City Day Park", "MANAGENCY": "MARICOPA COUNTY",
                                   "CAMPGROUND": "N", "FEE": "N", "OBJECTID": 46}),  # no camping -> dropped
        ])
        self.assertEqual(len(feats), 1)
        p = feats[0]["properties"]
        self.assertEqual(p["name"], "Ben Avery")
        self.assertEqual(p["state"], "AZ")
        self.assertEqual(p["operated_by"], "ARIZONA STATE PARKS & TRAILS")
        self.assertTrue(p["fee_charged"])
        self.assertEqual(p["directions"], "4044 W Black Canyon Blvd, PHOENIX")

    def _normalize(self, features):
        path = _write_geojson(features)
        try:
            return az_adapter.normalize(path, "2026-07-01", "az_parks")
        finally:
            path.unlink()


class BcAdapterTests(unittest.TestCase):
    def test_rec_site_campground_kept_trails_and_zero_campsite_dropped(self):
        feats = self._normalize([
            _point(-118.81, 49.52, {"PROJECT_TYPE": "SIT - Recreation Site", "PROJECT_NAME": "State Creek",
                                     "DEFINED_CAMPSITES": 11, "FTEN_RPD_SYSID": 3518,
                                     "SITE_DESCRIPTION": "Lakefront sites.", "DRIVING_DIRECTIONS": "Turn south on Dog Creek Rd."}),
            _point(-120.0, 50.0, {"PROJECT_TYPE": "RTR - Recreation Trail Reserve", "PROJECT_NAME": "Some Trail",
                                   "DEFINED_CAMPSITES": 0, "FTEN_RPD_SYSID": 99}),  # trail -> dropped
            _point(-121.0, 51.0, {"PROJECT_TYPE": "SIT - Recreation Site", "PROJECT_NAME": "Day Use Site",
                                   "DEFINED_CAMPSITES": 0, "FTEN_RPD_SYSID": 100}),  # no campsites -> dropped
        ])
        self.assertEqual(len(feats), 1)
        p = feats[0]["properties"]
        self.assertEqual(p["name"], "State Creek")
        self.assertEqual(p["state"], "BC")
        self.assertEqual(p["site_id"], "3518")
        self.assertEqual(p["total_capacity"], 11)
        self.assertEqual(p["operated_by"], "Recreation Sites and Trails BC")
        self.assertEqual(p["directions"], "Turn south on Dog Creek Rd.")

    def _normalize(self, features):
        path = _write_geojson(features)
        try:
            return bc_adapter.normalize(path, "2026-07-01", "bc_rec_sites")
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
