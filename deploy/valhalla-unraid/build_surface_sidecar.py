#!/usr/bin/env python3
"""Surface-provenance sidecar builder — gpxplore-pipeline#11 (parent #4).

One pass over the SAME `us-west` OSM extract used to build the Valhalla tiles,
emitting a lookup keyed by OSM way id so surface enrichment (#9) can tell a
community-**tagged** surface from a Valhalla-**inferred** (road-class default)
one. `trace_attributes` exposes no tag provenance, so a surveyed
`surface=gravel` is otherwise indistinguishable from a class-based guess
(`pbfgraphparser.cc:3020-3071`). Contract: `specs/surface-normalization--planned.md` §2.

  way_id -> { surface, tracktype, smoothness, mtb_scale, sac_scale }

COMPACTNESS DECISION (issue "open decision" #3): only ways that carry an
*explicit* surface-ish tag are stored. Untagged highway ways are omitted —
they are exactly Valhalla's road-class default and are recoverable without the
sidecar. Presence in the table therefore MEANS `hasExplicitSurface = true`.

The #10 service join (matched `edge.way_id` -> this sidecar) then reads:
    way_id present in sidecar     -> confidence = "tagged"
    way_id absent (way_id != 0)   -> confidence = "inferred"   (highway way, no tag)
    way_id == 0 / no matched edge -> confidence = "unknown"

Restricted to `highway=*` ways (the routable set) to keep the artifact small.

Usage:
    build_surface_sidecar.py <extract.osm.pbf> <out.sqlite> \
        [--data-version us-west-YYMMDD] [--extract-sha256 HEX]

Requires pyosmium (classic SimpleHandler API, osmium 3.x). Intended to run in a
throwaway python container from build-tiles.sh; see that script + the README.
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone

import osmium
import osmium.version  # submodule is not auto-imported by `import osmium`

# Any one of these OSM tags makes a way's surface community-supplied rather than
# Valhalla-guessed. `sac_scale`/`mtb:scale` are difficulty scales, not surface
# strings, but their PRESENCE is still explicit provenance and they seed the
# deferred v2 smoothness/difficulty taxonomy (spec §10.3) with no rebuild.
PROVENANCE_TAGS = ("surface", "tracktype", "smoothness", "mtb:scale", "sac_scale")

BATCH = 20_000


class SurfaceHandler(osmium.SimpleHandler):
    """Collect explicit surface provenance for every routable highway way."""

    def __init__(self, conn):
        super().__init__()
        self.cur = conn.cursor()
        self.rows = []
        self.highway_ways = 0   # all highway=* ways seen (the inferred denominator)
        self.tagged_ways = 0    # kept: at least one explicit provenance tag

    def way(self, w):
        tags = w.tags
        if "highway" not in tags:
            return
        self.highway_ways += 1
        surface = tags.get("surface")
        tracktype = tags.get("tracktype")
        smoothness = tags.get("smoothness")
        mtb_scale = tags.get("mtb:scale")
        sac_scale = tags.get("sac_scale")
        if not any(v is not None for v in
                   (surface, tracktype, smoothness, mtb_scale, sac_scale)):
            return  # inferred at route time; recoverable from Valhalla's default
        self.tagged_ways += 1
        self.rows.append((w.id, surface, tracktype, smoothness, mtb_scale, sac_scale))
        if len(self.rows) >= BATCH:
            self._flush()

    def _flush(self):
        if not self.rows:
            return
        self.cur.executemany(
            "INSERT OR REPLACE INTO surface_provenance"
            " (way_id, surface, tracktype, smoothness, mtb_scale, sac_scale)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            self.rows,
        )
        self.rows.clear()


def build(pbf_path, out_path, data_version, extract_sha256):
    conn = sqlite3.connect(out_path)
    conn.execute("PRAGMA journal_mode = OFF")     # bulk build, rebuilt from scratch
    conn.execute("PRAGMA synchronous = OFF")
    # way_id as INTEGER PRIMARY KEY aliases rowid -> the point-lookup index the
    # #10 join needs, with no secondary index to maintain.
    conn.execute("DROP TABLE IF EXISTS surface_provenance")
    conn.execute(
        "CREATE TABLE surface_provenance ("
        " way_id INTEGER PRIMARY KEY,"
        " surface TEXT,"
        " tracktype TEXT,"
        " smoothness TEXT,"
        " mtb_scale TEXT,"
        " sac_scale TEXT)"
    )
    conn.execute("DROP TABLE IF EXISTS meta")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

    handler = SurfaceHandler(conn)
    # locations=False: we only need way tags + id, not geometry -> far less memory.
    handler.apply_file(pbf_path, locations=False)
    handler._flush()

    meta = {
        "schema": "surface-provenance/1",
        "data_version": data_version or "unknown",
        "extract_sha256": extract_sha256 or "unknown",
        "highway_ways": str(handler.highway_ways),
        "tagged_ways": str(handler.tagged_ways),
        "osmium_version": getattr(osmium.version, "pyosmium_release", "unknown"),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "build_surface_sidecar.py (gpxplore-pipeline#11)",
    }
    conn.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     list(meta.items()))
    conn.commit()
    conn.execute("VACUUM")
    conn.close()

    tagged_pct = (100.0 * handler.tagged_ways / handler.highway_ways
                  if handler.highway_ways else 0.0)
    print(f"[sidecar] highway ways: {handler.highway_ways:,}")
    print(f"[sidecar] tagged (stored): {handler.tagged_ways:,} ({tagged_pct:.1f}%)")
    print(f"[sidecar] data_version: {meta['data_version']}")
    print(f"[sidecar] wrote {out_path}")
    # Emit the numbers the manifest step scrapes (stable, parseable).
    print(f"SIDECAR_HIGHWAY_WAYS={handler.highway_ways}")
    print(f"SIDECAR_TAGGED_WAYS={handler.tagged_ways}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pbf", help="input OSM .pbf extract (same one used for tiles)")
    ap.add_argument("out", help="output sidecar SQLite path")
    ap.add_argument("--data-version", default="",
                    help="us-west-YYMMDD; must match tile_manifest.json")
    ap.add_argument("--extract-sha256", default="",
                    help="sha256 of the .pbf, for provenance cross-check")
    args = ap.parse_args(argv)
    build(args.pbf, args.out, args.data_version, args.extract_sha256)
    return 0


if __name__ == "__main__":
    sys.exit(main())
