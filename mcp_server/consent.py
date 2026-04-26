import sys


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
    message = (
        f"{agent_description} wants to use your stored credential.\n\n"
        f"  Site   : {site_name}\n"
        f"  Target : {target_url}\n"
        f"  Mode   : {inject_as}\n\n"
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
        approved = False
        print("  Auto-denied — no GUI available for consent.", file=sys.stderr)

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
