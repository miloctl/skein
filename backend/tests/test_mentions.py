"""@mentions: who gets notified, who never does, and the no-double-ping rules."""

import pytest


@pytest.fixture(autouse=True)
def _no_slack(monkeypatch):
    from app.services import notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)


@pytest.fixture()
def roster(fresh_db):
    from app.services import users

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    return fresh_db


def _unread(user):
    from app.services import notifications

    return [n["message"] for n in notifications.list_notifications(user)]


def test_mention_in_task_description_notifies(roster):
    from app.services import work

    tid = work.create_task("Fix login", description="@mira knows the flow", actor="dana")["id"]
    assert any(f"task #{tid}" in m and "dana mentioned you" in m for m in _unread("mira"))


def test_mention_is_case_insensitive(roster):
    from app.services import work

    work.create_task("Fix login", description="ping @MIRA about this", actor="dana")
    assert _unread("mira")


def test_self_mention_is_silent(roster):
    from app.services import work

    work.create_task("Fix login", description="note to self @mira", actor="mira")
    assert _unread("mira") == []


def test_unknown_name_is_silent(roster):
    from app.services import notifications, work

    work.create_task("Fix login", description="@nobody-here look", actor="mira")
    assert notifications.list_notifications("nobody-here") == []


def test_edit_does_not_notify_twice(roster):
    from app.services import notifications, work

    tid = work.create_task("Fix login", description="@mira look", actor="dana")["id"]
    notifications.mark_read("mira")
    work.update_task(tid, description="@mira please look at this", actor="dana")
    assert _unread("mira") == []


def test_edit_notifies_only_the_newly_added_person(roster):
    from app.services import users, work

    users.ensure_user("dana")
    tid = work.create_task("Fix login", description="@mira look", actor="casey")["id"]
    work.update_task(tid, description="@mira and @dana look", actor="casey")
    assert len(_unread("mira")) == 1
    assert len(_unread("dana")) == 1


def test_question_assignee_is_not_double_pinged(roster):
    from app.services import collab

    qid = collab.ask_question("@mira what broke?", asked_by="dana", assigned_to="mira")["id"]
    msgs = _unread("mira")
    assert any(f"Question #{qid} assigned to you" in m for m in msgs)
    assert not any("mentioned you" in m for m in msgs)


def test_mention_in_answer_note_and_decision(roster):
    from app.services import collab, users

    users.ensure_user("dana")
    qid = collab.ask_question("what broke?", asked_by="dana")["id"]
    collab.answer_question(qid, "@mira fixed it once before", answered_by="dana")
    collab.save_note("postmortem", "ask @mira for the timeline", actor="dana")
    collab.record_decision("Rotate keys", "@mira owns the rotation", decided_by="dana")
    msgs = _unread("mira")
    assert sum("mentioned you" in m for m in msgs) == 3


def test_agent_mention_lands_in_its_inbox(roster):
    from app.services import work

    work.create_task("Sweep logs", description="@scout take this one", actor="mira")
    assert any("mentioned you" in m for m in _unread("scout"))


def test_sentence_final_punctuation_still_mentions(roster):
    from app.services import work

    work.create_task("Fix login", description="thanks @mira.", actor="dana")
    work.create_task("Fix logout", description="see @mira... then ship", actor="dana")
    assert len(_unread("mira")) == 2


def test_email_or_ssh_target_is_not_a_mention(roster):
    from app.services import work

    work.create_task("Rotate keys", description="run ssh root@scout tonight", actor="mira")
    assert _unread("scout") == []


def test_title_only_capture_shape_mentions(roster):
    from app.services import work

    # the short `todo: ask @mira ...` ⌘K capture lands entirely in the title
    tid = work.create_task("ask @mira about the rollout", actor="dana")["id"]
    assert any(f"task #{tid}" in m for m in _unread("mira"))


def test_question_creation_mentions_a_non_assignee(roster):
    from app.services import collab

    qid = collab.ask_question("@mira what broke?", asked_by="dana")["id"]
    assert any(f"question #{qid}" in m for m in _unread("mira"))


def test_note_edit_mentions_the_newly_added_person(roster):
    from app.services import collab

    nid = collab.save_note("postmortem", "draft", actor="dana")["id"]
    collab.update_note(nid, content="ask @mira for the timeline", actor="dana")
    assert any(f"note #{nid}" in m for m in _unread("mira"))


def test_answer_mentioning_the_asker_does_not_double_ping(roster):
    from app.services import collab, users

    users.ensure_user("dana")
    qid = collab.ask_question("what broke?", asked_by="dana")["id"]
    collab.answer_question(qid, "@dana it was the cache", answered_by="mira")
    msgs = _unread("dana")
    assert any("was answered" in m for m in msgs)
    assert not any("mentioned you" in m for m in msgs)


def test_retention_prunes_only_orphaned_mentions(roster):
    from app.services import collab, retention

    keep = collab.save_note("keep", "ping @mira", actor="dana")["id"]
    drop = collab.save_note("drop", "ping @scout", actor="dana")["id"]
    collab.delete_note(drop, actor="dana")
    removed = retention.prune()
    assert removed["mention_log"] == 1
    from app import db

    rows = db.query("SELECT entity_id FROM mention_log WHERE entity = 'note'")
    assert [r["entity_id"] for r in rows] == [keep]
