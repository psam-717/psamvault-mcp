# Unreleased Changes

<!--
  This file tracks changes merged to main but NOT yet published to PyPI.
  It is the single place to look for "what is pending for the next release?".

  When preparing a release:
    1. Cut/paste the sections below into the GitHub release notes
    2. Prepend them to CHANGELOG.md under the new version header
    3. Clear this file's content (keep this header + comment)
    4. Bump the version, add the compatibility.json entry, then publish

  Sections: Added / Changed / Fixed / Tests / Docs. One line per user-visible change:
  `type(scope): what changed and why it matters`.
-->

## Fixed

- fix(scan): `scan_and_protect` skips `.env.bak-*`, `.env.old`, `.env.save`, and dated `.env.<digits>` copies so they are not stored as a second live key. `.env.local` and `.env.production` are still scanned
- fix(session): a refresh that 401s because another client rotated the single-use token first now re-reads the keychain store and retries once with the newer token, instead of reporting the vault session as expired.
- fix(api_client): an API key lookup now resolves the name `list_api_keys` returns — `project/.env/KEY` and the unscoped `env/.env/KEY` are sent as the whole path parameter, and a bare leaf is matched against the key list — so the entries `scan_and_protect` stores can be read back by `export_key_to_env_file`, `run_with_credential`, `use_credential`, `export_key_to_mcp_config` and `verify_api_key` instead of reporting them as not found. A leaf matching several stored keys now errors with the candidates rather than 404ing. Matching is case-insensitive because the vault strips and lower-cases every name on write, so the stored spelling — not the caller's casing — is what is sent; that is what the issue's own repro needed, where the scan reported a name in upper case that the vault had stored in lower case.

## Docs

- docs(scan): the scan guide and the `scan_and_protect` tool page say backup env files are skipped
- docs(tools): the tool reference explains how an API key name is resolved — the full stored name, a unique leaf, case-insensitive matching, the ambiguous-leaf error, and which tools are unaffected
