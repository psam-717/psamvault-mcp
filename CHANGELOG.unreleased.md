# Unreleased Changes

<!--
  This file tracks changes merged to main but NOT yet published to PyPI.
  It is the single place to look for "what is pending for the next release?".

  When preparing a release:
    1. Cut/paste the sections below into the GitHub release notes
    2. Prepend them to CHANGELOG.md under the new version header
    3. Clear this file's content (keep this header + comment)
    4. Bump the version, add the compatibility.json entry, then publish

  Sections: Added / Changed / Fixed / Tests / Docs. One line per user-visible change:
  `type(scope): what changed and why it matters`.
-->

## Fixed

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

## Tests

- `test(doctor): pin the in-place record repair` — the record is corrected with the venv busy, the edit changes nothing else in pipx's file (CRLF endings included), a dated backup is kept, and an unlinked entry point still needs a free venv rather than being claimed as repaired. An unexpected pipx schema is reported, never guessed at.
- `test(install_env): pin the launcher-chain exclusion` — the shim, a contiguous shell wrapper, the stop at an unrelated ancestor, and the degraded no-parent-links path.
- `test(doctor): pin holder reporting` — the refusal names each holder, and the advice names the desktop app when MCP servers are holding the venv.
- `test(ci): run the suite on Python 3.11 and 3.13 for every push and pull request` — this repo had no CI at all, so the suite had only ever run on one machine and one OS, which is what let the two bugs above survive.
- `test(cmd_runner): pin that the timeout kill cannot signal the caller` — a timed-out command runs in its own process group, and the kill never signals ours.
- `test(compat): pin checkout discovery` — the env override wins, a checkout is found, the failure names `PSAMVAULT_MCP_REPO`, and the clone defaults to the repo sibling.
- `test(tools): use each platform's shell syntax for the env-echo tests` — `echo %VAR%` is cmd.exe, so on Linux those two assertions never validated redaction at all. The POSIX form is quoted as well, because an unquoted `$VAR` is glob-expanded and the fixture key is literally `***`.
- `test(dispatch): pin that every declared tool parameter reaches its handler` — three cases: `project_name` is forwarded, an omitted `project_name` stays `None` (the backwards-compatible `env/.env/KEY` layout), and no property declared in a tool schema is silently dropped. The third case fails on the unfixed dispatch, which is how the bug class gets caught next time rather than only this instance.

## Docs

- The upgrade playbook's "stop everything" scenario no longer hands out a hand-rolled `-like` kill filter (it disagreed with `doctor`: it matched nothing while `doctor` reported two holders). It drives the kill from `doctor --json` — the probe's own list — uses `hermes gateway stop --all` (there are usually two gateways), and states that the desktop app must be quit.

- `docs(repo): the user documentation now lives in docs/` — overview, installation and host wiring, a feature map, guides for credential injection and browser login, a complete tool reference (13 tools) and a configuration reference; a website can populate from the repo, and `docs/README.md` links every page so none is orphaned.
- `docs(reference): the tool reference is taken from the code` — tool names and parameters come from `TOOL_DEFINITIONS` rather than from prose, so a renamed or removed tool cannot leave the docs describing something the server no longer does.
- `docs(troubleshooting): the two playbooks now carry the same front matter` — every page under `docs/` satisfies one site contract (title/description/order) and is reachable from the docs root. Content unchanged.
