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
    # the three states recall distinguishes, each named
    in_atlas = [m["content"] for m in memory.recall(viewer=viewer, engagement_id=atlas["id"])]
    in_orion = [m["content"] for m in memory.recall(viewer=viewer, engagement_id=orion["id"])]
    unlinked = [m["content"] for m in memory.recall(viewer=viewer, engagement_id=0)]
    browsing = [m["content"] for m in memory.recall(viewer=viewer, engagement_id=None)]

    # the team's memory holds everywhere, which is why it is not replaced
    assert "standups are asynchronous here" in in_atlas
    assert "standups are asynchronous here" in in_orion
    assert "standups are asynchronous here" in unlinked
    # the engagement's own reaches its own conversation and nowhere else
    assert "the vendor needs 10 days notice" in in_atlas
    assert "the vendor needs 10 days notice" not in in_orion
    assert "the vendor needs 10 days notice" not in unlinked
    # ...but BROWSE sees every memory this reader may read, or an approved
    # engagement memory would steer conversations from a row no human could
    # reach: the memories page is the only surface that lists or deletes one
    assert "the vendor needs 10 days notice" in browsing
    assert "standups are asynchronous here" in browsing


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


def test_a_crew_engagements_memory_keeps_the_crew_tier(client, fresh_db):
    """A parent-to-child crossing: the memory takes the ENGAGEMENT's tier, not
    the workspace default. Without it the proposal is readable and approvable
    by everyone, and the approved row is indexed for search team-wide — a crew
    engagement's memory reaching the whole roster twice over."""
    from app import db
    from app.services import crews, engagements, review, users

    for n in ("insider", "outsider"):
        users.ensure_user(n)
    crew = crews.create_crew("ops", actor="insider")
    eng = engagements.create_engagement(
        "Nightshade",
        project_class="migration",
        actor="insider",
        visibility=scope.CREW,
        crew_id=crew["id"],
    )
    p = memory.propose_engagement_memory(
        eng["id"],
        "the client will not renew",
        actor="insider",
        viewer=scope.Viewer("insider", True),
    )
    # the proposal itself does not list for somebody outside the crew
    outside = [c["id"] for c in review.list_changes("pending", scope.Viewer("outsider", True))]
    assert p["id"] not in outside

    # the viewer is required: a crew proposal is judgeable only by somebody who
    # can read its target, and that is resolved from the Viewer, not the name
    review.approve_change(
        p["id"], actor="insider", strong=True, viewer=scope.Viewer("insider", True)
    )
    row = db.query_one("SELECT * FROM memories ORDER BY id DESC LIMIT 1")
    assert row["visibility"] == scope.CREW and row["crew_id"] == crew["id"]
    # and it is not recalled by anyone outside the crew
    assert not memory.recall(viewer=scope.Viewer("outsider", True), engagement_id=eng["id"])

    # ...but it MUST reach the prompt of somebody inside it. This is the whole
    # point of filing one, and the tier that protects it from the roster also
    # hides it from the default NOBODY viewer — so an injection path that does
    # not carry a viewer produces a row that steers no conversation at all.
    prompt = memory.memory_prompt(
        "insider", engagement_id=eng["id"], viewer=scope.Viewer("insider", True)
    )
    assert "the client will not renew" in prompt
    assert "the client will not renew" not in memory.memory_prompt(
        "outsider", engagement_id=eng["id"], viewer=scope.Viewer("outsider", True)
    )


def test_a_crew_memory_proposal_is_not_announced_to_the_roster(client, fresh_db):
    """`propose_change` posts its summary to the 'team' notification feed,
    which carries no tier and reaches everybody. Without the gate a crew
    engagement's NAME and eighty characters of its memory left that way."""
    from app.services import crews, engagements, notifications, users

    for n in ("insider", "outsider"):
        users.ensure_user(n)
    crew = crews.create_crew("ops", actor="insider")
    eng = engagements.create_engagement(
        "Nightshade",
        project_class="migration",
        actor="insider",
        visibility=scope.CREW,
        crew_id=crew["id"],
    )
    memory.propose_engagement_memory(
        eng["id"],
        "the client will not renew",
        actor="insider",
        viewer=scope.Viewer("insider", True),
    )
    for note in notifications.list_notifications("outsider"):
        assert "Nightshade" not in note["message"]
        assert "will not renew" not in note["message"]


def test_a_private_engagement_refuses_a_memory_nobody_could_review(client, fresh_db):
    from app.services import engagements, users

    users.ensure_user("ava")
    eng = engagements.create_engagement(
        "Solo", project_class="prototype", actor="ava", visibility=scope.PRIVATE
    )
    with pytest.raises(ValueError, match="no second person can review"):
        memory.propose_engagement_memory(
            eng["id"], "a thought", actor="ava", viewer=scope.Viewer("ava", True)
        )
