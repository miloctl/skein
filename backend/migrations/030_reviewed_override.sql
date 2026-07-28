-- sponsor-bound verdicts: 1 when someone other than the task's sponsor
-- judged a task_completion proposal (allowed only with a reason). Override
-- verdicts are provenance, not trust signals — streaks exclude them.
ALTER TABLE pending_changes ADD COLUMN reviewed_override INTEGER NOT NULL DEFAULT 0;
