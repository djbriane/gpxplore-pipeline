#!/usr/bin/env bash
# Orchestrate a complete data update across pipeline, web, and iOS repos:
#   1. Verify both downstream repos are clean and on main
#   2. Create data/ branches (tagged with today's date) in all 3 repos
#   3. Run the pipeline with --confirm to populate compact/ios-snapshot output
#   4. Commit and push changes in all 3 repos
#   5. Create PRs in gpxplore-web and gpxplore-ios
#
# Usage:
#   scripts/data-update.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_REPO="${WEB_REPO:-$REPO_ROOT/../gpxplore-web}"
IOS_REPO="${IOS_REPO:-$REPO_ROOT/../gpxplore-ios}"

TODAY=$(date +%Y-%m-%d)
BRANCH_NAME="data/$TODAY"

main() {
  echo "=== Data Update Workflow ==="
  echo "Pipeline repo: $REPO_ROOT"
  echo "Web repo:      $WEB_REPO"
  echo "iOS repo:      $IOS_REPO"
  echo "Branch name:   $BRANCH_NAME"
  echo ""

  # Step 1: Verify repos are clean and on main
  echo "Step 1: Verifying repos are clean and on main..."
  verify_repo_state "$WEB_REPO" "gpxplore-web"
  verify_repo_state "$IOS_REPO" "gpxplore-ios"
  verify_repo_state "$REPO_ROOT" "gpxplore-pipeline"
  echo "✓ All repos clean and on main"
  echo ""

  # Step 2: Create branches in all repos
  echo "Step 2: Creating branches in all repos..."
  create_branch "$REPO_ROOT" "$BRANCH_NAME"
  create_branch "$WEB_REPO" "$BRANCH_NAME"
  create_branch "$IOS_REPO" "$BRANCH_NAME"
  echo "✓ Branches created"
  echo ""

  # Step 3: Run pipeline and publish downstream
  echo "Step 3: Running pipeline and publishing to downstream repos..."
  cd "$REPO_ROOT"
  make pipeline
  make publish-downstream CONFIRM=1
  echo "✓ Pipeline completed and published"
  echo ""

  # Step 4: Commit and push in all repos
  echo "Step 4: Committing and pushing changes..."
  commit_and_push "$REPO_ROOT" "data/$TODAY" "Update campgrounds data ($TODAY)"
  commit_and_push "$WEB_REPO" "$BRANCH_NAME" "Update campgrounds data ($TODAY)"
  commit_and_push "$IOS_REPO" "$BRANCH_NAME" "Update campgrounds data ($TODAY)"
  echo "✓ Changes committed and pushed"
  echo ""

  # Step 5: Create PRs
  echo "Step 5: Creating pull requests..."
  create_pr "$WEB_REPO" "$BRANCH_NAME" "web"
  create_pr "$IOS_REPO" "$BRANCH_NAME" "iOS"
  echo "✓ Pull requests created"
  echo ""

  echo "=== Data Update Complete ==="
}

verify_repo_state() {
  local repo=$1
  local name=$2

  if [[ ! -d "$repo/.git" ]]; then
    echo "ERROR: $name is not a git repository: $repo" >&2
    exit 1
  fi

  cd "$repo"

  # Check if clean
  if ! git diff-index --quiet HEAD --; then
    echo "ERROR: $name has uncommitted changes" >&2
    exit 1
  fi

  # Check if untracked files
  if [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    echo "ERROR: $name has untracked files" >&2
    exit 1
  fi

  # Check if on main
  local current_branch=$(git rev-parse --abbrev-ref HEAD)
  if [[ "$current_branch" != "main" ]]; then
    echo "ERROR: $name is on branch '$current_branch', not 'main'" >&2
    exit 1
  fi

  # Fetch to ensure we're up to date
  git fetch origin main 2>/dev/null || true

  # Check if behind remote
  if git rev-list --count HEAD..origin/main | grep -q '[1-9]'; then
    echo "ERROR: $name is behind origin/main" >&2
    exit 1
  fi
}

create_branch() {
  local repo=$1
  local branch=$2

  cd "$repo"

  # Check if branch exists locally
  if git rev-parse --verify "$branch" >/dev/null 2>&1; then
    echo "ERROR: Branch $branch already exists in $(basename $repo)" >&2
    exit 1
  fi

  git checkout -b "$branch"
}

commit_and_push() {
  local repo=$1
  local branch=$2
  local message=$3

  cd "$repo"

  # Check if there are any changes to commit
  if git diff-index --quiet HEAD -- && [[ -z "$(git ls-files --others --exclude-standard)" ]]; then
    echo "  (no changes in $(basename $repo), skipping commit)"
    return
  fi

  git add -A
  git commit -m "$message"
  git push -u origin "$branch"
  echo "  ✓ Committed and pushed $branch in $(basename $repo)"
}

create_pr() {
  local repo=$1
  local branch=$2
  local name=$3

  cd "$repo"

  # Check if gh CLI is available
  if ! command -v gh &> /dev/null; then
    echo "  WARNING: 'gh' CLI not found, skipping PR creation for $name"
    echo "  Create PRs manually at:"
    echo "    Web:  $(git remote get-url origin | sed 's/.git$//')/pull/new/$branch"
    echo "    iOS:  $(git remote get-url origin | sed 's/.git$//')/pull/new/$branch"
    return
  fi

  # Create PR
  local title="Update campgrounds data ($TODAY)"
  local body="Automated data update from pipeline run.

- Updated campgrounds data
- All sources processed and validated
- Ready for review and merge"

  if gh pr create --title "$title" --body "$body" --base main --head "$branch" 2>/dev/null; then
    local pr_url=$(gh pr view "$branch" --json url --jq '.url' 2>/dev/null || echo "")
    echo "  ✓ PR created for $name: $pr_url"
  else
    echo "  ✓ PR likely already exists for $name, check GitHub"
  fi
}

main "$@"
