# Source Data Registry

One row per upstream dataset the pipeline needs to ingest. "Automatable today"
means an ArcGIS/REST-style endpoint is known to exist and has already been
scripted at least once (see `scripts/download_co_campgrounds.py` as the
working template for that pattern).

**Raw snapshots of sources #1–#5 are bundled in [`raw-sources/`](raw-sources/)**
(see [`raw-sources/MANIFEST.md`](raw-sources/MANIFEST.md) for exact file,
snapshot date, and checksum) — these datasets appear to refresh on a ~3-year
cadence, so re-pulling from source before building/testing the pipeline
should not be necessary.

| # | Dataset | Agency / Region | Format | Access today | Automatable? | Bundled snapshot | Notes |
|---|---------|------------------|--------|---------------|---------------|----------------------|-------|
| 1 | USFS National Recreation Sites (INFRA) | US Forest Service, national | CSV export | Manual download from [data.fs.usda.gov](https://data.fs.usda.gov/) | **Unknown — research task.** No script exists yet; investigate whether INFRA is also published via an ArcGIS FeatureServer/MapServer (most USDA open-data layers are) | [`raw-sources/Recreation_Sites_INFRA.csv`](raw-sources/Recreation_Sites_INFRA.csv) (25 MB) | ~32K rows, ~4.2–16K kept depending on filter flags |
| 2 | BLM National Recreation Site Points | Bureau of Land Management, national | CSV export | Manual download from [BLM National GEOMG hub](https://gbp-blm-egis.hub.arcgis.com/) | **Likely yes** — ArcGIS Hub items are usually backed by a FeatureServer; same pattern as #5 below | [`raw-sources/BLM_National_Recreation_Site_Points_-2581447096637901266.csv`](raw-sources/BLM_National_Recreation_Site_Points_-2581447096637901266.csv) (2.4 MB) | ~9.5K rows, ~2K–2.8K kept |
| 3 | Montana FWP State Parks facilities | Montana, state parks | CSV export | Manual download, Montana State Library / FWP GIS layer | **Likely yes** — coordinates are Web Mercator (EPSG:3857), consistent with an ArcGIS-hosted `x`/`y` FeatureServer export | [`raw-sources/FWPLND_STATEPARKS_FACILITIES_PTS_7905798971970118109.csv`](raw-sources/FWPLND_STATEPARKS_FACILITIES_PTS_7905798971970118109.csv) (440 KB) | 2,131 rows; ~821 kept as camp-related after keyword filter |
| 4 | Idaho Parks & Facilities | Idaho Dept. of Parks & Recreation | CSV export | Manual download from ArcGIS Hub (`services1.arcgis.com/CNPdEkvnGl65jCX8/.../IDPR_Parks_and_Facilities`) | **Yes** — FeatureServer URL is visible in the CSV's own `pic_url`/`thumb_url` fields; same query pattern as #5 | [`raw-sources/IDPR_Parks_and_Facilities.csv`](raw-sources/IDPR_Parks_and_Facilities.csv) (22 KB) | 54 rows (parks, not individual facilities); coordinates Web Mercator |
| 5 | Colorado campgrounds | Colorado Parks & Wildlife (via NDIS Fishing Atlas) | Live ArcGIS MapServer query | **Already scripted** — `scripts/download_co_campgrounds.py` pages a `FishingAtlas_Base_Map/MapServer/44/query` endpoint and writes GeoJSON directly | Yes (done) | [`raw-sources/co_campgrounds.geojson`](raw-sources/co_campgrounds.geojson) (193 KB) | 259 features; this script is the reference template for #2–#4 |
| 6 | OpenStreetMap camp_site / caravan_site | Global, community-mapped | Live Overpass API | Not part of this batch pipeline — served live via a Cloudflare Worker proxy per [`osm-campgrounds-overlay--planned.md`](reference/osm-campgrounds-overlay--planned.md) | N/A (live, viewport-driven, no offline snapshot) | — | Different architecture: no static file, ODbL attribution required |
| 7 | Recreation.gov (RIDB) | Federal reservation system | Live REST API | Already proxied at `apps/planner/src/routes/api/ridb.ts` | N/A (live) | — | Used for dedup (`nrrs_id`) and the "reservable" P2 tier, not for static ingestion |

Exact snapshot dates and checksums for #1–#5: [`raw-sources/MANIFEST.md`](raw-sources/MANIFEST.md).

## License / attribution notes

- USFS, BLM, and state-park sources are US government / public agency open
  data — generally free to use, but confirm each portal's specific terms
  before automating bulk re-download (rate limits, required attribution).
- OSM data is **ODbL** — different obligations (attribution, share-alike on
  derivative *databases*), already scoped separately in the OSM overlay spec.
- Recreation.gov data is accessed live via RIDB and is not stored/republished
  by this pipeline.

## Refresh cadence (anecdotal, not verified)

None of sources #1–#4 have a *documented* update cadence on their portals.
Anecdotally they seem to refresh on the order of **years, not months** —
which is why raw snapshots are simply bundled rather than re-fetched. This
is not independently confirmed against each portal's own changelog. Part of
the pipeline's job is to **detect** drift (record count deltas, schema
changes) each run rather than hard-code a 3-year assumption into scheduling.
