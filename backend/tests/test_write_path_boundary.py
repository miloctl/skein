"""Two write paths, one service layer: SQL lives in app/services/ alone.

Humans mutate via REST, agents via Strands tools, and both must call the shared
functions in app/services/ - a route or tool that writes its own SQL bypasses
provenance and the activity ledger with nothing to notice. This pins the rule
for every file in routes/ and tools/, not one instance.
"""

import ast
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

# `db.execute("DELETE FROM ...")` and friends. Matches the statement inside a
# string literal, which is how SQL reaches db.execute here.
# The table name can be a schema-qualified name or an f-string field
# (`UPDATE {table} SET`), and MERGE writes too. The allowlist test below is the
# stronger net; this one also catches SQL handed to a helper as a string.
SQL = re.compile(
    r"""["'(\s](?:SELECT\s+.+?\s+FROM|INSERT\s+INTO|UPDATE\s+[\w.{}]+\s+SET"""
    r"""|DELETE\s+FROM|MERGE\s+INTO)\s""",
    re.I | re.S,
)


def _sql_bearing_lines(path: Path) -> list[str]:
    return [
        f"{path.relative_to(APP.parent)}:{n}: {line.strip()[:90]}"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if SQL.search(line)
    ]


@pytest.mark.parametrize("layer", ["routes", "tools"])
def test_no_sql_outside_the_service_layer(layer):
    offenders = [
        hit for path in sorted((APP / layer).rglob("*.py")) for hit in _sql_bearing_lines(path)
    ]
    assert not offenders, (
        f"SQL in app/{layer}/ - move it behind a function in app/services/, "
        "or the write skips provenance and the activity ledger:\n" + "\n".join(offenders)
    )


# What a route or a tool may take from app/db.py: transaction scopes, the
# team clock, and the error types it catches. Every query and write function
# is missing on purpose - SQL can reach the database only through them, so
# this blocks every SQL shape at once, including the ones the regex above does
# not anticipate.
DB_ALLOWED = frozenset(
    {
        "transaction",
        "read_transaction",
        "savepoint",
        "today",
        "local_wall",
        "NotFound",
        "ResourceBusy",
        "BUSY_ERRORS",
    }
)


def _db_reaches(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    db_names = {"db"}
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "psycopg" or module.startswith("psycopg."):
                hits.append(f"{node.lineno}: from {module} import ...")
            for alias in node.names:
                if alias.name == "db":
                    db_names.add(alias.asname or "db")
                elif module.split(".")[-1] == "db" and alias.name not in DB_ALLOWED:
                    hits.append(f"{node.lineno}: from {module} import {alias.name}")
        elif isinstance(node, ast.Import):
            hits += [
                f"{node.lineno}: import {a.name}"
                for a in node.names
                if a.name.startswith("psycopg")
            ]
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in db_names
            and node.attr not in DB_ALLOWED
        ):
            hits.append(f"{node.lineno}: {node.value.id}.{node.attr}")
    return [f"{path.relative_to(APP.parent)}:{hit}" for hit in hits]


@pytest.mark.parametrize("layer", ["routes", "tools"])
def test_routes_and_tools_reach_the_database_only_through_transactions(layer):
    offenders = [hit for path in sorted((APP / layer).rglob("*.py")) for hit in _db_reaches(path)]
    assert not offenders, (
        f"app/{layer}/ reaches the database past a transaction scope - move the"
        " query or write behind a function in app/services/:\n" + "\n".join(offenders)
    )


def test_the_detector_actually_matches_sql():
    """A regex that matches nothing would make the two tests above pass forever.
    app/services/ is where SQL is supposed to live, so it is the positive
    control: if this finds none, the pattern is broken, not the codebase."""
    found = [
        hit for path in sorted((APP / "services").rglob("*.py")) for hit in _sql_bearing_lines(path)
    ]
    assert len(found) > 50, f"the SQL pattern matched only {len(found)} lines in app/services/"
