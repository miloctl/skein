"""Portable JSON exports account for every table and fail as one snapshot."""

import json
import threading
from pathlib import Path

import pytest


def _path(result):
    return Path(result["path"])


def _dump(result):
    return json.loads(_path(result).read_text(encoding="utf-8"))


def test_export_activity_action_is_registered(fresh_db):
    from app.services import activity

    assert activity.VERBS["export"] == ("exported portable work data", "loud")


def test_export_files_are_private_and_bounded(fresh_db):
    from app.services import admin

    first = _path(admin.export())
    first.chmod(0o644)
    stale = first.parent / "export-2000-01-01.json.tmp"
    stale.write_bytes(b"stale")
    second = _path(admin.export())
    assert not stale.exists()
    assert second.parent.stat().st_mode & 0o077 == 0
    assert second.stat().st_mode & 0o077 == 0
    assert list(second.parent.glob("export-*.json")) == [second]


def test_export_covers_the_newer_tables(fresh_db):
    from app.services import absences, admin, users

    users.ensure_user("mira")
    absences.add_absence("mira", "2026-08-03", "2026-08-04", actor="mira")
    dump = _dump(admin.export())
    assert len(dump["absences"]) == 1
    assert "task_worklog" in dump
    for table in (
        "activity",
        "app_settings",
        "context_packs",
        "feature_unlocks",
        "finding_dispositions",
        "findings",
        "flock_traces",
        "job_outcomes",
        "mention_log",
        "notifications",
        "pending_changes",
        "tool_usage",
        "usage_log",
    ):
        assert table not in dump


def test_export_covers_new_tables(fresh_db):
    from app.services import admin

    dump = _dump(admin.export())
    for table in (
        "promises",
        "agent_authority",
        "forecast_snapshots",
    ):
        assert table in dump
    assert "api_keys" not in dump


def test_export_accounts_for_every_table(fresh_db):
    """A migration decides each new table's portable-export fate explicitly."""
    from app.services.admin import EXCLUDED, TABLES

    rows = fresh_db.query(
        "SELECT table_name AS name FROM information_schema.tables"
        " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    )
    real = {r["name"] for r in rows}
    unaccounted = real - set(TABLES) - EXCLUDED
    assert not unaccounted, (
        f"tables neither exported nor excluded-with-reason: {sorted(unaccounted)}"
    )
    ghosts = (set(TABLES) | EXCLUDED) - real
    assert not ghosts, f"TABLES/EXCLUDED name tables that do not exist: {sorted(ghosts)}"


def test_export_reads_one_database_snapshot(fresh_db, monkeypatch):
    from app.services import admin, users, work

    users.ensure_user("writer")
    milestone_read = threading.Event()
    writer_done = threading.Event()
    errors = []
    original = admin.db.query_batches

    def insert_linked_work():
        try:
            milestone_read.wait(timeout=2)
            milestone = work.create_milestone("late milestone", actor="writer")
            work.create_task("late task", milestone_id=milestone["id"], actor="writer")
        except Exception as exc:
            errors.append(exc)
        finally:
            writer_done.set()

    def interleave(sql, params=(), *, batch_size=1000):
        for rows in original(sql, params, batch_size=batch_size):
            if sql.startswith("SELECT * FROM milestones") and not milestone_read.is_set():
                milestone_read.set()
                writer_done.wait(timeout=3)
            yield rows

    writer = threading.Thread(target=insert_linked_work)
    writer.start()
    monkeypatch.setattr(admin.db, "query_batches", interleave)
    dump = _dump(admin.export())
    writer.join(timeout=3)

    assert errors == []
    assert all(row["title"] != "late milestone" for row in dump["milestones"])
    assert all(row["title"] != "late task" for row in dump["tasks"])
    assert fresh_db.query_one("SELECT id FROM tasks WHERE title = 'late task'") is not None


def test_export_omits_allocations_for_private_engagements(fresh_db):
    from app.services import admin, engagements, users

    users.ensure_user("mira")
    private = engagements.create_engagement("private work", actor="mira", visibility="private")
    workspace = engagements.create_engagement("shared work", actor="mira")
    hidden = engagements.allocate("mira", private["id"], actor="mira")
    visible = engagements.allocate("mira", workspace["id"], actor="mira")

    ids = {row["id"] for row in _dump(admin.export())["allocations"]}
    assert visible["id"] in ids
    assert hidden["id"] not in ids


def test_export_redacts_relationships_to_omitted_rows(fresh_db):
    from app.services import admin, blockers, engagements, promises, users, work

    users.ensure_user("mira")
    private_engagement = engagements.create_engagement(
        "private parent", actor="mira", visibility="private"
    )["id"]
    private_milestone = work.create_milestone(
        "private milestone", actor="mira", visibility="private"
    )["id"]
    private_task = work.create_task("private task", actor="mira", visibility="private")["id"]
    private_blocker = blockers.raise_blocker("private blocker", actor="mira", visibility="private")[
        "id"
    ]
    private_promise = promises.add_promise("private promise", actor="mira", visibility="private")[
        "id"
    ]
    workspace_blocker = blockers.raise_blocker("legacy task link", actor="mira")["id"]
    direct = work.create_task("legacy engagement link", actor="mira")["id"]
    milestone_link = work.create_task("legacy milestone link", actor="mira")["id"]
    waiting = work.create_task("legacy waiting link", actor="mira")["id"]
    public_milestone = work.create_milestone("legacy parent link", actor="mira")["id"]
    fresh_db.execute(
        "UPDATE tasks SET engagement_id = ? WHERE id = ?", (private_engagement, direct)
    )
    fresh_db.execute(
        "UPDATE tasks SET milestone_id = ? WHERE id = ?",
        (private_milestone, milestone_link),
    )
    fresh_db.execute(
        "UPDATE tasks SET waiting_on_type = 'blocker', waiting_on_id = ? WHERE id = ?",
        (private_blocker, waiting),
    )
    fresh_db.execute(
        "UPDATE milestones SET engagement_id = ? WHERE id = ?",
        (private_engagement, public_milestone),
    )
    fresh_db.execute(
        "UPDATE blockers SET task_id = ? WHERE id = ?",
        (private_task, workspace_blocker),
    )
    finding = fresh_db.execute(
        "INSERT INTO findings (rule_id, severity, message, week, created_at)"
        " VALUES ('probe', 'low', 'probe', '2026-W34', ?) RETURNING id",
        (fresh_db.now(),),
    )
    fresh_db.execute("UPDATE tasks SET source_finding_id = ? WHERE id = ?", (finding, direct))
    question = fresh_db.execute(
        "INSERT INTO questions (asked_by, question, created_at, source_finding_id)"
        " VALUES ('mira', 'probe', ?, ?) RETURNING id",
        (fresh_db.now(), finding),
    )
    fresh_db.execute(
        "UPDATE tasks SET waiting_on_type = 'promise', waiting_on_id = ? WHERE id = ?",
        (private_promise, waiting),
    )

    dump = _dump(admin.export())
    tasks = {row["id"]: row for row in dump["tasks"]}
    assert tasks[direct]["engagement_id"] is None
    assert tasks[direct]["source_finding_id"] is None
    assert tasks[milestone_link]["milestone_id"] is None
    assert (tasks[waiting]["waiting_on_type"], tasks[waiting]["waiting_on_id"]) == (None, None)
    milestone = next(row for row in dump["milestones"] if row["id"] == public_milestone)
    assert milestone["engagement_id"] is None
    blocker = next(row for row in dump["blockers"] if row["id"] == workspace_blocker)
    assert blocker["task_id"] is None
    exported_question = next(row for row in dump["questions"] if row["id"] == question)
    assert exported_question["source_finding_id"] is None


def test_task_relationship_redaction_is_batched(fresh_db, monkeypatch):
    from app.services import admin, work

    sizes = []
    original = admin.db.query_batches

    def batches(sql, params=(), *, batch_size=1000):
        if sql.startswith("SELECT * FROM tasks"):
            yield [{"id": value} for value in range(admin._EXPORT_ID_BATCH)]
            yield [{"id": admin._EXPORT_ID_BATCH}]
            return
        yield from original(sql, params, batch_size=batch_size)

    def redact(rows, _viewer):
        sizes.append(len(rows))
        return [dict(row) for row in rows]

    monkeypatch.setattr(admin, "TABLES", ("tasks",))
    monkeypatch.setattr(admin.db, "query_batches", batches)
    monkeypatch.setattr(work, "redact_task_relationships", redact)
    admin.export()
    assert sizes == [admin._EXPORT_ID_BATCH, 1]


def test_parent_relationship_queries_are_batched(fresh_db, monkeypatch):
    from app.services import admin

    parameter_counts = []
    original_query = admin.db.query
    original_batches = admin.db.query_batches

    def query(sql, params=()):
        if sql.startswith("SELECT id FROM engagements WHERE id IN"):
            parameter_counts.append(len(params))
            return []
        return original_query(sql, params)

    def batches(sql, params=(), *, batch_size=1000):
        if sql.startswith("SELECT * FROM milestones"):
            yield [
                {"id": value, "engagement_id": value}
                for value in range(1, admin._EXPORT_ID_BATCH + 1)
            ]
            yield [{"id": admin._EXPORT_ID_BATCH + 1, "engagement_id": admin._EXPORT_ID_BATCH + 1}]
            return
        yield from original_batches(sql, params, batch_size=batch_size)

    monkeypatch.setattr(admin, "TABLES", ("milestones",))
    monkeypatch.setattr(admin.db, "query", query)
    monkeypatch.setattr(admin.db, "query_batches", batches)
    admin.export()
    # One visibility parameter follows each id batch.
    assert parameter_counts == [admin._EXPORT_ID_BATCH + 1, 2]


def test_export_lock_wait_is_bounded(fresh_db, monkeypatch):
    from app.services import admin

    monkeypatch.setattr(admin, "_EXPORT_LOCK_WAIT_SECONDS", 0)
    assert admin._EXPORT_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(admin.LockNotAvailable, match="still running"):
            admin.export()
    finally:
        admin._EXPORT_LOCK.release()


def test_download_size_limit_stops_generation_early(fresh_db, monkeypatch):
    from app.services import admin

    monkeypatch.setattr(admin, "MAX_EXPORT_DOWNLOAD_BYTES", 1)
    reads = []
    original = admin.db.query_batches

    def batches(sql, params=(), *, batch_size=1000):
        reads.append(sql)
        yield from original(sql, params, batch_size=batch_size)

    monkeypatch.setattr(admin.db, "query_batches", batches)
    with pytest.raises(admin.ExportTooLarge):
        admin.export_download(actor="tester")
    assert reads == []  # refused on the opening fragment, before any table scan
    exports = admin.Path(admin.config.DATA_DIR) / "exports"
    assert not exports.exists() or list(exports.iterdir()) == []
    assert fresh_db.query_one("SELECT id FROM activity WHERE action = 'export'") is None


def test_concurrent_export_retention_does_not_race(fresh_db):
    from app import config
    from app.services import admin

    barrier = threading.Barrier(8)
    errors = []

    def run():
        try:
            barrier.wait(timeout=3)
            admin.export(keep=1)
        except Exception as exc:
            errors.append(exc)

    workers = [threading.Thread(target=run) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert errors == []
    assert len(list((config.DATA_DIR / "exports").glob("export-*.json"))) == 1


def test_transient_download_does_not_consume_retained_export_slots(fresh_db):
    from app.services import admin

    retained = _path(admin.export(keep=1))
    opened, _ = admin.export_download(keep=1)
    try:
        assert retained.exists()
    finally:
        opened.close()


def test_download_file_stays_readable_after_later_retention(fresh_db):
    from app import config
    from app.services import admin

    opened, _ = admin.export_download(keep=1)
    assert list((config.DATA_DIR / "exports").glob("export-*.json")) == []
    try:
        for _ in range(3):
            admin.export(keep=1)
        assert json.load(opened)["users"] == []
    finally:
        opened.close()


def test_export_fails_instead_of_replacing_an_unreadable_table(fresh_db, monkeypatch):
    from app import config
    from app.services import admin

    original = admin.db.query_batches

    def fail_tasks(sql, params=(), *, batch_size=1000):
        if sql.startswith("SELECT * FROM tasks"):
            raise RuntimeError("unreadable tasks")
        yield from original(sql, params, batch_size=batch_size)

    monkeypatch.setattr(admin.db, "query_batches", fail_tasks)
    with pytest.raises(RuntimeError, match="unreadable tasks"):
        admin.export(actor="tester")
    exports = config.DATA_DIR / "exports"
    assert not exports.exists() or list(exports.iterdir()) == []
    assert fresh_db.query_one("SELECT id FROM activity WHERE action = 'export'") is None


def test_portable_export_excludes_live_chat_session_and_review_invocation_canaries(
    fresh_db,
):
    from strands.types.session import Session, SessionAgent, SessionMessage, SessionType

    from app.agents.session_store import DatabaseSessionRepository
    from app.services import admin, chat_threads, review, users

    canary = "LIVE-PORTABLE-EXCLUSION-CANARY"
    users.ensure_user("mira")
    chat_threads.log_message("canary-thread", "mira", "user", canary)
    repo = DatabaseSessionRepository()
    repo.create_session(Session(session_id="canary-session", session_type=SessionType.AGENT))
    repo.create_agent(
        "canary-session",
        SessionAgent(
            agent_id="default",
            state={"canary": canary},
            conversation_manager_state={},
        ),
    )
    repo.create_message(
        "canary-session",
        "default",
        SessionMessage.from_message({"role": "user", "content": [{"text": canary}]}, 0),
    )
    import asyncio

    from app.agents.session_store import DbOffloadStorage

    asyncio.run(DbOffloadStorage("canary-session").write("offloader/canary_0", canary.encode()))
    proposal = review.propose_extension_invocation(
        "core_tool",
        {"tool": "create_task", "agent": "scout", "preview": canary},
        {"tool": "create_task", "agent": "scout", "tool_use": {"canary": canary}},
        summary="run a governed stock tool",
        actor="scout",
        requested_by="mira",
    )
    assert (
        fresh_db.query_one("SELECT id FROM chat_messages WHERE content = ?", (canary,)) is not None
    )
    assert (
        fresh_db.query_one(
            "SELECT change_id FROM extension_review_invocations WHERE change_id = ?",
            (proposal["id"],),
        )
        is not None
    )

    text = _path(admin.export()).read_text(encoding="utf-8")
    dump = json.loads(text)
    assert canary not in text
    for excluded in (
        "chat_threads",
        "chat_messages",
        "sessions",
        "session_agents",
        "session_messages",
        "session_offload",
        "extension_review_invocations",
    ):
        assert excluded not in dump


def test_portable_export_excludes_private_and_operational_canaries(fresh_db):
    from app.services import admin, users, work

    canary = "PRIVATE-EXPORT-CANARY"
    users.ensure_user("mira")
    work.create_task("portable task", actor="mira")
    now = fresh_db.now()
    fresh_db.execute(
        "INSERT INTO pending_changes (entity, action, payload, proposed_by, created_at)"
        " VALUES ('task', 'create', ?, 'agent', ?)",
        (canary, now),
    )
    notification = fresh_db.execute(
        'INSERT INTO notifications ("user", message, created_at)'
        " VALUES ('mira', ?, ?) RETURNING id",
        (canary, now),
    )
    fresh_db.execute(
        "INSERT INTO notification_reads (notification_id, \"user\", read_at) VALUES (?, 'mira', ?)",
        (notification, now),
    )
    fresh_db.execute(
        "INSERT INTO feedback (kind, input, output, verdict, correction, created_at)"
        " VALUES ('chat', ?, ?, 'corrected', ?, ?)",
        (canary, canary, canary, now),
    )
    fresh_db.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES ('canary', ?, ?)",
        (canary, now),
    )
    fresh_db.execute(
        "INSERT INTO job_runs (job, run_key, created_at) VALUES ('canary', ?, ?)",
        (canary, now),
    )
    fresh_db.execute(
        "INSERT INTO job_outcomes (job, status, detail, created_at)"
        " VALUES ('canary', 'error', ?, ?)",
        (canary, now),
    )
    fresh_db.execute(
        "INSERT INTO artifacts (kind, title, path, created_by, created_at, content_sha256)"
        " VALUES ('document', 'portable artifact', ?, 'mira', ?, ?)",
        (f"/private/{canary}", now, "a" * 64),
    )
    fresh_db.log_activity("mira", "test_action", canary)
    fresh_db.execute(
        "INSERT INTO feature_unlocks (person, knot, first_at) VALUES (?, 'probe', ?)",
        (canary, now),
    )
    fresh_db.execute(
        "INSERT INTO mention_log (entity, entity_id, person, mentioned_by, created_at)"
        " VALUES ('task', 999, ?, 'mira', ?)",
        (canary, now),
    )
    fresh_db.execute(
        "INSERT INTO tool_usage (day, \"user\", surface) VALUES ('2026-08-22', ?, ?)",
        (canary, canary),
    )
    fresh_db.execute(
        "INSERT INTO usage_log (thread_id, model_id, created_at) VALUES (?, ?, ?)",
        (canary, canary, now),
    )
    fresh_db.execute(
        'INSERT INTO flock_traces (thread_id, "user", flock, members, created_at)'
        " VALUES (?, ?, ?, '[]', ?)",
        (canary, canary, canary, now),
    )
    fresh_db.execute(
        "INSERT INTO context_packs (version, content, content_hash, created_at)"
        " VALUES (1, ?, 'hash', ?)",
        (canary, now),
    )
    finding = fresh_db.execute(
        "INSERT INTO findings (rule_id, severity, message, receipt, week, created_at)"
        " VALUES ('probe', 'high', ?, ?, '2026-W34', ?) RETURNING id",
        (canary, json.dumps({"source": canary}), now),
    )
    fresh_db.execute(
        "INSERT INTO finding_dispositions"
        " (finding_id, rule_id, subject, disposition, reason, created_at)"
        " VALUES (?, 'probe', 'probe', 'resolved', ?, ?)",
        (finding, canary, now),
    )

    path = _path(admin.export())
    text = path.read_text(encoding="utf-8")
    dump = json.loads(text)
    assert canary not in text
    assert [row["title"] for row in dump["tasks"]] == ["portable task"]
    assert len(dump["artifacts"]) == 1
    assert dump["artifacts"][0]["title"] == "portable artifact"
    assert "path" not in dump["artifacts"][0]
    assert "content_sha256" not in dump["artifacts"][0]
