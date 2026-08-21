Source: https://foundry.mypaytm.com/roadmap/

# Ark Roadmap

Ark is the internal platform that runs agentic engineering workflows in a secure, managed way. It does not replace Jira, Bitbucket, or a team's process. Jira stays where work is created and tracked, Bitbucket stays where code and review live, and Ark runs the agent workflow between them, making the dev loop secure, reliable, self-serve, observable, and measurable.

Live tracker: https://docs.google.com/document/d/1gMR6HLks7LPLeurfMQpJbIyNa7StNDlDdct5jX-klMo/edit

## How work flows

- **Jira ticket:** Work is created and tracked where the team already works.
- **Ark reads the ticket:** Ark pulls ticket context and linked docs, holds the credentials, and a planner agent drafts a plan.
- **Ark runs the work:** Coding, testing, code review and validation run on managed compute.
- **Ark opens a PR:** Ark creates or updates a PR and attaches the test evidence.
- **Ark updates Jira:** Progress, the PR link, the test result and the status go back to the ticket.
- **Deploy and monitor:** Deployment and monitoring agents take the next approved action.

## Product bets

### 0. Make Ark Reliable

**Goal:** Stop the outages before we add anything new.

- Stop 503s: credential-refresh grant for the weekly DB password rotation, plus event-consumer backpressure.
- Redis recovery, and managed EC2 or EKS instead of laptops.
- Team-scoped compute and credentials, with pre-flight prerequisite checks.
- Prevent an arkd disconnect from stranding a run; detect machine loss and recover.
- Do not kill a healthy long build, and surface no silent hangs.
- Pause and resume on a rate limit, and preserve work on every kill.
- Reduce UI polling, keep auth stable under load, and deliver Slack alarms.

**Done when:** No unannounced outage over 5 minutes, failures are fast and clear, arkd holds at normal load, healthy builds keep running, no silent hangs, the UI stays fast, no repeated logouts, no lost work, and alerts fire before users report.

### 1. Easy Repo Onboarding and Workspace Setup

**Goal:** An engineer onboards a repo without asking the Ark team.

- An Onboard Repo button that analyses the repo: language, dependencies, versions, build and test.
- Claude generates a workspace YAML for the engineer to review.
- Detect and import an existing AGENTS.md, skills, agents, MCPs and flows.
- One guided setup, with doctor, dry-run and prepare-without-agent surfaced.
- Clear retryable errors and pre-flight validation.

**Done when:** An engineer onboards a repo and runs the first flow with no Ark-team help.

### 2. Compute Setup

**Goal:** Every session runs on approved, team-scoped compute.

- Compute preference order: Kubernetes, then Ark EC2, then tenant EC2, with managed EC2 as the default.
- Remove personal-machine execution (decision), and finish the RBAC migration to team scope.
- Workspace file-boundary isolation and per-session secret isolation on shared hosts.
- Session-scoped SSH keys, and automated cross-account VPC peering.

**Done when:** Compute and credentials are scoped to the right team, no shared-credential overwrite, no cross-workspace file access, and all sessions run on approved compute.

### 3. Session Dispatch and Lifecycle

**Goal:** Every accepted dispatch runs or fails fast, with a clear reason.

- Fail-fast pre-flight that refuses in seconds when a prerequisite is missing.
- A subprocess-aware watchdog that does not kill a 30 to 40 minute Android build.
- Park and auto-resume on a rate limit, with the reset time shown.
- Clear state, reason, timeout, cancel and retry on every session.
- Detect and clean stranded sessions, and preserve work on every kill.
- Alert within minutes on a park, a stuck run, a disconnect or a dispatch failure.

**Done when:** Every accepted dispatch runs or fails in seconds with a clear reason, healthy builds survive, rate-limited runs resume, stranded sessions are cleaned, and no work is lost.

### 3b. Jira Ticketing and Closed-Loop SDLC

**Goal:** A run starts from Jira and reports back to Jira, with no copy-paste.

- A Jira transition triggers a flow, with webhook support for Jira, Bitbucket, GitHub, PagerDuty, Prometheus and Slack.
- A Jira connection in onboarding, and auto-read ticket context: AC, PRD, design, APIs, dependencies, related tickets and existing PRs.
- Re-read the ticket during a run, and remove the manual ID and branch handoff.
- Jira write-back (comments, labels, status) and auto Jira-to-PR linking, with the Jira key in the PR title and body.
- Message a running agent, with approve and reject gates.
- Route Bitbucket and GitHub review comments back to the agent.
- Fix schedules to attach a workspace and accept ticket inputs; scheduled and event flows, outward actions, Slack and email notifications, and local-to-Ark session handoff.

**Done when:** A run starts from Jira with no copy-paste, Jira shows live progress, the PR links automatically, feedback lands without a restart, and scheduled and event runs work end to end.

### 4. DX Reporting and Adoption Funnel

**Goal:** Every PR and session is counted and attributed to a team.

- Count every PR: built-in action, custom action, git and CLI.
- Fix create_pr for target and stacked branches, and count multiple PRs per session.
- Team and session attribution, and surface anything unattributed.
- A funnel: repo selected, workspace generated, validation passed, first run, successful outcome.
- Measure successful outcomes by team, trust cost attribution, and push clean data to DX.

**Done when:** Every PR and session is counted and attributed, adoption is read by successful outcome, and leadership has one reliable view.

### 5. Cost and Token Reporting

**Goal:** The cost per successful workflow is trusted and visible.

- The model shown is the model that ran; warn or fail on a stage with no declared model.
- Stage and attempt land in the ledger.
- Cost by model, session, flow, stage, attempt, turn, agent, repo and team.
- Split input, output and cache tokens, and expose the review-loop cost.
- Alert on runaway token or retry growth, with cost per successful workflow as the core metric.

**Done when:** The model display is always correct, cost is visible by stage, attempt and turn, and the cost per successful workflow is trusted.

## Eighteen areas, gap to done

### Reliable platform and session recovery

**Gap today:** 503s under load, Redis loss, arkd disconnects and machine loss strand runs.
**What we will build:** Credential-refresh grant, backpressure, Redis recovery, disconnect and machine-loss recovery.
**Done when:** No unannounced outage over 5 minutes and no run lost to a disconnect.

### Managed compute and secure access

**Gap today:** Sessions still run on laptops and share credentials across a host.
**What we will build:** Managed EC2 or EKS default, team-scoped compute, per-session secret and file isolation.
**Done when:** Every session runs on approved compute with credentials scoped to its team.

### Pre-flight checks before dispatch

**Gap today:** A dispatch can accept, then fail minutes later on a missing prerequisite.
**What we will build:** Fail-fast pre-flight that validates prerequisites before the run starts.
**Done when:** A missing prerequisite is refused in seconds with a clear reason.

### Security and exposure

**Gap today:** Open critical and high findings, and no strict session or credential boundary.
**What we will build:** Session-scoped SSH keys, tight secret scope, internal-only exposure until findings close.
**Done when:** No cross-team access, and no external endpoint while critical or high findings are open.

### Repo onboarding and guided setup

**Gap today:** Onboarding a repo needs the Ark team and hand-written YAML.
**What we will build:** Onboard Repo button, repo analysis, generated workspace YAML, one guided setup.
**Done when:** An engineer onboards a repo and runs the first flow without help.

### Import team agents, skills and flows

**Gap today:** Existing AGENTS.md, skills, agents, MCPs and flows are not picked up.
**What we will build:** Detect and import a repo's existing agent assets during onboarding.
**Done when:** A repo's own agents, skills and flows are available on first run.

### Jira start, context and branch setup

**Gap today:** A run needs a copy-pasted ticket ID and branch, and reads no ticket context.
**What we will build:** Jira trigger, auto-read of AC, PRD, design and related PRs, no manual handoff.
**Done when:** A run starts from Jira with the ticket context already loaded.

### Jira write-back and PR linking

**Gap today:** Jira does not see progress, and the PR is not linked to the ticket.
**What we will build:** Write-back of comments, labels and status, and auto Jira-to-PR linking.
**Done when:** Jira shows live progress and the PR links back automatically.

### Agent collaboration and PR review

**Gap today:** You cannot steer a running agent, and review comments do not reach it.
**What we will build:** Message a running agent, approve and reject gates, route review comments back.
**Done when:** Feedback reaches the agent and is acted on without a restart.

### Notifications

**Gap today:** Alarms do not reliably reach Slack, and there is no email path.
**What we will build:** Slack alarm delivery and email notifications on the events that matter.
**Done when:** The right people are alerted before users report a problem.

### Large ephemeral test environments

**Gap today:** Big integration and device test environments are not available on demand.
**What we will build:** Larger ephemeral test environments spun up per session.
**Done when:** A session gets the test environment it needs and releases it after.

### Session sharing across flow stages

**Gap today:** Each stage starts cold and cannot reuse the prior stage's state.
**What we will build:** Session-state sharing so a later stage reuses earlier work.
**Done when:** A stage picks up where the prior stage left off.

### Event-driven workflows and connectors

**Gap today:** Flows run on demand only, with no event or connector entry points.
**What we will build:** Webhooks for Jira, Bitbucket, GitHub, PagerDuty, Prometheus and Slack, plus scheduled flows.
**Done when:** An external event starts the right flow end to end.

### Deployment and production monitoring

**Gap today:** There is no deployment agent and no production monitoring loop.
**What we will build:** Deployment agents, a production VPC, and monitoring connectors.
**Done when:** A deployment or monitoring agent takes the next approved action.

### Model routing and advanced agents

**Gap today:** Every stage runs one model, with no routing or parallel child agents.
**What we will build:** Model routing, adaptive flows and parallel child agents.
**Done when:** A stage runs the right model, and work fans out where it helps.

### PR and session attribution and DX data

**Gap today:** PRs and sessions are undercounted and often unattributed.
**What we will build:** Count every PR and session, attribute to a team, and push clean data to DX.
**Done when:** Every PR and session is counted and attributed.

### Adoption funnel reporting

**Gap today:** There is no funnel from repo selected to a successful outcome.
**What we will build:** A funnel measured by successful outcome per team.
**Done when:** Leadership reads adoption by successful outcome in one view.

### Cost and token reporting

**Gap today:** The model shown can be wrong, and cost is not visible by stage or attempt.
**What we will build:** Correct model display, stage and attempt in the ledger, cost per successful workflow.
**Done when:** Cost per successful workflow is trusted and visible by stage, attempt and turn.

## Decisions (these are decided, not open)

- **Compute:** Managed only. Laptops removed.
- **Local execution:** Revisit later through secure local-to-cloud portability.
- **Harness:** Standardise on Claude Agent. Cursor is secondary, run with the engineer's key.
- **Secrets and access:** Scope everything tightly: AWS secrets, session-scoped SSH, team and session scope, no cross-team access.
- **Infrastructure exposure:** Internal-only until every critical and high security finding closes.
- **Product sequencing:** Reliability before breadth.
- **Measurement:** Measure successful outcomes. Fix attribution first.
- **Roadmap and intake:** Publish the roadmap and run one shared tracker: problem, owner, priority, status, dependency, target and metric.
- **Communication:** Release notes in foundry-users, re-shared in all-engineer, with a weekly summary to Foundry-Project-Mgtm.
- **Operating review:** Weekly review.

## Sequence

### Week of Aug 10, 2026 — Establish the stable baseline

- Managed EC2 and EKS supported, laptops removed, Claude Agent standard.
- Core Bitbucket and Jira connectivity, and AWS secrets.
- Pre-flight checks and full observability.
- Basic workspace setup, session recovery and rate-limit handling.
- Slack alert delivery and a cost-reporting foundation.

### Week of Aug 31, 2026 — Self-serve onboarding

- Onboard Repo, repo analysis and generated workspace YAML.
- Import AGENTS.md, agents, skills, MCPs and flows.
- One guided setup, with doctor, dry-run and prepare-without-agent.
- Clear retry and an onboarding funnel.

### Week of Sep 7, 2026 — Jira-to-PR closed loop

- Jira trigger, auto context read and write-back.
- Auto PR linking and in-run messaging.
- Bitbucket and GitHub review loop.
- Slack and email notifications, and event and scheduled flows.

### After Sep 7 — Depth and scale

- Larger ephemeral test environments and more production and monitoring connectors.
- Deployment agents and a production VPC.
- Session-state sharing and model routing with adaptive flows.
- Parallel child agents and fuller DX reporting with cost optimisation.
- Local-to-cloud portability, and an external endpoint only after security closure.
