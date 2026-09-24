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
- `fix(cmd_runner): a timed-out run_with_credential killed the caller on POSIX` — the command was spawned without its own session, so it shared the caller's process group, and the timeout handler's `killpg` therefore signalled that same group: **the MCP server itself**. A command that hit the timeout took the server down instead of returning a timeout, and in CI it killed the test runner (four legs, ~11 minutes of silence, exit 137, no log published). The child now gets its own session on POSIX, and the kill refuses to signal our own group; Windows was already scoped by PID.
- `fix(compat): discover the checkout instead of hardcoding the author's path` — `DEFAULT_REPO` and `DEFAULT_CLONE` were `D:/Projects/py-projects/...`, so `--from-git` and the skill sync failed for every other user, and on CI, with a `FileNotFoundError` naming a path they had never heard of. Discovery is now `PSAMVAULT_MCP_REPO` → the checkout this process runs from → the checkout the current directory is inside; when nothing is found the error names the env var that fixes it, and `--apply` prints that message instead of a traceback.

## Tests

- `test(install_env): pin the launcher-chain exclusion` — the shim, a contiguous shell wrapper, the stop at an unrelated ancestor, and the degraded no-parent-links path.
- `test(doctor): pin holder reporting` — the refusal names each holder, and the advice names the desktop app when MCP servers are holding the venv.
- `test(ci): run the suite on Python 3.11 and 3.13 for every push and pull request` — this repo had no CI at all, so the suite had only ever run on one machine and one OS, which is what let the two bugs above survive.
- `test(cmd_runner): pin that the timeout kill cannot signal the caller` — a timed-out command runs in its own process group, and the kill never signals ours.
- `test(compat): pin checkout discovery` — the env override wins, a checkout is found, the failure names `PSAMVAULT_MCP_REPO`, and the clone defaults to the repo sibling.
- `test(tools): use each platform's shell syntax for the env-echo tests` — `echo %VAR%` is cmd.exe, so on Linux those two assertions never validated redaction at all. The POSIX form is quoted as well, because an unquoted `$VAR` is glob-expanded and the fixture key is literally `***`.

## Docs

- The upgrade playbook's "stop everything" scenario no longer hands out a hand-rolled `-like` kill filter (it disagreed with `doctor`: it matched nothing while `doctor` reported two holders). It drives the kill from `doctor --json` — the probe's own list — uses `hermes gateway stop --all` (there are usually two gateways), and states that the desktop app must be quit.
