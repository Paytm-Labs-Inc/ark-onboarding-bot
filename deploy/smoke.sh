#!/usr/bin/env bash
# Go-live smoke for the onboarding bot's web service.
#
# Runs the same checks against a local container and against the deployed URL,
# so "it works on my machine" and "it works behind the ingress" are the same
# assertion. Every check prints PASS/FAIL and the script exits non-zero if any
# check failed, which makes it usable as a deploy gate as well as by hand.
#
#   ./deploy/smoke.sh http://localhost:8765
#   ARK_ACCESS_TOKEN=… ./deploy/smoke.sh https://foundry.mypaytm.com/onboarding-bot
#
# ARK_ACCESS_TOKEN is optional: when it is set the script also asserts that the
# gate actually rejects an unauthenticated request, which is the check that
# matters once this is on a public hostname.

set -uo pipefail

BASE="${1:-http://localhost:8765}"
BASE="${BASE%/}"
TOKEN="${ARK_ACCESS_TOKEN:-}"
TIMEOUT="${SMOKE_TIMEOUT_SECONDS:-90}"

pass=0
fail=0

ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail + 1)); }
note() { printf '        %s\n' "$1"; }

# ask <session-id> <question> -> sets LAST_BODY and LAST_CODE.
# Deliberately assigns globals rather than printing for the caller to capture:
# a command substitution would run this in a subshell and LAST_CODE would never
# reach the caller, which silently disables the auth diagnosis below.
# The token is branched on rather than collected into an array: under set -u,
# bash before 4.4 rejects an empty array expansion, and macOS still ships 3.2.
LAST_CODE=""
LAST_BODY=""
ask() {
  local payload raw
  payload=$(printf '{"session_id":"%s","question":"%s"}' "$1" "$2")
  if [ -n "$TOKEN" ]; then
    raw=$(curl -sS -m "$TIMEOUT" -w '\n%{http_code}' \
      -H "Authorization: Bearer $TOKEN" \
      -H 'Content-Type: application/json' \
      -d "$payload" "$BASE/api/ask" 2>/dev/null)
  else
    raw=$(curl -sS -m "$TIMEOUT" -w '\n%{http_code}' \
      -H 'Content-Type: application/json' \
      -d "$payload" "$BASE/api/ask" 2>/dev/null)
  fi
  LAST_CODE=$(printf '%s' "$raw" | tail -1)
  LAST_BODY=$(printf '%s' "$raw" | sed '$d')
}

# Turn an auth rejection into one clear diagnosis instead of several confusing
# content failures. A deploy gate that reports "no answer" when the real problem
# is a missing token sends whoever runs it hunting the wrong bug.
auth_problem() {
  case "$LAST_CODE" in
    401|403|3??)
      if [ -n "$TOKEN" ]; then
        bad "server rejected the supplied ARK_ACCESS_TOKEN (HTTP $LAST_CODE)"
      else
        bad "server requires authentication and ARK_ACCESS_TOKEN is not set (HTTP $LAST_CODE)"
        note "This is a runner configuration problem, not a bot failure."
      fi
      return 0 ;;
  esac
  return 1
}

echo
echo "Smoke: $BASE"
echo

# ---------------------------------------------------------------- liveness ---
echo "1. Service is up"
body=$(curl -sS -m 20 -o - -w '\n%{http_code}' "$BASE/health" 2>/dev/null)
code=$(printf '%s' "$body" | tail -1)
if [ "$code" = "200" ]; then ok "/health -> 200"; else bad "/health -> ${code:-no response}"; fi

# ------------------------------------------------------------------ ready ----
# /ready is the one that proves the embedding model and the corpus actually
# loaded inside the container, rather than just that the process is listening.
echo
echo "2. Retrieval is ready"
body=$(curl -sS -m 30 -o - -w '\n%{http_code}' "$BASE/ready" 2>/dev/null)
code=$(printf '%s' "$body" | tail -1)
payload=$(printf '%s' "$body" | sed '$d')
if [ "$code" = "200" ]; then
  ok "/ready -> 200"
  note "$(printf '%s' "$payload" | head -c 200)"
else
  bad "/ready -> ${code:-no response}"
  note "$(printf '%s' "$payload" | head -c 300)"
fi

# ------------------------------------------------------------- auth gate -----
echo
echo "3. Access gate"
if [ -n "$TOKEN" ]; then
  # The gate answers a signed-out request with a 303 to /login, so accept any
  # 3xx as well as an outright 401/403 — what matters is that the page itself
  # is not served.
  code=$(curl -sS -m 20 -o /dev/null -w '%{http_code}' "$BASE/" 2>/dev/null)
  if [ "$code" = "401" ] || [ "$code" = "403" ] || [[ "$code" =~ ^3[0-9][0-9]$ ]]; then
    ok "unauthenticated / -> $code (gate is enforcing)"
  else
    bad "unauthenticated / -> $code (expected 401/403 or a redirect to login)"
  fi
else
  note "SKIPPED — ARK_ACCESS_TOKEN not set, so auth is disabled by design."
  note "Set it to assert the gate before going live on a public hostname."
fi

# ------------------------------------------------------------ real answer ----
echo
echo "4. A real question answers, with a citation"
start=$(date +%s)
ask "smoke-in-scope" "How do I enroll a host?"
resp="$LAST_BODY"
elapsed=$(( $(date +%s) - start ))

answer=$(printf '%s' "$resp" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("answer",""))' 2>/dev/null)
ncites=$(printf '%s' "$resp" | python3 -c 'import json,sys;print(len(json.load(sys.stdin).get("citations",[])))' 2>/dev/null)

if auth_problem; then
  answer=""
elif [ -n "$answer" ]; then
  ok "answered in ${elapsed}s"
  note "$(printf '%s' "$answer" | head -c 160)…"
else
  bad "no answer returned after ${elapsed}s"
  note "$(printf '%s' "$resp" | head -c 300)"
fi

# The citation check is the one that would have caught the bug Harsh found in
# #21, where answers rendered correct but source-less.
if [ "${ncites:-0}" -gt 0 ]; then
  ok "carried $ncites citation(s)"
elif [ "$LAST_CODE" = "200" ]; then
  bad "answer carried no citations"
fi

# On the Pi backend an answer should land in a few seconds; the old Cursor-agent
# path took 30-45s. A slow pass here means the deployment is on the wrong
# backend, so it is worth flagging without failing the run.
if [ "$elapsed" -gt 15 ]; then
  note "WARNING: ${elapsed}s is slow for the Pi backend — check ANSWER_BACKEND."
fi

# --------------------------------------------------------------- refusal -----
echo
echo "5. An out-of-scope question is refused"
ask "smoke-out-of-scope" "What is the capital of France?"
resp="$LAST_BODY"
answer=$(printf '%s' "$resp" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("answer",""))' 2>/dev/null)

if auth_problem; then
  :
elif printf '%s' "$answer" | grep -qiE "don't have an answer|do not have an answer|roadmap"; then
  ok "refused (or deferred to the roadmap) as expected"
  note "$(printf '%s' "$answer" | head -c 160)"
elif printf '%s' "$answer" | grep -qi "paris"; then
  bad "answered an out-of-scope question — the refusal contract is broken"
  note "$(printf '%s' "$answer" | head -c 160)"
else
  bad "unexpected response to an out-of-scope question"
  note "$(printf '%s' "$answer" | head -c 200)"
fi

# ---------------------------------------------------------- stream latency ---
# TTFT (time to first token) is the number streaming exists to improve, and it
# is invisible to the /api/ask checks above -- those only see total time. This
# samples /api/ask/stream so a deploy reports both, and so a regression in
# first-token latency is caught by the same gate that catches a broken corpus.
#
# Each sample uses a fresh session id and rotates the question, because #26
# caches answers per question per chat: reusing either would measure the cache
# rather than the model.
echo
echo "6. Streaming latency"
SAMPLES="${SMOKE_LATENCY_SAMPLES:-10}"
lat_out=$(SMOKE_BASE="$BASE" SMOKE_TOKEN="$TOKEN" SMOKE_LATENCY_SAMPLES="$SAMPLES" \
  SMOKE_TIMEOUT_SECONDS="$TIMEOUT" python3 - <<'PY' 2>&1
import json, os, sys, time, urllib.error, urllib.request

base = os.environ["SMOKE_BASE"].rstrip("/")
token = os.environ.get("SMOKE_TOKEN", "")
samples = int(os.environ.get("SMOKE_LATENCY_SAMPLES", "10"))
timeout = float(os.environ.get("SMOKE_TIMEOUT_SECONDS", "90"))

QUESTIONS = [
    "How do I enroll a host?",
    "How do I get started with Ark?",
    "What is a workspace?",
    "How do I run a session with Claude Code?",
    "What is a flow?",
    # Deliberately a long-answer question: it is the shape that pushes the
    # gateway toward PI_TIMEOUT_SECONDS, so leaving it out would report a p95
    # that flatters the tail we actually care about.
    "How do I get Cursor working with Ark?",
]


def sample(index):
    """Return (ttft_seconds, total_seconds, completed) for one streamed ask."""
    payload = json.dumps(
        {
            "session_id": "smoke-latency-%d" % index,
            "question": QUESTIONS[index % len(QUESTIONS)],
        }
    ).encode()
    request = urllib.request.Request(
        base + "/api/ask/stream",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    if token:
        request.add_header("Authorization", "Bearer " + token)

    start = time.monotonic()
    ttft = None
    completed = False
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for line in response:
            if not line.startswith(b"data:"):
                continue
            try:
                event = json.loads(line[5:].decode("utf-8").strip())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            kind = event.get("type")
            if kind == "delta" and ttft is None:
                ttft = time.monotonic() - start
            elif kind == "done":
                completed = True
            elif kind == "error":
                return ttft, time.monotonic() - start, False
    return ttft, time.monotonic() - start, completed


def pct(values, q):
    """Nearest-rank percentile. Honest for the small n a smoke run affords."""
    ordered = sorted(values)
    rank = int(round(q * (len(ordered) - 1)))
    return ordered[max(0, min(len(ordered) - 1, rank))]


ttfts, totals, incomplete = [], [], 0
for index in range(samples):
    try:
        ttft, total, completed = sample(index)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print("ERR sample %d failed: %s" % (index, exc))
        incomplete += 1
        continue
    totals.append(total)
    if ttft is not None:
        ttfts.append(ttft)
    if not completed:
        incomplete += 1

if not totals:
    print("ERR no sample completed")
    sys.exit(1)

ms = lambda v: int(round(v * 1000))
if ttfts:
    print("TTFT   p50 %dms  p95 %dms  (n=%d)" % (ms(pct(ttfts, 0.5)), ms(pct(ttfts, 0.95)), len(ttfts)))
else:
    print("TTFT   no delta events seen -- the backend is not token-streaming")
print("Total  p50 %dms  p95 %dms  (n=%d)" % (ms(pct(totals, 0.5)), ms(pct(totals, 0.95)), len(totals)))
if incomplete:
    print("ERR %d of %d streams did not complete" % (incomplete, samples))
PY
)
lat_status=$?

printf '%s\n' "$lat_out" | while IFS= read -r line; do
  case "$line" in ERR*) : ;; *) note "$line" ;; esac
done

# The numbers themselves are reported, not gated: we have no agreed budget yet,
# and a threshold invented here would either never fire or block a deploy on
# gateway weather. A stream that never completes is a different thing -- that is
# a broken answer path, so it fails.
if [ "$lat_status" -ne 0 ] || printf '%s' "$lat_out" | grep -q '^ERR'; then
  bad "streaming latency probe reported incomplete streams"
  printf '%s\n' "$lat_out" | grep '^ERR' | while IFS= read -r line; do note "$line"; done
else
  ok "sampled $SAMPLES streamed answers"
fi

# ----------------------------------------------------------------- result ----
echo
echo "-------------------------------------------"
printf '  %d passed, %d failed\n' "$pass" "$fail"
echo "-------------------------------------------"
echo
[ "$fail" -eq 0 ] || exit 1
