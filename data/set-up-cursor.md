Source: https://foundry.mypaytm.com/onboarding/cursor

# Set up Cursor 
Cursor shows up in Foundry in two unrelated ways, and this page covers both.
The first is Cursor as your editor: it connects to the control plane's MCP server and gets the `ark` toolbox, so you can dispatch sessions, inspect flows and steer runs without leaving Cursor. The second is Cursor as a **runtime** , one of the harnesses Foundry dispatches agents onto, alongside the `claude-agent` and `pi-dev` families and their `-pi-inference` gateway variants. In that mode nobody is sitting in the editor at all: a stage runs on Cursor's agent SDK, on an EC2 box, a registered host or in a Kubernetes pod, and reports back like any other stage.
If you only want to drive Foundry from your editor, read to the end of Use it and then skip to Notes and gotchas. If you are running flows on the Cursor harness, the sections from Cursor as an agent harness onward are the ones you need.
## Prerequisites 
  - An Ark account. If you do not have one yet, follow [Getting Access](/onboarding/getting-access) first.
  - A per-user API key. The quickest route is the Onboarding card in the next section, which mints one for you, so skip ahead if you plan to use it. To mint one by hand instead, go to **Settings - > API Keys -> \+ New key**, name it something you will recognise later (for example `cursor-<yourname>`), and copy it immediately. The key is shown once, looks like `ark_t-<hex>_<hex>`, and cannot be recovered. Store it in a password manager, and never paste it into a Slack channel. The [installation guide](/guide/installation#minting-a-key-by-hand) covers key hygiene in full.

You must be on the corp network or VPN for any of this to reach the control plane.
## Add the ark MCP server to Cursor 
The onboarding UI generates the config for you. In the web UI, open the **Onboarding** tab and click **Generate** on the **Connect Claude Code (superpowers)** card. Despite the name, the `.mcp.json` it produces is the one Cursor needs: it mints a fresh key and shows a project-scoped config with that key already embedded. Copy that block, or use the **Download .mcp.json** button. Cursor reads the same `mcpServers` structure, so the block transfers as-is.
Cursor reads MCP config from `.cursor/mcp.json`. Use a global config at `~/.cursor/mcp.json` to make `ark` available in every project, or a project-scoped `.cursor/mcp.json` at the repo root to scope it to one repo. The generated block already carries your key; if you are writing the file by hand instead, use this shape and substitute your own key for `<YOUR_KEY>`:
json
    
    {
      "mcpServers": {
        "ark": {
          "type": "http",
          "url": "https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com/mcp",
          "headers": {
            "Authorization": "Bearer <YOUR_KEY>"
          }
        }
      }
    }
You can also add it through Cursor's own settings: find the MCP servers list (Cursor has moved this pane between releases, and it sits under Tools and Integrations in recent versions) and add an HTTP server named `ark`, with the URL ending in `/mcp` and the `Authorization: Bearer <YOUR_KEY>` header. Either path writes the same `mcpServers.ark` entry.
Two details account for most failures: the URL must be the `https://ark.internal...` host ending in `/mcp` and not localhost, and the key goes in the `Authorization` header exactly as shown.
Use the global config for any repo Foundry will clone.
Put this in `~/.cursor/mcp.json` rather than in a project `.cursor/mcp.json`, if the repo is one Foundry checks out for a session. The cursor runtime refuses to start on a worktree that contains a `.cursor/mcp.json` or `.cursor/hooks.json` at all, tracked or not, because Cursor's SDK would load those project files and attach MCP servers and lifecycle hooks that sit outside the allowlist Foundry enforces. Adding the path to `.gitignore` does not help. When a cursor session will not start has the exact error and the fix.
## Verify the connection 
Open Cursor's MCP servers list again. When `ark` shows as connected with its tools listed (`agent`, `compute`, `flow`, `session_lifecycle`, `workspace`, and more), you are wired up. For an end-to-end check, ask Cursor to "list ark computes" and watch a real answer come back from your tenant.
To test the key itself, independently of Cursor, call `auth/whoami` against the control plane. It is the same identity check `ark login` makes:
bash
    
    curl -s https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com/api/rpc -X POST \
      -H "Authorization: Bearer <YOUR_KEY>" -H 'content-type: application/json' \
      -d '{"jsonrpc":"2.0","id":1,"method":"auth/whoami","params":{}}'
Read the response like this:
  - JSON with your email and a `"role"` of `member`, `admin`, `viewer` or `system-admin` means the key is good.
  - `Unauthorized` means the key is dead or wrong, so mint a new one.
  - `"role":"worker"` means you copied a machine token from the enroll card rather than a user key.
  - No response at all means you are not on the corp network or VPN.

## Use it 
Once `ark` is connected, drive Foundry from Cursor in plain English. Ask Cursor to "list ark flows" to see what you can dispatch, or "start an ark session" to kick off a run. The tools arrive under the server name `ark`, so their identities are `flow`, `session_lifecycle`, `workspace` and the rest. Ask for them by plain name and Cursor calls them for you.
For the full first-run flow, from dispatching your first session to watching it work end to end, follow the [Quickstart](/guide/quickstart). [Using Foundry over MCP](/guide/mcp) covers the whole tool surface once you are connected.
## Cursor as an agent harness 
Everything above is about your editor. This section starts the other half: running Foundry's own agents on Cursor's SDK.
`cursor` is a builtin runtime, declared by `runtimes/cursor.yaml` and registered in every tenant. You can see it in the web UI under **Agents - > Runtimes**:
The same list is one command away, which is how you confirm the runtime is available in your tenant before dispatching anything at it:
bash
    
    ark runtime list
    ark runtime show cursor
Three properties of that declaration shape everything else on this page:
  - **One credential,`CURSOR_API_KEY`.** The runtime declares it, so it comes from your secret rung rather than from a workspace. `ark secrets set CURSOR_API_KEY` prompts for the value and writes it at your own user scope by default. Resolution walks user, then team, then tenant and takes the nearest match, so a personal key shadows a team one. A key already set at team or tenant scope covers everyone under it, so check the web UI's **Secrets** view before minting your own; set a user-scope key when you want your Cursor usage billed to you rather than to the shared account.
  - **No gateway or BYOK variant.** Unlike `claude-agent`, which has a `claude-agent-pi-inference` twin, `cursor` has exactly one form. Cursor's SDK routes all inference through Cursor's hosted backend and exposes no endpoint override, and Cursor's own BYOK is disabled for agent mode. Every prompt, file and tool output in a cursor session goes to Cursor, and billing lands on the key's Cursor account.
  - **`permission_mode: bypassPermissions` and `interactive: false`.** No tool prompts, and no interactive pane. Foundry's compute isolation is the boundary around the agent, not a vendor sandbox.

The runtime is built on the same executor factory as `pi-dev`, so dispatch, secrets, workspace preparation, transcripts, verdicts, usage rows and stage routing all behave as they do on `claude-agent`. What differs is covered in What works differently inside a cursor session.
## Choosing the harness for a session 
No environment variable turns Cursor on, and looking for one is the most common wrong turn. An `IS_CURSOR` env var was the original design and it was rejected deliberately: the ark MCP server is a shared, stateless HTTP endpoint, so a process environment variable is a single global value across every user at once and cannot express one user's choice. Do not confuse it with `ark run-cursor`, which is a real command, the one `arkd` writes into each session's launcher after the harness has already been chosen.
The switch is a runtime named `cursor`, and four things set it. They apply in this order, so a later one overrides an earlier one:
  1. **The agent's own`runtime:` field** is the baseline, set in the agent YAML or in the web UI's agent form. Every shipped agent declares a `claude-agent` runtime, a couple of the security agents declaring its `claude-agent-pi-inference` gateway twin, and none declares `cursor`, so nothing runs on Cursor by accident. Author your own agent with `runtime: cursor` when you want one permanently bound to the harness.
  2. **A stored scoping override** pins a standing preference for a user, a team or a whole tenant, so every dispatch by that principal lands on Cursor without naming it. This is the deterministic setting, and it is admin-only today:
bash
         ark scoping set --scope user --scope-id <user-id> --key runtime --value '"cursor"'
The value is a JSON literal, so the quotes inside the single quotes are required. A value naming a runtime that is not registered fails the dispatch and tells you to fix the row.
  3. **An explicit`--runtime cursor`** on `ark session start`, or the `runtime` parameter on the MCP `session_lifecycle` op=start, wins outright over a stored preference. The control plane records the resolved value as `config.scoping_runtime_hint` and marks the explicit case with `scoping_runtime_hint_explicit`. That distinction matters: an explicit request that cannot be honoured fails loudly, where a stored preference that cannot be honoured is dropped quietly, so an A/B benchmark never silently runs the wrong harness.
  4. **A flow stage's`runtime:` field** overrides everything above it. A stage pin wins over the command-line flag, not the other way round.

**`runtime_locked: true` on the agent** vetoes the preference and the flag alike. An explicit `--runtime` against a locked agent fails the dispatch; a stored preference is discarded without comment.
One rare case sits outside that order: when a credential is exhausted, quota routing can swap the runtime and skip the session-level hint, so a `--runtime` flag is not honoured unconditionally.
Workspaces have no runtime knob at all. A workspace plays its usual role in a cursor session, supplying repos, MCP servers, actions and services.
Two shipped flows are worth reading as the worked examples of the two styles. `cursor-smoke` is the stage-pinned kind, whose whole job is to prove the harness starts (its `description` is elided here):
yaml
    
    name: cursor-smoke
    requires_repo: false
    stages:
      - name: work
        agent: worker
        runtime: cursor
        gate: auto
`sec-review-cursor` is the opposite: a thirteen-stage security review that pins no stage runtime and must be dispatched with `runtime=cursor`, because its per-stage models are Cursor-catalog names that only the cursor runtime can resolve.
From the CLI, either style is one command:
bash
    
    ark session start --flow cursor-smoke --workspace k8s-smoke --compute fndry-k8s
    ark session start --flow sec-review-cursor --workspace <slug> --compute fndry-ec2 --runtime cursor
The web UI has no runtime picker yet.
**New Session** asks for a flow, a workspace, a repository, a compute, a task, an optional ticket and whatever inputs the selected flow declares. There is no runtime field. To run on Cursor from the UI today, pick a flow whose stages pin `runtime: cursor`; the session-level override is available over MCP and the CLI only. A harness dropdown at session start, and a per-user default so dispatching from a coding agent does not mean naming the harness every time, are being built under PAI-42769.
## Choosing the model 
Model selection runs in two layers, and knowing which one you are talking to explains most of the surprises.
The first layer is the ordinary Foundry one. Whatever `model:` an agent declares, or a flow stage pins, or a tenant, team or user scoping override sets, is resolved through Foundry's model catalog at dispatch and handed to the box as `ARK_MODEL`. The second layer belongs to the cursor runtime, which turns that into a Cursor model id: `ARK_CURSOR_MODEL` wins if it is set, otherwise a small table maps the bare aliases `sonnet`, `haiku` and `opus`, otherwise the string passes through to Cursor unchanged, and if nothing at all resolved the runtime falls back to `composer-2.5`.
In practice the catalog has already rewritten the aliases before the runtime's table sees them, so pass-through is what actually happens. A dispatch of `cursor-smoke` on the stock `worker` agent, which declares `model: sonnet` and pins no Cursor model, recorded this in the ledger:
    
    model    claude-sonnet-4-6
    provider cursor
    runtime  cursor
Pass-through is what makes the Cursor catalog usable. `sec-review-cursor` pins stages to `claude-opus-4-8`, `glm-5.2`, `claude-sonnet-4-6` and `kimi-k3`, and every one of them reaches Cursor verbatim: the two Anthropic ids are in Foundry's catalog and resolve to themselves, `glm-5.2` is a catalog alias that resolves to itself, and `kimi-k3` is not in the catalog at all. Pin a Cursor model on a flow stage's `model:` field, or on the agent:
yaml
    
    stages:
      - name: assess-logic
        agent: sec-assess-logic
        model: glm-5.2
        gate: auto
A user, team or tenant scoping override is not a place for a Cursor-only name. Session start validates that value against Foundry's own catalog and rejects anything that is not a registered id or alias, so `kimi-k3` set there fails the dispatch even though the same string works on a stage.
Set `ARK_CURSOR_MODEL` only when you need to force a Cursor id regardless of what the catalog resolved. It overrides everything above it.
Every dollar figure on a cursor run is an estimate.
Cursor bills a pool of requests rather than tokens, so the per-token rates Foundry applies approximate what a run consumed rather than reproducing an invoice. The catalog carries rates for the `composer*` and Anthropic ids; a model it does not recognise, which today includes `kimi-k3`, is priced at the Sonnet default rather than rejected. Token counts are accurate regardless.
## Where cursor sessions run 
A session lands on one of three compute kinds: `ec2`, `registered-host` or `k8s`. The dispatch path is identical for all three, and the launcher's last line is always `exec ark run-cursor`. What differs happens inside that command, which branches on whether `ark` is a compiled binary or a source checkout.
On **EC2 and registered hosts** `ark` is a Bun-compiled binary, so it provisions its own runtime: a pinned Node into `~/.ark/runtime` and `@cursor/sdk` into `~/.ark/cursor-sdk`, then re-execs the launcher under that Node. That costs about a minute the first time a box runs a cursor session and nothing afterwards, until an upgrade bumps one of those pins and the next session provisions again. It needs egress to nodejs.org and the npm registry plus a writable `$HOME`.
On **Kubernetes** the pod runs the published image, where `ark` is a shim over the source tree under Bun. The SDK is imported in-process from the image's own `node_modules`. There is no Node download, no npm reach-out and no first-run cost, and `ARK_NODE_PATH` has no effect. The trade is that the pod hosts the SDK under Bun rather than the Node it is published for, which is why the compiled binary goes to the trouble of spawning a real Node. Kubernetes was never affected by the packaging bug described below, for the same reason.
Pools are listed under **Compute - > Pools**:
Target one by name at dispatch with `-c <name>`, and list what you have with `ark compute list`. How a pool resolves depends on its kind: a k8s pool cuts a fresh pod per session, so a session dispatched to `fndry-k8s` reports a compute like `fndry-k8s-10p3d0vb`, while an EC2 pool picks its least-loaded running member and reuses it, so consecutive sessions often land on the same box.
Nothing on a compute row records whether that box can run Cursor. On a registered host, `ark host doctor` tells you whether that build can resolve a cursor launcher at all, which is the common failure but not the only one. The conclusive check anywhere is to dispatch `cursor-smoke` at the compute and see whether the stage completes.
## Telling a cursor session apart 
The session detail page is the quickest answer. A cursor session carries a harness badge in the header and a `RUNTIME cursor` pair in the meta strip beneath it:
The sessions **list** is not a reliable surface for this. Its rows never carry the harness: the list labels a row from the agent name, and the web UI additionally asks for a trimmed projection that keeps only the keys a row renders. Open the session to be sure.
There is no `runtime` column on the session record either. The harness identity lives in the session's `config` blob under two keys: `launch_executor`, the runtime that is actually running the current stage, and `stage_runtimes`, a map from stage name to runtime. Both read `cursor` on a cursor session. A third key, `scoping_runtime_hint`, records the runtime the session was scoped to, whether that came from a `--runtime` flag or from a user, team or tenant override; its companion `scoping_runtime_hint_explicit` is the one that means a caller asked for it by hand.
From the CLI, neither `ark session list` nor `ark session show` prints the runtime in its human output, so read the field directly:
bash
    
    ark session show <session-id> --json | jq .config.launch_executor
Across the fleet, the ledger is the better instrument, because `cursor` is a value no other runtime writes to either the runtime or the provider column:
bash
    
    ark costs --by runtime    # or --by provider
Do not try to read the harness off the model id. A `claude-agent` row can carry `claude-sonnet-4-6` too, so only the runtime and provider columns separate the two cleanly. In SigNoz, the span attribute is `ark.harness` on the `session` and `stage:<name>` spans.
If you are reading a transcript with no field names in front of you, the tool calls give it away: a cursor session calls lowercase Cursor tools (`shell`, `read`, `grep`, `glob`, `edit`), where `claude-agent` calls `Bash`, `Read` and `Grep`.
## What works differently inside a cursor session 
Most of Foundry works unchanged. The differences are the places where Cursor's SDK has no equivalent of what `claude-agent` offers.
**Skills arrive as text, not as a system prompt.** The SDK's options carry no system-prompt parameter, so an agent's `skills:` are folded into a rules file the runtime writes at `.cursor/rules/ark.mdc` with `alwaysApply: true`. The file is added to the repo-local git exclude, and the runtime refuses to overwrite it if the repo already tracks that path.
**Workspace MCP servers are wired through to the agent** , codegraph included, and workspace actions arrive as tools named `mcp__ark-actions__<action>`. Of the action family only the invoke tools are bridged: `list_actions`, `get_action_result`, `list_action_results` and `module_viability` are absent, as are `ask_user`, `ark-memory` and `ark-services`. The stage-control tools `complete_stage` and `report_error` are present.
**Steering a running cursor session does not reach the agent yet.** A message sent to a live cursor session is stored and reported as delivered, but nothing in the cursor launcher is subscribed to that channel, so the agent never sees it. `ark session interrupt` is explicit about it and returns `interrupt not supported for runtime 'cursor'`. The fix, which delivers steers at stage completion boundaries and degrades an interrupt into a queued steer, is written and in review rather than deployed. Until it lands, treat a dispatched cursor session as fire-and-forget: put the full instruction in the task, and stop and re-dispatch rather than trying to redirect a run in flight. `ark session attach` gives you a log tail rather than a pane, because the runtime declares `interactive: false`.
**Cursor's own hooks are not available.** Foundry's hook contract and a workspace's `hooks:` work as usual, but `claude-agent`'s Stop-hook completion gate and stall watchdog have no cursor equivalent.
**Inline subagents run wider than they are declared.** An agent's `agents:` map is passed through to Cursor, but Cursor's subagent shape carries no tool list and no turn limit, so a subagent declared read-only or shell-less runs with the session's full tool surface. The restriction survives only as text in the subagent's prompt. Every dropped restriction is warned once per subagent in the session log, so check there before assuming a narrowing held.
**Observability stops at the session boundary.** Foundry records turns, tool calls, token classes, verdicts, cost and stage spans. What Cursor does not expose, and therefore what no Foundry view can show you, is per-request timing, a caller-supplied run id to join on, and traces. Attribution of a Cursor conversation to a Foundry session is time-and-user matching, not an exact join.
## Porting a flow from claude-agent to cursor 
A flow that runs green on `claude-agent` will not necessarily run green on Cursor, and the reason is worth understanding before you spend a day on it.
The two harnesses were put side by side on one real product ticket, running the same `ark-feature` flow, the same workspace and the same `fndry-k8s` pool. The `claude-agent` baseline completed all nine stages and raised [#1776](https://github.com/Paytm-Labs-Inc/foundry-platform/pull/1776). Cursor took several attempts to reach the same finish line and raised [#1798](https://github.com/Paytm-Labs-Inc/foundry-platform/pull/1798), later repeated on an EC2 pool as [#1861](https://github.com/Paytm-Labs-Inc/foundry-platform/pull/1861) and on a registered-host Mac as [#1925](https://github.com/Paytm-Labs-Inc/foundry-platform/pull/1925). The comparison and its root cause are recorded in PAI-42775.
Model quality was not the difference. The cursor `plan` stage that kept failing ran `claude-opus-4-8`, while the `claude-agent` `plan` stage that succeeded ran Sonnet.
What separated them was the stage boundary. The failing runs died in three shapes, all the same underlying event:
    
    Agent exited without committing any changes
    stage 'triage' did not produce required artifact: triage.md
    Stage 'plan' consumes missing artifacts: exploration.md
In each case the agent announced the work and then stopped before doing it. One transcript ends on the line "Let me explore the codebase to understand the current state of harness-related code", and that was the last thing the session ever said.
`claude-agent` absorbs this because it can refuse the model's own end of turn. A Stop hook blocks the SDK from stopping while neither `complete_stage` nor `report_error` has been called, and feeds the reason back as a user message, so an "I will now explore the codebase" turn never becomes a stage boundary. Cursor's SDK exposes no equivalent veto, so nothing intercepts a premature ending. The flow's `produces:` and `consumes:` gates do catch it, but only after the stage is over, which turns a recoverable moment into a failed stage.
A second difference decides where the fix has to go. Cursor's SDK takes no system-prompt parameter, so an agent's role reaches the session as an always-apply rules file rather than as a system prompt. Putting the same instruction in the session task text instead was tried, and it did not survive the run. The agent definition is the only reliable place for it.
The fix was prompt engineering. A stage exit contract was appended to all eight `ark-feature` agents under PAI-42813, and the first run carrying it went from triage to a pull request, two rework loops included. The wording that shipped is worth copying into your own agents:
    
    ## Stage exit contract (all harnesses)
    
    Before you finish this stage you MUST do BOTH, in order, no matter how
    small the task seems:
    
    1. Write your full output to $WORKSPACE_ROOT/.ark/artifacts/<name>
       (create directories if needed).
    2. Signal the outcome with exactly one mcp__ark-stage-control tool
       call, using the tool your instructions above prescribe for the
       verdict you reached: report_error for any verdict your instructions
       route as red (full pipeline, rework, review reject, failure),
       complete_stage otherwise. This contract changes WHEN you signal,
       never WHICH signal you send; if your instructions map a verdict to
       report_error, calling complete_stage instead misroutes the flow.
    
    Never end the session silently.
That last sentence carries the expensive lesson. A first draft told every agent to call `complete_stage` when it finished, which read as sound advice and quietly broke the pipeline: the triage stage signals `report_error` to mean "this needs the full pipeline", so forcing `complete_stage` routed every ticket straight to implementation. A stage exit contract must change **when** an agent signals, never **which** signal it sends.
That contract has shipped, so do not assume a missing artifact today means the same thing it meant then. The `produces:` gate is harness-neutral, and measured across the security-review flows the rate of artifact misses is close between the two harnesses. What differs is how far a run gets before it dies: cursor failures cluster overwhelmingly on the first stage, where a launch problem stops the run before any agent works, and the artifact gate then reports the symptom rather than the cause. Cursor handles multi-stage artifact handoff correctly when it starts cleanly, including full nine-stage `ark-feature` runs with rework loops.
So, before you point an existing flow at Cursor:
  - Name the artifact path explicitly in the agent prompt, as `$WORKSPACE_ROOT/.ark/artifacts/<name>`, rather than assuming the agent infers it from the flow's `produces:`.
  - Put the exit contract in the **agent definition** , not in the session task text, which does not survive the run.
  - Restate it for every stage agent, and check it against that stage's own routing convention before you paste it.
  - Put the full instruction in the task up front, because you cannot steer a cursor session once it is running.
  - Give the stage a realistic memory budget. Cursor stages have been killed at 512Mi, and the run reports a missing artifact rather than the kill.
  - Expect the first port to need iterations, and read the stage error rather than the agent's own summary when one fails: an agent that stopped early will still report success.

## When a cursor session will not start 
The failure that has cost the most time is a crash at module load, before the model is ever invoked, with `claude-agent` sessions on the same box unaffected:
    
    ResolveMessage: Cannot find module './986.js' from '/$bunfs/root/ark-linux-x64'
The trailing name is whichever binary is running, so an arm64 box reports `ark-linux-arm64`.
That is a packaging bug in older `ark` binaries, not a Cursor fault. `@cursor/sdk` loads its chunks through a computed import that `bun build --compile` cannot see, so a compiled binary embeds the entry file and none of the numbered chunks. `ark run-cursor` is supposed to hand the session to a real Node child and avoid the problem, but older builds decided whether they were a developer checkout by looking for a directory that only the release tarball has. Every bare-binary install therefore took the in-process branch and died. Kubernetes never hit it.
The version string does not tell you whether a box is fixed, because the fix shipped without a version bump. `ark host doctor` is the verdict:
bash
    
    ark host doctor
A fixed build prints a `cursor` line: `launcher available (embedded)` on a healthy release, or a warning about a missing launcher on one compiled without it. A build that predates the fix prints no `cursor` line at all, because that check shipped with the fix, so the presence of the line is the verdict.
The repair depends on the compute kind:
  - **Kubernetes:** nothing to do. Redeploy only if the pod image itself is out of date.
  - **EC2:** run `ark compute provision <name>` to re-run the bootstrap with a current binary.
  - **Registered host:** download the current binary from your own control plane at `/cli/ark-<os>-<arch>`, where the parts are `darwin` or `linux` and `arm64` or `x64`, move it into place, and make sure the name `ark` resolves to it. Use `mv` rather than downloading over the running file, which fails with `ETXTBSY` while `arkd` is executing it. Both halves matter: leaving nothing named `ark` on the launcher's `PATH` turns the failure into `exit 127, ark: not found`. Restarting `arkd` is not required, because it runs `ark run-cursor` as a fresh process per session.

Several environment overrides exist for the unusual cases, all read by the runtime's bootstrap. `ARK_CURSOR_LAUNCH_SCRIPT` points at a launcher you built yourself and wins over both the embedded copy and any sibling file. `ARK_CURSOR_SDK_DIR` moves the SDK install away from `~/.ark/cursor-sdk`, and `ARK_RUNTIME_DIR` moves the provisioned Node away from `~/.ark/runtime`. `ARK_NODE_PATH` nominates a Node you have vetted instead of the pinned one, and `ARK_NODE_AUTO_INSTALL=0` refuses the download outright on a box that must not reach nodejs.org. The bootstrap never searches `PATH` for Node on its own.
Other failures worth recognising:
  - **A quota error from Cursor's API** , such as a message about the account's monthly usage limit, means the launcher worked and the Cursor account is out of quota. The packaging bug fails much earlier, at module load, with zero turns.
  - **A missing`CURSOR_API_KEY`** does not stop the dispatch. The session starts, the launch event records its auth as missing, and the stage then fails at launch with `cursor: CURSOR_API_KEY is not set and ARK_CURSOR_STUB is unset` written into the transcript as an error turn. Check the web UI's **Secrets** view for a key on your user, team or tenant rung before blaming the harness.
  - **`is not a valid model`** or a similar rejection comes from Cursor, not from Foundry, because an unrecognised model id passes through rather than being guessed at. Cursor's error enumerates the ids it does accept, which is the quickest way to find the current catalog.
  - **`contains .cursor/mcp.json`** or **`contains .cursor/hooks.json`** is a deliberate refusal, not a bug, and it is the one that bites real security-review runs. Cursor's SDK loads project-level MCP and hooks config, which would attach servers and commands outside the allowlist Foundry enforces, so the runtime stops rather than silently merging them. The check is for the file being present in the worktree, so ignoring it in git does not help; remove it from the repo, or run that stage on a different runtime. This is why the MCP setup above recommends the global `~/.cursor/mcp.json` for any repo Foundry clones.
  - **`did not produce required artifact`** is usually not the real fault. That gate is harness-neutral and fires last, so on cursor it tends to overwrite the actual cause as the session's headline error. Open the session events and read the one before it: in a sample of these on cursor, most were the `.cursor/` config refusal below, and the rest were `exited with code 137` (the stage ran out of memory on an undersized budget) or `code 127` (no `ark` on that host). Only when nothing precedes it is this the exit-contract problem described in Porting a flow from claude-agent to cursor.

## The one-click bridge for older hosts 
There is an installer that puts a small Node `@cursor/sdk` bridge on a Mac and wraps `ark` so that only `run-cursor` routes to it. It exists for one narrow case: an enrolled Mac whose `ark` binary predates the packaging fix and which you cannot upgrade yet.
Prefer installing a current binary. The bridge shadows the real one with its own SDK copy and its own frozen launcher, and that copy has already drifted: it does not pass inline subagent declarations through, so an agent that declares them loses them silently. Once `ark host doctor` reports the cursor line, retire the bridge with the revert command below.
If you still need it, download it to a file rather than piping it into a shell, so you can pin and read exactly what you run:
bash
    
    curl -fsSL https://foundry.mypaytm.com/ark-cursor-bridge/install-ark-cursor-bridge.sh -o install-ark-cursor-bridge.sh
    bash install-ark-cursor-bridge.sh
Check before you install it
On a current `ark` binary the bridge is **obsolete** : `run-cursor` provisions the SDK itself. Installing the bridge replaces that native path with the old wrapper, so it can undo a fix the host already has. Run `ark run-cursor` first, and only install the bridge if it fails with the `./986.js` error.
It needs a Mac already enrolled as a [registered host](/onboarding/registering-compute), so that `~/.ark/bin/ark` exists, plus Node and `npm` on your `PATH`. You do not need a local Cursor API key: Foundry injects `CURSOR_API_KEY` into each session. The script is idempotent, so re-run it after any upgrade that resets the wrapper. Its canonical copy lives at `scripts/install-ark-cursor-bridge.sh`, and the `served-script-sync` fitness rule reports a violation if the published copy drifts from it.
The installer fetches the bridge and the SDK, exports your macOS keychain CAs to a `corp-ca.pem` so Node stops failing with `UNABLE_TO_GET_ISSUER_CERT_LOCALLY` behind corporate TLS interception, renames `~/.ark/bin/ark` to `ark-real` and installs the wrapper, then restarts your `arkd` LaunchAgent and self-tests. Check it with:
bash
    
    ark --version                   # still prints the real binary's version
    ark run-cursor 2>&1 | head -1   # prints the "[cursor-bridge] using Node @cursor/sdk ..." banner
To remove it:
bash
    
    mv ~/.ark/bin/ark-real ~/.ark/bin/ark && rm -rf ~/.ark/cursor-bridge
## Notes and gotchas 
  - **Key hygiene.** The key in `mcp.json` grants your access. Keep it out of shared repos and screenshares, and prefer a password manager over pasting it around. Each user may hold up to ten live keys at a time; revoke one before minting the eleventh.
  - **VPN required.** The control plane is reachable only from the corp network or VPN. "No response at all" from the checks above almost always means you are off the VPN.
  - **Worker token vs user key.** A key that reports `"role":"worker"` is a machine token minted for an `arkd` daemon, not a user key. Mint a fresh user key under **Settings - > API Keys** instead.
  - **Two different credentials.** Your `ark_t-...` key authenticates you to Foundry. `CURSOR_API_KEY` is Cursor's own credential, used only by the cursor runtime, and it is what Cursor bills. They are never interchangeable.
  - **Getting help.** If a good key still will not connect, send a tenant admin the exact output of the `auth/whoami` check plus a screenshot of the Cursor MCP settings. The full triage lives in [Getting Access](/onboarding/getting-access).
