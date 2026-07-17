# Routing Service API Contract — STRAWMAN for grilling (#10)

> **This is a STRAWMAN, not a decision.** It exists so the human can be grilled fast
> against a concrete, grounded proposal. Every strawman below is defeasible. The
> real decisions are in **OPEN QUESTIONS** at the bottom. Where a section depends
> on **#9 (surface normalization)**, that is called out so the human grills them in
> the right order (grill #9's surface schema *before* locking the enrichment response).

Ticket: `djbriane/gpxplore-pipeline#10` — Grilling: routing service API contract.

**REFRESH (2026-07-16).** Two upstream inputs finalized since the prior pass and are
now folded in:
- **#8 hosting is DECIDED** — self-host on the home-lab NUC (Unraid), exposed via
  Cloudflare Tunnel. The topology is now fixed: **`browser → Cloudflare Worker (this
  wrapper) → Cloudflare Tunnel → private Valhalla origin on the NUC`**
  (`deploy/valhalla-unraid/README.md:11-18,153-161`; map #8 line, issue #4). The
  Worker fronts a *private* Tunnel origin — not a public Valhalla. §1/§4 rewritten
  around this.
- **Provenance/versioning gap is CLOSED** — `deploy/valhalla-unraid/build-tiles.sh`
  now emits `tile_manifest.json` with `data_version = us-west-<YYMMDD>` (+ extract
  sha256, Valhalla version). §3 rebased on this real handle; old OQ-6 largely
  resolves into a plumbing question.
- **#9 now has its own strawman** (`specs/surface-normalization--grilling-prep.md`) —
  the prior prep said "no #9 prep exists." It does now, and it proposes concrete
  field names. §2b aligned; the #9 dependency is sharper, not gone.

Inputs consumed: **#5** (tile build), **#6** (map-match prototype
`prototypes/bdr-mapmatch/NOTES.md`), **#7** (costing profiles
`specs/valhalla-costing-profiles--research.md`), **#8** (hosting
`deploy/valhalla-unraid/`), **#9** (surface normalization
`specs/surface-normalization--grilling-prep.md`), plus the gpxplore-web codebase
(cited by file:line, re-verified for this refresh).

---

## 0. What the grounding forces (read first)

Five facts largely pre-decide the shape of this contract:

1. **The app's ubiquitous geometry model is a decoded point array, not an encoded
   polyline.** `TrackPoint = { lat, lon, ele?, time? }` is defined once
   (`gpxplore-web/packages/route-components/src/lib/gpx.ts:5`) and used everywhere.
   Routes render as Leaflet polylines built directly from decoded arrays:
   `input.points.map((p) => [p.lat, p.lon])` (`RouteMapView.tsx:328-329`) fed into
   `L.polyline(latlngs, …)` (`RouteMapView.tsx:441`). GeoJSON appears in the app
   *only* for ArcGIS overlay layers (wildfire perimeters `wildfireApi.ts:73,218,289`;
   arc overlays `apps/planner/src/lib/arcOverlayLayer.ts`), **never for
   routes/tracks**. → The routing API should return geometry the app already speaks:
   **decoded `{lat, lon}` arrays**, not Valhalla's encoded polyline.

2. **There is already a thin-wrapper Worker precedent, and it sets every convention
   this contract needs.** `POST /api/route-effort`
   (`apps/planner/src/lib/routeEffortApi.ts`) calls itself a "**thin HTTP wrapper**"
   in its header (`:1-14`). It establishes: streaming **body byte cap** (5 MB,
   `ROUTE_EFFORT_MAX_BODY_BYTES:26`), **point-count cap** (100,000,
   `ROUTE_EFFORT_MAX_POINTS:28`), a `TrackPoint[]` validator (`parsePoints:74-116`),
   stateless / no-geometry-logging privacy posture (`:11-13`), structured JSON errors
   with status codes (400/405/413/422, `:124-190`), and — critically — **version
   stamping in the response** (`engineVersion` `:156`, `profileVersion` `:178`). This
   is the template for the routing wrapper.

3. **There is already a "pipeline data behind a Cloudflare edge API" precedent, with
   a local-first carve-out.** `gpxplore-web/specs/campgrounds-edge-api--planned.md`
   moves curated pipeline data behind a **bbox-gated, rate-limited Cloudflare Worker**
   and states explicitly (`:28-36`) that this "does not touch local-first" because it
   governs *reference-data delivery*, not user data. The routing service is the same
   class of thing — app-served reference capability, not user data. Reuse this stance
   verbatim.

4. **The map-match prototype (#6) proved the hard parts are viable but impose a
   contract obligation: chunking.** `trace_attributes` enforces `trace.max_distance =
   200 km` (UTBDR window → HTTP 400 err 154) plus a max shape-point count
   (`NOTES.md:27,31`). Snap quality is high (p90 ≤ 7 m, max ~31 m across all BDR
   tracks; `NOTES.md:10-16`). Surface comes back as **Valhalla's own enum**
   (`paved_smooth`/`gravel`/`dirt`/`compacted`), 0% unknown, plus `edge.use`
   (`track` vs `road`) and `road_class` (`NOTES.md:22-25`).

5. **[NEW] The origin is now a fixed, private, home-hosted box.** #8 decided:
   Valhalla serves 24/7 on the NUC (~420 MB serving RAM, `README.md:24`) behind a
   Cloudflare Tunnel — **outbound-only, no open home ports**
   (`README.md:153-161`). The wrapper Worker treats that Tunnel origin as its private
   backend. This makes "expose raw Valhalla publicly" not even an option on the table
   (the origin is deliberately unreachable except through the Worker), and it means
   §4's exposure work is real and Worker-owned.

---

## 1. Recommendation: RAW Valhalla vs THIN WRAPPER

**Strawman recommendation (unchanged, now firmer under #8): a thin wrapper —
TypeScript on a Cloudflare Worker — fronting the private NUC Valhalla origin over the
Cloudflare Tunnel.** Do **not** expose raw Valhalla. This is not just "best practice"
now: #8 deliberately made the origin private (Tunnel, no open ports), so the Worker is
the *only* public surface by construction.

### Why a wrapper (rationale tied to the upstream tickets)

| Need | Raw Valhalla gives you | Why that fails the app |
| --- | --- | --- |
| **Named profiles (#7)** | Full `costing_options.motorcycle.*` object per request | We do *not* want clients composing `use_trails`/`use_tracks`/`use_highways`. #7 defines exactly two vetted profiles (`adv_balanced`, `avoid_highways`). Contract accepts `profile: "adv_balanced"`; the wrapper maps it to the vetted `costing_options`. Tuning stays server-side, versioned, un-abusable. |
| **Normalized surface (#9)** | Valhalla surface **enum** + `edge.use` + `edge.road_class` + index bookkeeping | App wants a normalized `surface_class` taxonomy — #9's job (`surface-normalization--grilling-prep.md §2`), keyed off the Valhalla enum. That mapping lives in one server-side place, not in every client. |
| **Chunking (#6)** | Hard 200 km / bounded-point cap; **HTTP 400 err 154** past it | A full imported BDR GPX is 300–800 km. The client must **not** split, call N times, and stitch. The wrapper chunks + stitches and returns one enrichment result. This is the single strongest argument for a wrapper. |
| **Geometry (fact 1)** | Encoded polyline6 (`shape`) | App speaks decoded `{lat,lon}` arrays. Wrapper decodes once. |
| **Versioning (#5/#8)** | `/status` gives `tileset_last_modified`, not our human `data_version` | Wrapper reads `tile_manifest.json`'s `data_version` and stamps every response (§3). |
| **Private-origin exposure (fact 5)** | No auth, no rate limit, no CORS, no clean "origin down" behavior | Public no-accounts endpoint needs CORS + rate limit + abuse protection + a graceful 503 when the home NUC is offline — all at the edge (§4). |

Raw Valhalla's response surface is also *huge* (dozens of edge attributes, maneuvers,
admins); the app needs a small slice. The wrapper is the projection layer.

### Why TypeScript-on-Cloudflare-Worker specifically

- **Consistency.** gpxplore-web is a TypeScript monorepo on Cloudflare Workers.
  `/api/route-effort` and the campgrounds edge API are both TS Workers. A TS Worker
  wrapper reuses the exact patterns: `apiRateLimit.ts`, capped body reads
  (`routeEffortApi.ts:37-72`), `withSecurityHeaders` (`server.ts:102-108`), structured
  JSON errors. No new language/runtime enters the project.
- **The edge is where CORS / rate-limit / abuse already live** (§4). Putting the
  wrapper there gets all of §4 for free.
- **#8 already put a private hop in place.** The Cloudflare Tunnel *is* the
  Worker→origin path (`README.md:153-161`). A Worker calling that Tunnel hostname is
  the intended composition — the README states it explicitly (`:158`,
  `browser → Worker → Tunnel → NUC Valhalla`).

### Worker vs origin-side sidecar — how #8 shifts this fork (OQ-1)

The prior prep floated a sidecar (FastAPI/Node) co-located with Valhalla as a real
alternative. **#8 weakens it** but does not kill it:
- *Against sidecar:* the Tunnel already supplies the private browser→origin hop, and
  the CORS/rate-limit/versioning machinery lives at the CF edge (Worker). A NUC-side
  sidecar would rebuild §4 and add a second language/runtime on a home box whose
  uptime is already the accepted risk.
- *For sidecar:* chunk-and-stitch of `trace_attributes` is CPU on the edge, and the
  Worker free-tier CPU budget is ~10 ms/request (paid: 30 s). Stitching a 5-chunk,
  800 km enrichment (decode + seam-dedup + concat) may exceed 10 ms. If it does, a
  thin **NUC-side chunk helper** (same box as the tiles, no extra public surface — it
  sits behind the same Tunnel) is the fallback, with the Worker still owning CORS /
  rate-limit / versioning / profile-mapping.
- **Strawman default:** do it all in the Worker; keep a NUC-side chunk helper as the
  documented fallback if edge CPU/subrequest limits bite. (Subrequest count is *not*
  the blocker — ~5 origin calls per enrichment is well under even the free-plan 50.)

---

## 2. Strawman endpoint set + schemas

Two endpoints. Both `POST` (bodies carry geometry; mirrors Valhalla's own map-matching
guidance and the route-effort precedent). JSON in/out. Plus a cheap `GET /version`
(§3).

### 2a. Connector route — `POST /route/connector`

Fills the routing gap **between** two off-pavement segments (#7's whole reason to
exist). Thin projection over Valhalla `/route` with the profile pre-mapped.

**Request**
```jsonc
{
  "from": { "lat": 44.12, "lon": -121.34 },
  "to":   { "lat": 44.31, "lon": -121.02 },
  "profile": "adv_balanced",        // enum: "adv_balanced" | "avoid_highways" (#7)
  "geometry": "points",             // "points" (default) | "polyline6" | "none"
  "max_snap_distance_m": 100        // optional; wrapper default below
}
```
- `profile` maps server-side to `costing: "motorcycle"` + the vetted
  `costing_options.motorcycle.*` from #7 (`valhalla-costing-profiles--research.md`
  profile tables, `:19-40`). Clients never send raw costing options.
- Wrapper derives Valhalla per-location `radius` / `search_cutoff` from
  `max_snap_distance_m` (see snap semantics).

**Response (success)**
```jsonc
{
  "status": "ok",
  "profile": "adv_balanced",
  "data_version": "us-west-260715",         // §3 — from tile_manifest.json
  "distance_m": 18240,
  "duration_s": 1620,
  "geometry": {
    "encoding": "points",
    "points": [ { "lat": 44.12, "lon": -121.34 }, /* … decoded from polyline6 */ ]
  },
  "snap": {
    "from": { "lat": 44.1201, "lon": -121.3398, "distance_m": 4.2 },
    "to":   { "lat": 44.3098, "lon": -121.0203, "distance_m": 11.7 }
  }
}
```

**Response (no route / snap failure)** — HTTP 422, structured (never leak raw Valhalla
error text):
```jsonc
{
  "status": "no_route",
  "reason": "no_path_between_locations",   // | "endpoint_unsnappable" | "snap_too_far"
  "detail": { "endpoint": "to", "nearest_snap_distance_m": 812 }
}
```
Valhalla raw behavior mapped here: `/route` "No path could be found for input" →
`no_path_between_locations`; an endpoint that can't correlate within `search_cutoff` →
`endpoint_unsnappable`; a snap beyond `max_snap_distance_m` → `snap_too_far`
(wrapper-enforced). See OQ-8 on a distinct reason for `highway=path`-blocked
connectors.

### 2b. Whole-route enrichment — `POST /trace/attributes`

Map-matches an imported GPX track and returns per-segment surface metadata. Thin
projection over Valhalla `trace_attributes` with **chunking made explicit in the
contract** (#6).

**Request**
```jsonc
{
  "points": [ { "lat": 44.1, "lon": -121.3 }, /* … up to the point cap */ ],
  "profile": "adv_balanced",
  "geometry": "points"
}
```
- Input is the app's native `TrackPoint[]` (`{lat, lon, ele?}`) — the exact shape
  `/api/route-effort` accepts (`routeEffortApi.ts:74-116`). Reuse that validator's
  caps: 5 MB body, 100k points.

**Chunking contract (explicit — the #6 obligation):**
- The **wrapper** splits `points` into windows of **≤ 200 km and ≤ the shape-point
  cap** before calling Valhalla, then **stitches** results into one continuous
  response. Clients send the whole GPX, unaware of chunking (`NOTES.md:27,31`).
- Chunk boundaries land on existing input points; the wrapper de-duplicates the seam
  edge so a boundary edge isn't double-counted.
- The response reports what happened via `chunks` (below) for debuggability.

**Response (success)** — *field naming aligns with #9's `SurfaceSegment`; the
normalized fields are #9-owned. See the callout.*
```jsonc
{
  "status": "ok",
  "profile": "adv_balanced",
  "data_version": "us-west-260715",
  "match": {
    "match_rate": 0.99,             // matched / input points (#6: 98–100%)
    "unmatched_count": 3,
    "snap_p90_m": 6.8,              // #6 tracks these per route
    "snap_max_m": 29.0
  },
  "chunks": [ { "start_index": 0, "end_index": 640, "distance_m": 199400 }, /* … */ ],
  "segments": [
    {
      "begin_index": 0,            // index into `points` (Valhalla begin/end_shape_index, remapped)
      "end_index": 12,
      "distance_m": 430,
      "surface_raw": "gravel",     // Valhalla Surface enum verbatim (#9 calls this valhallaSurface)
      "use": "track",              // Valhalla edge.use ("track" vs "road"); #6 finding 3
      "road_class": "unclassified" // Valhalla edge.road_class; #6 finding 4
      // surface_class / roughness / isTrack are #9's NORMALIZED outputs — NOT emitted here (see callout)
    }
  ],
  "geometry": { "encoding": "points", "points": [ /* matched shape, decoded */ ] }
}
```

> ⚠ **#9 DEPENDENCY — grill #9 first (now sharper, not gone).** A #9 strawman now
> exists (`specs/surface-normalization--grilling-prep.md`). It proposes the app-facing
> shape `SurfaceSegment = { startM, endM, lengthM, surfaceClass, roughness, isTrack,
> roadClass, valhallaSurface, confidence }` (#9 §3a) with a 6-bucket taxonomy +
> `roughness ∈ [0,1]` (#9 §2). **Open questions #9 has NOT settled that gate this
> contract:** (a) whether `edge.use=track` *promotes* roughness or rides as a separate
> `isTrack` flag (#9 OQ-3); (b) where surface is computed and how it reaches the web
> effort model — per-point in `PointData` vs a separate `SurfaceSegment[]` the web
> resamples (#9 OQ-6). Decision (b) directly shapes THIS response: if #9 wants
> per-point surface, #10 may need to emit surface aligned to input indices, not just
> per-edge segments.
> **Strawman for #10:** emit the stable RAW inputs (`surface_raw` = #9's
> `valhallaSurface`, `use`, `road_class`, plus `begin_index`/`end_index`/`distance_m`)
> and let #9 own `surfaceClass`/`roughness`/`isTrack`/`confidence`. Coordinate field
> names so #10's `surface_raw`/`use`/`road_class` feed #9's mapping 1:1. **Do not lock
> the enrichment response until #9 OQ-3 and OQ-6 are answered.**

**Response (partial / no match)** — HTTP 200 `status:"partial"` when some points
matched; HTTP 422 `status:"no_match"` when effectively nothing matched:
```jsonc
{ "status": "partial", "match": { "match_rate": 0.42, "unmatched_count": 380, … },
  "segments": [ /* matched portion only */ ], "data_version": "…" }
```
Unmatched points are Valhalla `matched_points[].type == "unmatched"` (no `edge_index`).
A hard chunk failure (a 200 km-exceeding chunk that still errors) surfaces as
`status:"error"` with the offending chunk index — never a raw 400.

### Geometry encoding — recommendation

**Return decoded `{lat, lon}` point arrays by default** (`geometry:"points"`),
grounded in fact 1: the app's entire route model is `TrackPoint[]` fed straight into
`L.polyline` (`RouteMapView.tsx:328-329,441`); routes are never GeoJSON internally.
Offer `polyline6` as an opt-in for payload-sensitive callers (Valhalla's native
`shape` is polyline6 — the wrapper passes it through un-decoded), and `none` to skip
geometry when only attributes are wanted. **Do not default to GeoJSON** — it's an
overlay-only format here and would force an unwanted client conversion. (GeoJSON could
be a third opt-in if a non-app consumer ever needs it.)

### Snap semantics — strawman

- **Max snap distance is a first-class contract concept.** Valhalla exposes per-
  location `radius` (default 0) and `search_cutoff` (default 35 km); neither hard-
  rejects a far snap, so the wrapper must. Strawman: `max_snap_distance_m` **default
  100 m** for connector endpoints; if the nearest correlated edge exceeds it, return
  `no_route/snap_too_far` rather than silently routing from a wrong road. 100 m is
  generous vs #6's observed p90 ≤ 7 m / max ~31 m (`NOTES.md:10-16`) but leaves
  headroom for faint-track endpoints (#7 pitfall 9: low-class edges are snap-radius
  sensitive, `valhalla-costing-profiles--research.md:120`).
- **Report the snapped point and its distance.** Connector: `snap.from/to` with
  `{lat, lon, distance_m}`. Enrichment: aggregate `snap_p90_m` / `snap_max_m` from
  `matched_points[].distance_from_trace_point` — the same numbers #6 reports.
- **Do not expose raw `radius`/`search_filter`/`search_cutoff`** to clients; the
  wrapper derives them from `max_snap_distance_m` and the profile.

---

## 3. Strawman versioning (ties responses to a tile build) — provenance gap now CLOSED

**Every response carries `data_version`**, a stable string identifying the tile build
the answer came from. **This is now grounded in a real artifact:**
`deploy/valhalla-unraid/build-tiles.sh` writes `tile_manifest.json` at build time
(`build-tiles.sh` step 5, `:writes data_version, extract_sha256, valhalla_version`):

```jsonc
{
  "region": "us-west",
  "data_version": "us-west-260715",     // us-west-<YYMMDD>, from the Geofabrik extract date
  "extract_date": "260715",
  "extract_sha256": "…",
  "valhalla_image": "ghcr.io/valhalla/valhalla:3.8.2",
  "valhalla_version": "…",
  "built_at": "2026-07-15T…Z"
}
```

- **Format:** `us-west-<YYMMDD>` (e.g. `us-west-260715`), derived from the resolved
  Geofabrik filename in the build script — **not** the free-form `us-west-2026-07-16`
  the prior prep guessed. Use the real handle.
- Mirrors the route-effort precedent, which already stamps `engineVersion`
  (`routeEffortApi.ts:156`) / `profileVersion` (`:178`) into responses. Same idea, for
  tiles.
- **Why it matters to the contract:** an enrichment or route result is valid only for
  the tile build that produced it. If the app caches enrichment against an imported
  GPX, `data_version` lets it invalidate when tiles are rebuilt, and lets the UI show
  "surfaces as of `<extract_date>`."
- **Also expose `GET /version`** → `{ data_version, valhalla_version, extract_date,
  profiles: ["adv_balanced","avoid_highways"], region: "us-west" }` for cheap
  capability discovery and cache-keying without a routing call.

**[NEW OQ — the remaining gap is plumbing, not provenance] How does the Worker learn
`data_version`?** The manifest is a *file on the NUC* (`/mnt/user/appdata/valhalla/
tile_manifest.json`), and Valhalla's own `/status` exposes `valhalla_version` +
`tileset_last_modified` but **not** our custom `data_version` string. Options:
(a) the serve container also serves the manifest at a path the Worker fetches through
the Tunnel (add a tiny static route to the serve image); (b) bake `data_version` into
the Worker as a build-time env var / secret updated when tiles are rebuilt; (c) fall
back to `/status`'s `tileset_last_modified` and drop the human-friendly string.
Strawman: (a) — the manifest already exists and is the source of truth; the Worker
reads it once and caches it. This is OQ-6 (reframed) below.

---

## 4. Strawman exposure plan — concrete for `Worker → Tunnel → NUC`

The topology is now fixed (#8). The Worker is the **only** public surface; the
Valhalla origin is reachable **only** through the Cloudflare Tunnel, and the home NUC
has **no open ports** (`README.md:153-161`). Reuse the app's edge machinery (fact 2 +
fact 3):

- **Keep the origin genuinely private (the key #8 detail to nail).** A Cloudflare
  Tunnel gives the NUC a public *hostname* by default — that hostname must NOT be an
  open Valhalla. Strawman: put **Cloudflare Access with a service-token policy** on the
  Tunnel hostname; the Worker attaches `CF-Access-Client-Id` / `CF-Access-Client-
  Secret` (stored as Worker secrets) on every origin subrequest. Public internet hits
  the Tunnel hostname → **403**; only the Worker passes. This is the concrete mechanism
  that makes "front the container, never expose Valhalla" real, not just a diagram.
  (Alternative: a shared secret header checked by a small origin guard — Access service
  tokens are the CF-native way and add no NUC code.)
- **CORS (genuinely new — route-effort has none).** `server.ts` sets **no CORS headers
  anywhere today** (verified: no `Access-Control-*` in `apps/planner/src/server.ts`),
  because route-effort is machine-to-machine (native URLSession, `routeEffortApi.ts:
  11-13`). The routing endpoints are **browser-facing**, so they DO need CORS.
  Strawman: allow-list the gpxplore-web origin(s) for `Access-Control-Allow-Origin` +
  handle `OPTIONS` preflight for `POST`+JSON. See OQ-4 (strict allow-list vs `*`).
- **Rate limiting.** Reuse `apiRateLimit.ts` verbatim: Cloudflare Workers Rate Limiting
  binding, per-IP via `CF-Connecting-IP` (`apiRateLimit.ts:30`), **fail-open**
  (`:8-13,44-50`), 429 + `Retry-After: 60` (`:52-64`). Wire it exactly like
  route-effort does in `server.ts:315-320`. Give routing its **own budget scope(s)**.
  Strawman: **separate scopes** for `/route/connector` (cheap: 1 origin call) vs
  `/trace/attributes` (expensive: N chunked origin calls) so the expensive endpoint
  can't be hammered on the cheap one's budget.
- **Abuse protection.**
  - Body/point caps from route-effort (5 MB, 100k points) bound worst-case chunk
    fan-out.
  - Cap chunk count per enrichment (reject an absurdly long GPX up front with 413, or
    degrade to a documented max coverage — OQ-3).
  - Cloudflare's edge already supplies bot/DDoS/WAF (`README.md:157`). Turnstile /
    proof-of-work only if abuse materializes — not v1.
  - `withSecurityHeaders` (`server.ts:102-108`, `SECURITY_HEADERS:90`) applies to every
    response.
- **[NEW] Graceful origin-down behavior (home-NUC reality).** #8 accepts that
  availability is tied to home power/internet/NUC uptime. The contract must define this:
  when the Tunnel origin is unreachable, return a clean **HTTP 503
  `{ "status": "origin_unavailable" }`**, never a raw fetch error or a hang. The app
  degrades gracefully (imported GPX is still ground truth; only on-demand routing is
  down). Consider a short Worker-side timeout + this 503.
- **Local-first stance intact.** Like the campgrounds edge API
  (`campgrounds-edge-api--planned.md:28-36`), this serves a *reference capability*, not
  user data: no accounts, no user routes leave the browser, no storage binding, no
  geometry logging (route-effort's stateless posture, `routeEffortApi.ts:11-13`, carries
  over).

---

## 5. OPEN QUESTIONS (the real decisions for the human)

Ordered so #9-gated questions are flagged; grill #9's surface schema before locking §2b.

1. **Wrapper placement: all-in-Worker vs a NUC-side chunk helper.** #8's fixed topology
   makes the Worker the clear owner of CORS/rate-limit/versioning/profile-mapping. The
   only live sub-question: does chunk-and-stitch of `trace_attributes` fit the Worker
   CPU budget (~10 ms free / 30 s paid), or does it move to a thin NUC-side helper
   behind the same Tunnel? Depends on the target CF plan and measured stitch cost. (§1)

2. **⚠ #9-GATED — grill #9 FIRST. Enrichment response shape.** #9's strawman now
   exists but leaves two questions that shape THIS response: (a) does `edge.use=track`
   promote roughness or ride as a separate `isTrack` flag (#9 OQ-3)? (b) does surface
   reach the web effort model per-point (in `PointData`) or as a separate
   `SurfaceSegment[]` the web resamples (#9 OQ-6)? If per-point, #10 may need to emit
   surface aligned to input indices, not just per-edge segments. Strawman: #10 emits raw
   `surface_raw`/`use`/`road_class` per edge + indices; #9 normalizes. (§2b)

3. **Chunking semantics under partial failure + max length.** When one 200 km chunk
   matches poorly but neighbors are fine, is the whole request `partial`, or do we
   return per-chunk match quality and let the client decide? And what's the max GPX
   length accepted before we reject (413) vs silently truncate coverage? (§2b, §4)

4. **CORS policy for a no-accounts public endpoint.** Strict allow-list of the
   gpxplore-web origin, or `*`? Route-effort chose *no CORS* (native only); routing is
   browser-facing so it needs *some* policy. `*` makes it a free public routing API for
   anyone (abuse + home-NUC cost surface); allow-list breaks third-party/native reuse.
   (§4)

5. **Profile exposure surface.** Lock clients to the two named profiles
   (`adv_balanced`, `avoid_highways`) only, or allow the optional third `road_touring`
   #7 floated (`valhalla-costing-profiles--research.md:98,128`)? Raw `costing_options`
   stays server-side either way — but how many named profiles ship in v1? (§1, #7)

6. **[REFRAMED — provenance gap now CLOSED] How does the Worker read `data_version`?**
   `tile_manifest.json` now exists and is the source of truth (`build-tiles.sh` step 5),
   so the *provenance* question the prior prep raised is resolved. The remaining
   question is plumbing: does the serve container expose the manifest for the Worker to
   fetch through the Tunnel, is it baked into the Worker as a build-time env var, or do
   we fall back to Valhalla `/status`'s `tileset_last_modified`? Strawman: serve the
   manifest. (§3)

7. **`geometry:"points"` default vs polyline6.** Decoded arrays match the app
   (`gpx.ts:5`, `RouteMapView.tsx:328-329`) but are ~2–3× the wire payload of polyline6.
   Is decoded the right *default* (with polyline6 opt-in), or default to polyline6 and
   decode client-side? (§2 geometry)

8. **`highway=path` connector coverage (latent, from #7).** Motorized costing can't
   traverse `path`-tagged ways without a build-time Lua change
   (`valhalla-costing-profiles--research.md:104,126`; #9 OQ-7). Not an API-contract
   decision per se, but connector `no_route` responses will sometimes trace to this.
   Does the contract need a distinct `reason` (e.g. `blocked_non_motorized_tagging`) so
   the app can explain it? (§2a)

9. **[NEW] Origin-down contract.** Is HTTP 503 `origin_unavailable` the right shape when
   the home NUC is offline, and what Worker-side timeout triggers it? Should the app
   cache the last `data_version` so it can label stale results while the origin is down?
   (§4)
