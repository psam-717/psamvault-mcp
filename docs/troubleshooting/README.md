---
title: Troubleshooting
description: Index of the psamvault-mcp repair playbooks: install, connect, PATH shadowing, and PYTHONPATH import conflict.
order: 80
---

# Troubleshooting index

Guides for fixing **psamvault-mcp** install and runtime issues (especially for AI agents).

| Doc | When to read |
|-----|----------------|
| [MCP-INSTALL-AND-CONNECT.md](./MCP-INSTALL-AND-CONNECT.md) | Missing binary, PATH shadowing, corrupt pipx, session reload, Grok/Hermes/Claude config |
| [PYTHONPATH-CONFLICT.md](./PYTHONPATH-CONFLICT.md) | `pydantic_core` / import errors when Hermes (or another tool) set global `PYTHONPATH` |

**Start here if the MCP server will not connect:** [MCP-INSTALL-AND-CONNECT.md](./MCP-INSTALL-AND-CONNECT.md)

## What is in each playbook

- **[MCP-INSTALL-AND-CONNECT.md](./MCP-INSTALL-AND-CONNECT.md)** — the goal state for a healthy
  setup, the mandatory rules while installing, per-host config blocks (JSON clients, Grok Build,
  Hermes), how to reload each host, the failure modes agents actually hit (A–F, from a missing
  executable to treating a bare stdio launch as a hang), the decision tree, and quick verification
  commands.
- **[PYTHONPATH-CONFLICT.md](./PYTHONPATH-CONFLICT.md)** — the root-cause chain for
  `ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'` when a global `PYTHONPATH`
  leaks another application's venv into the MCP server's imports, how to check whether you are
  affected, and the three ways to fix it.

## Related documentation

- [Documentation index](../README.md) — the map of the whole docs tree.
- [Installation](../installation.md) — the normal install, host wiring, the `PYTHONPATH` gotcha, and
  upgrading.
- [Configuration](../reference/configuration.md) — host config keys, the environment variables the
  server reads, where vault state lives, and the version-lockstep contract.
- [Tool reference](../reference/tools.md) — the 13 registered tools.
- [mcp_server/prompts/general-rules.md](../../mcp_server/prompts/general-rules.md) — the agent error-
  handling guidance (session timeout, CAPTCHA, browser timeout, unknown site).
