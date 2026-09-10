#!/usr/bin/env bash
# Quick local verification for Ask Ark (layers 1–3) without full e2e.
#
#   ./scripts/verify-local.sh
#   ./scripts/verify-local.sh --base http://127.0.0.1:8765
#   ./scripts/verify-local.sh --skip-dispatch    # skip Ark session start probe
#   ./scripts/verify-local.sh --doctor           # also run workspace doctor (slow)
#   ./scripts/verify-local.sh --full-tests       # full unittest suite (slow)
#
# Loads .env from the repo root. Exits non-zero if any check fails.

set -uo pipefail
cd "$(dirname "$0")/.."

BASE="http://127.0.0.1:8765"
SKIP_DISPATCH=0
RUN_DOCTOR=0
FULL_TESTS=0
STOP_PROBE_SESSION=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base) BASE="${2:?}"; shift 2 ;;
    --skip-dispatch) SKIP_DISPATCH=1; shift ;;
    --doctor) RUN_DOCTOR=1; shift ;;
    --full-tests) FULL_TESTS=1; shift ;;
    --keep-dispatch-session) STOP_PROBE_SESSION=0; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

BASE="${BASE%/}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

pass=0
fail=0
skip=0

ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail + 1)); }
note() { printf '        %s\n' "$1"; }
skipped() { printf '  \033[33mSKIP\033[0m  %s\n' "$1"; skip=$((skip + 1)); }

if [[ -x .venv/bin/python ]]; then
  PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  bad "python3 not found"
  exit 1
fi

echo
echo "Ask Ark local verify"
echo "  base: ${BASE}"
echo

# ------------------------------------------------------------------ Layer 1 --
echo "Layer 1 — Web app + unit tests"

code=$(curl -sS -m 15 -o /dev/null -w '%{http_code}' "$BASE/health" 2>/dev/null || true)
if [[ "$code" == "200" ]]; then
  ok "/health -> 200"
else
  bad "/health -> ${code:-no response} (is python -m src.web running?)"
fi

ready_body=$(curl -sS -m 45 "$BASE/ready" 2>/dev/null || true)
ready_code=$(curl -sS -m 45 -o /dev/null -w '%{http_code}' "$BASE/ready" 2>/dev/null || true)
if [[ "$ready_code" == "200" ]]; then
  ok "/ready -> 200"
  note "$(printf '%s' "$ready_body" | head -c 180)"
else
  bad "/ready -> ${ready_code:-no response} (check PI_API_KEY / corpus)"
  note "$(printf '%s' "$ready_body" | head -c 240)"
fi

if [[ "$FULL_TESTS" == "1" ]]; then
  note "Running full test suite (may take a minute)..."
  if "$PYTHON" -m unittest discover -s tests -q; then
    ok "unittest discover -s tests"
  else
    bad "unittest discover -s tests"
  fi
else
  note "Running focused unit tests (use --full-tests for all)..."
  if "$PYTHON" -m unittest \
    tests.test_ark_client \
    tests.test_session_actions \
    tests.test_intent_router \
    tests.test_session_debug \
    -q; then
    ok "unittest (session-debug subset)"
  else
    bad "unittest (session-debug subset)"
  fi
fi

# ------------------------------------------------------------------ Layer 2 --
echo
echo "Layer 2 — Pi Inference answer backend"

if [[ -z "${PI_API_KEY:-}" ]]; then
  skipped "src.ask (PI_API_KEY not set in .env)"
else
  ask_out=$("$PYTHON" -m src.ask "How do I enroll compute?" 2>&1) || true
  if printf '%s' "$ask_out" | grep -qi "PI_API_KEY not set\|RuntimeError\|refuse"; then
    bad "src.ask returned an error"
    note "$(printf '%s' "$ask_out" | tail -3 | head -c 240)"
  elif [[ -n "$ask_out" ]]; then
    ok "src.ask returned an answer"
    note "$(printf '%s' "$ask_out" | head -1 | head -c 120)..."
  else
    bad "src.ask produced no output"
  fi
fi

# ------------------------------------------------------------------ Layer 3 --
echo
echo "Layer 3 — Ark API + dispatch wiring"

if [[ "$SKIP_DISPATCH" == "1" ]]; then
  skipped "Ark session start probe (--skip-dispatch)"
else
  if [[ -z "${ARK_API_KEY:-}" ]]; then
    skipped "Ark session start (ARK_API_KEY not set)"
  else
    ARK_API_URL="${ARK_API_URL:-https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com/api/rpc}"
    WORKSPACE="${ARK_DEFAULT_WORKSPACE:-modeltest-modeltest-ark-onboarding-bot}"
    COMPUTE="${ARK_DEFAULT_COMPUTE:-ocl-cst-ticket-foundry-platform}"
    FLOW="${ARK_FIX_FLOW:-ark-feature}"

    payload=$(
      WORKSPACE="$WORKSPACE" COMPUTE="$COMPUTE" FLOW="$FLOW" "$PYTHON" - <<'PY'
import json, os
print(json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "session/start",
    "params": {
        "op": "start",
        "flow": os.environ["FLOW"],
        "workspace": os.environ["WORKSPACE"],
        "compute_name": os.environ["COMPUTE"],
        "summary": "Ask Ark verify-local probe (safe to stop)",
        "autonomy": "execute",
    },
}))
PY
    )

    response=$(curl -sS -m 30 "$ARK_API_URL" \
      -H "Authorization: Bearer $ARK_API_KEY" \
      -H "Content-Type: application/json" \
      -d "$payload")

    session_id=$(
      printf '%s' "$response" | "$PYTHON" -c "
import json, sys
d = json.load(sys.stdin)
r = d.get('result') or {}
if isinstance(r, dict):
    print(r.get('sessionId') or r.get('session_id') or (r.get('session') or {}).get('id') or '')
else:
    print('')
err = d.get('error') or {}
if isinstance(err, dict):
    print(err.get('message', ''), file=sys.stderr)
" 2>/dev/null || true
    )

    err_msg=$(
      printf '%s' "$response" | "$PYTHON" -c "
import json, sys
e = json.load(sys.stdin).get('error') or {}
print(e.get('message', '') if isinstance(e, dict) else e)
" 2>/dev/null || true
    )

    if [[ -n "$session_id" ]]; then
      ok "session/start -> $session_id"
      note "workspace=$WORKSPACE compute=$COMPUTE"

      if [[ "$STOP_PROBE_SESSION" == "1" ]]; then
        stop_payload=$(
          SESSION_ID="$session_id" "$PYTHON" - <<'PY'
import json, os
print(json.dumps({
    "jsonrpc": "2.0",
    "id": 2,
    "method": "session/stop",
    "params": {"op": "stop", "sessionId": os.environ["SESSION_ID"]},
}))
PY
        )
        stop_resp=$(curl -sS -m 20 "$ARK_API_URL" \
          -H "Authorization: Bearer $ARK_API_KEY" \
          -H "Content-Type: application/json" \
          -d "$stop_payload")
        if printf '%s' "$stop_resp" | grep -q '"error"'; then
          note "stop probe session manually: $session_id"
        else
          note "probe session stopped: $session_id"
        fi
      else
        note "probe session left running: $session_id (--keep-dispatch-session)"
      fi
    else
      bad "session/start failed"
      note "${err_msg:-$(printf '%s' "$response" | head -c 280)}"
      note "Check ARK_DEFAULT_WORKSPACE / ARK_DEFAULT_COMPUTE and workspace doctor"
    fi
  fi
fi

# ------------------------------------------------------------------ Optional --
if [[ "$RUN_DOCTOR" == "1" ]]; then
  echo
  echo "Optional — workspace doctor (slow; may timeout in UI)"
  if command -v ark >/dev/null 2>&1 && [[ -n "${ARK_DEFAULT_WORKSPACE:-}" ]]; then
    COMPUTE="${ARK_DEFAULT_COMPUTE:-ocl-cst-ticket-foundry-platform}"
    note "Running: ark workspace doctor ${ARK_DEFAULT_WORKSPACE} --compute ${COMPUTE} (may take several minutes)"
    doctor_out=$(ark workspace doctor "${ARK_DEFAULT_WORKSPACE}" --compute "$COMPUTE" 2>&1) || doctor_rc=$?
    if [[ "${doctor_rc:-0}" -eq 0 ]]; then
      ok "workspace doctor ${ARK_DEFAULT_WORKSPACE}"
      note "$(printf '%s' "$doctor_out" | tail -3 | head -c 240)"
    else
      bad "workspace doctor ${ARK_DEFAULT_WORKSPACE}"
      note "$(printf '%s' "$doctor_out" | tail -5 | head -c 320)"
      if printf '%s' "$doctor_out" | grep -qi '504\|gateway time'; then
        note "504 = API gateway timed out before doctor finished. Run doctor directly (slower path) or retry."
      fi
    fi
  else
    skipped "workspace doctor (ark CLI missing or ARK_DEFAULT_WORKSPACE unset)"
  fi
fi

# ------------------------------------------------------------------ Summary --
echo
echo "Summary: ${pass} passed, ${fail} failed, ${skip} skipped"
if [[ "$fail" -gt 0 ]]; then
  echo "Fix failures above before running full e2e."
  exit 1
fi
echo "Layers 1–3 look good. Next: workspace doctor, then Case 2 dispatch + Ask Ark."
exit 0
