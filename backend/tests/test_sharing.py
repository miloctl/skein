"""Personal records start at "only you", and their author widens them on
purpose (services/sharing.py). Never narrower."""

import json

from conftest import _strong

from app import config
from app.agents import identity
from app.services import scope, search, users


def test_personal_records_start_private_and_their_author_shares_them(client, fresh_db):
    """Standups and captures started at the workspace tier, and a private row
    had no way to reach the team without filing it again."""
    for name in ("ava", "bob"):
        users.ensure_user(name)
    ava, bob = _strong(client, "ava"), _strong(client, "bob")
    standup = client.post("/api/standups", json={"today": "ZZSTANDUPZZ"}, headers=ava).json()
    note = client.post("/api/capture", json={"text": "note: ZZNOTEZZ"}, headers=ava).json()
    rows = {
        table: fresh_db.query_one(f"SELECT visibility FROM {table} WHERE id = ?", (row_id,))  # noqa: S608
        for table, row_id in (("standups", standup["id"]), ("notes", note["id"]))
    }
    assert {row["visibility"] for row in rows.values()} == {"private"}

    def found(viewer):
        return [hit["entity"] for hit in search.search("ZZNOTEZZ", viewer=viewer)]

    assert found(scope.Viewer("bob", True)) == []
    share = f"/api/share/notes/{note['id']}"
    assert client.post(share, headers=bob).status_code == 404
    assert client.post(share, headers={"X-User": "ava"}).status_code in (401, 403)
    assert client.post("/api/share/memories/1", headers=ava).status_code == 404
    assert client.post(share, headers=ava).status_code == 200
    assert client.post(share, headers=ava).status_code == 400
    assert found(scope.Viewer("bob", True)) == ["note"]
    assert "ZZNOTEZZ" in client.get("/api/notes", headers=bob).text
    # the standup is its own row, and stays with its author until shared too
    assert "ZZSTANDUPZZ" not in client.get("/api/standups", headers=bob).text
    from app.services.fieldguide import PREDICATES

    assert PREDICATES["share_record"]("ava") and not PREDICATES["share_record"]("bob")


def test_an_agents_standup_starts_private_and_forks_no_unreadable_blocker(fresh_db, monkeypatch):
    """A private standup forks its blocker at the standup's tier, and an agent
    is the blocker's created_by: nobody could have read it."""
    from app.tools.collab import post_standup

    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    users.ensure_user("ava")
    users.ensure_user("scout", kind="agent")
    token = identity.set_agent_identity("scout")
    try:
        private = json.loads(post_standup(author="ava", today="ZZ1ZZ", blockers="vendor"))
        shared = json.loads(
            post_standup(author="ava", today="ZZ2ZZ", blockers="vendor", share_with_team=True)
        )
    finally:
        identity.reset_agent_identity(token)
    tiers = {
        row["id"]: row["visibility"]
        for row in fresh_db.query("SELECT id, visibility FROM standups")
    }
    assert tiers == {private["id"]: "private", shared["id"]: "workspace"}
    blockers = fresh_db.query("SELECT source, visibility FROM blockers")
    assert blockers == [{"source": f"standup:{shared['id']}", "visibility": "workspace"}]


def test_every_shareable_index_format_names_real_columns(fresh_db):
    """A column name that drifts from the schema fails only when somebody
    shares that kind, as a 500 after the row already went to the team."""
    from app.services.sharing import SHAREABLE

    for table, (_entity, text) in SHAREABLE.items():
        columns = fresh_db.query(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table,)
        )
        row = {c["column_name"]: None for c in columns}
        title, body = text(row)
        assert isinstance(title, str) and isinstance(body, str), table
