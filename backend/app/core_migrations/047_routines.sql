-- Weekly routines (docs/intent/routines.md). A routine is a template row, and
-- each firing is an ordinary task that carries routine_id. Firing state lives
-- on the row: next_at is the once-per-occurrence claim (services/routines.py
-- fire_due holds the row FOR UPDATE and advances it with the task). Every
-- reader sees paused_reason and last_outcome, so both are closed code sets and
-- never exception text.
CREATE TABLE routines (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title text NOT NULL,
    description text NOT NULL DEFAULT '',
    priority text NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    assignee text NOT NULL DEFAULT '',
    agent text NOT NULL DEFAULT '',
    acceptance_criteria text NOT NULL DEFAULT '',
    weekdays text NOT NULL CHECK (weekdays ~ '^[1-7](,[1-7]){0,6}$'),
    at_time text NOT NULL CHECK (at_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
    every_weeks bigint NOT NULL DEFAULT 1 CHECK (every_weeks BETWEEN 1 AND 4),
    starts_on text NOT NULL,
    due_days bigint CHECK (due_days BETWEEN 0 AND 27),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
    paused_reason text NOT NULL DEFAULT '' CHECK (paused_reason IN ('', 'by_person',
        'nobody_finished', 'owner_inactive', 'owner_left_crew', 'agent_unavailable',
        'fire_refused')),
    paused_by text NOT NULL DEFAULT '',
    next_at text,
    skipped_in_row bigint NOT NULL DEFAULT 0,
    last_outcome text NOT NULL DEFAULT ''
        CHECK (last_outcome IN ('', 'fired', 'late', 'previous_open')),
    last_outcome_at text,
    origin text NOT NULL DEFAULT 'human',
    created_by text NOT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    visibility text NOT NULL DEFAULT 'workspace'
        CHECK (visibility IN ('private', 'crew', 'workspace')),
    crew_id bigint REFERENCES crews(id),
    -- an agent routine delegates, and a delegated task has no other assignee
    CHECK (agent = '' OR assignee = ''),
    -- delegation.delegate_task refuses a private task, so the routine is refused first
    CHECK (agent = '' OR visibility <> 'private'),
    CHECK ((status = 'active') = (next_at IS NOT NULL))
);
CREATE INDEX idx_routines_due ON routines (next_at) WHERE status = 'active';
-- no foreign key: a deleted routine's tasks keep the id, so their
-- acceptances stay counted apart (docs/intent/routines.md D11)
ALTER TABLE tasks ADD COLUMN routine_id bigint;
CREATE INDEX idx_tasks_routine ON tasks (routine_id, id DESC) WHERE routine_id IS NOT NULL;
