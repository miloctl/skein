CREATE TABLE chat_agent_runs (
    turn_id text PRIMARY KEY,
    batch_id text NOT NULL,
    thread_id text NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    trigger_message_id bigint NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    response_message_id bigint REFERENCES chat_messages(id) ON DELETE SET NULL,
    agent text NOT NULL,
    requested_by text NOT NULL,
    requester_subject text NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN (
            'pending', 'running', 'completed', 'refused', 'failed', 'completion_unknown'
        )),
    requested_at text NOT NULL,
    started_at text,
    finished_at text,
    execution_active boolean NOT NULL DEFAULT FALSE,
    error_code text NOT NULL DEFAULT ''
        CHECK (error_code ~ '^[a-z_]{0,40}$')
);

CREATE UNIQUE INDEX uq_chat_agent_runs_trigger
    ON chat_agent_runs (thread_id, trigger_message_id, agent);

CREATE UNIQUE INDEX uq_chat_agent_runs_running
    ON chat_agent_runs (thread_id, agent)
    WHERE status = 'running';

CREATE INDEX idx_chat_agent_runs_pending
    ON chat_agent_runs (requested_at, turn_id)
    WHERE status = 'pending';

CREATE INDEX idx_chat_agent_runs_thread
    ON chat_agent_runs (thread_id, trigger_message_id, turn_id);

ALTER TABLE usage_log
    ADD COLUMN requested_by text NOT NULL DEFAULT '',
    ADD COLUMN trigger_message_id bigint REFERENCES chat_messages(id) ON DELETE SET NULL,
    ADD COLUMN chat_agent_run_id text REFERENCES chat_agent_runs(turn_id) ON DELETE SET NULL;

CREATE UNIQUE INDEX uq_usage_log_chat_agent_run
    ON usage_log (chat_agent_run_id)
    WHERE chat_agent_run_id IS NOT NULL;
