"""What changed since the reader last looked — and nothing they already read.

The digest lists this week's findings every morning and health states its
colour on every load. Both are correct, and both are why a reader skims: on day
three the fourth identical list is noise carrying one new line.
"""

from app.services import delta, scope


def _viewer(name: str = "ava") -> scope.Viewer:
    return scope.Viewer(name, True)


def _broke_yesterday() -> str:
    """A due date inside the window this brief reads.

    A promise that broke in 2020 is not news today, which is the whole point —
    so a fixture with a distant date pins nothing and passes against a brief
    that reports every overdue promise forever.
    """
    from datetime import timedelta

    from app import db

    return (db.today() - timedelta(days=1)).isoformat()


def test_reading_twice_leaves_nothing_the_second_time(client, fresh_db):
    """The honest answer, and the one that keeps the surface worth opening."""
    from app.services import promises, users

    users.ensure_user("ava")
    promises.add_promise("send it", to_whom="acme", due_date=_broke_yesterday(), actor="ava")

    first = delta.brief("ava", _viewer(), mark=True)
    assert first["items"], "a broken promise is news the first time"
    assert first["quiet"] is False

    second = delta.brief("ava", _viewer(), mark=True)
    assert second["items"] == []
    assert second["quiet"] is True


def test_two_readers_keep_their_own_marks(client, fresh_db):
    from app.services import promises, users

    for n in ("ava", "mira"):
        users.ensure_user(n)
    promises.add_promise("send it", to_whom="acme", due_date=_broke_yesterday(), actor="ava")

    delta.brief("ava", _viewer("ava"), mark=True)
    # mira has read nothing, so the same fact is still news to her
    assert delta.brief("mira", _viewer("mira"))["items"]


def test_a_preview_does_not_consume_the_brief(client, fresh_db):
    """The chat command shows it; only the surface that DISPLAYS it marks it."""
    from app.services import promises, users

    users.ensure_user("ava")
    promises.add_promise("send it", to_whom="acme", due_date=_broke_yesterday(), actor="ava")
    assert delta.brief("ava", _viewer())["items"]
    assert delta.brief("ava", _viewer())["items"], "a preview must not consume"


def test_a_first_green_score_is_not_news(client, fresh_db):
    """Every engagement is unscored until the daily snapshot has run once, so
    without this the first brief is a list of every healthy engagement."""
    from app.services import engagements, users

    users.ensure_user("ava")
    engagements.create_engagement("Calm", project_class="prototype", actor="ava")
    assert not any(i["kind"] == "health_moved" for i in delta.brief("ava", _viewer())["items"])


def test_a_finding_that_already_fired_is_not_new(client, fresh_db):
    """A rule re-firing weekly on the same subject is the same news. The
    (rule, subject, week) key makes a repeat a different ROW, so the comparison
    is on the subject."""
    from app import db
    from app.services import users

    users.ensure_user("ava")
    old = "2020-01-01T00:00:00+00:00"
    db.execute(
        "INSERT INTO findings (rule_id, severity, subject, message, receipt, week, created_at)"
        " VALUES ('aging_wip', 'medium', 'task-1', 'old news', '{}', '2020-W01', ?)",
        (old,),
    )
    delta.brief("ava", _viewer(), mark=True)
    # the same rule and subject fires again in a later week
    db.execute(
        "INSERT INTO findings (rule_id, severity, subject, message, receipt, week, created_at)"
        " VALUES ('aging_wip', 'medium', 'task-1', 'same news again', '{}', '2026-W33', ?)",
        (db.now(),),
    )
    assert not any(
        "same news again" in i["headline"] for i in delta.brief("ava", _viewer())["items"]
    )


def test_every_item_carries_a_resolvable_receipt(client, fresh_db):
    from app.services import promises, users

    users.ensure_user("ava")
    promises.add_promise("send it", to_whom="acme", due_date=_broke_yesterday(), actor="ava")
    for item in delta.brief("ava", _viewer())["items"]:
        assert item["receipts"], f"{item['kind']} carries no receipt"
        assert any(r["refs"] for r in item["receipts"])
