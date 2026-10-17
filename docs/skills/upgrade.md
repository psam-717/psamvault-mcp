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
| `compat --check` | whether an installed Hermes usage skill matches the server. A missing skill is not drift |

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
# Windows (PowerShell) — ask pipx. Do not hardcode %LOCALAPPDATA%\pipx\pipx
# (current default) or %USERPROFILE%\pipx (legacy, only if that directory already exists).
$py = "$((pipx environment --value PIPX_LOCAL_VENVS).Trim())\psamvault-mcp\Scripts\python.exe"
uv pip install --python $py --refresh psamvault-mcp
```

```bash
# macOS / Linux (bash, zsh) — let pipx report where its venvs live
uv pip install --python "$(pipx environment --value PIPX_LOCAL_VENVS)/psamvault-mcp/bin/python" --refresh psamvault-mcp
```

`pipx upgrade psamvault-mcp` also works (it upgrades inside the venv rather than recreating it) — its
displayed metadata is the only part that lags.

> Prefer `pipx environment`, above. If you construct the path by hand in PowerShell, `$LOCALAPPDATA`
> is an ordinary undefined variable and expands to the empty string, so the path silently loses its
> drive and uv answers *"No virtual environment or system Python installation found for path
> `/pipx/pipx/venvs/...`"*. A path with no drive letter **is** this mistake: use `$env:LOCALAPPDATA`
> there, `$LOCALAPPDATA` in bash. Current pipx on Windows stores venvs under
> `%LOCALAPPDATA%\pipx\pipx\venvs`; an older install keeps `%USERPROFILE%\pipx\venvs` only when that
> directory already exists.

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

This skill and the server are a **pair with a floor, not a pin**. The contract ships inside the wheel as
`mcp_server/compatibility.json` and maps each MCP release to the **minimum** skill version that documents
it. Any skill at or above that floor is healthy — the skill may legitimately move *ahead* of the MCP
(an improved description of an existing tool is a skill-only update), while a skill that has fallen
*below* the floor is drift.

- `get_version` reports the pairing (`compatibility.paired_skill_version`, newest release, whether a
  breaking release is pending) — check it before trusting this file.
- **`psamvault-mcp compat --check`** — diagnose the pair (`--json` for machines). Exit `0` in sync,
  `1` drift. A missing Hermes skill is not drift (the skill is optional; public procedures are in
  this directory). A skill **newer** than the floor reports `skill installed: <v> [ahead of the floor — fine]`
  and stays in sync — that is not a problem to fix. It also reports what PyPI has
  (`newest published`, `update available`), because the contract inside the installed wheel can only
  ever describe its own release.
- **`psamvault-mcp compat --apply`** — install the target release and bring the skill up to date (it
  installs the **newest** skill available, provided it meets the floor). A release marked **breaking**
  refuses without `--allow-breaking`.
- **`psamvault-mcp compat --apply --latest`** — target the newest release **published on PyPI**, not just
  the one recorded in the installed contract. That is the only way to leave an install that is already
  behind. It requires `--allow-breaking` when the target is newer than the installed contract, because
  such a release ships a contract this install has never seen.
- **`psamvault-mcp compat --sync-skill`** — **skill-only update**: install the clone's newest skill, MCP
  untouched. Use this when the skill improved but no MCP release happened. It reads the clone as-is
  (uncommitted skill work is installable), snapshots the current skill first, and refuses — writing
  nothing — if the clone's skill is below the floor.
- **`psamvault-mcp selfcheck`** — the version that is **installed** vs the version a **new session will
  actually serve** (it spawns a real server and asks `get_version`), plus the contract pairing and the
  newest published release. Exit `1` on any mismatch. Use it whenever a user asks "am I up to date?"
  and after any upgrade.
- **`psamvault-mcp doctor`** — why an installation looks wrong: entry points the package declares but
  pipx never linked on `PATH`, pipx records that disagree with the installed version, and processes
  holding the venv. `--fix` has **two repair paths**: a stale pipx *record* — `pipx list` showing an
  older version than the code that runs — is corrected **in place**, so sessions and MCP servers can
  stay up (0.5.4 and later); relinking an entry point pipx never linked *is* a venv recreation, so that
  path still refuses while the venv is held, names every holder (pid + image) and says what to stop. On
  0.5.3 and earlier `--fix` always wanted a quiet machine, and `doctor` could count its own launcher
  shim as a holder — so an idle venv read as busy and `--fix` refused on every machine (fixed in 0.5.4).
- `--apply` also has upgrade safety: it snapshots the skill, `--from-git --pull` stashes/pulls/restores
  the repo around the install, it smoke-tests the fresh install in a new interpreter from a neutral cwd,
  and it rolls back to the previous release if the install or the smoke test fails. When the venv is free
  it installs through **pipx** (so entry points stay linked and pipx's records stay honest); when MCP
  processes hold it, it falls back to `uv` and reports `relink_pending`.
- **Authority: the installed server wins.** The skill is brought to the version the installed server
  requires, never the reverse.
- A daily cron job (`psamvault MCP <-> skill lockstep`) runs the detector and wakes the agent on drift
  **or when a newer release is published** (`UPDATE AVAILABLE`); a breaking release is reported for
  approval, never installed silently.
- If `get_version` reports a tool this file does not describe (or vice versa), trust the check over
  your memory of this file — and **never** hand-edit `compatibility.json` or this frontmatter to
  silence it. Fix the pairing at release time, or with `--sync-skill` for a skill-only change.
- **Pre-0.5.3 installs:** the subcommands do not exist there, and `psamvault-mcp compat …` does **not**
  error on them — it ignores the argument and starts the stdio server (which looks like a hang). On those
  versions call the module directly:
  `<venv python> -c "from mcp_server.compat import main; raise SystemExit(main(['--check']))"`.

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
