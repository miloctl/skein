-- The worker publishes the reply it has streamed so far while a run is
-- 'running'; the room shows it under "<agent> is responding…". Cleared on
-- every terminal status: the stored message is the only durable reply.
ALTER TABLE chat_agent_runs ADD COLUMN partial_text TEXT NOT NULL DEFAULT '';
