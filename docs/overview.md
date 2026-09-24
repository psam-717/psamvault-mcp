---
title: Overview
description: What psamvault-mcp is, the problem it solves, how a credential is used without being seen, and what it is not for.
order: 10
---

# Overview

[psamvault-mcp](https://pypi.org/project/psamvault-mcp/) is an MCP server for
[psamvault](https://pypi.org/project/psamvault/) — a zero-knowledge password vault for AI agents.
It lets agents use stored credentials (site passwords, API keys, tokens) without ever seeing their
plaintext values. It also integrates with [pv-dotenv](https://pypi.org/project/pv-dotenv/) for
runtime credential resolution in your `.env` files.

## The problem it solves

An agent routinely needs to *use* a secret it must never *see*: upload a package with a PyPI token,
push to a private repo, log into a website, call an authenticated API. The obvious workflow — the
agent reads the secret, then pastes it into a command — puts the plaintext into the agent's context
window, where it can be echoed in a prompt, written into a transcript, or sent to a model provider.

psamvault-mcp inverts that. The credential is decrypted locally by the MCP server, injected into a
subprocess, an HTTP request, or a browser login, and the output is redacted before it reaches the
agent's context. This is the key differentiator: credentials are never in the agent's context window,
never in training data, and never accidentally leaked in a prompt.

## How it works

All sensitive session values (VEK, tokens, kdf_salt) are stored in the OS keychain (macOS Keychain,
Windows Credential Manager, Linux Secret Service) by the psamvault CLI at login time. The MCP server
reads the VEK from the keychain on every credential access — no key derivation happens in the server.
The CLI did all the derivation work (HMAC → PBKDF2 → AES-GCM-decrypt → VEK) at login time.

The credential value never crosses back to the agent. Concretely:

- `browser_login` fills credentials inside a browser — you never see them.
- `use_credential` injects credentials into HTTP requests — only the response comes back to you.
- `get_username_for_site` only returns the username, never the password.
- `list_vault_sites` only returns site names and username hints.
- `export_key_to_mcp_config` writes the key into an agent MCP config file — only a summary (paths,
  action, verification status) comes back to you.
- `export_key_to_env_file` writes the key into an agent `.env` as an environment variable — only a
  summary (path, line, action, backup) comes back to you, never the value.
- `verify_api_key` returns pass/fail + status only — never the key.
- `run_with_credential` runs a subprocess with the credential injected and returns its output with
  the credential value redacted.

### The three flows

Browser login (`browser_login`) — when an AI agent needs to log you into a website, psamvault opens
a real Chromium browser, navigates to the site, and fills in the credentials directly inside that
browser process. The agent never sees the credentials; it only sees whether the login succeeded.

API credential flow (`use_credential`) — psamvault decrypts the API key locally, makes the HTTP
request, and returns only the response. The credential is never in the agent's context. Supported
injection modes: `bearer_token` (`Authorization: Bearer ***`), `api_key_header`
(`<header_name>: <value>`), and `basic_auth` (`Authorization: Basic ***`).

CLI command flow (`run_with_credential`) — psamvault decrypts the credential locally, spawns the
subprocess with the credential injected as an env var (or piped via stdin), then scans all stdout and
stderr for the credential value and redacts it before returning. Supports `env` (default) and `stdin`
injection.

See [Credential injection](guides/credential-injection.md) and [Browser login](guides/browser-login.md)
for the guides, and [Tool reference](reference/tools.md) for the exact parameters.

## Security rules

These rules are security requirements. Violating them could expose credentials.

- **No shell commands for credentials.** Never run `psamvault get`, `psamvault show`,
  `psamvault open`, or any psamvault CLI command. Do not read credential files from the filesystem.
  The only permitted way to access credentials is through the MCP tools.
- **Never print raw credentials.** This keeps secrets out of the context window and the transcript.
- **Never ask the user to paste a credential into the chat.** Use `browser_login`,
  `use_credential`, or `run_with_credential` instead.
- **Discover first, then act.** Always call `search_vault_tools` first when you are unsure which tool
  to use. If you are unsure which sites exist, call `list_vault_sites`. If you are unsure whether a
  credential exists, call `check_credential_exists`.
- **`browser_login` for all website login requests**, `use_credential` for all authenticated
  HTTP/API requests.
- **Scan and protect existing projects.** When working in a project directory that has `.env` files,
  call `scan_and_protect()` to detect and encrypt any exposed secrets.

The full rule set, including error handling, ships with the server in
[`mcp_server/prompts/general-rules.md`](../mcp_server/prompts/general-rules.md).

## What it is not for

- **Reading credentials directly.** psamvault-mcp is not a way to fetch a secret's plaintext into the
  agent's context. There is no tool that returns a secret value, and asking for one — via the MCP
  tools, the psamvault CLI, or by reading `~/.psamvault/` — defeats the entire design.
- **Credential-free tasks.** If a task needs no stored credential, do not route it through this
  server.
- **Managing the vault.** Adding, editing, or deleting credentials is the psamvault CLI's job
  (`psamvault add`, `psamvault login`, and friends). The MCP server uses what is already stored.
- **A substitute for the psamvault CLI.** The server needs `psamvault` installed and logged in; if
  the session is missing, the fix is `psamvault login` in a terminal, run by the user.
- **Guessing what to do when a credential is missing.** If `check_credential_exists` returns
  `exists: false`, the site isn't in the vault. The user must add it before you can use it — do not
  substitute a different site or a shell workaround.

## Architecture

The MCP server manages a single Playwright Chromium instance in-process. No subprocess daemon is
used — the browser lives in the same process as the MCP server. If the browser crashes, it is
automatically restarted on the next `browser_login` call. This eliminates the fragile 3-process chain
(MCP → CLI daemon → browser) that caused connection errors with certain MCP clients (e.g. Goose's
`ECONNREFUSED` on internal proxy ports).

## Next steps

- [Installation](installation.md) — install the server and wire it into a host.
- [Features](features.md) — the capability map.
- [Tool reference](reference/tools.md) — the tools themselves.
