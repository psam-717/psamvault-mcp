# NemoClaw Compatibility

> **Last updated:** June 17, 2026
>
> This document explains how psamvault-MCP works inside (or alongside) an NVIDIA
> NemoClaw / OpenShell sandbox. The tl;dr is: **psamvault-MCP works with NemoClaw
> today** using the MCP bridge pattern, with credentials staying on the host.

---

## Overview

[NVIDIA NemoClaw](https://docs.nvidia.com/nemoclaw) runs AI agents inside
[OpenShell](https://github.com/NVIDIA/OpenShell) sandboxes with three immutable
protection layers: **network isolation**, **filesystem isolation**, and
**process isolation**.

psamvault's architecture aligns naturally with NemoClaw's security model:

| Security Concern | NemoClaw Protection | psamvault Solution |
|---|---|---|
| API keys in sandbox | ❌ Secrets must not enter the sandbox | ✅ Keys stored *on host*, never copied in |
| Agent reading secrets | ❌ Agent must not see plaintext | ✅ Credential values never returned to agent |
| Plaintext .env files | ❌ Sandbox reads expose secrets | ✅ `scan_and_protect` replaces with placeholders |
| Stripe provisioned creds | ❌ Fresh .env = leak risk | ✅ `capture_stripe_credentials` captures immediately |

---

## How psamvault-MCP Runs with NemoClaw

There are **two modes** depending on where you run the psamvault MCP server:

### Mode A: MCP Bridge (Recommended — Strongest Isolation)

NemoClaw has a built-in [MCP bridge](https://github.com/NVIDIA/NemoClaw/issues/566)
pattern that runs stdio-based MCP servers **on the host** and proxies them into
the sandbox via HTTP. This keeps credentials **permanently on the host**.

```
Host (psamvault lives here)         Sandbox (agent runs here)
┌──────────────────────────┐        ┌──────────────────────────┐
│  psamvault-mcp (stdio)   │        │       Hermes / OpenClaw  │
│    ↓                     │        │            ↓             │
│  stdio→HTTP proxy        │◄──────►│        mcporter          │
│  127.0.0.1:<port>        │ egress │   (MCP HTTP client)      │
└──────────────────────────┘  rule  └──────────────────────────┘
          host.openshell.internal:<port>
```

**Setup steps:**

1. Install psamvault-mcp on the **host** (not inside the sandbox):
   ```bash
   pipx install psamvault-mcp
   psamvault login
   ```

2. Add the psamvault network policy preset (see below).

3. Bridge the MCP server into the sandbox using the host-to-sandbox proxy
   pattern documented in NemoClaw's MCP bridge:
   ```bash
   # Start the psamvault-mcp stdio server
   psamvault-mcp &

   # Configure the sandbox to reach it — use an egress rule for
   # host.openshell.internal:<proxy-port>
   ```

4. The agent inside the sandbox calls psamvault tools through the bridge.
   Secret values are decrypted on the host and **never enter the sandbox**.

**Vault encryption key (VEK):** Lives in the host's OS keychain. The sandbox
never touches it. Token refresh (via `session.refresh_token`) also happens
on the host.

---

### Mode B: Direct stdio Transport (Simpler — Single Machine)

If you're running NemoClaw and psamvault-MCP **on the same physical machine**
(e.g., DGX Spark, developer laptop), configure Hermes/OpenClaw to spawn
psamvault-mcp as a direct stdio subprocess. The VEK and tokens stay in the
OS keychain on that machine.

**For Hermes:**
```yaml
# ~/.hermes/config.yaml — Hermes MCP config on the host
mcp_servers:
  psamvault:
    command: psamvault-mcp
    enabled: true
```

**For OpenClaw:**
```json
// OpenClaw MCP config
{
  "mcpServers": {
    "psamvault": {
      "command": "psamvault-mcp"
    }
  }
}
```

> **Note:** This works because psamvault uses **only** AES-256-GCM + OS keychain
> for all cryptographic operations — no GPU, no CUDA, no kernel modules. It
> runs anywhere Python runs.

---

## Network Policy Preset

If using Mode A (MCP bridge over HTTP), you need a network policy preset to
allow the sandbox to reach the host-side MCP proxy.

Create `nemoclaw-blueprint/policies/presets/psamvault.yaml`:

```yaml
preset:
  name: psamvault
  description: "psamvault MCP credential vault — host-side MCP bridge"
network_policies:
  psamvault_mcp:
    name: psamvault_mcp
    endpoints:
      - host: host.openshell.internal
        port: 3101  # default proxy port — change if using a different port
        protocol: rest
        enforcement: enforce
        rules:
          - allow: { method: GET, path: "/**" }
          - allow: { method: POST, path: "/**" }
    binaries:
      - { path: /usr/local/bin/node }
      - { path: /usr/local/bin/mcporter }
```

Apply it:

```bash
nemoclaw my-assistant policy-add psamvault --yes
```

> **Important:** The port number (`3101` above) must match the port your
> stdio→HTTP proxy binds to. If running multiple MCP servers, use a different
> port for each — the egress rule is per-port.

---

## Backend API Access

psamvault-MCP talks to the psamvault backend (`PSAMVAULT_API_URL`, default
`https://psam-vault-backend.onrender.com`). The **agent inside the sandbox
never calls this endpoint** — it only talks to the MCP proxy on the host.
The host-side MCP server makes the backend calls.

The agent only needs to reach:
- `host.openshell.internal:<proxy-port>` → the psamvault MCP bridge

That's it. No backend URL, no API endpoint, no credential endpoint needs
to be in the sandbox's network policy.

If you're using Mode B (direct stdio), no network policy is needed at all for
psamvault — the subprocess runs inside the sandbox with the VEK from the
host keychain (or its own local session).

---

## Filesystem Considerations

psamvault stores session data at `~/.psamvault/` (session.json presence marker):
- `session.json` — empty presence marker after keychain migration
- `config.env` — non-sensitive config (API URL)

In an OpenShell sandbox:
- `/home/sandbox` is writable (OpenShell grants `read_write` on `/home/*`)
- psamvault will create `~/.psamvault/` here naturally

**For Mode A (bridge):** psamvault runs entirely on the host — no filesystem
changes inside the sandbox.

**For Mode B (direct stdio):** The agent must run `psamvault login` inside
the sandbox (to set up the VEK in the keychain). This writes session data
to `~/.psamvault/` inside the sandbox. Snapshot your sandbox after login
so you don't need to re-authenticate on rebuild.

---

## Tool Compatibility Matrix

| Tool | Mode A (Bridge) | Mode B (Direct stdio) | Notes |
|---|---|---|---|
| `list_vault_sites` | ✅ | ✅ | No credential exposure |
| `check_credential_exists` | ✅ | ✅ | Returns boolean + username hint |
| `get_username_for_site` | ✅ | ✅ | Returns username only |
| `use_credential` | ✅ | ✅ | Key never enters agent context |
| `browser_login` | ⚠️ Requires GUI | ⚠️ Requires GUI | No display in headless sandbox |
| `scan_and_protect` | ✅ | ✅ | Scans `.env` in sandbox filesystem |
| `capture_stripe_credentials` | ⚠️ Stripe CLI | ⚠️ Stripe CLI | Needs Stripe CLI installed |

### browser_login note
`browser_login` requires a graphical display (Playwright Chromium). NemoClaw
sandboxes are typically headless. This tool is designed for developer machines
and non-sandboxed environments. If you need browser automation in NemoClaw,
you'd need to configure display forwarding or use a remote browser service.

---

## Security Properties Summary

| Property | psamvault-MCP in NemoClaw |
|---|---|
| **VEK location** | Host OS keychain (never in sandbox) |
| **Plaintext credential location** | MCP server memory only, milliseconds only |
| **Agent sees credential value?** | Never |
| **Backend stores plaintext?** | Never (zero-knowledge) |
| **Network policy needed?** | Mode A: 1 egress rule host.openshell.internal :port |
| **Additional attack surface** | None — same security model, just moved to host |
| **API key exfiltration via MCP?** | Not possible — MCP only returns HTTP responses |
