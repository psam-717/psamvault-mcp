---
title: "Skill — use the psamvault tools"
description: Agent-followable skill for using psamvault-mcp — which tool for which job, the workflows, and the rules that do not bend.
order: 150
name: psamvault-mcp-usage
version: 1.10.0
---

# Skill: use psamvault-mcp

**Load this when** the user asks you to use a stored credential: log into a website, call an API with a
key, run a CLI command with a token, inject a secret into a process, protect a project's `.env`, or list
what credentials exist.

**Definition of done.** The operation actually ran — you can name its result (a login that succeeded, an
HTTP status, an exit code) — and **no secret appears in your output, your files, or the transcript**.

## What the server gives you
<!-- section:usage/overview -->


A zero-knowledge vault for agents: the credential is decrypted server-side and used where it is needed, so
it never enters your context window. Three flows cover almost everything:

| Flow | Tools | What happens |
|---|---|---|
| Log into a website | `browser_login` | A real Chromium browser opens and the credential is filled in **in the browser process**. You learn only whether the login succeeded. |
| Make an authenticated request | `use_credential`, `run_with_credential` | The credential is injected into an HTTP request or a CLI command's environment — into the header or the env var, never into your output. |
| Secure a project's secrets | `scan_and_protect` | `.env` files are scanned, secrets encrypted into the vault, and the plaintext replaced with `psamvault:KEY_NAME` placeholders. |

**Never** read `~/.psamvault/` files, read the keychain, or ask the user to paste a credential instead.
Those defeat the point, and the tools are the only supported path.

<!-- /section:usage/overview -->
## When to use this
<!-- section:usage/when-to-use -->
(synced from the installed skill)
<!-- /section:usage/when-to-use -->


## Orientation — always first

Neither of these needs credential access, so start here when you are unsure which tool to use:

| Tool | Parameters | What it does |
|---|---|---|
| `get_version` | — | The installed version, plus its compatibility block |
| `search_vault_tools` | `query: str` | Which tool to use for a job (`""` returns the full list) |

## The tool catalogue
<!-- section:usage/tools -->


The authoritative catalogue — every parameter and every returned field — is
[Tool reference](../reference/tools.md). The short form, by purpose:

### Site authentication

| Tool | Parameters | What it does | Security |
|---|---|---|---|
| `list_vault_sites` | — | Stored sites, with username hints | Names only, no passwords |
| `check_credential_exists` | `site_name` | Whether a credential exists | Boolean + username hint |
| `get_username_for_site` | `site_name` | The stored username | Username only, never the password |
| `browser_login` | `site_name, login_url, selectors, timeout_ms` | Log into a site in a real browser | Fills directly in the browser process |

### API keys

| Tool | Parameters | What it does | Security |
|---|---|---|---|
| `list_api_keys` | `project_name` (optional) | Stored key names, with service hints | Names only, no values |
| `use_credential` | `site_name, target_url, method, inject_as, fields, header_name, body, extra_headers` | Authenticated HTTP request | Credential stays server-side |
| `run_with_credential` | `site_name, command, inject_as, env_var_name, extra_env, workdir, timeout` | Run a CLI command with the credential injected | Output redacted of the credential |
| `scan_and_protect` | `project_dir, patterns, project_name` | Scan and encrypt `.env` secrets | Encrypts into the vault |
| `export_key_to_mcp_config` | `key_name, server_name, url/command, inject_as, replace, dry_run, config_path, verify_url, skip_verify` | Export a key into a host MCP config; auto-verifies HTTP keys | Key value never returned |
| `export_key_to_env_file` | `key_name, env_var_name, agent, env_path, dry_run, verify_url, skip_verify` | Export a key into an agent `.env` (updates in place, backs up first) | Key value never returned |
| `verify_api_key` | `key_name, verify_url` (optional) | Probe the provider's read-only endpoint | Key value never returned |

> **Invalid keys are never written.** A probe that can be attempted is always attempted, and a provider
> rejecting the key blocks the write — `skip_verify=true` cannot override it. It covers only providers that
> cannot be probed at all, and the result then records `verification: skipped`.

<!-- /section:usage/tools -->

## Workflows
<!-- section:usage/workflows -->

### 1. Browser Login

When the user asks "Log me into X":

1. **Check** the credential exists:
   ```
   check_credential_exists(site_name="github.com")
   ```
2. **Call** browser_login:
   ```
   browser_login(site_name="github.com")
   ```
3. **Relay** the result — always include the `message` field from the response verbatim to the user
4. **Handle CAPTCHA**: If `captcha_detected` is `true`, tell the user the browser is paused and they need to solve the CAPTCHA manually

### 2. API Credential Usage

When the user needs an authenticated API call:

1. **Check** the credential:
   ```
   check_credential_exists(site_name="github.com")
   ```
2. **Call** use_credential:
   ```
   use_credential(
       site_name="github.com",
       target_url="https://api.github.com/user",
       inject_as="bearer_token"
   )
   ```
3. **Use the `fields` parameter** to return only needed data (reduces tokens):
   ```
   use_credential(
       site_name="github.com",
       target_url="https://api.github.com/user/repos",
       inject_as="bearer_token",
       fields=["name", "html_url", "language"]
   )
   ```

Supported injection modes:
- `bearer_token` — `Authorization: Bearer ***`
- `api_key_header` — Custom header name with API key value
- `basic_auth` — `Authorization: Basic base64(user:pass)`

### 3. CLI Command with Credential (run_with_credential)

When the user needs to run a CLI tool that requires credentials:

1. **Check** what credential to use:
   ```
   list_api_keys()  # or list_vault_sites()
   ```
2. **Call** run_with_credential:
   ```
   # Publish to PyPI
   run_with_credential(
       site_name="pypi",
       command="twine upload dist/*",
       inject_as="env",
       env_var_name="TWINE_PASSWORD"
   )

   # Docker login
   run_with_credential(
       site_name="dockerhub",
       command="docker login -u myuser --password-stdin",
       inject_as="stdin"
   )

   # Push to private repo
   run_with_credential(
       site_name="github-api",
       command="git push origin main",
       inject_as="env",
       env_var_name="GITHUB_TOKEN"
   )

   # npm publish
   run_with_credential(
       site_name="npm",
       command="npm publish",
       inject_as="env",
       env_var_name="NPM_TOKEN"
   )
   ```

3. **The credential value NEVER enters your context** — all output is scanned and redacted before being returned.

Supported injection modes:
- `env` — Set credential as an environment variable. Requires `env_var_name`.
- `stdin` — Pipe credential as stdin to the process.

Auto-convenience: When `env_var_name="TWINE_PASSWORD"`, the tool automatically sets `TWINE_USERNAME=__token__`.

### 4. Protecting .env Files

When the user wants to secure project secrets:

1. **Call** scan_and_protect:
   ```
   scan_and_protect(project_dir="/path/to/project")
   ```
2. **For cleaner per-project organisation**, pass a `project_name`:
   ```
   scan_and_protect(project_dir="/path/to/project", project_name="myapp")
   ```
   Keys are stored as `myapp/.env/KEY_NAME` — filterable later with `list_api_keys(project_name="myapp")`.
   (**0.5.4 and later.** On 0.5.3 and earlier the server's router dropped `project_name`, so keys were
   stored under `env/` with or without the argument — upgrade before relying on the namespace.)
3. **Inform** the user what was found and encrypted
4. **Recommend** [pv-dotenv](https://pypi.org/project/pv-dotenv/) for runtime resolution:
   ```
   pip install pv-dotenv
   # Then replace:
   from dotenv import load_dotenv  # → from pv_dotenv import load_dotenv
   ```
5. **Nothing else needed** — the `.env` file now has `psamvault:KEY` placeholders and pv-dotenv resolves them at runtime

### 5. Vault Discovery

When the user asks "What credentials do I have?":

1. **Call** `list_vault_sites()` to see all stored credential sites
2. **Call** `list_api_keys()` to see all stored API keys
3. **For each site**, optionally `get_username_for_site(site_name)` to show usernames
4. Present the information as a clean list to the user

### 6. Exporting a Key into an Agent `.env`

When the user asks "put my Tavily key in the agent's .env" or "make <tool> see my key without pasting it":

1. **Call** `list_api_keys()` to get the exact vault key name
2. **Call** `export_key_to_env_file(key_name=..., env_var_name=...)` — writes into the target host's `.env`
   (default `agent="hermes"` → `HERMES_HOME/.env`; pass `env_path` for any other host)
3. **Read the result**: `action` (`added` / `updated` / `appended`), `line`, `backup_path` — never the key value
4. **Restart the agent host** (new session) so its tools pick the variable up — hosts read `.env` at process start
5. **Verify with the consumer, not the file** — e.g. `web_extract` for `TAVILY_API_KEY`, not by reading `.env`

Notes:

- The variable's line is **updated in place** on re-runs. Duplicate `KEY=` lines are a real hazard because different dotenv loaders pick different winners
- Providers with a bundled verify recipe are probed first; an unverifiable provider requires `skip_verify=true` or an explicit `verify_url`
- **An invalid key is never written.** A probe that CAN be attempted is always attempted, and a provider rejecting the key blocks the write (`verification: failed`) — `skip_verify=true` does not override it; it covers only providers that cannot be probed at all (then `verification: skipped`)
- The `.env` is plaintext by design: it is the agent's local working copy, never the source of truth

<!-- /section:usage/workflows -->
## The rules that do not bend
<!-- section:usage/security-rules -->


| Rule | Why |
|---|---|
| Use the MCP tools for every credential operation | Never read `~/.psamvault/` or the keychain directly |
| Never print a raw credential — not even to "check" it | Keeps secrets out of the context window, the transcript and any log |
| Never ask the user to paste a credential | `browser_login` / `use_credential` exist precisely for this |
| Prefer `fields` on large responses | Fewer tokens, less data, nothing sensitive echoed |
| Relay the `message` field verbatim | It contains the instructions the user needs |
| Classify every call before making it | Read-only and verification are always safe; exports get `dry_run` or a throwaway path; `browser_login` uses a real account, so confirm first; nothing irreversible without approval |

<!-- /section:usage/security-rules -->

## Common pitfalls
<!-- section:usage/pitfalls -->

1. **Forgetting to call `search_vault_tools("")` first** — always discover available tools before assuming what's available
2. **Not checking `captcha_detected`** — if a CAPTCHA appears, the browser pauses. You must inform the user to solve it and click Sign in.
3. **Calling wrong injection mode** — `api_key_header` needs a `header_name` parameter; `basic_auth` uses username:password; `bearer_token` uses the password as the token
4. **Not using `fields` on large API responses** — a full GitHub user response is ~40 fields. Use `fields=["login","id","public_repos"]` to return only what's needed.
5. **Exporting to an unknown agent without `env_path`** — only `hermes` has a verified default `.env` location. `agent="grok"` (or any host without a verified path) is rejected on purpose: guessing a location would write the secret where nothing reads it.
6. **Expecting an exported variable to appear mid-session** — the host reads `.env` at process start. Restart the session before testing the consumer, and verify with a real call rather than by reading the file.
7. **Expecting a duplicate on re-export** — `export_key_to_env_file` updates in place, so a second run returns `action: updated`. Two `KEY=` lines in the file means something else wrote them.
8. **Mixing up site name vs API key name** — `use_credential` checks API key entries first, then vault password entries. If you need an API key but stored it as a site password, it still works as fallback.
9. **Server not running** — MCP tools return connection errors if the server is not started. If using stdio, Hermes starts it automatically on connect. If using HTTP/SSE, start with `psamvault-mcp --http --port 8433` in a terminal.
10. **Token expiry** — The access token has ~1 hour lifetime. psamvault-mcp auto-refreshes via `api_client.py`, but if the server has been running for hours without use, the initial call might need a refresh cycle. The `api_client` handles 401 → refresh → retry automatically.

<!-- /section:usage/pitfalls -->
## Prompts you can suggest to the user
<!-- section:usage/agent-prompts -->

Tell users they can say things like:

- "What credentials do I have stored?"
- "Log me into github.com"
- "Check my GitHub profile info"
- "Get my top 10 starred repositories"
- "What API keys do I have stored?"
- "Upload my package to PyPI"
- "Docker login"
- "Push to my private repo"
- "Protect the secrets in my project directory"
- "Put my Tavily key into my agent's .env"
- "Export my Render key into my MCP config"
- "Check if I have a credential for z.ai"
- "What's my username for Kaggle?"

<!-- /section:usage/agent-prompts -->
## Verification checklist
<!-- section:usage/verification -->

- [ ] MCP server is connected (stdio, managed by Hermes automatically)
- [ ] `search_vault_tools("")` returns all 13 tools (v0.5.0+; `verify_api_key`, `export_key_to_mcp_config` and `export_key_to_env_file` present; `capture_stripe_credentials` REMOVED in v0.5.0)
- [ ] `get_version()` returns a version string, quoted from the server (not from memory or this file), and
      the reported `tool_surface_matches_newest` / `expected_tool_count` agree with the tool list above
- [ ] "Are you connected?" answered by calling the server (that plus one read-only list tool), never from
      `config.yaml` or recall
- [ ] `list_vault_sites()` returns stored sites (or empty list)
- [ ] `list_api_keys()` returns stored API keys (or empty list)
- [ ] `check_credential_exists("github.com")` returns expected result
- [ ] `use_credential` makes authenticated HTTP calls successfully
- [ ] `run_with_credential` runs CLI commands with credential injection
<!-- /section:usage/verification -->
## Architecture notes
<!-- section:usage/architecture -->
(synced from the installed skill)
<!-- /section:usage/architecture -->

## Related projects
<!-- section:usage/related -->
(synced from the installed skill)
<!-- /section:usage/related -->

## See also

- [Tool reference](../reference/tools.md) — the full catalogue.
- [Credential injection](../guides/credential-injection.md) · [Browser login](../guides/browser-login.md)
- [Skill — install psamvault-mcp](./install.md) · [Skill — upgrade psamvault-mcp](./upgrade.md)
