Source: https://foundry.mypaytm.com/onboarding/updating-cli-plugin

# Updating the Ark CLI & plugin 
The `ark` binary and the `ark-superpowers` Claude Code plugin ship from the control plane, so they move forward as the platform does. When a release lands you re-pull both: the CLI to pick up new commands and fixes, the plugin so Claude Code sees the current agents, flows, and skills. This page is the update path once you already have an account and a working `ark login`. If you have not set either up yet, start at [Getting Access + MCP](/onboarding/getting-access).
## Run the update 
Just the plugin? Use the subcommand
If you only need to (re)install the `ark-superpowers` plugin -- not refresh the CLI binary -- run [`ark install-claude-code`](/reference/cli). It reads the token from your active context, so nothing sensitive lands on a command line, and it runs the same marketplace-add and plugin-install that the script below does. The script is the full path when you want to move the CLI binary forward too.
This reads your active context from `~/.ark/config.yaml`, so there is no token to paste, and it is safe to re-run whether or not the plugin is already installed:
bash
    
    # --- Update the Ark CLI and the ark-superpowers plugin -----------------------
    # Takes the credential from your own `ark login`, so there is no token to paste.
    # Safe to re-run, and works whether or not the plugin is already installed.
    
    CFG=~/.ark/config.yaml
    ACTIVE=$(awk '/^active:/{print $2; exit}' "$CFG")
    # Read the active context's server + token straight into shell vars with
    # `$(awk ...)`, NOT `eval`: a value in the file must never be executed.
    SERVER=$(awk -v w="$ACTIVE" '$1=="-" && $2=="name:"{b=($3==w)} b && $1=="server:"{print $2; exit}' "$CFG")
    TOKEN=$(awk -v w="$ACTIVE" '$1=="-" && $2=="name:"{b=($3==w)} b && $1=="token:"{print $2; exit}' "$CFG")
    HOST=${SERVER#https://}
    OS=$(uname -s | tr 'A-Z' 'a-z')
    ARCH=$(uname -m | sed 's/x86_64/x64/;s/aarch64/arm64/')
    echo "context: $ACTIVE -> $HOST ($OS-$ARCH)"
    
    # 1. CLI. Download to a temp file IN the bin dir, verify it is non-empty, then
    #    move into place. Writing straight to ~/.ark/bin/ark would FOLLOW a symlink
    #    and overwrite whatever it points at; a same-dir temp keeps the move on one
    #    filesystem. The guard matters: without it a failed download (server down,
    #    404, or a captive portal returning HTML) installs a 0-byte `ark` that
    #    `ark --version` still reports as "working" -- an empty file is a valid
    #    empty script -- silently replacing a good CLI with a broken one.
    TMP=~/.ark/bin/ark.download
    if curl -fsSL "$SERVER/cli/ark-$OS-$ARCH" -o "$TMP" && [ -s "$TMP" ]; then
      chmod +x "$TMP"
      mv -f "$TMP" ~/.ark/bin/ark
      xattr -cr ~/.ark/bin/ark 2>/dev/null || true
    else
      rm -f "$TMP"
      echo "CLI download failed; existing ark left untouched." >&2
    fi
    
    # 2. Plugin. `marketplace add` with the fresh URL, NOT `marketplace remove`
    #    first. The marketplace URL embeds whichever token was current at install
    #    time, so after a key rotation or a tenant switch the stored token is stale.
    #    Re-adding under the same `ark` name overwrites the stored source (token and
    #    all) in place, so this refreshes the credential without ever uninstalling
    #    anything. A pre-emptive `remove` would delete your working plugin BEFORE the
    #    add is known to succeed -- and if the token is stale (the reason you are on
    #    this page), the add then 401s and you are left with no plugin at all. The
    #    guard keeps the existing install untouched on a failed add.
    if claude plugin marketplace add "https://x-access-token:$TOKEN@$HOST/git/ark-superpowers.git"; then
      claude plugin install ark-superpowers@ark --config "ark_server=$SERVER" --config "ark_token=$TOKEN"
    else
      echo "Marketplace add failed (token stale?); existing plugin left untouched." >&2
    fi
    
    ark --version
    echo "Restart Claude Code for the plugin update to take effect."
The plugin URL carries your token
The `marketplace add` line embeds your API token in the URL. For the moment it runs, that URL is visible to every other user of the machine via `ps`, and it lands in your shell history. Don't run this on shared compute while other people are on the box -- use your own machine, or clear the relevant history line after. (`ark context current --json` deliberately withholds the token for the same reason.) To skip the manual marketplace surgery entirely, use [`ark install-claude-code`](/reference/cli): it reads the token from your active context, builds the same marketplace-add and plugin-install steps, and never puts the token on a command line you type. See below.
The script prints the context and target it resolved, updates the CLI, then re-adds the marketplace with the current token and installs the plugin. It ends by printing `ark --version`.
## Why it works this way 
Two steps look indirect on purpose.
The CLI downloads to a temp file and then moves into place rather than writing straight to `~/.ark/bin/ark`. A direct write would follow a symlink and overwrite whatever the link points at; the move replaces the entry itself.
The plugin marketplace is re-added with the current token rather than updated in place, and it is never removed first. The marketplace URL embeds whichever token was current when you installed it, so after a key rotation or a tenant switch a plain `claude plugin marketplace update` keeps presenting the stale token and returns a 401 with no way to recover in place. Re-adding under the same `ark` name overwrites the stored source, token and all, which refreshes the credential without uninstalling anything. Removing the marketplace up front would be the destructive move: it uninstalls the plugin before the add is known to work, so a stale token would strand you with no plugin at all. The add is guarded for that reason, and `install` is a no-op when the plugin is already present.
## After you update 
Restart Claude Code for the plugin update to take effect, then run `/mcp` to confirm `ark` still reports connected. If the marketplace re-add failed on authentication, your stored token is stale: remint a key and run `ark login` again, then re-run the update. The commands and identity check are the same ones covered under [Getting Access + MCP](/onboarding/getting-access).
Once you are on the current release, applying a tenant workspace takes an explicit `--namespace <token>`: `ark workspace apply <file> --namespace <token>` prepends the token to the YAML's `name:`, so the stored name is `<token>-<name>`. The namespace is required; `--legacy` applies under the bare name for records created before namespaces existed. See [Builtins vs tenant records](/guide/workspaces#builtins-vs-tenant-records) for the full mechanism.
