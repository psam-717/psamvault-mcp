---
name: how-to-get-username
description: Guide for retrieving a stored username (not password)
type: agent-skill
---

# How to retrieve a username for a site

## Goal
Return just the username (not the password) stored for a given site. The username
is returned to you so you can fill it in a form field or include it in an API
request body. The password is **never** returned.

## Prerequisites
- The user must be logged in to psamvault (`psamvault login` in their terminal).

## Tools you need
| Tool | Purpose |
|------|---------|
| `check_credential_exists` | Verify the credential exists and see the username hint |
| `get_username_for_site` | Retrieve the username |

## Workflow

### Step 1: Verify the credential exists
Call `check_credential_exists(site_name="site.example.com")`.
If it returns `exists: false`, tell the user the credential isn't stored yet.

### Step 2: Call get_username_for_site
Call `get_username_for_site(site_name="site.example.com")`.


### Step 3: Handle the response

#### ✅ On success
The response includes `username` — use it only for the task the user requested.

#### ❌ On failure
If `error` is returned, check the message and tell the user.

## What NOT to do
- **Never ask for or try to retrieve a password** — use `browser_login` instead.
- **Never return the username** in a format that could be confused with a password.
- **Never cache or store** the username after the task is complete.