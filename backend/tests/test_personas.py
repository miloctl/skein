"""The Bench: persona registry, /as invocation, per-persona identity."""

from app.agents import commands
from app.agents.identity import agent_identity, reset_agent_identity, set_agent_identity
from app.services import personas


def _read_chat(client, message):
    with client.stream("POST", "/api/chat", json={"thread_id": "p", "message": message}) as resp:
        assert resp.status_code == 200
        return resp.read().decode()


def test_bench_includes_the_bosun():
    roster = personas.list_personas()
    assert len(roster) == 11
    slugs = {p["slug"] for p in roster}
    assert {"bosun", "code-reviewer", "growth-mentor", "training-designer"} <= slugs
    assert all(p["name"] and p["description"] and p["emoji"] for p in roster)


def test_bosun_is_read_only_and_uses_the_field_guide():
    bosun = personas.get_persona("bosun")
    assert personas.behavior("bosun")["tools"] == ["field_guide"]
    assert "field guide" in bosun["body"].lower()


def test_using_the_bosun_ties_its_field_guide_card(client, fresh_db):
    from app.services import fieldguide

    out = _read_chat(client, "/as bosun How do I use reviews?")
    assert "Bosun" in out
    card = next(row for row in fieldguide.guide("tester")["cards"] if row["id"] == "bosun")
    assert card["tied"] is True


def test_mock_bosun_cannot_smart_capture_records(client, fresh_db):
    out = _read_chat(client, "/as bosun todo: write despite the read-only persona")
    assert "answers only with a model provider" in out
    assert client.get("/api/tasks").json() == []
    assert fresh_db.query("SELECT * FROM pending_changes") == []


def test_get_persona_body_and_unknown():
    p = personas.get_persona("code-reviewer")
    assert "review" in p["body"].lower()
    try:
        personas.get_persona("nope")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "code-reviewer" in str(exc)


def test_personas_rest(client):
    roster = client.get("/api/personas").json()
    assert len(roster) == 11
    assert "body" not in roster[0]
    one = client.get("/api/personas/growth-mentor").json()
    assert one["body"]
    assert client.get("/api/personas/nope").status_code == 400


def test_personas_command_lists_bench(client):
    out = _read_chat(client, "/personas")
    assert "The bench" in out
    assert "growth-mentor" in out and "/as" in out


def test_as_masthead_and_routing(client, fresh_db):
    out = _read_chat(client, "/as code-reviewer todo: refactor the gate")
    assert "Code Reviewer" in out  # masthead
    tasks = client.get("/api/tasks").json()
    assert any("refactor the gate" in t["title"] for t in tasks)
    # invocation registered the persona as an agent identity
    row = fresh_db.query_one("SELECT * FROM users WHERE name = 'code-reviewer'")
    assert row and row["kind"] == "agent"


def test_as_usage_and_unknown_are_deterministic(client):
    assert "Usage" in _read_chat(client, "/as")
    assert "Usage" in _read_chat(client, "/as code-reviewer")
    out = _read_chat(client, "/as ghost do things")
    assert "no persona 'ghost'" in out


def test_catalog_includes_bench_commands(client):
    names = [c["name"] for c in client.get("/api/chat/commands").json()]
    assert "personas" in names and "as" in names


def test_identity_contextvar_signs_proposals(fresh_db, monkeypatch):
    from app import config
    from app.tools._gate import gated_write

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    token = set_agent_identity("code-reviewer")
    try:
        assert agent_identity() == "code-reviewer"
        gated_write("task", "create", {"title": "signed"}, direct=lambda: {"id": 0})
    finally:
        reset_agent_identity(token)
    assert agent_identity() == "agent"
    row = fresh_db.query_one("SELECT * FROM pending_changes ORDER BY id DESC")
    assert row["proposed_by"] == "code-reviewer"


def test_slack_refuses_route_level_commands_without_eating_prose(client, monkeypatch):
    """Every handler-None command is refused by name, and ONLY by name. An
    earlier guard stripped the slash before matching, so "as discussed, the
    vendor slipped" was refused instead of captured — a working write path,
    silently removed. Unrefused, the raw slash command is smart-captured as a
    note against whoever typed it."""
    import hashlib
    import hmac
    import time
    import urllib.parse

    from app import config

    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "s3cret")

    def ask(text):
        body = urllib.parse.urlencode({"text": text, "user_name": "mira"})
        ts = str(int(time.time()))
        sig = "v0=" + hmac.new(b"s3cret", f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
        return client.post(
            "/api/slack/command",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": sig,
            },
        ).json()["text"]

    for cmd in ("/as", "/flock", "/FLOCK"):
        assert "web chat only" in ask(f"{cmd} engineering hello"), cmd
    for prose in ("as discussed, the vendor slipped", "flock behaviour is odd"):
        assert "web chat only" not in ask(prose), prose


def test_dispatch_passes_as_through_to_route():
    assert commands.dispatch("/as code-reviewer hello", "tester") is None


def test_as_cannot_smuggle_fb_past_the_guard(client):
    out = _read_chat(client, "/as growth-mentor fb: mira — private thing")
    assert "Feedback notes are private" in out


def test_masthead_and_disclosure_on_fresh_thread(client):
    out = _read_chat(client, "/as growth-mentor note: thinking about goals")
    assert "Growth Mentor" in out  # route-emitted masthead, provider-agnostic
    assert "chat isn" in out  # privacy disclosure for growth personas


def test_bench_slugs_are_reserved_names(client, fresh_db):
    from app.services.users import ensure_user

    try:
        ensure_user("code-reviewer", kind="human")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "reserved" in str(exc)
    # Startup persists the reservation, so removing a mounted file cannot
    # let a human take the identity before the content returns.
    assert fresh_db.query_one(
        "SELECT kind, identity_owner FROM users WHERE name = 'growth-mentor'"
    ) == {"kind": "agent", "identity_owner": "content"}


def test_persona_session_suffix_survives_long_thread_ids(client):
    # persona sessions keep the FULL base id + suffix (no truncation), so
    # deletion can glob session_{id}--* without collateral matches
    long_thread = "x" * 80
    with client.stream(
        "POST", "/api/chat", json={"thread_id": long_thread, "message": "/as code-reviewer hi"}
    ) as resp:
        assert resp.status_code == 200
        assert "Code Reviewer" in resp.read().decode()


def test_authority_post_requires_strong_identity(client):
    r = client.post(
        "/api/agents/authority",
        json={"agent": "code-reviewer", "entity": "task", "level": "autonomous"},
    )
    assert r.status_code == 403


def test_agent_inbox_rejects_human_targets(client):
    client.get("/api/briefing")  # ensure 'tester' exists as a human
    assert client.get("/api/agents/tester/inbox").status_code == 404


def test_requested_by_recorded_on_persona_proposals(fresh_db, monkeypatch):
    from app import config
    from app.agents.identity import (
        reset_requester_identity,
        set_requester_identity,
    )
    from app.tools._gate import gated_write

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    t1 = set_agent_identity("sprint-prioritizer")
    t2 = set_requester_identity("mira")
    try:
        gated_write("task", "create", {"title": "asked-for"}, direct=lambda: {"id": 0})
    finally:
        reset_agent_identity(t1)
        reset_requester_identity(t2)
    row = fresh_db.query_one("SELECT * FROM pending_changes ORDER BY id DESC")
    assert row["proposed_by"] == "sprint-prioritizer"
    assert row["requested_by"] == "mira"


def test_slack_as_points_to_web_chat(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "s3cret")
    import hashlib
    import hmac
    import time

    body = "text=%2Fas+code-reviewer+todo%3A+x&user_name=mira"
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(b"s3cret", f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    r = client.post(
        "/api/slack/command",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
    )
    assert "web chat" in r.json()["text"]


PROBE_MD = "---\nname: Probe\ndescription: probes\n---\nYou are a probe."


def test_overlay_persona_joins_the_bench(tmp_path, monkeypatch):
    from app import config
    from app.services import personas

    (tmp_path / "fixer.md").write_text(PROBE_MD)
    monkeypatch.setattr(config, "PERSONAS_OVERLAY", tmp_path)
    slugs = [p["slug"] for p in personas.list_personas()]
    assert "fixer" in slugs and "code-reviewer" in slugs
    assert personas.get_persona("fixer")["body"] == "You are a probe."


def test_overlay_wins_a_slug_collision(tmp_path, monkeypatch):
    from app import config
    from app.services import personas

    (tmp_path / "code-reviewer.md").write_text(PROBE_MD)
    monkeypatch.setattr(config, "PERSONAS_OVERLAY", tmp_path)
    assert personas.get_persona("code-reviewer")["name"] == "Probe"
    slugs = [p["slug"] for p in personas.list_personas()]
    assert slugs.count("code-reviewer") == 1


def test_an_unparseable_overlay_stem_reserves_no_bench_name(tmp_path, monkeypatch):
    """bench_slugs() reserves every key of _persona_files() as an agent
    identity. A stem the slug charset rejects would reserve a name against a
    persona that list_personas() hides and get_persona() refuses to produce."""
    from app import config
    from app.services import personas

    (tmp_path / "Vendor Audit.md").write_text(PROBE_MD)
    monkeypatch.setattr(config, "PERSONAS_OVERLAY", tmp_path)
    assert not any(" " in s for s in personas.bench_slugs())
    assert not any(" " in p["slug"] for p in personas.list_personas())
    # and the validator still REPORTS it, so the bad file is not silent
    assert any("Vendor Audit.md" in e for e in personas.validate_all())


def test_the_default_agent_turn_carries_the_requester(client, fresh_db, monkeypatch):
    """The default path set no requester — the one path most turns take. A
    proposal from the Chief of Staff then reached the review inbox with no
    requested_by, so a team reviewer could not tell whose chat produced it,
    and the gate's per-person write bucket had no person to key on."""
    from app.agents.identity import requester_identity
    from app.routes import chat as chat_route

    seen = {}

    class Peek:
        async def stream_async(self, message):
            seen["requester"] = requester_identity()
            yield {"data": "ok"}

    monkeypatch.setattr(chat_route, "build_agent", lambda *a, **k: Peek())
    with client.stream(
        "POST",
        "/api/chat",
        json={"thread_id": "d", "message": "hello"},
        headers={"X-User": "mira"},
    ) as resp:
        resp.read()
    assert seen["requester"] == "mira"


# The closing block is copy-pasted into every consulting persona rather than
# templated, so the files stay plain reviewable markdown. This pins the block
# byte-for-byte: an edit to one copy must change all copies (and this string),
# or the bench drifts one persona at a time.
_CLOSING_BLOCK = """You work inside Skein, the team's coordination platform. You have the same
tools as the Chief of Staff: tasks, questions, decisions, blockers,
standups, engagements, search. Your writes are recorded under YOUR name; when review mode is
on they land as proposals for a human to approve. Cite entity ids (#12) when
you reference platform records. Stay in your lane: when a request is
outside your specialty, say so and suggest the right persona or the
Chief of Staff."""


def test_every_consulting_persona_ends_with_the_canonical_block():
    # bosun is the one persona with its own contract (read-only field guide).
    checked = 0
    for path in sorted(personas.PERSONAS_DIR.glob("*.md")):
        if path.stem == "bosun":
            continue
        body = path.read_text().rstrip()
        assert body.endswith(_CLOSING_BLOCK), (
            f"{path.name} drifted from the canonical closing block"
        )
        checked += 1
    assert checked >= 10
