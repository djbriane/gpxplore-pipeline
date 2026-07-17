# PROTOTYPE — Valhalla local tile build (throwaway)

> **This is a throwaway measurement spike, not pipeline code.** It answers wayfinder
> ticket [djbriane/gpxplore-pipeline#5](https://github.com/djbriane/gpxplore-pipeline/issues/5)
> and should be **deleted once the numbers are captured** in the ticket / handoff spec.

## Question

Can we build a single western-states Valhalla tile set locally in Docker, and what does
it cost in **build time, disk, and RAM**? Also: does a basic route request work against
the freshly built tiles?

## What it measures

| Metric | How |
|---|---|
| Extract size | `stat` on the downloaded `.osm.pbf` |
| Build wall-time | `date` around `valhalla_build_tiles` |
| Tile-tree size on disk | `du -sb` on the tile dir |
| Peak build memory | cgroup v2 `memory.peak` (upper bound, incl. page cache) |
| Serving memory | `VmHWM` of the `valhalla_service` process after a route |
| Route works? | one `/route` request against the built tiles |

## Run it

```bash
# Smoke test on one state first (fast, ~minutes, low RAM):
REGION=colorado \
EXTRACT_URL=https://download.geofabrik.de/north-america/us/colorado-latest.osm.pbf \
  ./build.sh

# Full western-states target (~3.37 GB extract; the real #5 measurement):
./build.sh            # defaults to REGION=us-west
```

Results are appended to `results-<region>.md`. Working data lands in `_data/` (gitignored —
safe to wipe). Requires Docker Desktop running.

## Verdict

_Fill in once measured, then delete this prototype:_

- [ ] Full us-west tile build feasible on a 16 GB machine? (build RAM vs Docker VM limit)
- [ ] Build time acceptable for manual-cadence refresh?
- [ ] Serving memory → informs the hosting decision (#8)
- [ ] Basic route request succeeds
