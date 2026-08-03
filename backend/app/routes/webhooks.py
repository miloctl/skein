import json

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .. import ratelimit
from ..services import ci, forge
from .deps import CurrentUser, verify_forge_signature

router = APIRouter()

# the raw body is read into memory to verify the HMAC, so it is bounded here.
# Gitea's biggest payload is a push with many commits; 1 MiB clears it.
MAX_FORGE_BODY = 1_048_576


class CIEventIn(BaseModel):
    # repo and run_url are concatenated into a blocker title/detail, whose
    # create models cap at 200/4000
    repo: str = Field("", max_length=200)
    branch: str = Field("", max_length=200)
    status: str = Field("", max_length=20)
    run_url: str = Field("", max_length=4000)
    # raw GitHub Actions payloads are accepted too
    workflow_run: dict | None = None
    repository: dict | None = None


@router.post("/api/webhooks/ci")
def ci_webhook(body: CIEventIn, user: CurrentUser):
    if body.workflow_run is not None:
        mapped = ci.parse_github_actions(body.model_dump())
        if mapped is None:
            return {"ignored": "not a completed pass/fail workflow_run"}
        return ci.ci_event(**mapped, actor=user)
    return ci.ci_event(body.repo, body.branch, body.status, body.run_url, actor=user)


@router.post("/api/webhooks/forge")
async def forge_webhook(
    request: Request,
    x_gitea_event: str = Header(""),
    x_gitea_signature: str = Header(""),
    x_hub_signature_256: str = Header(""),
) -> dict:
    """Gitea moves tasks through here. No CurrentUser: the signature IS the
    identity, and a forge cannot send a personal key or sign in. Gitea-shaped
    payloads only — GitHub names the same fields differently (`compare`,
    `pusher.name`), so it needs its own parser, not just its header."""
    # bounded BEFORE the read, not after: an unsigned caller must never make
    # the process buffer an arbitrary body. Content-Length is a hint, so the
    # stream is counted too.
    if int(request.headers.get("content-length") or 0) > MAX_FORGE_BODY:
        raise HTTPException(400, "the webhook payload is too large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_FORGE_BODY:
            raise HTTPException(400, "the webhook payload is too large")
        chunks.append(chunk)
    body = b"".join(chunks)
    verify_forge_signature(body, x_gitea_signature or x_hub_signature_256)
    # RecursionError, not just ValueError: deeply nested JSON raises it, and
    # this route hand-rolls the parse instead of taking a pydantic model, so
    # main.py's RequestValidationError handler never sees the payload
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise HTTPException(400, "the webhook payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "the webhook payload must be a JSON object")
    # its own bucket, keyed to the integration: keying on the pusher's name
    # would let a signed caller drain a named teammate's REST write budget
    ratelimit.check("forge", "forge")
    mapped = forge.parse_gitea(x_gitea_event, payload)
    if mapped is None:
        # the event name is caller-supplied — name what we accept, never echo
        return {"ignored": "only push and pull_request events move work"}
    pushed_by = forge.resolve_pusher(mapped.pop("pushed_by", ""))
    return forge.forge_event(**mapped, pushed_by=pushed_by)
