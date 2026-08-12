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
    for name in ("team", "agent", "ci", "mcp"):
        with pytest.raises(ValueError, match="reserved for the system"):
            users.ensure_user(name, kind="agent")


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


def test_a_stuck_row_is_named_at_boot_and_moved_by_rename(fresh_db):
    from app import db
    from app.services import users

    # migration 040 is deliberately a no-op: moving a person is rename_user's
    # job, because it knows all 47 attribution columns and the private notes
    # DB that SQL cannot reach. Boot names the row instead.
    db.execute(
        "INSERT INTO users (name, kind, active, created_at) VALUES ('team', 'human', 1, ?)",
        (db.now(),),
    )
    assert users.reserved_name_rows() == ["team"]
    users.rename_user("team", "tamsin", actor="team")
    assert users.reserved_name_rows() == []


def test_a_case_variant_of_the_other_kind_is_refused(fresh_db):
    from app.services import users

    users.ensure_user("scout", kind="agent")
    # one name, one identity: a human `Scout` beside the agent `scout` makes
    # every identity question depend on which row a query returns first
    with pytest.raises(ValueError, match="differs only by case"):
        users.ensure_user("Scout")


def test_a_case_variant_of_the_same_kind_is_refused_too(fresh_db):
    from app.services import users

    users.ensure_user("scout", kind="agent")
    users.ensure_user("mira")
    users.ensure_user("bob")
    # same kind is not safer: authority_level and trust key on the EXACT name,
    # so a second agent row that folds onto the first answers to neither's
    # kill switch — and a second human row splits one person's notes and
    # preferences across two accounts
    with pytest.raises(ValueError, match="differs only by case"):
        users.ensure_user("SCOUT", kind="agent")
    with pytest.raises(ValueError, match="differs only by case"):
        users.ensure_user("MIRA")
    with pytest.raises(ValueError, match="differs only by case"):
        users.rename_user("bob", "MIRA", actor="bob")


def test_a_person_can_still_recase_their_own_name(fresh_db):
    from app.services import users

    users.ensure_user("mira")
    # the guard skips the row being moved, or fixing your own capitalization
    # would be the one rename it refuses
    users.rename_user("mira", "Mira", actor="mira")
    names = [r["name"] for r in users.list_users()]
    assert "Mira" in names and "mira" not in names


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


def test_rename_cannot_brick_an_account_on_an_agents_name(fresh_db):
    from app.services import users

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    # renaming a human onto an agent's name in ANY capitalization locks them
    # out of every door, including this route — with no self-service recovery
    with pytest.raises(ValueError, match="differs only by case"):
        users.rename_user("mira", "Scout", actor="mira")
    assert users.is_agent("mira") is False


def test_identity_folding_agrees_with_the_resolver(fresh_db):
    from app import db
    from app.services import users

    # sqlite's lower() is ASCII-only, so a SQL-folded wall reads these as two
    # names while resolve_teammate reads them as one
    db.execute(
        "INSERT INTO users (name, kind, active, created_at) VALUES ('scoüt', 'agent', 1, ?)",
        (db.now(),),
    )
    assert users.is_agent("SCOÜT") is True
    with pytest.raises(ValueError, match="differs only by case"):
        users.ensure_user("SCOÜT")


def test_a_bad_mcp_identity_never_takes_down_the_api(client, fresh_db, monkeypatch):
    # operator config degrades, it does not abort boot — the same rule the
    # model provider follows. Proven at the service the boot path calls.
    from app.services import users

    users.ensure_user("mira")
    with pytest.raises(ValueError):
        users.ensure_user("Mira", kind="agent")
    assert client.get("/health").status_code == 200


def test_a_reserved_key_cannot_read_through_the_perimeter(client, fresh_db, monkeypatch):
    """deps.py refuses this credential, but the catalog reads that resolve no
    caller never reach deps — in api-key mode the perimeter is their only
    gate, so the same wall belongs there. Asserted on those reads: against a
    route that resolves a caller the dependency refuses the key too, and the
    perimeter wall could be deleted with this test still green."""
    from app import config, db
    from app.services import api_keys

    db.execute(
        "INSERT INTO users (name, kind, active, created_at) VALUES ('team', 'human', 1, ?)",
        (db.now(),),
    )
    key = api_keys.create_key("team", "legacy")["key"]
    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    for path in ("/api/playbooks", "/api/personas", "/api/flocks"):
        r = client.get(path, headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 403, path
        assert "reserved for the system" in r.json()["detail"]
