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
    # names the action, not the key: this detail reaches chat's markdown
    # renderer, which cannot swap a ⌘K token for the reader's keyboard
    assert "quick capture" in note["detail"]
    assert "⌘" not in note["detail"]


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
    assert "quick capture" in body


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


def test_the_reprompt_runs_once_and_only_once(client, monkeypatch):
    """The opt-in second exchange: budget of exactly one, and a turn that
    stays unfiled after the reprompt still states the absence."""
    from app import config

    calls = []

    class CountingAgent:
        async def stream_async(self, message):
            calls.append(message)
            yield {"data": "noted."}

    monkeypatch.setattr(config, "TURN_GUARD", True)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr("app.routes.chat.build_agent", lambda *a, **k: CountingAgent())

    body = client.post(
        "/api/chat", json={"thread_id": "t-reprompt", "message": "todo: file me"}
    ).text
    assert len(calls) == 2  # the message, then the OBJECTION — never a third
    assert calls[0] == "todo: file me"
    assert "capture prefix" in calls[1]  # the objection text reached the agent
    assert '"kind": "nothing"' in body  # still unfiled -> the absence is stated


def test_a_reprompt_that_files_clears_the_guard(client, monkeypatch):
    from app import config
    from app.agents import receipts as receipts_mod

    class SecondTryAgent:
        def __init__(self):
            self.turn = 0

        async def stream_async(self, message):
            self.turn += 1
            if self.turn == 2:
                receipts_mod.record("wrote", "task", "filed on the retry", 7)
            yield {"data": "ok"}

    monkeypatch.setattr(config, "TURN_GUARD", True)
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr("app.routes.chat.build_agent", lambda *a, **k: SecondTryAgent())

    body = client.post(
        "/api/chat", json={"thread_id": "t-reprompt2", "message": "todo: file me"}
    ).text
    assert '"kind": "wrote"' in body
    assert '"kind": "nothing"' not in body


def test_a_failed_write_does_not_tie_the_knot(client, monkeypatch):
    """A failed receipt silences the guard — it told the truth — but nothing
    was filed, so the knot must not tie. `wrote` was the wrong predicate."""
    from app.agents import receipts as receipts_mod
    from app.services import fieldguide

    class FailingAgent:
        async def stream_async(self, message):
            receipts_mod.record("failed", "task", "rate capped")
            yield {"data": "could not file it"}

    client.post("/api/users", json={"name": "tester"})
    monkeypatch.setattr("app.routes.chat.build_agent", lambda *a, **k: FailingAgent())
    body = client.post("/api/chat", json={"thread_id": "t-fail", "message": "todo: doomed"}).text
    assert '"kind": "nothing"' not in body  # the failed receipt already said it
    tied = {k["id"] for k in fieldguide.guide("tester")["cards"] if k["tied"]}
    assert "chat_capture" not in tied
