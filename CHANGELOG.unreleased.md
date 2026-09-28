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

- fix(session): a refresh that 401s because another client rotated the single-use token first now re-reads the keychain store and retries once with the newer token, instead of reporting the vault session as expired.
