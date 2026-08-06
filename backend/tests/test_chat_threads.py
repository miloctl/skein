"""Chat threads: folders, transcripts, rehydration, deletion."""


def _read_chat(client, message, thread="t-hist"):
    with client.stream("POST", "/api/chat", json={"thread_id": thread, "message": message}) as resp:
        assert resp.status_code == 200
        return resp.read().decode()


def test_transcript_logged_and_titled(client):
    _read_chat(client, "todo: prep the sprint review deck", thread="th-1")
    chats = client.get("/api/chats").json()
    assert chats[0]["id"] == "th-1"
    assert chats[0]["title"].startswith("todo: prep the sprint review")
    msgs = client.get("/api/chats/th-1/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert "sprint review deck" in msgs[0]["content"]


def test_persona_chat_titles_without_plumbing(client):
    _read_chat(client, "/as growth-mentor help me plan a learning goal", thread="th-2")
    chats = {c["id"]: c for c in client.get("/api/chats").json()}
    assert chats["th-2"]["title"].startswith("help me plan a learning goal")
    msgs = client.get("/api/chats/th-2/messages").json()
    assert "Growth Mentor" in msgs[1]["content"]  # masthead in the transcript


def test_command_output_is_in_transcript(client):
    _read_chat(client, "/personas", thread="th-3")
    msgs = client.get("/api/chats/th-3/messages").json()
    assert "The bench" in msgs[1]["content"]
    assert "🔧" in msgs[1]["content"]  # tool markers survive rehydration


def test_fb_never_reaches_transcript(client):
    _read_chat(client, "fb: mira — sensitive", thread="th-fb")
    assert all(c["id"] != "th-fb" for c in client.get("/api/chats").json())


def test_folders_rename_and_owner_scoping(client):
    _read_chat(client, "note: folder me", thread="th-4")
    r = client.patch("/api/chats/th-4", json={"folder": "1:1 prep", "title": "Goals chat"}).json()
    assert r["folder"] == "1:1 prep" and r["title"] == "Goals chat"
    # another user cannot see or touch it
    other = client.get("/api/chats", headers={"X-User": "intruder"}).json()
    assert all(c["id"] != "th-4" for c in other)
    # 404, not 400: a lookup miss is not a malformed request, and answering
    # 404 to an intruder also declines to confirm that th-4 exists at all
    assert client.get("/api/chats/th-4/messages", headers={"X-User": "intruder"}).status_code == 404


def test_delete_removes_transcript(client):
    _read_chat(client, "note: delete me soon", thread="th-5")
    assert client.delete("/api/chats/th-5").json()["deleted"] is True
    assert client.get("/api/chats/th-5/messages").status_code == 404


def test_log_message_never_cross_files_on_id_collision(client, fresh_db):
    from app.services import chat_threads

    chat_threads.log_message("shared-id", "alice", "user", "alice's thread")
    chat_threads.log_message("shared-id", "bob", "user", "bob's message")
    msgs = chat_threads.get_messages("shared-id", "alice")
    assert all("bob" not in m["content"] for m in msgs)


def test_delete_removes_both_session_stores_precisely(client, fresh_db, tmp_path):
    """The database rows AND the pre-045 leftover files, persona variants
    included — and never a thread that merely shares the prefix."""
    from strands.types.session import Session, SessionType

    from app import config
    from app.agents.session_store import SqliteSessionRepository
    from app.services import chat_threads

    chat_threads.log_message("abc", "tester", "user", "mine")
    repo = SqliteSessionRepository()
    for sid in ("abc", "abc--growth-mentor", "abc2"):
        repo.create_session(Session(session_id=sid, session_type=SessionType.AGENT))
    (config.SESSIONS_DIR / "session_abc").mkdir(parents=True, exist_ok=True)
    (config.SESSIONS_DIR / "session_abc--growth-mentor").mkdir(exist_ok=True)
    (config.SESSIONS_DIR / "session_abc2").mkdir(exist_ok=True)  # different thread
    chat_threads.delete_thread("abc", "tester")
    assert repo.read_session("abc") is None
    assert repo.read_session("abc--growth-mentor") is None
    assert repo.read_session("abc2") is not None  # untouched
    assert not (config.SESSIONS_DIR / "session_abc").exists()
    assert not (config.SESSIONS_DIR / "session_abc--growth-mentor").exists()
    assert (config.SESSIONS_DIR / "session_abc2").exists()  # untouched


def test_folder_snap_is_case_insensitive(client):
    def chat(thread, msg):
        with client.stream("POST", "/api/chat", json={"thread_id": thread, "message": msg}) as resp:
            resp.read()

    chat("f-1", "note: one")
    chat("f-2", "note: two")
    client.patch("/api/chats/f-1", json={"folder": "ops"})
    r = client.patch("/api/chats/f-2", json={"folder": "OPS"}).json()
    assert r["folder"] == "ops"


def test_remember_refuses_fb(client):
    with client.stream(
        "POST", "/api/chat", json={"thread_id": "fbm", "message": "/remember fb: mira struggles"}
    ) as resp:
        out = resp.read().decode()
    assert "private" in out
    assert client.get("/api/memories").json() == []


def test_empty_folders_persist(client):
    client.post("/api/chats/folders", json={"name": "Research"})
    assert "Research" in client.get("/api/chats/folders").json()
    # case-insensitive create returns the existing spelling
    client.post("/api/chats/folders", json={"name": "research"})
    folders = client.get("/api/chats/folders").json()
    assert folders.count("Research") == 1 and "research" not in folders


def test_emptied_folder_survives_and_delete_unfiles(client):
    def chat(thread, msg):
        with client.stream("POST", "/api/chat", json={"thread_id": thread, "message": msg}) as r:
            r.read()

    chat("ef-1", "note: hello")
    client.patch("/api/chats/ef-1", json={"folder": "Keep"})
    client.patch("/api/chats/ef-1", json={"folder": ""})  # emptied
    assert "Keep" in client.get("/api/chats/folders").json()  # survives
    client.patch("/api/chats/ef-1", json={"folder": "Keep"})
    out = client.delete("/api/chats/folders/Keep").json()
    assert out["unfiled"] == 1
    assert "Keep" not in client.get("/api/chats/folders").json()
    chats = {c["id"]: c for c in client.get("/api/chats").json()}
    assert chats["ef-1"]["folder"] == ""


def test_chat_thread_id_sanitized(client):
    with client.stream(
        "POST", "/api/chat", json={"thread_id": "../../etc/passwd", "message": "/help"}
    ) as r:
        assert r.status_code == 200
        assert "Mock agent" in r.read().decode()


def test_mid_stream_error_reaches_sse_and_transcript(client, monkeypatch):
    class ExplodingAgent:
        async def stream_async(self, message):
            yield {"data": "partial "}
            raise RuntimeError("model fell over")

    monkeypatch.setattr("app.routes.chat.build_agent", lambda *a, **k: ExplodingAgent())
    body = client.post("/api/chat", json={"thread_id": "t-err", "message": "hi"}).text
    # the SSE protocol must survive the failure: an error event, then done —
    # a dropped connection here loses the turn with no test failing
    assert '"type": "error"' in body
    assert '"type": "done"' in body
    msgs = client.get("/api/chats/t-err/messages").json()
    assert msgs[-1]["role"] == "assistant"
    assert "partial" in msgs[-1]["content"]
    assert "⚠️" in msgs[-1]["content"]  # the failure is on the record, not vanished


def test_agent_construction_failure_streams_an_error(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr("app.routes.chat.build_agent", boom)
    body = client.post("/api/chat", json={"thread_id": "t-err2", "message": "hi"}).text
    assert '"type": "error"' in body
    assert '"type": "done"' in body


def test_a_provider_error_reaches_the_ui_as_a_class_name_not_a_body(client, monkeypatch):
    """A provider SDK error carries its raw HTTP body — request ids, key
    prefixes — and the SSE error line is served to the chat window and
    written into the saved transcript. Only the class name may travel; the
    full detail belongs to the server log."""

    class ExplodingAgent:
        async def stream_async(self, message):
            yield {"data": "partial "}
            raise RuntimeError("401: api key sk-SECRET-abc123, request id req_deadbeef")

    monkeypatch.setattr("app.routes.chat.build_agent", lambda *a, **k: ExplodingAgent())
    body = client.post("/api/chat", json={"thread_id": "t-leak", "message": "hi"}).text
    assert "sk-SECRET-abc123" not in body
    assert "req_deadbeef" not in body
    assert "RuntimeError" in body
    msgs = client.get("/api/chats/t-leak/messages").json()
    assert "sk-SECRET-abc123" not in msgs[-1]["content"]


def test_chat_refuses_another_owners_thread(client):
    """The transcript write already refused a cross-file, but silently and
    only AFTER build_agent had restored the model-side conversation — the
    stream carried the other person's history while their sidebar showed
    nothing. The claim runs first, and a miss is a 404."""
    _read_chat(client, "note: mine alone", thread="th-own")
    resp = client.post(
        "/api/chat",
        json={"thread_id": "th-own", "message": "what were we discussing?"},
        headers={"X-User": "intruder"},
    )
    assert resp.status_code == 404
    # and the refusal did not file the intruder a thread of that name
    theirs = client.get("/api/chats", headers={"X-User": "intruder"}).json()
    assert theirs == []


def test_a_persona_session_id_cannot_be_typed(client):
    """The ownership claim guards thread ROWS, and a persona session id names
    none — `th-p--growth-mentor` was the session of whoever owned `th-p`, and
    it sanitized clean, so the claim waved it through to build_agent. The
    separator now sits outside _THREAD_ID's charset, which is the whole
    guarantee: routes/chat.py strips it from anything a caller sends."""
    import re

    from app.services.chat_threads import _THREAD_ID, PERSONA_SEP, persona_session_id

    minted = persona_session_id("th-p", "growth-mentor")
    assert not _THREAD_ID.fullmatch(minted)
    assert re.sub(r"[^A-Za-z0-9_-]", "", minted) != minted

    # the old separator is now just characters: an intruder sending it gets a
    # thread of their own by that literal name, reaching no session but theirs
    _read_chat(client, "/as growth-mentor plan my week", thread="th-p")
    resp = client.post(
        "/api/chat",
        json={"thread_id": "th-p--growth-mentor", "message": "continue"},
        headers={"X-User": "intruder"},
    )
    assert resp.status_code == 200
    theirs = [c["id"] for c in client.get("/api/chats", headers={"X-User": "intruder"}).json()]
    assert theirs == ["th-p--growth-mentor"]
    assert PERSONA_SEP not in "th-p--growth-mentor"


def test_the_unnamed_thread_cannot_be_squatted(client):
    """default_thread_id hashes a name every caller can read off the roster,
    and the claim is first-come. One POST to a teammate's computed id took
    their unnamed thread for good: every later message of theirs answered
    404, and they could not delete it to take it back."""
    from app.services.chat_threads import default_thread_id

    victim = default_thread_id("tester")
    squat = client.post(
        "/api/chat", json={"thread_id": victim, "message": "mine now"}, headers={"X-User": "thief"}
    )
    assert squat.status_code == 404
    # the rightful owner still lands on it
    client.post("/api/chat", json={"message": "hello"}).read()
    assert [c["id"] for c in client.get("/api/chats").json()] == [victim]


def test_an_unnamed_thread_is_one_per_person(client):
    """The ChatRequest default made an omitted thread id and an explicit
    'default' the same row for everyone, so every scripted caller restored
    the same model session."""
    from app.services import chat_threads

    client.post("/api/chat", json={"message": "note: no thread id"}).read()
    client.post(
        "/api/chat", json={"message": "note: also none"}, headers={"X-User": "other"}
    ).read()
    mine = [c["id"] for c in client.get("/api/chats").json()]
    theirs = [c["id"] for c in client.get("/api/chats", headers={"X-User": "other"}).json()]
    assert mine == [chat_threads.default_thread_id("tester")]
    assert theirs == [chat_threads.default_thread_id("other")]
    assert mine != theirs
