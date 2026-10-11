---
title: Skills
description: The agent-followable skill set for psamvault-mcp — what each skill is for, when to load it, and what is deliberately excluded.
order: 120
---

# Skills

These pages are **skills**: procedures written to be *executed* by an AI agent — Hermes, Claude Code,
Codex, Cursor, Goose, or any agent that can read files and run shell commands — rather than read as
narrative documentation. Each one states when to load it, the steps to take, and how the agent knows it is
done.

**This folder is the single source for those procedures.** The repository-root [`SKILL.md`](../SKILL.md) is
an index into it, and the documentation site renders these same files. A change to how something is done is
made here — in one file — and nowhere else.

## The set

| Skill | Load it when | Page |
|---|---|---|
| **Install** | the user wants psamvault-mcp wired into an MCP host for the first time | [install.md](./install.md) |
| **Upgrade** | the server is already installed and a newer version is wanted, or the install is broken | [upgrade.md](./upgrade.md) |
| **Use the tools** | the agent has to use a stored credential: a site login, an authenticated API call, injecting a secret, scanning a project | [usage.md](./usage.md) |

## What the skills link to

These are not skills — they are the surfaces the skills delegate depth to, each with one owner:

| Surface | What it owns | Page |
|---|---|---|
| Tool catalogue | every tool, with parameters and returns | [Tool reference](../reference/tools.md) |
| Credential injection | running a CLI command or HTTP request with a secret the agent never receives | [Credential injection](../guides/credential-injection.md) |
| Browser login | the visible-browser model and its limits | [Browser login](../guides/browser-login.md) |
| Repair playbook | PATH shadowing, corrupt installs, session reload | [MCP install & connect](../troubleshooting/MCP-INSTALL-AND-CONNECT.md) |
| Host and agent rules | the security rules a host or agent must follow | [AGENTS.md](../AGENTS.md) |

## How an agent should use this folder

1. Pick the skill by its trigger — installing, upgrading, or using a credential.
2. Follow that skill's steps in order; each one names its own definition of done.
3. Follow its links only for depth (a tool's exact parameters, a workflow's failure modes) — do not read
   the whole folder.
4. Do not restate a procedure in another file. If it changed, the skill changed.

## Not published here: release and publishing skills

The skills for *cutting a release* — version bump, contract entry, TestPyPI/PyPI upload, GitHub release,
sandbox verification — are maintainer-only and deliberately excluded from this repository's skill set.
Nothing in them is needed to install, upgrade or use the server. The parts a user does need (installing,
proving which version is live, upgrading safely) are in [install.md](./install.md) and
[upgrade.md](./upgrade.md).

## See also

- [Installation](../installation.md) — the narrative version of the install procedure.
- [Configuration](../reference/configuration.md) — every setting, environment variable, and where vault
  state lives.
