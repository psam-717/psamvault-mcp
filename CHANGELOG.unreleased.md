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

### Fixed

- `fix(session): --version, --help, and the upgrade smoke import no longer crash when the OS keyring is missing` — `load_config()` ran at import, before the flags were read, and called `keyring.get_password`. A keyring-less Linux host raised `NoKeyringError`, and `compat --apply` treated that as a failed upgrade and rolled a good install back. Flags and the smoke import no longer touch the keychain. The server still starts. Vault tools return `✗ <what failed> — <what to do next>` when the keyring is missing or locked, and the existing login message when the keyring works but there is no session. A `PSAMVAULT_PEPPER` line left in `config.env` stays there when the keyring cannot store it. There is no plaintext fallback.
- `fix(session): on Linux, adopt the per-user session bus when the MCP host omits DBUS_SESSION_BUS_ADDRESS` — jeepney 0.9 reads only that variable, and the Python and TypeScript MCP SDKs do not pass it, so the documented `env: {PYTHONPATH: ""}` hid a working Secret Service. The server sets `DBUS_SESSION_BUS_ADDRESS` from `/run/user/<uid>/bus` when that path is a socket owned by this user, and sets `XDG_RUNTIME_DIR` when the directory exists and the variable is unset. An explicit value is never replaced. A regular file or another user's socket is not a bus. No other keyring backend is added.
- `fix(compat): a missing Hermes usage skill is not drift` — `doctor` and `compat --check` exited 1 on a fresh public install and told the user to run `--sync-skill`, which then failed because it looks for a private skill checkout. Absence is optional and exits 0 when nothing else is wrong. A skill file that is present but unversioned, or below the floor, is still drift. `--sync-skill` with no checkout returns `✗ no local skill checkout — …` and points at the public docs in this repository.

### Docs

- `docs(install): install Chromium with the pipx venv's Python` — `pipx install` does not put `playwright` on PATH, and the documented `~/.local/pipx/venvs` path is not where current pipx keeps venvs. The command asks `pipx environment --value PIPX_LOCAL_VENVS` and runs `python -m playwright install chromium`.
- `docs(config): per-platform keyring environment, and the public docs no longer link the private skill repo` — Linux host config must pass the session bus when `/run/user/<uid>/bus` is not visible; macOS and Windows need `PYTHONPATH=""` only. Headless use does not go through `PSAMVAULT_VEK` or `PSAMVAULT_TOKEN`. A missing Hermes skill is documented as optional.
