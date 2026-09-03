# Verify Vault Keys Before Export (never export an unverified key)

**Status:** 🟢 READY TO BUILD (implemented Sep 3 2026 — PR pending owner merge)

**Proposed by:** psam
**Date:** 2026-09-03

---

## Summary

`export_key_to_mcp_config` (0.4.5, merged PR #16) writes a vault API key into an agent host's MCP config. Today it is a *blind write*: it trusts whatever key name the agent passes. A wrong, expired, or revoked key lands in `config.yaml` and poisons every future session that tries to connect. This feature makes export a **validated action**: the key is proven against a cheap, read-only provider check *before* any config is written, and the export refuses on failure. Motivation came from real use — the Render key was verified via `use_credential` (`GET https://api.render.com/v1/owners` → 200) before export, and that discipline should be structural, not manual. The docs shipped in PR #16 already encode the rule ("never export an unverified key — manual step until built-in verification lands"); this feature makes the built-in real.

## Key Points

1. **Export must refuse invalid keys** — verification is a gate before the write, with config untouched on failure; the key value never enters the agent's context at any point.
2. **Verification needs a per-provider "recipe"** — how to probe provider X (endpoint + method + expected success). Render's is known (`GET /v1/owners` → 200); others must be curated.
3. **Not all keys are HTTP-probable** — stdio/env-style keys (e.g. a token consumed by a local command) need a different definition of "verified" or an explicit acknowledged skip.
4. **This overlaps the earlier deferred registry idea** — probe recipes could seed the endpoint registry (`list_known_mcp_endpoints` / `provider=` lookup, previously scored 6/10 and deferred). Scope boundary must be explicit.

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Verification architecture | Both via shared verify executor (opt 3) | Export becomes a gate *and* verification is reusable as a standalone tool; one internal executor, no way to forget |
| Probe recipe storage | Hybrid: bundled defaults + per-call override (opt 4) | Common providers auto-verify from a curated table; long tail covered by explicit `verify_url` |
| stdio/env verification | HTTP/bearer auto-verify only in v1 (opt 1) | stdio/env exports require explicit agent check or loud `skip_verify`; command probes deferred |
| Failure semantics | Hard block + `skip_verify` escape hatch (opt 2) | Strict by default; loud explicit override for providers verification cannot cover |
| Endpoint-registry tie-in | Verification-only recipes now, schema shaped to grow (opt 1) | Recipe table uses a provider-keyed schema (url/method/expect) the future registry can reuse; no extra tools in this PR |

## Key Decisions Needed (record — options for rejected alternatives)

### Decision 1: Verification architecture — ✅ DECIDED (option 3)

**Context:** Where should the verification logic live? It affects tool count, composability, and how much changes in `export_key_to_mcp_config`.

| # | Option | Pros | Cons |
|---|--------|------|------|
| 1 | **Built-in only** — `export_key_to_mcp_config` gains verify-before-write (auto-probe when a recipe is known, `verify_url` param otherwise, refuse on failure) | One flow, no way to forget; matches the rule exactly | Verify not reusable outside export |
| 2 | **Standalone `verify_api_key` tool only** — export unchanged; agent calls verify first | Simple, composable, zero risk to existing export behavior | Agent can still skip it — same forgetting problem we're fixing |
| 3 | **Both via a shared verify executor** — one internal `verify()` used by export's gate AND exposed as a standalone tool | Safety by default + reusable verification for other flows | More surface: new tool schema, docs, tests |
| 4 | **No structural change now** — keep the manual docs-only rule | Zero code | Doesn't actually fix the gap; relies on agent discipline |

### Decision 2: Probe recipe storage — ✅ DECIDED (option 4)

**Context:** To verify "does key X work for provider Y" the tool needs a recipe (probe URL + method + expected success). Where do recipes live and who maintains them?

| # | Option | Pros | Cons |
|---|--------|------|------|
| 1 | **Bundled static recipe table in the package** (curated dict/yaml shipped with releases, PR-reviewed) | Works offline, versioned, reviewed; render entry verified today | Curated list only; must be maintained; entries can rot |
| 2 | **Per-call `verify_url` param only** (agent/user supplies the whoami endpoint each time) | No bundled data, full flexibility | Agent must know/guess the probe URL — the same discovery problem as the export URL |
| 3 | **Vault-stored recipe metadata** (recipe lives beside the key in psamvault) | User-editable, per-key, syncs with vault | Vault is a secrets store; storing non-secret metadata muddies it; empty until populated |
| 4 | **Hybrid: bundled defaults + per-call override** | Best of 1+2; bundled for common providers, override for the long tail | Slightly more code paths |

### Decision 3: What counts as "verified" for stdio/env keys — ✅ DECIDED (option 1)

**Context:** HTTP bearer keys probe cleanly (GET whoami → 200). Stdio/env keys (command + env var) have no HTTP endpoint to hit.

| # | Option | Pros | Cons |
|---|--------|------|------|
| 1 | **HTTP/bearer auto-verify only in v1**; stdio/env exports require an explicit agent check or `skip_verify=true` acknowledgment | Clear semantics, small scope | stdio/env exports stay manual |
| 2 | **Recipes may define a command probe** (run via `run_with_credential`-style execution, e.g. `whoami`/list) | Full coverage for stdio servers | Probe commands are harder to curate safely; more executor code |
| 3 | **Excluded entirely for now** — documented gap, verify only ever applies to HTTP keys | Simplest | Weakest guarantee for a whole transport class |

### Decision 4: Failure semantics — ✅ DECIDED (option 2)

**Context:** What happens when verification fails or no recipe is known?

| # | Option | Pros | Cons |
|---|--------|------|------|
| 1 | **Hard block** — export refuses, config untouched, returns provider status + guidance | Matches "never export unverified" literally | User must resolve or explicitly skip |
| 2 | **Hard block + `skip_verify` escape hatch** (loud, recorded in output) | Strict default with an explicit override for edge cases | Two paths to test/document |
| 3 | **Warn-and-continue** (write anyway with warning) | Never blocks a user | Violates the core rule; dangerous default |

### Decision 5: Relationship to the deferred endpoint registry — ✅ DECIDED (option 1)

**Context:** Earlier discussion scored a full registry (`list_known_mcp_endpoints`, `provider=` lookup) 6/10 and deferred it. Probe recipes overlap that idea.

| # | Option | Pros | Cons |
|---|--------|------|------|
| 1 | **Verification-only recipes now**; shape the data structure so it can grow into the full registry later | Small scope; keeps option open | Registry still deferred |
| 2 | **Seed the registry now** — recipe table doubles as endpoint registry; add `provider=` + list tool in the same feature | One data model, one maintenance story | Bigger PR; resurrects a 6/10 feature on the back of a 7/10 one |
| 3 | **Keep them fully separate** (recipes ad hoc, no shared model) | No coupling | Likely rework later |

## Secondary Decisions (defaults — confirm or override)

1. **Error taxonomy:** distinguish key-invalid (HTTP 401/403) from probe-unavailable (5xx/timeout/network). Different guidance: "key rejected by provider (401) — check name/expiry" vs "probe failed (502) — retry or pass verify_url". Default: implement distinction.
2. **No last-verified persistence.** `list_api_keys` does not gain timestamps in this feature (scope creep). Verification runs live at export time.
3. **Version bump: 0.4.6** — feature addition following the 0.4.5 pattern; export behavior change is additive (new gate with documented skip path).

## Build Order

| Step | Feature | Depends On | Status |
|------|---------|-----------|--------|
| 1 | Probe recipe store: `mcp_server/verify_recipes.py` (provider-keyed dict: url, method, expect, auth_kind; render entry) | — | 🔴 |
| 2 | Shared verify executor: `verify_key(key, recipe|verify_url)` — httpx probe with credential, redacted result (success / status / error class) | Step 1 | 🔴 |
| 3 | Wire gate into `export_key_to_mcp_config` — auto-verify for recipe providers; `verify_url` override; `skip_verify` loud escape; hard-block on failure before any write | Step 2 | 🔴 |
| 4 | New standalone tool `verify_api_key` — same executor, returns pass/fail + status (no secret) | Step 2 | 🔴 |
| 5 | Register tool in `main.py` (schema, dispatch, instructions) — 13 tools | Step 4 | 🔴 |
| 6 | Docs: agent_guide.py prompt updates (Step 2 becomes automatic for known providers), general-rules Rule 8 rewrite, AGENTS.md tables, troubleshooting | Steps 3-5 | 🔴 |
| 7 | Tests: recipes unit, executor (200/401/5xx/timeout, key never in output), export gate (auto-verify ok, fail blocks + config untouched, skip_verify works, verify_url override), tool registration, stdio/env skip path | Steps 1-6 | 🔴 |

## Files Likely to Change

- `mcp_server/verify_recipes.py` — **new**: bundled recipe table (provider → probe spec), shaped for future registry reuse
- `mcp_server/verify.py` — **new**: shared verify executor (httpx probe, error taxonomy, redaction)
- `mcp_server/tools.py` — export gate wiring + new `verify_api_key` tool impl
- `mcp_server/main.py` — tool schema + dispatch for `verify_api_key`
- `mcp_server/agent_guide.py` + `mcp_server/prompts/general-rules.md` — docs: verification now automatic for known providers
- `AGENTS.md` — tool table rows
- `tests/test_verify_recipes.py`, `tests/test_verify.py`, `tests/test_export_verify_gate.py`, `tests/test_verify_api_key_tool.py` — new tests

## Acceptance Criteria

- [ ] Exporting a key for a provider with a known recipe auto-verifies; failure → hard block, config untouched, clear error (401/403 vs 5xx/network distinguished)
- [ ] `verify_url` overrides the recipe; unknown providers without `verify_url` and without `skip_verify` are refused
- [ ] `skip_verify=true` exports work and the response loudly records that verification was skipped
- [ ] stdio/env exports require explicit check or `skip_verify` (no silent auto-skip)
- [ ] New `verify_api_key(key_name, verify_url?)` tool: pass/fail + status, key value never in output
- [ ] Recipe store is provider-keyed and structured so the future endpoint registry can reuse it (no rework required)
- [ ] All existing 131 tests pass + new tests green; docs updated (how-to Step 2, Rule 8, AGENTS.md)
- [ ] Version bumped 0.4.6; granular commits on a feature branch → PR (owner merges)

## Risks & Mitigations

- **Recipe drift** (probe endpoints change) — recipes live in code, PR-reviewed, verified entries only; render verified live Sep 3 2026.
- **Probe availability flakiness** — error taxonomy separates key-invalid from probe-unavailable so a transient 5xx doesn't masquerade as a bad key; retry/guidance in message.
- **False confidence from skip_verify** — skip is loud and recorded in the tool result; docs say skip only when a provider has no sane probe.
- **Scope creep into the registry feature** — D5 option 1: no new endpoint tools in this PR; schema foresight only.
