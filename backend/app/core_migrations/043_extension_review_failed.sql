-- A reviewed call that did not complete (refused before it ran, a failed
-- read, a timed-out read) is its own outcome. Recorded as 'approved', the
-- review page showed it as done and the reviewer never learned it failed.
ALTER TABLE extension_review_invocations
    DROP CONSTRAINT extension_review_invocations_status_check;
ALTER TABLE extension_review_invocations
    ADD CONSTRAINT extension_review_invocations_status_check
    CHECK (status IN ('pending', 'approved', 'rejected', 'completion_unknown', 'failed'));
