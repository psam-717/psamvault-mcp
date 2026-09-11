# Version lockstep: MCP ↔ skill

**Status:** 🟢 READY TO BUILD
**Proposed by:** User
**Date:** 2026-09-10

---

## Summary

The psamvault MCP server (PyPI, `psamvault-mcp`) and its usage skill (`psamvault-mcp` in
`psam-717/private-skills`) version independently, and nothing detects when they disagree. Install one
without the other and the skill silently documents a tool set the server does not have.

This plan makes the pair **checkable and self-healing**: a machine-readable contract ships inside the
wheel, a checker detects drift against the *installed* server, and the skill is pulled to the version
pinned for that server. Breaking releases stop for a human.

## Problem (measured, not theoretical)

- Installed skill **1.2.0** documented MCP **0.4.6** (13 tools, incl. `capture_stripe_credentials`).
- The release being prepared is **0.5.0** — also **13 tools**, but `capture_stripe_credentials` is
  gone and `export_key_to_env_file` is new.
- Therefore **a tool-count check cannot detect the drift**, and today the only sync is an agent
  remembering to edit two repos by hand.
- A third copy (`psamvault-mcp.md`, v1.1.0) sits stale in the public `skills` repo.

## Agreed decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Where the pairing lives | `compatibility.json` in the MCP repo, **shipped inside the wheel** so the installed server can report its own expectation |
| 2 | Trigger | **Cron detector** (wakes the agent only on drift) **+ an on-demand command** |
| 3 | Autonomy | Auto-apply normal updates; **stop and ask on a release marked breaking** |
| 4 | Authority on disagreement | **Installed MCP wins** — pull the skill pinned to that MCP version |
| 5 | Latest-version source | Contract declares the target release; installs come from **PyPI** (a `--source git` fallback covers the pre-release case; see Open Questions) |

## Design

### 1. The contract — `mcp_server/compatibility.json` (package data)

```json
{
  "schema": 1,
  "skill": {"name": "psamvault-mcp", "repo": "psam-717/private-skills", "path": "psamvault-mcp/SKILL.md"},
  "releases": [
    {"mcp": "0.5.0", "skill": "1.4.0", "breaking": true,
     "added": ["export_key_to_env_file"], "removed": ["capture_stripe_credentials"],
     "tools": ["...13 sorted tool names..."]},
    {"mcp": "0.4.6", "skill": "1.2.0", "breaking": false,
     "added": ["export_key_to_mcp_config", "verify_api_key"], "removed": [],
     "tools": ["..."]}
  ]
}
```

Only the **tool names** are recorded per release (not signatures) — enough to catch an added/removed
tool, stable across refactors, and cheap to maintain at release time.

### 2. Checker — `mcp_server/compat.py` + `psamvault-compat` entry point

- `load_contract()`, `release_for(version)`, `latest_release()`
- `installed_version()` — `importlib.metadata` of the running environment
- `installed_tools()` — `mcp_server.main.TOOL_DEFINITIONS` (in-process; the process that runs the
  checker IS the installed server, so no subprocess or session is needed)
- `expected_skill_version()` / `installed_skill_version()` (frontmatter parse)
- `check()` → structured drift report: `version_drift`, `skill_drift`, `tool_drift`
  (`missing` / `unexpected`), `breaking_pending`
- CLI: `psamvault-compat` (`--check` default, `--json`, `--apply`, `--allow-breaking`)

Exit codes: `0` in sync, `1` drift found (a cron detector can branch on this without parsing prose).

### 3. `--apply` behaviour

1. Resolve the target release (contract's latest).
2. If the target is marked `breaking` and the installed version differs → **refuse and print what
   changed**, unless `--allow-breaking` is passed.
3. Consult the index **as advice only** (its JSON API lags an upload, so it must never refuse on its own) — a contract entry is created when a release is merged,
   which is before it ships, so applying early otherwise fails deep in the resolver with an opaque
   `no version of psamvault-mcp==X`. Refuse with exit `3` and name the fix (publish first, or
   `--from-git` for a merged-but-unreleased target).
4. Install the target into the pipx venv (uv, with deps — never `--no-deps`).
5. Pull the skill pinned to the target (`git` lookup in the private-skills clone by frontmatter
   version) and write it to `HERMES_HOME/skills/psam-custom/psamvault-mcp/SKILL.md`.
6. Re-verify (version + tool fingerprint + skill version) and report, including
   "restart the gateway/session so the running server picks it up".

### 4. Cron

- `HERMES_HOME/scripts/psamvault-compat-check.py` — thin detector, `.py` only (cron env has no bash),
  absolute interpreter path, cleared `PYTHONPATH`; **always exits 0** and prints nothing when in sync.
  The cron engine hashes the monitor script's stdout to decide whether to wake the agent, and treats a
  non-zero exit as a *script failure* — so the signal is stdout, while `psamvault-compat` keeps the
  0/1/2 exit codes for humans and other tooling. (Learned the hard way: the first fire marked the job
  `last_status=error` because the detector exited 1 on drift.)
- Cron job — daily: silent when in sync; on drift, apply if non-breaking (then verify + report), and
  on breaking drift **notify the user and stop** without installing.

### 5. Release-time discipline (what keeps the contract honest)

Both release skills must gain a step: bump `pyproject` version → **add the contract entry** (skill
version, breaking flag, added/removed, tool fingerprint generated from `TOOL_DEFINITIONS`) → bump the
skill's own `version:` → ship the matching skill in the same change. Without this the contract decays
into a lie, which is worse than not having one.

## Acceptance criteria

1. `psamvault-compat --check` on a mismatched pair exits `1` and names the drift precisely (version /
   skill version / added / removed tools).
2. On a matched pair it exits `0` and stays quiet.
3. `--apply` on a non-breaking drift installs the target and pulls the pinned skill, then re-verifies green.
4. `--apply` on a breaking drift refuses, names the removed/added tools, and requires `--allow-breaking`.
5. `get_version` reports the paired skill version + expected tool count (no new tool added — the tool
   surface must not change for this feature).
6. Unit tests cover: contract load, version→skill mapping, fingerprint diff, breaking detection,
   skill-frontmatter parsing, exit codes.
7. Cron detector stays silent when in sync and wakes the agent only on drift.

## Risks / notes

- **Contract maintenance is the failure mode.** Mitigated by making the contract entry a release step
  in both release skills and by unit-testing that the newest contract entry's fingerprint equals the
  code's actual `TOOL_DEFINITIONS` — that test fails loudly if a release forgets.
- **Runtime installed from git, not PyPI** (today's case) — `installed_version()` reports the
  pyproject version, which can differ from what PyPI serves. The checker reports both and never
  silently reinstalls over a git install unless `--apply` is explicit.
- The stale public-repo copy is **out of scope here**; recommended follow-up: delete it and point at
  private-skills (needs owner decision).
