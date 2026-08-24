-- One durable wake state per agent. Human delegations coalesce while pending
-- or running, and terminal rows become pending on the next human delegation.
CREATE TABLE agent_wakeups (
    agent text PRIMARY KEY,
    status text NOT NULL,
    requested_by text NOT NULL,
    trigger_task_id integer NOT NULL,
    requested_at text NOT NULL,
    started_at text,
    finished_at text,
    attempts integer NOT NULL DEFAULT 0,
    rerun_requested integer NOT NULL DEFAULT 0,
    thread_id text NOT NULL DEFAULT '',
    reason text NOT NULL DEFAULT '',
    CONSTRAINT agent_wakeups_agent_present CHECK (length(agent) BETWEEN 1 AND 64),
    CONSTRAINT agent_wakeups_requester_present CHECK (length(requested_by) BETWEEN 1 AND 64),
    CONSTRAINT agent_wakeups_task_positive CHECK (trigger_task_id > 0),
    CONSTRAINT agent_wakeups_attempts_nonnegative CHECK (attempts >= 0),
    CONSTRAINT agent_wakeups_rerun_boolean CHECK (rerun_requested IN (0, 1)),
    CONSTRAINT agent_wakeups_reason_bounded CHECK (length(reason) <= 64),
    CONSTRAINT agent_wakeups_status_valid CHECK (
        status IN (
            'pending',
            'running',
            'completed',
            'refused',
            'failed',
            'completion_unknown'
        )
    )
);

CREATE INDEX idx_agent_wakeups_pending
    ON agent_wakeups (requested_at, agent)
    WHERE status = 'pending';
