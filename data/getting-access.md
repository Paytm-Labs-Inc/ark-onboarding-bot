Source: https://foundry.mypaytm.com/onboarding/getting-access

# Getting Access 
## Get an Ark account 
**Step 1** \- Open <https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com> (you must be on the corp network/VPN). If the page doesn't load, reach out to Bhurva Sharma / Abhimanyu Singh Rathore with your **email ID and team name**. Onboarding a whole team? Share a .csv of everyone's email + team.
**Step 2** \- Check your inbox for an email from "Ark" and log in. If you can't log in even after setting your password, ping Bhurva Sharma.
## Set up the Ark MCP (use Ark from Claude Code) 
**Step 1** \- In the Ark web UI, open the **Onboarding** tab and click **Generate**.
**Step 2** \- Copy the two snippets shown, paste them into Claude Code in your terminal one at a time, and ask it to "Set up and connect with ark mcp".
**Step 3** \- In Claude Code, type `/mcp` and press Enter.
**Step 4** \- If you see **ark · ✔ connected** , you're done.
## Troubleshooting: "ark · △ connected · tools fetch failed" 
This means the MCP reached the server but your API key was rejected. Fix in 4 steps:
**Step 1 - Get your own API key** from the Ark web UI: **Settings → API Keys → Create key** , name it `mcp-<yourname>`. **Copy it immediately** \- it's shown once and looks like `ark_t-…_<hex>`. Store it in a password manager; never paste it in Slack channels. Can't log in at all? You don't have an account yet - see above.
**Step 2 - Verify the key** before touching MCP config:
bash
    
    curl -s https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com/api/rpc -X POST \
      -H "Authorization: Bearer <YOUR_KEY>" -H 'content-type: application/json' \
      -d '{"jsonrpc":"2.0","id":1,"method":"auth/whoami","params":{}}'
  - Good: JSON showing your email and `"role":"member"` or `"admin"`
  - Bad: `Unauthorized` (dead key - remint) or `"role":"worker"` (machine token, not a user key)
  - No response at all: you're not on the corp network/VPN

**Step 3 - Replace the MCP entry:**
bash
    
    claude mcp remove ark
    claude mcp add --transport http ark \
      https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com/mcp \
      --header "Authorization: Bearer <YOUR_KEY>"
Two classic mistakes: the URL must be the `https://ark.internal…` one ending in `/mcp` (not localhost), and the key goes in the `Authorization` header exactly as above.
**Step 4 - Restart Claude Code** (or `/mcp` → reconnect). You should see `ark · ✔ connected` with tools (agent, compute, flow, session_lifecycle, workspace, …). End-to-end test: ask Claude to "list ark computes".
Still failing? Send Bhurva Sharma the exact output of Step 2 plus a screenshot of `/mcp`.
## Onboarding your own agents and workflows 
Once the MCP is connected you can run the built-in flows, but you can also bring your own. This section covers what "your agents and workflows" map to in Ark, how to register them, and how to run them over MCP from Claude Code or Cursor.
### What your agents and workflows map to 
Three primitives make up a run, and your pre-made pieces map onto them:
  - **Agents** are agent definitions -- one worker with a model, a tool allow-list, a system prompt, and optional in-session subagents. Your "agents" become Ark agent definitions.
  - **Workflows** are flows -- a DAG of stages, where each stage is either an `agent` (a worker) or an `action` (a named workspace command). Stages hand typed artifacts to each other with `consumes:` / `produces:`, and `on_red:` back-edges route work backward for rework. Your "workflow" becomes an Ark flow.
  - **Workspaces** are repos plus tools, secrets, and named actions, materialised per session. A flow runs against a workspace on a compute, so the workspace is where your repo and your test/build/lint actions live.

Set expectations up front: a flow does not run in a vacuum. It runs against a **workspace** (your code and tools) on a **compute** (where the work executes). You register the flow and its agents once; you pick the workspace and compute at dispatch time.
Names are kebab-case and namespaced by team or product line -- existing families include `pml-*` (Paytm Money Android) and `inference-*`. (`ark-*`, `paytm-*`, and `default-*` are system/tenant-reserved -- you can reference agents that live in them but you cannot create there, so pick your own family.) Prefix your flow and agent names the same way (for example `myteam-review`) so they are easy to find in `flow list` and do not collide with anyone else's. Prefix by hand **or** pass `--namespace myteam` with an unprefixed name -- not both, or you get `myteam-myteam-review`.
### Register your own 
Do this from a terminal where `ark` is on your PATH. All three commands read from a YAML file.
**Step 1 - Define your agents.** Write each agent as a YAML file. Two keys are required: `name` (kebab-case) and `runtime` (a worker needs one to be dispatchable -- there is no default, and dispatch hard-fails without it). Everything else has a default, but you will normally also set `model`, `system_prompt`, and `tools`. Note the scope: agents register **project-scoped** by default while flows register **global** by default (Step 3), so a global flow cannot see a project-scoped agent at dispatch -- the `--global` below keeps them in the same scope. Register each one:
bash
    
    ark agent create myteam-reviewer --from myteam-reviewer.yaml --no-editor --global
    ark agent list          # confirm it shows up
A minimal agent YAML:
yaml
    
    name: myteam-reviewer
    runtime: claude-agent
    model: sonnet
    system_prompt: |
      Review the diff for correctness and flag any P0/P1 issues.
    tools:
      - Read
      - Grep
      - Bash
**Step 2 - Wire the workspace.** Write a workspace YAML naming the repo your flow runs against, plus any secrets, MCPs, and named actions your stages call (an action surfaces to agents as `mcp__ark-actions__<name>`). Apply it (create-or-update is the same command):
bash
    
    ark workspace apply myteam-workspace.yaml
    ark workspace list
**Step 3 - Register your flow from YAML.** Put your stages in a YAML file with a top-level `stages:` array. Validate it first, then create it:
bash
    
    ark flow validate ./myteam-review.yaml --repo .   # name OR path; --repo since requires_repo: true
    ark flow create myteam-review --from myteam-review.yaml
    ark flow list          # your flow now appears here
`ark flow create <name>` takes the flow name as a positional and reads the `stages:` array from the file you pass to `--from`. `ark flow validate` checks the DAG before you register it; pass `--repo <path>` if your flow declares `requires_repo: true`. A minimal two-stage flow -- your `myteam-reviewer` from Step 1, then the built-in `ark-pr-author` (you reference it, you do not create it) to open the PR:
yaml
    
    name: myteam-review
    description: "Review-only: run my reviewer, then open a PR"
    requires_repo: true
    stages:
      - name: review
        agent: myteam-reviewer
        gate: auto
        produces: [review.md]
        task: |
          Review the diff on branch {{branch}} of {{repo}} and write findings to
          $WORKSPACE_ROOT/.ark/artifacts/review.md.
      - name: pr
        agent: ark-pr-author
        gate: auto
        depends_on: [review]
        consumes: [review.md]
### Use them over MCP 
From Claude Code or Cursor with the MCP connected, discover what you registered, then dispatch it.
**Step 1 - Discover.** Ask in natural language ("list my ark flows", "show the myteam-review flow", "list ark agents"), which calls the `flow` and `agent` MCP tools. The explicit calls are `flow(op='list')`, `flow(op='show', name='myteam-review')`, and `agent(op='list')`.
**Step 2 - Dispatch.** Reference your flow, your workspace, and a compute. In natural language: "start my myteam-review flow on the myteam-workspace workspace". The explicit call is:
    
    session_lifecycle(op='start', summary='review the auth refactor',
      flow='myteam-review', workspace='myteam-workspace', compute='<your-compute>')
Only `summary` and `flow` are required; `workspace` and `compute` are what pin the run to your repo and where it executes (list computes with "list ark computes"). Add `ticket='PAI-1234'` to link a Jira issue, or `autonomy='read-only'` for a dry look.
**Step 3 - Watch it.** Ask "read that ark session" or "what is the status of my session" to follow the artifact and action trail as each stage hands off to the next.
### Worked example, end to end 
Bring a flow YAML you already have, then:
bash
    
    # 1. register the agents your flow references
    ark agent create myteam-reviewer --from myteam-reviewer.yaml --no-editor --global
    
    # 2. wire the workspace (your repo + tools + actions)
    ark workspace apply myteam-workspace.yaml
    
    # 3. validate and register the flow
    ark flow validate ./myteam-review.yaml --repo .
    ark flow create myteam-review --from myteam-review.yaml
Then, from Claude Code with the MCP connected:
> start my myteam-review flow on the myteam-workspace workspace, summary "review the auth refactor"
which dispatches `session_lifecycle(op='start', summary='review the auth refactor', flow='myteam-review', workspace='myteam-workspace', compute='<your-compute>')`. Watch it with "read that ark session".
