#!/usr/bin/env bash
# Dispatch an Ark session for Case 1 (already_fixed) testing.
#
# Case 1 needs:
#   - This session FAILS with a known error string
#   - main branch git log contains a commit whose subject matches that error
#
# Setup (once):
#   1. Run ./scripts/setup-case1-changelog.sh on main (subject includes full probe error)
#   2. Keep session-debug/case1-test branch with the probe still failing (CASE2_PROBE_FAIL=1)
#
# Usage:
#   CASE2_COMPUTE=picom-ark-machine ./scripts/dispatch-case1-test.sh

set -euo pipefail
cd "$(dirname "$0")/.."

# Capture explicit shell overrides only; read .env defaults after sourcing.
_CALLER_COMPUTE="${CASE2_COMPUTE:-}"
_CALLER_WORKSPACE="${ARK_DEFAULT_WORKSPACE:-}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${ARK_API_KEY:?Set ARK_API_KEY in .env}"
ARK_API_URL="${ARK_API_URL:-https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com/api/rpc}"
WORKSPACE="${_CALLER_WORKSPACE:-${ARK_DEFAULT_WORKSPACE:-modeltest-modeltest-ark-onboarding-bot}}"
COMPUTE="${_CALLER_COMPUTE:-${ARK_DEFAULT_COMPUTE:-picom-ark-machine}}"
FLOW="${ARK_FIX_FLOW:-ark-feature}"
SUMMARY="${1:-Case1 debug: probe failure already fixed on main}"

PROMPT=$(cat <<'EOF'
This is a controlled Case 1 debug test for Ask Ark (already fixed on main).

Goal: run the probe test so it FAILS — do not fix it.

Steps:
1. Export CASE2_PROBE_FAIL=1
2. Run: CASE2_PROBE_FAIL=1 python3 -m unittest tests.test_case2_probe -v
3. Confirm failure: "Intentional Case 2 probe failure"
4. Stop — do not change any files

The fix is already on main; this session should fail at verify with the known error.
EOF
)

payload=$(FLOW="$FLOW" WORKSPACE="$WORKSPACE" COMPUTE="$COMPUTE" SUMMARY="$SUMMARY" PROMPT="$PROMPT" python3 - <<'PY'
import json
import os

print(
    json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/start",
            "params": {
                "flow": os.environ["FLOW"],
                "workspace": os.environ["WORKSPACE"],
                "compute_name": os.environ["COMPUTE"],
                "summary": os.environ["SUMMARY"],
                "autonomy": "execute",
                "prompt": os.environ["PROMPT"],
            },
        }
    )
)
PY
)

echo "Dispatching Case 1 test session..."
echo "  workspace: $WORKSPACE"
echo "  compute:   $COMPUTE"
echo "  flow:      $FLOW"
echo ""

response=$(curl -s "$ARK_API_URL" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$payload")

echo "$response" | python3 -m json.tool 2>/dev/null || echo "$response"

session_id=$(echo "$response" | python3 -c "
import json, sys
d = json.load(sys.stdin)
r = d.get('result') or {}
print(r.get('sessionId') or r.get('session_id') or '')
if d.get('error'):
    raise SystemExit(1)
" 2>/dev/null || true)

if [[ -n "$session_id" ]]; then
  echo ""
  echo "Case 1 session started: $session_id"
  echo "Paste into Ask Ark after it fails at verify."
else
  echo ""
  echo "No session id returned — check the error above."
  exit 1
fi
