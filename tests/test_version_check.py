"""Tests for version_check.py — PyPI update notification."""

from pathlib import Path

import pytest

from mcp_server.version_check import (
    _VERSION_FILE,
    check_for_update,
    version_tuple,
)


class TestVersionTuple:
    def test_standard_semver(self):
        assert version_tuple("1.2.3") == (1, 2, 3)

    def test_major_only(self):
        assert version_tuple("2") == (2,)

    def test_non_numeric_part_returns_zero(self):
        """Non-numeric version parts cause a graceful fallback."""
        assert version_tuple("0.4.1.rc1") == (0,)

    def test_invalid_returns_zero(self):
        assert version_tuple("not.a.version") == (0,)

    def test_comparison(self):
        assert version_tuple("0.4.1") > version_tuple("0.4.0")
        assert version_tuple("1.0.0") > version_tuple("0.9.9")
        assert version_tuple("0.4.0") == version_tuple("0.4.0")


class TestCheckForUpdate:
    def test_no_update_when_installed_is_latest(self, monkeypatch: pytest.MonkeyPatch):
        """No notice when installed version matches or exceeds latest."""
        monkeypatch.setattr(
            "mcp_server.version_check._get_installed_version",
            lambda: "0.5.0",
        )
        monkeypatch.setattr(
            "mcp_server.version_check._get_latest_version",
            lambda: "0.5.0",
        )
        monkeypatch.setattr(
            "mcp_server.version_check._get_last_seen_version",
            lambda: None,
        )

        # Should not raise, should not print
        check_for_update()

    def test_no_update_when_installed_is_newer(self, monkeypatch: pytest.MonkeyPatch):
        """No notice when installed version is _ahead_ of PyPI (dev build)."""
        monkeypatch.setattr(
            "mcp_server.version_check._get_installed_version",
            lambda: "0.6.0",
        )
        monkeypatch.setattr(
            "mcp_server.version_check._get_latest_version",
            lambda: "0.5.0",
        )
        check_for_update()  # should not raise

    def test_update_notice_printed(self, monkeypatch: pytest.MonkeyPatch, capsys):
        """Notice printed when PyPI has a newer version."""
        monkeypatch.setattr(
            "mcp_server.version_check._get_installed_version",
            lambda: "0.4.0",
        )
        monkeypatch.setattr(
            "mcp_server.version_check._get_latest_version",
            lambda: "0.5.0",
        )
        monkeypatch.setattr(
            "mcp_server.version_check._get_last_seen_version",
            lambda: None,
        )
        monkeypatch.setattr(
            "mcp_server.version_check._set_last_seen_version",
            lambda v: None,
        )

        check_for_update()
        captured = capsys.readouterr()
        assert "Update available" in captured.err
        assert "0.4.0" in captured.err
        assert "0.5.0" in captured.err
        assert "pipx upgrade" in captured.err

    def test_notice_suppressed_for_already_seen_version(self, monkeypatch: pytest.MonkeyPatch, capsys):
        """Notice is NOT printed if we already notified about this latest version."""
        monkeypatch.setattr(
            "mcp_server.version_check._get_installed_version",
            lambda: "0.4.0",
        )
        monkeypatch.setattr(
            "mcp_server.version_check._get_latest_version",
            lambda: "0.5.0",
        )
        monkeypatch.setattr(
            "mcp_server.version_check._get_last_seen_version",
            lambda: "0.5.0",  # Already seen
        )

        check_for_update()
        captured = capsys.readouterr()
        assert captured.err == ""  # nothing printed

    def test_network_failure_silent(self, monkeypatch: pytest.MonkeyPatch):
        """Network errors are silently swallowed."""
        monkeypatch.setattr(
            "mcp_server.version_check._get_installed_version",
            lambda: "0.4.0",
        )
        monkeypatch.setattr(
            "mcp_server.version_check._get_latest_version",
            lambda: None,  # Network failure
        )
        check_for_update()  # should not raise

    def test_unknown_installed_version_silent(self, monkeypatch: pytest.MonkeyPatch):
        """When installed version can't be resolved, nothing happens."""
        monkeypatch.setattr(
            "mcp_server.version_check._get_installed_version",
            lambda: None,
        )
        check_for_update()  # should not raise