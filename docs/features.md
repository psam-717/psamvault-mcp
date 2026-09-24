---
title: Features
description: The psamvault-mcp capability map — what each capability is and when an agent or user reaches for it.
order: 30
---

# Features

Tools are grouped by purpose so AI agents can find the right tool faster. Always start in **Entry &
Orientation**. Every tool below is documented in full — parameters, returns, and when to call it — in
the [Tool reference](./reference/tools.md).

## Entry & orientation

### Discovery (`search_vault_tools`)

**What it is.** The one tool an agent needs to know about upfront: it returns tool names and
one-line descriptions, optionally filtered by a keyword. `search_vault_tools('login')` returns
`browser_login`, `search_vault_tools('api')` returns `use_credential`, `search_vault_tools('check')`
returns `check_credential_exists`, and an empty string returns all of them.

**When to reach for it.** First, whenever you are unsure which tool fits the task.

See [`search_vault_tools`](./reference/tools.md#search_vault_tools).

## Site credentials

### Vault discovery (`list_vault_sites`, `check_credential_exists`, `get_username_for_site`)

**What it is.** The read-only half of site authentication: list the stored site names with username
hints, check whether a credential exists for one site, and get the stored username for a site. None
of these ever returns a password.

**When to reach for them.** When the user asks "what credentials do I have?", before calling
`browser_login` (so you do not attempt a login for a site that is not stored), and when a form or
request body needs the username but not the password.

See [`list_vault_sites`](./reference/tools.md#list_vault_sites),
[`check_credential_exists`](./reference/tools.md#check_credential_exists), and
[`get_username_for_site`](./reference/tools.md#get_username_for_site).

## Browser login and autofill

### `browser_login`

**What it is.** Opens a visible browser and securely logs into a site using a stored psamvault
credential. Playwright navigates from the site homepage, finds the sign-in link, and handles the full
login flow — including multi-step flows (e.g., 'Continue with Email' → email → Next → password →
submit) — using semantic locators that work with Shadow DOM, React, and Vue apps. It saves the browser
session after a successful login so it can be reused on subsequent calls, and it detects CAPTCHAs,
takes a screenshot, and pauses automation so the user can solve them.

**When to reach for it.** **Always** for login/authenticate requests — "log into", "sign in to",
"authenticate to", "access my account on", or any request that involves entering credentials for a
website. Pass only `site_name` unless the user supplies a specific login URL or exact CSS selectors.

Guide: [Browser login](./guides/browser-login.md). Tool:
[`browser_login`](./reference/tools.md#browser_login).

## API key operations

### API key discovery (`list_api_keys`)

**What it is.** Lists all stored API key names with service hints, project grouping, and creation
dates — never the actual key values. The optional `project_name` filter narrows the list to keys
stored under a project namespace.

**When to reach for it.** Before `use_credential`, to find the exact key name to pass as `site_name`.

See [`list_api_keys`](./reference/tools.md#list_api_keys).

### HTTP calls with credentials (`use_credential`)

**What it is.** Makes an authenticated HTTP request using a credential stored in psamvault, returning
only the HTTP response from the target. The lookup checks API key entries first, then vault (site
password) entries — so both API keys and site passwords work. Supported injection modes:
`bearer_token`, `api_key_header`, and `basic_auth`. The `fields` parameter returns only the response
keys you need, which cuts token usage on large responses.

**When to reach for it.** **Always** for API calls that need auth — "check my GitHub profile",
"get my top 10 starred repos", or any authenticated request the agent should make on the user's
behalf.

Guide: [Credential injection](./guides/credential-injection.md). Tool:
[`use_credential`](./reference/tools.md#use_credential).

### Credential injection into commands (`run_with_credential`)

**What it is.** Runs an arbitrary shell command with a credential injected as an environment variable
or stdin pipe. The credential is decrypted locally, injected into the subprocess, and all output is
scanned for the credential value and redacted before being returned.

**When to reach for it.** For `twine upload`, `git push`, `docker login`, `npm publish`, `pip install`
against a private repo, or any CLI tool that needs an API key or password and that you would otherwise
have to feed a secret from chat.

Guide: [Credential injection](./guides/credential-injection.md). Tool:
[`run_with_credential`](./reference/tools.md#run_with_credential).

### Secret scanning and protection (`scan_and_protect`)

**What it is.** Scans a project directory for exposed secrets in `.env` files, detects API keys and
passwords using pattern matching, encrypts them into the psamvault vault, and replaces the plaintext
values with `psamvault:<KEY_NAME>` placeholders. Passing `project_name` groups the keys as
`project_name/.env/KEY_NAME`; without it they are stored as `env/.env/KEY_NAME`
(backwards-compatible). The captured secrets can then be used with `use_credential`.

**When to reach for it.** As a one-time safety check when the agent starts working in a project
directory that has `.env` files. Pair the result with
[pv-dotenv](https://pypi.org/project/pv-dotenv/) — a drop-in replacement for `python-dotenv` that
resolves `psamvault:` placeholders at runtime.

See [`scan_and_protect`](./reference/tools.md#scan_and_protect).

### Key verification (`verify_api_key`)

**What it is.** Verifies a vault API key is valid by probing the provider's read-only endpoint
(whoami) with the decrypted key. The provider is resolved from the vault entry's service hint;
`verify_url` overrides it for providers without a bundled recipe. It returns
`success/verification/status/error_class` — the key value is never returned.

**When to reach for it.** **Before** `export_key_to_mcp_config`, or whenever you need to prove a
stored key still works.

See [`verify_api_key`](./reference/tools.md#verify_api_key).

### Export to an MCP config (`export_key_to_mcp_config`)

**What it is.** Exports a vault API key directly into an agent host's MCP server config (Hermes
`config.yaml` → `mcp_servers.<server_name>`), writing an HTTP server (url + Authorization/custom
header) or a stdio server (command + env var). It backs up the config first, supports `dry_run` and
`replace`, and auto-verifies HTTP exports against the provider before writing — a failed verification
blocks the export. Only a summary with the config path and backup path comes back to you.

**When to reach for it.** When an agent host needs an MCP server whose auth is a vault key (for
example Render's hosted MCP) and the config must be provisioned without the key ever entering chat.
Never hand-edit the config or run the host's own `mcp add` CLI for this — psamvault owns the write.

See [`export_key_to_mcp_config`](./reference/tools.md#export_key_to_mcp_config).

### Export to an env file (`export_key_to_env_file`)

**What it is.** Exports a vault API key into an agent host's `.env` file as an environment variable
(default: `$HERMES_HOME/.env` for `agent='hermes'`). The variable is updated in place (idempotent
re-runs, no duplicate keys), a timestamped backup is written before any change, and the key is
auto-verified against the provider first.

**When to reach for it.** When an agent **tool** (not an MCP server) reads its credential from a
dotenv file — for example Hermes' web tools read `TAVILY_API_KEY` from `HERMES_HOME/.env`. Pass
`env_path` for a host without a verified default location; only `agent="hermes"` has a verified
`.env`, and an unknown host is rejected on purpose.

See [`export_key_to_env_file`](./reference/tools.md#export_key_to_env_file).

## Version and compatibility reporting

### `get_version`

**What it is.** Returns the installed psamvault-mcp version plus a `compatibility` block: the paired
skill version, effective and newest release, whether the tool surface matches the newest release,
whether a breaking release is pending, the expected tool count, and the exact check command. It needs
no session or login. If the contract cannot be read, the block degrades to an `error` field rather
than failing the call.

**When to reach for it.** Anytime — it is the first call when confirming a host is connected, and it
is how an agent self-checks the server/skill pairing without extra tooling.

See [`get_version`](./reference/tools.md#get_version) and
[Configuration](./reference/configuration.md#version-lockstep-and-compatibilityjson).

## Related capabilities

- **Version lockstep and maintenance subcommands** (`compat`, `selfcheck`, `doctor`) —
  see [Configuration](./reference/configuration.md#maintenance-subcommands).
- **The pinned usage skill** — [SKILL.md](../SKILL.md).
- **Agent security rules and workflows** — [AGENTS.md](../AGENTS.md).
