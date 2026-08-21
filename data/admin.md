Source: https://foundry.mypaytm.com/onboarding/admin

# Tenant Admin Guide 
Users, teams, grants, and team-scoped secrets.
## Adding a user to the tenant 
Account creation (Cognito login + tenant membership) is done by the platform team - send the email and team name (or a .csv for a whole team) to a tenant admin, listed on the **Settings** page under **Your admins**. Once the user exists, you control _what they can do_ with grants:
bash
    
    ark grant <email> --role member                       # tenant-wide member (default scope: tenant)
    ark grant <email> --role team-admin --scope team:<team-slug>
    ark grant list <email>                                # see someone's live grants
    ark grant revoke <grant-id>                           # remove one
Roles: `tenant-admin` | `team-admin` | `member` | `viewer`. Grants are the authority primitive - a team grant covers that team's subtree.
## Teams 
bash
    
    ark team list
    ark team create <slug>
    ark team members --help     # manage memberships
## Team-scoped secrets 
Teams can override identity secrets (Bitbucket bot user, own `CLAUDE_CODE_OAUTH_TOKEN`, JFrog account) for all their members at once - web UI → Secrets → Add secret → scope **team**.
Resolution is **user → team → tenant** , first hit per key wins; a team that sets nothing simply inherits tenant defaults. Team writes require a team-admin grant for that team (or tenant-admin).
WARNING
Never override infra values (`JIRA_BASE_URL`, JFrog URL, etc.) at team scope - it becomes invisible per-team configuration drift that looks like "Ark is broken only for our team."
## API-key hygiene 
  - Mint per-person keys (individually revocable), `member` role unless the person administers the tenant
  - Self-service keys (web UI → Settings → API Keys) are automatically bound to the person's identity - prefer them over admin-minted keys
  - Rotate anything that has ever been pasted into a chat
