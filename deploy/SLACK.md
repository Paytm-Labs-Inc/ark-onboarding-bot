# Slack integration (Ark trigger)

Ask Ark onboarding questions from Slack via **`/onboard`** (slash command) or
**@ArkBot** in a dedicated channel. Slack is a thin bridge; the deployed bot
(`POST /api/ask`) does retrieval + answer generation.

## Architecture

```
Slack /onboard <question>
    → Ark webhook (source: slack)
    → trigger: slack-onboarding-slash
    → flow: onboarding-bot-slack
    → agent curls POST /api/ask (channel=slack)
    → agent posts answer + sources to Slack thread (MONITOR_SLACK_TOKEN)
```

Artifacts live in this repo under `deploy/slack/`:

| File | Purpose |
|------|---------|
| `workspace.yaml` | Repo-less Ark workspace (curl + secrets) |
| `flow-onboarding-bot-slack.yaml` | Single-stage flow definition |
| `trigger-slack-onboarding-slash.yaml` | `/onboard` slash command |
| `trigger-slack-onboarding-mention.yaml` | Optional channel @-mention (disabled by default) |

## Prerequisites

1. **Deployed bot** reachable from Ark compute (not loopback-only).
   - Current backend example: `http://10.150.28.187:8765`
   - With prefix: `/onboarding-bot` → API at `/onboarding-bot/api/ask`
   - Public (after ingress): `https://foundry.mypaytm.com/onboarding-bot/`
2. **`MONITOR_SLACK_TOKEN`** — Slack bot token with `chat:write` (same env var
   used by PR-review Slack flows).
3. **`ONBOARDING_BOT_ACCESS_TOKEN`** (optional) — if the deployed UI gates
   `/api/ask` behind auth, set a service token the flow sends as
   `Authorization: Bearer …`.
4. **Slack app** — add slash command `/onboard` pointing at Ark's Slack webhook
   (`https://<ark-host>/api/webhooks/slack`). Coordinate with whoever administers
   ArkBot.

## 1. Register the Ark workspace

From a machine with Ark MCP or CLI access:

```bash
# Validate first (optional via MCP workspace op=validate)
ark workspace apply deploy/slack/workspace.yaml --legacy
```

Or via MCP: `workspace(op='create', definition=<workspace.yaml contents>, namespace='modeltest')`.

Update `env.ONBOARDING_BOT_URL` / `ONBOARDING_BOT_PREFIX` when the backend moves
(Foundry ingress, stable VM, etc.).

## 2. Register the flow

```bash
# Via MCP:
flow(op='validate', definition=<flow-onboarding-bot-slack.yaml>)
flow(op='create', definition=<...>, namespace='modeltest')
```

Stored name will be `modeltest-onboarding-bot-slack` (namespace prefix).

## 3. Register the trigger (platform repo)

Triggers are **not** created via MCP — copy
`deploy/slack/trigger-slack-onboarding-slash.yaml` into the Ark control plane
`triggers/` catalog and reload. Set `enabled: true`.

For channel mentions, fill in `channel_id` in
`trigger-slack-onboarding-mention.yaml` and enable it.

**Important:** point the trigger's `flow` and `workspace` at the namespaced slug
(e.g. `modeltest-onboarding-bot-slack`) if your tenant uses namespace prefixes.

## 4. Smoke test without Slack

Dispatch the flow manually:

```text
session_lifecycle(op='start', {
  flow: 'modeltest-onboarding-bot-slack',
  workspace: 'modeltest-onboarding-bot-slack',
  summary: 'Smoke: how do I enroll a host?',
  inputs: {
    prompt: 'how do I enroll a host?',
    slack_user: 'U_TEST',
    slack_channel: '',
    thread_ts: ''
  }
})
```

Check `answer.md` in session artifacts. If `MONITOR_SLACK_TOKEN` is set and you
pass a real `slack_channel`, a Slack reply should appear too.

## 5. Smoke test from Slack

In Slack:

```text
/onboard how do I set up Cursor?
```

Expect: grounded answer + source links in the thread within ~1–2 minutes.

Verify observability on the bot host:

```bash
tail -1 eval/query_log.jsonl   # should show "channel": "slack"
```

## 6. Ping the bot from the workspace

After workspace materializes on compute:

```bash
# action ping_bot — curls ${ONBOARDING_BOT_URL}${ONBOARDING_BOT_PREFIX}/health
```

Fails if Ark compute cannot reach the backend IP.

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| No Slack reply | `MONITOR_SLACK_TOKEN` unset or missing `chat:write` |
| curl timeout | Bot unreachable from Ark compute; fix network / use Foundry URL |
| 401/303 from bot | Auth on deployed UI; set `ONBOARDING_BOT_ACCESS_TOKEN` |
| Trigger doesn't fire | Trigger YAML not loaded, or `/onboard` not registered on Slack app |
| Wrong flow runs | Verb collision — ensure `match.verb: onboard` is unique |

## API note

`POST /api/ask` accepts optional `"channel": "slack"` so query logs distinguish
Slack traffic from web/CLI. Default remains `"web"`.
