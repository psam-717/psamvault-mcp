import sys


class ConsentGUIUnavailableError(RuntimeError):
    """Raised when no GUI is available to display the consent dialog."""
    pass


def _sanitize_for_display(value: str, max_length: int = 200) -> str:
    """
    Replace control characters (including newlines) with spaces and truncate.

    Prevents consent dialog UI injection: an adversarial agent could pass a
    site_name or target_url containing newlines that rearrange the dialog text
    to trick the user into approving a request they wouldn't otherwise approve.
    """
    return "".join(" " if ord(ch) < 32 else ch for ch in value)[:max_length]


def request_consent(
    site_name: str,
    target_url: str,
    inject_as: str,
    agent_description: str = "An AI agent"
) -> bool:
    """
    Show a native GUI dialog asking the user to approve credential access.

    Works on Windows, macOS, and Linux (requires a desktop environment).
    The dialog blocks until the user clicks Yes or No.

    Args:
        site_name:         The vault site whose credential will be used.
        target_url:        The URL the request will be sent to.
        inject_as:         How the credential will be injected.
        agent_description: Description of the requesting agent.

    Returns:
        True if the user approved, False if denied.
    """
    # Sanitize all user-controlled inputs before inserting into the dialog.
    # Without this, an adversarial agent could pass a site_name containing
    # newlines to rearrange the dialog text and deceive the user.
    _site     = _sanitize_for_display(site_name)
    _target   = _sanitize_for_display(target_url)
    _mode     = _sanitize_for_display(inject_as)
    _agent    = _sanitize_for_display(agent_description)

    message = (
        f"{_agent} wants to use your stored credential.\n\n"
        f"  Site   : {_site}\n"
        f"  Target : {_target}\n"
        f"  Mode   : {_mode}\n\n"
        f"The credential will NOT be shown to the agent.\n\n"
        f"Allow access?"
    )

    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.lift()
        root.attributes("-topmost", True)
        approved = messagebox.askyesno(
            title="psamvault — Credential Access Request",
            message=message,
            icon=messagebox.WARNING,
        )
        root.destroy()
    except Exception as e:
        # Fallback: tkinter unavailable (headless server, missing python3-tk, etc.)
        print("\n" + "=" * 60, file=sys.stderr)
        print("  psamvault — CREDENTIAL ACCESS REQUEST", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"  {message.replace(chr(10), chr(10) + '  ')}", file=sys.stderr)
        print(f"  (GUI dialog unavailable: {e})", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print("  Cannot show consent dialog — no GUI available.", file=sys.stderr)
        raise ConsentGUIUnavailableError(
            f"No GUI is available to display the credential consent dialog "
            f"(tkinter is missing or this is a headless environment). "
            f"Detail: {e}"
        ) from e

    if approved:
        print(f"  psamvault: approved — credential for '{site_name}' will be used.", file=sys.stderr)
    else:
        print(f"  psamvault: denied — credential for '{site_name}' was blocked.", file=sys.stderr)

    return approved


def notify_completion(site_name: str, status_code: int, target_url: str) -> None:
    """
    Print a notification after a proxy request completes so the user
    can see what happened without reading agent output.
    """
    print(
        f"  psamvault: credential for '{site_name}' used → "
        f"{target_url} responded {status_code}",
        file=sys.stderr,
    )
