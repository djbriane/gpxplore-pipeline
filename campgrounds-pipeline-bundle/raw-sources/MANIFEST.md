# Raw Source Snapshots

These are the exact raw files the normalizers in `scripts/` were run against
to produce every processed output referenced elsewhere in this bundle. They
are included because upstream portals for these datasets appear to refresh
on the order of **years, not months** — bundling them avoids re-pulling from
source just to get the pipeline running or tested.

| File | Source (see `SOURCES.md` for full detail) | Size | Snapshot pulled | SHA-256 |
|---|---|---|---|---|
| `Recreation_Sites_INFRA.csv` | USFS National Recreation Sites (INFRA) | 25 MB | 2026-05-20 | `cdbdc4117d854d97fd0a2d2bd9fef8bbf0c99f8cfe5bb752a6f32dcbd8d5fa0d` |
| `BLM_National_Recreation_Site_Points_-2581447096637901266.csv` | BLM National Recreation Site Points | 2.4 MB | 2026-05-20 | `b3b337ce8ae1860fa88c5fd764b581bbaf12779c663238dc39b03d70ab166a16` |
| `FWPLND_STATEPARKS_FACILITIES_PTS_7905798971970118109.csv` | Montana FWP State Parks facilities | 440 KB | 2026-05-20 | `693052ae10ffafdaa05126d93c8defeb1b6aba9fba54460bf7941dc3393aec70` |
| `IDPR_Parks_and_Facilities.csv` | Idaho Parks & Facilities | 22 KB | 2026-05-20 | `af38748bafc7a85a52340fcf56ac37a7d8dcb2d6d60cf14105b885a0334df446` |
| `co_campgrounds.geojson` | Colorado campgrounds (via `scripts/download_co_campgrounds.py`) | 193 KB | 2026-05-20 | `cc41bb4d969e08684721e27ae693bc4c31b6f1973a5b984312032cf45d850f6c` |
| `wy_camping.geojson` | Wyoming State Parks Camping points (WYSPHST_pts/MapServer/1) | 6 KB | 2026-07-01 | `5766734037209e084b0f05733727f3066f700bff35fa89406d1c0ab31960ea85` |
| `wa_campsites.geojson` | Washington State Parks Campsites (Campsites/FeatureServer/78) | 2.3 MB | 2026-07-01 | `762ac5b94e3b695ea1e725f5619cd115ab26dce37bc70c4f32143e239cf28e21` |
| `ca_campgrounds.geojson` | California State Parks Campgrounds (Campgrounds/FeatureServer/0) — COPYRIGHT CA State Parks, review allowed uses | 228 KB | 2026-07-01 | `b57730429bc57d3bbc787bbc88cc2a88dfc1f832942f7afb163f318fc50a28d7` |
| `or_state_parks.geojson` | Oregon State Parks boundaries (Oregon_State_Parks/FeatureServer/0) — **simplified geometry** (maxAllowableOffset=0.001; only centroids used) | 284 KB | 2026-07-01 | `0d3be4ee22a68272909aef6a3c46e0f1544496e95035b9d331701f9d5fa634d3` |
| `az_parks.geojson` | ParksInArizona point locations (ParksInArizona/FeatureServer/0; adapter filters to non-federal campgrounds) | 2.7 MB | 2026-07-01 | `620604574e3fba04b35ed6a46d8dcf867c84c827b716c3a9fb8d490da68d11d5` |
| `bc_rec_sites.geojson` | BC Recreation Sites and Trails (RecSitesReservesInterpForests point view; adapter filters to campgrounds with DEFINED_CAMPSITES>0) | 3.0 MB | 2026-07-01 | `935e41b62e847bc2ca3b2d8aae4ab23101d5eb8259264dfa8e458a4ee0e2d848` |

## Why checksums are recorded here

`ARCHITECTURE.md` (§4.2, Fetch adapters) calls for every fetch to record a
content hash so drift can be detected across runs. These are the baseline
hashes for the very first snapshot — the pipeline's own `fetch` stage should
compare against these (or its own prior run's hashes) to know whether an
upstream source has actually changed before spending time re-normalizing it.

## Refresh expectation

Anecdotally these sources seem to update on a ~3-year cadence rather than
monthly/quarterly — this is **not** independently verified against each
portal's changelog, just an observed pattern. The pipeline's `validate`
stage diff report (see `ARCHITECTURE.md` §4.5) is the mechanism that should
actually confirm this over time, rather than hard-coding a 3-year assumption
into any scheduling logic.

## Regenerating

If/when you do re-pull from source, drop the new files in here (same
filenames or updated with a new suffix) and update this manifest's snapshot
date + checksums. `SOURCES.md` has the portal links.
