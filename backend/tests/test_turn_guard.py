"""A chat turn that files nothing, on a message that asked to file something,
must say so. Silence reads as success, and that is the failure being closed."""

import pytest

from app.agents import turn_guard


@pytest.mark.parametrize(
    "message",
    [
        "todo: ship the migration",
        "q: where do we log retries?",
        "decision: we are going with SQLite",
        "promised: the readout by Friday",
        "blocked: waiting on the vendor key",
        "req: a dashboard for support",
    ],
)
def test_a_filing_request_that_wrote_nothing_is_reported(message):
    note = turn_guard.unfiled(message, wrote=False)
    assert note is not None
    assert note["kind"] == "nothing"
    assert "⌘K" in note["detail"]


@pytest.mark.parametrize(
    "message",
    [
        "what is the status of the migration?",
        "can you summarize this week?",
        "who is blocked on the vendor key?",
        "we decided to use SQLite last quarter, right?",
        "fix the flaky test",
        "thanks",
    ],
)
def test_ordinary_conversation_never_fires_the_guard(message):
    """The content heuristics in capture.PATTERNS classify several of these as
    filing requests. The guard uses prefixes ONLY, and this is why: a guard
    that nags on every question mark gets ignored, and then it protects
    nothing."""
    assert turn_guard.unfiled(message, wrote=False) is None


def test_any_receipt_at_all_silences_the_guard():
    """refused and failed already told the user the truth. Only total silence
    is the gap this closes."""
    assert turn_guard.unfiled("todo: ship it", wrote=True) is None


def test_the_note_names_the_record_type_it_would_have_filed():
    assert turn_guard.unfiled("q: who owns this?", wrote=False)["entity"] == "question"
    assert turn_guard.unfiled("todo: ship it", wrote=False)["entity"] == "task"


def test_reprompt_is_off_by_default_and_never_on_mock(monkeypatch):
    from app import config

    assert not turn_guard.reprompt_enabled()
    monkeypatch.setattr(config, "TURN_GUARD", True)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "mock")
    assert not turn_guard.reprompt_enabled()  # a mock re-prompt buys nothing
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    assert turn_guard.reprompt_enabled()


def test_the_keyless_path_reports_its_own_write(client):
    """The mock captures through capture.capture, not the tool gate, so nothing
    else would report the write. Before this, the one provider guaranteed to
    work end-to-end was the one where the UI could not state what happened to
    your data — and the guard would have called a real capture 'nothing filed'."""
    body = client.post(
        "/api/chat", json={"thread_id": "t-mock", "message": "todo: ship the guard"}
    ).text
    assert '"kind": "wrote"' in body
    assert '"kind": "nothing"' not in body


def test_the_guard_stays_quiet_on_ordinary_chat(client):
    body = client.post(
        "/api/chat", json={"thread_id": "t-plain", "message": "hmm todo-ish thoughts"}
    ).text
    assert '"kind": "nothing"' not in body


def test_the_guard_fires_in_the_stream_when_the_turn_writes_nothing(client, monkeypatch):
    """A silent turn is simulated by making the agent yield only text — the
    condition the guard exists for, which no provider is guaranteed to produce
    on demand."""

    class Silent:
        async def stream_async(self, message):
            yield {"data": "Noted."}

    monkeypatch.setattr("app.routes.chat.build_agent", lambda *a, **k: Silent())
    body = client.post(
        "/api/chat", json={"thread_id": "t-silent", "message": "todo: ship the guard"}
    ).text
    assert '"kind": "nothing"' in body
    assert "\\u2318K" in body or "⌘K" in body


def test_a_capture_prefixed_chat_write_ties_the_knot(client):
    from app.services import fieldguide

    client.post("/api/users", json={"name": "tester"})
    client.post("/api/chat", json={"thread_id": "t-knot", "message": "todo: ship the guard"})
    tied = {k["id"] for k in fieldguide.guide("tester")["cards"] if k["tied"]}
    assert "chat_capture" in tied


def test_a_silent_turn_does_not_tie_the_knot(client, monkeypatch):
    """The card means "you filed something from chat". Tying it on a turn that
    filed nothing would make the guide lie about what you have done."""
    from app.services import fieldguide

    class Silent:
        async def stream_async(self, message):
            yield {"data": "Noted."}

    client.post("/api/users", json={"name": "tester"})
    monkeypatch.setattr("app.routes.chat.build_agent", lambda *a, **k: Silent())
    client.post("/api/chat", json={"thread_id": "t-silent2", "message": "todo: nope"})
    tied = {k["id"] for k in fieldguide.guide("tester")["cards"] if k["tied"]}
    assert "chat_capture" not in tied
