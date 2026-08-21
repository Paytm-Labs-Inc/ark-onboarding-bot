Source: https://foundry.mypaytm.com/onboarding/slack-bot

# Ark Bot on Slack 
The bot is installed in the workspace; you just link your Slack identity to your Ark identity - that's what lets `/ark` run as you and lets Ark DM you about sessions and approvals.
## The quick way 
In the web UI, open the **Onboarding** tab and click **Generate** on the **Connect Slack** card. It mints your link token with no CLI and no admin involvement. Then go to Step 3 and DM it to the bot.
## The CLI way 
Use this if you would rather stay in the terminal. It needs your own personal API key, the same one from [Getting Access](/onboarding/getting-access). It must be an identity-bound user key: a shared key will not work.
**Step 1** \- Search for **Ark-bot** in Slack.
**Step 2 - Mint your link token** (any terminal with the `ark` CLI):
bash
    
    ark slack link \
      --server https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com \
      --token <YOUR_ARK_API_KEY>
The token expires in **15 minutes** and works once - do Step 3 immediately.
Do not reach for `--force`
`--force` mints a link token for **another** user and is a tenant-admin action, so on your own account it fails with `permission denied: tenant.admin at tenant scope`. If you cannot DM the bot, send your Slack member ID to an admin instead.
**Step 3 - DM the bot:** open a direct message to the Ark bot and send:
    
    ark link <token-from-step-2>
The bot confirms your accounts are linked.
**Step 4 - Use it.** In any channel with the bot (invite with `@Ark`), run `/ark` commands - sessions dispatch as you, and Ark DMs you for completions and gate approvals.
## If something's off 
  - `/ark` replies "Your Slack account is not linked" → token expired or the DM didn't land; redo Steps 2–3
  - Token minting fails with a permissions error → your API key isn't identity-bound; mint a fresh one from the web UI (Settings → API Keys), don't use a shared key
  - Can't DM the bot at all → send a tenant admin your Slack member ID for a direct admin-side link

## That is onboarding done 
You have access, credentials, a session you ran yourself, and notifications where you will see them. From here:
  - [Bring your own agents and flows](/onboarding/authoring-your-own) when a built-in flow stops fitting.
  - [When something breaks](/onboarding/troubleshooting), indexed by the exact error string.
  - If your team still has no compute of its own, that is [team infrastructure](/onboarding/team-infra), and it is a DevOps task.
