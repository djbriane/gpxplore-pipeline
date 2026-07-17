# Surface Normalization & Effort-Model Integration — GRILLING PREP

> **⚠️ SUPERSEDED 2026-07-16 by `surface-normalization--planned.md`** (settled via `/grilling`).
> Kept for provenance only. Notable changes from this strawman: `dirt` moderated to 0.35 (rider
> ordering, not `kSurfaceFactor`); a build-time **provenance sidecar** (`way_id → raw OSM tags`,
> joined via `edge.way_id`) makes `tagged` vs `inferred` visible — closing the "0% unknown is
> coverage, not accuracy" gap; per-point `pointRoughness[]` wiring; `roughnessP90` dropped;
> fresh-extract tile+sidecar rebuild. See the planned spec for the authoritative contract.
>
> **STATUS: STRAWMAN for grilling — NOT a settled decision.**
> This document exists so a human can be grilled fast against a grounded proposal.
> Everything below is a *proposal + the open questions only the human can answer*.
> Feeds wayfinder ticket **djbriane/gpxplore-pipeline#9**.
>
> **Refreshed 2026-07-16** — every code claim below re-verified against current source
> (Valhalla clone + gpxplore-web). All effort-model file:line citations confirmed to
> still hold at this date. Two refresh changes vs the prior draft: (a) the `roughness`
> scale is now **anchored to Valhalla's own `motorcycle` per-surface cost array**
> (source-grounded, and it *reorders* dirt vs gravel — see §2); (b) `data_version`
> provenance (now decided in #8) is threaded into the summary schema (§3b).
>
> Grounding sources (all re-read for this refresh):
> - `prototypes/bdr-mapmatch/NOTES.md` + `results.txt` (#6 map-match prototype — real attribute samples)
> - `specs/valhalla-costing-profiles--research.md` (#7 costing research)
> - Valhalla source: `/Users/djbriane/Development/gpxplore/valhalla/valhalla/baldr/graphconstants.h`
>   (enums) and `/Users/djbriane/Development/gpxplore/valhalla/src/sif/motorcyclecost.cc:73-81`
>   (the per-surface cost array — new anchor)
> - **The existing effort model** in `gpxplore-web/packages/route-components/src/lib/` (re-read; cited by file:line throughout)

---

## 0. The one thing to get right first

The ticket header says "how do raw OSM/Valhalla attributes normalize." **#6 proved raw OSM `tracktype`/`smoothness` are NOT available.** Valhalla's `trace_attributes` returns its OWN pre-normalized enums, folded at tile-build time. So the taxonomy maps **FROM {Valhalla `Surface` enum + `edge.use` + `edge.road_class`}**, not from OSM tags. This doc is built on that fact.

And the integration is **additive**: the effort model already combines curviness + grade + switchbacks multiplicatively per bin. Surface becomes a **fourth factor in that same product** — not a new algorithm. The template already exists in the code: switchbacks were added exactly this way (default-0, folds into the product), and we copy that pattern verbatim.

---

## 1. The full Valhalla enums (the actual input domain) — VERIFIED 2026-07-16

**`Surface` enum — 8 values** (`graphconstants.h:655-663`, string map `:667-674`; lower = smoother):

| # | enum | string (trace_attributes) | seen in #6? |
|---|---|---|---|
| 0 | `kPavedSmooth` | `paved_smooth` | yes (16% agg) |
| 1 | `kPaved` | `paved` | no |
| 2 | `kPavedRough` | `paved_rough` | no |
| 3 | `kCompacted` | `compacted` | yes (<1%) |
| 4 | `kDirt` | `dirt` | yes (27%) |
| 5 | `kGravel` | `gravel` | yes (56%) |
| 6 | `kPath` | `path` | no |
| 7 | `kImpassable` | `impassable` | no (routing removes it) |

**`edge.use` (full enum `graphconstants.h:302-`, strings `:360-`)** — relevant motorized values: `road`(0), `track`(3), `driveway`(4), `alley`(5), `parking_aisle`(6), `service_road`(11); pedestrian `path`(27). #6 saw `road`/`track` dominate (track 0–39% by route), plus a trace of `driveway`.

**`road_class` enum — 8 values** (`graphconstants.h:135-143`, strings `:157-164`): `motorway`(0), `trunk`(1), `primary`(2), `secondary`(3), `tertiary`(4), `unclassified`(5), `residential`(6), `service_other`(7). #6 backcountry was dominated by `unclassified` / `service_other` / `tertiary`.

Extracted per matched edge in the prototype: `edge.surface`, `edge.road_class`, `edge.use`, `edge.length`, `edge.id` (`prototypes/bdr-mapmatch/mapmatch.py`, `filters.attributes` block).

**NEW — Valhalla's own per-surface cost weights (the roughness anchor).** #7 already surfaced this but §2 now leans on it: the `motorcycle` costing model carries a hard-coded per-surface cost array indexed by the `Surface` enum (`src/sif/motorcyclecost.cc:73-81`):

```
kSurfaceFactor[] = { paved_smooth 0.0, paved 0.0, paved_rough 0.0,
                     compacted 0.1, dirt 0.2, gravel 0.5, path 1.0 }
```

This is Valhalla's OWN judgement of how much each surface costs a motorcycle — and it puts **`gravel` (0.5) meaningfully rougher than `dirt` (0.2)** (loose gravel is slower/harder than packed native soil). It is a source-grounded, calibration-free ordering we can borrow directly as the strawman `roughness`, rather than inventing numbers. **This reverses the prior draft's `dirt 0.60 > gravel 0.50` — flagged as a decision in §5.**

---

## 2. STRAWMAN `surface_class` taxonomy (the mapping at a glance)

Proposed **6 app-facing buckets + `unknown`**, with a scalar `roughness ∈ [0,1]` that becomes the effort input. **Refresh: `roughness` now mirrors Valhalla's `kSurfaceFactor` (§1) rather than hand-picked values** — same domain [0,1], but source-grounded and with gravel > dirt:

| `surface_class` | Maps FROM Valhalla `Surface` | `roughness` (strawman, ← kSurfaceFactor) | Notes |
|---|---|---|---|
| `paved` | `paved_smooth`, `paved` | 0.0 | Baseline; no effort penalty |
| `paved_broken` | `paved_rough` | 0.05 | kSurfaceFactor treats it as 0.0; nudge up so a rider still feels it |
| `compacted` | `compacted` | 0.10 | Graded/maintained smooth unpaved |
| `dirt` | `dirt` | 0.20 | Native soil, variable |
| `gravel` | `gravel` | 0.50 | Loose surface — Valhalla's costliest non-path |
| `rough` | `path`, **and** any unpaved `Surface` promoted by `edge.use=track` (see below) | 1.0 | 2-track / 4x4 / faint |
| `unknown` | `none` / null / missing | see §3c | 0% observed in #6, still handled |
| *(excluded)* | `impassable` | — | Valhalla drops from graph; never routed |

Note the roughness column is no longer monotonic with bucket order (`gravel` 0.50 > `dirt` 0.20) — that is deliberate and inherited from Valhalla's cost model. Bucket *ordering* for UI can still read paved→compacted→dirt→gravel→rough; only the numeric effort input follows kSurfaceFactor.

**How `edge.use=track` modulates (STRAWMAN — open question):** a graded `dirt` *road* (`use=road`) rides very differently from a `dirt` *2-track* (`use=track`). Proposal: `use=track` **promotes** `compacted`/`dirt`/`gravel` by one step toward `rough` (or bumps `roughness` by +0.2, capped at 1.0), and is *also* carried verbatim as an `isTrack` boolean so the app can badge "2-track" independently of effort. #6 saw 28% (WABDR) / 39% (Steens) `track` — material, not noise. (Note: Valhalla *separately* penalizes `highway=track` in routing via `use_tracks`/`kMaxTrackFactor`; that is a routing cost, distinct from our rider-effort roughness — don't conflate.)

**How `road_class` modulates:** largely **orthogonal to surface** and kept as its own normalized field (`paved connector` vs `backcountry`), driving map styling / route-type labeling rather than roughness directly. Its **one effort-relevant job** is the missing-tag fallback (§3c): if `Surface=unknown`, infer roughness from `road_class` (motorway/trunk/primary → likely paved 0.0; unclassified/service_other → likely unpaved ~0.4).

---

## 3. STRAWMAN schemas

### 3a. Per-segment (one per matched Valhalla edge)

```ts
type SurfaceSegment = {
  startM: number;          // cumulative distance from route start (m)
  endM: number;
  lengthM: number;         // == edge.length; length-weighting unit
  surfaceClass: SurfaceClass;   // the §2 bucket
  roughness: number;            // 0..1 scalar from §2 (the effort input)
  isTrack: boolean;             // edge.use === "track"
  roadClass: RoadClass;         // normalized road_class enum string
  valhallaSurface: string;      // raw enum kept for debug/confidence audit
  confidence: "matched" | "inferred" | "unknown";
};
```
`confidence`: `matched` = Valhalla returned a real surface enum; `inferred` = surface was `none` and we fell back to road_class; `unknown` = neither available.

### 3b. Route-summary

```ts
type SurfaceSummary = {
  byClassM: Record<SurfaceClass, number>;   // meters per bucket
  dominantClass: SurfaceClass;              // max byClassM
  pavedPct: number;                         // paved + paved_broken
  unpavedPct: number;
  trackPct: number;                         // Σ lengths where isTrack
  unknownPct: number;
  roughnessMean: number;    // length-weighted Σ(lengthM·roughness)/ΣlengthM → route-level effort feed
  roughnessP90?: number;    // OPEN (§5.4): length-weighted p90, so a short brutal stretch survives averaging
  engineVersion: string;    // effort/normalization engine stamp (mirrors EFFORT_ENGINE_VERSION)
  dataVersion: string;      // NEW: tile provenance, `us-west-<YYMMDD>` (#8 decision) — pins the surface to a tile build
};
```
This mirrors the mix #6 already reports (e.g. WABDR: gravel 68% / paved_smooth 21% / dirt 11%). **`dataVersion` is new**: #8 finalized a `tile_manifest.json → data_version=us-west-<YYMMDD>` provenance stamp; surface metadata is only as good as the tile build it came from, so a re-enrichment after a tile refresh must be distinguishable. `engineVersion` invalidates on *algorithm* change; `dataVersion` invalidates on *tile* change.

### 3c. Confidence / missing handling
#6 observed **0% unknown across every track**, but the schema must still handle it (the enum has no `none`, but map-matching can leave gaps, and off-corpus tracks — e.g. `highway=path` per #7 — may not match at all). Rule (strawman): `unknown` segments get `roughness` via road_class fallback if possible (→ `confidence:"inferred"`), else route `roughnessMean` (→ `confidence:"unknown"`), and the summary carries `unknownPct` so the UI can disclose low confidence. **Never** let an unknown segment silently read as paved-0 and deflate effort — unless the human decides conservative-paved is the desired default (open Q §5.5).

---

## 4. STRAWMAN integration point into the EXISTING effort model — file:line VERIFIED 2026-07-16

### Where the effort model lives (all citations re-confirmed at current HEAD)
- **`packages/route-components/src/lib/effortMiles.ts`** — the primary metric. `computeBinEffortMiles` (**:101-116**) is the per-bin core:
  `slowdown = min(SLOWDOWN_CAP, 1 / (curvSlow · gradeSlow · switchSlow))` (**:114**), `effortMiles = distanceMi · slowdown` (**:115**). `BinEffortInput` type at **:74-84**. Constants `EFFORT_CONSTANTS` incl. `K_CURV`/`K_GAIN`/`K_SWITCHBACK`/`SLOWDOWN_CAP` at **:34-55**. `EFFORT_ALGO_VERSION = 2` at **:30**. The switchback backward-compat trick lives at **:112** (`bin.switchbackScore ?? 0`).
- **`packages/route-components/src/lib/routeLoad.ts`** — the 0–100 "energy load" (`?load=1` debug). `computeBinEnergyLoadBreakdown` (**:116-151**): `curvyMult = 1 + curvyTerm + switchbackTerm` (**:139**), `raw = base · curvyMult` (**:149**). `LOAD_WEIGHTS` at **:32-43**.
- **`packages/route-components/src/lib/elevationProfile.ts`** — **the wiring hub.** `buildElevationProfileSeries` bins per-point data (loop **:195-231**) then per bin calls `computeBinEnergyLoadBreakdown` (**:242**) and `computeBinEffortMiles` (**:258-267**). **This is exactly where a surface term is threaded in.** Note the deliberate curviness-by-MEAN vs switchback-by-PEAK asymmetry documented at **:166-171** (peak-accumulate logic at **:223-226**) — surface must pick one; strawman = length-weighted mean (§4 plumbing).
- **`packages/route-components/src/lib/routeEffortCache.ts`** — aggregates bin `effortMiles` → route `effortMiles`/`ratio`/`effortTier`/`curviness` (`computeRouteEffortCache` **:41-79**; `ratio = effortMiles/realMiles` at **:76**). `EFFORT_ENGINE_VERSION = "2026-07-01"` at **:20** (the non-JS-client cache stamp).
- **`packages/route-components/src/lib/effortFactor.ts`** — `ratio → EffortBand` (`bandForRatio` **:49-54**) and the meter fill (`effortMeterFill` **:43-47**, MIN 1.3 / MAX 3.2). Only fires for `effortTier === "high"` (**:15,32**).
- **`packages/route-components/src/lib/trackProfile.ts`** — `PROFILE_VERSION = "2026-07-02"` at **:15** (a third stamp to bump on schema/behavior change).
- Per-point carrier: `PointData` (`gpx.ts:664-669`) = `{distanceM, slope, curviness, switchbackScore}`. Route-facing: `RouteStats` (`gpx.ts:369-380`) carries `effortMiles`/`effortTier`/`curviness`. `SWITCHBACK_AXIS_MAX = 400` (`gpx.ts:909`) is the divisor that normalizes switchbackScore to [0,1] before it enters the product — surface `roughness` is *already* [0,1], so it needs no such divisor.

### The proposed add (mirrors how switchbacks were added)

**Effort-miles (primary):** add a fourth multiplicative factor to the bin product — structurally identical to `switchSlow`, which itself is `1/(1 + K_SWITCHBACK·normSwitch)` folded into the denominator (`effortMiles.ts:113-114`):

```ts
// effortMiles.ts — BinEffortInput gains:  surfaceRoughness?: number;  (default 0)
const surfSlow = 1 / (1 + K_SURFACE * (bin.surfaceRoughness ?? 0));
const slowdown = Math.min(SLOWDOWN_CAP, 1 / (curvSlow * gradeSlow * switchSlow * surfSlow));
```
Default `0` ⇒ byte-identical to today for any route without surface data — the same backward-compat trick switchbacks use (`?? 0`, `effortMiles.ts:112`), so pre-surface cached routes are safe.

**Energy load (debug/UI):** add a term to the multiplier (`routeLoad.ts:139`):
```ts
const surfaceTerm = wSurface * normRoughness;      // normRoughness = surfaceRoughness (already 0..1)
const curvyMult   = 1 + curvyTerm + switchbackTerm + surfaceTerm;
```

**Plumbing:** surface arrives per-*edge* from map-matching (a pipeline/#10 service step), NOT from GPX geometry, so it must be resampled onto the same grid as curviness. Strawman: add `surfaceRoughness: number` to `PointData` (`gpx.ts:664-669`), populated by projecting matched `SurfaceSegment[]` onto GPX points; the existing bin loop (`elevationProfile.ts:195-231`) then length-accumulates it per bin (length-weighted mean, same as curviness at **:218-222,238**), and it flows into the `computeBinEffortMiles` call at `elevationProfile.ts:258`. Bump `EFFORT_ALGO_VERSION` 2→3 (`effortMiles.ts:30`) and the `EFFORT_ENGINE_VERSION` / `PROFILE_VERSION` stamps to invalidate caches.

**Why per-bin, not one route multiplier:** surface varies along a route (gravel → paved connector → dirt); the whole model is per-bin for exactly this reason. A single route-level `roughnessMean` multiplier on `ratio` (in `routeEffortCache.ts:76`) is the cheaper fallback if per-point surface plumbing proves too costly — flagged as the lower-fidelity option.

---

## 5. OPEN QUESTIONS (the real decisions for the human)

1. **Roughness *ordering & values* — adopt Valhalla's `kSurfaceFactor` (gravel > dirt), or a rider-effort ordering?** NEW/sharpened. The refresh anchors `roughness` to `motorcyclecost.cc:73-81` (`compacted 0.1, dirt 0.2, gravel 0.5, path 1.0`), which is source-grounded and calibration-free — but it encodes *routing cost*, which may not equal *rider physical effort* (e.g. deep sand/washboard dirt can be brutal despite low routing cost). Prior draft had `dirt 0.60 > gravel 0.50`; kSurfaceFactor says the opposite. **Which authority wins — Valhalla's cost model or rider intuition — and are the exact scalars right?**

2. **Bucket boundaries — is 6 the right count?** Does `paved_smooth` vs `paved` matter to a rider (proposal collapses both → `paved`)? Should `compacted` collapse into `dirt` (it was <1% in #6 and sits at 0.1 vs 0.2)? Is `paved_broken` worth a bucket given it wasn't observed and kSurfaceFactor treats it as 0.0? **Answerable by: what surface distinctions does the app actually surface to a user / feed to effort.**

3. **How much should surface weight effort — pick `K_SURFACE` (and `wSurface`).** There is **no surface-calibrated corpus**: the effort constants were locked against the 7-ride `specs/refs/ridden-gpx/` corpus, which is elevation+curviness only (`effortMiles.ts:15,35-37`). Even with the roughness *scale* borrowed from Valhalla (Q1), `K_SURFACE` — how hard that scale pushes the slowdown — is unconstrained. What magnitude makes `gravel`/`rough` shift a route's band without swamping the curviness signal the corpus validated? Does surface even belong in the primary `effortMiles`/band yet, or only in the `?load=1` debug load until it's calibrated? **This remains the single biggest unanswered number.**

4. **Does `edge.use=track` promote roughness, or ride as a separate `isTrack` flag?** A `dirt` 2-track vs a graded `dirt` road is a real difference and #6 saw 28–39% track on some routes — but folding it into roughness risks double-counting if curviness already captures the technical bits. Promote (+step / +0.2), keep flag-only, or both?

5. **Route-summary aggregation — mean vs dominant vs p90?** Effort wants length-weighted `roughnessMean`; the UI badge may want `dominantClass` or a mix-bar (like #6's "gravel 68% / paved 21%"). Which is authoritative, and does a short brutal `rough` stretch need `roughnessP90` so it isn't averaged away — mirroring how switchbacks bin by **peak** not mean (`elevationProfile.ts:166-171`)? (Schema §3b now carries `roughnessP90?` as optional pending this call.)

6. **Missing/`unknown` handling — fallback policy?** 0% observed in #6, but when it occurs: road_class-infer, treat as route-mean, or conservative paved-0? And how loudly should low `confidence` / `unknownPct` be surfaced to the user vs silently absorbed?

7. **Where does surface get computed and how does it reach the web effort model?** Map-matching is a pipeline/service step (#10, and #6 flagged the ≤200 km `trace_attributes` chunking limit). Does surface ride into `PointData` per-point (requires the service to return surface aligned to GPX points), or as a separate `SurfaceSegment[]` the web resamples onto the bin grid? This plumbing decision gates the per-bin-vs-route-multiplier fidelity choice in §4, and it interacts with #10's 200 km chunking (segments must stitch across chunk seams) and the `dataVersion` stamp (§3b) that pins each enrichment to a tile build.

8. **`highway=path` blind spot (from #7).** `motorcycle` costing can't route `highway=path` (`lua/graph.lua:30`), so path-tagged tracks are absent from the matched graph entirely — those meters get no surface at all (they surface as `unknown`, Q6). Not seen on official BDRs, but is that an accepted gap or a build-time Lua decision that must precede enrichment?
