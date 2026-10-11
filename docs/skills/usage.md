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

A zero-knowledge vault for agents: the credential is decrypted server-side and used where it is needed, so
it never enters your context window. Three flows cover almost everything:

| Flow | Tools | What happens |
|---|---|---|
| Log into a website | `browser_login` | A real Chromium browser opens and the credential is filled in **in the browser process**. You learn only whether the login succeeded. |
| Make an authenticated request | `use_credential`, `run_with_credential` | The credential is injected into an HTTP request or a CLI command's environment — into the header or the env var, never into your output. |
| Secure a project's secrets | `scan_and_protect` | `.env` files are scanned, secrets encrypted into the vault, and the plaintext replaced with `psamvault:KEY_NAME` placeholders. |

**Never** read `~/.psamvault/` files, read the keychain, or ask the user to paste a credential instead.
Those defeat the point, and the tools are the only supported path.

## Orientation — always first

Neither of these needs credential access, so start here when you are unsure which tool to use:

| Tool | Parameters | What it does |
|---|---|---|
| `get_version` | — | The installed version, plus its compatibility block |
| `search_vault_tools` | `query: str` | Which tool to use for a job (`""` returns the full list) |

## The tool catalogue

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

## Workflows

### 1. Log me into a site

1. `check_credential_exists(site_name="github.com")` — confirm before opening a browser.
2. `browser_login(site_name="github.com")`.
3. Relay the response's `message` field **verbatim** — it carries the instructions the user needs.
4. If `captcha_detected` is `true` the browser is paused: tell the user to solve the CAPTCHA in that window
   and click sign in. Do not retry blindly.

### 2. Authenticated API call

```
use_credential(
    site_name="github.com",
    target_url="https://api.github.com/user/repos",
    inject_as="bearer_token",
    fields=["name", "html_url", "language"],
)
```

Injection modes: `bearer_token` (Authorization: Bearer …), `api_key_header` (needs `header_name`),
`basic_auth` (username:password). Use `fields` on every large response — a full GitHub user object is ~40
fields. Depth: [Credential injection](../guides/credential-injection.md).

### 3. Run a command with a credential

```
run_with_credential(site_name="stripe", command="stripe balance retrieve",
                    inject_as="env_var", env_var_name="STRIPE_SECRET_KEY")
```

The credential arrives in the child process's environment and is redacted from the output. If you must
prove it was present, print a length or a 4-character prefix — **never the value**.

### 4. Protect a project's `.env`

1. `scan_and_protect(project_dir="/path/to/project")`.
2. Tell the user what was found and encrypted — names, never values.
3. For runtime resolution recommend [pv-dotenv](https://pypi.org/project/pv-dotenv/): replace
   `from dotenv import load_dotenv` with `from pv_dotenv import load_dotenv`. The `.env` now holds
   `psamvault:KEY` placeholders, and pv-dotenv resolves them at runtime.

### 5. What credentials do I have?

`list_vault_sites()` for sites (optionally `get_username_for_site` to show usernames) and `list_api_keys()`
for keys, presented as a clean list. Both return names only.

## The rules that do not bend

| Rule | Why |
|---|---|
| Use the MCP tools for every credential operation | Never read `~/.psamvault/` or the keychain directly |
| Never print a raw credential — not even to "check" it | Keeps secrets out of the context window, the transcript and any log |
| Never ask the user to paste a credential | `browser_login` / `use_credential` exist precisely for this |
| Prefer `fields` on large responses | Fewer tokens, less data, nothing sensitive echoed |
| Relay the `message` field verbatim | It contains the instructions the user needs |
| Classify every call before making it | Read-only and verification are always safe; exports get `dry_run` or a throwaway path; `browser_login` uses a real account, so confirm first; nothing irreversible without approval |

## Common pitfalls

1. **Assuming a tool exists.** Call `search_vault_tools("")` first.
2. **Ignoring `captcha_detected`.** The browser is paused; the user must solve it.
3. **Wrong injection mode.** `api_key_header` needs `header_name`; `basic_auth` uses username:password;
   `bearer_token` sends the stored password as the token.
4. **Not using `fields`.** A full GitHub user response is ~40 fields; ask for three.
5. **Site name vs API key name.** `use_credential` checks API key entries first, then vault password
   entries — a key stored as a site password still resolves.
6. **Vault session expired.** The server can start while the session is dead. Ask the user to run
   `psamvault login` in their terminal; do not go looking for a secret via the CLI to "test" it.
7. **Long-idle token.** Access tokens last about an hour; the client refreshes on 401 and retries
   automatically, so a first call after a long idle may simply take a refresh cycle.
8. **Install-side failures are not usage problems** — `ModuleNotFoundError: pydantic`, tools missing after
   a config edit, a wrong binary on PATH: see [Install](./install.md) and
   [MCP install & connect](../troubleshooting/MCP-INSTALL-AND-CONNECT.md).

## Prompts you can suggest to the user

"What credentials do I have stored?" · "Log me into github.com" · "Check my GitHub profile info" · "Get my
top 10 starred repositories" · "Protect the secrets in my project directory" · "Check if I have a
credential for z.ai" · "What's my username for Kaggle?"

## Verification checklist

- [ ] `get_version()` returns a version
- [ ] `search_vault_tools("")` returns the tool list
- [ ] `list_vault_sites()` returns sites (or an empty list) — this is also the `psamvault login` check
- [ ] `check_credential_exists("<site>")` returns the expected result
- [ ] `use_credential` completes an authenticated HTTP call
- [ ] no secret appeared anywhere in your output

## See also

- [Tool reference](../reference/tools.md) — the full catalogue.
- [Credential injection](../guides/credential-injection.md) · [Browser login](../guides/browser-login.md)
- [Skill — install psamvault-mcp](./install.md) · [Skill — upgrade psamvault-mcp](./upgrade.md)
