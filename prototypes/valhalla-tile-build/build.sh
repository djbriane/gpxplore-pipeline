#!/usr/bin/env bash
# PROTOTYPE — throwaway. Answers gpxplore-pipeline#5 (local Docker Valhalla tile build).
# Delete once the measurements are captured in the ticket. NOT production pipeline code.
set -euo pipefail

# ---- config (override via env) --------------------------------------------
REGION="${REGION:-us-west}"
EXTRACT_URL="${EXTRACT_URL:-https://download.geofabrik.de/north-america/us-west-latest.osm.pbf}"
IMAGE="${IMAGE:-ghcr.io/valhalla/valhalla:latest}"
# Route smoke-test coords (default: Denver -> Boulder, CO — inside us-west & colorado).
TEST_JSON="${TEST_JSON:-{\"locations\":[{\"lat\":39.7392,\"lon\":-104.9903},{\"lat\":40.0150,\"lon\":-105.2705}],\"costing\":\"auto\"}}"

HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="$HERE/_data/$REGION"          # gitignored working dir ("wipe me")
PBF="$DATA/extract.osm.pbf"
RESULTS="$HERE/results-$REGION.md"
CNAME="valhalla_proto_${REGION}"
PORT="${PORT:-8002}"

mkdir -p "$DATA"
log(){ printf '\n\033[1m=== %s ===\033[0m\n' "$*"; }
human(){ awk "BEGIN{b=$1; if(b>1073741824)printf \"%.2f GB\",b/1073741824; else printf \"%.0f MB\",b/1048576}"; }

cleanup(){ docker rm -f "$CNAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# ---- 0. preflight ----------------------------------------------------------
docker ps >/dev/null 2>&1 || { echo "Docker daemon not reachable — start Docker Desktop."; exit 1; }
cleanup

# ---- 1. download extract ---------------------------------------------------
if [[ ! -f "$PBF" ]]; then
  log "Downloading $REGION extract"
  curl -fL "$EXTRACT_URL" -o "$PBF"
fi
EXTRACT_BYTES=$(stat -f%z "$PBF")
log "Extract: $(human "$EXTRACT_BYTES")"

# ---- 2. start a long-lived container with the port published --------------
log "Pulling/starting $IMAGE"
docker run -d --name "$CNAME" -p "$PORT:8002" -v "$DATA":/data "$IMAGE" sleep infinity >/dev/null
docker exec "$CNAME" bash -lc 'command -v valhalla_build_tiles' \
  || { echo "valhalla binaries not on PATH in $IMAGE — adjust IMAGE."; exit 1; }

# ---- 3. build config (NO elevation) ---------------------------------------
log "valhalla_build_config (no elevation)"
docker exec "$CNAME" bash -lc 'valhalla_build_config \
  --mjolnir-tile-dir /data/tiles \
  --mjolnir-tile-extract /data/tiles.tar \
  --mjolnir-timezone /data/timezones.sqlite \
  --mjolnir-admin /data/admins.sqlite > /data/valhalla.json'

# ---- 4. build tiles (timed + peak memory) ---------------------------------
docker exec "$CNAME" bash -lc 'cat /sys/fs/cgroup/memory.peak > /dev/null 2>&1 && echo 0 > /sys/fs/cgroup/memory.peak' 2>/dev/null || true
log "valhalla_build_tiles — this is the slow one"
START=$(date +%s)
docker exec "$CNAME" bash -lc "valhalla_build_tiles -c /data/valhalla.json /data/extract.osm.pbf"
END=$(date +%s)
BUILD_SECS=$((END - START))

TILE_BYTES=$(docker exec "$CNAME" bash -lc 'du -sb /data/tiles 2>/dev/null | cut -f1' || echo 0)
PEAK_BUILD_BYTES=$(docker exec "$CNAME" bash -lc 'cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo 0')

# ---- 5. serve + route test + serving memory -------------------------------
log "Starting valhalla_service"
docker exec -d "$CNAME" bash -lc 'valhalla_service /data/valhalla.json 1'
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/status" || true)
  [[ "$code" == "200" ]] && break; sleep 1
done
# pass body as a file to avoid shell escaping mangling the JSON
printf '%s' "$TEST_JSON" > "$DATA/req.json"
ROUTE_HTTP=$(curl -s -o "$DATA/route.json" -w '%{http_code}' \
  "http://localhost:$PORT/route" --data @"$DATA/req.json" || true)
# pgrep -f: process name "valhalla_service" is 16 chars, over pgrep's 15-char name limit
SVC_PID=$(docker exec "$CNAME" bash -lc 'pgrep -f valhalla_service | head -1' || echo "")
SERVE_HWM_KB=$(docker exec "$CNAME" bash -lc "grep VmHWM /proc/${SVC_PID:-0}/status 2>/dev/null | awk '{print \$2}'" || echo 0)
SERVE_BYTES=$(( ${SERVE_HWM_KB:-0} * 1024 ))

# ---- 6. record -------------------------------------------------------------
{
  echo "## $REGION — $(date '+%Y-%m-%d %H:%M')"
  echo
  echo "| metric | value |"
  echo "|---|---|"
  echo "| extract | $(human "$EXTRACT_BYTES") |"
  echo "| build wall-time | $((BUILD_SECS/60))m $((BUILD_SECS%60))s |"
  echo "| tile-tree on disk | $(human "$TILE_BYTES") |"
  echo "| peak build mem (cgroup, incl cache) | $(human "$PEAK_BUILD_BYTES") |"
  echo "| serving mem (VmHWM) | $(human "$SERVE_BYTES") |"
  echo "| route request | HTTP $ROUTE_HTTP |"
  echo "| image | \`$IMAGE\` |"
  echo
} | tee -a "$RESULTS"
log "Appended to $RESULTS"
echo "Route response head:"; head -c 400 "$DATA/route.json" 2>/dev/null; echo
