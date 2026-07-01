# Trailhead — Campground POI Display System

A design spec for the map-marker, hover, and detail-drawer behavior in a GPX route-planning app. Aesthetic: technical/utilitarian, warm-neutral grays, topo-map feel (think Gaia GPS / onX). The app's primary purpose is route planning + GPX trimming; campground POIs are a secondary layer that should not compete with the route.

---

## 1. Design tokens

### Colors (OKLCH, with hex fallbacks)

| Token          | OKLCH                  | Hex (approx) | Purpose                                                           |
| -------------- | ---------------------- | ------------ | ----------------------------------------------------------------- |
| `--p1`         | `oklch(0.46 0.10 148)` | `#2F6A4E`    | **Priority 1** marker fill — non-reservable USFS/BLM campgrounds  |
| `--p2`         | `oklch(0.62 0.14 65)`  | `#B6803A`    | **Priority 2** marker outline — reservable (Recreation.gov) sites |
| `--p3`         | `oklch(0.55 0.015 95)` | `#888278`    | **Priority 3** marker dot — dispersed / undeveloped sites         |
| `--saved`      | `oklch(0.62 0.13 80)`  | `#BD8E2A`    | **Saved POI** fill — warm gold, distinct from p1/p2/p3/fee        |
| `--saved-soft` | `oklch(0.93 0.05 85)`  | `#F3EAD2`    | Saved chip / pill background                                      |
| `--saved-ink`  | `oklch(0.40 0.11 75)`  | `#6B5018`    | Saved text/icon foreground                                        |
| `--fee`        | `oklch(0.52 0.14 32)`  | `#B85432`    | Fee chip — ember, used in callouts only (NOT on markers)          |
| `--free`       | `oklch(0.48 0.10 155)` | `#347853`    | Free chip                                                         |
| `--route`      | `oklch(0.58 0.17 32)`  | `#D26330`    | The GPX route line + start/end markers                            |
| `--info`       | `oklch(0.50 0.10 235)` | `#3677AB`    | Type chip (Campground / Equestrian / Developed)                   |

**Ink scale** (warm neutral, slightly olive-tinted):

- `--ink-900` `oklch(0.20 0.012 95)` → near-black, headings
- `--ink-800` `oklch(0.28 0.012 95)` → body text
- `--ink-700` `oklch(0.38 0.011 95)` → secondary text
- `--ink-600` `oklch(0.48 0.010 95)` → muted text
- `--ink-500` `oklch(0.58 0.009 95)` → labels / eyebrows
- `--ink-300` → `oklch(0.78 0.007 95)` borders, disabled
- `--ink-200` → `oklch(0.88 0.006 95)` hairlines, dividers
- `--ink-150` → `oklch(0.92 0.006 95)` subtle dividers
- `--ink-100` → `oklch(0.95 0.005 95)` hover-fill
- `--ink-50` → `oklch(0.97 0.004 90)` very-light fill

**Surfaces:**

- `--paper` `oklch(0.985 0.004 85)` — primary surface, near-white warm
- `--paper-alt` `oklch(0.965 0.006 85)` — drawer footer, secondary surface
- `--canvas` `oklch(0.945 0.010 88)` — page background
- `--map-bg` `oklch(0.91 0.018 110)` — base map fill

### Typography

- **Sans**: IBM Plex Sans, weights 400/500/600/700
- **Mono**: IBM Plex Mono, weights 400/500/600 — used for all numerics, eyebrows, dist/elevation values, and small data
- **Body** 13px / 1.4
- **Card titles** 15px / 1.25, weight 600
- **Drawer title** 22px / 1.15, weight 600, `letter-spacing: -0.012em`
- **Eyebrow** 10.5px mono, uppercase, `letter-spacing: 0.1em`, color `--ink-500`

### Spacing / radii / shadow

- Radii: `--r-sm` 4px · `--r-md` 6px · `--r-lg` 10px · `--r-xl` 14px
- `--shadow-sm` — chrome/button: `0 1px 2px rgba(35,30,20,.06), 0 0 0 1px rgba(35,30,20,.05)`
- `--shadow-md` — popovers/drawer: `0 2px 4px rgba(35,30,20,.04), 0 6px 16px rgba(35,30,20,.08), 0 0 0 1px rgba(35,30,20,.05)`
- `--shadow-marker` — map markers: `0 1px 2px rgba(0,0,0,.25), 0 2px 6px rgba(0,0,0,.15)`

---

## 2. Marker system (the core of the system)

Markers encode **camper-priority**, not data-source. USFS vs BLM is carried in the detail callout, never on the pin itself.

### Tiers

| Tier   | Visual                                 | Size                 | Use                                                                                    |
| ------ | -------------------------------------- | -------------------- | -------------------------------------------------------------------------------------- |
| **P1** | Filled circle, tier color, white glyph | 18px (24px selected) | Non-reservable USFS/BLM campgrounds — the bread & butter for FCFS-seeking moto campers |
| **P2** | Outlined ring, tier color, no glyph    | 16px (20px selected) | Reservable sites (Recreation.gov) — visible but de-emphasized                          |
| **P3** | Tiny muted dot, no glyph               | 7px (9px selected)   | Dispersed / undeveloped — present but doesn't fight the route line                     |

### Glyphs (inside P1 markers)

- `tent` — default campground
- `horse` — equestrian camp
- `group` — group/reservable group sites (typically off by default)

Drawn as minimal monoline SVG sized ~55% of the marker radius, in `--paper` color over the tier fill.

### States

- **Default** — base size, normal opacity
- **Hover** — marker scales to 1.08, displays a small dark name pill (`--ink-900` bg, `--paper` text, 11px) anchored 6px below the marker. **Hover shows ONLY the campground name — no metadata, no chips.**
- **Selected** — marker pops 33% larger, outer glow ring in tier color (2px halo against paper, then 2px tier color)
- **Saved** — marker fill replaced with `--saved` (warm gold), glyph stays white. P3 saved markers promote from a dot to a full 12px gold-filled circle so they actually read.
- **Layer hidden + saved** — saved POIs stay rendered (in the saved gold) even when their tier's checkbox is off in the layer panel. Unsaved markers vanish.

### Clusters (low zoom)

A filled circle in the dominant tier color, white count text in mono, double-ring outer halo. Size scales `22 + log10(count) * 6`, capped at 36px.

### Route start/end markers

Both styled identically — small circled dot (9px white circle, 3.5px inner dot). Start uses `--p1` (forest green), end uses `--route` (ember orange).

---

## 3. Hover behavior

**On marker hover only:**

- Marker scales to 1.08
- Small dark name pill appears 6px below: just the campground name, 11px sans, white-on-ink-900, 7px corner radius

**No floating tooltip, no metadata on hover.** All details are reached by clicking the marker, which opens the detail drawer.

---

## 4. Detail drawer (right slide-over)

420px wide, full viewport height, anchored to map's right edge with `border-radius: 12px 0 0 12px` and `--shadow-lg`.

### Header

- Eyebrow: small green pin icon + "Campground details"
- Title (22px, 600 weight) — the campground name
- **Badge row** — chips in this order:
  - Source: `USFS` / `BLM` / `Rec.gov` (mono, `--ink-100` bg, `--ink-700` text)
  - Type: `Campground` / `Equestrian` / `Developed` (info-soft bg, info-ink text)
  - Reservation: `First-come` (p1-soft/p1-ink) / `Mixed` (p2-soft/p2-ink) / `Reservable` (p2-soft/p2-ink)
  - Fee: `Fee` (fee-soft/fee-ink) or `Free` (free-soft/free-ink)
- **Save to route button** — see section 5

### Scrollable body

- **Stat grid** (2×N) — bordered, 1px dividers, each cell has a small inline icon + eyebrow label + value. Cells included when data is present:
  - Capacity (tent icon) — e.g. "~10 sites"
  - Elevation (mountain icon) — e.g. "4,800 ft"
  - Water (droplet or droplet-off icon) — e.g. "Drinking water"
  - Restroom (restroom icon) — e.g. "Vault"
  - Develop. (small gold numeric badge `1`–`4` in a pill) — e.g. "Moderate"
  - From route — e.g. "0.4 mi off route" (mono)

- **Description** — section with `── DESCRIPTION ──` hairline label. Prose paragraph in `--ink-800`, 13px, line-height 1.55. Optional "Operated by …" italic line in `--ink-500`.

- **Fee info** — `── FEE INFO ──`. Renders as a `<pre>` mono block in `--ink-50` background with `--ink-150` border. Preserves multi-line fee structures.

- **Details `<dl>`** — `── DETAILS ──`. 88px-wide eyebrow `<dt>` column + value `<dd>` column. Rows for Season, Hours, Conditions, Restrictions, Important.

- **Coordinates** — `── COORDINATES ──`. Mono row with lat / lng.

### Footer

- Left: ghost-style external link button: `View on fs.usda.gov ↗`
- Right: tiny `--ink-500` mono note: "Saved POIs export with your GPX as separate waypoints"
- Background `--paper-alt`, separated from body by 1px top border

---

## 5. Save-to-route (the single pinning action)

Located in the drawer header below the badge row. **There is no separate "Add as waypoint" action — `Save to route` is the only way to pin a POI.**

### Visual

- Full-width button, left-aligned, 10–14px padding
- Two-column grid: 22px swatch + label/sublabel stack
- Swatch is a 14px circle, hollow when unsaved (1.5px ink-300 border on paper), filled `--saved` gold with a paper inset ring when saved
- **Unsaved state**: `--ink-200` border, paper bg, ink-800 text
  - Label: "Save to route"
  - Sub (mono, ink-500, 10.5px): "Pins this site to your trip · exports as a waypoint"
- **Saved state**: `--saved` border, `--saved-soft` bg, `--saved-ink` text
  - Label: "Saved to route"
  - Sub: "Marker stays gold · exports as a waypoint"
- Hover (unsaved): swatch border + button border → `--saved`, bg → `--saved-soft`

### Behavior

- Click toggles between saved and unsaved
- When saving: the marker on the map immediately switches fill to `--saved`; a chip appears in the **Saved-to-route strip** in the app header
- When unsaving: marker reverts to its tier color; chip disappears from strip

---

## 6. Saved-to-route strip (in app header)

A horizontal bar sitting just below the numeric stat cards in the app header. Shape: `--paper` background, `--ink-150` border, 6px radius, 8/14px padding.

Two columns:

- **Left**: small gold-dot swatch + "Saved to route" eyebrow + count pill (gold-soft bg, saved-ink text)
- **Right**: horizontally-scrolling chip list

### Chips

- Pill shape (border-radius 999px), 26px tall
- Tiny saved-marker pip (scaled to ~12px) + name + distance separator + distance value in mono
- Background `--ink-50`, border `--ink-150`. Active state: `--saved` border, `--saved-soft` bg
- Click → recenters the map on the POI and opens its drawer

### Empty state

- Italic text: "No saved POIs yet — open a campground and tap "Save to route.""

---

## 7. Layer panel (top-left of map)

248px wide floating panel, `--paper` bg, `--shadow-md`. Sections:

### Federal campgrounds (grouped by priority)

- **P1 · Non-reservable** — three layer rows: Campground (default on), Horse camp (default on), Group camp (default off)
- **P2 · Reservable** — Recreation.gov (default on)
- **P3 · Dispersed** — Camping areas (default on)

Each row: checkbox + 22px marker preview + label + small mono count on the right. Tier groups separated by 8px gap + 1px dashed top border. Group headers carry a small mono tag (`P1`/`P2`/`P3`) in an ink-100 pill.

### Near track

- Eyebrow + current value (e.g. "25 mi" mono) right-aligned
- Slider 1–100, accent color `--p1`
- Scale labels "1 mi" / "100 mi" below in mono ink-500

---

## 8. App chrome (context for the POI system)

### Header

- Logo (38px green tile with mountain glyph) + brand name "Trailhead" + GPX filename in mono ink-500
- Centered nav tabs: Trim (active), Waypoints (with count badge), Layers — pill-style tab bar with active tab gets paper bg + shadow
- Right: "Load another" ghost button with refresh icon

### Stat cards row

Seven cards, equal columns: Distance, Gain (accented in p1), Loss, Min ele, Max ele, Points, References (with count + route-colored dot). Each card: 10/14px padding, 1px ink-150 border, paper bg, hover → ink-300 border, active → p1 border. Label is eyebrow + value is mono 16px.

### Below stat cards

The **Saved-to-route strip** from section 6.

### Below strip

The map (1px ink-200 border, 6px radius, min-height 540px, contains all the floating panels and markers).

### Below map

- **Elevation profile** — full-width SVG with y-axis ticks (mono ink-500), p1-stroke line over a p1-gradient fill. Trim handles render as vertical lines (p1 for start, route ember for end). X-axis ticks in km mono.
- **Trim bar** — three-up readouts (Start / Segment / End) in a paper card with ink-150 border. Right side: Reverse ghost button + "Download trimmed GPX" primary button (p1 bg).

---

## 9. Data shape

The components consume a single `campground` object:

```ts
type Campground = {
  // Identity
  id: string;
  name: string;
  lat: string; // formatted, e.g. "47.0234° N"
  lng: string;

  // Source & classification (for the badge row)
  source: "USFS" | "BLM" | "Rec.gov";
  type: "Campground" | "Equestrian" | "Group" | "Developed" | "Primitive";
  reservation: "fcfs" | "reservable" | "mixed";
  fee: boolean;

  // Priority (drives marker tier)
  tier: "p1" | "p2" | "p3";
  glyph?: "tent" | "horse" | "group";

  // Stats
  sites?: string; // e.g. "~10 sites"
  water?: "have" | "none";
  waterLabel?: string;
  restroom?: string; // "Vault" | "Flush" | "None"
  elevation?: string; // "4,800 ft"
  devScale?: 1 | 2 | 3 | 4;
  devLabel?: string; // "Basic" | "Moderate" | "Developed"
  distance?: string; // "0.4 mi off route"

  // Prose
  description?: string;
  operatedBy?: string;
  feeDetail?: string; // multi-line fee table
  season?: string;
  hours?: string;
  conditions?: string;
  restrictions?: string;
  important?: string;

  // External
  url?: string;
  urlHost?: string; // "fs.usda.gov" | "blm.gov" | "recreation.gov"
};
```

State that lives on the route, not the campground:

```ts
type Route = {
  savedPoiIds: string[]; // ordered list of saved POI ids — exported as GPX waypoints
};
```

---

## 10. Interaction flow

1. **Idle** — map shows route + all marker layers as filtered. No drawer, no saved strip content.
2. **Hover** — marker scales, name pill appears below. **No tooltip with detail.**
3. **Click marker** — detail drawer slides in from the right. Marker becomes selected.
4. **Click "Save to route"** in drawer → marker turns gold, chip appears in the header strip. Button label becomes "Saved to route".
5. **Toggle a layer off** in the layer panel → unsaved markers of that tier disappear; saved markers of that tier stay (in gold).
6. **Click a saved chip** in the header strip → recenters map on that POI and opens its drawer.
7. **Download trimmed GPX** → exports the track plus the saved POIs as GPX `<wpt>` waypoints.

---

## 11. Themes (optional, for parity with the design canvas)

Three themes are wired via a single class on `<html>`:

- `.theme-paper` — default, described above
- `.theme-blueprint` — same structure, blue-shifted ink + p1 → `oklch(0.46 0.14 235)`
- `.theme-dusk` — full dark mode: paper → `oklch(0.22 0.008 250)`, ink scale inverted, marker accents brightened (`--p1` → `oklch(0.70 0.14 148)`, etc.)

If implementing only one theme initially: ship `.theme-paper` first.

---

## 12. Critical design principles (don't lose these)

- **Priority over source** — markers encode camper-priority, NOT data origin. A USFS reservable and a Rec.gov reservable look identical.
- **Quiet dispersed sites** — P3 dispersed dots must not visually compete with the route line. If anything, lean them quieter.
- **Hover is name-only** — no chips, no tooltip card. All details live in the drawer.
- **One save action** — never both "Add as waypoint" and "Save to route". Just the one.
- **Saved POIs persist across layer filters** — a saved P2 site stays visible (in gold) even when the P2 layer is toggled off.
- **Source and fee are details, not pin treatments** — they live as chips in the drawer / hover, never as marker overlays.
- **Mono for numbers** — every count, distance, elevation, coord, and tier indicator uses IBM Plex Mono.
