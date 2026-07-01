# Prompt: Build a repeatable campgrounds data ingestion pipeline

Copy everything below the line into a fresh agent session (Plan mode
recommended for the first pass, since there are open architectural
decisions called out below).

---

## Context

I have a working but entirely manual data pipeline that produces campground
reference data (USFS, BLM, and state parks for MT/ID/CO) for a GPX
route-planning app called `gpx-route-planner`. Every stage so far has been
run by hand, one script at a time, with no fetch automation for most
sources, no merge/validation scripts committed to the repo, and no tests.

I want you to design and build a **repeatable, versioned, testable ingestion
pipeline** that replaces this manual process, without changing the data
contract the app already depends on.

## Required reading before you start

This bundle is the authoritative context — read all of it before proposing
anything:

1. `ARCHITECTURE.md` — current (as-built) pipeline, target architecture
   proposal, known gaps, and a list of **open decisions** you must resolve
   or explicitly ask me about before writing code (§5 in that doc).
2. `SOURCES.md` — the five data sources, what's known/unknown about
   automating their fetch, and license notes.
3. `schema/canonical-campground.schema.json` — the internal normalized
   record shape every source adapter should produce.
4. `schema/camp-record.schema.json` — the **frozen, app-facing** compact
   record shape. Do not change these key names.
5. `samples/normalized-sample.geojson` and `samples/camp-record-sample.json`
   — worked examples showing a record at each stage of the pipeline.
6. `reference/` — frozen snapshots of the app-repo files this pipeline must
   stay compatible with: `campgroundShared.ts` (the live `CampRecord` type),
   `federalCampgrounds.ts` (the live loader), and three specs
   (`campground-poi-design--ref.md`, `campgrounds-edge-api--planned.md`,
   `osm-campgrounds-overlay--planned.md`) for context on locked design
   decisions and adjacent/future work you should NOT implement.
7. `scripts/` — the current reference implementation, frozen as a snapshot:
   - `filter_usfs_campgrounds.py` / `filter_blm_campgrounds.py` — the
     existing per-source Python normalizers (stdlib only, no dependencies).
   - `build_state_campgrounds_geojson.py` — the MT/ID/CO normalizer.
   - `download_co_campgrounds.py` — the one existing *fetch* script, and the
     template for automating the other ArcGIS-backed sources.
   - `build-campgrounds.mjs` — the Node script (lives in the app repo) that
     compacts the merged GeoJSON into the short-key `CampRecord[]` files the
     app actually loads.
8. `raw-sources/` — **actual raw snapshots** of all 5 upstream files
   (`raw-sources/MANIFEST.md` has exact filenames, snapshot date, and
   checksums). These datasets appear to refresh on a ~3-year cadence, so you
   should be able to build and test-run every pipeline stage against these
   without needing network access to re-fetch anything.

## What "done" looks like

A single, documented entrypoint (CLI command or `make`/`npm` target) that
runs: **fetch → normalize → merge → validate → compact → publish**, where:

- Each stage is independently runnable and testable (not one monolithic
  script).
- Adding a new source (a new state, say) means adding one registry entry and
  (if needed) one new adapter — not writing a new end-to-end script.
- `validate` catches the failure modes described in `ARCHITECTURE.md` §4.5
  (schema violations, out-of-range coordinates, duplicate ids, and a
  before/after diff report comparing to the previous snapshot) and fails
  loudly instead of silently publishing bad data.
- `compact` produces the currently-missing `state-campgrounds.json` builder
  (today only USFS/BLM are compacted — see `ARCHITECTURE.md` §3, gap #4) in
  addition to USFS and BLM.
- The `reservation_tier` taxonomy (`definite_fcfs` / `likely_fcfs` /
  `reservable` / `mixed`) stays consistent across every adapter, and the
  final `CampRecord` shape validates against `schema/camp-record.schema.json`.
- There's a README explaining how to run the pipeline end to end and how to
  add a new source.
- Reasonable test coverage: at minimum, one adapter test with a small fixture
  input → expected canonical output, and one compact-stage golden-file test.

## Explicit constraints — do not violate these

- **Do not change `CampRecord` key names** (`i`, `n`, `t`, `y`, `x`, `r`,
  `rc`, `f`, ...) or the `RES_MAP`/`RES_CONFIDENCE` mapping logic without
  flagging it to me first — see `ARCHITECTURE.md` §6. These are consumed
  directly by `packages/route-components/src/lib/campgroundShared.ts` and
  the marker rendering / drawer / GPX export code in `gpx-route-planner`.
- **Do not implement the Cloudflare KV edge-tiling API** described in
  `gpx-route-planner/specs/campgrounds-edge-api--planned.md`. That's a
  separate, already-spec'd effort with its own open decisions. Your
  `publish` stage should just leave a clean seam for it (i.e., publish
  should be swappable, not hard-coded to only write static files) — don't
  build the KV side itself.
- **Do not touch UI/marker rendering code** in `gpx-route-planner` — this is
  a data pipeline task only.
- If you keep Python for normalize and Node for compact (matching today's
  split), that's fine — but say so explicitly and justify it, don't just
  drift into it. See the language/location question below.

## Questions to resolve before or during implementation

These are called out in `ARCHITECTURE.md` §5 — either make a reasoned
default choice and clearly state it, or ask me:

1. Should the pipeline live in this repo (`gpx/`) as a standalone
   data-engineering tool, or move into `gpx-route-planner` as a workspace
   package? (Default suggestion: keep it in `gpx/`, since it's
   source-agnostic and the app repo shouldn't need Python.)
2. Which of the USFS/BLM/MT/ID sources actually have stable ArcGIS
   REST/FeatureServer endpoints that can be automated like Colorado's? This
   needs a short research spike — don't assume, verify (e.g. by probing the
   hub pages listed in `SOURCES.md` for a FeatureServer URL).
3. Should `publish` auto-publish on every successful validate, or should it
   stop after generating a diff report for human review before anything
   touches `gpx-route-planner/apps/planner/public/data/`? Given this is
   described as "curated reference data" that's expensive to reproduce,
   default to **review-gated** (e.g. the pipeline's output is a PR, not a
   direct write) unless I say otherwise.
4. What cadence should this run on — scheduled (cron/CI) or manually
   triggered? Given none of the upstream sources have a documented refresh
   cadence, default to **manually triggered with a diff report**, not a
   blind scheduled auto-publish, until we've seen a few runs.
5. Is it worth improving the state-park (MT/ID/CO) reservation-tier
   heuristic, which is much cruder than the USFS/BLM logic (see
   `ARCHITECTURE.md` §2)? Default: leave as-is for v1, note it as a known
   limitation, since state parks are a secondary layer in the app.

## Suggested first step

Before writing any code, produce a short plan (stages, chosen language(s)/
location, source-registry format, and your answers to the five questions
above) and confirm it with me. Then implement stage by stage, with tests,
rather than all at once.
