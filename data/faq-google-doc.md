Source: https://docs.google.com/document/d/1cFO96__cGuADEFvR_ahHcc0ILmYWvIrodjwMguihVbY/edit

﻿Ark — FAQ & Troubleshooting
A living doc, written by engineers who onboarded the first teams. If something here is wrong or out of date, fix it — that's the whole point. Last updated 4 Aug 2026. Sourced from the teams' own onboarding threads (Foundry Slack + WhatsApp) and the platform engineers who answer them day to day. Questions are kept in the voice people actually ask them.
Most of what slows people down on Ark isn't the agent. It's the boring stuff around it: a key in the wrong scope, a laptop that went to sleep, egress that isn't whitelisted. This doc is ordered so you hit the common ones first. Start at Quick Answers. If you're still stuck, the section-by-section part goes deeper, and the last two sections are the real war stories from onboarding — the exact errors people hit and the exact commands that fixed them.
One mental model before you start: the agent runs the inner loop — plan, code, test, review. You own the outer loop — dispatch it, approve the plan, review the PR. Everything below is about getting that inner loop a clean environment to run in.


Quick answers — the 15 we get asked most
Q1. "My session is stuck at Ready / Bootstrap since 10 mins, is there any ongoing issue?" First figure out if you're on your laptop or managed compute. Give it a couple of minutes, then retry — a lot of the old stuck-forever behaviour is gone now and runs fail fast with a real reason. If everyone is seeing it at the same time, it's the platform, not you. Stop dispatching and check Slack.
Q2. "I have enrolled my compute but session is not getting dispatched, please help." The box needs to be healthy, online, and have capacity set. Enrolling isn't enough — if you registered a machine and it's sitting there doing nothing, it probably has no capacity configured (see Compute). Just enrolled? Wait a minute and retry. Still stuck? Run host doctor or ping your team admin.
Q3. "Getting SSH / clone error while preparing workspace. Kindly check." This is the single most common onboarding problem, so don't feel bad — everyone hits it. You need a passphrase-free private SSH key in Ark Secrets, actual repo access on Bitbucket, and the username set to git. Details in Repository Access.
Q4. "It is showing bun not found / command not found." Your compute image is missing dev tools. Re-enroll with the latest image or ask the platform team to update it. This is not something you fix inside the session.
Q5. "Where to add Bitbucket / Jira / Claude credentials?" User scope, not Team scope. If you put your personal creds at team level you overwrite everyone else's — so your teammate's PRs suddenly get authored as you.
Q6. "I added the secret but Ark is not picking it up, is the cache getting expired?" There's a ~10-minute cache on secrets right now (a deliberate temp fix). Add it, wait, retry. If it's still missing after that, then something's actually wrong.
Q7. "Who can onboard the team members? Do I need tenant-admin for that?" Team Admins now can — for their own team. You don't need Tenant Admin for normal onboarding anymore. Host enrollment works on a team-admin key too.
Q8. "Should we go with laptop or managed EC2/EKS?" Managed, and it's not close. Managed teams have far higher success rates. Laptops are where sessions go to die.
Q9. "On my laptop sessions keep failing, why?" Sleep, low disk, missing tools, a VPN/network flip, or general drift. All of it goes away on managed compute.
Q10. "How much disk is needed on the compute?" 100 GB minimum. Every session prepares its own workspace and big repos eat space fast.
Q11. "I am not able to see my compute / flows anywhere." 9 times out of 10 you're in the wrong tenant or team. Check that first.
Q12. "It is saying no compute available." Everything's busy or unhealthy. Wait for a slot or use another managed box.
Q13. "What is the correct onboarding order to follow?" Join team → add secrets → enroll compute → run workspace doctor → first PR Review → Developer→PR → then Jira→PR. Don't skip doctor.
Q14. "Where should I raise this — Slack or WhatsApp?" Foundry Slack for onboarding questions. WhatsApp for genuine "we're blocked right now" escalations only.
Q15. "Before raising a ticket what all should I check?" Right tenant · secrets added · compute healthy · repo access works · enough disk · latest Ark version. Most "bugs" are one of these.


1. Getting started
1.1 "What exactly is Ark?" It's a platform for running supervised AI agents through your software lifecycle, on your own cloud. The agent plans, writes code, runs tests, reviews itself. You dispatch the work, approve the plan, and review the PR at the end. It's not a chatbot and it's not fully autonomous — it's an engineer's tool with a human gate on the parts that matter.
1.2 "What all can we use Ark for?" PR reviews, Developer→PR, turning a Jira ticket into a feature, QA/E2E test generation, security-vuln fixes, and clearing review comments. Start with PR Review — it's the lowest-risk way to build trust in the output.
1.3 "How much time onboarding will take?" Once your compute and workspace are healthy, the first real outcome comes fast — one team went from nothing to a raised PR in about a day. The time goes into environment setup, not the agent. That's genuinely the hard part, which is why most of this doc is about it.
1.4 "Do I need a dedicated machine for this?" No. A shared managed EC2/EKS pool per team beats one box per person. You don't need to babysit hardware.
1.5 "Can I run it on my laptop only?" You can, and we'd gently talk you out of it. Laptops sleep, fill up, and drift. Every one of those is an avoidable failed session.
1.6 "Which OS is supported?" Linux on managed EC2/EKS is the main path. macOS when you need iOS builds with Xcode. Laptops work but are the least reliable option.
1.7 "Will it work for our repo / stack?" Any Git repo Ark can reach with your credentials — Bitbucket, GitHub. Android, iOS, backend, frontend, all of it. iOS just needs a Mac with Xcode.


2. Access & accounts
2.1 "How do I get access to Ark?" Open the Ark link. Can't see it? Email Bhurva or Abhimanyu with your email + team, or have your lead send a CSV for the whole team at once. Then log in from the "Ark" invite email and set a password.
2.2 "Login is not accepting my email — domain not allowed." Some domains (e.g. ocltp) weren't in the allowed list initially and get added on request — ping the platform team. In the meantime, use the "log in via password" option with the credentials shared with you 1-1, enter your number, generate the TOTP with the CLI command you were given, and you're in.
2.3 "Which tenant should I join?" Your org's — OCL, OCIL, PPSL, PML, FNDRY. If you're not sure, ask your team admin. Being in the wrong tenant is the #1 cause of "I can't see anything."
2.4 "I am not able to see my team in the list." Either you're in the wrong tenant, or the team doesn't exist yet. Your Org/Tenant admin creates it.
2.5 "Who can invite the engineers?" Your team admin, for their own team. New teams are created by the Org/Tenant admin, who names a team admin.
2.6 "What's the difference between Member / Team Admin / Tenant Admin?" Member = contributor. Team Admin = manage your own team's people and compute (this is the role most leads want). Tenant Admin = manage the whole tenant, and we're deliberately handing this out sparingly now — only to people who genuinely need to stand up new teams. If you're a TL or EM, ask for team-admin; it lets you manage multiple teams and onboard people without the tenant-wide blast radius.
2.7 "Why I am not able to enroll compute?" You need Team Admin (or Tenant Admin). This changed — it used to force everyone to tenant-admin, which was wrong. Members can enroll a machine for themselves with a member key; team-admins can enroll for the team. Ask for the grant you need.
2.8 "I got the new role but still getting permission denied." Grants are additive and cached. Log out and back in. And if you minted an API key before the grant landed, mint a fresh one — the old key carries the old permissions.


3. Compute
3.1 "Laptop or managed — what do you suggest?" Managed EC2/EKS. Saying it again because it's the highest-leverage decision you'll make.
3.2 "What EC2 size should I take?" m6i.2xlarge (8 vCPU / 32 GB) is a solid default. Scale by real concurrency, not headcount.
3.3 "How many sessions can one machine run?" Roughly cores ÷ 2 — capacity is derived with headroom. An 8-core box runs about 4 sessions. Exactly how much a session needs depends on the workflow and how many services it has to stand up, so treat this as a starting point, not a law.
3.4 "My machine is showing running but it is not taking any session." Enrolling registers the box; it doesn't give it capacity. As a member you may not be able to set capacity through Claude on your own. Get someone with the right role to run:


ark compute update <your-machine> --set capacity.max_sessions=16 --set capacity.memory=16Gi --set capacity.cpu=1600
	

Tune the numbers to the box. Once capacity is set, it'll start picking up work.
3.5 "I onboarded my laptop earlier via admin grant — is that fine?" Please re-register it with a member API key instead. That scopes the machine to you, admins can still see it for reference, and everyone's machines stay cleanly isolated. Cleaning this up saves a lot of "whose box is this?" confusion.
3.6 "My compute is going offline again and again." Machine asleep or unreachable, or arkd (the little agent that runs on the box) isn't running. Wake it, check arkd.
3.7 "Ark is not able to find my compute." Enroll it to the correct team and reconnect if you just added it. The old compute-list cap that hid boxes is fixed — resolve-by-name is the fix if you still see it.
3.8 "It is showing my machine as unhealthy." A health probe failed — missing tool, full disk, or arkd down. Run host doctor; it'll tell you which.
3.9 "How to update the compute / add missing tools?" Re-enroll with the latest image, or run the install script to lay down the standard toolchain. Don't try to hand-patch tools inside a session.
3.10 "How much disk and RAM to keep?" ≥ 100 GB disk (repos re-clone per session), ≥ 16 GB RAM, 32 GB for big monorepos.
3.11 "We want to run parallel sessions on the same shared EC2 — how?" Yes, this works, and it's worth getting right. Two things bite people: capacity has to actually be set (above), and the workspace has to isolate sessions properly — use an SSH key from the workspace root so parallel sessions don't stomp on each other's credentials. If a 14 GB box is only running one session, that was the resource_budget / default-10 GB-memory fallback bug on registered hosts — it's fixed; parallel tasks on the same EC2 work now. Connecting EKS is the cleanest path if you want boxes to spin up and tear down on demand.


4. Sessions
4.1 "Session is stuck at Ready." Secrets/dispatch, mostly fixed. Retry. If a crowd sees it at once, it's the platform — and if it's the SSM rate-limit flavour (lots of sessions queued at "ready", nothing running tenant-wide), stop dispatching new ones and wait for the all-clear. Fail-fast now surfaces the real cause.
4.2 "Session is stuck at Bootstrap." Compute reachability or arkd. Run host doctor.
4.3 "It failed during Workspace Prepare." Repo clone, credentials, or missing tools. Check your SSH key, your secrets, and the compute image.
4.4 "It is showing waiting for compute." No healthy capacity. Wait or move to another box.
4.5 "Session keeps retrying continuously." That old behaviour is gone — it fails fast now. Update and read the actual error; it's telling you something real.
4.6 "Can I resume a session?" Yes.
4.7 "Can I restart from a particular stage?" Yes.
4.8 "One session is failing but a fresh one works — same code." It happens — sometimes it's genuinely session-specific state. Kick off a new one; if the fresh session runs clean, don't spend an hour on the dead one.
4.9 "Where can I see the session logs?" Session view → events / output / transcript, or the CLI stdio/transcript. This is where you debug, not Slack.
4.10 "How to cancel a running session?" Stop it from the UI or CLI.


5. Repository access
This is the section everyone needs. Clone failures are the most common blocker, full stop.
5.1 "Clone is failing." Almost always the SSH key. You need a passphrase-free private key stored in Ark Secrets, real repo access, and Git creds set.
5.2 "SSH authentication failed." Usual suspects: you pasted the public key instead of the private one, the key has a passphrase, or it isn't registered on Bitbucket. Sanity-check with ssh -T git@bitbucket.org before anything else. In the SSH secret, the username is git — not your email, not your handle.
5.3 "How to set my git identity + Bitbucket so PRs come under my name?" Put these at user scope:
* ARK_GIT_AUTHOR_NAME and ARK_GIT_AUTHOR_EMAIL — without these, your commits aren't attributed to you.
* For Bitbucket: generate a new SSH key, register it on Bitbucket, and store the private key as an SSH-type secret in Ark, username git.
5.4 "Bitbucket app-password is showing invalid." It needs Repositories + Pull requests read/write. Username, not email.
5.5 "Repo not found." Access wasn't granted, or you're pointing at the wrong workspace.
5.6 "It checked out the wrong branch." Set the base branch in the workspace/flow.
5.7 "Our repo is big (~1 GB) and it clones every session — can we clone once?" They re-clone per session today because the clone is gated on your credentials — that's a deliberate security call, not an oversight. Disk space shouldn't be the issue; add disk if needed. Clone-once / branch-checkout is in progress.
5.8 "Should I use SSH key or personal access token?" A token is fine for a plain top-level clone. But if your package.json (or equivalent) pulls nested git-dependent packages, a PAT tends to break during install — use an SSH key for those. That's the practical reason we push SSH.


6. Secrets
6.1 "How to add a secret?" Ark UI → Secrets → User scope.
6.2 "User scope vs Team scope — which one?" User = yours, and it overrides the shared default for your sessions (so PRs are authored as you). Team = shared default for the team. Personal creds always go at User scope — putting them at Team scope overwrites your teammates.
6.3 "How does Ark decide which secret to use?" Narrowest wins: user → team → tenant. Secrets are injected per session, scoped to that session's environment, never global. So on a shared box, one person's session secrets don't leak into another's.
6.4 "I added it but Ark is not able to find the secret." There's a ~10-minute cache (temp fix for a propagation gap). Wait it out and retry before assuming it's broken.
6.5 "Which secrets are mandatory?" JIRA_USER_EMAIL, JIRA_API_TOKEN, CONFLUENCE_API_TOKEN + base URLs, Bitbucket username + app-password (or SSH key), and your git author name/email. A Claude token is optional (see next section) and may not be needed in a few weeks. Everything else depends on your flow and MCPs. For Jira/Confluence/Bitbucket MCPs you need the secrets set — you don't need admin; a member key works fine.
6.6 "Can I set secrets from the MCP itself?" Yes — the MCP can set secrets too, so you don't have to click through the UI for each one.
6.7 "Can I rotate the secrets?" Yes. Update the secret; new sessions pick it up.


7. Claude / models
7.1 "It says Claude rate limit reached / fleet auth quota exhausted." You're sharing a quota that got throttled. Fix: use your own Claude token. Run claude setup-token, then add CLAUDE_CODE_OAUTH_TOKEN as a User secret. Your sessions will use your token from then on. Team-level keys and Pi-Inference are the other options. If the error names a reset time (e.g. seven_day), that's when the shared one frees up.
7.2 "It says native cli binary missing for claude (on my Mac)." The image doesn't have the Claude CLI where Ark expects it. Quick unblock: export ARK_CLAUDE_EXECUTABLE_PATH=$(which claude), or drop a claude binary at /.ark/bin/claude. Setup scripts are being updated to include it.
7.3 "Can I use my own Claude account?" Yes — the CLAUDE_CODE_OAUTH_TOKEN user secret above.
7.4 "Can we use Cursor?" Yes. Connect Cursor to Ark via MCP: mint a user API key, add the `ark` HTTP MCP server in `~/.cursor/mcp.json` (or a project `.cursor/mcp.json`), and verify under Cursor Settings → MCP. See Set up Cursor for the full steps.
7.5 "Which models does Ark support?" The Claude harness today, more through the Pi-Inference gateway. The default model is fine to start; cost/quality routing is rolling out.
7.6 "I am creating a model alias but the Create Alias button is not working." Known UI bug (PAI-40393). Two gotchas: (1) picking a Provider Model in the dropdown isn't enough — you have to click the round button just right of the Model dropdown to actually add it as a target; the button looks active before that but silently ignores clicks. (2) For chat aliases, Input and Output price are required — the backend rejects the alias if they're blank. Working order: Alias Name → Input/Output price (USD per 1M tokens) → pick Provider Model → click the round button (target row appears) → Create Alias.


8. Flows
8.1 "Which flow should I start with?" PR Review, then Developer→PR, then Jira→PR / full SDLC. Trust builds as you let it do more.
8.2 "How to create a flow?" A YAML flow of agent + action stages with human gates. Teams author their own.
8.3 "It is saying stage not found." A typo or undefined stage in the flow YAML. Fix the YAML.
8.4 "My workspace actions are getting rejected as unknown action, but the workspace defines them." This one's subtle and worth understanding. The agent's toolset is built from each stage's allowed_actions list. The built-in flows declare allowed_actions on every action stage, so the actions reach the agent. If someone hand-authored a flow and left allowed_actions empty on the stages, the agent gets an empty toolset and dead-ends. Why do people strip them? Because the flow validator only checks allowed_actions against the global action registry — your workspace-scoped actions aren't in it, so it rejects them as "unknown." The fix: declare allowed_actions on each action stage anyway; the workspace resolves them at run time.
8.5 "My flow is not visible." Wrong tenant/team, or it's team-scoped. Team-owned flows with versions are coming.
8.6 "Can I share a flow with another team?" Anything in the shared catalog runs for any team. For a private flow, ask the owning team to share it.
8.7 "We referenced a custom MCP and doctor is saying unknown MCP name." Custom MCPs need to be added to the catalog. Share the MCP details (e.g. the npx config) with the platform team and, if it's something other teams will use too, it gets added to the shared catalog.


9. Workspace
9.1 "Workspace doctor failed — what to do?" It's telling you what's missing — a secret, repo access, or an action. Read the output and fix each line. Don't guess.
9.2 "Workspace not found." Wrong tenant/team, or not created.
9.3 "Some tools / SDK are missing." The image lacks them. Bake them into the image or run the install script — don't patch per session.
9.4 "On my laptop the environment keeps drifting." A laptop symptom. A managed image makes it go away.
9.5 "Unit tests / coverage pass on my local but fail on Ark." First check you have the Ark Claude plugin installed, then ask Claude to validate your unit-test workspace actions and confirm they run the right commands. A couple of real gotchas we hit: a wrong module name (equity_sdk, not equity-sdk) will fail the baseline silently, and on Android-with-Flutter, the workspace's generated gradle.properties sets org.gradle.configureondemand=true, which breaks building an isolated Flutter-dependent module (:flutter:copyFlutterAssetsDebug not found). Fix for that one is workspace-only, no repo change: drop that flag from the workspace and re-run.
9.6 "How to update my workspace?" Edit the workspace YAML and re-run doctor. Honestly, the fastest path for most workspace fixes is to ask Claude to update the workspace — validate actions, disable configure-on-demand, make a flaky MCP optional, switch to a workspace-scoped SSH key. It knows the shape.
9.7 "arkd is stuck as an orphan process, how to kill it?" Kill the process, then unregister the compute in the UI.


10. Best practices (the short version)
* Managed compute (EC2/EKS), not laptops.
* Personal creds at User scope.
* ≥ 100 GB free disk.
* Run Workspace Doctor before your first real run.
* PR Review before Jira→PR.
* Don't leave sessions idling — they hold a capacity slot.


11. For team managers
11.1 "How do I onboard my entire team in one go?" Your team admin invites by email, or send a CSV (names/emails/teams) to Bhurva or Abhimanyu for bulk onboarding.
11.2 "How many people can share one compute pool?" Lots. A box runs ~cores÷2 sessions and not everyone runs at once. Size on real concurrency, not headcount.
11.3 "How many machines do I need for 50 engineers?" Start small — 2–3 managed boxes — and scale on observed concurrency. Not one box per person.
11.4 "Which workflows should we start with?" PR Review → Developer→PR → Jira→PR. Let confidence grow with autonomy.
11.5 "How do I measure adoption / value?" The live control-plane funnel: onboarded → active → runs → PR → merged. The number that actually matters is reviewed, merged PRs per team — not raw session count.
11.6 "How do I reduce onboarding issues for my team?" Move to managed compute, set identity secrets once, nominate a team admin, verify keys before go-live.
11.7 "How do I nominate team admins?" Ask your Org/Tenant admin to grant the team-admin role.
11.8 "What should my engineers learn first?" Add secrets, run doctor, dispatch a PR-review flow, read the session log. That loop teaches most of it.


12. For DevOps / platform
12.1 "What are the enrollment best practices?" Enroll to the right team, bake the standard toolchain into the image, verify with host doctor before go-live.
12.2 "What EC2 sizing do you recommend?" m6i.2xlarge (8 vCPU / 32 GB) baseline; scale by concurrency.
12.3 "EKS or EC2 — which one?" EKS for a managed, autoscaling shared pool; EC2 for a dedicated team box. EKS is nicer if you want dynamic bring-up/teardown per session.
12.4 "Jira / Bitbucket MCP is blocked from Ark — pods are not on VPN. How to fix?" Ark's compute (k8s pods / EC2) is not on your VPN, and outbound doesn't route through Zscaler. So anything IP-protected — Bitbucket, Atlassian/Jira, the inference gateway — needs a fixed egress IP that you get whitelisted. Concretely: put a NAT gateway with fixed public IPs on the compute pool and EC2s, then raise the whitelisting request (the Foundry ↔ Atlassian one is tracked as RMB-3516). Ark talks to Jira over REST once the source IP is allowed — it doesn't need the MCP for that. And VPC peering alone isn't enough: you also need the security group opened on port 443.
12.6 "What is needed for the MLOps → Foundry account migration?" (1) NAT gateway with fixed public IPs, reused across the compute pool and EC2s. (2) Raise the IP-whitelisting Jira (RMB-3516 style) for Bitbucket + Atlassian. (3) K8s compute pool. (4) A couple of EC2 boxes for easy workspace debugging. #1 covers OCL; for PML get the same IPs whitelisted if the Slack bot is in that account.
12.7 "What IAM does the control plane need?" Cross-account STS AssumeRole with an ExternalId, so the control plane can provision/adopt EC2.
12.8 "VPC peering or TGW?" Either works — follow your org standard for control-plane reachability. Remember the SG:443 point above.
12.9 "The whole Ark UI is slow for everyone — is it k8s?" Usually not k8s — it's the RDS disk on the foundry control-plane DB. Watch for BurstBalance hitting zero and IOPS pinned at the cap; the tell is sessions-list requests dying at ~30.5s (the Postgres statement_timeout is 30s). Fix is the volume: gp2 → gp3 gives 3,000 baseline IOPS regardless of size and it's an online change. One call can bundle the storage + class bump:


aws rds modify-db-instance --db-instance-identifier foundry \
  --db-instance-class db.r7g.large --storage-type gp3 \
  --allocated-storage 100 --apply-immediately --profile mlops --region ap-south-1
	

The class change forces one ~1–2 min reboot; running agent sessions survive it (they reconnect, Temporal retries its activities).
12.10 "Users are not able to view/edit secrets and dispatch is backing up." It's Parameter Store (SSM) throttling. Short term: paid tier lifts DescribeParameters from 3 → 10 TPS (already done). But there's no tier above that, and Ark's steady state is ~6 TPS with bursts to 9–10, so this comes back as you grow. Durable fix is on Ark's side: move the enumeration path (DescribeParameters with BeginsWith /ark/<tenant>/) to GetParametersByPath (100 TPS same prefix), and use WithDecryption=false for admin/list views to skip the per-secret KMS decrypt. Tracked as PAI-41913.
12.11 "How do we monitor compute health?" host doctor + CloudWatch (CPU / disk / connections). Slack alarms are being added.
12.12 "How should we manage compute images?" Ship the toolchain (bun, JDK, Node, Android SDK) in the image, not per session.
12.13 "Storage / capacity planning?" ≥ 100 GB per box; ~cores÷2 sessions; size pools for concurrency.
12.14 "How do I add a new tenant to the metrics tooling?" Append one block under contexts: in ~/.ark/config.yaml (2-space indent before -, 4 before each field), with that tenant's admin key:


- name: <tenant-slug>
  server: https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com
  token: ark_t-xxxx_...
  tenant: t-xxxxxxxxxxxx
  role: admin
	

Verify with ./scripts/tenant-metrics/rpc.sh <slug> auth/whoami — it should print the slug with role admin. Then it works everywhere, e.g. make funnel-report TENANTS=<slug>.


13. For security
13.1 "Where are the secrets stored?" Encrypted, resolved narrowest-first (user → team → tenant), injected only into the session env, never global.
13.2 "Does Ark store our source code anywhere?" No. Code is cloned onto your compute inside your network and cleaned up after. It isn't stored centrally.
13.3 "What actually leaves our network?" Only the model API call (Anthropic, or your gateway). With Cursor, inference goes to Cursor's cloud.
13.4 "How is audit logging handled?" Every agent decision, artifact, action and gate is logged against the original ticket — a durable record of what was done and why.
13.5 "Can Ark access production?" Only if you hand it prod credentials. By default it works on repos and CI, not prod. (See the architecture note below for how to give it prod signals without prod access.)
13.6 "How does RBAC work?" Three levels — member, team-admin, tenant-admin — enforced via scoped grants.
13.7 "How are the API keys protected?" Stored hashed, scoped to tenant/role, revocable. Mint identity-bound keys from the web UI, not shared/service keys.


14. Architecture notes (from the field)
14.1 "Can Ark's agent monitor a prod pipeline end to end without prod connectivity?" Yes — and the trick is that the agent doesn't need to reach prod, it needs signals from prod. Keep the network boundary intact and move the data instead. In order of preference:
1. Consume read-only surfaces that already exist. Pipeline state lives in Bitbucket (SaaS — the pipeline-watch stage already works this way). Prod rollout/health is usually already in Grafana/dashboards + deploy notifications. If a human can watch the rollout without a prod VPN, so can the agent — attach that surface as an MCP and it gets typed read-only tools. Already done for mf-nonprod Elasticsearch/Prometheus.
2. Cross-account read-only IAM role. A tightly-scoped role in the prod account (CloudWatch read + Describe* on the relevant services, nothing else), trusted by the compute's instance role with an ExternalId, via STS AssumeRole. Have infra vet it first.
3. Push signals out to a shared place both sides can read. Aim for a reusable pattern, not a one-off.
14.2 "Does Ark understand our whole codebase — CodeGraph vs Graphify?" Today Ark uses CodeGraph to index the repo at the file level — it tells the agent where code is. Graphify goes further: a full knowledge graph of call chains, dependencies and cross-module links, so the agent understands how the code connects. Richer context, better accuracy (validated on the UPI SDLC dev/review agents). It's SHA-smart — it only re-graphs when main's last commit SHA changes — and you can view the graph for debugging. A shared/global graph is planned as a separate scope; indexers (Graphify, gitnexus, others) are under evaluation.
14.3 "Indexing runs every session and burns tokens (hitting the 5-hour limit). Can it be workspace-level?" Agreed, and planned. Move indexing to the workspace: index once, re-index only on a new commit, instead of every session.
14.4 "Is there outbound Slack connector support?" Not a general built-in connector yet — it's on the list. ArkBot already handles approvals and session notifications; general outbound Slack from flows is planned.


15. Known ops issues (being fixed)
Keeping these honest and visible so you don't burn time thinking it's you:
* ark slack link returns 401 on a member key. It should work with a member key; a bug pushed some people to admin. Mint a fresh identity-bound key from the web UI; if it persists, grab someone on a call.
* exec: ark: not found in launcher.sh. The ark binary is missing at /.ark/bin/ark (or it's a dangling symlink). Re-run the enroll script — it pulls a fresh ark into /.ark/bin.
* base-deps install failed — curl-minimal vs curl conflict (dnf). Already fixed in the workspace; re-run.
* Doctor sessions (wstest*) stuck at "ready" when arkd is offline pile up against the max_sessions cap and aren't auto-cleaned — delete them to unblock. Fail-fast + cleanup shipping.
* workspace op=doctor returns a timeout to the caller even though the operation keeps running in the background — looks like a failure, isn't.
* workspace_undoctored blocks smoke sessions but regular work sessions run fine — the hint should say so.
* Compute enrolled + arkd running but missing from the compute list / rejected as "not a registered target" — the old capped/stale compute-list bug; resolve-by-name is the fix.
* dispatchChild is not yet supported / for_each stages not ported to Temporal. Your flow is creating nested invocations, which may not work as-is yet. Flag it to the platform team.


Pre-support checklist — before you raise anything, confirm: ✓ right tenant · ✓ required secrets added · ✓ compute healthy · ✓ repo access works · ✓ ≥ 100 GB disk · ✓ latest Ark version. Most tickets are one of these six.
