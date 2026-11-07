---
title: MCP use policy
description: When a tool may use a stored secret, when the server asks, and what a refusal tells the human to run.
order: 45
---

# MCP use policy

`use_credential`, `run_with_credential`, `browser_login`, `export_key_to_mcp_config`,
`export_key_to_env_file`, and `verify_api_key` decrypt a stored secret. Before that decryption, and
before the destination check, the server reads `~/.psamvault/policy.json`.

Discovery tools do not decrypt for this check and are not gated. That includes `get_version`,
`search_vault_tools`, `list_vault_sites`, `check_credential_exists`, `get_username_for_site`,
`list_api_keys`, and `scan_and_protect`. They write no MCP-use audit row.

## When each mode applies

| `mcp_use` | Unlisted tool | Listed tool | Ask |
|---|---|---|---|
| `audit` | Allowed, reason `not-listed` | Allowed, reason `listed` | Never, even if `ask_each_time` is true |
| `enforce` | Refused, reason `not-listed` | Allowed, reason `listed` | Only when `ask_each_time` is true |
| `off` | Allowed, reason `mcp-use-off` | Allowed, same reason | Never |

`audit` is the mode when the key is absent, when it is JSON `null`, when the value is not one of the
three modes, and when the file is missing, unreadable, not UTF-8, or not a JSON object. A broken
file is not treated as `enforce`. `get_version` and the audit row use that same `audit` mode, and
the absent-key notice is shown from `get_version`. An explicit `"audit"` does not carry the notice.
A missing key does, and `get_version` is where that sentence is shown.

After an allow, a locked or unreachable OS keyring returns the `✗` keyring message. Unlock it and
retry. A `STOP` refusal is decided before that read, so unlocking the keyring does not allow the call.

`ask_each_time` is true only for JSON `true`. The server asks with MCP form elicitation: "Allow
\<tool\> to use \<entry\>?". Yes keeps the allow. No is `ask-declined`. If the host has no form
elicitation, or the prompt errors, times out, or is cancelled, the use is `ask-unanswered`. The
server does not turn a denial into an allow by asking.

## What a refusal looks like

The result starts with `STOP`. For a tool that is not listed, it names
`psamvault policy allow-tool <entry> <tool>`. That command is for a human at a terminal. The agent
stops. It does not edit `policy.json`, it does not run `psamvault`, and it does not call another
tool to get the secret.

A no, or a prompt that did not complete, also says `STOP` and does not offer a second command that
would let the same call through.

## What a human runs

```
psamvault policy tools
psamvault policy allow-tool ledger-key use_credential
psamvault policy ask-each-time ledger-key on
psamvault policy mcp-use enforce
```

`psamvault policy mcp-use` with no mode prints the current mode, and the planned-flip sentence when
the key is absent. Granting a tool does not change `mcp_use`. `allow_hosts` still answers a
different question: where a secret may be sent, after this check has allowed the tool to use it.

Every gated call appends one row to `~/.psamvault/audit.jsonl` before decryption. The shape is in
[Configuration](../reference/configuration.md#audit-trail). A failure to write the row does not fail
the tool.
