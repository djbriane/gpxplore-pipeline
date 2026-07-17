#!/usr/bin/env bash
# Roll the serve container back to a previously built tile set (#14 retention).
# Repoints the `current` symlink and restarts the serve container. No rebuild.
#
#   ./rollback.sh            # roll back to the previous (N-1) build
#   ./rollback.sh --list     # show retained builds and which one is current
#   ./rollback.sh us-west-260701   # roll to a specific retained build
set -euo pipefail

APPDATA="${APPDATA:-/mnt/user/appdata/valhalla}"
SERVE_CONTAINER="${SERVE_CONTAINER:-valhalla}"
BUILDS="$APPDATA/builds"

CUR=$(basename "$(readlink "$APPDATA/current" 2>/dev/null || echo "")")

# newest-first list of retained build dir names
mapfile -t DIRS < <(ls -1dt "$BUILDS"/*/ 2>/dev/null | xargs -n1 basename 2>/dev/null)
if (( ${#DIRS[@]} == 0 )); then
  echo "No builds under $BUILDS — nothing to roll back to." >&2; exit 1
fi

if [[ "${1:-}" == "--list" || "${1:-}" == "-l" ]]; then
  echo "Retained builds (newest first):"
  for d in "${DIRS[@]}"; do
    [[ "$d" == "$CUR" ]] && echo "  * $d  (current)" || echo "    $d"
  done
  exit 0
fi

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  # default: the newest build that isn't current = the previous one
  for d in "${DIRS[@]}"; do
    [[ "$d" != "$CUR" ]] && { TARGET="$d"; break; }
  done
  [[ -z "$TARGET" ]] && { echo "Only one build retained ($CUR) — no previous build to roll back to." >&2; exit 1; }
fi

if [[ ! -d "$BUILDS/$TARGET" ]]; then
  echo "No such retained build: $TARGET" >&2
  echo "Run '$0 --list' to see available builds." >&2
  exit 1
fi
if [[ "$TARGET" == "$CUR" ]]; then
  echo "'$TARGET' is already current — nothing to do."; exit 0
fi

echo "Rolling back: $CUR -> $TARGET"
ln -sfn "builds/$TARGET" "$APPDATA/.current.new"
mv -Tf "$APPDATA/.current.new" "$APPDATA/current"

if docker inspect "$SERVE_CONTAINER" >/dev/null 2>&1; then
  echo "Restarting serve container '$SERVE_CONTAINER'..."
  docker restart "$SERVE_CONTAINER" >/dev/null && echo "restarted — now serving $TARGET"
else
  echo "NOTE: serve container '$SERVE_CONTAINER' not found — current now points at $TARGET." >&2
fi
