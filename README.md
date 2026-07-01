# Campgrounds Ingestion Pipeline

A repeatable, versioned, testable pipeline that turns raw public-land campground
data (USFS, BLM, and state parks for MT / ID / CO) into the compact
`CampRecord[]` JSON files the `gpx-route-planner` app loads.

It replaces the old one-script-at-a-time manual process with six independent,
individually runnable stages driven by a single source registry:

```
fetch → normalize → merge → validate → compact → publish
```

- **Stdlib only.** No third-party Python dependencies (no `requests`, no
  `jsonschema`, no `pytest`). Python 3.10+.
- **Offline-first.** Every stage runs against the checksummed raw snapshots in
  `campgrounds-pipeline-bundle/raw-sources/`, so you can build and test the
  whole thing with no network access.
- **Review-gated.** `publish` writes a local reviewable artifact by default and
  only touches an external target when you pass `--confirm`.
- **The frozen contracts are untouched.** `CampRecord` key names and the
  `RES_MAP` / `RES_CONFIDENCE` mapping are ported verbatim. No edge-KV API and
  no app UI code is implemented here.

## Quickstart

```bash
make pipeline        # fetch(offline) → normalize → merge → validate → compact
make test            # run the unittest suite
```

Then review the outputs before publishing:

- `data/reports/<date>/validation.md` — schema/bounds/dupe checks + diff report
- `data/compact/<date>/*.json` — the app-facing `CampRecord[]` files

To stage into the app repo (nothing is written without `--confirm`):

```bash
# dry run: shows what would change
make publish TARGET=/path/to/gpx-route-planner/apps/planner/public/data
# actually write:
make publish-confirm TARGET=/path/to/gpx-route-planner/apps/planner/public/data
```

### Publishing to gpxplore-web and gpxplore-ios

Both client repos are usually checked out as siblings of this one
(`../gpxplore-web`, `../gpxplore-ios`). `gpxplore-web` loads this pipeline's
compact JSON directly; `gpxplore-ios` bundles a gzipped marker/detail snapshot
that `pipeline/ios_snapshot.py` builds *in this repo*, directly from the same
compact `CampRecord[]` output (a Python port of gpxplore-web's now-retired
`build-ios-campground-snapshot.mjs` - see that module's docstring for the
ported-behavior list). `make publish-downstream` drives both steps + the copy
into `gpxplore-ios`, in the right order, with no Node/npm dependency:

```bash
# just the snapshot, locally, no other repos touched
make ios-snapshot

# dry run: shows byte-size deltas for every file in both client repos, writes nothing
make publish-downstream

# actually write into gpxplore-web/apps/planner/public/data and
# gpxplore-ios/gpxplore/Resources/Campgrounds
make publish-downstream CONFIRM=1

# one-liner: run the full pipeline, then publish downstream
make publish-all CONFIRM=1
```

Override the sibling paths with `WEB_REPO=`/`IOS_REPO=` if your checkouts
live elsewhere. After a confirmed run, review and open a PR in each of the
two client repos — this script does not commit or push anything itself.

`ios_snapshot.py`'s `tier_for()` is a manually-kept-in-sync copy of
`packages/route-components/src/lib/campgroundShared.ts::tierFor` in
gpxplore-web (the source of truth for tier semantics), same as it was in the
retired `.mjs` script. If that function's rules change, update this by hand.

## Stages

| Stage | Command | What it does |
|---|---|---|
| fetch | `make fetch` | Copies the checksummed offline snapshot into `data/raw/<id>/` and writes a fetch manifest (origin, timestamp, sha256). `make fetch-live` pulls from a confirmed ArcGIS endpoint instead. |
| normalize | `make normalize` | Runs each source's adapter → canonical GeoJSON in `data/processed/<id>/`. |
| merge | `make merge` | Concatenates all sources into one dated snapshot `data/merged/<date>/merged.geojson`. |
| validate | `make validate` | Schema, coordinate-bounds, and exact-duplicate checks (hard, non-zero exit on failure) plus an id-reuse warning and a diff-vs-previous-snapshot report. |
| compact | `make compact` | Builds `usfs-campgrounds.json`, `blm-campgrounds.json`, and `state-campgrounds.json`, validating each against `schema/camp-record.schema.json`. |
| publish | `make publish` | Writes the reviewable artifact to `data/publish/<date>/`; `--confirm` copies into an external `TARGET`. |
| ios-snapshot | `make ios-snapshot` | Builds `gpxplore-ios`'s gzipped `campground-marker-index.json.gz` / `campground-detail.json.gz` from compact output, into `data/ios-snapshot/<date>/`. |
| publish-downstream | `make publish-downstream` | Publishes into `gpxplore-web`, builds the iOS snapshot, and copies the result into `gpxplore-ios`. `CONFIRM=1` to write; see below. |

Every stage also has a direct CLI form, e.g. `python3 -m pipeline.cli validate --snapshot 2026-07-01`. Run `python3 -m pipeline.cli <stage> -h` for options. Per-source runs are supported where it makes sense: `make normalize SOURCE=mt`.

## Repository layout

```
registry.json            # one entry per source (the thing you edit to add data)
schema/                  # canonical (internal) + camp-record (frozen app-facing) schemas
pipeline/
  common.py              # shared helpers: hashing, reprojection, classifiers, schema validator, rollup
  registry.py            # loads/validates registry.json
  fetch/                 # arcgis paginator + manual-file (offline) adapters
  normalize/             # one adapter module per source (usfs, blm, mt, id_, co)
    state_base.py        # shared canonical builder for the state adapters
  merge.py validate.py compact.py publish.py ios_snapshot.py cli.py
  overrides/id_no_camping.json   # hand-curated Idaho day-use deny-list
tests/                   # unittest suite (adapters, compact golden file, validate, common, ios snapshot)
data/                    # all generated output (gitignored)
campgrounds-pipeline-bundle/     # original reference material + raw snapshots
scripts/publish_downstream.sh   # cross-repo orchestration: gpxplore-web + gpxplore-ios
```

## Adding a new source

1. **Add a registry entry** to `registry.json` under `sources`:

   ```json
   {
     "id": "wy",
     "source_tag": "wy_state_parks",
     "adapter": "wy",
     "label": "Wyoming State Parks",
     "fetch": {
       "type": "arcgis",
       "offline_path": "campgrounds-pipeline-bundle/raw-sources/wy_parks.geojson",
       "offline_format": "geojson",
       "offline_sha256": "…",
       "live": { "url": "https://…/FeatureServer/0/query", "format": "geojson", "page_size": 1000, "confirmed": false }
     }
   }
   ```

2. **Reuse an adapter or add one.** If the raw shape matches an existing source,
   point `adapter` at it. Otherwise add `pipeline/normalize/wy.py` exposing:

   ```python
   def normalize(raw_path, snapshot, source_tag) -> list[dict]:
       ...  # return canonical GeoJSON Features (schema/canonical-campground.schema.json)
   ```

   State-style sources should build properties via
   `state_base.canonical_properties(...)`; use `common.rollup_sites(...)` if the
   raw data is one row per campsite. Register the module in
   `pipeline/normalize/__init__.py`'s `ADAPTERS` dict, and add it to compact's
   `STATE_SOURCES` if it's a state-park layer.

3. **Add a small test** with a fixture row → expected canonical output (see
   `tests/test_adapters.py`).

That's it — `merge`, `validate`, `compact`, and `publish` pick the new source up
automatically from the registry.

## Live fetch

Offline is the default. Sources with a confirmed live ArcGIS endpoint can be
fetched with `make fetch-live` (or `--live`):

- **USFS** (`EDW_InfraRecreationSites_01/MapServer/0`), **Idaho**
  (`IDPR_Parks_and_Facilities/FeatureServer/0`), and **Colorado** (Fishing
  Atlas layer 44) are confirmed.
- **BLM** (`BLM_Natl_Recreation_Offline/MapServer/2`) is confirmed via
  `make blm-verify` (live count 9483 vs snapshot 9481; field aliases match). The
  live layer uses machine field names (`FET_SUBTYPE`, `LAT`, …) which the BLM
  adapter maps back to the alias names (`blm.LIVE_FIELD_MAP`).
- **Montana** has no live layer exposing the granular facility-points table, so
  it stays offline/manual-file for now.

`make blm-verify` re-checks the BLM endpoint (row count + field aliases) before
trusting live mode.

## Intentional behavior changes vs. the old scripts

Everything is a like-for-like port of the reference scripts **except** these two
deliberate fixes (both surfaced in the validate diff / compact summary):

1. **MT/ID campground-vs-campsite.** Montana previously emitted one marker per
   *campsite* (e.g. 45 markers for one campground) and swept in non-camping
   infrastructure by name; Idaho emitted trails and a banner row. Now MT rolls
   individual `Campsite` rows into their parent campground (recording the site
   count as capacity), keeps `Backcountry Camp` sites individual, and drops
   infrastructure; Idaho drops trail/parkway/banner rows and applies a curated
   deny-list. Result: MT ~821 → ~102 records, ID 27 → 20.
2. **USFS water/restroom flags.** The compact stage used to set `w=1`/`rt=1`
   whenever the free-text field was non-empty — so "No water is available" was
   shown as *water available*. It now reads the text semantically
   (`common.text_indicates_available`), so ~2,150 false water flags and ~500
   false restroom flags are removed. The detail text (`w_d`/`rt_d`) is still
   shown verbatim; only the boolean flag changed.

## Known limitations

- **State-park reservation tiers** use a cruder free-text keyword scan than the
  USFS/BLM logic (state sources carry little structured text). Ported as-is.
- **Idaho camping presence** cannot be confirmed from the source alone; the
  `pipeline/overrides/id_no_camping.json` deny-list is hand-maintained and should
  be reviewed on each refresh. A properly-typed Idaho "Campgrounds" layer would
  remove the need for it.
- **Montana** is offline-only until a granular live layer is found.
