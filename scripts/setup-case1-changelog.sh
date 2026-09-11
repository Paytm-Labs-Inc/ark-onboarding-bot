#!/usr/bin/env bash
# One-time setup: add a main-branch commit Ask Ark can match for Case 1.
# Creates an empty commit whose subject contains the probe error string.
#
# Usage: ./scripts/setup-case1-changelog.sh

set -euo pipefail
cd "$(dirname "$0")/.."

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Commit or stash local changes first." >&2
  exit 1
fi

branch=$(git rev-parse --abbrev-ref HEAD)
if [[ "$branch" != "main" ]]; then
  echo "Checkout main first (currently on $branch)." >&2
  exit 1
fi

git commit --allow-empty -m "$(cat <<'EOF'
fix: Intentional Case 2 probe failure resolved on main for session debug Case 1

The probe test failure text is matched by Ask Ark changelog evidence.
EOF
)"

echo "Created Case 1 changelog marker on main."
echo "Push: git push origin main"
