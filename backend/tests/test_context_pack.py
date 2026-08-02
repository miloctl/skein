"""The versioned context pack: it versions only on change, and scopes to an engagement."""


def test_context_pack_versions_only_on_change(client):
    client.post("/api/decisions", json={"title": "Ship weekly", "decision": "always"})
    pack = client.get("/api/context-pack").json()
    assert pack["version"] == 1
    assert "Ship weekly" in pack["content"]

    again = client.post("/api/context-pack/publish").json()
    assert again["changed"] is False and again["version"] == 1

    client.post(
        "/api/notes", json={"topic": "convention: PR size", "content": "keep diffs under 400 lines"}
    )
    bumped = client.post("/api/context-pack/publish").json()
    assert bumped["changed"] is True and bumped["version"] == 2
    pack = client.get("/api/context-pack").json()
    assert "PR size" in pack["content"]


def test_engagement_pack_scoped(client, fresh_db):
    from app.services import blockers, engagements, work

    engagements.create_engagement(
        "Retrieval spike",
        kind="experiment",
        timebox_end="2026-08-15",
        kill_criteria="no lift in 2 weeks",
        outcome="median lookup under 8 min",
        actor="m",
    )
    engagements.create_engagement("Other project", actor="m")
    m = work.create_milestone(title="Eval baseline", project="Retrieval spike", actor="m")
    t = work.create_task(title="build harness", milestone_id=m["id"], actor="m")
    b = blockers.raise_blocker("gpu quota", actor="m")
    work.update_task(t["id"], waiting_on=f"blocker:{b['id']}", actor="m")
    work.create_milestone(title="Unrelated milestone", project="Other project", actor="m")

    pack = client.get("/api/context-pack?engagement=1").json()["content"]
    assert "Retrieval spike" in pack
    assert "median lookup under 8 min" in pack
    assert "kill criteria" in pack.lower() or "Kill criteria" in pack
    assert "build harness" in pack and "waiting on blocker" in pack
    assert "Unrelated milestone" not in pack  # scoped: other engagements stay out
    assert client.get("/api/context-pack?engagement=999").status_code == 404
    # the global pack still works and is versioned
    assert "version" in client.get("/api/context-pack").json()


def test_engagement_pack_reachable_by_agents(fresh_db):
    from app.services.engagements import create_engagement
    from app.tools.portfolio import get_context_pack

    create_engagement("Agent scoped", actor="m")
    import json as j

    out = j.loads(get_context_pack(engagement_id=1))
    assert "Agent scoped" in out["content"]
