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
    assert client.get("/api/chats/th-4/messages", headers={"X-User": "intruder"}).status_code == 400


def test_delete_removes_transcript(client):
    _read_chat(client, "note: delete me soon", thread="th-5")
    assert client.delete("/api/chats/th-5").json()["deleted"] is True
    assert client.get("/api/chats/th-5/messages").status_code == 400
