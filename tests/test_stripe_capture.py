"""Tests for stripe_capture.py — Stripe Projects credential capture logic."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mcp_server.stripe_capture import (
    _is_known_non_secret,
    _matches_stripe_or_known,
    _parse_env_file,
    get_env_file_path,
    capture_stripe_credentials,
)


# ── Unit tests: _is_known_non_secret ───────────────────────────────────────


class TestIsKnownNonSecret:
    def test_node_env_is_not_secret(self):
        assert _is_known_non_secret("NODE_ENV") is True

    def test_port_is_not_secret(self):
        assert _is_known_non_secret("PORT") is True

    def test_debug_is_not_secret(self):
        assert _is_known_non_secret("DEBUG") is True

    def test_stripe_project_name_not_secret(self):
        assert _is_known_non_secret("STRIPE_PROJECT_NAME") is True

    def test_api_key_is_secret(self):
        assert _is_known_non_secret("OPENAI_API_KEY") is False

    def test_database_url_is_secret(self):
        # *_DATABASE_URL is caught by env_scanner patterns, not in non-secrets
        assert _is_known_non_secret("NEON_DATABASE_URL") is False

    def test_case_insensitive(self):
        assert _is_known_non_secret("node_env") is True
        assert _is_known_non_secret("Node_Env") is True


# ── Unit tests: _matches_stripe_or_known ───────────────────────────────────


class TestMatchesStripeOrKnown:
    def test_matches_api_key_pattern(self):
        assert _matches_stripe_or_known("OPENAI_API_KEY") is True

    def test_matches_secret_pattern(self):
        assert _matches_stripe_or_known("API_SECRET") is True

    def test_matches_token_pattern(self):
        assert _matches_stripe_or_known("GITHUB_TOKEN") is True

    def test_matches_database_url_pattern(self):
        assert _matches_stripe_or_known("NEON_DATABASE_URL") is True

    def test_matches_password_pattern(self):
        assert _matches_stripe_or_known("DB_PASSWORD") is True

    def test_does_not_match_plain_var(self):
        assert _matches_stripe_or_known("NODE_ENV") is False

    def test_does_not_match_filename(self):
        assert _matches_stripe_or_known("FILENAME") is False


# ── Unit tests: _parse_env_file ────────────────────────────────────────────


class TestParseEnvFile:
    def test_parses_simple_env(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value\nPORT=8080\nSECRET=abc123\n")
        result = _parse_env_file(env_file)
        assert len(result) == 3
        assert result[0] == {"key": "KEY", "value": "value", "file": ".env", "index": 0}
        assert result[1] == {"key": "PORT", "value": "8080", "file": ".env", "index": 1}
        assert result[2] == {"key": "SECRET", "value": "abc123", "file": ".env", "index": 2}

    def test_skips_comments_and_blanks(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("# This is a comment\n\nKEY=value\n# Another comment\nPORT=8080\n")
        result = _parse_env_file(env_file)
        assert len(result) == 2

    def test_strips_quotes(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text('KEY="quoted_value"\nSIMPLE=\'single_quoted\'\n')
        result = _parse_env_file(env_file)
        assert result[0]["value"] == "quoted_value"
        assert result[1]["value"] == "single_quoted"

    def test_handles_export_prefix(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("export OPENAI_API_KEY=sk-abc\n")
        result = _parse_env_file(env_file)
        assert result[0]["key"] == "OPENAI_API_KEY"

    def test_empty_file(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        assert _parse_env_file(env_file) == []

    def test_missing_file(self, tmp_path: Path):
        env_file = tmp_path / "nonexistent.env"
        assert _parse_env_file(env_file) == []


# ── Unit tests: get_env_file_path ──────────────────────────────────────────


class TestGetEnvFilePath:
    def test_finds_dot_env(self, tmp_path: Path):
        (tmp_path / ".env").write_text("KEY=val")
        result = get_env_file_path(str(tmp_path))
        assert result == tmp_path / ".env"

    def test_finds_dot_env_local(self, tmp_path: Path):
        (tmp_path / ".env.local").write_text("KEY=val")
        result = get_env_file_path(str(tmp_path))
        assert result == tmp_path / ".env.local"

    def test_env_takes_priority_over_env_local(self, tmp_path: Path):
        (tmp_path / ".env").write_text("KEY=val")
        (tmp_path / ".env.local").write_text("KEY2=val2")
        result = get_env_file_path(str(tmp_path))
        assert result == tmp_path / ".env"

    def test_falls_back_to_any_env_file(self, tmp_path: Path):
        (tmp_path / ".env.prod").write_text("KEY=val")
        result = get_env_file_path(str(tmp_path))
        assert result == tmp_path / ".env.prod"

    def test_skips_example_files(self, tmp_path: Path):
        (tmp_path / ".env.example").write_text("KEY=val")
        (tmp_path / ".env").write_text("REAL=val")
        result = get_env_file_path(str(tmp_path))
        assert result == tmp_path / ".env"

    def test_returns_none_if_no_env(self, tmp_path: Path):
        assert get_env_file_path(str(tmp_path)) is None

    def test_returns_none_if_dir_not_found(self):
        assert get_env_file_path("/nonexistent/path/12345") is None


# ── Integration tests: capture_stripe_credentials ──────────────────────────


class TestCaptureStripeCredentials:
    """Tests for capture_stripe_credentials with mocked subprocess and API calls.

    We patch:
    - asyncio.create_subprocess_exec to simulate `stripe projects env --pull`
    - mcp_server.api_client.add_api_key_entry to avoid real HTTP calls
    - mcp_server.session.get_vek / get_access_token to avoid keychain access
    """

    @pytest.fixture
    def mock_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Set up a temporary project dir with a .env file and mock deps."""
        # Create .env with mixed content
        env_file = tmp_path / ".env"
        env_file.write_text(
            "NODE_ENV=production\n"
            "OPENAI_API_KEY=***\n"
            "PORT=3000\n"
            "NEON_DATABASE_URL=postgresql://user:pass@neon.tech/db\n"
            "DEBUG=false\n"
        )

        # Mock session dependencies
        monkeypatch.setattr(
            "mcp_server.session.get_vek",
            lambda: bytes(range(32)),
        )
        monkeypatch.setattr(
            "mcp_server.session.get_access_token",
            lambda: "test_token",
        )
        monkeypatch.setattr(
            "mcp_server.session.is_logged_in",
            lambda: True,
        )

        # Mock API client add_api_key_entry
        mock_add = AsyncMock(return_value={"name": "stripe/test/KEY", "success": True})
        monkeypatch.setattr(
            "mcp_server.api_client.add_api_key_entry",
            mock_add,
        )

        yield tmp_path, env_file

    @pytest.fixture
    def mock_stripe_cli(self, monkeypatch: pytest.MonkeyPatch):
        """Mock asyncio.create_subprocess_exec to simulate stripe CLI."""

        async def _mock_create_subprocess_exec(*args, **kwargs):
            """Return a mock process that succeeds."""
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(
                return_value=(b"Environment variables pulled successfully.", b"")
            )
            return mock_proc

        monkeypatch.setattr(
            "asyncio.create_subprocess_exec",
            _mock_create_subprocess_exec,
        )

    @pytest.mark.asyncio
    async def test_dry_run_preview(self, mock_env, mock_stripe_cli):
        """dry_run=True returns preview without modifying anything."""
        tmp_path, env_file = mock_env
        result = await capture_stripe_credentials(
            provider="neon",
            project_dir=str(tmp_path),
            dry_run=True,
        )

        assert result["success"] is True
        assert result["dry_run"] is True
        assert result["provider"] == "neon"
        assert result["captured_count"] == 2  # OPENAI_API_KEY + NEON_DATABASE_URL
        assert result["env_file"] == str(env_file)

        # Check env file was NOT modified
        content = env_file.read_text()
        assert "psamvault:" not in content

        # Check the preview contains correct captured items
        captured_keys = {c["key"] for c in result["captured"]}
        assert "OPENAI_API_KEY" in captured_keys
        assert "NEON_DATABASE_URL" in captured_keys
        assert "NODE_ENV" not in captured_keys  # non-secret
        assert "PORT" not in captured_keys  # non-secret
        assert "DEBUG" not in captured_keys  # non-secret

    @pytest.mark.asyncio
    async def test_actual_capture(self, mock_env, mock_stripe_cli):
        """Non-dry-run captures, stores, and replaces secrets."""
        tmp_path, env_file = mock_env
        result = await capture_stripe_credentials(
            provider="neon",
            project_dir=str(tmp_path),
            dry_run=False,
        )

        assert result["success"] is True
        assert result["dry_run"] is False
        assert result["captured_count"] == 2
        assert "psamvault" in result["message"]

        # Check env file WAS modified
        content = env_file.read_text()
        assert "psamvault:OPENAI_API_KEY" in content
        assert "psamvault:NEON_DATABASE_URL" in content
        assert "NODE_ENV=production" in content  # untouched
        assert "PORT=3000" in content  # untouched

        # Check API was called to store each key
        from mcp_server import api_client

        api_client.add_api_key_entry.assert_called()

    @pytest.mark.asyncio
    async def test_stripe_cli_not_found(self, mock_env, monkeypatch):
        """When stripe CLI is missing, returns a descriptive error."""
        monkeypatch.setattr(
            "asyncio.create_subprocess_exec",
            AsyncMock(side_effect=FileNotFoundError("stripe not found")),
        )

        result = await capture_stripe_credentials(
            provider="neon",
            project_dir=str(mock_env[0]),
        )

        assert result["success"] is False
        assert "Stripe CLI not found" in result["message"]

    @pytest.mark.asyncio
    async def test_stripe_cli_fails(self, mock_env, monkeypatch):
        """When stripe CLI exits with non-zero, returns error."""
        async def _mock_failing_process(*args, **kwargs):
            mock_proc = AsyncMock()
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(
                return_value=(b"", b"Error: not a Stripe project")
            )
            return mock_proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", _mock_failing_process)

        result = await capture_stripe_credentials(
            provider="neon",
            project_dir=str(mock_env[0]),
        )

        assert result["success"] is False
        assert "exited with code 1" in result["message"]

    @pytest.mark.asyncio
    async def test_no_env_file_after_pull(self, tmp_path: Path, mock_stripe_cli, monkeypatch):
        """When no .env file exists after pull, returns descriptive message."""
        monkeypatch.setattr(
            "mcp_server.session.get_vek",
            lambda: bytes(range(32)),
        )
        monkeypatch.setattr(
            "mcp_server.session.get_access_token",
            lambda: "test_token",
        )

        result = await capture_stripe_credentials(
            provider="neon",
            project_dir=str(tmp_path),
        )

        assert result["success"] is False
        assert "No .env file found" in result["message"]

    @pytest.mark.asyncio
    async def test_already_protected_skipped(self, tmp_path: Path, mock_stripe_cli, monkeypatch):
        """Entries already with psamvault: prefix are skipped."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "OPENAI_API_KEY=psamvault:OPENAI_API_KEY\n"
            "NEON_DATABASE_URL=postgresql://user:pass@neon.tech/db\n"
        )

        monkeypatch.setattr("mcp_server.session.get_vek", lambda: bytes(range(32)))
        monkeypatch.setattr("mcp_server.session.get_access_token", lambda: "test_token")

        result = await capture_stripe_credentials(
            provider="neon",
            project_dir=str(tmp_path),
            dry_run=True,
        )

        # Only NEON_DATABASE_URL should be captured (OPENAI_API_KEY already protected)
        captured_keys = {c["key"] for c in result["captured"]}
        assert "NEON_DATABASE_URL" in captured_keys
        assert "OPENAI_API_KEY" not in captured_keys
        assert result["captured_count"] == 1


# ── Tests for not-logged-in guard in tools.py wrapper ────────────────────


class TestCaptureStripeCredentialsToolGuard:
    """Tests the auth guard in tools.py's capture_stripe_credentials wrapper."""

    @pytest.mark.asyncio
    async def test_not_logged_in(self, monkeypatch):
        """When not logged in, returns error dict."""
        import mcp_server.tools
        monkeypatch.setattr(mcp_server.tools, "is_logged_in", lambda: False)

        from mcp_server.tools import capture_stripe_credentials as tool_fn

        result = await tool_fn(provider="neon")
        assert "error" in result
        assert "Not logged in" in result["error"]

    @pytest.mark.asyncio
    async def test_not_logged_in_no_provider(self):
        """Missing provider argument should raise TypeError."""
        from mcp_server.tools import capture_stripe_credentials as tool_fn

        with pytest.raises(TypeError):
            # missing required 'provider' argument
            await tool_fn()  # type: ignore[call-arg]
