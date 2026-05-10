# psamvault-mcp

MCP server for [psamvault](https://pypi.org/project/psamvault/) — lets AI agents
use your stored credentials without ever seeing their plaintext values.

## How it works

psamvault exposes two complementary flows depending on what the agent needs.

### API request flow (`use_credential`)

When an AI agent needs to call an API on your behalf, psamvault decrypts the
credential locally and forwards the authenticated request through its backend proxy.

**The agent never sees the password.** It only sees the HTTP response.

```
Agent: "Call the GitHub API using my stored credential"
         ↓
psamvault shows a consent dialog: "Allow agent to use github.com credential?"
         ↓ (you approve)
psamvault decrypts credential locally using your Vault Encryption Key
         ↓
psamvault makes: GET https://api.github.com/user
                 Authorization: Bearer <your token>
         ↓
Agent receives: {"login": "yourusername", "id": 12345, ...}
```

### Browser login flow (`browser_login`)

When an AI agent needs to log you into a website, psamvault opens a real
Chromium browser, navigates to the site, and fills in the credentials directly
inside that browser process.

**The agent never sees the credentials.** It only sees whether the login succeeded.

```
Agent: "Log me into kaggle.com"
         ↓
psamvault opens Chromium → navigates to kaggle.com → finds the login page
         ↓
psamvault takes a screenshot of the confirmed login page
         ↓
psamvault shows a consent dialog with the confirmed login URL
         ↓ (you approve)
psamvault decrypts credential locally
         ↓
psamvault fills username + password fields directly in the browser
         ↓
Agent receives: {"success": true, "steps_completed": [...], "url": "..."}
         ↓
Browser stays open — you take over from there
```

## Prerequisites

- Python ≥ 3.11
- [psamvault](https://pypi.org/project/psamvault/) installed and logged in

```bash
pip install psamvault
psamvault configure
psamvault login
```

## Installation

```bash
pipx install psamvault-mcp
playwright install chromium
```

### MCP client setup

Register the server in your MCP client's configuration.

**Claude Desktop** — config file location:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "psamvault": {
      "command": "psamvault-mcp"
    }
  }
}
```

Restart your MCP client after saving.


## Configuration

psamvault-mcp reads its backend URL from `~/.psamvault/config.env`, written
automatically by `psamvault configure`.

| Variable | Default | Description |
|---|---|---|
| `PSAMVAULT_API_URL` | `https://psam-vault-backend.onrender.com` | psamvault backend endpoint |

To point at a self-hosted backend, set the variable in `~/.psamvault/config.env`:

```
PSAMVAULT_API_URL=https://your-backend.example.com
```

## Available tools

| Tool | Description |
|---|---|
| `list_vault_sites` | List stored site names (no passwords) |
| `check_credential_exists` | Check if a credential exists for a site |
| `use_credential` | Make an authenticated HTTP request |
| `get_username_for_site` | Get username only (not password) |
| `browser_login` | Open a real browser and log into a website — credentials filled silently, never shown to the agent |

## Injection modes

| Mode | Header format | Use case |
|---|---|---|
| `bearer_token` | `Authorization: Bearer <password>` | GitHub, OpenAI, most APIs |
| `api_key_header` | `<custom-header>: <password>` | APIs with X-API-Key headers |
| `basic_auth` | `Authorization: Basic base64(user:pass)` | HTTP basic auth |

## Example agent prompts

Once connected, you can ask your agent things like:

- *"What credentials do I have stored in my vault?"*
- *"Check my GitHub notifications using my stored github.com credential"*
- *"List my AWS S3 buckets using my stored aws credential"*
- *"Log me into kaggle.com"*
- *"Open github.com and log me in"*

## Security

- Credentials are decrypted locally on your machine — never sent to the agent
- Every credential use requires explicit approval via a consent dialog
- The agent only receives HTTP responses, never credential values
- All communication with the psamvault backend uses HTTPS