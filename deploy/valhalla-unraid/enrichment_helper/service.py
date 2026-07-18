"""Stdlib HTTP runtime for the NUC-side surface enrichment helper."""

import json
import math
import os
import sqlite3
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .transform import normalize_surface


MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_POINTS = 100_000
MAX_ROUTE_DISTANCE_M = 2_500_000
MAX_CHUNK_DISTANCE_M = 190_000
MAX_CHUNK_POINTS = 8_000

PROFILES = {
    "adv_balanced": {
        "use_trails": 0.7,
        "use_tracks": 0.7,
        "use_highways": 0.3,
        "shortest": False,
    },
    "avoid_highways": {
        "use_trails": 1.0,
        "use_tracks": 1.0,
        "use_highways": 0.0,
        "shortest": False,
    },
}

TRACE_ATTRIBUTES = [
    "edge.way_id",
    "edge.surface",
    "edge.road_class",
    "edge.use",
    "edge.length",
    "edge.id",
    "matched.type",
    "matched.distance_from_trace_point",
    "matched.edge_index",
]


class RequestProblem(Exception):
    def __init__(self, status_code, reason, detail=None):
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason
        self.detail = detail


class OriginProblem(Exception):
    pass


class ConfigurationProblem(Exception):
    pass


def _haversine_m(a, b):
    radius_m = 6_371_000.0
    lat1 = math.radians(a["lat"])
    lat2 = math.radians(b["lat"])
    dlat = lat2 - lat1
    dlon = math.radians(b["lon"] - a["lon"])
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * radius_m * math.asin(math.sqrt(value))


def _validated_points(payload):
    if not isinstance(payload, dict):
        raise RequestProblem(400, "invalid_request")
    points = payload.get("points")
    if not isinstance(points, list) or len(points) < 2:
        raise RequestProblem(400, "invalid_points", {"minimum": 2})
    if len(points) > MAX_POINTS:
        raise RequestProblem(413, "too_many_points", {"maximum": MAX_POINTS})

    validated = []
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise RequestProblem(400, "invalid_point", {"index": index})
        lat = point.get("lat")
        lon = point.get("lon")
        if (
            isinstance(lat, bool)
            or isinstance(lon, bool)
            or not isinstance(lat, (int, float))
            or not isinstance(lon, (int, float))
            or not math.isfinite(lat)
            or not math.isfinite(lon)
            or not -90 <= lat <= 90
            or not -180 <= lon <= 180
        ):
            raise RequestProblem(400, "invalid_point", {"index": index})
        clean = {"lat": float(lat), "lon": float(lon)}
        ele = point.get("ele")
        if (
            ele is not None
            and not isinstance(ele, bool)
            and isinstance(ele, (int, float))
            and math.isfinite(ele)
        ):
            clean["ele"] = float(ele)
        validated.append(clean)
    return validated


def _route_distance_m(points):
    return sum(_haversine_m(points[index - 1], points[index])
               for index in range(1, len(points)))


def _plan_chunks(points):
    chunks = []
    start_index = 0
    while start_index < len(points) - 1:
        end_index = start_index
        distance_m = 0.0
        while end_index + 1 < len(points):
            next_distance_m = _haversine_m(points[end_index], points[end_index + 1])
            next_point_count = end_index - start_index + 2
            if next_distance_m > MAX_CHUNK_DISTANCE_M:
                raise RequestProblem(
                    413,
                    "segment_too_long",
                    {"start_index": end_index, "distance_m": round(next_distance_m, 1)},
                )
            if (
                end_index > start_index
                and (
                    distance_m + next_distance_m > MAX_CHUNK_DISTANCE_M
                    or next_point_count > MAX_CHUNK_POINTS
                )
            ):
                break
            distance_m += next_distance_m
            end_index += 1
            if next_point_count >= MAX_CHUNK_POINTS:
                break

        chunks.append(
            {
                "start_index": start_index,
                "end_index": end_index,
                "distance_m": distance_m,
                "points": points[start_index:end_index + 1],
            }
        )
        if end_index == len(points) - 1:
            break
        start_index = end_index
    return chunks


def _nearest_rank(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        int(round((percentile / 100.0) * (len(ordered) - 1))),
    )
    return round(ordered[index], 3)


def _encode_polyline6(points):
    encoded = []
    previous_lat = 0
    previous_lon = 0

    def encode_delta(delta):
        value = ~(delta << 1) if delta < 0 else delta << 1
        while value >= 0x20:
            encoded.append(chr((0x20 | (value & 0x1F)) + 63))
            value >>= 5
        encoded.append(chr(value + 63))

    for point in points:
        lat = int(round(point["lat"] * 1_000_000))
        lon = int(round(point["lon"] * 1_000_000))
        encode_delta(lat - previous_lat)
        encode_delta(lon - previous_lon)
        previous_lat = lat
        previous_lon = lon
    return "".join(encoded)


class ValhallaClient:
    def __init__(self, base_url, timeout_s=180):
        self.url = base_url.rstrip("/") + "/trace_attributes"
        self.timeout_s = timeout_s

    def trace_attributes(self, points, profile):
        payload = {
            "shape": [{"lat": point["lat"], "lon": point["lon"]} for point in points],
            "costing": "motorcycle",
            "costing_options": {"motorcycle": PROFILES[profile]},
            "shape_match": "map_snap",
            "filters": {"attributes": TRACE_ATTRIBUTES, "action": "include"},
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise OriginProblem("valhalla_http_{}".format(error.code)) from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise OriginProblem("valhalla_unavailable") from error


class ManifestStore:
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        try:
            with self.path.open(encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigurationProblem("manifest_unavailable") from error
        if not manifest.get("data_version"):
            raise ConfigurationProblem("manifest_missing_data_version")
        return manifest


class SidecarStore:
    def __init__(self, path):
        self.path = Path(path)

    def rows_for(self, way_ids, expected_data_version):
        if not self.path.is_file():
            raise ConfigurationProblem("sidecar_unavailable")
        try:
            connection = sqlite3.connect(
                "{}?mode=ro".format(self.path.resolve().as_uri()),
                uri=True,
            )
            meta_row = connection.execute(
                "SELECT value FROM meta WHERE key='data_version'"
            ).fetchone()
            sidecar_version = meta_row[0] if meta_row else None
            if sidecar_version != expected_data_version:
                raise ConfigurationProblem("sidecar_data_version_mismatch")

            rows = {}
            ids = sorted(
                way_id for way_id in set(way_ids)
                if isinstance(way_id, int) and way_id != 0
            )
            for start in range(0, len(ids), 900):
                batch = ids[start:start + 900]
                placeholders = ",".join("?" for _ in batch)
                query = (
                    "SELECT way_id, surface, tracktype, smoothness, "
                    "mtb_scale, sac_scale FROM surface_provenance "
                    "WHERE way_id IN ({})".format(placeholders)
                )
                for row in connection.execute(query, batch):
                    rows[row[0]] = {
                        "surface": row[1],
                        "tracktype": row[2],
                        "smoothness": row[3],
                        "mtb_scale": row[4],
                        "sac_scale": row[5],
                    }
            return rows
        except sqlite3.Error as error:
            raise ConfigurationProblem("sidecar_unreadable") from error
        finally:
            if "connection" in locals():
                connection.close()


class EnrichmentService:
    def __init__(self, trace_client, sidecar_store, manifest_store):
        self.trace_client = trace_client
        self.sidecar_store = sidecar_store
        self.manifest_store = manifest_store

    def version(self):
        manifest = self.manifest_store.read()
        return {
            "data_version": manifest["data_version"],
            "valhalla_version": manifest.get("valhalla_version", "unknown"),
            "extract_date": manifest.get("extract_date", "unknown"),
            "region": manifest.get("region", "us-west"),
            "profiles": list(PROFILES),
        }

    def enrich(self, payload):
        points = _validated_points(payload)
        profile = payload.get("profile", "adv_balanced")
        if profile not in PROFILES:
            raise RequestProblem(
                400, "invalid_profile", {"profiles": list(PROFILES)}
            )
        geometry = payload.get("geometry", "none")
        if geometry not in {"none", "points", "polyline6"}:
            raise RequestProblem(
                400,
                "unsupported_geometry",
                {"supported": ["none", "points", "polyline6"]},
            )

        distance_m = _route_distance_m(points)
        if distance_m > MAX_ROUTE_DISTANCE_M:
            raise RequestProblem(
                413,
                "route_too_long",
                {"maximum_m": MAX_ROUTE_DISTANCE_M, "distance_m": round(distance_m, 1)},
            )

        planned_chunks = _plan_chunks(points)
        transform_chunks = []
        chunk_metrics = []
        failed_chunks = []
        global_matches = [False] * len(points)
        snap_distances = []

        for chunk_index, chunk in enumerate(planned_chunks):
            try:
                response = self.trace_client.trace_attributes(
                    chunk["points"], profile
                )
            except OriginProblem:
                failed_chunks.append(chunk_index)
                response = {"edges": [], "matched_points": []}

            matched_points = response.get("matched_points", []) or []
            matched_count = 0
            for local_index, matched_point in enumerate(matched_points):
                global_index = chunk["start_index"] + local_index
                is_matched = (
                    matched_point.get("type") != "unmatched"
                    and isinstance(matched_point.get("edge_index"), int)
                )
                if is_matched:
                    matched_count += 1
                    if 0 <= global_index < len(global_matches):
                        global_matches[global_index] = True
                    snap_distance = matched_point.get("distance_from_trace_point")
                    if isinstance(snap_distance, (int, float)):
                        snap_distances.append(float(snap_distance))

            local_point_count = len(chunk["points"])
            chunk_metrics.append(
                {
                    "start_index": chunk["start_index"],
                    "end_index": chunk["end_index"],
                    "distance_m": round(chunk["distance_m"], 1),
                    "match_rate": round(
                        matched_count / local_point_count, 6
                    ) if local_point_count else 0.0,
                }
            )
            transform_chunks.append(
                {
                    "start_index": chunk["start_index"],
                    "end_index": chunk["end_index"],
                    "distance_m": chunk["distance_m"],
                    "response": response,
                }
            )

        manifest = self.manifest_store.read()
        way_ids = [
            edge.get("way_id")
            for chunk in transform_chunks
            for edge in chunk["response"].get("edges", [])
        ]
        sidecar_rows = self.sidecar_store.rows_for(
            way_ids, manifest["data_version"]
        )
        enrichment = normalize_surface(
            transform_chunks,
            sidecar_rows,
            manifest,
            point_count=len(points),
        )

        matched_total = sum(global_matches)
        match_rate = round(matched_total / len(points), 6)
        if failed_chunks:
            status = "error"
            http_status = 200
        elif matched_total == 0:
            status = "no_match"
            http_status = 422
        elif matched_total < len(points):
            status = "partial"
            http_status = 200
        else:
            status = "ok"
            http_status = 200

        envelope = {
            "status": status,
            "profile": profile,
            "match": {
                "match_rate": match_rate,
                "unmatched_count": len(points) - matched_total,
                "snap_p90_m": _nearest_rank(snap_distances, 90),
                "snap_max_m": round(max(snap_distances), 3)
                if snap_distances else None,
            },
            "chunks": chunk_metrics,
            **enrichment,
        }
        if failed_chunks:
            envelope["error"] = {"chunk_indices": failed_chunks}
        if geometry == "points":
            envelope["geometry"] = {
                "encoding": "points",
                "points": points,
            }
        elif geometry == "polyline6":
            envelope["geometry"] = {
                "encoding": "polyline6",
                "shape": _encode_polyline6(points),
            }
        return http_status, envelope


def _json_response(handler, status_code, payload, extra_headers=None):
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.send_header("Cache-Control", "no-store")
    for key, value in (extra_headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(encoded)


def build_handler(service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "gpxplore-enrichment/1"

        def log_message(self, format_string, *args):
            print(
                json.dumps(
                    {
                        "event": "http",
                        "client": self.client_address[0],
                        "message": format_string % args,
                    }
                ),
                flush=True,
            )

        def do_GET(self):
            if self.path != "/version":
                _json_response(self, 404, {"status": "not_found"})
                return
            try:
                _json_response(self, 200, service.version())
            except ConfigurationProblem:
                _json_response(self, 500, {"status": "configuration_error"})

        def do_POST(self):
            if self.path != "/trace/attributes":
                _json_response(self, 404, {"status": "not_found"})
                return
            content_length = self.headers.get("Content-Length")
            if content_length is None:
                _json_response(self, 411, {"status": "length_required"})
                return
            try:
                body_length = int(content_length)
            except ValueError:
                _json_response(self, 400, {"status": "invalid_request"})
                return
            if body_length > MAX_BODY_BYTES:
                _json_response(
                    self,
                    413,
                    {"status": "error", "reason": "body_too_large"},
                )
                return
            try:
                payload = json.loads(self.rfile.read(body_length))
                status_code, response = service.enrich(payload)
                print(
                    json.dumps(
                        {
                            "event": "enrichment",
                            "status": response["status"],
                            "profile": response["profile"],
                            "match_rate": response["match"]["match_rate"],
                            "snap_p90_m": response["match"]["snap_p90_m"],
                            "snap_max_m": response["match"]["snap_max_m"],
                            "inferred_pct": response["summary"]["inferredPct"],
                            "data_version": response["summary"]["dataVersion"],
                            "chunk_count": len(response["chunks"]),
                        }
                    ),
                    flush=True,
                )
                _json_response(self, status_code, response)
            except json.JSONDecodeError:
                _json_response(
                    self, 400, {"status": "error", "reason": "invalid_json"}
                )
            except RequestProblem as problem:
                response = {"status": "error", "reason": problem.reason}
                if problem.detail is not None:
                    response["detail"] = problem.detail
                _json_response(self, problem.status_code, response)
            except ConfigurationProblem:
                _json_response(self, 500, {"status": "configuration_error"})

    return Handler


def create_service_from_env():
    data_dir = Path(os.environ.get("ENRICHMENT_DATA_DIR", "/data"))
    valhalla_url = os.environ.get(
        "VALHALLA_URL", "http://valhalla:8002"
    )
    return EnrichmentService(
        ValhallaClient(valhalla_url),
        SidecarStore(data_dir / "surface_provenance.sqlite"),
        ManifestStore(data_dir / "tile_manifest.json"),
    )


def main():
    host = os.environ.get("ENRICHMENT_HOST", "0.0.0.0")
    port = int(os.environ.get("ENRICHMENT_PORT", "8003"))
    server = ThreadingHTTPServer((host, port), build_handler(create_service_from_env()))
    print(
        json.dumps(
            {
                "event": "started",
                "host": host,
                "port": port,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
