"""Why the trust card cannot fill, when it cannot.

Two deployment settings make a promotion streak structurally unreachable, and
under either one the card's "No reviewed proposals yet" reads as "wait" when
the truth is "change a setting". The card cannot say that on its own — both
facts live on the server."""

from app import config
from app.services import delegation


def test_the_gate_being_off_is_named_with_its_fix(fresh_db, monkeypatch):
    """With the gate off, tools/_gate.py takes the direct branch: the write
    lands and no proposal — so no verdict — is ever created."""
    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    msg = delegation.trust_blocked()
    assert "review gate is off" in msg
    assert "SKEIN_AGENT_REVIEW=1" in msg  # names the fix, not only the fault


def test_silence_when_the_gate_is_on_and_nothing_is_settled(fresh_db, monkeypatch):
    """An empty ledger is a genuine "not yet". Warning there would train the
    reader to ignore the line that matters."""
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    assert delegation.trust_blocked() == ""


def _settle(status="approved", strong=0, override=0):
    from app import db

    db.execute(
        "INSERT INTO pending_changes (entity, entity_id, action, payload, summary,"
        " proposed_by, status, reviewed_strong, reviewed_override, created_at)"
        " VALUES ('task', 1, 'create', '{}', 's', 'agent', ?, ?, ?, '2026-08-08T00:00:00+00:00')",
        (status, strong, override),
    )


def test_weak_verdicts_are_reported_as_not_counting(fresh_db, monkeypatch):
    """The subtler dead end: rows exist, the card fills, and the streak stays
    zero forever because no verdict carried a key."""
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    _settle(strong=0)
    _settle(strong=0)
    msg = delegation.trust_blocked()
    assert "Skein recorded 2 verdicts." in msg  # plural agrees with the number
    assert "personal API key" in msg


def test_the_count_agrees_with_its_noun(fresh_db, monkeypatch):
    """Sentence-form text computes its plurals (CLAUDE.md wording). The
    earlier wording put two verbs in one sentence and agreed only the first:
    "1 verdict is recorded and none carry a personal API key"."""
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    _settle(strong=0)
    msg = delegation.trust_blocked()
    assert "Skein recorded 1 verdict." in msg
    assert "verdicts" not in msg.split(".")[0]


def test_one_strong_verdict_clears_the_warning(fresh_db, monkeypatch):
    """The warning is about a structural dead end. One key-authenticated
    verdict proves the path works, so the line must stop."""
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    _settle(strong=0)
    _settle(strong=1)
    assert delegation.trust_blocked() == ""


def test_an_override_verdict_does_not_count_as_strong(fresh_db, monkeypatch):
    """trust_scores excludes reviewed_override from streaks, so this must
    exclude it too — otherwise the card says trust can accrue and it cannot."""
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    _settle(strong=1, override=1)
    assert "personal API key" in delegation.trust_blocked()


def test_the_status_endpoint_carries_it(client, monkeypatch):
    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    body = client.get("/api/agents/status").json()
    assert "review gate is off" in body["trust_blocked"]
