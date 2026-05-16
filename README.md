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
If a CAPTCHA appears, psamvault takes a screenshot, pauses automation,
and tells you to solve the CAPTCHA and click Sign in manually
         ↓
Agent receives: {"success": true, "steps_count": 8, "url": "..."}
         ↓
Browser stays open — you take over from there
```

## Prerequisites

- Python ≥ 3.11
- [psamvault](https://pypi.org/project/psamvault/) installed and logged in

```bash
pipx install psamvault
psamvault configure
psamvault login
```

## Installation

```bash
pipx install psamvault-mcp
playwright install chromium
```

### Goose setup (recommended)

[Goose](https://goose-docs.ai) is an open-source AI agent with native MCP support. There are three ways to add psamvault-mcp as a Goose extension:

---

#### Option A — One-click deeplink

Click or paste this URL into your browser while Goose Desktop is running:

```
goose://extension?cmd=psamvault-mcp&timeout=300&id=psamvault&name=psamVault&description=Use%20stored%20credentials%20without%20exposing%20them%20to%20the%20agent
```

Goose will prompt you to confirm, then the extension is added instantly.

---

#### Option B — Goose Desktop UI

1. Open Goose Desktop.
2. Click the **sidebar button** (top-left) → **Extensions**.
3. Click **Add custom extension**.
4. Fill in the form:

   | Field | Value |
   |---|---|
   | **Type** | `Standard IO` |
   | **ID** | `psamvault` |
   | **Name** | `psamVault` |
   | **Description** | `Use stored credentials without exposing them to the agent` |
   | **Command** | `psamvault-mcp` |
   | **Timeout** | `300` |

5. Click **Add**.

The extension appears in your Extensions list — toggle it on to activate it.

---

#### Option C — Config file (advanced)

Edit `~/.config/goose/config.yaml` and add the following under `extensions:`:

```yaml
extensions:
  psamvault:
    name: psamVault
    cmd: psamvault-mcp
    args: []
    enabled: true
    type: stdio
    timeout: 300
```

Save the file and restart Goose (or reload the session).

---

#### Verifying the extension works

Once added, start a Goose session and try:

```
What credentials do I have stored in my vault?
```

Goose will call `list_vault_sites` via psamvault-mcp. If you see your stored sites, everything is working.

---

### Other MCP clients

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
| `search_vault_tools` | Discover which tool to use — call this first; accepts a keyword or empty string for all tools |
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