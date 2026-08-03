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


def test_a_case_variant_of_the_other_kind_is_refused(fresh_db):
    from app.services import users

    users.ensure_user("scout", kind="agent")
    # one name, one identity: a human `Scout` beside the agent `scout` makes
    # every identity question depend on which row a query returns first
    with pytest.raises(ValueError, match="differs only by case"):
        users.ensure_user("Scout")


def test_a_name_that_merely_renders_as_a_system_actor_is_refused(fresh_db):
    from app.services import users

    # NFKC folds the fullwidth form, and category Cf is stripped: both render
    # as `team` in every surface
    for lookalike in ("\uff34\uff25\uff21\uff2d", "team\u200b", "  team  "):
        with pytest.raises(ValueError, match="reserved for the system"):
            users.ensure_user(lookalike)


def test_the_reads_a_system_name_can_make_are_refused_too(client, fresh_db):
    # the trusted-header READ path returns before ensure_user, so the wall
    # has to be applied at the door
    assert client.get("/api/briefing", headers={"X-User": "forge"}).status_code == 403
    assert client.get("/api/briefing", headers={"X-User": "TEAM"}).status_code == 403


def test_an_agent_is_refused_at_the_door_whatever_its_capitalization(client, fresh_db):
    from app.services import users

    users.ensure_user("scout", kind="agent")
    for spelling in ("scout", "Scout", "SCOUT", "sCoUt"):
        r = client.get("/api/briefing", headers={"X-User": spelling})
        assert r.status_code == 403, spelling
