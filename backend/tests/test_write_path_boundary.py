"""Two write paths, one service layer: SQL lives in app/services/ alone.

Humans mutate via REST, agents via Strands tools, and both must call the shared
functions in app/services/ — a route or tool that writes its own SQL bypasses
provenance and the activity ledger with nothing to notice. The review found one
breach (a SELECT inside tools/schedule.py, added because no service exposed a
single event) and no test to catch the next one, so this pins the rule rather
than that instance.
"""

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

# `db.execute("DELETE FROM ...")` and friends. Matches the statement inside a
# string literal, which is how SQL reaches sqlite3 here.
SQL = re.compile(
    r"""["'(\s](?:SELECT\s+.+?\s+FROM|INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE\s+\w+\s+SET"""
    r"""|DELETE\s+FROM)\s""",
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
        f"SQL in app/{layer}/ — move it behind a function in app/services/, "
        "or the write skips provenance and the activity ledger:\n" + "\n".join(offenders)
    )


def test_the_detector_actually_matches_sql():
    """A regex that matches nothing would make the two tests above pass forever.
    app/services/ is where SQL is supposed to live, so it is the positive
    control: if this finds none, the pattern is broken, not the codebase."""
    found = [
        hit for path in sorted((APP / "services").rglob("*.py")) for hit in _sql_bearing_lines(path)
    ]
    assert len(found) > 50, f"the SQL pattern matched only {len(found)} lines in app/services/"
