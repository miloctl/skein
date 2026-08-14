"""The operator CLI for legacy identity conflicts.

The exit codes are the contract a runbook or monitoring hook keys on:
0 = clean or repaired, 1 = conflicts found or a refusal, 2 = bad usage.
Refusals and instructions go to stderr so a piped stdout stays parseable.
The rename path is pinned in test_extension_composition.py; this file covers
the inspection mode, the two claim commands, and the exit codes.
"""

import sys

import pytest

from app import identity_audit


def _legacy_agent(fresh_db, name):
    # the exact state migration 018 leaves a pre-018 agent row in: kind
    # 'agent', identity_owner 'generic-agent', no modern ensure_* involved
    fresh_db.execute(
        "INSERT INTO users (name, kind, identity_owner, created_at)"
        " VALUES (?, 'agent', 'generic-agent', ?)",
        (name, fresh_db.now()),
    )


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["identity_audit", *argv])
    identity_audit.main()


def test_a_clean_roster_reports_clean_and_exits_zero(fresh_db, monkeypatch, capsys):
    _run(monkeypatch)
    out = capsys.readouterr()
    assert "No conflicting identity ownership found." in out.out
    assert out.err == ""


def test_conflicts_list_each_group_and_exit_one(fresh_db, monkeypatch, capsys):
    for name in ("Casey", "CASEY"):
        fresh_db.execute(
            "INSERT INTO users (name, kind, created_at) VALUES (?, 'human', ?)",
            (name, fresh_db.now()),
        )
    with pytest.raises(SystemExit) as stop:
        _run(monkeypatch)
    assert stop.value.code == 1
    out = capsys.readouterr()
    assert "folded-roster" in out.out
    assert "Casey" in out.out and "CASEY" in out.out
    # the merge prohibition is the instruction, and it must not pollute stdout
    assert "Do not merge human and agent rows." in out.err
    assert "Do not merge" not in out.out


def test_claim_content_assigns_a_configured_slug(fresh_db, monkeypatch, capsys):
    from app.services import users

    _legacy_agent(fresh_db, "backend-architect")
    _run(monkeypatch, "claim-content", "backend-architect")
    assert "Assigned backend-architect to configured content." in capsys.readouterr().out
    row = fresh_db.query_one("SELECT identity_owner FROM users WHERE name = 'backend-architect'")
    assert row["identity_owner"] == users.CONTENT_OWNER
    assert fresh_db.query_one("SELECT 1 AS x FROM activity WHERE action = 'claim_content_identity'")


def test_claim_content_refuses_a_slug_that_is_not_content(fresh_db, monkeypatch, capsys):
    _legacy_agent(fresh_db, "not-a-persona")
    with pytest.raises(SystemExit) as stop:
        _run(monkeypatch, "claim-content", "not-a-persona")
    assert stop.value.code == 1
    assert "content claim refused" in capsys.readouterr().err
    row = fresh_db.query_one("SELECT identity_owner FROM users WHERE name = 'not-a-persona'")
    assert row["identity_owner"] == "generic-agent"


def test_claim_machine_assigns_a_generic_row_to_a_service(fresh_db, monkeypatch, capsys):
    _legacy_agent(fresh_db, "relay")
    _run(monkeypatch, "claim-machine", "relay", "service:relay")
    assert "Assigned relay to service:relay." in capsys.readouterr().out
    row = fresh_db.query_one("SELECT identity_owner FROM users WHERE name = 'relay'")
    assert row["identity_owner"] == "service:relay"


def test_claim_machine_refuses_a_malformed_owner(fresh_db, monkeypatch, capsys):
    _legacy_agent(fresh_db, "relay")
    with pytest.raises(SystemExit) as stop:
        _run(monkeypatch, "claim-machine", "relay", "sidecar")
    assert stop.value.code == 1
    assert "machine claim refused" in capsys.readouterr().err
    row = fresh_db.query_one("SELECT identity_owner FROM users WHERE name = 'relay'")
    assert row["identity_owner"] == "generic-agent"


def test_unknown_arguments_print_usage_and_exit_two(fresh_db, monkeypatch, capsys):
    with pytest.raises(SystemExit) as stop:
        _run(monkeypatch, "merge", "a", "b")
    assert stop.value.code == 2
    assert "usage: python -m app.identity_audit" in capsys.readouterr().err
