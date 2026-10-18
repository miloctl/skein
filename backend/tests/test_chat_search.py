"""Chat search: every word as part of a word, across a person's own solo chats
and the shared chats they belong to, and opening a chat at the matched message."""

import re
import threading

from app import db, ratelimit
from app.services import chat_threads, fieldguide, users
from app.services.api_keys import create_key


def auth(name: str) -> dict[str, str]:
    users.ensure_user(name)
    return {"Authorization": f"Bearer {create_key(name, 'chat-search')['key']}"}


def search(client, q: str, headers: dict | None = None) -> dict:
    response = client.get("/api/chats/search", params={"q": q}, headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()


def threads(found: dict) -> list[str]:
    return [row["thread_id"] for row in found["results"]]


def test_part_of_a_word_finds_the_chat_at_its_newest_matching_message(client):
    for thread, message in (
        ("cert-chat", "How do I rotate the staging certificate?"),
        ("cert-chat", "And the production CERTIFICATES as well"),
        ("lunch-chat", "Where is lunch today?"),
    ):
        with client.stream(
            "POST", "/api/chat", json={"thread_id": thread, "message": message}
        ) as response:
            assert response.status_code == 200
            response.read()

    found = search(client, "rtifica")

    assert threads(found) == ["cert-chat"]
    assert found["truncated"] is False
    hit = found["results"][0]
    transcript = chat_threads.get_message_page("cert-chat", "tester")["messages"]
    assert hit["message_id"] == max(
        m["id"] for m in transcript if "rtifica" in m["content"].lower()
    )
    assert hit["kind"] == "solo"
    assert re.search(r"<b>rtifica</b>", hit["snippet"], re.IGNORECASE)
    assert "\n" not in hit["snippet"]


def test_every_word_must_appear_in_one_message(client):
    chat_threads.log_message("both", "tester", "user", "rotate the staging certificate")
    chat_threads.log_message("split", "tester", "user", "rotate the keys")
    chat_threads.log_message("split", "tester", "user", "certificate renewal")

    found = search(client, "certif ROTAT")

    assert threads(found) == ["both"]
    assert found["results"][0]["snippet"] == "<b>rotat</b>e the staging <b>certif</b>icate"
    # the longer word wins where one word starts another
    overlap = {r["thread_id"]: r["snippet"] for r in search(client, "cert certificate")["results"]}
    assert overlap["both"] == "rotate the staging <b>certificate</b>"


def test_wildcard_characters_match_only_themselves(client):
    chat_threads.log_message("plain", "tester", "user", "the xat file is ready")
    chat_threads.log_message("percent", "tester", "user", "coverage went to 100% today")
    chat_threads.log_message("underscore", "tester", "user", "set max_tokens lower")
    chat_threads.log_message("backslash", "tester", "user", r"the share is C:\builds")

    assert threads(search(client, "%")) == ["percent"]
    assert threads(search(client, "x_t")) == ["underscore"]
    assert threads(search(client, "\\")) == ["backslash"]


def test_another_persons_chat_never_matches(client):
    users.ensure_user("bob")
    chat_threads.log_message("mine", "tester", "user", "quarterly numbers")
    chat_threads.log_message("bobs", "bob", "user", "quarterly numbers for bob")

    assert threads(search(client, "quarter")) == ["mine"]
    assert threads(search(client, "quarter", {"X-User": "bob"})) == ["bobs"]
    assert threads(search(client, "quarter", auth("bob"))) == ["bobs"]


def test_shared_chats_match_only_for_a_strong_active_member(client):
    mira = auth("mira")
    room = client.post("/api/shared-chats", json={"title": "Launch room"}, headers=mira).json()
    posted = client.post(
        f"/api/shared-chats/{room['id']}/messages",
        json={"message": "The launch codename is Bluefin", "client_key": "m1"},
        headers=mira,
    )
    assert posted.status_code == 200
    dana = auth("dana")
    invitation = client.post(
        f"/api/shared-chats/{room['id']}/invitations",
        json={"person": "dana", "share_history": True},
        headers=mira,
    ).json()
    accepted = client.post(f"/api/shared-chats/invitations/{invitation['id']}/accept", headers=dana)
    assert accepted.status_code == 200

    hit = search(client, "bluef", dana)["results"]
    assert [(r["thread_id"], r["kind"], r["message_id"]) for r in hit] == [
        (room["id"], "shared", posted.json()["id"])
    ]
    # the room title is a match too, with no message behind it
    title_hit = search(client, "aunch roo", dana)["results"]
    assert [(r["thread_id"], r["message_id"], r["snippet"]) for r in title_hit] == [
        (room["id"], None, "")
    ]
    # the same name without a key cannot open the room, so it cannot search it
    assert search(client, "bluef", {"X-User": "dana"})["results"] == []
    assert search(client, "bluef", auth("raj"))["results"] == []

    assert client.post(f"/api/shared-chats/{room['id']}/leave", headers=dana).status_code == 200
    assert search(client, "bluef", dana)["results"] == []

    deleted = client.delete(
        f"/api/shared-chats/{room['id']}/messages/{posted.json()['id']}", headers=mira
    )
    assert deleted.status_code == 200
    assert search(client, "bluef", mira)["results"] == []


def test_a_chat_past_the_sidebar_cap_does_not_match(client):
    with db.transaction():
        for n in range(chat_threads.THREAD_LIMIT + 1):
            chat_threads.log_message(f"cap-{n:03d}", "tester", "user", f"marker{n:03d}x")
    listed = {t["id"] for t in chat_threads.list_threads("tester")}
    (hidden,) = {f"cap-{n:03d}" for n in range(chat_threads.THREAD_LIMIT + 1)} - listed
    shown = next(iter(listed))

    # the client loads a transcript only for a chat in GET /api/chats, so a
    # hit on the hidden one would open an empty chat
    assert search(client, f"marker{hidden[4:]}x")["results"] == []
    assert threads(search(client, f"marker{shown[4:]}x")) == [shown]


def test_a_renamed_chat_is_found_by_its_title(client):
    chat_threads.log_message("renamed", "tester", "user", "some first words")
    assert (
        client.patch("/api/chats/renamed", json={"title": "Vendor escalation"}).status_code == 200
    )

    found = search(client, "escalat")

    assert [(r["thread_id"], r["message_id"], r["snippet"]) for r in found["results"]] == [
        ("renamed", None, "")
    ]


def test_a_deleted_chat_stops_matching(client):
    chat_threads.log_message("gone", "tester", "user", "ephemeral zebra")
    assert threads(search(client, "zebra")) == ["gone"]

    assert client.delete("/api/chats/gone").status_code == 200

    assert search(client, "zebra")["results"] == []


def test_bounds_refuse_without_echoing_the_query(client):
    assert client.get("/api/chats/search", params={"q": ""}).status_code == 422
    assert client.get("/api/chats/search", params={"q": "a" * 201}).status_code == 422
    assert client.get("/api/chats/search", params={"q": "   "}).status_code == 400
    nine = "alpha bravo charlie delta echo foxtrot golf hotel india"
    refused = client.get("/api/chats/search", params={"q": nine})
    assert refused.status_code == 400
    assert refused.json()["detail"] == "The search has more than 8 words. Use 8 words or fewer."
    for odd in ('"', "-", "!", ":*", "&|", "'", ";"):
        assert search(client, odd)["results"] == []


def test_more_than_thirty_chats_are_truncated_newest_first(client):
    for n in range(31):
        chat_threads.log_message(f"many-{n:02d}", "tester", "user", f"standup note {n}")

    found = search(client, "standup")

    assert found["truncated"] is True
    assert len(found["results"]) == 30
    assert threads(found) == [f"many-{n:02d}" for n in range(30, 0, -1)]


def test_a_search_ties_the_card_only_when_it_finds_a_chat(client):
    users.ensure_human_identity("tester")
    chat_threads.log_message("guide", "tester", "user", "harbor schedule")

    def tied() -> bool:
        return any(
            c["id"] == "chat_search" and c["tied"] for c in fieldguide.guide("tester")["cards"]
        )

    assert search(client, "nothing-like-this")["results"] == []
    assert not tied()
    assert threads(search(client, "harb")) == ["guide"]
    assert tied()


def test_through_opens_the_chat_with_that_message_loaded(client):
    users.ensure_user("bob")
    for n in range(120):
        chat_threads.log_message("long", "tester", "user", f"line {n}")
    chat_threads.log_message("bobs-long", "bob", "user", "bob's line")
    ids = {
        m["content"]: m["id"]
        for m in chat_threads.get_message_page("long", "tester", limit=200)["messages"]
    }
    path = "/api/chats/long/messages/page"

    page = client.get(path, params={"through": ids["line 10"]}).json()
    assert [m["content"] for m in page["messages"]] == [f"line {n}" for n in range(10, 120)]
    assert page["next_before"] == ids["line 10"]

    # a recent hit opens the ordinary newest page, never a shorter one
    recent = client.get(path, params={"through": ids["line 100"]}).json()
    assert [m["content"] for m in recent["messages"]] == [f"line {n}" for n in range(70, 120)]

    foreign = chat_threads.get_message_page("bobs-long", "bob")["messages"][0]["id"]
    assert client.get(path, params={"through": foreign}).status_code == 404
    both = client.get(path, params={"through": ids["line 10"], "before": ids["line 50"]})
    assert both.status_code == 400


def test_through_stops_at_the_transcript_cap(client):
    with db.transaction():
        for n in range(chat_threads.MESSAGE_LIMIT + 5):
            chat_threads.log_message("huge", "tester", "user", f"entry {n}")
    oldest = db.query_row(
        "SELECT id FROM chat_messages WHERE thread_id = 'huge' AND content = 'entry 0'"
    )["id"]

    page = client.get("/api/chats/huge/messages/page", params={"through": oldest}).json()

    assert len(page["messages"]) == chat_threads.MESSAGE_LIMIT
    assert page["messages"][0]["content"] == "entry 5"
    assert page["next_before"] == page["messages"][0]["id"]


def test_the_snippet_is_a_window_around_the_first_match(client):
    chat_threads.log_message("window", "tester", "user", "x" * 100 + " mid " + "y" * 100)
    chat_threads.log_message("indented", "tester", "user", "word" + " " * 300 + "tail")

    snippets = {r["thread_id"]: r["snippet"] for r in search(client, "mid")["results"]}
    assert snippets["window"] == "…" + "x" * 59 + " <b>mid</b> " + "y" * 59 + "…"
    # whitespace is collapsed before the window is cut, not after
    assert search(client, "word")["results"][0]["snippet"] == "<b>word</b> tail"


def test_a_search_that_runs_too_long_stops_with_a_400(client, monkeypatch):
    with db.transaction():
        for _ in range(300):
            chat_threads.log_message("slow", "tester", "user", "a" * 20_000)
    monkeypatch.setattr(chat_threads, "SEARCH_TIMEOUT_MS", 1)

    stopped = client.get("/api/chats/search", params={"q": "a" * 150 + "b"})

    assert stopped.status_code == 400
    assert stopped.json()["detail"].startswith("The search stopped after ")


def test_searches_have_a_rate_cap(client, monkeypatch):
    monkeypatch.setitem(ratelimit.LIMITS, "chat_search", 2)
    assert search(client, "x")["results"] == []
    assert search(client, "x")["results"] == []

    capped = client.get("/api/chats/search", params={"q": "x"})

    assert capped.status_code == 429
    assert capped.headers["Retry-After"]
    assert "The limit for chat searches is 2 per minute" in capped.json()["detail"]


def test_through_reads_the_window_from_one_snapshot(client, monkeypatch):
    with db.transaction():
        for n in range(80):
            chat_threads.log_message("race", "tester", "user", f"line {n}")
    target = db.query_row(
        "SELECT id FROM chat_messages WHERE thread_id = 'race' AND content = 'line 10'"
    )["id"]
    count = db.query_row

    def reply_after_the_count(sql, params=()):
        row = count(sql, params)
        if "COUNT(*)" in sql:
            # a reply commits on its own connection between the count and the read
            late = threading.Thread(
                target=chat_threads.log_message, args=("race", "tester", "assistant", "late")
            )
            late.start()
            late.join()
        return row

    monkeypatch.setattr(db, "query_row", reply_after_the_count)
    page = client.get("/api/chats/race/messages/page", params={"through": target}).json()

    assert page["messages"][0]["content"] == "line 10"
