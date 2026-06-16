---
name: how-to-discover-creds
description: Guide for discovering what credentials are stored in the vault — list sites and check for specific ones
type: agent-skill
---

# How to discover what credentials are stored

## Goal
Find out which credentials exist in the user's psamvault vault and report the
site names and username hints.

## Prerequisites
- The user must be logged in to psamvault (`psamvault login` in their terminal).

## Tools you need
| Tool | Purpose |
|------|---------|
| `list_vault_sites` | List all stored site names with username hints |
| `check_credential_exists` | Check if a specific site has a credential |

## Workflow

### Step 1: Call list_vault_sites()
This returns all stored sites with their username hints. For example:
```json
{
  "sites": [
    {"site_name": "github.com", "username_hint": "user@example.com"},
    {"site_name": "gitlab.com", "username_hint": "user@example.com"}
  ],
  "total": 2
}
```

### Step 2: Present the results to the user
Tell them:
- How many sites are stored
- Each site name and its username hint (so they recognise which credential is which)

### Step 3: (Optional) Check a specific site
If the user asks about a specific site, call:
`check_credential_exists(site_name="site.example.com")`
This returns whether the credential exists and the username hint.

## What NOT to do
- **Never return passwords** — `list_vault_sites` never shows them.
- **Never call browser_login** unless the user explicitly asks to log in.