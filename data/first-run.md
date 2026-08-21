Source: https://foundry.mypaytm.com/onboarding/getting-started

# Getting Started with Ark 
You have an account and the `ark` MCP connected in Claude Code or Cursor. This page is the first run: it takes you from a connected editor to a session you dispatched, watched, and reviewed, in seven ordered steps. Every step is a small command you can run and read the answer to, so you are never guessing whether the last one worked.
The four nouns everything is built from are a **compute** (where a run happens), a **workspace** (your project made real: repos, tools, secrets, actions), a **flow** (the delivery process written down as stages), and a **session** (one run of a flow on a workspace on a compute). Steps 2 through 4 pick each of the first three; step 5 puts them together into a session.
Most people drive Ark from Claude Code in plain English, so each step below shows the natural-language MCP phrasing alongside the `ark` CLI command. Both hit the same control plane. Use whichever reads better to you.
**Every step ends with what you should see.** If your output does not match, that step did not work, and each one names the likely cause rather than leaving you to guess.
Before you begin
  - An Ark account, and the `ark` MCP connected in your editor. Both come from [Getting Access](/onboarding/getting-access).
  - The `ark` CLI on your PATH, from the same page.
  - Corp network or VPN. Nothing here reaches the control plane without it.
  - About 20 minutes, most of it waiting on step 3.

By the end you will have dispatched a real session, watched it work, and read its result.
## 1\. Confirm the prerequisites 
Two things must already be true before this page helps you: you have an Ark account, and the `ark` MCP is connected in your editor. If either is missing, stop here and go through [Getting Access](/onboarding/getting-access) first, or read the narrative version in [Get access and connect](/guide/installation). Both cover requesting an account and wiring the MCP into Claude Code or Cursor end to end.
Confirm the connection, then confirm **who** you are connected as. The second check is the one that matters: a leftover registration from an earlier setup reports as connected and returns real data while acting as a different identity.
bash
    
    ark auth whoami
**You should see** your own email, and the role and tenant you expect:
    
      User:    you@paytm.com
      Role:    member
      Tenant:  t-fcc4403d28ef (fndry)
      Auth:    api-key: mcp-you
**If the tenant or role is not what you expected** , you have a stale or duplicate registration. See [When something breaks](/onboarding/troubleshooting#credentials-and-keys). If `/mcp` shows `ark · △ connected · tools fetch failed`, the server was reached and your key was rejected: the triage is in [Getting Access](/onboarding/getting-access).
## 2\. Pick a compute 
Compute is the machine your session runs on: one of our own, in our own AWS accounts. For a first run you do not have to create anything. Most teams have a shared or pooled compute you can borrow, so all you need here is the name of one.
List what your tenant can reach:
bash
    
    ark compute list
Over MCP, ask:
> list ark computes
**You should see** a table with at least one row whose `STATUS` is `running`:
    
      NAME                 KIND      COMPUTE           ISOLATION  ARCH   STATUS
      fndry-k8s-4mawzw6o   compute   k8s               direct     -      running
      fndry-k8s            pool      k8s               direct     -      null
Note the name of a `running` row. You will pass it in steps 3 and 5. A `pool` row is a template rather than a single machine, so its `STATUS` is `null` by design and you can still dispatch to it.
The full set of machine types and how sessions are isolated on them is [Compute and isolation](/guide/compute). You do not need any of it to finish this page.
**If the list is empty** , your tenant has no compute you can reach and you need your team's box: see [Creating compute](/onboarding/compute). **If a row says`running` but a later step reports `no arkd worker is registered`**, the row is registered and its daemon is not checking in, which is a different problem: see [When something breaks](/onboarding/troubleshooting#no-worker-is-registered-for-the-compute).
The one rule that bites first-timers: your flow has to land on a compute that can reach the repos it needs to clone. A pooled machine is fine for anything it can reach over the network, which covers most work.
## 3\. Set up a workspace 
A workspace is your project written down once: which repos to clone, which tools to install, which services to run, and which secrets a session needs. It is rebuilt fresh for every session, so an agent starts with a working checkout and a ready toolchain instead of guessing how to build your project.
Start by reading what your tenant already has, rather than authoring one straight away:
bash
    
    ark workspace list
    ark workspace show foundry-platform
Or over MCP:
> list ark workspaces, then show me the foundry-platform workspace
One term to know before your first run: **actions** are named commands a workspace defines, such as `run_unit_tests` or `run_lint`. A stage can call them and read a pass or fail result, which is how a flow gates on your tests rather than on an agent's opinion of them.
Before you spend an agent on a workspace, prove it materialises. This is the step most people skip and the one that saves the afternoon:
bash
    
    ark workspace test foundry-platform --compute <compute-name>
**You should see** each phase tick past, ending in `PASS`. This takes a minute or two, most of it the clone:
    
    Testing workspace 'foundry-platform' on compute 'fndry-k8s' (session s-q6axx86liw)
    • prepare started
    • secrets placed (0.1s)
    ✓ tool present base-deps / bun / node
    ✓ tool installed gh (1.7s)
    ✓ repo cloned foundry-platform (20.0s)
    ✓ hook install-deps (1.8s)
    ✓ prepare completed
    PASS -- workspace 'foundry-platform' prepared cleanly on 'fndry-k8s'
`ark workspace test` runs the real provision, clone, and services phases with no agent in the loop, which makes it the cheapest way to find a broken workspace. Add `--keep` to leave the prepared tree up so you can inspect exactly what the agent would see. Full authoring in [Workspaces](/guide/workspaces).
**If it fails at`repo cloned`**, that is a credentials problem and it names itself as one. The usual cause is the wrong Bitbucket SSH secret: the suffix on `BITBUCKET_SSH_KEY_<WORKSPACE>` names the Bitbucket workspace, and there is more than one. See [Personal credentials](/onboarding/secrets#bitbucket-ssh-key). **If it fails at`secrets placed`** with a missing secret, the workspace declares something that does not resolve in your scope chain: see [When something breaks](/onboarding/troubleshooting#a-secret-will-not-resolve). **If it refuses with`workspace_undoctored`**, see [the doctor gate](/onboarding/troubleshooting#the-workspace-has-never-been-checked).
## 4\. Choose a flow 
A flow is your delivery process written down as an ordered set of stages. Each stage is either an agent doing work or one of your workspace's commands being run, and each one hands its output to the next. A reviewing stage can send work backwards to be redone, which is how a plan gets scrutinised before any code is written and how the tests get re-run before a pull request opens.
List what is registered and read the wiring of the one you plan to run:
bash
    
    ark flow list
    ark flow show ark-feature
Or over MCP:
> list ark flows, then show me the ark-feature flow
**You should see** `ark flow show` print the real stage wiring, numbered, with each stage's agent and its back-edges:
    
      1. triage         [agent:ark-triager] gate=auto
           on_red -> explore
           on_green -> implement
      2. explore        [agent:ark-explorer] gate=auto
      3. plan           [agent:ark-planner] gate=auto
           max_iterations: 3 on_exceeded -> escalate
      4. plan-review    [agent:ark-plan-reviewer] gate=auto
           on_red -> plan
      ...
      9. pr             [action:create_pr] gate=auto
`ark flow list` renders stage names as `[object Object]`
A known defect in the current CLI. The arrow count is right and the names are not. Use `ark flow show <name>` to read wiring; it is unaffected.
The flagship is `ark-feature`, the flow Ark uses on itself. It branches at the front rather than running as a straight line:
    
    triage -> { trivial: implement | full: explore -> plan -> plan-review -> implement }
           -> review -> pr-prep -> pr
`triage` right-sizes the run at the front: a trivial one-line fix skips straight to `implement`, while a real feature takes the full `explore -> plan -> plan-review` path. `plan-review` reads the plan adversarially before a single line is written and routes back to `plan` when it finds a hole; `gate-verify` and `review` both route back to `implement` when the tests or the read do not pass. Each loop carries an iteration cap so a design that will not converge escalates instead of spinning. Read the stages from `ark flow show`, not from this page: it prints the real wiring for the build your tenant is pointed at.
If no registered flow fits, author your own from YAML. Validate first, then create: validation creates nothing, so running it first is what saves you the compute.
bash
    
    ark flow validate ./flows/my-pipeline.yaml --repo .
    ark flow create my-pipeline --from ./flows/my-pipeline.yaml
`ark flow validate` runs the same checks a real dispatch runs, on the structure, the stage wiring and the declared inputs. Pass `--repo` when the flow declares `requires_repo: true`. Full detail in [Flows](/guide/flows).
**If validation reports`routes to unknown stage`**, a back-edge names a stage that is not defined in the flow: see [When something breaks](/onboarding/troubleshooting#a-stage-routes-somewhere-that-does-not-exist).
## 5\. Start your first session 
A session is one run: one flow, one working tree, one transcript, one cost ledger, addressable by id. Starting one resolves the flow, materialises the workspace on the compute, and launches the first stage's agent.
Over MCP, this is a single `session_lifecycle` call, and in Claude Code you can just ask for it in English:
> start an ark session on the ark-feature flow with the foundry-platform workspace on `<compute-name>`, summary "Add a /healthz endpoint that returns build info"
The tool call that assembles into is `session_lifecycle(op='start')` with `{flow, workspace, compute, summary}`, plus the optional `ticket` and `autonomy`. `summary` is required; `ticket` records the Jira key on the session so a flow that triages from Jira can pull the ticket's own context; `autonomy` is one of `full`, `execute`, `edit`, or `read-only`.
The same dispatch from the CLI, which is unambiguous to read:
bash
    
    ark session start FNDRY-1234 \
      --flow ark-feature \
      --workspace foundry-platform \
      --compute <compute-name> \
      --summary "Add a /healthz endpoint that returns build info"
The ticket key is the first positional argument, and it is optional. The flags cover the same fields: `--flow`, `--workspace`, `--compute`, and `--summary`. `autonomy` has no CLI flag: it is set over MCP on the `session_lifecycle` call, or per stage in the flow YAML. The command prints a session id; capture it, because everything in step 6 takes it:
bash
    
    SID=<sessionId>
**You should see** a session id of the form `s-` followed by ten characters, and the session appear in the web UI's Sessions list within a few seconds. That id is the single most useful thing to quote when you ask for help: it lets whoever picks it up read the event trail directly.
**If it refuses with`required input "ticket" missing`**, the flow declares a ticket and you did not pass one. Pass any valid key as the first positional argument. **If it refuses with a missing secret** , dispatch checks every secret the workspace declares, before the agent starts, whichever runtime you chose. Both are covered in [Starting a session](/onboarding/troubleshooting#starting-a-session).
## 6\. Watch it, and approve at the gates 
A session is not a black box. From the CLI, read where it is and tail what the agent is doing:
bash
    
    ark session show $SID       # stage, status, compute, branch, duration, budget, PR link
    ark session events $SID     # the event stream: stage transitions, gate stops, artifacts
    ark session output $SID     # tail the running agent's output
Over MCP the read side is `session_read` and `status`; ask in English:
> read session $SID and show me its status
For the workspace side of the run, the clone and provision trail plus every action result, go through the runtime. Over MCP that is the `workspace` tool's `runtime_events` op; from the CLI:
bash
    
    ark workspace runtime list --session $SID
    ark workspace runtime events <runtime-id>
`ark-feature` runs gate-free end to end: every one of its stages is `gate: auto`, so it does not park for a human, and the run carries itself from `triage` to `pr` on its own. Approve and reject come into play on flows that declare a manual gate (`gate: manual` or `gate: review`) on a stage. There the run parks, Ark bot DMs you on Slack, and exactly two commands move it:
bash
    
    ark session approve $SID
    ark session reject $SID -r "Null-check the request body before parsing"
Give reject a specific reason: it is not a status change, it is the rework instruction the agent reads next. Over MCP, both are the `session_message` tool. `ark-feature` finishes at the `pr` stage, which pushes the branch and opens the pull request; once it lands, `ark session show $SID` prints the PR link, and from there it is an ordinary pull request in an ordinary review queue.
## 7\. Next steps 
**Step 4 of 4:** [Link the Slack bot](/onboarding/slack-bot), so gate approvals and session notifications reach you without watching a terminal. That finishes onboarding.
After that, as you need them:
  - [Bring your own agents and flows](/onboarding/authoring-your-own): when a built-in flow no longer fits your team's process.
  - [Compute and isolation](/guide/compute): the machine types and how sessions are isolated on them.
  - [Secrets](/guide/secrets): the full scope-resolution rules.
  - [Tenant admin guide](/onboarding/admin): users, teams, grants, and team-scoped secrets, if you run a team.
