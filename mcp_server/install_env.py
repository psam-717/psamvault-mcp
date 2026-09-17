"""Which venv is the psamvault-mcp MCP installed in, and is anything using it right now?

``psamvault-mcp compat --apply`` has two install paths and must pick between them:

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
parsing, since the process asking the question (an entry point invoked *from* the venv under test)
must not count itself.

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


def _pipx_env_path(value_name: str) -> Path | None:
    """Ask pipx for one of its own directories (``PIPX_BIN_DIR``, ``PIPX_LOCAL_VENVS``). None if it
    cannot answer — absent, unhappy, or an empty/NULL value."""
    try:
        proc = subprocess.run(
            ["pipx", "environment", "--value", value_name],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT,
        )
        if proc.returncode == 0:
            value = (proc.stdout or "").strip().strip("\"'")
            if value and value.lower() not in {"none", "null"}:
                return Path(value)
    except Exception:  # noqa: BLE001 - the documented default still answers the question
        pass
    return None


def venv_dir() -> Path:
    """The pipx venv directory that holds psamvault-mcp.

    Order: ``PSAMVAULT_MCP_VENV`` → **pipx's own answer** (``PIPX_LOCAL_VENVS``) → this platform's
    default. Asking pipx matters: a relocated pipx home (``PIPX_HOME``) otherwise leaves the holder
    probe watching a ghost venv (usually "free"), uv writing the wheel into that ghost, and
    ``pipx install --force`` hitting the real one — three components, three different interpreters.
    ``bin_dir()`` already asks pipx for ``PIPX_BIN_DIR``; this is the same question about venvs.

    The defaults are the ones ``compat.pipx_python()`` installs into, so the modules cannot disagree.
    When ``LOCALAPPDATA`` is unset on Windows we keep compat's ``~/.local`` fallback rather than
    inventing a third location.
    """
    override = os.environ.get(VENV_ENV)
    if override:
        return Path(override)
    root = _pipx_env_path("PIPX_LOCAL_VENVS")
    if root is not None:
        return root / PACKAGE
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
    """Run a PowerShell script, falling back to ``pwsh`` on a host that only has PowerShell 7.

    Two encoding details, both learned the hard way: the child's console output codepage on Windows is
    the OEM one (437 on this host) while Python 3.11 decodes as UTF-8, so a *single* non-ASCII byte in
    any matched command line — a stray '\xb5', an accented username — makes the implicit decode fail in
    the reader thread, leaving stdout empty. The script therefore forces UTF-8 output, and the decode
    is explicit and lossy-but-loud (`errors="replace"`), so a surprise byte yields unparseable JSON
    that fails the probe loudly instead of an empty string that reads as "nothing is running".
    """
    missing: Exception | None = None
    for exe in ("powershell", "pwsh"):
        try:
            return subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command", _PS_FORCE_UTF8 + script],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=PROBE_TIMEOUT,
            )
        except FileNotFoundError as exc:  # try the next shell; re-raise if there is none
            missing = exc
    raise missing or FileNotFoundError("powershell")


#: Prepended to every probe script. Without it PowerShell writes the console codepage and the parent's
#: UTF-8 decode of a non-ASCII command line silently produces nothing (see _run_powershell).
_PS_FORCE_UTF8 = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"


def _probe_windows(python: Path) -> list[dict]:
    """Processes whose command line carries this venv, via CIM (``ps`` is not available here).

    The script only ENUMERATES; every matching decision happens in :func:`_parse_windows`, on one
    rule (:func:`_venv_matcher`) instead of a PowerShell ``-like`` clause. The coarse
    ``'*<venv>\\*'`` clause this used to send misses any process whose path was spelled with forward
    slashes — verified live, that reports a busy venv as free, the dangerous direction.

    ``ParentProcessId`` travels with every row so :func:`_parse_windows` can drop this invocation's
    own launcher chain: ``psamvault-mcp doctor`` runs as entry-point shim -> python -> this probe, and
    the shim is a process of its own whose command line carries the venv path.

    One row comes back as a bare JSON object and several as a list — ``ConvertTo-Json`` decides that
    at runtime, so both are handled. The array wrapper (and ``-InputObject``) is deliberate: a plain
    ``| ConvertTo-Json`` prints NOTHING when the pipeline is empty, which made "no process matched"
    indistinguishable from "the probe died" — see :func:`is_free`'s fail-busy contract. Now the
    script always prints JSON, so empty output means exactly one thing: the probe failed.
    """
    venv = _venv_root(python)
    script = (
        "$hits = @(Get-CimInstance Win32_Process | "
        # $PID is this very PowerShell process: it holds the pattern text on its own command line
        # and would otherwise always match itself.
        "Where-Object { $_.ProcessId -ne $PID } | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine); "
        "ConvertTo-Json -InputObject $hits -Compress"
    )
    proc = _run_powershell(script)
    if proc.returncode != 0:
        raise RuntimeError(f"powershell exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
    raw = (proc.stdout or "").strip()
    if not raw:
        # The script ALWAYS prints JSON (`[]` when nothing matched), so empty output can only mean the
        # probe failed — a decode error, a killed child, a swallowed write. Returning [] would read as
        # "no process holds the venv": the dangerous direction, because it sends `--apply` down the
        # `pipx install --force` path that recreates the venv while a live server still has it open.
        raise RuntimeError("powershell produced no output — cannot tell whether the venv is held")
    payload = json.loads(raw)  # ValueError/JSONDecodeError propagates on purpose
    if not isinstance(payload, (dict, list, type(None))):
        # Parseable but the wrong shape: this probe cannot be trusted, so it fails loudly rather
        # than reporting an empty (i.e. "free") venv.
        raise ValueError(f"unexpected PowerShell payload: {type(payload).__name__}")
    return _parse_windows(payload, venv, parent_of=_parent_map(payload).get)


def _parent_map(payload: object) -> dict:
    """``{pid: parent_pid}`` from a raw process listing — the links :func:`_drop_launcher_chain` walks.

    Missing links simply end a walk, so a listing that omits ``ParentProcessId`` degrades to the old
    behaviour (self excluded, launcher chain kept) rather than to a wrong answer.
    """
    items = payload if isinstance(payload, list) else ([payload] if isinstance(payload, dict) else [])
    links: dict = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        child, parent = _as_pid(item.get("ProcessId")), _as_pid(item.get("ParentProcessId"))
        if child is not None and parent is not None:
            links[child] = parent
    return links


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


def _parse_windows(payload: object, venv: Path, parent_of=None) -> list[dict]:
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
    found.sort(key=lambda holder: holder["pid"])
    return _drop_launcher_chain(found, matcher.search, parent_of)


def _drop_launcher_chain(found: list[dict], matches, parent_of) -> list[dict]:
    """Drop the processes that ARE this invocation: the running interpreter and its launcher chain.

    ``psamvault-mcp doctor --fix`` runs as entry-point shim -> python -> probe. The shim is a process
    of its own whose command line carries the venv path, so it counted as a HOLDER: the count could
    never reach zero and ``--fix`` refused forever — on every machine, including one whose venv was
    genuinely idle. A shell that inlined the command (``bash -c '… venv … python …'``) does the same.

    Only CONTIGUOUS ancestors are dropped, and only while their command line still carries this venv:
    the walk stops at the first unrelated ancestor, so a genuinely separate process is never hidden.
    A caller that cannot walk (``parent_of is None``) degrades to the old behaviour.
    """
    if parent_of is None:
        return found
    by_pid = {holder["pid"]: holder for holder in found}
    drop = {os.getpid()}
    pid = parent_of(os.getpid())
    while pid and pid not in drop:
        holder = by_pid.get(pid)
        if holder is None or not matches(holder.get("cmdline") or ""):
            break
        drop.add(pid)
        pid = parent_of(pid)
    return [holder for holder in found if holder["pid"] not in drop]


def _posix_parent(pid: int):
    """PPID of ``pid``: ``/proc`` where it exists, ``ps`` where it does not (macOS, BSD)."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        # "pid (comm) state ppid …" — comm may contain spaces and ')', so split after the LAST ')'.
        return int(data[data.rindex(b")") + 1:].split()[1])
    except Exception:
        pass
    try:
        proc = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
        text = (proc.stdout or "").strip()
        return int(text) if proc.returncode == 0 and text else None
    except Exception:
        return None


def _probe_posix(python: Path) -> list[dict]:
    """Processes matching the venv interpreter, via ``pgrep -fl`` ('pid cmdline' lines)."""
    proc = subprocess.run(
        ["pgrep", "-fl", str(python)], capture_output=True, text=True, timeout=PROBE_TIMEOUT
    )
    if proc.returncode == 1:
        return []  # pgrep exits 1 for "no process matched" — a result, not a failure
    if proc.returncode != 0:
        raise RuntimeError(f"pgrep exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
    if not (proc.stdout or "").strip():
        # Exit 0 means pgrep DID match, so stdout cannot legitimately be empty — same decode failure
        # as on Windows, and it must not be read as "nothing holds the venv".
        raise RuntimeError("pgrep matched but produced no output — cannot tell whether the venv is held")
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
    found.sort(key=lambda holder: holder["pid"])
    return _drop_launcher_chain(found, lambda line: needle in line, _posix_parent)


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
    asked = _pipx_env_path("PIPX_BIN_DIR")
    if asked is not None:
        return asked
    try:
        if _is_windows():
            profile = os.environ.get("USERPROFILE")
            return ((Path(profile) if profile else Path.home()) / ".local" / "bin")
        return Path.home() / ".local" / "bin"
    except Exception:
        return None


def linked_apps(prefix: str = "psamvault") -> set[str]:
    """Names of the entry points pipx linked into :func:`bin_dir`, ``.exe`` suffix stripped.

    pipx links one shim per console script (today: ``psamvault-mcp``; the ``psamvault-compat`` shim
    existed until 0.5.3 removed it). Drift shows up here: ``pipx install --force`` relinks the app
    set, while a plain ``uv pip install`` into the venv leaves an old shim pointing at a script the
    new wheel no longer defines.
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
