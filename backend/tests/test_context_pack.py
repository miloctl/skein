"""The versioned context pack: it versions only on change, and scopes to an engagement."""

import threading
from pathlib import Path

import pytest


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


def test_context_pack_file_failure_rolls_back_the_version(fresh_db, monkeypatch):
    from app.services import context_pack

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(context_pack.artifact_files, "publish", fail)
    with pytest.raises(OSError, match="disk full"):
        context_pack.publish_pack(actor="mira")
    assert fresh_db.query_one("SELECT id FROM context_packs") is None


def test_context_pack_repairs_a_missing_archive(fresh_db):
    from app.services import context_pack

    first = context_pack.publish_pack(actor="mira")
    path = Path(first["path"])
    path.unlink()
    repeated = context_pack.publish_pack(actor="mira")
    assert repeated["changed"] is False
    assert Path(repeated["path"]).is_file()
    assert "# Team context pack" in Path(repeated["path"]).read_text(encoding="utf-8")


def test_concurrent_archive_repairs_recheck_after_the_lock(fresh_db, monkeypatch):
    from app.services import context_pack

    first = context_pack.publish_pack(actor="mira")
    path = Path(first["path"])
    expected = path.read_bytes()
    path.unlink()
    barrier = threading.Barrier(2)
    acquired = 0
    guard = threading.Lock()
    local = threading.local()
    original_lock = context_pack.db.name_lock
    original_log = context_pack.db.log_activity

    def synchronize(namespace, name):
        nonlocal acquired
        barrier.wait(timeout=3)  # both observed the missing file before locking
        original_lock(namespace, name)
        with guard:
            acquired += 1
            local.second = acquired == 2

    def fail_if_second(*args, **kwargs):
        if getattr(local, "second", False):
            raise RuntimeError("second repair rolls back")
        return original_log(*args, **kwargs)

    monkeypatch.setattr(context_pack.db, "name_lock", synchronize)
    monkeypatch.setattr(context_pack.db, "log_activity", fail_if_second)
    errors = []

    def repair():
        try:
            context_pack.publish_pack(actor="mira")
        except Exception as exc:
            errors.append(exc)

    workers = [threading.Thread(target=repair) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(4)
    assert errors == []
    assert path.read_bytes() == expected


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
