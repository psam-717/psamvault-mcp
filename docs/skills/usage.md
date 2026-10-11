---
title: "Skill — use the psamvault tools"
description: Agent-followable routing skill for using psamvault-mcp — which document to load for each credential task, and the rules that do not bend.
order: 150
---

# Skill: use psamvault-mcp

**Load this when** the user asks you to use a stored credential: call an API with a key, run a CLI with a
token, log in to a website, inject a secret into a process, or scan a project for exposed secrets.

This page routes you to the right document and states the rules that apply to every vault call. The
procedure itself lives in the version-pinned usage skill, [SKILL.md](../SKILL.md) at the repository root —
load that before your first tool call, because the contract pairs it to the installed server version.

## Routing

| Task | Load |
|---|---|
| Anything that calls a vault tool | [SKILL.md](../SKILL.md) — the canonical usage skill |
| What a tool takes and returns | [Tool reference](../reference/tools.md) |
| Running a command or HTTP request with a credential | [Credential injection](../guides/credential-injection.md) |
| Logging in to a site | [Browser login](../guides/browser-login.md) |
| Which tools your session has | `search_vault_tools("")` in-session, or [Tool reference](../reference/tools.md) |
| Where vault state, endpoints and env vars live | [Configuration](../reference/configuration.md) |
| The install looks broken | [MCP install & connect](../troubleshooting/MCP-INSTALL-AND-CONNECT.md) |

## The rules that do not bend

1. **The secret never enters your context.** Use injection (`run_with_credential`, `use_credential`) or
   export — not a tool that prints a value. When you must prove a value exists, print a length or a
   4-character prefix, never the value.
2. **Never ask for, accept, or echo a password, key or token in chat** — not even one the user offers
   first. Point them at the vault (`scan_and_protect`, the host's browser login) instead.
3. **Classify the call before making it.** Read-only and verification calls are always safe. Injection is
   safe only if the command cannot print the secret. Writes (`export_key_to_env_file`,
   `export_key_to_mcp_config`) get a `dry_run` or a throwaway path — never the live agent config.
   `browser_login` uses a real browser and a real account: confirm with the user. Nothing irreversible
   (publishing, deleting) happens without explicit approval.
4. **Report the outcome, not the tool call.** An operation is done when the command exited 0 or the HTTP
   call returned a status — not when a tool returned successfully.

## Definition of done for a credential task

- the operation actually ran, and you can name its exit code or HTTP status;
- no secret appears in your output, your files, or the transcript;
- if finishing would require revealing a secret, you stopped and said so instead.

## See also

- [Overview](../overview.md) — why the credential is never revealed, and what this is not for.
- [Features](../features.md) — the capability map.
- [Skill — install psamvault-mcp](./install.md) · [Skill — upgrade psamvault-mcp](./upgrade.md)
