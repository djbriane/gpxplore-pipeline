# Campgrounds Data Pipeline Bundle

A self-contained snapshot of the campground data work done so far (USFS,
BLM, Montana, Idaho, Colorado), packaged so a coding agent can build a
repeatable ingestion pipeline from it going forward.

## Contents

| File / dir | What it is |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | As-built pipeline diagram, target architecture proposal, known gaps, and open decisions. **Read this first.** |
| [`SOURCES.md`](SOURCES.md) | Registry of the 5 upstream data sources: URLs, formats, what's automatable today vs. manual. |
| [`AGENT_PROMPT.md`](AGENT_PROMPT.md) | Ready-to-paste prompt for a coding agent to build the pipeline. Points at everything else in this bundle. |
| `schema/canonical-campground.schema.json` | JSON Schema for the internal normalized record (Stage 1 output). |
| `schema/camp-record.schema.json` | JSON Schema for the compact, app-facing `CampRecord` (frozen contract — do not change). |
| `samples/normalized-sample.geojson` | 4 example records in the canonical schema (2× USFS campground, 1× BLM, 1× USFS dispersed). |
| `samples/camp-record-sample.json` | The same 4 records after compaction to the app's short-key shape. |
| `scripts/` | Frozen copies of the current reference implementation — the scripts a rebuilt pipeline should generalize or replace. |
| `raw-sources/` | **Full raw snapshots** of all 5 upstream CSVs/GeoJSON (~28 MB total) — see `raw-sources/MANIFEST.md` for exact files, snapshot date, and checksums. Included so the pipeline can be built and test-run without re-fetching from source (these datasets appear to refresh on a ~3-year cadence). |

## How to use this

1. Skim `ARCHITECTURE.md` to understand what exists today and where the
   seams are.
2. Open a new agent session and paste the contents of `AGENT_PROMPT.md`.
3. The agent should read the rest of this bundle before proposing an
   implementation plan (the prompt tells it to).

## Where the "live" code actually lives

This bundle contains **copies**, frozen at the time of packaging, for
portability. The real, currently-in-use versions are:

- `gpx/filter_usfs_campgrounds.py`
- `gpx/filter_blm_campgrounds.py`
- `gpx/build_state_campgrounds_geojson.py`
- `gpx/scripts/download_co_campgrounds.py`
- `gpx/gpx-route-planner/apps/planner/scripts/build-campgrounds.mjs`

A rebuilt pipeline should ultimately replace these (or the ones in `gpx/`,
at least — `build-campgrounds.mjs` lives in the app repo and is the seam
between this data repo and the app).

## Relevant specs from the app repo (copied into `reference/` so this bundle is self-contained)

- [`reference/campground-poi-design--ref.md`](reference/campground-poi-design--ref.md) — locks the marker-tier design that depends on the `reservation_tier` → `r`/`rc` mapping.
- [`reference/campgrounds-edge-api--planned.md`](reference/campgrounds-edge-api--planned.md) — the planned Stage 5 (Cloudflare KV bbox-tiled delivery), explicitly **out of scope** for the pipeline this bundle asks for, but the `publish` stage should leave room for it.
- [`reference/osm-campgrounds-overlay--planned.md`](reference/osm-campgrounds-overlay--planned.md) — a *different* data source (live OSM Overpass) with its own architecture; not part of this batch pipeline.
- [`reference/campgroundShared.ts`](reference/campgroundShared.ts) — the live `CampRecord` type + `tierFor()` logic that `schema/camp-record.schema.json` was reverse-engineered from.
- [`reference/federalCampgrounds.ts`](reference/federalCampgrounds.ts) — the live loader/renderer that consumes the compacted JSON files; shows exactly how `CampRecord[]` is fetched and used today.

These are frozen snapshots, copied at packaging time — not symlinks. If the
app repo changes these files later, re-copy them into `reference/` before
handing this bundle to an agent again.
