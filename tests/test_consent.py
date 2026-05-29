"""Tests for consent.py — consent dialog and sanitization."""

import sys

import pytest

from mcp_server.consent import (
    ConsentGUIUnavailableError,
    _sanitize_for_display,
    notify_completion,
    request_consent,
)


class TestSanitizeForDisplay:
    def test_strips_control_characters(self):
        """Newlines and other control chars are replaced with spaces."""
        result = _sanitize_for_display("hello\nworld\0evil")
        assert "\n" not in result
        assert "\0" not in result
        assert result == "hello world evil"

    def test_truncates_long_strings(self):
        """Strings over max_length are truncated."""
        long_str = "a" * 300
        result = _sanitize_for_display(long_str, max_length=200)
        assert len(result) == 200

    def test_passes_through_clean_strings(self):
        """Normal strings are unchanged."""
        result = _sanitize_for_display("github.com")
        assert result == "github.com"


class TestRequestConsent:
    def test_raises_when_tkinter_unavailable(self, monkeypatch: pytest.MonkeyPatch):
        """ConsentGUIUnavailableError is raised when tkinter is unavailable."""
        import tkinter

        monkeypatch.setattr(
            tkinter, "Tk",
            lambda: (_ for _ in ()).throw(RuntimeError("no display available")),
        )

        with pytest.raises(ConsentGUIUnavailableError):
            request_consent("github.com", "https://github.com/login", "bearer_token")


class TestNotifyCompletion:
    def test_prints_to_stderr(self, capsys):
        """notify_completion prints a message to stderr."""
        notify_completion("github.com", 200, "https://api.github.com/user")
        captured = capsys.readouterr()
        assert "github.com" in captured.err
        assert "200" in captured.err

    def test_prints_on_error_too(self, capsys):
        """Even 4xx/5xx status codes are reported."""
        notify_completion("example.com", 401, "https://example.com/api")
        captured = capsys.readouterr()
        assert "401" in captured.err