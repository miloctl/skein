"""One ranked queue of what a manager could actually do something about.

Skein computes the manager's evidence in seven places that never met:
engagement health, the escalated blocker register, overdue given promises,
unowned due work, undispositioned findings, stale decisions, and stale WIP.
Each has its own page, its own ordering and its own vocabulary, and no reader
has ever seen them ranked against each other. The count is the ARMS below, and
docs/FEATURES.md states the same seven — a number in two places disagreed once
already.

Composition only — no table, no write path, no new habit. Every row here is a
row one of those engines already produced, restated in one shape and ordered by
consequence. The receipt travels with it, so a reader can disagree with the
ranking without leaving the page.

Deliberately NOT a score anybody sees. The rank exists to order the list; the
row says what is true and what to do about it, and a manager who reads the
receipt and skips the item has used this correctly.
"""

from collections.abc import Callable
from datetime import date, timedelta

from .. import db
from . import refs, scope, wording
from .slas import STALE_WIP_DAYS

# What each condition is worth. These are not measurements — they are an
# ordering the team can argue with, written in one place so the argument has
# somewhere to happen. The rule they encode: a commitment already broken
# outranks one about to break, and both outrank a thing that is merely untidy.
#
# KIND decides the band and nothing else moves a row out of it. Age and reach
# order rows WITHIN a band (see the sort key in `interventions`). Folded into
# one number instead, the aging term was two thirds the size of this whole
# table: a 30-day-old stale decision reached 90, level with a red engagement
# and above a promise broken last Tuesday, so the two conditions the table
# calls untidy sorted above the two it calls serious.
_WEIGHT = {
    "blocker_escalated": 100,
    "engagement_red": 90,
    "promise_overdue": 80,
    "finding_high": 70,
    "engagement_yellow": 40,
    "finding_medium": 35,
    "decision_stale": 30,
    "work_unowned": 25,
    "stale_wip": 20,
    "finding_low": 10,
}


def _age_days(stamp: str | None) -> int:
    """Whole days since a stored timestamp or date, floored at 0.

    A future date scores 0 rather than a negative. Without the floor a task due
    next week subtracts from its own weight and sorts below rows of the same
    kind that are merely younger.
    """
    if not stamp:
        return 0
    try:
        # db.local_day for a timestamp, never the raw slice: half these call
        # sites pass a UTC timestamp and the rest a date column, and the slice
        # buckets evening work under the wrong day for any zone behind UTC.
        day = db.local_day(stamp) if "T" in stamp else stamp[:10]
        gap = db.today() - date.fromisoformat(day)
    except ValueError:
        return 0
    return max(0, gap.days)


def _order(kind: str, *, age: int = 0, reach: int = 0) -> tuple[int, int, int]:
    """The sort key: band, then reach, then age. Higher sorts earlier.

    A tuple, not a sum. Age is capped at 30 days — past that a thing is not
    getting more urgent, it is getting ignored — and an uncapped term would pin
    one forgotten row to the top of its band forever.
    """
    return (_WEIGHT.get(kind, 10), reach, min(age, 30))


# Findings whose subject a raw arm above already files. The finding survives on
# /insights with its disposition controls; it does not ride this queue twice
# under a generic triage verb that replaces the rule's own instruction.
#
# Only rules that name the SAME ROW as a raw arm belong here. `review_stall`
# and `question_aging` stay: nothing else in this queue reports them.
_RESTATED_BY_A_RAW_ARM = frozenset(
    {
        "promise_due",  # section 3 emits the promise itself
        "decision_decay",  # section 6 emits the decision itself
        "aging_wip",  # section 7 emits the task itself
        "escalation_spike",  # section 2 emits each escalated blocker
    }
)

# Rules only whoever runs the server can act on. This queue is the Monday
# meeting's running order, and job_stale is severity high, so a stale cron
# outranked an overdue promise to a customer — on the seeded team, four of
# the top eight calls were Skein's own plumbing. These rules keep firing and
# keep their surfaces (Insights, the digest, OperationsCard on /settings);
# they are only out of the meeting agenda.
# `budget` stays IN the queue on purpose: month-over-budget is a spend
# decision with a decision-maker in the room, not a server condition.
_SYSTEM_AUDIENCE = frozenset(
    {
        "job_stale",  # the scheduler did not run — process/config
        "activity_chain_broken",  # compare ledger against backups — operator
        "ledger_rows_adopted",  # same remediation path as the chain rule
        "flock_member_failing",  # fix is a persona file and the model it names
        "token_anomaly",  # model-spend telemetry, not assignable work
        "turn_runaway",  # a runaway agent turn — operator inspects the run
    }
)


# A finding is minted at most once per ISO week, so a condition that CLEARS
# mid-week kept presenting as a current call for up to six days: "the review
# queue is stalled: 3 proposals (#4, #5, #13)" sat high in the Monday order
# with all three settled — and a manager who checks and finds nothing learns
# to distrust the queue. Rules whose condition is one cheap read get that
# read at queue time. Only short-lived, cheaply-verifiable conditions belong
# here: a windowed trend (rejection_spike, interrupt_load) is true ABOUT its
# window however this week ends, and re-deriving it would be a second
# definition of the rule.
_RECHECK = {
    "review_stall": lambda f: (
        (db.query_one("SELECT COUNT(*) AS n FROM pending_changes WHERE status = 'pending'") or {})
        .get("n", 0)
        > 0
    ),
    "question_aging": lambda f: (
        db.query_one(
            "SELECT 1 FROM questions WHERE id = ? AND status = 'open'",
            (f["receipt"].get("question_id", 0),),
        )
        is not None
    ),
}


def _still_true(finding: dict) -> bool:
    probe = _RECHECK.get(finding["rule_id"])
    if probe is None:
        return True
    try:
        return bool(probe(finding))
    except Exception:  # a malformed receipt must not empty the arm
        return True


def _dedupe(rows: list[dict]) -> list[dict]:
    """One row per (entity, id), keeping the strongest band it appeared in.

    The sources overlap by design — a task that is unassigned, due, in progress
    and untouched satisfies both the unowned arm and the stale-WIP arm. Left in,
    the manager reads the same task twice inside one ranked list, and the page
    hands React a duplicate key.

    Takes an already-sorted list, so the first appearance is the strongest one.
    """
    seen: set[tuple[str, int]] = set()
    out = []
    for row in rows:
        key = (row["entity"], row["entity_id"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _finding_action(finding: dict) -> str:
    """What to do about a finding.

    The rule's own message ends with its instruction — "conclude it or extend
    it on purpose", "reconfirm the grant or demote it to review" — and a
    generic triage verb in this slot REPLACED it, so the row stated the real
    move in small text and the wrong one in the action line. The triage verbs
    are the fallback for a rule whose message is a statement only.
    """
    message = finding.get("message", "")
    # the em dash first, then a sentence break: those are the two shapes the
    # rules use. NOT case-gated — the rules write their instruction in lower
    # case ("conclude it or extend it on purpose"), and an isupper() test sent
    # every one of them to the fallback, which is the exact substitution this
    # function exists to stop.
    tail = ""
    for sep in ("—", ". ", ": "):
        if sep in message:
            tail = message.rsplit(sep, 1)[-1].strip().rstrip(".")
            break
    # an instruction starts with a verb, so it is short. A long tail is the
    # rest of a statement, and a statement in the action slot says nothing.
    if tail and len(tail) < 120:
        return tail[:1].upper() + tail[1:]
    return "Convert it to work, defer it with a date, or dismiss it with a reason"


def _finding_receipt(finding: dict) -> str:
    """A finding's stored receipt as one sentence naming its rows.

    `findings.receipt` is a JSON object recorded when the rule fired
    (services/insights.py) — the ids and numbers behind the claim. A key ending
    in `_id` whose stem names an entity becomes a reference, so
    `{"promise_id": 1}` reads "promise #1" and services/refs.py resolves it to
    a link. Everything else renders as `key: value`, in the order the rule
    wrote it. A rule that stored nothing falls back to its own message, which
    is worse than a receipt and better than an empty line.
    """
    parts = []
    for key, value in (finding.get("receipt") or {}).items():
        stem = key[:-3] if key.endswith("_id") else ""
        if stem in refs.TARGETS and isinstance(value, int):
            parts.append(f"{stem} #{value}")
        elif isinstance(value, list):
            # a rule may store whole rows here (review_stall keeps its pending
            # proposals). str() on that list printed a page of Python dicts
            # into the meeting agenda — the count and the ids are the receipt,
            # the rows themselves stay on /insights.
            ids = [v["id"] for v in value if isinstance(v, dict) and isinstance(v.get("id"), int)]
            named = f" ({', '.join(f'#{i}' for i in ids[:5])})" if ids else ""
            parts.append(f"{key.replace('_', ' ')}: {wording.count(len(value), 'row')}{named}")
        else:
            parts.append(f"{key.replace('_', ' ')}: {value}")
    return ", ".join(parts) if parts else finding["message"]


def interventions(
    viewer: scope.Viewer = scope.NOBODY,
    limit: int = 12,
    resource_filter: Callable[[str, int, dict[str, str]], bool] | None = None,
) -> list[dict]:
    """The manager's queue, most consequential first.

    Viewer-scoped: this composes rows that carry tiers, and a queue assembled
    from rows the caller cannot open would leak both the row and the fact that
    it exists.

    The findings arm is the one unscoped read. `findings` carries no visibility
    column (001_baseline.sql) — a tier filter there would filter on nothing.
    """
    from .insights import list_findings
    from .portfolio import engagement_health
    from .work import downstream

    out: list[dict] = []
    today = db.today().isoformat()

    # 1. Engagement health. The receipts are already written; this adds the
    #    ordering and the action.
    for eng in engagement_health(viewer, resource_filter=resource_filter):
        if eng["health"] == "green":
            continue
        red = eng["health"] == "red"
        out.append(
            {
                "kind": "engagement_red" if red else "engagement_yellow",
                "entity": "engagement",
                "entity_id": eng["id"],
                "title": eng["name"],
                "condition": f"health is {eng['health']}",
                "owner": eng["lead"] or "",
                "action": (
                    "Check the receipts, then re-plan, change the staff, or accept the date"
                    if red
                    else "Start with the receipt closest to the date, or re-plan now"
                ),
                "receipts": [refs.receipt(r) for r in eng["receipts"]],
                "order": _order(
                    "engagement_red" if red else "engagement_yellow",
                    reach=len(eng["receipts"]),
                ),
                # the engagement's own page, the same target `lib/entity-ref.ts`
                # resolves an `engagement #N` reference to. `#engagement-N` was
                # an anchor no page renders, so the row's title landed on a
                # list with nothing to scroll to.
                "link": f"/engagement/{eng['id']}",
            }
        )

    # 2. Escalated blockers. The register escalates on its own clock, so
    #    ranking it against everything else happens only here.
    bfrag, bp = scope.visible_filter(viewer, "blockers", alias="b")
    for b in db.query(
        f"SELECT b.id, b.title, b.owner, b.impact, b.escalated_at, b.created_at, b.task_id"  # noqa: S608 — scope.visible_filter emits only bound marks
        # LIMITed before the per-row `downstream` walk below, which costs up
        # to eleven queries each. Unbounded, a register nobody has drained runs
        # thousands of round trips and then the cap discards almost all of it —
        # the worse the state, the slower the page that exists to fix it.
        # Oldest escalation first: it has been shouting longest.
        f" FROM blockers b WHERE b.status = 'escalated' AND {bfrag}"
        " ORDER BY b.escalated_at NULLS FIRST, b.created_at LIMIT 10",
        tuple(bp),
    ):
        reach = len(downstream(b["task_id"], viewer)["unblocks"]) if b["task_id"] else 0
        out.append(
            {
                "kind": "blocker_escalated",
                "entity": "blocker",
                "entity_id": b["id"],
                "title": b["title"],
                "condition": f"blocker #{b['id']} escalated at {b['impact']} impact",
                "owner": b["owner"] or "",
                "action": (
                    f"Unblock {b['owner']} or take it off them"
                    if b["owner"]
                    else "Give it an owner — nobody holds this one"
                ),
                "receipts": [
                    refs.receipt(
                        f"blocker #{b['id']} {wording.quoted(b['title'])} escalated"
                        + (f", holding up {reach} task{'' if reach == 1 else 's'}" if reach else "")
                    )
                ],
                "order": _order("blocker_escalated", age=_age_days(b["escalated_at"]), reach=reach),
                # the ROW: no element ever carried `#blockers`, so the fragment
                # did nothing. `blocker-N` is on the row in
                # app/dashboard/page.tsx.
                "link": f"/dashboard#blocker-{b['id']}",
            }
        )

    # 3. Overdue external promises. The one class of commitment whose reader is
    #    outside the team and cannot be re-planned by talking to each other.
    for p in db.query(
        f"SELECT id, promise, to_whom, due_date, created_by FROM promises"  # noqa: S608 — module constant
        f" WHERE status = 'open' AND direction = 'given' AND {scope.WORKSPACE_ONLY}"
        " AND due_date IS NOT NULL AND due_date < ? ORDER BY due_date",
        (today,),
    ):
        out.append(
            {
                "kind": "promise_overdue",
                "entity": "promise",
                "entity_id": p["id"],
                "title": p["promise"][:80],
                "condition": f"promise to {p['to_whom'] or 'the team'} is past its date",
                "owner": p["created_by"] or "",
                "action": "Settle it or renegotiate the date — the other side is still waiting",
                "receipts": [refs.receipt(f"promise #{p['id']} was due {p['due_date']}")],
                "order": _order("promise_overdue", age=_age_days(p["due_date"])),
                "link": f"/portfolio#promise-{p['id']}",
            }
        )

    # 4. Work with no owner. Not a findings rule and not a health receipt:
    #    this is the only surface that reports it.
    tfrag, tp = scope.visible_filter(viewer, "tasks", alias="t")
    for t in db.query(
        f"SELECT t.id, t.title, t.due_date FROM tasks t"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" WHERE t.assignee = '' AND t.status NOT IN ('done', 'void') AND {tfrag}"
        " AND t.due_date IS NOT NULL AND t.due_date <= ? ORDER BY t.due_date LIMIT 10",
        (*tp, today),
    ):
        out.append(
            {
                "kind": "work_unowned",
                "entity": "task",
                "entity_id": t["id"],
                "title": t["title"],
                "condition": "due and nobody owns it",
                "owner": "",
                "action": "Assign it or drop it — until somebody owns it, nobody acts on it",
                "receipts": [refs.receipt(f"task #{t['id']} was due {t['due_date']}")],
                "order": _order("work_unowned", age=_age_days(t["due_date"])),
                "link": f"?task={t['id']}",
            }
        )

    # 5. Findings the team has not dispositioned. A dismissed or converted
    #    finding is a decision already made, and re-ranking it here would ask
    #    the manager to make it twice.
    #    EVERY filter runs before the [:30] budget, not inside the loop: a
    #    row the loop would skip still spends a slot when the slice comes
    #    first, and a run of already-handled findings — or a deployment with
    #    thirty stale jobs — silently shortens this arm to nothing.
    #
    #    On _RESTATED_BY_A_RAW_ARM: a rule whose subject a raw arm above
    #    already filed is not filed twice. `promise_due` fires on the same
    #    overdue promise section 3 emits: the manager saw promise #14 at one
    #    band saying "settle it or renegotiate" and again three rows down at
    #    another saying "convert it to work". The raw arm wins because it
    #    names the owner and the real transition; the finding keeps its
    #    disposition controls on its own page.
    fresh = [
        f
        for f in list_findings(weeks=4, limit=200)
        if not f["disposition"]
        and f"finding_{f['severity']}" in _WEIGHT
        and f["rule_id"] not in _RESTATED_BY_A_RAW_ARM
        and f["rule_id"] not in _SYSTEM_AUDIENCE
        and _still_true(f)
    ]
    for f in fresh[:30]:
        kind = f"finding_{f['severity']}"
        out.append(
            {
                "kind": kind,
                "entity": "finding",
                "entity_id": f["id"],
                # the message IS the finding, so it is the title and nothing
                # else. `condition` names the rule instead: a card that put the
                # same sentence in the heading, the condition and the receipt
                # printed it three times and said one thing.
                "title": f["message"],
                "condition": f"{f['severity']} · {f['rule_id']}",
                # no owner, ever. `findings.subject` is the dedupe key for the
                # (rule, subject, week) fire — "anchor:44", "promise-1",
                # "job:digest" — so rendering it as a person put row keys where
                # every other kind puts a name to go and talk to.
                "owner": "",
                "action": _finding_action(f),
                # the STORED receipt, recorded at fire time, not the message
                # again: the ids and numbers are what let a reader check the
                # rule rather than take its word (services/insights.py).
                "receipts": [refs.receipt(_finding_receipt(f))],
                "order": _order(kind),
                # the engagement this finding is ABOUT, when its rule recorded
                # one. Carried on the row rather than re-derived by every
                # reader: `engagement_brief._mine` narrows on it, and the link
                # below is built from the same value, so a row cannot land on a
                # page that then says it does not belong there.
                "engagement_id": (
                    f["receipt"].get("engagement_id")
                    if isinstance(f.get("receipt"), dict)
                    else None
                ),
                "link": (
                    f"/engagement/{f['receipt']['engagement_id']}"
                    if isinstance(f.get("receipt"), dict) and f["receipt"].get("engagement_id")
                    else "/insights"
                ),
            }
        )

    # 6. Stale decisions the team never re-confirmed. My Day shows a person
    #    only their OWN (services/briefing.py); the ones whose author has moved
    #    on are the ones with nobody left to notice them.
    for d in db.query(
        f"SELECT id, title, decided_by, review_by FROM decisions"  # noqa: S608 — module constant
        f" WHERE status = 'stale' AND {scope.WORKSPACE_ONLY} ORDER BY review_by NULLS FIRST LIMIT 10"
    ):
        out.append(
            {
                "kind": "decision_stale",
                "entity": "decision",
                "entity_id": d["id"],
                "title": d["title"],
                "condition": "past its review-by date",
                "owner": d["decided_by"] or "",
                "action": "Reconfirm it, supersede it, or hand it to somebody who can",
                "receipts": [
                    refs.receipt(f"decision #{d['id']} was due for review {d['review_by']}")
                ],
                "order": _order("decision_stale", age=_age_days(d["review_by"])),
                "link": f"/charter#charter-entry-{d['id']}",
            }
        )

    # 7. Work in progress that has not moved. The flow metrics count it;
    #    this names the assignee, which is who a count cannot name.
    cutoff = db.local_midnight_utc(db.today() - timedelta(days=STALE_WIP_DAYS))
    for t in db.query(
        f"SELECT t.id, t.title, t.assignee, t.updated_at FROM tasks t"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" WHERE t.status = 'in_progress' AND {tfrag} AND t.updated_at < ?"
        " ORDER BY t.updated_at LIMIT 10",
        (*tp, cutoff),
    ):
        out.append(
            {
                "kind": "stale_wip",
                "entity": "task",
                "entity_id": t["id"],
                "title": t["title"],
                "condition": f"in progress and not moved for more than {STALE_WIP_DAYS} days",
                "owner": t["assignee"] or "",
                "action": "Ask the assignee what the task needs",
                "receipts": [
                    refs.receipt(f"task #{t['id']} last moved {db.local_day(t['updated_at'])}")
                ],
                "order": _order("stale_wip", age=_age_days(t["updated_at"])),
                "link": f"?task={t['id']}",
            }
        )

    if resource_filter is not None:
        from . import policy_context

        contexts = policy_context.resource_contexts(
            [(str(row["entity"]), int(row["entity_id"])) for row in out],
            viewer,
        )
        out = [
            row
            for row in out
            if resource_filter(
                str(row["entity"]),
                int(row["entity_id"]),
                contexts.get((str(row["entity"]), int(row["entity_id"])), {}),
            )
        ]

    # band, then reach, then age, then a stable tie-break. `order` is internal
    # and never rendered: a number beside a row invites the reader to argue
    # with the arithmetic instead of with the receipt.
    out.sort(key=lambda r: (tuple(-v for v in r["order"]), r["entity"], r["entity_id"]))
    out = _dedupe(out)
    for row in out:
        row.pop("order")
    return out[: max(1, min(int(limit), 50))]
