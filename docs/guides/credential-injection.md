---
title: Credential injection
description: How an agent uses a credential it never sees — which host may receive it, run_with_credential, use_credential, env vs stdin, scrubbing, and the failure modes.
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

> Run a shell command with a credential injected as an environment variable or stdin pipe. Use this
> for a CLI that reads a token from the environment or stdin (twine upload, docker login, npm publish,
> git push, pip install), not for an HTTP API — that is use_credential.
>
> The credential is decrypted locally and injected into the subprocess. Stdout and stderr are scrubbed
> of the raw value, its 8-character prefix, the reversed text, and the common encodings (base64 and
> urlsafe base64, with and without padding, URL-encoding, hex, and the basic-auth user:pass blob).
> Scrubbing is defence in depth, not a boundary: a form the scrubber does not know, or a program that
> contacts a host never written in the command, can still move the value.
>
> Every http(s) URL written in the command is checked the same way as use_credential. A site password
> may only be used when those URLs stay on that site. An API key may only be used when they stay on
> its service hosts, or on hosts a human allowed for that entry in ~/.psamvault/policy.json under
> entries["<entry>"].allow_hosts. Any other URL is refused before the command runs. Loopback and
> private URLs stay blocked. A command with no URL is not host-checked, because the destination is
> not in the command.

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

A dict with `exit_code`, `stdout`, and `stderr`; `error` is added when the command could not run. Both
streams are scrubbed before they are returned. The replacement is `[REDACTED]`. The forms covered are
the raw value (any length), the reversed text when the secret is at least 4 characters, the first 8
characters when it is longer than 8, and — when the encoded form is at least 8 characters — standard
and urlsafe base64 with and without padding, URL-encoding, hex, and the basic-auth `username:secret`
base64 blob. A short encoding is left alone so the scrubber does not eat ordinary tokens.

Scrubbing is defence in depth. It is not the boundary. A transform the scrubber does not know (a
cipher, a secret split across lines, a value the program hashes) can still appear in the output, and
only if the command was allowed to run.

Three checks run before the subprocess starts:

- **SSRF guard.** Every `http(s)://` URL in the command is extracted and checked; a URL pointing at a
  loopback, private, or link-local address is rejected, so a command like
  `curl http://169.254.169.254/$KEY` cannot be used to send the credential to an internal endpoint.
  Listing that address in `allow_hosts` does not override this.
- **Destination check.** The same URLs must stay on the entry's own site, the API key's service
  hosts, or a host a human listed for that entry. A command with no `http(s)` URL is not host-checked:
  `twine upload` and `git push` name no URL, so the tool cannot see where they connect. That is a
  limit of reading the command text, not a grant to send the secret anywhere.
- **Blocked-command guard.** As above — the psamvault read commands and `.psamvault` file reads are
  refused.

A refused URL returns `exit_code: -1` and the reason in `error` and `stderr`. The command does not
run.

## Making an authenticated HTTP request (`use_credential`)

The tool description, verbatim:

> Make an authenticated HTTP request using a credential stored in psamvault. The lookup checks API key
> entries first, then vault (site password) entries — so you can use both API keys and site passwords.
> Use it when target_url is that entry's own site, or the API key's service. A site password is sent
> only to the registrable domain of its site name or login URL, and to subdomains of that domain. An
> API key is sent only to hosts its service already names — a bundled verify recipe matched from the
> service, the service hint, or the key name, or a hostname written in those fields — including that
> host's registrable domain and subdomains, or to hosts a human has listed for the entry. Any other
> host is refused, including a redirect that leaves an allowed host. An API key with no known service
> host is refused until a human allows one. Loopback and private addresses stay blocked, and
> allow_hosts cannot override that block. target_url must be an absolute http or https URL.
>
> The credential value is not returned. Response bodies and headers are scrubbed of the secret in its
> raw form, reversed text, 8-character prefix, and common encodings (base64 and urlsafe base64, with
> and without padding, URL-encoding, hex, and the basic-auth user:pass blob). Scrubbing is defence in
> depth. The host check is the boundary.

```
Agent: "Get my top 10 starred repos"
         ↓
use_credential("github.com", target_url="https://api.github.com/users/psam-717/starred")
         ↓
psamvault decrypts the key locally, checks the host, makes the HTTP request,
returns the response with the secret scrubbed out
```

Injection modes, as documented in the schema:

- `bearer_token` (default) — `Authorization: Bearer ***`
- `api_key_header` — `<header_name>: <value>`; `header_name` is **required** in this mode
- `basic_auth` — `Authorization: Basic ***`; for a vault entry the username:password pair is used, for
  an API key entry the service name is the username and the key is the password

`target_url` must be an absolute `http` or `https` URL. `api.github.com/users/...` with no scheme is
refused.

### Where the secret may go

This is the boundary. Scrubbing the response is not.

- **Site password.** The host must be the registrable domain of the site name or of the entry's login
  URL, or a subdomain of that domain. `leakcheck.example` may be sent to `leakcheck.example` and
  `api.leakcheck.example`. It may not be sent to `notleakcheck.example` or to `httpbin.org`. A name
  that is not a hostname (`MyBank`) has no implicit host unless the entry has a login URL; until a
  human allows a host, every target is refused. Registrable domains follow the Public Suffix List,
  including private suffixes, so `bank.co.uk` does not cover `attacker.co.uk` and `psam.github.io`
  does not cover `other.github.io`.
- **API key.** The host must belong to the service, or be listed for that entry. Service hosts come
  from a bundled verify recipe (`github` → `api.github.com`, and then `github.com` and its other
  subdomains; likewise `render`, `openrouter`, `tavily`) when the service, the service hint, or the
  key name matches that recipe, and from any hostname written in those fields. A key whose name,
  service, and hint match nothing — no recipe, no hostname — has **no known service host** and is
  refused until a human allows one. A token inside the name can select a recipe: a key called
  `github-backup` is treated as GitHub.
- **Human allow-list.** `~/.psamvault/policy.json`, key `entries["<entry>"].allow_hosts`. One host, or
  `*.example.com` for that domain and its subdomains. A human edits the file (mode 0600). There is no
  MCP parameter and no MCP tool that adds a host. See
  [Configuration](../reference/configuration.md#destination-allow-list). The psamvault CLI does not
  validate this key yet; writing it by hand is the supported step.
- **Redirects.** Each hop is checked before the secret is sent there. A redirect from an allowed host
  to a disallowed host is refused, and so is a redirect to a loopback or private address. At most 5
  redirects are followed. `http` to an allowed public host is still accepted; only the host is bound.
- **SSRF.** Loopback, private, link-local, and `.local` / `.internal` targets are still refused, and
  `allow_hosts` cannot override that.

The response is parsed as JSON where possible (falling back to text). Sensitive response headers —
`set-cookie`, `authorization`, `www-authenticate`, `proxy-authenticate`, `set-cookie2` — are stripped,
and the body and the remaining headers are scrubbed with the same encodings as command output, before
the response reaches the agent. `fields` is applied after that scrub.

**Use `fields` to cut tokens.** A full GitHub user object is around 40 fields; asking for
`fields=["login", "id", "public_repos"]` returns three. It works on dict responses and on
lists-of-dicts.

Returns: `success`, `status_code`, `headers` (minus the sensitive set, and scrubbed), and `data` (the
possibly filtered body).

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

### The host was refused

`use_credential` returns `{"error": ...}` and `run_with_credential` returns `exit_code: -1` when the
target is not this entry's site or the key's service. The error names the host and tells the user to
add it under `entries["<entry>"].allow_hosts` in `~/.psamvault/policy.json`. Relay that. Do not edit
the policy file, do not invent an MCP argument for it, and do not point the same secret at another
host to read it back. An API key with no known service host says so explicitly ("no known service
host") and stays refused until a human allows one. A redirect off an allowed host is the same refusal,
and the request to the second host is not made.

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
