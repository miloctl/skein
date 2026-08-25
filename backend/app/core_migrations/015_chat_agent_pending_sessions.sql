CREATE INDEX idx_chat_agent_runs_pending_session
    ON chat_agent_runs (thread_id, agent, trigger_message_id, turn_id)
    WHERE status = 'pending';

CREATE INDEX idx_chat_agent_runs_pending_order
    ON chat_agent_runs (trigger_message_id, turn_id, thread_id, agent)
    WHERE status = 'pending';
