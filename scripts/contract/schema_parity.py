import sys

import psycopg


def schema(url):
    """Every table and column, as the catalog reports them.

    The catalog, not a dump: pg_dump output carries owners, comments and an
    ordering that differ between two freshly created databases, which would
    make every run a false failure.
    """
    with psycopg.connect(url) as connection:
        return connection.execute(
            "SELECT table_name, column_name, data_type, is_nullable, column_default"
            " FROM information_schema.columns WHERE table_schema = 'public'"
            " ORDER BY table_name, column_name"
        ).fetchall()


upgraded, fresh = schema(sys.argv[1]), schema(sys.argv[2])
if upgraded != fresh:
    print("upgraded-only:", [r for r in upgraded if r not in fresh][:10])
    print("fresh-only:", [r for r in fresh if r not in upgraded][:10])
    sys.exit("reference-extension-contract: upgraded schema differs from fresh")
