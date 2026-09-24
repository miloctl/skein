"""Daily retention pruning. activity is the provenance ledger — kept
forever; everything pruned here is derivable telemetry, already-consumed
claims/notifications, or a copy of content that outlived its reason.

Daily, because a horizon is a promise about how long a copy lives: a monthly
run kept every copy up to a month past it. The horizons and the backup keep
count (admin.BACKUP_KEEP) together set how long a deleted record can still
exist anywhere."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .. import db
from . import artifact_files, scope, wording

# every key of `removed` needs an entry: a missing one raises KeyError while
# building the feed sentence, which is louder and earlier than shipping a
# table name to a reader
PRUNE_LABEL = {
    "forecast_snapshots": "forecast snapshot",
    "health_snapshots": "health snapshot",
    "notifications": "read notification",
    "job_runs": "job run",
    "job_outcomes": "job outcome",
    "mention_log": "orphan mention record",
    "extension_outbox": "delivered extension event",
    "browser_sessions": "expired browser session",
    "mcp_oauth_flows": "expired MCP sign-in",
    "rate_hits": "expired rate window",
    "sessions": "model session",
    "artifacts": "old daily digest",
    "context_packs": "old context-pack version",
    "pending_changes": "cleared proposal",
    "usage_log": "cleared cost-row name",
}

FORECAST_SNAPSHOT_DAYS = 365
READ_NOTIFICATION_DAYS = 90
JOB_ROW_DAYS = 90
EXTENSION_EVENT_DAYS = 90
IDLE_SESSION_DAYS = 90
# digests, old context-pack versions, and the text of settled proposals and
# their remote tool calls: copies of records, made for one day's reader
DERIVED_COPY_DAYS = 180
# an unattended run's session: scratch once the run wrote what it wrote
RUNNER_SESSION_DAYS = 30
# usage_log keeps its cost forever; the requester's name leaves after a year
USAGE_NAME_DAYS = 365

# Every public table carries one recorded retention decision: pruned here
# (PRUNE_LABEL), pruned by cascade with its parent (CASCADED), or kept with
# the reason (KEPT). tests/test_retention.py enumerates the live schema
# against these maps, so a new table fails CI until its migration author
# records the decision — silence would default it to kept-forever unread.
_USER_DELETED = "user-deleted content: removal is a person's decision, never an age prune"
_CHAT_LIFECYCLE = "chat lifecycle owns deletion (delete_thread and folder operations)"
_DERIVED = "derived from content: rebuilt on demand, rows leave with their entity"
KEPT = {
    "activity": "hash-chained provenance ledger, kept forever",
    "flock_traces": "per-turn token counts usage_log cannot reconstruct",
    "tool_usage": "one row per day/user/surface; the adoption trend is the read",
    "schema_version": "migration receipts the boot depends on",
    "users": "the roster: deactivation, never deletion, keeps provenance resolvable",
    "released_names": "freed names stay refused as long as the ledger names them, which is forever",
    "oidc_identities": "stable sign-in bindings; removal would let a subject claim a new user",
    "api_keys": "credential audit rows: revocation deactivates in place",
    "mcp_servers": "owner-deleted personal servers, never age-pruned",
    "app_settings": "admin-set values: the settings form owns their lifecycle",
    "agent_authority": "the authority matrix the tool gate reads",
    "agent_wakeups": "one current operational wake state per agent, updated in place",
    "feature_unlocks": "one row per unlocked feature",
    "extension_review_invocations": "execution outcome of a reviewed remote write; its arguments and result are cleared with its proposal's text (prune)",
    "extension_command_receipts": "extension write receipts: provenance",
    "forge_receipts": "permanent repository/event/raw-payload fingerprints and delivery-ID bindings: pruning re-enables replay after human edits",
    "memories": "owner-forgettable (memory.forget), never age-pruned",
    "one_on_one_pairs": "consent records: a participant ends a pairing, never an age prune",
    "merge_requests": "consent records for a merge that moved private data, kept as its receipt",
    "search_index": _DERIVED,
    "embeddings": _DERIVED,
    **dict.fromkeys(
        (
            "chat_folders",
            "chat_threads",
            "chat_messages",
            "chat_members",
            "chat_invitations",
            "chat_agent_runs",
        ),
        _CHAT_LIFECYCLE,
    ),
    **dict.fromkeys(
        (
            "tasks",
            "notes",
            "questions",
            "decisions",
            "blockers",
            "milestones",
            "engagements",
            "promises",
            "standups",
            "task_worklog",
            "absences",
            "allocations",
            "events",
            "intake_requests",
            "lessons",
            "findings",
            "finding_dispositions",
            "feedback",
            "crews",
            "crew_members",
        ),
        _USER_DELETED,
    ),
}
# child table -> the pruned-or-kept parent whose ON DELETE CASCADE removes it.
# The test verifies the cascade exists in the live schema, so this map cannot
# claim a cleanup the database does not perform.
CASCADED = {
    "extension_event_attempts": "extension_outbox",
    "extension_event_deliveries": "extension_outbox",
    "notification_reads": "notifications",
    "session_agents": "sessions",
    "session_messages": "session_agents",
    "session_multi_agents": "sessions",
    "session_offload": "sessions",
}
# The private schema's tables carry the same contract, enumerated separately
# because information_schema scoping differs (tests/test_retention.py).
PRIVATE_KEPT = {
    "notes": "owner-deleted (private_notes.delete_note), never age-pruned",
    "audit": "private write trail beside the notes, kept forever",
}


def _cutoff(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


@db.transaction()
def prune(*, actor: str = "scheduler") -> dict:
    # the TEAM day, matching the local 04:00 the scheduler fires on
    # (config.TZ_NAME). Keyed on the UTC day, a zone more than 4 hours east
    # of UTC computes the PREVIOUS day at 04:00 local: the key is already
    # claimed, and the prune silently never runs.
    day = db.today().isoformat()
    if not db.claim_job("retention-prune", day):
        return {"skipped": "already pruned today", "status": "noop"}
    # tool_usage is deliberately absent: one row per (day, user, surface), so
    # a year of a ten-person team is a few thousand rows, and the adoption
    # trend is the read it exists for — pruning it deletes the trend.
    # usage_log rows are the platform's cost history (spend per thread and
    # engagement over time), not derivable from anything else: only the
    # requester's name leaves them, below.
    # flock_traces is absent for the same reason: it carries the per-turn token
    # counts that usage_log cannot reconstruct (usage rows key on thread +
    # agent, so two flock turns in one thread are indistinguishable there). It
    # is bounded in practice by the chat cap, and chat_threads.delete_thread
    # removes a thread's traces with the thread.
    from .. import ratelimit
    from . import browser_sessions, mcp_servers

    removed = {
        "browser_sessions": browser_sessions.prune_expired(),
        "mcp_oauth_flows": mcp_servers.prune_oauth_flows(),
        "rate_hits": ratelimit.prune(),
        "forecast_snapshots": db.execute_rowcount(
            "DELETE FROM forecast_snapshots WHERE created_at < ?",
            (_cutoff(FORECAST_SNAPSHOT_DAYS),),
        ),
        # the same one-row-per-entity-per-day growth, and the same horizon:
        # the readout compares back weeks, never years
        "health_snapshots": db.execute_rowcount(
            "DELETE FROM health_snapshots WHERE created_at < ?",
            (_cutoff(FORECAST_SNAPSHOT_DAYS),),
        ),
        # "read" means read_at for a personal row and a `notification_reads`
        # row for a 'team' one (009). A prune that tests read_at alone makes
        # every team announcement immortal — mark_read never stamps that
        # column on a shared record — and drags its dismissal rows along with
        # it, so the two tables grow together and forever. A team row is
        # prunable once EVERY active human has dismissed it: one straggler
        # keeps it, which is the same promise the unread query makes them.
        "notifications": db.execute_rowcount(
            "DELETE FROM notifications WHERE created_at < ? AND ("
            " (\"user\" != 'team' AND read_at IS NOT NULL)"
            # read_at counts for a team row too. `mark_read_matching` stamps it
            # when the THING a notification points at is settled — a fact about
            # the world, not about one reader — and that write also hides the
            # row from every unread list, so nobody can ever add the per-person
            # dismissal the arm below waits for. Without this disjunct every
            # "Review needed: #N" notification the product sends is permanent.
            " OR (\"user\" = 'team' AND read_at IS NOT NULL)"
            " OR (\"user\" = 'team' AND NOT EXISTS ("
            "   SELECT 1 FROM users u WHERE u.kind = 'human' AND u.active = 1"
            "   AND u.name != 'anonymous'"
            "   AND NOT EXISTS (SELECT 1 FROM notification_reads r"
            '     WHERE r.notification_id = notifications.id AND r."user" = u.name)))'
            ")",
            (_cutoff(READ_NOTIFICATION_DAYS),),
        ),
        # This also expires capture idempotency receipts (`capture:<user>`),
        # so an outbox row re-sent after the horizon files a duplicate —
        # at-least-once, the safe direction, and old enough to notice.
        # Forge redeliveries and annual firings outlive the telemetry horizon.
        # Their small receipt rows grow permanently: pruning them permits an
        # old event or an uncertain external effect to run again.
        # An INTERVAL firing's key is a window number (jobs.fire_key) that
        # is never computed again once the window passes, so its receipt is
        # safe to prune; kept, a per-minute job wrote ~525k rows a year. A
        # cron key is a timestamp an annual job recomputes for two periods.
        "job_runs": db.execute_rowcount(
            "DELETE FROM job_runs WHERE created_at < ? AND job != 'forge-delivery'"
            " AND (job NOT LIKE 'fire:%' OR run_key ~ '^[0-9]+$')"
            " AND (lease_until = '' OR NULLIF(lease_until, '')::timestamptz <= clock_timestamp())",
            (_cutoff(JOB_ROW_DAYS),),
        ),
        "job_outcomes": db.execute_rowcount(
            "DELETE FROM job_outcomes WHERE created_at < ?", (_cutoff(JOB_ROW_DAYS),)
        ),
        # pending too: zero-composition dispatch leaves rows pending on
        # purpose (a disabled extension's backlog survives re-enable), so on
        # the core-only default deployment nothing else ever finalizes them
        # and the table grew without bound.
        "extension_outbox": db.execute_rowcount(
            "DELETE FROM extension_outbox"
            " WHERE status IN ('delivered', 'dead', 'pending') AND created_at < ?",
            (_cutoff(EXTENSION_EVENT_DAYS),),
        ),
        # A model session replays everything its agent read on every later
        # turn, a record deleted since included. An idle chat's sessions go
        # and the chat stays: the next turn starts fresh, and a room agent
        # re-reads the room from its join point (shared_chat_agents._prompt).
        "sessions": _prune_idle_sessions()
        # agent_runner and agent_wakeups name these; the date lives in the
        # SDK's own session payload, since the table has no column for it
        + db.execute_rowcount(
            "DELETE FROM sessions WHERE session_id ~ '^(run|wake):[^:]+:'"
            " AND payload::jsonb ->> 'created_at' < ?",
            (_cutoff(RUNNER_SESSION_DAYS),),
        ),
        # in users.rename_user's table order (_ATTRIBUTION): a rename walks
        # these the same way, and two transactions that lock them in
        # opposite orders deadlock
        "pending_changes": _clear_settled_proposals(),
        "usage_log": db.execute_rowcount(
            "UPDATE usage_log SET requested_by = '' WHERE requested_by <> '' AND created_at < ?",
            (_cutoff(USAGE_NAME_DAYS),),
        ),
        "artifacts": _prune_digests(),
        "context_packs": _prune_pack_versions(),
        # orphans only, never by age: the (entity, entity_id, person) key is
        # the notify-once promise, and an age prune would let an edit of an
        # old row ping the same person again. AUTOINCREMENT ids never come
        # back, so an orphan can never suppress a mention on a future row.
        "mention_log": db.execute_rowcount(
            "DELETE FROM mention_log WHERE"
            " (entity = 'task' AND entity_id NOT IN (SELECT id FROM tasks))"
            " OR (entity = 'note' AND entity_id NOT IN (SELECT id FROM notes))"
            " OR (entity = 'question' AND entity_id NOT IN (SELECT id FROM questions))"
            " OR (entity = 'decision' AND entity_id NOT IN (SELECT id FROM decisions))"
            " OR (entity = 'chat_message' AND entity_id NOT IN (SELECT id FROM chat_messages))"
        ),
    }
    # this detail renders verbatim in the My Day feed, so it is a sentence,
    # not a payload — json.dumps put a raw dict in front of every reader
    gone = [wording.count(n, PRUNE_LABEL[table]) for table, n in removed.items() if n]
    db.log_activity(
        actor,
        "retention_prune",
        ", ".join(gone) if gone else "nothing old enough to remove",
    )
    return removed


def _prune_digests() -> int:
    """Daily digests past the horizon. Uploads and documents are never
    age-pruned: removing those is their person's decision."""
    rows = db.query(
        "DELETE FROM artifacts WHERE kind = 'digest' AND created_at < ? RETURNING path",
        (_cutoff(DERIVED_COPY_DAYS),),
    )
    for row in rows:
        if row["path"]:
            artifact_files.delete_after_commit(Path(row["path"]))
    return len(rows)


def _prune_pack_versions() -> int:
    """Old context-pack versions and their archive files. The newest version
    of each pack stays at any age: publish_pack numbers the next version
    from it, and get_pack serves it."""
    from .context_pack import _pack_path

    rows = db.query(
        "DELETE FROM context_packs p WHERE created_at < ? AND version < ("
        " SELECT MAX(version) FROM context_packs q"
        " WHERE COALESCE(q.crew_id, 0) = COALESCE(p.crew_id, 0))"
        " RETURNING COALESCE(crew_id, 0) AS crew_id, version",
        (_cutoff(DERIVED_COPY_DAYS),),
    )
    for row in rows:
        artifact_files.delete_after_commit(_pack_path(int(row["crew_id"]), int(row["version"]))[1])
    return len(rows)


def _tier_keys() -> frozenset[str]:
    from .review import _CREATE_PARENT

    return frozenset(
        {"visibility", "crew_id", *scope.CLASSIFIED.values()}
        | {key for _table, key in _CREATE_PARENT.values()}
    )


def _clear_settled_proposals() -> int:
    """The text of a settled proposal past the horizon: the payload and the
    summary. Who proposed, who judged, the verdict and its time stay.

    The payload keeps the keys review._governing_tier reads for a create
    whose row is gone or never came: without them the proposal resolves to
    the workspace tier, and a private create's record reaches the team. An
    approved first use of a personal MCP tool keeps its server, tool and
    version, which mcp_tools._first_use_approved matches: without them the
    tool asks for approval again."""
    keys = _tier_keys()
    # A remote call whose completion is unknown keeps everything: reconciling
    # it needs its arguments, and the review list names it by its summary.
    rows = db.query(
        "SELECT id, payload FROM pending_changes WHERE status IN ('approved', 'rejected')"
        " AND reviewed_at < ? AND text_cleared_at IS NULL"
        " AND NOT EXISTS (SELECT 1 FROM extension_review_invocations i"
        "   WHERE i.change_id = pending_changes.id AND i.status = 'completion_unknown')",
        (_cutoff(DERIVED_COPY_DAYS),),
    )
    now = db.now()
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except ValueError:
            payload = {}
        kept = {k: v for k, v in payload.items() if k in keys} if isinstance(payload, dict) else {}
        # the invocation first, the order erasure.erase deletes them in. Any
        # status but completion_unknown: an automatic rejection
        # (review.approve_change) settles the proposal and leaves its
        # invocation 'pending'.
        db.execute(
            "UPDATE extension_review_invocations SET result = '{}', invocation = CASE"
            " WHEN kind = 'mcp_tool' THEN jsonb_build_object("
            " 'server', invocation::jsonb -> 'server', 'tool', invocation::jsonb -> 'tool',"
            " 'version', invocation::jsonb -> 'version')::text ELSE '{}' END"
            " WHERE change_id = ? AND status <> 'completion_unknown'",
            (row["id"],),
        )
        db.execute(
            "UPDATE pending_changes SET payload = ?, summary = '', text_cleared_at = ? WHERE id = ?",
            (json.dumps(kept), now, row["id"]),
        )
    return len(rows)


def _prune_idle_sessions() -> int:
    """The sessions of every chat idle past IDLE_SESSION_DAYS, matched the way
    session_store._THREAD_SESSION matches one thread's. One equality per
    form, so each is a hash join: a pattern built from each thread id was
    compiled again for every (session, thread) pair, a minute per thousand
    idle chats."""
    cutoff = _cutoff(IDLE_SESSION_DAYS)
    own = db.execute_rowcount(
        "DELETE FROM sessions s USING chat_threads t WHERE t.updated_at < ? AND s.session_id = t.id",
        (cutoff,),
    )
    persona = db.execute_rowcount(
        "DELETE FROM sessions s USING chat_threads t WHERE t.updated_at < ?"
        " AND s.session_id ~ '^[^:]+:[^:]+$' AND split_part(s.session_id, ':', 1) = t.id",
        (cutoff,),
    )
    # sessions minted before PERSONA_SEP, few by now, so a pairwise match
    # over them alone is cheap; `abc--x` stays when it is a chat of its own
    legacy = db.execute_rowcount(
        "DELETE FROM sessions s USING chat_threads t WHERE t.updated_at < ?"
        " AND strpos(s.session_id, ':') = 0 AND strpos(s.session_id, '--') > 0"
        " AND left(s.session_id, length(t.id) + 2) = t.id || '--'"
        " AND strpos(substr(s.session_id, length(t.id) + 3), '--') = 0"
        " AND NOT EXISTS (SELECT 1 FROM chat_threads o WHERE o.id = s.session_id)",
        (cutoff,),
    )
    return own + persona + legacy
