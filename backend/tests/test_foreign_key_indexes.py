"""Foreign keys a delete has to check need an index on the referencing column.

Deleting a chat message makes PostgreSQL find every row that references it,
and without an index that is a scan of the whole referencing table. usage_log
is kept forever, so deleting one 500-message chat took 6 s against 100k usage
rows, and 0.04 s with the index."""


def test_every_reference_to_a_chat_message_is_indexed(fresh_db):
    rows = fresh_db.query(
        "SELECT c.conrelid::regclass::text AS tbl, a.attname AS col"
        " FROM pg_constraint c"
        " JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]"
        " WHERE c.contype = 'f' AND c.confrelid = 'chat_messages'::regclass"
        " AND NOT EXISTS ("
        "  SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1])"
    )
    assert rows == []
