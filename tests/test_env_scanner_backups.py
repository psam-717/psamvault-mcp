"""Backup copies of .env must not be scanned as a second set of live keys."""

from pathlib import Path

from mcp_server.env_scanner import find_env_files, is_backup_env_file


def test_backup_names_are_recognised_and_live_variants_are_not():
    assert is_backup_env_file(Path(".env")) is False
    assert is_backup_env_file(Path(".env.local")) is False
    assert is_backup_env_file(Path(".env.production")) is False
    assert is_backup_env_file(Path(".env.bak-20260911t114609z")) is True
    assert is_backup_env_file(Path(".env.old")) is True
    assert is_backup_env_file(Path(".env.save")) is True
    assert is_backup_env_file(Path(".env.20260926")) is True


def test_find_env_files_skips_backups_unless_asked(tmp_path: Path):
    (tmp_path / ".env").write_text("LIVE=1\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("LOCAL=1\n", encoding="utf-8")
    (tmp_path / ".env.bak-20260911t114609z").write_text("OLD=1\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("EXAMPLE=1\n", encoding="utf-8")

    found = [path.name for path in find_env_files(str(tmp_path))]
    assert found == [".env", ".env.local"]

    with_backups = [path.name for path in find_env_files(str(tmp_path), include_backups=True)]
    assert ".env.bak-20260911t114609z" in with_backups
    assert ".env.example" not in with_backups
