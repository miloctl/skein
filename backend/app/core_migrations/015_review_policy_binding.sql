-- Keep the exact policy input with a core agent proposal. Approval refreshes
-- the requester and evaluates this input through the currently composed policy.
ALTER TABLE pending_changes ADD COLUMN policy_context TEXT NOT NULL DEFAULT '{}';
