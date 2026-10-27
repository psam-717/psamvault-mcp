---
title: Documentation
description: Map of the psamvault-mcp docs: introduction, guides, reference, and troubleshooting.
order: 0
---

# psamvault-mcp documentation

psamvault-mcp is an MCP server for [psamvault](https://pypi.org/project/psamvault/) — a
zero-knowledge password vault for AI agents. It lets agents use stored credentials (site passwords,
API keys, tokens) without ever seeing their plaintext values.

New here? Read [Overview](overview.md), then [Installation](installation.md).

## Introduction

- [Overview](overview.md) — what psamvault-mcp is, the problem it solves, how a credential is used without ever being revealed, and what it is not for.
- [Features](features.md) — the capability map: API keys, site credentials, browser login, credential injection, secret scanning, key verification, exports, and version reporting.
- [Installation](installation.md) — install with pipx, verify the server, and wire it into a host (Hermes, Claude Desktop, Cursor, Cline, Goose, Grok Build).

## Guides

- [Credential injection](guides/credential-injection.md) — how an agent runs a CLI command or makes an HTTP request with a credential it never receives: which hosts `use_credential` and `run_with_credential` will send it to, what is refused, env vs stdin, response scrubbing, and the real failure modes.
- [Browser login](guides/browser-login.md) — `browser_login`: the visible-browser model, when to use it instead of injecting, and its limits.

## Reference

- [Tool reference](reference/tools.md) — every tool the server registers, each with purpose, key parameters, what it returns, and when to use it.
- [Configuration](reference/configuration.md) — MCP host config entries, `PYTHONPATH=""` and why, vault endpoints and the environment variables the server reads, the `policy.json` destination allow-list, where vault state lives, and the version-lockstep contract.

## Troubleshooting

- [Troubleshooting index](troubleshooting/README.md) — index of the repair playbooks.
- [MCP install & connect](troubleshooting/MCP-INSTALL-AND-CONNECT.md) — the agent playbook for PATH shadowing, corrupt pipx installs, absolute-path config, session reload, and per-host verification.
- [PYTHONPATH conflict](troubleshooting/PYTHONPATH-CONFLICT.md) — `pydantic_core` / native-module import errors when another tool (e.g. Hermes) sets a global `PYTHONPATH`.

## Skills

- [Skills index](skills/README.md) — the agent-followable set: what each skill is for and when to load it. Release/publishing skills are deliberately excluded.
- [Install](skills/install.md) — install the server into an MCP host and prove it works, end to end.
- [Upgrade](skills/upgrade.md) — upgrade an existing install on Windows, macOS or Linux, and prove which version a new session actually gets.
- [Use the tools](skills/usage.md) — which tool for which job, the workflows, and the rules that do not bend.

## Related documents outside this tree

- [README.md](../README.md) — project readme: features, client setup examples, and the changelog / version-lockstep notes.
- [SKILL.md](../SKILL.md) — the skill index. It routes a host or agent to the procedures in `docs/skills/`, which are the single source for them; release and publishing procedure is not published there.
- [AGENTS.md](../AGENTS.md) — agent security rules and tool workflows.
- [CLAUDE.md](../CLAUDE.md) — the same rules expressed for Claude-style agents.
- [CONTRIBUTING.md](../CONTRIBUTING.md)
- [CHANGELOG.md](../CHANGELOG.md) — released history, newest first.
- [CHANGELOG.unreleased.md](../CHANGELOG.unreleased.md) — merges that are on `main` but not yet on PyPI.
- [PLAYWRIGHT_INTEGRATION.md](../PLAYWRIGHT_INTEGRATION.md) — the original design plan behind `browser_login`.
- [NEMOCLAW_COMPAT.md](../NEMOCLAW_COMPAT.md) — compatibility notes for the nemoclaw host.
- [mcp_server/prompts/](../mcp_server/prompts/) — the agent prompt fragments shipped with the server (`general-rules.md`, `how-to-login.md`, `how-to-discover-creds.md`, `how-to-get-username.md`).
