# Changelog

All notable changes to `psamvault-mcp`, newest first.

Unreleased work lives in [`CHANGELOG.unreleased.md`](CHANGELOG.unreleased.md) and is rolled into this
file **at release time**, together with the GitHub release notes (see the `psamvault-release` skill).
`scripts/docs-sync-check.py` fails the release if this file's newest section is not the release the
contract calls newest, so the two cannot silently drift apart.

Releases up to and including 0.4.6 predate this file — see the [GitHub releases](https://github.com/psam-717/psamvault-mcp/releases).

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
