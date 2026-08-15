"""Turn-level anomaly rules. usage_log and flock_traces have carried the raw
material since they shipped and nothing read it: a cost spike or a persona
that fails every call was discovered by a human browsing raw JSON."""

import json

from app import db
from app.services.insights import TURN_CYCLE_ALARM, _r_flock_failures, _r_turn_runaway


def _turn(cycles: int, agent="planner-agent", tokens=1000, ago_days=0):
    from datetime import timedelta

    db.execute(
        "INSERT INTO usage_log (thread_id, agent_name, model_id, input_tokens,"
        " output_tokens, cycles, latency_ms, created_at)"
        " VALUES ('t', ?, 'm', ?, 0, ?, 10, ?)",
        (agent, tokens, cycles, db.local_midnight_utc(db.today() - timedelta(days=ago_days))),
    )


def _trace(members, ago_days=0):
    from datetime import timedelta

    db.execute(
        'INSERT INTO flock_traces (thread_id, "user", flock, members, created_at)'
        " VALUES ('t', 'tester', 'engineering', ?, ?)",
        (
            json.dumps(members),
            db.local_midnight_utc(db.today() - timedelta(days=ago_days)),
        ),
    )


def test_silence_on_ordinary_turns(fresh_db):
    _turn(cycles=3)
    _turn(cycles=7)
    assert _r_turn_runaway() == []


def test_a_looping_turn_is_named_with_its_cost(fresh_db):
    _turn(cycles=TURN_CYCLE_ALARM + 5, agent="research-agent", tokens=250_000)
    found = _r_turn_runaway()
    assert len(found) == 1
    assert "research-agent" in found[0]["message"]
    assert "250,000" in found[0]["message"]  # the number is what makes it act-on-able


def test_the_window_is_bounded(fresh_db):
    """A runaway from two months ago is history, not a thing to go read now."""
    _turn(cycles=TURN_CYCLE_ALARM + 50, ago_days=60)
    assert _r_turn_runaway() == []


def test_a_specialist_that_fails_every_call_is_reported(fresh_db):
    _trace([{"slug": "code-reviewer", "status": "failed"}])
    _trace([{"slug": "code-reviewer", "status": "failed"}])
    found = _r_flock_failures()
    assert len(found) == 1
    assert "code-reviewer" in found[0]["message"]


def test_an_intermittent_failure_is_not_reported(fresh_db):
    """A persona that sometimes fails is a slow model. A rule that fires on
    that gets ignored, and then the rule that matters gets ignored with it."""
    _trace([{"slug": "code-reviewer", "status": "failed"}])
    _trace([{"slug": "code-reviewer", "status": "ok"}])
    assert _r_flock_failures() == []


def test_one_failure_is_not_a_pattern(fresh_db):
    _trace([{"slug": "code-reviewer", "status": "failed"}])
    assert _r_flock_failures() == []


def test_a_malformed_trace_does_not_break_the_findings_run(fresh_db):
    """Every rule runs in one nightly pass. A row this cannot parse must cost
    its own finding, never the fifteen rules queued behind it."""
    db.execute(
        'INSERT INTO flock_traces (thread_id, "user", flock, members, created_at)'
        " VALUES ('t', 'tester', 'engineering', 'not json', ?)",
        (db.now(),),
    )
    _trace([{"slug": "code-reviewer", "status": "failed"}])
    _trace([{"slug": "code-reviewer", "status": "failed"}])
    found = _r_flock_failures()
    assert len(found) == 1


def test_both_rules_are_registered(fresh_db):
    """A rule that is written and not registered never runs."""
    from app.services.insights import RULES

    assert _r_turn_runaway in RULES
    assert _r_flock_failures in RULES
