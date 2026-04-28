#!/usr/bin/env bash
# Switch this repo's `origin` remote from the internal gitea mirror to GitHub,
# preserving the gitea URL under the alias `gitea` so we can still push there
# on demand. Idempotent: if origin already points at the GitHub URL, exits 0.
#
# Usage:
#   scripts/switch_remote_to_github.sh
#
# Pre-requisites (do these by hand first):
#   - https://github.com/Masashi-Lateolabrax/roverc created EMPTY (no README,
#     no .gitignore, no auto-init)
#   - `ssh -T git@github.com` authenticates with your key
#   - working tree clean (`git status` shows nothing to commit)
set -euo pipefail

GITEA_REMOTE="gitea"
GITHUB_URL="git@github.com:Masashi-Lateolabrax/roverc.git"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
echo "Repo:  $REPO_ROOT"

# 1. Clean working tree (so push --all reflects committed state).
if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: working tree has uncommitted changes; commit or stash first." >&2
  git status --short >&2
  exit 1
fi

# 2. Inspect current `origin`.
current_origin="$(git remote get-url origin 2>/dev/null || true)"
if [[ -z "$current_origin" ]]; then
  echo "ERROR: no 'origin' remote configured. Aborting." >&2
  exit 1
fi
echo "Current origin: $current_origin"

if [[ "$current_origin" == "$GITHUB_URL" ]]; then
  echo "origin already points at GitHub. Nothing to do."
  exit 0
fi

# 3. Refuse if a remote named `gitea` already exists -- the user may have run
# this before and we'd lose information silently otherwise.
if git remote get-url "$GITEA_REMOTE" >/dev/null 2>&1; then
  echo "ERROR: a remote named '$GITEA_REMOTE' already exists." >&2
  echo "       Existing URL: $(git remote get-url "$GITEA_REMOTE")" >&2
  echo "       Resolve manually (rename / remove) and re-run." >&2
  exit 1
fi

# 4. Rename current origin (gitea) and install GitHub as the new origin.
echo "Renaming  origin -> $GITEA_REMOTE  ($current_origin)"
git remote rename origin "$GITEA_REMOTE"

echo "Adding    origin -> $GITHUB_URL"
git remote add origin "$GITHUB_URL"

# 5. Sanity-check GitHub SSH auth before attempting the push. The check exits
# 1 with the success banner so we explicitly inspect output.
echo "Probing GitHub SSH..."
ssh_out="$(ssh -o BatchMode=yes -T git@github.com 2>&1 || true)"
if echo "$ssh_out" | grep -qi "successfully authenticated"; then
  echo "  ok: $(echo "$ssh_out" | head -n1)"
else
  echo "  WARN: SSH probe did not show the success banner."
  echo "  Output was:"
  printf '    %s\n' "$ssh_out"
  echo "  Continuing -- the push below will surface the actual error."
fi

# 6. Push every local branch + tag to GitHub. -u retargets upstream tracking
# to origin/<branch> for each pushed branch.
echo
echo "Pushing all branches to origin (GitHub)..."
git push -u origin --all

echo "Pushing tags..."
git push origin --tags

# 7. Final state for the user to eyeball.
echo
echo "=== Remote layout: ==="
git remote -v
echo
echo "=== Branch tracking: ==="
git branch -vv
echo
echo "Done. Default 'git push' now goes to GitHub."
echo "To mirror to the gitea backup later: git push $GITEA_REMOTE <branch>"
