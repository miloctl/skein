-- Rows that predate the review-policy contract cannot prove which policy and
-- identity created them. New proposals set this value to 1 when they enter the
-- current review service.
ALTER TABLE pending_changes ADD COLUMN review_contract_version INTEGER NOT NULL DEFAULT 0;
