"""Which venv is the psamvault-mcp MCP installed in, and is anything using it right now?

``psamvault-compat --apply`` has two install paths and must pick between them:

* ``pipx install --force ...`` — replaces the whole venv. Only safe when nothing holds the venv's
  ``python.exe``: on Windows a running executable cannot be replaced, so the install fails
  half-way ("Access is denied") and can leave a broken venv behind a live server.
* ``uv pip install --python <venv python> ...`` — writes *into* the existing venv, which is safe
  while MCP processes are running (they keep their already-loaded code until a restart).

So the decision needs two answers: where the venv is, and whether it is being held. This module is
the foundation for both, plus the entry-point linking facts pipx drift detection needs.

**stdlib only, on purpose.** This code runs *during* an upgrade, in whatever interpreter is driving
that upgrade — it cannot assume psutil (or anything else) is installed there.

Two rules the callers depend on:

1. **A probe never raises.** A missing ``powershell``/``pgrep``, a timeout, a non-zero exit or
   output that is not JSON all degrade to an empty holder list. ``holders()`` therefore cannot
   distinguish "nothing holds the venv" from "could not find out" — that is deliberate, and
   ``is_free()`` exists for the question a decision actually hangs on.
2. **``is_free()`` fails BUSY.** An unanswerable probe returns ``False``. A wrong "busy" costs a
   redundant but safe ``uv`` install; a wrong "free" destroys the venv under a running server.
   The risk is asymmetric, so the answer is asymmetric too.

**Self-exclusion.** The probe's own process is never reported as a holder. On Windows the
PowerShell child carries the *pattern text* (the venv path) on its own command line, so the naive
where-clause matches itself — verified on Windows 11 — which would make every venv look busy
forever; ``$PID`` is filtered inside the script. On both platforms ``os.getpid()`` is filtered while
parsing, since the process asking the question (a ``psamvault-compat`` entry point lives *in* the
venv under test) must not count itself.

**Known limit.** Matching is on the venv path *as spelled in a command line*, in either separator
form (Windows keeps the separators a process was launched with — a forward-slashed path is invisible
to a backslash-only pattern, found live). A process launched through a different spelling of the
same directory — a symlink, an 8.3 short name — is therefore not detected; the comparison itself is
case-insensitive, which is what makes the default ``%LOCALAPPDATA%\\pipx\\pipx\\venvs\\…`` spelling
match reliably.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

PACKAGE = "psamvault-mcp"

#: Point at the venv directory directly (tests, non-default pipx roots, a relocated install).
VENV_ENV = "PSAMVAULT_MCP_VENV"

#: A probe is allowed to be slow (PowerShell cold start), never to hang an upgrade.
PROBE_TIMEOUT = 15.0


# ── discovery ──────────────────────────────────────────────────────────────────
def _is_windows() -> bool:
    return os.name == "nt"


def venv_dir() -> Path:
    """The pipx venv directory that holds psamvault-mcp.

    Order: ``PSAMVAULT_MCP_VENV`` → pipx's default for this platform. The defaults are the ones
    ``mcp_server.compat.pipx_python()`` installs into, so the two modules can never disagree about
    which venv "the install" means. When ``LOCALAPPDATA`` is unset on Windows we keep compat's
    ``~/.local`` fallback rather than inventing a third location.
    """
    override = os.environ.get(VENV_ENV)
    if override:
        return Path(override)
    if _is_windows():
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "pipx" / "pipx" / "venvs" / PACKAGE
    return Path.home() / ".local" / "pipx" / "venvs" / PACKAGE


def venv_python_of(venv: Path) -> Path:
    """The interpreter inside a given venv directory."""
    return (venv / "Scripts" / "python.exe") if _is_windows() else (venv / "bin" / "python")


def venv_python() -> Path:
    """The interpreter in :func:`venv_dir` — the ``--python`` target for an in-venv install."""
    return venv_python_of(venv_dir())


def _venv_root(python: Path) -> Path:
    """The venv a given interpreter belongs to (``<venv>/Scripts/python.exe`` → ``<venv>``)."""
    return python.parent.parent


# ── probes: raw, and allowed to fail loudly ────────────────────────────────────
def _probe(python: Path) -> list[dict]:
    """Raw holder probe. RAISES when it cannot tell — ``holders()``/``is_free()`` decide what a
    failure means. Keeping the failure on the inside is what lets the two public functions give
    deliberately different answers for the same broken probe."""
    return _probe_windows(python) if _is_windows() else _probe_posix(python)


def _run_powershell(script: str) -> subprocess.CompletedProcess:
    """Run a PowerShell script, falling back to ``pwsh`` on a host that only has PowerShell 7."""
    missing: Exception | None = None
    for exe in ("powershell", "pwsh"):
        try:
            return subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=PROBE_TIMEOUT,
            )
        except FileNotFoundError as exc:  # try the next shell; re-raise if there is none
            missing = exc
    raise missing or FileNotFoundError("powershell")


def _ps_like_literal(value: str) -> str:
    """Escape a path for use inside a PowerShell single-quoted ``-like`` pattern.

    ``''`` is a literal quote and ``[[]`` a literal ``[`` (``-like`` treats ``[`` as a character
    class, so an unescaped one in a path would silently stop matching — a missed holder).
    """
    return value.replace("'", "''").replace("[", "[[]")


def _probe_windows(python: Path) -> list[dict]:
    """Processes whose command line carries this venv, via CIM (``ps`` is not available here).

    The where-clause is deliberately COARSE and separator-agnostic: Windows command lines keep
    whatever separators the process was launched with, and a path spelled ``D:/…/.venv/Scripts/
    python.exe`` has no backslash in it at all — verified live, a ``'*<venv>\\*'``-only clause misses
    that process entirely (i.e. reports a busy venv as free, the dangerous direction). Precision
    belongs to :func:`_parse_windows`, which requires the venv path followed by a real separator.

    One match comes back as a bare JSON object and several as a list — ``ConvertTo-Json`` decides
    that at runtime, so both are handled. No match at all prints nothing, not ``null``.
    """
    venv = _venv_root(python)
    spelled = str(venv)
    forms = [spelled] + ([spelled.replace("\\", "/")] if "\\" in spelled else [])
    like = " -or ".join(f"$_.CommandLine -like '*{_ps_like_literal(form)}*'" for form in forms)
    script = (
        "Get-CimInstance Win32_Process | "
        # $PID is this very PowerShell process: it holds the pattern text on its own command line
        # and would otherwise always match itself.
        f"Where-Object {{ $_.ProcessId -ne $PID -and ({like}) }} | "
        "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    proc = _run_powershell(script)
    if proc.returncode != 0:
        raise RuntimeError(f"powershell exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
    raw = (proc.stdout or "").strip()
    if not raw:
        return []  # no matches
    payload = json.loads(raw)  # ValueError/JSONDecodeError propagates on purpose
    if not isinstance(payload, (dict, list, type(None))):
        # Parseable but the wrong shape: this probe cannot be trusted, so it fails loudly rather
        # than reporting an empty (i.e. "free") venv.
        raise ValueError(f"unexpected PowerShell payload: {type(payload).__name__}")
    return _parse_windows(payload, venv)


def _venv_matcher(venv: Path) -> re.Pattern:
    """Matches the venv path FOLLOWED BY A SEPARATOR, in either separator form, case-insensitively.

    Two things break a naive substring check here:

    * Windows command lines keep whatever separators the process was launched with. Found live: a
      process started as ``D:/…/.venv/Scripts/python.exe`` has no backslash in its line at all, so a
      backslash-only needle silently misses a real holder.
    * A venv whose name is a PREFIX of another's (``psamvault-mcp`` vs ``psamvault-mcp-old``) must
      not be mistaken for this one — hence the required separator.
    """
    pattern = "".join(r"[\\/]" if char in "\\/" else re.escape(char) for char in str(venv)) + r"[\\/]"
    return re.compile(pattern, re.IGNORECASE)


def _parse_windows(payload: object, venv: Path) -> list[dict]:
    if payload is None:
        return []
    items = payload if isinstance(payload, list) else [payload]
    matcher = _venv_matcher(venv)
    found: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cmdline = item.get("CommandLine")
        if not isinstance(cmdline, str) or matcher.search(cmdline) is None:
            continue
        pid = _as_pid(item.get("ProcessId"))
        if pid is None or pid == os.getpid():
            continue
        found.append({"pid": pid, "name": str(item.get("Name") or ""), "cmdline": cmdline})
    return sorted(found, key=lambda holder: holder["pid"])


def _probe_posix(python: Path) -> list[dict]:
    """Processes matching the venv interpreter, via ``pgrep -fl`` ('pid cmdline' lines)."""
    proc = subprocess.run(
        ["pgrep", "-fl", str(python)], capture_output=True, text=True, timeout=PROBE_TIMEOUT
    )
    if proc.returncode == 1:
        return []  # pgrep exits 1 for "no process matched" — a result, not a failure
    if proc.returncode != 0:
        raise RuntimeError(f"pgrep exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
    needle = str(python)
    found: list[dict] = []
    for line in (proc.stdout or "").splitlines():
        pid_text, _, cmdline = line.strip().partition(" ")
        # pgrep's pattern is a regex over a joined command line: verify the real path is in there.
        if not cmdline or needle not in cmdline:
            continue
        pid = _as_pid(pid_text)
        if pid is None or pid == os.getpid():
            continue
        found.append({"pid": pid, "name": _command_name(cmdline), "cmdline": cmdline})
    return sorted(found, key=lambda holder: holder["pid"])


def _command_name(cmdline: str) -> str:
    first = cmdline.strip().split(" ", 1)[0].strip("\"'")
    return Path(first).name if first else ""


def _as_pid(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# ── the public answers ─────────────────────────────────────────────────────────
def holders(python: Path | None = None) -> list[dict]:
    """Processes holding ``python`` (default: the psamvault-mcp venv interpreter).

    Returns ``[{"pid": int, "name": str, "cmdline": str}, ...]`` sorted by pid.

    An empty list means either "nothing holds it" or "the probe failed" — the callers cannot tell
    them apart here, which is why decisions must go through :func:`is_free`.
    """
    try:
        return _probe(Path(python) if python is not None else venv_python())
    except Exception:
        return []


def is_free(python: Path | None = None) -> bool:
    """True only when the venv is provably unheld.

    **Fails busy**: any probe failure (no ``powershell``/``pgrep``, timeout, non-zero exit,
    unparseable output) returns ``False``. The caller then takes the safe in-venv path instead of
    ``pipx install --force`` — a redundant install is a much better outcome than replacing a venv
    that a running server still has open.
    """
    try:
        return not _probe(Path(python) if python is not None else venv_python())
    except Exception:
        return False


def describe(holders: list[dict]) -> str:
    """One human line: WHO is holding the venv, for the report a user reads.

    Every holder is listed — this is read when an upgrade is being explained, and "3 processes"
    without names is useless for deciding whether the right thing is running.
    """
    count = len(holders)
    if not count:
        return "no processes hold the venv"
    labels = ", ".join(
        f"{holder.get('name') or 'unknown'}({holder.get('pid')})" for holder in holders
    )
    return f"{count} process{'es' if count != 1 else ''} {'hold' if count != 1 else 'holds'} the venv: {labels}"


# ── pipx entry points ──────────────────────────────────────────────────────────
def bin_dir() -> Path | None:
    """pipx's app bin dir — asked of pipx first, so a moved ``PIPX_BIN_DIR`` is honoured.

    Falls back to pipx's default (``%USERPROFILE%\\.local\\bin`` on Windows, ``~/.local/bin``
    elsewhere). ``None`` only when neither source can produce a directory (no pipx, no home).
    """
    try:
        proc = subprocess.run(
            ["pipx", "environment", "--value", "PIPX_BIN_DIR"],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT,
        )
        if proc.returncode == 0:
            value = (proc.stdout or "").strip().strip("\"'")
            if value and value.lower() not in {"none", "null"}:
                return Path(value)
    except Exception:
        pass  # pipx absent or unhappy: the documented default still answers the question
    try:
        if _is_windows():
            profile = os.environ.get("USERPROFILE")
            return ((Path(profile) if profile else Path.home()) / ".local" / "bin")
        return Path.home() / ".local" / "bin"
    except Exception:
        return None


def linked_apps(prefix: str = "psamvault") -> set[str]:
    """Names of the entry points pipx linked into :func:`bin_dir`, ``.exe`` suffix stripped.

    pipx links one shim per console script (``psamvault-mcp``, ``psamvault-compat``). Drift shows
    up here: ``pipx install --force`` relinks the app set, while a plain ``uv pip install`` into the
    venv leaves an old shim pointing at a script the new wheel no longer defines.
    """
    directory = bin_dir()
    if directory is None:
        return set()
    try:
        names = {
            _strip_exe(entry.name)
            for entry in directory.iterdir()
            if entry.name.startswith(prefix) and not entry.is_dir()
        }
    except Exception:
        return set()
    return {name for name in names if name}


def _strip_exe(name: str) -> str:
    return name[: -len(".exe")] if name.lower().endswith(".exe") else name
