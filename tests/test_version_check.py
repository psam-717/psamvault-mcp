"""Tests for version_check.py — PyPI update notification and the shared version comparator."""

import pytest

from mcp_server.version_check import (
    _VERSION_FILE,
    check_for_update,
    latest_published,
    update_available,
    version_tuple,
)


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class TestVersionOrdering:
    """The comparator is shared with compat (mcp_server.versions.vkey)."""

    def test_standard_semver(self):
        assert version_tuple("1.2.3") == (1, 2, 3, 1)

    def test_major_only_pads_to_three_parts(self):
        assert version_tuple("2") == (2, 0, 0, 1)

    def test_numeric_compare_not_string_compare(self):
        """0.5.10 must beat 0.5.9 — a string compare gets this backwards."""
        assert version_tuple("0.5.10") > version_tuple("0.5.9")

    def test_prerelease_sorts_below_its_release(self):
        assert version_tuple("0.4.1.rc1") < version_tuple("0.4.1")

    def test_postrelease_sorts_above_its_release(self):
        assert version_tuple("0.5.3.post1") > version_tuple("0.5.3")

    def test_invalid_version_degrades_without_raising(self):
        assert version_tuple("not.a.version") == (0, 0, 0, 1)

    def test_empty_degrades(self):
        assert version_tuple("") == (0, 0, 0, 1)

    def test_equality(self):
        assert version_tuple("0.4.0") == version_tuple("0.4.0")
        assert version_tuple("v0.4.0") == version_tuple("0.4.0")


class TestLatestPublished:
    def test_returns_the_pypi_version(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "mcp_server.version_check.httpx.get",
            lambda *a, **k: _FakeResponse({"info": {"version": "0.5.3"}}),
        )
        assert latest_published() == "0.5.3"

    def test_network_failure_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        def boom(*a, **k):
            raise OSError("no network")

        monkeypatch.setattr("mcp_server.version_check.httpx.get", boom)
        assert latest_published() is None

    def test_malformed_payload_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "mcp_server.version_check.httpx.get",
            lambda *a, **k: _FakeResponse({"unexpected": True}),
        )
        assert latest_published() is None


class TestUpdateAvailable:
    def test_newer_published_is_reported(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("mcp_server.version_check.latest_published", lambda timeout=5.0: "0.5.3")
        assert update_available(installed="0.5.2") == "0.5.3"

    def test_current_install_reports_nothing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("mcp_server.version_check.latest_published", lambda timeout=5.0: "0.5.2")
        assert update_available(installed="0.5.2") is None

    def test_installed_ahead_reports_nothing(self, monkeypatch: pytest.MonkeyPatch):
        """A dev/git build ahead of PyPI must not be told to 'update'."""
        monkeypatch.setattr("mcp_server.version_check.latest_published", lambda timeout=5.0: "0.5.2")
        assert update_available(installed="0.6.0") is None

    def test_unreachable_index_reports_nothing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("mcp_server.version_check.latest_published", lambda timeout=5.0: None)
        assert update_available(installed="0.5.2") is None


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
        """Notice printed when PyPI has a newer version, advising the command that works."""
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
        # The advice must be the command that actually works, not `pipx upgrade`
        assert "psamvault-mcp compat --apply --latest" in captured.err

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


class TestTheNoticeNamesACommandTheGateAccepts:
    """The one notice every live server prints on startup must be a command that can upgrade.

    It previously re-derived the string and, through a swallowed `NameError` (no `import json`),
    silently dropped `--allow-breaking` — so the printed line exited 2 whenever PyPI was newer than
    the wheel's contract. There is one builder now (`compat.apply_command`).
    """

    def test_newer_than_the_contract_gets_the_flag(self):
        from mcp_server.version_check import _upgrade_command

        # 9.9.9 is past anything the shipped contract knows about -> the gate requires the flag
        assert _upgrade_command("9.9.9") == "psamvault-mcp compat --apply --latest --allow-breaking"

    def test_within_the_contract_keeps_the_short_form(self, monkeypatch):
        from mcp_server import version_check as vc

        monkeypatch.setattr(vc, "_contract_newest", lambda: "0.5.3")
        assert vc._upgrade_command("0.5.3") == "psamvault-mcp compat --apply --latest"

    def test_an_unreadable_contract_still_offers_a_working_command(self, monkeypatch):
        """No contract to compare against: err on the side the gate accepts."""
        from mcp_server import version_check as vc

        monkeypatch.setattr(vc, "_contract_newest", lambda: None)
        assert vc._upgrade_command("9.9.9").endswith("--latest --allow-breaking")

    def test_the_printed_notice_carries_the_flag(self, monkeypatch, capsys):
        from mcp_server import version_check as vc

        monkeypatch.setattr(vc, "_get_installed_version", lambda: "0.5.3")
        monkeypatch.setattr(vc, "_get_latest_version", lambda: "9.9.9")
        monkeypatch.setattr(vc, "_get_last_seen_version", lambda: None)
        monkeypatch.setattr(vc, "_set_last_seen_version", lambda v: None)
        vc.check_for_update()
        err = capsys.readouterr().err
        assert "psamvault-mcp compat --apply --latest --allow-breaking" in err, err
