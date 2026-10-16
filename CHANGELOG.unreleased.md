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
- `fix(session): a locked or uninitialised Secret Service does not block startup` — `keyring.get_password` calls `Unlock` on a locked default collection and `CreateCollection` when the `default` alias is missing, and both open a GUI prompt and wait. Startup now reads the alias and the lock state and does not make those calls, so `initialize` is answered. Vault tools return the `✗` message. The next tool call looks again, so unlocking takes effect without restarting the host. The probe runs on a worker thread and does not block the stdio loop.
- `fix(cmd): run_with_credential does not inherit the session bus` — adopting `DBUS_SESSION_BUS_ADDRESS` and `XDG_RUNTIME_DIR` into `os.environ` copied them into every child, so a command could open the login keyring (`secret-tool`, or GDBus via `$XDG_RUNTIME_DIR/bus`). Those variables, plus `GNOME_KEYRING_CONTROL` and `DBUS_STARTER_ADDRESS`, are removed from the child. `extra_env` is applied after that and is how a command is given the bus on purpose.
- `fix(compat): a missing Hermes usage skill is not drift` — `doctor` and `compat --check` exited 1 on a fresh public install and told the user to run `--sync-skill`, which then failed because it looks for a private skill checkout. Absence is optional and exits 0 when nothing else is wrong. A skill file that is present but unversioned, or below the floor, is still drift. `--sync-skill` with no checkout returns `✗ no local skill checkout — …` and points at the public docs in this repository.

### Docs

- `docs(install): install Chromium with the pipx venv's Python` — `pipx install` does not put `playwright` on PATH, and the documented `~/.local/pipx/venvs` path is not where current pipx keeps venvs. The command asks `pipx environment --value PIPX_LOCAL_VENVS` and runs `python -m playwright install chromium`.
- `docs(config): per-platform keyring environment, and the public docs no longer link the private skill repo` — Linux host config must pass the session bus when `/run/user/<uid>/bus` is not visible; macOS and Windows need `PYTHONPATH=""` only. Headless use does not go through `PSAMVAULT_VEK` or `PSAMVAULT_TOKEN`. A missing Hermes skill is documented as optional.
