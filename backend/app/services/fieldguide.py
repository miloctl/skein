"""Field guide: first-use feature discovery ("knots"). Predicates DETECT use
from data the platform already has; feature_unlocks HOLDS the state (activity
gets pruned, unlocks survive). A person's unlock rows are self-visible only —
the anti-surveillance rule outranks the provenance convention here, so like
tool_usage these writes never touch the team-visible activity feed. Spec and
the non-negotiables: docs/FIELD-GUIDE.md."""

import contextlib
import logging
import time
from collections.abc import Callable
from datetime import timedelta

import yaml

from .. import config, db

KNOTS_FILE = config.STOCK_DIR / "fieldguide" / "knots.yaml"
SETS = ("loops", "hitches", "bends", "stoppers", "manager")
UNADOPTED_GRACE_DAYS = 30

log = logging.getLogger("skein.fieldguide")


def _has(sql: str, params: tuple) -> bool:
    return db.query_one(sql + " LIMIT 1", params) is not None


def _act(user: str, action: str, like: str = "") -> bool:
    sql = "SELECT 1 FROM activity WHERE actor = ? AND action = ?"
    params: tuple = (user, action)
    if like:
        sql += " AND detail LIKE ?"
        params = (user, action, like)
    return _has(sql, params)


# id -> first-use test. None = tied only via mark() (read-only features write
# nothing to detect against). Detail-string predicates are pinned by tests in
# test_fieldguide.py — if a service changes its activity wording, the test
# breaks loudly instead of the knot silently going untieable.
PREDICATES: dict[str, Callable[[str], bool] | None] = {
    "capture": lambda u: _act(u, "capture"),
    "standup": lambda u: _has("SELECT 1 FROM standups WHERE author = ?", (u,)),
    # trailing % : update_task's detail carries a channel suffix when a machine
    # wrote it (work.py's `note`), and an anchored pattern would silently stop
    # tying the day any human-actor caller passes one
    "task_done": lambda u: _act(u, "complete_task") or _act(u, "update_task", "#% done%"),
    "decision": lambda u: _has(
        "SELECT 1 FROM decisions WHERE decided_by = ? AND review_by IS NOT NULL"
        " AND review_by != ''",
        (u,),
    ),
    "question_asked": lambda u: _has("SELECT 1 FROM questions WHERE asked_by = ?", (u,)),
    "question_answered": lambda u: _act(u, "answer_question"),
    "convention": lambda u: _has(
        "SELECT 1 FROM notes WHERE (author = ? OR created_by = ?) AND topic LIKE 'convention%'",
        (u, u),
    ),
    "search": None,
    "timeaway": lambda u: _act(u, "add_absence"),
    "intake": lambda u: _has(
        "SELECT 1 FROM intake_requests WHERE requester = ? OR created_by = ?", (u, u)
    ),
    "promise": lambda u: _has("SELECT 1 FROM promises WHERE created_by = ?", (u,)),
    "growth": lambda u: _act(u, "set_growth_interests"),
    # a chat_threads row means a message reached POST /api/chat, which claims
    # the id before anything else runs (chat_threads.claim_thread) — so a turn
    # that dies after the claim ties this too. Still the right probe:
    # tool_usage's 'chat' surface would tie on merely opening the page.
    "chat": lambda u: _has("SELECT 1 FROM chat_threads WHERE owner = ?", (u,)),
    # one row per flock turn, written when the turn closes — a cancelled turn
    # ties it too, which is right: the person called a flock and it flew
    "flocks": lambda u: _has("SELECT 1 FROM flock_traces WHERE user = ?", (u,)),
    # tied by the chat route when a capture-prefixed turn actually writes —
    # the write lands under the AGENT's name, so no actor predicate can find it
    "chat_capture": None,
    # tied by the chat route when a consulted specialist actually spoke. No
    # query works: a consult's spend is a usage_log row under the specialist's
    # slug, and `/as` writes exactly the same row
    "consult": None,
    "activity_feed": None,  # read-only page — tied by mark() on the feed route
    "chat_engagement": lambda u: _has(
        "SELECT 1 FROM chat_threads WHERE owner = ? AND engagement_id IS NOT NULL", (u,)
    ),
    "offweb": lambda u: _has(
        "SELECT 1 FROM tool_usage WHERE user = ? AND surface IN ('cli', 'mcp')", (u,)
    ),
    "ingest": lambda u: _act(u, "ingest_notes"),
    "delegate": lambda u: _act(u, "delegate_task"),
    "mention": lambda u: _has("SELECT 1 FROM mention_log WHERE mentioned_by = ?", (u,)),
    # never ties, and says so in knots.yaml (`ties: never`). A team-wide query
    # would tie this card for people who never used it — the opposite of first
    # use — and services/forge.py deliberately keeps the pusher out of the
    # ledger, so there is no honest per-person signal to key on either.
    "forge": None,
    # reviewed_override=0 on a task_completion verdict means the reviewer WAS
    # the sponsor at verdict time — the loop closed the designed way
    "sponsor_verdict": lambda u: _has(
        "SELECT 1 FROM pending_changes WHERE entity = 'task_completion' AND reviewed_by = ?"
        " AND status IN ('approved', 'rejected') AND reviewed_override = 0",
        (u,),
    ),
    "standup_blocker": lambda u: _has(
        "SELECT 1 FROM standups WHERE author = ? AND TRIM(blockers) != ''", (u,)
    ),
    "finding_converted": lambda u: _act(u, "disposition_finding", "% converted"),
    # terminal statuses only — update_promise also logs an open→open no-op
    "settle": lambda u: any(
        _act(u, "update_promise", f"#% {s}") for s in ("kept", "missed", "withdrawn")
    ),
    "resolve_blocker": lambda u: _act(u, "resolve_blocker"),
    "close_engagement": lambda u: _act(u, "update_engagement", "#% closed"),
    # the brief is a READ, and reads leave no ledger row — but the reader who
    # got there followed a link somebody had to make, so the honest signal is
    # the engagement existing at all under their name
    "engagement_brief": lambda u: _has(
        "SELECT 1 FROM engagements WHERE created_by = ? OR lead = ?", (u, u)
    ),
    # the queue is a read too. Acting on one of its rows is not: a disposition,
    # a reconfirm or a resolve are all writes this person made after reading it
    "needs_a_call": lambda u: (
        _act(u, "disposition_finding")
        or _act(u, "reconfirm_decision")
        or _act(u, "resolve_blocker")
    ),
    # a read with no write at all, and no honest per-person signal — the panel
    # records nothing. Named here rather than omitted, because an absent key
    # fails the registry test and a None says the decision was made.
    "provenance": None,
    "project_memory": lambda u: _has(
        "SELECT 1 FROM memories WHERE created_by = ? AND engagement_id IS NOT NULL", (u,)
    ),
    "review": lambda u: _has(
        "SELECT 1 FROM pending_changes WHERE reviewed_by = ?"
        " AND status IN ('approved', 'rejected')",
        (u,),
    ),
    "ritual": lambda u: _act(u, "week_open") or _act(u, "week_close"),
    "readout": lambda u: _act(u, "exec_readout"),
    "handoff": lambda u: _act(u, "generate_handoff"),
    "model_pick": lambda u: _act(u, "set_model_pick"),
    "playbook_closeout": lambda u: _act(u, "playbook_closeout"),
}

_registry_cache: list[dict] | None = None


def registry() -> list[dict]:
    """Load + validate knots.yaml once. Fails loudly on a card without a
    predicate or a predicate without a card — both are shipping mistakes."""
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache
    data = yaml.safe_load(KNOTS_FILE.read_text())
    knots = data.get("knots") if isinstance(data, dict) else None
    if not isinstance(knots, list) or not knots:
        raise ValueError("knots.yaml is malformed (expected a 'knots' list)")
    seen: set[str] = set()
    for k in knots:
        kid = k.get("id", "")
        if not kid or kid in seen:
            raise ValueError(f"knots.yaml: missing or duplicate id '{kid}'")
        seen.add(kid)
        if kid not in PREDICATES:
            raise ValueError(f"knot '{kid}' has no predicate in fieldguide.PREDICATES")
        if k.get("set") not in SETS:
            raise ValueError(f"knot '{kid}' has invalid set '{k.get('set')}'")
        ties = k.get("ties", "predicate")
        if ties not in ("predicate", "mark", "never"):
            raise ValueError(f"knot '{kid}' has unknown ties '{ties}'")
        # a card with no predicate must say how it DOES tie, or it becomes an
        # unsatisfiable nag: unadopted() would report it forever and the
        # weekly suggestion would keep offering it to everyone
        if PREDICATES[kid] is None and ties == "predicate":
            raise ValueError(
                f"knot '{kid}' has no predicate, so it must declare ties:"
                " 'mark' (a route calls fieldguide.mark) or 'never'"
            )
        # and the reverse: `ties: never` on a card that HAS a predicate would
        # quietly hide a real feature from the zero-adoption sweep
        if PREDICATES[kid] is not None and ties != "predicate":
            raise ValueError(f"knot '{kid}' has a predicate, so ties must be 'predicate'")
        for field in ("feature", "knot", "pitch", "how", "link", "since"):
            if not k.get(field):
                raise ValueError(f"knot '{kid}' is missing '{field}'")
        if not str(k["link"]).startswith("/"):
            raise ValueError(f"knot '{kid}' link must be an in-app path")
        db.validate_date("since", str(k["since"]), allow_clear=False)
        # suggestion exclusion keys on role, grouping keys on set — a card
        # with one but not the other would be pushed as a weekly suggestion
        # despite sitting behind the manager toggle
        if (k["set"] == "manager") != (k.get("role") == "manager"):
            raise ValueError(f"knot '{kid}': set 'manager' and role 'manager' must travel together")
    orphans = set(PREDICATES) - seen
    if orphans:
        raise ValueError(f"predicates without a card in knots.yaml: {sorted(orphans)}")
    _registry_cache = knots
    # a copy per caller — the cache must not be poisonable by a mutating one.
    # dict(k), not a bare list copy: the cards are the mutable part
    return [dict(k) for k in knots]


def _is_active_human(person: str) -> bool:
    row = db.query_one(
        "SELECT 1 FROM users WHERE name = ? AND kind = 'human' AND active = 1"
        " AND name != 'anonymous'",
        (person,),
    )
    return row is not None


def _tied(person: str) -> dict[str, dict]:
    return {
        r["knot"]: r
        for r in db.query(
            "SELECT knot, seen, first_at FROM feature_unlocks WHERE person = ? AND kind = 'tied'",
            (person,),
        )
    }


# hint() rides My Day and the nav menu, so detect() would otherwise run on
# nearly every page load — 1-2 queries per untied knot, some LIKE scans over
# activity. Within the TTL, hint() reuses the last sweep's rows; guide() and
# unadopted() always sweep, so the guide page itself is never stale.
# Process-local like ratelimit; the conftest autouse reset clears it so a
# sweep against one test's database cannot suppress the next test's.
DETECT_TTL_SECONDS = 15 * 60
_last_detect: dict[str, float] = {}


def reset() -> None:
    _last_detect.clear()


def detect(person: str) -> int:
    """Evaluate untied predicates and materialize unlocks. A person's very
    first detection seeds silently (seen=1): a veteran's history renders as
    already-tied with zero ceremony, never as a wall of 'newly tied'."""
    if not _is_active_human(person):
        return 0
    tied = _tied(person)
    seeding = not tied
    n = 0
    for k in registry():
        pred = PREDICATES[k["id"]]
        if k["id"] in tied or pred is None:
            continue
        try:
            hit = pred(person)
        except Exception:
            # a predicate broken by schema drift must be loud in logs but
            # must not take the guide (or the findings run) down with it
            log.exception("field-guide predicate %s crashed", k["id"])
            continue
        if hit:
            n += db.execute_rowcount(
                "INSERT OR IGNORE INTO feature_unlocks (person, knot, kind, seen, first_at)"
                " VALUES (?, ?, 'tied', ?, ?)",
                (person, k["id"], 1 if seeding else 0, db.now()),
            )
    # stamped at the END: a sweep that raised above must not buy hint() 15
    # minutes of silence on the strength of a sweep that never ran
    _last_detect[person] = time.monotonic()
    return n


def mark(person: str, knot: str) -> None:
    """Direct tie for read-only features (search, /ask) — fire-and-forget,
    must never break the request it rides on."""
    with contextlib.suppress(Exception):
        if knot not in PREDICATES:
            # a typo'd knot id in a route would otherwise no-op forever
            log.debug("mark() called with unknown knot %r", knot)
            return
        if not _is_active_human(person):
            return
        seeding = not _has(
            "SELECT 1 FROM feature_unlocks WHERE person = ? AND kind = 'tied'", (person,)
        )
        db.execute(
            "INSERT OR IGNORE INTO feature_unlocks (person, knot, kind, seen, first_at)"
            " VALUES (?, ?, 'tied', ?, ?)",
            (person, knot, 1 if seeding else 0, db.now()),
        )


def dismiss(person: str, knot: str) -> dict:
    """Permanently drop a knot from this person's suggestions. The card stays
    on their guide page; only the unprompted nudge goes quiet."""
    if knot not in PREDICATES:
        raise ValueError("unknown knot — the guide page lists every valid name")
    if not _is_active_human(person):
        raise ValueError("pick a name first — the guide is per-person")
    db.execute(
        "INSERT OR IGNORE INTO feature_unlocks (person, knot, kind, seen, first_at)"
        " VALUES (?, ?, 'dismissed', 1, ?)",
        (person, knot, db.now()),
    )
    return {"dismissed": knot}


def _state(person: str, *, throttled: bool = False) -> tuple[dict[str, dict], set[str]]:
    """detect + tied rows + dismissed set — the choreography hint() and
    guide() share. Caller must have verified _is_active_human. throttled=True
    (hint's lightweight read) skips detect within DETECT_TTL_SECONDS of the
    last sweep; the tied/dismissed rows below are read fresh either way."""
    last = _last_detect.get(person)
    if not (throttled and last is not None and time.monotonic() - last < DETECT_TTL_SECONDS):
        detect(person)
    tied = _tied(person)
    dismissed = {
        r["knot"]
        for r in db.query(
            "SELECT knot FROM feature_unlocks WHERE person = ? AND kind = 'dismissed'",
            (person,),
        )
    }
    return tied, dismissed


def _suggestion(cards: list[dict], tied: set[str], dismissed: set[str]) -> dict | None:
    """One untied card, rotating weekly (deterministic within a week for a
    given untied set — tying or dismissing mid-week may reshuffle the pick).
    Manager-tagged cards are never pushed; they wait on the page."""
    candidates = [
        k
        for k in cards
        if k["id"] not in tied
        and k["id"] not in dismissed
        and k.get("role") != "manager"
        # a card that never ties would be offered every week forever
        and k.get("ties") != "never"
    ]
    if not candidates:
        return None
    week = db.today().isocalendar().week
    k = candidates[week % len(candidates)]
    return {"id": k["id"], "feature": k["feature"], "pitch": k["pitch"], "link": k["link"]}


def _tieable(cards: list[dict]) -> int:
    """The denominator of "N of M tied". A card that never ties is not a card
    you missed — counting it caps everyone below M forever, on a number the
    UI presents as completable."""
    return sum(1 for k in cards if k.get("ties") != "never")


def hint(person: str) -> dict:
    """The lightweight read (My Day one-liner, nav menu count): suggestion +
    counts, NO side effects on seen state — landing on My Day must never
    consume the guide page's 'newly tied' strip."""
    cards = registry()
    if not _is_active_human(person):
        return {"suggestion": None, "tied_count": 0, "total": _tieable(cards)}
    tied, dismissed = _state(person, throttled=True)
    ids = {k["id"] for k in cards}
    return {
        "suggestion": _suggestion(cards, set(tied), dismissed),
        # intersect with the registry — a retired card must not leave a
        # veteran at "27 of 26 tied"
        "tied_count": len(set(tied) & ids),
        "total": _tieable(cards),
    }


def guide(person: str) -> dict:
    """The person's own guide — and ONLY their own; there is deliberately no
    way to read anyone else's (docs/FIELD-GUIDE.md, self-scoped forever)."""
    named = _is_active_human(person)
    tied, dismissed = _state(person) if named else ({}, set())
    cards = []
    newly = []
    for k in registry():
        t = tied.get(k["id"])
        card = {
            "id": k["id"],
            "feature": k["feature"],
            "knot": k["knot"],
            "set": k["set"],
            "pitch": k["pitch"],
            "how": k["how"],
            "link": k["link"],
            "role": k.get("role", ""),
            "tied": t is not None,
            # local_day: first_at is a UTC timestamp, and this date is read
            # by the person who earned it. The last [:10] in the backend —
            # leaving one behind makes the slice look sometimes-acceptable,
            # and the next reader cannot tell which sites were considered.
            "tied_on": db.local_day(t["first_at"]) if t else "",
        }
        cards.append(card)
        if t and not t["seen"]:
            newly.append({"id": k["id"], "feature": k["feature"], "knot": k["knot"]})
    # scoped to the rows just shown — an unlock landing mid-request (a
    # concurrent mark() or the findings sweep) must not be swallowed unseen
    for n in newly:
        db.execute(
            "UPDATE feature_unlocks SET seen = 1 WHERE person = ? AND kind = 'tied' AND knot = ?",
            (person, n["id"]),
        )
    return {
        "cards": cards,
        "newly_tied": newly,
        "suggestion": _suggestion(registry(), set(tied), dismissed) if named else None,
        # registry intersection — a retired card must not yield "27 of 26"
        "tied_count": len(set(tied) & {c["id"] for c in cards}),
        "total": _tieable(cards),
        # false = the roster hasn't met this name yet (or it's anonymous/agent)
        # — the UI can explain the all-untied page instead of implying deficit
        "known": named,
    }


def unadopted(grace_days: int = UNADOPTED_GRACE_DAYS) -> list[dict]:
    """Feature-keyed, nameless: cards past their grace window that NOBODY has
    tied. Sweeps detection for all active humans first so the findings rule
    never fires on stale lazy state. Zero-adoption only — when the count is
    zero no individual can be singled out, which is what keeps this on the
    right side of the anti-surveillance rule."""
    humans = db.query(
        "SELECT name FROM users WHERE kind = 'human' AND active = 1 AND name != 'anonymous'"
    )
    for h in humans:
        detect(h["name"])
    cutoff = (db.today() - timedelta(days=grace_days)).isoformat()
    out = []
    for k in registry():
        if str(k["since"]) > cutoff:
            continue
        # a card that never ties has no adoption signal to report — listing it
        # would print a zero-adoption nag every day that nobody can satisfy
        if k.get("ties") == "never":
            continue
        if not _has("SELECT 1 FROM feature_unlocks WHERE knot = ? AND kind = 'tied'", (k["id"],)):
            out.append(
                {
                    "id": k["id"],
                    "feature": k["feature"],
                    "link": k["link"],
                    "since": str(k["since"]),
                }
            )
    return out
