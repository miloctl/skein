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
    """Gitea (and any forge that speaks its shape) moves tasks through here.
    No CurrentUser: the signature IS the identity, and a forge cannot send a
    personal key or sign in."""
    body = await request.body()
    if len(body) > MAX_FORGE_BODY:
        raise HTTPException(400, "the webhook payload is too large")
    verify_forge_signature(body, x_gitea_signature or x_hub_signature_256)
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(400, "the webhook payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "the webhook payload must be a JSON object")
    mapped = forge.parse_gitea(x_gitea_event, payload)
    if mapped is None:
        return {"ignored": f"'{x_gitea_event}' is not an event that moves work"}
    actor = forge.resolve_actor(mapped.pop("actor", ""))
    # signed callers only, so this bounds a leaked-secret flood, not the world
    ratelimit.check("write", actor)
    return forge.forge_event(**mapped, actor=actor)
