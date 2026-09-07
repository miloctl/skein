-- A leased claim can be retaken after it lapses (services/leases.py); a
-- permanent claim keeps lease_until = '' and is never retaken. Existing rows
-- are permanent claims and receipts, which the '' default preserves.
ALTER TABLE job_runs
    ADD COLUMN lease_owner text NOT NULL DEFAULT '',
    ADD COLUMN lease_until text NOT NULL DEFAULT '';
