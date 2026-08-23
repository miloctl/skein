-- A human verdict and a remote write outcome are separate facts. A timed-out
-- write can have completed remotely, so it stays settled and must not replay,
-- while the invocation records that completion needs reconciliation.
ALTER TABLE extension_review_invocations
    DROP CONSTRAINT extension_review_invocations_status_check;
ALTER TABLE extension_review_invocations
    ADD CONSTRAINT extension_review_invocations_status_check
    CHECK (status IN ('pending', 'approved', 'rejected', 'completion_unknown'));
