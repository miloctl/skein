"""The daily digest: the narrator hook, opener selection, row scrubbing and caps, and the Monday pulse question."""


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

    monkeypatch.setattr(digest, "_utc_today", lambda: date(2026, 7, 20))  # a Monday
    assert "Weekly pulse" in digest.build_digest()
    monkeypatch.setattr(digest, "_utc_today", lambda: date(2026, 7, 21))  # a Tuesday
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
