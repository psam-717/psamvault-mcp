# Export a credential into a `.env` file

**Status:** 🟡 EXPLORING

**Proposed by:** User
**Date:** 2026-09-10

---

## Summary

psamvault can already export a stored key into an agent's **MCP server config** (`config.yaml` →
`mcp_servers.<name>`). It cannot write a key where most agent *tools* actually read it: a `.env`
file. That gap bit us directly — `web_extract` needs `TAVILY_API_KEY` in `HERMES_HOME/.env`, and
today the only way there is a manual copy/paste of the plaintext. This feature adds a first-class
"put this vault key into this `.env` as `THIS_VAR`" path, so a key can go from the encrypted vault
to a working tool variable without the value ever entering the agent's context or chat.

## Key Points

1. **Real need, not hypothetical:** the Tavily/web_extract setup was blocked on exactly this step.
2. **Secret hygiene is the point:** the tool writes the value; the caller/agent only learns the
   variable name and a redacted confirmation.
3. **Reversible:** every write leaves a timestamped backup, exactly like the config writer does.

## Key Decisions Needed

### Decision 1: Tool shape

**Context:** the existing `export_key_to_mcp_config` handles MCP-server config only. A `.env` write
is a different destination shape (flat `KEY=value` lines, no YAML), so either it grows a sibling
tool or the existing tool becomes destination-aware.

| # | Option | Pros | Cons |
|---|--------|------|------|
| 1 | **New tool `export_key_to_env_file`** | Focused, obvious name, no risk to the working config path, easy to test in isolation | A 14th tool; some duplicated boilerplate (decrypt, verify gate, backup) |
| 2 | **Extend `export_key_to_mcp_config` with `target: mcp_config \| env_file`** | One entry point to learn; shares decrypt/verify/backup code | The name stops matching half its job; every param (server_name, inject_as, header_name) becomes conditional |
| 3 | **Generalize to `export_key(key_name, target, ...)`** | Cleanest long-term: one dispatcher, pluggable targets (`config_targets.py` already has the shape) | Bigger refactor of a tool that is currently verified working — regression risk on the path psam relies on for Render |

### Decision 2: Write semantics when the variable already exists

**Context:** `.env` files are hand-edited and may already contain the variable name. Overwriting
vs refusing vs appending changes what "just works" means.

| # | Option | Pros | Cons |
|---|--------|------|-------|
| 1 | **Update the line in place if the name exists (idempotent), else append** | Re-running is safe; no duplicate keys; matches how `export_key_to_mcp_config` treats an existing entry with `replace=true` | A silent overwrite could clobber a value the user set deliberately (mitigated by the backup) |
| 2 | **Refuse without `replace=true`** | Explicit, mirrors the MCP-config tool's default exactly | An extra round-trip for the common "I want it set to the vault value" case |
| 3 | **Always append, never touch existing lines** | Never destroys anything | Duplicate keys in one file — last-one-wins behaviour differs per loader; a trap |

### Decision 3: Which `.env` file, and how is it chosen?

**Context:** "the `.env` file" is ambiguous on this machine: `HERMES_HOME/.env` (what Hermes tools
read), a project's `./.env` (what app code reads), and per-profile locations all exist.

| # | Option | Pros | Cons |
|---|--------|------|------|
| 1 | **Explicit `env_path` required** | Zero ambiguity, no surprise writes to the wrong file | The caller must know the layout; not "just works" |
| 2 | **`env_path` optional, default `HERMES_HOME/.env`, with a `scope: hermes \| project` shorthand** | Works out of the box for the common case, still explicit when needed | Needs the same path-resolution logic as `resolve_hermes_config_path` (already exists, so cheap) |
| 3 | **Auto-detect from the agent name (`hermes` → HERMES_HOME, `claude` → ~/.claude, ...)** | Feels magic, helps multi-agent users | Fragile guessing; wrong-file writes are the worst failure mode for a secret |

**Resolution (see Decisions Made):** option 1's explicit path stays the fallback, but the default is
a **per-agent table** — `hermes` → `HERMES_HOME/.env`, other hosts added only once their location is
verified, and an unknown agent requires `env_path` rather than guessing.

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Tool shape | **New tool `export_key_to_env_file`** | Leaves the working MCP-config export path untouched; focused name; testable in isolation |
| Existing variable | **Update the line in place, append when absent** | Idempotent re-runs, no duplicate keys, timestamped backup still written before the edit |
| Target file | **Per-agent default table + explicit `env_path` override** | `agent="hermes"` → `HERMES_HOME/.env` out of the box; other hosts (claude, grok, ...) added as entries once their location is *verified*, never guessed |

**Rule for the table:** an entry is added only when the host's `.env` location has been confirmed —
same policy as `verify_recipes.get_verify_recipe` (verified entries only). An unknown `agent` returns
a clear error naming the known agents and asking for `env_path`, never a silent guess.

## Build Order

| Step | Work | Depends On | Status |
|------|------|-----------|--------|
| 1 | `resolve_agent_env_path(agent, env_path, env)` in `config_targets.py` + tests (hermes default, unknown agent errors, explicit path wins) | — | 🔴 |
| 2 | `write_env_var(env_path, name, value, dry_run)` — in-place update, append when absent, timestamped backup, parent dirs, owner-only where supported + tests | Step 1 | 🔴 |
| 3 | `export_key_to_env_file` tool: decrypt → optional verify gate → write → redacted result (`env_path`, `variable`, `action`, `backup_path`) + tests | Step 2 | 🔴 |
| 4 | Register the tool (14 tools), update README/SKILL.md tool lists and the agent guide | Step 3 | 🔴 |
| 5 | Live verification over real stdio MCP (write into a temp `.env`, then a real `HERMES_HOME/.env` case for Tavily) | Step 4 | 🔴 |

## Acceptance Criteria

- [ ] `export_key_to_env_file(key_name="tavily", env_var_name="TAVILY_API_KEY")` writes
      `HERMES_HOME/.env` without an explicit path, and the key value is never returned.
- [ ] Re-running updates the existing line instead of appending a duplicate.
- [ ] A timestamped backup exists for every write that modified an existing file.
- [ ] `dry_run=True` reports the intended change and writes nothing.
- [ ] Unknown `agent` without `env_path` → clear error listing supported agents.
- [ ] A non-HTTP-verifiable key requires `skip_verify=true` (same gate as stdio exports).
- [ ] Live end-to-end: a Hermes tool that reads the variable (`web_extract` with `TAVILY_API_KEY`)
      works straight after the export, with no manual copy/paste.

## Files Likely to Change

- `mcp_server/config_targets.py` — agent→env-path resolution + dotenv writer (sibling of the existing YAML writer)
- `mcp_server/tools.py` — the new tool + verification gate reuse
- `mcp_server/main.py` — tool registration
- `README.md`, `SKILL.md`, `mcp_server/agent_guide.py` — tool count and usage docs
- `tests/test_config_targets.py`, `tests/test_tools.py` — unit coverage
- `PLAN_env_export.md` — this file

## Risks & Mitigations

- **Wrong-file write** (a secret in the wrong `.env`) — explicit-path override, per-agent table only
  with verified locations, unknown agent fails closed.
- **Plaintext at rest is inherent** — `.env` is unencrypted by design; that is why the vault stays the
  source of truth and this tool only ever writes a named variable.
- **Overwriting a hand-set value** — timestamped backup before every modifying write.
- **Duplicate variables from other tooling** — we update in place; the tool never appends a second
  line for a variable that already exists.

## Open Questions

- [ ] Verification gate: an HTTP key can be probed (`verify_api_key`); an arbitrary variable (e.g.
      a signing secret) cannot. Require `skip_verify=true` for non-HTTP-verifiable keys, as the
      stdio export path already does?
- [ ] Should the tool also support the reverse (import a value from a `.env` into the vault) — or is
      that out of scope?
- [ ] Restrict the target file to known-safe locations, or allow any path the caller names?
- [ ] File permissions on the written file (owner-only where the OS supports it)?

## Rejected so far

- **Documenting the shell workaround instead of building this** (`psamvault ak get X | ... > .env`):
  rejected — it puts the plaintext on the command line/pipeline and relies on every caller
  remembering the incantation; it is the exact workaround that motivated the feature.
