import fs from "node:fs";
import path from "node:path";

const SRC = process.argv[2] || "/tmp/fed.geojson";
const OUT = "public/data";
fs.mkdirSync(OUT, { recursive: true });

const raw = JSON.parse(fs.readFileSync(SRC, "utf8"));

// ---- helpers ----
const titleCase = (s) => s.replace(/\w\S*/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase());
const cleanName = (n) => {
  if (!n) return "";
  const letters = n.replace(/[^A-Za-z]/g, "");
  if (letters.length > 2 && letters === letters.toUpperCase()) return titleCase(n);
  return n;
};

// Trim very long descriptions to keep payload reasonable. Most are < 500 chars.
const trim = (s, max = 1200) => {
  if (!s || typeof s !== "string") return undefined;
  const cleaned = s.trim().replace(/\s+/g, " ");
  if (!cleaned) return undefined;
  if (cleaned.length <= max) return cleaned;
  return cleaned.slice(0, max - 1).trimEnd() + "…";
};

// Preserve multi-line structure (fee_description, fee tables) but strip trailing whitespace.
const trimMulti = (s, max = 800) => {
  if (!s || typeof s !== "string") return undefined;
  const cleaned = s
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .trim();
  if (!cleaned) return undefined;
  if (cleaned.length <= max) return cleaned;
  return cleaned.slice(0, max - 1).trimEnd() + "…";
};

const USFS_TYPE = {
  CAMPGROUND: "campground",
  "GROUP CAMPGROUND": "group",
  "CAMPING AREA": "dispersed",
  "HORSE CAMP": "horse",
};
const RES_MAP = {
  reservable: "res",
  mixed: "mixed",
  likely_fcfs: "fcfs",
  definite_fcfs: "fcfs",
};
const RES_CONFIDENCE = {
  definite_fcfs: "def",
  likely_fcfs: "likely",
};

const usfs = [];
const blm = [];
let dropped = 0;

for (const f of raw.features) {
  const p = f.properties || {};
  const [lon, lat] = f.geometry.coordinates;
  if (typeof lat !== "number" || typeof lon !== "number") continue;
  const y = +lat.toFixed(5);
  const x = +lon.toFixed(5);

  if (p.source === "usfs_infra") {
    const sub = p.site_subtype;
    if (sub === "CAMP UNIT" || sub === "CAMP UNIT - TENT") {
      dropped++;
      continue;
    }
    const t = USFS_TYPE[sub] || "other";
    const rec = {
      i: p.site_id,
      n: cleanName(p.public_name || p.name),
      t,
      y,
      x,
      r: RES_MAP[p.reservation_tier] || null,
      f: p.fee_charged === true ? 1 : p.fee_charged === false ? 0 : null,
    };
    if (RES_CONFIDENCE[p.reservation_tier]) rec.rc = RES_CONFIDENCE[p.reservation_tier];

    // Light-weight booleans (kept for fast hover / filter checks)
    if (p.total_capacity) rec.c = p.total_capacity;
    if (p.water_availability) rec.w = 1;
    if (p.restroom_availability) rec.rt = 1;
    if (p.usda_portal_url) rec.u = p.usda_portal_url;
    if (p.elevation_ft && p.elevation_ft !== "feet") rec.el = p.elevation_ft;
    if (p.closest_towns) rec.tn = p.closest_towns;
    if (p.development_label) rec.d = p.development_label;

    // === Rich detail fields for the drawer ===
    if (typeof p.development_scale === "number") rec.ds = p.development_scale;
    const desc = trim(p.description, 1400);
    if (desc) rec.desc = desc;
    const op = trim(p.operated_by, 100);
    if (op) rec.op = op;
    const feeD = trimMulti(p.fee_description, 800);
    if (feeD) rec.fee_d = feeD;
    const ft = trim(p.fee_type, 80);
    if (ft) rec.ft = ft;
    const wd = trim(p.water_availability, 160);
    if (wd) rec.w_d = wd;
    const rtd = trim(p.restroom_availability, 160);
    if (rtd) rec.rt_d = rtd;
    const dirs = trim(p.directions, 600);
    if (dirs) rec.dir = dirs;
    const rest = trim(p.restrictions, 400);
    if (rest) rec.rest = rest;
    const cond = trim(p.current_conditions, 400);
    if (cond) rec.cond = cond;
    const season = trim(p.open_season, 200);
    if (season) rec.sea = season;
    const hours = trim(p.operational_hours, 200);
    if (hours) rec.hrs = hours;
    const imp = trim(p.important_info, 400);
    if (imp) rec.imp = imp;

    usfs.push(rec);
  } else if (p.source === "blm_recreation") {
    const t = p.development === "campground" ? "campground" : "developed";
    const rec = {
      i: p.object_id,
      n: cleanName(p.name),
      t,
      y,
      x,
      r:
        RES_MAP[p.reservation_tier] ||
        (p.reservable === "reservable" ? "res" : p.reservable === "non_reservable" ? "fcfs" : null),
      f: p.has_fee === "fee" ? 1 : p.has_fee === "no_fee" ? 0 : null,
    };
    if (RES_CONFIDENCE[p.reservation_tier]) rec.rc = RES_CONFIDENCE[p.reservation_tier];

    if (p.web_link) rec.u = p.web_link;
    if (p.admin_state) rec.st = p.admin_state;
    if (p.unit_name) rec.tn = p.unit_name;
    if (p.feature_subtype) rec.sub = p.feature_subtype;
    // BLM data is sparse — pass anything richer through if present
    const desc = trim(p.description, 1400);
    if (desc) rec.desc = desc;
    blm.push(rec);
  }
}

const writeJson = (name, data) => {
  const out = path.join(OUT, name);
  fs.writeFileSync(out, JSON.stringify(data));
  const kb = (fs.statSync(out).size / 1024).toFixed(0);
  console.log(`  ${name}: ${data.length} records, ${kb} KB`);
};

writeJson("usfs-campgrounds.json", usfs);
writeJson("blm-campgrounds.json", blm);
console.log(`dropped ${dropped} USFS camp-unit records`);
