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
from ..agents import commands
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

    from ..services import users as users_svc
    from ..services.adoption import record_use
    from ..services.users import ensure_user

    record_use(user, "slack")
    # every other write surface registers its writer (deps.py does it for
    # REST); without this, Slack captures logged under an unrostered name
    # were invisible to the scoped activity surfaces.
    #
    # A clash with an agent identity is a REFUSAL, not something to suppress:
    # deps.py refuses an agent identity on REST because agent rows carry trust
    # scores and gate levels, and writes as them sidestep the review gate.
    # Slack had no equivalent, so a workspace member whose user_name matched an
    # agent wrote as that agent with origin=human.
    try:
        ensure_user(user)
    except ValueError as exc:
        return {"response_type": "ephemeral", "text": str(exc)}
    if users_svc.is_agent(user):
        return {
            "response_type": "ephemeral",
            "text": (
                f"'{user}' is an agent identity in Skein. Agents write through the gated"
                " tool surface, not Slack. Ask whoever runs the server for a different name."
            ),
        }
    # EVERY route-level command, not a hardcoded list: a handler-None entry is
    # resolved by routes/chat.py, so commands.dispatch returns None for it and
    # the MockAgent below would smart-capture the raw slash command as a note
    # attributed to this person. That is what `/flock ...` did in Slack until
    # this loop replaced a literal `/as` check.
    # the token exactly as typed, slash included. lstrip("/") here matched a
    # plain note that merely STARTS with a command word — "as discussed, the
    # vendor slipped" came back refused and was never captured — and matched
    # "//flock" too.
    first = text.split(maxsplit=1)[:1]
    route_level = {"/" + c["name"] for c in commands.COMMANDS if c["handler"] is None}
    if first and first[0].lower() in route_level:
        return {
            "response_type": "ephemeral",
            "text": f"{first[0].lower()} runs in the web chat only — open /chat and use it there.",
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
