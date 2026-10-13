#!/usr/bin/env bash
#
# publish-to-shared.sh — publish this backend repo into the shared_pwa monorepo.
#
# Mirrors this repo's working tree into `backend/` of the shared repository and
# pushes. Idempotent (safe to run repeatedly), never force-pushes, and excludes
# secrets/build junk. Intended for the backend VM; the frontend VM does the same
# into `frontend/`.
#
# Usage:
#   ./publish-to-shared.sh              # sync + commit + push
#   ./publish-to-shared.sh --dry-run    # show what would change, no push
#
# Override anything via env, e.g.:
#   SHARED_REPO=git@github.com:acme/shared_pwa.git SHARED_SUBDIR=backend ./publish-to-shared.sh
#
set -euo pipefail

# ── Config ───────────────────────────────────────────────────────────────────
BACKEND_DIR="${BACKEND_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
SHARED_REPO="${SHARED_REPO:-git@github.com:manishk-variyas/shared_pwa.git}"
WORK_DIR="${SHARED_WORK_DIR:-/tmp/shared_pwa}"
SUBDIR="${SHARED_SUBDIR:-backend}"
BRANCH="${SHARED_BRANCH:-main}"

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

log() { printf '\033[1;34m[publish]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[publish:error]\033[0m %s\n' "$*" >&2; }

command -v git   >/dev/null || { err "git not found"; exit 1; }
command -v rsync >/dev/null || { err "rsync not found"; exit 1; }

# ── 1. Ensure a clone of the shared repo ─────────────────────────────────────
if [ ! -d "$WORK_DIR/.git" ]; then
  log "Cloning $SHARED_REPO -> $WORK_DIR"
  if ! git clone "$SHARED_REPO" "$WORK_DIR"; then
    err "Could not clone $SHARED_REPO — does it exist and do you have access?"
    exit 1
  fi
fi
cd "$WORK_DIR"

log "Fetching '$BRANCH'"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
# Bring in the other team's commits; never rewrite history.
git pull --ff-only origin "$BRANCH" || { err "pull not fast-forward — resolve manually"; exit 1; }

# ── 2. Mirror the backend tree into $SUBDIR ──────────────────────────────────
log "Syncing $BACKEND_DIR -> $WORK_DIR/$SUBDIR"
mkdir -p "$SUBDIR"
RSYNC_FLAGS=(-a --delete --human-readable)
[ "$DRY_RUN" = "1" ] && RSYNC_FLAGS+=(--dry-run --itemize-changes)

rsync "${RSYNC_FLAGS[@]}" \
  --exclude='.git/' \
  --include='.env.example' --exclude='.env' --exclude='.env.*' \
  --exclude='logs/' --exclude='*.log' \
  --exclude='__pycache__/' --exclude='*.py[cod]' --exclude='*.egg-info/' \
  --exclude='.venv/' --exclude='venv/' \
  --exclude='.opencode/' \
  --exclude='admin_token.txt' --exclude='cookie*.txt' --exclude='cookies.txt' \
  --exclude='*.zip' --exclude='*.db' --exclude='*.sqlite' \
  --exclude='.DS_Store' --exclude='Thumbs.db' \
  --exclude='*.tmp' --exclude='*.temp' \
  "$BACKEND_DIR/" "$SUBDIR/"

# ── 3. Commit + push ─────────────────────────────────────────────────────────
if [ -z "$(git status --porcelain "$SUBDIR")" ]; then
  log "No changes to publish. Nothing to do."
  exit 0
fi

log "Changes staged under $SUBDIR/:"
git status --short "$SUBDIR" | sed 's/^/  /'

if [ "$DRY_RUN" = "1" ]; then
  log "Dry run — not committing or pushing."
  exit 0
fi

git add "$SUBDIR"
git commit -m "sync backend @ $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "Pushing to $SHARED_REPO ($BRANCH)"
git push origin "$BRANCH"
log "Done."
