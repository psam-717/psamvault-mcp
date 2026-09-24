---
title: Credential injection
description: How an agent uses a credential it never sees — run_with_credential, use_credential, env vs stdin, redaction, and the failure modes.
order: 40
---

# Credential injection

This is the flagship flow: an agent runs a command or makes an HTTP request that **needs** a secret
without ever **receiving** one. Two tools do it — `run_with_credential` for CLI tools,
`use_credential` for HTTP — plus `get_username_for_site` for the narrow case where only the username
is needed.

## What the agent must never ask the user for

There is no tool that returns a secret value, and no supported workflow that produces one. In
particular:

- **Never ask the user to paste a credential into the chat.** Use `use_credential`,
  `run_with_credential`, or `browser_login` instead.
- **Never run `psamvault get`, `psamvault show`, `psamvault open`, or any psamvault CLI command**, and
  never read credential files from the filesystem. The only permitted way to access credentials is
  through the MCP tools.
- **Never read `~/.psamvault/` for secret values**, even while "just testing" an install.
- **Never print raw credentials** into chat, a file, or a command line you then echo.

`run_with_credential` enforces part of this mechanically: commands matching a blocked pattern are
refused before anything runs. The blocked patterns are `psamvault get|show|ak-get|ak-show|search|list|export`,
`psamvault vault-get|credential-get|entry-get`, `cat ...*.psamvault`, and `type ...*.psamvault`. A
refused command returns an error naming the pattern and the advice to use the appropriate MCP tool
instead.

## Running a CLI command with a credential (`run_with_credential`)

The tool description, verbatim:

> Run an arbitrary shell command with a credential injected as an environment variable or stdin pipe.
> The credential is decrypted locally, injected into the subprocess, and all output is scanned for the
> credential value and redacted before being returned — the credential NEVER enters the agent's
> context.

```
Agent: "Upload my package to PyPI"
         ↓
run_with_credential("pypi", "twine upload dist/*",
                    inject_as="env", env_var_name="TWINE_PASSWORD")
         ↓
psamvault decrypts the credential locally, spawns the subprocess
with the credential injected as an env var (or piped via stdin)
         ↓
All stdout and stderr is scanned for the credential value
and redacted before being returned
         ↓
Agent receives only the redacted output
```

The credential is looked up first in API key entries, then in vault entries — so a key stored as a
site password still works. `site_name` accepts either an API key name (`pypi`, `testpypi`,
`github-api`) or a vault site name (`github.com`, `dockerhub`).

### `env` vs `stdin`

| Mode | Default? | Credential is required | Use for |
|---|---|---|---|
| `env` | yes (`inject_as="env"`) | `env_var_name` is required | Anything that reads a token from an environment variable: `TWINE_PASSWORD`, `GITHUB_TOKEN`, `NPM_TOKEN`, `DOCKER_PASSWORD` |
| `stdin` | no | — | Anything that asks on the terminal: `docker login` reading the password from stdin |

Use cases from the tool description: `twine upload`, `docker login`, `npm publish`, `git push`,
`pip install` (private repo), and any CLI tool that needs an API key or password.

When `env_var_name` is `TWINE_PASSWORD`, `TWINE_USERNAME=__token__` is set automatically as a
convenience. `extra_env` merges non-sensitive values into the subprocess environment alongside the
credential (e.g. `{"TWINE_REPOSITORY_URL": "https://upload.pypi.org/legacy/"}`), and `workdir` sets
the subprocess working directory (default: the MCP server's own cwd).

### What comes back

A dict with `exit_code`, `stdout`, and `stderr`; `error` is added when the command could not run. The
redaction is applied to both streams before they are returned: every occurrence of the credential
value is replaced with `[REDACTED]`, and for credentials longer than 8 characters the 8-character
prefix is redacted too (so a log line that prints a truncated token still leaks nothing).

Two security guards run before the subprocess starts:

- **SSRF guard.** Every `http(s)://` URL in the command is extracted and checked; a URL pointing at a
  loopback, private, or link-local address is rejected, so a command like
  `curl http://169.254.169.254/$KEY` cannot be used to exfiltrate the credential to an internal
  endpoint.
- **Blocked-command guard.** As above — the psamvault read commands and `.psamvault` file reads are
  refused.

## Making an authenticated HTTP request (`use_credential`)

The tool description, verbatim:

> Make an authenticated HTTP request using a credential stored in psamvault. The lookup checks API key
> entries first, then vault (site password) entries — so you can use both API keys and site passwords.
> The credential value is NEVER returned to you — only the HTTP response from the target is returned.

```
Agent: "Get my top 10 starred repos"
         ↓
use_credential("github.com", target_url="https://api.github.com/users/psam-717/starred")
         ↓
psamvault decrypts the key locally, makes the HTTP request,
returns only the response
```

Injection modes, as documented in the schema:

- `bearer_token` (default) — `Authorization: Bearer ***`
- `api_key_header` — `<header_name>: <value>`; `header_name` is **required** in this mode
- `basic_auth` — `Authorization: Basic ***`; for a vault entry the username:password pair is used, for
  an API key entry the service name is the username and the key is the password

The response is parsed as JSON where possible (falling back to text), and sensitive response headers —
`set-cookie`, `authorization`, `www-authenticate`, `proxy-authenticate`, `set-cookie2` — are stripped
before the response reaches the agent. Requests to loopback, private, or link-local addresses are
rejected by the same SSRF guard. Redirects are followed.

**Use `fields` to cut tokens.** A full GitHub user object is around 40 fields; asking for
`fields=["login", "id", "public_repos"]` returns three. It works on dict responses and on
lists-of-dicts.

Returns: `success`, `status_code`, `headers` (minus the sensitive set), and `data` (the possibly
filtered body).

## Failure modes and what to do

### The command timed out (the 120s pitfall)

`run_with_credential` has a `timeout` parameter with a **default of 120 seconds**. When a command
exceeds it, the process tree is killed and the result comes back with `exit_code: -1`,
`error: "timeout"`, `timed_out: true`, and whatever the command had already printed in `stdout` /
`stderr` (already redacted). A **partial result is evidence; an empty string is not.**

This matters most for uploads and publishes. A long `twine upload`, a large `git push`, or a
`docker push` can be reported as a timeout while the work has in fact **already completed on the
server**. The failure the agent sees is a deadline, not a verdict.

**Verify server-side instead of blind-retrying.** Before running the command again, check whether the
effect already landed — the package/release page, the remote branch, the pushed artifact. A second
run of a publish is not a harmless retry: it can fail on a duplicate version, or produce a second
artifact. If the work did land, report success; only re-run when you have positive evidence it did
not. And if a re-run is genuinely needed for a long operation, raise `timeout` rather than repeating
the call at 120s, which risks hammering the same non-idempotent step.

### Never retry a payment step

**Do not retry a step that spends money.** A timeout, a dropped connection, or an ambiguous response
after a payment, checkout, or purchase call is not a licence to run it again — the first attempt may
have succeeded, and a retry can double-charge. Report the ambiguity to the user and let them confirm
the outcome. This rule outranks any "the call failed, try again" instinct.

### "Not logged in" / session expired

The session file or keychain entry is missing or expired. Tell the user: "Please run `psamvault login`
in your terminal, then ask me again." Do not try to refresh the session by reading vault files or
running `psamvault get`.

### The credential is not found

`run_with_credential` and `use_credential` both report `Credential '<site_name>' not found in API keys
or vault entries.` — the entry is not stored. Use `list_api_keys()` / `list_vault_sites()` to find the
right name, and tell the user they need to add it (via the psamvault CLI) if it is genuinely absent.
Do not substitute a different site.

### Unknown injection mode

`inject_as` must be exactly `env` or `stdin` for `run_with_credential`, and `bearer_token`,
`api_key_header`, or `basic_auth` for `use_credential`. Anything else returns an error naming the
valid modes — the most common cause is choosing `api_key_header` and omitting `header_name`.

### Token expiry mid-session

The access token has ~1 hour lifetime. The API client handles 401 → refresh → retry automatically, so
an idle server may absorb one refresh cycle on the next call. If the refresh itself fails, the call
returns a session-expired error and the user must run `psamvault login`.

## See also

- [Tool reference](./../reference/tools.md) — exact parameters and return shapes.
- [Browser login](./browser-login.md) — the other way to use a credential, for websites rather than
  APIs and CLIs.
- [`mcp_server/prompts/general-rules.md`](../../mcp_server/prompts/general-rules.md) — the security rules
  the server ships to agents.
- [AGENTS.md](../../AGENTS.md) — agent workflows end to end.
