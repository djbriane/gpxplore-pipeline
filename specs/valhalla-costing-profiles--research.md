# Valhalla Costing Profiles for BDR/ADV Connector Routing — Research

**One-line summary:** Build both connector profiles on Valhalla's `motorcycle` costing model (the only motorized model with a surface-preference knob, `use_trails`, and unpaved-friendly defaults), and tune `use_trails` / `use_tracks` / `use_highways` per profile.

Feeds ticket **djbriane/gpxplore-pipeline#7**. Primary source of truth: the local Valhalla clone at `/Users/djbriane/Development/gpxplore/valhalla/`. Where the clone's own docs and its C++ disagree, the code wins and the disagreement is flagged.

---

## Recommendation (up front)

**Base costing model: `motorcycle`** (marked BETA in Valhalla). It is the only motorized model that exposes a per-request road-surface preference (`use_trails`), it inherits an unpaved-friendly `use_tracks` default of 0.5 (vs 0.0 for `auto`/`truck`), and it has no vehicle-dimension restrictions that would exclude narrow/low forest roads. `auto` cannot tune surfaces at all (its surface factor is hard-coded); `truck` adds weight/height/length restrictions that wrongly exclude ADV-suitable roads. See the base-costing evaluation below.

Both profiles set `costing: "motorcycle"` and differ only in `costing_options.motorcycle.*`.

### Profile: `adv_balanced`

Willing to mix pavement connectors with gravel/dirt/tracks; mild avoidance of big highways; no surface penalty.

| Option | Value | Why |
| --- | --- | --- |
| `use_trails` | `0.7` | Removes the default unpaved-surface penalty and mildly prefers dirt/gravel/track surfaces. |
| `use_tracks` | `0.7` | Clears the `highway=track` penalty and mildly favors tracks. |
| `use_highways` | `0.3` | Mild motorway/trunk avoidance; still allowed when a connector needs them. |
| `use_living_streets` | `0.5` | Neutral. |
| `service_penalty` | `0` | Forest/USFS roads are frequently tagged `service`; don't penalize them. |
| `shortest` | `false` | Must stay false — `true` disables all surface/track/highway costing. |

### Profile: `avoid_highways`

Maximize dirt/track routing, avoid pavement and highways as hard as costing allows.

| Option | Value | Why |
| --- | --- | --- |
| `use_highways` | `0.0` | Strongest available highway avoidance. |
| `use_trails` | `1.0` | Strongest available unpaved/rough-surface preference. |
| `use_tracks` | `1.0` | Strongest available track preference. |
| `use_living_streets` | `0.5` | Neutral. |
| `service_penalty` | `0` | Keep forest/service roads cheap. |
| `shortest` | `false` | Same reason as above. |

**Is two the right number?** Two covers the useful axis (mixed vs maximally-off-pavement). Consider a third, `road_touring` / `pavement_connector` (`use_trails: 0.0`, `use_highways: 0.5`, defaults elsewhere), for the case where the desired connector between two dirt sections is deliberately a paved road. Not required for v1; flagged as an option. See rationale section.

---

## Base-costing evaluation

All three motorized candidates permit essentially every surface (all reject only `Surface::kImpassable`) and all permit `highway=track` by default (`lua/graph.lua:20` sets `motorcycle_forward/auto_forward/truck_forward = true` for `track`). The differences that matter for ADV are the **tunable knobs** and the **default surface/track posture**.

| Model | Surface knob | `use_tracks` default | Dimension restrictions | Verdict for ADV |
| --- | --- | --- | --- | --- |
| `auto` | **none** — `surface_factor_` hard-coded to `0.5` (`src/sif/autocost.cc:369-370`); only lever is `exclude_unpaved` (all-or-nothing) or `use_tracks` | `0.0` (`src/sif/autocost.cc:35`) | none | Weak: can't express "prefer dirt," only "exclude unpaved." |
| `motorcycle` (BETA) | **`use_trails`** modulates a per-surface penalty (`src/sif/motorcyclecost.cc:330-345,429`) | `0.5` (base default, `src/sif/dynamiccost.cc:96`; motorcycle doesn't override it) | none | **Best.** Purpose-built: its doc says it "provides options to tune the route to take roadways (road touring) vs. tracks and trails (adventure motorcycling)" (`docs/.../api-reference.md:81`). |
| `truck` | none | `0.0` | weight/height/length/axle — excludes many forest/narrow roads | Worst: dimension restrictions falsely prune ADV-suitable roads. |

The clone's docs corroborate this framing: `auto` "routes ... tend to favor highways and higher classification roads" (`docs/.../api-reference.md:73`); `motorcycle` "provides options to tune the route to take roadways (road touring) vs. tracks and trails (adventure motorcycling)" (`:81`).

**Caveat:** `motorcycle` is labeled **BETA** in Valhalla (`docs/.../api-reference.md:81,213`). It is well-established and the intended model for this use case, but the tag is worth noting for expectations.

**How the `motorcycle` surface knob actually works** (from `src/sif/motorcyclecost.cc`): there is a per-surface cost array `kSurfaceFactor[] = {paved 0.0, paved 0.0, pavedRough 0.0, compacted 0.1, dirt 0.2, gravel 0.5, path 1.0}` (`:73-81`). At runtime `factor += surface_factor_ * kSurfaceFactor[edge->surface()]` (`:429`). `surface_factor_` is derived from `use_trails` (`:330-345`): at `use_trails=0.0` it climbs to `kMaxTrailBiasFactor = 8.0` (`:71`) — i.e. the **default heavily penalizes gravel/dirt/path**; at `use_trails=0.5` it is 0 (neutral); above 0.5 it goes slightly negative (mild preference, floor ≈ −0.125). This is the single most important reason to raise `use_trails` for ADV: the default value fights you.

---

## Verified option reference

Ranges/defaults below are taken from the C++ (authoritative) and cross-checked against the clone's `docs/docs/api/turn-by-turn/api-reference.md`. "Model" indicates which costing methods accept the option.

| Option | Default | Range | What it does | Model(s) | Source |
| --- | --- | --- | --- | --- | --- |
| `use_highways` | `0.5` | 0–1 | Willingness to use motorway/trunk. Near 0 avoids highways (penalty up to `kMaxHighwayBiasFactor = 8.0`), near 1 mildly favors. | auto, truck, bus, **motorcycle** | `src/sif/motorcyclecost.cc:27,54,313-320`; `valhalla/sif/dynamiccost.h:199`; doc `:221` |
| `use_trails` | `0.0` | 0–1 | **Motorcycle-only.** Surface preference. 0 penalizes trails/tracks/unclassified/bad surfaces (factor up to 8.0); toward 1 prefers them. | **motorcycle only** | `src/sif/motorcyclecost.cc:29,56,332-345`; doc `:222` |
| `use_tracks` | `0.5` (motorcycle, scooter); `0.0` (auto, truck) | 0–1 | Willingness to use `highway=track`. Below 0.5 adds a track penalty (up to `kMaxTrackPenalty = 300s`) and a factor (up to `kMaxTrackFactor = 4.0`); above 0.5 mildly favors (factor floor `kMinTrackFactor = 0.8`). | all motorized | `src/sif/dynamiccost.cc:96,410-421`; `src/sif/autocost.cc:35`; doc `:125` |
| `use_living_streets` | `0.1` (cars/motorcycle/scooter); `0.0` (truck) | 0–1 | Willingness to use `highway=living_street`. Below 0.5 penalizes (up to `kMaxLivingStreetPenalty = 500s`). | all motorized | `src/sif/dynamiccost.cc:97,423-438`; doc `:124` |
| `service_penalty` | **`75`s (auto — code)** / doc says `15`; `15` (motorcycle/scooter/bus, base default); `0` (truck) | penalty (0–43200 s) | Fixed cost added on transition onto a generic `service` road. Lower it to keep forest/USFS service roads cheap. | all motorized | `src/sif/dynamiccost.cc:91`; `src/sif/autocost.cc:30,111`; doc `:126` — **doc/code disagree, see pitfalls** |
| `service_factor` | `1.0` | 0.1–100000 | Multiplies cost on service roads. | all | doc `:127` |
| `use_tolls` | `0.5` | 0–1 | Willingness to use tolled roads. | all motorized | `src/sif/motorcyclecost.cc:28,55`; doc `:123` |
| `top_speed` | `140` KPH (motorcycle/auto/bus); `120` (truck) | 10–252 KPH (motorcycle range 10–140, `kMaxSpeedKph=kMaxAssumedSpeed=140`) | Caps assumed speed and avoids roads faster than this. Marginal for short connectors. | all motorized | `src/sif/motorcyclecost.cc:57`; `valhalla/baldr/graphconstants.h:101,107`; doc `:133` |
| `shortest` | `false` | bool | Pure distance metric — **disables all other costs, penalties and factors.** Do NOT enable for ADV profiles. Also does not disable hierarchy pruning. | all except multimodal | doc `:94,130,223` |
| `exclude_unpaved` | `false` | bool | If true, unpaved allowed only at the very start/end, never mid-route. **Not available on `motorcycle`** (listed only for auto/bus/taxi/truck). | auto, bus, taxi, truck | doc `:146,154` |
| `use_hills` | n/a | — | **Not available for auto/motorcycle/truck.** Only bicycle, motor_scooter, pedestrian. Do not use for our base model. | bicycle, scooter, pedestrian | doc `:191,209,242` |
| `disable_hierarchy_pruning` | `false` | bool | Finds the true optimal path within a distance cap; performance-costly. Useful for exact short connectors. | all motorized | doc `:96,224` |
| `destination_only_penalty` | (default per base) | penalty | Cost to enter `access=destination` roads. Relevant to forest roads tagged destination-only. | all motorized | doc `:117` |
| `gate_penalty` / `private_access_penalty` | `300` / `450` s | penalty | Applied at gates / `access=private` gates and bollards. | all motorized | doc `:115-116` |
| `maneuver_penalty` | `5` s | penalty | Cost on turns between roads with no common name. | all motorized | doc `:113` |

Confirmed **non-existent / not-as-assumed** for our base model, so do not use them in the motorcycle profiles: `use_trails` exists only on motorcycle (good); `use_hills` does **not** exist on motorcycle/auto; `exclude_unpaved` does **not** exist on motorcycle; there is no `use_dirt`, `use_roads` (bicycle-only), or `surface`/`tracktype`/`smoothness` costing option — those tags are consumed at tile-build time (Lua) into the `Surface` enum and edge speed, not passed at request time.

---

## Profile rationale

Both profiles keep `costing: "motorcycle"` and adjust three levers plus `service_penalty`.

- **`use_trails`** is the surface lever. Default `0.0` actively penalizes exactly the gravel/dirt/track surfaces ADV wants. `adv_balanced` uses `0.7` (neutral-to-mild-prefer); `avoid_highways` uses `1.0` (max prefer). Neither uses values below 0.5, which would penalize unpaved.
- **`use_tracks`** clears/mild-favors `highway=track` (faint 4x4-ish tracks that are tagged `track`). Note the favor side is weak by design (`kMinTrackFactor=0.8`, only a 20% discount) — you can stop avoiding tracks but cannot strongly force onto them. That's acceptable here: imported GPX geometry is the ground truth for the off-pavement portions; Valhalla only fills connector gaps.
- **`use_highways`** separates the two profiles' intent: `0.3` (balanced) still permits a highway when it's the sensible connector; `0.0` (avoid) pushes onto secondary/unpaved alternatives.
- **`service_penalty: 0`** because western-US forest roads are frequently tagged `highway=service` or `service=*`; the default (75s on auto, 15s on motorcycle) discourages them unnecessarily for this use case.

**Optional third profile (`road_touring`/`pavement_connector`).** If a rider wants the connector between two dirt segments to be *paved on purpose*, `use_trails: 0.0` + `use_highways: 0.5` gives Valhalla's road-touring behavior. Keeping it out of v1 is fine; two profiles cover the primary axis. Flagged for the spec owner to decide.

---

## Pitfalls (unpaved / track / forest-road routing)

1. **`highway=path` is NOT routable by `motorcycle` (or `auto`/`truck`) by default.** `lua/graph.lua:30` sets `motorcycle_forward=false` (and auto/truck false) for `path`; only pedestrian and bike get access. Faint 4x4 or singletrack tagged `highway=path` will be **absent from the motorized graph**, so a connector that depends on one will fail to route or detour widely. `highway=track` *is* routable (`lua/graph.lua:20`). If path-tagged tracks matter, that requires a **build-time Lua change**, not a request option. (Open question below.)

2. **The default `use_trails=0.0` penalizes the very surfaces you want.** Motorcycle's out-of-the-box behavior avoids gravel/dirt/path (surface factor up to 8.0). You must raise `use_trails` — do not rely on defaults for ADV. (`src/sif/motorcyclecost.cc:330-345,429`.)

3. **`auto` cannot express surface preference at all.** Its `surface_factor_` is hard-coded to 0.5 (`src/sif/autocost.cc:369-370`); the only surface lever is the binary `exclude_unpaved`. This is the core reason to prefer `motorcycle`.

4. **`shortest: true` silently defeats the profiles.** It switches to pure distance and "disables all other costings & penalties" (doc `:94,130`) — every surface/track/highway preference is ignored. Never set it on these profiles. It also does not disable hierarchy pruning, so it isn't even a true shortest path.

5. **`tracktype`/`surface`/`smoothness` are build-time inputs, not runtime knobs.** `tracktype` only lowers a track's assumed speed (`grade1→20`, `grade2→15`, `grade3→12`, `grade4→10` KPH; untagged/`grade5` stays 5; `lua/graph.lua:1783-1796`). `surface`/`smoothness` feed the `Surface` enum (`kPavedSmooth…kGravel…kPath…kImpassable`, `valhalla/baldr/graphconstants.h:655-663`). **`smoothness=impassable` removes access entirely** (`lua/graph.lua:986,1071,2150`). So a legitimately-rideable road mis-tagged `impassable` will be dropped from the graph.

6. **Track/trail *favor* is capped low.** `use_tracks=1.0` gives at most a 20% discount (`kMinTrackFactor=0.8`) and `use_trails=1.0` at most ≈−0.125. You can neutralize avoidance but cannot strongly force off-pavement. Acceptable because imported GPX geometry is authoritative for the off-pavement legs.

7. **Destination-only and gated forest roads add large penalties.** `access=destination` roads incur `destination_only_penalty`; gates/`access=private` add `gate_penalty=300` / `private_access_penalty=450` (doc `:115-117`). Western forest roads are often destination-tagged or gated; if connectors avoid legitimate roads, consider lowering these. Fully private/no-access roads are excluded regardless.

8. **Doc-vs-code discrepancy on `service_penalty` (auto).** The clone's own API doc says the default is 15 for cars (`docs/.../api-reference.md:126`), but `src/sif/autocost.cc:30,111` sets it to **75**. Per instructions, trust the code: auto's real default is 75s. Motorcycle inherits the base default of 15s (`src/sif/dynamiccost.cc:91`; motorcycle overrides only `disable_rail_ferry_`). Our profiles set it explicitly to `0`, so this only matters if the value is omitted.

9. **Snap/reachability on faint tracks.** Low-classification edges can be pruned by Loki's reachability check and are more sensitive to snap radius. For connector endpoints that sit on faint tracks, expect to tune the location `radius`/`search_filter` and to validate that both endpoints snap to the intended low-class edge rather than a nearby higher-class road. (General Loki behavior; not a costing option.)

---

## Open questions

- **`highway=path` accessibility.** If BDR connectors need path-tagged segments, the only fix is a **tile-build-time** change to `lua/graph.lua` to grant `motorcycle_forward` on `path` (or on `path` + `motor_vehicle=yes`). This is a build decision, out of scope for request-time profiles, but it materially affects connector coverage. Recommend deciding before the first `central-rockies` build.
- **Motorcycle BETA status.** The model is intended for exactly this use but carries a BETA label; confirm the target Valhalla build/version treats it as stable enough for production connector generation.
- **Third profile.** Whether to ship `road_touring`/`pavement_connector` (deliberately-paved connectors) is a product decision, not resolvable from source.
- **Online docs cross-check incomplete.** `valhalla.github.io` returned a redirect/404 for the route API reference during this research; all option facts were verified against the local clone's code and its bundled `docs/`, which is the authoritative source per the task.

---

## Sources

Local Valhalla clone (`/Users/djbriane/Development/gpxplore/valhalla/`):

- `src/sif/motorcyclecost.cc` — `use_highways`/`use_trails`/`use_tolls` defaults & ranges (`:27-58`), surface factor array (`:73-81`), `kMaxTrailBiasFactor` (`:71`), `surface_factor_`/`highway_factor_` derivation (`:308-345`), EdgeCost surface/track application (`:403-449`), Allowed surface check (`:353-374`).
- `src/sif/autocost.cc` — `use_tracks` default 0 (`:35`), `service_penalty` default 75 (`:30,111`), fixed `surface_factor_=0.5` (`:369-370`), highway bias (`:382-390`).
- `src/sif/dynamiccost.cc` — base defaults `kDefaultServicePenalty=15` (`:91`), `kDefaultUseTracks=0.5` (`:96`), `kDefaultUseLivingStreets=0.1` (`:97`), track/living-street penalty & factor derivation and constants `kMaxTrackPenalty=300`/`kMinTrackFactor=0.8`/`kMaxTrackFactor=4`/`kMaxLivingStreetPenalty=500` (`:62-69,410-438`), option parsing (`:577-589`).
- `valhalla/sif/dynamiccost.h` — `kMaxHighwayBiasFactor=8.0` (`:199`).
- `valhalla/baldr/graphconstants.h` — `Surface` enum (`:655-663`), `Use::kPath` (`:327`), `kMaxAssumedSpeed=140`/`kMaxSpeedKph` (`:101,107`).
- `lua/graph.lua` — access defaults: `track` motorized-forward true (`:20`), `path` motorized-forward false (`:30`); track `use`/speed by `tracktype` (`:1581-1596,1783-1796`); `smoothness=impassable` access removal (`:986,1071,2150`).
- `docs/docs/api/turn-by-turn/api-reference.md` — costing model summaries (`:73-83`), penalty/factor semantics (`:91-96`), automobile options table (`:107-159`), motorcycle options section (`:213-224`), `exclude_unpaved` (`:154`), bicycle/scooter/pedestrian `use_hills` (`:191,209,242`).
- `/Users/djbriane/Development/gpxplore/valhalla-routing-handoff-spec.md` — draft handoff spec (profile names `adv_balanced`/`avoid_highways`, pipeline context); reviewed, not treated as a source.

Online (attempted, not usable): `https://valhalla.github.io/valhalla/api/turn-by-turn/api-reference/` and `.../route/api-reference/` returned a redirect / HTTP 404 during this session; the bundled clone docs were used instead.
