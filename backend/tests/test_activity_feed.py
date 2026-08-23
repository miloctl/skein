"""The activity feed: one sentence per ledger row, scoped to agents, the
system, and the viewer's own actions. The scope is the anti-surveillance rule
made structural — another human's rows must never appear, and the restriction
lives in the service so no route can widen it."""

import threading

from app import db
from app.services import activity, users


def _seed_people(fresh_db):
    users.ensure_user("ava")
    users.ensure_user("ben")
    users.ensure_user("bot", kind="agent")


def test_scoping_is_the_point(fresh_db):
    """ava sees her own rows, the agent's, and the system's — never ben's."""
    _seed_people(fresh_db)
    db.log_activity("ava", "save_note", "#1 mine")
    db.log_activity("ben", "save_note", "#2 his")
    db.log_activity("bot", "resolve_blocker", "#3 fixed")
    db.log_activity("scheduler", "publish_digest", "")

    actors = {e["actor"] for e in activity.feed("ava")["entries"]}
    assert actors == {"ava", "bot", "scheduler"}
    assert "ben" not in str(activity.feed("ava"))

    # symmetric: ben never sees ava's rows either
    assert {e["actor"] for e in activity.feed("ben")["entries"]} == {"ben", "bot", "scheduler"}


def test_a_deactivated_human_is_still_a_human(fresh_db):
    """Deactivation removes someone from the roster, not from the
    anti-surveillance rule — their history stays theirs."""
    _seed_people(fresh_db)
    db.log_activity("ben", "save_note", "#1 his")
    users.set_active("ben", False, actor="ava")
    assert all(e["actor"] != "ben" for e in activity.feed("ava")["entries"])


def test_who_classification(fresh_db):
    _seed_people(fresh_db)
    db.log_activity("ava", "capture", "x")
    db.log_activity("bot", "capture", "y")
    db.log_activity("scheduler", "publish_digest", "")
    by_actor = {e["actor"]: e["who"] for e in activity.feed("ava")["entries"]}
    assert by_actor == {"ava": "you", "bot": "agent", "scheduler": "system"}


def test_registered_actions_render_sentences(fresh_db):
    _seed_people(fresh_db)
    db.log_activity("bot", "resolve_blocker", "#3 vendor key arrived")
    entry = activity.feed("ava")["entries"][0]
    assert entry["sentence"] == "bot resolved a blocker"
    assert entry["registered"] is True
    assert entry["salience"] == "normal"


def test_an_unregistered_action_degrades_honestly(fresh_db):
    """A new log_activity call without a registry entry renders as the raw
    action name — clearly generic, never a fabricated verb, never a raise."""
    _seed_people(fresh_db)
    db.log_activity("bot", "brand_new_action", "payload")
    entry = activity.feed("ava")["entries"][0]
    assert entry["sentence"] == "bot: brand_new_action"
    assert entry["registered"] is False


def test_salience_tracks_consequence(fresh_db):
    _seed_people(fresh_db)
    db.log_activity("bot", "forget", "#1 a memory")
    db.log_activity("bot", "save_note", "#2")
    db.log_activity("scheduler", "publish_context_pack", "v3")
    by_action = {e["action"]: e["salience"] for e in activity.feed("ava")["entries"]}
    assert by_action == {
        "forget": "loud",
        "save_note": "normal",
        "publish_context_pack": "quiet",
    }


def test_cursor_pages_without_gaps_or_repeats(fresh_db):
    _seed_people(fresh_db)
    for i in range(7):
        db.log_activity("bot", "capture", f"#{i}")
    first = activity.feed("ava", limit=3)
    assert [e["seq"] for e in first["entries"]] == [7, 6, 5]
    assert first["next_before"] == 5
    second = activity.feed("ava", limit=3, before=first["next_before"])
    assert [e["seq"] for e in second["entries"]] == [4, 3, 2]
    third = activity.feed("ava", limit=3, before=second["next_before"])
    assert [e["seq"] for e in third["entries"]] == [1]
    assert third["next_before"] is None


def test_cursor_is_stable_while_new_rows_arrive(fresh_db):
    """seq only grows, so a page taken before new appends still returns the
    same older rows — the reason the cursor is seq and not OFFSET."""
    _seed_people(fresh_db)
    for i in range(4):
        db.log_activity("bot", "capture", f"#{i}")
    first = activity.feed("ava", limit=2)
    threads = [
        threading.Thread(target=db.log_activity, args=("bot", "capture", f"new{i}"))
        for i in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    second = activity.feed("ava", limit=2, before=first["next_before"])
    assert [e["seq"] for e in second["entries"]] == [2, 1]


def test_pre_chain_rows_stay_out_of_the_feed(fresh_db):
    """seq is the cursor; unchained legacy rows have none and stay reachable
    through the raw endpoint instead."""
    _seed_people(fresh_db)
    db.execute(
        "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
        ("bot", "capture", "legacy", db.now()),
    )
    db.log_activity("bot", "capture", "chained")
    entries = activity.feed("ava")["entries"]
    assert [e["detail"] for e in entries] == ["chained"]


def test_limit_is_bounded(fresh_db):
    _seed_people(fresh_db)
    db.log_activity("bot", "capture", "x")
    assert activity.feed("ava", limit=100000)["entries"]  # clamped, not a 500


def test_the_verb_registry_and_the_logged_actions_agree(fresh_db):
    """Both directions. A renamed action with a stale registry entry silently
    degrades every future row of that verb to the generic form — and a NEW
    action nobody registered renders generic from its first day, which for a
    year meant supersede_decision and set_user_active read as raw slugs in
    the feed. One check per direction, so the failure names the fix."""
    import re
    from pathlib import Path

    src_dir = Path(activity.__file__).resolve().parent.parent
    logged = set()
    for path in src_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        logged |= set(re.findall(r'log_activity\(\s*[^,]+,\s*"([a-z_]+)"', text))
        for m in re.finditer(r"log_activity\(\s*$", text, re.M):
            # multiline call: the action is the first string AFTER a comma —
            # the first string outright is often the actor ("system")
            tail = text[m.end() : m.end() + 200]
            found = re.search(r',\s*"([a-z_]+)"', tail)
            if found:
                logged.add(found.group(1))
    stale = set(activity.VERBS) - logged
    assert not stale, f"registry names actions nothing logs: {sorted(stale)}"
    unregistered = logged - set(activity.VERBS)
    assert not unregistered, f"actions logged but not registered: {sorted(unregistered)}"


def test_the_route_scopes_to_the_header_user(client, fresh_db):
    _seed_people(fresh_db)
    db.log_activity("ben", "save_note", "#1 his")
    db.log_activity("bot", "capture", "#2")
    body = client.get("/api/activity/feed").json()  # X-User: tester
    assert all(e["actor"] != "ben" for e in body["entries"])
    assert any(e["actor"] == "bot" for e in body["entries"])


def test_rename_leaves_the_ledger_alone(fresh_db):
    """rename rewrote activity.actor in bulk, and every chained digest covers
    its actor — one rename permanently broke verify_chain at the renamed
    person's earliest row, with the external anchor making re-chaining
    impossible by design. History stays under the old name."""
    _seed_people(fresh_db)
    db.log_activity("ben", "save_note", "#1 his")
    assert activity.verify_chain()["ok"]
    users.rename_user("ben", "benjamin", actor="ava")
    assert activity.verify_chain()["ok"]
    assert db.query_row("SELECT actor FROM activity WHERE seq = 1")["actor"] == "ben"
    # and the old-name rows stay hidden from everyone: default-closed scope
    assert all(e["actor"] != "ben" for e in activity.feed("benjamin")["entries"])


def test_an_unrostered_human_actor_is_hidden_not_shown_as_system(fresh_db):
    """The blocklist version was default-open: a human writing under a name
    with no users row (the Slack path did this) leaked to every viewer's feed
    labeled system. Default-closed: unknown actors are hidden."""
    _seed_people(fresh_db)
    db.log_activity("jane.slack", "capture", "her note")
    assert activity.feed("ava")["entries"] == []


def test_the_raw_endpoint_is_scoped_like_the_feed(client, fresh_db):
    """Without this, GET /api/activity was the one-curl bypass of the rule
    the feed enforces."""
    from app.services import collab

    _seed_people(fresh_db)
    db.log_activity("ben", "save_note", "#1 his")
    db.log_activity("bot", "capture", "#2")
    db.execute(
        "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
        ("bot", "capture", "legacy unchained", db.now()),
    )
    rows = client.get("/api/activity").json()
    actors = {r["actor"] for r in rows}
    assert "ben" not in actors
    assert "bot" in actors
    # unlike the feed, the raw endpoint still reaches unchained rows
    assert any(r["detail"] == "legacy unchained" for r in rows)
    service_actors = {r["actor"] for r in collab.recent_activity("ava")}
    assert "ben" not in service_actors  # scheduler/system rows are fine
    assert "bot" in service_actors


def test_my_day_recent_activity_is_scoped(fresh_db):
    """My Day must not be the surface where colleagues watch each other."""
    from app.services import briefing

    _seed_people(fresh_db)
    db.log_activity("ava", "save_note", "#1 mine")
    db.log_activity("ben", "save_note", "#2 his")
    db.log_activity("bot", "capture", "#3")
    recent = briefing.my_day("ava")["team"]["recent_activity"]
    actors = {r["actor"] for r in recent}
    assert "ben" not in actors
    assert {"ava", "bot"} <= actors


def test_limit_clamp_value(fresh_db):
    _seed_people(fresh_db)
    for i in range(205):
        db.log_activity("bot", "capture", f"#{i}")
    assert len(activity.feed("ava", limit=100000)["entries"]) == 200


def test_rename_carries_the_field_guide_state(fresh_db):
    """The ledger-immutability fix removed the accidental self-heal: activity
    used to be renamed, so predicates re-tied under the new name. Now the
    unlock STATE moves instead — a renamed veteran must not watch their guide
    reset toward zero."""
    from app.services import fieldguide

    _seed_people(fresh_db)
    db.log_activity("ben", "capture", "note #1")
    fieldguide.detect("ben")
    assert any(k["id"] == "capture" and k["tied"] for k in fieldguide.guide("ben")["cards"])
    users.rename_user("ben", "benjamin", actor="ava")
    assert any(k["id"] == "capture" and k["tied"] for k in fieldguide.guide("benjamin")["cards"])
    # merge: the target's existing unlock wins, no UNIQUE collision
    users.ensure_user("cara")
    db.log_activity("cara", "capture", "note #2")
    fieldguide.detect("cara")
    users.rename_user("benjamin", "cara", actor="ava")
    tied = [k for k in fieldguide.guide("cara")["cards"] if k["tied"]]
    assert any(k["id"] == "capture" for k in tied)
