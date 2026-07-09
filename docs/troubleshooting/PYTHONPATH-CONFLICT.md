# PYTHONPATH Conflict: MCP Server Fails to Load

**Date:** 2026-06-19
**Package:** psamvault-mcp v0.4.0
**Affected users:** Anyone running Hermes Agent (or any Python-based tool) with `PYTHONPATH` set globally

> **Also read:** [MCP-INSTALL-AND-CONNECT.md](./MCP-INSTALL-AND-CONNECT.md) for PATH
> shadowing, corrupt pipx installs, absolute-path config, and session reload.

---

## Problem

After upgrading psamvault-mcp from v0.3.0 to v0.4.0 and restarting the Hermes Agent gateway, the MCP server failed to connect. The gateway log showed:

```
MCP server 'psamvault' failed initial connection after 3 attempts, giving up
```

The MCP server process crashed immediately on startup with:
```
ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'
```

## Root Cause

The issue was **not in psamvault-mcp's code** and **not in Hermes Agent's MCP transport**. It was an **environment contamination** problem caused by a system-level `PYTHONPATH` environment variable.

### The Chain of Events

1. **Hermes Agent installation** created a virtual environment at `C:\Users\USER\AppData\Local\hermes\hermes-agent\venv\`
2. Hermes' installation process added the following paths to the **system-level** `PYTHONPATH` environment variable:
   - `C:\Users\USER\AppData\Local\hermes\hermes-agent`
   - `C:\Users\USER\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages`
3. These paths are **Windows system environment variables** — they affect **every Python process** on the machine, not just Hermes itself
4. When the MCP server process (`psamvault-mcp`) starts as a subprocess of Hermes, it inherits this `PYTHONPATH`
5. The MCP server's own virtual environment (installed via `pipx`) has its own copies of packages like `mcp`, `pydantic`, and `pydantic-core`
6. **`PYTHONPATH` takes priority** over the process's own `site-packages` directory
7. The MCP server loads `pydantic` from the Hermes venv instead of its own pipx venv
8. The Hermes venv's `pydantic-core` is a **native/Cython module** compiled specifically for that venv's Python version/environment
9. This native module fails to load in the pipx venv's Python process — hence `ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'`

### Why It Seemed Intermittent

- Before v0.4.0, the MCP server (v0.3.0) happened to work because its dependency chain loaded in a different order that didn't trigger the native module mismatch
- The `update_check` function in v0.4.0 imports `httpx` which triggered the full pydantic import chain
- Sometimes it worked because `PYTHONPATH` paths were resolved before the pipx paths but didn't cause a crash if the module versions happened to be compatible

### Why Both `pip install` and `pipx` Failed

- **`pip install`** (into system Python): Installed to `C:\Python314\Lib\site-packages`. System Python 3.14 didn't have `mcp` installed at all — it was relying on the Hermes venv's `mcp` via PYTHONPATH
- **`pipx install`**: Created an isolated venv at `C:\Users\USER\pipx\venvs\psamvault-mcp`, but when the pipx process's Python started, PYTHONPATH added the Hermes venv paths **before** the pipx venv paths, so the wrong `pydantic-core` was loaded

---

## Solution

Two things were needed:

### 1. Clear `PYTHONPATH` for the MCP subprocess

In the Hermes config (`config.yaml`), the MCP server entry now includes:

```yaml
mcp_servers:
  psamvault:
    command: C:\Users\USER\pipx\venvs\psamvault-mcp\Scripts\psamvault-mcp.exe
    enabled: true
    env:
      PYTHONPATH: ""
```

This tells Hermes' MCP transport to launch the subprocess with `PYTHONPATH` explicitly emptied, so the process only uses its own venv.

### 2. Use the absolute path to the pipx-installed executable

Instead of relying on `psamvault-mcp` being on PATH (which pointed to the system Python's broken shim at `C:\Python314\Scripts\psamvault-mcp.exe`), the config now uses the full path to the pipx venv's executable:

```
C:\Users\USER\pipx\venvs\psamvault-mcp\Scripts\psamvault-mcp.exe
```

---

## Prevention

### For psamvault-mcp (package authors)

There's nothing the package can do about PYTHONPATH contamination — it's a deployment/env issue. However:

- **Add a startup guard** that checks if the process is loading packages from unexpected paths and warns the user
- Example: On startup, check `sys.path` for entries outside the package's own venv or site-packages and log a warning with the offending paths

### For Hermes Agent / other tools that spawn MCP subprocesses

- When launching MCP server subprocesses, **always clear `PYTHONPATH`** by default (or at least provide the option to do so)
- The MCP config should support `env` overrides out of the box (which it does — this is how we fixed it)

### For users

- **Don't set `PYTHONPATH` globally** unless you really need to. If Hermes requires it, set it only for the Hermes process, not system-wide
- **Install MCP servers via `pipx`** — it creates isolated venvs that should work independently
- If you see `ModuleNotFoundError` for a native module (`_pydantic_core`, `_cffi_backend`, etc.) in a pipx-installed tool, check your `PYTHONPATH` first

---

## How to Check If You're Affected

```bash
# Check if PYTHONPATH is set
echo $PYTHONPATH    # Linux/Mac
echo %PYTHONPATH%   # Windows cmd
$env:PYTHONPATH     # Windows PowerShell

# Check if it contaminates your MCP server's Python
python -c "import sys; print([p for p in sys.path if 'hermes' in p.lower()])"
# If this prints anything, you're affected
```

## How to Fix

1. **Remove the system-level PYTHONPATH variable** (Windows: System Properties → Environment Variables → delete `PYTHONPATH`)
2. **OR** set `PYTHONPATH: ""` in the MCP server's `env` config as shown above
3. **OR** set `PYTHONPATH` only for the Hermes process, not globally (e.g., in a startup script)
