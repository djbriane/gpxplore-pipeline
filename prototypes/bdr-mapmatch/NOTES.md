# #6 — map-match real BDR tracks via trace_attributes

Run 2026-07-16 against the local us-west tile set (#5). Costing `motorcycle` with
`use_trails=1.0`, `use_tracks=1.0` (per #7, so the matcher will snap onto backcountry).
`shape_match=map_snap`. Each track downsampled to ~200 m spacing, contiguous ~400-point
window from the middle. Raw output: `results.txt`. Harness: `mapmatch.py` (throwaway).

## Result — whole-route enrichment is VIABLE

| track | match% | snap p90 | max | matched km | unknown surface |
|---|---|---|---|---|---|
| WABDR (WA, forest roads) | 100% | 7.2 m | 20 m | 99 | 0% |
| COBDR (CO, alpine mixed) | 100% | 6.8 m | 29 m | 118 | 0% |
| NVBDR (NV, remote 2-track) | 98% | 6.5 m | 31 m | 146 | 0% |
| Steens-Alvord (OR, high-desert 2-track) | 100% | 4.0 m | 27 m | 147 | 0% |
| White Rim 4x4 (UT, Canyonlands) | 100% | 0.4 m | 15 m | 93 | 0% |

Aggregate matched surface mix (by distance): **gravel 56%, dirt 27%, paved_smooth 16%, compacted <1%.**

## Findings (feed #9 normalization + the spec)

1. **Match quality is high on genuine backcountry.** 98–100% of points matched; snap p90 ≤ 7 m even on faint 2-track and 4x4. The White Rim (genuine 4x4) matched at 0.4 m p90 as 100% `dirt` / `use=track`. Map-matching is not the risk the map feared it might be.
2. **Valhalla returns its OWN normalized surface enum, not raw OSM `tracktype`/`smoothness`.** Observed values: `paved_smooth`, `gravel`, `dirt`, `compacted`. **0% unknown/none across every track** — each edge was classified. → **#9's `surface_class` taxonomy should map FROM Valhalla's ~8-value surface enum, not from raw OSM tags.** (Confirms #7's note that Valhalla folds surface+tracktype+smoothness into one enum at tile-build time.) The raw OSM `tracktype`/`smoothness` the ticket asked about are **not exposed** by trace_attributes.
3. **`edge.use` is a useful second signal** — cleanly separates `track` (2-track / 4x4) from `road`. White Rim = 100% `track`; WABDR = 28% `track`. Worth carrying alongside `surface` into the app model.
4. **`road_class` comes through** (unclassified/tertiary/service_other dominate backcountry) — usable for the road-class mapping #9 needs.
5. **No `highway=path` access failures in this sample** — these BDRs are tagged `track`/`unclassified`/`service`, so #7's path-access concern didn't bite here. It remains a latent risk for trails tagged `path`; not observed on official BDR routes.
6. **Request limits → whole-route enrichment must chunk.** `trace_attributes` enforces `trace.max_distance` = **200 km** (UTBDR's window exceeded it → HTTP 400 err 154) and a max shape-point count. Enriching a full imported GPX must **split into ≤~200 km / bounded-point segments** and stitch results. This is a contract/implementation note for the service API (#10) and the pipeline.

## Open / not chased here

- UTBDR errored only because its sampled window exceeded the 200 km trace cap (a chunking
  artifact, not a matching failure); Utah dirt is already represented by White Rim.
- Didn't observe `paved_rough` / `path` / `compacted`-heavy tracks — the taxonomy in #9
  should still enumerate the full Valhalla enum, not just the 4 values seen here.

## Verdict — #6 answered

- [x] Map-matching returns usable surface metadata on real BDR backcountry tracks.
- [x] Whole-route enrichment as specced is viable.
- [x] Real attribute samples captured to ground #9's taxonomy (Valhalla surface enum + use + road_class).
