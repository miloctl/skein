"""Field guide: predicates detect real use, unlocks are self-scoped and
silent-seeded, the coverage signal is nameless. Detail-string predicates are
pinned here — if a service reworders its activity line, these break loudly
instead of a knot silently going untieable."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest


def _unwrap(tool):
    for attr in ("original_function", "_tool_func", "func", "__wrapped__"):
        fn = getattr(tool, attr, None)
        if callable(fn):
            return fn
    return tool


def _mint(db, name, kind="human"):
    db.execute(
        "INSERT INTO users (name, kind, active, created_at) VALUES (?, ?, 1, ?)",
        (name, kind, db.now()),
    )


def test_registry_is_valid_and_complete(fresh_db):
    from app.services import fieldguide

    cards = fieldguide.registry()
    assert len(cards) == 53
    ids = {k["id"] for k in cards}
    assert ids == set(fieldguide.PREDICATES)
    for k in cards:
        assert k["set"] in fieldguide.SETS
        assert k["link"].startswith("/")


def test_cached_registry_cards_cannot_be_poisoned(fresh_db):
    from app.services import fieldguide

    fieldguide.registry()  # fill the cache
    caller_cards = fieldguide.registry()
    original = caller_cards[0]["feature"]
    caller_cards[0]["feature"] = "poisoned"

    assert fieldguide.registry()[0]["feature"] == original


def test_field_guide_tool_returns_the_live_registry(fresh_db):
    from app.services import fieldguide
    from app.tools import ALL_TOOLS, field_guide

    rows = json.loads(_unwrap(field_guide)())
    assert len(rows) == len(fieldguide.registry())
    assert set(rows[0]) == {"id", "feature", "knot", "pitch", "how", "link"}
    assert all(row["link"].startswith("/") for row in rows)
    names = {getattr(t, "tool_name", getattr(t, "__name__", "")) for t in ALL_TOOLS}
    assert "field_guide" in names


def test_cards_for_path_match_exact_and_nested_routes(fresh_db):
    from app.services import fieldguide

    exact = {row["id"] for row in fieldguide.cards_for_path("/review")}
    nested = {row["id"] for row in fieldguide.cards_for_path("/review/42")}
    root = {row["id"] for row in fieldguide.cards_for_path("/")}
    chat = {row["id"] for row in fieldguide.cards_for_path("/chat")}

    assert exact == nested == {"review", "sponsor_verdict"}
    assert "capture" in root and "review" not in root
    assert "bosun" in chat
    assert fieldguide.cards_for_path("/reviews") == []


def test_cards_for_path_rejects_non_paths_without_echoing_them(fresh_db):
    from app.services import fieldguide

    rejected = "https://example.test/review"
    with pytest.raises(ValueError, match="in-app path") as exc:
        fieldguide.cards_for_path(rejected)
    assert rejected not in str(exc.value)


def test_field_guide_for_route_does_not_consume_newly_tied_cards(client, fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "tester")
    fieldguide.mark("tester", "search")
    fresh_db.execute(
        "INSERT INTO pending_changes (entity, entity_id, action, payload, proposed_by,"
        " status, reviewed_by, created_at)"
        " VALUES ('task', 1, 'update', '{}', 'agent', 'approved', 'tester', ?)",
        (fresh_db.now(),),
    )
    fieldguide.detect("tester")

    response = client.get("/api/field-guide/for", params={"path": "/review"})

    assert response.status_code == 200
    assert {row["id"] for row in response.json()["cards"]} == {"review", "sponsor_verdict"}
    unseen = fresh_db.query_one(
        "SELECT seen FROM feature_unlocks WHERE person = ? AND knot = ?",
        ("tester", "review"),
    )
    assert unseen == {"seen": 0}


def test_first_watch_projection_is_ordered_and_pure(client, fresh_db):
    _mint(fresh_db, "tester")

    response = client.get("/api/field-guide/first-watch")

    assert response.status_code == 200
    assert [row["id"] for row in response.json()["steps"]] == [
        "first_watch",
        "capture",
        "task_peek",
        "search",
        "review",
        "activity_feed",
        "bosun",
    ]
    assert set(response.json()["steps"][0]) == {
        "id",
        "feature",
        "knot",
        "pitch",
        "how",
        "link",
    }
    assert fresh_db.query("SELECT * FROM feature_unlocks") == []


def test_first_watch_start_marks_only_its_fixed_card(client, fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "tester")
    assert client.get("/api/field-guide/first-watch").status_code == 200

    response = client.post("/api/field-guide/first-watch")

    assert response.status_code == 200
    tied = {row["id"] for row in fieldguide.guide("tester")["cards"] if row["tied"]}
    assert tied == {"first_watch"}


def test_opening_page_help_ties_its_guide_card(client, fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "tester")
    response = client.get("/api/field-guide/for", params={"path": "/review"})
    assert response.status_code == 200
    card = next(row for row in fieldguide.guide("tester")["cards"] if row["id"] == "page_help")
    assert card["tied"] is True


def test_field_guide_for_route_classifies_bad_paths_as_input_errors(client):
    rejected = "//example.test/review"
    response = client.get("/api/field-guide/for", params={"path": rejected})
    assert response.status_code == 400
    assert rejected not in response.text


def test_hint_and_guide_use_the_same_tieable_total(fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "ava")
    assert fieldguide.hint("ava")["total"] == fieldguide.guide("ava")["total"] == 52


def test_first_detection_seeds_silently(fresh_db):
    from app.services import capture, fieldguide

    _mint(fresh_db, "ava")
    capture.capture("todo: try the guide", actor="ava")
    g = fieldguide.guide("ava")
    tied = {c["id"] for c in g["cards"] if c["tied"]}
    assert "capture" in tied
    # history renders as already-tied with zero ceremony — never "newly"
    assert g["newly_tied"] == []


def test_organic_unlock_shows_once_then_settles(fresh_db):
    from app.services import capture, collab, fieldguide

    _mint(fresh_db, "ava")
    capture.capture("todo: seed history", actor="ava")
    fieldguide.guide("ava")  # seed pass
    collab.post_standup(author="ava", yesterday="x", today="y", actor="ava")
    g = fieldguide.guide("ava")
    assert [n["id"] for n in g["newly_tied"]] == ["standup", "guided_first_week"]
    assert fieldguide.guide("ava")["newly_tied"] == []


def test_guided_first_week_ties_only_after_capture_and_standup(fresh_db):
    from app.services import capture, collab, fieldguide

    _mint(fresh_db, "ava")
    capture.capture("todo: learn the basics", actor="ava")
    assert not fieldguide.PREDICATES["guided_first_week"]("ava")
    collab.post_standup(author="ava", yesterday="x", today="y", actor="ava")
    assert fieldguide.PREDICATES["guided_first_week"]("ava")

    _mint(fresh_db, "ben")
    collab.post_standup(author="ben", yesterday="x", today="y", actor="ben")
    assert not fieldguide.PREDICATES["guided_first_week"]("ben")


def test_guided_first_week_backfills_silently_for_veterans(fresh_db, monkeypatch):
    from app.services import capture, collab, fieldguide, scope

    _mint(fresh_db, "ava")
    fieldguide.mark("ava", "search")
    fieldguide.guide("ava")
    monkeypatch.setattr(fresh_db, "now", lambda: "2026-08-14T12:00:00+00:00")
    capture.capture("todo: historical setup", actor="ava", visibility=scope.PRIVATE)
    collab.post_standup(
        author="ava",
        yesterday="x",
        today="y",
        actor="ava",
        visibility=scope.PRIVATE,
    )
    guide = fieldguide.guide("ava")
    card = next(row for row in guide["cards"] if row["id"] == "guided_first_week")
    assert card["tied"] is True
    assert "guided_first_week" not in {row["id"] for row in guide["newly_tied"]}


def test_terminal_verb_predicates_pin_activity_wording(fresh_db):
    from app.services import engagements, fieldguide, promises, work

    _mint(fresh_db, "ava")
    t = work.create_task(title="t", actor="ava")
    work.update_task(t["id"], status="done", actor="ava")
    c = promises.add_promise(promise="demo Friday", actor="ava")
    promises.update_promise(c["id"], "kept", actor="ava")
    e = engagements.create_engagement(name="probe", project_class="prototype", actor="ava")
    engagements.update_engagement(e["id"], status="closed", conclusion="unmeasured", actor="ava")
    tied = {
        k["id"]
        for k in fieldguide.registry()
        if fieldguide.PREDICATES[k["id"]] is not None and fieldguide.PREDICATES[k["id"]]("ava")
    }
    assert {"task_done", "settle", "close_engagement", "promise"} <= tied


def test_sponsor_verdict_requires_non_override(fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "ava")
    fresh_db.execute(
        "INSERT INTO pending_changes (entity, entity_id, action, payload, proposed_by,"
        " status, reviewed_by, reviewed_override, created_at)"
        " VALUES ('task_completion', 1, 'update', '{}', 'agent', 'approved', 'ava', 1, ?)",
        (fresh_db.now(),),
    )
    assert not fieldguide.PREDICATES["sponsor_verdict"]("ava")
    fresh_db.execute(
        "UPDATE pending_changes SET reviewed_override = 0 WHERE reviewed_by = 'ava'", ()
    )
    assert fieldguide.PREDICATES["sponsor_verdict"]("ava")


def test_agents_and_anonymous_never_tie(fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "research-agent", kind="agent")
    fresh_db.log_activity("research-agent", "capture", "note #1")
    assert fieldguide.detect("research-agent") == 0
    assert fieldguide.detect("anonymous") == 0
    g = fieldguide.guide("anonymous")
    assert g["tied_count"] == 0 and g["suggestion"] is None
    assert fresh_db.query("SELECT * FROM feature_unlocks") == []


def test_guides_are_self_scoped(fresh_db):
    from app.services import capture, fieldguide

    _mint(fresh_db, "ava")
    _mint(fresh_db, "ben")
    capture.capture("todo: mine", actor="ava")
    ava = fieldguide.guide("ava")
    ben = fieldguide.guide("ben")
    assert any(c["tied"] for c in ava["cards"])
    assert not any(c["tied"] for c in ben["cards"])
    # nothing in one person's payload references anyone else
    assert "ava" not in str(ben)


def test_dismiss_kills_the_suggestion_permanently(fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "ava")
    g = fieldguide.guide("ava")
    doomed = g["suggestion"]["id"]
    fieldguide.dismiss("ava", doomed)
    for _ in range(len(fieldguide.registry()) + 1):
        s = fieldguide.guide("ava")["suggestion"]
        assert s is None or s["id"] != doomed


def test_suggestion_never_pushes_manager_cards(fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "ava")
    manager_ids = {k["id"] for k in fieldguide.registry() if k.get("role") == "manager"}
    s = fieldguide.guide("ava")["suggestion"]
    assert s is not None and s["id"] not in manager_ids


def test_task_peek_ties_only_after_a_readable_task_projection(client, fresh_db):
    from app.services import fieldguide, work

    _mint(fresh_db, "tester")
    task = work.create_task(title="Readable", actor="tester")

    assert client.get("/api/tasks/999999").status_code == 404
    assert (
        fresh_db.query_one(
            "SELECT 1 FROM feature_unlocks WHERE person = ? AND knot = ?",
            ("tester", "task_peek"),
        )
        is None
    )
    assert client.get(f"/api/tasks/{task['id']}").status_code == 200

    tied = {row["id"] for row in fieldguide.guide("tester")["cards"] if row["tied"]}
    assert "task_peek" in tied
    assert fresh_db.query_one(
        "SELECT COUNT(*) AS n FROM feature_unlocks WHERE person = ? AND knot = ?",
        ("tester", "task_peek"),
    ) == {"n": 1}


def test_mark_ties_readonly_features_via_route(client, fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "tester")
    client.get("/api/search", params={"q": "anything"})
    g = fieldguide.guide("tester")
    assert any(c["id"] == "search" and c["tied"] for c in g["cards"])


def test_mark_refuses_cards_without_mark_semantics(fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "ava")
    fieldguide.mark("ava", "review")
    fieldguide.mark("ava", "forge")
    fieldguide.mark("ava", "missing")

    assert fresh_db.query("SELECT knot FROM feature_unlocks WHERE person = ?", ("ava",)) == []


def test_first_direct_mark_materializes_old_predicates_before_the_new_tie(fresh_db):
    from app.services import capture, fieldguide

    _mint(fresh_db, "ava")
    capture.capture("todo: historical work", actor="ava")
    fieldguide.mark("ava", "search")

    guide = fieldguide.guide("ava")
    assert [row["id"] for row in guide["newly_tied"]] == ["search"]
    capture_card = next(row for row in guide["cards"] if row["id"] == "capture")
    assert capture_card["tied"] is True


def test_direct_mark_takes_the_person_lock(fresh_db, monkeypatch):
    from app.services import fieldguide

    _mint(fresh_db, "ava")
    original = fieldguide.db.name_lock
    calls = []

    def tracked(namespace, name):
        calls.append((namespace, name))
        original(namespace, name)

    monkeypatch.setattr(fieldguide.db, "name_lock", tracked)
    fieldguide.mark("ava", "search")

    assert calls == [(fieldguide.db.LOCK_FIELD_GUIDE, "ava")]


def test_concurrent_first_marks_leave_one_visible_unlock(fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "ava")
    start = threading.Barrier(2)

    def mark(knot):
        start.wait(timeout=3)
        fieldguide.mark("ava", knot)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(mark, ("search", "page_help")))

    rows = fresh_db.query(
        "SELECT seen FROM feature_unlocks WHERE person = ? AND kind = 'tied' ORDER BY seen",
        ("ava",),
    )
    assert rows == [{"seen": 0}, {"seen": 1}]


def test_reports_page_ties_the_read_only_history_knot(client, fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "tester")
    client.get("/api/artifacts/page")
    card = next(row for row in fieldguide.guide("tester")["cards"] if row["id"] == "reports")
    assert card["tied"] is True


def test_reading_a_document_ties_the_agent_document_knot(client, fresh_db):
    """The write is signed by the AGENT, so no ledger predicate can find the
    person who asked — the honest per-person moment is the read."""
    from app.services import documents, fieldguide

    _mint(fresh_db, "tester")
    doc = documents.create_document("Plan", "# Plan\n", actor="agent")["artifact_id"]
    client.get(f"/api/artifacts/{doc}")
    card = next(r for r in fieldguide.guide("tester")["cards"] if r["id"] == "agent_document")
    assert card["tied"] is True


def test_reading_a_report_that_is_not_a_document_does_not_tie_it(client, fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "tester")
    client.post("/api/portfolio/readout")
    readout = next(a for a in client.get("/api/artifacts").json() if a["kind"] == "readout")
    client.get(f"/api/artifacts/{readout['id']}")
    card = next(r for r in fieldguide.guide("tester")["cards"] if r["id"] == "agent_document")
    assert card["tied"] is False


def test_todays_three_route_ties_only_its_fixed_knot(client, fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "tester")
    response = client.post("/api/field-guide/todays-three")
    assert response.status_code == 200
    tied = {row["id"] for row in fieldguide.guide("tester")["cards"] if row["tied"]}
    assert tied == {"todays_three"}


def test_first_watch_mark_is_rate_capped(client, fresh_db):
    from app import ratelimit

    _mint(fresh_db, "tester")
    ratelimit.reset()
    for _ in range(30):
        assert client.post("/api/field-guide/first-watch").status_code == 200
    response = client.post("/api/field-guide/first-watch")
    assert response.status_code == 429
    ratelimit.reset()


def test_todays_three_mark_is_rate_capped(client, fresh_db):
    from app import ratelimit

    _mint(fresh_db, "tester")
    ratelimit.reset()
    for _ in range(30):
        assert client.post("/api/field-guide/todays-three").status_code == 200
    response = client.post("/api/field-guide/todays-three")
    assert response.status_code == 429
    ratelimit.reset()


def test_authority_change_ties_the_half_life_knot(fresh_db):
    from app.services import delegation, fieldguide

    _mint(fresh_db, "ava")
    delegation.set_authority("scout", "task", "review", actor="ava")
    assert fieldguide.PREDICATES["authority_half_life"]("ava")


def test_unadopted_is_nameless_and_respects_grace(fresh_db):
    from app.services import capture, fieldguide

    _mint(fresh_db, "ava")
    # every card is inside its 30-day grace window on ship day
    assert fieldguide.unadopted() == []
    zero_grace = fieldguide.unadopted(grace_days=0)
    assert any(k["id"] == "capture" for k in zero_grace)
    for k in zero_grace:
        assert set(k) == {"id", "feature", "link", "since"}  # no names, ever
    capture.capture("todo: adopt it", actor="ava")
    assert not any(k["id"] == "capture" for k in fieldguide.unadopted(grace_days=0))


def test_more_activity_wordings_are_pinned(fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "ava")
    fresh_db.log_activity("ava", "disposition_finding", "#7 converted")
    fresh_db.log_activity("ava", "complete_task", "#9 ship it")
    fresh_db.log_activity("ava", "update_promise", "#3 open")  # no-op, not a settle
    assert fieldguide.PREDICATES["finding_converted"]("ava")
    assert fieldguide.PREDICATES["task_done"]("ava")
    assert not fieldguide.PREDICATES["settle"]("ava")
    fresh_db.log_activity("ava", "update_promise", "#3 kept")
    assert fieldguide.PREDICATES["settle"]("ava")


def test_chat_ties_on_a_thread_not_a_page_load(fresh_db):
    from app.services import adoption, fieldguide

    _mint(fresh_db, "ava")
    # opening /chat records tool_usage surface=chat — must NOT tie the knot
    adoption.record_use("ava", "chat")
    assert not fieldguide.PREDICATES["chat"]("ava")
    fresh_db.execute(
        "INSERT INTO chat_threads (id, owner, title, created_at, updated_at)"
        " VALUES ('t1', 'ava', 'New chat', ?, ?)",
        (fresh_db.now(), fresh_db.now()),
    )
    assert fieldguide.PREDICATES["chat"]("ava")
    # but CLI/MCP usage does tie offweb — that's the surface itself
    adoption.record_use("ava", "cli")
    assert fieldguide.PREDICATES["offweb"]("ava")


def test_shared_chat_ties_only_after_membership_exists(fresh_db):
    from app.services import chat_threads, fieldguide

    _mint(fresh_db, "ava")
    assert not fieldguide.PREDICATES["shared_chat"]("ava")
    chat_threads.create_shared_chat("Private room", "ava")
    assert fieldguide.PREDICATES["shared_chat"]("ava")


def test_mark_seeds_silently_on_first_ever_unlock(fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "ava")
    fieldguide.mark("ava", "search")
    assert fieldguide.guide("ava")["newly_tied"] == []  # first unlock = seed
    fieldguide.mark("ava", "page_help")
    assert [n["id"] for n in fieldguide.guide("ava")["newly_tied"]] == ["page_help"]


def test_hint_never_consumes_the_newly_tied_strip(fresh_db):
    from app.services import capture, collab, fieldguide

    _mint(fresh_db, "ava")
    capture.capture("todo: seed history", actor="ava")
    fieldguide.guide("ava")  # seed pass
    collab.post_standup(author="ava", yesterday="x", today="y", actor="ava")
    fieldguide.hint("ava")  # My Day landing — must NOT mark seen
    assert [n["id"] for n in fieldguide.guide("ava")["newly_tied"]] == [
        "standup",
        "guided_first_week",
    ]


def test_dismiss_route_rejects_unknown_knot(client, fresh_db):
    _mint(fresh_db, "tester")
    r = client.post("/api/field-guide/dismiss", json={"knot": "granny-knot"})
    assert r.status_code == 400
    ok = client.post("/api/field-guide/dismiss", json={"knot": "growth"})
    assert ok.status_code == 200


def test_dismiss_is_rate_capped(client, fresh_db):
    from app import ratelimit

    _mint(fresh_db, "tester")
    ratelimit.reset()
    for _ in range(30):
        client.post("/api/field-guide/dismiss", json={"knot": "growth"})
    r = client.post("/api/field-guide/dismiss", json={"knot": "growth"})
    assert r.status_code == 429 and "The limit for" in r.json()["detail"]
    ratelimit.reset()


def test_registry_rejects_protocol_relative_links(fresh_db, tmp_path, monkeypatch):
    from app.services import fieldguide

    bad = tmp_path / "knots.yaml"
    bad.write_text(
        "knots:\n"
        "  - id: capture\n"
        "    feature: X\n"
        "    knot: K\n"
        "    set: loops\n"
        "    pitch: p\n"
        "    how: h\n"
        "    link: //example.test/x\n"
        "    since: 2026-07-31\n"
    )
    monkeypatch.setattr(fieldguide, "KNOTS_FILE", bad)
    monkeypatch.setattr(fieldguide, "PREDICATES", {"capture": lambda _: False})
    monkeypatch.setattr(fieldguide, "_registry_cache", None)
    with pytest.raises(ValueError, match="in-app path") as exc:
        fieldguide.registry()
    assert "example.test" not in str(exc.value)


def test_registry_rejects_manager_set_without_role(fresh_db, tmp_path, monkeypatch):
    import pytest

    from app.services import fieldguide

    bad = tmp_path / "knots.yaml"
    bad.write_text(
        "knots:\n"
        "  - id: capture\n"
        "    feature: X\n"
        "    knot: K\n"
        "    set: manager\n"  # no role: manager — would be pushed as a suggestion
        "    pitch: p\n"
        "    how: h\n"
        "    link: /x\n"
        "    since: 2026-07-31\n"
    )
    monkeypatch.setattr(fieldguide, "KNOTS_FILE", bad)
    monkeypatch.setattr(fieldguide, "_registry_cache", None)
    with pytest.raises(ValueError, match="travel together"):
        fieldguide.registry()
    monkeypatch.setattr(fieldguide, "_registry_cache", None)


def test_a_card_must_say_how_it_ties(fresh_db, monkeypatch):
    """The whole `ties` mechanism could be deleted with the suite green. It
    exists because a predicate-less card that says nothing becomes a nag no
    one can satisfy: unadopted() reports it forever and the weekly suggestion
    keeps offering it."""
    import pytest

    from app.services import fieldguide

    def rebuild(cards):
        monkeypatch.setattr(fieldguide, "_registry_cache", None)
        monkeypatch.setattr(fieldguide.yaml, "safe_load", lambda _t: {"knots": cards})

    base = [dict(k) for k in fieldguide.registry()]
    forge_card = next(k for k in base if k["id"] == "forge")
    capture = next(k for k in base if k["id"] == "capture")

    forge_card.pop("ties")
    rebuild(base)
    with pytest.raises(ValueError, match="must declare ties"):
        fieldguide.registry()

    forge_card["ties"] = "sometimes"
    rebuild(base)
    with pytest.raises(ValueError, match="unknown ties"):
        fieldguide.registry()

    # and the reverse: a card WITH a predicate cannot claim never and hide a
    # real feature from the zero-adoption sweep
    forge_card["ties"] = "never"
    capture["ties"] = "never"
    rebuild(base)
    with pytest.raises(ValueError, match="ties must be 'predicate'"):
        fieldguide.registry()


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        (["capture", "capture"], "repeats"),
        (["missing"], "unknown knot"),
        (["forge"], "never ties"),
    ],
)
def test_tour_manifest_rejects_invalid_steps(fresh_db, monkeypatch, steps, message):
    from app.services import fieldguide

    cards = fieldguide.registry()
    monkeypatch.setattr(fieldguide, "_registry_cache", None)
    monkeypatch.setattr(
        fieldguide.yaml,
        "safe_load",
        lambda _text: {"tours": {"first-watch": steps}, "knots": cards},
    )

    with pytest.raises(ValueError, match=message):
        fieldguide.registry()


def test_a_never_tying_card_is_out_of_the_denominator_and_the_sweep(fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "ava")
    cards = fieldguide.registry()
    never = [k["id"] for k in cards if k.get("ties") == "never"]
    assert never  # the mechanism is live, not theoretical
    assert fieldguide.hint("ava")["total"] == len(cards) - len(never)
    assert not any(k["id"] in never for k in fieldguide.unadopted(grace_days=0))
    for _ in range(len(cards) + 1):
        s = fieldguide.guide("ava")["suggestion"]
        assert s is None or s["id"] not in never


def test_hint_skips_detect_within_ttl_and_guide_never_does(fresh_db, monkeypatch):
    from app.services import fieldguide, users

    users.ensure_user("ava")
    fieldguide.hint("ava")  # first read sweeps and stamps the throttle clock
    swept: list[str] = []
    monkeypatch.setattr(fieldguide, "detect", lambda person: swept.append(person))
    fieldguide.hint("ava")
    assert swept == []  # within DETECT_TTL_SECONDS the lightweight read skips the sweep
    fieldguide.guide("ava")
    assert swept == ["ava"]  # the guide page always sweeps
