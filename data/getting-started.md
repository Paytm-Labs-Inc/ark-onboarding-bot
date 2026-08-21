Source: https://foundry.mypaytm.com/onboarding/

# Onboarding 
Foundry runs coding agents on your team's repositories. You describe a piece of work, Foundry runs it through a defined process on a machine your team owns, and you get a pull request back. Many of those runs happen at once, on real hardware, rather than one at a time on your laptop.
This section gets you from nothing to your first run. You will need an account, your editor connected, and a few credentials so the work is done as you and not as a shared account.
It takes about an hour of your own time. The one thing you cannot do yourself is compute, which your DevOps sets up once for the whole team, so start that conversation early.
## Start here 
Four steps stand between a new account and a session that runs. Do them in order. Each page ends every step with what you should see, so you never have to guess whether the last one worked.
#| Do this| Who| Roughly| You are done when  
---|---|---|---|---  
1| [Get access and connect](/onboarding/getting-access)| you| 15 min| `ark auth whoami` prints your email, your role, and the tenant you expected  
2| [Set your personal credentials](/onboarding/secrets)| you| 20 min| `ssh -i ~/.ssh/ark_bitbucket -T git@bitbucket.org` authenticates  
3| [Run your first session](/onboarding/getting-started)| you| 20 min| a session id comes back and the run reaches its last stage  
4| [Link the Slack bot](/onboarding/slack-bot)| you| 5 min| the bot confirms your accounts are linked  
If step 3 has nowhere to run, your team has no compute yet. That is [team infrastructure](/onboarding/team-infra) and [creating compute](/onboarding/compute), both one-time and both done by your DevOps rather than by you. It is worth starting that conversation on day one, because it is the long pole.
Then, as you need them: [Set up Cursor](/onboarding/cursor) to drive Foundry from Cursor instead of Claude Code, [Updating the CLI and plugin](/onboarding/updating-cli-plugin) when a release lands, [Registering a machine you already own](/onboarding/registering-compute), and the [Tenant admin guide](/onboarding/admin) if you run a team.
Keep this one open
[When something breaks](/onboarding/troubleshooting) is indexed by the exact error strings Foundry produces. Search it for the text you were given rather than reading it. Every step in the pages above links into it at the point where that step tends to fail.
## The words a run uses 
These come up the moment you read a session that went wrong, and they mean something specific.
  - A **stage** is one step of a flow. It is either an **agent** (a worker with a model and a prompt) or an **action** (a named command the workspace defines, such as `run_unit_tests`).
  - An **artifact** is a file a stage writes for later stages to read, under `$WORKSPACE_ROOT/.ark/artifacts/`. A stage declares what it `produces:` and what it `consumes:`. A stage that consumes an artifact its predecessor never wrote fails, and the fault is in the stage that did not write it.
  - **Green and red** are a stage's verdict. Green passes work forward. Red can route work **backward** along an `on_red:` edge, so a reviewer sends a change back to be redone.
  - **`max_iterations`** caps how many times a back-edge may fire before the run escalates instead of looping. Any stage with a back-edge pointing at it wants one, and a stage without one is the usual cause of a run that burns its budget.
  - A **gate** is where a human can enter. `gate: auto` never stops. `gate: manual` parks the run and DMs you on Slack, and `ark session approve` or `ark session reject` moves it. Give a reject a reason: it is the rework instruction the agent reads next.
  - **Autonomy** is how much the run may do: `full`, `execute`, `edit` or `read-only`. It is set at dispatch or per stage.
  - A **runtime** is the harness the agent runs under. Selecting one does not change what the workspace requires: a workspace still demands every secret it declares.

## Roles, and how a team comes online 
Onboarding a team comes down to one person. The platform team creates the accounts and gives one person a tenant-admin grant. From there that admin unlocks the rest for the team: they onboard everyone else, register the team's compute, and set the team's secrets, without the platform team in the loop for each step.
Ark has four roles:
  - **Member** is the everyday role. A member creates flows, agents, and workspaces, runs sessions, uses shared secrets, and edits or deletes what they created. Most people are members.
  - **Team-admin** runs one team. It does everything a member can, and manages its own team's shared resources and secrets. Its authority covers that team and its sub-teams, and nothing outside them.
  - **Tenant-admin** runs the whole tenant: onboard anyone, manage every team, register compute, and set tenant-wide secrets.
  - **Viewer** is read-only.

A tenant-admin hands out these roles with grants:
bash
    
    ark grant <email> --role member                          # everyday access
    ark grant <email> --role team-admin --scope team:<slug>  # run one team
The [Tenant admin guide](/onboarding/admin) covers users, teams, and team secrets in full.
Two limits:
  - **Your role decides where a registered machine lands, not whether you may register one.** A member can enrol a box onto their own rung, where only they and their admins can use it. Putting a box on a **team's** rung, so the team can share it, is the part that needs an admin. A viewer cannot enrol at all. See [Registering compute](/onboarding/registering-compute) for the rung table.
  - **A team-admin cannot reach outside its own team.** It cannot touch another team's people or resources, and tenant-wide secrets stay with the tenant-admin.

**The shape of it (a superset):** Compute -> Workspace -> Flows -> Agents
  - **Compute** is a provisioned machine (memory and storage) where sessions run.
  - **Workspace** is a full environment: repos, branches, dependencies, services, declared in YAML.
  - **Flows** are sequences of agents with optional human-intervention gates.
  - **Loops** are scheduled re-runs of flows with feedback cycles.
  - **The Slack bot** handles approvals, manual gates, and session monitoring.
  - Two flow kinds: **built-in** (platform defaults) and **user-defined** (team-owned, custom prompts).

## What you get 
  - Parallel task execution: each session runs in a fresh, isolated environment (its own database, services, and Docker).
  - No local-machine bottleneck of one environment and one task at a time.
  - Feedback loops: reject at any stage and the agent reworks and retries (the retry cap is configurable, e.g. up to 8 before auto-reject).
  - **Artifacts** : inspect a flow's output at any stage while the session is still running.
  - **Manual gates** : approve or reject before a PR is raised, straight from Slack.
  - **Actions** : deterministic per-repo scripts (lint, test, build) so agents get consistent, typed results.
  - **Workspace hooks** : post-provision, post-clone, and post-services steps.

## Contacts 
  - **Your tenant's admins are listed in the product.** Open **Settings** and read the **Your admins** card, which names the tenant admins and the admins of every team you belong to. Ask one of them first: most access, grant and secret questions are theirs to answer and do not need the platform team.
  - **Passwords and invites** : your team admin, then `cnoc@paytm.com` if they cannot help.
  - **Infra and peering** : the platform team.
  - **Anything broken** : send the exact command and its exact output, plus the session id. A session id lets whoever picks it up read the event trail directly. A description of the problem costs a round trip that a paste would have saved.
