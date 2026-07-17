#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Answers gpxplore-pipeline#6.

Does Valhalla map-matching (`trace_attributes`, map_snap) return USABLE surface
metadata on real BDR backcountry tracks? Runs several western BDR GPX tracks against
the local us-west tile set and reports, per track: match rate, snap distances, and the
distribution/quality of surface + road_class along the matched path.

Stdlib only (repo constraint). Delete once findings are captured in the ticket.
"""
import json, math, sys, urllib.request, urllib.error
from pathlib import Path
from collections import defaultdict

SERVICE = "http://localhost:8002/trace_attributes"
BDR_DIR = Path("/Users/djbriane/Development/gpxplore/gpxplore-web/specs/refs/bdr-gpx")

# Western BDR tracks (inside the us-west tile set), chosen for surface variety.
TRACKS = [
    "WABDR-Apr2026-v2",                    # WA — forest service roads
    "COBDR-Mar2026",                       # CO — high alpine, mixed pavement/dirt
    "UTBDR-Mar2026",                       # UT — desert dirt / slickrock
    "NVBDR-April2026",                     # NV — remote, faint 2-track
    "BDRX-Steens-Alvord-OR-June2025-v2",   # OR — high-desert faint 2-track
    "white-rim-trails-offroad",            # UT — genuine 4x4 (Canyonlands White Rim)
]

# Per #7: use motorcycle costing with trails/tracks wide open so the matcher is WILLING
# to snap onto backcountry surfaces (auto refuses tracks; motorcycle default use_trails=0
# penalizes dirt). This is the "will it even match backcountry" question.
COSTING = "motorcycle"
COSTING_OPTS = {"motorcycle": {"use_trails": 1.0, "use_tracks": 1.0, "use_highways": 0.5}}

TARGET_SPACING_M = 200      # downsample to ~1 point / 200 m
MAX_POINTS = 400            # contiguous window cap per track (keep requests small)


def haversine(a, b):
    R = 6371000.0
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dp = math.radians(la2 - la1); dl = math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def parse_trkpts(path):
    # namespace-agnostic: match any tag ending in 'trkpt'
    import xml.etree.ElementTree as ET
    pts = []
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag.rsplit("}", 1)[-1] == "trkpt":
            try:
                pts.append((float(el.attrib["lat"]), float(el.attrib["lon"])))
            except (KeyError, ValueError):
                pass
            el.clear()
    return pts


def downsample(pts, spacing_m):
    if not pts:
        return []
    out = [pts[0]]
    acc = 0.0
    for i in range(1, len(pts)):
        acc += haversine(pts[i - 1], pts[i])
        if acc >= spacing_m:
            out.append(pts[i]); acc = 0.0
    return out


def window(pts, n):
    """Contiguous middle slice of up to n points (backcountry-representative)."""
    if len(pts) <= n:
        return pts
    start = (len(pts) - n) // 2
    return pts[start:start + n]


def trace_attributes(shape):
    payload = {
        "shape": [{"lat": la, "lon": lo} for la, lo in shape],
        "costing": COSTING,
        "costing_options": COSTING_OPTS,
        "shape_match": "map_snap",
        "filters": {
            "attributes": [
                "edge.surface", "edge.road_class", "edge.use", "edge.length", "edge.id",
                "matched.type", "matched.distance_from_trace_point", "matched.edge_index",
            ],
            "action": "include",
        },
    }
    req = urllib.request.Request(
        SERVICE, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def pct(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = min(len(sorted_vals) - 1, int(round((p / 100) * (len(sorted_vals) - 1))))
    return sorted_vals[k]


def analyze(name, resp):
    edges = resp.get("edges", []) or []
    mpts = resp.get("matched_points", []) or []

    # match rate + snap distances
    types = defaultdict(int)
    snaps = []
    for m in mpts:
        types[m.get("type", "?")] += 1
        d = m.get("distance_from_trace_point")
        if d is not None and m.get("type") == "matched":
            snaps.append(d)
    total = len(mpts) or 1
    matched = types.get("matched", 0)
    snaps.sort()

    # surface + road_class distribution, length-weighted (km)
    surf_km = defaultdict(float)
    rc_km = defaultdict(float)
    use_km = defaultdict(float)
    total_km = 0.0
    for e in edges:
        L = e.get("length", 0.0) or 0.0
        total_km += L
        surf_km[e.get("surface", "(none)")] += L
        rc_km[e.get("road_class", "(none)")] += L
        use_km[e.get("use", "(none)")] += L

    print(f"\n{'='*70}\n{name}")
    print(f"  points sent: {len(mpts)}  |  matched: {matched}/{len(mpts)} "
          f"({100*matched/total:.0f}%)  interpolated: {types.get('interpolated',0)}  "
          f"unmatched: {types.get('unmatched',0)}")
    if snaps:
        print(f"  snap dist (m): p50={pct(snaps,50):.1f}  p90={pct(snaps,90):.1f}  "
              f"max={snaps[-1]:.1f}")
    print(f"  matched path: {total_km:.1f} km over {len(edges)} edges")

    def hist(d, unit="km"):
        tot = sum(d.values()) or 1
        for k, v in sorted(d.items(), key=lambda kv: -kv[1]):
            print(f"      {k:<16} {v:6.1f} {unit}  ({100*v/tot:.0f}%)")

    print("  surface (Valhalla enum, by distance):"); hist(surf_km)
    unknown = surf_km.get("(none)", 0) + surf_km.get("impassable", 0)
    print(f"    -> unknown/none surface: {100*unknown/(total_km or 1):.0f}% of distance")
    print("  road_class (by distance):"); hist(rc_km)
    print("  edge.use (by distance):"); hist(use_km)

    return {
        "name": name, "sent": len(mpts), "matched_pct": 100*matched/total,
        "snap_p90": pct(snaps, 90) if snaps else None,
        "km": total_km, "unknown_surface_pct": 100*unknown/(total_km or 1),
        "surfaces": dict(surf_km),
    }


def main():
    summaries = []
    for stem in TRACKS:
        path = BDR_DIR / f"{stem}.gpx"
        if not path.exists():
            print(f"MISSING: {path}"); continue
        raw = parse_trkpts(str(path))
        shape = window(downsample(raw, TARGET_SPACING_M), MAX_POINTS)
        if len(shape) < 2:
            print(f"{stem}: too few points ({len(raw)} raw)"); continue
        print(f"\n### {stem}: {len(raw)} raw pts -> {len(shape)} sampled (~{TARGET_SPACING_M}m spacing)")
        resp, err = trace_attributes(shape)
        if err:
            print(f"  ERROR: {err}"); continue
        summaries.append(analyze(stem, resp))

    # overall
    print(f"\n\n{'#'*70}\nSUMMARY across {len(summaries)} tracks")
    print(f"{'track':<38}{'match%':>7}{'snap_p90':>10}{'km':>7}{'unk_surf%':>11}")
    for s in summaries:
        sp = f"{s['snap_p90']:.0f}m" if s['snap_p90'] is not None else "-"
        print(f"{s['name']:<38}{s['matched_pct']:>6.0f}%{sp:>10}{s['km']:>6.0f}"
              f"{s['unknown_surface_pct']:>10.0f}%")
    all_surf = defaultdict(float)
    for s in summaries:
        for k, v in s["surfaces"].items():
            all_surf[k] += v
    tot = sum(all_surf.values()) or 1
    print("\nAggregate surface mix (by distance, all tracks):")
    for k, v in sorted(all_surf.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<16} {100*v/tot:5.1f}%")


if __name__ == "__main__":
    main()
