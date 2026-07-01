# Campgrounds Data Pipeline — Architecture (as-built + target)

This document describes how campground reference data currently flows from
raw government CSVs into the `gpx-route-planner` app, and proposes a target
architecture for making that flow **repeatable, versioned, and testable**
instead of ad hoc chat-driven script runs.

Two repos are involved:

- **`gpx/`** (this repo) — where raw sources land and get normalized/merged.
  Everything under `SOURCES.md`, `filter_*.py`, `build_state_campgrounds_geojson.py`.
- **`gpx/gpx-route-planner/`** — the consuming app. It has its own compaction
  step (`apps/planner/scripts/build-campgrounds.mjs`) that turns the merged
  GeoJSON into the small `CampRecord[]` files it actually ships to the browser.

---

## 1. Current pipeline (as-built)

```
┌─────────────────────────────────────────────────────────────────────┐
│ STAGE 0 — ACQUIRE (manual, mostly)                                  │
│                                                                       │
│  USFS INFRA CSV ──manual download──┐                                 │
│  BLM Rec Sites CSV ──manual download┤                                │
│  MT FWP CSV ──manual download───────┼──► repo root (gitignored)      │
│  ID IDPR CSV ──manual download──────┤                                │
│  CO campgrounds ──scripted (ArcGIS)─┘──► scripts/co_campgrounds.geojson │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STAGE 1 — NORMALIZE (Python, per-source, run by hand)               │
│                                                                       │
│  filter_usfs_campgrounds.py    → data/processed/usfs/*.{geojson,ndjson,stats.json} │
│  filter_blm_campgrounds.py     → data/processed/blm/*.{geojson,ndjson,stats.json}  │
│  build_state_campgrounds_geojson.py → data/processed/state/state-campgrounds.geojson │
│                                                                       │
│  Each adapter independently reimplements: CSV parsing, coordinate    │
│  validation, reservation-tier classification, content-hash ids,      │
│  GeoJSON writing, and a stats summary.                                │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STAGE 2 — MERGE (ad hoc, one-off script run in chat)                │
│                                                                       │
│  data/processed/federal-campgrounds.geojson  (USFS + BLM, full props)│
│  data/processed/state/state-campgrounds.geojson (MT + ID + CO)       │
│                                                                       │
│  No committed "merge" script exists yet — this has been done inline. │
└─────────────────────────────────────────────────────────────────────┘
                              │
                    (manual copy / /tmp/fed.geojson)
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STAGE 3 — COMPACT (Node, in the app repo)                            │
│                                                                       │
│  apps/planner/scripts/build-campgrounds.mjs                          │
│    reads a merged federal GeoJSON path (argv[2], defaults to         │
│    /tmp/fed.geojson) and emits SHORT-KEY records:                    │
│      public/data/usfs-campgrounds.json                               │
│      public/data/blm-campgrounds.json                                │
│    (state-campgrounds.json exists in public/data/ but there is       │
│     NO committed builder for it — currently a gap / manual step)     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STAGE 4 — SERVE + CONSUME (already built, do not change)             │
│                                                                       │
│  apps/planner/src/lib/federalCampgrounds.ts                          │
│    fetch("/data/<agency>-campgrounds.json") → in-memory cache        │
│  packages/route-components/.../campgroundShared.ts                   │
│    CampRecord type, tierFor(), filters                               │
│  RouteMapView / marker layer renders clusters, popups, drawer        │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼ (planned, not yet built)
┌─────────────────────────────────────────────────────────────────────┐
│ STAGE 5 — EDGE DELIVERY (spec'd, not implemented)                    │
│                                                                       │
│  specs/campgrounds-edge-api--planned.md:                             │
│    - Bucket records into a 1°×1° grid, store per-cell in Cloudflare  │
│      KV, versioned by publish date.                                  │
│    - GET /api/campgrounds?agency=&bbox= replaces the static files.   │
│    - apps/planner/scripts/tile-campgrounds.ts is the NAMED-BUT-      │
│      NOT-YET-WRITTEN publish tool for this stage.                    │
│    - Source JSON relocates from public/ to apps/planner/data/source/.│
└─────────────────────────────────────────────────────────────────────┘
```

### Why this matters for the pipeline design

Stage 5 is coming. Whatever repeatable pipeline gets built for Stages 0–3
should **not** hard-code "write to `public/data/*.json`" as its only publish
target — it should treat "compacted `CampRecord[]` per agency" as the
pipeline's output artifact, with *where that goes* (static files today,
Cloudflare KV cells tomorrow) as a pluggable last step. See §4.

---

## 2. Data contracts

Two schemas, two audiences:

| Schema | File | Audience | Stability |
|---|---|---|---|
| **Canonical normalized record** | [`schema/canonical-campground.schema.json`](schema/canonical-campground.schema.json) | Internal to this pipeline (Stage 1 output / Stage 2 input) | Can evolve freely — nothing outside the pipeline reads it directly |
| **CampRecord (compact)** | [`schema/camp-record.schema.json`](schema/camp-record.schema.json) | The app's runtime (`campgroundShared.ts`, marker layer, drawer, GPX export) | **Frozen contract.** Changing key names or removing fields requires updating `packages/route-components` and everything that consumes it |

The canonical schema is a superset — every adapter (USFS, BLM, MT, ID, CO)
populates the fields it has and leaves the rest null/omitted. The
`reservation_tier` enum (`definite_fcfs` / `likely_fcfs` / `reservable` /
`mixed`) is the one taxonomy **every adapter must agree on**, because it's
what ultimately drives `r`/`rc` in `CampRecord` and therefore the P1/P2/P3
marker tier in the app (see [`reference/campground-poi-design--ref.md`](reference/campground-poi-design--ref.md)).

Sample files (illustrative, not exhaustive):

- [`samples/normalized-sample.geojson`](samples/normalized-sample.geojson) — 4 canonical records (USFS × 2, BLM × 1, USFS dispersed × 1)
- [`samples/camp-record-sample.json`](samples/camp-record-sample.json) — the same 4 records after compaction

### Reservation tier classification (must stay consistent across adapters)

| Tier | USFS logic | BLM logic | State-park logic (current, heuristic) |
|---|---|---|---|
| `definite_fcfs` | Text explicitly says "first come first served" | Subtype contains "Non Reservable" or is "Undeveloped" | Text/keyword match on `first come`, `non reservable`, `walk-in only`, etc. |
| `likely_fcfs` | No `nrrs_id`, no reservation language | Subtype is bare "Campground" | No FCFS or reservable keyword found (default) |
| `reservable` | Has `nrrs_id` or reservation text | Subtype contains "Reservable" | Keyword match on `reserv`, `book online`, etc. |
| `mixed` | Text mentions both FCFS and reservable | *(not used — BLM subtype is unambiguous)* | Keyword match on both |

The state-park adapters (`build_state_campgrounds_geojson.py`) use a much
cruder keyword heuristic than USFS/BLM because MT/ID/CO source data has no
structured reservation field at all. **This is a known weak point** — flag it
in any pipeline redesign rather than silently trusting it.

---

## 3. Known gaps / pain points in the current (manual) process

1. **No fetch automation for 4 of 5 sources.** Only Colorado has a working
   scripted downloader (`scripts/download_co_campgrounds.py`, an ArcGIS
   REST paginator). USFS, BLM, MT, and ID CSVs are hand-downloaded with no
   record of *when* or *from what exact URL*.
2. **Duplicated normalization logic.** `filter_usfs_campgrounds.py`,
   `filter_blm_campgrounds.py`, and `build_state_campgrounds_geojson.py`
   each reimplement: CSV reading, lat/lon validation, content hashing,
   GeoJSON writing, and reservation-tier classification. A shared library
   would remove ~70% of the duplication.
3. **No committed merge step.** `data/processed/federal-campgrounds.geojson`
   was produced by an inline script during a chat session, not a script in
   the repo. Not reproducible without re-deriving it.
4. **Missing state compactor.** `apps/planner/scripts/build-campgrounds.mjs`
   only emits `usfs-campgrounds.json` and `blm-campgrounds.json`. How
   `public/data/state-campgrounds.json` gets built is undocumented.
5. **No validation step.** Nothing checks for: out-of-range coordinates,
   duplicate ids within an agency, required-field presence, or unexpected
   swings in record count between runs (which would catch upstream schema
   changes early).
6. **No versioning/changelog.** Every run overwrites the previous output.
   There's no way to answer "what changed since the last publish?" or roll
   back a bad ingest.
7. **Manual handoff between repos.** Getting normalized data from `gpx/` into
   `gpx-route-planner/apps/planner/public/data/` today means copying a file
   to `/tmp/fed.geojson` and running a script by hand.
8. **No tests.** None of the five scripts have unit tests. A silent
   upstream column rename could produce an empty or garbage output with no
   signal.
9. **No scheduling/CI.** Everything so far has been run interactively.

---

## 4. Target architecture (proposed)

A single pipeline with five composable, independently runnable stages, driven
by one **source registry** file instead of one script per source:

```
                 ┌───────────────┐
                 │ source registry│  (config: one entry per dataset —
                 │  (config file) │   name, fetch adapter, normalize adapter,
                 └───────┬────────┘   output slot)
                         │
        ┌────────────────┼─────────────────┐
        ▼                ▼                 ▼
   ┌─────────┐      ┌─────────┐      ┌─────────┐
   │  fetch  │      │  fetch  │      │  fetch  │   ... one per source
   └────┬────┘      └────┬────┘      └────┬────┘
        ▼                ▼                 ▼
   ┌───────────┐   ┌───────────┐    ┌───────────┐
   │ normalize │   │ normalize │    │ normalize │   → canonical schema
   └─────┬─────┘   └─────┬─────┘    └─────┬─────┘     (schema/canonical-*.json)
         └────────────────┼─────────────────┘
                           ▼
                     ┌───────────┐
                     │   merge   │  → one versioned canonical dataset,
                     └─────┬─────┘     full provenance retained
                           ▼
                     ┌───────────┐
                     │ validate  │  → schema check, dedupe, bounds check,
                     └─────┬─────┘     diff vs previous snapshot → report
                           ▼
                     ┌───────────┐
                     │  compact  │  → CampRecord[] per agency
                     └─────┬─────┘     (schema/camp-record.schema.json)
                           ▼
                     ┌───────────┐
                     │  publish  │  → pluggable sink:
                     └───────────┘       (a) write static JSON to
                                             apps/planner/public/data/ (today)
                                          (b) tile + push to Cloudflare KV
                                             (once Stage 5 ships)
```

### 4.1 Source registry

One manifest (JSON/YAML/TS — agent's choice) listing every source with:

```
{
  "id": "usfs_infra",
  "label": "USFS National Recreation Sites (INFRA)",
  "fetch": { "adapter": "manual-file", "path": "Recreation_Sites_INFRA.csv" },
  "normalize": { "adapter": "usfs" },
  "reservationTaxonomy": "usfs-text-heuristic"
}
```

Adding a new state/agency should be "add one registry entry (+ implement the
adapter if new)," not "write a new end-to-end script."

### 4.2 Fetch adapters

- **ArcGIS-paginated** (generalize `download_co_campgrounds.py`): given a
  FeatureServer/MapServer query URL, page through `resultOffset` and write
  raw GeoJSON or CSV. Reuse this one adapter for BLM, MT, and ID once their
  ArcGIS endpoints are confirmed (see `SOURCES.md` — this is a research task,
  not an assumption).
- **Manual-file**: source has no known API; adapter just validates the
  expected file exists and records its mtime/checksum for the snapshot
  manifest, so at least *when it was last refreshed* is known.
- Every fetch adapter should record: source URL (if any), fetch timestamp,
  and a content hash of the raw file — this is the seed for drift detection.

### 4.3 Normalize adapters

Keep the existing per-source logic (it's already correct and tested by
production use) but factor out the shared pieces into a small internal
library: coordinate validation, `_safe_int`/`_safe_float`/`_clean_str`,
`ingest_hash` generation, reservation-tier keyword scanning, and GeoJSON
Feature construction. Each adapter becomes: read rows → map to canonical
schema → apply shared helpers. Output: one GeoJSON + one stats.json per
source, as today.

### 4.4 Merge

Combine all normalized sources into **one** versioned canonical dataset
(concatenate features, preserve every property, tag `snapshot_date`). This
replaces the ad hoc merge done in past chat sessions with a committed,
re-runnable script.

### 4.5 Validate (new)

Before anything is published:

- Schema-check every feature against `schema/canonical-campground.schema.json`.
- Coordinate bounds check (CONUS + AK + HI: lat 28.9–60.9, lon −149.9 to −70.8
  per `campgrounds-edge-api--planned.md`, or wider if new regions are added).
- Dedupe check: no two features share `(source, site_id)`.
- **Diff vs the previous snapshot**: record-count delta per source, newly
  appeared/disappeared `site_id`s, any source that dropped to near-zero
  records (likely a broken scraper/schema change, not real-world attrition).
- Fail loudly (non-zero exit, or open a PR flagged for human review — see
  §4.7) rather than silently publishing bad data, since this feeds curated
  reference data described as "the most exposed and most expensive-to-
  reproduce asset in the app."

### 4.6 Compact

Extend `build-campgrounds.mjs` (or its replacement) to:

- Take the merged canonical file as an explicit, versioned input (not
  `/tmp/fed.geojson`).
- Emit `usfs-campgrounds.json` and `blm-campgrounds.json` as today, **plus**
  the currently-missing `state-campgrounds.json` builder, using the same
  short-key mapping conventions (see `campMapping` logic already in the
  script for the pattern to extend).
- Validate its own output against `schema/camp-record.schema.json`.

### 4.7 Publish

Keep this as a **separate, swappable last step**:

- **Today**: copy compacted JSON into `apps/planner/public/data/`.
- **Once Stage 5 ships**: tile compacted records into 1°×1° cells and push to
  Cloudflare KV per `campgrounds-edge-api--planned.md`, bumping the
  `version` in the manifest atomically.
- Given this is curated, expensive-to-reproduce data, consider gating
  publish behind a human review step (e.g., the pipeline opens a PR with the
  validation diff report attached, and a person merges to actually publish)
  — at least until the pipeline has a track record.

---

## 5. Open decisions (flag these, don't silently assume)

The current implementation mixes Python (normalize) and Node (compact)
across two repos. A rebuild should explicitly decide, not drift into:

1. **Language/location** — keep the Python/Node split across two repos, or
   consolidate into one pipeline package (and if so, which repo owns it —
   `gpx/` as the data-engineering repo, or a new package inside
   `gpx-route-planner`)?
2. **USFS/BLM/MT/ID fetch automation** — confirm which of these actually
   have stable ArcGIS REST endpoints before assuming they can be automated
   like Colorado. This needs a short research spike, not a guess.
3. **Human-in-the-loop gate** — should every run auto-publish, or should
   validate produce a report that a person approves before compact/publish
   runs? Given the "curated reference data" framing in the edge-api spec,
   leaning toward review-gated is reasonable, but confirm with the user.
4. **Scheduling** — cron/GitHub Action on a fixed cadence, or manually
   triggered? Given upstream refresh cadence is unknown (see `SOURCES.md`),
   a monthly check-and-diff (not blind republish) is a reasonable default.
5. **Where compacted output lands** — stays static-file-based until Stage 5
   (edge API) is separately built, or should this pipeline also implement
   `apps/planner/scripts/tile-campgrounds.ts` as part of the same effort?
   Recommend treating that as explicitly out of scope unless asked — it's
   its own spec (`campgrounds-edge-api--planned.md`) with its own open
   decisions (cell size, `MAX_CELLS_PER_REQUEST`, light/heavy payload split).
6. **State-park adapter quality** — the MT/ID/CO reservation-tier heuristic
   is much weaker than USFS/BLM's. Worth improving, or acceptable as-is
   given state parks are a secondary layer?

---

## 6. What must NOT change without coordination

- `CampRecord` key names (`i`, `n`, `t`, `y`, `x`, `r`, `rc`, `f`, ...) —
  consumed directly by `packages/route-components/src/lib/campgroundShared.ts`,
  the marker layer, the detail drawer, and GPX export.
- The `reservation_tier` → `r`/`rc` mapping (`RES_MAP`, `RES_CONFIDENCE` in
  `build-campgrounds.mjs`) — this drives P1/P2/P3 marker tiering, a locked
  design decision in `campground-poi-design--ref.md`.
- The `source` values (`usfs_infra`, `blm_recreation`, ...) — used for
  provenance and badge display.
