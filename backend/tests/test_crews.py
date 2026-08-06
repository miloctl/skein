"""Crews: membership, stewardship, and the walls around both.

A crew grants nothing yet (docs/VISIBILITY.md phase 1). What it must already
get right is who can change one, because the tier that reads this membership
lands on top of it.
"""

import pytest

from app import db
from app.services import crews, users


def _key(client, owner="tester"):
    from app.services.api_keys import create_key

    users.ensure_user(owner)
    return {"Authorization": f"Bearer {create_key(owner, 'k')['key']}"}


def test_the_creator_becomes_the_first_steward(fresh_db):
    users.ensure_user("ava")
    crew = crews.create_crew("Platform", summary="the backend half", actor="ava")
    assert crew["name"] == "Platform"
    assert crew["members"] == [{"person": "ava", "role": "steward"}]


def test_a_crew_name_is_unique_case_insensitively(fresh_db):
    """The service pre-checks then inserts in two statements, so the NOCASE
    index in migration 003 is what stops two concurrent creates from both
    landing and showing one crew twice in the picker."""
    users.ensure_user("ava")
    crews.create_crew("Platform", actor="ava")
    with pytest.raises(ValueError, match="already exists"):
        crews.create_crew("platform", actor="ava")
    with pytest.raises(Exception, match=r"UNIQUE|already exists"):
        db.execute("INSERT INTO crews (name, created_at, updated_at) VALUES ('PLATFORM', 'x', 'x')")
    # and the fold guard, which the ASCII-only NOCASE index does not catch
    crews.create_crew("Café", actor="ava")
    with pytest.raises(ValueError, match="already exists"):
        crews.create_crew("CAFÉ", actor="ava")


def test_a_crew_cannot_take_a_system_actor_name(fresh_db):
    """`team` addresses a notifications broadcast whose rows the first reader
    clears for everyone. A crew by that name reads as one on every surface."""
    users.ensure_user("ava")
    for reserved in ("team", "system", "scheduler", "forge"):
        with pytest.raises(ValueError, match="reserved"):
            crews.create_crew(reserved, actor="ava")


def test_an_agent_cannot_join_a_crew(fresh_db):
    """Agents read through the gated tool surface with its own authority
    matrix. A membership row would hand one a human's read scope."""
    users.ensure_user("ava")
    users.ensure_user("scout", kind="agent")
    crew = crews.create_crew("Platform", actor="ava")
    with pytest.raises(ValueError, match="agent identity"):
        crews.add_member(crew["id"], "scout", actor="ava")


def test_a_member_must_be_on_the_roster(fresh_db):
    users.ensure_user("ava")
    crew = crews.create_crew("Platform", actor="ava")
    with pytest.raises(ValueError, match="not an active teammate"):
        crews.add_member(crew["id"], "nobody", actor="ava")
    # and 'team' is not a phantom member
    with pytest.raises(ValueError, match="not an active teammate"):
        crews.add_member(crew["id"], "team", actor="ava")


def test_the_last_steward_cannot_leave(fresh_db):
    """A crew with no steward can only be edited by an administrator, and
    nothing on any surface says so."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    crew = crews.create_crew("Platform", actor="ava")
    crews.add_member(crew["id"], "bo", actor="ava")
    with pytest.raises(ValueError, match="needs one steward"):
        crews.remove_member(crew["id"], "ava", actor="ava")
    crews.add_member(crew["id"], "bo", role="steward", actor="ava")
    after = crews.remove_member(crew["id"], "ava", actor="ava")
    assert after["members"] == [{"person": "bo", "role": "steward"}]


def test_crews_of_survives_deactivation(fresh_db):
    """A row scoped to a deactivated crew must stay readable by the people who
    could always read it, or deactivating a crew hides their own work."""
    users.ensure_user("ava")
    crew = crews.create_crew("Platform", actor="ava")
    crews.update_crew(crew["id"], active=False, actor="ava")
    assert crews.crews_of("ava") == [crew["id"]]
    assert crews.list_crews() == []
    assert [c["id"] for c in crews.list_crews(active_only=False)] == [crew["id"]]


def test_a_rename_moves_crew_membership(fresh_db):
    """crew_members keys on the roster name and there is no foreign key to
    users(name) anywhere in this schema. Left out of users._ATTRIBUTION, a
    rename drops the person out of every crew they could read."""
    users.ensure_user("ava")
    crew = crews.create_crew("Platform", actor="ava")
    users.rename_user("ava", "ava-p", actor="system")
    assert crews.crews_of("ava") == []
    assert crews.crews_of("ava-p") == [crew["id"]]
    assert crews.get_crew(crew["id"])["members"] == [{"person": "ava-p", "role": "steward"}]


def test_only_a_steward_or_an_administrator_edits_a_crew(client, fresh_db, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "ADMINS", ["boss"])
    users.ensure_user("ava")
    users.ensure_user("bo")
    crew = crews.create_crew("Platform", actor="ava")

    hdr = _key(client, "bo")
    r = client.post(f"/api/crews/{crew['id']}/members", json={"person": "bo"}, headers=hdr)
    assert r.status_code == 403
    assert "steward" in r.json()["detail"]

    hdr = _key(client, "ava")
    assert (
        client.post(
            f"/api/crews/{crew['id']}/members", json={"person": "bo"}, headers=hdr
        ).status_code
        == 200
    )

    hdr = _key(client, "boss")
    assert (
        client.patch(
            f"/api/crews/{crew['id']}", json={"summary": "renamed by an admin"}, headers=hdr
        ).status_code
        == 200
    )


def test_editing_a_crew_needs_strong_identity(client, fresh_db):
    """Membership is about to decide what a person reads, and in
    trusted-header mode X-User is whatever the caller typed. Same bar
    routes/private.py holds for the other surface that answers per person."""
    users.ensure_user("ava")
    crew = crews.create_crew("Platform", actor="ava")
    r = client.post(
        f"/api/crews/{crew['id']}/members", json={"person": "ava"}, headers={"X-User": "ava"}
    )
    assert r.status_code == 403
    assert "personal API key" in r.json()["detail"]

    assert (
        client.post("/api/crews", json={"name": "Weak"}, headers={"X-User": "ava"}).status_code
        == 403
    )


def test_reading_crews_is_open_to_the_roster(client, fresh_db):
    """Who is in which crew is not itself scoped — a picker in every create
    form reads it, and hiding it would only hide the consequence of a choice
    the writer is about to make."""
    users.ensure_user("ava")
    crews.create_crew("Platform", actor="ava")
    listed = client.get("/api/crews").json()
    assert [c["name"] for c in listed] == ["Platform"]
    assert client.get("/api/crews/mine", headers={"X-User": "ava"}).json() == [listed[0]["id"]]
    assert client.get("/api/crews/mine", headers={"X-User": "bo"}).json() == []


def test_a_missing_crew_is_a_404(client, fresh_db):
    assert client.get("/api/crews/999").status_code == 404


def test_a_sole_steward_cannot_demote_themselves(fresh_db):
    """The floor lived only in remove_member, so setting your own role to
    member locked the crew: nobody could edit it afterwards, including you."""
    users.ensure_user("ava")
    crew = crews.create_crew("Platform", actor="ava")
    with pytest.raises(ValueError, match="needs one steward"):
        crews.add_member(crew["id"], "ava", role="member", actor="ava")
    assert crews.get_crew(crew["id"])["members"] == [{"person": "ava", "role": "steward"}]


def test_the_last_steward_guard_holds_under_concurrency(fresh_db):
    """The count ran before BEGIN IMMEDIATE, so two concurrent removals both
    saw two stewards, both passed, and the crew ended with none."""
    import threading

    users.ensure_user("ava")
    users.ensure_user("bo")
    crew = crews.create_crew("Platform", actor="ava")
    crews.add_member(crew["id"], "bo", role="steward", actor="ava")

    errors: list[Exception] = []

    def drop(person: str) -> None:
        try:
            crews.remove_member(crew["id"], person, actor="admin")
        except Exception as exc:  # the refusal is the point
            errors.append(exc)

    threads = [threading.Thread(target=drop, args=(p,)) for p in ("ava", "bo")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(crews.get_crew(crew["id"])["members"]) == 1
    assert len(errors) == 1


def test_removal_resolves_the_roster_name(fresh_db):
    """add_member resolves case-insensitively, so removal must too — one
    condition, one resolution rule."""
    users.ensure_user("ava")
    users.ensure_user("Bo")
    crew = crews.create_crew("Platform", actor="ava")
    crews.add_member(crew["id"], "bo", actor="ava")
    crews.remove_member(crew["id"], "BO", actor="ava")
    assert crews.get_crew(crew["id"])["members"] == [{"person": "ava", "role": "steward"}]


def test_assert_writable_is_the_phase_three_write_guard(fresh_db):
    """Without it a writer scopes a row into a crew they are not in — either
    injecting it into that crew's reading list, or hiding it from everyone
    including themselves."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    crew = crews.create_crew("Platform", actor="ava")

    assert crews.assert_writable(crew["id"], "ava") == crew["id"]
    with pytest.raises(db.NotFound):
        crews.assert_writable(crew["id"], "bo")
    with pytest.raises(db.NotFound):
        crews.assert_writable(999, "ava")

    # a deactivated crew keeps its old rows readable and takes no new ones
    crews.update_crew(crew["id"], active=False, actor="ava")
    with pytest.raises(ValueError, match="not active"):
        crews.assert_writable(crew["id"], "ava")
    assert crews.crews_of("ava") == [crew["id"]]


def test_a_member_removed_by_body_survives_a_slash_in_the_name(client, fresh_db):
    """A roster name may hold any character, and starlette matches no path
    segment containing `/` even percent-encoded — the person was addable and
    then unremovable by any request a client could form."""
    from app.services.api_keys import create_key

    users.ensure_user("ava")
    users.ensure_user("a/b")
    crew = crews.create_crew("Platform", actor="ava")
    hdr = {"Authorization": f"Bearer {create_key('ava', 'k')['key']}"}

    assert (
        client.post(
            f"/api/crews/{crew['id']}/members", json={"person": "a/b"}, headers=hdr
        ).status_code
        == 200
    )
    out = client.post(
        f"/api/crews/{crew['id']}/members/remove", json={"person": "a/b"}, headers=hdr
    )
    assert out.status_code == 200
    assert [m["person"] for m in out.json()["members"]] == ["ava"]


def test_membership_is_cached_per_request_and_dropped_on_a_write(fresh_db):
    """crews_of is on the hot path of every scoped read in phase 3. The cache
    must never outlive a membership change."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    crew = crews.create_crew("Platform", actor="ava")
    assert crews.crews_of("bo") == []
    crews.add_member(crew["id"], "bo", actor="ava")
    assert crews.crews_of("bo") == [crew["id"]]
    crews.remove_member(crew["id"], "bo", actor="ava")
    assert crews.crews_of("bo") == []


def test_crew_membership_records_provenance(fresh_db):
    """Membership decides what a person reads, so who put them here and
    through which path is the question asked after an incident."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    crew = crews.create_crew("Platform", actor="ava")
    crews.add_member(crew["id"], "bo", actor="ava")
    rows = fresh_db.query(
        "SELECT person, origin, created_by FROM crew_members WHERE crew_id = ? ORDER BY person",
        (crew["id"],),
    )
    assert rows == [
        {"person": "ava", "origin": "human", "created_by": "ava"},
        {"person": "bo", "origin": "human", "created_by": "ava"},
    ]


def test_a_deactivated_crew_takes_nobody_new(fresh_db):
    """crews_of keeps returning a deactivated crew so its rows stay readable, so
    adding a member to one would hand them every row already scoped to it —
    the opposite of what retiring means."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    crew = crews.create_crew("Platform", actor="ava")
    crews.add_member(crew["id"], "bo", actor="ava")
    crews.update_crew(crew["id"], active=False, actor="ava")

    users.ensure_user("cass")
    with pytest.raises(ValueError, match="not active"):
        crews.add_member(crew["id"], "cass", actor="ava")
    # an existing member's role still changes, so a deactivated crew can be tidied
    assert crews.add_member(crew["id"], "bo", role="steward", actor="ava")


def test_assert_writable_names_no_crew_to_a_non_member(fresh_db):
    """The not-active refusal names the crew. Checked before membership, it
    told a stranger that a crew by that id exists and what it is called."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    crew = crews.create_crew("Platform", actor="ava")
    crews.update_crew(crew["id"], active=False, actor="ava")
    with pytest.raises(db.NotFound) as caught:
        crews.assert_writable(crew["id"], "bo")
    assert "Platform" not in str(caught.value)


def test_a_key_holder_cannot_take_a_crew_from_its_steward(client, fresh_db):
    """_is_admin returns True for EVERY strong caller in a default
    trusted-header deployment (the scarcity fallback). Applied to crews that
    let any key holder make themselves steward and evict the one who was
    there — in three calls, on a boundary that decides what a person reads."""
    users.ensure_user("ava")
    crew = crews.create_crew("Platform", actor="ava")
    hdr = _key(client, "mallory")

    grab = client.post(
        f"/api/crews/{crew['id']}/members",
        json={"person": "mallory", "role": "steward"},
        headers=hdr,
    )
    assert grab.status_code == 403
    assert "steward" in grab.json()["detail"]
    assert (
        client.patch(f"/api/crews/{crew['id']}", json={"name": "Owned"}, headers=hdr).status_code
        == 403
    )
    assert crews.get_crew(crew["id"])["members"] == [{"person": "ava", "role": "steward"}]


def test_a_named_administrator_can_still_repair_a_crew(client, fresh_db, monkeypatch):
    """The strict test is not a lockout: an operator who wants an
    administrator to fix a crew whose only steward left names one."""
    from app import config

    monkeypatch.setattr(config, "ADMINS", ["boss"])
    users.ensure_user("ava")
    crew = crews.create_crew("Platform", actor="ava")
    hdr = _key(client, "boss")
    assert (
        client.post(
            f"/api/crews/{crew['id']}/members",
            json={"person": "boss", "role": "steward"},
            headers=hdr,
        ).status_code
        == 200
    )


def test_a_rename_merge_folds_crew_membership(fresh_db):
    """crew_members is PRIMARY KEY (crew_id, person), so the generic UPDATE in
    rename_user collided and answered 500. Two rows that are the same person
    almost always share a crew, so this is the normal merge, not an edge."""
    users.ensure_user("mira")
    users.ensure_user("ava")
    crew = crews.create_crew("Platform", actor="mira")
    crews.add_member(crew["id"], "ava", actor="mira")

    out = users.rename_user("mira", "ava", actor="system")
    assert out["merged"] is True
    # the steward row wins: a merge must not quietly demote the person
    assert crews.get_crew(crew["id"])["members"] == [{"person": "ava", "role": "steward"}]
    assert crews.crews_of("mira") == []
