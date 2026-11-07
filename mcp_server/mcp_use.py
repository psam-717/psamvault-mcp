"""Which MCP tools may use a stored secret.

This is psamvault-mcp's copy of psamvault-cli ``mcp_use``. The two packages do
not import each other. The on-disk contract is ``~/.psamvault/policy.json``
and one JSONL row in ``~/.psamvault/audit.jsonl``. Keep ``decide``,
``should_ask`` and ``finalize`` identical to the CLI.

``mcp_use`` (top-level string):

* ``audit`` (default when the key is absent) — grace. An unlisted tool is
  allowed and the audit reason is ``not-listed``. Existing installs keep
  working when the MCP starts writing rows.
* ``enforce`` — a tool not in ``entries.<name>.allow_tools`` is refused.
  A human turns this on with ``psamvault policy mcp-use enforce``.
* ``off`` — the tool list and ``ask_each_time`` are not consulted. The use
  is still audited, reason ``mcp-use-off``.

An unrecognised ``mcp_use`` is ``audit`` plus a warning. Enforcing on a typo
would refuse every tool.

``entries.<name>.allow_tools`` is a list of MCP tool names. The grant command
writes only tools that decrypt a secret. Loading keeps any token that is a
tool name (``^[a-z][a-z0-9_]*$`` after casefold) so a hand-edited future tool
still matches, and warns when the name is not one of :data:`TOOLS`. A token
that is not a tool name is ignored. The file is not rewritten.

``entries.<name>.ask_each_time`` is a boolean, default false when absent.
Only JSON ``true`` counts. The CLI stores it. The MCP asks, via elicitation,
only when ``mcp_use`` is ``enforce``, the pre-ask decision is allow, and the
flag is true. ``audit`` and ``off`` never ask. A no or a missing answer
becomes deny ``ask-declined`` or ``ask-unanswered``. A denial is not asked
back into an allow.

A missing ``mcp_use`` key is still ``audit`` in this release.
:data:`ABSENT_DEFAULT_NOTICE` says a later release will treat that absence as
``enforce``. An explicit ``"audit"`` does not carry the notice.

Discovery tools (``list_api_keys``, ``get_version``, and the rest of
:data:`DISCOVERY`) do not decrypt a secret. They are not gated by this list.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MODE_AUDIT = "audit"
MODE_ENFORCE = "enforce"
MODE_OFF = "off"
MODES = (MODE_AUDIT, MODE_ENFORCE, MODE_OFF)
DEFAULT_MODE = MODE_AUDIT

# Tools that decrypt a stored secret. The grant command accepts only these.
# Synced with psamvault-mcp TOOL_DEFINITIONS. A new consuming tool needs both
# sides updated; a hand-edited future name still matches on load.
TOOLS = (
    "use_credential",
    "run_with_credential",
    "browser_login",
    "export_key_to_mcp_config",
    "export_key_to_env_file",
    "verify_api_key",
)

# Registered MCP tools that do not decrypt a secret. Granting one is refused
# so the list does not pretend to gate a lookup.
DISCOVERY = frozenset({
    "get_version",
    "search_vault_tools",
    "list_vault_sites",
    "check_credential_exists",
    "get_username_for_site",
    "list_api_keys",
    "scan_and_protect",
})

REASON_LISTED = "listed"
REASON_NOT_LISTED = "not-listed"
REASON_OFF = "mcp-use-off"
REASON_ASK_DECLINED = "ask-declined"
REASON_ASK_UNANSWERED = "ask-unanswered"
REASONS = (
    REASON_LISTED,
    REASON_NOT_LISTED,
    REASON_OFF,
    REASON_ASK_DECLINED,
    REASON_ASK_UNANSWERED,
)

ALLOW = "allow"
DENY = "deny"

# Printed by `policy tools` and `policy mcp-use` when the key is absent, and
# carried on McpPlan.notice so the MCP can show the same sentence. Not a
# policy.warnings entry: other commands must not repeat it.
ABSENT_DEFAULT_NOTICE = (
    "mcp_use is not set, so it defaults to audit; "
    "a future release will default to enforce; "
    "run `psamvault policy mcp-use audit` to keep audit explicitly, "
    "or `enforce` to opt in now"
)

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ToolVerdict:
    """A normalised tool name, and why a grant must not write it."""

    normalized: str | None
    problem: str | None


@dataclass(frozen=True)
class McpPlan:
    """What the MCP should do before elicitation.

    ``ask`` is true only under ``enforce``, when the use would be allowed and
    the entry's ``ask_each_time`` is true. ``audit`` and ``off`` leave it
    false. ``notice`` is :data:`ABSENT_DEFAULT_NOTICE` when ``mcp_use`` was
    not set in the file, and None when the mode was explicit. Call
    :func:`finalize` with the human's answer. Surface ``notice`` when showing
    the mode, not on every tool result.
    """

    decision: str
    reason: str
    ask: bool
    mcp_use: str
    notice: str | None = None


def normalize_tool(value: str) -> str | None:
    """Lower-case tool name, or None when it is not one identifier."""
    text = (value or "").strip().lower()
    if _TOOL_NAME.match(text):
        return text
    return None


def judge(value: str, *, strict: bool) -> ToolVerdict:
    """Classify one allow_tools value. Never raises.

    Grant (``strict=True``) accepts only :data:`TOOLS`. Load accepts any
    tool-shaped token and reports the ones a human did not mean to grant.
    """
    raw = value or ""
    if not raw.strip():
        return ToolVerdict(None, "A tool name is required.")
    normalized = normalize_tool(raw)
    if not normalized:
        return ToolVerdict(
            None,
            f"{raw!r} is not an MCP tool that uses a stored secret.",
        )
    if normalized in TOOLS:
        return ToolVerdict(normalized, None)
    if normalized in DISCOVERY:
        return ToolVerdict(
            normalized,
            (
                f"{normalized} does not use a stored secret. "
                "allow_tools is only for tools that decrypt one."
            ),
        )
    return ToolVerdict(
        normalized,
        (
            f"{normalized} is not an MCP tool that uses a stored secret. "
            "It is kept and will match only a tool with that exact name."
            if not strict
            else f"{normalized} is not an MCP tool that uses a stored secret."
        ),
    )


def decide(mode: str, tools: list[str], tool: str) -> tuple[str, str]:
    """Return ``(decision, reason)`` before any in-chat question.

    ``off`` does not look at ``tools``. ``enforce`` refuses an unlisted tool.
    ``audit`` allows it and says so in the reason. Any other mode is treated
    as ``audit`` — the loader has already warned and substituted that.
    Asking is :func:`should_ask`, not this pair: ``audit`` never asks.
    """
    if mode == MODE_OFF:
        return ALLOW, REASON_OFF
    normalized = normalize_tool(tool)
    listed = bool(normalized) and normalized in tools
    if mode == MODE_ENFORCE:
        return (ALLOW, REASON_LISTED) if listed else (DENY, REASON_NOT_LISTED)
    return (ALLOW, REASON_LISTED) if listed else (ALLOW, REASON_NOT_LISTED)


def should_ask(mode: str, decision: str, *, ask_each_time: bool) -> bool:
    """True only for an allowed use under ``enforce`` when the entry asks.

    ``audit`` records the use and does not ask, even when the flag is true.
    ``off`` does not ask. A denial is not asked back into an allow.
    """
    return mode == MODE_ENFORCE and decision == ALLOW and bool(ask_each_time)


def absent_default_notice(*, implicit: bool) -> str | None:
    """The planned-flip sentence, or None when ``mcp_use`` was set explicitly."""
    if not implicit:
        return None
    return ABSENT_DEFAULT_NOTICE


def finalize(decision: str, reason: str, *, ask: bool, accepted: bool | None) -> tuple[str, str]:
    """Apply the in-chat answer. A refusal stays a refusal.

    ``accepted`` is None when the prompt did not complete. ``ask`` false
    leaves the pair unchanged, which is how ``mcp_use: off`` skips the flag.
    """
    if decision != ALLOW or not ask:
        return decision, reason
    if accepted is True:
        return decision, reason
    if accepted is False:
        return DENY, REASON_ASK_DECLINED
    return DENY, REASON_ASK_UNANSWERED
