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

- `fix(install_env): stop counting this command's own launcher as a venv holder` — `psamvault-mcp doctor --fix` runs as entry-point shim → python → probe, and the shim is a process of its own whose command line carries the venv path. Counting it meant the holder total could never reach zero, so **`doctor --fix` refused to repair on every machine, including one whose venv nothing else held**. The running interpreter was already excluded; now self plus the contiguous launcher ancestors (the shim, a shell that inlined the command) are excluded, and the walk stops at the first ancestor that does not carry the venv.
- `fix(doctor): name the process holding the venv, and stop giving advice that repeats what was just done` — the refusal said `2 process(es) holding it … stop the gateway first`, which is unactionable when the gateway is already stopped (the usual case). It now lists every holder with pid and image name, and when those look like MCP servers it points at both the gateways **and** the Hermes desktop app, whose open sessions each hold one and which respawns a server the moment you kill it.
- `fix(install_env): one matching rule for "is this process holding the venv"` — the Windows probe sent a PowerShell `-like` clause and then re-filtered in Python. The clause had to be told both separator spellings by hand and a backslash-only one silently missed real holders (a busy venv read as free — the dangerous direction). The script now only enumerates and `_venv_matcher` decides, so there is a single rule and it is the one the tests exercise.

## Tests

- `test(install_env): pin the launcher-chain exclusion` — the shim, a contiguous shell wrapper, the stop at an unrelated ancestor, and the degraded no-parent-links path.
- `test(doctor): pin holder reporting` — the refusal names each holder, and the advice names the desktop app when MCP servers are holding the venv.

## Docs

- The upgrade playbook's "stop everything" scenario no longer hands out a hand-rolled `-like` kill filter (it disagreed with `doctor`: it matched nothing while `doctor` reported two holders). It drives the kill from `doctor --json` — the probe's own list — uses `hermes gateway stop --all` (there are usually two gateways), and states that the desktop app must be quit.

- `docs(repo): the user documentation now lives in docs/` — overview, installation and host wiring, a feature map, guides for credential injection and browser login, a complete tool reference (13 tools) and a configuration reference; a website can populate from the repo, and `docs/README.md` links every page so none is orphaned.
- `docs(reference): the tool reference is taken from the code` — tool names and parameters come from `TOOL_DEFINITIONS` rather than from prose, so a renamed or removed tool cannot leave the docs describing something the server no longer does.
- `docs(troubleshooting): the two playbooks now carry the same front matter` — every page under `docs/` satisfies one site contract (title/description/order) and is reachable from the docs root. Content unchanged.
