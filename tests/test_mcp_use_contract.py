"""The decision, the file loader, and the audit row, matched to the CLI.

psamvault-mcp does not import psamvault-cli. These tests pin the copy:
``mcp_use.decide``, ``should_ask``, ``finalize``, ``policy.mcp_plan``, and
``audit.record_mcp_use``.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

audit = None
mcp_use = None
policy = None


@pytest.fixture(autouse=True)
def _load_contract_modules():
    """Import the CLI-shaped modules. On main they are absent, so each test errors here."""
    global audit, mcp_use, policy
    from mcp_server import audit as audit_mod
    from mcp_server import mcp_use as mcp_use_mod
    from mcp_server import policy as policy_mod

    audit, mcp_use, policy = audit_mod, mcp_use_mod, policy_mod


def write_policy(tmp_path: Path, body, name: str = "policy.json") -> Path:
    target = tmp_path / name
    target.write_text(json.dumps(body) if not isinstance(body, str) else body, encoding="utf-8")
    return target


def test_the_consuming_tools_are_the_contract():
    assert mcp_use.TOOLS == (
        "use_credential",
        "run_with_credential",
        "browser_login",
        "export_key_to_mcp_config",
        "export_key_to_env_file",
        "verify_api_key",
    )
    assert "get_username_for_site" not in mcp_use.TOOLS
    assert "scan_and_protect" not in mcp_use.TOOLS
    assert "list_api_keys" not in mcp_use.TOOLS
    assert mcp_use.DISCOVERY >= {
        "get_version",
        "search_vault_tools",
        "list_vault_sites",
        "check_credential_exists",
        "get_username_for_site",
        "list_api_keys",
        "scan_and_protect",
    }


def test_absent_file_is_audit_and_does_not_refuse(tmp_path):
    loaded = policy.load(tmp_path / "nope.json")
    assert loaded.mcp_use == "audit"
    assert loaded.mcp_use_implicit is True
    assert loaded.entry_tools == {}
    assert loaded.entry_ask == {}
    plan = policy.mcp_plan(loaded, "ledger-key", "use_credential")
    assert (plan.decision, plan.reason, plan.ask) == ("allow", "not-listed", False)
    assert plan.notice == mcp_use.ABSENT_DEFAULT_NOTICE


def test_older_file_without_mcp_keys_is_not_rewritten(tmp_path):
    target = write_policy(
        tmp_path,
        {
            "reveal": "strict",
            "allow_entries": ["github.com"],
            "entries": {"ledger-key": {"allow_hosts": ["api.ledger.example"], "allow_commands": ["curl"]}},
        },
    )
    if os.name == "posix":
        os.chmod(target, 0o600)
    before = target.read_bytes()
    loaded = policy.load(target)
    assert loaded.mcp_use == "audit"
    assert loaded.mcp_use_implicit is True
    assert loaded.entry_tools == {}
    assert loaded.warnings == []
    assert target.read_bytes() == before
    plan = policy.mcp_plan(loaded, "LEDGER-KEY", "browser_login")
    assert (plan.decision, plan.reason, plan.ask) == ("allow", "not-listed", False)


def test_unknown_mcp_use_falls_back_to_audit_without_the_flip_notice(tmp_path):
    loaded = policy.load(write_policy(tmp_path, {"mcp_use": "enforced"}))
    assert loaded.mcp_use == "audit"
    assert loaded.mcp_use_implicit is False
    assert policy.mcp_use_notice(loaded) is None
    assert any("unknown mcp_use 'enforced'" in warning for warning in loaded.warnings)


def test_null_mcp_use_is_the_absent_default(tmp_path):
    loaded = policy.load(write_policy(tmp_path, {"mcp_use": None}))
    assert loaded.mcp_use == "audit"
    assert loaded.mcp_use_implicit is True
    assert policy.mcp_use_notice(loaded) == mcp_use.ABSENT_DEFAULT_NOTICE


def test_a_non_object_file_is_audit_and_does_not_raise(tmp_path):
    loaded = policy.load(write_policy(tmp_path, "[1, 2]"))
    assert loaded.mcp_use == "audit"
    assert loaded.mcp_use_implicit is True
    assert loaded.entry_tools == {}
    assert loaded.warnings


def test_allow_tools_are_normalised_and_bad_tokens_are_reported(tmp_path):
    target = write_policy(
        tmp_path,
        {
            "mcp_use": "enforce",
            "entries": {
                "ledger-key": {
                    "allow_hosts": ["api.ledger.example"],
                    "allow_commands": ["curl"],
                    "allow_tools": ["Use_Credential", "use_credential", "list_api_keys", "not a tool", ""],
                    "ask_each_time": "yes",
                }
            },
        },
    )
    before = target.read_bytes()
    loaded = policy.load(target)
    assert loaded.mcp_use == "enforce"
    assert loaded.entry_tools["ledger-key"] == ["use_credential", "list_api_keys"]
    assert loaded.entry_ask["ledger-key"] is False
    text = "\n".join(loaded.warnings)
    assert "list_api_keys" in text
    assert "not a tool" in text
    assert "ask_each_time" in text
    assert target.read_bytes() == before
    plan = policy.mcp_plan(loaded, "ledger-key", "use_credential")
    assert (plan.decision, plan.reason, plan.ask) == ("allow", "listed", False)


def test_allow_tools_of_the_wrong_shape_allows_nothing(tmp_path):
    loaded = policy.load(
        write_policy(tmp_path, {"mcp_use": "enforce", "entries": {"ledger-key": {"allow_tools": "use_credential"}}})
    )
    assert loaded.entry_tools["ledger-key"] == []
    assert any("allow_tools must be a list" in warning for warning in loaded.warnings)
    plan = policy.mcp_plan(loaded, "ledger-key", "use_credential")
    assert (plan.decision, plan.reason) == ("deny", "not-listed")


def test_a_future_tool_name_is_kept_and_matches(tmp_path):
    loaded = policy.load(
        write_policy(
            tmp_path,
            {"mcp_use": "enforce", "entries": {"ledger-key": {"allow_tools": ["export_future_tool"]}}},
        )
    )
    assert "export_future_tool" in loaded.entry_tools["ledger-key"]
    assert any("export_future_tool" in warning for warning in loaded.warnings)
    plan = policy.mcp_plan(loaded, "ledger-key", "export_future_tool")
    assert (plan.decision, plan.reason) == ("allow", "listed")


@pytest.mark.parametrize(
    ("mode", "tools", "tool", "decision", "reason"),
    [
        ("enforce", ["use_credential"], "use_credential", "allow", "listed"),
        ("enforce", ["use_credential"], "browser_login", "deny", "not-listed"),
        ("enforce", [], "use_credential", "deny", "not-listed"),
        ("audit", [], "export_key_to_env_file", "allow", "not-listed"),
        ("audit", ["verify_api_key"], "verify_api_key", "allow", "listed"),
        ("off", [], "run_with_credential", "allow", "mcp-use-off"),
        ("off", ["use_credential"], "browser_login", "allow", "mcp-use-off"),
        ("nope", ["use_credential"], "use_credential", "allow", "listed"),
        ("nope", [], "use_credential", "allow", "not-listed"),
    ],
)
def test_decide_matches_the_rollout_matrix(mode, tools, tool, decision, reason):
    assert mcp_use.decide(mode, tools, tool) == (decision, reason)


def test_should_ask_is_only_enforce_and_an_allowed_use():
    assert mcp_use.should_ask("audit", "allow", ask_each_time=True) is False
    assert mcp_use.should_ask("off", "allow", ask_each_time=True) is False
    assert mcp_use.should_ask("enforce", "deny", ask_each_time=True) is False
    assert mcp_use.should_ask("enforce", "allow", ask_each_time=False) is False
    assert mcp_use.should_ask("enforce", "allow", ask_each_time=True) is True


def test_finalize_applies_the_answer_without_hiding_a_denial():
    assert mcp_use.finalize("allow", "listed", ask=True, accepted=True) == ("allow", "listed")
    assert mcp_use.finalize("allow", "listed", ask=True, accepted=False) == ("deny", "ask-declined")
    assert mcp_use.finalize("allow", "listed", ask=True, accepted=None) == ("deny", "ask-unanswered")
    assert mcp_use.finalize("deny", "not-listed", ask=True, accepted=True) == ("deny", "not-listed")
    assert mcp_use.finalize("allow", "mcp-use-off", ask=False, accepted=False) == ("allow", "mcp-use-off")


def test_a_leaf_name_unions_tools_and_ask_flags(tmp_path):
    loaded = policy.load(
        write_policy(
            tmp_path,
            {
                "mcp_use": "enforce",
                "entries": {
                    " KEY ": {"allow_tools": ["use_credential"], "ask_each_time": False},
                    "project/.env/KEY": {"allow_tools": ["browser_login"], "ask_each_time": True},
                },
            },
        )
    )
    listed = policy.mcp_plan(loaded, "project/.env/KEY", "use_credential")
    assert (listed.decision, listed.reason, listed.ask) == ("allow", "listed", True)
    other = policy.mcp_plan(loaded, "project/.env/KEY", "browser_login")
    assert (other.decision, other.reason) == ("allow", "listed")
    refused = policy.mcp_plan(loaded, "project/.env/KEY", "run_with_credential")
    assert (refused.decision, refused.reason, refused.ask) == ("deny", "not-listed", False)


def test_only_json_true_sets_ask_each_time(tmp_path):
    loaded = policy.load(
        write_policy(
            tmp_path,
            {
                "entries": {
                    "a": {"ask_each_time": True},
                    "b": {"ask_each_time": False},
                    "c": {"ask_each_time": 1},
                    "d": {},
                }
            },
        )
    )
    assert loaded.entry_ask["a"] is True
    assert loaded.entry_ask["b"] is False
    assert loaded.entry_ask["c"] is False
    assert "d" not in loaded.entry_ask
    assert policy.mcp_plan(loaded, "a", "use_credential").ask is False  # mode is audit


def test_explicit_audit_has_no_notice(tmp_path):
    loaded = policy.load(write_policy(tmp_path, {"mcp_use": "audit"}))
    assert loaded.mcp_use_implicit is False
    assert policy.mcp_plan(loaded, "ledger-key", "use_credential").notice is None


def test_mcp_audit_row_shape_mode_and_rotation(tmp_path, monkeypatch):
    target = tmp_path / "sub" / "audit.jsonl"
    audit.record_mcp_use(
        tool="use_credential",
        entry="ledger-key",
        decision="deny",
        reason="not-listed",
        mcp_use="enforce",
        ask=False,
        path=target,
    )
    raw = target.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert '", "' not in raw
    assert '": "' not in raw
    row = audit.read(target)[0]
    assert row["command"] == "mcp use_credential"
    assert row["entry"] == "ledger-key"
    assert row["tool"] == "use_credential"
    assert row["decision"] == "deny"
    assert row["reason"] == "not-listed"
    assert row["mcp_use"] == "enforce"
    assert row["ask"] is False
    assert row["caller"] == "mcp"
    assert row["signals"] == []
    assert row["tty"] is False
    assert row["policy"] is None
    assert row["token_id"] is None
    assert "host" not in row
    assert row["pid"] == os.getpid()
    assert row["ppid"] == os.getppid()
    if os.name == "posix":
        assert target.stat().st_mode & 0o077 == 0
        assert target.parent.stat().st_mode & 0o077 == 0
        assert stat.S_IMODE(target.parent.stat().st_mode) & 0o700 == 0o700

    monkeypatch.setattr(audit, "MAX_AUDIT_BYTES", len(raw.encode("utf-8")))
    audit.record_mcp_use(
        tool="browser_login",
        entry="github.com",
        decision="allow",
        reason="listed",
        mcp_use="audit",
        path=target,
    )
    rotated = target.parent / "audit.jsonl.1"
    assert rotated.exists()
    assert "use_credential" in rotated.read_text(encoding="utf-8")
    assert audit.read(target)[0]["tool"] == "browser_login"
    # A second rotation replaces .1 rather than growing a .2.
    monkeypatch.setattr(audit, "MAX_AUDIT_BYTES", 1)
    audit.record_mcp_use(
        tool="verify_api_key",
        entry="github.com",
        decision="allow",
        reason="listed",
        mcp_use="audit",
        path=target,
    )
    assert not (target.parent / "audit.jsonl.2").exists()
    assert "browser_login" in rotated.read_text(encoding="utf-8")


def test_a_row_has_nowhere_to_put_a_secret(tmp_path):
    target = tmp_path / "audit.jsonl"
    secret = "SUPERSECRET-VALUE-should-never-print"
    audit.record_mcp_use(
        tool=secret,
        entry="ledger-key",
        decision="allow",
        reason=secret,
        mcp_use="audit",
        path=target,
    )
    blob = target.read_text(encoding="utf-8") if target.exists() else ""
    assert secret not in blob
    audit.record_mcp_use(
        tool="use_credential",
        entry=secret,
        decision="maybe",
        reason="listed",
        mcp_use="audit",
        path=target,
    )
    blob = target.read_text(encoding="utf-8") if target.exists() else ""
    assert secret not in blob
    assert audit.read(target) == []


def test_a_write_failure_is_swallowed(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x", encoding="utf-8")
    audit.record_mcp_use(
        tool="use_credential",
        entry="ledger-key",
        decision="deny",
        reason="not-listed",
        mcp_use="enforce",
        path=blocked / "audit.jsonl",
    )


def test_interpret_elicitation_answers():
    from mcp_server import mcp_gate

    assert mcp_gate.accepted_from_action("accept", {"allow": True}) is True
    assert mcp_gate.accepted_from_action("accept", {"allow": False}) is False
    assert mcp_gate.accepted_from_action("decline", {"allow": True}) is False
    assert mcp_gate.accepted_from_action("cancel", None) is None
    assert mcp_gate.accepted_from_action("accept", None) is None
    assert mcp_gate.accepted_from_action("accept", {}) is None


def test_form_support_fails_closed_without_the_capability():
    from types import SimpleNamespace

    from mcp_server import mcp_gate

    assert mcp_gate.form_supported(None) is False
    bare = SimpleNamespace(client_params=None)
    assert mcp_gate.form_supported(bare) is False
    url_only = SimpleNamespace(
        client_params=SimpleNamespace(
            capabilities=SimpleNamespace(elicitation=SimpleNamespace(form=None, url=object()))
        )
    )
    assert mcp_gate.form_supported(url_only) is False
    legacy = SimpleNamespace(
        client_params=SimpleNamespace(
            capabilities=SimpleNamespace(elicitation=SimpleNamespace(form=None, url=None))
        )
    )
    assert mcp_gate.form_supported(legacy) is True
    form = SimpleNamespace(
        client_params=SimpleNamespace(
            capabilities=SimpleNamespace(elicitation=SimpleNamespace(form=object(), url=None))
        )
    )
    assert mcp_gate.form_supported(form) is True
