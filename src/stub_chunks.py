"""Hardcoded doc chunks for answer-layer testing before real retrieval lands."""

STUB_CHUNKS = [
    {
        "source": (
            "getting-started — https://foundry.mypaytm.com/onboarding/getting-started"
        ),
        "text": (
            "Step 5 — Enroll your machine (compute). Your laptop becomes a "
            "registered-host compute target agents run on. Preferred (current): "
            "From Ark Connect → Enroll a machine → copy the generated command, or run: "
            "ark host enroll <compute-name> --launchd --detach, then "
            "ark host status <compute-name>. Expected: arkd: running, health OK, "
            "enrolled yes. Legacy script: some tenants still ship ark-enroll-host.sh — "
            "run MACHINE_NAME=<host-identifier> ./ark-enroll-host.sh, then "
            "ark compute show <host-identifier>. If enroll or doctor fails on Mac, "
            "install prerequisites: brew install tmux coreutils and install bun. "
            "For capacity errors: ark compute update <host-identifier> "
            "--set config.capacity.memory=8Gi --set config.capacity.cpu=400."
        ),
    },
    {
        "source": "set-up-cursor — https://foundry.mypaytm.com/onboarding/set-up-cursor",
        "text": (
            "Connect Ark MCP in Cursor. Use a dedicated MCP key, not the same key as "
            "CLI login. Edit ~/.cursor/mcp.json with an ark http MCP entry pointing at "
            "https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com/mcp and "
            "Authorization: Bearer <YOUR_MCP_API_KEY>. Then in Cursor: Customize → MCP "
            "→ enable ark User. Approve if prompted. For Cursor CLI agent, also run: "
            "agent mcp enable ark. Ignore a red ark Plugin error if ark User shows tools "
            "enabled — that is a duplicate plugin MCP, not your User entry. Store "
            "CURSOR_API_KEY from https://cursor.com/dashboard/api in Ark Secrets when "
            "your workspace uses the Cursor runtime."
        ),
    },
    {
        "source": "set-up-cursor — https://foundry.mypaytm.com/onboarding/set-up-cursor",
        "text": (
            "Choose your agent runtime before starting a session. Prerequisites list "
            "Cursor and/or Claude Code subscription, depending on runtime. For Cursor "
            "runtime sessions, pass --runtime cursor when starting from CLI, for example: "
            "ark session start --flow <your-flow> --workspace <your-workspace> "
            "--compute <your-compute-name> --runtime cursor --summary "
            "<task description>. The workspace must have CURSOR_API_KEY configured in "
            "Ark Secrets. For Claude Code runtime, use Connect → Connect Claude Code "
            "(superpowers) and configure CLAUDE_CODE_OAUTH_TOKEN instead."
        ),
    },
]
