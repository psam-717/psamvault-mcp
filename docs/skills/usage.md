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

[psamvault-mcp](https://pypi.org/project/psamvault-mcp/) is an MCP server for [psamvault](https://pypi.org/project/psamvault/) — a zero-knowledge credential vault for AI agents. It lets agents use stored credentials (site passwords, API keys, tokens) without ever seeing their plaintext values.

The server provides four complementary flows:

- **`browser_login`** — Opens a real Chromium browser, navigates to the site, fills credentials directly in the browser process. The agent only sees whether login succeeded.
- **`use_credential`** — Authenticated HTTP, only to that entry's own site or the API key's service (or a host a human allowed in `~/.psamvault/policy.json`). Any other host is refused. The response is scrubbed of the secret; that scrub is defence in depth.
- **`run_with_credential`** — A CLI that reads a token from the environment or stdin. `http(s)` URLs in the command follow the same host rule. Output is scrubbed of the secret and its common encodings, including reversed text. A command with no URL is not host-checked.
- **`scan_and_protect`** — Scans a project directory for `.env` files, detects secrets using pattern matching, encrypts them into the psamvault vault, and replaces plaintext with `psamvault:KEY_NAME` placeholders.

This is the **key differentiator**: credentials are never in the agent's context window, never in training data, and never accidentally leaked in a prompt.

<!-- /section:usage/overview -->
## When to use this
<!-- section:usage/when-to-use -->

- User asks you to interact with psamvault-mcp tools
- User wants to log into a website via agent (`browser_login`)
- User needs authenticated API calls via agent (`use_credential`)
- User needs to run CLI commands with credentials (twine upload, docker login, git push — `run_with_credential`)
- User wants to secure their project's `.env` files (`scan_and_protect`)
- Any task involving credential lookup or use — prefer MCP tools over terminal-based CLI
- **Do not use for**: reading credentials directly (always use `use_credential` instead)

<!-- /section:usage/when-to-use -->


## Orientation — always first

Neither of these needs credential access, so start here when you are unsure which tool to use:

| Tool | Parameters | What it does |
|---|---|---|
| `get_version` | — | The installed version, plus its compatibility block |
| `search_vault_tools` | `query: str` | Which tool to use for a job (`""` returns the full list) |

## The tool catalogue
<!-- section:usage/tools -->

Once connected, these tools become available (prefixed `mcp_psamvault_*` in Hermes):

| Tool | Parameters | Description | Security |
|------|-----------|-------------|----------|
| `get_version` | None | Return installed version | No secrets involved |
| `search_vault_tools` | `query: str` | Discover which tool to use (call this first) | No secrets involved |
| `list_vault_sites` | None | List stored credential sites | Returns names only, no passwords |
| `list_api_keys` | `project_name: str (optional)` | List stored API key names with service hints | Returns names only, no key values |
| `check_credential_exists` | `site_name: str` | Check if credential exists | Returns boolean + username hint |
| `get_username_for_site` | `site_name: str` | Get stored username | Returns username, never password |
| `use_credential` | `site_name, target_url, method, inject_as, fields, header_name, body, extra_headers` | Authenticated HTTP to the entry's site or the key's service. `target_url` is an absolute http(s) URL. Other hosts are refused | ❗ Secret is not a result field; response is scrubbed. Host check is the boundary |
| `browser_login` | `site_name, login_url, selectors, timeout_ms` | Log into website via browser | ❗ Fills directly in browser |
| `run_with_credential` | `site_name, command, inject_as, env_var_name, extra_env, workdir, timeout` | CLI with the credential in env or stdin. URLs in the command are host-checked | ❗ Output scrubbed (raw, reversed, encodings). Scrub is not the boundary |
| `scan_and_protect` | `project_dir, patterns, project_name` | Scan & encrypt .env secrets | ❗ Encrypts into vault |
| `export_key_to_mcp_config` | `key_name, server_name, url/command, inject_as, replace, dry_run, config_path, verify_url, skip_verify` | Export a vault API key into a host MCP config (e.g. Hermes `config.yaml`) — auto-verifies HTTP keys | ❗ Key value never returned |
| `export_key_to_env_file` | `key_name, env_var_name, agent, env_path, dry_run, verify_url, skip_verify` | Export a vault API key into an agent `.env` as an environment variable (default `HERMES_HOME/.env`) — updates in place, backs up first, **never writes an invalid key** | ❗ Key value never returned |
| `verify_api_key` | `key_name, verify_url (optional)` | Verify a vault API key by probing the provider's read-only endpoint | No secrets involved — key value never returned |

### Bundled verify recipes (no `verify_url` needed)

`verify_api_key` and both export tools probe the provider's read-only whoami endpoint when the provider
is one of the bundled recipes. If the vault entry's service hint matches, you pass **nothing extra**:

| Provider (service hint) | Probe | Expect |
|---|---|---|
| `render` | `GET https://api.render.com/v1/owners` | 200 |
| `openrouter` | `GET https://openrouter.ai/api/v1/auth/key` | 200 |
| `tavily` | `GET https://api.tavily.com/usage` | 200 |
| `github` | `GET https://api.github.com/user` | 200 |

Anything else needs `verify_url=<read-only whoami endpoint>` or `skip_verify=true` — and `skip_verify`
covers only *cannot probe*, never *probe said no* (a rejected key is definitively invalid and blocks the
write). PyPI/TestPyPI upload tokens are deliberately absent: no read-only endpoint can validate them, so
a recipe there would "verify" an invalid key. When adding a provider, probe it with a real key first and
record the date — the recipe store's docstring holds that rule.

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

The call is refused unless the host is that entry's own site (a site password: its registrable domain
and subdomains) or the API key's service hosts, or a host a human listed under
`entries["<entry>"].allow_hosts` in `~/.psamvault/policy.json`. There is no parameter that grants a
host. An API key with no known service host stays refused until a human allows one. A redirect off an
allowed host is refused and not followed. `target_url` must include `https://` (or `http://`). Do not
send the secret to an echo service or any other unrelated host to read it back. The response body and
headers are scrubbed of the secret and its common encodings; that scrub is defence in depth.

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

3. **Stdout and stderr are scrubbed** of the raw secret, the reversed text, the 8-character prefix, and the common encodings (base64, urlsafe base64, URL-encoding, hex, the basic-auth blob) before they are returned. That scrub is defence in depth. Every `http(s)` URL in the command must stay on the entry's site or the key's service, or on a host a human allowed; any other URL is refused and the command does not run. A command with no URL (`twine upload`, `git push`, `npm publish`) is not host-checked.

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

| Rule | Rationale |
|------|-----------|
| **Always use MCP tools** for credential ops | Never read `~/.psamvault/` files or env vars directly |
| **Never print raw credentials** | Keeps secrets out of context window and transcript |
| **Never ask user to paste credentials** | Use `browser_login`, `use_credential`, or `run_with_credential` instead |
| **Use `fields` parameter** | Reduces token usage and avoids returning unnecessary data |
| **Relay `message` field verbatim** | Browser login messages contain user-facing instructions |
| **Never run `psamvault` CLI commands** | Always use MCP tools for credential access |
| **Remember exports write plaintext into `.env`** | The variable is the agent's local working copy; the vault stays the source of truth — rotate from the vault, not the file |

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
9. **Sending a secret to a host it does not belong to** — `use_credential("github.com", "https://httpbin.org/anything")` is refused. Relay the error. A human allows the host by editing `~/.psamvault/policy.json`; you do not edit that file and you do not retry against another host. A key with no known service host (no verify recipe, no hostname in its name or service) is refused until they do.
10. **Server not running** — MCP tools return connection errors if the server is not started. If using stdio, Hermes starts it automatically on connect. If using HTTP/SSE, start with `psamvault-mcp --http --port 8433` in a terminal.
11. **Token expiry** — The access token has ~1 hour lifetime. psamvault-mcp auto-refreshes via `api_client.py`, but if the server has been running for hours without use, the initial call might need a refresh cycle. The `api_client` handles 401 → refresh → retry automatically.

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

- **Single-process Playwright**: The browser lives in-process with the MCP server. No subprocess daemon chain. If the browser crashes, the next call auto-restarts it.
- **Stdio transport (default)**: Hermes manages the process lifecycle. No manual server start needed.
- **OS keychain auth**: The VEK (Vault Encryption Key) is stored in the OS keychain by the psamvault CLI at login time.
- **No consent dialog**: The v0.4.0+ architecture removed the consent dialog requirement. Credentials are used on demand.
- **Host binding**: a stored secret goes only to the entry's own site, the API key's service hosts, or `allow_hosts` in `~/.psamvault/policy.json`. Redirects are checked one hop at a time.
- **Output scrubbing**: `use_credential` responses and `run_with_credential` stdout/stderr are scrubbed of the secret in its raw form, reversed text, and common encodings. That is defence in depth. The host check is the boundary.

<!-- /section:usage/architecture -->

## Related projects
<!-- section:usage/related -->

| Package | What It Does |
|---------|-------------|
| [`psamvault`](https://pypi.org/project/psamvault/) | CLI — store, list, manage credentials |
| [`psamvault-mcp`](https://pypi.org/project/psamvault-mcp/) | MCP server for AI agents |
| [`pv-dotenv`](https://pypi.org/project/pv-dotenv/) | Drop-in python-dotenv replacement — resolves `psamvault:` placeholders |
| [`psamvault-cli`](https://github.com/psam-717/psamvault-cli) | CLI + vault management |

<!-- /section:usage/related -->

## See also

- [Tool reference](../reference/tools.md) — the full catalogue.
- [Credential injection](../guides/credential-injection.md) · [Browser login](../guides/browser-login.md)
- [Skill — install psamvault-mcp](./install.md) · [Skill — upgrade psamvault-mcp](./upgrade.md)
