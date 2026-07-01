# Spec: OpenStreetMap Campgrounds Overlay

## Overview

Add **OpenStreetMap (OSM) campsites** as an optional live map overlay in GPXplore, using tag semantics and classification logic extracted from the [OpenCampingMap](https://opencampingmap.org/) (OCM) project (`../gpx/opencampsitemap` locally). OCM is a global Leaflet map over a **PostGIS-backed API** ([osmpoidb](https://github.com/giggls/osmpoidb)); this spec adapts OCM's **data model and tag vocabulary** into GPXplore's existing **`CampRecord` → `Place` → drawer/export** pipeline without requiring the full osmpoidb stack.

This is a **new data source**, not a replacement for USFS/BLM/state static layers or the Recreation.gov (RIDB) live layer. OSM fills geographic and agency gaps (international sites, community-mapped US sites missing from federal datasets).

> **Relationship to [`campgrounds-edge-api--planned.md`](campgrounds-edge-api--planned.md):** Independent. The edge-API spec protects **curated** federal/state JSON. OSM is **third-party live data** (ODbL), analogous to RIDB. They can ship in either order.

**Research (2026-06-02):** Perplexity Deep Research on Options A/B/C — summary locked below in [§1 Locked decisions](#1-locked-decisions-r1--data-source). Full report: [`specs/refs/osm-campsites-data-source-research--ref.md`](refs/osm-campsites-data-source-research--ref.md).

**Reference design:** [`specs/refs/campground-poi-design--ref.md`](refs/campground-poi-design--ref.md) — markers encode camper-priority tier, not data source; source appears as a drawer chip only.

---

## Goals

1. User can toggle an **"OpenStreetMap campsites"** overlay alongside existing campground layers.
2. Campgrounds load **viewport-driven** (pan/zoom), not as a full-planet static download.
3. Each OSM site maps into **`CampRecord`** with `agency: "osm"` and flows through **`campgroundToPlace`**, **`PlaceDetailDrawer`**, save-to-route, and GPX export unchanged.
4. Tag → field mapping follows OCM's documented OSM vocabulary where it fits `CampRecord`; extra OSM tags are optional drawer enrichment.
5. **ODbL attribution** is visible when the layer is used (About page + layer/status copy).
6. No regression to existing federal or RIDB overlay behavior.

## Explicit non-goals

- **Not** running osmpoidb, osm2pgsql, or PostGIS in this repo (unless a later decision explicitly chooses self-hosting — see Research §8).
- **Not** POI-in-POI amenity rollup (toilets inside camp polygons merged onto parent) — that requires osmpoidb SQL preprocessing; Overpass returns tags on the camp object only unless separately queried.
- **Not** OCM facility PNG tiles at z 18–19 ([campsite-features](https://github.com/giggls/campsite-features)).
- **Not** OCM data-quality UI (`isBroken`, JOSM/iD edit links, Mangrove reviews) in v1 — optional follow-up.
- **Not** changing `CampRecord` shape beyond extending `Agency`.
- **Not** merging OSM records with federal duplicates (dedup by proximity/name is a follow-up).
- **Not** offline caching of OSM tiles beyond normal session fetch cache.

---

## Background: OpenCampingMap architecture (source project)

Extracted from `/Users/djbriane/Development/playground/gpx/opencampsitemap` (local clone of [giggls/opencampsitemap](https://github.com/giggls/opencampsitemap)).

### What OCM is

| Layer          | Repo / service                                                   | Role                                                               |
| -------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------ |
| Frontend       | `opencampsitemap`                                                | Leaflet map, bbox fetch, markers, sidebar, filters                 |
| Data backend   | [osmpoidb](https://github.com/giggls/osmpoidb)                   | PostGIS + `get-campsites.cgi` → GeoJSON                            |
| Facility tiles | [campsite-features](https://github.com/giggls/campsite-features) | Inner-site PNG tiles z 18–19                                       |
| OSM ingest     | osm2pgsql                                                        | Planet/replication → PostGIS (~10 min refresh per osmpoidb README) |

The frontend **never calls Overpass**. It POSTs a bbox to `/getcampsites`:

```
POST /getcampsites
Content-Type: application/x-www-form-urlencoded

bbox=west,south,east,north
```

Also: `GET /getcampsites?osm_id=&osm_type=node|way|relation` for single-object deep links.

Client fetch wiring: `js/campmap.js` (`updateMapContents`, min zoom 8, debounced on `dragend`/`zoomend`).

### OSM objects OCM includes

From `taginfo.json` and OCM docs — primary selectors:

| Key       | Value          | Object types              | Notes                                           |
| --------- | -------------- | ------------------------- | ----------------------------------------------- |
| `tourism` | `camp_site`    | node, area (way/relation) | Default campsite                                |
| `tourism` | `caravan_site` | node, area                | RV/caravan-focused sites                        |
| `type`    | `site`         | relation                  | Site relations with `tourism=camp_site` members |

**Research needed:** Whether v1 Overpass query should include `relation["tourism"~"camp_site|caravan_site"]` and how to derive a representative point for multipolygon relations (OCM uses `ST_PointOnSurface` server-side).

Additional tags OCM surfaces (not all are selection filters): `backcountry`, `group_only`, `scout`, `nudism`, `camp_site=*`, `access`, `fee`, `reservation`, `permanent_camping`, capacity tags, amenity tags — see §5.

### OCM computed `category` (marker classification)

OCM's backend SQL (`gen_poi_campsites.sql` in **osmpoidb**, not in opencampsitemap) assigns a `category` property consumed by the frontend:

| `category`    | Documented OSM logic (from osmpoidb / OCM docs)           |
| ------------- | --------------------------------------------------------- |
| `nudist`      | `nudism` ∈ `yes`, `obligatory`, `customary`, `designated` |
| `group_only`  | `group_only=yes` or `scout=yes`                           |
| `backcountry` | `backcountry=yes`                                         |
| `camping`     | `tents=yes`, `caravans=no`, and no `motorhome=yes`        |
| `caravan`     | `tents=no`, or `tourism=caravan_site` without `tents` tag |
| `standard`    | default                                                   |

Frontend categories array: `js/campmap.js` — `["standard", "caravan", "camping", "nudist", "group_only", "backcountry"]`.

**Research needed:** Confirm exact SQL precedence in `osmpoidb/gen_poi_campsites.sql` before locking client-side `inferFacilityType()` — order may matter when multiple tags apply.

### OCM private / access handling

From `js/campmap.js`:

- `access` ∈ `private`, `members`, `no` → private marker styling; filterable per category.
- `permanent_camping=only` → treated as private (`access=private`).

**Open decision:** Show private OSM sites in GPXplore v1 (grey/de-emphasized) or hide by default?

### OCM optional UI filters

From `js/filters.js` — regex/value filters on rendered features:

| Tag            | Match values     |
| -------------- | ---------------- |
| `dog`          | `yes`, `leashed` |
| `fee`          | `no`             |
| `power_supply` | `^(?!no).+$`     |

**Non-goal for v1** unless explicitly scoped — federal layers use type/proximity filters; RIDB has none.

### OCM facility tag vocabulary (drawer enrichment)

From `l10n/en.js` → `facilities` object — keys OCM renders as icon bars:

`tents`, `caravans`, `motorhome`, `static_caravans`, `cabins`, `permanent_camping`, `toilets`, `shower`, `drinking_water`, `power_supply`, `sanitary_dump_station`, `shop`, `laundry`, `washing_machine`, `pub`, `bar`, `restaurant`, `fast_food`, `telephone`, `post_box`, `playground`, `internet_access`, `bbq`, `picnic_table`, `kitchen`, `fridge`, `sink`, `dog`, `sport` (array), `reception`, `fire-extinguisher`, …

Full list: `opencampsitemap/l10n/en.js` lines ~80–445.

Capacity tag rules from `js/site-feature.js`:

- `maxtents` → deprecated; prefer `capacity:tents`
- On `tourism=caravan_site`, bare `capacity` → treat as `capacity:pitches`
- Prefer `capacity:tents` / `capacity:caravans` over `capacity:pitches` when present

### OCM data-quality hints (optional v2)

From `js/site-feature.js` (`f2bugInfo`) — not selection logic, mapper hints:

- Missing `name`, `toilets`, `shower`, `tents`, `caravans` tags
- Node-only site without site relation (should be area)
- `site_relation_state` useless/invalid, `contains_sites`, `inside_sites`

Requires osmpoidb-computed properties — **not available from raw Overpass** without reimplementing relation analysis.

---

## Background: GPXplore integration surface

Existing patterns to reuse:

| Pattern             | Reference                                                                                                                                        | Use for OSM                                                      |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------- |
| Live viewport layer | [`apps/planner/src/lib/ridbLayer.ts`](../apps/planner/src/lib/ridbLayer.ts)                                                                      | Debounced fetch, abort, saved-place dedup, marker click → drawer |
| Worker proxy        | [`apps/planner/src/routes/api/ridb.ts`](../apps/planner/src/routes/api/ridb.ts)                                                                  | Hide upstream, cache headers, error shape                        |
| Bbox validation     | [`apps/planner/src/lib/wildfireSources.ts`](../apps/planner/src/lib/wildfireSources.ts)                                                          | Reject oversized viewports                                       |
| Canonical model     | [`packages/route-components/src/lib/campgroundShared.ts`](../packages/route-components/src/lib/campgroundShared.ts)                              | Extend `Agency`, `SOURCE_LABEL`                                  |
| Overlay registry    | [`apps/planner/src/lib/overlayConfig.ts`](../apps/planner/src/lib/overlayConfig.ts), [`TopoMap.tsx`](../apps/planner/src/components/TopoMap.tsx) | New overlay id                                                   |
| Places pipeline     | [`apps/planner/src/store/usePlacesStore.ts`](../apps/planner/src/store/usePlacesStore.ts)                                                        | `campgroundToPlace(rec, "osm")`                                  |
| Detail drawer       | [`apps/planner/src/components/PlaceDetailDrawer.tsx`](../apps/planner/src/components/PlaceDetailDrawer.tsx)                                      | Source chip, external OSM link                                   |
| Design tokens       | [`specs/refs/campground-poi-design--ref.md`](refs/campground-poi-design--ref.md)                                                                 | Tier from `tierFor(rec)`, not source                             |

Current `Agency` type: `"usfs" | "blm" | "recgov" | "state"` — add `"osm"`.

RIDB gating today: silent skip when viewport radius > 50 mi; no min zoom. OCM min zoom: **8**.

---

## Design

### 1. Locked decisions (R1 — data source)

**Resolved 2026-06-02** via external research (see overview link).

| Option                           | Verdict                                                                                                                                     |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Overpass via Worker proxy** | **Ship v1.** Primary path.                                                                                                                  |
| **B. Self-host osmpoidb**        | **Defer.** Revisit at ~500 MAU with overlay actively used, or if POI-in-POI amenity rollup becomes core UX. Est. $17–60/mo + 2–4 hr/mo ops. |
| **C. Proxy opencampingmap.org**  | **Rejected.** No public API, no ToS, volunteer infra, ethically unacceptable without maintainer agreement.                                  |

**v1 Overpass endpoints (locked):**

| Role        | URL                                               | Notes                                                                                |
| ----------- | ------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Primary     | `https://overpass.private.coffee/api/interpreter` | Most permissive public policy; notify operator before large-scale use                |
| Fallback    | `https://overpass-api.de/api/interpreter`         | ~10k req/day guideline; FOSSGIS lists app-backend use as problematic — fallback only |
| Future paid | Geofabrik hosted Overpass                         | If public instances become insufficient                                              |

**Required request headers:** `User-Agent: GPXplore/<version> (<contact-email>)` and `Referer: <production-origin>` on every upstream Overpass call (FOSSGIS policy).

**Build Overpass-agnostic:** Worker accepts `OVERPASS_URL` env var so primary/fallback/paid instance can change without client changes.

**v2 migration paths (not v1):**

- **PMTiles on R2** — monthly Geofabrik `osmium tags-filter` → tippecanoe → ~20–50 MB global camping layer; zero live Overpass dependency.
- **Regional osmpoidb** — only if amenity rollup or rate pressure justifies PostGIS ops.

**What Overpass gives up vs osmpoidb (accepted for v1):**

| Capability                                   | Overpass       | osmpoidb        |
| -------------------------------------------- | -------------- | --------------- |
| Name, lat/lon, fee/reservation/operator tags | ✅             | ✅              |
| Amenities inside camp polygon (POI-in-POI)   | ❌             | ✅              |
| Precomputed `category`, site-relation QA     | ❌             | ✅              |
| Route-planner marker use case                | **Sufficient** | Overkill for v1 |

### 2. Edge endpoint

```
GET /api/osm-campgrounds?west=&south=&east=&north=
```

Response:

```ts
{
  facilities: CampRecord[];  // same shape as RIDB client expects after mapping
  meta?: {
    source: "overpass";
    fetchedAt: string;       // ISO
    attribution: string;     // ODbL line for UI
  };
}
```

Handler behavior:

1. Parse and validate bbox — **OSM-specific limits** (tighter than wildfire `BBOX_MAX_DEGREES = 12`):

   | Map zoom | Max bbox (each axis) | Overpass `[timeout:…]` | Worker `AbortController` |
   | -------- | -------------------- | ---------------------- | ------------------------ |
   | 9        | 2°                   | 20                     | 24s                      |
   | 10       | 1°                   | 10                     | 12s                      |
   | 11–12    | 0.5°                 | 5                      | 8s                       |
   | < 9      | **no fetch**         | —                      | —                        |

2. Reject if bbox exceeds zoom-tier limit → **400** `{ error: "bbox too large" }`.
3. POST Overpass QL (`Content-Type: application/x-www-form-urlencoded`, body `data=…`); try primary, on 429/503/timeout retry fallback once.
4. Normalize elements → `CampRecord[]` (§5–6).
5. Return JSON with `Cache-Control: public, max-age=3600` (1 hr — campsite tags change slowly).
6. **Edge cache:** quantize bbox to 0.25° grid before cache key (improves hit rate on pan).

**Cloudflare Workers (locked):** CPU time limit does not include `fetch()` wait time; 5–25s Overpass responses are viable on paid Workers plan. Set explicit `AbortController` timeout per table above. Do not use `out geom` — payload 10–50× larger with no marker benefit.

Overpass query (locked for v1):

```overpass
[out:json][timeout:{{timeout}}];
(
  node["tourism"~"^(camp_site|caravan_site)$"]({{south}},{{west}},{{north}},{{east}});
  way["tourism"~"^(camp_site|caravan_site)$"]({{south}},{{west}},{{north}},{{east}});
  relation["tourism"~"^(camp_site|caravan_site)$"]({{south}},{{west}},{{north}},{{east}});
);
out center tags;
```

- **`out center tags`** — point at centroid for ways/relations; no full polygon geometry.
- **Include `relation`** — render as single point at `center` (same as ways). Verify in implementation (R2 partial).

**Traffic etiquette (locked):**

- Email Private.coffee proactively before sustained **>2,000 sessions/day** with overlay enabled.
- Treat overpass-api.de fallback as best-effort only; stay under ~10k req/day aggregate to that instance.
- Consider dashboard rate limit on `/api/osm-campgrounds` at launch (defense in depth); not blocking v1.

### 3. Client layer (`createOsmLayer`)

Mirror `createRidbLayer`:

| Behavior  | Detail                                                                                                                               |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Trigger   | Overlay enabled → fetch; `moveend` → debounced refetch (~500 ms)                                                                     |
| Cancel    | `AbortController` on new pan                                                                                                         |
| Cache key | Rounded bbox string (reuse `wildfireSources` rounding or RIDB center+radius pattern)                                                 |
| Gating    | Min zoom **9** — show overlay status _"Zoom in to see OpenStreetMap campsites"_ below 9 (not silent skip). Bbox limits per §2 table. |
| Dedup     | Skip marker if `usePlacesStore` has `osm-{type}-{id}` and not removed                                                                |
| Marker    | `buildMarkerIcon({ tier: tierFor(rec), glyph, ... })`                                                                                |
| Click     | `campgroundToPlace(rec, "osm")` → `PlaceDetailDrawer`                                                                                |
| Errors    | `toast.error` + optional `OverlayStatus` (wildfire pattern)                                                                          |

**Open decision:** Marker clustering — federal layers use `markercluster`; RIDB does not. **Research needed:** typical OSM density at zoom 10–12 in target regions (US mountain west vs Europe).

Register overlay:

```ts
{ id: "osm", label: "OpenStreetMap campsites" }
```

Layer panel placement: alongside **P2 · Reservable** (RIDB) as another live/global source, or new **"Community mapped"** group — **open decision**.

### 4. OSM element → stable ID

```
i = `osm-${element.type}-${element.id}`   // e.g. osm-way-115074273
u = `https://www.openstreetmap.org/${type}/${id}`
```

**Research needed:** Collision handling if the same physical site exists as both node and way (OCM dedupes in PostGIS; Overpass may return both).

### 5. OCM `category` → GPXplore `FacilityType` + tier

Map OCM categories to existing `CampRecord.t` and let `tierFor()` assign marker tier:

| OCM `category` | `CampRecord.t`  | `tierFor` result                  | Glyph hint |
| -------------- | --------------- | --------------------------------- | ---------- |
| `backcountry`  | `dispersed`     | p3                                | —          |
| `group_only`   | `group`         | p1                                | `group`    |
| `camping`      | `campground`    | p1 (unless reservation tags → p2) | `tent`     |
| `caravan`      | `campground`    | p1                                | `tent`     |
| `standard`     | `campground`    | p1                                | `tent`     |
| `nudist`       | **Research §8** | likely p1                         | —          |

Client-side inference (when not using osmpoidb), evaluate in order:

1. If `nudism` matches OCM set → category `nudist`
2. Else if `group_only=yes` or `scout=yes` → `group_only`
3. Else if `backcountry=yes` or `camp_site=wildcamp` → `backcountry`
4. Else if `tents=yes` and `caravans=no` and `motorhome!=yes` → `camping`
5. Else if `tents=no` or (`tourism=caravan_site` and no `tents`) → `caravan`
6. Else → `standard`

Also map `camp_site=*` values from taginfo where relevant (`basic`, `standard`, `serviced`, `deluxe`, `wildcamp`, …) — **Research needed:** which values affect `FacilityType` vs display-only `sub`.

### 6. OSM tags → `CampRecord` fields

| OSM tag(s)                                                            | `CampRecord` field    | Notes                                                                                                     |
| --------------------------------------------------------------------- | --------------------- | --------------------------------------------------------------------------------------------------------- |
| `name`                                                                | `n`                   | Fallback: `"Unnamed campsite"` (OCM: `l10n.unnamed_campsite`)                                             |
| lat/lon or way `center`                                               | `y`, `x`              | Skip if non-finite                                                                                        |
| `reservation=required`                                                | `r: "res"`            |                                                                                                           |
| `reservation=no`                                                      | `r: "fcfs"`           |                                                                                                           |
| other / absent                                                        | `r: null`             |                                                                                                           |
| `fee=no`                                                              | `f: 0`                |                                                                                                           |
| `fee` present and not `no`                                            | `f: 1`                |                                                                                                           |
| absent                                                                | `f: null`             |                                                                                                           |
| `description`                                                         | `desc`                |                                                                                                           |
| `operator`                                                            | `op`                  |                                                                                                           |
| `charge`, `fee` (text)                                                | `fee_d`               | Prefer `charge` when both                                                                                 |
| `directions`                                                          | `dir`                 |                                                                                                           |
| `phone`, `contact:phone`                                              | `ph`                  |                                                                                                           |
| `email`, `contact:email`                                              | `em`                  |                                                                                                           |
| `drinking_water=yes`                                                  | `w: 1`                |                                                                                                           |
| `capacity:pitches`, `capacity:tents`, `capacity:caravans`, `capacity` | `c`                   | Apply OCM capacity rules                                                                                  |
| `ele`                                                                 | `el`                  | Uncommon on campsites                                                                                     |
| `toilets`                                                             | `rest`                | Map yes/no/typical values to label string                                                                 |
| `website`, `contact:website`                                          | `u`                   | **Conflict:** `u` is also OSM permalink — **open decision:** keep OSM link in `u`, website in drawer only |
| `tourism=caravan_site`                                                | `sub: "Caravan site"` |                                                                                                           |

Reservation tier override: if `r === "res"` → marker tier p2 per [`tierFor`](../packages/route-components/src/lib/campgroundShared.ts).

### 7. Drawer & export

- **Header chip:** `SOURCE_LABEL.osm` → `"OpenStreetMap"` (exact string TBD).
- **Footer link:** `View on openstreetmap.org ↗` using `rec.u`.
- **Optional v1.1 section:** "Amenities" — subset of OCM `facilities` tags present on the record (text list, no SVG port from OCM).
- **No** RIDB-style `/api/ridb-campsites` equivalent — OSM has no per-pitch inventory API.
- **Export:** existing `exportWaypoints.ts` path works if `CampRecord` populated; include source in waypoint description metadata.

### 8. Attribution & compliance

**Partially resolved (2026-06-02 research)** — not legal advice; counsel review still recommended before commercial launch.

| Surface                     | Requirement (locked for v1)                                                                                                              |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Map                         | Show `© OpenStreetMap contributors` linked to `openstreetmap.org/copyright` when OSM overlay is **active** (OSMF attribution guidelines) |
| About / THIRD_PARTY_NOTICES | OSM **data** layer documented separately from basemap tiles                                                                              |
| Edge cache (1 hr)           | Permitted — caching Produced Work data is OK under ODbL                                                                                  |
| Saved places (localStorage) | Permitted — not publishing a Derivative Database                                                                                         |
| GPX export                  | Add attribution when export includes OSM-sourced campsites — XML comment and/or `<desc>` on affected `<wpt>` elements                    |

**Still open:** Exact GPX attribution string and whether all exports need a global comment vs per-waypoint when mixed sources.

---

## Files to create or change

| File                                                    | Change                                    |
| ------------------------------------------------------- | ----------------------------------------- |
| `packages/route-components/src/lib/campgroundShared.ts` | `Agency` + `SOURCE_LABEL`                 |
| `apps/planner/src/routes/api/osm-campgrounds.ts`        | Worker Overpass proxy (name TBD)          |
| `apps/planner/src/lib/osmCampgroundsLayer.ts`           | `createOsmLayer` (name TBD)               |
| `apps/planner/src/lib/osmCampgroundsMap.ts`             | Pure tag → `CampRecord` mapping + tests   |
| `apps/planner/src/lib/overlayConfig.ts`                 | New overlay entry                         |
| `apps/planner/src/components/TopoMap.tsx`               | Wire layer + status                       |
| `apps/planner/src/components/PlaceDetailDrawer.tsx`     | OSM footer link; optional amenities block |
| `apps/planner/src/routes/about.tsx`                     | Data attribution                          |
| `apps/planner/THIRD_PARTY_NOTICES.md`                   | ODbL entry                                |
| `apps/planner/src/lib/exportWaypoints.test.ts`          | `"osm"` agency case                       |
| `apps/planner/src/lib/overlayConfig.test.ts`            | Include `osm` id                          |

Optional reference docs (no code dependency):

- [`specs/refs/osm-campsites-data-source-research--ref.md`](refs/osm-campsites-data-source-research--ref.md) — R1 Overpass vs osmpoidb vs OCM proxy research (2026-06-02).
- `specs/refs/opencampingmap-tag-vocabulary--ref.md` — snapshot of OCM tag list if we want to decouple from sibling repo path (not yet created).

---

## Acceptance criteria

- [ ] Toggling **OpenStreetMap campsites** fetches camps in the current viewport and renders markers.
- [ ] Panning/zooming debounces refetch; in-flight requests abort on rapid pan.
- [ ] Oversized bbox returns **400** from API (no silent full-country pull).
- [ ] Clicking a marker opens **`PlaceDetailDrawer`** with source chip **OpenStreetMap** and link to osm.org.
- [ ] **Save to route** persists OSM place; marker shows saved gold styling; exports as GPX waypoint.
- [ ] Saved OSM place is not duplicated as a discovery marker when overlay refreshes.
- [ ] `Agency` type includes `"osm"`; TypeScript build clean across packages.
- [ ] About + third-party notices mention OSM **data** (ODbL).
- [ ] `npm test` and `npm run build` green; unit tests for tag mapping and bbox gating.
- [ ] Manual: verify sites in a known OSM-dense area (e.g. Alps, US national forest with OSM camps) at zoom ≥ **9**.
- [ ] Worker sends `User-Agent` + `Referer` on Overpass upstream requests.
- [ ] Primary → fallback retry on 429/503/timeout (at least one retry).

---

## Research & open decisions

### R1 — Data source choice ✅ LOCKED (2026-06-02)

- [x] **Option A (Overpass)** for v1 — Private.coffee primary, overpass-api.de fallback.
- [x] **Option C rejected** — do not proxy OpenCampingMap.
- [x] **Option B deferred** — migration trigger ~500 MAU with active overlay, or core need for POI-in-POI rollup.
- [x] Overpass usage policy reviewed — public instances are a time-limited path; build Overpass-agnostic; plan PMTiles v2.

### R2 — Query completeness

- [x] Include `relation` with `out center tags` (locked in §2).
- [ ] **`camp_site=*` as selector** — v1 uses `tourism` only; evaluate whether `camp_site=wildcamp` without `tourism=camp_site` is common enough to widen query.
- [ ] **Site-relation dedup** — relations may duplicate member ways in results; implementation must dedupe or prefer one type (see R5).
- [ ] **Manual test:** French Riviera / Germany 1°×1° bbox — `caravan_site` density may hit 500+ features and timeout ceiling.

### R3 — Bbox and zoom gating ✅ PARTIALLY LOCKED

- [x] Min zoom **9** with visible status message (research overrides OCM's 8).
- [x] Max bbox per zoom tier — see §2 table (not wildfire's 12°).
- [x] Status message when zoomed out (not RIDB-style silent skip).

### R4 — Category inference parity

- [ ] Read `osmpoidb/gen_poi_campsites.sql` for exact `category` CASE order.
- [ ] Map `nudist` → `campground` + `sub: "Nudist campsite"` (proposed; confirm).

### R5 — Duplicate objects

- [ ] Strategy when Overpass returns node + way (+ relation) for same site — **proposed v1:** show all; accept occasional double markers; dedupe in v1.1 by proximity + name if needed.
- [ ] Federal overlap — **defer v1:** show both OSM and federal markers.

### R6 — Private / members-only sites

- [ ] Default visibility for `access=private|members|no` and `permanent_camping=only`.
- [ ] Filter UI in v1 or later?

### R7 — Performance & UX

- [ ] Clustering on/off — research notes high EU `caravan_site` density; test without clustering first (match RIDB); add clustering if cluttered.
- [ ] Cap max features returned per bbox? (not in research recommendation — test in EU)

### R8 — Legal / attribution ✅ PARTIALLY LOCKED

- [x] Edge cache + localStorage — OK per ODbL Produced Work interpretation (see §8).
- [ ] GPX export attribution format — implement per §8; verify against current `exportWaypoints.ts` behavior.

### R9 — Production limits ✅ PARTIALLY LOCKED

- [x] Worker `fetch()` wait for Overpass is viable; use `AbortController` timeouts per §2.
- [ ] Dashboard rate limit on `/api/osm-campgrounds` — optional v1; recommended before marketing push.
- [ ] Email Private.coffee — do before >2k sessions/day with overlay on.

---

## Suggested implementation phases (for orchestrator if multi-commit)

| Phase    | Scope                                                                                  |
| -------- | -------------------------------------------------------------------------------------- |
| **PR-A** | Types (`Agency`), pure `osmToCampRecord` mapper + tests, API route with Overpass proxy |
| **PR-B** | `createOsmLayer`, overlay registration, TopoMap wiring, basic drawer link              |
| **PR-C** | Attribution, About/THIRD_PARTY_NOTICES, overlay status UX, manual QA fixes             |

Write `specs/osm-campgrounds-overlay--orchestrator.md` when implementation starts (per [`AGENTS.md`](../AGENTS.md)).

---

## References

| Resource                     | Path / URL                                                   |
| ---------------------------- | ------------------------------------------------------------ |
| OCM frontend (local)         | `/Users/djbriane/Development/playground/gpx/opencampsitemap` |
| OCM tag documentation        | `opencampsitemap/taginfo.json`                               |
| OCM bbox client              | `opencampsitemap/js/campmap.js`                              |
| OCM facility vocabulary      | `opencampsitemap/l10n/en.js`                                 |
| OCM UI filters               | `opencampsitemap/js/filters.js`                              |
| OCM sidebar / capacity rules | `opencampsitemap/js/site-feature.js`                         |
| OCM data backend             | https://github.com/giggls/osmpoidb                           |
| GPXplore RIDB template       | `apps/planner/src/lib/ridbLayer.ts`                          |
| GPXplore campground design   | `specs/refs/campground-poi-design--ref.md`                   |
| Overpass API                 | https://wiki.openstreetmap.org/wiki/Overpass_API             |
