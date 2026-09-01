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
render "${BASE[@]}" --set slack.enabled=true
[ "$RC" -eq 0 ] && grep -q 'name: ark-onboarding-bot-slack' <<<"$OUT" && grep -q 'secretKey: SLACK_APP_TOKEN' <<<"$OUT" && grep -q 'src.slack_app' <<<"$OUT" && pass "slack worker + tokens render" || fail "slack render wrong"
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

echo; [ "$FAILED" -eq 0 ] && echo "All ark-onboarding-bot render assertions passed." || echo "Render assertions FAILED."; exit "$FAILED"
