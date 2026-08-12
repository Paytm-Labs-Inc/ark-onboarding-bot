Source: https://foundry.mypaytm.com/onboarding/cursor

# Set up Cursor 
Cursor drives Foundry the same way Claude Code does: it connects to the control plane's MCP server and gets the `ark` toolbox. Foundry is a Streamable-HTTP MCP server, so you point Cursor at the `/mcp` endpoint, pass your key in the `Authorization` header, and you can dispatch sessions, inspect flows, and steer runs without leaving the editor.
This page is the Cursor-specific companion to [Getting Access](/onboarding/getting-access) and [Using Foundry over MCP](/guide/mcp). Both mint and wire the same key; the only difference here is where the config goes.
## Prerequisites 
  - An Ark account. If you do not have one yet, follow [Getting Access](/onboarding/getting-access) first.
  - A per-user API key. Mint it in the web UI under **Settings - > API Keys -> Create key**, name it something you will recognise later (for example `cursor-<yourname>`), and copy it immediately. The key is shown once, looks like `ark_t-<hex>_<hex>`, and cannot be recovered. Store it in a password manager, and never paste it into a Slack channel. The [installation guide](/guide/installation#minting-a-key-by-hand) covers key hygiene in full.

You must be on the corp network or VPN for any of this to reach the control plane.
## Add the ark MCP server to Cursor 
The onboarding UI generates the config for you. In the web UI, open the **Onboarding** tab and click **Generate** on the MCP card. It mints a fresh key and shows a project-scoped `.mcp.json` with that key already embedded. Copy that block, or use the **Download .mcp.json** button. The generated config is editor-agnostic: the same `mcpServers` block works for Cursor.
Cursor reads MCP config from `.cursor/mcp.json`. Use a global config at `~/.cursor/mcp.json` to make `ark` available in every project, or a project-scoped `.cursor/mcp.json` at the repo root to scope it to one repo. Paste the generated block, replacing `<YOUR_KEY>` with your key:
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
You can also add it through the UI: open **Cursor Settings - > MCP -> Add Server** and enter the same values (an HTTP server named `ark`, URL ending in `/mcp`, and the `Authorization: Bearer <YOUR_KEY>` header). Either path writes the same `mcpServers.ark` entry.
Two details account for most failures: the URL must be the `https://ark.internal...` host ending in `/mcp` and not localhost, and the key goes in the `Authorization` header exactly as shown.
## Verify the connection 
Open **Cursor Settings - > MCP**. When `ark` shows as connected with its tools listed (`agent`, `compute`, `flow`, `session_lifecycle`, `workspace`, and more), you are wired up. For an end-to-end check, ask Cursor to "list ark computes" and watch a real answer come back from your tenant.
To test the key itself, independently of Cursor, call `auth/whoami` against the control plane. It is the same identity check `ark login` makes:
bash
    
    curl -s https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com/api/rpc -X POST \
      -H "Authorization: Bearer <YOUR_KEY>" -H 'content-type: application/json' \
      -d '{"jsonrpc":"2.0","id":1,"method":"auth/whoami","params":{}}'
Read the response like this:
  - JSON with your email and `"role":"member"` or `"admin"` means the key is good.
  - `Unauthorized` means the key is dead or wrong, so mint a new one.
  - `"role":"worker"` means you copied a machine token from the enroll card rather than a user key.
  - No response at all means you are not on the corp network or VPN.

## Use it 
Once `ark` is connected, drive Foundry from Cursor in plain English. Ask Cursor to "list ark flows" to see what you can dispatch, or "start an ark session" to kick off a run. The tools are namespaced as `mcp__ark__<name>` (for example `mcp__ark__flow`, `mcp__ark__session_lifecycle`), and Cursor calls them for you.
For the full first-run flow, from dispatching your first session to watching it work end to end, follow the [Quickstart](/guide/quickstart). [Using Foundry over MCP](/guide/mcp) covers the whole tool surface once you are connected.
## Notes and gotchas 
  - **Key hygiene.** The key in `mcp.json` grants your access. Keep it out of shared repos and screenshares, and prefer a password manager over pasting it around. Each user may hold up to ten live keys at a time; revoke one before minting the eleventh.
  - **VPN required.** The control plane is reachable only from the corp network or VPN. "No response at all" from the checks above almost always means you are off the VPN.
  - **Worker token vs user key.** A key that reports `"role":"worker"` is a machine token minted for an `arkd` daemon, not a user key. Mint a fresh user key under **Settings - > API Keys** instead.
  - **Getting help.** If a good key still will not connect, send Bhurva Sharma the exact output of the `auth/whoami` check plus a screenshot of the Cursor MCP settings. The full triage lives in [Getting Access](/onboarding/getting-access).
