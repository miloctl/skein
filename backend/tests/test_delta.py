"""Recent-window previews and explicit, source-independent review metadata."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, timedelta

import pytest

from app import db
from app.services import collab, delta, fieldguide, insights, promises, scope, users, wording


def _questions(count, monkeypatch):
    for i in range(count):
        collab.ask_question(f"Question {i}", asked_by="ava", actor="ava")
    db.execute(
        "UPDATE questions SET created_at = ?",
        (db.local_midnight_utc(db.today() - timedelta(days=8)),),
    )
    monkeypatch.setattr(insights, "RULES", (insights._r_question_aging,))
    return insights.run_findings(actor="ava")["findings"]


def test_get_uses_seven_team_dates_and_ignores_legacy_mark(client, monkeypatch):
    users.ensure_user("ava")
    monkeypatch.setattr(db, "today", lambda: date(2026, 9, 20))
    promises.add_promise("inside", due_date="2026-09-13", actor="ava")
    promises.add_promise("outside", due_date="2026-09-12", actor="ava")
    db.execute(
        "INSERT INTO app_settings (key,value,updated_at) VALUES (?,?,?)",
        ("delta_seen:ava", db.now(), db.now()),
    )
    before = db.query("SELECT * FROM app_settings ORDER BY key")
    first = client.get("/api/delta?mark=true", headers={"X-User": "ava"}).json()
    second = client.get("/api/delta", headers={"X-User": "ava"}).json()
    assert first == second
    assert first["window_start"] == "2026-09-14"
    assert first["window_end"] == "2026-09-20"
    assert first["since"] == db.local_midnight_utc(date(2026, 9, 14))
    assert len(first["items"]) == 1
    assert len(first["snapshot_id"]) == 64
    assert first["review_revision"] == 0
    assert first["truncated"] is False
    assert db.query("SELECT * FROM app_settings ORDER BY key") == before


def test_findings_are_new_structured_and_deduplicated_before_cap(fresh_db, monkeypatch):
    users.ensure_user("ava")
    rows = _questions(51, monkeypatch)
    # Re-firing in a second ISO week is a real rule-engine output.
    monkeypatch.setattr(insights, "_week", lambda: "2099-W01")
    insights.run_findings(actor="ava")
    out = delta.brief("ava", _viewer())
    findings = [i for i in out["items"] if i["kind"] == "finding_new"]
    assert len(findings) == 50
    assert len({i["entity_id"] for i in findings}) == 50
    assert {i["entity_id"] for i in findings} == {r["id"] for r in rows[:50]}
    assert out["truncated"] is True
    assert all(
        i["rule_id"] == "question_aging" and i["severity"] == "low" and i["direction"] == "new"
        for i in findings
    )


def test_resolved_subject_reappears_when_the_rule_refires(fresh_db, monkeypatch):
    users.ensure_user("ava")
    first = _questions(1, monkeypatch)[0]
    insights.disposition_finding(first["id"], "resolved", "Answered it", actor="ava")
    assert delta.brief("ava", _viewer())["items"] == []
    # insights._suppressed: resolved never quiets a subject, so the next week
    # mints a new row and the summary must show it
    monkeypatch.setattr(insights, "_week", lambda: "2099-W01")
    refired = insights.run_findings(actor="ava")["findings"]
    assert [r["id"] for r in refired] != [first["id"]]
    out = delta.brief("ava", _viewer())
    assert [i["entity_id"] for i in out["items"]] == [refired[0]["id"]]


def test_old_subjects_do_not_spend_finding_cap(fresh_db, monkeypatch):
    users.ensure_user("ava")
    _questions(55, monkeypatch)
    db.execute(
        "UPDATE findings SET created_at = ?",
        (db.local_midnight_utc(db.today() - timedelta(days=7)),),
    )
    monkeypatch.setattr(insights, "_week", lambda: "2099-W01")
    insights.run_findings(actor="ava")
    collab.ask_question("New eligible question", asked_by="ava", actor="ava")
    db.execute(
        "UPDATE questions SET created_at = ?",
        (db.local_midnight_utc(db.today() - timedelta(days=8)),),
    )
    new = insights.run_findings(actor="ava")["findings"]
    out = delta.brief("ava", _viewer())
    assert [i["entity_id"] for i in out["items"]] == [new[0]["id"]]
    assert out["truncated"] is False


def test_window_label_alone_does_not_change_fingerprint(fresh_db, monkeypatch):
    users.ensure_user("ava")
    day = db.today()
    first = delta.brief("ava", _viewer())
    monkeypatch.setattr(db, "today", lambda: day + timedelta(days=1))
    second = delta.brief("ava", _viewer())
    assert first["window_start"] != second["window_start"]
    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["snapshot_id"] != delta.brief("mira", _viewer("mira"))["snapshot_id"]


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


def test_system_findings_never_reach_the_brief(fresh_db, monkeypatch):
    """The brief opens My Day, and a new joiner's first screen was eleven
    findings about cron jobs and token spend they could not act on. The
    meeting queue's audience set applies here too; the rules keep /insights."""
    from app import config
    from app.services import delta, insights, users

    users.ensure_user("reader")
    monkeypatch.setattr(config, "SCHEDULER_ENABLED", True)
    fresh_db.execute(
        "INSERT INTO job_outcomes (job, status, detail, duration_ms, created_at)"
        " VALUES ('daily-digest', 'ok', '', 0, '2020-01-01T00:00:00+00:00')"
    )
    insights.run_findings(actor="tester")
    assert any(f["rule_id"] == "job_stale" for f in insights.list_findings())

    out = delta.brief("reader")
    assert not [
        i for i in out["items"] if i["kind"] == "finding_new" and "Scheduled job" in i["headline"]
    ]


def test_legacy_mark_is_a_repeatable_pure_preview(client, fresh_db):
    """Only explicit acknowledgment records a reviewed summary."""
    from app.services import promises, users

    users.ensure_user("ava")
    promises.add_promise("send it", to_whom="acme", due_date=_broke_yesterday(), actor="ava")

    first = delta.brief("ava", _viewer())
    assert first["items"], "a broken promise is news the first time"
    assert first["quiet"] is False

    second = delta.brief("ava", _viewer())
    assert second["items"] == first["items"]
    assert second["snapshot_id"] == first["snapshot_id"]
    assert second["reviewed"] is False
    assert fresh_db.query("SELECT * FROM app_settings WHERE key LIKE 'delta_%'") == []


def test_legacy_mark_does_not_consume_another_readers_summary(client, fresh_db):
    from app.services import promises, users

    for n in ("ava", "mira"):
        users.ensure_user(n)
    promises.add_promise("send it", to_whom="acme", due_date=_broke_yesterday(), actor="ava")

    delta.brief("ava", _viewer("ava"))
    # mira has read nothing, so the same fact is still news to her
    assert delta.brief("mira", _viewer("mira"))["items"]


def test_a_preview_does_not_consume_the_brief(client, fresh_db):
    """Reading a summary never records its acknowledgment."""
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
    delta.brief("ava", _viewer())
    # the same rule and subject fires again THIS week (a literal week ages out
    # of list_findings' four-week window, and the test then passes with the
    # row unseen, pinning nothing)
    from app.services import insights

    db.execute(
        "INSERT INTO findings (rule_id, severity, subject, message, receipt, week, created_at)"
        " VALUES ('aging_wip', 'medium', 'task-1', 'same news again', '{}', ?, ?)",
        (insights._week(), db.now()),
    )
    assert not any(
        "same news again" in i["headline"] for i in delta.brief("ava", _viewer())["items"]
    )


def test_a_new_low_finding_survives_a_crowded_window(client, fresh_db):
    """The cap has to apply to the rows that QUALIFY, not to the whole table.

    Read through `list_findings`, the LIMIT lands before the since and
    disposition filters and its ordering is week then severity — so a hundred
    already-seen high findings from this week push the one new low finding off
    the end, and the brief reports "quiet" about a window that had news.
    """
    from app import db
    from app.services import users

    users.ensure_user("ava")
    week = db.local_day(db.now())[:4] + "-W33"
    old = "2020-01-01T00:00:00+00:00"
    # crowd the window with rows the reader has already been told about, all
    # sorting AHEAD of the new one on both keys the old query ordered by
    for i in range(120):
        db.execute(
            "INSERT INTO findings (rule_id, severity, subject, message, receipt, week, created_at)"
            " VALUES ('aging_wip', 'high', ?, 'seen already', '{}', ?, ?)",
            (f"task-{i}", week, old),
        )
    delta.brief("ava", _viewer())

    db.execute(
        "INSERT INTO findings (rule_id, severity, subject, message, receipt, week, created_at)"
        " VALUES ('promise_due', 'low', 'promise-9', 'the quiet new one', '{}', ?, ?)",
        (week, db.now()),
    )
    out = delta.brief("ava", _viewer())
    assert any("the quiet new one" in i["headline"] for i in out["items"])
    assert out["quiet"] is False


def test_every_item_carries_a_resolvable_receipt(client, fresh_db):
    from app.services import promises, users

    users.ensure_user("ava")
    promises.add_promise("send it", to_whom="acme", due_date=_broke_yesterday(), actor="ava")
    for item in delta.brief("ava", _viewer())["items"]:
        assert item["receipts"], f"{item['kind']} carries no receipt"
        assert any(r["refs"] for r in item["receipts"])


def _preview(client, user="ava"):
    response = client.get("/api/delta", headers={"X-User": user})
    assert response.status_code == 200, response.text
    return response.json()


def _ack(client, summary, user="ava"):
    return client.post(
        "/api/delta/ack",
        headers={"X-User": user},
        json={
            "snapshot_id": summary["snapshot_id"],
            "review_revision": summary["review_revision"],
        },
    )


def test_ack_is_explicit_idempotent_bounded_and_self_only(client):
    from app.services import activity, briefing

    users.ensure_user("ava")
    users.ensure_user("mira")
    promises.add_promise(
        "Private source content",
        to_whom="Secret stakeholder",
        due_date=_broke_yesterday(),
        actor="ava",
    )
    first = _preview(client)
    before = db.query("SELECT * FROM activity ORDER BY id")
    response = _ack(client, first)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "snapshot_id": first["snapshot_id"],
        "review_revision": 1,
        "reviewed": True,
    }
    stored = db.query_row("SELECT value FROM app_settings WHERE key = 'delta_reviewed:ava'")[
        "value"
    ]
    assert json.loads(stored) == {
        "version": 1,
        "snapshot_id": first["snapshot_id"],
        "revision": 1,
        "origin": "human",
        "created_by": "ava",
    }
    assert len(stored) < 300
    assert len(db.query("SELECT * FROM activity")) == len(before) + 1
    row = db.query_row("SELECT * FROM activity WHERE action = 'review_delta'")
    assert row["actor"] == "ava" and row["detail"] == "origin=human"
    assert activity.verify_chain()["ok"]
    assert any(e["action"] == "review_delta" for e in activity.feed("ava")["entries"])
    for other in (
        activity.feed("mira")["entries"],
        collab.recent_activity("mira"),
        briefing.my_day("mira")["team"]["recent_activity"],
    ):
        assert not any(e["action"] == "review_delta" for e in other)
    saved = db.query("SELECT * FROM app_settings WHERE key LIKE 'delta_%'")
    assert _ack(client, first).json() == response.json()
    assert db.query("SELECT * FROM app_settings WHERE key LIKE 'delta_%'") == saved
    assert len(db.query("SELECT * FROM activity")) == len(before) + 1
    assert _preview(client)["reviewed"] is True
    assert _preview(client, "mira")["reviewed"] is False
    assert _preview(client, "mira")["review_revision"] == 0


def test_changed_batch_and_stale_revision_do_not_write(client):
    users.ensure_user("ava")
    first = _preview(client)
    promises.add_promise("new", due_date=_broke_yesterday(), actor="ava")
    assert _ack(client, first).status_code == 409
    assert not db.query("SELECT * FROM app_settings WHERE key LIKE 'delta_%'")
    second = _preview(client)
    assert _ack(client, second).status_code == 200
    promises.add_promise("another", due_date=_broke_yesterday(), actor="ava")
    third = _preview(client)
    assert third["reviewed"] is False
    stale = {**third, "review_revision": 0}
    assert _ack(client, stale).status_code == 409
    assert _ack(client, third).status_code == 200
    assert _ack(client, second).status_code == 409


def test_feature_adoption_findings_fold_into_one_row_outside_the_cap(client):
    """The adoption rule files one finding for each unused field-guide card, all
    at once on a new install. Each one spent a slot of the 50-row cap, so the
    first week's summary was incomplete and could not be marked reviewed."""
    users.ensure_user("ava")
    collab.ask_question("Who owns the rollback?", asked_by="ava", actor="ava")
    db.execute(
        "UPDATE questions SET created_at = ?",
        (db.local_midnight_utc(db.today() - timedelta(days=8)),),
    )
    minted = insights.run_findings(actor="ava")["findings"]
    adoption = sorted(
        (f for f in minted if f["rule_id"] == "feature_unadopted"), key=lambda f: f["id"]
    )
    assert len(adoption) >= 2, "the field guide has cards past their grace window"

    summary = _preview(client)
    rows = [i for i in summary["items"] if i.get("rule_id") == "feature_unadopted"]
    assert len(rows) == 1
    assert rows[0]["severity"] == "low"
    assert rows[0]["headline"] == (
        f"{len(adoption)} field-guide features have no team-wide first use"
        " 30 days after they entered the field guide."
    )
    features = {k["id"]: k["feature"] for k in fieldguide.registry()}
    assert [r["message"] for r in rows[0]["receipts"]] == [
        f"finding #{f['id']} (low): {wording.quoted(features[f['subject']])}" for f in adoption
    ]
    assert all(
        r["refs"] == [{"entity": "finding", "id": f["id"]}]
        for r, f in zip(rows[0]["receipts"], adoption, strict=True)
    )
    assert any(i.get("rule_id") == "question_aging" for i in summary["items"])
    assert not summary["truncated"]
    assert _ack(client, summary).status_code == 200

    # The row speaks for its set, so a changed set is a new batch to review.
    insights.disposition_finding(adoption[0]["id"], "dismissed", actor="ava")
    changed = _preview(client)
    assert changed["snapshot_id"] != summary["snapshot_id"]
    assert not changed["reviewed"]


def test_incomplete_summary_cannot_be_reviewed(client, monkeypatch):
    users.ensure_user("ava")
    _questions(51, monkeypatch)
    summary = _preview(client)
    assert summary["truncated"]
    assert _ack(client, summary).status_code == 400
    assert not db.query("SELECT * FROM app_settings WHERE key LIKE 'delta_%'")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"snapshot_id": "x" * 64, "review_revision": 0},
        {"snapshot_id": "a" * 63, "review_revision": 0},
        {"snapshot_id": "a" * 64, "review_revision": -1},
        {"snapshot_id": "a" * 64, "review_revision": True},
        {"snapshot_id": "a" * 64, "review_revision": 0, "user": "mira"},
        {"snapshot_id": "a" * 64, "review_revision": 0, "since": "2026-01-01"},
    ],
)
def test_ack_accepts_only_fingerprint_and_revision(client, body):
    response = client.post("/api/delta/ack", json=body)
    assert response.status_code == 422
    assert not db.query("SELECT * FROM app_settings WHERE key LIKE 'delta_%'")


def test_ack_uses_write_rate_cap(client, monkeypatch):
    from app import ratelimit

    summary = _preview(client)
    checks = []

    def deny(bucket, actor):
        checks.append((bucket, actor))
        raise ratelimit.RateLimited("Rate cap reached. Retry later.", 2)

    monkeypatch.setattr(ratelimit, "check", deny)
    response = _ack(client, summary)
    assert checks == [("write", "ava")]
    assert response.status_code == 429
    assert response.headers["Retry-After"]
    assert not db.query("SELECT * FROM app_settings WHERE key LIKE 'delta_%'")


def test_commit_between_comparison_and_metadata_save_returns_next_time(client, monkeypatch):
    users.ensure_user("ava")
    summary = _preview(client)
    original = db.read_transaction
    committed = []

    @contextmanager
    def gap():
        with original():
            yield
        # A second connection commits after the read snapshot has ended.
        with ThreadPoolExecutor(max_workers=1) as pool:
            committed.append(
                pool.submit(
                    promises.add_promise, "gap", due_date=_broke_yesterday(), actor="ava"
                ).result()
            )

    monkeypatch.setattr(db, "read_transaction", gap)
    response = _ack(client, summary)
    assert response.status_code == 200, response.text
    monkeypatch.setattr(db, "read_transaction", original)
    next_summary = _preview(client)
    assert next_summary["reviewed"] is False
    assert next_summary["snapshot_id"] != summary["snapshot_id"]
    assert any(i["entity_id"] == committed[0]["id"] for i in next_summary["items"])


def test_older_timestamp_late_commit_is_not_consumed(client, monkeypatch):
    users.ensure_user("ava")
    from threading import Event

    inserted, release = Event(), Event()
    original_now = db.now()
    monkeypatch.setattr(db, "now", lambda: db.local_midnight_utc(db.today() - timedelta(days=2)))

    def late_writer():
        with db.transaction():
            result = collab.ask_question("late", asked_by="ava", actor="ava")
            db.execute(
                "UPDATE questions SET created_at = ? WHERE id = ?",
                (db.local_midnight_utc(db.today() - timedelta(days=8)), result["id"]),
            )
            monkeypatch.setattr(insights, "RULES", (insights._r_question_aging,))
            findings = insights.run_findings(actor="ava")["findings"]
            inserted.set()
            assert release.wait(10)
        return findings

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(late_writer)
        assert inserted.wait(10)
        monkeypatch.setattr(db, "now", lambda: original_now)
        try:
            summary = _preview(client)
            assert _ack(client, summary).status_code == 200
        finally:
            release.set()
        rows = pending.result()
    next_summary = _preview(client)
    assert next_summary["reviewed"] is False
    assert any(i["entity_id"] == rows[0]["id"] for i in next_summary["items"])


def test_same_second_finding_after_ack_changes_summary(client, monkeypatch):
    users.ensure_user("ava")
    stamp = db.now()
    monkeypatch.setattr(db, "now", lambda: stamp)
    _questions(1, monkeypatch)
    summary = _preview(client)
    assert _ack(client, summary).status_code == 200
    _questions(1, monkeypatch)
    changed = _preview(client)
    assert len(changed["items"]) == 2
    assert changed["snapshot_id"] != summary["snapshot_id"]
    assert changed["reviewed"] is False


def test_chat_delta_is_non_consuming(client):
    from app.agents import commands

    users.ensure_user("ava")
    promises.add_promise("send", due_date=_broke_yesterday(), actor="ava")

    async def read():
        return [e async for e in commands.dispatch("/delta", "ava", _viewer())]

    before = db.query("SELECT * FROM app_settings ORDER BY key")
    first = asyncio.run(read())
    assert first == asyncio.run(read())
    text = str(first)
    assert "Recent changes" in text
    assert "last looked" not in text
    assert db.query("SELECT * FROM app_settings ORDER BY key") == before


@pytest.mark.parametrize("action", ["skein.rest.post.delta.ack", "skein.rest.get.delta"])
@pytest.mark.parametrize("direct_denial", [True, False])
def test_ack_policy_denial_writes_no_metadata_or_activity(
    client, monkeypatch, action, direct_denial
):
    from dataclasses import replace

    from app.extensions.contracts import PolicyContribution
    from app.extensions.policy import PolicyDecision, PolicyEffect

    users.ensure_user("ava")
    summary = _preview(client)
    before = db.query("SELECT * FROM activity ORDER BY id")

    class Rule:
        skein_policy_actions = (action,)

        def __call__(self, request):
            if direct_denial:
                return PolicyDecision(PolicyEffect.DENY, ("Denied",))
            return None

    registry = client.app.state.skein_registry
    monkeypatch.setattr(
        client.app.state,
        "skein_registry",
        replace(registry, policies=(PolicyContribution("test.delta", Rule()),)),
    )
    response = _ack(client, summary)
    assert response.status_code == 403, response.text
    assert not db.query("SELECT * FROM app_settings WHERE key LIKE 'delta_%'")
    assert db.query("SELECT * FROM activity ORDER BY id") == before


def test_concurrent_exact_acknowledgments_write_once(client, monkeypatch):
    from threading import Barrier

    users.ensure_user("ava")
    summary = _preview(client)
    barrier = Barrier(2)
    original = delta.acknowledge

    def together(*args):
        assert not db.in_transaction()
        barrier.wait(10)
        return original(*args)

    monkeypatch.setattr(delta, "acknowledge", together)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_ack, client, summary) for _ in range(2)]
        responses = [future.result() for future in futures]
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert len(db.query("SELECT * FROM activity WHERE action = 'review_delta'")) == 1
    assert _preview(client)["review_revision"] == 1


def test_ack_comparison_keeps_one_snapshot_during_source_commit(client, monkeypatch):
    users.ensure_user("ava")
    summary = _preview(client)
    original = db.query
    committed = []

    def interleave(sql, params=()):
        result = original(sql, params)
        if "DISTINCT ON (f.rule_id, f.subject)" in sql and not committed:
            # Commit after the finding scan, before the promise scan. A joined
            # READ COMMITTED transaction would reject the exact viewed summary.
            with ThreadPoolExecutor(max_workers=1) as pool:
                committed.append(
                    pool.submit(
                        promises.add_promise,
                        "during comparison",
                        due_date=_broke_yesterday(),
                        actor="ava",
                    ).result()
                )
        return result

    monkeypatch.setattr(db, "query", interleave)
    response = _ack(client, summary)
    assert response.status_code == 200, response.text
    assert _preview(client)["reviewed"] is False


def test_team_day_calculated_once_and_window_ages_out(client, monkeypatch):
    from zoneinfo import ZoneInfo

    from app import config

    users.ensure_user("ava")
    monkeypatch.setattr(config, "TZ", ZoneInfo("America/New_York"))
    # This window straddles the daylight-saving transition: seven dates are
    # not seven 24-hour durations.
    dates = []

    def day():
        dates.append(True)
        return date(2026, 3, 10) if len(dates) == 1 else date(2026, 3, 11)

    promises.add_promise("edge", due_date="2026-03-03", actor="ava")
    monkeypatch.setattr(db, "today", day)
    result = delta.brief("ava", _viewer())
    assert len(dates) == 1
    assert result["window_start"] == "2026-03-04"
    assert result["window_end"] == "2026-03-10"
    assert result["since"] == "2026-03-04T05:00:00+00:00"
    assert len(result["items"]) == 1
    assert delta.brief("ava", _viewer())["items"] == []


def test_health_comparison_repeats_then_changes(client, monkeypatch):
    from app.services import adoption, engagements, work

    users.ensure_user("ava")
    today = db.today()
    engagement = engagements.create_engagement("Health", actor="ava")
    monkeypatch.setattr(db, "today", lambda: today - timedelta(days=6))
    adoption.snapshot_health()
    monkeypatch.setattr(db, "today", lambda: today)
    milestone = work.create_milestone("Late milestone", due_date=_broke_yesterday(), actor="ava")
    work.update_milestone(milestone["id"], engagement_id=engagement["id"], actor="ava")
    first = _preview(client)
    assert first["items"][0]["direction"] == "worse"
    assert _ack(client, first).status_code == 200
    assert _preview(client)["reviewed"] is True
    work.update_milestone(milestone["id"], status="done", actor="ava")
    next_summary = _preview(client)
    assert next_summary["items"] == []
    assert next_summary["reviewed"] is False


def test_duplicate_subjects_do_not_fill_or_truncate_preview(fresh_db, monkeypatch):
    users.ensure_user("ava")
    first = _questions(26, monkeypatch)
    monkeypatch.setattr(insights, "_week", lambda: "2099-W01")
    assert len(insights.run_findings(actor="ava")["findings"]) == 26
    result = delta.brief("ava", _viewer())
    assert [i["entity_id"] for i in result["items"]] == [r["id"] for r in first]
    assert result["truncated"] is False


def test_disposition_subjects_do_not_spend_cap(fresh_db, monkeypatch):
    users.ensure_user("ava")
    findings = _questions(51, monkeypatch)
    for row in findings[:50]:
        insights.disposition_finding(row["id"], "dismissed", "Not relevant", actor="ava")
    result = delta.brief("ava", _viewer())
    assert [i["entity_id"] for i in result["items"]] == [findings[-1]["id"]]
    assert result["truncated"] is False


def test_higher_severity_survives_low_finding_burst(fresh_db, monkeypatch):
    from app.services import engagements

    users.ensure_user("ava")
    _questions(50, monkeypatch)
    engagements.create_engagement(
        "Overdue experiment",
        kind="experiment",
        timebox_end=_broke_yesterday(),
        kill_criteria="No useful result",
        actor="ava",
    )
    monkeypatch.setattr(insights, "RULES", (insights._r_experiment_overdue,))
    rows = insights.run_findings(actor="ava")["findings"]
    assert len(rows) == 1
    result = delta.brief("ava", _viewer())
    findings = [i for i in result["items"] if i["kind"] == "finding_new"]
    assert findings[0]["entity_id"] == rows[0]["id"]
    assert result["truncated"] is True


def test_acceptance_is_sponsored_scoped_and_keeps_receipt(client):
    from conftest import _strong

    from app.services import crews, delegation, work

    users.ensure_user("ava")
    users.ensure_user("mira")
    crew = crews.create_crew("Read summary", actor="ava")
    task = work.create_task(
        "Crew-only acceptance", visibility="crew", crew_id=crew["id"], actor="ava"
    )
    delegation.delegate_task(task["id"], "scout", "ava", actor="ava")
    delegation.claim_task(task["id"], actor="scout")
    proposal = delegation.submit_completion(task["id"], "Ready", actor="scout")
    assert _preview(client)["items"] == []
    response = client.get("/api/delta", headers=_strong(client, "ava"))
    assert response.status_code == 200
    result = response.json()
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["kind"] == "acceptance_waiting"
    assert item["entity_id"] == proposal["proposal_id"]
    assert item["link"] == f"/review?id={proposal['proposal_id']}"
    assert {r["entity"] for r in item["receipts"][0]["refs"]} == {"proposal", "task"}
    assert delta.brief("mira", _viewer("mira"))["items"] == []
    assert not db.query("SELECT * FROM app_settings WHERE key LIKE 'delta_%'")


def test_received_and_private_promises_are_not_team_changes(fresh_db):
    users.ensure_user("ava")
    promises.add_promise("received", due_date=_broke_yesterday(), direction="received", actor="ava")
    promises.add_promise("private", due_date=_broke_yesterday(), visibility="private", actor="ava")
    assert delta.brief("ava", _viewer())["items"] == []
