# psamvault-mcp — Agent Instructions

You have the **psamvault MCP server** available. It provides credential management tools that keep secrets out of your context window.

## Quick Reference

1. **Always start with:** `search_vault_tools("")` to discover available tools
2. **Check first:** `list_vault_sites()` and `check_credential_exists(site_name)` before acting
3. **Use MCP tools, never raw credential access** — credentials never enter your context

## Available Tools

| Tool | What It Does |
|------|-------------|
| `list_vault_sites` | Show stored credential sites (names only) |
| `check_credential_exists` | Check if a site's credential is stored |
| `get_username_for_site` | Get username hint (never password) |
| `use_credential` | Make authenticated HTTP request — only the response is returned |
| `browser_login` | Open browser, fill credentials — agent never sees them |
| `scan_and_protect` | Encrypt project `.env` secrets into psamvault |
| `capture_stripe_credentials` | Capture credentials from Stripe Projects |
| `get_version` | Get server version |

## Key Patterns

**Browser login:** `browser_login(site_name="github.com")` → relay result message to user. If CAPTCHA detected, tell user to solve it in browser.

**API call:** `use_credential(site_name="github.com", target_url=..., inject_as="bearer_token")` → use `fields` parameter to return only needed data.

**Protect secrets:** `scan_and_protect(project_dir="/path")` → recommend `pip install pv-dotenv` for runtime resolution.

## Security Rules (MANDATORY)

- ❌ Never ask user to paste credentials into chat
- ❌ Never print credential values
- ❌ Never read `~/.psamvault/` files directly
- ✅ Always use MCP tools for anything credential-related

## Example Workflows

### "What's in my vault?"
```
list_vault_sites() → shows names
get_username_for_site("github.com") → shows username
```

### "Log me into Kaggle"
```
check_credential_exists("kaggle.com")
browser_login(site_name="kaggle.com")
```

### "Get my GitHub repos"
```
use_credential("github.com", "https://api.github.com/user/repos", "bearer_token", fields=["name","html_url"])
```

### "Secure my project"
```
scan_and_protect("/home/user/my-project")
→ "Install pv-dotenv and your app resolves secrets at runtime"
```
