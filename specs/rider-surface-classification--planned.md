# Rider Surface Classification Across Pipeline, Web, and iOS -- SPEC

> Status: planned
> Scope: gpxplore-pipeline surface enrichment contract, gpxplore-web route profile UI, and gpxplore-ios route/day profile UI.
> Supersedes: `surface-display-labels--planned.md`.
> Background: `surface-normalization--planned.md`, `routing-api-contract--planned.md`, Web surface display specs, iOS companion surface visualization spec, and the July 2026 rider-terminology + classification grilling session.

## Problem Statement

GPXplore already enriches routes with Valhalla-derived road-surface data and displays those surfaces in route profiles, chart coloring, legends, and surface mix cards. The current machine classes are useful as normalized inputs, but the primary UI model is confusing for adventure riders.

Two issues drive this change:

- Valhalla `compacted` is closer to what riders often experience as firm graded unpaved road or hardpack, while Valhalla `gravel` is closer to loose aggregate, sand, pebble, or degraded unpaved. Showing bare `Gravel` makes a loose-surface warning sound like a normal maintained gravel road.
- The current `Two-track` overlay is valuable as a rough-road signal, but the literal rider meaning of two-track is narrower: two tire tracks with grass or vegetation between them. In this data, `isTrack` is better treated as a road-use signal that can refine rider-facing classification, not as a route-level visible overlay.

The goal is to keep the existing machine taxonomy intact while adding a rider-facing, mutually exclusive classification that is easier to understand and sums cleanly to 100% in the UI.

## Solution

Add an additive rider-facing surface classification to the enrichment response. The existing normalized machine fields remain unchanged and continue to be available for compatibility, debugging, and future analysis.

Existing machine classes remain:

- `paved`
- `compacted`
- `dirt`
- `gravel`
- `rough`
- `unknown`

New rider classes:

```ts
type RiderSurfaceClass =
  | "paved"
  | "hardpack"
  | "dirt_road"
  | "primitive_road"
  | "loose_gravel"
  | "rough_trail"
  | "unknown";
```

Derivation:

| Existing inputs | `riderSurfaceClass` | Display label | Meaning |
|---|---|---|---|
| `surfaceClass = paved` | `paved` | Paved | Paved road surface. |
| `surfaceClass = compacted` | `hardpack` | Hardpack | Firm graded unpaved surface. |
| `surfaceClass = dirt`, `isTrack = false` | `dirt_road` | Dirt road | Native-soil road; condition varies. |
| `surfaceClass = dirt`, `isTrack = true` | `primitive_road` | Primitive road | Track-like dirt road; rougher/unmaintained character. |
| `surfaceClass = gravel` | `loose_gravel` | Loose gravel | Loose or soft surface; reduced traction. |
| `surfaceClass = rough` | `rough_trail` | Rough trail | Trail-like or very rough surface; verify rideability. |
| `surfaceClass = unknown` | `unknown` | Unknown | Surface not known. |

Notable locked decisions:

- `compacted + isTrack` remains `hardpack`. Firmness wins; do not make stable hardpack look worse than it is.
- `gravel + isTrack` remains `loose_gravel`. The traction warning wins; do not hide loose/soft surface under a broad primitive-road label.
- `rough` remains path-only in this pass. Do not promote dirt/gravel/track combinations into `rough_trail` without a separate empirical classification audit.
- `paved_rough` remains folded into `paved` because the current normalized pipeline already maps it to `surfaceClass = paved`, and ADV value is low for splitting it now.
- `impassable` remains excluded from normal surface segments and is not part of `RiderSurfaceClass`.
- `unknown` is a real rider bucket when present and participates in the Road Surface distribution denominator.

## API Contract

The enrichment response becomes additive:

```ts
type SurfaceSegment = {
  startM: number;
  endM: number;
  lengthM: number;
  surfaceClass: SurfaceClass;
  riderSurfaceClass: RiderSurfaceClass;
  roughness: number;
  isTrack: boolean;
  roadClass: RoadClass;
  valhallaSurface: string;
  confidence: "tagged" | "inferred" | "unknown";
  osmTags?: {
    surface?: string;
    tracktype?: string;
    smoothness?: string;
    mtb_scale?: string;
  };
};

type SurfaceSummary = {
  byClassM: Record<SurfaceClass, number>;
  dominantClass: SurfaceClass;
  byRiderClassM: Record<RiderSurfaceClass, number>;
  dominantRiderSurfaceClass: RiderSurfaceClass;
  pavedPct: number;
  unpavedPct: number;
  trackPct: number;
  roughnessMean: number;
  taggedPct: number;
  inferredPct: number;
  unknownPct: number;
  engineVersion: string;
  dataVersion: string;
};
```

`byClassM` and `dominantClass` remain for backward compatibility and analysis. New UI should prefer `byRiderClassM` and `dominantRiderSurfaceClass`.

Because this changes the enrichment algorithm output, the pipeline must bump the surface-normalization engine version. This is not a Valhalla tile change and does not require changing Valhalla or rebuilding tiles solely for labels.

## User Stories

1. As an adventure rider, I want the Road Surface card to show categories that match how riders talk about roads, so that I can quickly understand the route.
2. As an adventure rider, I want firm graded unpaved roads to show as Hardpack, so that I do not confuse them with loose gravel.
3. As an adventure rider, I want loose gravel to be called Loose gravel, so that I recognize it as a traction warning.
4. As an adventure rider, I want dirt roads with track-like road use to show as Primitive road, so that rougher dirt roads are distinguished from ordinary dirt roads.
5. As an adventure rider, I do not want a route-level Two-track percentage, so that I am not misled by a label that implies literal twin tire ruts.
6. As an adventure rider inspecting a point or segment, I want the raw track-like road-use signal available in details, so that I can audit why a segment became Primitive road.
7. As a Web planner user, I want Road Surface rows and chart colors to use one mutually exclusive rider class per segment, so that percentages add to 100%.
8. As an iOS user, I want route/day profiles to use the same rider classes as Web, so that trip exports and standalone routes feel consistent.
9. As a developer, I want existing `surfaceClass` and `isTrack` fields preserved, so that old consumers, tests, caches, and debugging tools remain useful.
10. As a developer, I want new trip exports to include `riderSurfaceClass`, so that iOS can consume the authoritative derived class.
11. As a developer, I want old trip exports to remain readable, so that clients can derive fallback rider classes when the new field is missing.

## Implementation Decisions

- Pipeline owns the canonical derivation of `riderSurfaceClass`.
- Pipeline adds `riderSurfaceClass` to every returned surface segment.
- Pipeline adds `summary.byRiderClassM` and `summary.dominantRiderSurfaceClass`.
- Pipeline keeps existing `surfaceClass`, `byClassM`, `dominantClass`, `isTrack`, `trackPct`, `roughness`, and confidence fields.
- Pipeline bumps the surface-normalization engine version because the response's normalized product interpretation changed.
- Web and iOS switch primary Road Surface display, summaries, legends, and chart coloring to rider classes.
- Web and iOS remove route-level visible Two-track percentage and separate Two-track visual overlay from primary charts/cards.
- Web and iOS may show the raw `isTrack`/track-like road-use signal only in segment-specific inspection, hover, scrub, debug, or audit contexts. Do not show it as an overall route statistic.
- Web and iOS should prefer pipeline-provided rider fields when present, but must derive fallback rider classes locally for older saved/enriched data.
- Effective rider summary fallback order:
  1. Use `summary.byRiderClassM` and `summary.dominantRiderSurfaceClass` when present.
  2. Else, if segments are present, derive `riderSurfaceClass` per segment from `surfaceClass + isTrack`, aggregate meters, and compute the dominant rider class.
  3. Else, if only `summary.byClassM` is present, map machine summary meters directly (`paved -> paved`, `compacted -> hardpack`, `dirt -> dirt_road`, `gravel -> loose_gravel`, `rough -> rough_trail`, `unknown -> unknown`). This fallback cannot recover `primitive_road`.
  4. Else, surface display is unavailable.
- UI headlines should always come from the effective rider distribution, not directly from old `dominantClass`.
- If the effective dominant rider class is `unknown`, compact/headline copy should read `Surface unknown`; row labels can remain `Unknown`.
- Chart coloring should use rider class segments/bands only. If old data has only per-sample roughness and no class segments or rider-class bands, do not invent rider classes from roughness; leave the surface chart layer neutral/unavailable.
- Existing per-sample `surfaceRoughness` export/sample fields remain unchanged. Do not replace them with per-sample rider classes.
- Web trip export should include `riderSurfaceClass` when present.
- iOS import should decode `riderSurfaceClass` optionally and derive the same fallback from `surfaceClass + isTrack` when absent.
- Web and iOS should remap the existing surface color ramp to rider classes, adding only one new color for `primitive_road`:
  - `paved` uses old `paved`.
  - `hardpack` uses old `compacted`.
  - `dirt_road` uses old `dirt`.
  - `primitive_road` gets a new color between `dirt_road` and `loose_gravel`.
  - `loose_gravel` uses old `gravel`.
  - `rough_trail` uses old `rough`.
  - `unknown` uses old `unknown`.
- Confidence should not be shown as a primary chart overlay. Surface chart/card visuals encode rider class only.
- Route-level confidence text appears under/near the Road Surface card only when `inferredPct >= 3` or `unknownPct >= 3`.
- Segment inspection, hover, or scrub details may show confidence regardless of the route-level threshold.
- Canonical route confidence copy:
  - `~N% estimated from road type`
  - `~N% unknown`
  - `~N% estimated; ~M% unknown`
- The rider-class display order is stable, not sorted by percentage:
  1. `paved`
  2. `hardpack`
  3. `dirt_road`
  4. `primitive_road`
  5. `loose_gravel`
  6. `rough_trail`
  7. `unknown`
- Percentages in the rider-surface distribution include `unknown` meters in the denominator so the distribution sums to 100%.
- Road Surface rows show only classes present in the effective rider distribution, but any nonzero class should be shown. Nonzero classes under 1% display as `<1%`.
- Compact Road Surface headlines use the dominant rider class, not a paved/unpaved split. Detail views can still show the full distribution.
- Existing `pavedPct` and `unpavedPct` remain unchanged for compatibility. Clients that need a rider-class paved/unpaved split compute it from `byRiderClassM`.
- `riderSurfaceClass` is display/distribution only in this pass. Effort continues to use existing `pointRoughness` / segment `roughness`.
- Confidence remains separate. Inferred segments can still become `primitive_road`; clients disclose confidence separately as "Estimated from road type" when relevant.
- Web and iOS use identical labels and copy casing:
  - `Paved`
  - `Hardpack`
  - `Dirt road`
  - `Primitive road`
  - `Loose gravel`
  - `Rough trail`
  - `Unknown`

## Testing Decisions

- Pipeline unit tests should pin the rider-class derivation table, including:
  - `compacted + isTrack -> hardpack`
  - `dirt + !isTrack -> dirt_road`
  - `dirt + isTrack -> primitive_road`
  - `gravel + isTrack -> loose_gravel`
  - `rough -> rough_trail`
  - `unknown -> unknown`
  - `impassable` remains excluded.
- Pipeline tests should assert `byRiderClassM` is length-weighted and sums to the route meters, including unknown when present.
- Pipeline public-boundary tests should assert the additive fields are returned by the enrichment service.
- Web tests should keep fixtures using machine `surfaceClass` values and assert visible rider labels and rider-class distributions.
- Web should test fallback derivation for older stored/enriched routes without `riderSurfaceClass`.
- Web should test the effective summary fallback order, including summary-only data where `primitive_road` cannot be recovered.
- Web should test that chart surface coloring is unavailable/neutral when only per-sample roughness exists without class segments.
- iOS tests should assert Road Surface cards, chart scrub readouts, and detail rows use rider labels.
- iOS should test optional decoding of `riderSurfaceClass` and fallback derivation from old exports.
- iOS should test the same effective summary fallback order as Web.
- Tests should assert that route-level Two-track percentage is no longer shown in the primary Road Surface card.
- Tests should assert route-level confidence copy appears at the 3% threshold and is omitted below it.
- Tests should assert nonzero classes below 1% render as `<1%`.
- Tests should assert compact/headline unknown state reads `Surface unknown`.
- Tests should assert that effort inputs/roughness behavior do not change.

## Corpus Sanity Check

The July 2026 BDR path-level census supports using `isTrack` as a dirt-road refinement rather than as a broad replacement class.

Official BDR sample:

- 12 official BDR files.
- 190 GPX paths/tracks.
- 11,890.9 total miles.
- 2,187.1 `isTrack` miles.
- `isTrack` share: 18.4%.

Composition of `isTrack` miles:

| Surface under `isTrack` | Share of `isTrack` miles |
|---|---:|
| `dirt` | 61.7% |
| `gravel` | 24.9% |
| `compacted` | 11.7% |
| `rough` | 1.2% |
| `paved` | 0.5% |

Inverse view:

| Surface class | Percent that is `isTrack` |
|---|---:|
| `dirt` | 65.9% |
| `compacted` | 42.8% |
| `gravel` | 10.4% |
| `paved` | 0.3% |
| `rough` | 100.0% |

This argues against collapsing all `isTrack` miles into a single visible Two-track or Primitive-road bucket. `isTrack` is most useful as a promotion signal for `dirt`, while `compacted` should stay Hardpack and `gravel` should stay Loose gravel.

Corpus check also found no `impassable` segments in the stored Web surface census or raw-ish BDR prototype fixture, supporting the decision to keep `impassable` excluded from `RiderSurfaceClass`.

## Out of Scope

- Changing Valhalla tiles, Valhalla source, or Valhalla's surface enum.
- Changing the existing machine `surfaceClass` enum names.
- Promoting additional combinations into `rough_trail`.
- Adding classes beyond the seven `RiderSurfaceClass` values above.
- Changing roughness values, effort scoring, `K_SURFACE`, or surface-effort calibration.
- Re-enriching or migrating saved route data as a hard requirement.
- Recoloring the whole route profile design beyond mapping existing chart/card colors to rider classes.
- Adding raw OSM `smoothness`/`tracktype`/`surface` v2 taxonomy.

## Further Notes

The key design distinction is "machine facts vs rider interpretation." `surfaceClass` remains the normalized Valhalla-derived class. `riderSurfaceClass` is GPXplore's rider-facing interpretation of those normalized inputs.

The product copy should avoid saying "Two-track" as a broad visible class because the data signal is `edge.use = track`, not confirmed twin tire ruts. In inspection contexts, prefer wording like "track-like road use" or "road-use: track."

This spec intentionally makes `Primitive road` a narrow derived class: `dirt + isTrack`. That gives GPXplore a useful middle category between Dirt road and Rough trail without overstating what Valhalla/OSM can actually prove.
