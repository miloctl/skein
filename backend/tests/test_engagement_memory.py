"""A conversation's outcome, filed as the engagement's memory.

A memory is injected into every future conversation's system prompt, which
makes it the highest-leverage write in the app. Until it carried an engagement,
a fact learned on one piece of work was recalled into every conversation about
every other one — so the useful ones were diluted by the irrelevant ones and
nobody could tell which was which.
"""

import pytest

from app.services import memory, scope


def test_an_engagements_memory_reaches_its_own_chat_and_no_other(client, fresh_db):
    from app.services import engagements, users

    users.ensure_user("ava")
    atlas = engagements.create_engagement("Atlas", project_class="migration", actor="ava")
    orion = engagements.create_engagement("Orion", project_class="prototype", actor="ava")

    memory.remember("the vendor needs 10 days notice", engagement_id=atlas["id"], actor="ava")
    memory.remember("standups are asynchronous here", actor="ava")  # the team's own

    viewer = scope.Viewer("ava", True)
    in_atlas = [m["content"] for m in memory.recall(viewer=viewer, engagement_id=atlas["id"])]
    in_orion = [m["content"] for m in memory.recall(viewer=viewer, engagement_id=orion["id"])]
    unlinked = [m["content"] for m in memory.recall(viewer=viewer)]

    # the team's memory holds everywhere, which is why it is not replaced
    assert "standups are asynchronous here" in in_atlas
    assert "standups are asynchronous here" in in_orion
    assert "standups are asynchronous here" in unlinked
    # the engagement's own reaches its own conversation and nowhere else
    assert "the vendor needs 10 days notice" in in_atlas
    assert "the vendor needs 10 days notice" not in in_orion
    assert "the vendor needs 10 days notice" not in unlinked


def test_a_memory_cannot_be_filed_against_an_engagement_you_cannot_read(client, fresh_db):
    from app.services import crews, engagements, users

    for n in ("insider", "outsider"):
        users.ensure_user(n)
    crew = crews.create_crew("ops", actor="insider")
    eng = engagements.create_engagement(
        "Secret",
        project_class="migration",
        actor="insider",
        visibility=scope.CREW,
        crew_id=crew["id"],
    )
    with pytest.raises(ValueError, match=r"engagement"):
        memory.remember("leaked", engagement_id=eng["id"], actor="outsider")


def test_filing_an_outcome_is_always_a_proposal(client, fresh_db):
    """Never a direct write, whatever SKEIN_AGENT_REVIEW says: the text comes
    out of a chat, where a model's summary and a person's own words are hard to
    tell apart, and it steers every future conversation about this work."""
    from conftest import _strong

    from app.services import engagements, users

    users.ensure_user("tester")
    eng = engagements.create_engagement("Atlas", project_class="migration", actor="tester")
    r = client.post(
        f"/api/engagements/{eng['id']}/memory",
        json={"content": "the cutover needs a read replica", "topic": "cutover"},
        headers=_strong(client, "tester"),
    )
    assert r.status_code == 200 and r.json()["status"] == "pending"
    # nothing is recalled until a human approves it
    assert not memory.recall(viewer=scope.Viewer("tester", True), engagement_id=eng["id"])


def test_an_approved_outcome_carries_its_source(client, fresh_db):
    from conftest import _strong

    from app import db
    from app.services import engagements, review, users

    users.ensure_user("tester")
    eng = engagements.create_engagement("Atlas", project_class="migration", actor="tester")
    p = client.post(
        f"/api/engagements/{eng['id']}/memory",
        json={"content": "read replica first", "thread_id": "chat-77"},
        headers=_strong(client, "tester"),
    ).json()
    review.approve_change(p["id"], actor="tester", strong=True)

    row = db.query_one("SELECT * FROM memories ORDER BY id DESC LIMIT 1")
    assert row["engagement_id"] == eng["id"]
    assert row["source_kind"] == "chat" and row["source_id"] == "chat-77"
    assert row["origin"] == "agent_verified"
