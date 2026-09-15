# 0.5.3 — release readiness: three pillars

**Status:** 🟡 EXPLORING — decisions D8–D12 proposed for psam's pick
**Author:** Atlas · **Date:** 2026-09-14
**Companion:** `PLAN_pypi_latest_detection.md` (D1–D7: seeing and applying the newest published release)
**Goal:** after 0.5.3 ships, the three problems below **no longer exist** for psam *or* for any other user.

---

## The three pillars (psam's own framing)

| # | Pillar | Meaning |
|---|---|---|
| 1 | **Agents use psamvault-mcp without breaking** | an upgrade must never leave a live session's MCP broken or half-updated |
| 2 | **A user updates smoothly, guided by their agent, zero issues** | the documented flow must succeed on any OS, any install state, without dead ends |
| 3 | **Compatibility / "is there an update?" verification just works** | the verification command itself must be reachable and reliable |

---

## The PATH defect — root cause, and why it is *not* just psam's machine

**Symptom:** `psamvault-compat: command not found`, while the file exists at
`…\pipx\pipx\venvs\psamvault-mcp\Scripts\psamvault-compat.exe`.

**Root cause (verified in source):**

1. The package declares two entry points — `psamvault-mcp` → `mcp_server.main:main` and
   `psamvault-compat` → `mcp_server.compat:main` (`pyproject.toml [project.scripts]`).
2. **pipx links apps only when pipx itself installs/reinstalls the package.** psam's venv was created in the
   0.4.4 era, when only `psamvault-mcp` existed.
3. Every later upgrade was performed by `uv pip install --python <pipx venv python>` — which includes
   `psamvault-compat`'s **own** `_install()` (`mcp_server/compat.py`). uv writes the `.exe` into the venv but
   never touches pipx's records or its bin directory.
4. Therefore `pipx list` still reports `0.4.4` with one app, `pipx expose` answers *"already exposed"* (it only
   relinks *recorded* apps), and the new entry point is invisible on PATH.

**Consequence — this is the important part:** the defect is **self-perpetuating and universal**. The command
users are told to run to upgrade adds a fresh unlinked entry point each time a release introduces one, and
sets up the *next* release to be unreachable too. psam hit it; every user who has ever upgraded via
`--apply` has the same latent state. Documenting a workaround (done in the skills: venv path + module
fallback) does not remove it.

**Three-layer fix**

| Layer | Fix | Effect |
|---|---|---|
| **L1 — reachability** | expose compat (and the new diagnostics) as **subcommands of the already-linked `psamvault-mcp` entry point**: `psamvault-mcp compat --check`, `psamvault-mcp selfcheck`, `psamvault-mcp doctor` | PATH-proof for every existing user immediately — their `psamvault-mcp` shim already exists. `main()` already parses `sys.argv` for `-h/-V`, so dispatch is natural and the no-arg path stays "serve stdio" |
| **L2 — repair** | `doctor` compares *declared* entry points against *linked* apps (pipx records + `PIPX_BIN_DIR`) and reports/repairs drift | the stale `pipx list 0.4.4` and missing shims become *detectable and fixable*, not invisible |
| **L3 — root cause** | choose the install method: **pipx reinstall when no MCP process holds the venv** (correct state: apps linked, metadata fresh) and **uv install only when processes are running**, recording "relink pending" | upgrades stop creating the defect in the first place |

---

## Gap analysis against the three pillars

| Pillar | Today | Gap 0.5.3 must close |
|---|---|---|
| 1 · Agents don't break | Upgrade safety (snapshot/smoke/rollback), fresh processes per session — genuinely good | No in-package way to prove the running MCP is healthy after an upgrade; `--latest` must inherit the same guards; a half-relinked venv must never be left behind |
| 2 · Smooth update, zero issues | Documented flows exist, but they assume a shim on PATH and a known venv path | **Fails today.** Needs L1+L2+L3, one canonical command per OS, and a decision tree the agent can follow without improvisation |
| 3 · Verify compatibility/latest | `--check` works (if reachable); PyPI-latest detection is planned (D1–D7) | The verification entry point itself is unreliable; the "what actually runs" probe lives in a skill file rather than in the package, so a user without our skills cannot run it |

---

## New decisions

### D8 — Where should compat and diagnostics live?

| # | Option | Pros | Cons |
|---|---|---|---|
| 1 | **Subcommands on `psamvault-mcp` (`psamvault-mcp compat …`), keep `psamvault-compat` as an alias** | PATH-safe everywhere today; one documented path; no new shims needed | two ways to invoke the same thing |
| 2 | Only `psamvault-compat`, plus a PATH repair | one canonical name | unreachable *until* repaired — the failing state stays the default |
| 3 | Only subcommands; drop `psamvault-compat` | cleanest surface | breaks anyone (and any doc/cron) already calling the standalone name |

**Recommendation: 1.**

### D9 — Which installer performs an upgrade?

| # | Option | Pros | Cons |
|---|---|---|---|
| 1 | **Detect running MCP processes: `pipx install --force` when the venv is free, `uv pip install` when it is not (plus a "relink pending" note)** | fixes the root cause whenever it safely can; never blocked by the lock | process detection must be correct on all platforms |
| 2 | Always `uv pip install`, then always try to relink | simple | keeps pipx's records permanently stale; relinking without a reinstall is not supported by pipx |
| 3 | Always `pipx install --force` | correct state | fails with the Windows lock whenever a session is open — i.e. most of the time |

**Recommendation: 1.**

### D10 — Process detection

| # | Option | Pros | Cons |
|---|---|---|---|
| 1 | **stdlib-only, per-OS: Windows `Get-CimInstance Win32_Process`, POSIX `pgrep -f <venv>/bin/python`** | no new dependency; we already validated the exact commands | PowerShell/CIM spawn per check (~0.3s) |
| 2 | Add `psutil` as a dependency | one clean API | new runtime dependency for the MCP — heavier than the problem |
| 3 | Skip detection; add `--force-uv` | least code | the "safe moment" never gets used |

**Recommendation: 1.**

### D11 — Where does the "is what runs actually latest?" probe live?

| # | Option | Pros | Cons |
|---|---|---|---|
| 1 | **Ship it in the package: `psamvault-mcp selfcheck` (installed vs served, exit 0/1)** | any user, any agent, no skills required; testable in the release gate | more product surface |
| 2 | Keep it as the skill script (`check_live_mcp.py`) only | already written and verified | unavailable to users without our skills — fails pillar 3 for them |
| 3 | Both (package command + skill wrapper) | best of both | slight duplication |

**Recommendation: 1** (the skill script becomes a thin pointer to the command).

### D12 — Scope of 0.5.3

| # | Option | Pros | Cons |
|---|---|---|---|
| 1 | **All three pillars + D1–D7 in one release** | after it ships, the stated problems are gone | bigger release; needs the full sandbox gate |
| 2 | 0.5.3 = PyPI-latest + reachability (L1); 0.5.4 = installer choice + selfcheck | smaller steps | the PATH/update problems persist through an extra release cycle |
| 3 | 0.5.3 = only the PATH fix; rest later | fastest | pillar 3 (verification) still incomplete |

**Recommendation: 1**, shipped through the sandbox gate (L1/L2/L3) with the new tools added to the probe spec.

---

## Build order (if D12 → 1)

| Step | Work | Pillar | Depends on |
|---|---|---|---|
| 1 | `main()` subcommand dispatch: `compat`, `selfcheck`, `doctor` (no-arg still serves stdio) | 1, 3 | — |
| 2 | `doctor`: entry points declared vs linked; stale pipx metadata; venv lock holders; actionable output | 2 | 1 |
| 3 | Process detection helper (Windows CIM / POSIX pgrep) with a `--assume-free` override | 2 | 2 |
| 4 | Installer choice in `_install()` (pipx when free, uv when busy, record relink pending) | 2 | 3 |
| 5 | `selfcheck` — installed vs served + optional PyPI-latest comparison | 1, 3 | 1 |
| 6 | PyPI-latest detection + `--apply --latest` (D1–D7 from the companion plan) | 3 | 1 |
| 7 | Docs: README, usage skill, release skill; contract entry 0.5.3; floor bump | all | 1–6 |
| 8 | Sandbox verification: L1 + L2 + L3 with the new subcommands in the probe spec; live E2E per pillar | all | 1–7 |

## Acceptance criteria

- [ ] `psamvault-mcp compat --check` works on a machine where `psamvault-compat` is **not** on PATH (psam's machine is exactly that state — use it as the test case)
- [ ] `doctor` reports psam's current state as drift and its fix path, then reports clean after the fix
- [ ] An upgrade performed with no MCP processes running leaves `pipx list` metadata **current** and all entry points linked
- [ ] An upgrade performed with processes running still succeeds (no lock failure) and clearly states what to run afterwards
- [ ] `selfcheck` exits 1 for a stale install and 0 for a fresh one (both verified live)
- [ ] `--check` reports `update_available` from PyPI; offline degrades to a note (D1–D7)
- [ ] An agent following only the skill can complete an upgrade on Windows **and** macOS/Linux without a dead end
- [ ] Full sandbox gate green; no regression in the 13 tools; live MCP unaffected throughout

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Subcommand dispatch breaks the stdio server (Hermes launches `psamvault-mcp` with no args) | dispatch only on a known subcommand; no-arg path unchanged; covered by the L3 probe (13 tools over real stdio) |
| `pipx install --force` chosen while a process *is* running (detection false negative) | the attempt fails loudly with the exit-32 message; fall back to uv + relink-pending in the same run |
| Process detection false positive ⇒ needless uv install | relink pending is reported and `doctor` fixes it at the next safe moment |
| Bigger release than planned | the sandbox gate is unchanged, and step 8 verifies each pillar live before publish |
| Users on very old versions | L1 makes the *new* path reachable only after they upgrade once; the skills keep the venv-path fallback for that first hop |

## Open questions

- [ ] Should `doctor --fix` be automatic, or always require confirmation? *Default: report by default, `--fix` to act.*
- [ ] Should `selfcheck` also compare against PyPI-latest (merging pillars 1 and 3 in one command)? *Default: yes when reachable.*
- [ ] Keep `psamvault-compat.exe` forever, or deprecate it in a later release once subcommands are established? *Default: keep, document subcommands as canonical.*
