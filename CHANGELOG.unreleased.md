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

## Added

- feat(apply): **upgrade safety** for `psamvault-compat --apply`, ported from the CLI's upgrade path — the installed skill is snapshotted before it is overwritten (newest 5 kept); `--pull` stashes uncommitted work (untracked included), pulls `--ff-only origin main` and restores it, stopping with the work restored if the pull fails and parking it in a labelled stash if the restore conflicts; `--from-git` reports branch/HEAD/dirtiness/position vs origin (and flags an editable install); a **smoke test** imports the freshly installed server in a fresh interpreter from a neutral cwd; and a failed install or smoke test **rolls back** to the previously installed release.
- feat(apply): `--pull` — bring the repo-sourced install up to date as part of the same command.

## Fixed

- fix(compat): the index pre-check is now **advisory** — `--apply` always attempts the install (`--refresh` makes it authoritative) and consults PyPI's JSON API only to explain a genuine resolver failure. Previously a lagging JSON API (it follows an upload by up to ~1 minute) made `--apply` refuse with exit 3, reporting the target as "not published on PyPI yet" while the index already had it — found live applying 0.5.1 seconds after publishing it.
- fix(compat): an unreachable PyPI no longer short-circuits `--apply`; the install proceeds and a note records that the published-ness of the target could not be confirmed.

## Tests

- test(compat): the breaking-release tests build their own contract fixtures instead of asserting on the shipped contract's newest release — that assumption broke on the 0.5.1 bump and would break on every bump.
- test(compat): regression test pinning "a lagging index must not block the repair".

## Docs

- docs: exit 3 documented as "install failed: the target is not on the index" (it no longer means "the index has not caught up").
- docs: `PLAN_version_lockstep.md` describes the index check as advice-only.
