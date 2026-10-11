---
title: Skills
description: Agent-followable skills for installing, upgrading and using psamvault-mcp — what to load for which task.
order: 120
---

# Skills

These pages are **skills**: procedures written to be *executed* by an AI agent — Hermes, Claude Code,
Codex, Cursor, Goose, or any agent that can read files and run shell commands — rather than read as
narrative documentation. Each one states when to load it, the steps to take, and how the agent knows it
is done.

Hand your agent the page for the task, or point it at this folder and let it pick.

## The set

| Skill | Load it when | Page |
|---|---|---|
| **Install** | the user wants psamvault-mcp wired into an MCP host for the first time | [install.md](./install.md) |
| **Upgrade** | the server is already installed and a newer version is wanted (or the install is broken) | [upgrade.md](./upgrade.md) |
| **Use the tools** | the agent has to use a stored credential: call an API, run a CLI with a token, log in to a site, inject a secret, scan a project | [usage.md](./usage.md) |
| **Usage skill (canonical)** | anything that calls a vault tool — this is the version-pinned one | [SKILL.md](../SKILL.md) |
| **Repair a broken install** | the server is installed but the host cannot see it, or the install looks half-linked | [MCP install & connect](../troubleshooting/MCP-INSTALL-AND-CONNECT.md) |
| **Import errors** | the server crashes on `pydantic`/native-module import | [PYTHONPATH conflict](../troubleshooting/PYTHONPATH-CONFLICT.md) |

## Why the usage skill is not duplicated here

[SKILL.md](../SKILL.md) at the repository root is the agent-facing usage skill, and the release contract
pins it to the installed server version — `psamvault-mcp compat --check` reports the pair, and
`compat --apply` keeps them in step. Copying it into this folder would create a second copy free to
drift from the pinned one, so [usage.md](./usage.md) is a routing skill that points at it rather than
restating it.

## Not published here: release and publishing skills

The skills for *cutting a release* — version bump, contract entry, TestPyPI/PyPI upload, GitHub release,
sandbox verification — are maintainer-only and deliberately excluded from this folder. Nothing in them is
needed to install, upgrade or use the server. The parts a user does need (installing, verifying which
version is live, upgrading safely) are in [install.md](./install.md) and [upgrade.md](./upgrade.md).

## Provenance

| Page | Procedure comes from | Maintained in |
|---|---|---|
| [install.md](./install.md) | [Installation](../installation.md) and [MCP install & connect](../troubleshooting/MCP-INSTALL-AND-CONNECT.md), with the host-config shapes from [Configuration](../reference/configuration.md) | this repository |
| [upgrade.md](./upgrade.md) | the psamvault-mcp upgrade playbook (install paths, venv-holder handling, shell syntax, verification) | this repository |
| [usage.md](./usage.md) | routes to [SKILL.md](../SKILL.md), [Tool reference](../reference/tools.md), and the guides | this repository |

## See also

- [Installation](../installation.md) — the narrative version of the install procedure.
- [Configuration](../reference/configuration.md) — every setting, env var and where vault state lives.
- [Tool reference](../reference/tools.md) — all 13 registered tools.
