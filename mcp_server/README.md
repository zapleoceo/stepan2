# Stepan MCP connector

Drive the Stepan lead funnel from an MCP client (Claude Desktop, etc.) by phone number.

```
Claude Desktop  ──stdio──▶  stepan_mcp.py  ──HTTPS + Bearer──▶  stepan2.zapleo.com/mcp/*
```

The MCP server runs **locally** on your machine (Claude Desktop spawns it). It never
touches the database directly — it calls Stepan's authenticated `/mcp` HTTP API.

## Tools

| Tool | What it does |
|------|--------------|
| `find_lead(phone)` | Look up a lead by phone; returns id, name, IG username, stage, bot on/off |
| `close_deal(phone, note?)` | Deal won → hand off, stop the bot |
| `call_failed(phone, note?)` | Call didn't connect → journal it, re-arm the bot, Stepan messages the lead to continue in chat |
| `move_lead(phone, stage, note?)` | Set an explicit funnel stage (`new`…`manager`) |

Leads are addressed by **phone in E.164** (`+6281234567890`). Spacing/dashes are tolerated.

## Server setup (one-time, on Hetzner)

Set the shared secret in the server env and redeploy:

```bash
# generate a long random token
openssl rand -hex 32
```

Add to Stepan's `.env` (never commit it):

```
STEPAN2_MCP_SECRET=<the token>
```

Redeploy (git push → GitHub Actions). Until this is set, `/mcp/*` returns `403`.

## Client setup (your machine)

```bash
cd mcp_server
python -m venv .venv && . .venv/bin/activate   # or your preferred env
pip install -r requirements.txt
```

Add to Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "stepan": {
      "command": "python",
      "args": ["/absolute/path/to/mcp_server/stepan_mcp.py"],
      "env": {
        "STEPAN2_MCP_URL": "https://stepan2.zapleo.com",
        "STEPAN2_MCP_SECRET": "<the same token>"
      }
    }
  }
}
```

Restart Claude Desktop. The four tools appear under the **stepan** server.

## Quick check (without a client)

```bash
curl -s "https://stepan2.zapleo.com/mcp/find_lead?phone=%2B6281234567890" \
     -H "Authorization: Bearer $STEPAN2_MCP_SECRET"
```
