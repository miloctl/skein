import sys

import psycopg

SCHEMAS = ("public", "private", "ext_atlas_contract")


def catalog(url):
    """Return the schema shapes whose fresh and upgraded forms must match."""
    with psycopg.connect(url) as connection:
        columns = connection.execute(
            "SELECT table_schema, table_name, ordinal_position, column_name,"
            " data_type, udt_name, is_nullable, column_default, is_identity,"
            " identity_generation, is_generated, generation_expression"
            " FROM information_schema.columns WHERE table_schema = ANY(%s)"
            " ORDER BY table_schema, table_name, ordinal_position",
            (list(SCHEMAS),),
        ).fetchall()
        constraints = connection.execute(
            "SELECT ns.nspname, rel.relname,"
            " CASE WHEN con.contype = 'n'"
            " AND con.conname ~ '^[0-9]+_[0-9]+_not_null$'"
            " THEN '<generated-not-null>' ELSE con.conname END AS constraint_name,"
            " con.contype, pg_get_constraintdef(con.oid, true)"
            " FROM pg_constraint con"
            " JOIN pg_class rel ON rel.oid = con.conrelid"
            " JOIN pg_namespace ns ON ns.oid = rel.relnamespace"
            " WHERE ns.nspname = ANY(%s)"
            " ORDER BY ns.nspname, rel.relname, constraint_name, con.contype,"
            " pg_get_constraintdef(con.oid, true)",
            (list(SCHEMAS),),
        ).fetchall()
        indexes = connection.execute(
            "SELECT schemaname, tablename, indexname, indexdef FROM pg_indexes"
            " WHERE schemaname = ANY(%s)"
            " ORDER BY schemaname, tablename, indexname",
            (list(SCHEMAS),),
        ).fetchall()
        sequences = connection.execute(
            "SELECT sequence_schema, sequence_name, data_type, start_value,"
            " minimum_value, maximum_value, increment, cycle_option"
            " FROM information_schema.sequences WHERE sequence_schema = ANY(%s)"
            " ORDER BY sequence_schema, sequence_name",
            (list(SCHEMAS),),
        ).fetchall()
    return columns, constraints, indexes, sequences


upgraded, fresh = catalog(sys.argv[1]), catalog(sys.argv[2])
if upgraded != fresh:
    for label, upgraded_rows, fresh_rows in zip(
        ("columns", "constraints", "indexes", "sequences"), upgraded, fresh, strict=True
    ):
        if upgraded_rows != fresh_rows:
            print(f"{label} upgraded-only:", [r for r in upgraded_rows if r not in fresh_rows][:10])
            print(f"{label} fresh-only:", [r for r in fresh_rows if r not in upgraded_rows][:10])
    sys.exit("reference-extension-contract: upgraded schema differs from fresh")
