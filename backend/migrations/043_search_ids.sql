-- entity and entity_id are UNINDEXED in the FTS5 table (004), so every
-- DELETE FROM search_index WHERE entity = ... scanned the whole virtual
-- table -- under the write lock, on every service write, since index_record
-- runs on each create and update. search_ids gives (entity, entity_id) an
-- indexed home whose id IS the FTS rowid, so deletes and point reads in
-- services/search.py become rowid lookups.
CREATE TABLE search_ids (
    id INTEGER PRIMARY KEY,
    entity TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    UNIQUE (entity, entity_id)
);

-- backfill from live rowids. index_record deletes before it inserts, so
-- (entity, entity_id) is already unique -- a duplicate here must fail the
-- migration loudly rather than silently orphan an FTS row.
INSERT INTO search_ids (id, entity, entity_id)
    SELECT rowid, entity, entity_id FROM search_index;
