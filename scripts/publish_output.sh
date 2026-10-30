#!/usr/bin/env bash
# Publish one generated file to an output branch, committing only when it changed.
# Usage: scripts/publish_output.sh <file> [branch] [name-on-branch]
set -euo pipefail

SRC="${1:?usage: publish_output.sh <file> [branch] [name-on-branch]}"
BRANCH="${2:-snake-output}"
DEST="${3:-$(basename "$SRC")}"
REMOTE="${REMOTE:-origin}"

SRC="$(cd "$(dirname "$SRC")" && pwd)/$(basename "$SRC")"
WORK="$(mktemp -d)"
ORPHAN=0
cleanup() {
  cd "$ROOT"
  rm -rf "$WORK"
  git worktree prune >/dev/null 2>&1 || true
  # Only remove the local branch if this run created it.
  if [ "$ORPHAN" = 1 ]; then git branch -D "$BRANCH" >/dev/null 2>&1 || true; fi
}
ROOT="$(pwd)"
trap cleanup EXIT

if git fetch --quiet --no-tags --depth=1 "$REMOTE" "$BRANCH" 2>/dev/null; then
  git worktree add --quiet --detach "$WORK/out" FETCH_HEAD
else
  echo "Branch '$BRANCH' not found on '$REMOTE'; creating it."
  git worktree add --quiet --detach "$WORK/out"
  ORPHAN=1
  git -C "$WORK/out" checkout --quiet --orphan "$BRANCH"
  git -C "$WORK/out" rm -rf --quiet . >/dev/null 2>&1 || true
fi

cp "$SRC" "$WORK/out/$DEST"
cd "$WORK/out"
git add -- "$DEST"

if git diff --cached --quiet; then
  echo "No change to $DEST; nothing to publish."
  exit 0
fi

git -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit --quiet -m "Update snake contribution graph"
git push --quiet "$REMOTE" "HEAD:refs/heads/$BRANCH"
echo "Published $DEST to $BRANCH."
