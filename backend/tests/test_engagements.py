"""Engagements: orphan-milestone adoption, rename propagation, the task and milestone links, and closing with work still open."""

import pytest
from conftest import _unread_for


def test_create_engagement_adopts_orphan_milestones(fresh_db):
    from app.services import engagements, work
    from app.services.portfolio import _linked_blockers  # noqa: F401 — import sanity

    work.create_milestone(title="Early milestone", project="Comet", actor="tester")
    eng = engagements.create_engagement(name="Comet", actor="tester")
    row = fresh_db.query_row("SELECT engagement_id FROM milestones")
    assert row["engagement_id"] == eng["id"]


def test_ship_it_and_handoff_survive_rename(client, fresh_db):
    from app.services import engagements, handoff, work

    eng = engagements.create_engagement(name="Old Name", actor="tester")
    work.create_milestone(title="M1", project="Old Name", actor="tester")
    fresh_db.execute("UPDATE engagements SET name = 'New Name' WHERE id = ?", (eng["id"],))
    result = handoff.generate_handoff(eng["id"], actor="tester")
    assert "M1" in result["markdown"]  # name join would have lost the milestone


def test_same_day_handoff_after_rename_stays_one_row(client, fresh_db):
    from pathlib import Path

    from app.services import artifact_files, engagements, handoff

    eng = engagements.create_engagement(name="Old Name", actor="tester")
    first = handoff.generate_handoff(eng["id"], actor="tester")
    fresh_db.execute("UPDATE engagements SET name = 'New Name' WHERE id = ?", (eng["id"],))
    second = handoff.generate_handoff(eng["id"], actor="tester")
    assert second["artifact_id"] == first["artifact_id"]
    assert second["file"] != first["file"]
    # the answer names the file; the row holds where it is
    assert "/" not in first["file"]
    rows = fresh_db.query("SELECT id, path, content_sha256 FROM artifacts WHERE kind = 'handoff'")
    assert not (Path(rows[0]["path"]).parent / first["file"]).exists()
    assert len(rows) == 1
    assert rows[0]["content_sha256"] == artifact_files.content_sha256(
        Path(rows[0]["path"]).read_bytes()
    )


def test_engagement_rename_propagates_and_reindexes(client):
    from app import db
    from app.services import engagements, search, work

    e = engagements.create_engagement("Aurora Launch")
    m = work.create_milestone("ship v1", project="Aurora Launch")

    client.patch(f"/api/engagements/{e['id']}", json={"name": "Borealis Launch"})
    row = db.query_one("SELECT project, engagement_id FROM milestones WHERE id = ?", (m["id"],))
    assert row["project"] == "Borealis Launch" and row["engagement_id"] == e["id"]
    hits = [h for h in search.search("Borealis") if h["entity"] == "engagement"]
    assert hits and hits[0]["entity_id"] == e["id"]


def test_engagement_rename_collision_rejected(client):
    from app.services import engagements

    engagements.create_engagement("Taken")
    e = engagements.create_engagement("Original")
    with pytest.raises(ValueError, match="already exists"):
        engagements.update_engagement(e["id"], name="Taken")
    assert client.patch(f"/api/engagements/{e['id']}", json={"name": "Taken"}).status_code == 400


def test_failed_rename_close_does_not_orphan_milestone_labels(fresh_db):
    from app import db
    from app.services import engagements, work

    e = engagements.create_engagement("Aurora")
    m = work.create_milestone("ship", project="Aurora")
    with pytest.raises(ValueError, match="conclusion"):
        engagements.update_engagement(e["id"], name="Borealis", status="closed")
    row = db.query_one("SELECT project FROM milestones WHERE id = ?", (m["id"],))
    assert row["project"] == "Aurora"  # rename never landed; the label must not move


def test_milestone_resolves_engagement_id(client, fresh_db):
    e = client.post("/api/engagements", json={"name": "Linked"}).json()
    m = client.post("/api/milestones", json={"title": "m", "project": "Linked"}).json()
    row = fresh_db.query_one("SELECT engagement_id FROM milestones WHERE id = ?", (m["id"],))
    assert row["engagement_id"] == e["id"]
    m2 = client.post("/api/milestones", json={"title": "adhoc", "project": "default"}).json()
    row2 = fresh_db.query_one("SELECT engagement_id FROM milestones WHERE id = ?", (m2["id"],))
    assert row2["engagement_id"] is None


def test_unmatched_project_milestone_files_team_notification(fresh_db):
    from app import db
    from app.services import work

    work.create_milestone("lost milestone", project="ghost-project")
    rows = db.query("SELECT * FROM notifications WHERE \"user\" = 'team' AND tier = 'digest'")
    assert any("ghost-project" in r["message"] for r in rows)

    db.execute("DELETE FROM notifications")
    work.create_milestone("plain milestone")  # project=default: no nag
    assert db.query("SELECT * FROM notifications") == []


def test_task_links_to_engagement_directly(client, fresh_db):
    from app.services import engagements, portfolio, work

    eng = engagements.create_engagement(name="Direct-link", actor="tester")
    t = work.create_task(title="orphan work", engagement_id=eng["id"], actor="tester")
    health = {h["name"]: h for h in portfolio.engagement_health()}
    assert "Direct-link" in health
    # the direct-linked task counts as engagement work (silence check sees it)
    row = fresh_db.query_one("SELECT engagement_id FROM tasks WHERE id = ?", (t["id"],))
    assert row["engagement_id"] == eng["id"]


def test_task_engagement_in_handoff_and_pack(client):
    from app.services import context_pack, engagements, handoff, work

    eng = engagements.create_engagement(name="Pack-link", actor="tester")
    work.create_task(title="direct task for pack", engagement_id=eng["id"], actor="tester")
    pack = context_pack.build_engagement_pack(eng["id"])
    assert "direct task for pack" in pack
    h = handoff.generate_handoff(eng["id"], actor="tester")
    if "path" in h:
        from pathlib import Path

        text = Path(h["path"]).read_text()
    else:
        text = str(h)
    assert "direct task for pack" in text


def test_create_task_rejects_unknown_engagement(client):
    r = client.post("/api/tasks", json={"title": "x", "engagement_id": 999})
    assert r.status_code == 400


def test_close_with_open_tasks_is_loud(fresh_db):
    from app.services import engagements, work

    eng = engagements.create_engagement("loose ends", actor="claude")
    work.create_task("straggler", engagement_id=eng["id"], actor="claude")
    out = engagements.update_engagement(
        eng["id"], status="closed", conclusion="achieved", actor="claude"
    )
    assert out["open_tasks"] == 1
    assert _unread_for(fresh_db, "team", "%open task%")


def test_case_variant_engagement_name_is_refused_by_the_schema(fresh_db):
    """The unique index must also stop writers that bypass the service's name lock."""
    from app import db
    from app.services import engagements

    engagements.create_engagement("Alpha", actor="tester")
    with pytest.raises(db.IntegrityError):
        # Direct SQL has no service lock, so the schema remains the backstop.
        db.execute(
            "INSERT INTO engagements (name, created_at, updated_at) VALUES (?, ?, ?)",
            ("alpha", db.now(), db.now()),
        )


def test_closing_again_after_a_reopen_does_not_repeat_the_close(fresh_db):
    """Reopening an experiment and closing it again wrote a second lesson and
    a second Ship It notice."""
    from app.services import engagements

    e = engagements.create_engagement("Probe", kind="experiment", timebox_end="2026-12-01")
    engagements.update_engagement(e["id"], status="closed", conclusion="invalidated")
    engagements.update_engagement(e["id"], status="active")
    engagements.update_engagement(e["id"], status="closed", conclusion="invalidated")
    lessons = fresh_db.query("SELECT id FROM lessons WHERE engagement_id = ?", (e["id"],))
    ship = fresh_db.query("SELECT id FROM notifications WHERE message LIKE '%Probe%shipped%'")
    assert len(lessons) == 1
    assert len(ship) <= 1


def test_the_ship_recap_counts_only_what_the_team_can_open(fresh_db):
    """The recap is one workspace note for everybody, and it counted a
    private task inside the engagement, a row its readers could not open."""
    from app.services import engagements, users, work

    users.ensure_user("ada")
    eng = engagements.create_engagement("Borealis", actor="ada")
    for tier in ("workspace", "private"):
        task = work.create_task(
            f"{tier} step", actor="ada", engagement_id=eng["id"], visibility=tier
        )
        work.update_task(task["id"], status="done", actor="ada")
    engagements.update_engagement(eng["id"], status="closed", conclusion="achieved", actor="ada")
    recap = fresh_db.query_one("SELECT content FROM notes WHERE topic = 'shipped-Borealis'")
    assert "1 tasks done" in recap["content"]
