-- The dashboard home path (GET /api/briefing) full-scanned tasks twice per
-- load, per user, with only idx_tasks_status to work with.
--
-- tasks(assignee, due_date) serves BOTH halves: your_work.tasks filters on
-- assignee, and due_soon's `assignee IN (?, '')` is an equality constraint the
-- planner drives from the leading column. Verified with EXPLAIN QUERY PLAN --
-- both read SEARCH ... USING INDEX where they read SCAN before.
--
-- Deliberately NOT added: a partial tasks(due_date) WHERE status != 'done'
-- index for due_soon. The planner picks the assignee index for that query
-- instead, so the partial one would never be chosen and would cost a write on
-- every task insert and update to earn nothing.
--
-- allocations(engagement_id): list_engagements runs one allocations lookup per
-- engagement, up to its 200-row cap, and allocations carried NO index at all
-- -- every one of those was a full scan of the table.
CREATE INDEX idx_tasks_assignee_due ON tasks(assignee, due_date);
CREATE INDEX idx_allocations_engagement ON allocations(engagement_id)
