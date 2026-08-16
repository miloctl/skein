-- Uploaded files ride the artifacts table: it already carries visibility,
-- crew_id, created_by and the containment root under data/artifacts, and a
-- second store would mean a second scoping model beside the one the policy
-- engine filters on.
--
-- mime and size are stored rather than read off the file, because the
-- download route answers from the ROW. Reading the file to decide what to
-- claim it is would hand a rewritten file the right to change its own
-- Content-Type after the format allowlist accepted it.
ALTER TABLE artifacts
ADD COLUMN mime text NOT NULL DEFAULT '',
ADD COLUMN size bigint NOT NULL DEFAULT 0,
-- What an agent-written artifact was derived FROM. services/uploads.py floors
-- the derived row's visibility to the source's: without it, an agent asked to
-- summarize a private upload writes the summary at the workspace tier by
-- default, and one workspace holds everyone.
ADD COLUMN derived_from bigint REFERENCES artifacts(id) ON DELETE SET NULL;

CREATE INDEX artifacts_derived_from_idx
ON artifacts (derived_from)
WHERE derived_from IS NOT NULL;

-- The quota sums a person's uploads on every upload.
CREATE INDEX artifacts_upload_owner_idx
ON artifacts (created_by)
WHERE kind = 'upload';
