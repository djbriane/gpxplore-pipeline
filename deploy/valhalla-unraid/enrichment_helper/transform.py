"""Pure surface normalization for recorded Valhalla trace chunks."""

from collections.abc import Mapping, Sequence
from typing import Any


SURFACE_CLASSES = {
    "paved_smooth": ("paved", 0.0),
    "paved": ("paved", 0.0),
    "paved_rough": ("paved", 0.0),
    "compacted": ("compacted", 0.10),
    "dirt": ("dirt", 0.35),
    "gravel": ("gravel", 0.40),
    "path": ("rough", 1.0),
}
SURFACE_CLASS_ORDER = ("paved", "compacted", "dirt", "gravel", "rough", "unknown")
RIDER_SURFACE_CLASS_ORDER = (
    "paved",
    "hardpack",
    "dirt_road",
    "primitive_road",
    "loose_gravel",
    "rough_trail",
    "unknown",
)
UNPAVED_CLASSES = frozenset({"compacted", "dirt", "gravel", "rough"})
SURFACE_ENGINE_VERSION = "surface-normalization/2"

UNKNOWN_ROAD_CLASS_ROUGHNESS = {
    "motorway": 0.0,
    "trunk": 0.0,
    "primary": 0.0,
    "secondary": 0.0,
    "tertiary": 0.0,
    "residential": 0.0,
    "unclassified": 0.4,
    "service_other": 0.4,
}


def _sidecar_by_way_id(sidecar_rows: Mapping[Any, Mapping[str, Any]]):
    return {int(way_id): row for way_id, row in sidecar_rows.items()}


def _edge_key(edge):
    edge_id = edge.get("id")
    if edge_id is not None:
        return ("id", edge_id)
    return (
        "attributes",
        edge.get("way_id"),
        edge.get("surface"),
        edge.get("road_class"),
        edge.get("use"),
    )


def _rider_surface_class(surface_class, is_track):
    if surface_class == "compacted":
        return "hardpack"
    if surface_class == "dirt":
        return "primitive_road" if is_track else "dirt_road"
    return {
        "paved": "paved",
        "gravel": "loose_gravel",
        "rough": "rough_trail",
        "unknown": "unknown",
    }[surface_class]


def _normalized_segment(edge, sidecar, start_m):
    way_id = edge.get("way_id")
    raw_surface = str(edge.get("surface") or "")
    surface_class, roughness = SURFACE_CLASSES.get(raw_surface, ("unknown", 0.0))
    is_track = edge.get("use") == "track"
    if is_track:
        roughness = min(1.0, roughness + 0.2)

    sidecar_row = sidecar.get(way_id)
    if way_id in (None, 0):
        confidence = "unknown"
        surface_class = "unknown"
        road_class = edge.get("road_class")
        if road_class in UNKNOWN_ROAD_CLASS_ROUGHNESS:
            roughness = UNKNOWN_ROAD_CLASS_ROUGHNESS[road_class]
            if is_track:
                roughness = min(1.0, roughness + 0.2)
            roughness = round(roughness, 2)
        else:
            roughness = None
    elif sidecar_row is not None:
        confidence = "tagged"
    else:
        confidence = "inferred"
    if roughness is not None:
        roughness = round(roughness, 2)

    length_m = float(edge.get("length") or 0.0) * 1000.0
    segment = {
        "startM": start_m,
        "endM": start_m + length_m,
        "lengthM": length_m,
        "surfaceClass": surface_class,
        "riderSurfaceClass": _rider_surface_class(surface_class, is_track),
        "roughness": roughness,
        "isTrack": is_track,
        "roadClass": edge.get("road_class") or "service_other",
        "valhallaSurface": raw_surface,
        "confidence": confidence,
    }
    if confidence == "tagged":
        osm_tags = {
            key: value
            for key, value in sidecar_row.items()
            if key in {"surface", "tracktype", "smoothness", "mtb_scale"}
            and value is not None
        }
        segment["osmTags"] = osm_tags
    return segment


def normalize_surface(
    chunks: Sequence[Mapping[str, Any]],
    sidecar_rows: Mapping[Any, Mapping[str, Any]],
    manifest: Mapping[str, Any],
    *,
    point_count: int,
):
    """Normalize Valhalla trace chunks into the public SurfaceEnrichment shape."""
    sidecar = _sidecar_by_way_id(sidecar_rows)
    segments = []
    start_m = 0.0
    previous_edge_key = None
    chunk_normalized_edges = []
    for chunk_index, chunk in enumerate(chunks):
        edges = chunk.get("response", {}).get("edges", [])
        normalized_edges = [
            (
                None
                if edge.get("surface") == "impassable"
                else _normalized_segment(edge, sidecar, 0.0)
            )
            for edge in edges
        ]
        chunk_normalized_edges.append(normalized_edges)
        for edge_index, edge in enumerate(edges):
            if edge.get("surface") == "impassable":
                continue
            edge_key = _edge_key(edge)
            if chunk_index > 0 and edge_index == 0 and edge_key == previous_edge_key:
                continue
            segment = normalized_edges[edge_index]
            segment["startM"] = start_m
            segment["endM"] = start_m + segment["lengthM"]
            segments.append(segment)
            start_m = segment["endM"]
            previous_edge_key = edge_key

    total_length_m = sum(segment["lengthM"] for segment in segments)
    known_roughness_length_m = sum(
        segment["lengthM"] for segment in segments if segment["roughness"] is not None
    )
    unknown_fallback = (
        sum(
            segment["lengthM"] * segment["roughness"]
            for segment in segments
            if segment["roughness"] is not None
        )
        / known_roughness_length_m
        if known_roughness_length_m
        else 0.4
    )
    unknown_fallback = round(unknown_fallback, 12)
    for normalized_edges in chunk_normalized_edges:
        for segment in normalized_edges:
            if segment is not None and segment["roughness"] is None:
                segment["roughness"] = unknown_fallback

    route_roughness_mean = (
        sum(segment["lengthM"] * segment["roughness"] for segment in segments)
        / total_length_m
        if total_length_m
        else 0.4
    )
    route_roughness_mean = round(route_roughness_mean, 12)

    point_roughness = [None] * point_count
    for chunk, normalized_edges in zip(chunks, chunk_normalized_edges):
        chunk_start = int(chunk.get("start_index", 0))
        matched_points = chunk.get("response", {}).get("matched_points", [])
        for local_index, matched_point in enumerate(matched_points):
            global_index = chunk_start + local_index
            edge_index = matched_point.get("edge_index")
            if not 0 <= global_index < point_count:
                continue
            if not isinstance(edge_index, int) or not 0 <= edge_index < len(
                normalized_edges
            ):
                continue
            segment = normalized_edges[edge_index]
            if segment is None:
                continue
            if point_roughness[global_index] is None:
                point_roughness[global_index] = segment["roughness"]
    point_roughness = [
        value if value is not None else route_roughness_mean
        for value in point_roughness
    ]

    by_class_m = {surface_class: 0.0 for surface_class in SURFACE_CLASS_ORDER}
    by_rider_class_m = {
        surface_class: 0.0 for surface_class in RIDER_SURFACE_CLASS_ORDER
    }
    by_confidence_m = {"tagged": 0.0, "inferred": 0.0, "unknown": 0.0}
    track_m = 0.0
    for segment in segments:
        by_class_m[segment["surfaceClass"]] += segment["lengthM"]
        by_rider_class_m[segment["riderSurfaceClass"]] += segment["lengthM"]
        by_confidence_m[segment["confidence"]] += segment["lengthM"]
        if segment["isTrack"]:
            track_m += segment["lengthM"]

    dominant_class = (
        max(SURFACE_CLASS_ORDER, key=by_class_m.__getitem__)
        if total_length_m
        else "unknown"
    )
    dominant_rider_surface_class = (
        max(RIDER_SURFACE_CLASS_ORDER, key=by_rider_class_m.__getitem__)
        if total_length_m
        else "unknown"
    )

    def percentage(length_m):
        return round(100.0 * length_m / total_length_m, 12) if total_length_m else 0.0

    summary = {
        "byClassM": by_class_m,
        "dominantClass": dominant_class,
        "byRiderClassM": by_rider_class_m,
        "dominantRiderSurfaceClass": dominant_rider_surface_class,
        "pavedPct": percentage(by_class_m["paved"]),
        "unpavedPct": percentage(
            sum(by_class_m[surface_class] for surface_class in UNPAVED_CLASSES)
        ),
        "trackPct": percentage(track_m),
        "roughnessMean": route_roughness_mean,
        "taggedPct": percentage(by_confidence_m["tagged"]),
        "inferredPct": percentage(by_confidence_m["inferred"]),
        "unknownPct": percentage(by_confidence_m["unknown"]),
        "engineVersion": SURFACE_ENGINE_VERSION,
        "dataVersion": manifest.get("data_version", "unknown"),
    }

    return {
        "segments": segments,
        "pointRoughness": point_roughness,
        "summary": summary,
    }
