# POI Layers Ingestion Pipeline (historic sites, mines, lookouts, ghost towns)

Extends the campgrounds pipeline pattern to a second family of map layers: **rider
points-of-interest** — historic sites, interpretive sites/viewpoints, fire lookouts, mines, and
(v2) ghost towns and named passes. Same repeatable, versioned, stdlib-only, offline-first,
review-gated machinery (`fetch → normalize → merge → validate → compact → publish`), driven by
`registry.json` + per-source normalize adapters, producing compact app-facing records and a
gzipped iOS snapshot.

## Why / context

The iOS Day Narratives work hit a wall trying to *algorithmically* decide which historic
places along a corridor are "interesting" (it pulled ghost towns 100–170 mi off-route). The
better division of labor: **the pipeline publishes clean POI layers → the user browses them on
the map and adds the ones they want to their route (the shipped `addCampgroundPlace` pattern) →
the iOS narrative pipeline enriches the *promoted* places deeply.** Let the human decide what is
interesting; we make it rich.

This spec covers only the **data pipeline** (this repo). Downstream consumers are separate:

- **Web review first** — the `gpxplore-web` planner loads the compact JSON as a review layer so
  the data can be validated on a fast-iterating surface *before* committing to iOS.
- **iOS second** — bundles the gzipped POI snapshot as promotable map layers + add-to-route,
  then narrative enrichment. Specced separately after the web review
  (`../gpxplore-ios/specs/ios-companion-day-narratives-enrichment-adapters--planned.md`, to be
  rewritten as the iOS consumer spec).

## Scope

- **Geography:** match the campground footprint's US states first (AZ/CA/CO/ID/MT/OR/WA/WY). **BC
  is deferred** — Canadian POIs need Canadian sources (Canadian Register of Historic Places,
  provincial GIS), out of v1.
- **Data lane (licensing):** US federal / state **public-domain government GIS only**, same
  posture as campgrounds. Explicitly:
  - ✅ NPS (NRHP), USGS (MRDS, GNIS), USFS (INFRA), BLM, FHWA, state GIS — public domain, bundle
    freely.
  - ⛔ **NHLR** (Forest Fire Lookout Association terms) and **HMdb** (licensing) — **not** in the
    pipeline. INFRA is expected to supply lookouts, making NHLR unnecessary.
  - ⚠️ **OpenStreetMap** (ODbL, attribution + share-alike) — excluded from v1; a different
    license than the current data. Only add if the ODbL share-alike obligation on the published
    output is accepted.

## Layers (v1 → later)

Each POI carries a `t` category that maps to an iOS map-layer toggle + glyph.

| `t` category | Source(s) | Phase | Rider value |
| --- | --- | --- | --- |
| `historic` | NRHP; USFS INFRA (HISTORIC SITE) | v1 | Listed historic sites/districts, ranger stations |
| `interpretive` | USFS INFRA (INTERPRETIVE SITE) | v1 | Agency interpretive stops (corridor history) |
| `viewpoint` | USFS INFRA (VIEWPOINT/SCENIC) | v1 | Scenic overlooks |
| `lookout` | USFS INFRA (fire lookout / lookout cabin subtypes) | v1 | Fire towers — a top BDR draw |
| `mine` | USGS MRDS/USMIN | v1 | Old mines/mills/prospects — top BDR interest; pairs with Wikipedia |
| `ghost_town` | USGS GNIS (populated-place historical / locale) | v2 | Ghost towns / former settlements |
| `landmark` | USGS GNIS (summit/gap/pass) | v2 | Named passes and summits |
| `hot_spring` | USGS GNIS (spring subset) | later | Hot springs |
| `byway` | BLM Backcountry / FHWA Scenic Byways | later | Route **lines** (different render) |

**Headline cheap win:** the USFS INFRA adapter (`pipeline/normalize/usfs.py`) already fetches the
**full** Recreation Sites dataset and filters to campground subtypes only
(`ALLOWED_SUBTYPES = CAMPGROUND / GROUP CAMPGROUND / HORSE CAMP / CAMPING AREA / …`). Interpretive
sites, historic sites, viewpoints, trailheads, cabins, and **fire lookouts** are in the *same
raw snapshot*, discarded today. The `usfs_infra_poi` layer is therefore a new subtype filter on
data already ingested — the cheapest possible first layer, and it proves the POIRecord shape +
web-review loop end-to-end.

## Data model

Two-schema pattern, mirroring campgrounds:

### Canonical normalized POI (intermediate, per-adapter output)

GeoJSON `Feature.properties` superset produced by every adapter before merge. Fields not
applicable to a source are null/omitted, never fabricated.

```
source            provenance tag (nrhp, usfs_infra_poi, mrds, gnis)
site_id           source-native id
name              raw name
public_name       display name (falls back to name)
category          POI category → compact `t` (historic|interpretive|viewpoint|lookout|mine|ghost_town|landmark|hot_spring)
subtype_raw       source-native subtype string (INFRA site_subtype, MRDS dev_status, NRHP resource type)
state, county
lat, lon
elevation_ft
significant_year  NRHP listing date / built date, as sourced
ref_number        external reference (NRHP reference number)
commodity         MRDS commodities
dev_status        MRDS development status (producer/past producer/prospect/occurrence)
description       prose when the source has it (INFRA)
operated_by       administering agency/unit
url               detail/source URL
snapshot_date, ingest_hash
```

### Compact POIRecord (app-facing, short keys — frozen contract like CampRecord)

```
required: i, n, t, y, x
  i   stable id, unique within a source file
  n   display name, title-cased if source was all-caps
  t   category enum (glyph + layer grouping): historic|interpretive|viewpoint|lookout|mine|ghost_town|landmark|hot_spring
  y   latitude, 5 decimals      x   longitude, 5 decimals
optional:
  src provenance (nrhp|usfs_infra|mrds|gnis)
  sub raw subtype for the detail drawer
  u   detail/source URL
  el  elevation, free text
  st  two-letter state
  yr  significant year/date (NRHP listing, built date)
  ref external reference (NRHP number)
  com commodity (mines)
  desc truncated prose description (maxLength ~1400, INFRA)
  op  operated/administered by
```

iOS snapshot marker fields = `{i, n, t, y, x}` (no reservation tier — POIs aren't reservable);
everything else is the detail payload. Reuse `pipeline/ios_snapshot.py`'s marker/detail-split +
base36 id + atomic detail-map approach.

## Sources & adapters

Add one `registry.json` entry per source + a `pipeline/normalize/<adapter>.py`. Confirm live
endpoints at build time (set `confirmed` like the BLM entry); ship offline snapshots as the
default.

- **`usfs_infra_poi`** — reuse the *existing* `Recreation_Sites_INFRA` raw snapshot (no new
  fetch). New adapter (or a `usfs.py` mode) with a POI subtype map → `historic` / `interpretive`
  / `viewpoint` / `lookout` / `trailhead`. Carries `description`, `operated_by`, `url` from INFRA.
- **`nrhp`** — NPS National Register spatial layer (ArcGIS FeatureServer; confirm exact endpoint
  at build). Fields → name, ref_number, significant_year (listing date), state/county,
  resource type → `subtype_raw`, coords. `category = historic`. No prose in point data (depth is
  an iOS-side, on-demand concern, not the pipeline's).
- **`mrds`** — USGS MRDS (mrdata.usgs.gov/mrds bulk download; offline snapshot). Fields → name,
  commodity, dev_status, coords. `category = mine`. **Quality floor:** keep named records with a
  meaningful `dev_status` (producer / past producer / mine); drop unnamed prospects/occurrences
  so the layer isn't map spam.
- **`gnis`** (v2) — USGS GNIS Domestic Names bulk file, state-scoped. Feature-class filter →
  `ghost_town` (historical populated place / locale / post-office heuristics), `landmark`
  (summit / gap / pass), `hot_spring` (spring + name heuristic). Note: ghost-town identification
  needs a documented heuristic (name/status flags); prototype and validate on the web review
  layer before trusting it.

## Merge / quality

- **Cross-source dedupe** (reuse the merge stage): an NRHP historic site that is also a USFS
  INFRA historic site, or an MRDS mine that is also a GNIS locale, must collapse to one record
  by name + proximity (same approach campgrounds already use). Keep the richest/most-authoritative
  (NRHP ref# + INFRA prose beat a bare GNIS point).
- **Quality floor** (the "specificity floor" idea, lighter — the human is the real filter):
  named records only; drop unnamed MRDS prospects and GNIS entries with no useful class.
- **Coordinate + bounds validation** as campgrounds; POIs out of the state bbox are dropped in
  `validate` with a report row.

## Outputs & downstream

- `data/compact/<date>/*.json` — compact `POIRecord[]`, per source or per category (decide in
  Phase 1; per-source is simplest and matches campgrounds).
- `data/reports/<date>/validation.md` — schema/bounds/dupe checks + diff vs previous snapshot.
- `pipeline/ios_snapshot.py` extended (or a sibling `ios_poi_snapshot.py`) → gzipped marker +
  detail snapshot for iOS, built from the same compact output.
- **Publish order (matches your iteration plan):** `publish` compact JSON → **web planner review
  layer** (validate the data on the fast surface) → only then build + copy the iOS snapshot.
  Review-gated: nothing external is written without `--confirm`.

## Build sequence

1. **Phase 1 — `usfs_infra_poi` re-filter.** New POI subtype map on the existing INFRA snapshot;
   define + freeze the compact `POIRecord` schema (`schema/poi-record.schema.json`) and canonical
   POI schema; wire the compact stage + a POI iOS-snapshot; publish to web as a review layer.
   Proves the whole loop with zero new fetch. **This is the milestone to build first.**
2. **Phase 2 — `nrhp` adapter.** New registry entry (ArcGIS fetch) + adapter; the `historic`
   layer gains listed sites beyond INFRA.
3. **Phase 3 — `mrds` adapter.** Mines, with the named/dev_status quality filter.
4. **Phase 4 (v2) — `gnis` adapter.** Ghost towns + passes; validate the ghost-town heuristic on
   the web layer before trusting it.
5. **Later:** byways (line geometry — new render), hot springs (GNIS spring subset + curated).

Each phase: fetch(offline) → normalize → merge → validate → compact → **web review** → iOS
snapshot. Each leaves the suite green.

## Test plan

Stdlib `unittest`, same as campgrounds:

- Per-adapter normalize tests over fixture rows (INFRA POI subtype mapping incl. lookout/historic/
  interpretive; NRHP field mapping + listing date; MRDS dev_status filter + commodity; GNIS
  feature-class → category + the ghost-town heuristic).
- Quality-floor tests (unnamed MRDS prospect dropped; unclassifiable GNIS dropped).
- Cross-source dedupe test (NRHP + INFRA same site → one record; richest kept).
- Compact key-contract test (only allowed short keys; 5-decimal coords; required fields present).
- Schema validation of `schema/poi-record.schema.json` against compact output.
- iOS-snapshot marker/detail split + reproducibility (same compact → same gzip).

## Invariants

- **Stdlib only, offline-first, review-gated** — unchanged from the campgrounds pipeline.
- **Public-domain government GIS only** (see Scope). No NHLR, no HMdb, no OSM in v1.
- **Never fabricate fields** — null/omit what a source lacks; prose is pass-through only.
- **Frozen compact contract** — `POIRecord` short keys don't change without updating the web +
  iOS consumers (mirror the `CampRecord` discipline).
- **The pipeline classifies location/category, not "interestingness"** — curation is the user's
  job downstream via add-to-route; the pipeline only enforces a light named/specificity floor.

## References

- This repo: `README.md`, `registry.json`, `schema/camp-record.schema.json`,
  `schema/canonical-campground.schema.json`, `pipeline/normalize/usfs.py` (the INFRA subtype
  filter to reuse), `pipeline/ios_snapshot.py` (marker/detail split to mirror).
- Downstream: `../gpxplore-web` (review layer), `../gpxplore-ios/specs/` — the Day Narratives
  fact-corpus spec (`ios-companion-day-narratives-fact-corpus--planned.md`) and the map
  environment-layers spec (`ios-companion-map-environment-layers--planned.md`, the
  `addCampgroundPlace` promote pattern this builds on).
