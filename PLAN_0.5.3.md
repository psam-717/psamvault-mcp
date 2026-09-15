# 0.5.3 — Final Build Plan (consolidated)

**Status:** 🟢 DECIDED — ready to build on approval
**Author:** Atlas · **Date:** 2026-09-14 · **Owner:** psam
**Supersedes:** `PLAN_pypi_latest_detection.md` (D1–D7), `PLAN_0.5.3_release_readiness.md` (D8–D12) — kept as the decision record.
**Release:** psamvault-mcp **0.5.3**

---

## 1. Why this release exists

Three problems, stated by psam, must no longer exist after 0.5.3:

| # | Problem | Definition of done |
|---|---|---|
| **P1** | Agents using psamvault-mcp must never be broken by an upgrade | an upgrade cannot leave a live install half-updated or unverifiable; every install ends with a proof |
| **P2** | A user must be able to update smoothly, guided by their agent, with **zero issues** | the documented flow works on any OS and any install state, with no dead end and no command that isn't reachable |
| **P3** | Compatibility and "is there an update?" must be verifiable without issues | the verification command is always reachable, and it can see both the local pair **and** what is published |

**Root defect driving P2/P3:** the package declares two entry points (`psamvault-mcp`, `psamvault-compat`), but pipx links apps **only when pipx itself installs the package**. Every upgrade performed by `uv pip install --python <pipx venv python>` — including compat's own `_install()` — adds entry points inside the venv that are never linked on PATH, and leaves pipx's records stale (`pipx list` still said 0.4.4). The defect is self-perpetuating: the upgrade command is what creates it, for every user, on every release that adds a command.

**Correction to an earlier claim in this plan's drafts.** It is *not* true that nothing can see a newer release. `mcp_server/version_check.py` already queries PyPI on every server start (`check_for_update()`, called from `main()`). The problem is that the signal is unusable in practice:

| Why the existing check doesn't help | Detail |
|---|---|
| It writes to **stderr through the logger** | in MCP stdio mode that lands in the client's logs, never in front of the user |
| It fires **once per version, ever** | `~/.psamvault/last_seen_version` suppresses every repeat |
| Its advice can fail | it says `pipx upgrade psamvault-mcp` — the command that hits the Windows lock and whose metadata is stale |
| Its comparator is weaker than the contract's | `version_tuple()` splits on `.` and `int()`s it, so any suffix (`0.5.3rc1`) collapses to `(0,)` |

So D1 is **not "add detection"** — it is "surface the detection that already exists, where people actually look, with the contract's comparator and correct advice", and collapse the two PyPI probes into one helper.

---

## 2. Decisions locked (psam, 2026-09-14)

| # | Decision | Choice |
|---|---|---|
| D1 | Where "newest published" comes from | **`--check` gains an advisory PyPI probe** |
| D1b | Exit code when an update exists but the pair is in sync | **exit 0 — report only** |
| D2 | How an upgrade to the newest is triggered | **`--apply --latest` (explicit); plain `--apply` unchanged** |
| D3 | Safety for a release newer than the installed contract | **breaking-eligible → requires `--allow-breaking`** |
| D4 | When PyPI is unreachable | **advisory note; fall back to contract behaviour** |
| D5 | How it is proven | **stubbed unit tests + one live E2E against real PyPI** |
| D6 | Bookkeeping | **README + usage skill + contract entry + floor bump in the same release** |
| D7 | `--apply --latest` and the skill | **also syncs the skill when the newer release raises the floor** |
| D8 | Where compat live | **REMOVED as a console script — `psamvault-mcp compat …` is the only command** (`psamvault-compat` deleted, every reference swept) |
| D9 | Which installer performs an upgrade | **pipx when the venv is free; uv when processes hold it (+ relink-pending note)** |
| D10 | Process detection | **stdlib per-OS (Windows CIM, POSIX `pgrep -f <venv>/bin/python`)** |
| D11 | Where the "what actually runs" probe lives | **in the package: `psamvault-mcp selfcheck`** |
| D12 | Scope | **all three pillars + D1–D7 in 0.5.3** |

---

## 3. Command surface

| Command | Status | Notes |
|---|---|---|
| `psamvault-mcp` (no args) | **unchanged** | serves MCP over stdio; Hermes keeps launching it exactly as today |
| `psamvault-mcp -h / -V` | unchanged | existing behaviour preserved |
| `psamvault-mcp compat …` | **NEW** | canonical path; identical flags to today's `psamvault-compat` |
| `psamvault-mcp selfcheck` | **NEW** | installed vs served (+ PyPI-latest when reachable); exit 0/1 |
| `psamvault-mcp doctor [--fix]` | **NEW** | entry-point/PATH drift, stale pipx metadata, lock holders, floor state, latest-published |
| `psamvault-compat …` | **REMOVED in 0.5.3** | entry point deleted from `[project.scripts]`; all references swept. The internal module `mcp_server.compat` is **kept** (the cron detector calls it directly and it is not a user-facing name) |

**Dispatch rule:** `main()` inspects `sys.argv[1]` **before** the existing `-h/-V` scan and before any server startup. `compat`, `selfcheck`, `doctor` → delegate to their implementation and exit with its code. Anything else (including no arguments) → current stdio server path, untouched.

**Why this fixes P2/P3 immediately:** every affected user already has `psamvault-mcp` linked on PATH. `psamvault-mcp compat --check` therefore works on a machine where `psamvault-compat` does not exist on PATH — which is psam's machine today, and the acceptance test for this release.

---

## 4. Behaviour changes

### 4.1 `compat --check`

Adds two rendered lines and two `--json` keys (`latest_published`, `update_available`); exit code unchanged; `--no-index` skips the probe.

```
psamvault-mcp compatibility
  server installed : 0.5.2  [sync]
  server target    : 0.5.2
  newest published : 0.5.3                                    ← NEW (PyPI)
  update available : yes — run: psamvault-mcp compat --apply --latest   ← NEW
  effective release: 0.5.2
  skill installed  : 1.8.0
  skill floor      : 1.8.0  (minimum this server requires)
  tools            : match contract
in sync — nothing to do
```

Offline / 5xx / malformed JSON → `newest published : unknown (PyPI unreachable — advisory)` and every other behaviour is unchanged.

### 4.2 `compat --apply --latest`

1. target = max(contract newest, PyPI newest) by the contract's existing version comparison (never hand-rolled).
2. If target is newer than the installed contract's newest → treated as breaking-eligible → requires `--allow-breaking` (D3).
3. Installer choice (D9/D10): probe for processes holding the venv python —
   - **free** → `pipx install --force psamvault-mcp==<target>` → apps relinked, pipx metadata refreshed;
   - **held** → `uv pip install --python <venv python> --refresh psamvault-mcp==<target>`, and the report records `relink_pending` with the exact command to run at the next safe moment.
   - `--assume-free` / `--force-uv` override the probe.
4. After install: re-read the *newly installed* contract, sync the skill to `max(newest skill, floor)` (D7), run the existing smoke test, roll back on failure (existing upgrade-safety path).

### 4.3 `selfcheck` (new, D11)

Reports the two numbers that can disagree — **installed** (read from a neutral cwd via `importlib.metadata`) and **served** (a freshly spawned MCP process asked for `get_version` + `tools/list`) — plus the contract pairing and, when reachable, the newest published version. Exit 1 on any mismatch. Same logic as the verified `check_live_mcp.py`, now shipped in the wheel so any user or agent can run it without our skills.

### 4.4 `doctor` (new)

Findings, each with a concrete fix:

| Check | Reported when |
|---|---|
| Entry points declared by the package vs apps linked in `PIPX_BIN_DIR` | `psamvault-compat` exists in the venv but is not linked (exactly psam's state) |
| pipx metadata version vs installed version | stale records (`pipx list` 0.4.4 vs installed 0.5.3) |
| Processes holding the venv python | an upgrade would hit the Windows lock |
| Fresh-interpreter import of the installed package | the install is broken/half-written |
| Skill floor vs installed skill | drift |
| Installed vs PyPI-latest | an update exists |

`--fix` performs the safe repairs (pipx reinstall when the venv is free; otherwise it prints the exact Scenario-2 sequence). Default is report-only.

### 4.5 Cron detector

`HERMES_HOME/scripts/psamvault-compat-check.py` gains one condition: wake the agent when `update_available` is true, not only on drift (~5 lines). Until now the daily job was structurally incapable of reporting a new release.

---

## 5. Build order

| Step | Work | Pillar | Verify with |
|---|---|---|---|
| 1 | `main()` subcommand dispatch (no-arg path untouched) | P1·P3 | L3 probe: 13 tools over real stdio, unchanged |
| 2 | **Unify + surface the PyPI probe**: one shared helper (refactor `version_check.py`), the contract's `_vkey` comparator everywhere, correct advice text; `--check` reports it; `--no-index` skips | P3 | stubbed tests (newer / equal / offline / `rc` suffix) + live E2E |
| 3 | `--apply --latest` targeting + breaking gate + skill sync (D2, D3, D7) | P3 | stubbed tests + live E2E on the sandbox |
| 4 | Process probe (D10) + installer choice (D9) + `relink_pending` | P2 | live: free-venv run leaves pipx metadata current |
| 5 | `selfcheck` (D11) | P1·P3 | live: exit 1 stale, exit 0 fresh |
| 6 | `doctor` + `--fix` (L2) | P2 | psam's machine: reports the PATH drift, then clean |
| 7 | Docs, contract 0.5.3 entry, skill floor bump, usage skill, README, agent guide | D6 | `scripts/docs-sync-check.py` |
| 8 | Sandbox verification (L1/L2/L3) with the new subcommands in the probe spec | all | full gate before any publish |

## 6. Acceptance criteria

**P1 — nothing breaks**
- [ ] No-arg `psamvault-mcp` still serves exactly 13 tools over real stdio (L3)
- [ ] A failed install or failed smoke test still rolls back to the previous release
- [ ] Live sessions keep working throughout; only new sessions see the new version

**P2 — zero-issue upgrade**
- [ ] `psamvault-mcp compat --check` works on psam's machine **as it is today** (`psamvault-compat` absent from PATH)
- [ ] An upgrade with the venv free leaves `pipx list` metadata current and all entry points linked
- [ ] An upgrade with processes running succeeds (no lock error) and states exactly what to run afterwards
- [ ] `doctor` flags psam's PATH drift and reports clean after `--fix`
- [ ] Every documented command runs on Windows **and** macOS/Linux, or is explicitly marked platform-specific

**P3 — verification always works**
- [ ] One PyPI probe implementation: `compat --check` and the startup notice cannot disagree
- [ ] `version_check.py` uses the contract's comparator (a `0.5.3rc1`-style fixture orders correctly)
- [ ] The startup notice's advice matches the documented upgrade command (no more `pipx upgrade` under a locked venv)
- [ ] `--check` reports `update_available` against real PyPI; offline degrades to a note, exit code unchanged
- [ ] `--apply --latest` installs the newest published release; refuses without `--allow-breaking` when unknown-newer
- [ ] `selfcheck` distinguishes installed from served and exits 1 on mismatch
- [ ] The cron detector can finally say "a newer release exists"

**Release hygiene**
- [ ] Docs gate green; contract entry added (**`breaking: true`** — the console-script removal); skill floor bumped; changelog rolled
- [ ] `psamvault-compat` is gone: entry point deleted, a test asserts `entry_points()` no longer exposes it, and the reference sweep is clean (only historical changelog lines and the cron script filename remain)
- [ ] TestPyPI → sandbox L1/L2/L3 → user gate → PyPI → GitHub release → PR

## 7. Test matrix

| Case | Type | Expectation |
|---|---|---|
| PyPI newer than installed | stub | `update_available: true`, exit 0 |
| PyPI equal | stub | `update_available: false`, exit 0 |
| PyPI unreachable / 5xx / bad JSON | stub | advisory note, all else unchanged |
| `--apply --latest` with unknown-newer, no flag | stub | refused, names `--allow-breaking` |
| `--apply --latest` with flag | sandbox live | installs, relinks or records pending |
| venv free vs held | live | pipx path vs uv path chosen correctly |
| `selfcheck` fresh vs stale | live | exit 0 vs 1 |
| `doctor` on psam's machine | live | drift reported, then clean after `--fix` |
| No-arg server + 13 tools | L3 probe | unchanged fingerprint |

## 8. Out of scope (explicit)

- No changes to the 13 MCP tools, their schemas, or the vault/credential flows
- No change to the contract/floor semantics themselves
- `psamvault-compat.exe` is **removed** (console script). The internal module `mcp_server.compat` stays — it is not user-facing, and the cron detector imports it
- **This removal is a breaking surface change** → the 0.5.3 contract entry carries `breaking: true`, and the changelog names it. No external users are known (evidence in §11), so the practical impact is limited to psam's own machine
- No gbrain/Hermes-side changes beyond the cron detector's one condition

## 9. Risks

| Risk | Mitigation |
|---|---|
| Subcommand dispatch breaks the stdio server | dispatch only on a known first argument; L3 probe covers the no-arg path |
| Process probe false negative → pipx attempts a locked venv | the attempt fails loudly with the OS error; the same run falls back to uv + relink-pending |
| Process probe false positive → unnecessary uv install | `relink_pending` is reported and `doctor --fix` repairs at the next safe moment |
| PyPI probe slows every `--check` | 5s timeout, skipped entirely with `--no-index` |
| Two PyPI probes (startup notice + compat) drifting apart | **one helper serves both**; a test asserts `compat` and `version_check` resolve the same version for the same fixture |
| The naive `version_tuple()` mis-orders a suffix release (`0.5.3rc1`) | replace with the contract's `_vkey`; explicit test case with a pre-release suffix |
| Users on very old versions cannot reach the new subcommands until they upgrade once | the skills keep the venv-path/module fallback documented for that first hop |

## 10. Reference sweep checklist (no `psamvault-compat` command may survive)

Counts measured on 2026-09-14:

| Target | Hits | Action |
|---|---|---|
| `pyproject.toml` `[project.scripts]` | 1 | delete the entry point |
| `README.md` | 8 | rewrite every example to `psamvault-mcp compat …` |
| `AGENTS.md` | 2 | same |
| `SKILL.md` (repo) | 3 | same |
| `CHANGELOG.md` / `CHANGELOG.unreleased.md` | 3 | keep historical entries; add the removal to the 0.5.3 section |
| `mcp_server/compat.py` | 3 | docstrings/help text only (module name unchanged) |
| `mcp_server/main.py` | 1 | help text lists subcommands |
| `mcp_server/upgrade_safety.py` | 1 | messages reference the new command |
| `tests/test_compat.py`, `tests/test_compat_detector.py` | 1 + 3 | update invocations; add a test asserting the entry point is gone |
| `psam-custom/psamvault-mcp-release` (skill) | many | canonical command in the playbook |
| `psam-custom/psamvault-release`, `release-sandbox-verification`, `psamvault-mcp` (usage), `pypi-release-verification` (skills) | several | sweep to the subcommand |
| `HERMES_HOME/scripts/psamvault-compat-check.py` | prose | update the docstring; **filename unchanged** (avoid cron-job churn) — renaming is a separate, optional cleanup |
| `PLAN_*.md` (repo) | several | update the plans themselves so docs don't contradict the code |

**Verification of the sweep:** after edits, `grep -rn "psamvault-compat" <repo> <skills>` returns only historical changelog lines and the cron script's filename/docstring. A test asserts `importlib.metadata.entry_points()` no longer exposes the old name.

## 11. Evidence behind "no external users" (2026-09-14)

| Signal | Value | Interpretation |
|---|---|---|
| PyPI non-mirror downloads | 1416 all-time | **not user evidence** — daily shape spikes on our publish days (09-03: 96, 09-10: 144) and sits at 1–6/day otherwise: mirrors, scanners, and our own installs |
| GitHub release assets | v0.5.2 0/0 · v0.5.1 1/1 · v0.4.6 2/8 | single digits — psam and the agent |
| Stars / forks / watchers | 2 / 0 / 0 | no community |
| Third-party issues | none | — |

**Residual risk, stated plainly:** if a pipx-installed user does exist, removing the entry point leaves their shim dangling; the cure is one `pipx install --force psamvault-mcp==0.5.3`, which the release notes will name explicitly. PyPI exposes no per-file install attribution, so "zero users" cannot be proven — only strongly indicated.

## 12. Open questions (defaults set)

- [ ] `doctor --fix` automatic? *Default: report-only unless `--fix`.*
- [ ] `selfcheck` also compares PyPI-latest? *Default: yes when reachable.*
- [ ] Deprecate `psamvault-compat.exe`? *Default: keep; subcommands are canonical.*
