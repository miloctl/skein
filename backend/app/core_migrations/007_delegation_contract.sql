-- The first slice of the delegation contract (docs/reviews/2026-07-24-agent-sol.md
-- section 5): what done means, and when to hear from the delegate. Columns on
-- tasks rather than a contract table — the other contract fields have no
-- consumer yet, and a new entity costs seven gated registries. A season of
-- sponsors filling these two in is the evidence the full entity waits for.
ALTER TABLE tasks ADD COLUMN acceptance_criteria text NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN check_in_at text;
