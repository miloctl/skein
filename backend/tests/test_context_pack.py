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


def test_a_filtered_projection_never_reuses_the_stored_version(fresh_db):
    """Versions bump only when content changes. The filtered read rebuilt a
    different body and returned it under the stored version and created_at,
    so one version identified two bodies and a version-keyed cache kept the
    wrong one."""
    from app.services import context_pack, engagements, scope

    engagements.create_engagement("Visible project", actor="mira")
    engagements.create_engagement("Filtered project", actor="mira")
    viewer = scope.Viewer("mira", True)
    context_pack.publish_pack(actor="mira", viewer=viewer)

    unfiltered = context_pack.get_pack(actor="mira", viewer=viewer, resource_filter=lambda *_: True)
    filtered = context_pack.get_pack(
        actor="mira",
        viewer=viewer,
        resource_filter=lambda entity, entity_id, _attributes: (
            not (entity == "engagement" and entity_id == 2)
        ),
    )
    assert unfiltered["version"] == 1
    assert filtered["version"] == 0
    assert filtered["hash"] != unfiltered["hash"]
    assert "Filtered project" not in filtered["content"]
    assert "policy-filtered" in filtered["content"]


def test_a_filtered_reader_never_receives_the_stored_snapshot(fresh_db):
    """A denied row that has since LEFT the live build (superseded, closed,
    pushed past a section LIMIT) is still inside the stored snapshot. The
    live-build comparison saw no difference and handed that snapshot to the
    reader whose policy denies the row."""
    from app.services import collab, context_pack, scope

    secret = collab.record_decision("Buy NorthCo", "for 40M", decided_by="mira", actor="mira")
    viewer = scope.Viewer("mira", True)
    context_pack.publish_pack(actor="mira", viewer=viewer)
    collab.supersede_decision(
        secret["id"], "Course change", "Do not buy NorthCo", decided_by="mira", actor="mira"
    )

    def deny_secret(entity, entity_id, _attributes):
        return not (entity == "decision" and entity_id == secret["id"])

    filtered = context_pack.get_pack(actor="mira", viewer=viewer, resource_filter=deny_secret)
    assert "Buy NorthCo" not in filtered["content"]
    assert filtered["version"] == 0
