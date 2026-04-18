import sys

def request_consent(
    site_name: str,
    target_url: str,
    inject_as: str,
    agent_description: str = "An AI agent"
) -> bool:
    """
    Display a consent prompt in the user's terminal and wait for approval
    before any credential is used.
 
    This is the critical security gate — no credential access proceeds
    without explicit user approval. The prompt is shown in the terminal
    where the MCP server is running, not in the agent's interface.
 
    Args:
        site_name:         The vault site whose credential will be used.
        target_url:        The URL the request will be sent to.
        inject_as:         How the credential will be injected.
        agent_description: Description of the requesting agent.
 
    Returns:
        True if the user approved, False if denied
    """
    print("\n" + "=" * 60, file=sys.stderr)
    print("  psamvault — CREDENTIAL ACCESS REQUEST", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  Agent    : {agent_description}", file=sys.stderr)
    print(f"  Site     : {site_name}", file=sys.stderr)
    print(f"  Target   : {target_url}", file=sys.stderr)
    print(f"  Auth mode: {inject_as}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("  The agent wants to use your stored credential.", file=sys.stderr)
    print("  The credential will NOT be shown to the agent.", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    
    try:
        response = input("  Allow? [y/N]: ").strip().lower()
        approved = response in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        approved = False
    
    if approved:
        print(f"  Approved — using credential for {site_name}.\n", file=sys.stderr)
    else:
        print(" Denied - credential access blocked.\n", file=sys.stderr)
    
    
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