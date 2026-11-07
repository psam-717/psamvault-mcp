"""Decide, ask, and audit before a gated tool reads the VEK.

Order, for one call of a tool that decrypts a secret:

1. Read ``policy.json`` and compute the plan (no keychain, no network).
2. When the plan says to ask, elicit. Nothing is sent when the client has
   no form elicitation or there is no session. A sent prompt that errors,
   times out, or is cancelled is still unanswered, and the audit ``ask``
   bit records that the request was sent.
3. Append one audit row. A write failure is ignored.
4. Return a refusal, or ``None`` so the tool may continue.

The destination check stays inside the tool, after this returns ``None`` and
after ``get_vek()``. It needs the decrypted service identity. A tool that is
not allowed never reaches it and never reads the VEK.
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from typing import Any

from mcp_server import audit, mcp_use, policy
from mcp_server.log import get_logger

logger = get_logger()

# The SDK's elicit_form does not take a timeout, and the stdio session's read
# timeout is unset, so a host that never answers would wait forever. Bound it.
ELICIT_TIMEOUT_SECONDS = 300

_ASK_SCHEMA = {
    "type": "object",
    "properties": {
        "allow": {
            "type": "boolean",
            "title": "Allow",
            "description": "Yes allows this tool to use the stored secret once. No refuses it.",
        }
    },
    "required": ["allow"],
}


def form_supported(session: Any) -> bool:
    """True when this client can answer a yes/no form.

    No session and no elicitation capability are unsupported. A client that
    advertises only URL elicitation cannot answer the form. An empty
    elicitation capability is the older form-mode advertisement.
    """
    if session is None:
        return False
    params = getattr(session, "client_params", None)
    if params is None:
        return False
    capabilities = getattr(params, "capabilities", None)
    elicitation = getattr(capabilities, "elicitation", None)
    if elicitation is None:
        return False
    form = getattr(elicitation, "form", None)
    url = getattr(elicitation, "url", None)
    if form is None and url is not None:
        return False
    return True


def accepted_from_action(action: str | None, content: Any) -> bool | None:
    """Map an elicitation result onto finalize's ``accepted`` flag.

    ``True`` keeps the allow. ``False`` is a no (``ask-declined``). ``None``
    is a prompt that did not complete (``ask-unanswered``).
    """
    if action == "decline":
        return False
    if action != "accept":
        return None
    if not isinstance(content, dict):
        return None
    allow = content.get("allow")
    if allow is True:
        return True
    if allow is False:
        return False
    return None


def _current_session() -> Any:
    try:
        from mcp_server.main import server
    except Exception:
        return None
    try:
        return server.request_context.session
    except Exception:
        return None


@dataclass(frozen=True)
class ElicitOutcome:
    """Whether ``elicitation/create`` was sent, and the human's answer.

    ``sent`` is false when there is no session or the client cannot take a
    form, so nothing is written to the wire. It is true once ``elicit_form``
    (or ``elicit``) is invoked: that method sends the request and then waits.
    A decline, cancel, empty accept, timeout, or error after that send stays
    ``sent`` true. ``accepted`` is true, false, or None (no usable answer).
    """

    sent: bool
    accepted: bool | None


async def elicit(tool: str, entry: str) -> ElicitOutcome:
    """Ask the human.

    Uses ``session.elicit_form`` (mcp 1.30). ``session.elicit`` is the same
    call on a build that has not renamed it. The dependency floor is
    ``mcp>=1.30.0,<2``. A missing session or a client without form elicitation
    returns ``sent=False`` and does not call the SDK.
    """
    session = _current_session()
    if not form_supported(session):
        return ElicitOutcome(False, None)
    method = getattr(session, "elicit_form", None) or getattr(session, "elicit", None)
    if method is None:
        return ElicitOutcome(False, None)
    try:
        result = await asyncio.wait_for(
            method(
                message=f"Allow {tool} to use {entry}?",
                requestedSchema=_ASK_SCHEMA,
            ),
            timeout=ELICIT_TIMEOUT_SECONDS,
        )
    except Exception:
        return ElicitOutcome(True, None)
    return ElicitOutcome(
        True,
        accepted_from_action(getattr(result, "action", None), getattr(result, "content", None)),
    )


def _coerce_outcome(result: ElicitOutcome | bool | None) -> ElicitOutcome:
    """Normalise ``elicit`` and a test double that still returns yes/no/None."""
    if isinstance(result, ElicitOutcome):
        return result
    if result is True or result is False:
        return ElicitOutcome(True, result)
    return ElicitOutcome(True, None)


def _refusal(tool: str, entry: str | None, reason: str, mode: str) -> dict:
    shown = entry if entry else ""
    if reason == mcp_use.REASON_NOT_LISTED:
        command = f"psamvault policy allow-tool {shlex.quote(shown)} {shlex.quote(tool)}"
        text = (
            f"STOP. {tool} is not allowed to use {shown!r}. "
            f"mcp_use is {mode}. "
            f"A human allows it by running this in a terminal: {command}. "
            "Do not retry this call. Do not edit ~/.psamvault/policy.json. "
            "Do not run psamvault yourself. Do not use another tool to obtain the secret."
        )
    elif reason == mcp_use.REASON_ASK_DECLINED:
        text = (
            f"STOP. The human said no to {tool} using {shown!r}. "
            "Do not retry this call. Do not ask again. "
            "Do not use another tool to obtain the secret."
        )
    else:
        text = (
            f"STOP. {tool} needed a yes before using {shown!r}, and no answer was received "
            "(the host has no elicitation form, or the prompt failed, timed out, or was cancelled). "
            "Do not retry this call. Do not use another tool to obtain the secret."
        )
    return {
        "error": text,
        "decision": mcp_use.DENY,
        "reason": reason,
        "mcp_use": mode,
    }


async def before_use(tool: str, entry: str | None) -> dict | None:
    """Audit the decision and return a refusal, or ``None`` to continue.

    ``None`` means the tool may read the VEK. A refusal is returned instead
    of that read. This never raises.
    """
    try:
        loaded = policy.load()
        plan = policy.mcp_plan(loaded, entry or "", tool)
        accepted: bool | None = None
        sent = False
        if plan.ask:
            try:
                outcome = _coerce_outcome(await elicit(tool, entry or ""))
            except Exception:
                # The ask was entered and then failed. Count it as sent so a
                # timeout or client error is not recorded like "never asked".
                outcome = ElicitOutcome(True, None)
            sent = outcome.sent
            accepted = outcome.accepted
        decision, reason = mcp_use.finalize(
            plan.decision,
            plan.reason,
            ask=plan.ask,
            accepted=accepted,
        )
        mode = plan.mcp_use
        asked = sent
    except Exception as exc:
        # A bug in the checker must not decrypt. not-listed is the closed reason
        # a human can answer with allow-tool; the real mode could not be read.
        logger.warning("mcp-use decision failed closed: %s", type(exc).__name__)
        decision, reason, mode, asked = (
            mcp_use.DENY,
            mcp_use.REASON_NOT_LISTED,
            mcp_use.MODE_ENFORCE,
            False,
        )

    try:
        audit.record_mcp_use(
            tool=tool,
            entry=entry if entry else None,
            decision=decision,
            reason=reason,
            mcp_use=mode,
            ask=asked,
        )
    except Exception as exc:
        logger.warning("mcp-use audit was not written: %s", type(exc).__name__)

    if decision != mcp_use.ALLOW:
        return _refusal(tool, entry, reason, mode)
    return None
