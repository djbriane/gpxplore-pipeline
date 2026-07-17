#!/usr/bin/env bash
# Build a us-west Valhalla tile set on the Unraid NUC, then hot-swap the serve container onto it.
# Run from the Unraid host (SSH, or the User Scripts plugin). Manual/refresh cadence.
#
# Uses the OFFICIAL GHCR image (NOT the abandoned Docker Hub `valhalla/valhalla`).
# Keep IMAGE in sync with the serve container's tag (tile format is tied to the version).
#
# Retention (#14): each run builds into its own `builds/us-west-<YYMMDD>/` directory and, only on
# success, atomically flips the `current` symlink at it. The serve container mounts `.../current`,
# so a build is invisible until it's complete, and rollback is just repointing the symlink
# (see rollback.sh). Old builds are pruned to the most recent $RETAIN (current + N-1).
set -euo pipefail

# ---- config (override via env) --------------------------------------------
IMAGE="${IMAGE:-ghcr.io/valhalla/valhalla:3.8.2}"
APPDATA="${APPDATA:-/mnt/user/appdata/valhalla}"        # the appdata root (holds builds/ + current)
EXTRACT_URL="${EXTRACT_URL:-https://download.geofabrik.de/north-america/us-west-latest.osm.pbf}"
SERVE_CONTAINER="${SERVE_CONTAINER:-valhalla}"          # name of the Unraid serve container
BUILD_ADMINS="${BUILD_ADMINS:-1}"                       # border/driving-side awareness
BUILD_TIMEZONES="${BUILD_TIMEZONES:-1}"                 # time-dependent routing (needs internet)
BUILD_SIDECAR="${BUILD_SIDECAR:-1}"                     # surface-provenance sidecar (#11)
RETAIN="${RETAIN:-2}"                                   # #14: builds to keep (current + N-1); >=1
# Debian ships a PREBUILT python3-pyosmium (bookworm = 3.6.0) — apt avoids the
# from-source build (cmake/boost/protozero) that `pip install osmium` triggers.
PYOSMIUM_IMAGE="${PYOSMIUM_IMAGE:-debian:bookworm-slim}"  # throwaway image for the sidecar pass
REUSE_PBF="${REUSE_PBF:-0}"                             # 1 = skip download if extract present

PBF="$APPDATA/extract.osm.pbf"                          # shared scratch input (NOT kept per-build)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"  # so we can mount our own scripts
mkdir -p "$APPDATA/builds"
log(){ printf '\n\033[1m=== %s ===\033[0m\n' "$*"; }
# Build steps mount the per-build dir as /data and the shared extract read-only as /extract.osm.pbf,
# so every path baked into valhalla.json (/data/tiles, /data/admins.sqlite, ...) is correct both now
# (build dir = /data) and later (serve mounts current = the same build dir = /data).
run(){ docker run --rm -v "$BUILD_DIR":/data -v "$PBF":/extract.osm.pbf:ro "$IMAGE" "$@"; }

# ---- 1. download extract (capture provenance) -----------------------------
if [[ "$REUSE_PBF" != "1" || ! -f "$PBF" ]]; then
  log "Downloading us-west extract"
  # -R (--remote-time) stamps the file's mtime from the server's Last-Modified — the extract's
  # real data date, which we read back below. -w prints the resolved URL for the manifest.
  EFFECTIVE_URL=$(curl -fL -R "$EXTRACT_URL" -o "$PBF" -w '%{url_effective}')
else
  EFFECTIVE_URL="(reused existing $PBF)"
  log "Reusing existing extract"
fi
# Data date drives the per-build dir name, so each refresh gets a DISTINCT build (the #14 rollback
# target). Read the extract's own mtime (set from Last-Modified via -R above): `us-west-latest.osm.pbf`
# does NOT redirect to a dated URL, so the URL is not a reliable date source. Fall back to today's
# date — never a fixed literal, which would collapse every build into one dir and defeat retention.
EXTRACT_DATE=$(date -u -r "$PBF" +%y%m%d 2>/dev/null || true)
EXTRACT_DATE="${EXTRACT_DATE:-$(date -u +%y%m%d)}"

# ---- per-build directory (this run's isolated output) ---------------------
DATA_VERSION="us-west-${EXTRACT_DATE}"
BUILD_DIR="$APPDATA/builds/$DATA_VERSION"
mkdir -p "$BUILD_DIR"
log "Building into $BUILD_DIR (data_version=$DATA_VERSION)"

# ---- 2. config (tiles + admins + timezones, NO elevation) -----------------
log "valhalla_build_config"
run valhalla_build_config \
  --mjolnir-tile-dir /data/tiles \
  --mjolnir-admin /data/admins.sqlite \
  --mjolnir-timezone /data/timezones.sqlite \
  > "$BUILD_DIR/valhalla.json"

# ---- 3. optional enrichment DBs (before tiles) ----------------------------
if [[ "$BUILD_ADMINS" == "1" ]]; then
  log "valhalla_build_admins"; run valhalla_build_admins -c /data/valhalla.json /extract.osm.pbf
fi
if [[ "$BUILD_TIMEZONES" == "1" ]]; then
  # NOTE: valhalla_build_timezones writes the DB to STDOUT — must redirect, not pass a path.
  # Optional for this use case (time-of-day routing only); set BUILD_TIMEZONES=0 to skip.
  log "valhalla_build_timezones (downloads TZ shapefile)"
  run valhalla_build_timezones > "$BUILD_DIR/timezones.sqlite"
fi

# ---- 4. build tiles (timed) -----------------------------------------------
log "valhalla_build_tiles — the slow step (~20+ min for us-west)"
START=$(date +%s)
run valhalla_build_tiles -c /data/valhalla.json /extract.osm.pbf
BUILD_SECS=$(( $(date +%s) - START ))

# ---- 5. provenance stamp shared by tiles + sidecar ------------------------
PBF_SHA=$(sha256sum "$PBF" | cut -d' ' -f1)
SIDECAR="$BUILD_DIR/surface_provenance.sqlite"

# ---- 6. surface-provenance sidecar (#11) — SAME extract as the tiles -------
# A pyosmium pass over the identical .pbf: way_id -> explicit surface tags.
# Runs in a throwaway python image (the Valhalla image has no pyosmium). The
# sidecar MUST share this extract with the tiles or the way_id join drifts.
SIDECAR_HIGHWAY_WAYS=0; SIDECAR_TAGGED_WAYS=0; SIDECAR_BYTES=0; SIDECAR_SHA=""
if [[ "$BUILD_SIDECAR" == "1" ]]; then
  log "surface-provenance sidecar (pyosmium)"
  SIDECAR_OUT=$(docker run --rm \
    -v "$BUILD_DIR":/data -v "$PBF":/extract.osm.pbf:ro -v "$SCRIPT_DIR":/scripts:ro \
    "$PYOSMIUM_IMAGE" bash -c '
      set -e
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq >/dev/null
      apt-get install -y -qq python3-pyosmium >/dev/null
      python3 /scripts/build_surface_sidecar.py \
        /extract.osm.pbf /data/surface_provenance.sqlite \
        --data-version "'"$DATA_VERSION"'" --extract-sha256 "'"$PBF_SHA"'"
    ')
  echo "$SIDECAR_OUT"
  SIDECAR_HIGHWAY_WAYS=$(printf '%s\n' "$SIDECAR_OUT" | sed -n 's/^SIDECAR_HIGHWAY_WAYS=//p' | tail -1)
  SIDECAR_TAGGED_WAYS=$(printf '%s\n' "$SIDECAR_OUT" | sed -n 's/^SIDECAR_TAGGED_WAYS=//p' | tail -1)
  SIDECAR_HIGHWAY_WAYS="${SIDECAR_HIGHWAY_WAYS:-0}"; SIDECAR_TAGGED_WAYS="${SIDECAR_TAGGED_WAYS:-0}"
  SIDECAR_BYTES=$(du -sb "$SIDECAR" 2>/dev/null | cut -f1 || echo 0)
  SIDECAR_SHA=$(sha256sum "$SIDECAR" 2>/dev/null | cut -d' ' -f1 || echo "")
else
  log "sidecar skipped (BUILD_SIDECAR=0)"
fi

# ---- 7. provenance manifest (feeds #10 versioning: data_version) -----------
VALHALLA_VER=$(docker run --rm --entrypoint valhalla_service "$IMAGE" --version 2>/dev/null | head -1 || echo "$IMAGE")
TILE_BYTES=$(du -sb "$BUILD_DIR/tiles" 2>/dev/null | cut -f1 || echo 0)
cat > "$BUILD_DIR/tile_manifest.json" <<JSON
{
  "region": "us-west",
  "data_version": "${DATA_VERSION}",
  "extract_url": "${EFFECTIVE_URL}",
  "extract_date": "${EXTRACT_DATE}",
  "extract_sha256": "${PBF_SHA}",
  "valhalla_image": "${IMAGE}",
  "valhalla_version": "${VALHALLA_VER}",
  "built_admins": ${BUILD_ADMINS:-0},
  "built_timezones": ${BUILD_TIMEZONES:-0},
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "build_seconds": ${BUILD_SECS},
  "tile_bytes": ${TILE_BYTES},
  "sidecar": {
    "built": ${BUILD_SIDECAR:-0},
    "file": "surface_provenance.sqlite",
    "sha256": "${SIDECAR_SHA}",
    "bytes": ${SIDECAR_BYTES:-0},
    "highway_ways": ${SIDECAR_HIGHWAY_WAYS:-0},
    "tagged_ways": ${SIDECAR_TAGGED_WAYS:-0}
  }
}
JSON
log "Manifest -> $BUILD_DIR/tile_manifest.json"
cat "$BUILD_DIR/tile_manifest.json"

# ---- 8. promote: atomically flip `current` at this build ------------------
# ln -sfn + mv -T replaces the symlink atomically (no window with a missing/half-built current).
# The target is RELATIVE so the appdata dir stays relocatable.
log "Promoting $DATA_VERSION -> current"
ln -sfn "builds/$DATA_VERSION" "$APPDATA/.current.new"
mv -Tf "$APPDATA/.current.new" "$APPDATA/current"

# ---- 9. hot-swap: restart the serve container to load new tiles -----------
# Restart re-resolves the `current` symlink bind source, so the container comes up on this build.
# Done BEFORE pruning so the new build is confirmed live before any old build is deleted.
if docker inspect "$SERVE_CONTAINER" >/dev/null 2>&1; then
  log "Restarting serve container '$SERVE_CONTAINER'"
  docker restart "$SERVE_CONTAINER" >/dev/null && echo "restarted"
else
  echo "NOTE: serve container '$SERVE_CONTAINER' not found — start it from the Unraid template."
fi

# ---- 10. prune old builds (keep current + N-1) ----------------------------
if (( RETAIN >= 1 )); then
  CUR=$(basename "$(readlink "$APPDATA/current" 2>/dev/null || echo "")")
  # newest-first list of build dir names
  KEEP=" $CUR "; COUNT=1
  while IFS= read -r p; do
    name=$(basename "$p")
    [[ "$name" == "$CUR" ]] && continue
    if (( COUNT < RETAIN )); then KEEP+="$name "; COUNT=$((COUNT+1)); fi
  done < <(ls -1dt "$APPDATA"/builds/*/ 2>/dev/null)
  while IFS= read -r p; do
    name=$(basename "$p")
    if [[ "$KEEP" != *" $name "* ]]; then
      log "Pruning old build: $name"; rm -rf "$APPDATA/builds/$name"
    fi
  done < <(ls -1dt "$APPDATA"/builds/*/ 2>/dev/null)
  echo "Retained builds:$KEEP"
fi

log "Done in $((BUILD_SECS/60))m $((BUILD_SECS%60))s. Tiles: $(du -sh "$BUILD_DIR/tiles" 2>/dev/null | cut -f1)"
