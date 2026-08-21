Source: https://foundry.mypaytm.com/onboarding/getting-access

# Getting Access 
## Get an Ark account 
**Step 1** \- Open <https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com> (you must be on the corp network/VPN). If the page doesn't load, ask one of your tenant's admins for an account, with your **email and team name**. They are listed on the **Settings** page under **Your admins**. Onboarding a whole team? Send a .csv of everyone's email and team.
**Step 2** \- Check your inbox for an email from "Ark" and log in.
Login problems have their own fixes, and most of them cannot be solved from the login page: an account invited but never activated cannot receive a password-reset code, and a half-finished sign-in leaves browser state that fails with `{"error":"authentication failed"}` until it is cleared. See [Signing in](/onboarding/troubleshooting#signing-in) for both. Password and invite issues go to your team admin first, then to `cnoc@paytm.com`.
Your tenant's admins are listed on the **Settings** page under **Your admins** , so you never have to guess who to ask.
## Install the CLI 
Several steps below and on later pages need the `ark` binary on your PATH. Install it now.
In the web UI, open the **Onboarding** tab. The **Install the CLI** card generates a one-liner that downloads the binary and logs it in against this control plane, with a key minted at your role. The same card offers direct downloads for macOS and Linux, on both architectures, if you would rather place the binary yourself and run `ark login`.
Confirm it landed, and confirm which identity it holds:
bash
    
    ark --version
    ark auth whoami
`whoami` prints the user, role and tenant the credential resolves to. Read it now rather than later: every command on the following pages acts as that identity.
## Set up the Ark MCP (use Ark from Claude Code) 
**Step 1** \- In the Ark web UI, open the **Onboarding** tab and click **Generate** on the **Connect Claude Code** card.
**Step 2** \- The card offers **two snippets, and they are alternatives rather than a sequence**. Pick one:
  - **The plugin one-liner** installs the `ark-superpowers` plugin, which carries the MCP tools and the skills, once, for every project. Use this unless you have a reason not to.
  - **The project-scoped`.mcp.json`** wires the MCP into a single repo and nothing else. Use this when you want Foundry available in one project only.

The embedded key is the same in both. Running both is the mistake to avoid: it leaves two registrations of the same MCP server, they drift apart as keys are rotated, and the stale one can answer your calls without any visible sign.
The key is masked until you press **Reveal** , and **Copy** puts the real value on your clipboard without ever showing it. This is the only time the key is available: if you lose it, mint a new one rather than hunting for it.
**Step 3** \- In Claude Code, type `/mcp` and press Enter.
**Step 4** \- Confirm you are connected, then confirm **what you are connected to**. Ask Claude to run `auth/whoami` and check the user, role and tenant that come back are the ones you expect.
A connection state alone does not tell you this. An MCP server left over from an earlier setup will report as connected and return real data while acting as a different identity, so `connected` and `connected as me` are separate claims and only the second one is worth acting on. An end-to-end check is asking Claude to "list ark computes" and recognising the computes it names.
The CLI and the MCP authenticate separately
They do not share a credential. The CLI reads the active context in `~/.ark/config.yaml`; the MCP presents whatever token its own registration carries. If those were minted under different tenants, `ark auth whoami` and the MCP's `auth/whoami` will each correctly report a different tenant, and neither is wrong.
Compare them now, because **the MCP is the one your agent dispatches through**. If they disagree, [work out which case you are in](/onboarding/troubleshooting#the-cli-and-the-mcp-report-different-tenants): a separate credential needs a new key, while a connection left over from an earlier setup only needs a restart.
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

**Step 3 - Refresh the credential.** What you run depends on which snippet you took in Step 2.
If you installed the **plugin** , re-run the plugin one-liner from the Onboarding card, or run `ark install-claude-code`, which reads the key from your active `ark login` context so no token is typed on a command line. The plugin's registration is named `plugin:ark-superpowers:ark` rather than `ark`, so `claude mcp remove ark` does not touch it.
If you took the **project-scoped** snippet, replace that entry:
bash
    
    claude mcp remove ark
    claude mcp add --transport http ark \
      https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com/mcp \
      --header "Authorization: Bearer <YOUR_KEY>"
Two classic mistakes: the URL must be the `https://ark.internal…` one ending in `/mcp` (not localhost), and the key goes in the `Authorization` header exactly as above.
Run `claude mcp list` afterwards. Seeing both a `plugin:ark-superpowers:ark` entry **and** a bare `ark` entry means you have the duplicate registration described in Step 2 of the setup above, and you should remove the one you are not using.
**Step 4 - Restart Claude Code.** A `/mcp` reconnect re-uses the server definition the session already holds and will not pick up a corrected config, so the restart is the step that makes your change take effect. You should then see the tools (agent, compute, flow, session_lifecycle, workspace and more). Finish by checking identity with `auth/whoami`, not by checking that the connection is green.
Still failing? Send one of your tenant's admins the exact output of Step 2 plus a screenshot of `/mcp`. The exact output matters: a description of it costs a round trip.
## Next steps 
You have an account, the CLI, and Foundry connected to your editor.
**Step 2 of 4:** [Set your personal credentials](/onboarding/secrets). Do this before you try a session: a session checks the credentials it needs before it starts, so running one without them fails immediately.
Later, when a built-in flow no longer fits, you can [bring your own agents and flows](/onboarding/authoring-your-own).
