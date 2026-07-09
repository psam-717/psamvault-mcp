# psamvault-mcp — Agent Instructions

You have the **psamvault MCP server** available. It provides credential management tools that keep secrets out of your context window.

Prompt templates are available via the MCP Prompts capability (`list_prompts` / `get_prompt`) — load these at runtime for detailed step-by-step guidance on each workflow.

## If the MCP server is NOT connected yet

Do **not** improvise with `pip install` into system Python or bare `psamvault get`. Follow this playbook:

**Full guide:** [docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md](docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md)

### Short install playbook

1. **Install with pipx only:** `pipx install psamvault-mcp`
2. If pipx reports **missing metadata** or uninstall fails, delete the broken venv under `~/pipx/venvs/psamvault-mcp` (Windows: `%USERPROFILE%\pipx\venvs\psamvault-mcp`) and reinstall.
3. **Resolve the absolute path** to the pipx binary (`~/.local/bin/psamvault-mcp` or `%USERPROFILE%\.local\bin\psamvault-mcp.exe`). If PATH has multiple copies, **ignore** system `Python3xx\Scripts\` shims — they often lack deps (`ModuleNotFoundError: pydantic`).
4. Write host config with that **absolute path** and `env: { PYTHONPATH: "" }` (avoids Hermes/global PYTHONPATH contamination).
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
| `how-to-capture-stripe` | Guide for capturing Stripe Projects credentials |
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
| `use_credential` | Make authenticated HTTP request — only the response is returned |
| `run_with_credential` | Run a CLI command with credential injected via env var or stdin |
| `scan_and_protect` | Encrypt project `.env` secrets into psamvault |
| `capture_stripe_credentials` | Capture credentials from Stripe Projects |

## Key Patterns

**Discovery:** `search_vault_tools("login")` → shows tools relevant to login workflows.

**Browser login:** `browser_login(site_name="github.com")` → relay result message to user. If CAPTCHA detected, tell user to solve it in browser.

**API call:** `use_credential(site_name="github.com", target_url=..., inject_as="bearer_token")` → use `fields` parameter to return only needed data.

**Run a command with a credential:**
`run_with_credential(site_name="pypi", command="twine upload dist/*", inject_as="env", env_var_name="TWINE_PASSWORD")`
→ Use for twine upload, docker login, npm publish, git push, or any CLI tool that needs an API key or password. The credential value is **redacted** from all output returned to you.

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
