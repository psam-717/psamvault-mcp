# psamvault-compat: see and apply the newest PUBLISHED release

**Status:** 🟡 EXPLORING — decisions proposed, awaiting psam's picks
**Proposed by:** psam
**Authored by:** Atlas
**Date:** 2026-09-14
**Target release:** psamvault-mcp 0.5.3

---

## Summary

`psamvault-compat` currently follows the contract that **ships inside the installed wheel**. That makes
`--check` unable to see (and `--apply` unable to reach) any release newer than the contract the running
install carries. Proven on 2026-09-14: the 0.5.1 wheel's contract records `['0.5.1','0.5.0','0.4.6']`, so a
user on 0.5.1 is told "in sync — nothing to do" while 0.5.2 sits on PyPI. The cron lockstep detector is
silent for the same reason.

This plan makes **detection** come from PyPI and keeps **action** explicit and gated.

---

## Evidence

| Fact | Where it comes from |
|---|---|
| `target_mcp = latest_release(contract)` | `mcp_server/compat.py` — `check()` |
| The contract ships inside the wheel | `load_contract()` reads `mcp_server/compatibility.json` from the installed package |
| The 0.5.1 wheel's newest contract entry is 0.5.1 | downloaded the 0.5.1 sdist from PyPI and read its `compatibility.json` |
| PyPI is already contacted, but only to *explain* a failure | `target_is_published()` — `httpx.get("https://pypi.org/pypi/psamvault-mcp/json")`, advisory since 0.5.2 |
| An index install *does* reach the newest release | `uv pip install --upgrade-package psamvault-mcp psamvault-mcp` → 0.5.1 → 0.5.2 verified live |

---

## Decisions needed

### D1 — Where does "newest published" come from?

**Context:** `--check` is the only command every user and the daily cron run, so whatever it does sets the cost and the failure modes.

| # | Option | Pros | Cons |
|---|---|---|---|
| 1 | **`--check` gains an advisory PyPI probe** (report `update_available` / `latest_published`) | Everyone sees it, including the cron detector; index unreachable ⇒ note, not failure | One network call per check; needs a timeout and a `--json` key |
| 2 | New explicit `--check-latest` flag | Zero behaviour change for existing callers | Nobody runs it unless they know it exists; the cron job would need editing |
| 3 | Leave `compat` offline; the **cron detector script** does the PyPI probe | Keeps the CLI pure | Interactive users still can't see it; duplicates logic outside the package |

**Recommendation: 1** — detection is cheap and read-only; failure is advisory. `--json` gains keys additively so the existing detector keeps working.

### D2 — How does `--apply` reach a newer release?

| # | Option | Pros | Cons |
|---|---|---|---|
| 1 | **`--apply --latest`** targets PyPI's newest; plain `--apply` unchanged | Explicit, scriptable, keeps the documented contract behaviour intact | Two modes to explain |
| 2 | `--apply` silently targets PyPI's newest when it is newer than the contract | One command "just works" | Changes the meaning of an existing command; a surprise upgrade on a cron path |
| 3 | `--apply --version <X>` (explicit pin) | Fully deterministic | Still requires knowing the version exists |

**Recommendation: 1** (`--latest`), optionally adding 3 later for scripted pinning.

### D3 — Safety gate for a release the installed contract has never seen

| # | Option | Pros | Cons |
|---|---|---|---|
| 1 | **Treat an unknown-newer release as breaking-eligible → require `--allow-breaking`** | Consistent with today's gate; a stranger release can't land silently | One extra flag on a routine upgrade |
| 2 | Assume it is safe unless PyPI metadata says otherwise | Fewer prompts | The metadata that says "breaking" lives in the *new* contract, which is exactly what we cannot read yet |
| 3 | Always require an explicit `--yes` for `--latest` | Strongest | Another flag on top of `--allow-breaking` |

**Recommendation: 1** — the gate already exists; unknown ⇒ treat as breaking-eligible.

### D4 — When PyPI cannot be reached

| # | Option | Pros | Cons |
|---|---|---|---|
| 1 | **Advisory note; fall back to the contract target** | Never blocks a repair that would otherwise work | Slightly less informative offline |
| 2 | Hard error | Loud | Breaks `--check` on a plane/offline box |
| 3 | Stay silent | Clean output | Hides the fact that the answer is incomplete |

**Recommendation: 1** — same posture as the 0.5.2 index pre-check fix.

### D5 — Verification and shipping

| # | Option | Pros | Cons |
|---|---|---|---|
| 1 | **Unit tests with a stubbed PyPI response + one live E2E against real PyPI** | Covers offline, newer-available, and up-to-date paths; live E2E proves the real JSON shape | Slow-ish E2E |
| 2 | Unit tests only | Fast | Misses real-shape drift (this project has been bitten before) |
| 3 | Live only | Proves reality | Not reproducible in CI |

**Recommendation: 1** — this repo's own history says fakes alone miss the real shape.

### D6 — Skill/contract bookkeeping

| # | Option | Pros | Cons |
|---|---|---|---|
| 1 | **Document the new flag in the usage skill, bump its floor, add a 0.5.3 contract entry** | Keeps the lockstep honest | Routine work |
| 2 | Ship the code, update the skill later | Faster | Drift; `docs-sync-check` will fail the release anyway |

**Recommendation: 1** — the release gate enforces it regardless.

---

## Build order

| Step | Work | Depends on |
|---|---|---|
| 1 | `latest_published(timeout)` — read PyPI JSON, return version or `None`, never raise | — |
| 2 | `check()` takes an optional probe; adds `latest_published` + `update_available`; `--no-index` to skip | 1 |
| 3 | `render()` shows "newest published: X (you have Y)"; stays silent when equal | 2 |
| 4 | `--apply --latest` targets the probed version; unknown-newer ⇒ `--allow-breaking` required | 2, 3 |
| 5 | Detector script: unchanged (additive keys), but surface `update_available` | 2 |
| 6 | Tests (stub + live E2E), docs, contract entry, usage skill, release | 1–5 |

## Files likely to change

- `mcp_server/compat.py` — probe, `--latest`, rendering, gate
- `tests/test_compat.py` — offline / newer / up-to-date / breaking-unknown cases
- `README.md`, `SKILL.md` (repo), `mcp_server/agent_guide.py` if the flag is user-facing there
- `mcp_server/compatibility.json` — 0.5.3 entry
- usage skill in `private-skills` — document `--check-latest`/`--apply --latest`, floor bump

## Acceptance criteria

- [ ] A 0.5.1 install reports `update_available: 0.5.2` (proven against real PyPI in a live E2E)
- [ ] Offline / 5xx / malformed JSON ⇒ advisory note, existing behaviour preserved, no non-zero exit from the probe alone
- [ ] `--apply --latest` installs the newest published release; refuses without `--allow-breaking` when it is newer than the contract
- [ ] Plain `--check` / `--apply` behave exactly as today (no silent behaviour change)
- [ ] Cron detector keeps working unmodified and can now surface "update available"
- [ ] Docs + skill + contract entry ship in the same release (docs gate green)

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| A cron path auto-upgrades unexpectedly | `--latest` is opt-in; the detector only *reports* |
| PyPI latency slows every `--check` | Short timeout (default ~5s), skippable with `--no-index` |
| JSON-API lag right after a publish re-creates 0.5.1's confusion | Compare versions, don't trust absence; the resolver (`--refresh`) remains authoritative |
| Version comparison bugs (`0.10.0` vs `0.9.0`) | Reuse the contract's existing `_vkey` comparison, don't hand-roll |

## Open questions

- [ ] Should `--latest` also sync the skill when the newer release records a higher floor? *Default: yes — same as `--apply`.*
- [ ] Expose it in the Hermes cron detector's report text this release, or keep it to the CLI first? *Default: expose it — that is half the value.*
