#!/usr/bin/env python3
"""Verify the surface-provenance sidecar — gpxplore-pipeline#11, point 4.

Re-runs #6's western BDR tracks through `trace_attributes` (this time with
`edge.way_id` in the filter), joins each matched edge to the sidecar, and reports
the tagged / inferred / unknown split so we can confirm it is sane:

  - official BDRs should skew high-`tagged` (they are well surveyed), and
  - some untagged `highway=unclassified` roads should surface as `inferred`
    rather than silently returning `paved_*` — the exact under-count the sidecar
    exists to expose (spec §1).

The join mirrors the #10 service contract (spec §2):
    way_id in sidecar        -> tagged
    way_id absent, way_id!=0 -> inferred
    way_id == 0 / no edge    -> unknown

Run on the NUC after a build, against the local serve container + the sidecar it
just produced. Stdlib + sqlite3 only (repo constraint), so it runs anywhere.

    verify_sidecar.py [--sidecar /path/surface_provenance.sqlite] \
                      [--service http://localhost:8002/trace_attributes] \
                      [--bdr-dir /path/to/bdr-gpx]
"""
import argparse
import json
import math
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

# Same western BDR set as prototypes/bdr-mapmatch/mapmatch.py (#6).
TRACKS = [
    "WABDR-Apr2026-v2",
    "COBDR-Mar2026",
    "UTBDR-Mar2026",
    "NVBDR-April2026",
    "BDRX-Steens-Alvord-OR-June2025-v2",
    "white-rim-trails-offroad",
]

# Per #7: motorcycle costing with trails/tracks open so the matcher will snap
# onto backcountry surfaces (mirrors #6 exactly for comparability).
COSTING = "motorcycle"
COSTING_OPTS = {"motorcycle": {"use_trails": 1.0, "use_tracks": 1.0, "use_highways": 0.5}}
TARGET_SPACING_M = 200
MAX_POINTS = 400

DEFAULT_BDR_DIR = "/Users/djbriane/Development/gpxplore/gpxplore-web/specs/refs/bdr-gpx"


def haversine(a, b):
    R = 6371000.0
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dp = math.radians(la2 - la1)
    dl = math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def parse_trkpts(path):
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
            out.append(pts[i])
            acc = 0.0
    return out


def window(pts, n):
    if len(pts) <= n:
        return pts
    start = (len(pts) - n) // 2
    return pts[start:start + n]


def trace_attributes(service, shape):
    payload = {
        "shape": [{"lat": la, "lon": lo} for la, lo in shape],
        "costing": COSTING,
        "costing_options": COSTING_OPTS,
        "shape_match": "map_snap",
        "filters": {
            # #6 set + edge.way_id (the sidecar join key, spec §9).
            "attributes": [
                "edge.way_id", "edge.surface", "edge.road_class", "edge.use",
                "edge.length", "edge.id",
            ],
            "action": "include",
        },
    }
    req = urllib.request.Request(
        service, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


class Sidecar:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key='data_version'").fetchone()
        self.data_version = row[0] if row else "(no meta)"

    def has(self, way_id):
        return self.conn.execute(
            "SELECT 1 FROM surface_provenance WHERE way_id=? LIMIT 1",
            (way_id,)).fetchone() is not None


def classify(sidecar, way_id):
    if way_id is None or way_id == 0:
        return "unknown"
    return "tagged" if sidecar.has(way_id) else "inferred"


def analyze(name, resp, sidecar):
    edges = resp.get("edges", []) or []
    conf_km = defaultdict(float)      # tagged/inferred/unknown -> km
    total_km = 0.0
    # The honesty win: inferred edges Valhalla fabricated as paved_* (spec §1).
    silent_paved = defaultdict(float)  # road_class -> km, inferred + paved-ish
    for e in edges:
        L = e.get("length", 0.0) or 0.0
        total_km += L
        conf = classify(sidecar, e.get("way_id"))
        conf_km[conf] += L
        if conf == "inferred" and str(e.get("surface", "")).startswith("paved"):
            silent_paved[e.get("road_class", "(none)")] += L

    tot = total_km or 1.0
    print(f"\n{'=' * 70}\n{name}  ({total_km:.1f} km over {len(edges)} edges)")
    for conf in ("tagged", "inferred", "unknown"):
        km = conf_km.get(conf, 0.0)
        print(f"  {conf:<9} {km:6.1f} km  ({100 * km / tot:.0f}%)")
    if silent_paved:
        print("  inferred-but-paved (silent under-count risk, spec §1):")
        for rc, km in sorted(silent_paved.items(), key=lambda kv: -kv[1]):
            print(f"      {rc:<16} {km:6.1f} km")
    return {"name": name, "km": total_km, "conf_km": dict(conf_km),
            "silent_paved_km": sum(silent_paved.values())}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sidecar", default="/mnt/user/appdata/valhalla/surface_provenance.sqlite")
    ap.add_argument("--service", default="http://localhost:8002/trace_attributes")
    ap.add_argument("--bdr-dir", default=DEFAULT_BDR_DIR)
    args = ap.parse_args(argv)

    if not Path(args.sidecar).exists():
        print(f"ERROR: sidecar not found: {args.sidecar}", file=sys.stderr)
        return 2
    sidecar = Sidecar(args.sidecar)
    bdr_dir = Path(args.bdr_dir)
    print(f"sidecar data_version: {sidecar.data_version}")
    print(f"service: {args.service}")

    summaries = []
    for stem in TRACKS:
        path = bdr_dir / f"{stem}.gpx"
        if not path.exists():
            print(f"MISSING: {path}")
            continue
        shape = window(downsample(parse_trkpts(str(path)), TARGET_SPACING_M), MAX_POINTS)
        if len(shape) < 2:
            print(f"{stem}: too few points")
            continue
        resp, err = trace_attributes(args.service, shape)
        if err:
            print(f"{stem}: ERROR {err}")
            continue
        summaries.append(analyze(stem, resp, sidecar))

    if not summaries:
        print("\nNo tracks analyzed.")
        return 1

    agg = defaultdict(float)
    total = 0.0
    silent = 0.0
    for s in summaries:
        total += s["km"]
        silent += s["silent_paved_km"]
        for conf, km in s["conf_km"].items():
            agg[conf] += km
    tot = total or 1.0
    print(f"\n\n{'#' * 70}\nAGGREGATE across {len(summaries)} tracks ({total:.0f} km)")
    for conf in ("tagged", "inferred", "unknown"):
        print(f"  {conf:<9} {100 * agg.get(conf, 0.0) / tot:5.1f}%")
    print(f"\n  inferred-but-paved total: {silent:.0f} km "
          f"({100 * silent / tot:.0f}%) — surfaced as inferred, NOT silently trusted.")
    # Sanity gates (spec §1 expectations): BDRs skew tagged; inferred is non-zero.
    tagged_pct = 100 * agg.get("tagged", 0.0) / tot
    inferred_pct = 100 * agg.get("inferred", 0.0) / tot
    print("\nSanity:")
    print(f"  tagged share {tagged_pct:.0f}% "
          f"({'OK — BDRs skew tagged' if tagged_pct >= 40 else 'LOW — investigate join/extract match'})")
    print(f"  inferred share {inferred_pct:.0f}% "
          f"({'OK — under-count made visible' if inferred_pct > 0 else 'ZERO — suspicious, expected some'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
