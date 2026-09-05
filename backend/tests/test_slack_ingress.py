"""Unsigned Slack requests are bounded before signature verification."""

import asyncio
import time

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import config, ratelimit
from app.routes import slack


def _request(body=b"", *, headers=(), delay=0):
    async def receive():
        await asyncio.sleep(delay)
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "headers": list(headers), "client": ("test", 123)}, receive)


@pytest.mark.parametrize("declared", [False, True])
def test_slack_refuses_oversized_input_before_signature(monkeypatch, declared):
    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "test")
    monkeypatch.setattr(slack, "MAX_SLACK_BODY", 8, raising=False)
    monkeypatch.setattr(slack, "_verify", lambda *args: pytest.fail("unbounded signature work"))
    headers = [(b"content-length", b"16")] if declared else []
    with pytest.raises(HTTPException) as refused:
        asyncio.run(slack.slack_command(_request(b"x" * 16, headers=headers)))
    assert refused.value.status_code == 400


def test_slack_body_read_has_a_deadline(monkeypatch):
    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "test")
    monkeypatch.setattr(slack, "SLACK_READ_TIMEOUT", 0.01, raising=False)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(slack.slack_command(_request(delay=0.05)))
    assert refused.value.status_code == 400
    assert "time" in refused.value.detail


def test_slack_limits_unsigned_calls_by_address(monkeypatch):
    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "test")
    monkeypatch.setitem(ratelimit.LIMITS, "slack_addr", 1)
    with pytest.raises(HTTPException) as unsigned:
        asyncio.run(slack.slack_command(_request()))
    assert unsigned.value.status_code == 401
    with pytest.raises(ratelimit.RateLimited):
        asyncio.run(slack.slack_command(_request()))


def test_slack_refuses_non_ascii_signature_without_a_server_error(monkeypatch):
    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "test")
    headers = [
        (b"x-slack-request-timestamp", str(int(time.time())).encode()),
        (b"x-slack-signature", b"\xff"),
    ]
    with pytest.raises(HTTPException) as refused:
        asyncio.run(slack.slack_command(_request(b"text=hello", headers=headers)))
    assert refused.value.status_code == 401
