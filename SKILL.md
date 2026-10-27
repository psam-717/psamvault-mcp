---
name: psamvault-mcp
description: "Use psamvault MCP server: credential vault, browser login, API key injection, .env secret protection. MCP tools keep secrets out of the agent's context window."
version: 1.10.0
author: psam-717
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [mcp, credentials, security, vault, browser-login]
    related_skills: [native-mcp, mcp-tool-integration-skill, mcp-server-diagnostics]
---

# psamvault-mcp — skill index

[psamvault-mcp](https://pypi.org/project/psamvault-mcp/) is an MCP server for
[psamvault](https://pypi.org/project/psamvault/) — a zero-knowledge password vault for AI agents. It lets
an agent use stored credentials (site passwords, API keys, tokens) without ever seeing their plaintext
values.

**This file is an index, not a skill.** Every procedure lives in its own file under `docs/skills/`, one per
task, so a change to how something is done is made in exactly one place — and that place is the docs tree,
which is also what the documentation site renders. Nothing procedural belongs in this file.

## The skills

| Load | When | File |
|---|---|---|
| **Install** | wiring the server into an MCP host for the first time | [docs/skills/install.md](docs/skills/install.md) |
| **Upgrade** | moving an existing install to a newer release, or repairing a broken one | [docs/skills/upgrade.md](docs/skills/upgrade.md) |
| **Use the tools** | using a stored credential — a site login, an authenticated API call, injecting a secret, scanning a project | [docs/skills/usage.md](docs/skills/usage.md) |

[docs/skills/README.md](docs/skills/README.md) summarises the set and what it deliberately excludes.

## Reference surfaces

| Surface | What it holds |
|---|---|
| [docs/reference/tools.md](docs/reference/tools.md) | The tool catalogue: every registered tool with purpose, parameters and returns. A new or renamed tool is documented here, in this one file. |
| [docs/reference/configuration.md](docs/reference/configuration.md) | Host config entries, environment variables, where vault state lives, and the version contract. |
| [docs/guides/credential-injection.md](docs/guides/credential-injection.md) | Running a CLI command or HTTP request with a credential the agent never receives, including which hosts that secret may be sent to and what is refused. |
| [docs/guides/browser-login.md](docs/guides/browser-login.md) | The visible-browser login model and its limits. |
| [docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md](docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md) | The repair playbook: PATH shadowing, corrupt installs, session reload, per-host verification. |
| [docs/installation.md](docs/installation.md) | The narrative install and upgrade page. |
| [AGENTS.md](AGENTS.md) | The security rules a host or agent must follow. |
| [CLAUDE.md](CLAUDE.md) | The same rules expressed for Claude-style agents. |

## Not published here

The procedure for *cutting a release* — version bump, contract entry, TestPyPI/PyPI upload, GitHub release,
sandbox verification — is maintainer-only and deliberately excluded from this repository's skill set.
Nothing in it is needed to install, upgrade or use the server.
