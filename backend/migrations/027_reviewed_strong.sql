-- Verdict provenance: whether the reviewer held strong identity when the
-- verdict landed. Weak-identity verdicts still apply (keyless-first) but are
-- excluded from agent trust-promotion suggestions.
ALTER TABLE pending_changes ADD COLUMN reviewed_strong INTEGER NOT NULL DEFAULT 0;
