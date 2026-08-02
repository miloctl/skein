"""The roster: rename, merge, deactivate, growth interests, the anonymous
exclusion, and the attribution map that must match the schema."""


def test_merge_backfills_profile_fields_target_never_set(fresh_db):
    from app.services import users

    users.ensure_user("mira")  # target: no theme, no growth interests
    users.ensure_user("Mira K")
    users.set_theme("Mira K", '{"pack":"atelier"}')
    users.set_growth_interests("Mira K", "rust, distributed systems")

    out = users.rename_user("Mira K", "mira")
    assert out["merged"] is True
    assert users.get_theme("mira") == '{"pack":"atelier"}'
    row = users.list_users()
    me = next(u for u in row if u["name"] == "mira")
    assert me["growth_interests"] == "rust, distributed systems"


def test_user_deactivate_needs_strong_identity(client):
    client.post("/api/users/growth-interests", json={"interests": ""}, headers={"X-User": "typo"})
    r = client.post("/api/users/typo/active", json={"active": False})
    assert r.status_code == 403


def test_user_deactivate_removes_from_roster(client, fresh_db):
    from app.services import users

    users.ensure_user("typo")
    users.set_active("typo", False, actor="tester")
    assert "typo" not in [u["name"] for u in users.list_users()]
    # history stays; the row still exists
    assert "typo" in [u["name"] for u in users.list_users(active_only=False)]


def test_deactivate_revokes_keys(client, fresh_db):
    from app.services import api_keys, users

    users.ensure_user("leaver")
    minted = api_keys.create_key("leaver", label="test")
    assert api_keys.verify_key(minted["key"]) == "leaver"
    out = users.set_active("leaver", False, actor="tester")
    assert out["keys_revoked"] == 1
    assert api_keys.verify_key(minted["key"]) is None


def test_users_all_param_lists_inactive(client, fresh_db):
    from app.services import users

    users.ensure_user("gone")
    users.set_active("gone", False, actor="tester")
    assert "gone" not in [u["name"] for u in client.get("/api/users").json()]
    assert "gone" in [u["name"] for u in client.get("/api/users?all=1").json()]


def test_rename_user_moves_attribution(client, fresh_db):
    from app.services import users, work

    users.ensure_user("Mira")
    work.create_task(title="typo-cased work", assignee="Mira", actor="Mira")
    out = users.rename_user("Mira", "mira", actor="tester")
    assert out["merged"] is False and out["rows_moved"] >= 2
    t = fresh_db.query_one(
        "SELECT assignee, created_by FROM tasks WHERE title = ?", ("typo-cased work",)
    )
    assert t["assignee"] == "mira" and t["created_by"] == "mira"
    names = [u["name"] for u in users.list_users(active_only=False)]
    assert "Mira" not in names and "mira" in names


def test_rename_user_merges_into_existing(client, fresh_db):
    from app.services import adoption, users, work

    users.ensure_user("Mira")
    users.ensure_user("mira")
    adoption.record_use("Mira", "api")
    adoption.record_use("mira", "api")
    work.create_task(title="merge me", assignee="Mira", actor="Mira")
    out = users.rename_user("Mira", "mira", actor="tester")
    assert out["merged"] is True
    row = fresh_db.query_one("SELECT SUM(actions) AS n FROM tool_usage WHERE user = 'mira'")
    assert row["n"] == 2
    assert not fresh_db.query("SELECT * FROM tool_usage WHERE user = 'Mira'")
    assert len([u for u in users.list_users(active_only=False) if u["name"].lower() == "mira"]) == 1


def test_attribution_map_matches_schema(client, fresh_db):
    """Every declared column exists; new attribution-shaped columns must be
    added to the map (or this fails and forces the decision)."""
    from app.services.users import _ATTRIBUTION

    tables = {
        r["name"] for r in fresh_db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    for table, cols in _ATTRIBUTION.items():
        assert table in tables, table
        have = {c["name"] for c in fresh_db.query(f"PRAGMA table_info({table})")}
        for col in cols:
            assert col in have, f"{table}.{col}"


def test_ensure_user_concurrent_safe(fresh_db):
    from app.services import users

    a = users.ensure_user("sam")
    b = users.ensure_user("sam")
    assert a["id"] == b["id"]
    assert len(users.list_users()) == 1


def test_users_listing_excludes_anonymous(client):
    client.get("/api/briefing", headers={"X-User": ""})  # ensure_user("anonymous")
    names = [u["name"] for u in client.get("/api/users").json()]
    assert "anonymous" not in names
    names_all = [u["name"] for u in client.get("/api/users", params={"all": 1}).json()]
    assert "anonymous" not in names_all


def test_growth_interests_self_declared_and_in_what_if(client, fresh_db):
    client.post(
        "/api/users/growth-interests",
        json={"interests": "RAG evaluation, incident command"},
        headers={"X-User": "chen"},
    )
    req = client.post("/api/intake", json={"title": "RAG revamp"}).json()
    out = client.post(
        f"/api/intake/{req['id']}/what-if", json={"people": ["chen", "dana"], "percent": 40}
    ).json()
    by_person = {p["person"]: p for p in out["projection"]}
    assert "RAG" in by_person["chen"]["growth_interests"]
    assert by_person["dana"]["growth_interests"] == ""
