---
name: general-rules
description: Security rules, error handling guidance, and complete tool reference for psamvault
type: agent-skill
---

# General rules for using psamvault

## Security rules — these are not suggestions
These rules are security requirements. Violating them could expose credentials.

### Rule 1: No shell commands for credentials
Never run `psamvault get`, `psamvault show`, `psamvault open`,
or any psamvault CLI command. Do not read credential files from the filesystem.
The only permitted way to access credentials is through the MCP tools.

### Rule 2: browser_login for all website login requests
Use `browser_login` for: logging in, signing in, authenticating, signing on,
accessing an account, or entering credentials for a website.
This includes trigger phrases like "log into", "log in to", "sign into",
"sign in to", "sign on to", "authenticate to", "access my account on",
"enter my password for", "fill in credentials for", or "login to".

### Rule 3: use_credential for API requests
Use `use_credential` for an authenticated HTTP request to that entry's own site
or the API key's service. It looks up API key entries first, then falls back to
vault (site password) entries if no API key is found. A site password is sent
only to the registrable domain of its site name or login URL, and to subdomains
of that domain. An API key is sent only to hosts its service already names (a
bundled verify recipe, or a hostname in the service, the service hint, or the
key name), or to a host a human has listed for the entry. Any other host is
refused, including a redirect that leaves an allowed host. An API key with no
known service host is refused until a human allows one. `target_url` must be an
absolute http or https URL. Loopback and private addresses stay blocked.

A human allows a host by editing `~/.psamvault/policy.json` (mode 0600) under
`entries["<entry>"].allow_hosts`. There is no MCP parameter that grants a host.
Relay the error. Do not edit the policy file, and do not send the secret to
another host to read it back.

The credential value is not a result field. The response body and headers are
scrubbed of the secret and its common encodings. Scrubbing is defence in depth.
The host check is the boundary.

Supported injection modes:
- `bearer_token` — Authorization: Bearer <key> (default)
- `api_key_header` — <header_name>: <key> (requires `header_name`)
- `basic_auth` — Authorization: Basic base64(<key>:<key>)

### Rule 4: Scan and protect existing projects
When working in a project directory that has `.env` files, call
`scan_and_protect()` to detect and encrypt any exposed secrets. This is a
one-time safety check before the agent reads any files in the project.

### Rule 5: Discover first, then act
Always call `search_vault_tools` first when you are unsure which tool to use.
If you are unsure which sites exist, call `list_vault_sites`.
If you are unsure whether a credential exists, call `check_credential_exists`.

### Rule 6: Credential values are never returned to you
- `browser_login` fills credentials inside a browser — you never see them.
- `use_credential` injects a credential into an HTTP request to that entry's own
  site or the key's service, and returns the response after scrubbing the secret
  out of the body and headers. Any other host is refused.
- `run_with_credential` injects a credential into a CLI subprocess. `http(s)` URLs
  in the command follow the same host rule. Stdout and stderr are scrubbed of the
  raw value, the reversed text, and the common encodings. That scrub is defence
  in depth: a command with no URL is not host-checked. Use it for a CLI that
  reads a token from the environment or stdin, not to call an arbitrary URL.
- `get_username_for_site` only returns the username, never the password.
- `list_vault_sites` only returns site names and username hints.
- `export_key_to_mcp_config` writes the key into an agent MCP config file —
  only a summary (paths, action, verification status) comes back to you.
- `export_key_to_env_file` writes the key into an agent `.env` as an
  environment variable — only a summary (path, line, action, backup) comes
  back to you, never the value.
- `verify_api_key` returns pass/fail + status only — never the key.

### Rule 7: Provision MCP servers with export_key_to_mcp_config
When an agent host needs an MCP server whose auth is a vault API key
(e.g. Render's hosted MCP), call
`export_key_to_mcp_config(key_name=..., server_name=..., url=...)` to write
the entry into `config.yaml` directly. Never ask the user to paste the key,
never hand-edit the config, and never run the agent host's own `mcp add`
CLI — psamvault owns the write (with backup + dry_run).

**Never export an unverified key.** `export_key_to_mcp_config` auto-verifies
HTTP exports against the provider's bundled recipe (or an explicit
`verify_url`) and refuses to write on failure (result: `verification:
failed`, config untouched). For providers without a recipe and for
stdio/env exports, verify manually via `verify_api_key` /
`use_credential` / `run_with_credential` and pass `skip_verify=true` only
after a real check — the result records `verification: skipped` loudly.
**A failed probe is final:** `skip_verify=true` never overrides a provider
rejecting the key (that is a definitive "invalid", not an unknown), so an
invalid key can never be written into a config or `.env`.

### Rule 8: Put tool credentials into the agent `.env` with export_key_to_env_file
When an agent *tool* (not an MCP server) needs a credential, it usually reads
a dotenv file at process start — Hermes' web tools read `TAVILY_API_KEY` from
`HERMES_HOME/.env`. Call
`export_key_to_env_file(key_name=..., env_var_name=...)` instead of asking
the user to paste the value or hand-editing the file. The write is idempotent
(the variable's line is updated in place, so re-runs cannot leave duplicate
keys that different loaders resolve differently) and a timestamped backup is
taken first. Pass `env_path` for a host without a verified default location —
**never** invent a path: only `agent="hermes"` has a verified `.env`, and an
unknown host is rejected on purpose. Restart the host session afterwards (the
`.env` is read at startup) and verify with the consumer, not the file.

## Error handling

### "Not logged in" / session timed out
The session file or keychain entry is missing or expired. Tell the user:
"Please run `psamvault login` in your terminal, then ask me again."
Do not try to refresh the session by reading vault files or running
`psamvault get`. Optionally they can try `psamvault list` / `psamvault whoami`
first; if those fail, login is required.

### MCP tools missing or server will not start
Do not invent shell workarounds that print secrets. Follow the install playbook:
`docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md`.

Typical causes and fixes:
- **Missing binary in config** → `pipx install psamvault-mcp`; point config at the absolute path under `~/.local/bin/`
- **Broken system Python shim on PATH** (`ModuleNotFoundError: pydantic`) → use absolute pipx path; do not prefer `Python3xx\Scripts\psamvault-mcp.exe`
- **Corrupt pipx metadata** → delete the venv `pipx environment --value PIPX_LOCAL_VENVS` names (`…/psamvault-mcp`) and reinstall. Do not hardcode `~/.local/pipx/venvs`
- **PYTHONPATH contamination** → set `env.PYTHONPATH=""` on the MCP server entry (see `docs/troubleshooting/PYTHONPATH-CONFLICT.md`). On Linux the server adopts `/run/user/<uid>/bus` for Secret Service; pass `DBUS_SESSION_BUS_ADDRESS` only when that socket is not visible
- **`✗` keyring error** → the OS keyring is missing or locked. Relay the message. Do not store the secret in a file. `--version` and `--help` do not need a keyring
- **Config fixed but tools still absent** → restart/reload the agent session so MCP is rediscovered
- **Smoke-test hang** → `psamvault-mcp` without flags waits on stdio; use `--version` or `--help` instead

### "Consent GUI unavailable"
The user is on a headless system without a graphical display.
They need to run the command in a GUI environment.

### CAPTCHA during browser_login
1. Inform the user a CAPTCHA was detected.
2. If a `captcha_screenshot` path is provided, tell them to inspect it.
3. Ask them to solve the CAPTCHA in the open browser window.
4. Ask them to click the Sign in / Login button manually after solving it.

### Browser timeout
If `browser_login` times out (600 seconds), the browser may be stuck on
a slow or JavaScript-heavy page. Suggest retrying with a higher `timeout_ms`
or checking for network issues.

### Unknown site
If `check_credential_exists` returns `exists: false`, the site isn't in the vault.
The user must add it via `psamvault add` before you can use it.

### scan_and_protect returns nothing found
If `scan_and_protect` returns 0 secrets found, the project is clean —
no action needed. If it returns `files_not_gitignored`, suggest the user
add `.env` to their `.gitignore`.

## Tool reference

| Tool | Purpose | When to call |
|------|---------|--------------|
| `get_version` | Check psamvault-mcp version | Anytime — no login needed |
| `search_vault_tools` | Discover which tool to use | **First** — when unsure what tool fits |
| `list_vault_sites` | List stored sites with hints | When user asks "what do I have" |
| `list_api_keys` | List stored API key names | When user asks "what API keys do I have" |
| `check_credential_exists` | Check if a site has a credential | Before any credential-dependent tool |
| `get_username_for_site` | Get username only | When username is needed in a form/API |
| `browser_login` | Full login via browser | **Always** for login/authenticate requests |
| `use_credential` | Authenticated HTTP to the entry's site or the key's service | When that host is the one you are calling. Other hosts are refused |
| `scan_and_protect` | Scan .env files for exposed secrets | **First** when working in a project with .env files |
| `run_with_credential` | CLI with the credential in env or stdin; URLs in the command are host-checked; output scrubbed | twine, docker login, npm, git, pip — not an arbitrary HTTP call |
| `export_key_to_mcp_config` | Write a vault key into an agent MCP config (auto-verifies) | When an agent host needs an MCP server authenticated by a vault key |
| `export_key_to_env_file` | Write a vault key into an agent `.env` as an env var (in place, backed up) | When an agent **tool** reads its credential from a dotenv file |
| `verify_api_key` | Probe a stored key against its provider | Before exporting, or to prove a key still works |
