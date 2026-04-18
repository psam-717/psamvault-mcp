# psamvault-mcp

MCP server for [psamvault](https://pypi.org/project/psamvault/) — lets AI agents
use your stored credentials without ever seeing their plaintext values.

## How it works

When an AI agent needs to authenticate with a service (GitHub, OpenAI, AWS, etc.),
it calls psamvault's MCP tools. psamvault fetches the credential, shows you a
consent prompt, and makes the authenticated request on the agent's behalf.

**The agent never sees the password.** It only sees the HTTP response.

```
Agent: "Call the GitHub API using my stored credential"
         ↓
psamvault shows you: "Agent wants to use github.com credential → Allow? [y/N]"
         ↓ (you approve)
psamvault fetches + decrypts credential locally
         ↓
psamvault makes: GET https://api.github.com/user
                 Authorization: Bearer <your token>
         ↓
Agent receives: {"login": "yourusername", "id": 12345, ...}
```

## Prerequisites

You must have [psamvault](https://pypi.org/project/psamvault/) installed and
be logged in before using this MCP server.

```bash
pip install psamvault
psamvault configure
psamvault login
```

## Installation

```bash
pipx install psamvault-mcp
```


## Available tools

| Tool | Description |
|---|---|
| `list_vault_sites` | List stored site names (no passwords) |
| `check_credential_exists` | Check if a credential exists for a site |
| `use_credential` | Make an authenticated HTTP request |
| `get_username_for_site` | Get username only (not password) |

## Injection modes

| Mode | Header format | Use case |
|---|---|---|
| `bearer_token` | `Authorization: Bearer <password>` | GitHub, OpenAI, most APIs |
| `api_key_header` | `<custom-header>: <password>` | APIs with X-API-Key headers |
| `basic_auth` | `Authorization: Basic base64(user:pass)` | HTTP basic auth |

## Example agent prompts

Once connected, you can ask Claude things like:

- *"Check my GitHub notifications using my stored github.com credential"*
- *"List my AWS S3 buckets using my stored aws credential"*
- *"What credentials do I have stored in my vault?"*

## Security

- Credentials are decrypted locally on your machine — never sent to the agent
- Every credential use requires explicit approval in your terminal
- The agent only receives HTTP responses, never credential values
- All communication with the psamvault backend uses HTTPS