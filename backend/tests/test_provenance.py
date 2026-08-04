"""Provenance: the origin a write claims, and the ledger row it leaves.

db.py's hash chain is pinned hard elsewhere (no-op log_activity fails ~80
tests). What was NOT pinned is whether writes reach it, and whether they carry
the origin the caller asked for. Both held only by reading the source:
hardcoding origin="human" in seven services — the two agent tools included —
passed the whole suite, and so did wrapping a log_activity call in `if False:`,
because the one guard was a regex over *.py text rather than an executed write.

An agent write recorded as human-authored is the precise failure the
provenance constraint exists to prevent, so it is pinned here by execution.
"""

import sys
from pathlib import Path

import pytest

from app import db, ratelimit
from app.tools import ALL_TOOLS

# the ALL_TOOLS sweep machinery (unwrap, argument heuristics, one-row seed)
# already exists for the receipt sweep; a second copy would drift from it
sys.path.insert(0, str(Path(__file__).parent))
from test_gate_coverage import _kwargs_for, _seed, _unwrap

AGENT_ORIGINS = {"agent", "agent_verified"}


def _origin_tables() -> list[str]:
    """Discovered, never listed: a new table with an origin column joins this
    sweep on its first run instead of waiting for someone to remember."""
    return sorted(
        t["name"]
        for t in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
        if any(c["name"] == "origin" for c in db.query(f"PRAGMA table_info({t['name']})"))
    )


def _origin_rows(tables: list[str]) -> dict[tuple[str, int], str]:
    rows = {}
    for t in tables:
        for r in db.query(f"SELECT rowid AS rid, origin FROM {t}"):  # noqa: S608 — from sqlite_master
            rows[(t, r["rid"])] = r["origin"]
    return rows


def test_every_agent_tool_records_an_agent_origin(fresh_db, monkeypatch):
    """The severe half of the gap: a tool that writes origin='human' launders
    an agent's write as a person's, and provenance is the whole point of
    telling them apart. Sweeps the registry, so a new tool is covered on the
    first run rather than on the day someone remembers to add it."""
    from app.services import notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    _seed(fresh_db)
    tables = _origin_tables()
    assert tables, "no table carries an origin column — the sweep would pass vacuously"

    laundered: list[str] = []
    for tool in ALL_TOOLS:
        fn = _unwrap(tool)
        try:
            kwargs = _kwargs_for(fn)
        except AssertionError:
            continue  # the receipt sweep already fails loudly on an uncallable tool
        ratelimit.reset()
        before = _origin_rows(tables)
        try:
            fn(**kwargs)
        except Exception:  # noqa: S112 — the receipt sweep owns "a tool must not raise"
            continue
        for key, origin in _origin_rows(tables).items():
            if key not in before and origin not in AGENT_ORIGINS:
                laundered.append(
                    f"{fn.__name__} wrote {key[0]} rowid {key[1]} as origin={origin!r}"
                )
    assert not laundered, "agent writes recorded as human-authored:\n" + "\n".join(laundered)


def test_rest_write_paths_record_a_human_origin(client, fresh_db):
    """The other direction: the REST door must not claim agent provenance."""
    client.post("/api/tasks", json={"title": "Cut release"})
    client.post("/api/notes", json={"topic": "t", "content": "c"})
    client.post("/api/questions", json={"question": "Who owns infra?"})
    for table in ("tasks", "notes", "questions"):
        rows = db.query(f"SELECT origin, created_by FROM {table}")  # noqa: S608 — literal tuple
        assert rows, f"{table}: the REST write did not land"
        for r in rows:
            assert r["origin"] == "human", f"{table} claims origin={r['origin']!r}"
            assert r["created_by"], f"{table} recorded no created_by"


@pytest.mark.parametrize(
    "path,body,table",
    [
        ("/api/tasks", {"title": "ledger probe"}, "tasks"),
        ("/api/notes", {"topic": "p", "content": "ledger probe"}, "notes"),
        ("/api/blockers", {"title": "ledger probe"}, "blockers"),
        (
            "/api/absences",
            {"person": "tester", "starts_on": "2030-01-01", "ends_on": "2030-01-02"},
            "absences",
        ),
    ],
)
def test_a_write_leaves_a_ledger_row(client, fresh_db, path, body, table):
    """Executed, not grepped. The registered-verb test reads *.py source text,
    so it still passes when the log_activity call is present but unreachable —
    an early return or a moved branch stops the ledger silently."""
    before = db.query_row("SELECT COUNT(*) AS n FROM activity")["n"]
    r = client.post(path, json=body)
    assert r.status_code == 200, r.text
    after = db.query_row("SELECT COUNT(*) AS n FROM activity")["n"]
    assert after > before, f"POST {path} wrote {table} but appended no activity row"
