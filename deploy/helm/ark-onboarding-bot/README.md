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
| `web.forwardedAllowIps` required behind the ingress; `*` refused | guard | Per-user rate limiting and the `Secure` login cookie both depend on trusting only the ingress controller's `X-Forwarded-*`. |
| `replicas: 1`, `strategy: Recreate` | `values.yaml`, guard | Sessions and the answer cache are in-process; logs are files. More than one pod needs sticky sessions and a durable log first (`stateful.multiReplicaAcknowledged`). |
| Secrets only via `ExternalSecret` | `external-secret.yaml` | Nothing secret in git. One SM entry, JSON properties named in values. |
| `proxy-buffering: off` on the Ingress | `values.yaml` | The bot streams answers over SSE; buffered, they arrive all at once. |
| `startupProbe` on `/ready` | `values.yaml` | The encoder warms on first `/ready`; liveness must not kill a pod that is still warming. `/ready` also fails without the backend credential. |
| Auth gate off, `required` URLs when on | `ingress.yaml`, guard | Same auth-url pattern as the doc-site funnel; an empty auth-url is silently dropped by nginx-ingress (fails OPEN), so the render refuses it. |
| `readOnlyRootFilesystem: false` for the first deploy | `values.yaml` | The mounts for every write path are in place; flip to true after the smoke passes against it, not before. |

## Go-live, in order

Each step unblocks the next. Steps 1-4 are platform-side.

1. **ECR repository** `pai-mlops-platform/ark-chatbot`, and in the bot repo the
   four variables `AWS_ECR_PUSH_ROLE_ARN / REGION / ACCOUNT_ID / REPO_PREFIX`. The
   bot's `publish-image.yml` already emits the platform's tag scheme
   (`<ordinal>-<sha>-arm64`) and is gated on its eval workflow; the next merge to
   `main` pushes the first image.
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
     # image-updater, same scheme as the control plane:
     annotations:
       argocd-image-updater.argoproj.io/image-list: bot=880170353725.dkr.ecr.ap-south-1.amazonaws.com/pai-mlops-platform/ark-chatbot
       argocd-image-updater.argoproj.io/bot.allow-tags: regexp:^[0-9]{14}-[a-f0-9]{7,40}-arm64$
       argocd-image-updater.argoproj.io/bot.update-strategy: alphabetical
       argocd-image-updater.argoproj.io/bot.helm.image-name: image.repository
       argocd-image-updater.argoproj.io/bot.helm.image-tag: image.tag
       argocd-image-updater.argoproj.io/write-back-method: git
       argocd-image-updater.argoproj.io/write-back-target: helmvalues:./pai-risk-mlops-platform-values.yaml
   ```
   This repository is public, so ArgoCD reads it with no repo-creds entry. If the
   platform prefers charts co-located in foundry-platform, the same directory moves
   there unchanged (it was written in that chart's shape).
5. **Fill the overlay**: `web.forwardedAllowIps` = the nginx-ingress controller
   pods' CIDR on the cluster (`kubectl -n <ingress-ns> get pods -o wide`). The
   render refuses to proceed without it. `image.tag` fills itself on the first
   image-updater write-back; until then the Application shows a render error,
   which is the correct state for "no image has been published yet".
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
helm lint deploy/helm/ark-onboarding-bot -f deploy/helm/ark-onboarding-bot/pai-risk-mlops-platform-values.yaml \
  --set image.tag=90000000000001-abcdef0-arm64 --set web.forwardedAllowIps=10.42.0.0/16
bash deploy/helm/ark-onboarding-bot/test-render.sh
```

## Not in this chart, on purpose

- **No PodDisruptionBudget**: with one replica it would block node drains.
- **No NetworkPolicy**: the cluster's egress posture was not visible from the
  repo; step 3 above is where that lives.
- **No HPA**: scaling is a stateful decision here (see the replicas guard), not a CPU one.
