Source: https://foundry.mypaytm.com/onboarding/troubleshooting

# When something breaks 
Most Foundry problems announce themselves with an exact error string. This page is indexed by those strings, so the fastest way to use it is to search the page for the text you were given (Cmd+F) rather than reading top to bottom.
Entries are grouped by what you were doing when it broke. Each one says what the message means and what to do about it. If your error is not here, send the exact command and its output to the channel, not a description of it.
## Signing in 
### The reset email never arrives, or a new password is refused 
    
    user not found in Cognito
An account that was invited but never activated sits in a state where the password-reset flow cannot complete: the reset code is never sent, and setting a new password fails with the message above. Nothing you do from the login page will clear it.
Ask your team admin to reset your access. They can issue a temporary password directly. If your team admin cannot do it either, mail `cnoc@paytm.com`. Password and invite problems are the one class of issue that does not start in the users channel.
### Login fails with an authentication error 
    
    {"error":"authentication failed"}
Stale browser storage from a half-finished sign-in causes this, and it survives a normal refresh. Clear the storage for both hosts:
  1. Open the Cognito host `https://ark-platform.auth.ap-south-1.amazoncognito.com/` in one tab and the control plane in another. Do not click anything on either page.
  2. Open the browser inspector on both tabs.
  3. Application, then Storage, then Clear site data. Do this on both tabs.
  4. Go back to the control plane and sign in.

### Sign in with Paytm SSO sends you to a password prompt 
Some accounts land on the password form instead of completing SSO. Sign in with the email and password from your Ark invite, which works, and tell your team admin that SSO did not complete for your account so they can chase it.
### The temporary password has already expired 
Temporary passwords are short-lived. Ask your team admin to issue a fresh one and change it from the account menu as soon as you are in.
## Credentials and keys 
### Which key am I supposed to use 
Onboarding hands out several credentials that look alike and are not interchangeable. This is the most common source of "valid API key required" confusion. See [Which credential is which](/onboarding/secrets#which-credential-is-which) for the full table. In short: the key you paste into a `curl` call or an MCP config is a **user API key** from Settings, and a token minted by the enroll card is a **worker token** that only a machine uses.
### The CLI and the MCP report different tenants 
    
    $ ark auth whoami
      Tenant:  t-fcc4403d28ef (fndry)
    
    # ...while the MCP, asked the same question, answers
      "tenantId": "t-b2be4bb6b0cf"
**This is not necessarily a fault.** The CLI and the MCP authenticate independently. The CLI resolves the active context in `~/.ark/config.yaml`; the MCP presents whatever token its own registration carries. Two credentials, two lookups, and nothing keeps them in step. If those two tokens were minted under different tenants, each surface is correctly reporting its own, and the CLI can sit on one tenant while the MCP works in another all day without complaint.
That matters because **the MCP is what your agent uses**. A session dispatched from your editor lands wherever the MCP resolves, not where the CLI points, so this is worth settling before you dispatch anything.
Two different situations produce this exact symptom, and the fix differs:
| Separate credentials| Stale connection  
---|---|---  
Cause| The MCP's token was minted under a different tenant| The editor is holding a connection from a registration you have since changed or removed  
Tell| A token for the other tenant **exists in your config**| **No token for the reported tenant exists anywhere in your config**  
Fix| Mint a key in the tenant you want and update the MCP registration| Fully restart your editor  
To tell them apart, look for the token rather than guessing:
bash
    
    # Claude Code
    grep -ho 'ark_t-[a-f0-9]*' ~/.claude.json ~/.claude/settings.json 2>/dev/null | sort -u
    # Cursor
    grep -ho 'ark_t-[a-f0-9]*' ~/.cursor/mcp.json .cursor/mcp.json 2>/dev/null | sort -u
Each hit is a token, and the `t-` segment in it names the tenant that token belongs to. If the tenant the MCP reports is **not** in that list, no configuration is producing it and you are looking at a cached connection: restart the editor. A `/mcp` reconnect is not enough, since it re-uses the server definition the session already holds.
If it **is** in the list, that is the separate-credential case. Regenerate the key from the tenant you want (the Connect page mints for the tenant you are currently viewing), update the registration, and restart.
Check this once, at setup
Whichever case you are in, the check is the same one: run `auth/whoami` on both surfaces and compare. Doing it once when you set up costs a minute. Discovering it after a session ran against the wrong tenant costs considerably more.
### The API key is rejected 
    
    valid API key required
Verify the key against the control plane before you change any config:
bash
    
    curl -s https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com/api/rpc -X POST \
      -H "Authorization: Bearer <YOUR_KEY>" -H 'content-type: application/json' \
      -d '{"jsonrpc":"2.0","id":1,"method":"auth/whoami","params":{}}'
Read the reply. Your email with `"role":"member"` or `"admin"` means the key is good. `Unauthorized` means the key is dead or was copied wrong. `"role":"worker"` means you copied a machine token from the enroll card rather than a user key. No response at all means you are not on the corp network or VPN.
### Slack linking is refused 
    
    ark slack link: permission denied: tenant.admin at tenant scope
You reached for `--force`, which mints a link token for another user and is a tenant-admin action. Do not use `--force` on your own account. Run the plain `ark slack link` and DM the token it prints to the bot inside 15 minutes, or use the one-click **Connect Slack** card on the Onboarding page, which needs neither the CLI nor an API key.
## Starting a session 
### A required input is missing 
    
    Flow validation failed:
      - required input "ticket" missing
The flow declares `ticket` as a required input and none was passed. Pass one:
bash
    
    ark session start PAI-1234 \
      --flow <flow> --workspace <workspace> --compute <compute> \
      --summary "what this run should do"
In the web UI, fill the Ticket field in the start dialog. If the flow should not need a ticket, that is a change to the flow definition, not to your dispatch.
### The workspace has never been checked 
    
    workspace_undoctored
Dispatch runs a doctor gate before it places your session, and a workspace that has never passed it is refused wherever it would have landed. The related states read the same way: **undoctored** means it has never been checked, **doctor stale** means the last check is too old to trust, and **doctor fail** means the last check failed.
Prove the workspace materialises, then dispatch:
bash
    
    ark workspace test <workspace> --compute <compute>
`workspace test` runs the real provision, clone and services phases with no agent in the loop, and it is the cheapest way to find a broken workspace. Add `--keep` to leave the prepared tree up for inspection.
### A secret will not resolve 
    
    Secret resolution failed: Missing required secrets: <NAME>
A workspace that declares a secret which resolves to nothing fails at dispatch. This is a hard failure and not a warning, and it happens before the agent starts, which is why the run dies instantly.
Secrets resolve narrowest first: user, then team, then tenant. When a secret is set and still reports missing, it is almost always one of these:
  - It was set against the team's **name or slug** rather than its canonical `tm-` id. Dispatch looks up the canonical id.
  - The session is running under a **different team** than the one holding the secret. The session's team must match.
  - The value **expired**. `CLAUDE_CODE_OAUTH_TOKEN` in particular is an OAuth token that rotates, and a lapsed one reads as missing.

Setting the same secret at **user** scope resolves ahead of team and tenant, and is the quickest way to unblock yourself while a team-scope problem is sorted out.
### The runtime is Cursor but Claude credentials are still demanded 
Dispatch checks every secret the **workspace** declares, whichever runtime you selected. A workspace that lists `CLAUDE_CODE_OAUTH_TOKEN` fails without it even on a `--runtime cursor` run. Either supply the token, or use a workspace that does not declare it. Selecting a runtime does not change what the workspace requires.
## The session starts, then fails 
### No worker is registered for the compute 
    
    no arkd worker is registered for compute "<name>" (kind: registered-host) --
    workspace prepare must execute on that compute's arkd, not in this control-plane
    process. Waited 300s for the compute's arkd to register and it did not.
The control plane has the compute registered, but its `arkd` daemon is not checking in. The compute page can still show the box as running, because that column reflects the registration rather than a live worker.
On the host itself, in order:
  1. Restart `arkd`. On Linux that is your systemd unit; on macOS it is the LaunchAgent.
  2. If that does not bring it back, re-enroll: `ark host enroll <name>`. This re-registers the worker and mints a fresh token, which also covers an expired one.
Re-enrolling cuts off anything already running on that box
Enrolling against an existing name **rotates the token, and the previous one stops working immediately**. On a shared box that kills every session currently running on it, not just yours. Check with `ark compute show <name>` for live sessions before you re-enroll, and restart `arkd` first: that fixes most cases without rotating anything.
  3. Confirm from **another** machine with `ark compute list`. Checking on the box itself with `ark host status` is not proof: that command reads local state and will report a healthy enrollment for a compute the control plane no longer has.

A control-plane outage will knock workers offline, and they do not always reconnect on their own. If several people report this at once, check the channel before debugging your own box.
### Artifacts are missing from an earlier stage 
    
    Stage '<later>' consumes missing artifacts: plan.md
    (expected from stage '<earlier>', write at $WORKSPACE_ROOT/.ark/artifacts/plan.md)
Stages hand work forward as typed artifacts. The named stage did not write the file the later stage declared it would consume, so the later stage has nothing to read. The failure is reported at the consuming stage, but the fault is in the producing one: open that stage's output and find out why it ended without writing.
### A stage routes somewhere that does not exist 
    
    stage '<name>' routes to unknown stage '<other>'
The flow definition names a stage in an `on_red` or `depends_on` edge that is not defined in that flow. Validate the flow before spending compute on it:
bash
    
    ark flow validate ./my-flow.yaml --repo .
`flow validate` runs the same structural and DAG checks a real dispatch runs, and creates nothing.
### The model rejects the credential 
    
    401 OAuth access token has expired. Re-authenticate to continue.
The Claude OAuth token in the scope your session resolved has expired. If it is your own, mint a new one with `claude setup-token` and set it again at user scope. If your sessions use a shared team or tenant token, that token needs rotating by whoever owns it, and every run in that scope is failing the same way, so say so in the channel rather than debugging your own setup.
### The model rate limit is reached 
    
    model rate limit reached (five_hour); resets at <timestamp>
The subscription behind your runs is rate-limited, and the limit is shared by everyone resolving the same token. Switching model does not avoid it, because the limit is on the credential rather than the model. Wait for the reset, or have a different token set in your scope. If you hit this repeatedly, the run itself is probably too expensive: see Keeping a session affordable.
## Cloning repositories 
### Permission denied on clone 
    
    clone failed for <repo>: config/access problem (wrong URL, missing or unauthorized
    SSH key, repo not visible to this identity) -- NOT a compute failure
    git@bitbucket.org: Permission denied (publickey).
The message names the three real causes, and the compute is explicitly not one of them. Work through them in this order:
  1. **The wrong key name.** The Bitbucket SSH secret is suffixed with the Bitbucket workspace it unlocks. `BITBUCKET_SSH_KEY_PAYTMTEAM` covers the `paytmteam` workspace; `BITBUCKET_SSH_KEY_PAYTMMONEY` covers `paytmmoney`. Setting the wrong one for your repos looks exactly like an unauthorized key. See [the Bitbucket SSH key](/onboarding/secrets#bitbucket-ssh-key).
  2. **The key was never valid.** Test it from your own machine before blaming Foundry: `ssh -i ~/.ssh/ark_bitbucket -T git@bitbucket.org`.
  3. **The repo is not visible to that identity.** The key authenticates a Bitbucket account, and that account needs access to the repo.

### The clone is blocked by an IP allowlist 
    
    To access this repository, an admin must whitelist your IP.
The compute's outbound address is not on the Bitbucket or Jira allowlist. Put your compute behind one stable egress NAT gateway and raise a request to add that single address, which is far easier to maintain than one entry per box.
## Apple Silicon and registered hosts 
Personal laptops are being withdrawn as compute
Sessions on personal Macs fail often, for reasons that have nothing to do with your code: the disk fills, the machine sleeps, the network drops, and macOS offers no good process isolation for a shared box. Foundry is moving these runs onto team cloud machines. Work with your DevOps to get your team's compute set up, and prefer it for anything you care about. If you must register a Mac, scope it to **your own user** and not to the team.
### A module is missing when the runtime is Cursor 
    
    Cannot find module './986.js' from '/$bunfs/root/ark-darwin-arm64'
This is a packaging bug in the Ark binary, not a problem with your repo or your ticket. Update to a build past it. Two traps make the update look like it did not work:
  - A running `arkd` keeps executing the **old binary from memory** even after the file on disk is replaced. Stop and restart `arkd` after updating.
  - The `ark` that sessions actually launch is the one first on `PATH`, which is often `/usr/local/bin/ark` rather than the copy under `~/.ark/bin`. Updating one and not the other leaves the stale binary in the launch path, so an on-box smoke test passes while real sessions keep failing.

After updating, confirm which binary is being resolved with `which ark` and `ark --version`.
### The MCP probe cannot find a command 
    
    <mcp-name> MCP failed to start: timeout: command not found
The probe's search path does not include the Homebrew directory where Apple Silicon puts these binaries, and a user without sudo cannot write to the system directories it does search. This affects every non-admin Apple Silicon user and a platform-side fix is in flight. Until it ships, link the binaries into the directory the probe does look at, then retry the stage:
bash
    
    ln -sfn ~/.local/bin/uv  /opt/homebrew/bin/uv
    ln -sfn ~/.local/bin/uvx /opt/homebrew/bin/uvx
    ark session retry-stage <session-id>
### Actions fail with a read-only filesystem 
    
    EROFS: read-only file system, mkdir '/workspace'
Workspace actions are spawning against the Linux container path rather than the prepared session directory on your Mac. The macOS system volume is sealed, so the directory cannot be created. Workspace prepare succeeds and only the action path fails, which makes every gate go red while the implementation is fine. There is no local fix that does not involve root; run these gates on a Linux compute.
## Keeping a session affordable 
Token spend is dominated by **cache reads** , not by the visible input and output of a single turn, so a run can cost far more than its transcript suggests. A stage that carries a large context and then iterates re-reads that context every time.
Two things drive nearly every runaway run:
  - **Uncapped back-edges.** A reviewer that routes work back to an earlier stage will do it as many times as the flow allows. Set `max_iterations` on any stage with an `on_red` edge pointing back at it.
  - **An oversized context in the implementing stage.** Large files or accumulated artifacts pulled into a stage are re-read on every iteration of that stage.

Because the rate limit belongs to the credential rather than to one session, a single expensive run will stall every other agent resolving the same token. Stagger heavy runs, or give them a different credential.
Per-session cost and token counts are on the session's Cost tab.
## Getting help 
When you report a problem, send the exact command and its exact output, plus the session id. A session id lets whoever picks it up read the event trail directly, and it saves a round trip that usually costs an hour.
Check whether the control plane itself is degraded before deep-diving your own setup. A broad outage produces 503s, socket errors, sessions stuck mid-stage and workers dropping offline all at once, and it is announced in the channel.
