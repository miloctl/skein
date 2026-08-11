-- v1 baseline: the full schema as built by the 45 pre-release migrations
-- (squashed 2026-08-04, before the first production deploy - git history
-- keeps the originals). DDL only: on an empty database every backfill in
-- the old corpus was a no-op, so none carried forward.
-- The activity chain starts here: no migration may ever UPDATE or DELETE
-- an activity row that carries a seq (tests/test_migrations.py scans for
-- it) - a rewrite breaks hash verification at the earliest touched row.
-- Append-only from now on: schema changes go in a new numbered file, and
-- after the first production deploy a migration keeps its filename forever
-- (the runner tracks applied migrations by filename in schema_version).

CREATE TABLE milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL DEFAULT 'default',
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'in_progress', 'blocked', 'done')),
    owner TEXT DEFAULT '',
    due_date TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
, origin TEXT NOT NULL DEFAULT 'human', created_by TEXT NOT NULL DEFAULT '', engagement_id INTEGER REFERENCES engagements(id), completed_at TEXT);

CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    milestone_id INTEGER REFERENCES milestones(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo'
        CHECK (status IN ('todo', 'in_progress', 'blocked', 'done')),
    assignee TEXT DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    due_date TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
, origin TEXT NOT NULL DEFAULT 'human', created_by TEXT NOT NULL DEFAULT '', committed_week TEXT, delegated_agent TEXT NOT NULL DEFAULT '', sponsor TEXT NOT NULL DEFAULT '', completed_at TEXT, source_finding_id INTEGER REFERENCES findings(id), waiting_on_type TEXT
    CHECK (waiting_on_type IN ('task', 'blocker', 'promise')), waiting_on_id INTEGER, engagement_id INTEGER REFERENCES engagements(id), forge_url TEXT NOT NULL DEFAULT '',
-- both halves of the waiting_on pair or neither: a half-set pair renders
-- "waiting on task #None" receipts and feeds NULL into the settled probe
    CHECK ((waiting_on_type IS NULL) = (waiting_on_id IS NULL)));

CREATE TABLE questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_by TEXT NOT NULL,
    assigned_to TEXT DEFAULT '',
    question TEXT NOT NULL,
    answer TEXT DEFAULT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered')),
    created_at TEXT NOT NULL,
    answered_at TEXT DEFAULT NULL
, origin TEXT NOT NULL DEFAULT 'human', created_by TEXT NOT NULL DEFAULT '', source_finding_id INTEGER REFERENCES findings(id));

CREATE TABLE decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    context TEXT DEFAULT '',
    decision TEXT NOT NULL,
    decided_by TEXT DEFAULT '',
    created_at TEXT NOT NULL
, origin TEXT NOT NULL DEFAULT 'human', created_by TEXT NOT NULL DEFAULT '', review_by TEXT, status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'stale', 'superseded')), superseded_by INTEGER, category TEXT NOT NULL DEFAULT '');

CREATE TABLE standups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author TEXT NOT NULL,
    yesterday TEXT DEFAULT '',
    today TEXT DEFAULT '',
    blockers TEXT DEFAULT '',
    created_at TEXT NOT NULL
, origin TEXT NOT NULL DEFAULT 'human', created_by TEXT NOT NULL DEFAULT '');

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    starts_at TEXT NOT NULL,
    ends_at TEXT DEFAULT NULL,
    attendees TEXT DEFAULT '',
    created_at TEXT NOT NULL
, origin TEXT NOT NULL DEFAULT 'human', created_by TEXT NOT NULL DEFAULT '');

CREATE TABLE notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    content TEXT NOT NULL,
    author TEXT DEFAULT '',
    created_at TEXT NOT NULL
, origin TEXT NOT NULL DEFAULT 'human', created_by TEXT NOT NULL DEFAULT '');

CREATE TABLE activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT DEFAULT '',
    created_at TEXT NOT NULL
, seq INTEGER, hash TEXT, prev_hash TEXT);

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'human' CHECK (kind IN ('human', 'agent')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
, growth_interests TEXT NOT NULL DEFAULT '', theme TEXT NOT NULL DEFAULT '');

CREATE TABLE blockers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    detail TEXT DEFAULT '',
    owner TEXT DEFAULT '',
    impact TEXT NOT NULL DEFAULT 'medium'
        CHECK (impact IN ('low', 'medium', 'high', 'critical')),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'escalated', 'resolved')),
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    source TEXT DEFAULT '',
    escalate_after_hours INTEGER NOT NULL DEFAULT 24,
    escalated_at TEXT,
    resolved_at TEXT,
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE intake_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    detail TEXT DEFAULT '',
    requester TEXT NOT NULL,
    project_class TEXT DEFAULT '',
    reach INTEGER NOT NULL DEFAULT 0,
    impact INTEGER NOT NULL DEFAULT 0,
    confidence INTEGER NOT NULL DEFAULT 0,
    effort INTEGER NOT NULL DEFAULT 1,
    score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted', 'scored', 'accepted', 'deferred', 'declined')),
    disposition_reason TEXT DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE pending_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    entity_id INTEGER,
    action TEXT NOT NULL CHECK (action IN ('create', 'update')),
    payload TEXT NOT NULL,
    summary TEXT DEFAULT '',
    proposed_by TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'agent',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_by TEXT,
    review_note TEXT DEFAULT '',
    reviewed_at TEXT,
    result_id INTEGER,
    created_at TEXT NOT NULL
, claim_at TEXT, requested_by TEXT, reviewed_strong INTEGER NOT NULL DEFAULT 0, reviewed_override INTEGER NOT NULL DEFAULT 0);

CREATE TABLE usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    agent_name TEXT NOT NULL DEFAULT 'chief-of-staff',
    model_id TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cycles INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
, cost_usd REAL);

CREATE TABLE engagements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    project_class TEXT NOT NULL DEFAULT 'general',
    summary TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('proposed', 'active', 'closing', 'closed')),
    lead TEXT DEFAULT '',
    started_at TEXT,
    closed_at TEXT,
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
, kind TEXT NOT NULL DEFAULT 'delivery'
    CHECK (kind IN ('delivery', 'experiment')), timebox_end TEXT, kill_criteria TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL DEFAULT '', conclusion TEXT
    CHECK (conclusion IN ('achieved', 'partial', 'missed', 'invalidated', 'unmeasured', 'stopped')));

CREATE TABLE allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,
    engagement_id INTEGER NOT NULL REFERENCES engagements(id) ON DELETE CASCADE,
    percent INTEGER NOT NULL DEFAULT 100,
    starts_on TEXT,
    ends_on TEXT,
    created_at TEXT NOT NULL
, origin TEXT NOT NULL DEFAULT 'human', created_by TEXT NOT NULL DEFAULT '');

CREATE TABLE lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id INTEGER REFERENCES engagements(id) ON DELETE SET NULL,
    project_class TEXT NOT NULL DEFAULT 'general',
    lesson TEXT NOT NULL,
    recommendation TEXT DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id INTEGER REFERENCES engagements(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    path TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE search_index USING fts5(
    entity UNINDEXED, entity_id UNINDEXED, title, body
);

CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    user TEXT NOT NULL DEFAULT '',
    thread_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
, origin TEXT NOT NULL DEFAULT 'human', created_by TEXT NOT NULL DEFAULT '');

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT 'digest'
        CHECK (tier IN ('immediate', 'digest', 'passive')),
    message TEXT NOT NULL,
    link TEXT DEFAULT '',
    sent_at TEXT,
    read_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_notifications_unread
    ON notifications (user, read_at);

CREATE TABLE job_runs (
    job TEXT NOT NULL,
    run_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job, run_key)
);

CREATE TABLE api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,
    owner TEXT NOT NULL,
    label TEXT DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);

CREATE TABLE agent_authority (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    entity TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'review'
        CHECK (level IN ('autonomous', 'notify', 'review', 'forbidden')),
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL, review_by TEXT,
    UNIQUE (agent, entity)
);

CREATE TABLE promises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    promise TEXT NOT NULL,
    to_whom TEXT NOT NULL DEFAULT '',
    engagement_id INTEGER REFERENCES engagements(id) ON DELETE SET NULL,
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'kept', 'missed', 'withdrawn')),
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
, audience TEXT NOT NULL DEFAULT 'external'
    CHECK (audience IN ('external', 'team')));

CREATE TABLE context_packs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX ux_context_packs_version
    ON context_packs(version);

CREATE INDEX idx_pending_changes_proposer_entity
    ON pending_changes(proposed_by, entity, id);

CREATE TABLE tool_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    user TEXT NOT NULL,
    surface TEXT NOT NULL,
    actions INTEGER NOT NULL DEFAULT 0,
    UNIQUE (day, user, surface)
);

CREATE TABLE forecast_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    milestone_id INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    forecast_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (day, milestone_id)
);

CREATE TABLE findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL CHECK (severity IN ('high', 'medium', 'low', 'positive')),
    message TEXT NOT NULL,
    n INTEGER,
    window TEXT NOT NULL DEFAULT '',
    receipt TEXT NOT NULL DEFAULT '{}',
    week TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (rule_id, subject, week)
);

CREATE INDEX idx_activity_actor ON activity(actor, created_at);

CREATE INDEX idx_activity_action ON activity(action, created_at);

CREATE INDEX idx_tasks_status ON tasks(status);

CREATE INDEX idx_blockers_status ON blockers(status);

CREATE INDEX idx_milestones_project ON milestones(project);

CREATE TABLE job_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    detail TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_job_outcomes_job ON job_outcomes (job, created_at);

CREATE TABLE finding_dispositions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id INTEGER NOT NULL REFERENCES findings(id),
    rule_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    disposition TEXT NOT NULL
        CHECK (disposition IN ('dismissed', 'deferred', 'converted', 'resolved')),
    reason TEXT NOT NULL DEFAULT '',
    deferred_until TEXT,
    created_by TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'human',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_finding_dispositions_rule
    ON finding_dispositions (rule_id, subject, created_at);

CREATE TABLE "feedback" (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('chat', 'capture', 'proposal', 'finding', 'pulse')),
    input TEXT NOT NULL,
    output TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL CHECK (verdict IN ('up', 'down', 'corrected')),
    correction TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE chat_threads (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT 'New chat',
    folder TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
, engagement_id INTEGER REFERENCES engagements(id));

CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL REFERENCES chat_threads(id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_chat_messages_thread ON chat_messages (thread_id, id);

CREATE TABLE chat_folders (
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner, name)
);

CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE absences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'pto' CHECK (kind IN ('pto', 'oncall', 'focus')),
    starts_on TEXT NOT NULL,
    ends_on TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_absences_window ON absences (person, starts_on, ends_on);

CREATE TABLE task_worklog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
, origin TEXT NOT NULL DEFAULT 'agent');

CREATE INDEX idx_worklog_task ON task_worklog (task_id);

CREATE TABLE feature_unlocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,
    knot TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'tied' CHECK (kind IN ('tied', 'dismissed')),
    seen INTEGER NOT NULL DEFAULT 0,
    first_at TEXT NOT NULL,
    UNIQUE (person, knot, kind)
);

CREATE INDEX idx_feature_unlocks_person ON feature_unlocks (person);

CREATE INDEX idx_feature_unlocks_knot ON feature_unlocks (knot);

CREATE TABLE embeddings (
    entity TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    vector TEXT NOT NULL,
    PRIMARY KEY (entity, entity_id)
);

CREATE UNIQUE INDEX idx_activity_seq ON activity(seq) WHERE seq IS NOT NULL;

CREATE TABLE mention_log (
    entity TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    person TEXT NOT NULL,
    mentioned_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (entity, entity_id, person)
);

CREATE INDEX idx_activity_unchained ON activity(seq) WHERE seq IS NULL;

CREATE INDEX idx_usage_log_created ON usage_log(created_at);

CREATE INDEX idx_standups_created ON standups(created_at);

CREATE INDEX idx_events_starts_at ON events(starts_at);

CREATE TABLE search_ids (
    id INTEGER PRIMARY KEY,
    entity TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    UNIQUE (entity, entity_id)
);

CREATE INDEX idx_tasks_assignee_due ON tasks(assignee, due_date);

CREATE INDEX idx_allocations_engagement ON allocations(engagement_id);

CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);

CREATE TABLE session_agents (
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (session_id, agent_id)
);

CREATE TABLE session_messages (
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (session_id, agent_id, message_id),
    FOREIGN KEY (session_id, agent_id)
        REFERENCES session_agents(session_id, agent_id) ON DELETE CASCADE
);

CREATE TABLE session_multi_agents (
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    multi_agent_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (session_id, multi_agent_id)
);

-- backs the NOCASE duplicate guard in services/engagements.py - the
-- pre-check and the INSERT are separate statements, so without this index
-- two concurrent creates of 'Alpha' and 'alpha' both land and fork usage
-- rollups across two near-identical engagements (ASCII fold, matching the
-- service check exactly)
CREATE UNIQUE INDEX ux_engagements_name_nocase ON engagements(name COLLATE NOCASE);

