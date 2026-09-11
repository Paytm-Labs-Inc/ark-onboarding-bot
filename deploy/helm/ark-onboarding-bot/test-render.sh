#!/usr/bin/env bash
#
# Render-assert matrix for the ark-onboarding-bot chart. Nothing else in CI
# renders it, so each launch-review decision the chart encodes is asserted
# here against the RENDERED output: image tag required, forwarded-proxy CIDR
# required behind the ingress, single replica unless acknowledged, secrets from
# Secrets Manager only, SSE-safe ingress, auth gate fails closed.
#
# Run locally: deploy/helm/ark-onboarding-bot/test-render.sh
# CI: the helm-render job in .github/workflows/helm.yml
set -uo pipefail
CHART="$(cd "$(dirname "$0")" && pwd)"
FAILED=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILED=1; }
render() { OUT="$(helm template bot "$CHART" "$@" 2>&1)"; RC=$?; }
BASE=(-f "$CHART/pai-risk-mlops-platform-values.yaml" --set image.tag=90000000000001-abcdef0-arm64 --set web.forwardedAllowIps=10.42.0.0/16)
# The overlay now enables Redis, because chat threads must outlive a deploy.
# Cases below that assert CHART-level behaviour (redis is opt-in; the guard
# fires without SESSION_STORE) have to say so explicitly, or they would be
# reading production's decision back as if it were the default and quietly
# stop testing anything.
NOREDIS=("${BASE[@]}" --set redis.enabled=false)

echo "== A: defaults with no overlay -- must FAIL (image.tag required) =="
render; { [ "$RC" -ne 0 ] && grep -q 'image.tag is required' <<<"$OUT"; } && pass "no tag rejected" || fail "rendered without an image tag"

echo "== B: production overlay + tag + proxy CIDR -- renders, encodes the launch decisions =="
render "${BASE[@]}"
[ "$RC" -eq 0 ] && pass "renders clean" || fail "should render: $(echo "$OUT" | grep -o 'Error:.*' | head -1)"
grep -q 'replicas: 1' <<<"$OUT" && grep -q 'type: Recreate' <<<"$OUT" && pass "single replica, Recreate" || fail "replica/strategy drift"
grep -q 'secretKey: PI_API_KEY' <<<"$OUT" && grep -q 'secretKey: ARK_ACCESS_TOKEN' <<<"$OUT" && pass "ExternalSecret carries both required keys" || fail "ExternalSecret keys missing"
grep -q 'key: "pai-risk-mlops/platform/ark-onboarding-bot"' <<<"$OUT" && pass "Secrets Manager path from overlay" || fail "SM path missing"
[ "$(grep -c 'path: /onboarding-bot' <<<"$OUT")" -eq 2 ] && pass "/onboarding-bot on both hosts" || fail "ingress paths: $(grep -c 'path: /onboarding-bot' <<<"$OUT")"
grep -q 'proxy-buffering: "off"' <<<"$OUT" && pass "SSE-safe: proxy buffering off" || fail "proxy buffering not off -- streaming would arrive all at once"
grep -q 'FORWARDED_ALLOW_IPS: "10.42.0.0/16"' <<<"$OUT" && pass "proxy CIDR reaches the pod env" || fail "FORWARDED_ALLOW_IPS not in env"
grep -q 'readOnlyRootFilesystem' <<<"$OUT" && grep -q 'drop: \["ALL"\]' <<<"$OUT" && grep -q 'runAsNonRoot: true' <<<"$OUT" && pass "hardened securityContext" || fail "securityContext incomplete"
grep -A3 'startupProbe:' <<<"$OUT" | grep -q 'path: /ready' && grep -A3 'readinessProbe:' <<<"$OUT" | grep -q 'path: /ready' && grep -A3 'livenessProbe:' <<<"$OUT" | grep -q 'path: /health' && pass "startup+readiness on /ready, liveness on /health" || fail "probes drift"
grep -q 'runAsUser: 10001' <<<"$OUT" && grep -q 'fsGroup: 10001' <<<"$OUT" && pass "pod runs as the image uid with fsGroup (mounted volumes writable)" || fail "runAsUser/fsGroup missing"
grep -q 'name: ark-onboarding-bot-slack' <<<"$OUT" && fail "slack deployment rendered while slack.enabled=false" || pass "no slack deployment by default"
grep -q 'auth-url' <<<"$OUT" && fail "auth annotations present while authGate off" || pass "no auth gate by default"
grep -q 'automountServiceAccountToken: false' <<<"$OUT" && pass "no SA token mounted" || fail "SA token mounted"

echo "== C: forwardedAllowIps='*' (platform interim) -- must RENDER =="
render "${BASE[@]}" --set 'web.forwardedAllowIps=*'; { [ "$RC" -eq 0 ] && grep -q 'FORWARDED_ALLOW_IPS: "\*"' <<<"$OUT"; } && pass "wildcard interim renders into the pod env" || fail "'*' did not render (rc=$RC)"

echo "== C2: ingress on, value forced empty -- must FAIL =="
render -f "$CHART/pai-risk-mlops-platform-values.yaml" --set image.tag=90000000000001-abcdef0-arm64 --set 'web.forwardedAllowIps='; { [ "$RC" -ne 0 ] && grep -q 'forwardedAllowIps is required' <<<"$OUT"; } && pass "empty value rejected" || fail "rendered with an empty proxy value"

echo "== C3: the guards no other case reaches -- must FAIL =="
render "${BASE[@]}" --set ingress.authGate.enabled=true --set ingress.authGate.authUrl=http://oauth2-proxy.foundry-site.svc.cluster.local/oauth2/auth; { [ "$RC" -ne 0 ] && grep -q 'signinUrl is required' <<<"$OUT"; } && pass "auth gate without signinUrl rejected" || fail "auth gate rendered without a signin URL"
render "${BASE[@]}" --set 'externalSecret.awsSecretPath='; { [ "$RC" -ne 0 ] && grep -q 'awsSecretPath is required' <<<"$OUT"; } && pass "missing awsSecretPath rejected" || fail "rendered without a secret path"

echo "== D: replicas=2 without acknowledgement -- must FAIL; with it -- renders =="
render "${BASE[@]}" --set replicas=2; { [ "$RC" -ne 0 ] && grep -q 'blocker 5' <<<"$OUT"; } && pass "multi-replica rejected until state is out of the pod" || fail "replicas=2 rendered silently"
render "${BASE[@]}" --set replicas=2 --set stateful.multiReplicaAcknowledged=true; [ "$RC" -eq 0 ] && grep -q 'replicas: 2' <<<"$OUT" && pass "acknowledged multi-replica renders" || fail "acknowledged multi-replica failed"

echo "== E: auth gate on without URLs -- must FAIL; with both -- annotations present =="
render "${BASE[@]}" --set ingress.authGate.enabled=true; { [ "$RC" -ne 0 ] && grep -q 'authUrl is required' <<<"$OUT"; } && pass "gate without auth-url rejected (fails closed)" || fail "gate rendered with empty auth-url"
render "${BASE[@]}" --set ingress.authGate.enabled=true --set ingress.authGate.authUrl=http://oauth2-proxy.foundry-site.svc.cluster.local/oauth2/auth --set 'ingress.authGate.signinUrl=https://foundry.mypaytm.com/oauth2/start?rd=$escaped_request_uri'
[ "$RC" -eq 0 ] && grep -q 'auth-url: "http://oauth2-proxy' <<<"$OUT" && grep -q 'X-Auth-Request-Email' <<<"$OUT" && pass "gate renders with auth-url + identity headers" || fail "gate render wrong (rc=$RC)"

echo "== F: slack.enabled -- worker + its secret keys =="
render "${NOREDIS[@]}" --set slack.enabled=true
[ "$RC" -eq 0 ] && grep -q 'name: ark-onboarding-bot-slack' <<<"$OUT" && grep -q 'secretKey: SLACK_APP_TOKEN' <<<"$OUT" && grep -q 'src.slack_app' <<<"$OUT" && pass "slack worker + tokens render" || fail "slack render wrong"
# Redis off here on purpose: the claim is that the SLACK worker adds no Service,
# and Redis legitimately brings its own, which would mask a regression.
[ "$(grep -c '^kind: Service$' <<<"$OUT")" -eq 1 ] && pass "slack worker has no Service (outbound only)" || fail "Service count with slack on: $(grep -c '^kind: Service$' <<<"$OUT")"

echo "== G: no ingress -- CIDR not required =="
render -f "$CHART/pai-risk-mlops-platform-values.yaml" --set image.tag=90000000000001-abcdef0-arm64 --set ingress.enabled=false; [ "$RC" -eq 0 ] && ! grep -q 'kind: Ingress' <<<"$OUT" && pass "renders without ingress" || fail "no-ingress case failed"

echo "== H: log persistence -- PVC and claim =="
render "${BASE[@]}" --set logs.persistence.enabled=true; [ "$RC" -eq 0 ] && grep -q 'kind: PersistentVolumeClaim' <<<"$OUT" && grep -q 'claimName: ark-onboarding-bot-logs' <<<"$OUT" && pass "PVC wired when enabled" || fail "persistence render wrong"
# The deployed overlay MUST persist: on an emptyDir every release destroys the
# query log and the feedback, which is the beta's only record of what was asked.
render "${BASE[@]}"; [ "$RC" -eq 0 ] && grep -q 'kind: PersistentVolumeClaim' <<<"$OUT" && grep -q 'claimName: ark-onboarding-bot-logs' <<<"$OUT" && pass "production overlay persists the log" || fail "overlay no longer persists the query log"
# The chart's own default stays ephemeral -- persistence is an explicit per-
# environment choice, not something a new consumer of the chart inherits silently.
# awsSecretPath is required by a guard, so it must be supplied or the render
# FAILS and the absence of a PVC in an error message would read as a pass --
# an assertion that can never fail. RC is checked for exactly that reason.
render --set image.tag=90000000000001-abcdef0-arm64 --set ingress.enabled=false --set externalSecret.awsSecretPath=example/path; [ "$RC" -eq 0 ] && ! grep -q 'PersistentVolumeClaim' <<<"$OUT" && pass "chart default is emptyDir (documented as ephemeral)" || fail "chart default should render, and stay emptyDir"

echo "== I: redis for chat sessions =="
# Off by default: a chart consumer who has provisioned no volume must still render.
render "${NOREDIS[@]}"; [ "$RC" -eq 0 ] && ! grep -q 'component: redis' <<<"$OUT" && pass "redis absent unless enabled" || fail "redis should be opt-in"
# Enabled: the three objects, and a StatefulSet rather than a Deployment -- the
# AOF is the record, and a rolling Deployment fights its own RWO volume.
render "${BASE[@]}" --set redis.enabled=true --set web.env.SESSION_STORE=redis --set web.env.REDIS_URL=redis://ark-onboarding-bot-redis:6379/0
[ "$RC" -eq 0 ] && grep -q 'kind: StatefulSet' <<<"$OUT" && grep -q 'name: ark-onboarding-bot-redis' <<<"$OUT" && grep -q 'targetPort: redis' <<<"$OUT" && pass "statefulset + service render" || fail "redis render wrong (rc=$RC)"
# The durability argument, asserted rather than trusted: without an AOF a
# restart loses every chat, and under allkeys-lru a body can be evicted while
# its sidebar entry survives, leaving a thread that lists and opens empty.
[ "$RC" -eq 0 ] && grep -q -- '--appendonly' <<<"$OUT" && grep -q -- '--appendfsync' <<<"$OUT" && grep -q 'noeviction' <<<"$OUT" && pass "AOF + noeviction on the server args" || fail "redis durability config missing"
# The probe must fail on a Redis ERROR REPLY, not only a non-zero exit: redis-cli
# exits 0 when the server answers "OOM command not allowed", which is precisely
# the write-refusing state readiness is here to catch.
# Grep the NESTED form: a `command:` at exec's own indent is a sibling, not a
# child, so the probe renders with no command at all -- and a bare grep for the
# script text passes anyway, which is how the mis-indent shipped in the first place.
[ "$RC" -eq 0 ] && grep -q '^              command: \["sh", "-c", "\[ ..\$(redis-cli set' <<<"$OUT" && pass "readiness command is nested under exec" || fail "readiness probe command is not nested under exec"
# Redis is the record here, so it must hold a volume. foundry-platform's Redis
# is a bus and holds none; copying that shape would lose every chat on restart.
[ "$RC" -eq 0 ] && grep -q 'volumeClaimTemplates' <<<"$OUT" && grep -q 'mountPath: /data' <<<"$OUT" && pass "AOF has a volume to live on" || fail "redis has no persistent volume"
# The exporter mirrors foundry-platform and stays off unless asked for.
[ "$RC" -eq 0 ] && ! grep -q 'redis-exporter' <<<"$OUT" && pass "exporter off by default" || fail "exporter should be opt-in"
render "${BASE[@]}" --set redis.enabled=true --set redis.exporter.enabled=true --set web.env.SESSION_STORE=redis
[ "$RC" -eq 0 ] && grep -q 'redis-exporter' <<<"$OUT" && grep -q 'name: metrics' <<<"$OUT" && pass "exporter adds a sidecar and a metrics port" || fail "exporter render wrong"
# Fail closed: a Redis the app never connects to looks healthy while chats
# still vanish on restart.
# Built from the chart, not the overlay: the overlay supplies SESSION_STORE, so
# redis.enabled on top of it is the CORRECT pairing rather than the one the
# guard rejects.
MINIMAL=(--set image.tag=90000000000099-0000000-arm64 --set ingress.enabled=false --set externalSecret.awsSecretPath=example/path)
# Each guard is greped for its OWN message, the way every other guard case in
# this file does it. Exit code alone is not enough: the fixture below trips both
# guards, so `RC -ne 0` passed with either one deleted -- which is how a guard
# added specifically to stop an untested claim ended up untested itself.
render "${MINIMAL[@]}" --set redis.enabled=true
{ [ "$RC" -ne 0 ] && grep -q 'without SESSION_STORE=redis' <<<"$OUT"; } && pass "redis.enabled without SESSION_STORE=redis rejected" || fail "should reject redis.enabled with no SESSION_STORE"
# An absent key and a wrong VALUE are the same mistake with different symptoms.
render "${MINIMAL[@]}" --set redis.enabled=true --set web.env.SESSION_STORE=memory --set web.env.REDIS_URL=redis://x:6379/0
{ [ "$RC" -ne 0 ] && grep -q 'without SESSION_STORE=redis' <<<"$OUT"; } && pass "redis.enabled with SESSION_STORE=memory rejected" || fail "should reject a Redis the app will not use"
# The URL guard keys on SESSION_STORE, not on redis.enabled, so it also covers
# the external-Redis path values.yaml documents (enabled:false + a URL).
render "${MINIMAL[@]}" --set redis.enabled=true --set web.env.SESSION_STORE=redis
{ [ "$RC" -ne 0 ] && grep -q 'no non-empty REDIS_URL' <<<"$OUT"; } && pass "SESSION_STORE=redis without REDIS_URL rejected" || fail "should reject a missing REDIS_URL"
render "${MINIMAL[@]}" --set web.env.SESSION_STORE=redis
{ [ "$RC" -ne 0 ] && grep -q 'no non-empty REDIS_URL' <<<"$OUT"; } && pass "external-redis path without REDIS_URL rejected" || fail "enabled:false + SESSION_STORE=redis must still need a URL"
# Empty is not present: every other guard here rejects a blank value, and a
# blank URL is the exact READY-pod-that-500s state the message describes.
render "${MINIMAL[@]}" --set redis.enabled=true --set web.env.SESSION_STORE=redis --set web.env.REDIS_URL=""
{ [ "$RC" -ne 0 ] && grep -q 'no non-empty REDIS_URL' <<<"$OUT"; } && pass "empty REDIS_URL rejected" || fail "an empty URL must be rejected like a missing one"
# Matches the app, which does .strip().lower() on this variable.
render "${MINIMAL[@]}" --set redis.enabled=true --set web.env.SESSION_STORE=Redis --set web.env.REDIS_URL=redis://x:6379/0
[ "$RC" -eq 0 ] && pass "SESSION_STORE is case-insensitive, as the app reads it" || fail "Redis should be accepted: the app lowercases"

echo "== J: the overlay actually persists chat, and the tag is well-formed =="
# The feature this chart exists to ship is the sidebar; on the default store a
# release empties it. Assert the deployed overlay opts out of that.
render "${BASE[@]}"
[ "$RC" -eq 0 ] && grep -q 'SESSION_STORE: "redis"' <<<"$OUT" && grep -q 'REDIS_URL:' <<<"$OUT" && grep -q 'kind: StatefulSet' <<<"$OUT" && pass "deployed overlay persists chat threads" || fail "overlay must set SESSION_STORE=redis AND REDIS_URL"
# Nothing rendered the tag actually committed: every case above overrides it,
# so a fat-fingered digit or a reverted bump stayed green. The overlay comment
# records a hard floor of ordinal 59 -- below it the pod crash-loops on first
# sync and nothing recovers it, since there is no image-updater.
render -f "$CHART/pai-risk-mlops-platform-values.yaml" --set web.forwardedAllowIps=10.42.0.0/16
COMMITTED_TAG=$(grep -oE 'ark-chatbot:[^"]+' <<<"$OUT" | head -1 | cut -d: -f2)
if [[ "$COMMITTED_TAG" =~ ^9[0-9]{13}-[0-9a-f]{7,40}-arm64$ ]]; then
  pass "committed tag is well-formed ($COMMITTED_TAG)"
  # Computed INSIDE the match. Unconditionally, a malformed tag makes this
  # substring a non-number and `set -u` kills the script here -- which today
  # only hides the summary line, because this is the last assertion in the
  # file, and tomorrow silently skips whoever appends a case K.
  ORDINAL=$(( 10#${COMMITTED_TAG:2:12} ))
  [ "$ORDINAL" -ge 59 ] && pass "committed ordinal $ORDINAL is above the floor of 59" || fail "ordinal $ORDINAL is below the documented floor of 59"
else
  fail "committed tag malformed: $COMMITTED_TAG"
fi

echo; [ "$FAILED" -eq 0 ] && echo "All ark-onboarding-bot render assertions passed." || echo "Render assertions FAILED."; exit "$FAILED"
