"""Slack slash-command surface. Active only when SLACK_SIGNING_SECRET is set.

Commands route through the same deterministic engine as the mock agent
(capture, /briefing, /search, /plan…) — well inside Slack's 3-second response
budget and independent of whether an LLM provider is configured.
"""

import hashlib
import hmac
import time

from fastapi import APIRouter, HTTPException, Request

from .. import config
from ..agents.mock_agent import MockAgent

router = APIRouter()


def _verify(raw: bytes, timestamp: str, signature: str) -> bool:
    try:
        if abs(time.time() - float(timestamp or 0)) > 60 * 5:
            return False
        base = f"v0:{timestamp}:{raw.decode()}"
    except (ValueError, UnicodeDecodeError):
        return False
    expected = (
        "v0="
        + hmac.new(config.SLACK_SIGNING_SECRET.encode(), base.encode(), hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature or "")


@router.post("/api/slack/command")
async def slack_command(request: Request):
    if not config.SLACK_SIGNING_SECRET:
        raise HTTPException(status_code=404, detail="Slack integration not configured")
    raw = await request.body()
    if not _verify(
        raw,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        request.headers.get("X-Slack-Signature", ""),
    ):
        raise HTTPException(status_code=401, detail="bad Slack signature")

    form = await request.form()
    text = str(form.get("text", "")).strip()
    user = str(form.get("user_name", "slack-user"))

    from ..services.adoption import record_use

    record_use(user, "slack")
    if text.lower().split(maxsplit=1)[:1] == ["/as"]:
        return {
            "response_type": "ephemeral",
            "text": "Bench personas live in the web chat — open /chat and use /as there.",
        }
    agent = MockAgent(thread_id="slack", user=user)
    chunks = []
    try:
        async for event in agent.stream_async(text or "/help"):
            if "data" in event:
                chunks.append(event["data"])
    except Exception as exc:  # a Slack-visible message beats a 500 + retry loop
        chunks.append(f"⚠️ that did not land: {exc}")
    return {"response_type": "ephemeral", "text": "\n".join(chunks) or "(no output)"}
