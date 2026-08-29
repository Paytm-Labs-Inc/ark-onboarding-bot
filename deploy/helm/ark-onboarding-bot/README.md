# ark-onboarding-bot -- Helm chart

The AskArk onboarding assistant (`Paytm-Labs-Inc/ark-onboarding-bot`): a web UI
served at `/onboarding-bot` on the doc-site hosts, and an optional Slack
(Socket Mode) worker. One image, two Deployments.

This chart follows the same shape as `foundry-site`: a `values.yaml` of
defaults with the reason beside every knob, a `pai-risk-mlops-platform-values.yaml`
overlay for the cluster, secrets from AWS Secrets Manager through the cluster's
`ClusterSecretStore`, an nginx Ingress on the shared hosts, and a
`test-render.sh` matrix that turns each launch decision into a render assertion.

## What the chart encodes on purpose

| Decision | Where | Why |
|---|---|---|
| `image.tag` is required, no default | `_helpers.tpl` guard | argocd-image-updater writes the tag; nothing should ever deploy `:latest`. |
| `web.forwardedAllowIps` required behind the ingress; empty refused | guard | Per-user rate limiting and the `Secure` login cookie both depend on trusting only the ingress controller's `X-Forwarded-*`. `*` is the platform's interim behind the ClusterIP-only Service (only nginx can reach the pod); the overlay swaps it for the ingress-pod CIDR when the EKS/VPC value arrives. |
| `replicas: 1`, `strategy: Recreate` | `values.yaml`, guard | Sessions and the answer cache are in-process; logs are files. More than one pod needs sticky sessions and a durable log first (`stateful.multiReplicaAcknowledged`). |
| Secrets only via `ExternalSecret` | `external-secret.yaml` | Nothing secret in git. One SM entry, JSON properties named in values. |
| `proxy-buffering: off` on the Ingress | `values.yaml` | The bot streams answers over SSE; buffered, they arrive all at once. |
| `startupProbe` on `/ready` | `values.yaml` | The encoder warms on first `/ready`; liveness must not kill a pod that is still warming. `/ready` also fails without the backend credential. |
| Auth gate off, `required` URLs when on | `ingress.yaml`, guard | Same auth-url pattern as the doc-site funnel; an empty auth-url is silently dropped by nginx-ingress (fails OPEN), so the render refuses it. |
| `readOnlyRootFilesystem: false` for the first deploy | `values.yaml` | The mounts for every write path are in place; flip to true after the smoke passes against it, not before. |

## Go-live, in order

Each step unblocks the next. Steps 2-4 are platform-side.

1. ~~**ECR repository** `pai-mlops-platform/ark-chatbot`, and the four
   `AWS_ECR_*` variables in the bot repo.~~ **Done.** `publish-image.yml`
   authenticates over GitHub OIDC, emits the platform's tag scheme
   (`<ordinal>-<sha>-arm64`), and is gated on the eval workflow, so every green
   merge to `main` publishes automatically and a red eval publishes nothing.
2. **Secrets Manager entry** `pai-risk-mlops/platform/ark-onboarding-bot` with JSON
   properties `PI_API_KEY`, `ARK_ACCESS_TOKEN` (and `SLACK_BOT_TOKEN`,
   `SLACK_APP_TOKEN` when the Slack worker is enabled).
3. **Egress** from the cluster to `api.inference.paytm.com` (the Pi Inference
   gateway). The pod refuses to start without `PI_API_KEY`, but a blocked egress
   shows up as every answer failing after a healthy start -- check this before
   the smoke, not after.
4. **The GitOps change** -- one Application in the pi-risk-mlops app-of-apps
   (`k8s/infra-applications/application-bootstrap/pai-risk-mlops-platform-values.yaml`):

   ```yaml
   - name: ark-onboarding-bot
     repo: https://github.com/Paytm-Labs-Inc/ark-onboarding-bot   # public: ArgoCD needs no new credential
     path: deploy/helm/ark-onboarding-bot
     namespace: ark-onboarding-bot
     createNamespace: true
     autosync: true
     selfheal: true
     autodelete: true
     helm:
       releaseName: ark-onboarding-bot
       valueFiles:
         - values.yaml
         - pai-risk-mlops-platform-values.yaml
   ```
   **Raised as pi-risk-mlops PR #268.** This repository is public, so ArgoCD reads
   it with no repo-creds entry -- the same arrangement `foundry-site` already uses.
   If the platform prefers charts co-located in foundry-platform, the same
   directory moves there unchanged (it was written in that chart's shape).

   **The image tag is pinned here, not tracked by argocd-image-updater.** An
   earlier version of this file suggested the control plane's updater annotations.
   Two things were wrong with that, both checked against the app-of-apps repo:

   - The key is `extraAnnotations:`, not `annotations:`. The bootstrap chart only
     renders the former (`templates/application.yaml`), so the block would have
     been dropped silently and the updater would never have run.
   - `write-back-method: git` needs a **write-enabled** repository credential for
     the repo it commits to, and credentials in that cluster are per-repository --
     there are no repo-creds prefix templates. There is none for
     `ark-onboarding-bot`. `pi-agents-insights-repo-external-secret.yaml` records
     how the read-only case fails: ArgoCD syncs normally for a day, the updater
     picks the right image and builds the commit, and then `git push` returns
     `128 Unauthorized`.

   So bumping the deployed image is a one-line PR to `image.tag` in the overlay in
   *this* repo, which needs nobody outside the team. Wire the updater up later if
   someone provisions a write-enabled credential -- and use `extraAnnotations`.

5. **The overlay ships `web.forwardedAllowIps: "*"`**, the platform's interim
   behind the ClusterIP-only Service. When the nginx-ingress pod CIDR arrives
   (`kubectl -n <ingress-ns> get pods -o wide`), change it in the overlay file --
   never `kubectl edit`; `selfHeal` reverts that. The render refuses an empty
   value. `image.tag` already carries a published image; bump it there to deploy a
   newer one (step 4 explains why the updater is not wired up).
   **Secret rotation**: the pod reads the Secret at start (`envFrom`) and
   `checksum/env` covers only the ConfigMap, so a rotated `PI_API_KEY` needs
   `kubectl -n ark-onboarding-bot rollout restart deploy/ark-onboarding-bot-web`
   after the ExternalSecret refresh (there is no Reloader in the platform charts).
6. **Smoke**: from the bot repo, `ARK_ACCESS_TOKEN=... ./deploy/smoke.sh
   https://foundry.mypaytm.com/onboarding-bot` -- seven checks including
   TTFT p50/p95. Post the output.
7. **Then**, one at a time: `slack.enabled: true` once the Slack tokens are in the
   secret bag; `web.securityContext.readOnlyRootFilesystem: true` once the smoke
   passes against it; the auth gate once an oauth2-proxy with an all-employee
   audience exists (the doc-site one is scoped to a need-to-know group and
   should not be shared).

## Rollback

Edit `image.tag` in the overlay to a previous tag that exists in ECR and merge.
ArgoCD rolls it within minutes. Do not `kubectl set image`: `selfHeal` reverts it.

## Verify locally

```bash
helm lint deploy/helm/ark-onboarding-bot -f deploy/helm/ark-onboarding-bot/pai-risk-mlops-platform-values.yaml
bash deploy/helm/ark-onboarding-bot/test-render.sh
```

## Not in this chart, on purpose

- **No PodDisruptionBudget**: with one replica it would block node drains.
- **No NetworkPolicy**: the cluster's egress posture was not visible from the
  repo; step 3 above is where that lives.
- **No HPA**: scaling is a stateful decision here (see the replicas guard), not a CPU one.
- **No probes on the Slack worker**: it exposes no HTTP endpoint, so a wedged
  Socket Mode connection looks like a quiet channel. Add a heartbeat-file probe
  when `slack.enabled` is flipped on, not before.
