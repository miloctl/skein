-- question joins the waiting_on grammar: a task stuck behind an unanswered
-- question was invisible as a dependency, so the sweep nagged the sponsor
-- for progress on work that was waiting on the sponsor's own answer.
-- The unnamed CHECK from the baseline got the default name; both halves of
-- the pair constraint stay as they are.
ALTER TABLE tasks DROP CONSTRAINT tasks_waiting_on_type_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_waiting_on_type_check
    CHECK (waiting_on_type IN ('task', 'blocker', 'promise', 'question'));
