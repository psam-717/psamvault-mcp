# psamvault-mcp — Agent Instructions

You have the **psamvault MCP server** available. It provides credential management tools that keep secrets out of your context window.

Prompt templates are available via the MCP Prompts capability (`list_prompts` / `get_prompt`) — load these at runtime for detailed step-by-step guidance on each workflow.

## If the MCP server is NOT connected yet

Do **not** improvise with `pip install` into system Python or bare `psamvault get`. Follow this playbook:

**Full guide:** [docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md](docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md)

### Short install playbook

1. **Install with pipx only:** `pipx install psamvault-mcp`
2. If pipx reports **missing metadata** or uninstall fails, delete the broken venv. Ask pipx where it is: `pipx environment --value PIPX_LOCAL_VENVS` (current pipx: `~/.local/share/pipx/venvs` on Linux, `~/Library/Application Support/pipx/venvs` on macOS, `%LOCALAPPDATA%\pipx\pipx\venvs` on Windows). Then reinstall.
3. **Resolve the absolute path** to the pipx binary (`~/.local/bin/psamvault-mcp` or `%USERPROFILE%\.local\bin\psamvault-mcp.exe`). If PATH has multiple copies, **ignore** system `Python3xx\Scripts\` shims — they often lack deps (`ModuleNotFoundError: pydantic`).
4. Write host config with that **absolute path** and `env: { PYTHONPATH: "" }` (avoids Hermes/global PYTHONPATH contamination). On Linux the server adopts `/run/user/<uid>/bus` when that socket belongs to this user; if it is not visible, also pass `DBUS_SESSION_BUS_ADDRESS`. macOS and Windows need no extra variable. See [OS keyring](docs/reference/configuration.md#os-keyring).
5. **Reload the host** (restart session / refresh MCP). Mid-session config edits often do not expose tools until reload.
6. Smoke-test the binary with `psamvault-mcp --version` or `psamvault-mcp --help` (safe — print and exit). Do **not** leave a bare `psamvault-mcp` process running in a shell — it waits on stdio for MCP.
7. Ask the user to run `psamvault login` if vault tools report session timeout / not logged in.
8. Verify: `get_version` → `list_vault_sites`.

Also see [docs/troubleshooting/PYTHONPATH-CONFLICT.md](docs/troubleshooting/PYTHONPATH-CONFLICT.md) for import errors involving `pydantic_core`.

## Quick Reference

1. **Always start with:** call a tool from **🛠 Entry & Orientation** — `search_vault_tools("")` to discover available tools or `get_version()` to check the version
2. **🔐 Site Authentication workflow:** `list_vault_sites()` → `check_credential_exists(site_name)` → `browser_login(site_name)`
3. **🔑 API Key Operations workflow:** `list_api_keys()` → `use_credential(site_name, ...)` or `run_with_credential(site_name, ...)`
4. **Use MCP tools, never raw credential access** — credentials never enter your context

## Available Loadable Prompts

| Prompt | Description |
|--------|-------------|
| `how-to-login` | Step-by-step guide for logging into a website |
| `how-to-discover-creds` | Guide for discovering what credentials are stored |
| `how-to-get-username` | Guide for retrieving a stored username |
| `how-to-use-api-credential` | Guide for making authenticated HTTP requests |
| `how-to-scan-and-protect` | Guide for scanning and protecting `.env` secrets |
| `how-to-export-key-to-mcp-config` | Guide for exporting a vault key into an agent's MCP config |
| `general-rules` | Security rules, error handling, and full tool reference |

Call `get_prompt("how-to-login")` to load the login workflow into your context when needed.

## Available Tools

Tools are grouped by purpose so AI agents navigate them efficiently:

### 🛠  Entry & Orientation
*Always start here to discover what tool to use.*

| Tool | Description |
|---|---|
| `search_vault_tools` | Discover which tool fits your task — call this first |
| `get_version` | Return the installed psamvault-mcp version |

### 🔐  Site Authentication
*End-to-end: discover, check, and log into websites.*

| Tool | Description |
|---|---|
| `list_vault_sites` | Show stored credential sites (names + username hints) |
| `check_credential_exists` | Check if a site's credential is stored |
| `get_username_for_site` | Get username hint (never password) |
| `browser_login` | Open browser, fill credentials — agent never sees them |

### 🔑  API Key Operations
*All tools that deal with API keys — discover, use, inject, and protect.*

| Tool | Description |
|---|---|
| `list_api_keys` | List stored API key names (never key values) — optionally filter by project |
| `use_credential` | Authenticated HTTP to the entry's own site or the API key's service. Other hosts, including redirects, are refused. The response is scrubbed; that scrub is defence in depth |
| `run_with_credential` | CLI with the credential in env or stdin. `http(s)` URLs in the command follow the same host rule. Output is scrubbed of the secret and its common encodings |
| `scan_and_protect` | Encrypt project `.env` secrets into psamvault |
| `export_key_to_mcp_config` | Export a vault API key into an agent's MCP config (Hermes) — auto-verifies, key never returned |
| `export_key_to_env_file` | Export a vault API key into an agent's `.env` as an env var (default `HERMES_HOME/.env`) — updates in place, backs up, auto-verifies |
| `verify_api_key` | Verify a stored vault key is valid (pass/fail + status) |

## Key Patterns

**Discovery:** `search_vault_tools("login")` → shows tools relevant to login workflows.

**Browser login:** `browser_login(site_name="github.com")` → relay result message to user. If CAPTCHA detected, tell user to solve it in browser.

**API call:** `use_credential(site_name="github.com", target_url="https://api.github.com/user/repos", inject_as="bearer_token")` → use `fields` to return only needed data. `target_url` must be an absolute http or https URL on that entry's site or the key's service. A site named `github.com`, or a key whose name or service matches the bundled `github` recipe, may call `api.github.com`. A call to any other host is refused. An API key with no known service host stays refused until a human adds it under `entries["<name>"].allow_hosts` in `~/.psamvault/policy.json`. There is no MCP parameter that grants a host. Do not edit that file. The response is scrubbed of the secret; the host check is the boundary.

**Run a command with a credential:**
`run_with_credential(site_name="pypi", command="twine upload dist/*", inject_as="env", env_var_name="TWINE_PASSWORD")`
→ Use for twine upload, docker login, npm publish, git push, or any CLI that reads a token from the environment or stdin. Every `http(s)` URL in the command is host-checked the same way; a command with no URL is not. Stdout and stderr are scrubbed of the raw secret, the reversed text, and the common encodings. That scrub is defence in depth.

**Give a tool a credential without pasting it** (most tools read a dotenv file, not MCP config):
`export_key_to_env_file(key_name="tavily", env_var_name="TAVILY_API_KEY")` → writes `HERMES_HOME/.env`,
updates in place on re-runs, returns `action` / `line` / `backup_path` (never the value). Restart the
host session so its tools pick the variable up, then verify with the consumer (e.g. a real
`web_extract` call), not by reading the file.

**Version lockstep:** `get_version()` returns a `compatibility` block (skill floor, newest release,
`breaking_pending`). If it disagrees with the skill you loaded, run `psamvault-mcp compat --check` — never
hand-edit `compatibility.json` or the skill frontmatter to make a check pass. The recorded skill version
is a **floor**: a newer skill is fine, and a skill-only improvement ships with
`psamvault-mcp compat --sync-skill` (no MCP release needed) when you already have a local skill checkout. A missing Hermes skill is not drift — `doctor` and `compat --check` exit 0 for that on an otherwise healthy install. Public procedures are in [docs/skills/](docs/skills/README.md). On Windows the shim is `psamvault-mcp.exe`.

**Protect secrets:** `scan_and_protect(project_dir="/path")` → recommend `pip install pv-dotenv` for runtime resolution.

## Security Rules (MANDATORY)

- ❌ Never ask user to paste credentials into chat
- ❌ Never print credential values
- ❌ Never read `~/.psamvault/` files directly
- ❌ Never run psamvault CLI commands (`psamvault get`, `psamvault show`, etc.)
- ❌ Never install psamvault-mcp with system `pip` “just to make PATH work” when pipx is available
- ✅ Always use MCP tools for anything credential-related
- ✅ Prefer absolute pipx path + `PYTHONPATH=""` in MCP host config
- ✅ If tools are missing after config change, reload/restart the host session before claiming install failed

## Example Workflows

### "What's in my vault?"
```
list_vault_sites() → shows names
get_username_for_site("github.com") → shows username
```

### "What API keys do I have?"
```
list_api_keys() → shows all API key names
list_api_keys(project_name="my-project") → shows keys for a specific project
```

### "Log me into Kaggle"
```
check_credential_exists("kaggle.com")
browser_login(site_name="kaggle.com")
```

### "Get my GitHub repos"
```
use_credential("github.com", "https://api.github.com/user/repos", "bearer_token", fields=["name","html_url"])
```

### "Publish to PyPI"
```
run_with_credential(site_name="pypi", command="twine upload dist/*", inject_as="env", env_var_name="TWINE_PASSWORD")
→ Returns exit_code, stdout, stderr (credential redacted)
```

### "Secure my project"
```
scan_and_protect("/home/user/my-project")
→ "Install pv-dotenv and your app resolves secrets at runtime"
```

### "Am I up to date, or is my install broken?"

```bash
psamvault-mcp selfcheck        # installed vs what a NEW session actually serves (exit 1 on mismatch)
psamvault-mcp doctor           # entry points pipx never linked, stale pipx records, processes holding the venv
psamvault-mcp doctor --fix     # repair the above (a stale pipx record is corrected in place; relinking an
                               # entry point still wants no sessions running)
psamvault-mcp compat --check   # the MCP <-> usage-skill pairing
psamvault-mcp compat --apply --latest   # upgrade to the newest release on PyPI (add --allow-breaking
                                        # when it is newer than the installed contract, or removes a tool)
```

`selfcheck` is the one that catches a long-lived session still serving an older build; `doctor` covers the
half-linked install where `pipx` never put the console script on `PATH`.
