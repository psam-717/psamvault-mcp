"""Report which psamvault-mcp version is INSTALLED and which one a new session would be SERVED.

Two different numbers matter, and they can disagree:

  * installed — the version recorded in the interpreter's site-packages (read with
    ``importlib.metadata`` from a NEUTRAL cwd).
  * served    — what a freshly spawned MCP server (``python -c "from mcp_server.main import main; main()"``)
    reports over JSON-RPC for ``tools/call get_version``. That is what a NEW agent session gets.

Long-lived sessions keep the MCP process they spawned when they started, so they can serve an older
version than the one installed. This command spawns its own server, so it always shows what a new
session would get.

Exit codes: ``0`` everything agrees, ``1`` mismatch or probe failure, ``2`` no usable interpreter.

Usage::

    psamvault-mcp selfcheck --expect 0.5.2
    psamvault-mcp selfcheck --json
    psamvault-mcp selfcheck --python "C:/Users/me/AppData/Local/pipx/pipx/venvs/psamvault-mcp/Scripts/python.exe"

Why the neutral cwd: run from the repository checkout, ``import mcp_server`` resolves to the working
tree instead of the installed wheel, so the "installed" reading is a lie. Both the metadata read and
the spawned server therefore run from a directory that is not the checkout.

Security: this command only reads version metadata and spawns the server's own stdio protocol. It
never opens the vault, never authenticates, and never prints credential material.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

DIST = "psamvault-mcp"
SERVER_SPAWN_CODE = "from mcp_server.main import main; main()"
CLIENT_INFO = {"name": "psamvault-mcp-selfcheck", "version": "1"}
PROTOCOL_VERSION = "2024-11-05"

# Deadlines. A real deadline (queue + reader threads) is used for the child: a bare readline() on a
# server that never answers would hang the command forever.
CHILD_TIMEOUT_S = 120.0
INSTALLED_TIMEOUT_S = 60.0
POLL_S = 0.25

EXIT_OK = 0
EXIT_PROBLEM = 1
EXIT_NO_INTERPRETER = 2

_METADATA_CODE = (
    "import importlib.metadata as md, json\n"
    f"print(json.dumps({{'version': md.version({DIST!r})}}))\n"
)


# ── environment helpers ────────────────────────────────────────────────────────
def repo_root() -> Path:
    """The checkout this module was imported from (used only to recognise and avoid it)."""
    return Path(__file__).resolve().parent.parent


def _is_inside(path: str | os.PathLike[str], root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def neutral_cwd() -> str:
    """A cwd that is NOT the repository checkout, so the local package cannot shadow site-packages."""
    root = repo_root()
    anchor = Path(tempfile.gettempdir()).anchor or os.sep
    for candidate in (tempfile.gettempdir(), anchor, os.path.expanduser("~")):
        try:
            if candidate and os.path.isdir(candidate) and not _is_inside(candidate, root):
                return os.path.normpath(candidate)
        except OSError:  # pragma: no cover - defensive
            continue
    return os.path.normpath(anchor)  # pragma: no cover - defensive


def _child_env() -> dict[str, str]:
    """Environment for the child processes: the caller's venv must not leak in via PYTHONPATH."""
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    env.pop("PYTHONSTARTUP", None)
    return env


# ── interpreter discovery ──────────────────────────────────────────────────────
def _from_install_env() -> str | None:
    """Ask the package's own install_env module (may not be in an older installed wheel).

    Imported with ``from mcp_server.install_env import venv_python`` so the module is resolved
    through ``sys.modules`` (patchable in tests) rather than through an attribute on the package.
    """
    try:
        from mcp_server.install_env import venv_python
    except Exception:
        return None
    try:
        value = venv_python() if callable(venv_python) else venv_python
    except Exception:
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _pipx_env_value() -> str | None:
    """``pipx environment --value PIPX_LOCAL_VENVS`` — correct on Windows, macOS and Linux."""
    try:
        proc = subprocess.run(
            ["pipx", "environment", "--value", "PIPX_LOCAL_VENVS"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return None
    base = (proc.stdout or "").strip()
    if proc.returncode != 0 or not base:
        return None
    leaf = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return os.path.join(base, DIST, *leaf)


def candidate_pythons() -> list[tuple[str, str]]:
    """(path, why) for every place the server's venv can live, most reliable first."""
    cands: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(path: str | None, why: str) -> None:
        if not path:
            return
        norm = os.path.normpath(path)
        key = norm.lower() if os.name == "nt" else norm
        if key in seen:
            return
        seen.add(key)
        cands.append((norm, why))

    leaf = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")

    add(_from_install_env(), "mcp_server.install_env.venv_python")
    from_pipx = _pipx_env_value()
    if from_pipx:
        add(from_pipx, "pipx environment PIPX_LOCAL_VENVS")
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        add(os.path.join(local, "pipx", "pipx", "venvs", DIST, *leaf), "LOCALAPPDATA pipx default")
    else:
        add(os.path.expanduser(f"~/.local/pipx/venvs/{DIST}/bin/python"), "pipx default")
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        add(os.path.join(venv, *leaf), "active VIRTUAL_ENV")
    add(os.path.expanduser(f"~/.local/share/uv/tools/{DIST}/bin/python"), "uv tool")
    add(os.path.expanduser(f"~/Library/Application Support/uv/tools/{DIST}/bin/python"), "uv tool (macOS)")
    add(os.path.expanduser(f"~/.local/pipx/venvs/{DIST}/bin/python"), "posix pipx path")
    return cands


def resolve_python(explicit: str | None = None) -> tuple[str | None, str]:
    """(interpreter, where it came from). The interpreter is ``None`` when none can be found.

    The RUNNING interpreter wins when it is itself a psamvault-mcp install — asking "what version am
    I?" from inside a sandbox or a venv must answer about *that* install, not about the pipx one on the
    same machine. Only when the running environment has no psamvault-mcp do we go looking for the pipx
    venv, and the returned label always says which of the two answered.
    """
    if explicit:
        if os.path.isfile(explicit):
            return os.path.normpath(explicit), "--python"
        return None, f"--python {explicit} does not exist"
    running = _installed_version_of(sys.executable)
    if running:
        return sys.executable, f"this interpreter (has psamvault-mcp {running} installed)"
    tried: list[str] = []
    for path, why in candidate_pythons():
        if os.path.isfile(path):
            return path, why
        tried.append(path)
    detail = "; ".join(tried) if tried else "no candidate locations"
    return None, f"no psamvault-mcp venv python found (tried: {detail})"


def _installed_version_of(python: str) -> str | None:
    """The psamvault-mcp version installed in ``python``'s environment, or None. Never raises."""
    code = (
        "import importlib.metadata as m\n"
        "try: print(m.version('psamvault-mcp'))\n"
        "except Exception: pass\n"
    )
    try:
        proc = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=30, env=_child_env())
    except Exception:
        return None
    return (proc.stdout or "").strip() or None


# ── probes ─────────────────────────────────────────────────────────────────────
def _run_child(argv: list[str], cwd: str, timeout: float):
    """subprocess.run seam (tests monkeypatch this)."""
    return subprocess.run(
        argv, cwd=cwd, env=_child_env(), capture_output=True, text=True, timeout=timeout,
    )


def _spawn_child(argv: list[str], cwd: str):
    """subprocess.Popen seam (tests monkeypatch this)."""
    return subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=cwd,
        env=_child_env(),
    )


def _error_line(text: str | None, limit: int = 300) -> str:
    """One readable line from a child's stderr: the last real line, whitespace collapsed.

    A failed interpreter prints a whole traceback; the useful part is its final line
    (ModuleNotFoundError, StopIteration, ...), and a multi-line blob would wreck the PROBLEMS list.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    joined = " ".join((lines[-1] if lines else (text or "").strip()).split())
    return joined[:limit] or "the interpreter exited non-zero"


def probe_installed(python: str, timeout: float | None = None) -> tuple[str | None, str | None]:
    """The version in the interpreter's site-packages, read from a neutral cwd. -> (version, error)."""
    limit = INSTALLED_TIMEOUT_S if timeout is None else timeout
    try:
        proc = _run_child([python, "-c", _METADATA_CODE], neutral_cwd(), limit)
    except subprocess.TimeoutExpired:
        return None, f"reading the installed version timed out after {limit:g}s"
    except OSError as exc:
        return None, f"could not run {python}: {exc}"
    if proc.returncode != 0:
        return None, _error_line(proc.stderr or proc.stdout)
    try:
        payload = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except Exception:
        return None, f"unreadable metadata output: {(proc.stdout or '').strip()[:200]!r}"
    version = payload.get("version")
    if not version:
        return None, "importlib.metadata reported no version (is psamvault-mcp installed here?)"
    return str(version), None


def _read_lines(stream, sink) -> None:
    """Reader thread body: turn newline-delimited JSON-RPC frames into queue items."""
    try:
        for line in stream:  # type: ignore[union-attr]
            line = line.strip()
            if not line:
                continue
            try:
                sink.put(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception:  # pragma: no cover - stream closed under us when the child is killed
        return


def _read_stderr(stream, sink: list[str]) -> None:
    """Reader thread body for stderr: keep the last few lines so failures can explain themselves."""
    try:
        for line in stream:
            line = line.strip()
            if line:
                sink.append(line)
    except Exception:  # pragma: no cover - stream closed under us when the child is killed
        return


def probe_served(python: str, timeout: float | None = None) -> dict:
    """Spawn a fresh MCP server and ask it for its version, tool list and compatibility block.

    Returns a report dict; ``error`` is set (and ``version`` absent) when the server never answered.
    """
    limit = CHILD_TIMEOUT_S if timeout is None else timeout
    cwd = neutral_cwd()
    result: dict = {
        "python": python,
        "child_cwd": cwd,
        "tool_count": None,
        "tools": [],
        "compatibility": {},
        "stderr": "",
        "error": None,
    }
    try:
        proc = _spawn_child([python, "-c", SERVER_SPAWN_CODE], cwd)
    except OSError as exc:
        result["error"] = f"could not start the server: {exc}"
        return result

    frames: queue.Queue = queue.Queue()
    stderr_lines: list[str] = []
    threading.Thread(target=_read_lines, args=(proc.stdout, frames), daemon=True).start()
    threading.Thread(target=_read_stderr, args=(proc.stderr, stderr_lines), daemon=True).start()
    io_error: list[str] = []

    def send(message: dict) -> None:
        try:
            proc.stdin.write(json.dumps(message) + "\n")  # type: ignore[union-attr]
            proc.stdin.flush()  # type: ignore[union-attr]
        except Exception as exc:
            io_error.append(f"{type(exc).__name__}: {exc}")

    def call(message: dict) -> dict | None:
        """Write one frame and wait for its id, on a real deadline."""
        send(message)
        deadline = time.monotonic() + limit
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                frame = frames.get(timeout=min(POLL_S, remaining))
            except queue.Empty:
                continue
            if isinstance(frame, dict) and frame.get("id") == message.get("id"):
                return frame

    def dead_reason(what: str) -> str:
        code = None
        try:
            code = proc.poll()
        except Exception:  # pragma: no cover - defensive
            pass
        tail = " | ".join(line.strip() for line in stderr_lines[-4:] if line.strip())
        if io_error:
            return f"the server closed its input while waiting for {what}: {io_error[0]}"
        if code is not None:
            return f"the server exited (code {code}) before answering {what}" + (f": {tail}" if tail else "")
        return f"{what} timed out after {limit:g}s" + (f" (server stderr: {tail})" if tail else "")

    try:
        if not call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                "clientInfo": dict(CLIENT_INFO)}}):
            result["error"] = dead_reason("initialize")
            return result
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        listed = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        if listed is None:
            result["error"] = dead_reason("tools/list")
            return result
        tools = sorted(t.get("name", "") for t in listed.get("result", {}).get("tools", []))
        result["tool_count"] = len(tools)
        result["tools"] = tools

        answer = call({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "get_version", "arguments": {}}})
        if answer is None:
            result["error"] = dead_reason("tools/call get_version")
            return result
        body = "".join(part.get("text", "") for part in answer.get("result", {}).get("content", []))
        try:
            payload = json.loads(body)
        except Exception:
            result["error"] = f"get_version returned unreadable content: {body[:200]!r}"
            return result
        version = payload.get("version")
        if not version:
            result["error"] = "get_version returned no version field"
            return result
        result["version"] = str(version)
        result["compatibility"] = payload.get("compatibility") or {}
        return result
    finally:
        try:
            proc.kill()
        except Exception:  # pragma: no cover - defensive
            pass
        try:
            if not io_error:
                proc.stdin.close()  # type: ignore[union-attr]
        except Exception:  # pragma: no cover - defensive
            pass
        result["stderr"] = " | ".join(line.strip() for line in stderr_lines[-4:] if line.strip())


# ── optional published-version lookup ──────────────────────────────────────────
def _load_latest_published():
    """Import seam: tests patch this so they never touch PyPI."""
    from mcp_server.version_check import latest_published  # type: ignore[attr-defined]

    return latest_published


def published_version() -> tuple[str | None, str | None]:
    """Newest version on PyPI, or (None, why) — never a failure and never a network hang."""
    try:
        lookup = _load_latest_published()
    except ImportError:
        return None, "unknown (this build has no latest_published lookup)"
    except Exception as exc:
        return None, f"unknown ({type(exc).__name__}: {exc})"
    if not callable(lookup):
        return (str(lookup) if lookup else None), (None if lookup else "unknown (no version returned)")
    try:
        value = lookup()
    except Exception as exc:
        return None, f"unknown ({type(exc).__name__}: {exc})"
    return (str(value) if value else None), (None if value else "unknown (the lookup returned nothing)")


# ── report ─────────────────────────────────────────────────────────────────────
def build_report(python: str | None, python_source: str, installed: str | None,
                 installed_error: str | None, served: dict, published: str | None,
                 published_note: str | None, expect: str | None) -> dict:
    compat = served.get("compatibility") or {}
    served_version = served.get("version")
    report: dict = {
        "python": python,
        "python_source": python_source,
        "neutral_cwd": neutral_cwd(),
        "installed_version": installed,
        "installed_error": installed_error,
        "served_version": served_version,
        "served_error": served.get("error"),
        "served_stderr": served.get("stderr") or None,
        "tool_count": served.get("tool_count"),
        "tools": served.get("tools") or [],
        "compatibility": compat,
        "skill_floor": compat.get("newest_release_skill_version"),
        "paired_skill_version": compat.get("paired_skill_version"),
        "tool_surface_matches_newest": compat.get("tool_surface_matches_newest"),
        "breaking_pending": compat.get("breaking_pending"),
        "expected_tool_count": compat.get("expected_tool_count"),
        "published_version": published,
        "published_note": published_note,
        "expected": expect,
        "ok": True,
        "failures": [],
    }

    if installed_error:
        report["failures"].append(f"could not read the installed version: {installed_error}")
    if served.get("error"):
        report["failures"].append(f"could not read the served version: {served['error']}")
    elif not served_version:
        report["failures"].append("could not read the served version: the server reported no version")
    if installed and served_version and installed != served_version:
        report["failures"].append(
            f"installed {installed} but a fresh process serves {served_version} "
            "- reinstall, or the venv is inconsistent"
        )
    if expect and served_version != expect:
        report["failures"].append(f"served {served_version or '?'}, expected {expect}")
    report["ok"] = not report["failures"]
    return report


def render_text(report: dict) -> str:
    lines = ["psamvault-mcp selfcheck"]
    source = f"  ({report['python_source']})" if report.get("python_source") else ""
    lines.append(f"  python              : {report['python']}{source}")
    lines.append(f"  read from cwd       : {report['neutral_cwd']}  (never the checkout)")
    lines.append(f"  installed           : {report['installed_version'] or '?'}")
    count = f"  ({report['tool_count']} tools)" if report.get("tool_count") else ""
    lines.append(f"  a NEW session serves: {report['served_version'] or '?'}{count}")
    if report.get("published_version"):
        lines.append(f"  newest published    : {report['published_version']}")
    elif report.get("published_note"):
        lines.append(f"  newest published    : {report['published_note']}")
    if report.get("skill_floor") or report.get("paired_skill_version"):
        lines.append(
            f"  skill floor         : {report.get('skill_floor')}  "
            f"(paired skill {report.get('paired_skill_version')})"
        )
        lines.append(
            f"  tools match contract: {report.get('tool_surface_matches_newest')}"
            f" | breaking pending: {report.get('breaking_pending')}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="psamvault-mcp selfcheck",
        description="Report the INSTALLED vs SERVED psamvault-mcp version (0 ok, 1 mismatch, 2 no interpreter)",
    )
    parser.add_argument("--python", default=None, help="interpreter of the psamvault-mcp venv to probe")
    parser.add_argument("--expect", default=None, help="expected version; exit 1 if the SERVED version differs")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--timeout", type=float, default=None,
                        help=f"seconds to wait for the spawned server (default {CHILD_TIMEOUT_S:g})")
    parser.add_argument("--no-network", action="store_true",
                        help="skip the published-version lookup entirely (no PyPI call)")
    args = parser.parse_args(argv)
    timeout = CHILD_TIMEOUT_S if args.timeout is None else args.timeout

    python, python_source = resolve_python(args.python)
    if not python:
        message = f"error: {python_source}\n       pass --python <venv>/Scripts/python.exe"
        if args.json:
            print(json.dumps({"ok": False, "python": None, "python_source": python_source,
                              "error": python_source, "exit_code": EXIT_NO_INTERPRETER}, indent=2))
        else:
            print(message, file=sys.stderr)
        return EXIT_NO_INTERPRETER

    installed, installed_error = probe_installed(python)
    served = probe_served(python, timeout)
    published, published_note = (None, "not checked (--no-network)") if args.no_network else published_version()

    report = build_report(python, python_source, installed, installed_error, served,
                          published, published_note, args.expect)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_text(report))
        if report["failures"]:
            print("\nPROBLEMS:")
            for failure in report["failures"]:
                print(f"  - {failure}")
        print(f"\nresult: {'OK' if report['ok'] else 'FAIL'}")
    return EXIT_OK if report["ok"] else EXIT_PROBLEM


if __name__ == "__main__":
    raise SystemExit(main())
