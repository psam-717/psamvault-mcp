# Changelog

All notable changes to `psamvault-mcp`, newest first.

Unreleased work lives in [`CHANGELOG.unreleased.md`](CHANGELOG.unreleased.md) and is rolled into this
file **at release time**, together with the GitHub release notes (see the `psamvault-release` skill).
`scripts/docs-sync-check.py` fails the release if this file's newest section is not the release the
contract calls newest, so the two cannot silently drift apart.

Releases up to and including 0.4.6 predate this file — see the [GitHub releases](https://github.com/psam-717/psamvault-mcp/releases).

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
