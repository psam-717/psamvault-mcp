---
title: Installation
description: Install psamvault-mcp, verify it runs, and wire it into an MCP host (Hermes, Claude Desktop, Cursor, Cline, Goose, Grok Build).
order: 20
---

# Installation

This page covers installing the server, verifying it runs, wiring it into an MCP host, and
upgrading. For the deep repair playbook — PATH shadowing, corrupt pipx metadata, a half-linked
install, session reload — read
[MCP install & connect](./troubleshooting/MCP-INSTALL-AND-CONNECT.md) rather than re-deriving it
from this page.

## Prerequisites

- Python ≥ 3.11
- [psamvault](https://pypi.org/project/psamvault/) installed and logged in

```bash
pipx install psamvault
psamvault configure
psamvault login
```

- Playwright Chromium browser (needed only for `browser_login`)

`pipx` does not put `playwright` on PATH. Use the interpreter inside the pipx venv, and ask pipx
where that venv is. Current pipx (1.7 and later) uses `~/.local/share/pipx` on Linux,
`~/Library/Application Support/pipx` on macOS, and `%LOCALAPPDATA%\pipx\pipx` on Windows. An older
install keeps `~/.local/pipx` (or `%USERPROFILE%\pipx` on Windows) when that directory already
exists. Do not hardcode either path.

```bash
VENVS="$(pipx environment --value PIPX_LOCAL_VENVS)"
"$VENVS/psamvault-mcp/bin/python" -m playwright install chromium          # Linux / macOS
```

```powershell
$venvs = (pipx environment --value PIPX_LOCAL_VENVS).Trim()
& "$venvs\psamvault-mcp\Scripts\python.exe" -m playwright install chromium   # Windows
```

The server needs an active vault session. If the session is missing or expired, the fix is
`psamvault login` run by the user in a terminal — an agent cannot supply the password.

## Install the server

**Prefer pipx** (isolated venv). Avoid `pip install` into system Python — on Windows this often
leaves a broken shim on PATH that shadows the good install.

```bash
pipx install psamvault-mcp
```

`pipx` is the recommended (and best-supported) route because it puts the entry point under your user
local bin and keeps the dependency set isolated. `uv tool install psamvault-mcp` and
`pip install psamvault-mcp` also install the package from PyPI, but they place the console script
wherever that tool puts scripts — which is exactly the shadowing situation the host config below is
designed to survive, so resolve the absolute path carefully (next section).

## Verify the server runs

```bash
psamvault-mcp --version   # safe smoke test (prints and exits)
psamvault-mcp --help      # same — usage text, then exit
```

Both flags are handled by the entry point itself: `--version` / `-V` prints the package version and
exits, `--help` / `-h` prints usage and exits.

> **A bare `psamvault-mcp` is not a hang.** Running the server with no arguments starts the stdio
> MCP protocol loop and waits on stdin. That is the normal mode; use `--version` / `--help` for
> smoke tests and let the host spawn the process for real use.

### Resolve the real binary

If `where psamvault-mcp` (Windows) / `which -a psamvault-mcp` (Linux, macOS) shows **more than one**
path, configure your MCP client with the **absolute path** under your user local bin (pipx), not the
system `Python3xx\Scripts` copy:

| OS | Typical pipx path |
|----|-------------------|
| Windows | `%USERPROFILE%\.local\bin\psamvault-mcp.exe` |
| Linux / macOS | `~/.local/bin/psamvault-mcp` |

Avoid bare names that resolve to `...\Python3xx\Scripts\psamvault-mcp.exe` (global pip install) when a
pipx copy exists.

## Wire it into a host

### Hardened config (recommended, JSON-style clients)

Always pass an absolute `command` and clear `PYTHONPATH` so other tools (e.g. Hermes) cannot
contaminate imports. This is the shape for Claude Desktop, Cursor, Cline, and the many clients that
read an `mcpServers` map:

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

On Linux/macOS use `/home/YOU/.local/bin/psamvault-mcp` (no `.exe`), and for Cursor/Cline point the
file at the location that client reads (for example a project or user `.mcp.json`).

### Hermes

Add this block to `~/.hermes/config.yaml` under `mcp_servers`:

```yaml
mcp_servers:
  psamvault:
    command: C:\Users\YOU\.local\bin\psamvault-mcp.exe
    enabled: true
    env:
      PYTHONPATH: ""
```

The simpler form from the project readme — with the bare command name — is also valid when only one
`psamvault-mcp` exists on PATH:

```yaml
mcp_servers:
  psamvault:
    command: psamvault-mcp
    enabled: true
```

Prefer the absolute path plus `env.PYTHONPATH: ""` on any machine where a global `PYTHONPATH` is set
(see the gotcha below).

### Claude Desktop

Config file location:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "psamvault": {
      "command": "psamvault-mcp"
    }
  }
}
```

Restart Claude Desktop after saving — fully quit and reopen, not just close the window.

### Goose

Option A, one-click deeplink — click or paste this URL into your browser while Goose Desktop is
running:

```
goose://extension?cmd=psamvault-mcp&timeout=300&id=psamvault&name=psamVault&description=Use%20stored%20credentials%20without%20exposing%20them%20to%20the%20agent
```

Goose will prompt you to confirm, then the extension is added instantly.

Option B, Goose Desktop UI: open Goose Desktop, click the **sidebar button** (top-left) →
**Extensions** → **Add custom extension**, then fill in the form:

| Field | Value |
|---|---|
| **Type** | `Standard IO` |
| **ID** | `psamvault` |
| **Name** | `psamVault` |
| **Description** | `Use stored credentials without exposing them to the agent` |
| **Command** | `psamvault-mcp` |
| **Timeout** | `300` |

Click **Add**. The extension appears in your Extensions list — toggle it on to activate it.

Option C, config file (advanced): edit `~/.config/goose/config.yaml` and add the following under
`extensions:`.

```yaml
extensions:
  psamvault:
    name: psamVault
    cmd: psamvault-mcp
    args: []
    enabled: true
    type: stdio
    timeout: 300
```

Save the file and restart Goose (or reload the session).

### Grok Build

Add the server to `~/.grok/config.toml` using the **absolute** pipx path:

```toml
[mcp_servers.psamvault]
command = "C:\\Users\\YOU\\.local\\bin\\psamvault-mcp.exe"
args = []
enabled = true
startup_timeout_sec = 30
tool_timeout_sec = 300

[mcp_servers.psamvault.env]
PYTHONPATH = ""
```

If you already use `~/.mcp.json`, the JSON shape above works there too.

### Any other MCP client

Any MCP client supporting stdio transport can use psamvault-mcp. **Prefer the absolute pipx path**
over a bare command name:

```json
{
  "mcpServers": {
    "psamvault": {
      "command": "/home/YOU/.local/bin/psamvault-mcp",
      "env": { "PYTHONPATH": "" }
    }
  }
}
```

## The `PYTHONPATH` gotcha

A system-level `PYTHONPATH` (for example one added by a Hermes install) leaks into every Python
process on the machine, including the MCP server subprocess. That is how a pipx-installed server ends
up importing `pydantic` from another application's venv and crashing with:

```
ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'
```

The fix is one line in the host config — an empty `PYTHONPATH` for the server subprocess:

```json
"env": { "PYTHONPATH": "" }
```

or, in YAML/TOML hosts, the equivalent `env.PYTHONPATH = ""` / `[mcp_servers.psamvault.env]`. The full
root-cause write-up, including how to confirm you are affected, is in
[PYTHONPATH conflict](./troubleshooting/PYTHONPATH-CONFLICT.md).

## OS keyring

`--version`, `--help`, and `selfcheck` do not need a keyring and do not crash when none is
reachable. The server still starts. Vault tools then return an error of the form
`✗ <what failed> — <what to do next>` instead of a traceback. There is no plaintext fallback:
a `PSAMVAULT_PEPPER` line left in `config.env` stays in that file and is not loaded when the
keyring cannot store it.

A locked Secret Service collection, or a daemon with no default collection yet, does not
open a GUI prompt and does not block startup. The server reads the `default` alias and
whether that collection is locked. It does not call `Unlock` or `CreateCollection`. The
next vault tool call looks again, so unlocking the keyring takes effect without restarting
the host.

| Platform | What the keyring backend reads | What you put in `env` |
|---|---|---|
| Linux | Secret Service, via jeepney, which reads **only** `DBUS_SESSION_BUS_ADDRESS` | `PYTHONPATH=""`. The server fills in `DBUS_SESSION_BUS_ADDRESS` from `/run/user/<uid>/bus` when that socket exists and is owned by this user, and sets `XDG_RUNTIME_DIR` when the directory exists and the variable is unset. Pass both explicitly when the socket is not visible (some containers). The MCP SDK does not expand `${...}`. |
| macOS | The login keychain. `HOME` is enough; `KEYCHAIN_PATH` is an optional override | `PYTHONPATH=""` |
| Windows | Credential Manager. The SDK already passes `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `SYSTEMROOT` | `PYTHONPATH=""` |

The official Python MCP SDK (1.30) and the TypeScript SDK both build that environment from a short
allow-list — POSIX: `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`; Windows adds the Credential
Manager set above and, in the TypeScript SDK, `PROGRAMFILES` — then overlay your `env` block.
`DBUS_SESSION_BUS_ADDRESS` is not on the list, which is why a working Secret Service used to raise
`NoKeyringError` under the documented config. macOS and Windows do not need an extra variable.

A headless machine with no Secret Service, no login keychain, or no Credential Manager can run the
smoke tests. It cannot use vault tools until one of those is available. This server does not read
`PSAMVAULT_VEK` or `PSAMVAULT_TOKEN`.

Full table and the explicit Linux `env` block:
[Configuration — OS keyring](./reference/configuration.md#os-keyring).

## Confirm the tools loaded

Editing config **does not** always inject tools into an already-running agent session. Restart or
reload the host first:

| Host | How to reload |
|------|----------------|
| Grok Build | Restart the session, or open `/mcps` and press `r` |
| Hermes | Restart/reload gateway or session |
| Claude Desktop | Fully quit and reopen |
| Goose | Reload extensions / restart session |

Then verify, in this order:

1. The host reports the server **healthy** / tools discovered — **13 tools** (v0.5.0 and later).
2. Call `get_version` — it needs no login and returns the installed version plus the compatibility
   block (see [Configuration](./reference/configuration.md)).
3. Call `list_vault_sites` — this one needs an active vault session, so it is also your
   `psamvault login` check.
4. `search_vault_tools("")` returns the full tool list.

A tool count alone is **not** a version check: v0.5.0 removed one tool and added another, leaving the
count at 13 — only the tool fingerprint reveals that.

For a two-way check of what is installed against what a *new* session will actually serve:

```bash
psamvault-mcp selfcheck   # exit 1 on mismatch
psamvault-mcp doctor      # why an install looks broken: entry points, pipx records, processes holding the venv
```

## Upgrading

```bash
pipx upgrade psamvault-mcp
```

`pipx reinstall psamvault-mcp` (or a fresh `pipx install psamvault-mcp`) is the clean repair when
pipx metadata is corrupt and `upgrade` refuses to touch the package.

**Never install the wheel with `--no-deps`.** The 0.4.5 release added a runtime dependency,
`ruamel-yaml>=0.19.1`. If a new wheel is installed into an existing uv-managed pipx venv without deps
(e.g. `uv pip install --no-deps ...whl` because that venv has no pip), the dependency is skipped and
the server crashes at import — `ModuleNotFoundError: No module named 'ruamel'` — so the host
discovers **zero** psamvault tools. Reinstall **with** deps against the venv's own Python:

```bash
# Linux / macOS — pipx reports the venv; do not hardcode ~/.local/pipx or ~/.local/share/pipx
uv pip install --python "$(pipx environment --value PIPX_LOCAL_VENVS)/psamvault-mcp/bin/python" \
  --force-reinstall dist/psamvault_mcp-0.4.5-py3-none-any.whl
```

```powershell
# Windows
$py = "$((pipx environment --value PIPX_LOCAL_VENVS).Trim())\psamvault-mcp\Scripts\python.exe"
uv pip install --python $py --force-reinstall dist/psamvault_mcp-0.4.5-py3-none-any.whl
```

Verify afterwards with `psamvault-mcp --version` (it must print a version and exit cleanly, with no
traceback), then restart the host session.

The server also exposes an upgrade path that keeps the **usage skill** in lockstep with the installed
release:

```bash
psamvault-mcp compat --check              # exit 0 in sync, 1 drift; a missing Hermes skill is not drift
psamvault-mcp compat --apply              # install the target release and update the skill
psamvault-mcp compat --apply --latest     # ...or the newest release published on PyPI
```

See [Configuration](./reference/configuration.md) for the contract and the exit codes.

## See also

- [MCP install & connect](./troubleshooting/MCP-INSTALL-AND-CONNECT.md) — the agent repair playbook.
- [PYTHONPATH conflict](./troubleshooting/PYTHONPATH-CONFLICT.md) — native-module import errors.
- [Configuration](./reference/configuration.md) — everywhere the server reads settings from.
- [Tool reference](./reference/tools.md) — the 13 registered tools.
