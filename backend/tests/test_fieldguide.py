"""Field guide: predicates detect real use, unlocks are self-scoped and
silent-seeded, the coverage signal is nameless. Detail-string predicates are
pinned here — if a service reworders its activity line, these break loudly
instead of a knot silently going untieable."""


def _mint(db, name, kind="human"):
    db.execute(
        "INSERT INTO users (name, kind, active, created_at) VALUES (?, ?, 1, ?)",
        (name, kind, db.now()),
    )


def test_registry_is_valid_and_complete(fresh_db):
    from app.services import fieldguide

    cards = fieldguide.registry()
    assert len(cards) == 30
    ids = {k["id"] for k in cards}
    assert ids == set(fieldguide.PREDICATES)
    for k in cards:
        assert k["set"] in fieldguide.SETS
        assert k["link"].startswith("/")


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
    assert [n["id"] for n in g["newly_tied"]] == ["standup"]
    assert fieldguide.guide("ava")["newly_tied"] == []


def test_terminal_verb_predicates_pin_activity_wording(fresh_db):
    from app.services import commitments, engagements, fieldguide, work

    _mint(fresh_db, "ava")
    t = work.create_task(title="t", actor="ava")
    work.update_task(t["id"], status="done", actor="ava")
    c = commitments.add_commitment(promise="demo Friday", actor="ava")
    commitments.update_commitment(c["id"], "kept", actor="ava")
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


def test_mark_ties_readonly_features_via_route(client, fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "tester")
    client.get("/api/search", params={"q": "anything"})
    g = fieldguide.guide("tester")
    assert any(c["id"] == "search" and c["tied"] for c in g["cards"])


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
    fresh_db.log_activity("ava", "update_commitment", "#3 open")  # no-op, not a settle
    assert fieldguide.PREDICATES["finding_converted"]("ava")
    assert fieldguide.PREDICATES["task_done"]("ava")
    assert not fieldguide.PREDICATES["settle"]("ava")
    fresh_db.log_activity("ava", "update_commitment", "#3 kept")
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


def test_mark_seeds_silently_on_first_ever_unlock(fresh_db):
    from app.services import fieldguide

    _mint(fresh_db, "ava")
    fieldguide.mark("ava", "search")
    assert fieldguide.guide("ava")["newly_tied"] == []  # first unlock = seed
    fieldguide.mark("ava", "chat")
    assert [n["id"] for n in fieldguide.guide("ava")["newly_tied"]] == ["chat"]


def test_hint_never_consumes_the_newly_tied_strip(fresh_db):
    from app.services import capture, collab, fieldguide

    _mint(fresh_db, "ava")
    capture.capture("todo: seed history", actor="ava")
    fieldguide.guide("ava")  # seed pass
    collab.post_standup(author="ava", yesterday="x", today="y", actor="ava")
    fieldguide.hint("ava")  # My Day landing — must NOT mark seen
    assert [n["id"] for n in fieldguide.guide("ava")["newly_tied"]] == ["standup"]


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
    assert r.status_code == 400 and "slow down" in r.json()["detail"]
    ratelimit.reset()


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
