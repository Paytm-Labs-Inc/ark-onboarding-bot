Source: https://foundry.mypaytm.com/onboarding/

# Onboarding 
Foundry is a platform for running many agentic sessions in parallel, not a JS-style framework. This section gets you set up: an account, Claude Code connected over MCP, your personal credentials, and (for team leads) compute in your own AWS account.
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
  - **Registering a machine as compute is an admin action.** Enrolling a box mints a credential that lets it join the fleet, so a member or viewer cannot do it and must ask an admin. See [Registering compute](/onboarding/registering-compute) for who can enrol and how.
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

## Onboarding path 
  1. [Getting access + MCP setup](/onboarding/getting-access): account, API key, Claude Code integration.
  2. [Personal credentials](/onboarding/secrets): Jira/Bitbucket identity and SSH key.
  3. [The Slack bot](/onboarding/slack-bot): approvals and session DMs.
  4. [Team infrastructure](/onboarding/team-infra): VPC peering and cross-account role (one-time, per team).
  5. [Creating compute](/onboarding/compute): EC2 boxes for your team.
  6. [Registering a machine you already own](/onboarding/registering-compute): adopting your own EC2 or bare-metal box, and who is allowed to.
  7. [Tenant admin guide](/onboarding/admin): users, teams, grants, and team secrets.

## Contacts 
  - Access and onboarding: Bhurva Sharma / Abhimanyu Singh Rathore
  - Infra and peering: the platform team
  - Anything broken: send the exact command and its output, not a description.
