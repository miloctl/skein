"""UX restructure coverage: capture prefix precedence, briefing digest
helpers (_coalesce/_ellipsize/_human_digest/_standup_suggestion), zero-stat
ship-it recaps, onboarding scopes, and key self-request edges."""

import pytest

# ---- capture precedence -----------------------------------------------------------


@pytest.mark.parametrize(
    "text,kind",
    [
        ("req: blocked on vendor sandbox", "request"),  # the live bug: prefix beats heuristic
        ("request: new dashboards", "request"),
        ("todo: fix login?", "task"),  # explicit prefix beats trailing "?"
        ("blocked: waiting on legal", "blocker"),
        ("note: we decided to punt", "note"),  # prefix beats decision heuristic
        ("q: blocked on CI", "question"),
        ("is prod ok?", "question"),  # bare trailing "?" still classifies
    ],
)
def test_capture_prefix_beats_content_heuristics(text, kind):
    from app.services import capture

    assert capture.classify(text) == kind


def test_capture_req_blocked_on_routes_to_intake_not_blockers(client):
    out = client.post("/api/capture", json={"text": "req: blocked on vendor sandbox"}).json()
    assert out["kind"] == "request"
    reqs = client.get("/api/intake").json()
    assert any(r["title"] == "blocked on vendor sandbox" for r in reqs)
    assert client.get("/api/blockers").json() == []


# ---- _ellipsize -------------------------------------------------------------------


def test_ellipsize_short_and_exact_strings_untouched():
    from app.services.briefing import _ellipsize

    assert _ellipsize("hello", 100) == "hello"
    exact = "x" * 100
    assert _ellipsize(exact, 100) == exact


def test_ellipsize_cuts_at_word_boundary():
    from app.services.briefing import _ellipsize

    text = ("word " * 40).strip()
    out = _ellipsize(text, 100)
    assert out.endswith("…") and len(out) <= 100
    assert set(out[:-1].split(" ")) == {"word"}  # never a partial word


def test_ellipsize_strips_dangling_separators():
    from app.services.briefing import _ellipsize

    text = "x" * 95 + " — trailing tail"
    assert _ellipsize(text, 100) == "x" * 95 + "…"


def test_ellipsize_single_long_word_hard_cuts():
    from app.services.briefing import _ellipsize

    assert _ellipsize("y" * 150, 100) == "y" * 99 + "…"


# ---- _coalesce --------------------------------------------------------------------


def test_coalesce_stacks_near_duplicates():
    from app.services.briefing import _coalesce

    n1 = {"id": 3, "message": "claude ingested notes: standup", "link": "/ingest"}
    n2 = {"id": 2, "message": "claude ingested notes: retro", "link": "/ingest"}
    n3 = {"id": 1, "message": "blocker resolved: CI", "link": "/dashboard"}
    assert _coalesce([n1, n2, n3]) == [(n1, 1), (n3, 0)]


def test_coalesce_same_prefix_different_link_stays_separate():
    from app.services.briefing import _coalesce

    a = {"id": 2, "message": "ingested: a", "link": "/x"}
    b = {"id": 1, "message": "ingested: b", "link": "/y"}
    assert _coalesce([a, b]) == [(a, 0), (b, 0)]


def _notice_items(client):
    b = client.get("/api/briefing").json()
    return [a for a in b["attention"] if a["kind"] == "notification"]


def test_briefing_coalesces_and_resurfaces_on_dismiss(client):
    from app.services import notifications

    for suffix in ("standup", "retro", "planning"):
        notifications.notify("tester", f"agent ingested notes: {suffix}", link="/ingest")

    items = _notice_items(client)
    assert len(items) == 1
    assert items[0]["label"].endswith("(+2 similar)")
    assert items[0]["reason"] == "for you — dismiss when read"

    client.post("/api/notifications/read", json={"notification_id": items[0]["ref_id"]})
    items = _notice_items(client)
    assert len(items) == 1
    assert items[0]["label"].endswith("(+1 similar)")


def test_briefing_notice_cap_applies_after_coalescing(client):
    from app.services import notifications

    for i in range(6):
        notifications.notify("tester", f"distinct thing {i} happened", link=f"/l{i}")
    for suffix in ("a", "b", "c"):
        notifications.notify("tester", f"agent ingested notes: {suffix}", link="/ingest")

    items = _notice_items(client)
    assert len(items) == 5
    assert items[0]["label"].endswith("(+2 similar)")  # dupes consume one slot, not three
    assert sum("distinct thing" in i["label"] for i in items) == 4


def test_briefing_team_notification_reason_label(client):
    from app.services import notifications

    notifications.notify("team", "all hands moved to Friday", link="/")
    items = _notice_items(client)
    assert items[0]["reason"] == "for the whole team — dismiss when read"


# ---- _human_digest ----------------------------------------------------------------


def test_human_digest_scrubs_uuids_and_coalesces_housekeeping():
    from app.services.briefing import _human_digest

    uuid = "123e4567-e89b-12d3-a456-426614174000"
    rows = [
        {
            "id": 1,
            "actor": "ava",
            "action": "create_task",
            "detail": f"s {uuid} ok",
            "created_at": "t",
        },
        {"id": 2, "actor": "ava", "action": "delete_chat", "detail": uuid, "created_at": "t"},
        {"id": 3, "actor": "ava", "action": "rename_chat", "detail": "x", "created_at": "t"},
        {"id": 4, "actor": "bo", "action": "move_chat", "detail": "y", "created_at": "t"},
    ]
    out = _human_digest(rows)
    assert out[0]["detail"] == "s … ok"
    assert not any(uuid in str(r["detail"]) for r in out)
    assert all(r["action"] not in ("delete_chat", "rename_chat", "move_chat") for r in out)
    tidy = {r["actor"]: r for r in out if r["action"] == "tidied"}
    assert tidy["ava"]["detail"] == "2 chats" and tidy["ava"]["id"] == "tidy-ava"
    assert tidy["bo"]["detail"] == "1 chat"


def test_human_digest_caps_at_twenty_rows():
    from app.services.briefing import _human_digest

    rows = [
        {"id": i, "actor": "ava", "action": "capture", "detail": f"n{i}", "created_at": "t"}
        for i in range(30)
    ]
    assert len(_human_digest(rows)) == 20


# ---- _standup_suggestion ----------------------------------------------------------


def test_standup_suggestion_derives_from_activity(fresh_db):
    from app.services import briefing

    uuid = "123e4567-e89b-12d3-a456-426614174000"
    fresh_db.log_activity("dana", "create_task", "#12 ship the API")
    fresh_db.log_activity("dana", "capture", f"note {uuid}")
    fresh_db.log_activity("dana", "delete_chat", "old thread")  # housekeeping: excluded
    fresh_db.log_activity("dana", "request_key", "asked for a personal API key")  # excluded

    s = briefing.my_day("dana")["your_work"]["standup_suggestion"]
    assert s == "capture note …; create task #12 ship the API"


def test_standup_suggestion_empty_without_activity(fresh_db):
    from app.services import briefing

    assert briefing.my_day("ghost")["your_work"]["standup_suggestion"] == ""


def test_standup_suggestion_caps_at_three_items(fresh_db):
    from app.services import briefing

    for i in range(5):
        fresh_db.log_activity("dana", "capture", f"item {i}")
    s = briefing.my_day("dana")["your_work"]["standup_suggestion"]
    assert s.count(";") == 2
    assert s.startswith("capture item 4")


# ---- zero-stat ship-it recap ------------------------------------------------------


def test_ship_it_recap_omits_zero_stats(fresh_db, monkeypatch):
    from app.services import engagements, notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    e = engagements.create_engagement("Bare", actor="ava")
    engagements.update_engagement(e["id"], status="closed", conclusion="achieved", actor="ava")

    note = fresh_db.query_one("SELECT content FROM notes WHERE topic = 'shipped-Bare'")
    assert "Shipped: Bare" in note["content"]
    for phrase in ("0 milestones", "0 tasks done", "0 blockers survived"):
        assert phrase not in note["content"]
    assert "·" not in note["content"]  # no orphaned separators when every stat is zero
    msg = fresh_db.query_one(
        "SELECT message FROM notifications WHERE user = 'team' AND message LIKE '%Shipped: Bare%'"
    )
    assert "·" not in msg["message"]


def test_ship_it_recap_omits_zero_day_duration(fresh_db, monkeypatch):
    from app.services import engagements, notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    e = engagements.create_engagement("SameDay", actor="ava")
    engagements.update_engagement(e["id"], status="closed", conclusion="achieved", actor="ava")
    note = fresh_db.query_one("SELECT content FROM notes WHERE topic = 'shipped-SameDay'")
    assert "0 days" not in note["content"]


def test_ship_it_recap_single_stat_no_orphan_separators(client, fresh_db, monkeypatch):
    from app.services import notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    e = client.post("/api/engagements", json={"name": "OneStat"}).json()
    client.post("/api/milestones", json={"title": "m1", "project": "OneStat"})
    client.patch(f"/api/engagements/{e['id']}", json={"status": "closed", "conclusion": "achieved"})

    content = fresh_db.query_one("SELECT content FROM notes WHERE topic = 'shipped-OneStat'")[
        "content"
    ]
    assert "1 milestones" in content
    assert "tasks done" not in content and "blockers survived" not in content
    assert content.count("·") == 1 and not content.rstrip().endswith("·")


# ---- onboarding scopes ------------------------------------------------------------


def test_onboarding_scopes_personal_steps_first(client):
    steps = client.get("/api/onboarding").json()["steps"]
    scopes = [s["scope"] for s in steps]
    assert scopes == ["you"] * 4 + ["team"] * 2
    assert steps[0]["id"] == "pick_name"
    assert {s["id"] for s in steps if s["scope"] == "team"} == {"first_engagement", "invite_team"}


# ---- key self-request edges -------------------------------------------------------


def test_key_request_rejects_anonymous(client):
    r = client.post("/api/keys/request", headers={"X-User": ""})
    assert r.status_code == 400
    assert "pick your name" in r.json()["detail"]


def test_key_request_refiles_after_notification_read(client):
    assert client.post("/api/keys/request").json()["already_pending"] is False
    assert client.post("/api/keys/request").json()["already_pending"] is True
    client.post("/api/notifications/read", json={"notification_id": 0})  # dismiss all
    assert client.post("/api/keys/request").json()["already_pending"] is False


# ---- user theme persistence -------------------------------------------------------


def test_theme_is_per_user(client):
    blob_a = '{"pack":"ledger","colorway":"madder"}'
    blob_b = '{"pack":"phosphor","colorway":"verdigris"}'
    client.post("/api/users/theme", json={"theme": blob_a})
    client.post("/api/users/theme", json={"theme": blob_b}, headers={"X-User": "other"})
    assert client.get("/api/users/theme").json()["theme"] == blob_a
    assert client.get("/api/users/theme", headers={"X-User": "other"}).json()["theme"] == blob_b


def test_theme_unknown_user_gets_empty(client):
    assert client.get("/api/users/theme", headers={"X-User": "never-seen"}).json()["theme"] == ""


def test_theme_service_rejects_oversize_and_non_object(fresh_db):
    from app.services import users

    with pytest.raises(ValueError, match="too large"):
        users.set_theme("tester", "{" + '"pack":"' + "x" * 400 + '"}')
    with pytest.raises(ValueError, match="unknown keys"):
        users.set_theme("tester", '["pack"]')  # JSON but not an object


def test_theme_survives_rename_but_merge_keeps_target(fresh_db):
    from app.services import users

    users.set_theme("Mira", '{"pack":"atelier"}')
    users.rename_user("Mira", "Mira K")
    assert users.get_theme("Mira K") == '{"pack":"atelier"}'
    # merge: the target row's theme wins; the source row (and its theme) is deleted
    users.ensure_user("mira")
    users.set_theme("mira", '{"pack":"ledger"}')
    out = users.rename_user("Mira K", "mira")
    assert out["merged"] is True
    assert users.get_theme("mira") == '{"pack":"ledger"}'  # atelier is gone (documented loss)
    assert users.get_theme("Mira K") == ""


# ---- correction contract ----------------------------------------------------------


def test_note_edit_delete_and_deindex(client):
    n = client.post("/api/notes", json={"topic": "conv", "content": "old text zebra"}).json()
    client.patch(f"/api/notes/{n['id']}", json={"content": "new text giraffe"})
    assert client.get("/api/search", params={"q": "giraffe"}).json()
    client.delete(f"/api/notes/{n['id']}")
    assert client.get("/api/search", params={"q": "giraffe"}).json() == []
    assert client.delete(f"/api/notes/{n['id']}").status_code == 404


def test_blocker_and_commitment_edits_guard_history(client):
    from app.services import blockers, commitments

    b = blockers.raise_blocker(title="typo'd", owner="ava", actor="ava")
    assert blockers.edit_blocker(b["id"], title="fixed title", actor="ava")["updated"] == ["title"]
    blockers.resolve_blocker(b["id"], actor="ava")
    try:
        blockers.edit_blocker(b["id"], title="nope", actor="ava")
        raise AssertionError("resolved blocker was editable")
    except ValueError:
        pass

    c = commitments.add_commitment("shipp the thing", actor="ava")
    commitments.edit_commitment(c["id"], promise="ship the thing", actor="ava")
    commitments.update_commitment(c["id"], "kept", actor="ava")
    try:
        commitments.edit_commitment(c["id"], promise="rewrite history", actor="ava")
        raise AssertionError("settled commitment was editable")
    except ValueError:
        pass


def test_intake_edit_only_before_disposition(client):
    from app.services import intake

    r = intake.submit_request("typo titel", actor="ava")
    assert intake.edit_request(r["id"], title="typo title fixed", actor="ava")
    intake.score_request(r["id"], 3, 3, 3, 3, actor="ava")
    intake.edit_request(r["id"], detail="still editable while scored", actor="ava")
    intake.disposition_request(r["id"], "declined", reason="no", actor="ava")
    try:
        intake.edit_request(r["id"], title="after the fact", actor="ava")
        raise AssertionError("dispositioned request was editable")
    except ValueError:
        pass
