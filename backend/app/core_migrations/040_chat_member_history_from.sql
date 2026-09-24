-- The first message an agent member may read (services/shared_chat_agents.py
-- `_prompt`). A new room agent starts at its join point unless the steward
-- shares the earlier history when adding it; the room's system message says
-- which, so every member sees it. 0 for every existing member: those agents
-- were added under the rule that shared the whole history.
ALTER TABLE chat_members ADD COLUMN history_from bigint NOT NULL DEFAULT 0;
