# psamvault-mcp

MCP server for [psamvault](https://pypi.org/project/psamvault/) — lets AI agents
use your stored credentials without ever seeing their plaintext values.

## How it works

psamvault provides two complementary flows depending on what the agent needs.

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
psamvault shows a consent dialog with the confirmed login URL
         ↓ (you approve)
psamvault decrypts credential locally
         ↓
psamvault fills username + password fields directly in the browser
         ↓
If a CAPTCHA appears, psamvault takes a screenshot, pauses automation,
and tells you to solve the CAPTCHA and click Sign in manually
         ↓
Agent receives:
         {
           "success": true,
           "message": "Logged in to github.com successfully. The browser is open.",
           "steps_count": 8,
           "url": "https://github.com/dashboard",
           "captcha_detected": false,
           "captcha_screenshot": null,
           "failed_at": null,
           "hint": null
         }
         ↓
Browser stays open — you take over from there.
The browser session is saved and reused on subsequent calls to the same site.
```

## Prerequisites

- Python ≥ 3.11
- [psamvault](https://pypi.org/project/psamvault/) installed and logged in

```bash
pipx install psamvault
psamvault configure
psamvault login
```

- Playwright Chromium browser

```bash
playwright install chromium
```

## Installation

```bash
pipx install psamvault-mcp
```

## Transport modes

psamvault-mcp supports two transport modes. Use the one that matches your agent.

### stdio (default — for Goose, Claude Desktop, Cline)

```bash
psamvault-mcp
```

Starts the MCP server over stdin/stdout. Most desktop MCP clients use this mode.

### HTTP/SSE (for Hermes, custom clients, or network-accessible setups)

```bash
psamvault-mcp --http --port 8433
```

Starts an HTTP server with Server-Sent Events (SSE) transport.

| Option | Default | Description |
|--------|---------|-------------|
| `--http` | off | Enable HTTP/SSE transport |
| `--port` | `8433` | HTTP server port |
| `--host` | `127.0.0.1` | HTTP server bind address |

### Goose setup

#### Option A — One-click deeplink

Click or paste this URL into your browser while Goose Desktop is running:

```
goose://extension?cmd=psamvault-mcp&timeout=300&id=psamvault&name=psamVault&description=Use%20stored%20credentials%20without%20exposing%20them%20to%20the%20agent
```

Goose will prompt you to confirm, then the extension is added instantly.

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

#### Verifying the extension works

Once added, start a Goose session and try:

```
What credentials do I have stored in my vault?
```

Goose will call `list_vault_sites` via psamvault-mcp. If you see your stored sites, everything is working.

### Hermes setup

Connect to psamvault-mcp via its HTTP/SSE transport. Add this block to
`~/.hermes/config.yaml` under `mcp_servers`:

```yaml
mcp_servers:
  psamvault:
    url: "http://127.0.0.1:8433/sse"
    enabled: true
```

Then start the server in a terminal:

```bash
psamvault-mcp --http --port 8433
```

Restart or reload Hermes — the tools will be discovered automatically.

### Claude Desktop setup

Config file location:
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

Restart Claude Desktop after saving.

### Other MCP clients

Any MCP client supporting stdio transport can use psamvault-mcp:

```json
{
  "mcpServers": {
    "psamvault": {
      "command": "psamvault-mcp"
    }
  }
}
```

For HTTP/SSE support, point the client at `http://127.0.0.1:8433/sse`.

## Configuration

psamvault-mcp reads its backend URL from `~/.psamvault/config.env`, written
automatically by `psamvault configure`.

| Variable | Default | Description |
|---|---|---|
| `PSAMVAULT_API_URL` | `https://psam-vault-backend.onrender.com` | psamvault backend endpoint |
| `PSAMVAULT_LOG_LEVEL` | `INFO` | Log verbosity. Accepts any standard Python level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Logs go to stderr. |

To point at a self-hosted backend, set the variable in `~/.psamvault/config.env`:

```
PSAMVAULT_API_URL=https://your-backend.example.com
```

## Available tools

| Tool | Description |
|---|---|
| `get_version` | Return the installed psamvault-mcp version. No session or login required |
| `search_vault_tools` | Discover which tool to use — call this first; accepts a keyword or empty string for all tools |
| `list_vault_sites` | List stored site names (no passwords) |
| `check_credential_exists` | Check if a credential exists for a site |
| `get_username_for_site` | Get username only (not password) |
| `browser_login` | Open a real browser and log into a website — credentials filled silently, never shown to the agent |

## Architecture

The MCP server manages a single Playwright Chromium instance in-process.
No subprocess daemon is used — the browser lives in the same process as the
MCP server. If the browser crashes, it is automatically restarted on the
next `browser_login` call.

This eliminates the fragile 3-process chain (MCP → CLI daemon → browser)
that caused connection errors with certain MCP clients (e.g. Goose's
`ECONNREFUSED` on internal proxy ports).

## Example agent prompts

Once connected, you can ask your agent things like:

- *"What credentials do I have stored in my vault?"*
- *"Log me into kaggle.com"*
- *"Open github.com and log me in"*
- *"Check if I have a credential stored for z.ai"*

## Testing

```bash
# From the repo root
pytest
```

Tests live in `tests/` and cover crypto primitives, session management, consent
logic, the API client (with httpx mocking), and MCP tool behaviour. The test suite
requires no real network access or OS keychain — all external dependencies are mocked.

## Security

- Credentials are decrypted locally on your machine — never sent to the agent
- Every credential use requires explicit approval via a consent dialog
- The agent only receives HTTP responses, never credential values
- All communication with the psamvault backend uses HTTPS
- The browser is managed in-process — no subprocess daemon or internal HTTP proxy

## License

MIT — see [LICENSE](LICENSE).
