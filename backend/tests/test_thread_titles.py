"""Thread titles: a model summary never overwrites a name a person chose."""

import pytest

from app.services import chat_threads

USER = "tester"


def _read_chat(client, message, thread):
    with client.stream("POST", "/api/chat", json={"thread_id": thread, "message": message}) as resp:
        assert resp.status_code == 200
        return resp.read().decode()


def _title(client, thread):
    return {c["id"]: c for c in client.get("/api/chats").json()}[thread]["title"]


def test_mock_keeps_the_deterministic_title(client):
    """build_titler is None without a provider, so the person's own first line
    stands and no model invents a name over it."""
    _read_chat(client, "todo: rewrite the scheduler", thread="tt-1")
    assert _title(client, "tt-1").startswith("todo: rewrite the scheduler")


def test_a_summary_is_pending_only_while_the_title_is_the_derived_one(client):
    _read_chat(client, "plan the migration", thread="tt-2")
    pending = chat_threads.pending_auto_title("tt-2", USER)
    assert pending is not None
    assert pending[1] == "plan the migration"
    client.patch("/api/chats/tt-2", json={"title": "Migration"})
    assert chat_threads.pending_auto_title("tt-2", USER) is None


def test_a_rename_beats_a_summary_that_lands_after_it(client):
    """The race the compare-and-set exists for: the model read the title, the
    owner renamed the thread, and the summary must lose."""
    _read_chat(client, "review the auth flow", thread="tt-3")
    pending = chat_threads.pending_auto_title("tt-3", USER)
    assert pending is not None
    previous, _ = pending
    client.patch("/api/chats/tt-3", json={"title": "Auth review"})
    assert chat_threads.set_auto_title("tt-3", USER, previous, "Auth flow review") is False
    assert _title(client, "tt-3") == "Auth review"


def test_a_summary_lands_as_one_unquoted_line(client):
    _read_chat(client, "ship the billing page", thread="tt-4")
    pending = chat_threads.pending_auto_title("tt-4", USER)
    assert pending is not None
    previous, _ = pending
    assert chat_threads.set_auto_title(
        "tt-4", USER, previous, '  "Billing page launch"\nand more text'
    )
    assert _title(client, "tt-4") == "Billing page launch"


def test_an_empty_summary_changes_nothing(client):
    _read_chat(client, "draft the retro notes", thread="tt-5")
    pending = chat_threads.pending_auto_title("tt-5", USER)
    assert pending is not None
    previous, _ = pending
    assert chat_threads.set_auto_title("tt-5", USER, previous, "   \n  ") is False
    assert _title(client, "tt-5") == previous


def test_a_summary_is_capped_at_the_column_width(client):
    _read_chat(client, "audit the export job", thread="tt-6")
    pending = chat_threads.pending_auto_title("tt-6", USER)
    assert pending is not None
    previous, _ = pending
    assert chat_threads.set_auto_title("tt-6", USER, previous, "x" * 200)
    assert _title(client, "tt-6") == "x" * chat_threads.TITLE_LEN


def test_a_thread_with_no_message_has_nothing_to_summarize(client):
    assert chat_threads.pending_auto_title("tt-absent", USER) is None


def test_only_the_first_turn_summarizes(client):
    """The bound on cost. Every failure leaves the title matching _title_from,
    so without this a degraded provider is retried once per turn forever."""
    _read_chat(client, "unpack the billing bug", thread="tt-7")
    assert chat_threads.pending_auto_title("tt-7", USER) is not None
    _read_chat(client, "and the refund path", thread="tt-7")
    assert chat_threads.pending_auto_title("tt-7", USER) is None


def test_the_flock_command_is_not_the_title(client):
    """The flock path logs the raw command, so _title_from has to strip it or
    every flock thread is named after the command it ran."""
    _read_chat(client, "/flock delivery what should we cut", thread="tt-8")
    assert _title(client, "tt-8") == "what should we cut"


def test_the_derived_title_is_the_guard_that_pending_matches(client):
    """_title_from and pending_auto_title are one contract. Change the derived
    string without this test and every summary stops firing — silently, since
    a thread that never matches simply keeps the title it already had."""
    _read_chat(client, "/as growth-mentor map out a learning goal", thread="tt-9")
    pending = chat_threads.pending_auto_title("tt-9", USER)
    assert pending is not None
    # the LITERAL derived title, not _title_from() again: comparing the
    # function against itself can never fail, and the hazard is that the
    # derived shape changes while stored titles keep the old one
    assert pending[0] == "map out a learning goal"


@pytest.mark.parametrize(
    "answer,want",
    [
        ('Name: "Billing page launch"', "Billing page launch"),
        ("Title: Billing page launch", "Billing page launch"),
        ("**Billing page launch**", "Billing page launch"),
        ("# Billing page launch", "Billing page launch"),
        ("“Billing page launch”", "Billing page launch"),
        ("Billing page launch\nand more text", "Billing page launch"),
    ],
)
def test_a_summary_lands_bare_whatever_shape_the_model_answered_in(client, answer, want):
    # TITLE_PROMPT forbids every one of these, which is why they are tested:
    # a prompt names a failure because models produce it anyway
    thread = f"tt-s{abs(hash(answer)) % 997}"
    _read_chat(client, "ship the billing page", thread=thread)
    pending = chat_threads.pending_auto_title(thread, USER)
    assert pending is not None
    assert chat_threads.set_auto_title(thread, USER, pending[0], answer)
    assert _title(client, thread) == want


class _FakeTitler:
    """Stands in for the model. event_loop_metrics is None on purpose: it is
    the shape _usage_row reads, and it must yield no spend row rather than a
    zero-token one."""

    event_loop_metrics = None

    def __init__(self, answer: str):
        self.answer = answer

    async def stream_async(self, message):
        yield {"data": self.answer}


def test_a_real_summary_reaches_the_sidebar(client, monkeypatch):
    """The wiring. Without this every assertion above still passes with
    _summarize_title unwired from routes/chat.py."""
    monkeypatch.setattr("app.routes.chat.build_titler", lambda: _FakeTitler("Billing bug triage"))
    _read_chat(client, "the billing page throws on submit", thread="tt-10")
    assert _title(client, "tt-10") == "Billing bug triage"


# NOT tested here: that the title write lands before the response body ends,
# which is the ordering the inline await exists for. TestClient runs a
# StreamingResponse background task before its context manager exits, so a
# pytest assertion passes against BOTH designs and pins neither. The failure
# it guards is client-side (frontend/app/runtime-provider.tsx fires the
# sidebar refresh when its reader loop ends) and is reproducible only in a
# browser. Do not add a pytest test claiming to cover it.


def test_title_provider_failure_logs_only_the_exception_class(client, monkeypatch, caplog):
    canary = "sk-live-title-secret request_id=title-42"

    def broken():
        raise RuntimeError(canary)

    monkeypatch.setattr("app.routes.chat.build_titler", broken)
    _read_chat(client, "keep the deterministic title", thread="tt-title-fault")

    assert canary not in caplog.text
    assert "RuntimeError" in caplog.text


def test_a_later_turn_builds_no_titler_at_all(client, monkeypatch):
    """The cost bound is on the model CALL, not just on the write.

    Counted, never raised: _summarize_title wraps its body in `except
    Exception`, so a titler that raises is swallowed and the test passes with
    the bound deleted."""
    _read_chat(client, "open the migration thread", thread="tt-13")
    calls: list[int] = []
    monkeypatch.setattr("app.routes.chat.build_titler", lambda: calls.append(1))
    _read_chat(client, "and the rollback plan", thread="tt-13")
    assert calls == []


def test_a_command_turn_buys_no_model_call(client, monkeypatch):
    """Slash commands are deterministic for every provider: no agent, no
    tokens. A title summary on that path would be the one model call it costs."""

    calls: list[int] = []
    monkeypatch.setattr("app.routes.chat.build_titler", lambda: calls.append(1))
    _read_chat(client, "/help", thread="tt-11")
    assert calls == []
    assert _title(client, "tt-11") == "/help"
