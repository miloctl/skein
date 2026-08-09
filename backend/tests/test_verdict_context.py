"""The proposer's record, at the verdict.

A reviewer judged every proposal blind. Approval rate and streak were already
computed and rendered on /agents — two pages from the one screen where the
number decides something. This attaches the record for the (proposer, entity)
pair to each pending row, read from `delegation.trust_scores` so a second
definition of "streak" cannot disagree with the promotion job.
"""

from app import db
from app.services import delegation, review, users


def _settle(client, change_id: int, verdict: str) -> None:
    """A verdict that COUNTS: trust_scores reads only strong-identity,
    non-override rows, so a plain X-User approval feeds no streak."""
    r = client.post(f"/api/review/{change_id}/{verdict}", json={"note": "ok"})
    # asserted: a 4xx here would leave the row pending with a strong-verdict
    # flag on it — a state no code path produces — and the failure would
    # surface several asserts later as a None record
    assert r.status_code == 200, r.text
    db.execute(
        "UPDATE pending_changes SET reviewed_strong = 1, reviewed_override = 0 WHERE id = ?",
        (change_id,),
    )


def _propose(n: int = 1) -> list[int]:
    # scout is registered as an AGENT identity, which is what the delegation
    # and persona paths do before an agent ever proposes anything. Without the
    # row the record is withheld by design, and every assertion below would
    # pass against a service that computed nothing.
    users.ensure_user("scout", kind="agent")
    return [
        review.propose_change("task", "create", {"title": f"t{i}"}, actor="scout", origin="agent")[
            "id"
        ]
        for i in range(n)
    ]


def test_a_pending_row_carries_the_proposers_record_on_that_entity(client):
    for cid in _propose(3):
        _settle(client, cid, "approve")
    open_id = _propose(1)[0]

    row = next(r for r in client.get("/api/review?status=pending").json() if r["id"] == open_id)
    assert row["record"]["approved"] == 3
    assert row["record"]["proposed"] == 3
    assert row["record"]["streak"] == 3
    assert row["record"]["level"] == "review"


def test_the_record_is_absent_for_a_proposer_with_no_settled_verdicts(client):
    open_id = _propose(1)[0]
    row = next(r for r in client.get("/api/review?status=pending").json() if r["id"] == open_id)
    # None, not a zeroed record: "0 of 0 approved" is a claim about a history
    # that does not exist, and the queue must not invent one
    assert row["record"] is None


def test_promotion_proximity_is_stated_only_on_the_verdict_that_earns_it(client):
    """One approval short of the streak, and only while a promotion is
    actually available from the current level."""
    for cid in _propose(delegation.TRUST_STREAK - 2):
        _settle(client, cid, "approve")
    row = next(iter(client.get("/api/review?status=pending").json()), None)
    open_id = _propose(1)[0]
    row = next(r for r in client.get("/api/review?status=pending").json() if r["id"] == open_id)
    assert row["record"]["promotes_at"] == 0  # still two away

    for cid in _propose(1):
        _settle(client, cid, "approve")
    open_id = _propose(1)[0]
    row = next(r for r in client.get("/api/review?status=pending").json() if r["id"] == open_id)
    assert row["record"]["streak"] == delegation.TRUST_STREAK - 1
    assert row["record"]["promotes_at"] == delegation.TRUST_STREAK


def test_an_agent_already_past_review_is_not_offered_a_promotion(client):
    for cid in _propose(delegation.TRUST_STREAK - 1):
        _settle(client, cid, "approve")
    delegation.set_authority("scout", "task", "autonomous", actor="tester")
    open_id = _propose(1)[0]
    row = next(r for r in client.get("/api/review?status=pending").json() if r["id"] == open_id)
    assert row["record"]["level"] == "autonomous"
    assert row["record"]["promotes_at"] == 0


def test_no_promotion_is_promised_where_none_can_be_filed(client):
    """`task_completion` is the entity a delegated agent proposes on MOST, and
    `review_authority` skips it outright — so a restated rule here advertised a
    promotion that could never be filed, on the common case. The queue asks
    delegation for the predicate instead of repeating part of it."""
    users.ensure_user("scout", kind="agent")

    # the pairs a promotion can never reach, each for its own reason
    assert delegation.promotion_blocked("scout", "task_completion", "review")
    assert delegation.promotion_blocked("scout", "authority", "review")
    assert delegation.promotion_blocked("scout", "absence", "review")  # ALWAYS_REVIEW
    assert delegation.promotion_blocked("scout", "weekly_plan", "review")  # NO_AUTHORITY
    assert delegation.promotion_blocked("mira", "task", "review")  # not an agent
    # and the one that can
    assert delegation.promotion_blocked("scout", "task", "review") == ""

    # a streak on a blocked entity renders no promotion line. `ava` has to be
    # a real teammate or add_absence refuses at APPLY time, the proposal
    # returns to pending, and nothing ever settles to build a streak from.
    users.ensure_user("ava")
    for _ in range(delegation.TRUST_STREAK - 1):
        cid = review.propose_change(
            "absence",
            "create",
            {"person": "ava", "starts_on": "2026-09-01", "ends_on": "2026-09-02"},
            actor="scout",
            origin="agent",
        )["id"]
        _settle(client, cid, "approve")
    open_id = review.propose_change(
        "absence",
        "create",
        {"person": "ava", "starts_on": "2026-09-03", "ends_on": "2026-09-04"},
        actor="scout",
        origin="agent",
    )["id"]
    row = next(r for r in client.get("/api/review?status=pending").json() if r["id"] == open_id)
    assert row["record"]["streak"] == delegation.TRUST_STREAK - 1
    assert row["record"]["promotes_at"] == 0


def test_the_queue_pays_only_for_the_pairs_it_shows(client):
    """`trust_scores` runs a per-pair lookup for every pair in the settled
    history. Unfiltered, rendering a queue holding one proposer cost a lookup
    per pair the deployment had ever settled — a cost that grew with its age
    rather than with the page."""
    users.ensure_user("scout", kind="agent")
    # settled history for three pairs that will NOT be on the page
    for i in range(3):
        other = f"bot{i}"
        users.ensure_user(other, kind="agent")
        cid = review.propose_change("note", "create", {"topic": "t", "content": "c"}, actor=other)[
            "id"
        ]
        _settle(client, cid, "approve")
    _propose(1)  # the only pending row: (scout, task)

    calls = {"n": 0}
    real = review.db.query

    def counting(sql, *a, **kw):
        if "ORDER BY reviewed_at DESC" in sql:
            calls["n"] += 1
        return real(sql, *a, **kw)

    review.db.query = counting  # type: ignore[assignment]
    try:
        client.get("/api/review?status=pending")
    finally:
        review.db.query = real  # type: ignore[assignment]
    # one pair on the page, four pairs with history
    assert calls["n"] <= 1, f"per-pair lookups: {calls['n']} (expected at most 1)"


def test_a_human_proposer_gets_no_record_at_all(client):
    """Ingest files proposals under the PERSON who pasted the notes, and
    /review is team-visible. A record keyed on the proposer alone would put
    one teammate's approval history in front of the whole roster — person-level
    data judging the past, which the anti-surveillance rule refuses. It is also
    the wrong question: the record decides whether an AGENT earned autonomy."""
    from app.services import ingest

    ingest.ingest_notes("todo: ship the thing tomorrow", actor="mira")
    settled = db.query("SELECT id FROM pending_changes WHERE proposed_by = 'mira'")
    for row in settled:
        _settle(client, row["id"], "approve")
    ingest.ingest_notes("todo: ship the other thing tomorrow", actor="mira")

    rows = client.get("/api/review?status=pending").json()
    mine = [r for r in rows if r["proposed_by"] == "mira"]
    assert mine, "the ingest proposal did not reach the queue"
    assert all(r["record"] is None for r in mine)


def test_the_record_names_no_human(client):
    """The queue is a team-visible surface and the anti-surveillance rule
    holds: a proposer is an agent identity, and the record must not carry a
    person's approval history under any key."""
    for cid in _propose(2):
        _settle(client, cid, "approve")
    open_id = _propose(1)[0]
    row = next(r for r in client.get("/api/review?status=pending").json() if r["id"] == open_id)
    assert set(row["record"]) == {
        "approved",
        "proposed",
        "approval_rate",
        "streak",
        "streak_blocked",
        "level",
        "promotes_at",
    }
