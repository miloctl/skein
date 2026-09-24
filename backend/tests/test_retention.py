"""Monthly retention pruning. The activity ledger is kept forever."""

from datetime import UTC, datetime, timedelta

import pytest


def test_failed_retention_rolls_back_its_claim_and_retries(fresh_db, monkeypatch):
    from psycopg.errors import LockNotAvailable

    from app.services import jobs, retention

    execute = fresh_db.execute_rowcount

    def fail_delete(sql, params=()):
        if sql.startswith("DELETE FROM forecast_snapshots"):
            raise LockNotAvailable("transient lock")
        return execute(sql, params)

    with monkeypatch.context() as fault:
        fault.setattr(fresh_db, "execute_rowcount", fail_delete)
        with pytest.raises(LockNotAvailable):
            retention.prune()
    assert fresh_db.query_one("SELECT 1 FROM job_runs WHERE job = 'retention-prune'") is None
    assert "forecast_snapshots" in retention.prune()
    spec = next(job for job in jobs.JOBS if job.name == "retention-prune")
    jobs.run_job(spec)
    assert fresh_db.query_one("SELECT 1 FROM job_outcomes WHERE job = 'retention-prune'") is None


def _iso_hours_ago(hours: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")


def test_retention_keeps_an_unsafe_annual_firing_receipt(fresh_db):
    from app.services import jobs, retention

    calls = []
    half_year_ago = (datetime.now(UTC).month + 5) % 12 + 1
    spec = jobs.JobSpec(
        "annual-effect",
        lambda: calls.append("effect"),
        {"trigger": "cron", "month": half_year_ago, "day": 1, "hour": 0},
        period_hours=24 * 366,
    )
    jobs.run_job(spec)
    assert calls == ["effect"]
    receipt = fresh_db.query_row("SELECT * FROM job_runs WHERE job = 'fire:annual-effect'")
    fired_at = (
        datetime.fromisoformat(receipt["run_key"]).astimezone(UTC).isoformat(timespec="seconds")
    )
    fresh_db.execute(
        "UPDATE job_runs SET created_at = ? WHERE job = 'fire:annual-effect'", (fired_at,)
    )
    assert fired_at < retention._cutoff(retention.JOB_ROW_DAYS)
    retention.prune()
    jobs.run_job(spec)
    assert calls == ["effect"]


def test_retention_prune(fresh_db):
    from app.services.retention import prune

    old = _iso_hours_ago(24 * 400)
    fresh_db.execute(
        "INSERT INTO forecast_snapshots (day, milestone_id, due_date, forecast_date, created_at)"
        " VALUES ('2025-01-01', 1, '2025-01-01', '2025-01-01', ?)",
        (old,),
    )
    fresh_db.execute(
        "INSERT INTO notifications (\"user\", message, read_at, created_at) VALUES ('a', 'm', ?, ?)",
        (old, old),
    )
    fresh_db.execute(
        "INSERT INTO notifications (\"user\", message, created_at) VALUES ('a', 'unread', ?)",
        (old,),
    )
    fresh_db.execute(
        "INSERT INTO job_runs (job, run_key, created_at) VALUES ('digest', '2025-01-01', ?)",
        (old,),
    )
    removed = prune(actor="tester")
    assert removed["forecast_snapshots"] == 1
    assert removed["notifications"] == 1  # unread rows are never pruned
    assert removed["job_runs"] == 1
    assert prune(actor="tester") == {"skipped": "already pruned today", "status": "noop"}
    assert fresh_db.query_row("SELECT COUNT(*) AS n FROM notifications")["n"] == 1


def test_prune_logs_a_sentence_not_a_payload(fresh_db):
    """The activity detail renders verbatim in the My Day feed, so a
    reader on the landing page saw a raw dict: {"forecast_snapshots": 0,
    "notifications": 0, ...}. It must be a sentence, and it must count."""
    from app.services import retention

    retention.prune(actor="scheduler")
    detail = fresh_db.query_row("SELECT detail FROM activity WHERE action = 'retention_prune'")[
        "detail"
    ]
    assert not detail.startswith("{") and ":" not in detail, detail
    assert detail == "nothing old enough to remove"  # empty database


def test_chat_message_mention_dedupe_leaves_only_with_its_message(fresh_db):
    from app.services import chat_threads, retention, users

    users.ensure_user("ava")
    users.ensure_user("dana")
    room = chat_threads.create_shared_chat("Private room", "ava")
    message = chat_threads.post_shared_message(room["id"], "ava", "Hello @dana", "mention")
    fresh_db.execute(
        "INSERT INTO mention_log (entity, entity_id, person, mentioned_by, created_at)"
        " VALUES ('chat_message', ?, 'dana', 'ava', ?),"
        " ('chat_message', ?, 'dana', 'ava', ?)",
        (message["id"], fresh_db.now(), message["id"] + 1000, fresh_db.now()),
    )

    removed = retention.prune(actor="tester")
    assert removed["mention_log"] == 1
    assert fresh_db.query_row("SELECT entity_id FROM mention_log")["entity_id"] == message["id"]


def test_retention_accounts_for_every_table(fresh_db):
    """A migration decides each new table's retention fate explicitly —
    an unrecorded table silently defaults to kept-forever."""
    from app import config
    from app.services import private_notes
    from app.services.retention import CASCADED, KEPT, PRIVATE_KEPT, PRUNE_LABEL

    rows = fresh_db.query(
        "SELECT table_name AS name FROM information_schema.tables"
        " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    )
    real = {r["name"] for r in rows}

    # the private schema carries the same contract; its tables appear on
    # first use, so create them the way the app does before enumerating
    private_notes._ready()
    private_rows = fresh_db.query(
        "SELECT table_name AS name FROM information_schema.tables"
        " WHERE table_schema = ? AND table_type = 'BASE TABLE'",
        (config.PRIVATE_SCHEMA,),
    )
    private_real = {r["name"] for r in private_rows}
    private_undecided = private_real - set(PRIVATE_KEPT)
    assert not private_undecided, (
        f"private tables with no recorded retention decision: {sorted(private_undecided)}"
    )
    private_ghosts = set(PRIVATE_KEPT) - private_real
    assert not private_ghosts, (
        f"PRIVATE_KEPT names tables that do not exist: {sorted(private_ghosts)}"
    )
    pruned, kept, cascaded = set(PRUNE_LABEL), set(KEPT), set(CASCADED)

    undecided = real - pruned - kept - cascaded
    assert not undecided, f"tables with no recorded retention decision: {sorted(undecided)}"
    ghosts = (pruned | kept | cascaded) - real
    assert not ghosts, f"retention maps name tables that do not exist: {sorted(ghosts)}"
    doubled = (pruned & kept) | (pruned & cascaded) | (kept & cascaded)
    assert not doubled, f"tables with two retention decisions: {sorted(doubled)}"

    # a cascade claim needs a real parent decision and a real cascade —
    # otherwise the map documents a cleanup the database does not perform.
    # A parent may itself be cascaded (sessions -> session_agents ->
    # session_messages); the chain still ends at a pruned or kept root.
    orphaned = set(CASCADED.values()) - pruned - kept - cascaded
    assert not orphaned, f"cascade parents with no decision of their own: {sorted(orphaned)}"
    live_cascades = {
        (r["child"], r["parent"])
        for r in fresh_db.query(
            "SELECT tc.table_name AS child, ccu.table_name AS parent"
            " FROM information_schema.table_constraints tc"
            " JOIN information_schema.referential_constraints rc"
            "   ON rc.constraint_name = tc.constraint_name"
            " JOIN information_schema.constraint_column_usage ccu"
            "   ON ccu.constraint_name = tc.constraint_name"
            " WHERE tc.constraint_type = 'FOREIGN KEY' AND rc.delete_rule = 'CASCADE'"
            # scoped: constraint names are unique per schema, and a private or
            # extension schema duplicate would fabricate child/parent pairs
            " AND tc.table_schema = 'public' AND ccu.table_schema = 'public'"
        )
    }
    for child, parent in CASCADED.items():
        assert (child, parent) in live_cascades, (
            f"{child} claims cascade cleanup from {parent}, but no ON DELETE CASCADE exists"
        )


def test_retention_prunes_past_interval_firing_receipts(fresh_db):
    """extension-events fires every minute, and each window left a permanent
    receipt: about 525k rows a year. An interval key names one past window,
    which fire_key never computes again, so it is safe to prune. A cron key
    is not (the annual test above)."""
    from app.services import retention

    old = _iso_hours_ago(24 * 400)
    for key in ("29000001", "29000002"):
        fresh_db.execute(
            "INSERT INTO job_runs (job, run_key, created_at) VALUES ('fire:extension-events', ?, ?)",
            (key, old),
        )
    fresh_db.execute(
        "INSERT INTO job_runs (job, run_key, created_at)"
        " VALUES ('fire:annual', '2025-01-01T00:00+00:00', ?)",
        (old,),
    )
    retention.prune(actor="tester")
    left = {r["job"] for r in fresh_db.query("SELECT job FROM job_runs WHERE job LIKE 'fire:%'")}
    assert left == {"fire:annual"}


def test_an_idle_chat_loses_its_model_sessions_and_keeps_the_chat(fresh_db):
    """A session replays what its agent read on every later turn, a record
    deleted since included. Kept forever, a teammate's deleted note rode
    every idle chat that had once read it."""
    from strands.types.session import Session, SessionType

    from app.agents.session_store import DatabaseSessionRepository
    from app.services import chat_threads
    from app.services.retention import IDLE_SESSION_DAYS, prune

    repo = DatabaseSessionRepository()
    for thread, days in (
        ("idle-chat", IDLE_SESSION_DAYS + 1),
        ("recent-chat", IDLE_SESSION_DAYS - 1),
    ):
        chat_threads.claim_thread(thread, "ava")
        for session_id in (thread, chat_threads.persona_session_id(thread, "scout")):
            repo.create_session(Session(session_id=session_id, session_type=SessionType.AGENT))
        fresh_db.execute(
            "UPDATE chat_threads SET updated_at = ? WHERE id = ?",
            (_iso_hours_ago(24 * days), thread),
        )

    assert prune(actor="tester")["sessions"] == 2
    left = {r["session_id"] for r in fresh_db.query("SELECT session_id FROM sessions")}
    assert left == {"recent-chat", "recent-chat:scout"}
    assert fresh_db.query_one("SELECT 1 FROM chat_threads WHERE id = 'idle-chat'")


def _backdate(fresh_db, table: str, column: str, days: int, row_id) -> None:
    fresh_db.execute(
        f"UPDATE {table} SET {column} = ? WHERE id = ?",  # noqa: S608 — test constants
        (_iso_hours_ago(24 * days), row_id),
    )


def test_the_prune_runs_daily():
    """A horizon is a promise about how long a copy lives. Monthly, every
    copy lived up to a month past its horizon."""
    from app.services import jobs

    spec = next(job for job in jobs.JOBS if job.name == "retention-prune")
    assert "day" not in spec.trigger and spec.period_hours == 24


def test_old_digests_leave_with_their_files_and_documents_stay(fresh_db):
    from pathlib import Path

    from app.services import digest, documents
    from app.services.retention import DERIVED_COPY_DAYS, prune

    path = Path(digest.publish_digest(actor="tester", force=True)["path"])
    old = fresh_db.query_row("SELECT id FROM artifacts WHERE kind = 'digest'")["id"]
    _backdate(fresh_db, "artifacts", "created_at", DERIVED_COPY_DAYS + 1, old)
    doc = documents.create_document("Plan", "# Plan\n", actor="scout")["id"]
    _backdate(fresh_db, "artifacts", "created_at", DERIVED_COPY_DAYS + 30, doc)

    assert prune(actor="tester")["artifacts"] == 1
    assert not path.exists()
    assert fresh_db.query_one("SELECT 1 FROM artifacts WHERE id = ?", (doc,))


def test_old_context_pack_versions_go_and_the_newest_stays(client, fresh_db):
    from app.services import context_pack
    from app.services.retention import DERIVED_COPY_DAYS, prune

    first = context_pack.publish_pack(actor="mira")
    client.post("/api/decisions", json={"title": "Ship weekly", "decision": "always"})
    second = context_pack.publish_pack(actor="mira")
    assert second["version"] == first["version"] + 1
    for row in fresh_db.query("SELECT id FROM context_packs"):
        _backdate(fresh_db, "context_packs", "created_at", DERIVED_COPY_DAYS + 1, row["id"])

    assert prune(actor="tester")["context_packs"] == 1
    assert not context_pack._pack_path(0, first["version"])[1].exists()
    assert context_pack._pack_path(0, second["version"])[1].exists()
    assert context_pack.publish_pack(actor="mira")["version"] == second["version"]


def test_finished_agent_run_sessions_go_after_their_horizon(fresh_db):
    """An unattended run's session is scratch once the run wrote what it
    wrote, and it holds every tool result the run read."""
    from strands.types.session import Session, SessionType

    from app.agents.session_store import DatabaseSessionRepository
    from app.services import chat_threads
    from app.services.retention import RUNNER_SESSION_DAYS, prune

    repo = DatabaseSessionRepository()
    # a chat named "run", in use: its persona session starts like a run id
    chat_threads.claim_thread("run", "ava")
    for session_id in ("run:scout:2026-01-01", "wake:scout:3", "run:scout"):
        repo.create_session(Session(session_id=session_id, session_type=SessionType.AGENT))
    fresh_db.execute(
        "UPDATE sessions SET payload = jsonb_set(payload::jsonb, '{created_at}', to_jsonb(?::text))::text"
        " WHERE session_id IN ('run:scout:2026-01-01', 'run:scout')",
        (_iso_hours_ago(24 * (RUNNER_SESSION_DAYS + 1)),),
    )

    assert prune(actor="tester")["sessions"] == 1
    left = {r["session_id"] for r in fresh_db.query("SELECT session_id FROM sessions")}
    assert left == {"wake:scout:3", "run:scout"}


def test_a_settled_proposal_loses_its_text_and_keeps_its_audience(fresh_db):
    """Cleared to nothing, a rejected private create resolves to the
    workspace tier (review._declared_tier), and the record of a proposal only
    its person could read reaches the team."""
    import json

    from app.services import review
    from app.services.retention import DERIVED_COPY_DAYS, prune

    change = review.propose_change(
        "standup",
        "create",
        {"author": "ava", "visibility": "private", "yesterday": "interviewed elsewhere"},
        summary="ava's standup: interviewed elsewhere",
        actor="scout",
        requested_by="ava",
    )
    review.reject_change(change["id"], actor="ava", viewer=review.scope.Viewer("ava", True))
    before = review._governing_tier(
        fresh_db.query_row("SELECT * FROM pending_changes WHERE id = ?", (change["id"],))
    )
    _backdate(fresh_db, "pending_changes", "reviewed_at", DERIVED_COPY_DAYS + 1, change["id"])

    assert prune(actor="tester")["pending_changes"] == 1
    row = fresh_db.query_row("SELECT * FROM pending_changes WHERE id = ?", (change["id"],))
    assert "interviewed" not in row["payload"] + row["summary"]
    assert json.loads(row["payload"]) == {"author": "ava", "visibility": "private"}
    assert row["text_cleared_at"] and row["status"] == "rejected" and row["reviewed_by"] == "ava"
    assert review._governing_tier(row) == before == ("private", None, "ava")


def test_a_cost_row_loses_its_requester_name_after_a_year(fresh_db):
    from app.services.retention import USAGE_NAME_DAYS, prune

    for days in (USAGE_NAME_DAYS + 1, USAGE_NAME_DAYS - 1):
        fresh_db.execute(
            "INSERT INTO usage_log (thread_id, model_id, created_at, requested_by, cost_usd)"
            " VALUES ('room', 'm', ?, 'ava', 0.5)",
            (_iso_hours_ago(24 * days),),
        )

    assert prune(actor="tester")["usage_log"] == 1
    rows = fresh_db.query("SELECT requested_by, cost_usd FROM usage_log ORDER BY created_at")
    assert [(r["requested_by"], r["cost_usd"]) for r in rows] == [("", 0.5), ("ava", 0.5)]
