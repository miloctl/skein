-- deindex_record used to remove deleted records from search_index but not
-- from embeddings, so every deletion left an orphaned vector behind. Orphans
-- cannot leak content (snippets resolve through search_index) but they
-- outrank live records and burn one semantic result slot per query, forever.
-- The code now deletes both together — this cleans up what already leaked.
DELETE FROM embeddings WHERE NOT EXISTS (
    SELECT 1 FROM search_index
    WHERE search_index.entity = embeddings.entity
      AND search_index.entity_id = embeddings.entity_id
)
