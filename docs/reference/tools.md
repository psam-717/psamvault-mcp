---
title: Tool reference
description: Every tool psamvault-mcp registers: purpose, parameters, what it returns.
order: 60
---

# Tool reference

**13 tools.** The list below is the server's registered tool surface, taken from the tool registry in
`mcp_server/main.py` (the `TOOL_DEFINITIONS` list). The descriptions and parameter documentation are
the registry's own text.

Tools are grouped into three categories:

| Group | Tools |
|---|---|
| Entry & Orientation | `get_version`, `search_vault_tools` |
| Site Authentication | `list_vault_sites`, `check_credential_exists`, `get_username_for_site`, `browser_login` |
| API Key Operations | `list_api_keys`, `use_credential`, `run_with_credential`, `scan_and_protect`, `export_key_to_mcp_config`, `export_key_to_env_file`, `verify_api_key` |

A tool count is **not** a version check: v0.5.0 removed `capture_stripe_credentials` and added
`export_key_to_env_file`, leaving the count at 13 — only the tool **fingerprint** reveals that. The
matching fingerprint ships in `mcp_server/compatibility.json` (see
[Configuration](./configuration.md#version-lockstep-and-compatibilityjson)).

## Discovering tools at runtime

Do not hard-code this page into a prompt — ask the server:

- **`search_vault_tools(query)`** — call this **first** when you are unsure which tool to use. Pass a
  keyword (`'login'`, `'api'`, `'check'`) or an empty string for all tools. It returns tool names with
  one-line descriptions.
- **`get_version()`** — returns the installed version and the compatibility block (paired skill
  version, newest release, whether the tool surface matches, expected tool count). It needs no
  session or login, so it is also the first call when confirming a host is connected.

Neither touches a credential.

---

## `get_version`

**Group:** Entry & Orientation.

**Purpose.** Return the installed psamvault-mcp version. No session or login required.

**Parameters.** None.

**Returns.** A dict with `version`, plus a `compatibility` block:

| Field | Meaning |
|---|---|
| `paired_skill_version` | The skill version that documents the effective release. |
| `effective_release` | The contract release this install is treated as (fingerprint-aware — a git/pre-release install can carry an older version label while already exposing the newest tool surface). |
| `newest_release` | The newest release recorded in the contract. |
| `newest_release_skill_version` | The skill version recorded for the newest release. |
| `tool_surface_matches_newest` | Whether this install's tools equal the newest release's tools. |
| `breaking_pending` | Whether the newest contract entry is marked breaking and this install is not that release. |
| `expected_tool_count` | The tool count the effective release expects. |
| `check_command` | The exact command to check the pairing (`psamvault-mcp compat --check`). |

If the contract cannot be read, `compatibility` degrades to `{"error": "<type>: <message>"}` — a
broken contract never breaks `get_version`.

**When to use it.** Anytime, with or without a vault session. First call after wiring a host; also how
an agent self-checks the server/skill pairing without extra tooling.

**Secrets.** No credential is involved.

---

## `search_vault_tools`

**Group:** Entry & Orientation.

**Purpose**, verbatim: Discover available psamvault tools. Call this FIRST to find the right tool for
your task. Returns tool names and one-line descriptions. Pass an empty string to list all tools.

Examples from the tool description:

```
search_vault_tools('')          → all tools
search_vault_tools('login')     → browser_login
search_vault_tools('api')       → use_credential
search_vault_tools('check')     → check_credential_exists
```

**Parameters.**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `query` | string | no (default `""`) | Keyword to filter tools by. Pass an empty string to return all tools. |

**Returns.** A JSON object of tool name → one-line description. Matching is case-insensitive against
the tool name and its description; **if nothing matches, the full registry is returned** rather than
an empty result.

**When to use it.** First, whenever you are unsure which tool fits the task.

**Secrets.** No credential is involved.

---

## `list_vault_sites`

**Group:** Site Authentication.

**Purpose**, verbatim: List all sites stored in the psamvault vault. Returns site names and username
hints only — never passwords. Call this before browser_login to discover what sites are available.

**Parameters.** None.

**Returns.** `{"sites": [{"site_name": ..., "username_hint": ...}, ...], "total": <n>}` — or
`{"error": ...}` when the session is missing or the request fails.

**When to use it.** When the user asks "what credentials do I have?", and before any credential-
dependent tool to discover the exact site name.

**Never returns the password.**

---

## `check_credential_exists`

**Group:** Site Authentication.

**Purpose**, verbatim: Check whether a credential is stored for a given site. Returns the username
hint if available. Never returns the password. Use this before browser_login to avoid errors.

**Parameters.**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `site_name` | string | **yes** | The site to check e.g. 'github.com' |

**Returns.** The backend's check result — `exists` plus the username hint when one is available (the
upstream endpoint is described as returning `exists` bool and `username_hint`). Returns
`{"error": ...}` if the session is missing or the call fails.

**When to use it.** Before calling any credential-dependent tool. If it returns `exists: false`, the
site isn't in the vault — the user must add it via `psamvault add` before you can use it.

**Never returns the password.**

---

## `get_username_for_site`

**Group:** Site Authentication.

**Purpose**, verbatim: Return the username (not the password) stored for a site. Use this when you
need the username for a form or request body but not the password.

**Parameters.**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `site_name` | string | **yes** | The site to get the username for. |

**Returns.** `{"site_name": ..., "username": ...}`, or `{"error": ...}` when the session is missing,
the site is unknown, or decryption fails.

**When to use it.** When a form or request body needs the username alone. This is the only tool that
returns part of a credential, and it returns **only the username — never the password**.

---

## `browser_login`

**Group:** Site Authentication.

**Purpose**, verbatim: Open a visible browser and securely log into a site using a stored psamvault
credential. Playwright navigates from the site homepage, finds the sign-in link, and handles the full
login flow — including multi-step flows (e.g., 'Continue with Email' → email → Next → password →
submit). Uses semantic locators (get_by_role, get_by_label) that work with Shadow DOM, React, and Vue
apps. Saves the browser session after a successful login so it can be reused on subsequent calls. The
credential is NEVER returned to you — psamvault fills the fields directly inside its own browser.
Returns a concise summary: success, message, captcha_detected, captcha_screenshot, final_url,
steps_count, and failed_at. When success is true, the response includes a message field — always relay
it to the user. When captcha_detected is true, the tool pauses automation; inform the user and tell
them to solve the CAPTCHA and click Sign in/Login manually in the browser. When captcha_screenshot is
not null, tell the user a screenshot of the CAPTCHA was saved to that path so they can inspect it.
Only site_name is required. All other parameters are optional.

**Parameters.**

| Name | Type | Required | Meaning (registry text) |
|---|---|---|---|
| `site_name` | string | **yes** | The vault site whose credential to use, e.g. 'github.com' or 'z.ai'. psamvault will navigate from the homepage and find the login page automatically. |
| `login_url` | string | no | Optional full URL of the login page. Auto-discovered from the homepage if not provided. |
| `username_selector` | string | no | Optional CSS selector for the username/email field. Auto-detected if not provided. |
| `password_selector` | string | no | Optional CSS selector for the password field. Auto-detected if not provided. |
| `submit_selector` | string | no | Optional CSS selector for the submit button. Auto-detected if not provided. |
| `timeout_ms` | integer | no (default `8000`) | Per-step detection timeout in milliseconds. Default is 8000. Increase for slow or JS-heavy sites. |

**Returns.** `success`, `message`, `captcha_detected`, `captcha_screenshot`, `steps_count`,
`failed_at`, `url`, `title`, `error_text`, `hint`, `login_page_screenshot`. (The registry description
calls the ending URL `final_url`; the login flow returns it as `url`.) A launch failure returns
`{"success": false, "error": ...}`, and an unexpected flow error returns `success: false` with
`failed_at: "unexpected_error"`.

**When to use it.** **Always** for login/authenticate requests — logging in, signing in,
authenticating, signing on, accessing an account, or entering credentials for a website.

**Never returns the credential** — psamvault types it into the browser's own fields. See the
[Browser login guide](../guides/browser-login.md) for the visible-browser model, CAPTCHA handling, and
limits.

---

## `list_api_keys`

**Group:** API Key Operations.

**Purpose**, verbatim: List all stored API key names with service hints. Never returns the actual key
values. Use this to discover what API keys are available. Optionally pass project_name to filter keys
for a specific project (stored via scan_and_protect as 'project/.env/KEY').

**Parameters.**

| Name | Type | Required | Meaning (registry text) |
|---|---|---|---|
| `project_name` | string | no | Optional project name to filter by. Keys stored via scan_and_protect(project_name=...) use the format 'project/.env/KEY'. |

**Returns.** `api_keys` (a flat list of `{name, service_hint, notes, created_at, project, key_name}`),
`total`, `projects` (the keys grouped by project namespace), and `standalone` (keys with no project) —
or `{"error": ...}`.

**When to use it.** When the user asks "what API keys do I have", and before `use_credential` or
`run_with_credential` to find the exact key name to pass as `site_name`.

**Never returns key values.**

---

## `use_credential`

**Group:** API Key Operations.

**Purpose**, verbatim: Make an authenticated HTTP request using a credential stored in psamvault. The
lookup checks API key entries first, then vault (site password) entries — so you can use both API keys
and site passwords. The credential value is NEVER returned to you — only the HTTP response from the
target is returned. Supported injection modes: bearer_token, api_key_header, basic_auth.

TOKEN EFFICIENCY: Use the `fields` parameter to return only the response keys you need. Example:
fields=['login','public_repos'] instead of the full GitHub user object (~40 fields). Works on both
dict responses and lists-of-dicts.

**Parameters.**

| Name | Type | Required | Meaning (registry text) |
|---|---|---|---|
| `site_name` | string | **yes** | The vault site whose credential to use, e.g.'github.com' |
| `target_url` | string | **yes** | The URL to send the authenticated request to |
| `method` | string, enum `GET` `POST` `PUT` `PATCH` `DELETE` | no (default `GET`) | HTTP method |
| `inject_as` | string, enum `bearer_token` `api_key_header` `basic_auth` | no (default `bearer_token`) | How to inject the credential: bearer_token = Authorization: Bearer ***; api_key_header = <header_name>: <password>; basic_auth = Authorization: Basic *** |
| `header_name` | string | required when `inject_as='api_key_header'` | The header name. |
| `body` | object | no | Optional JSON body for POST/PUT/PATCH |
| `fields` | array of string | no | Optional list of JSON keys to return from the response. Use this to reduce token usage — only the listed keys are returned. Works on both dict and list-of-dict responses. Example: `["login", "id", "public_repos"]` omits the other ~37 fields in a GitHub user response. |
| `extra_headers` | object | no | Optional additional headers to include in the request. |

**Returns.** `{"success": true, "status_code": ..., "headers": {...}, "data": ...}`. The body is
parsed as JSON where possible and falls back to text; `data` is filtered by `fields` when given.
Sensitive response headers (`set-cookie`, `authorization`, `www-authenticate`, `proxy-authenticate`,
`set-cookie2`) are stripped. Errors return `{"error": ...}` — including
`header_name is required when inject_as='api_key_header'.`, an unknown `inject_as` mode, an unknown
credential, and a rejected internal-address target.

**When to use it.** **Always** for authenticated HTTP/API requests.

**Never returns the credential value** — only the HTTP response.

---

## `run_with_credential`

**Group:** API Key Operations.

**Purpose**, verbatim: Run an arbitrary shell command with a credential injected as an environment
variable or stdin pipe. The credential is decrypted locally, injected into the subprocess, and all
output is scanned for the credential value and redacted before being returned — the credential NEVER
enters the agent's context.

Use cases from the tool description:

- twine upload: inject_as='env', env_var_name='TWINE_PASSWORD'
- docker login: inject_as='stdin' (password piped to stdin)
- npm publish: inject_as='env', env_var_name='NPM_TOKEN'
- git push: inject_as='env', env_var_name='GITHUB_TOKEN'
- pip install (private repo): inject_as='env', env_var_name='PIP_TOKEN'

Only site_name and command are required. When inject_as='env', env_var_name is required.

**Parameters.**

| Name | Type | Required | Meaning (registry text) |
|---|---|---|---|
| `site_name` | string | **yes** | The credential to use. This can be an API key name (e.g. 'pypi', 'testpypi', 'github-api') or a vault site name (e.g. 'github.com', 'dockerhub'). Check with list_api_keys or list_vault_sites first. |
| `command` | string | **yes** | Shell command to run with the credential injected. Example: 'twine upload dist/*' or 'git push origin main' |
| `inject_as` | string, enum `env` `stdin` | no (default `env`) | 'env' — set credential as an environment variable (requires env_var_name). 'stdin' — pipe credential as stdin to the process. |
| `env_var_name` | string | required when `inject_as='env'` | The environment variable name to set the credential as. Examples: 'TWINE_PASSWORD', 'GITHUB_TOKEN', 'DOCKER_PASSWORD', 'NPM_TOKEN'. When 'TWINE_PASSWORD' is used, TWINE_USERNAME is automatically set to '__token__'. |
| `extra_env` | object | no | Optional additional environment variables to pass to the subprocess. These are non-sensitive values merged into the subprocess environment alongside the credential. Example: `{'TWINE_REPOSITORY_URL': 'https://upload.pypi.org/legacy/'}` |
| `workdir` | string | no | Optional working directory for the command. Defaults to the MCP server's current working directory. |
| `timeout` | integer | no (default `120`) | Max seconds to wait for the command (default: 120). |

**Returns.** `exit_code` (int; `-1` for errors), `stdout`, `stderr` — both streams with the credential
value replaced by `[REDACTED]` (for credentials longer than 8 characters the 8-character prefix is
redacted too). `error` is added when the command could not run. A timeout returns `exit_code: -1`,
`error: "timeout"`, `timed_out: true`, and whatever output the command had already produced (already
redacted). A blocked command returns the blocking reason in `error` and `stderr` without running
anything.

**When to use it.** For `twine upload`, `git push`, `docker login`, `npm publish`, `pip install`
against a private repo, or any CLI tool that needs an API key or password. For the timeout pitfall and
the retry rules, read the [Credential injection guide](../guides/credential-injection.md).

**Never returns the credential value** — stdout and stderr are redacted before they reach the agent.
Commands that could dump a secret are refused (`psamvault get|show|ak-get|ak-show|search|list|export`,
`psamvault vault-get|credential-get|entry-get`, `cat ...*.psamvault`, `type ...*.psamvault`), and URLs
in the command targeting loopback, private, or link-local addresses are rejected.

---

## `scan_and_protect`

**Group:** API Key Operations.

**Purpose**, verbatim: Scan a project directory for exposed secrets in .env files and protect them.
Finds .env files, detects API keys and passwords using pattern matching, encrypts them into the
psamvault vault, and replaces the plaintext values with 'psamvault:<KEY_NAME>' placeholders. The
captured secrets can then be used with use_credential.

**Parameters.**

| Name | Type | Required | Meaning (registry text) |
|---|---|---|---|
| `project_dir` | string | no | Path to the project directory. Defaults to current working directory. |
| `patterns` | array of string | no | Optional custom key name patterns to scan for (e.g. ['MY_CUSTOM_KEY']). |
| `project_name` | string | no | Optional project name for grouping. Keys stored as 'project_name/.env/KEY_NAME' instead of 'env/.env/KEY_NAME'. Use this for cleaner per-project organisation. |

> **Note on `project_name`.** The parameter is declared in the tool schema and accepted by the tool
> implementation, but the server's request router (`handle_call_tool` in `mcp_server/main.py`) does
> not currently forward `project_name` from the request to the handler, so the per-project namespace
> is **not applied** in the installed 0.5.3. Pass it only if you have verified the behaviour against
> your installed build.

**Returns.** A dict with `scanned_dir`, `files_scanned`, `secrets_found`, `captured`, `files_modified`,
`errors` — or `{"error": ...}` when not logged in or the scan fails. When no secrets are found the
project is clean and there is nothing to do; `files_not_gitignored` in the result means the user
should add `.env` to their `.gitignore`.

**When to use it.** As a one-time safety check when the agent starts working in a project directory
that has `.env` files. After protecting, pair with
[pv-dotenv](https://pypi.org/project/pv-dotenv/) — a drop-in replacement for `python-dotenv` that
resolves `psamvault:` placeholders at runtime.

**Never returns the secret values** — the plaintext is replaced with `psamvault:<KEY_NAME>`
placeholders.

---

## `export_key_to_mcp_config`

**Group:** API Key Operations.

**Purpose**, verbatim: Export a vault API key directly into an agent host's MCP server config (Hermes
config.yaml → mcp_servers.<server_name>). The key is decrypted locally and written into the config
file as an HTTP server (url + Authorization/custom header) or stdio server (command + env var). The
key value is NEVER returned to you — only a summary with config path and backup path. HTTP exports
auto-verify the key against the provider before writing (verification: verified|skipped|failed in the
result); failed verification blocks the export. Use when the agent needs an MCP server whose auth is
a vault key (e.g. Render's hosted MCP) and the config must be provisioned without the key ever
entering chat.

**Parameters.**

| Name | Type | Required | Meaning (registry text) |
|---|---|---|---|
| `key_name` | string | **yes** | Name of the vault API key to export (see list_api_keys). |
| `server_name` | string | **yes** | Name of the mcp_servers entry to add/replace, e.g. 'render'. |
| `agent` | string, enum `hermes` | no (default `hermes`) | Target agent host whose config to write. v1 supports 'hermes'. |
| `url` | string | one of `url` / `command` | HTTP transport: MCP server URL, e.g. https://mcp.render.com/mcp. Provide url (HTTP) XOR command (stdio). |
| `inject_as` | string, enum `bearer_token` `api_key_header` `env` | no (default `bearer_token`) | bearer_token → headers.Authorization: Bearer ***; api_key_header → headers.<header_name>: <key> (header_name required); env → env.<env_var_name>: <key> (env_var_name required, stdio only). |
| `header_name` | string | required when `inject_as='api_key_header'` | Header to put the key in. |
| `command` | string | one of `url` / `command` | Stdio transport: executable for the MCP server, e.g. 'uvx', 'npx'. Provide url (HTTP) XOR command (stdio). |
| `args` | array of string | no | Optional args for a stdio MCP server command. |
| `env_var_name` | string | required when `inject_as='env'` | Env var to set the key as. |
| `config_path` | string | no | Optional explicit path to the Hermes config.yaml. Defaults to $HERMES_HOME/config.yaml or the platform default. |
| `replace` | boolean | no (default `false`) | If the mcp_servers entry already exists, error unless replace=true. |
| `dry_run` | boolean | no (default `false`) | Preview the change and write nothing. Returns the action that would occur. |
| `verify_url` | string | no | Optional read-only provider endpoint to probe with the key (whoami). Overrides the bundled recipe for providers that have one; required for HTTP providers without a recipe unless skip_verify=true. |
| `skip_verify` | boolean | no (default `false`) | Loud escape hatch for providers that CANNOT be probed (or stdio/env exports where you ran a manual check). It never overrides a failed probe: a key the provider rejects is definitively invalid and the write is blocked. The result records verification: skipped when it applies. |

**Returns.** On success: `success`, `verification` (`verified` | `skipped` | `failed`), `action`,
`agent`, `server_name`, `config_path`, `backup_path`, `dry_run`, `credential` (literally
`vault key '<key_name>' (redacted)`), and a `note` to restart the agent host. A failed verification
returns `success: false`, `verification: "failed"`, `detail`, `error`, and `server_name` — with the
config untouched.

**When to use it.** When an agent host needs an MCP server authenticated by a vault key and the config
must be provisioned without the key entering chat. Never hand-edit the config, never ask the user to
paste the key, and never run the host's own `mcp add` CLI — psamvault owns the write (with backup and
`dry_run`).

**Never returns the key value** — only the summary above. A failed probe is final:
`skip_verify=true` never overrides a provider rejecting the key.

---

## `export_key_to_env_file`

**Group:** API Key Operations.

**Purpose**, verbatim: Export a vault API key into an agent host's .env file as an environment
variable (default: $HERMES_HOME/.env for agent='hermes'). Use this when an agent TOOL reads its
credential from a dotenv file rather than from MCP config — e.g. Hermes' web tools read
HERMES_HOME/.env. The key value is NEVER returned to you — only a summary with the path, line, action
and backup path. The existing variable is updated in place (idempotent re-runs, no duplicate keys) and
a timestamped backup is written before any change. The key is auto-verified against the provider first
(verification: verified|skipped|failed); a key that cannot be probed requires skip_verify=true. Pass
env_path to target a host without a verified default location.

**Parameters.**

| Name | Type | Required | Meaning (registry text) |
|---|---|---|---|
| `key_name` | string | **yes** | Name of the vault API key to export (see list_api_keys). |
| `env_var_name` | string | **yes** | Environment variable to set, e.g. 'TAVILY_API_KEY'. |
| `agent` | string, enum `hermes` | no (default `hermes`) | Target agent host whose .env to write. v1 has a verified location for 'hermes' only; other hosts need env_path. |
| `env_path` | string | no | Optional explicit path to the .env file. Overrides the per-agent default. |
| `dry_run` | boolean | no (default `false`) | Preview the change and write nothing. Returns the action that would occur. |
| `verify_url` | string | no | Optional read-only provider endpoint to probe with the key (whoami). Overrides the bundled recipe; required for providers without one unless skip_verify=true. |
| `skip_verify` | boolean | no (default `false`) | Loud escape hatch for providers with no probe endpoint. It never overrides a failed probe: a key the provider rejects is definitively invalid and the write is blocked. The result records verification: skipped when it applies. |

**Returns.** On success: `success`, `verification`, `agent`, `variable`, `action`, `env_path`, `line`,
`backup_path`, `dry_run`, `credential` (literally `vault key '<key_name>' (redacted)`), and a `note`
to restart the agent host. A failed verification returns `success: false`,
`verification: "failed"`, `detail`, `error`, `agent`, and `variable`. A write failure returns
`{"error": "could not write <target>: <reason>"}`.

**When to use it.** When an agent **tool** (not an MCP server) reads its credential from a dotenv
file. Only `agent="hermes"` has a verified `.env` location — never invent a path; an unknown host is
rejected on purpose and must be targeted with `env_path`. Restart the host session afterwards (the
`.env` is read at startup) and verify with the consumer, not the file.

**Never returns the key value** — only the summary above.

---

## `verify_api_key`

**Group:** API Key Operations.

**Purpose**, verbatim: Verify a vault API key is valid by probing the provider's read-only endpoint
(whoami) with the decrypted key. Provider is resolved from the vault entry's service hint; pass
verify_url to override for providers without a bundled recipe. Returns
success/verification/status/error_class — the key value is NEVER returned. Use BEFORE
export_key_to_mcp_config or whenever you need to prove a stored key still works.

**Parameters.**

| Name | Type | Required | Meaning (registry text) |
|---|---|---|---|
| `key_name` | string | **yes** | Name of the vault API key to verify (see list_api_keys). |
| `verify_url` | string | no | Optional read-only provider endpoint to probe with the key (whoami). Overrides the bundled recipe; required for providers without a recipe. |

**Returns.** `success`, `verification` (`verified` | `failed`), `key_name`, `provider`, `status`,
`error_class`, `detail`. When no recipe exists for the provider and no `verify_url` was given, it
returns `success: false`, `verification: "failed"` and a `detail` saying so.

**When to use it.** **Before** `export_key_to_mcp_config`, or whenever you need to prove a stored key
still works.

**Never returns the key value.**

**Bundled recipes.** The store ships recipes only for providers whose read-only endpoint has been
verified against the live service: `openrouter`, `render`, `tavily`, and `github`. Deliberately
absent are `pypi` / `testpypi` — an upload token cannot be validated without attempting an upload, so
those need `verify_url` or `skip_verify=true`. Recipes are non-secret transport metadata only
(`{url, method, expect, auth_kind}`); no provider is added without a 200-for-valid / non-200-for-bad
probe, because an endpoint that answers 200 anonymously would "verify" an invalid key.

---

## Not in this build

`capture_stripe_credentials` existed in v0.4.6 and earlier and was **removed** in v0.5.0 — the Stripe
Projects capture flow is gone. Use `scan_and_protect` for project `.env` secrets.

## See also

- [Configuration](./configuration.md) — how the server is configured and how the version contract
  works.
- [Credential injection guide](../guides/credential-injection.md) — the two injection tools in depth.
- [Browser login guide](../guides/browser-login.md) — `browser_login` in depth.
- [SKILL.md](../../SKILL.md) — the agent-facing usage skill.
