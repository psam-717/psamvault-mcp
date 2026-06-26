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

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from mcp_server.log import get_logger
from mcp_server.session import is_logged_in, load_config

load_config()

from mcp_server import tools

from mcp_server.version_check import check_for_update

logger = get_logger()

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


        "4. ALWAYS call search_vault_tools first to find the right tool for your task. "
        "If you are unsure which sites are stored, call list_vault_sites(). "
        "If you are unsure whether a credential exists, call check_credential_exists(site_name) first.\n\n"

         "Tool summary (tools grouped by purpose):\n"
         "\n"
         "🛠  Entry & Orientation — always start here:\n"
        "- search_vault_tools       → discover which tool to use (call this first)\n"
        "- get_version              → check installed version\n"
         "\n"
         "🔐  Site Authentication — log into websites:\n"
        "- list_vault_sites         → list stored sites (names + username hints, no passwords)\n"
        "- check_credential_exists  → verify a credential exists for a site\n"
        "- get_username_for_site    → get just the username (not password) for a site\n"
        "- browser_login            → open a browser and log into a website silently\n"
         "\n"
         "🔑  API Key Operations — use and protect API keys:\n"
        "- list_api_keys            → list stored API keys (no key values)\n"
        "- use_credential           → make an authenticated HTTP/API request\n"
        "- run_with_credential      → run a CLI command with credential injected (env/stdin)\n"
        "- scan_and_protect         → scan project .env files for exposed secrets and protect them\n"
        "- capture_stripe_credentials → capture Stripe Projects provisioned credentials\n"
    ),
)

# ── Version ────────────────────────────────────────────────────────────────────
try:
    _VERSION = importlib.metadata.version("psamvault-mcp")
except importlib.metadata.PackageNotFoundError:
    _VERSION = "unknown"

# ── Tool registry used by search_vault_tools ──────────────────────────────────
# Each entry is a one-line description string.
# Kept here so it stays in sync with TOOL_DEFINITIONS below.
_ENTRY_DESCRIPTION = (
    "Discover available psamvault tools. Call this FIRST to find the right tool. "
    "Pass a keyword like 'login', 'api', 'check', or '' for all tools."
)
_TOOL_REGISTRY: dict[str, str] = {
    # ── Entry & Orientation ────────────────────────────────────────────
    "search_vault_tools": _ENTRY_DESCRIPTION,
    "get_version": (
        "Return the installed psamvault-mcp version. "
        "Use this to verify the version you have."
    ),

    # ── Site Authentication ────────────────────────────────────────────
    "list_vault_sites": (
        "List all site names in the vault (names + username hints, no passwords). "
        "Call before browser_login to discover what sites are available."
    ),
    "check_credential_exists": (
        "Check whether a credential exists for a site. "
        "Params: site_name. Returns exists + username_hint."
    ),
    "get_username_for_site": (
        "Get the stored username only (not password) for a site. "
        "Params: site_name."
    ),
    "browser_login": (
        "Open a real Chromium browser and log into a website silently. "
        "Params: site_name (required), login_url, username_selector, "
        "password_selector, submit_selector, timeout_ms (all optional)."
    ),

    # ── API Key Operations ─────────────────────────────────────────────
    "list_api_keys": (
        "List all stored API key names (never key values). "
        "Optionally filter by project_name. "
        "Use before use_credential to discover API keys."
    ),
    "use_credential": (
        "Make an authenticated HTTP request using a stored credential. "
        "Params: site_name, target_url, method, inject_as, fields (optional — "
        "pass field names to trim the response and save tokens, e.g. [\"login\", \"id\"])."
    ),
    "run_with_credential": (
        "Run an arbitrary shell command with a credential injected as an env var "
        "or stdin. Use for twine upload, git push, docker login, npm publish, "
        "pip install, or any CLI tool that needs an API key or password. "
        "The credential is never returned to you — output is redacted automatically. "
        "Params: site_name (required), command (required), inject_as='env', "
        "env_var_name, extra_env, workdir, timeout=120."
    ),
    "scan_and_protect": (
        "Scan a project directory for exposed secrets in .env files and protect them. "
        "Encrypts secrets into the vault and replaces plaintext with "
        "'psamvault:KEY_NAME' placeholders. "
        "Params: project_dir (optional), patterns (optional), project_name (optional)."
    ),
    "capture_stripe_credentials": (
        "Capture credentials provisioned by Stripe Projects into psamvault. "
        "After 'stripe projects add <provider>', call this to securely store "
        "the provisioned credentials. Params: provider (required), project_dir (optional), "
        "dry_run (optional)."
    ),
}


TOOL_DEFINITIONS = [
    # ═══════════════════════════════════════════════════════════════════════
    # 🛠  ENTRY & ORIENTATION
    # Tools the agent calls when it has no context about what to do.
    # ═══════════════════════════════════════════════════════════════════════

    # ── Version tool — no session needed ────────────────────────────
    Tool(
        name="get_version",
        description="[🛠 Entry & Orientation] Return the installed psamvault-mcp version. No session or login required.",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": []
        }
    ),

    # ── Discovery tool — the only tool agents need to know about upfront ──
    Tool(
        name="search_vault_tools",
        description=(
            "[🛠 Entry & Orientation] Discover available psamvault tools. "
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

    # ═══════════════════════════════════════════════════════════════════════
    # 🔐  SITE AUTHENTICATION
    # End-to-end: discover, check, and log into websites.
    # ═══════════════════════════════════════════════════════════════════════

    Tool(
        name="list_vault_sites",
        description=(
            "[🔐 Site Authentication] List all sites stored in the psamvault vault. "
            "Returns site names and username hints only — never passwords. "
            "Call this before browser_login to discover what sites are available."
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
            "[🔐 Site Authentication] Check whether a credential is stored for a given site. "
            "Returns the username hint if available. Never returns the password. "
            "Use this before browser_login to avoid errors."
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
        name="get_username_for_site",
        description=(
            "[🔐 Site Authentication] Return the username (not the password) stored for a site. "
            "Use this when you need the username "
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
            "[🔐 Site Authentication] Open a visible browser and securely log into a site using a stored psamvault credential. "
            "Playwright navigates from the site homepage, finds the sign-in link, and handles the "
            "full login flow — including multi-step flows (e.g., 'Continue with Email' → email → Next → password → submit). "
            "Uses semantic locators (get_by_role, get_by_label) that work with Shadow DOM, React, and Vue apps. "
            "Saves the browser session after a successful login so it can be reused on subsequent calls. "
            "The credential is NEVER returned to you — psamvault fills the fields directly inside its own browser. "
            ""
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
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # 🔑  API KEY OPERATIONS
    # All tools that deal with API keys — discover, use, inject, and protect.
    # ═══════════════════════════════════════════════════════════════════════

    Tool(
        name="list_api_keys",
        description=(
            "[🔑 API Key Operations] List all stored API key names with service hints. "
            "Never returns the actual key values. "
            "Use this to discover what API keys are available. "
            "Optionally pass project_name to filter keys for a specific project "
            "(stored via scan_and_protect as 'project/.env/KEY')."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Optional project name to filter by. "
                                   "Keys stored via scan_and_protect(project_name=...) "
                                   "use the format 'project/.env/KEY'."
                }
            },
            "required": []
        }
    ),
    Tool(
        name="use_credential",
        description=(
            "[🔑 API Key Operations] Make an authenticated HTTP request using a credential stored in psamvault. "
            "The lookup checks API key entries first, then vault (site password) entries — "
            "so you can use both API keys and site passwords. "
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
                        "bearer_token = Authorization: Bearer *** "
                        "api_key_header = <header_name>: <password>, "
                        "basic_auth = Authorization: Basic base64...ss)"
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
                },
                "extra_headers": {
                    "type": "object",
                    "description": "Optional additional headers to include in the request."
                }
            },
            "required": ["site_name", "target_url"],
        }
    ),
    Tool(
        name="run_with_credential",
        description=(
            "[🔑 API Key Operations] Run an arbitrary shell command with a credential injected as an "
            "environment variable or stdin pipe. "
            "The credential is decrypted locally, injected into the subprocess, "
            "and all output is scanned for the credential value and redacted "
            "before being returned — the credential NEVER enters the agent's context.\n\n"
            "Use cases:\n"
            "- twine upload: inject_as='env', env_var_name='TWINE_PASSWORD'\n"
            "- docker login: inject_as='stdin' (password piped to stdin)\n"
            "- npm publish: inject_as='env', env_var_name='NPM_TOKEN'\n"
            "- git push: inject_as='env', env_var_name='GITHUB_TOKEN'\n"
            "- pip install (private repo): inject_as='env', env_var_name='PIP_TOKEN'\n\n"
            "Only site_name and command are required. "
            "When inject_as='env', env_var_name is required."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "site_name": {
                    "type": "string",
                    "description": (
                        "The credential to use. This can be an API key name "
                        "(e.g. 'pypi', 'testpypi', 'github-api') or a vault "
                        "site name (e.g. 'github.com', 'dockerhub'). "
                        "Check with list_api_keys or list_vault_sites first."
                    )
                },
                "command": {
                    "type": "string",
                    "description": (
                        "Shell command to run with the credential injected. "
                        "Example: 'twine upload dist/*' or 'git push origin main'"
                    )
                },
                "inject_as": {
                    "type": "string",
                    "enum": ["env", "stdin"],
                    "default": "env",
                    "description": (
                        "'env' — set credential as an environment variable "
                        "(requires env_var_name). "
                        "'stdin' — pipe credential as stdin to the process. "
                        "Default: 'env'"
                    )
                },
                "env_var_name": {
                    "type": "string",
                    "description": (
                        "Required when inject_as='env'. "
                        "The environment variable name to set the credential as. "
                        "Examples: 'TWINE_PASSWORD', 'GITHUB_TOKEN', "
                        "'DOCKER_PASSWORD', 'NPM_TOKEN'. "
                        "When 'TWINE_PASSWORD' is used, TWINE_USERNAME is "
                        "automatically set to '__token__'."
                    )
                },
                "extra_env": {
                    "type": "object",
                    "description": (
                        "Optional additional environment variables to pass to "
                        "the subprocess. These are non-sensitive values merged "
                        "into the subprocess environment alongside the credential. "
                        "Example: {'TWINE_REPOSITORY_URL': 'https://upload.pypi.org/legacy/'}"
                    )
                },
                "workdir": {
                    "type": "string",
                    "description": (
                        "Optional working directory for the command. "
                        "Defaults to the MCP server's current working directory."
                    )
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait for the command (default: 120).",
                    "default": 120,
                }
            },
            "required": ["site_name", "command"],
        }
    ),
    Tool(
        name="scan_and_protect",
        description=(
            "[🔑 API Key Operations] Scan a project directory for exposed secrets in .env files and protect them. "
            "Finds .env files, detects API keys and passwords using pattern matching, "
            "encrypts them into the psamvault vault, and replaces the plaintext values "
            "with 'psamvault:<KEY_NAME>' placeholders. "
            "The captured secrets can then be used with use_credential."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_dir": {
                    "type": "string",
                    "description": "Path to the project directory. Defaults to current working directory."
                },
                "patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional custom key name patterns to scan for (e.g. ['MY_CUSTOM_KEY'])."
                },
                "project_name": {
                    "type": "string",
                    "description": "Optional project name for grouping. Keys stored as 'project_name/.env/KEY_NAME' instead of 'env/.env/KEY_NAME'. Use this for cleaner per-project organisation."
                }
            }
        }
    ),
    Tool(
        name="capture_stripe_credentials",
        description=(
            "[🔑 API Key Operations] Capture credentials provisioned by Stripe Projects into psamvault. "
            "After running the 'stripe projects add <provider>' command, the provisioned "
            "credentials land in the project's .env file. This tool runs "
            "'stripe projects env --pull', parses the resulting .env for secrets, "
            "encrypts them into the psamvault API key store, and replaces the "
            "plaintext values with 'psamvault:<KEY_NAME>' placeholders. "
            "The captured secrets can then be used with use_credential."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "The Stripe Projects provider name, e.g. 'neon', 'supabase', 'openrouter'"
                },
                "project_dir": {
                    "type": "string",
                    "description": "Path to the project directory. Defaults to current working directory."
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If True, only preview what would be captured without storing anything.",
                    "default": False,
                }
            },
            "required": ["provider"],
        }
    ),
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
        
        elif name == "list_api_keys":
            project_name = arguments.get("project_name")
            result = await tools.list_api_keys(project_name=project_name)

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

        elif name == "scan_and_protect":
            result = await tools.scan_and_protect(
                project_dir=arguments.get("project_dir"),
                patterns=arguments.get("patterns"),
            )

        elif name == "capture_stripe_credentials":
            result = await tools.capture_stripe_credentials(
                provider=arguments["provider"],
                project_dir=arguments.get("project_dir"),
                dry_run=arguments.get("dry_run", False),
            )

        elif name == "run_with_credential":
            result = await tools.run_with_credential(
                site_name=arguments["site_name"],
                command=arguments["command"],
                inject_as=arguments.get("inject_as", "env"),
                env_var_name=arguments.get("env_var_name"),
                extra_env=arguments.get("extra_env"),
                workdir=arguments.get("workdir"),
                timeout=arguments.get("timeout", 120),
            )

        else:
            result = {"error": f"Unknown tool: {name}"}
    
    except Exception as e:
        logger.error("tool '%s' raised an unexpected error: %s", name, e)
        result = {"error": "Tool execution failed. Check the terminal for details."}
        
    return [TextContent(type="text", text=json.dumps(result, indent=2))]

async def _run_server() -> None:
    async with stdio_server() as (read_stream, write_stream):
        try:
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
        finally:
            await tools.close_all_browsers()


def main() -> None:
    """Start the psamvault MCP server over stdio."""
    logger.info("MCP server starting")

    if not is_logged_in():
        logger.warning("not logged in — run 'psamvault login' before using vault tools")

    check_for_update()

    try:
        asyncio.run(_run_server())
    finally:
        # Fallback: if the event loop crashed or was cancelled so hard that
        # _run_server's finally didn't run, force-close any tracked browsers
        # here (sync call — creates a throwaway loop for the async cleanup).
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(tools.close_all_browsers())
            loop.close()
        except Exception:
            pass
    

if __name__ == "__main__":
    main()