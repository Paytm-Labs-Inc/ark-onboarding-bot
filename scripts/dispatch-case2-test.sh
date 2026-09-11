#!/usr/bin/env bash
# Dispatch an Ark session designed to fail at verify with a fixable test error,
# so Ask Ark classifies it as Case 2 (needs_fix).
#
# Prerequisites:
#   1. ARK_API_KEY in .env (or exported)
#   2. Workspace doctor green for modeltest-modeltest-ark-onboarding-bot
#   3. Do NOT force cursor runtime — use default/claude runtime (cursor arkd is broken on your Mac)
#   4. Push a branch with an intentional test failure (see scripts/case2-test-failure.md)
#
# Usage:
#   ./scripts/dispatch-case2-test.sh
#   ./scripts/dispatch-case2-test.sh "my custom summary"

set -euo pipefail
cd "$(dirname "$0")/.."

# Capture explicit shell overrides only; read .env defaults after sourcing.
_CALLER_COMPUTE="${CASE2_COMPUTE:-}"
_CALLER_WORKSPACE="${ARK_DEFAULT_WORKSPACE:-}"
_CALLER_FLOW="${ARK_FIX_FLOW:-}"

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
FLOW="${_CALLER_FLOW:-${ARK_FIX_FLOW:-ark-feature}}"
SUMMARY="${1:-Case2 debug: fix intentional unit test failure}"

PROMPT=$(cat <<'EOF'
This is a controlled Case 2 debug test for Ask Ark.

Goal: fix the failing unit test, nothing else.

Steps:
1. Export CASE2_PROBE_FAIL=1
2. Run: CASE2_PROBE_FAIL=1 python3 -m unittest tests.test_case2_probe -v
3. You should see failure: "Intentional Case 2 probe failure"
4. Fix tests/test_case2_probe.py so the test passes when CASE2_PROBE_FAIL=1
5. Re-run until green: CASE2_PROBE_FAIL=1 python3 -m unittest tests.test_case2_probe -v
6. Do not change unrelated files

Open a PR when tests pass.
EOF
)

payload=$(FLOW="$FLOW" WORKSPACE="$WORKSPACE" COMPUTE="$COMPUTE" SUMMARY="$SUMMARY" PROMPT="$PROMPT" python3 - <<'PY'
import json
import os

compute = os.environ["COMPUTE"]
print(
    json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/start",
            "params": {
                "flow": os.environ["FLOW"],
                "workspace": os.environ["WORKSPACE"],
                "compute_name": compute,
                "summary": os.environ["SUMMARY"],
                "autonomy": "execute",
                "prompt": os.environ["PROMPT"],
            },
        }
    )
)
PY
)

echo "Dispatching Case 2 test session..."
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
" 2>/dev/null || true)

if [[ -n "$session_id" ]]; then
  echo ""
  echo "Session started: $session_id"
  echo ""
  echo "Wait until it fails (verify/implement), then paste this id into Ask Ark:"
  echo "  $session_id"
  echo ""
  echo "Poll status:"
  echo "  curl -s \"\$ARK_API_URL\" -H \"Authorization: Bearer \$ARK_API_KEY\" \\"
  echo "    -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"session/read\",\"params\":{\"op\":\"show\",\"sessionId\":\"$session_id\"}}'"
else
  echo ""
  echo "No session id returned — check the error above."
  exit 1
fi
