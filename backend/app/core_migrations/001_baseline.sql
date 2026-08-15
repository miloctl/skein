-- Skein schema baseline (PostgreSQL).
--
-- Squashed from the 21 SQLite migrations that preceded the engine change. The
-- squash was legal exactly once: it happened before the first production
-- deploy, while every live database was still disposable. From here the
-- append-only rule in CLAUDE.md applies again — a new numbered file per
-- change, and this file never changes name (db.py records applied migrations
-- by filename, so a rename re-runs it on every existing database).
--
-- Timestamps are TEXT holding UTC ISO-8601, written by db.now(). NOT
-- timestamptz: every comparison in the service layer is lexicographic on that
-- shape, and the activity ledger HASHES the stored string — a type change
-- would rewrite the preimage of every chained row and break verification
-- permanently.

CREATE TABLE activity (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor text NOT NULL,
    action text NOT NULL,
    detail text DEFAULT '',
    created_at text NOT NULL
, seq bigint, hash text, prev_hash text);

CREATE TABLE agent_authority (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent text NOT NULL,
    entity text NOT NULL,
    level text NOT NULL DEFAULT 'review'
        CHECK (level IN ('autonomous', 'notify', 'review', 'forbidden')),
    updated_by text NOT NULL DEFAULT '',
    updated_at text NOT NULL, review_by text,
    UNIQUE (agent, entity)
);

CREATE TABLE api_keys (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key_hash text NOT NULL UNIQUE,
    prefix text NOT NULL,
    owner text NOT NULL,
    label text DEFAULT '',
    active bigint NOT NULL DEFAULT 1,
    created_at text NOT NULL,
    last_used_at text
);

CREATE TABLE app_settings (
    key text PRIMARY KEY,
    value text NOT NULL DEFAULT '',
    updated_at text NOT NULL
);

CREATE TABLE chat_folders (
    owner text NOT NULL,
    name text NOT NULL,
    created_at text NOT NULL,
    PRIMARY KEY (owner, name)
);

CREATE TABLE crews (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL UNIQUE,
    summary text NOT NULL DEFAULT '',
    -- deactivate rather than delete, the same word and the same shape as
    -- users.active: a crew that
    -- scoped rows must keep resolving after it stops being used, or those
    -- rows name a crew id that no longer exists
    active bigint NOT NULL DEFAULT 1,
    origin text NOT NULL DEFAULT 'human',
    created_by text NOT NULL DEFAULT '',
    created_at text NOT NULL,
    updated_at text NOT NULL
);

CREATE TABLE embeddings (
    entity text NOT NULL,
    entity_id bigint NOT NULL,
    model text NOT NULL,
    vector text NOT NULL,
    PRIMARY KEY (entity, entity_id)
);

CREATE TABLE extension_command_receipts (
    namespace text NOT NULL,
    idempotency_key text NOT NULL,
    result_type text NOT NULL,
    result_id bigint NOT NULL,
    created_at text NOT NULL,
    PRIMARY KEY (namespace, idempotency_key)
);

CREATE TABLE extension_outbox (
    -- Insertion order for the delivery sweep. created_at is second-precision,
    -- so it cannot break a tie on its own, and event_id is a text id with no
    -- order at all. public/events.py orders by (created_at, seq); without this
    -- column a task's update can be delivered before its creation.
    seq bigint GENERATED ALWAYS AS IDENTITY,
    event_id text PRIMARY KEY,
    event_type text NOT NULL,
    schema_version bigint NOT NULL,
    payload text NOT NULL,
    visibility text NOT NULL DEFAULT 'workspace',
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'delivered', 'dead')),
    attempts bigint NOT NULL DEFAULT 0,
    last_error_code text NOT NULL DEFAULT '',
    created_at text NOT NULL,
    delivered_at text
);

CREATE TABLE feature_unlocks (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person text NOT NULL,
    knot text NOT NULL,
    kind text NOT NULL DEFAULT 'tied' CHECK (kind IN ('tied', 'dismissed')),
    seen bigint NOT NULL DEFAULT 0,
    first_at text NOT NULL,
    UNIQUE (person, knot, kind)
);

CREATE TABLE "feedback" (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('chat', 'capture', 'proposal', 'finding', 'pulse')),
    input text NOT NULL,
    output text NOT NULL DEFAULT '',
    verdict text NOT NULL CHECK (verdict IN ('up', 'down', 'corrected')),
    correction text NOT NULL DEFAULT '',
    created_by text NOT NULL DEFAULT '',
    created_at text NOT NULL
);

CREATE TABLE findings (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_id text NOT NULL,
    subject text NOT NULL DEFAULT '',
    severity text NOT NULL CHECK (severity IN ('high', 'medium', 'low', 'positive')),
    message text NOT NULL,
    n bigint,
    "window" text NOT NULL DEFAULT '',
    receipt text NOT NULL DEFAULT '{}',
    week text NOT NULL,
    created_at text NOT NULL,
    UNIQUE (rule_id, subject, week)
);

CREATE TABLE flock_traces (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id text NOT NULL,
    "user" text NOT NULL,
    flock text NOT NULL,
    -- JSON array: [{slug, name, status, ms, receipts, tokens_in, tokens_out}]
    -- status is one of ok | failed | cancelled
    members text NOT NULL,
    -- NULL when the flock does not synthesize. Otherwise the same object
    -- shape as one member. The diamond's bottom node needs data, not a flag.
    -- No semicolons in this file, comments included -- db.py::_statements
    -- splits on them and the tail half is a syntax error at startup.
    synthesis text,
    created_at text NOT NULL
);

CREATE TABLE forecast_snapshots (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    day text NOT NULL,
    milestone_id bigint NOT NULL,
    due_date text NOT NULL,
    forecast_date text NOT NULL,
    created_at text NOT NULL,
    UNIQUE (day, milestone_id)
);

CREATE TABLE health_snapshots (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    day text NOT NULL,
    engagement_id bigint NOT NULL,
    health text NOT NULL,
    status text NOT NULL DEFAULT '',
    created_at text NOT NULL
);

CREATE TABLE job_outcomes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job text NOT NULL,
    status text NOT NULL CHECK (status IN ('ok', 'error')),
    detail text NOT NULL DEFAULT '',
    duration_ms bigint NOT NULL DEFAULT 0,
    created_at text NOT NULL
);

CREATE TABLE job_runs (
    job text NOT NULL,
    run_key text NOT NULL,
    created_at text NOT NULL,
    PRIMARY KEY (job, run_key)
);

CREATE TABLE mention_log (
    entity text NOT NULL,
    entity_id bigint NOT NULL,
    person text NOT NULL,
    mentioned_by text NOT NULL,
    created_at text NOT NULL,
    PRIMARY KEY (entity, entity_id, person)
);

CREATE TABLE notifications (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    "user" text NOT NULL DEFAULT '',
    tier text NOT NULL DEFAULT 'digest'
        CHECK (tier IN ('immediate', 'digest', 'passive')),
    message text NOT NULL,
    link text DEFAULT '',
    sent_at text,
    read_at text,
    created_at text NOT NULL
, source_entity text NOT NULL DEFAULT '', source_id bigint, source_policy_context text NOT NULL DEFAULT '{}');

CREATE TABLE pending_changes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity text NOT NULL,
    entity_id bigint,
    action text NOT NULL CHECK (action IN ('create', 'update')),
    payload text NOT NULL,
    summary text DEFAULT '',
    proposed_by text NOT NULL,
    origin text NOT NULL DEFAULT 'agent',
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_by text,
    review_note text DEFAULT '',
    reviewed_at text,
    result_id bigint,
    created_at text NOT NULL
, claim_at text, requested_by text, reviewed_strong bigint NOT NULL DEFAULT 0, reviewed_override bigint NOT NULL DEFAULT 0, sponsor_at_submission text, policy_obligations text NOT NULL DEFAULT '[]', approver_groups text NOT NULL DEFAULT '[]', approver_capabilities text NOT NULL DEFAULT '[]', review_visibility text NOT NULL DEFAULT 'workspace'
    CHECK (review_visibility IN ('private', 'crew', 'workspace')), review_crew_id bigint, review_owner text NOT NULL DEFAULT '', reviewer_qualifications text NOT NULL DEFAULT '{}', policy_context text NOT NULL DEFAULT '{}', review_contract_version bigint NOT NULL DEFAULT 0);

CREATE TABLE sessions (
    session_id text PRIMARY KEY,
    payload text NOT NULL
);

CREATE TABLE tool_usage (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    day text NOT NULL,
    "user" text NOT NULL,
    surface text NOT NULL,
    actions bigint NOT NULL DEFAULT 0,
    UNIQUE (day, "user", surface)
);

CREATE TABLE usage_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id text NOT NULL,
    agent_name text NOT NULL DEFAULT 'chief-of-staff',
    model_id text NOT NULL,
    input_tokens bigint NOT NULL DEFAULT 0,
    output_tokens bigint NOT NULL DEFAULT 0,
    cycles bigint NOT NULL DEFAULT 0,
    latency_ms bigint NOT NULL DEFAULT 0,
    created_at text NOT NULL
, cost_usd double precision);

CREATE TABLE users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL UNIQUE,
    kind text NOT NULL DEFAULT 'human' CHECK (kind IN ('human', 'agent')),
    active bigint NOT NULL DEFAULT 1,
    created_at text NOT NULL
, growth_interests text NOT NULL DEFAULT '', theme text NOT NULL DEFAULT '', identity_owner text NOT NULL DEFAULT '');

CREATE TABLE absences (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person text NOT NULL,
    kind text NOT NULL DEFAULT 'pto' CHECK (kind IN ('pto', 'oncall', 'focus')),
    starts_on text NOT NULL,
    ends_on text NOT NULL,
    note text NOT NULL DEFAULT '',
    origin text NOT NULL DEFAULT 'human',
    created_by text NOT NULL DEFAULT '',
    created_at text NOT NULL
, visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id));

CREATE TABLE context_packs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version bigint NOT NULL,
    content text NOT NULL,
    content_hash text NOT NULL,
    created_by text NOT NULL DEFAULT '',
    created_at text NOT NULL
, crew_id bigint REFERENCES crews(id));

CREATE TABLE crew_members (
    crew_id bigint NOT NULL REFERENCES crews(id) ON DELETE CASCADE,
    -- a roster name, matching every other person column in the schema. There
    -- are no foreign keys to users(name) anywhere here, so services/users.py
    -- ::_ATTRIBUTION carries this column and rename_user moves it -- left out
    -- of that map, a rename orphans the membership and the person silently
    -- loses every crew row they could see.
    person text NOT NULL,
    -- a steward edits this crew's membership. It is NOT an authorization tier
    -- of its own: AdminUser stays deployment-wide, and a steward administers
    -- nothing outside these rows.
    role text NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'steward')),
    -- provenance, like the allocations row this most resembles: membership
    -- decides what a person reads, so "who put them here, through which
    -- path" is the question asked after an incident
    origin text NOT NULL DEFAULT 'human',
    created_by text NOT NULL DEFAULT '',
    created_at text NOT NULL,
    PRIMARY KEY (crew_id, person)
);

CREATE TABLE decisions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title text NOT NULL,
    context text DEFAULT '',
    decision text NOT NULL,
    decided_by text DEFAULT '',
    created_at text NOT NULL
, origin text NOT NULL DEFAULT 'human', created_by text NOT NULL DEFAULT '', review_by text, status text NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'stale', 'superseded')), superseded_by bigint, category text NOT NULL DEFAULT '', visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id));

CREATE TABLE engagements (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL UNIQUE,
    project_class text NOT NULL DEFAULT 'general',
    summary text DEFAULT '',
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('proposed', 'active', 'closing', 'closed')),
    lead text DEFAULT '',
    started_at text,
    closed_at text,
    origin text NOT NULL DEFAULT 'human',
    created_by text NOT NULL DEFAULT '',
    created_at text NOT NULL,
    updated_at text NOT NULL
, kind text NOT NULL DEFAULT 'delivery'
    CHECK (kind IN ('delivery', 'experiment')), timebox_end text, kill_criteria text NOT NULL DEFAULT '', outcome text NOT NULL DEFAULT '', conclusion text
    CHECK (conclusion IN ('achieved', 'partial', 'missed', 'invalidated', 'unmeasured', 'stopped')), visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id));

CREATE TABLE extension_event_attempts (
    event_id text NOT NULL REFERENCES extension_outbox(event_id) ON DELETE CASCADE,
    subscriber text NOT NULL,
    attempts bigint NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'dead')),
    last_error_code text NOT NULL DEFAULT '',
    updated_at text NOT NULL,
    PRIMARY KEY (event_id, subscriber)
);

CREATE TABLE extension_event_deliveries (
    event_id text NOT NULL REFERENCES extension_outbox(event_id) ON DELETE CASCADE,
    subscriber text NOT NULL,
    delivered_at text NOT NULL,
    PRIMARY KEY (event_id, subscriber)
);

CREATE TABLE "extension_review_invocations" (
    -- The pending_changes id, not one of its own: this table is 1:1 with a
    -- review and the caller supplies the key. An identity column here
    -- would refuse that insert outright.
    change_id bigint PRIMARY KEY,
    kind text NOT NULL
        CHECK (kind IN ('tool', 'workflow', 'mcp_tool', 'core_tool', 'public_command')),
    invocation text NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    result text NOT NULL DEFAULT '{}',
    error_code text NOT NULL DEFAULT '',
    executed_at text,
    FOREIGN KEY (change_id) REFERENCES pending_changes(id)
);

CREATE TABLE finding_dispositions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    finding_id bigint NOT NULL REFERENCES findings(id),
    rule_id text NOT NULL,
    subject text NOT NULL,
    disposition text NOT NULL
        CHECK (disposition IN ('dismissed', 'deferred', 'converted', 'resolved')),
    reason text NOT NULL DEFAULT '',
    deferred_until text,
    created_by text NOT NULL DEFAULT '',
    origin text NOT NULL DEFAULT 'human',
    created_at text NOT NULL
);

CREATE TABLE intake_requests (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title text NOT NULL,
    detail text DEFAULT '',
    requester text NOT NULL,
    project_class text DEFAULT '',
    reach bigint NOT NULL DEFAULT 0,
    impact bigint NOT NULL DEFAULT 0,
    confidence bigint NOT NULL DEFAULT 0,
    effort bigint NOT NULL DEFAULT 1,
    score double precision NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted', 'scored', 'accepted', 'deferred', 'declined')),
    disposition_reason text DEFAULT '',
    origin text NOT NULL DEFAULT 'human',
    created_by text NOT NULL DEFAULT '',
    created_at text NOT NULL,
    updated_at text NOT NULL
, visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id));

CREATE TABLE notes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    topic text NOT NULL,
    content text NOT NULL,
    author text DEFAULT '',
    created_at text NOT NULL
, origin text NOT NULL DEFAULT 'human', created_by text NOT NULL DEFAULT '', visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id));

CREATE TABLE notification_reads (
    notification_id bigint NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    "user" text NOT NULL,
    read_at text NOT NULL,
    PRIMARY KEY (notification_id, "user")
);

CREATE TABLE questions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asked_by text NOT NULL,
    assigned_to text DEFAULT '',
    question text NOT NULL,
    answer text DEFAULT NULL,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered')),
    created_at text NOT NULL,
    answered_at text DEFAULT NULL
, origin text NOT NULL DEFAULT 'human', created_by text NOT NULL DEFAULT '', source_finding_id bigint REFERENCES findings(id), visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id));

CREATE TABLE session_agents (
    session_id text NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    agent_id text NOT NULL,
    payload text NOT NULL,
    PRIMARY KEY (session_id, agent_id)
);

CREATE TABLE session_multi_agents (
    session_id text NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    multi_agent_id text NOT NULL,
    payload text NOT NULL,
    PRIMARY KEY (session_id, multi_agent_id)
);

CREATE TABLE standups (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    author text NOT NULL,
    yesterday text DEFAULT '',
    today text DEFAULT '',
    blockers text DEFAULT '',
    created_at text NOT NULL
, origin text NOT NULL DEFAULT 'human', created_by text NOT NULL DEFAULT '', visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id));

CREATE TABLE task_worklog (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id bigint NOT NULL,
    author text NOT NULL,
    note text NOT NULL,
    created_at text NOT NULL
, origin text NOT NULL DEFAULT 'agent', visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id));

CREATE TABLE allocations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person text NOT NULL,
    engagement_id bigint NOT NULL REFERENCES engagements(id) ON DELETE CASCADE,
    percent bigint NOT NULL DEFAULT 100,
    starts_on text,
    ends_on text,
    created_at text NOT NULL
, origin text NOT NULL DEFAULT 'human', created_by text NOT NULL DEFAULT '');

CREATE TABLE artifacts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    engagement_id bigint REFERENCES engagements(id) ON DELETE SET NULL,
    kind text NOT NULL,
    title text NOT NULL,
    path text NOT NULL,
    created_by text NOT NULL DEFAULT '',
    created_at text NOT NULL
, visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id));

CREATE TABLE chat_threads (
    id text PRIMARY KEY,
    owner text NOT NULL,
    title text NOT NULL DEFAULT 'New chat',
    folder text NOT NULL DEFAULT '',
    created_at text NOT NULL,
    updated_at text NOT NULL
, engagement_id bigint REFERENCES engagements(id));

CREATE TABLE events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title text NOT NULL,
    description text DEFAULT '',
    starts_at text NOT NULL,
    ends_at text DEFAULT NULL,
    attendees text DEFAULT '',
    created_at text NOT NULL
, origin text NOT NULL DEFAULT 'human', created_by text NOT NULL DEFAULT '', visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id), agenda text NOT NULL DEFAULT '', engagement_id bigint
    REFERENCES engagements(id) ON DELETE SET NULL, outcome_status text NOT NULL DEFAULT 'pending'
    CHECK (outcome_status IN ('pending', 'recorded', 'none')));

CREATE TABLE lessons (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    engagement_id bigint REFERENCES engagements(id) ON DELETE SET NULL,
    project_class text NOT NULL DEFAULT 'general',
    lesson text NOT NULL,
    recommendation text DEFAULT '',
    origin text NOT NULL DEFAULT 'human',
    created_by text NOT NULL DEFAULT '',
    created_at text NOT NULL
, visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id));

CREATE TABLE memories (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    topic text NOT NULL DEFAULT '',
    content text NOT NULL,
    "user" text NOT NULL DEFAULT '',
    thread_id text NOT NULL DEFAULT '',
    created_at text NOT NULL
, origin text NOT NULL DEFAULT 'human', created_by text NOT NULL DEFAULT '', visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id), engagement_id bigint REFERENCES engagements(id), source_kind text NOT NULL DEFAULT '', source_id text NOT NULL DEFAULT '');

CREATE TABLE milestones (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project text NOT NULL DEFAULT 'default',
    title text NOT NULL,
    description text DEFAULT '',
    status text NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'in_progress', 'blocked', 'done')),
    owner text DEFAULT '',
    due_date text DEFAULT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL
, origin text NOT NULL DEFAULT 'human', created_by text NOT NULL DEFAULT '', engagement_id bigint REFERENCES engagements(id), completed_at text, visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id));

CREATE TABLE promises (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    promise text NOT NULL,
    to_whom text NOT NULL DEFAULT '',
    engagement_id bigint REFERENCES engagements(id) ON DELETE SET NULL,
    due_date text,
    status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'kept', 'missed', 'withdrawn')),
    origin text NOT NULL DEFAULT 'human',
    created_by text NOT NULL DEFAULT '',
    created_at text NOT NULL,
    updated_at text NOT NULL
, audience text NOT NULL DEFAULT 'external'
    CHECK (audience IN ('external', 'team')), visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id), direction text NOT NULL DEFAULT 'given'
    CHECK (direction IN ('given', 'received')), last_nudged_at text, nudge_count bigint NOT NULL DEFAULT 0);

CREATE TABLE session_messages (
    session_id text NOT NULL,
    agent_id text NOT NULL,
    message_id bigint NOT NULL,
    payload text NOT NULL,
    PRIMARY KEY (session_id, agent_id, message_id),
    FOREIGN KEY (session_id, agent_id)
        REFERENCES session_agents(session_id, agent_id) ON DELETE CASCADE
);

CREATE TABLE chat_messages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id text NOT NULL REFERENCES chat_threads(id),
    role text NOT NULL CHECK (role IN ('user', 'assistant')),
    content text NOT NULL,
    created_at text NOT NULL
);

CREATE TABLE tasks (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    milestone_id bigint REFERENCES milestones(id) ON DELETE SET NULL,
    title text NOT NULL,
    description text DEFAULT '',
    status text NOT NULL DEFAULT 'todo'
        CHECK (status IN ('todo', 'in_progress', 'blocked', 'done')),
    assignee text DEFAULT '',
    priority text NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    due_date text DEFAULT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL
, origin text NOT NULL DEFAULT 'human', created_by text NOT NULL DEFAULT '', committed_week text, delegated_agent text NOT NULL DEFAULT '', sponsor text NOT NULL DEFAULT '', completed_at text, source_finding_id bigint REFERENCES findings(id), waiting_on_type text
    CHECK (waiting_on_type IN ('task', 'blocker', 'promise')), waiting_on_id bigint, engagement_id bigint REFERENCES engagements(id), forge_url text NOT NULL DEFAULT '', visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id),
-- both halves of the waiting_on pair or neither: a half-set pair renders
-- "waiting on task #None" receipts and feeds NULL into the settled probe
    CHECK ((waiting_on_type IS NULL) = (waiting_on_id IS NULL)));

CREATE TABLE blockers (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title text NOT NULL,
    detail text DEFAULT '',
    owner text DEFAULT '',
    impact text NOT NULL DEFAULT 'medium'
        CHECK (impact IN ('low', 'medium', 'high', 'critical')),
    status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'escalated', 'resolved')),
    task_id bigint REFERENCES tasks(id) ON DELETE SET NULL,
    source text DEFAULT '',
    escalate_after_hours bigint NOT NULL DEFAULT 24,
    escalated_at text,
    resolved_at text,
    origin text NOT NULL DEFAULT 'human',
    created_by text NOT NULL DEFAULT '',
    created_at text NOT NULL,
    updated_at text NOT NULL
, visibility text NOT NULL DEFAULT 'workspace'
    CHECK (visibility IN ('private', 'crew', 'workspace')), crew_id bigint REFERENCES crews(id));

CREATE INDEX idx_absences_window ON absences (person, starts_on, ends_on);

CREATE INDEX idx_activity_action ON activity(action, created_at);

CREATE INDEX idx_activity_actor ON activity(actor, created_at);

CREATE UNIQUE INDEX idx_activity_seq ON activity(seq) WHERE seq IS NOT NULL;

CREATE INDEX idx_activity_unchained ON activity(seq) WHERE seq IS NULL;

CREATE INDEX idx_allocations_engagement ON allocations(engagement_id);

CREATE INDEX idx_artifacts_engagement_link
    ON artifacts (engagement_id) WHERE engagement_id IS NOT NULL;

CREATE INDEX idx_blockers_status ON blockers(status);

CREATE INDEX idx_blockers_task_link
    ON blockers (task_id) WHERE task_id IS NOT NULL;

CREATE INDEX idx_chat_messages_thread ON chat_messages (thread_id, id);

CREATE INDEX idx_crew_members_person ON crew_members (person, crew_id);

CREATE INDEX idx_events_engagement_link
    ON events (engagement_id) WHERE engagement_id IS NOT NULL;

CREATE INDEX idx_events_outcome
    ON events (outcome_status, starts_at);

CREATE INDEX idx_events_starts_at ON events(starts_at);

CREATE INDEX idx_extension_outbox_delivery
    ON extension_outbox (status, created_at);

CREATE INDEX idx_feature_unlocks_knot ON feature_unlocks (knot);

CREATE INDEX idx_feature_unlocks_person ON feature_unlocks (person);

CREATE INDEX idx_finding_dispositions_rule
    ON finding_dispositions (rule_id, subject, created_at);

CREATE INDEX idx_flock_traces_flock ON flock_traces(flock);

CREATE INDEX idx_flock_traces_thread ON flock_traces(thread_id);

CREATE UNIQUE INDEX idx_health_snapshots_day
    ON health_snapshots (day, engagement_id);

CREATE INDEX idx_health_snapshots_eng
    ON health_snapshots (engagement_id, day);

CREATE INDEX idx_job_outcomes_job ON job_outcomes (job, created_at);

CREATE INDEX idx_lessons_engagement_link
    ON lessons (engagement_id) WHERE engagement_id IS NOT NULL;

CREATE INDEX idx_memories_engagement
    ON memories (engagement_id) WHERE engagement_id IS NOT NULL;

CREATE INDEX idx_memories_engagement_link
    ON memories (engagement_id) WHERE engagement_id IS NOT NULL;

CREATE INDEX idx_milestones_engagement_link
    ON milestones (engagement_id) WHERE engagement_id IS NOT NULL;

CREATE INDEX idx_milestones_project ON milestones(project);

CREATE INDEX idx_notification_reads_user ON notification_reads ("user");

CREATE INDEX idx_notifications_source
    ON notifications (source_entity, source_id);

CREATE INDEX idx_notifications_unread
    ON notifications ("user", read_at);

CREATE INDEX idx_pending_changes_proposer_entity
    ON pending_changes(proposed_by, entity, id);

CREATE INDEX idx_promises_direction_status
    ON promises (direction, status, due_date);

CREATE INDEX idx_promises_engagement_link
    ON promises (engagement_id) WHERE engagement_id IS NOT NULL;

CREATE INDEX idx_standups_created ON standups(created_at);

CREATE INDEX idx_tasks_assignee_due ON tasks(assignee, due_date);

CREATE INDEX idx_tasks_engagement_link
    ON tasks (engagement_id) WHERE engagement_id IS NOT NULL;

CREATE INDEX idx_tasks_milestone_link
    ON tasks (milestone_id) WHERE milestone_id IS NOT NULL;

CREATE INDEX idx_tasks_status ON tasks(status);

CREATE INDEX idx_usage_log_created ON usage_log(created_at);

CREATE INDEX idx_worklog_task ON task_worklog (task_id);

CREATE UNIQUE INDEX ux_context_packs_crew_version
    ON context_packs (COALESCE(crew_id, 0), version);

CREATE UNIQUE INDEX ux_crews_name_nocase ON crews (lower(name));

CREATE UNIQUE INDEX ux_engagements_name_nocase ON engagements (lower(name));

-- Full-text search. FTS5 was a virtual table with an external-rowid twin
-- (search_ids) that existed only because FTS5 remints freed rowids: the twin
-- held the mapping so a deleted row could not hand its id to a bystander.
-- A real table with an identity column has no such failure, so the twin is
-- gone and search.py addresses rows by (entity, entity_id) directly.
--
-- tsv is STORED and generated, so it can never disagree with the text beside
-- it — the FTS5 index had to be written by hand on every index_record call.
-- Title outranks body (setweight A over B), which bm25 over equally-weighted
-- columns did not do.
CREATE TABLE search_index (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity text NOT NULL,
    entity_id bigint NOT NULL,
    title text NOT NULL DEFAULT '',
    body text NOT NULL DEFAULT '',
    tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', title), 'A')
        || setweight(to_tsvector('english', body), 'B')
    ) STORED,
    UNIQUE (entity, entity_id)
);

CREATE INDEX idx_search_index_tsv ON search_index USING GIN (tsv);
