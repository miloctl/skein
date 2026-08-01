-- Embeddings move from an ad-hoc CREATE TABLE inside services/search.py into
-- a real migration, and every vector is tagged with the model that produced
-- it: cosine similarity across two embedding spaces is noise, so changing
-- SKEIN_EMBED_MODEL must invalidate old vectors rather than silently poison
-- search results. Reads filter on the current model, and writes re-embed on
-- the next touch (INSERT OR REPLACE on the same key). NB the migration runner
-- splits on semicolons, including inside comments — do not use them in prose.
--
-- DROP rather than ALTER: vectors are a rebuildable best-effort cache (FTS
-- stays authoritative), and the old rows carry no record of which model wrote
-- them — rebuilding beats guessing.
DROP TABLE IF EXISTS embeddings;
CREATE TABLE embeddings (
    entity TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    vector TEXT NOT NULL,
    PRIMARY KEY (entity, entity_id)
);
