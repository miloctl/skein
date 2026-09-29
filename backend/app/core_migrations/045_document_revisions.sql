-- Every save, agent edit and restore of a document is a numbered revision
-- (docs/intent/document-revisions.md). Rows, not files: they are in both
-- database dumps, and the cascade from artifacts covers every delete path.
-- No tier column. A revision takes its document's tier by join
-- (services/scope.py UNSCOPED), so no tier change needs a cascade here.
CREATE TABLE document_revisions (
    artifact_id bigint NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    revision integer NOT NULL CHECK (revision >= 1),
    body text NOT NULL,
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    author text NOT NULL,
    origin text NOT NULL CHECK (origin IN ('human', 'agent', 'agent_verified')),
    change_id bigint REFERENCES pending_changes(id) ON DELETE SET NULL,
    restored_from integer,
    created_at text NOT NULL,
    PRIMARY KEY (artifact_id, revision)
);
-- the erase deletes private proposals, and each deleted row otherwise scans
-- this table for the SET NULL
CREATE INDEX document_revisions_change_idx ON document_revisions (change_id)
    WHERE change_id IS NOT NULL;
