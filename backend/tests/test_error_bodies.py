"""A 4xx body never echoes the rejected value (CLAUDE.md, "Input errors
are 4xx").

The boundary these pin: an error may name server-held state — a roster row,
a registry entry, our own constants — but never a value that matched
NOTHING, because that is the caller's unvalidated input reflected back.
The marker is 10 characters, sized to slip under the tightest field cap
(review_by) and still reach the parse."""

import pytest

MARKER = "ZZMARKERZZ"
# date fields check the SHAPE before the calendar, so a naked marker never
# reaches the echoing line — a well-formed unreal date does
DATE_MARKER = "9999-99-99"


def _no_echo(r):
    assert 400 <= r.status_code < 500, r.text
    assert MARKER not in r.text
    assert DATE_MARKER not in r.text
    return r


def test_a_4xx_never_echoes_the_rejected_value(client, fresh_db):
    _no_echo(
        client.post(
            "/api/decisions",
            json={"title": "t", "decision": "d", "review_by": DATE_MARKER},
        )
    )
    _no_echo(
        client.post(
            "/api/absences",
            json={"person": MARKER, "starts_on": "2026-01-01", "ends_on": "2026-01-02"},
        )
    )
    _no_echo(client.get(f"/api/events?from_date={DATE_MARKER}"))
    _no_echo(
        client.post(
            "/api/playbooks/instantiate",
            json={"playbook": MARKER, "engagement_name": "e"},
        )
    )
    _no_echo(client.post("/api/field-guide/dismiss", json={"knot": MARKER}))
    _no_echo(client.post(f"/api/users/{MARKER}/active", json={"active": False}))

    from app.services import users, work

    users.ensure_user("scout", kind="agent")
    task = work.create_task(title="probe", actor="tester")
    _no_echo(
        client.post(
            f"/api/tasks/{task['id']}/delegate",
            json={"agent": "scout", "sponsor": MARKER},
        )
    )


def test_a_service_refusal_never_echoes_the_unmatched_value(fresh_db):
    from app.services import review

    for kwargs in (
        {"entity": MARKER, "action": "create", "payload": {}},
        {"entity": "task", "action": MARKER, "payload": {}},
    ):
        with pytest.raises(ValueError) as exc:
            review.propose_change(actor="tester", **kwargs)
        assert MARKER not in str(exc.value)
