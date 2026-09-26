# Changelog

All notable changes to `psamvault-mcp`, newest first.

Unreleased work lives in [`CHANGELOG.unreleased.md`](CHANGELOG.unreleased.md) and is rolled into this
file **at release time**, together with the GitHub release notes (see the `psamvault-release` skill).
`scripts/docs-sync-check.py` fails the release if this file's newest section is not the release the
contract calls newest, so the two cannot silently drift apart.

Releases up to and including 0.4.6 predate this file — see the [GitHub releases](https://github.com/psam-717/psamvault-mcp/releases).

## 0.5.4 — 2026-09-26

### Fixed

- `fix(doctor): keep the version pipx records — correct it in place, so the repair no longer needs a quiet machine` — `pipx records : 0.4.4` beside `installed : 0.5.3` is one string in pipx's own JSON, and the venv already holds the right code. `--fix` now rewrites exactly that token (indentation, key order and CRLF endings preserved; dated `.bak` kept) when the stale record is the only drift, so `psamvault-mcp doctor --fix` succeeds with sessions running. When an entry point also needs **relinking** — which really does recreate the venv — `--fix` still refuses, names the holders and waits.
- `fix(install_env): stop counting this command's own launcher as a venv holder` — `psamvault-mcp doctor --fix` runs as entry-point shim → python → probe, and the shim is a process of its own whose command line carries the venv path. Counting it meant the holder total could never reach zero, so **`doctor --fix` refused to repair on every machine, including one whose venv nothing else held**. The running interpreter was already excluded; now self plus the contiguous launcher ancestors (the shim, a shell that inlined the command) are excluded, and the walk stops at the first ancestor that does not carry the venv.
- `fix(doctor): name the process holding the venv, and stop giving advice that repeats what was just done` — the refusal said `2 process(es) holding it … stop the gateway first`, which is unactionable when the gateway is already stopped (the usual case). It now lists every holder with pid and image name, and when those look like MCP servers it points at both the gateways **and** the Hermes desktop app, whose open sessions each hold one and which respawns a server the moment you kill it.
- `fix(install_env): one matching rule for "is this process holding the venv"` — the Windows probe sent a PowerShell `-like` clause and then re-filtered in Python. The clause had to be told both separator spellings by hand and a backslash-only one silently missed real holders (a busy venv read as free — the dangerous direction). The script now only enumerates and `_venv_matcher` decides, so there is a single rule and it is the one the tests exercise.
- `fix(cmd_runner): a timed-out run_with_credential killed the caller on POSIX` — the command was spawned without its own session, so it shared the caller's process group, and the timeout handler's `killpg` therefore signalled that same group: **the MCP server itself**. A command that hit the timeout took the server down instead of returning a timeout, and in CI it killed the test runner (four legs, ~11 minutes of silence, exit 137, no log published). The child now gets its own session on POSIX, and the kill refuses to signal our own group; Windows was already scoped by PID.
- `fix(compat): discover the checkout instead of hardcoding the author's path` — `DEFAULT_REPO` and `DEFAULT_CLONE` were `D:/Projects/py-projects/...`, so `--from-git` and the skill sync failed for every other user, and on CI, with a `FileNotFoundError` naming a path they had never heard of. Discovery is now `PSAMVAULT_MCP_REPO` → the checkout this process runs from → the checkout the current directory is inside; when nothing is found the error names the env var that fixes it, and `--apply` prints that message instead of a traceback.
- `fix(mcp): scan_and_protect silently ignored project_name` — the tool schema declared it, `tools.scan_and_protect` honoured it, and four documents (README, SKILL.md, AGENTS.md, the general-rules prompt) told agents to pass it, but the dispatch branch in `handle_call_tool` forwarded only `project_dir` and `patterns`. **scan_and_protect(project_name='acme') stored keys as env/.env/KEY instead of acme/.env/KEY**, so the documented per-project namespace never existed and a later `list_api_keys(project_name='acme')` found nothing. Found by the docs work that had to read the code to write the tool reference; the parameter is now forwarded.
- `fix(mcp): the browser_login description promised a final_url field the tool never returns` — the flow returns the ending URL as `url`. An agent following the description would look for a key that is not there; the description now names `url`.
- `docs(readme,skill): remove the HTTP/SSE transport that was never implemented` — the README documented `psamvault-mcp --http --port 8433`, a `--port`/`--host` options table, an SSE endpoint at `http://127.0.0.1:8433/sse`, and Hermes/Grok configs pointing at it; SKILL.md offered the same as 'Option A (Recommended for Hermes)'. **No such flags exist** — the entry point accepts `--version` / `--help` and the maintenance subcommands only, and nothing in the package serves HTTP or imports starlette/uvicorn. A host configured from those instructions could never connect. Both files now say stdio is the only transport, and why a credential vault needs an authentication design before a network transport can ship.
- `docs(readme): correct the version banner and the export host claim` — the header still said v0.4.4 while pyproject and `mcp_server/__init__.py` are 0.5.3, and `export_key_to_mcp_config` / `export_key_to_env_file` were described as writing configs for Hermes, Claude, etc. while the `agent` enum accepts `hermes` only and rejects anything else.
- `docs(playwright): browser_login requires only site_name` — the integration guide listed `login_url` and the three selectors as required, contradicting the tool's own description; they are optional and auto-discovered when omitted.

### Tests

- `test(doctor): pin the in-place record repair` — the record is corrected with the venv busy, the edit changes nothing else in pipx's file (CRLF endings included), a dated backup is kept, and an unlinked entry point still needs a free venv rather than being claimed as repaired. An unexpected pipx schema is reported, never guessed at.
- `test(install_env): pin the launcher-chain exclusion` — the shim, a contiguous shell wrapper, the stop at an unrelated ancestor, and the degraded no-parent-links path.
- `test(doctor): pin holder reporting` — the refusal names each holder, and the advice names the desktop app when MCP servers are holding the venv.
- `test(ci): run the suite on Python 3.11 and 3.13 for every push and pull request` — this repo had no CI at all, so the suite had only ever run on one machine and one OS, which is what let the two bugs above survive.
- `test(cmd_runner): pin that the timeout kill cannot signal the caller` — a timed-out command runs in its own process group, and the kill never signals ours.
- `test(compat): pin checkout discovery` — the env override wins, a checkout is found, the failure names `PSAMVAULT_MCP_REPO`, and the clone defaults to the repo sibling.
- `test(tools): use each platform's shell syntax for the env-echo tests` — `echo %VAR%` is cmd.exe, so on Linux those two assertions never validated redaction at all. The POSIX form is quoted as well, because an unquoted `$VAR` is glob-expanded and the fixture key is literally `***`.
- `test(dispatch): pin that every declared tool parameter reaches its handler` — three cases: `project_name` is forwarded, an omitted `project_name` stays `None` (the backwards-compatible `env/.env/KEY` layout), and no property declared in a tool schema is silently dropped. The third case fails on the unfixed dispatch, which is how the bug class gets caught next time rather than only this instance.

### Docs

- The upgrade playbook's "stop everything" scenario no longer hands out a hand-rolled `-like` kill filter (it disagreed with `doctor`: it matched nothing while `doctor` reported two holders). It drives the kill from `doctor --json` — the probe's own list — uses `hermes gateway stop --all` (there are usually two gateways), and states that the desktop app must be quit.
- `docs(repo): the user documentation now lives in docs/` — overview, installation and host wiring, a feature map, guides for credential injection and browser login, a complete tool reference (13 tools) and a configuration reference; a website can populate from the repo, and `docs/README.md` links every page so none is orphaned.
- `docs(reference): the tool reference is taken from the code` — tool names and parameters come from `TOOL_DEFINITIONS` rather than from prose, so a renamed or removed tool cannot leave the docs describing something the server no longer does.
- `docs(troubleshooting): the two playbooks now carry the same front matter` — every page under `docs/` satisfies one site contract (title/description/order) and is reachable from the docs root. Content unchanged.
- `docs(readme,skill,agents): doctor --fix is described by its TWO repair paths` — an in-place pipx-record correction that works with sessions running, and a relinking path that still needs the venv free. The README, the repo skill and AGENTS.md still said "run when no session is holding the venv", which stopped being the whole truth the moment the record repair landed.
- `docs(reference): the tool reference resyncs browser_login's verbatim description with the code` — the ending URL is `url`; both the quoted registry text and the note explaining the old `final_url` divergence were stale the moment the description was fixed.
- `docs(reference): the pre-0.5.4 scan_and_protect caveat is replaced` — it told readers to distrust `project_name` because the router dropped it; it now records that 0.5.4 forwards it and that an older install must be upgraded first.
- `docs(reference): PSAMVAULT_MCP_REPO and PSAMVAULT_SKILL_CLONE are documented as discovery, not defaults` — "a default repo path" described the hardcoded author path that made `--from-git` fail for everyone else; the rows now name the search order and the variable that overrides it.
- `docs(troubleshooting): the in-place record repair is labelled **0.5.4 and later**` — a reader on 0.5.3 is no longer told to run a repair their build refuses, and the launcher-selfcount bug is named as the reason an idle venv could read as held.

## 0.5.3 — 2026-09-14

### Breaking

- **BREAKING: the `psamvault-compat` console script is removed.** pipx links a package's console scripts only when pipx itself installs it, so a script added by a later release stays invisible on `PATH` — the file existed in the venv while the shell answered `command not found`. Everything it did now rides the entry point users already have: `psamvault-mcp compat …` (`--check`, `--apply`, `--apply --latest`, `--sync-skill`, `--from-git`, `--pull`, `--allow-breaking`). The `mcp_server.compat` module is unchanged, so pre-0.5.3 installs and the cron detector (which imports it directly) keep working.

### Added

- feat(cli): **`psamvault-mcp selfcheck`** — reports the version that is installed AND the version a **new** session will actually serve (it spawns a real server and calls `get_version`), plus the contract pairing and the newest published release. Exit 1 on any mismatch. This probe previously existed only in a private skill, so a user without our skills could not answer "what am I running?".
- feat(cli): **`psamvault-mcp doctor`** — diagnoses installation drift with concrete fixes: entry points the installed package declares but pipx never linked, pipx records that disagree with the installed version, and processes holding the venv. `--fix` reinstalls through pipx when the venv is free; otherwise it prints the exact commands to run at a safe moment.
- feat(compat): **`--apply --latest`** installs the newest release **published on PyPI**, not merely the one recorded in the installed contract — the only way to leave an install that is already behind.
- feat(compat): **`--check` asks PyPI** what the newest published release is (`latest_published` / `published_newer` in `--json`, two new lines in the human output).
- feat(compat): upgrades **choose their installer** — `pipx install --force` when no MCP process holds the venv (the venv is recreated, so entry points stay linked and pipx's records stay honest), `uv pip install` when it is held (never blocked by the Windows file lock), reported as `relink_pending` with the follow-up command.
- feat(install_env): cross-platform discovery of the pipx venv and of the processes holding it (Windows CIM query / POSIX `pgrep`), standard library only.

### Changed

- changed(compat): `--apply --latest` will only target a release **newer than the installed contract** with `--allow-breaking` — such a release ships a contract this install has never seen, so its compatibility is unverified by definition.
- changed(compat): a newer published release **never changes the exit code**. An update is news, not breakage. The daily lockstep detector now surfaces it as `UPDATE AVAILABLE`.
- changed(version_check): the startup notice and `compat --check` share one PyPI probe, so they cannot disagree.

### Fixed

- fix(versions): **one version comparator for the whole package.** The startup notice collapsed any suffixed version to `(0,)` while `compat` used a different rule; both now use `mcp_server.versions.vkey`, which orders `0.5.10` above `0.5.9`, sorts a pre-release below its release, and never raises on a garbage string from the network.
- fix(version_check): the update notice advised `pipx upgrade psamvault-mcp`, which fails on Windows while MCP servers hold the venv; it now names `psamvault-mcp compat --apply --latest` and points at `doctor`.
- fix(doctor): apps owned by another pipx package (the `psamvault` CLI lives in the same bin dir) are no longer reported as leftovers of this one.

### Tests

- test: version ordering (pre/post-release, numeric-vs-lexical, never-raises), the PyPI probe and `update_available`, `install_env` path resolution and holder parsing, `selfcheck` exit codes and timeouts, `doctor` against the exact drift shape seen on a real machine, and the compat apply path with the new installer kwargs.

### Docs

- docs: every reference to the removed `psamvault-compat` command swept to `psamvault-mcp compat …` across the README, AGENTS.md, the repo skill and the four release skills; the pre-0.5.3 module fallback is kept and labelled, because on older installs the new subcommand would start the server and hang rather than error.

## 0.5.2 — 2026-09-12

### Added

- feat(verify): bundled verify recipes for **Tavily** (`GET https://api.tavily.com/usage`) and **GitHub** (`GET https://api.github.com/user`) — both probed live with real keys before being recorded, so `verify_api_key` and both export tools now verify them with **no `verify_url` override**. The store's docstring records the rule (only a read-only endpoint that answers 200 for a *valid* key and rejects a bad one may be added, with the date it was proven) and names the providers that must stay absent because no read-only whoami exists (pypi/testpypi upload tokens).
- feat(compat): **`--sync-skill`** — a skill-only update path. Installs the clone's newest skill without touching the MCP, so an improved description of an existing tool no longer needs a release. Reads the clone's working tree as-is (like `--from-git`), snapshots the current skill first, and refuses without writing when the clone's skill is below the floor.
- feat(compat): **the recorded skill version is a floor, not a pin.** Any skill at or above it is healthy (`skill_ahead`), so the skill can move ahead of the MCP; only a skill *below* the floor is drift, and its remedy is `--sync-skill` (never an MCP install, which would downgrade a runtime carrying unreleased work). `--apply` installs `max(newest available, floor)` so an MCP install also brings the skill current.
- feat(apply): **upgrade safety** for `psamvault-compat --apply`, ported from the CLI's upgrade path — the installed skill is snapshotted before it is overwritten (newest 5 kept); `--pull` stashes uncommitted work (untracked included), pulls `--ff-only origin main` and restores it, stopping with the work restored if the pull fails and parking it in a labelled stash if the restore conflicts; `--from-git` reports branch/HEAD/dirtiness/position vs origin (and flags an editable install); a **smoke test** imports the freshly installed server in a fresh interpreter from a neutral cwd; and a failed install or smoke test **rolls back** to the previously installed release.
- feat(apply): `--pull` — bring the repo-sourced install up to date as part of the same command.

### Changed

- changed(compat): the cron detector's drift report now names **both** remedies (`--sync-skill` for a skill below its floor, `--apply` for an MCP update) and exports `skill_floor`/`skill_ahead`, falling back to `expected_skill` for older compat builds.

### Fixed

- fix(compat): **`--sync-skill` refuses to downgrade.** The clone is read as-is, so a clone parked on an older branch holds an older skill; installing it silently deleted newer documentation that exists nowhere else. It now refuses (naming the clone, its branch and both versions) unless `--allow-downgrade` is passed. `--check` additionally reports `skill_source` / `skill_source_stale` so a behind-the-installed clone is visible *before* anyone runs the command.
- fix(compat): `--apply` and `--sync-skill` share the same guard (both call `_sync_skill`), so a release install cannot quietly roll the skill back either.
- fix(compat): the index pre-check is now **advisory** — `--apply` always attempts the install (`--refresh` makes it authoritative) and consults PyPI's JSON API only to explain a genuine resolver failure. Previously a lagging JSON API (it follows an upload by up to ~1 minute) made `--apply` refuse with exit 3, reporting the target as "not published on PyPI yet" while the index already had it — found live applying 0.5.1 seconds after publishing it.
- fix(compat): an unreachable PyPI no longer short-circuits `--apply`; the install proceeds and a note records that the published-ness of the target could not be confirmed.

### Tests

- test(compat): the breaking-release tests build their own contract fixtures instead of asserting on the shipped contract's newest release — that assumption broke on the 0.5.1 bump and would break on every bump.
- test(compat): regression test pinning "a lagging index must not block the repair".

### Docs

- docs: exit 3 documented as "install failed: the target is not on the index" (it no longer means "the index has not caught up").
- docs: `PLAN_version_lockstep.md` describes the index check as advice-only.

## 0.5.1 — 2026-09-10

### Fixed

- fix(compat): `psamvault-compat --apply` now installs with `--refresh`, so it can install a release published moments earlier. PyPI serves the simple index with `cache-control: max-age=600` and uv honours it: `uv pip install psamvault-mcp==X` failed with *"there is no version of psamvault-mcp==X"* while the index already listed it. Reproduced on demand seconds after publishing this release.

### Known limitations

- The up-front index check reads PyPI's **JSON API**, which also lags an upload, so `--apply` could refuse with exit 3 for up to ~1 minute after a release. Workaround: re-run a minute later, or use `--from-git`. Fixed on `main`, unreleased (see `CHANGELOG.unreleased.md`).

## 0.5.0 — 2026-09-10

### Breaking

- BREAKING: removed the `capture_stripe_credentials` tool (`mcp_server/stripe_capture.py`, 447 lines). A tool count cannot see this — the count stayed at 13 — so `get_version`'s compatibility block and `tests/test_compat.py`'s tool fingerprint are what catch it.

### Added

- feat: `export_key_to_env_file(key_name, env_var_name, agent="hermes", ...)` — exports a vault key into an agent's `.env`, updating in place (timestamped backup) or appending, idempotently.
- feat: **version lockstep** — `mcp_server/compatibility.json` ships inside the wheel (per-release skill version + tool fingerprint); `psamvault-compat --check | --apply [--allow-breaking] [--from-git]`; `get_version` returns a `compatibility` block; daily cron detector (`aff012dc7190`) that stays silent when in sync.

### Fixed

- fix(exports): `export_key_to_mcp_config` no longer crashes on a missing config path (`FileNotFoundError`) or an empty config file (`AttributeError: 'NoneType' object has no attribute 'get'`); parent directories are created.
- fix(credentials): `run_with_credential` works under the MCP event loop on Windows — the blocking child process runs on a worker thread instead of asyncio's Windows subprocess transport.

### Security

- fix(exports): an invalid key is never written. The probe is always attempted when the provider can be probed, and a **failed probe blocks the write even with `skip_verify=true`**; `skip_verify` now only covers providers that cannot be probed.

### Docs

- docs: `scripts/docs-sync-check.py` — release-time doc-drift gate (stale tool names, stale counts, missing tools, contract disagreement) made step zero of every release.
