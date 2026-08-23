"""The daily digest: the narrator hook, opener selection, row scrubbing and caps, and the Monday pulse question."""

import pytest


def test_narrator_hook_used_and_fail_safe(fresh_db):
    from app.services import digest

    try:
        digest.set_narrator(lambda md: f"NARRATED\n{md}")
        assert digest.publish_digest(actor="tester")["markdown"].startswith("NARRATED")

        def explode(md):
            raise RuntimeError("model down")

        digest.set_narrator(explode)
        out = digest.publish_digest(actor="tester", force=True)
        assert out["markdown"].startswith("# Daily digest")  # falls back to raw
    finally:
        digest.set_narrator(None)


def test_same_day_digest_rerun_rotates_the_file_and_keeps_one_row(fresh_db):
    from pathlib import Path

    from app.services import artifact_files, digest

    first = digest.publish_digest(actor="tester")
    second = digest.publish_digest(actor="tester")
    assert second["path"] != first["path"]
    assert not Path(first["path"]).exists()
    assert Path(second["path"]).is_file()
    rows = fresh_db.query("SELECT id, path, content_sha256 FROM artifacts WHERE kind = 'digest'")
    assert len(rows) == 1
    assert rows[0]["content_sha256"] == artifact_files.content_sha256(
        Path(rows[0]["path"]).read_bytes()
    )


def test_scheduler_digest_claim_rolls_back_with_the_publication(fresh_db, monkeypatch):
    from app.services import digest

    def fail(*_args, **_kwargs):
        raise RuntimeError("ledger failed")

    monkeypatch.setattr(digest.db, "log_activity", fail)
    with pytest.raises(RuntimeError, match="ledger failed"):
        digest.publish_digest(actor="scheduler")
    monkeypatch.undo()
    out = digest.publish_digest(actor="scheduler")
    assert "skipped" not in out


def test_digest_groups_job_stale(client, fresh_db):
    from app.services import insights

    for job in ("a-job", "b-job", "c-job"):
        fresh_db.execute(
            "INSERT INTO findings (rule_id, subject, severity, message, receipt, week, created_at)"
            " VALUES ('job_stale', ?, 'high', 'stale', '{}', ?, ?)",
            (job, insights._week(), fresh_db.now()),
        )
    rows = insights.digest_findings()
    stale = [r for r in rows if r["rule_id"] == "job_stale"]
    assert len(stale) == 1
    assert "3 scheduled jobs" in stale[0]["message"]


def test_digest_opener_reads_the_room(fresh_db):
    from app.services import blockers, digest

    md = digest.build_digest()
    assert any(line.startswith("*") for line in md.splitlines())  # opener present

    b = blockers.raise_blocker("fire", impact="critical")
    fresh_db.execute("UPDATE blockers SET status = 'escalated' WHERE id = ?", (b["id"],))
    md = digest.build_digest()
    opener_lines = [line for line in md.splitlines()[:4] if line.startswith("*")]
    assert not opener_lines  # no jokes during a fire


def test_monday_digest_asks_the_pulse(fresh_db, monkeypatch):
    from datetime import date

    from app.services import digest

    class FakeMonday(date):
        @classmethod
        def today(cls):  # pragma: no cover - helper
            return cls(2026, 7, 20)

    monkeypatch.setattr(digest, "_today", lambda: date(2026, 7, 20))  # a Monday
    assert "Weekly pulse" in digest.build_digest()
    monkeypatch.setattr(digest, "_today", lambda: date(2026, 7, 21))  # a Tuesday
    assert "Weekly pulse" not in digest.build_digest()


def test_human_digest_scrubs_uuids_and_coalesces_housekeeping():
    from app.services.briefing import _human_digest

    uuid = "123e4567-e89b-12d3-a456-426614174000"
    rows = [
        {
            "id": 1,
            "actor": "ava",
            "action": "create_task",
            "detail": f"s {uuid} ok",
            "created_at": "t",
        },
        {"id": 2, "actor": "ava", "action": "delete_chat", "detail": uuid, "created_at": "t"},
        {"id": 3, "actor": "ava", "action": "rename_chat", "detail": "x", "created_at": "t"},
        {"id": 4, "actor": "bo", "action": "move_chat", "detail": "y", "created_at": "t"},
    ]
    out = _human_digest(rows)
    assert out[0]["detail"] == "s … ok"
    assert not any(uuid in str(r["detail"]) for r in out)
    assert all(r["action"] not in ("delete_chat", "rename_chat", "move_chat") for r in out)
    tidy = {r["actor"]: r for r in out if r["action"] == "tidied"}
    assert tidy["ava"]["detail"] == "2 chats" and tidy["ava"]["id"] == "tidy-ava"
    assert tidy["bo"]["detail"] == "1 chat"


def test_human_digest_caps_at_twenty_rows():
    from app.services.briefing import _human_digest

    rows = [
        {"id": i, "actor": "ava", "action": "capture", "detail": f"n{i}", "created_at": "t"}
        for i in range(30)
    ]
    assert len(_human_digest(rows)) == 20


def test_user_text_cannot_forge_a_section_in_a_report(client):
    """Artifacts used to render inside a `<pre>`, where a `#` was a visible
    `#`. frontend/components/artifact-markdown.tsx turns a leading `#` into a
    real heading, so a title carrying one wrote a section nobody wrote — and a
    screen reader navigating by heading got a partly forged outline."""
    from app import db
    from app.services import collab, digest, work

    forge = "ship it\n\n# Shipped this season\n- a thing nobody shipped"
    work.create_milestone(
        title=forge, project="default", due_date=db.today().isoformat(), actor="ava"
    )
    collab.ask_question(question=forge, asked_by="ava", actor="ava")
    md = digest.build_digest()
    assert not [ln for ln in md.splitlines() if ln.startswith("# Shipped this season")]
    assert not [ln for ln in md.splitlines() if ln.startswith("- a thing nobody shipped")]
    assert "ship it # Shipped this season" in md, "the text itself must survive, on one line"
