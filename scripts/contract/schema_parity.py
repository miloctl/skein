import os
import sys

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

SCHEMAS = ("public", "private", "ext_atlas_contract")


def catalog(url, schemas=SCHEMAS):
    """Return the schema shapes whose fresh and upgraded forms must match.

    Catalog rows, not a dump: a dump's owners, comments and ordering differ
    between two freshly created databases and would fail every run. The
    activity hash chain's guards are CHECK constraints
    (008_activity_chain_guards.sql), and a drift in one changes no column.
    No migration creates a trigger or a function today; they are compared so
    that the first one to do so is covered too.
    """
    names = list(schemas)
    with psycopg.connect(url) as connection:
        columns = connection.execute(
            "SELECT table_schema, table_name, ordinal_position, column_name,"
            " data_type, udt_name, is_nullable, column_default, is_identity,"
            " identity_generation, is_generated, generation_expression"
            " FROM information_schema.columns WHERE table_schema = ANY(%s)"
            " ORDER BY table_schema, table_name, ordinal_position",
            (names,),
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
            (names,),
        ).fetchall()
        indexes = connection.execute(
            "SELECT schemaname, tablename, indexname, indexdef FROM pg_indexes"
            " WHERE schemaname = ANY(%s)"
            " ORDER BY schemaname, tablename, indexname",
            (names,),
        ).fetchall()
        sequences = connection.execute(
            "SELECT sequence_schema, sequence_name, data_type, start_value,"
            " minimum_value, maximum_value, increment, cycle_option"
            " FROM information_schema.sequences WHERE sequence_schema = ANY(%s)"
            " ORDER BY sequence_schema, sequence_name",
            (names,),
        ).fetchall()
        triggers = connection.execute(
            "SELECT ns.nspname, rel.relname, tg.tgname, pg_get_triggerdef(tg.oid, true)"
            " FROM pg_trigger tg"
            " JOIN pg_class rel ON rel.oid = tg.tgrelid"
            " JOIN pg_namespace ns ON ns.oid = rel.relnamespace"
            " WHERE ns.nspname = ANY(%s) AND NOT tg.tgisinternal"
            " ORDER BY ns.nspname, rel.relname, tg.tgname",
            (names,),
        ).fetchall()
        functions = connection.execute(
            "SELECT ns.nspname, proc.proname,"
            " pg_get_function_identity_arguments(proc.oid), pg_get_functiondef(proc.oid)"
            " FROM pg_proc proc JOIN pg_namespace ns ON ns.oid = proc.pronamespace"
            " WHERE ns.nspname = ANY(%s) AND proc.prokind IN ('f', 'p')"
            " ORDER BY ns.nspname, proc.proname, 3",
            (names,),
        ).fetchall()
    return {
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "sequences": sequences,
        "triggers": triggers,
        "functions": functions,
    }


def differences(upgraded, fresh):
    """One line per side for every shape that differs, at most ten rows each."""
    lines = []
    for label, upgraded_rows in upgraded.items():
        fresh_rows = fresh[label]
        if upgraded_rows != fresh_rows:
            lines.append(
                f"{label} upgraded-only: {[r for r in upgraded_rows if r not in fresh_rows][:10]}"
            )
            lines.append(
                f"{label} fresh-only: {[r for r in fresh_rows if r not in upgraded_rows][:10]}"
            )
    return lines


def main():
    base = os.environ["SKEIN_DATABASE_URL"]
    fields = conninfo_to_dict(base)
    fields["dbname"] = sys.argv[1]
    fresh_url = make_conninfo(
        **{key: str(value) for key, value in fields.items() if value is not None}
    )
    lines = differences(catalog(base), catalog(fresh_url))
    if lines:
        print("\n".join(lines))
        sys.exit("reference-extension-contract: upgraded schema differs from fresh")


if __name__ == "__main__":
    main()
