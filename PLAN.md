# Export Vault Key → Agent MCP Config

**Status:** 🟢 READY TO BUILD

**Proposed by:** psam
**Date:** 2026-09-02

---

## Summary

Add a psamvault-mcp tool that takes an API key stored in psamvault and writes it **directly into an agent host's MCP server config** (e.g. Hermes `config.yaml` → `mcp_servers.<name>.headers.Authorization`), decrypting locally and never returning the key value to the calling agent. Motivation: when an agent needs an MCP server whose auth is a vault-stored key (Render case: hosted MCP at `https://mcp.render.com/mcp` with `Bearer <key>`), the key currently must either transit chat or be pasted manually — this tool makes the agent able to self-provision the config with the same "never expose the secret" property as `use_credential`.

## Key Points

1. **Local decrypt, remote write path stays secret-safe** — reuse VEK decryption + api_client lookup from `use_credential`; the tool writes config file(s) itself and returns only a redacted summary.
2. **Target = MCP server config entries**, not arbitrary config keys — scope the write to adding/replacing one `mcp_servers.<name>` block in a recognized agent config.
3. **Hermes first** — the concrete need is `HERMES_HOME/config.yaml` (Render hosted MCP). Other agents (Claude/Cursor/Codex) are future targets with different file formats.
4. **File-format-safe edits** — Hermes config.yaml is comment-rich and hand-maintained; naive YAML round-trips destroy it.

## Key Decisions Needed

### Decision 1: Where the capability lives

**Context:** The feature could ship as an MCP tool (agent-facing — matches the ask), as a CLI subcommand (`pv ...`), or both. MCP tools are the only surface agents can call without shelling out; the CLI already has `ak-*` commands and is the manual/scripting surface.

| # | Option | Pros | Cons |
|---|--------|------|------|
| 1 | **psamvault-mcp tool only** | Single repo, matches the ask; agents call it natively | Manual/cron users can't use it |
| 2 | **psamvault-cli subcommand only** | Scriptable, manual-friendly | Agents must shell out via `run_with_credential` (awkward for file writes) |
| 3 | **CLI core + MCP wrapper** | Both surfaces, DRY | Two repos, publish/sync burden |

### Decision 2: What the tool writes and how it knows where

**Context:** "Export to config" can be generic (patch any path in any file) or structured (add an MCP server entry to a known agent config). Generic is flexible but unsafe and needs YAML/JSON/TOML parsers for everything; structured is scoped but needs per-agent knowledge.

| # | Option | Pros | Cons |
|---|--------|------|------|
| 1 | **Generic file/path/value patch** | One tool for every config need | Dangerous; agent could overwrite anything; format chaos |
| 2 | **Structured MCP-server entry writer** (agent + server name + url/command + auth; allowlist of known config paths) | Scoped, safe, matches MCP config shapes; easy to extend agents | Needs a small per-agent "where + how to write" table |
| 3 | **Host-CLI delegation** (`hermes mcp add` subprocess with env-injected key) | Zero file parsing | Ties psamvault to Hermes internals; non-portable; breaks if CLI path/env differs |

### Decision 3: Which agents/formats in v1

**Context:** Hermes = YAML (`config.yaml`, `mcp_servers` dict, HTTP servers use `url` + `headers`). Claude Desktop = JSON (`claude_desktop_config.json`). Cursor = JSON (`~/.cursor/mcp.json`). Codex = TOML/JSON. Supporting more formats means more parsers + more allowlist entries + more test surface.

| # | Option | Pros | Cons |
|---|--------|------|------|
| 1 | **Hermes YAML only** | Solves the actual Render need; one format; comment-preserving edit | Doesn't help other agent setups |
| 2 | **Hermes + Claude/Cursor JSON** | Covers common coding agents | Two more file formats + discovery rules; JSON writes must be surgical too |
| 3 | **Hermes + Claude + Cursor + Codex** | Broad | Scope creep; each format's edge cases; hardest to keep safe |

### Decision 4: YAML editing approach (Hermes config.yaml)

**Context:** Hermes config.yaml has section comments and carefully ordered keys. A full PyYAML round-trip strips comments and re-serializes everything — risky for a live gateway config. The `mcp_servers:` block currently holds `gbrain` and `psamvault` entries.

| # | Option | Pros | Cons |
|---|--------|------|------|
| 1 | **ruamel.yaml round-trip** (comment-preserving) | Safe, correct, standard tool | New dependency (pure-Python) |
| 2 | **PyYAML full round-trip** | No new dep (already common) | Destroys comments/formatting of the whole file |
| 3 | **Surgical text insertion** under `mcp_servers:` | Zero dep | Fragile: indentation, quoting, list-vs-dict, idempotency all hand-rolled |

### Decision 5: Guardrail depth

**Context:** This tool writes to a live agent's config from an agent context — the blast radius is "config corruption" or "wrong value written". Depth of safety rails is a product choice.

| # | Option | Pros | Cons |
|---|--------|------|------|
| 1 | **Minimal** — write, return done | Fast to build | No undo; user can't audit |
| 2 | **Standard** — timestamped backup + dry_run + only touch the named server entry + redacted summary | Auditable, reversible, scoped | Slightly more code |
| 3 | **Paranoid** — everything in Standard + explicit confirm param + refuse overwrite unless replace=true | Maximum safety | Friction on every call |

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| 1 — Where it lives | psamvault-mcp tool only | Matches the ask (agent self-provisioning); CLI parity can come later |
| 2 — What it writes | Structured MCP-server entry writer + allowlist of known agent configs | Scoped + safe, mirrors use_credential design |
| 3 — Target agents v1 | Hermes YAML only | Solves the actual Render need; one format; JSON targets are additive later |
| 4 — YAML editing | ruamel.yaml comment-preserving round-trip | config.yaml is comment-rich; naive round-trips destroy it; pure-Python dep is cheap insurance |
| 5 — Guardrails | Standard: timestamped backup + dry_run + only touch the named server entry + redacted summary | Editing a live agent config from an agent context needs backup + dry-run as the trust floor; paranoid confirm adds friction |
| 6 — Tool name | `export_key_to_mcp_config` | Explicit about source (vault key) and destination (MCP config) |
| 7 — Server shapes v1 | HTTP (`url`+`headers`) AND stdio (`command`/`args`+`env`) | Both are first-class Hermes MCP shapes; env injection is cheap alongside headers |
| 8 — Existing entry | Error unless `replace=true` | Predictable, safe default; explicit opt-in to overwrite |

## Architecture: how targets handle "each agent has its own CLI"

**Chosen: psamvault does NOT shell out to agent CLIs (`hermes mcp add`, `claude mcp add`, etc.) as the write mechanism.**

Rationale:
- Agent CLIs are interactive for auth values (Hermes `mcp add --auth header` prompts — verified no non-interactive flag) and depend on PATH/env of the MCP subprocess (Hermes spawns psamvault-mcp with a *filtered* environment — shelling back into the host is fragile/circular).
- Vendor CLIs change; format writers we control don't drift under us.

**Design: a small target-adapter interface** — each supported agent = one adapter module that knows its config path, format, and how to write an `mcp_servers.<name>` entry (Hermes v1 = direct comment-preserving YAML edit). Adapters decide their own mechanism; a future agent with a solid non-interactive CLI *could* delegate to it inside its adapter. psamvault core stays: decrypt key → hand to adapter → return redacted summary.

**Rejected alternative (documented):** generic per-agent CLI delegation as the primary mechanism — would avoid config-format code but couples psamvault to external CLIs' presence, versions, and interactivity.

## Open Questions (resolved)

- [x] ~~Tool naming~~ → `export_key_to_mcp_config`
- [x] ~~HTTP-only or stdio too~~ → both (HTTP `url`+`headers` and stdio `command`/`args`+`env`)
- [x] ~~CLI parity~~ → later, not v1 (feature lives in psamvault-mcp only)
- [x] ~~Existing entry semantics~~ → error unless `replace=true`

---

## Build Order

| Step | Feature | Depends On | Status |
|------|---------|-----------|--------|
| 1 | Add `ruamel.yaml` dep + bump version 0.4.4 → 0.4.5 in pyproject.toml | — | 🔴 |
| 2 | New `mcp_server/config_targets.py`: Hermes YAML target adapter (resolve config path via `HERMES_HOME` env → platform default; comment-preserving add/replace of `mcp_servers.<name>`; timestamped backup; dry_run; HTTP + stdio entry builders) | Step 1 | 🔴 |
| 3 | `export_key_to_mcp_config` in `mcp_server/tools.py`: login check → API-key lookup (api_client) → local VEK decrypt (reuse `decrypt_api_key`) → validate inputs (url+headers XOR command+args+env, auth mode) → adapter write (dry_run/replace honored) → redacted summary (path, server name, backup path, NOT the key) | Step 2 | 🔴 |
| 4 | Register tool in `mcp_server/main.py`: Tool schema, call_tool dispatch, instructions string | Step 3 | 🔴 |
| 5 | Docs: `agent_guide.py` index, `prompts/general-rules.md`, AGENTS.md tool table | Step 4 | 🔴 |
| 6 | Tests (`tests/test_export_key_to_mcp_config.py`): happy path on temp config copy, comment preservation, dry_run writes nothing, backup created, error-if-exists, replace=true, malformed config → clean error, unknown agent → error, key value never in output, stdio env shape | Steps 1–4 | 🔴 |
| 7 | `pytest` green + local install check (rebuild wheel, `pipx install --force` in dev or editable run) | Step 6 | 🔴 |
| 8 | E2E: run against a COPY of the real Hermes config (dry_run → real), then `hermes mcp test render` after gateway restart + verify `mcp_render_*` in a new session | Step 7 | 🔴 |
| 9 | Publish (TestPyPI → PyPI → GitHub release) via py-publish workflow | Step 8 | 🔴 |

## Files Likely to Change

- `pyproject.toml` — ruamel dep, version bump
- `mcp_server/config_targets.py` — **new**: HermesTarget adapter (path resolution, YAML write, backup, dry_run)
- `mcp_server/tools.py` — new tool + shared decrypt/lookup helpers
- `mcp_server/main.py` — tool registration + dispatch + instructions
- `mcp_server/agent_guide.py` — tool index entry (search_vault_tools discovery)
- `mcp_server/prompts/general-rules.md` — tool reference entry
- `AGENTS.md` — API Key Operations table row
- `tests/test_export_key_to_mcp_config.py` — **new** test file
- `PLAN.md` — this file (feature record; delete or archive after release)

## Acceptance Criteria

- [ ] `export_key_to_mcp_config` adds/replaces exactly one `mcp_servers.<name>` entry in Hermes `config.yaml`; all comments + unrelated keys preserved (verified by diff on a copy)
- [ ] HTTP shape writes `url` + `headers.Authorization: Bearer <key>`; stdio shape writes `command`/`args` + `env.<VAR>: <key>`
- [ ] `dry_run=true` returns the intended change and writes nothing
- [ ] Timestamped `.bak` backup created before any write
- [ ] Existing server name → error; `replace=true` → overwrites
- [ ] Unknown agent, malformed config, missing key → clean, helpful errors
- [ ] Key value never appears in any tool output (asserted in tests)
- [ ] `pytest` green; E2E verified on a config copy and on the real config (dry-run first)
- [ ] Render MCP actually usable afterwards: `hermes mcp test render` passes and `mcp_render_*` tools appear in a new session

## Risks & Mitigations

- [ruamel round-trip surprises (aliases/tags/quoting) on a real config] — validate against a byte-copy of the live `config.yaml` before touching the real file; backup always
- [Corrupting the live gateway config] — dry_run default in docs, backup before write, only-target-one-entry, E2E on copies first
- [Config path resolution differs across machines (HERMES_HOME env vs defaults)] — adapter checks `HERMES_HOME` then per-OS defaults; path overridable for tests
- [Key leak via errors/logs] — redaction helper + tests assert absence
- [main.py instructions string drift] — update manually with the other tools (repo pattern)
- [Live gateway needs restart to load new MCP server] — documented in E2E step; new sessions only (Hermes behavior)
