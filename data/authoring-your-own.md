Source: https://foundry.mypaytm.com/onboarding/authoring-your-own

# Bring your own agents and flows 
Once you have run a session with a built-in flow, you can register your own. This page is for that point, not for your first day: it assumes you are comfortable dispatching a session and now want your team's own process encoded.
If you have not run a session yet, go to [Run your first session](/onboarding/getting-started) first. Nothing here is needed to get started.
## What your agents and workflows map to 
Three primitives make up a run, and your pre-made pieces map onto them:
  - **Agents** are agent definitions -- one worker with a model, a tool allow-list, a system prompt, and optional in-session subagents. Your "agents" become Ark agent definitions.
  - **Workflows** are flows -- a DAG of stages, where each stage is either an `agent` (a worker) or an `action` (a named workspace command). Stages hand typed artifacts to each other with `consumes:` / `produces:`, and `on_red:` back-edges route work backward for rework. Your "workflow" becomes an Ark flow.
  - **Workspaces** are repos plus tools, secrets, and named actions, materialised per session. A flow runs against a workspace on a compute, so the workspace is where your repo and your test/build/lint actions live.

Set expectations up front: a flow does not run in a vacuum. It runs against a **workspace** (your code and tools) on a **compute** (where the work executes). You register the flow and its agents once; you pick the workspace and compute at dispatch time.
Names are kebab-case and namespaced by team or product line -- existing families include `pml-*` (Paytm Money Android) and `inference-*`. (`ark-*`, `paytm-*`, and `default-*` are system/tenant-reserved -- you can reference agents that live in them but you cannot create there, so pick your own family.) Prefix your flow and agent names the same way (for example `myteam-review`) so they are easy to find in `flow list` and do not collide with anyone else's. Prefix by hand **or** pass `--namespace myteam` with an unprefixed name -- not both, or you get `myteam-myteam-review`.
## Register your own 
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
## Use them over MCP 
From Claude Code or Cursor with the MCP connected, discover what you registered, then dispatch it.
**Step 1 - Discover.** Ask in natural language ("list my ark flows", "show the myteam-review flow", "list ark agents"), which calls the `flow` and `agent` MCP tools. The explicit calls are `flow(op='list')`, `flow(op='show', name='myteam-review')`, and `agent(op='list')`.
**Step 2 - Dispatch.** Reference your flow, your workspace, and a compute. In natural language: "start my myteam-review flow on the myteam-workspace workspace". The explicit call is:
    
    session_lifecycle(op='start', summary='review the auth refactor',
      flow='myteam-review', workspace='myteam-workspace', compute='<your-compute>')
Only `summary` and `flow` are required; `workspace` and `compute` are what pin the run to your repo and where it executes (list computes with "list ark computes"). Add `ticket='PAI-1234'` to link a Jira issue, or `autonomy='read-only'` for a dry look.
**Step 3 - Watch it.** Ask "read that ark session" or "what is the status of my session" to follow the artifact and action trail as each stage hands off to the next.
## Worked example, end to end 
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
