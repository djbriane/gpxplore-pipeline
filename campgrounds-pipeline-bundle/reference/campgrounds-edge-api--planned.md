# Spec: Campgrounds Edge API — Protect Curated Reference Data

## Overview

Today the three curated campground datasets ship as **static, fully-downloadable
files** under `apps/planner/public/data/`:

| File                     | Records | Size   |
| ------------------------ | ------- | ------ |
| `usfs-campgrounds.json`  | ~15,770 | 7.1 MB |
| `blm-campgrounds.json`   | —       | 424 KB |
| `state-campgrounds.json` | —       | 256 KB |

A single unauthenticated `GET /data/usfs-campgrounds.json` returns the **entire
transformed corpus** — all the USFS/BLM/state cleaning, geocoding, fee/water/
restroom normalization, tiering, and prose. This is the most exposed and most
expensive-to-reproduce asset in the app, and it is trivially scrapeable.

This spec moves that data **behind a bbox-gated Cloudflare edge API** so that no
single request can return the whole dataset, bulk harvesting becomes slow and
rate-limited, and the curated records never ship as a static blob.

This is **Phase 1** of a broader "protect curated data / move value to the edge"
initiative. Phase 2 (move Energy Load _scoring constants_ server-side — see
[`routeLoad.ts`](../packages/route-components/src/lib/routeLoad.ts)) is **out of
scope here** and deferred; the algorithm is small, physics-based, and a far lower-
value target than the data. See "Deferred / non-goals".

> **This does not touch local-first.** The "local-first, no auth, no server-side
> _user_ data" global decision governs **user routes and trips**. Campground data
> is **app-shipped reference data** that is _already_ a network fetch
> ([`federalCampgrounds.ts:41`](../apps/planner/src/lib/federalCampgrounds.ts)).
> We are changing _how reference data is delivered_, not where user data lives.
> No accounts, no Supabase, no user data leaves the browser.

---

## Goals

1. No endpoint returns the full dataset in one (or a few) requests.
2. A determined scraper must **enumerate the map cell-by-cell**, slowly, against
   a rate limit, leaving a detectable trail — instead of one `curl`.
3. The heavy curated fields (`desc`, `fee_d`, `dir`, `rest`, `cond`, …) are never
   served except for records actually in view.
4. No visible regression to the current campground UX: clustering, proximity-to-
   route filtering, dispersed-site zoom gating, and popups all still work.
5. Storage stays local-first for user data; campground delivery moves to the edge.

## Explicit non-goals / deferred

- **Not** making the data un-stealable. A cell-walking scraper can still harvest
  over time. The goal is to raise cost from _trivial_ to _expensive + detectable_,
  not to achieve true secrecy (impossible for data a client renders).
- **Not** moving Energy Load / effort-miles scoring server-side (Phase 2, deferred).
- **Not** adding auth, accounts, or user-data sync.
- **Not** changing the `CampRecord` shape consumed by the marker layer.
- **Not** touching the Recreation.gov live proxy
  ([`api/ridb.ts`](../apps/planner/src/routes/api/ridb.ts)) — it already proxies a
  third-party API per-region and is not our curated data.

---

## Background: how the data is consumed today

[`federalCampgrounds.ts`](../apps/planner/src/lib/federalCampgrounds.ts) has two
access patterns, both of which currently rely on having the **whole dataset** in
memory:

- **`loadCampgrounds(agency)`** — fetches the full static file once, caches the
  parsed array in a module-level in-memory `cache` (per session; not persisted).
- **`createFederalLayer().refresh(filters, track)`** — iterates the _entire_ array
  every render, filtering by:
  - facility type (`filters.types`),
  - optional **proximity to the loaded route track** (`filters.proximityEnabled`,
    `filters.proximityMiles`) via `buildProximityTest`,
  - **dispersed sites** shown only within `DISPERSED_MAX_MILES = 2` of the track
    and only at zoom ≥ `DISPERSED_MIN_ZOOM = 11`.

Records use `y` = latitude, `x` = longitude (`CampRecord` in
[`campgroundShared.ts:23`](../packages/route-components/src/lib/campgroundShared.ts)).
Coordinates span CONUS + AK + HI (lat 28.9…60.9, lon −149.9…−70.8).

**Key consequence:** the renderer assumes it can see every record. Moving to a
bbox API means the layer must fetch the **region it needs** and re-fetch as that
region changes. The two existing access patterns both reduce to _"give me the
records inside this bounding box"_:

- **Route loaded + proximity on** → bbox = route bounds, padded by `proximityMiles`
  (the exact per-point proximity test stays client-side on that subset).
- **Browsing, no route** → bbox = current map viewport.

---

## Design

### Spatial tiling (the core anti-scrape mechanism)

At **build/publish time**, bucket each agency's records into a **1° × 1° grid**
keyed by `floor(lat)` / `floor(lon)`:

```
cellKey(agency, version, lat, lon) = `${agency}/${version}/${Math.floor(lat)}_${Math.floor(lon)}`
```

Each populated cell is stored as its own value in **Cloudflare KV** (fast, edge-
cached reads; small per-cell payloads — USFS averages well under KV limits at 1°).
A small **manifest** value lists populated cells + the data `version` (publish
date, e.g. `2026-05-24`).

`version` is embedded in the key so a re-publish is **immutable and atomically
swapped** by bumping the version in the manifest — no cache invalidation dance.

> **Decision — KV over R2.** Per-cell payloads are small and read-latency-
> sensitive (every pan can refetch). KV is edge-cached and fast. R2 is the
> fallback only if a 1° cell ever exceeds practical KV value size (it won't at
> current density; a denser future dataset would drop to 0.5° cells instead).

### The edge endpoint

`GET /api/campgrounds?agency=<usfs|blm|state>&bbox=<w,s,e,n>`

1. Validate `agency` ∈ {usfs, blm, state} and `bbox` (4 finite numbers, w<e, s<n).
2. **Reject oversized bboxes** — compute the integer cells the bbox spans; if the
   count exceeds `MAX_CELLS_PER_REQUEST` (default **24**), return **400**
   `{ error: "bbox too large" }`. _This is the primary anti-"give me everything"
   control_ — it caps how much one request can pull and forces cell-walking.
3. Read the current `version` from the manifest.
4. `KV.get` each intersecting populated cell **in parallel**; merge records.
5. Filter merged records to the **exact** bbox (cells are coarser than the query).
6. Return `{ version, records: CampRecord[] }` with:
   - `Cache-Control: public, max-age=86400, immutable` (data is versioned),
   - CORS locked to the app origin(s),
   - no request-body logging of bbox (privacy — see below).

The response `CampRecord[]` is **shape-identical** to today's array, so the marker
layer consumes it unchanged.

### Rate limiting & abuse

- A **Cloudflare Rate Limiting Rule** on `/api/campgrounds*` (dashboard/Terraform,
  no app code) — e.g. N requests / 10s / IP, challenge or 429 over budget.
- Optionally the **Workers Rate Limiting binding** in the handler as defense-in-
  depth (per-IP token bucket) — return 429 with `Retry-After`.
- CORS `Access-Control-Allow-Origin` restricted to the production + preview
  origins; reject others. (Not real security — raises the bar for casual reuse.)

### Client changes

Replace the whole-file model in
[`federalCampgrounds.ts`](../apps/planner/src/lib/federalCampgrounds.ts):

- `loadCampgrounds(agency)` → **`fetchCampgroundsInBbox(agency, bbox)`** hitting
  `/api/campgrounds`. Keep a **client-side per-cell session cache** (a `Map` keyed
  by `agency/cell`) so panning over already-seen cells does **not** refetch — this
  preserves the "load once, feel instant" experience within a session and avoids
  refetch storms.
- `createFederalLayer().refresh(filters, track)` computes the bbox it needs:
  - track present → route bounds padded by `max(proximityMiles, DISPERSED_MAX_MILES)`,
  - else → the current map viewport bounds (passed in by the host).
    Then fetches (cache-aware) and runs the **existing** in-memory type/proximity/
    dispersed filters on the returned subset. The per-point `buildProximityTest`,
    tiering, and dispersed-zoom logic are **unchanged**.
- [`TopoMap.tsx`](../apps/planner/src/components/TopoMap.tsx) wires a debounced
  `moveend`/`zoomend` → `refresh` with the new viewport bounds when browsing
  without a route (so panning loads new regions). With a route loaded, behavior is
  unchanged (route-bounds bbox).

### Build/publish tooling

`apps/planner/scripts/tile-campgrounds.ts` (or a workspace script):

1. Reads the existing three JSON arrays (kept in-repo as the **source of truth**;
   they move out of `public/` so they stop shipping).
2. Buckets records by `floor(y)`/`floor(x)` per agency.
3. Writes a `version` (publish date) + manifest of populated cells.
4. Emits `wrangler kv bulk put` payloads (one entry per cell + manifest) and
   uploads via `wrangler kv:bulk put`.

Source JSON relocates to e.g. `apps/planner/data/source/` (out of `public/`),
documented as the regenerate-and-republish input. Re-publishing = run the script,
bump `version`.

---

## Privacy & offline notes

- **Privacy:** the server now sees _viewport/route bboxes_ (roughly, where a user
  is looking) where before it saw only "downloaded the file." Mitigate by **not
  logging bbox values** (or logging only coarse counts). Acceptable: this is
  reference-data delivery, not user-data collection.
- **Offline:** current behavior is already online-only and only in-memory-cached
  per session, so there is **no offline regression**. The new per-cell session
  cache is at least as good within a session. (Persisting tiles to IndexedDB for
  true offline is a possible future follow-up, not in scope.)

---

## Acceptance criteria

- [ ] `GET /data/*-campgrounds.json` no longer exists / 404s; the files are gone
      from `apps/planner/public/`.
- [ ] `GET /api/campgrounds?agency=usfs&bbox=<small-region>` returns only records
      inside that bbox, shape-identical to the old `CampRecord[]`.
- [ ] A bbox spanning more than `MAX_CELLS_PER_REQUEST` cells returns **400**, not
      data — verified there is no single-request path to the full corpus.
- [ ] Rate limiting returns **429** past budget (rule active; handler optional).
- [ ] CORS rejects non-app origins.
- [ ] In-app: browsing pans load new regions; loading a route shows the same
      proximity-filtered campgrounds as before; dispersed sites still gate on
      zoom ≥ 11 and ≤ 2 mi of track; clustering and popups unchanged.
- [ ] Panning back over a visited region does **not** refetch (session cache hit).
- [ ] `npm test` and `npm run build` green; e2e campground flows pass.

---

## Open decisions to lock before build (grill-me targets)

1. **Cell size** — 1° confirmed sufficient, or pre-empt density with 0.5°?
2. **`MAX_CELLS_PER_REQUEST`** — 24 (≈ a generous multi-state view) vs tighter.
3. **Split light vs heavy payload?** — serve a slim record (marker + popup chips)
   for the bbox list and lazy-fetch the heavy `desc`/`fee_d`/`dir` only on marker
   click. Better protection + smaller responses, more work. In scope or Phase 1.5?
4. **Rate-limit budget** numbers and whether the Workers binding ships in v1 or
   just the dashboard rule.
5. **Source-JSON location** — `apps/planner/data/source/` vs a top-level `data/`.
