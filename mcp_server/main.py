"""
psamvault MCP server entry point.
 
This file registers all psamvault tools with the MCP protocol and starts
the stdio server that Claude Desktop / Claude Code / other MCP-compatible
agents connect to.
 
Usage:
  # Install
  pipx install psamvault-mcp
 
  # Must be logged in via the CLI first
  psamvault login
  
Token-efficiency changes (Anthropic Code Execution with MCP pattern):
  - search_vault_tools added as the single always-loaded discovery tool
  - All other tool descriptions trimmed in the instructions string
  - use_credential accepts optional `fields` to filter response payload
  - browser_login returns a slim summary instead of the full step list
"""

import asyncio
import importlib.metadata
import json
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from mcp_server.session import is_logged_in, load_config

load_config()

from mcp_server import tools

# Initialize MCP server
server = Server(
    "psamvault",
    instructions=(
        "You are connected to psamvault, a credential vault. "
        "These are the strict rules you MUST follow — they are security requirements, not suggestions:\n\n"

        "1. NEVER run shell commands to retrieve credentials. "
        "Do not call 'psamvault get', 'psamvault show', or any psamvault CLI command. "
        "Do not read any credential file. "
        "The only permitted way to access credentials is through the MCP tools listed below.\n\n"

        "2. To log a user into a website, you MUST call browser_login(site_name=...). "
        "This tool opens a real browser, fills in the username and password silently inside "
        "that browser process, and submits the form. "
        "The credential values are NEVER returned to you — they go directly from the vault into "
        "the browser fields. Do not attempt any other login method.\n\n"

        "3. To make an authenticated API call, use use_credential(). "
        "Do not ask the user to provide their API key or password — psamvault will inject it.\n\n"
        "Pass the `fields` parameter to return only the response keys you need — "
        "this reduces token usage significantly for large API responses.\n\n"

        "4. Every tool that accesses a credential will show the user a consent dialog first. "
        "Do not attempt to bypass or pre-approve this step.\n\n"

        "5. ALWAYS call search_vault_tools first to find the right tool for your task. "
        "If you are unsure which sites are stored, call list_vault_sites(). "
        "If you are unsure whether a credential exists, call check_credential_exists(site_name) first.\n\n"

         "Tool summary:\n"
        "- search_vault_tools       → discover which tool to use (call this first)\n"
        "- list_vault_sites         → list stored sites (no passwords)\n"
        "- check_credential_exists  → verify a credential exists for a site\n"
        "- browser_login            → open a browser and log into a website silently\n"
        "- use_credential           → make an authenticated HTTP/API request\n"
        "- get_username_for_site    → get just the username (not password) for a site\n"
    ),
)

# ── Version ────────────────────────────────────────────────────────────────────
try:
    _VERSION = importlib.metadata.version("psamvault-mcp")
except importlib.metadata.PackageNotFoundError:
    _VERSION = "unknown"

# ── Tool registry used by search_vault_tools ──────────────────────────────────
# Each entry is (one-line description, key param hints).
# Kept here so it stays in sync with TOOL_DEFINITIONS below.
_TOOL_REGISTRY: dict[str, str] = {
    "get_version": (
        "Return the installed psamvault-mcp version. "
        "Use this to verify the version you have."
    ),
    "search_vault_tools": (
        "Discover available psamvault tools. Call this FIRST to find the right tool. "
        "Pass a keyword like 'login', 'api', 'check', or '' for all tools."
    ),
    "list_vault_sites": (
        "List all site names in the vault (no passwords). "
        "Use before use_credential to see what's available."
    ),
    "check_credential_exists": (
        "Check whether a credential exists for a site. "
        "Params: site_name. Returns exists + username_hint."
    ),
    "use_credential": (
        "Make an authenticated HTTP request using a stored credential. "
        "Params: site_name, target_url, method, inject_as, fields (optional — "
        "pass field names to trim the response and save tokens, e.g. [\"login\", \"id\"])."
    ),
    "get_username_for_site": (
        "Get the stored username only (not password) for a site. "
        "Params: site_name. Requires user consent."
    ),
    "browser_login": (
        "Open a real Chromium browser and log into a website silently. "
        "Params: site_name (required), login_url, username_selector, "
        "password_selector, submit_selector, timeout_ms (all optional)."
    ),
}


TOOL_DEFINITIONS = [
    # ── Version tool — no session needed ─────────────────────────────────────
    Tool(
        name="get_version",
        description="Return the installed psamvault-mcp version. No session or login required.",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": []
        }
    ),

    # ── Discovery tool — the only tool agents need to know about upfront ──────
    Tool(
        name="search_vault_tools",
        description=(
            "Discover available psamvault tools. "
            "Call this FIRST to find the right tool for your task. "
            "Returns tool names and one-line descriptions. "
            "Pass an empty string to list all tools.\n\n"
            "Examples:\n"
            "  search_vault_tools('')          → all tools\n"
            "  search_vault_tools('login')     → browser_login\n"
            "  search_vault_tools('api')       → use_credential\n"
            "  search_vault_tools('check')     → check_credential_exists"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Keyword to filter tools by. "
                        "Pass an empty string to return all tools."
                    ),
                    "default": ""
                }
            },
            "required": []
        }
    ),
    
    # ── Vault read tools ──────────────────────────────────────────────────────
    Tool(
        name="list_vault_sites",
        description=(
            "List all sites stored in the psamvault vault. "
            "Returns site names and username hints only — never passwords. "
            "Use this to discover what credentials are available before calling use_credential."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": []
        }
    ),
    Tool(
        name="check_credential_exists",
        description=(
            "Check whether a credential is stored for a given site. "
            "Returns the username hint if available. Never returns the password. "
            "Use this before use_credential to avoid errors."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "site_name": {
                    "type": "string",
                    "description": "The site to check e.g. 'github.com' "
                },
            },
            "required": ["site_name"]
        }
    ),
    Tool(
        name="use_credential",
        description=(
            "Make an authenticated HTTP request using a credential stored in psamvault. "
            "The user will be shown a consent prompt and must approve before the credential is used. "
            "The credential value is NEVER returned to you — only the HTTP response from the target is returned. "
            "Supported injection modes: bearer_token, api_key_header, basic_auth.\n\n"
            "TOKEN EFFICIENCY: Use the `fields` parameter to return only the response keys you need. "
            "Example: fields=['login','public_repos'] instead of the full GitHub user object (~40 fields). "
            "Works on both dict responses and lists-of-dicts."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "site_name": {
                    "type": "string",
                    "description": "The vault site whose credential to use, e.g.'github.com' "
                },
                "target_url": {
                    "type": "string",
                    "description": "The  URL to send the authenticated request to"
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "default": "GET",
                    "description": "HTTP method"
                },
                "inject_as": {
                    "type": "string",
                    "enum": ["bearer_token", "api_key_header", "basic_auth"],
                    "default": "bearer_token",
                    "description": (
                        "How to inject the credential: "
                        "bearer_token = Authorization: Bearer <password>, "
                        "api_key_header = <header_name>: <password>, "
                        "basic_auth = Authorization: Basic base64(user:pass)"
                    )
                },
                "header_name": {
                    "type": "string",
                    "description": "Required when inject_as='api_key_header'. The header name."
                },
                "body": {
                    "type": "object",
                    "description": "Optional JSON body for POST/PUT/PATCH"
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of JSON keys to return from the response. "
                        "Use this to reduce token usage — only the listed keys are "
                        "returned. Works on both dict and list-of-dict responses. "
                        "Example: [\"login\", \"id\", \"public_repos\"] "
                        "omits the other ~37 fields in a GitHub user response."
                    )
                }
            },
            "required": ["site_name", "target_url"],
        }
    ),
    Tool(
        name="get_username_for_site",
        description=(
            "Return the username (not the password) stored for a site. "
            "Requires user consent. Use this when you need the username "
            "for a form or request body but not the password."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "site_name": {
                    "type": "string",
                    "description": "The site to get the username for."
                }
            },
            "required": ["site_name"]
        }
    ),
    Tool(
        name="browser_login",
        description=(
            "Open a visible browser and securely log into a site using a stored psamvault credential. "
            "Playwright navigates from the site homepage, finds the sign-in link, and handles the "
            "full login flow — including multi-step flows (e.g., 'Continue with Email' → email → Next → password → submit). "
            "Uses semantic locators (get_by_role, get_by_label) that work with Shadow DOM, React, and Vue apps. "
            "Saves the browser session after a successful login so it can be reused on subsequent calls. "
            "The credential is NEVER returned to you — psamvault fills the fields directly inside its own browser. "
            "The user will be shown a consent prompt and must approve before any credential is used. "
            "Returns a concise summary: success, message, captcha_detected, captcha_screenshot, final_url, steps_count, and failed_at. "
            "When success is true, the response includes a message field — always relay it to the user. "
            "When captcha_detected is true, the tool pauses automation; inform the user and tell them to solve the CAPTCHA and click Sign in/Login manually in the browser. "
            "When captcha_screenshot is not null, tell the user a screenshot of the CAPTCHA was saved to that path so they can inspect it. "
            "Only site_name is required. All other parameters are optional."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "site_name": {
                    "type": "string",
                    "description": "The vault site whose credential to use, e.g. 'github.com' or 'z.ai'. psamvault will navigate from the homepage and find the login page automatically."
                },
                "login_url": {
                    "type": "string",
                    "description": "Optional full URL of the login page. Auto-discovered from the homepage if not provided."
                },
                "username_selector": {
                    "type": "string",
                    "description": "Optional CSS selector for the username/email field. Auto-detected if not provided."
                },
                "password_selector": {
                    "type": "string",
                    "description": "Optional CSS selector for the password field. Auto-detected if not provided."
                },
                "submit_selector": {
                    "type": "string",
                    "description": "Optional CSS selector for the submit button. Auto-detected if not provided."
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Per-step detection timeout in milliseconds. Default is 8000. Increase for slow or JS-heavy sites.",
                    "default": 8000
                }
            },
            "required": ["site_name"]
        }
    )
]




# Tool list handler
@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """Return all available psamvault tools to the MCP client"""
    return TOOL_DEFINITIONS


# Tool call handler
@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    """
    Route tool calls to the appropriate handler function and return results.
    All results are returned as TextContent — the agent reads text, not raw dicts.
    """
    # ── Tools handled inline, no session check needed ─────────────────────────
    if name == "get_version":
        return [TextContent(type="text", text=json.dumps({"version": _VERSION}))]

    if name == "search_vault_tools":
        query = (arguments.get("query") or "").lower().strip()
        if query:
            matches = {
                k: v for k, v in _TOOL_REGISTRY.items()
                if query in k or query in v.lower()
            }
            result = matches if matches else _TOOL_REGISTRY
        else:
            result = _TOOL_REGISTRY
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    if not is_logged_in():
        result = {
            "error": (
                "psamvault session not found. "
                "Run  'psamvault login'  in your terminal first, then try again"
            )
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    
    try:
        if name == "list_vault_sites":
            result = await tools.list_vault_sites()
        
        elif name == "check_credential_exists":
            result = await tools.check_credential_exists(
                site_name=arguments["site_name"]
            )

        elif name == "use_credential":
            result = await tools.use_credential(
                site_name=arguments["site_name"],
                target_url=arguments["target_url"],
                method=arguments.get("method", "GET"),
                inject_as=arguments.get("inject_as", "bearer_token"),
                header_name=arguments.get("header_name"),
                body=arguments.get("body"),
                extra_headers=arguments.get("extra_headers"),
                fields=arguments.get("fields"), 
            )
            
        elif name == "get_username_for_site":
            result = await tools.get_username_for_site(
                site_name=arguments["site_name"]
            )

        elif name == "browser_login":
            result = await tools.browser_login(
                site_name=arguments["site_name"],
                login_url=arguments.get("login_url"),
                username_selector=arguments.get("username_selector"),
                password_selector=arguments.get("password_selector"),
                submit_selector=arguments.get("submit_selector"),
                timeout_ms=arguments.get("timeout_ms", 8000),
            )

        else:
            result = {"error": f"Unknown tool: {name}"}
    
    except Exception as e:
        print(
            f"  psamvault: tool '{name}' raised an unexpected error: {e}",
            file=sys.stderr,
        )
        result = {"error": "Tool execution failed. Check the terminal for details."}
        
    return [TextContent(type="text", text=json.dumps(result, indent=2))]

async def _run_server() -> None: 
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Start the psamvault MCP server over stdio."""
    print(
        "psamvault MCP server starting...",
        file=sys.stderr
    )

    if not is_logged_in():
        print(
            "Warning: not logged in to psamvault. "
            "Run psamvault login before using vault tools",
            file=sys.stderr
        )

    asyncio.run(_run_server())
    

if __name__ == "__main__":
    main()