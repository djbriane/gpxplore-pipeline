#!/usr/bin/env bash
# Publish this pipeline's compact output downstream to the sibling gpxplore-web
# and gpxplore-ios repos, in the order both repos actually depend on:
#
#   1. compact/<snapshot>/*.json        -> gpxplore-web/apps/planner/public/data/
#   2. ios-snapshot/<snapshot>/*.json.gz  (built locally, from the same compact
#      output - no dependency on a checked-out gpxplore-web)
#   3. ios-snapshot/<snapshot>/*.json.gz -> gpxplore-ios/gpxplore/Resources/Campgrounds/
#
# Review-gated like the rest of the pipeline: without --confirm this is a dry
# run (step 1 delegates to pipeline.cli publish's own dry-run reporting; step 3
# reports old/new byte sizes and writes nothing). Nothing outside this repo is
# touched unless --confirm is passed. Pure Python/stdlib - no Node/npm needed.
#
# Usage:
#   scripts/publish_downstream.sh [--confirm] [--snapshot=YYYY-MM-DD]
#
# Env overrides (default to sibling checkouts of this repo):
#   WEB_REPO=/path/to/gpxplore-web
#   IOS_REPO=/path/to/gpxplore-ios

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_REPO="${WEB_REPO:-$REPO_ROOT/../gpxplore-web}"
IOS_REPO="${IOS_REPO:-$REPO_ROOT/../gpxplore-ios}"

CONFIRM=0
SNAPSHOT=""
SNAPSHOT_ARGS=()

for arg in "$@"; do
  case "$arg" in
    --confirm)
      CONFIRM=1
      ;;
    --snapshot=*)
      SNAPSHOT="${arg#--snapshot=}"
      SNAPSHOT_ARGS=(--snapshot "$SNAPSHOT")
      ;;
    *)
      echo "publish_downstream.sh: unknown argument '$arg'" >&2
      exit 1
      ;;
  esac
done

if [[ ! -d "$WEB_REPO" ]]; then
  echo "WEB_REPO not found: $WEB_REPO (set WEB_REPO=/path/to/gpxplore-web)" >&2
  exit 1
fi
if [[ ! -d "$IOS_REPO" ]]; then
  echo "IOS_REPO not found: $IOS_REPO (set IOS_REPO=/path/to/gpxplore-ios)" >&2
  exit 1
fi

# Resolve to absolute paths up front: nothing below should silently resolve a
# still-relative WEB_REPO/IOS_REPO against the wrong cwd.
WEB_REPO="$(cd "$WEB_REPO" && pwd)"
IOS_REPO="$(cd "$IOS_REPO" && pwd)"

WEB_DATA_DIR="$WEB_REPO/apps/planner/public/data"
IOS_RESOURCES_DIR="$IOS_REPO/gpxplore/Resources/Campgrounds"
IOS_SNAPSHOT_ROOT="$REPO_ROOT/data/ios-snapshot"
SNAPSHOT_FILES=(campground-marker-index.json.gz campground-detail.json.gz)

if [[ $CONFIRM -eq 1 ]]; then
  echo "Mode: CONFIRM (will write into $WEB_REPO and $IOS_REPO)"
else
  echo "Mode: DRY RUN (writes nothing outside this repo; pass --confirm to publish)"
fi

echo ""
echo "== 1/3: compact output -> $WEB_DATA_DIR =="
cd "$REPO_ROOT"
if [[ $CONFIRM -eq 1 ]]; then
  python3 -m pipeline.cli publish "${SNAPSHOT_ARGS[@]}" --target "$WEB_DATA_DIR" --confirm
else
  python3 -m pipeline.cli publish "${SNAPSHOT_ARGS[@]}" --target "$WEB_DATA_DIR"
fi

echo ""
echo "== 2/3: build iOS campground snapshot (from this repo's compact output) =="
python3 -m pipeline.cli ios-snapshot "${SNAPSHOT_ARGS[@]}"

if [[ -z "$SNAPSHOT" ]]; then
  SNAPSHOT="$(basename "$(find "$IOS_SNAPSHOT_ROOT" -maxdepth 1 -mindepth 1 -type d | sort | tail -1)")"
fi
SNAPSHOT_OUT="$IOS_SNAPSHOT_ROOT/$SNAPSHOT"

echo ""
echo "== 3/3: snapshot -> $IOS_RESOURCES_DIR =="
for f in "${SNAPSHOT_FILES[@]}"; do
  src="$SNAPSHOT_OUT/$f"
  dest="$IOS_RESOURCES_DIR/$f"
  if [[ ! -f "$src" ]]; then
    echo "  ERROR: generated snapshot file missing: $src" >&2
    exit 1
  fi
  old_size="new"
  [[ -f "$dest" ]] && old_size=$(wc -c < "$dest" | tr -d ' ')
  new_size=$(wc -c < "$src" | tr -d ' ')
  if [[ $CONFIRM -eq 1 ]]; then
    mkdir -p "$IOS_RESOURCES_DIR"
    cp "$src" "$dest"
    echo "  wrote     $f  ($old_size -> $new_size bytes)"
  else
    echo "  DRY RUN   $f  ($old_size -> $new_size bytes)"
  fi
done

echo ""
if [[ $CONFIRM -eq 1 ]]; then
  echo "Done. Open a PR in each repo:"
  echo "  gpxplore-web: apps/planner/public/data/{usfs,blm,state}-campgrounds.json + usfs-pois.json"
  echo "  gpxplore-ios: gpxplore/Resources/Campgrounds/campground-{marker-index,detail}.json.gz"
else
  echo "Dry run complete. Re-run with --confirm to write into gpxplore-web and gpxplore-ios."
fi
