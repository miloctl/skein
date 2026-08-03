"""System-actor names are refused at every identity entry point.

The activity feed shows a system actor's rows to EVERY viewer, so whoever
holds one of those names walks their own writes past the scope rule that
hides a teammate's. One wall in one function is not enough — a roster row can
arrive through a rename or an agent-minting call, and a credential door can
skip ensure_user entirely.
"""

import pytest


def test_the_name_picker_refuses_a_system_name(fresh_db):
    from app.services import users

    for name in ("team", "system", "scheduler", "forge", "Team", "FORGE"):
        with pytest.raises(ValueError, match="reserved for the system"):
            users.ensure_user(name)


def test_minting_an_agent_cannot_plant_a_system_name(fresh_db):
    from app.services import users

    # delegate_task and set_authority both mint an agent from a caller-supplied
    # string, so a human-only check is a hole rather than a wall
    with pytest.raises(ValueError, match="reserved for the system"):
        users.ensure_user("team", kind="agent")


def test_rename_cannot_reach_a_system_name(fresh_db):
    from app.services import users

    users.ensure_user("victim")
    with pytest.raises(ValueError, match="reserved for the system"):
        users.rename_user("victim", "team", actor="victim")


def test_a_key_for_a_system_name_is_refused_at_the_door(client, fresh_db):
    from app import db
    from app.services import api_keys

    # a row that predates the wall: written directly, the way an old database
    # carries it. Its key must not keep writing rows every viewer can read.
    db.execute(
        "INSERT INTO users (name, kind, active, created_at) VALUES ('team', 'human', 1, ?)",
        (db.now(),),
    )
    key = api_keys.create_key("team", "legacy")["key"]
    r = client.post(
        "/api/tasks",
        json={"title": "a system-actor write"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 403
    assert "reserved for the system" in r.json()["detail"]


def test_the_migration_frees_a_reserved_roster_row(fresh_db):
    from app import db

    # 040 runs at startup; a row that arrived before it keeps its work under
    # the freed name rather than being deleted
    rows = db.query("SELECT name FROM users WHERE lower(name) IN ('team','system','forge')")
    assert rows == []
