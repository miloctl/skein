"""Agent identity: who may claim it, what mints a roster row, and how identity strength is reported."""

import pytest


def test_weak_header_cannot_claim_agent_identity(client, fresh_db):
    from app.services import users

    users.ensure_user("scout", kind="agent")
    # /notifications carries CurrentUser on GET; /tasks GET is identity-free
    r = client.get("/api/notifications", headers={"X-User": "scout"})
    assert r.status_code == 403 and "agent identity" in r.json()["detail"]
    w = client.post("/api/tasks", json={"title": "as scout"}, headers={"X-User": "scout"})
    assert w.status_code == 403


def test_reads_do_not_mint_roster_rows(client, fresh_db):
    client.get("/api/notifications", headers={"X-User": "drive-by-reader"})
    assert not fresh_db.query_one("SELECT id FROM users WHERE name = ?", ("drive-by-reader",))
    client.post("/api/tasks", json={"title": "t"}, headers={"X-User": "drive-by-writer"})
    assert fresh_db.query_one("SELECT id FROM users WHERE name = ?", ("drive-by-writer",))


def test_reserved_agent_identities_minted_at_startup(client, fresh_db):
    # the client fixture runs the lifespan — 'agent' (default chat identity)
    # must exist as kind=agent so a weak header can never shadow it
    row = fresh_db.query_one("SELECT kind FROM users WHERE name = 'agent'")
    assert row and row["kind"] == "agent"
    assert (
        client.post("/api/tasks", json={"title": "t"}, headers={"X-User": "agent"}).status_code
        == 403
    )


def test_agent_owned_key_is_refused_on_rest(client, fresh_db):
    from app.services import users
    from app.services.api_keys import create_key

    users.ensure_user("scout", kind="agent")
    key = create_key("scout", "probe")["key"]
    r = client.get("/api/notifications", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403 and "gated tool surface" in r.json()["detail"]


def test_rename_honors_identity_walls(fresh_db):
    from app.services import personas, users

    users.ensure_user("bob")
    users.ensure_user("scout", kind="agent")
    with pytest.raises(ValueError, match="human/agent boundary"):
        users.rename_user("bob", "scout", actor="mira")
    slug = personas.list_personas()[0]["slug"]
    with pytest.raises(ValueError, match="reserved for a bench persona"):
        users.rename_user("bob", slug, actor="mira")


def test_whoami_reports_identity_strength(client, fresh_db):
    from app.services.api_keys import create_key

    weak = client.get("/api/whoami", headers={"X-User": "chen"}).json()
    # admin is gated on strong for the same reason keys_minted is: an
    # unproven identity is whatever the caller typed into the name picker
    assert weak == {"user": "chen", "strong": False, "admin": False, "keys_minted": 0}
    key = create_key("chen", "t")["key"]
    strong = client.get("/api/whoami", headers={"Authorization": f"Bearer {key}"}).json()
    assert strong["user"] == "chen" and strong["strong"] is True
    assert strong["keys_minted"] == 1
