/** Types, labels, and helpers shared by UI and map layers — no Leaflet imports (SSR-safe). */

export type Agency = "usfs" | "blm" | "recgov" | "state";

export type FacilityType = "campground" | "group" | "dispersed" | "horse" | "developed" | "other";

export const TYPE_LABEL: Record<FacilityType, string> = {
  campground: "Campground",
  group: "Group campground",
  dispersed: "Dispersed / camping area",
  horse: "Horse camp",
  developed: "Developed site (BLM)",
  other: "Other",
};

export const SOURCE_LABEL: Record<Agency, string> = {
  usfs: "USFS",
  blm: "BLM",
  recgov: "Rec.gov",
  state: "State Park",
};

export type CampRecord = {
  i: string;
  n: string;
  t: FacilityType;
  y: number;
  x: number;
  r: "res" | "fcfs" | "mixed" | null;
  rc?: "def" | "likely";
  f: 0 | 1 | null;
  c?: number;
  w?: 1;
  rt?: 1;
  u?: string;
  el?: string;
  tn?: string;
  d?: string;
  st?: string;
  sub?: string;
  ds?: number;
  desc?: string;
  op?: string;
  fee_d?: string;
  ft?: string;
  w_d?: string;
  rt_d?: string;
  dir?: string;
  rest?: string;
  cond?: string;
  sea?: string;
  hrs?: string;
  imp?: string;
  ph?: string;
  em?: string;
};

export type CampFilters = {
  types: Set<FacilityType>;
  proximityEnabled: boolean;
  proximityMiles: number;
};

export const ALL_TYPES: FacilityType[] = [
  "campground",
  "group",
  "dispersed",
  "horse",
  "developed",
  "other",
];

export type Tier = "p1" | "p2" | "p3";

export function tierFor(rec: CampRecord): Tier {
  if (rec.t === "dispersed") return "p3";
  if (rec.r === "res") return "p2";
  return "p1";
}

export function approxMiles(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 3958.8;
  const toRad = Math.PI / 180;
  const x = (lon2 - lon1) * toRad * Math.cos(((lat1 + lat2) / 2) * toRad);
  const y = (lat2 - lat1) * toRad;
  return Math.sqrt(x * x + y * y) * R;
}
