import L from "leaflet";
import "leaflet.markercluster";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import {
  SOURCE_LABEL,
  TYPE_LABEL,
  approxMiles,
  tierFor,
  type Agency,
  type CampFilters,
  type CampRecord,
  type FacilityType,
  type Tier,
} from "@gpx-route-planner/route-components";
import {
  buildMarkerIcon,
  type CampMarkerGlyph,
} from "@gpx-route-planner/route-components/campMarkerIcons";

export type { Agency, CampFilters, CampRecord, FacilityType, Tier };
export {
  ALL_TYPES,
  SOURCE_LABEL,
  TYPE_LABEL,
  approxMiles,
  tierFor,
} from "@gpx-route-planner/route-components";
export { buildMarkerIcon } from "@gpx-route-planner/route-components/campMarkerIcons";

const DATA_URLS: Record<Exclude<Agency, "recgov">, string> = {
  usfs: "/data/usfs-campgrounds.json",
  blm: "/data/blm-campgrounds.json",
  state: "/data/state-campgrounds.json",
};

const cache: Partial<Record<Agency, Promise<CampRecord[]>>> = {};

export function loadCampgrounds(agency: Exclude<Agency, "recgov">): Promise<CampRecord[]> {
  if (!cache[agency]) {
    cache[agency] = fetch(DATA_URLS[agency])
      .then((r) => {
        if (!r.ok) throw new Error(`Failed to load ${agency} data (HTTP ${r.status})`);
        return r.json() as Promise<CampRecord[]>;
      })
      .catch((e) => {
        delete cache[agency];
        throw e;
      });
  }
  return cache[agency]!;
}

const escapeHtml = (s: string) =>
  s.replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!,
  );

function glyphFor(rec: CampRecord): CampMarkerGlyph {
  if (rec.t === "horse") return "horse";
  if (rec.t === "group") return "group";
  return "tent";
}

const cssVar = (name: string): string => {
  if (typeof window === "undefined") return "";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
};

function tierColor(tier: Tier): string {
  return cssVar(tier === "p1" ? "--p1" : tier === "p2" ? "--p2" : "--p3");
}

function popupHtml(rec: CampRecord, agency: Agency): string {
  // Popup retains data; hover tooltip is name-only (see refresh()).
  const typeLabel = TYPE_LABEL[rec.t];
  const agencyLabel = SOURCE_LABEL[agency];
  const resBadge =
    rec.r === "res"
      ? `<span class="th-chip th-chip--info">Reservable</span>`
      : rec.r === "fcfs"
        ? `<span class="th-chip th-chip--free">First-come</span>`
        : rec.r === "mixed"
          ? `<span class="th-chip th-chip--mixed">Mixed</span>`
          : "";
  const feeBadge =
    rec.f === 1
      ? `<span class="th-chip th-chip--fee">Fee</span>`
      : rec.f === 0
        ? `<span class="th-chip th-chip--free">Free</span>`
        : "";
  const lines: string[] = [];
  if (rec.c) lines.push(`<strong>${rec.c}</strong> capacity`);
  if (rec.el) lines.push(escapeHtml(rec.el));
  if (rec.tn) lines.push(escapeHtml(rec.tn));
  if (rec.w) lines.push("Drinking water");
  if (rec.rt) lines.push("Restrooms");
  if (rec.sub) lines.push(escapeHtml(rec.sub));
  const linkLabel =
    agency === "usfs"
      ? "View on fs.usda.gov →"
      : agency === "blm"
        ? "View on blm.gov →"
        : "View details →";
  const link = rec.u
    ? `<a href="${rec.u}" target="_blank" rel="noopener" class="th-popup-link">${linkLabel}</a>`
    : "";

  return `<div class="th-popup">
    <div class="th-popup-title">${escapeHtml(rec.n) || "Unnamed site"}</div>
    <div class="th-popup-chips">
      <span class="th-chip th-chip--agency">${agencyLabel}</span>
      <span class="th-chip th-chip--info">${escapeHtml(typeLabel)}</span>
      ${resBadge}${feeBadge}
    </div>
    ${lines.length ? `<div class="th-popup-meta">${lines.join(" · ")}</div>` : ""}
    ${link}
  </div>`;
}

function buildProximityTest(
  track: Array<{ lat: number; lon: number }>,
  maxMiles: number,
): (lat: number, lon: number) => boolean {
  if (track.length === 0) return () => false;
  const stride = Math.max(1, Math.floor(track.length / 500));
  const sample: Array<[number, number]> = [];
  for (let i = 0; i < track.length; i += stride) sample.push([track[i].lat, track[i].lon]);
  const last = track[track.length - 1];
  if (sample[sample.length - 1][0] !== last.lat) sample.push([last.lat, last.lon]);
  return (lat, lon) => {
    for (const [tlat, tlon] of sample) {
      if (approxMiles(lat, lon, tlat, tlon) <= maxMiles) return true;
    }
    return false;
  };
}

// ============================================================
// Layer
// ============================================================

export type FederalLayer = {
  /** Composite layer: clustered markers + a non-clustered dispersed group that
   *  only joins the map at close zoom. Add/remove this on the map (or via a
   *  layers control) — the dispersed sub-layer is wired up automatically. */
  cluster: L.FeatureGroup;
  refresh: (filters: CampFilters, track: Array<{ lat: number; lon: number }>) => Promise<void>;
  isLoaded: () => boolean;
  /** Optional predicate — return true if a record id is in the user's saved set. */
  setIsSaved: (fn: ((id: string) => boolean) | null) => void;
  /** Clear any internal "selected" marker state and re-render so the visual
   *  selection ring is removed. Safe to call when nothing is selected. */
  clearSelection: () => void;
};

/** Zoom at/above which dispersed sites become visible. Below this, dispersed
 *  records are hidden entirely (and never contribute to clusters). */
const DISPERSED_MIN_ZOOM = 11;
const DISPERSED_MAX_MILES = 2;

// Internal: tier priority for cluster dominance (lower index = higher priority)
const TIER_RANK: Tier[] = ["p1", "p2", "p3"];

export type FederalLayerOptions = {
  /** Called when a marker is clicked. If provided, the built-in Leaflet popup
   *  is suppressed and the host app is expected to open its own detail UI. */
  onSelect?: (rec: CampRecord, agency: Agency) => void;
};

export function createFederalLayer(
  agency: Exclude<Agency, "recgov">,
  options: FederalLayerOptions = {},
): FederalLayer {
  const cluster = L.markerClusterGroup({
    chunkedLoading: true,
    chunkInterval: 100,
    spiderfyOnMaxZoom: true,
    showCoverageOnHover: false,
    maxClusterRadius: 50,
    iconCreateFunction: (c) => {
      const children = c.getAllChildMarkers() as Array<L.Marker & { _tier?: Tier }>;
      // Pick dominant tier among children
      let dominant: Tier = "p3";
      let bestRank = TIER_RANK.indexOf("p3");
      for (const m of children) {
        const t = m._tier ?? "p3";
        const r = TIER_RANK.indexOf(t);
        if (r < bestRank) {
          bestRank = r;
          dominant = t;
          if (r === 0) break;
        }
      }
      const n = c.getChildCount();
      const size = Math.min(36, Math.round(22 + Math.log10(Math.max(1, n)) * 6));
      const color = tierColor(dominant);
      const paper = cssVar("--paper") || "#fbfaf6";
      const html = `
        <div class="th-cluster" style="
          width:${size}px;height:${size}px;border-radius:9999px;
          background:${color};color:${paper};
          font-family:var(--font-mono,'IBM Plex Mono',monospace);
          font-weight:600;font-size:11px;
          display:flex;align-items:center;justify-content:center;
          box-shadow:
            0 0 0 2px ${paper},
            0 0 0 4px ${color}80,
            var(--shadow-marker);
        ">${n}</div>`;
      return L.divIcon({
        className: "th-cluster-wrap",
        html,
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
      });
    },
  });

  // Dispersed sites bypass clustering entirely — they're quiet P3 dots that
  // only appear when the user zooms in close enough to actually plan around
  // them. Kept in a plain FeatureGroup that we attach/detach by zoom.
  const dispersedGroup = L.featureGroup();

  // Public composite layer. Layers control toggles this; we manage the
  // dispersed sub-layer's map membership ourselves based on zoom.
  const wrapper = L.featureGroup([cluster]);
  let zoomHandler: (() => void) | null = null;
  wrapper.on("add", (e) => {
    const map = (e.target as L.Layer & { _map?: L.Map })._map;
    if (!map) return;
    const update = () => {
      const shouldShow = map.getZoom() >= DISPERSED_MIN_ZOOM;
      if (shouldShow && !map.hasLayer(dispersedGroup)) dispersedGroup.addTo(map);
      else if (!shouldShow && map.hasLayer(dispersedGroup)) map.removeLayer(dispersedGroup);
    };
    zoomHandler = update;
    map.on("zoomend", update);
    update();
  });
  wrapper.on("remove", (e) => {
    const map = (e.target as L.Layer & { _map?: L.Map })._map;
    if (map && zoomHandler) map.off("zoomend", zoomHandler);
    zoomHandler = null;
    if (map && map.hasLayer(dispersedGroup)) map.removeLayer(dispersedGroup);
  });

  let loaded = false;
  let data: CampRecord[] = [];
  let isSaved: ((id: string) => boolean) | null = null;
  let selectedId: string | null = null;
  let lastFilters: CampFilters | null = null;
  let lastTrack: Array<{ lat: number; lon: number }> = [];

  const buildMarker = (rec: CampRecord): L.Marker => {
    const tier = tierFor(rec);
    const glyph = glyphFor(rec);
    const saved = isSaved ? isSaved(rec.i) : false;
    const selected = selectedId === rec.i;
    const icon = buildMarkerIcon({ tier, glyph, selected, saved });
    const m = L.marker([rec.y, rec.x], { icon, riseOnHover: true }) as L.Marker & {
      _tier?: Tier;
      _recId?: string;
    };
    m._tier = tier;
    m._recId = rec.i;

    // Hover tooltip — name only, dark pill below the marker.
    m.bindTooltip(rec.n || "Unnamed", {
      direction: "bottom",
      offset: [0, 6],
      className: "th-hover-pill",
      opacity: 1,
    });
    // If host provides an onSelect, suppress the built-in popup and delegate.
    if (!options.onSelect) {
      m.bindPopup(() => popupHtml(rec, agency));
    }

    m.on("click", () => {
      const prev = selectedId;
      selectedId = selectedId === rec.i ? null : rec.i;
      if (lastFilters) void render(lastFilters, lastTrack, prev);
      if (options.onSelect && selectedId === rec.i) {
        options.onSelect(rec, agency);
      }
    });

    return m;
  };

  const render = async (
    filters: CampFilters,
    track: Array<{ lat: number; lon: number }>,
    _prevSelectedId?: string | null,
  ) => {
    if (!loaded) {
      try {
        data = await loadCampgrounds(agency);
        loaded = true;
      } catch (e) {
        console.warn(`Failed to load ${agency} campgrounds`, e);
        return;
      }
    }
    lastFilters = filters;
    lastTrack = track;

    cluster.clearLayers();
    dispersedGroup.clearLayers();
    const proxTest =
      filters.proximityEnabled && track.length > 0
        ? buildProximityTest(track, filters.proximityMiles)
        : null;
    // Dispersed sites are always limited to a tight band around the track —
    // without a route loaded, they're noise; with one, only nearby ones matter.
    const dispersedProxTest =
      track.length > 0 ? buildProximityTest(track, DISPERSED_MAX_MILES) : null;
    const clustered: L.Layer[] = [];
    for (const rec of data) {
      // Saved sites live only in RouteMapView's saved-camp layer.
      if (isSaved?.(rec.i)) continue;
      const passesType = filters.types.has(rec.t);
      if (!passesType) continue;
      if (proxTest && !proxTest(rec.y, rec.x)) continue;
      if (rec.t === "dispersed") {
        if (!dispersedProxTest) continue;
        if (!dispersedProxTest(rec.y, rec.x)) continue;
        dispersedGroup.addLayer(buildMarker(rec));
      } else {
        clustered.push(buildMarker(rec));
      }
    }
    cluster.addLayers(clustered);
  };

  return {
    cluster: wrapper,
    refresh: render,
    isLoaded: () => loaded,
    setIsSaved: (fn) => {
      isSaved = fn;
      if (lastFilters) void render(lastFilters, lastTrack);
    },
    clearSelection: () => {
      if (selectedId === null) return;
      const prev = selectedId;
      selectedId = null;
      if (lastFilters) void render(lastFilters, lastTrack, prev);
    },
  };
}
