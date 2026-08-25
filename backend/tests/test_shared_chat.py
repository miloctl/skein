"""Private invite-only shared chats: membership, history, and authorship."""

from app import db
from app.services import users
from app.services.api_keys import create_key


def auth(name: str) -> dict[str, str]:
    users.ensure_user(name)
    return {"Authorization": f"Bearer {create_key(name, 'shared-chat')['key']}"}


def create_room(client, owner: str = "mira", title: str = "Launch room") -> tuple[dict, dict]:
    headers = auth(owner)
    response = client.post("/api/shared-chats", json={"title": title}, headers=headers)
    assert response.status_code == 200
    return response.json(), headers


def invite(client, thread_id: str, headers: dict, person: str) -> dict:
    response = client.post(
        f"/api/shared-chats/{thread_id}/invitations",
        json={"person": person, "share_history": True},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


def post_message(client, thread_id: str, headers: dict, text: str, key: str) -> dict:
    response = client.post(
        f"/api/shared-chats/{thread_id}/messages",
        json={"message": text, "client_key": key},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


def test_shared_chat_requires_strong_identity(client):
    response = client.post("/api/shared-chats", json={"title": "Private room"})
    assert response.status_code == 403
    assert client.get("/api/shared-chats").status_code == 403


def test_creator_is_the_first_steward_and_nonmembers_learn_nothing(client):
    room, mira = create_room(client)
    dana = auth("dana")

    assert room["kind"] == "shared"
    assert room["role"] == "steward"
    assert room["members"] == [
        {"person": "mira", "role": "steward", "joined_at": room["members"][0]["joined_at"]}
    ]
    assert client.get("/api/shared-chats", headers=mira).json()[0]["id"] == room["id"]
    assert client.get("/api/shared-chats", headers=dana).json() == []
    absent = client.get(f"/api/shared-chats/{room['id']}", headers=dana)
    assert absent.status_code == 404
    assert room["id"] not in absent.text


def test_invitation_discloses_no_chat_content_before_acceptance(client):
    room, mira = create_room(client, title="Acquisition decision")
    post_message(client, room["id"], mira, "The confidential price is 40 million.", "m1")
    dana = auth("dana")
    invitation = invite(client, room["id"], mira, "dana")

    pending = client.get("/api/shared-chats/invitations", headers=dana).json()
    assert pending == [
        {
            "id": invitation["id"],
            "invited_by": "mira",
            "created_at": invitation["created_at"],
        }
    ]
    assert "Acquisition" not in str(pending)
    assert "40 million" not in str(pending)
    assert client.get(f"/api/shared-chats/{room['id']}/messages", headers=dana).status_code == 404


def test_accepting_an_invitation_grants_the_full_history(client):
    room, mira = create_room(client)
    first = post_message(client, room["id"], mira, "Before the invitation", "before")
    dana = auth("dana")
    invitation = invite(client, room["id"], mira, "dana")

    accepted = client.post(
        f"/api/shared-chats/invitations/{invitation['id']}/accept",
        headers=dana,
    )
    assert accepted.status_code == 200
    assert accepted.json()["role"] == "member"
    messages = client.get(f"/api/shared-chats/{room['id']}/messages", headers=dana).json()
    assert messages[0] == first
    assert messages[1]["author_kind"] == "system"
    assert messages[1]["content"] == "dana joined the private shared chat."


def test_declining_an_invitation_does_not_grant_access(client):
    room, mira = create_room(client)
    dana = auth("dana")
    invitation = invite(client, room["id"], mira, "dana")

    declined = client.post(
        f"/api/shared-chats/invitations/{invitation['id']}/decline",
        headers=dana,
    )
    assert declined.status_code == 200
    assert declined.json()["status"] == "declined"
    assert client.get(f"/api/shared-chats/{room['id']}", headers=dana).status_code == 404


def test_shared_messages_keep_authorship_and_are_idempotent(client):
    room, mira = create_room(client)
    dana = auth("dana")
    invitation = invite(client, room["id"], mira, "dana")
    client.post(f"/api/shared-chats/invitations/{invitation['id']}/accept", headers=dana)

    first = post_message(client, room["id"], mira, "From Mira", "mira-1")
    duplicate = post_message(client, room["id"], mira, "Changed on retry", "mira-1")
    # Idempotency belongs to the sender. Another member can use the same key.
    second = post_message(client, room["id"], dana, "From Dana", "mira-1")

    assert duplicate == first
    assert [
        (row["author"], row["author_kind"], row["content"])
        for row in client.get(f"/api/shared-chats/{room['id']}/messages", headers=mira).json()
        if row["author_kind"] != "system"
    ] == [
        ("mira", "human", "From Mira"),
        ("dana", "human", "From Dana"),
    ]
    assert second["id"] > first["id"]


def test_identity_merge_preserves_messages_with_the_same_sender_key(client):
    room, mira = create_room(client)
    dana = auth("dana")
    invitation = invite(client, room["id"], mira, "dana")
    client.post(f"/api/shared-chats/invitations/{invitation['id']}/accept", headers=dana)
    first = post_message(client, room["id"], mira, "From Mira", "shared-key")
    second = post_message(client, room["id"], dana, "From Dana", "shared-key")

    users.rename_user("mira", "dana", actor="mira", expected_merge=True)

    messages = client.get(f"/api/shared-chats/{room['id']}/messages", headers=dana).json()
    assert [message["id"] for message in messages if message["author_kind"] == "human"] == [
        first["id"],
        second["id"],
    ]


def test_removed_member_loses_access_but_authorship_remains(client):
    room, mira = create_room(client)
    dana = auth("dana")
    invitation = invite(client, room["id"], mira, "dana")
    client.post(f"/api/shared-chats/invitations/{invitation['id']}/accept", headers=dana)
    post_message(client, room["id"], dana, "Dana wrote this", "dana-note")

    removed = client.request(
        "DELETE",
        f"/api/shared-chats/{room['id']}/members",
        json={"person": "dana"},
        headers=mira,
    )
    assert removed.status_code == 200
    assert client.get(f"/api/shared-chats/{room['id']}/messages", headers=dana).status_code == 404
    messages = client.get(f"/api/shared-chats/{room['id']}/messages", headers=mira).json()
    authored = next(message for message in messages if message["content"] == "Dana wrote this")
    assert authored["author"] == "dana"


def test_last_steward_cannot_leave_and_a_promoted_steward_can_take_over(client):
    room, mira = create_room(client)
    assert client.post(f"/api/shared-chats/{room['id']}/leave", headers=mira).status_code == 409

    dana = auth("dana")
    invitation = invite(client, room["id"], mira, "dana")
    client.post(f"/api/shared-chats/invitations/{invitation['id']}/accept", headers=dana)
    promoted = client.post(
        f"/api/shared-chats/{room['id']}/members/role",
        json={"person": "dana", "role": "steward"},
        headers=mira,
    )
    assert promoted.status_code == 200
    assert client.post(f"/api/shared-chats/{room['id']}/leave", headers=mira).status_code == 200
    assert client.get(f"/api/shared-chats/{room['id']}", headers=dana).json()["role"] == "steward"


def test_archived_room_is_readable_but_refuses_new_messages_and_invitations(client):
    room, mira = create_room(client)
    post_message(client, room["id"], mira, "Keep this history", "history")
    archived = client.post(f"/api/shared-chats/{room['id']}/archive", headers=mira)
    assert archived.status_code == 200

    assert client.get(f"/api/shared-chats/{room['id']}/messages", headers=mira).status_code == 200
    assert (
        client.post(
            f"/api/shared-chats/{room['id']}/messages",
            json={"message": "No new message", "client_key": "blocked"},
            headers=mira,
        ).status_code
        == 409
    )
    users.ensure_user("dana")
    assert (
        client.post(
            f"/api/shared-chats/{room['id']}/invitations",
            json={"person": "dana", "share_history": True},
            headers=mira,
        ).status_code
        == 409
    )
    assert client.post(f"/api/shared-chats/{room['id']}/restore", headers=mira).status_code == 200
    assert post_message(client, room["id"], mira, "Restored", "restored")["content"] == "Restored"


def test_sender_message_and_idempotent_retry_advance_the_read_cursor(client):
    room, mira = create_room(client)
    first = post_message(client, room["id"], mira, "Mine", "sender-read")
    assert client.get("/api/shared-chats", headers=mira).json()[0]["unread_count"] == 0
    duplicate = post_message(client, room["id"], mira, "Changed", "sender-read")
    assert duplicate == first
    assert client.get("/api/shared-chats", headers=mira).json()[0]["unread_count"] == 0


def test_unread_cursor_is_monotonic_and_clamped_to_the_thread(client):
    room, mira = create_room(client)
    dana = auth("dana")
    invitation = invite(client, room["id"], mira, "dana")
    client.post(f"/api/shared-chats/invitations/{invitation['id']}/accept", headers=dana)
    first = post_message(client, room["id"], mira, "one", "one")
    second = post_message(client, room["id"], mira, "two", "two")

    listed = client.get("/api/shared-chats", headers=dana).json()[0]
    assert listed["unread_count"] == 3
    client.post(
        f"/api/shared-chats/{room['id']}/read",
        json={"message_id": first["id"]},
        headers=dana,
    )
    assert client.get("/api/shared-chats", headers=dana).json()[0]["unread_count"] == 1
    read = client.post(
        f"/api/shared-chats/{room['id']}/read",
        json={"message_id": second["id"] + 1000},
        headers=dana,
    ).json()
    assert read["last_read_message_id"] == second["id"]
    lower_retry = client.post(
        f"/api/shared-chats/{room['id']}/read",
        json={"message_id": first["id"]},
        headers=dana,
    ).json()
    assert lower_retry["last_read_message_id"] == second["id"]
    assert client.get("/api/shared-chats", headers=dana).json()[0]["unread_count"] == 0


def test_complete_history_is_reachable_from_the_zero_cursor(client):
    from app.services.chat_threads import MESSAGE_LIMIT

    room, mira = create_room(client)
    with db.transaction():
        for index in range(MESSAGE_LIMIT + 1):
            db.execute(
                "INSERT INTO chat_messages"
                " (thread_id, role, content, created_at, author_kind, author, client_key)"
                " VALUES (?, 'user', ?, ?, 'human', 'mira', ?)",
                (room["id"], f"message {index}", db.now(), f"page-{index}"),
            )

    first = client.get(f"/api/shared-chats/{room['id']}/messages?after=0", headers=mira).json()
    assert len(first) == MESSAGE_LIMIT
    assert first[0]["content"] == "message 0"
    second = client.get(
        f"/api/shared-chats/{room['id']}/messages?after={first[-1]['id']}",
        headers=mira,
    ).json()
    assert [row["content"] for row in second] == [f"message {MESSAGE_LIMIT}"]

    latest = client.get(f"/api/shared-chats/{room['id']}/messages", headers=mira).json()
    assert latest[0]["content"] == "message 1"
    older = client.get(
        f"/api/shared-chats/{room['id']}/messages?before={latest[0]['id']}",
        headers=mira,
    ).json()
    assert [row["content"] for row in older] == ["message 0"]


def test_steward_can_review_and_revoke_pending_invitations(client):
    room, mira = create_room(client)
    dana = auth("dana")
    invitation = invite(client, room["id"], mira, "dana")

    details = client.get(f"/api/shared-chats/{room['id']}", headers=mira).json()
    assert details["pending_invitations"] == [
        {
            "id": invitation["id"],
            "person": "dana",
            "invited_by": "mira",
            "created_at": invitation["created_at"],
        }
    ]
    revoked = client.request(
        "DELETE",
        f"/api/shared-chats/{room['id']}/invitations",
        json={"invitation_id": invitation["id"]},
        headers=mira,
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert client.get("/api/shared-chats/invitations", headers=dana).json() == []


def test_member_names_with_slashes_travel_in_the_request_body(client):
    room, mira = create_room(client)
    slash = auth("dana/ops")
    invitation = invite(client, room["id"], mira, "dana/ops")
    client.post(f"/api/shared-chats/invitations/{invitation['id']}/accept", headers=slash)
    removed = client.request(
        "DELETE",
        f"/api/shared-chats/{room['id']}/members",
        json={"person": "dana/ops"},
        headers=mira,
    )
    assert removed.status_code == 200


def test_identity_merge_folds_members_and_pending_invitations(client):
    from app.services import chat_threads

    room, mira = create_room(client)
    dana = auth("dana")
    other = auth("dana-alt")
    first = invite(client, room["id"], mira, "dana")
    second = invite(client, room["id"], mira, "dana-alt")
    client.post(f"/api/shared-chats/invitations/{first['id']}/accept", headers=dana)
    client.post(f"/api/shared-chats/invitations/{second['id']}/accept", headers=other)
    client.post(
        f"/api/shared-chats/{room['id']}/members/role",
        json={"person": "dana", "role": "steward"},
        headers=mira,
    )
    chat_threads.post_shared_message(room["id"], "dana", "Authored before merge", "merge")

    # A second room keeps two pending invitations, which the merge must fold
    # before the partial unique index sees both under one person.
    pending_room, pending_owner = create_room(client, owner="mira", title="Pending")
    invite(client, pending_room["id"], pending_owner, "dana")
    invite(client, pending_room["id"], pending_owner, "dana-alt")
    users.rename_user("dana", "dana-alt", actor="dana", expected_merge=True)

    members = client.get(f"/api/shared-chats/{room['id']}", headers=other).json()["members"]
    assert [member for member in members if member["person"] == "dana-alt"] == [
        {
            "person": "dana-alt",
            "role": "steward",
            "joined_at": next(m["joined_at"] for m in members if m["person"] == "dana-alt"),
        }
    ]
    messages = client.get(f"/api/shared-chats/{room['id']}/messages", headers=other).json()
    authored = next(
        message for message in messages if message["content"] == "Authored before merge"
    )
    assert authored["author"] == "dana-alt"
    pending = client.get("/api/shared-chats/invitations", headers=other).json()
    assert len(pending) == 1


def test_identity_merge_keeps_an_active_sole_steward_over_a_left_target(client):
    room, dana = create_room(client, owner="dana")
    target = auth("dana-alt")
    invitation = invite(client, room["id"], dana, "dana-alt")
    client.post(f"/api/shared-chats/invitations/{invitation['id']}/accept", headers=target)
    assert client.post(f"/api/shared-chats/{room['id']}/leave", headers=target).status_code == 200

    users.rename_user("dana", "dana-alt", actor="dana", expected_merge=True)

    merged = client.get(f"/api/shared-chats/{room['id']}", headers=target)
    assert merged.status_code == 200
    assert merged.json()["role"] == "steward"


def test_identity_merge_revokes_an_invitation_to_an_existing_active_member(client):
    room, target = create_room(client, owner="dana-alt")
    auth("dana")
    invite(client, room["id"], target, "dana")

    users.rename_user("dana", "dana-alt", actor="dana", expected_merge=True)

    assert client.get("/api/shared-chats/invitations", headers=target).json() == []
    assert client.get(f"/api/shared-chats/{room['id']}", headers=target).json()["role"] == "steward"


def test_shared_room_id_cannot_enter_the_solo_agent_route(client):
    room, mira = create_room(client)
    response = client.post(
        "/api/chat",
        json={"thread_id": room["id"], "message": "Read this private room"},
        headers=mira,
    )
    assert response.status_code == 404


def test_agents_and_unconfirmed_history_sharing_are_refused(client):
    room, mira = create_room(client)
    users.ensure_user("scout", kind="agent")
    assert (
        client.post(
            f"/api/shared-chats/{room['id']}/invitations",
            json={"person": "scout", "share_history": True},
            headers=mira,
        ).status_code
        == 400
    )
    users.ensure_user("dana")
    assert (
        client.post(
            f"/api/shared-chats/{room['id']}/invitations",
            json={"person": "dana", "share_history": False},
            headers=mira,
        ).status_code
        == 422
    )


def test_invitation_notification_reveals_no_private_room_data(client):
    room, mira = create_room(client, title="Secret acquisition")
    post_message(client, room["id"], mira, "The private price is 40 million.", "secret")
    dana = auth("dana")
    first = invite(client, room["id"], mira, "dana")
    second = invite(client, room["id"], mira, "dana")
    assert second["id"] == first["id"]

    weak = {"X-User": "dana"}
    assert client.get("/api/notifications", headers=weak).json() == []
    invitation_notice = db.query_row(
        "SELECT id FROM notifications WHERE source_entity = 'chat_invitation'"
    )["id"]
    assert (
        client.post(
            "/api/notifications/read",
            json={"notification_id": invitation_notice},
            headers=weak,
        ).status_code
        == 404
    )
    rows = client.get("/api/notifications", headers=dana).json()
    assert len(rows) == 1
    assert rows[0]["message"] == "You have a private shared-chat invitation."
    assert rows[0]["link"] == "/chat"
    rendered = str(rows)
    assert "Secret acquisition" not in rendered
    assert "40 million" not in rendered
    assert room["id"] not in rendered


def test_acceptance_files_a_private_system_message_for_members(client):
    room, mira = create_room(client)
    dana = auth("dana")
    invitation = invite(client, room["id"], mira, "dana")
    assert client.get(f"/api/shared-chats/{room['id']}/messages", headers=dana).status_code == 404

    client.post(
        f"/api/shared-chats/invitations/{invitation['id']}/accept",
        headers=dana,
    )
    messages = client.get(f"/api/shared-chats/{room['id']}/messages", headers=dana).json()
    assert messages == [
        {
            "id": messages[0]["id"],
            "thread_id": room["id"],
            "role": "assistant",
            "author_kind": "system",
            "author": "Skein",
            "content": "dana joined the private shared chat.",
            "created_at": messages[0]["created_at"],
            "turn_id": "",
            "reply_to_message_id": None,
        }
    ]


def test_shared_message_mentions_only_notify_current_human_members(client):
    room, mira = create_room(client)
    dana = auth("dana")
    outsider = auth("outsider")
    invitation = invite(client, room["id"], mira, "dana")
    client.post(
        f"/api/shared-chats/invitations/{invitation['id']}/accept",
        headers=dana,
    )
    with db.transaction():
        db.execute("DELETE FROM notifications")
    message = post_message(
        client,
        room["id"],
        mira,
        "Ask @dana and @outsider for a review.",
        "human-mentions",
    )

    notice = db.query_row('SELECT * FROM notifications WHERE "user" = ?', ("dana",))
    assert notice["message"] == "mira mentioned you in a private shared chat."
    assert notice["link"] == f"/chat?shared={room['id']}#shared-message-{message['id']}"
    assert db.query_one('SELECT 1 FROM notifications WHERE "user" = ?', ("outsider",)) is None
    assert db.query_row(
        "SELECT entity, entity_id, person FROM mention_log WHERE person = 'dana'"
    ) == {"entity": "chat_message", "entity_id": message["id"], "person": "dana"}
    weak = {"X-User": "dana"}
    assert client.get("/api/notifications", headers=weak).json() == []
    assert (
        client.post(
            "/api/notifications/read",
            json={"notification_id": notice["id"]},
            headers=weak,
        ).status_code
        == 404
    )

    client.request(
        "DELETE",
        f"/api/shared-chats/{room['id']}/members",
        json={"person": "dana"},
        headers=mira,
    )
    assert client.get("/api/notifications", headers=dana).json() == []
    assert client.get("/api/notifications", headers=outsider).json() == []
    assert db.query_row("SELECT read_at FROM notifications WHERE id = ?", (notice["id"],))[
        "read_at"
    ]


def test_shared_chat_links_only_workspace_engagements_under_stewardship(client):
    from app.services import engagements

    room, mira = create_room(client)
    dana = auth("dana")
    invitation = invite(client, room["id"], mira, "dana")
    client.post(
        f"/api/shared-chats/invitations/{invitation['id']}/accept",
        headers=dana,
    )
    workspace = engagements.create_engagement("Open delivery", actor="mira")
    private = engagements.create_engagement(
        "Private delivery",
        actor="mira",
        visibility="private",
    )

    refused_member = client.patch(
        f"/api/shared-chats/{room['id']}",
        json={"engagement_id": workspace["id"]},
        headers=dana,
    )
    assert refused_member.status_code == 403
    linked = client.patch(
        f"/api/shared-chats/{room['id']}",
        json={"engagement_id": workspace["id"]},
        headers=mira,
    )
    assert linked.status_code == 200
    assert linked.json()["engagement_id"] == workspace["id"]
    assert linked.json()["engagement_name"] == "Open delivery"
    assert (
        client.get("/api/shared-chats", headers=mira).json()[0]["engagement_id"] == workspace["id"]
    )
    refused_private = client.patch(
        f"/api/shared-chats/{room['id']}",
        json={"engagement_id": private["id"]},
        headers=mira,
    )
    assert refused_private.status_code == 404
    cleared = client.patch(
        f"/api/shared-chats/{room['id']}",
        json={"engagement_id": 0},
        headers=mira,
    )
    assert cleared.status_code == 200
    assert cleared.json()["engagement_id"] is None
