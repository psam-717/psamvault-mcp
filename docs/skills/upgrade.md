---
title: "Skill — upgrade psamvault-mcp"
description: Agent-followable procedure for upgrading an installed psamvault-mcp to a newer release, on Windows, macOS and Linux, and proving which version a new session gets.
order: 140
---

# Skill: upgrade psamvault-mcp

**Load this when** the server is already installed and the user wants a newer version — or the installed
one is broken and needs reinstalling.

**Rule zero — never install a build you have not verified over a live install.** Verification happens in
a throwaway environment (a separate venv, a probe, a scratch host) first. An upgrade of the *live* install
is a deliberate act, done afterwards, once the user agrees.

**The trap this skill exists for:** on Windows a running `python.exe` cannot be replaced, so a
venv-recreating install fails while any agent session is live — and on macOS/Linux the same install
usually *succeeds*, which is worse, because the running processes keep the **old code in memory** and the
user is told they upgraded when nothing they are using changed. Both cases end the same way: stop the
holders, install, then verify from a **fresh** process.

## Step 1 — find out what is actually installed

```bash
psamvault-mcp --version         # what is on disk
psamvault-mcp selfcheck         # on-disk version vs what a NEW session would get (exit 1 on mismatch)
psamvault-mcp doctor            # entry points, pipx records, and which processes hold the venv
psamvault-mcp compat --check    # server ↔ usage-skill pairing (exit 1 = drift)
```

| Command | Answers |
|---|---|
| `--version` | which code is on disk |
| `selfcheck` | whether a **new** session will actually load it (it spawns a fresh server) |
| `doctor` | *why* an install looks wrong: PATH shims, pipx metadata drift, `venv_holders` |
| `compat --check` | whether the installed usage skill matches the installed server |

⚠️ **A running agent session cannot answer "which version am I running?"** — it serves the code loaded
when it started. `pipx list` is also unreliable (its metadata lags); trust `selfcheck` and
`importlib.metadata`. An agent reporting its own stale version after a successful install is expected,
not a bug.

## Step 2 — choose the install path

### A. The newest published release (the normal case)

```bash
psamvault-mcp compat --apply --latest
```

`--latest` asks PyPI for the newest **published** release and installs that, then syncs the usage skill.
Plain `compat --apply` targets only the newest release recorded in the contract **inside the wheel you
already have**, so an older install can never reach a newer release that way — add `--latest`, or install
from the index directly.

Direct index install, into the existing venv (no venv recreation, so nothing is locked out):

```powershell
# Windows (PowerShell) — note $env:, a bare $LOCALAPPDATA expands to nothing here
uv pip install --python "$env:LOCALAPPDATA\pipx\pipx\venvs\psamvault-mcp\Scripts\python.exe" --refresh psamvault-mcp
```

```bash
# macOS / Linux (bash, zsh) — let pipx report where its venvs live
uv pip install --python "$(pipx environment --value PIPX_LOCAL_VENVS)/psamvault-mcp/bin/python" --refresh psamvault-mcp
```

`pipx upgrade psamvault-mcp` also works (it upgrades inside the venv rather than recreating it) — its
displayed metadata is the only part that lags.

> In PowerShell, `$LOCALAPPDATA` is an ordinary undefined variable and expands to the empty string, so
> the path silently loses its drive and uv answers *"No virtual environment or system Python installation
> found for path `/pipx/pipx/venvs/...`"*. A path with no drive letter **is** this mistake: use
> `$env:LOCALAPPDATA` there, `$LOCALAPPDATA` in bash.

> The simple index caches for ~10 minutes. Right after a release is published, keep `--refresh`, or
> install the wheel URL from the registry JSON.

### B. A pre-release: merged to GitHub, not yet on PyPI

```bash
cd <checkout>
rm -rf dist/*
uv build                       # dist/psamvault_mcp-<VER>-py3-none-any.whl
uv pip install --python "<that venv's python>" --force-reinstall dist/psamvault_mcp-<VER>-py3-none-any.whl
```

**Always install with dependencies — never `--no-deps`.** A `--no-deps` install skips `ruamel-yaml`,
after which the server starts and discovers **zero** tools. Use `--force-reinstall` when the version
number is unchanged (a local fix); a version bump upgrades without it. `compat --apply --from-git` does
this whole dance for you — snapshot, install, smoke-test, roll back on failure — and `--pull` fetches
`origin/main` first.

### Where each command gets its code

| Command | Code comes from |
|---|---|
| `compat --apply` | PyPI, pinned to the contract **inside your installed wheel** |
| `compat --apply --latest` | PyPI, newest published |
| `compat --apply --from-git [--pull]` | the local checkout (`PSAMVAULT_MCP_REPO`, else discovered) |
| `compat --sync-skill` | the skill clone only — never touches the server |
| `uv pip install --refresh psamvault-mcp` | PyPI, newest published |
| `uv pip install <path or wheel>` | that path — a repo or an editable install (different animal: installing by name from an index afterwards silently detaches the repo link) |

## Step 3 — if the venv must be recreated, retire the holders first

Two different things hold the venv, and stopping only the first is why this fails the first time:

1. **the agent gateways** — possibly one per profile;
2. **the desktop/agent app itself** — every open session owns a server process, and the app spawns a new
   one moments after you kill it. Killing processes without quitting the app looks like it "did nothing".

```bash
hermes gateway stop --all        # --all covers every profile
hermes gateway list              # every row must read "not running"
```

Then **quit the app** (close the chat window), and ask the install itself who is left — never hand-roll a
process filter, it disagrees with `doctor`:

```powershell
$mp = "$env:LOCALAPPDATA\pipx\pipx\venvs\psamvault-mcp\Scripts\psamvault-mcp.exe"
$held = (& $mp doctor --json | ConvertFrom-Json).venv_holders
$held | Format-Table pid, name                                # read this before killing anything
$held | ForEach-Object { Stop-Process -Id $_.pid -Force }
pipx install --force psamvault-mcp
hermes gateway start --all
```

```bash
# macOS / Linux — same source of truth (safer than pgrep -f, which can match your own shell)
pipx install --force psamvault-mcp
```

`doctor --fix` repairs pipx metadata drift in place and refuses while the venv is held — and it is right
to. If it refuses *after* the gateways are stopped, the holder is the app, not the gateway: read the list
it prints rather than repeating the stop.

### Never run these while sessions are live

| Command | What goes wrong |
|---|---|
| `pipx install --force psamvault-mcp==<v>` | **Windows:** fails (`os error 32 … venvlauncher.exe … in use`). **macOS/Linux:** usually succeeds, leaving every live session on the old code |
| `pipx uninstall psamvault-mcp`, or deleting the venv | breaks live sessions mid-turn |
| `uv pip install --upgrade psamvault-mcp` (bare `--upgrade`) | churns unrelated dependencies |
| editing the host config without reloading | running servers keep the old config |

## Step 4 — verify, from a fresh process

```bash
cd C:/                          # a neutral cwd: inside the checkout the local package shadows site-packages
python -c "import importlib.metadata as m; print(m.version('psamvault-mcp'))"
psamvault-mcp selfcheck         # on-disk vs what a new session gets
psamvault-mcp --version         # must print a version and exit cleanly, no traceback
```

Then open a **new** agent session and call `get_version` — the only check that reflects what the user
will actually be served.

## Version lockstep — what the pairing means
<!-- section:upgrade/version-lockstep -->

`mcp_server/compatibility.json` ships inside the wheel and records, for every MCP release, the usage-skill
version and the expected tool fingerprint. `compat --check` compares the **installed** skill against that
record.

- The recorded skill version is a **floor, not a pin**: a skill *ahead* of it is healthy, and only a skill
  *below* it is drift. `compat --apply` on a breaking target needs `--allow-breaking`; `--from-git` installs
  the local checkout instead of the index, and `--from-git --pull` stashes uncommitted work, pulls
  `--ff-only origin main`, then restores it.
- `compat --apply` snapshots the installed skill, smoke-tests the fresh install in a new interpreter from a
  **neutral cwd**, and rolls back to the previous release if the install or the smoke test fails.
- A skill-only improvement ships with `compat --sync-skill` (the clone's working tree, as-is) with no MCP
  release at all.
- **The installed server is authoritative.** Pull the skill to match it — never the reverse.
- On Windows the shim is `psamvault-mcp.exe`.

<!-- /section:upgrade/version-lockstep -->
## Traps

| Trap | Reality |
|---|---|
| "It upgraded" because the install command exited 0 | the sessions running before it keep the old code; only a new session reloads |
| Tool count unchanged | a rename keeps the count — 13 tools has been constant since 0.4.6; the version string and fingerprint are the identifiers |
| `pipx list` shows the old version | its metadata lags; use `importlib.metadata` / `selfcheck` |
| `python -c "from mcp_server import __version__"` reports the new version | you are standing in the checkout — that is the working tree, not the install |
| Blind-retrying an upload that "timed out" | it often completed; verify against the registry instead of re-uploading |
| `compat --apply` says "nothing to apply" | it follows the installed wheel's contract; add `--latest` for the newest published release |

## See also

- [Installation](../installation.md) — install and upgrade, narrative form.
- [Configuration](../reference/configuration.md) — the version-lockstep contract and its exit codes.
- [Skill — install psamvault-mcp](./install.md) — first-time install.
