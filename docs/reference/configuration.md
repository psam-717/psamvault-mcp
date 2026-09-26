---
title: Configuration
description: MCP host config, PYTHONPATH, vault endpoints and env vars, vault state locations, and the version-lockstep contract.
order: 70
---

# Configuration

How the server is configured: what goes in the host config, what the server reads from the
environment, where vault state lives, and how the version contract works.

## Host config entries

The server is a console script — `psamvault-mcp`, registered in `pyproject.toml` as
`psamvault-mcp = "mcp_server.main:main"`. Every host config is therefore three things: the **command**
(absolute path preferred), optional **args**, and an **env** map used to clear `PYTHONPATH`.

| Key | Meaning |
|---|---|
| `command` | The executable to launch. Prefer the absolute pipx path (`%USERPROFILE%\.local\bin\psamvault-mcp.exe` on Windows, `~/.local/bin/psamvault-mcp` on Linux/macOS) when more than one copy is on PATH. |
| `args` | Optional. Empty for a normal install — with no arguments the server serves stdio. |
| `env` | Environment overrides for the subprocess. The one that matters is `PYTHONPATH: ""`. |

JSON-style clients (Claude Desktop, Cursor, Cline, `.mcp.json`):

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

Hermes (`~/.hermes/config.yaml`):

```yaml
mcp_servers:
  psamvault:
    command: C:\Users\YOU\.local\bin\psamvault-mcp.exe
    enabled: true
    env:
      PYTHONPATH: ""
```

Grok Build (`~/.grok/config.toml`):

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

Goose (`~/.config/goose/config.yaml`):

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

Goose, Grok, and Hermes accept host-specific timeout keys (`timeout`,
`startup_timeout_sec`, `tool_timeout_sec`); these are the **host's** keys, not the server's — the
server reads no timeout from its own environment.

> Host configs in the wild sometimes carry a `connect_timeout` key. Nothing in this repository
> defines or reads it, and the server does not look for it. Use your host's documented timeout keys.

## `PYTHONPATH=""` and why

A system-level `PYTHONPATH` — for example one added by a Hermes install — is inherited by every Python
process on the machine, including the MCP server subprocess launched by the host. `PYTHONPATH` entries
take priority over the process's own `site-packages`, so a pipx-installed server can load another
application's `pydantic` and then fail to load that venv's native module:

```
ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'
```

Setting `PYTHONPATH` to the empty string for the server subprocess tells the host to launch it with
`PYTHONPATH` explicitly emptied, so it uses only its own venv. The full root-cause chain, and how to
confirm you are affected, is in
[PYTHONPATH conflict](./../troubleshooting/PYTHONPATH-CONFLICT.md).

## Environment variables the server reads

| Variable | Default | Meaning |
|---|---|---|
| `PSAMVAULT_API_URL` | `https://psam-vault-backend.onrender.com` | The psamvault backend endpoint. Read from `~/.psamvault/config.env` (written by `psamvault configure`) and/or the process environment, whichever is present first. **Only HTTPS URLs are accepted.** A non-HTTPS or malformed value is silently ignored during config loading, and the API client refuses to start with a non-HTTPS base URL — so a compromised config file cannot redirect credential-bearing requests to a plain-HTTP server. |
| `PSAMVAULT_LOG_LEVEL` | `INFO` | Log verbosity; any standard Python level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). All log output goes to **stderr** — stdout is reserved for the MCP JSON-RPC transport. |
| `PSAMVAULT_PEPPER` | — (keychain) | The pepper used by the CLI's key derivation. Read from the OS keychain under the `psamvault` service (`config.pepper`), loaded into the process environment at config-load time. A legacy value found in `config.env` is migrated to the keychain and removed from the file. |
| `PSAMVAULT_MCP_SKIP_UPDATE_CHECK` | unset | When set, the startup "update available" check is skipped and the PyPI index is not contacted. `psamvault-mcp selfcheck` sets this for the diagnostic child it spawns, so a check cannot consume the one-shot notice meant for the next real session. |
| `PSAMVAULT_MCP_VENV` | — | Overrides the venv path used by the install/upgrade helper when it needs to act on a specific environment. |
| `PSAMVAULT_SKILL_CLONE` | — | Overrides where `compat` looks for the local skill clone (the source `--sync-skill` installs from). Without it the clone is `private-skills` **beside the discovered checkout**. |
| `PSAMVAULT_MCP_REPO` | — | Overrides the checkout `compat --from-git` installs from and whose git state `--apply` reports. Without it the checkout is **discovered**: the one this process runs from, else the one the current directory is inside — so `--from-git` in the repo you are standing in works with no configuration. Nothing found raises, naming this variable. |
| `HERMES_HOME` | `~/.hermes` | Base directory for the Hermes export targets: `config.yaml` (`export_key_to_mcp_config`) and `.env` (`export_key_to_env_file`), and the base for the installed skill path the compatibility check reads. On Windows the platform default is `%LOCALAPPDATA%\hermes`. |

Platform variables (`LOCALAPPDATA`, `USERPROFILE`, `VIRTUAL_ENV`) are used only for path resolution on
Windows and for venv detection; they are not settings.

To point at a self-hosted backend, set the variable in `~/.psamvault/config.env`:

```
PSAMVAULT_API_URL=https://your-backend.example.com
```

## Where vault state lives

The server itself stores **no credentials**. All state is written and owned by the psamvault CLI, and
the server reads it:

| Location | Holds |
|---|---|
| `~/.psamvault/config.env` | Non-sensitive configuration: `PSAMVAULT_API_URL`, log level. Written by `psamvault configure`. Plaintext, contains no secrets. |
| `~/.psamvault/session.json` | A **presence marker** only. A live session is confirmed by the keychain entry, not this file: the file holds `{}` after migration, and a mere file creation by another process cannot fake a session because the keychain entry must also exist. |
| OS keychain, service name `psamvault` | Every sensitive session value: `config.pepper`, `session.access_token`, `session.refresh_token`, `session.kdf_salt`, `session.vek`, `session.encrypted_vek`, `session.vek_iv`. On macOS Keychain, Windows Credential Manager, or Linux Secret Service. |
| The psamvault backend | Encrypted vault entries and API key entries (encrypted blobs + IVs). The server decrypts them locally with the VEK; the plaintext never goes back to the backend. |

The VEK is the direct AES-256 key used to decrypt every vault entry. It is stored in the keychain
after being decrypted locally at login time (login_password → HMAC → PBKDF2 → AES-GCM-decrypt → VEK).
No key derivation happens in the MCP server.

Clearing the session (the CLI's logout) deletes all keychain entries and removes the presence marker.
When a required keychain value is missing, the server raises: `Session value '<key>' not found in
keychain. Run psamvault login in your terminal to restore your session.`

## Transport

**stdio is the transport.** Running `psamvault-mcp` with no arguments starts the MCP protocol loop on
stdin/stdout, which is the mode every host config above uses. The entry point's own usage text lists
only:

```
psamvault-mcp              Start MCP server on stdin/stdout (host-managed)
psamvault-mcp --version    Print version and exit
psamvault-mcp --help       Show this help and exit
```

> An HTTP/SSE mode (`psamvault-mcp --http --port 8433`, with `--host` defaulting to `127.0.0.1`, and an
> SSE endpoint at `http://127.0.0.1:8433/sse`) used to be documented in the README and in the usage
> skill. **It does not exist**: the entry point defines no `--http`, `--port` or `--host` flag — it
> accepts `--version`/`-V`, `--help`/`-h` and the maintenance subcommands only — and nothing in the
> package serves HTTP. Stdio is the only transport this server speaks, and the config
> above reflects that.

Because the server is a stdio process, a bare launch that appears to sit there is **not** a hang — it
is waiting for JSON-RPC on stdin. Use `--version` / `--help` for smoke tests.

## Maintenance subcommands

Three subcommands hang off the same entry point (they do not start the server):

```bash
psamvault-mcp compat       # server <-> skill version lockstep (--check, --apply, --apply --latest)
psamvault-mcp selfcheck    # which version is installed AND which one a new session gets
psamvault-mcp doctor       # diagnose PATH/entry-point and pipx drift (--fix to repair)
```

| Command | Purpose |
|---|---|
| `psamvault-mcp compat --check` | Report drift. Exit 0 in sync, 1 on drift. Advisory: a newer *published* release never changes this. An available update is news, not breakage. |
| `psamvault-mcp compat --apply` | Install the target release and bring the skill up to date. Exit codes: **0** ok, **1** install failed, **2** refused (needs `--allow-breaking`), **3** the contract's release is not on PyPI yet (publish it, or use `--from-git`). |
| `psamvault-mcp compat --apply --latest` | Target the newest release published on PyPI — the only way **out** of an install that is already behind. Needs `--allow-breaking` when the target is newer than the install's contract or removes a tool. |
| `psamvault-mcp compat --sync-skill` | Skill-only update: install the clone's newest skill, MCP untouched. |
| `psamvault-mcp compat --apply --from-git` | Install the local repo (merged but not yet released). `--pull` stashes local changes, pulls `--ff-only origin main`, and restores first. |
| `psamvault-mcp compat --check --json` | Machine-readable report. `--no-index` skips the PyPI probe entirely. |
| `psamvault-mcp selfcheck` | Installed vs what a **new session** actually serves. Exit 1 on mismatch — the only way to catch a stale long-lived session. |
| `psamvault-mcp doctor` | Why an install looks broken: entry points pipx never linked, pipx records that disagree with reality, processes holding the venv. `--fix` repairs it — a stale **record** is corrected in place, so sessions can stay up, while relinking entry points still needs a free venv. |

Both subcommands are subcommands of the server's own entry point on purpose, so they need no separate
install and no venv path. On Windows the shim is `psamvault-mcp.exe`. The pre-0.5.3 standalone
`psamvault-compat` console script was **removed** in 0.5.3, because `pipx` never linked it onto `PATH`
— it answered `command not found`.

### Upgrade safety (`--apply`)

The same model as the psamvault CLI's upgrade path: **local work is never lost, and a failed upgrade is
never left installed.**

| Step | What it does |
|---|---|
| Snapshot | Copies the installed skill aside (`backups/backup-<stamp>/`, keeps the newest 5) before overwriting it. |
| `--pull` | Stashes uncommitted work (untracked included), `git pull --ff-only origin main`, then restores it. A failed pull restores immediately and stops; a *conflicting* restore leaves the work parked in a labelled stash and prints the recovery commands. |
| Repo report | `--from-git` prints branch, HEAD, dirtiness and position vs `origin/main` (fetched, or marked "as of the last fetch"), and warns when the venv is an editable/source install that a released wheel would detach. |
| Smoke test | Imports the freshly installed server in a **fresh** interpreter from a neutral cwd — so the repo tree cannot masquerade as the install — and reports its version + tool count. |
| Rollback | If the install fails or the smoke test fails, the previously installed release is put back automatically. |

A release marked **breaking** is never applied without `--allow-breaking` — a silently disappearing
tool is exactly the change a human should see. And the installed server wins: the skill is pulled to
match it, never the reverse, and never rolled back.

## Version lockstep and `compatibility.json`

The server and its usage skill are a **pinned pair**, and the pairing ships *inside the wheel* as
`mcp_server/compatibility.json`. Each release entry records:

| Field | Meaning |
|---|---|
| `mcp` | The release version. |
| `skill` | The skill version that documents it — a **floor**, not a pin (see below). |
| `breaking` | Whether the release is breaking. |
| `added` / `removed` | Tools added and removed in that release. |
| `notes` | A one-line summary of the release. |
| `tools` | The release's tool **fingerprint** — the sorted list of tool names. |

The newest entry in the contract is **0.5.4** (skill floor 1.9.1, **not** breaking — fixes and
documentation only, with the 13 MCP tools and their names unchanged). Older entries are kept, so an
install can be told what it is missing without reaching the network. The contract's own schema, skill repo/path, and
`installed_path` (`skills/psam-custom/psamvault-mcp/SKILL.md` under `HERMES_HOME`) head the file.

**The recorded skill version is a floor, not a pin.** Each release says the *minimum* skill version
that documents it, and any skill at or above that floor is healthy:

| State | Meaning |
|---|---|
| skill ≥ floor | fine — the skill may legitimately move **ahead** of the MCP |
| skill < floor | drift — the skill is older than the server it documents; `--sync-skill` repairs it |
| skill missing | drift (same remedy) |

### The compatibility block returned by `get_version`

`get_version` reports the pairing inline, so an agent can self-check without extra tooling:
`paired_skill_version`, `effective_release`, `newest_release`, `newest_release_skill_version`,
`tool_surface_matches_newest`, `breaking_pending`, `expected_tool_count`, and `check_command`.

The version label alone is not decisive: a git or pre-release install can carry an older label while
already exposing the newest tool surface, so the block compares the **tool fingerprint** before
deciding which contract release the install is treated as. That is also why a tool count cannot
substitute for the check — v0.5.0 removed one tool and added another, leaving the count at 13.

The contract is enforced in both directions:

- `tests/test_compat.py` fails when the newest contract entry disagrees with the code's actual tool
  surface, so a release that forgets to record itself cannot ship quietly.
- `scripts/docs-sync-check.py` is the release-time **docs gate**: it fails when any tracked doc still
  names a tool the code dropped, claims a stale tool count, omits a tool, or disagrees with the
  contract.

## See also

- [Installation](./../installation.md) — host wiring and upgrading.
- [Tool reference](./tools.md) — the 13 tools.
- [MCP install & connect](./../troubleshooting/MCP-INSTALL-AND-CONNECT.md) — the repair playbook.
- [PYTHONPATH conflict](./../troubleshooting/PYTHONPATH-CONFLICT.md) — import contamination.
