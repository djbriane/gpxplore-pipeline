# Surface Normalization & Effort-Model Integration — SPEC (decision-complete)

> **STATUS: SETTLED.** Supersedes the strawman `surface-normalization--grilling-prep.md`.
> Closes wayfinder ticket **djbriane/gpxplore-pipeline#9** (parent #4; blocked-by #6; blocks #10).
> Decisions reached via `/grilling` on 2026-07-16. An implementing agent should be able to
> execute this without further decisions.
>
> Grounding sources (all re-verified 2026-07-16):
> - `prototypes/bdr-mapmatch/NOTES.md` + `results.txt` (#6 map-match samples)
> - `specs/valhalla-costing-profiles--research.md` (#7)
> - Valhalla source: `lua/graph.lua`, `src/mjolnir/pbfgraphparser.cc:1480–1573,3020–3071`
>   (surface tag parsing + the road-class default fallback), `src/tyr/trace_serializer.cc:152`
>   (`edge.way_id` emission), `src/sif/motorcyclecost.cc:73–81` (`kSurfaceFactor`),
>   `valhalla/baldr/graphconstants.h` (enums)
> - The existing effort model in `gpxplore-web/packages/route-components/src/lib/` (cited by file:line)

---

## 0. Framing — what this ticket decides

1. **A decision-complete surface data contract** (taxonomy + per-segment + summary schemas + provenance), and
2. **Precisely how surface feeds the existing per-bin effort model** — as a fourth multiplicative factor, *additive to the algorithm*, mirroring how switchbacks were added.

Two facts set the foundation:

- **#6 proved raw OSM `tracktype`/`smoothness` are NOT exposed by `trace_attributes`** — Valhalla returns its own pre-normalized 8-value `Surface` enum, folded at tile-build time. So v1's taxonomy maps FROM `{Valhalla Surface enum + edge.use + edge.road_class}`.
- **There is no surface-calibrated corpus.** The effort constants (`K_CURV`/`K_GAIN`/`K_SWITCHBACK`) were locked against a 7-ride elevation+curviness corpus. Therefore surface ships **inert** in the live band (`K_SURFACE = 0`) — built, wired, visible in the `?load=1` debug panel, but not moving any route's band until a surface-inclusive corpus calibrates it.

---

## 1. The reliability problem this spec must not paper over

Valhalla's surface is **crowd-sourced OSM tags**, not sensed data. At tile build, `pbfgraphparser.cc:1480–1573` maps OSM `surface`/`tracktype`/`smoothness`/`mtb:scale`/`sac_scale` to the enum. **When a way has none of those tags, Valhalla fabricates a surface from road class + use** (`pbfgraphparser.cc:3020–3071`):

```
no surface tag → assign from road_class + use:
   motorway…residential, unclassified   → paved_smooth
   use = track                          → dirt
   use = path / footway / bridleway     → compacted
   everything else                      → paved
```

Consequences that shape every decision below:

- **"0% unknown" (#6) is a *coverage* metric, not *accuracy*.** No code path returns "unknown" for a routed edge — untagged edges get a class-based guess. `trace_attributes` exposes no tag provenance, so a surveyed `surface=gravel` is indistinguishable from a guess.
- **The dominant failure is *under-counting*, not gaps.** An untagged backcountry `highway=unclassified` road that is really gravel returns `paved_smooth` → reads as effortless. That is exactly the sparse-data terrain the connector-routing feature (#4) targets. #6's clean numbers came from well-surveyed *official* BDRs and **do not transfer** to obscure connectors.

**Mitigation (§2): a build-time provenance sidecar** makes "surveyed vs guessed" visible per segment.

---

## 2. Data build & provenance sidecar

**Rebuild the dataset now**, one coherent `dataVersion`:

- Pull a fresh `us-west-latest.osm.pbf`; on the NUC (#8), in one one-shot pass build **stock Valhalla tiles** (routing-only — **no fork, frozen tile format untouched**) **and** a **provenance sidecar** from the *same* extract. Tiles + sidecar MUST share the extract or the `way_id` join drifts. #8's build script already rebuilds tiles+admins+timezones+manifest (~22 min).
- **Sidecar** = lookup keyed by OSM way id:

  ```
  way_id → {
    hasExplicitSurface: bool,     // v1 provenance signal
    surface, tracktype,           // raw OSM tags — captured, UNUSED in v1
    smoothness, mtb_scale         //   (ready for a v2 taxonomy, no rebuild needed)
  }
  ```

  Rationale: capture the maximum during the one pass; use only the bit now. A future difficulty-oriented taxonomy (mapping from OSM `smoothness`) then needs **no further build**.
- Suggested artifact: compact table (SQLite or parquet) keyed by `way_id`, published alongside tiles and stamped with the same `data_version` (`us-west-<YYMMDD>`) from `tile_manifest.json` (#8).

**The join** (`src/tyr/trace_serializer.cc:152`): `trace_attributes` returns `edge.way_id` per matched edge (request must list `edge.way_id` in the attribute filter). Normalization joins matched edges → sidecar → sets `confidence`:

- `tagged` — `hasExplicitSurface = true`.
- `inferred` — way present, no explicit surface tag (Valhalla road-class default).
- `unknown` — no matched edge at all (map-match gap, or `way_id = 0` synthesized edge).

Caveats: hierarchy **shortcut** edges merge multiple ways, but map-matching snaps to **base** edges, so the join is reliable for backcountry; `way_id = 0` → `unknown`.

---

## 3. Taxonomy — `surface_class` (v1 maps from the Valhalla enum)

Five buckets + `unknown`. Raw `valhallaSurface` is retained per segment, so re-bucketing is reversible.

| `surface_class` | Maps FROM Valhalla `Surface` | `roughness` | Notes |
|---|---|---|---|
| `paved` | `paved_smooth`, `paved`, `paved_rough` | 0.0 | Baseline; no penalty |
| `compacted` | `compacted` | 0.10 | Graded/maintained smooth unpaved |
| `dirt` | `dirt` | 0.35 | Native soil; **bimodal** (hard-pack ↔ sand) with no sub-signal — moderated from a worst-case 0.5. Documented weakness. |
| `gravel` | `gravel` | 0.40 | Loose surface |
| `rough` | `path` | 1.0 | Rare — `path` seldom routes (see §8) |
| `unknown` | none / `way_id = 0` / no edge | see §5 | Handled though 0% observed in #6 |
| *(excluded)* | `impassable` | — | Valhalla drops from graph; never routed |

- **Roughness ordering is rider-effort, not Valhalla `kSurfaceFactor`.** `kSurfaceFactor` (`motorcyclecost.cc:73–81`) encodes *routing cost* and puts gravel (0.5) > dirt (0.2); we deliberately set `dirt (0.35) ≈ gravel (0.40)` on rider intuition (sand/washboard dirt can rival loose gravel). All scalars are **inert at `K_SURFACE = 0`** and are calibration item #1/#2.
- **`isTrack`** (`edge.use = track`) is **orthogonal** — it never rewrites `surface_class`. It rides as a first-class boolean per segment (+ `trackPct` in the summary) and adds a tunable **+0.2** to the roughness scalar (capped 1.0).

---

## 4. Schemas

### 4a. Per-segment (one per matched Valhalla edge)

```ts
type SurfaceClass = "paved" | "compacted" | "dirt" | "gravel" | "rough" | "unknown";

type SurfaceSegment = {
  startM: number;          // cumulative distance from route start (m); continuous across chunk seams
  endM: number;
  lengthM: number;         // == edge.length; length-weighting unit
  surfaceClass: SurfaceClass;
  roughness: number;       // 0..1 = f(surface, isTrack); the effort input (inert v1)
  isTrack: boolean;        // edge.use === "track"
  roadClass: RoadClass;    // normalized road_class enum string
  valhallaSurface: string; // raw Valhalla enum, retained for debug / re-bucketing
  confidence: "tagged" | "inferred" | "unknown";  // provenance, grounded via §2 sidecar
  osmTags?: {              // raw OSM tags from sidecar, present when confidence === "tagged"
    surface?: string; tracktype?: string; smoothness?: string; mtb_scale?: string;
  };
};
```

### 4b. Route-summary (display-only — effort feeds off per-point roughness, §6)

```ts
type SurfaceSummary = {
  byClassM: Record<SurfaceClass, number>;   // meters per bucket (the mix-bar, authoritative UI)
  dominantClass: SurfaceClass;
  pavedPct: number;
  unpavedPct: number;
  trackPct: number;                          // Σ lengths where isTrack
  roughnessMean: number;                     // length-weighted; display stat (NOT the effort feed)
  taggedPct: number;                         // provenance honesty metric
  inferredPct: number;                       // Valhalla-guessed share → drives UI disclosure
  unknownPct: number;                        // true match gaps
  engineVersion: string;                     // mirrors EFFORT_ENGINE_VERSION (algorithm stamp)
  dataVersion: string;                       // us-west-<YYMMDD> — pins to tiles + sidecar extract
};
```

`roughnessP90` is **not** shipped: the per-bin effort path (§6) already preserves a short brutal stretch, so a route-level peak is redundant. Add later only if a single display number is wanted.

### 4c. Composite service response (input to #10)

```ts
type SurfaceEnrichment = {
  segments: SurfaceSegment[];   // UI source of truth
  pointRoughness: number[];     // 1:1 with submitted GPX points → effort
  summary: SurfaceSummary;
};
```

---

## 5. Confidence / missing handling

- **`inferred`** (Valhalla road-class default): carry the value, flag it, count in `inferredPct`. Not silently trusted — the UI discloses (§7).
- **`unknown`** (no matched edge): road_class-infer roughness where a road class is present (motorway/trunk/primary → ~0 paved; unclassified/service_other → ~0.4); else assign route `roughnessMean`. **Never** default to paved-0 (would silently deflate effort). Counted in `unknownPct`.
- `#6` observed 0% unknown, but `highway=path` geometry (§8) and off-corpus tracks will produce both `inferred` and `unknown`.

---

## 6. Integration into the existing effort model (additive; file:line verified)

Effort lives in `gpxplore-web/packages/route-components/src/lib/`. Surface is a **fourth multiplicative factor**, structurally identical to how switchbacks were added (default-0, folds into the product — backward-compatible for pre-surface cached routes).

**Effort-miles (primary metric).** In `computeBinEffortMiles` (`effortMiles.ts:101–116`), extend `BinEffortInput` (`:74–84`) with `surfaceRoughness?: number` (default 0) and add:

```ts
const surfSlow = 1 / (1 + K_SURFACE * (bin.surfaceRoughness ?? 0));
const slowdown = Math.min(SLOWDOWN_CAP, 1 / (curvSlow * gradeSlow * switchSlow * surfSlow));
```

`K_SURFACE = 0` in `EFFORT_CONSTANTS` (`:34–55`) ⇒ **byte-identical live band** until calibrated. `roughness` is already [0,1], so (unlike `switchbackScore` / `SWITCHBACK_AXIS_MAX`) it needs no normalizing divisor.

**Energy load (`?load=1` debug/UI — where surface IS visible).** In `computeBinEnergyLoadBreakdown` (`routeLoad.ts:116–151`, `:139`):

```ts
const surfaceTerm = wSurface * normRoughness;    // normRoughness = surfaceRoughness (0..1)
const curvyMult   = 1 + curvyTerm + switchbackTerm + surfaceTerm;
```

`wSurface` is a strawman debug weight so surface can be eyeballed/tuned before it goes live.

**Plumbing.** Surface arrives from map-matching **per-point** (`pointRoughness[]`, 1:1 with submitted GPX points), not from GPX geometry:

- Add `surfaceRoughness: number` to `PointData` (`gpx.ts:664–669`), populated directly from `pointRoughness[]`.
- The existing bin loop (`elevationProfile.ts:195–231`) length-accumulates it per bin (length-weighted mean, mirroring curviness at `:218–222,238`), flowing into `computeBinEffortMiles` at `elevationProfile.ts:258`.
- Per-bin (not one route multiplier) because surface varies along a route — the whole model is per-bin for exactly this reason.

**Cache invalidation.** Bump `EFFORT_ALGO_VERSION 2→3` (`effortMiles.ts:30`), `EFFORT_ENGINE_VERSION` (`routeEffortCache.ts:20`), and `PROFILE_VERSION` (`trackProfile.ts:15`).

---

## 7. UI provenance disclosure

Surface is presented as **"from OpenStreetMap — community-tagged where available; N% inferred from road type"**, N driven by `inferredPct`. The mix-bar (`byClassM` → percentages) is the authoritative surface UI; `dominantClass` a convenience label.

---

## 8. `highway=path` blind spot (from #7)

`motorcycle` costing cannot route `highway=path` (`lua/graph.lua:30`), so path-tagged geometry never enters the matched graph — those meters get no surface and fall to the §5 `unknown` policy. **Accepted + documented** as a limitation (cross-link #7). No Lua/tile-build change in #9; that decision belongs to #5/#7. Not observed on official BDRs.

---

## 9. Contract notes for #10 (routing service API)

- `trace_attributes` requests must include `edge.way_id` (plus the #6 attribute set: `edge.surface`, `edge.road_class`, `edge.use`, `edge.length`, `edge.id`) in the filter.
- `trace_attributes` caps at **200 km / bounded shape points** (#6): whole-route enrichment must **chunk** and the service must **stitch** segments across seams into continuous `startM`.
- The service performs the sidecar join and returns the §4c composite. Responses carry `dataVersion` (tiles + sidecar) so an enrichment is pinned to a build.

---

## 10. Deferred / calibration backlog

1. **`K_SURFACE` magnitude** — no surface corpus yet; the single biggest open number. Until set, surface stays inert in the live band.
2. **Roughness values/ordering**, especially bimodal `dirt` (0.35) and the `isTrack` +0.2 bump.
3. **v2 taxonomy** — normalize from raw OSM `smoothness` (a real difficulty scale) for `tagged` ways; the sidecar already carries it, so no rebuild.
4. **Down-weight `inferred` segments' effort contribution** once calibration begins (they are Valhalla guesses).
5. **Reliability & limitations** (this §1 + the trade-offs) travel as a first-class section for anyone who later raises `K_SURFACE`: mixed provenance (measured GPX geometry vs crowd-sourced surface), snap-error → wrong-surface (unmeasured), edge-level granularity (no sub-segment variation), surface *type* ≠ *difficulty*, weather/season absent, and double-counting risk with curviness/switchback.
