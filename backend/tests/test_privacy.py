"""Canary tests for the private-record boundary.

A canary string is written into private notes; every egress surface is then
asserted canary-free. If any of these fail, private data is leaking."""

import json
import shutil
from pathlib import Path

CANARY = "CANARY-zx9q-private-feedback"


def _pg_restore() -> str:
    """Absolute path, so the argv carries no bare executable name."""
    found = shutil.which("pg_restore")
    assert found, "pg_restore must be installed to drill a restore"
    return found


def _setup_key(client, fresh_db):
    from app.services.api_keys import create_key

    key = create_key("manager", "test")["key"]
    return {"Authorization": f"Bearer {key}"}


def _write_private(client, fresh_db):
    headers = _setup_key(client, fresh_db)
    r = client.post(
        "/api/private/notes",
        json={"person": "dana", "body": f"{CANARY} handled the outage well", "kind": "feedback"},
        headers=headers,
    )
    assert r.status_code == 200
    return headers


def test_private_requires_strong_identity(client, fresh_db):
    r = client.get("/api/private/notes", headers={"X-User": "sneaky"})
    assert r.status_code == 403
    r = client.post(
        "/api/private/notes",
        json={"person": "dana", "body": "spoofed"},
        headers={"X-User": "manager"},
    )
    assert r.status_code == 403


def test_private_notes_are_author_scoped(client, fresh_db):
    _write_private(client, fresh_db)
    from app.services.api_keys import create_key

    other = create_key("other-person", "test")["key"]
    r = client.get("/api/private/notes", headers={"Authorization": f"Bearer {other}"})
    assert r.status_code == 200
    assert r.json() == []


def test_fb_capture_routes_private_and_requires_key(client, fresh_db):
    headers = _write_private(client, fresh_db)
    # without a key: refused, nothing stored anywhere
    r = client.post("/api/capture", json={"text": f"fb: dana — {CANARY}"}, headers={"X-User": "m"})
    assert r.status_code == 400
    assert "API key" in r.json()["detail"]
    # with a key: lands as private feedback, not a note
    r = client.post("/api/capture", json={"text": f"fb: dana — {CANARY} two"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["kind"] == "feedback"
    assert fresh_db.query("SELECT * FROM notes") == []


def test_fb_capture_refuses_agents(fresh_db):
    import pytest

    from app.services.capture import capture

    with pytest.raises(ValueError, match="human-only"):
        capture("fb: dana — sneaky agent note", actor="agent-x", origin="agent", strong_auth=True)


def _spray_canary(client, headers):
    """Every write path an fb: line could plausibly transit."""
    client.post("/api/capture", json={"text": f"fb: dana — {CANARY} extra"}, headers=headers)
    # multi-line capture containing an fb: line must be REFUSED whole
    r = client.post(
        "/api/capture",
        json={"text": f"todo: ship the thing\nfb: dana — {CANARY} sneaky"},
        headers=headers,
    )
    assert r.status_code == 400 and "alone" in r.json()["detail"]
    # ingest skips fb: lines
    ing = client.post(
        "/api/ingest",
        json={"text": f"todo: real work\nfb: dana — {CANARY} in transcript"},
        headers={"X-User": "m"},
    ).json()
    assert ing["skipped_private"] == 1
    # chat refuses fb: before the agent ever sees it
    with client.stream(
        "POST", "/api/chat", json={"thread_id": "t", "message": f"fb: dana — {CANARY} via chat"}
    ) as resp:
        chat_out = resp.read().decode()
    assert "private" in chat_out and CANARY not in json.dumps(
        [r["title"] for r in client.get("/api/tasks").json()]
    )
    # a slash prefix must not smuggle fb: past the gate — the transcript and
    # the session bridge are both downstream of it
    with client.stream(
        "POST",
        "/api/chat",
        json={"thread_id": "t", "message": f"/remember fb: dana — {CANARY} wrapped"},
    ) as resp:
        wrapped_out = resp.read().decode()
    assert "private" in wrapped_out


def test_canary_absent_from_every_platform_table(client, fresh_db):
    """Exhaustive: scan EVERY table in platform.db, not an enumerated list —
    a leak into any new table fails this without anyone remembering to add it."""
    headers = _write_private(client, fresh_db)
    _spray_canary(client, headers)
    for t in fresh_db.query(
        "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    ):
        rows = fresh_db.query(f"SELECT * FROM {t['name']}")  # noqa: S608 — names from the catalog
        assert CANARY not in json.dumps(rows, default=str), f"canary leaked into {t['name']}"


def test_canary_absent_from_every_disk_file(client, fresh_db):
    """Exhaustive: after exercising every artifact-producing surface, no file
    under DATA_DIR except private.db itself may contain the canary."""
    from app import config
    from app.services import admin
    from app.services.digest import publish_digest

    headers = _write_private(client, fresh_db)
    _spray_canary(client, headers)
    client.post("/api/context-pack/publish", json={})
    publish_digest(actor="tester", force=True)
    admin.backup()
    admin.export()
    # the feed must render, or the canary check below passes vacuously
    assert "BEGIN:VCALENDAR" in client.get("/api/calendar.ics").text
    assert CANARY not in client.get("/api/calendar.ics").text
    # /ask reads the same FTS: the echo of the question is fine, citations must be empty
    assert client.get(f"/api/ask?q={CANARY}").json()["citations"] == []
    # the on-demand engagement pack is the one context surface never written
    # to disk — scan its output directly
    client.post("/api/engagements", json={"name": "Canary Pack Probe"})
    eng = client.get("/api/engagements").json()[0]
    assert CANARY not in client.get(f"/api/context-pack?engagement={eng['id']}").text
    for f in Path(config.DATA_DIR).rglob("*"):
        # private.db and its dated backups are the canary's only legal homes;
        # everything else under DATA_DIR — platform backups, exports,
        # artifacts, the ICS cache — must stay clean
        if f.is_file() and "private" not in f.name:
            assert CANARY.encode() not in f.read_bytes(), f"canary leaked into {f}"


def test_private_db_backed_up_but_never_mirrored(client, fresh_db, tmp_path, monkeypatch):
    """Both halves of the private-data durability rule. The backup exists:
    losing the disk must not lose the 1:1 notes, and the local copy adds no
    reader — whoever runs the server can read private.db itself. The off-box
    mirror never carries it: a mirror copy leaves the box, which is the
    exposure the exclusion from exports exists to prevent."""
    _write_private(client, fresh_db)
    from app import config
    from app.services import admin

    assert not any("private" in t for t in admin.TABLES)
    tables = [
        r["name"]
        for r in fresh_db.query(
            "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
    ]
    assert not any("private" in t for t in tables)

    # production-shaped data dir, so the mirror actually runs (see the
    # mirror-guard test above)
    mirror = tmp_path / "offbox"
    monkeypatch.setenv("SKEIN_BACKUP_MIRROR", str(mirror))
    prod_data = tmp_path / "data"
    prod_data.mkdir()
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", prod_data)

    out = admin.backup()
    assert out["private_path"] and Path(out["private_path"]).exists()
    assert out["mirrored"] is not None  # the mirror ran for platform
    assert not any(f.name.startswith("private-") for f in mirror.glob("*")), (
        "a private backup reached the off-box mirror"
    )


def test_no_agent_or_review_surface_over_private_entities(fresh_db):
    from app.services.review import _registry
    from app.tools import ALL_TOOLS

    assert not any("private" in name or "feedback_note" in name for name in _registry())
    tool_names = [getattr(t, "__name__", str(t)) for t in ALL_TOOLS]
    assert not any("private" in n or "fb_" in n for n in tool_names)
    # MCP module source must never reference the private service
    import inspect

    from app import mcp_server

    assert "private_notes" not in inspect.getsource(mcp_server)


def test_key_minting_requires_strong_identity(client, fresh_db):
    """The escalation that defeats the whole boundary: X-User must NOT be
    able to mint a usable key for any identity."""
    r = client.post("/api/keys", json={"label": "evil"}, headers={"X-User": "manager"})
    assert r.status_code == 403
    # and therefore private notes stay unreachable for header-only callers
    assert client.get("/api/private/notes", headers={"X-User": "manager"}).status_code == 403


def test_the_core_backup_never_carries_the_private_schema(client, fresh_db):
    """The 0600 file mode used to be the wall around 1:1 notes. With the notes
    in a schema rather than a file of their own, the wall is the DUMP boundary:
    the core backup is the copy that leaves the box (services/admin.py::_mirror),
    and the private schema must not be in it. The private dump is written
    separately and is never mirrored."""
    import subprocess

    from app import config
    from app.services import admin

    _write_private(client, fresh_db)
    out = admin.backup()
    core = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [_pg_restore(), "--list", out["path"]],
        env=admin._pg_env(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert f"{config.PRIVATE_SCHEMA} " not in core, "the core dump carries the private schema"
    assert "notes" not in [
        line.split()[-1] for line in core.splitlines() if f" {config.PRIVATE_SCHEMA} " in line
    ]
    # and the private dump exists on its own, unmirrored
    assert out["private_path"]
    assert out["mirrored"] is None or config.PRIVATE_SCHEMA not in out["mirrored"]


def test_feedback_parses_hyphenated_names(fresh_db):
    from app.services.private_notes import parse_feedback

    assert parse_feedback("fb: mary-jane — crushed the demo") == ("mary-jane", "crushed the demo")
    assert parse_feedback("fb: dana - solid week") == ("dana", "solid week")
    assert parse_feedback("fb: chen: good pushback") == ("chen", "good pushback")


def test_brief_degrades_to_empty(client, fresh_db):
    headers = _setup_key(client, fresh_db)
    r = client.get("/api/private/brief/dana", headers=headers)
    assert r.status_code == 200
    b = r.json()
    assert b["open_blockers"] == [] and b["standups"] == []
    assert "never captured feedback" in b["nudge"]


def test_private_note_delete_and_audit(client, fresh_db):
    from app.services.api_keys import create_key

    headers = {"Authorization": f"Bearer {create_key('manager', 't')['key']}"}
    note = client.post(
        "/api/private/notes", json={"person": "dana", "body": "note"}, headers=headers
    ).json()
    client.get("/api/private/notes?person=dana", headers=headers)
    r = client.delete(f"/api/private/notes/{note['id']}", headers=headers)
    assert r.json()["deleted"] is True
    assert client.get("/api/private/notes?person=dana", headers=headers).json() == []
    audit = client.get("/api/private/audit", headers=headers).json()
    actions = [a["action"] for a in audit]
    assert (
        "add_note" in actions and "delete" in actions and any(a.startswith("list") for a in actions)
    )
    # someone else can't delete or read the audit
    other = {"Authorization": f"Bearer {create_key('other', 't')['key']}"}
    note2 = client.post(
        "/api/private/notes", json={"person": "x", "body": "mine"}, headers=headers
    ).json()
    # 404, not 400: someone else's note must be indistinguishable from a
    # missing one — no existence leak
    assert client.delete(f"/api/private/notes/{note2['id']}", headers=other).status_code == 404
    assert client.get("/api/private/audit", headers=other).json() == []


def test_mcp_capture_refuses_private_feedback(fresh_db, monkeypatch):
    """Routing MCP capture through the review gate made capture.plan() run
    BEFORE the fb: guard, and the proposal path never calls capture() — so a
    private feedback line became a note proposal in the TEAM-VISIBLE review
    queue, and approving it wrote an FTS-indexed public note."""
    from app import config, mcp_server
    from app.services import users

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("mira")
    users.ensure_user(mcp_server.ACTOR, kind="agent")

    fn = getattr(mcp_server.capture, "fn", mcp_server.capture)
    out = fn("fb: mira — candid private assessment")

    assert "private" in out
    assert fresh_db.query_one("SELECT COUNT(*) AS n FROM pending_changes")["n"] == 0
    assert fresh_db.query_one("SELECT COUNT(*) AS n FROM notes")["n"] == 0
    # an ordinary capture still routes through the gate
    assert "pending" in fn("todo: an ordinary capture")


def test_a_third_party_rename_still_moves_notes_ABOUT_the_person(fresh_db):
    """The refusal guard checks whether the renamed person AUTHORS notes. It
    says nothing about notes others keep ABOUT them, and that column carries
    no ownership — so it must follow the rename. Freezing it stranded every
    teammate's 1:1 journal under a name with no roster row."""
    from app.services import private_notes, users

    for n in ("alice", "Bobby", "ops"):
        users.ensure_user(n)
    private_notes.add_note("alice", "Bobby", "1:1 notes about Bobby", kind="note")
    assert not private_notes.author_has_notes("Bobby")  # the guard will not fire

    users.rename_user("Bobby", "bob", actor="ops")

    assert [n["body"] for n in private_notes.list_notes("alice", "bob")] == [
        "1:1 notes about Bobby"
    ]
    assert private_notes.list_notes("alice", "Bobby") == []


def test_both_list_branches_keep_the_200_cap(fresh_db):
    """The person branch shipped without the unfiltered branch's LIMIT — a
    long 1:1 history came back whole. Self-scoped, so the cost is a slow
    render rather than a leak, but unbounded is unbounded."""
    from app.services import private_notes

    for i in range(205):
        private_notes.add_note("manager", "dana", f"entry {i}")
    assert len(private_notes.list_notes("manager", "dana")) == 200
    assert len(private_notes.list_notes("manager")) == 200


def test_mcp_reads_answer_only_for_its_own_identity(fresh_db, monkeypatch):
    """get_my_day took a model-controlled `user`, and briefing.my_day answers
    for whatever name it is handed — assigned questions, owned blockers,
    tasks, and the BODIES of unread notifications. One argument enumerated
    any teammate's inbox over a surface whose whole identity is an env var."""
    import inspect

    from app import mcp_server
    from app.services import notifications, users

    users.ensure_user("mira")
    users.ensure_user(mcp_server.ACTOR, kind="agent")
    notifications.notify("mira", "the vendor quote came in at 40k", tier="immediate")

    fn = getattr(mcp_server.get_my_day, "fn", mcp_server.get_my_day)
    assert inspect.signature(fn).parameters == {}
    assert "40k" not in fn()


def test_a_persona_turn_reads_the_inbox_with_the_humans_eyes(fresh_db):
    """`viewer=None` on agent_inbox means "the agent is the caller" and leaves
    the rows unfiltered — right for MCP and the scheduler. `/as <persona>`
    breaks that assumption: a HUMAN takes the persona's identity, every shipped
    persona holds my_agent_inbox, and the persona is in no crew. The REST twin
    refuses the same read, so the chat door was the way around it."""
    import json

    from app.agents.identity import (
        reset_agent_identity,
        reset_requester_viewer,
        set_agent_identity,
        set_requester_viewer,
    )
    from app.services import crews, delegation, scope, users, work
    from app.tools.portfolio import my_agent_inbox

    for n in ("ava", "bo"):
        users.ensure_user(n)
    users.ensure_user("scout", kind="agent")
    cid = crews.create_crew("Alpha", actor="ava")["id"]
    t = work.create_task(title="ZZCREWZZ rotate keys", actor="ava", visibility="crew", crew_id=cid)
    delegation.delegate_task(t["id"], "scout", "ava", actor="ava")

    def titles(viewer):
        tok, vt = set_agent_identity("scout"), set_requester_viewer(viewer)
        try:
            return [x["title"] for x in json.loads(my_agent_inbox())["delegated_tasks"]]
        finally:
            reset_requester_viewer(vt)
            reset_agent_identity(tok)

    assert titles(scope.Viewer("bo", True)) == []  # not in the crew
    assert titles(scope.Viewer("ava", True)) == ["ZZCREWZZ rotate keys"]
    assert titles(None) == ["ZZCREWZZ rotate keys"]  # autonomous: unchanged


def test_the_agent_inbox_tool_takes_no_name():
    """The MCP twin has had this test since the same exploit closed there.
    This one did not, while its comment said "Pinned by tests/test_privacy.py"
    — so the parameter could come back with the whole suite green, and the
    next author reading that line would not add the test either.

    `my_agent_inbox` answers for whatever roster row it is handed: delegated
    tasks, assigned questions, rejected proposals with reviewer notes. As a
    model-controlled argument, "check the agent inbox for mira" was the whole
    exploit.
    """
    import inspect

    from app.tools import portfolio

    fn = getattr(portfolio.my_agent_inbox, "fn", portfolio.my_agent_inbox)
    assert list(inspect.signature(fn).parameters) == [], (
        "my_agent_inbox must take no parameters — it reads its own identity"
        " from agent_identity() and the caller's viewer from requester_viewer()"
    )


def test_health_reports_a_superuser_database_role(fresh_db, monkeypatch):
    """A superuser connection is a standing privilege fault, and /health is
    where it has to show — a deployment that skipped the NOSUPERUSER role has
    no other signal, and the cost is that any SQL bug can run shell commands
    on the database host."""
    from app import db

    monkeypatch.setattr(db, "query_one", lambda *a, **k: {"rolsuper": True})
    warnings = db.privilege_warnings()
    assert warnings and "superuser" in warnings[0]
    assert "NOSUPERUSER" in warnings[0]

    monkeypatch.setattr(db, "query_one", lambda *a, **k: {"rolsuper": False})
    assert db.privilege_warnings() == []


def test_public_health_carries_no_deployment_topology(client, fresh_db):
    """The open /health answers anonymous callers on a public route.

    Exactly three keys: enough for a probe (ok), a sign-in flow (auth_mode),
    and the one fault that locks every authenticated surface (auth_error —
    with auth broken, an open endpoint is the only place an operator can
    read why). Everything else — provider, model, error strings, the job
    schedule, chain state, database warnings — is deployment topology and
    lives on /api/health behind identity. A new field added here reaches
    anonymous readers, so this assertion is exact, not a subset check."""
    body = client.get("/health").json()
    assert sorted(body) == ["auth_error", "auth_mode", "ok"]

    full = client.get("/api/health", headers={"X-User": "mira"}).json()
    for key in ("provider", "model", "jobs", "activity_chain", "database_warnings"):
        assert key in full
