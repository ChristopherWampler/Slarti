# Discord Setup — Phase 7

Complete instructions for creating the Slarti Discord bot, setting up the 7 channels, and wiring everything into `.env` and `openclaw.json`.

---

## Overview

Phase 7 requires four things:
1. A Discord bot (application + token)
2. A Discord server (guild) with the 7 Slarti channels
3. A webhook for the `#admin-log` channel
4. The Discord user IDs for Emily and Christopher

When done, these values will be set in `.env` and the Discord section of `~/.openclaw/openclaw.json` will be uncommented.

---

## Step 1 — Create the Discord Application and Bot

1. Go to [https://discord.com/developers/applications](https://discord.com/developers/applications) and log in with Christopher's Discord account.

2. Click **"New Application"** in the top right.
   - Name: `Slarti`
   - Click **Create**.

3. In the left sidebar, click **Bot**.

4. Click **"Add Bot"** → **"Yes, do it!"**

5. Under **Privileged Gateway Intents**, enable ALL THREE:
   - **Presence Intent** → ON
   - **Server Members Intent** → ON
   - **Message Content Intent** → ON (required — without this, Slarti cannot read messages)

6. Click **Save Changes**.

7. Still on the Bot page, click **"Reset Token"** → confirm.
   - Copy the token — **you will only see it once**.
   - This is your `DISCORD_BOT_TOKEN`.

---

## Step 2 — Create the Discord Server

If Slarti already has a dedicated Discord server, skip to Step 3. Otherwise:

1. In your Discord client, click the **+** (Add a Server) button in the left panel.
2. Choose **"Create My Own"** → **"For me and my friends"**.
3. Server name: `Slarti Garden` (or anything you prefer).
4. Click **Create**.

---

## Step 3 — Create the 7 Slarti Channels

In the new server, create these exact channels (exact names matter — Slarti's AGENTS.md routes by channel name):

| Channel | Type | Purpose |
|---|---|---|
| `#garden-chat` | Text | General garden conversation — Slarti's primary channel |
| `#garden-photos` | Text | Photo drops for analysis (Modes A/B/D) |
| `#garden-design` | Text | Design descriptions without photos (Mode C) |
| `#garden-log` | Text | Read-only log of all garden updates Slarti writes |
| `#garden-builds` | Text | Build summaries from approved designs — Christopher's workspace |
| `#plant-alerts` | Text | Frost/heat advisories, treatment reminders |
| `#weekly-summary` | Text | Slarti's Sunday evening summary |
| `#admin-log` | Text | System alerts (git failures, errors) — Christopher only |

To create a channel: right-click your server name → **Create Channel** → Text Channel → enter the name.

For `#admin-log`: right-click → **Edit Channel** → **Permissions** → remove `@everyone` view permission; add Christopher's username with view permission.

---

## Step 4 — Get the Guild (Server) ID

The Guild ID is your Discord server's unique identifier.

1. In Discord, go to **User Settings** → **Advanced** → enable **Developer Mode**.
2. Right-click on your server name in the left panel.
3. Click **"Copy Server ID"**.
4. This is your `DISCORD_GUILD_ID`.

---

## Step 5 — Create the #admin-log Webhook

1. Right-click the `#admin-log` channel → **Edit Channel**.
2. In the left sidebar, click **Integrations** → **Create Webhook**.
3. Name it `Slarti Admin`.
4. Click **"Copy Webhook URL"**.
5. This is your `DISCORD_ADMIN_WEBHOOK`.

---

## Step 6 — Invite the Bot to Your Server

1. Go back to [https://discord.com/developers/applications](https://discord.com/developers/applications) → select the Slarti application.
2. In the left sidebar, click **OAuth2** → **URL Generator**.
3. Under **Scopes**, check: `bot`, `applications.commands`
4. Under **Bot Permissions**, check:
   - Read Messages / View Channels
   - Send Messages
   - Read Message History
   - Manage Messages (to delete its own staging messages)
   - Embed Links
   - Attach Files
5. Copy the generated URL at the bottom and open it in your browser.
6. Select your Slarti Garden server → **Authorize**.

---

## Step 7 — Get Discord User IDs

1. With Developer Mode enabled (Step 4), right-click on **Emily's username** in any channel.
2. Click **"Copy User ID"**.
3. Repeat for **Christopher's username**.

---

## Step 8 — Fill in `.env`

Open `C:\Openclaw\slarti\.env` and fill in the three empty Discord fields:

```
DISCORD_BOT_TOKEN=your-bot-token-here
DISCORD_GUILD_ID=your-guild-id-here
DISCORD_ADMIN_WEBHOOK=https://discord.com/api/webhooks/...
```

---

## Step 9 — Update `config/discord_users.json`

Open `C:\Openclaw\slarti\config\discord_users.json` and replace the placeholder IDs:

```json
{
  "YOUR_EMILY_USER_ID": "emily",
  "YOUR_CHRISTOPHER_USER_ID": "christopher"
}
```

For example:
```json
{
  "123456789012345678": "emily",
  "987654321098765432": "christopher"
}
```

---

## Step 10 — Update `~/.openclaw/openclaw.json`

Add the Discord channel to the Slarti agent. Open `C:\Users\Chris\.openclaw\openclaw.json` and add a `channels` section to the slarti agent entry:

```json
{
  "agents": {
    "list": [
      {
        "id": "slarti",
        "default": true,
        "name": "Slarti",
        "workspace": "C:\\Openclaw\\slarti",
        "model": "anthropic/claude-sonnet-4-6",
        "identity": {
          "name": "Slarti",
          "emoji": "🌱"
        },
        "groupChat": {
          "mentionPatterns": [
            "@Slarti",
            "@slarti"
          ]
        },
        "sandbox": { "mode": "off" },
        "tools": { "profile": "minimal" },
        "channels": [
          {
            "platform": "discord",
            "token": "${DISCORD_BOT_TOKEN}",
            "guildId": "${DISCORD_GUILD_ID}"
          }
        ]
      }
    ]
  }
}
```

The `${DISCORD_BOT_TOKEN}` and `${DISCORD_GUILD_ID}` references will be resolved from your Windows environment variables at runtime — the same mechanism used for API keys.

**Set the env vars now** (run in PowerShell):
```powershell
# Open .env and copy the values, then run:
setx DISCORD_BOT_TOKEN "your-bot-token-here"
setx DISCORD_GUILD_ID "your-guild-id-here"
```

---

## Step 11 — Restart the Gateway

After updating `openclaw.json`, restart the gateway so it picks up the new Discord channel:

```powershell
# In PowerShell
openclaw gateway stop
openclaw gateway start
openclaw gateway status
```

Or from WSL2:
```bash
/mnt/c/Openclaw/slarti/scripts/restart.sh
```

---

## Step 12 — Verify

1. **Bot is online**: The Slarti bot should appear as online in your Discord server.
2. **Test message**: In `#garden-chat`, type `@Slarti hello` — Slarti should respond in character.
3. **Check gateway logs**: `openclaw gateway status` should show the Discord channel connected.
4. **Admin log test** (optional): Slarti's `HEARTBEAT` cron (every 30 min) will eventually post to `#admin-log`. You can also trigger it manually in `#garden-chat` with `!heartbeat` once COMMAND mode is live.

---

## Done Conditions

Phase 7 is complete when:

- [ ] `DISCORD_BOT_TOKEN` set in `.env` and as Windows env var
- [ ] `DISCORD_GUILD_ID` set in `.env` and as Windows env var
- [ ] `DISCORD_ADMIN_WEBHOOK` set in `.env`
- [ ] `config/discord_users.json` has Emily's and Christopher's real user IDs
- [ ] `~/.openclaw/openclaw.json` has the `channels` section uncommented
- [ ] Gateway restarted and shows Discord channel connected
- [ ] `@Slarti hello` in `#garden-chat` gets a response in character
- [ ] Git commit: `slarti: phase 7 discord bot connected`

---

## Troubleshooting

**Bot doesn't respond:**
- Check that Message Content Intent is enabled in the Discord Developer Portal
- Verify the bot was invited with correct permissions
- Check `openclaw gateway status` for connection errors

**`${DISCORD_BOT_TOKEN}` not resolved:**
- Run `setx DISCORD_BOT_TOKEN "..."` in PowerShell (not WSL2)
- Restart your terminal/PowerShell session after `setx`
- Restart the gateway after

**Bot online but won't respond to messages:**
- Ensure `mentionPatterns: ["@Slarti", "@slarti"]` is in openclaw.json
- In group channels, Slarti only responds when @mentioned (per `groupChat` config)
- In DMs, Slarti responds to all messages

**Wrong user identified (emily/christopher mix-up):**
- Double-check the user IDs in `config/discord_users.json`
- User IDs are 18-digit numbers — confirm by right-clicking the username with Developer Mode enabled
