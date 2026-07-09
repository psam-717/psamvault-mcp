# MCP Install & Connect: Agent Playbook

**Audience:** AI agents (and humans) installing or repairing **psamvault-mcp** for any MCP host  
(Grok Build, Hermes, Goose, Claude Desktop, Cursor, Cline, etc.)

**Package:** `psamvault-mcp` (PyPI)  
**Related:** [PYTHONPATH-CONFLICT.md](./PYTHONPATH-CONFLICT.md) for Hermes + global `PYTHONPATH` issues

---

## Goal

A healthy setup means **all** of the following:

1. A working `psamvault-mcp` binary (prefer **pipx**, not system `pip`)
2. MCP client config points at that binary (prefer **absolute path**)
3. User is logged in: `psamvault login`
4. Host has **restarted / reloaded MCP** after config changes
5. Agent can call `get_version` or `list_vault_sites` successfully

---

## Mandatory agent rules while installing

| Do | Don't |
|----|--------|
| Install with **`pipx install psamvault-mcp`** | `pip install` into system Python (fragile deps, PATH collisions) |
| Put the **absolute path** to the pipx binary in MCP config | Rely on bare `psamvault-mcp` if multiple copies exist on PATH |
| Verify with a **host doctor / restart**, then `get_version` | Assume config file edits alone reload tools mid-session |
| Tell the user to run **`psamvault login`** if vault tools fail auth | Run `psamvault get` / read `~/.psamvault/` to “test” secrets |
| Clear **`PYTHONPATH`** for the MCP subprocess if contaminated | Ignore `ModuleNotFoundError: pydantic` / `pydantic_core` without checking env |

---

## Recommended install (all platforms)

### 1. Prerequisites

```bash
# CLI vault (separate package)
pipx install psamvault
psamvault configure
psamvault login

# MCP server
pipx install psamvault-mcp

# Browser for browser_login
# Use the Playwright that ships with the pipx venv when possible:
#   ~/.local/pipx/venvs/psamvault-mcp/...  (Linux/macOS)
#   %USERPROFILE%\pipx\venvs\psamvault-mcp\Scripts\playwright.exe  (Windows)
playwright install chromium
```

### 2. Resolve the real binary path

```bash
# Prefer the pipx-managed binary under the user local bin dir
# Linux/macOS:
which -a psamvault-mcp
ls -la ~/.local/bin/psamvault-mcp

# Windows (PowerShell):
where.exe psamvault-mcp
Get-Command psamvault-mcp -All | Format-Table Source
Test-Path "$env:USERPROFILE\.local\bin\psamvault-mcp.exe"
```

**If more than one path appears**, always configure the one under:

| OS | Typical good path |
|----|-------------------|
| Windows | `%USERPROFILE%\.local\bin\psamvault-mcp.exe` |
| Linux/macOS | `~/.local/bin/psamvault-mcp` |

Avoid bare names that resolve to `...\Python3xx\Scripts\psamvault-mcp.exe` (global pip install) when a pipx copy exists.

### 3. Verify the binary without hanging the agent

`psamvault-mcp` is a **stdio MCP server**. Running it with no flags starts the protocol loop and waits on stdin — that can look like a hang.

```bash
# Safe checks (print and exit — do not leave the process running):
psamvault-mcp --version
psamvault-mcp --help
```

Do **not** use a long-running bare `psamvault-mcp` invocation as a “smoke test” inside an agent shell unless you are ready to kill the process.

### 4. Configure the MCP host with absolute path + clean env

**JSON-style clients** (Claude Desktop, many Cursor/`.mcp.json` setups):

```json
{
  "mcpServers": {
    "psamvault": {
      "command": "C:\\Users\\YOU\\.local\\bin\\psamvault-mcp.exe",
      "args": [],
      "env": {
        "PYTHONPATH": ""
      }
    }
  }
}
```

**Grok Build** (`~/.grok/config.toml`):

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

**Hermes** (`~/.hermes/config.yaml`):

```yaml
mcp_servers:
  psamvault:
    command: C:\Users\YOU\.local\bin\psamvault-mcp.exe
    enabled: true
    env:
      PYTHONPATH: ""
```

Linux/macOS: use `/home/YOU/.local/bin/psamvault-mcp` (no `.exe`).

Setting `PYTHONPATH: ""` prevents another app’s venv (e.g. Hermes) from contaminating imports. See [PYTHONPATH-CONFLICT.md](./PYTHONPATH-CONFLICT.md).

### 5. Reload the host session

Editing config **does not** always inject tools into an already-running agent session.

| Host | How to reload |
|------|----------------|
| Grok Build | Restart the session, or open `/mcps` and press `r` |
| Hermes | Restart/reload gateway or session |
| Claude Desktop | Fully quit and reopen |
| Goose | Reload extensions / restart session |

Then verify:

1. Host reports the server **healthy** / tools discovered (~11 tools)
2. Call `get_version`
3. Call `list_vault_sites` (needs `psamvault login`)

---

## Failure modes agents actually hit

### A. Config points to a missing executable

**Symptom:** Host doctor: `command not found` for `...\psamvault-mcp.exe`

**Cause:** Config was written for a path that was never installed, or a broken pipx install left no shim.

**Fix:**

```bash
pipx uninstall psamvault-mcp   # if metadata is corrupt, also delete the venv dir
pipx install psamvault-mcp
# Confirm the path in config matches the real file
```

### B. Corrupt / legacy pipx metadata

**Symptom:**

```text
package psamvault-mcp has missing internal pipx metadata
Not upgrading psamvault-mcp. It has missing internal pipx metadata.
```

or uninstall crashes (`pyvenv.cfg` missing, JSON decode errors).

**Fix (Windows example):**

```powershell
pipx uninstall psamvault-mcp
# If uninstall fails, remove the broken venv manually:
Remove-Item -Recurse -Force "$env:USERPROFILE\pipx\venvs\psamvault-mcp" -ErrorAction SilentlyContinue
pipx install psamvault-mcp
pipx list   # should show a healthy package version
```

### C. PATH shadowing — broken system install wins

**Symptom:**

```text
ModuleNotFoundError: No module named 'pydantic'
# or
ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'
```

when running the first `psamvault-mcp` on PATH, while a pipx install also exists.

**Cause:** Global install under e.g. `C:\Python314\Scripts\psamvault-mcp.exe` is incomplete or contaminated; it appears **before** `~\.local\bin` on PATH.

**Fix:**

1. Configure MCP with the **absolute pipx path** (do not rely on PATH order).
2. Optionally remove the broken global package:
   ```bash
   python -m pip uninstall psamvault-mcp
   ```
3. If errors mention `pydantic_core` and Hermes is installed, clear `PYTHONPATH` (section above + [PYTHONPATH-CONFLICT.md](./PYTHONPATH-CONFLICT.md)).

### D. Vault session expired / not logged in

**Symptom:** MCP server starts and tools appear, but vault calls fail with session timeout / not logged in. CLI:

```text
Session timed out after inactivity.
 -> Run  psamvault list  to refresh your session, then try again.
```

**Fix (user interactive — agents cannot supply the password):**

```bash
psamvault login
# or sometimes:
psamvault list
psamvault whoami
```

Tell the user to run this in their terminal, then retry the MCP tool.

### E. Tools missing in the current chat after a successful install

**Symptom:** `grok mcp doctor psamvault` (or host equivalent) is healthy, but the agent’s tool list has no `psamvault__*` tools.

**Cause:** MCP servers are usually attached at **session start**. Mid-session config fixes are invisible until reload.

**Fix:** Restart the agent session / refresh MCP list, then call `get_version`.

### F. Agent treats bare `psamvault-mcp` as a hang

**Symptom:** Shell tool times out after launching `psamvault-mcp` with no args.

**Cause:** Process is correctly waiting for MCP stdio JSON-RPC.

**Fix:** Use `--version` / `--help` for smoke tests. Let the **MCP host** spawn the process for real use.

---

## Agent decision tree (copy this)

```
User wants psamvault MCP connected
│
├─ Is the host already listing healthy psamvault tools?
│    YES → get_version → list_vault_sites
│           ├─ works → done
│           └─ auth error → ask user: psamvault login
│
└─ NO → install/repair path:
     1. pipx install psamvault-mcp  (force clean reinstall if metadata broken)
     2. Resolve absolute path to pipx binary (ignore broken system Scripts copy)
     3. Write host config with that absolute path + PYTHONPATH=""
     4. Ask user to reload/restart host MCP
     5. Verify doctor/handshake + get_version
     6. If tools fail auth → psamvault login
```

---

## Quick host verification commands

**Grok Build:**

```bash
grok mcp doctor psamvault
# Expect: command found, handshake OK, ~11 tools discovered
```

**Any host after connect:**

- `get_version` → version string (no login required)
- `search_vault_tools("")` → tool list
- `list_vault_sites` → needs active vault session

---

## Security reminder (install does not change this)

Even while debugging installs:

- Never run `psamvault get` / `psamvault show` to “verify” keys
- Never read vault files under `~/.psamvault/` for secret values
- Never print API keys into the chat
- Use only MCP tools once the server is connected

---

## See also

- [PYTHONPATH-CONFLICT.md](./PYTHONPATH-CONFLICT.md) — Hermes global `PYTHONPATH` breaking pipx imports
- [README.md](../../README.md) — installation and client config examples
- [AGENTS.md](../../AGENTS.md) — agent security rules and tool workflows
