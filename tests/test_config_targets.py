"""Tests for the config-target adapters (Hermes YAML first).

These write MCP server entries into agent config files. The most important
property: the key VALUE must never be returned by the tool layer, and config
edits must preserve everything else in the file (comments, unrelated keys).
"""

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from pathlib import Path

import pytest

from mcp_server import config_targets

# Hermes config.yaml is comment-rich; the writer must not destroy that.
SAMPLE_CONFIG = """# Hermes Agent configuration
model: deepseek-v4-flash

mcp_servers:
  gbrain:
    command: C:\\Users\\psam\\.bun\\bin\\gbrain.exe
    args:
      - serve
    enabled: true

# ── Security ──────────────────────────────────────────────────────────
redact_secrets: true
"""


class TestWriteHermesMcpServer:
    def test_adds_http_server_entry_and_preserves_comments(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")

        result = config_targets.write_hermes_mcp_server(
            config_path=cfg,
            spec=config_targets.ServerSpec(
                name="render",
                url="https://mcp.render.com/mcp",
                headers={"Authorization": "Bearer rnd_secret123"},
            ),
        )

        assert result["action"] == "added"
        text = cfg.read_text(encoding="utf-8")
        # Original comments + unrelated keys survive.
        assert "# Hermes Agent configuration" in text
        assert "gbrain:" in text
        assert "# ── Security" in text
        assert "redact_secrets: true" in text
        # New entry is present, nested under mcp_servers, correctly quoted.
        assert "render:" in text
        assert "url: https://mcp.render.com/mcp" in text
        assert "Authorization: Bearer rnd_secret123" in text

    def test_dry_run_reports_added_without_writing(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
        before = cfg.read_text(encoding="utf-8")

        result = config_targets.write_hermes_mcp_server(
            config_path=cfg,
            spec=config_targets.ServerSpec(
                name="render",
                url="https://mcp.render.com/mcp",
                headers={"Authorization": "Bearer rnd_secret123"},
            ),
            dry_run=True,
        )

        assert result["action"] == "added"
        assert cfg.read_text(encoding="utf-8") == before

    def test_existing_entry_raises_without_replace(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
        config_targets.write_hermes_mcp_server(
            config_path=cfg,
            spec=config_targets.ServerSpec(name="render", url="https://mcp.render.com/mcp"),
        )
        before = cfg.read_text(encoding="utf-8")

        with pytest.raises(ValueError, match="already exists"):
            config_targets.write_hermes_mcp_server(
                config_path=cfg,
                spec=config_targets.ServerSpec(
                    name="render",
                    url="https://mcp.render.com/mcp",
                    headers={"Authorization": "Bearer rnd_new_secret456"},
                ),
            )

        # Failed write leaves the config untouched.
        assert cfg.read_text(encoding="utf-8") == before

    def test_replace_true_overwrites_and_creates_backup(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
        config_targets.write_hermes_mcp_server(
            config_path=cfg,
            spec=config_targets.ServerSpec(
                name="render",
                url="https://mcp.render.com/mcp",
                headers={"Authorization": "Bearer rnd_old_secret111"},
            ),
        )

        result = config_targets.write_hermes_mcp_server(
            config_path=cfg,
            spec=config_targets.ServerSpec(
                name="render",
                url="https://mcp.render.com/mcp",
                headers={"Authorization": "Bearer rnd_new_secret222"},
            ),
            replace=True,
        )

        assert result["action"] == "replaced"
        assert result["backup_path"] is not None
        backup = cfg.parent / result["backup_path"]
        assert backup.exists()
        # Backup holds the PRE-write content (old secret); live file has the new one.
        assert "rnd_old_secret111" in backup.read_text(encoding="utf-8")
        assert "rnd_new_secret222" not in backup.read_text(encoding="utf-8")
        assert "rnd_new_secret222" in cfg.read_text(encoding="utf-8")
        # Comments survive the replace too.
        assert "# Hermes Agent configuration" in cfg.read_text(encoding="utf-8")

    def test_stdio_shape_writes_command_args_env(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")

        result = config_targets.write_hermes_mcp_server(
            config_path=cfg,
            spec=config_targets.ServerSpec(
                name="mycli",
                command="npx",
                args=["-y", "some-server"],
                env={"SOME_API_KEY": "sk_test_value"},
            ),
        )

        assert result["action"] == "added"
        text = cfg.read_text(encoding="utf-8")
        assert "mycli:" in text
        assert "command: npx" in text
        assert "some-server" in text
        assert "SOME_API_KEY: sk_test_value" in text

    def test_rejects_both_url_and_command(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")

        with pytest.raises(ValueError, match="exactly one transport"):
            config_targets.write_hermes_mcp_server(
                config_path=cfg,
                spec=config_targets.ServerSpec(
                    name="both",
                    url="https://mcp.render.com/mcp",
                    command="npx",
                ),
            )

    def test_rejects_missing_transport(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")

        with pytest.raises(ValueError, match="exactly one transport"):
            config_targets.write_hermes_mcp_server(
                config_path=cfg,
                spec=config_targets.ServerSpec(name="neither"),
            )

    def test_creates_mcp_servers_section_when_missing(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: deepseek-v4-flash\n", encoding="utf-8")

        result = config_targets.write_hermes_mcp_server(
            config_path=cfg,
            spec=config_targets.ServerSpec(
                name="render",
                url="https://mcp.render.com/mcp",
                headers={"Authorization": "Bearer rnd_secret123"},
            ),
        )

        assert result["action"] == "added"
        text = cfg.read_text(encoding="utf-8")
        assert "mcp_servers:" in text
        assert "render:" in text
        assert "model: deepseek-v4-flash" in text

    def test_mcp_servers_not_a_mapping_raises(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("mcp_servers:\n  - not\n  - a\n  - map\n", encoding="utf-8")

        with pytest.raises(ValueError, match="not a mapping"):
            config_targets.write_hermes_mcp_server(
                config_path=cfg,
                spec=config_targets.ServerSpec(
                    name="render", url="https://mcp.render.com/mcp"
                ),
            )


class TestResolveHermesConfigPath:
    def test_uses_hermes_home_env_when_set(self, tmp_path):
        fake_home = tmp_path / "hermes"
        fake_home.mkdir()

        path = config_targets.resolve_hermes_config_path(
            env={"HERMES_HOME": str(fake_home)}
        )

        assert path == fake_home / "config.yaml"

    @pytest.mark.skipif(_os.name != "nt", reason="Windows-specific default path")
    def test_defaults_under_localappdata_on_windows(self, monkeypatch):
        monkeypatch.delenv("HERMES_HOME", raising=False)

        path = config_targets.resolve_hermes_config_path(
            env={"LOCALAPPDATA": "C:/Users/test/AppData/Local"}
        )

        assert path.name == "config.yaml"
        assert "hermes" in path.parts


class TestResolveAgentEnvPath:
    """Where does an agent host read its .env? Explicit path wins; known hosts have defaults."""

    def test_hermes_uses_hermes_home(self):
        path = config_targets.resolve_agent_env_path("hermes", env={"HERMES_HOME": "C:/Users/test/.hermes"})

        assert path.name == ".env"
        assert path.parent.name == ".hermes"

    def test_explicit_env_path_wins_over_the_table(self, tmp_path):
        explicit = tmp_path / "custom.env"

        path = config_targets.resolve_agent_env_path("hermes", env_path=explicit,
                                                     env={"HERMES_HOME": "C:/elsewhere"})

        assert path == explicit

    def test_unknown_agent_without_env_path_fails_closed(self):
        with pytest.raises(ValueError) as exc:
            config_targets.resolve_agent_env_path("groku", env={"HERMES_HOME": "C:/x"})

        message = str(exc.value)
        assert "groku" in message
        assert "env_path" in message, "the error must tell the caller how to proceed"
        assert "hermes" in message, "the error must list the known agents"


class TestWriteEnvVar:
    """Dotenv writes: idempotent update in place, append when new, backup before modifying."""

    def test_appends_a_new_variable(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=1\n", encoding="utf-8")

        result = config_targets.write_env_var(env_file, "TAVILY_API_KEY", "tvly-secret")

        assert result["action"] == "appended"
        text = env_file.read_text(encoding="utf-8")
        assert "EXISTING=1" in text
        assert "TAVILY_API_KEY=tvly-secret" in text

    def test_updates_in_place_instead_of_duplicating(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\nTAVILY_API_KEY=old\nOTHER=2\n", encoding="utf-8")

        result = config_targets.write_env_var(env_file, "TAVILY_API_KEY", "new-value")

        assert result["action"] == "updated"
        text = env_file.read_text(encoding="utf-8")
        assert text.count("TAVILY_API_KEY=") == 1
        assert "TAVILY_API_KEY=new-value" in text
        assert "OTHER=2" in text
        assert "# comment" in text

    def test_export_prefix_is_recognised(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("export TAVILY_API_KEY=old\n", encoding="utf-8")

        result = config_targets.write_env_var(env_file, "TAVILY_API_KEY", "new")

        assert result["action"] == "updated"
        assert env_file.read_text(encoding="utf-8").count("TAVILY_API_KEY=") == 1

    def test_creates_the_file_and_parents_when_missing(self, tmp_path):
        env_file = tmp_path / "nested" / "dir" / ".env"

        result = config_targets.write_env_var(env_file, "KEY", "value")

        assert result["action"] == "appended"
        assert env_file.is_file()
        assert result["backup_path"] is None, "nothing existed to back up"

    def test_backup_is_written_before_modifying_an_existing_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=old\n", encoding="utf-8")

        result = config_targets.write_env_var(env_file, "KEY", "new")

        assert result["backup_path"]
        assert Path(result["backup_path"]).read_text(encoding="utf-8") == "KEY=old\n"

    def test_dry_run_writes_nothing(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=old\n", encoding="utf-8")

        result = config_targets.write_env_var(env_file, "KEY", "new", dry_run=True)

        assert result["action"] == "updated"
        assert result["backup_path"] is None
        assert env_file.read_text(encoding="utf-8") == "KEY=old\n"

    def test_quotes_values_that_need_it_and_rejects_multiline(self, tmp_path):
        env_file = tmp_path / ".env"

        config_targets.write_env_var(env_file, "WITH_SPACE", "a b")
        assert 'WITH_SPACE="a b"' in env_file.read_text(encoding="utf-8")

        with pytest.raises(ValueError):
            config_targets.write_env_var(env_file, "MULTI", "line1\nline2")

    def test_rejects_invalid_variable_names(self, tmp_path):
        with pytest.raises(ValueError):
            config_targets.write_env_var(tmp_path / ".env", "NOT-A-NAME", "x")


class TestWriteToMissingOrEmptyConfig:
    """A destination that does not exist yet, or is empty, is a legitimate target.

    Covers a scratch path passed via ``config_path`` and a freshly installed agent whose
    config.yaml exists but has no content yet. Both used to fail: ``FileNotFoundError`` on the
    read, and ``AttributeError: 'NoneType' object has no attribute 'get'`` on the empty file.
    """

    @staticmethod
    def _spec():
        return config_targets.ServerSpec(
            name="render",
            url="https://mcp.render.com/mcp",
            headers={"Authorization": "Bearer rnd_secret123"},
        )

    def test_creates_the_file_when_the_target_does_not_exist(self, tmp_path):
        cfg = tmp_path / "new-config.yaml"  # deliberately never created

        result = config_targets.write_hermes_mcp_server(config_path=cfg, spec=self._spec())

        assert result["action"] == "added"
        assert cfg.is_file(), "a missing target config should be created, not rejected"
        text = cfg.read_text(encoding="utf-8")
        assert "mcp_servers:" in text
        assert "render:" in text
        assert result["backup_path"] is None, "nothing existed to back up"

    def test_empty_config_file_is_treated_as_an_empty_mapping(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("", encoding="utf-8")

        result = config_targets.write_hermes_mcp_server(config_path=cfg, spec=self._spec())

        assert result["action"] == "added"
        assert "render:" in cfg.read_text(encoding="utf-8")

    def test_creates_missing_parent_directories(self, tmp_path):
        cfg = tmp_path / "nested" / "deeper" / "config.yaml"

        config_targets.write_hermes_mcp_server(config_path=cfg, spec=self._spec())

        assert cfg.is_file()






