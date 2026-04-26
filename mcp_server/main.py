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
"""

import asyncio
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from mcp_server.session import is_logged_in, load_config
from mcp_server import tools

load_config()

# Initialize MCP server
server = Server("psamvault")


TOOL_DEFINITIONS = [
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
            "Supported injection modes: bearer_token, api_key_header, basic_auth."
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
        name="debug_dump_credential",
        description=(
            "DIAGNOSTIC TOOL — decrypts a stored credential and writes the username "
            "and password to a plaintext file at ~/psamvault_debug_dump.txt. "
            "Use this to verify that credential retrieval and decryption work correctly, "
            "independently of the browser flow. "
            "Requires user consent. The user should delete the file after testing."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "site_name": {
                    "type": "string",
                    "description": "The vault site to dump, e.g. 'github.com'."
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
            "The credential is NEVER returned to you — psamvault fills the fields directly inside its own browser. "
            "The user will be shown a consent prompt and must approve before any credential is used. "
            "Only site_name is required. All selectors are optional and auto-detected."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "site_name": {
                    "type": "string",
                    "description": "The vault site whose credential to use, e.g. 'github.com' or 'z.ai'. psamvault will navigate from the homepage and find the login page automatically."
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
    import json
    
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
            )
            
        elif name == "get_username_for_site":
            result = await tools.get_username_for_site(
                site_name=arguments["site_name"]
            )

        elif name == "debug_dump_credential":
            result = await tools.debug_dump_credential(
                site_name=arguments["site_name"]
            )

        elif name == "browser_login":
            result = await tools.browser_login(
                site_name=arguments["site_name"],
                username_selector=arguments.get("username_selector"),
                password_selector=arguments.get("password_selector"),
                submit_selector=arguments.get("submit_selector"),
            )

        else:
            result = {"error": f"Unknown tool: {name}"}
    
    except Exception as e:
        result = {"error": f"Tool execution failed: {str(e)}"}
        
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