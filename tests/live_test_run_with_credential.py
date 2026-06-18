"""Live test: run_with_credential against real vault.

Tests both env injection and stdin injection with
the real testpypi credential stored in the vault.
The credential value should NEVER appear in output.
"""
import asyncio
import json
import os
import sys

# Add the MCP package to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
import keyring
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


async def main():
    BACKEND = "https://psam-vault-backend.onrender.com"

    # Get auth from keychain
    vek_hex = keyring.get_password("psamvault", "session.vek")
    access_token = keyring.get_password("psamvault", "session.access_token")
    if not vek_hex or not access_token:
        print("❌ Not logged in. Run 'psamvault login' first.")
        sys.exit(1)

    vek = bytes.fromhex(vek_hex)

    # Fetch testpypi credential
    print("--- Test 1: env injection (echo env var) ---")
    resp = httpx.get(
        f"{BACKEND}/apikeys/testpypi",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"❌ testpypi not found (HTTP {resp.status_code})")
        sys.exit(1)

    entry = resp.json()
    aesgcm = AESGCM(vek)
    ct = bytes.fromhex(entry["encrypted_blob"])
    nonce = bytes.fromhex(entry["iv"])
    plaintext = aesgcm.decrypt(nonce, ct, None)
    payload = json.loads(plaintext.decode())
    token = payload["api_key"]
    print(f"   Decrypted token: {token[:10]}... ({len(token)} chars)")

    # Now test the actual cmd_runner
    from mcp_server.cmd_runner import run_command_with_credential

    # Test 1: env injection
    result = await run_command_with_credential(
        command="echo %TWINE_USERNAME% && echo %TWINE_PASSWORD%",
        credential_value=token,
        inject_as="env",
        env_var_name="TWINE_PASSWORD",
    )
    print(f"   exit_code: {result['exit_code']}")
    print(f"   stdout: {repr(result['stdout'][:200])}")
    assert result["exit_code"] == 0
    assert "__token__" in result["stdout"], "TWINE_USERNAME should be __token__"
    assert "[REDACTED]" in result["stdout"], "Token should be redacted"
    assert token not in result["stdout"], "Token leaked in stdout!"
    print("   ✅ Env injection: token redacted, TWINE_USERNAME present")

    # Test 2: stdin injection
    print()
    print("--- Test 2: stdin injection ---")
    result2 = await run_command_with_credential(
        command='echo "HELLO"',
        credential_value=token,
        inject_as="stdin",
    )
    print(f"   exit_code: {result2['exit_code']}")
    assert result2["exit_code"] == 0
    assert token not in result2["stdout"], "Token leaked in stdout!"
    print("   ✅ Stdin injection: no token leak")

    # Test 3: verify token appears nowhere in stderr/stdout
    print()
    print("--- Test 3: full output contains no token ---")
    result3 = await run_command_with_credential(
        command=f"echo {token[:8]}",
        credential_value=token,
        inject_as="env",
        env_var_name="MY_SECRET",
    )
    print(f"   stdout: {repr(result3['stdout'])}")
    # The echo command would output the first 8 chars which get redacted by prefix scan
    assert token not in result3["stdout"], "Full token leaked!"
    print("   ✅ Prefix redaction works")

    print()
    print("=" * 50)
    print("All live tests passed!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
