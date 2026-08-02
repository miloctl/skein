"""Per-user themes: storage, validation, and what survives a rename or a merge."""

import pytest


def test_theme_is_per_user(client):
    blob_a = '{"pack":"ledger","colorway":"madder"}'
    blob_b = '{"pack":"phosphor","colorway":"verdigris"}'
    client.post("/api/users/theme", json={"theme": blob_a})
    client.post("/api/users/theme", json={"theme": blob_b}, headers={"X-User": "other"})
    assert client.get("/api/users/theme").json()["theme"] == blob_a
    assert client.get("/api/users/theme", headers={"X-User": "other"}).json()["theme"] == blob_b


def test_theme_unknown_user_gets_empty(client):
    assert client.get("/api/users/theme", headers={"X-User": "never-seen"}).json()["theme"] == ""


def test_theme_service_rejects_oversize_and_non_object(fresh_db):
    from app.services import users

    with pytest.raises(ValueError, match="too large"):
        users.set_theme("tester", "{" + '"pack":"' + "x" * 400 + '"}')
    with pytest.raises(ValueError, match="unknown keys"):
        users.set_theme("tester", '["pack"]')  # JSON but not an object


def test_theme_survives_rename_but_merge_keeps_target(fresh_db):
    from app.services import users

    users.set_theme("Mira", '{"pack":"atelier"}')
    users.rename_user("Mira", "Mira K")
    assert users.get_theme("Mira K") == '{"pack":"atelier"}'
    # merge: the target row's theme wins; the source row (and its theme) is deleted
    users.ensure_user("mira")
    users.set_theme("mira", '{"pack":"ledger"}')
    out = users.rename_user("Mira K", "mira")
    assert out["merged"] is True
    assert users.get_theme("mira") == '{"pack":"ledger"}'  # atelier is gone (documented loss)
    assert users.get_theme("Mira K") == ""
