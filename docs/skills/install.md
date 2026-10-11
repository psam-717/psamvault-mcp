---
title: "Skill — install psamvault-mcp"
description: Agent-followable procedure for installing psamvault-mcp into an MCP host and proving it works end to end.
order: 130
---

# Skill: install psamvault-mcp

**Load this when** the user wants psamvault-mcp running in their MCP host — Hermes, Claude Desktop,
Claude Code, Cursor, Cline, Goose, Grok Build, or any stdio MCP client — for the first time.

**Definition of done.** All four, in this order:

1. `psamvault-mcp --version` prints a version and exits cleanly (no traceback).
2. The host reports the server connected, with **13 tools**.
3. `get_version` in a session opened *after* the config change returns that same version plus a
   `compatibility` block.
4. `list_vault_sites` returns a list — which is also the proof that a vault session exists.

## Transport — stdio only

The server speaks MCP over **stdio**: the host spawns the process and exchanges JSON-RPC over
stdin/stdout. That is the only transport the package implements.

> An HTTP/SSE mode (`psamvault-mcp --http --port 8433`, endpoint `/sse`) has been documented in the past as
> a way to connect. **It does not exist** — the entry point defines no such flags and nothing in the
> package serves HTTP, so pointing a host at that URL fails to connect.

## Step 0 — preconditions

| Need | How to confirm | If missing |
|---|---|---|
| Python ≥ 3.11 | `python --version` | install Python, then re-run |
| The psamvault CLI | `psamvault --version` | `pipx install psamvault` |
| A configured, logged-in vault | `psamvault login` | `psamvault configure` first — **the user must run `login` themselves**; an agent cannot supply the password |
| Chromium (only for `browser_login`) | — | `playwright install chromium` |

An expired session is not an install failure: the fix is `psamvault login` run by the user in a terminal.

## Step 1 — install the server

Prefer **pipx**: it isolates the dependency set and puts the entry point in your user bin.

```bash
pipx install psamvault-mcp
```

`uv tool install psamvault-mcp` and `pip install psamvault-mcp` also work, but they place the console
script wherever that tool puts scripts. On Windows, a `pip install` into the system Python commonly
leaves a broken shim on `PATH` that *shadows* the working install — which is what Step 2 is for.

## Step 2 — resolve the real binary

If `where psamvault-mcp` (Windows) or `which -a psamvault-mcp` (Linux, macOS) prints **more than one**
path, configure the host with the **absolute path** of the pipx copy, never the bare name:

| OS | Typical pipx path |
|---|---|
| Windows | `%USERPROFILE%\.local\bin\psamvault-mcp.exe` |
| Linux / macOS | `~/.local/bin/psamvault-mcp` |

A bare name that resolves to `...\Python3xx\Scripts\psamvault-mcp.exe` is the global-install copy — not
the one you just installed.

## Step 3 — wire it into the host

Every host config is three things: the **command** (absolute path preferred), optional **args** (empty —
with no arguments the server speaks stdio), and an **env** map that clears `PYTHONPATH`.

```json
{
  "mcpServers": {
    "psamvault": {
      "command": "C:\\Users\\YOU\\.local\\bin\\psamvault-mcp.exe",
      "args": [],
      "env": { "PYTHONPATH": "" }
    }
  }
}
```

```yaml
# Hermes — ~/.hermes/config.yaml
mcp_servers:
  psamvault:
    command: C:\Users\YOU\.local\bin\psamvault-mcp.exe
    enabled: true
    env:
      PYTHONPATH: ""
```

```toml
# Grok Build — ~/.grok/config.toml
[mcp_servers.psamvault]
command = "C:\\Users\\YOU\\.local\\bin\\psamvault-mcp.exe"
args = []
enabled = true
startup_timeout_sec = 30
tool_timeout_sec = 300

[mcp_servers.psamvault.env]
PYTHONPATH = ""
```

For Goose and every other client, and for the full list of host config locations, see
[Installation](../installation.md#wire-it-into-a-host).

`PYTHONPATH: ""` is not decoration: a machine-wide `PYTHONPATH` (a Hermes install sets one) is inherited
by the server subprocess and outranks its own `site-packages`, which is how a pipx-installed server ends
up crashing on `ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'`. Clearing it for
this subprocess is the fix — see [PYTHONPATH conflict](../troubleshooting/PYTHONPATH-CONFLICT.md).

## Step 4 — reload the host

Editing config does **not** inject tools into a session that is already running.

| Host | How to reload |
|---|---|
| Hermes | restart the gateway, or open a new session |
| Claude Desktop | fully quit and reopen (not just close the window) |
| Grok Build | restart the session, or `/mcps` then `r` |
| Goose | reload extensions, or restart the session |
| Cursor / Cline | reload the window / reconnect the server |

## Step 5 — verify, in this order

```bash
psamvault-mcp selfcheck    # installed version vs what a NEW session gets (exit 1 on mismatch)
psamvault-mcp compat --check   # server ↔ usage-skill pairing (exit 1 = drift)
```

Then, from a new session:

1. the host shows the server **connected with 13 tools** (v0.5.0 and later);
2. `get_version` — needs no login, returns the version and the compatibility block;
3. `list_vault_sites` — needs a live vault session, so it doubles as the `psamvault login` check;
4. `search_vault_tools("")` — returns the full tool list.

A tool count is **not** a version check: v0.5.0 removed one tool and added another, leaving the count at
13. Only the version string and the tool fingerprint distinguish releases.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Server starts, host discovers **zero** tools | installed without deps (`--no-deps` drops `ruamel-yaml`) | reinstall **with** deps against that venv's Python |
| `ModuleNotFoundError: pydantic_core._pydantic_core` | a machine-wide `PYTHONPATH` leaked in | set `env.PYTHONPATH: ""` in the host config |
| `command not found`, or an old version reported | a stale shim earlier on `PATH` | use the absolute pipx path (Step 2); `psamvault-mcp doctor` names the entry points it finds |
| `psamvault-mcp` with no arguments "hangs" | not a bug — that is the stdio server waiting on stdin | use `--version` / `--help` for smoke tests |
| Tools missing although config is right | the session predates the config edit | reload the host (Step 4) |
| Every tool errors with "run psamvault login" | no/expired vault session | user runs `psamvault login` |

## Rules for the agent

- Never ask for, accept, or echo a password, API key or token in chat — not even one the user pastes
  first. Route them to the vault instead.
- Never write a secret into a host config, a repository file, or a commit.
- Report the verdict first (version, tool count, host state), then the detail.

## See also

- [Installing psamvault-mcp](../installation.md) — the narrative version.
- [MCP install & connect](../troubleshooting/MCP-INSTALL-AND-CONNECT.md) — when the install looks broken.
- [Skill — upgrade psamvault-mcp](./upgrade.md) — the next step once it works.
