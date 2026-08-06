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
    # planted raw: ensure_user refuses to CREATE a same-kind case variant now,
    # and this test is about repairing a roster that carries one from before
    # that guard — rename-merge is the repair path, so it must keep working
    fresh_db.execute(
        "INSERT INTO users (name, kind, active, created_at) VALUES ('mira', 'human', 1, ?)",
        (fresh_db.now(),),
    )
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
    """Every declared column exists. This is the FORWARD direction only — the
    test below is the one that catches a new column nobody added."""
    from app.services.users import _ATTRIBUTION

    tables = {
        r["name"] for r in fresh_db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    for table, cols in _ATTRIBUTION.items():
        assert table in tables, table
        have = {c["name"] for c in fresh_db.query(f"PRAGMA table_info({table})")}
        for col in cols:
            assert col in have, f"{table}.{col}"


# column names that hold a person. A new table carrying one of these joins
# _ATTRIBUTION or this list, with a reason — the map is what rename_user walks,
# and a column left out of it silently strands that person's rows under the old
# name forever.
_PERSON_SHAPED = frozenset(
    {
        "actor", "added_by", "agent", "asked_by", "assigned_to", "assignee", "author",
        "created_by", "decided_by", "delegated_agent", "lead", "mentioned_by", "owner",
        "person", "proposed_by", "requested_by", "requester", "reviewed_by", "sponsor",
        "subject", "updated_by", "user",
    }
)  # fmt: skip

# deliberate absences, each with the consequence of leaving it out
_NOT_RENAMED = {
    ("activity", "actor"): (
        "every chained row's digest covers its actor, so a rewrite breaks"
        " verify_chain permanently at the renamed person's earliest row. The"
        " ledger records what was true when it was written."
    ),
    ("findings", "subject"): (
        "a finding is a weekly snapshot keyed UNIQUE (rule_id, subject, week)."
        " Moving the subject would collide with the renamed person's own row"
        " for the same week, and the message text names the old name anyway."
    ),
    ("finding_dispositions", "subject"): (
        "it points at findings.subject above, and must not drift from it."
    ),
    ("mention_log", "person"): (
        "a dedupe key, not attribution. Left behind, the worst case is one"
        " repeated notification the next time the same row is scanned."
    ),
    ("mention_log", "mentioned_by"): ("the other half of the dedupe key above."),
}


def test_no_person_column_is_left_out_of_the_rename_map(client, fresh_db):
    """The reverse direction. Without it, adding a table with a `person` or
    `owner` column passes every gate in the repository while rename_user
    quietly stops being able to move that person."""
    from app.services.users import _ATTRIBUTION

    unlisted = set()
    for row in fresh_db.query("SELECT name FROM sqlite_master WHERE type = 'table'"):
        table = row["name"]
        for col in fresh_db.query(f"PRAGMA table_info({table})"):
            name = col["name"]
            if name not in _PERSON_SHAPED:
                continue
            if name in _ATTRIBUTION.get(table, ()) or (table, name) in _NOT_RENAMED:
                continue
            unlisted.add(f"{table}.{name}")
    assert not unlisted, (
        f"these name a person and rename_user does not move them: {sorted(unlisted)}."
        " Add each to users._ATTRIBUTION, or to _NOT_RENAMED here with the reason."
    )


def test_the_deliberate_absences_are_still_real_columns(client, fresh_db):
    """A reason kept for a column that no longer exists is a reason the next
    reader trusts about something else."""
    from app.services.users import _ATTRIBUTION

    for (table, col), reason in _NOT_RENAMED.items():
        have = {c["name"] for c in fresh_db.query(f"PRAGMA table_info({table})")}
        assert col in have, f"{table}.{col} is gone — delete its entry"
        assert col not in _ATTRIBUTION.get(table, ()), f"{table}.{col} is in both lists"
        assert reason.strip(), f"{table}.{col} needs a reason"


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


def test_an_overlay_persona_slug_is_reserved_from_humans(fresh_db, tmp_path, monkeypatch):
    """The bench-slug guard must see personas added through the overlay, live -
    a cached roster would let a human absorb an overlay persona's identity."""
    import pytest

    from app import config
    from app.services import users

    users.ensure_user("fixer")  # before the overlay exists, the name is free
    (tmp_path / "fixer2.md").write_text("---\nname: Fixer\ndescription: fixes\n---\nYou fix.")
    monkeypatch.setattr(config, "PERSONAS_OVERLAY", tmp_path)
    with pytest.raises(ValueError, match="reserved for a bench persona"):
        users.ensure_user("fixer2")
