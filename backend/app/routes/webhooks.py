import asyncio
import json
from hashlib import sha256

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import config, ratelimit
from ..extensions.fastapi import PolicySubjectDep, enforce_decision
from ..extensions.policy import PolicyInput, PolicyResource
from ..services import ci, forge
from .deps import StrongUser, forge_webhook_off, verify_forge_signature

router = APIRouter()

# the raw body is read into memory to verify the HMAC, so it is bounded here.
# Gitea's biggest payload is a push with many commits; 256 KiB clears it.
MAX_FORGE_BODY = 262_144
# a forge delivers in one burst. Without this an unsigned caller holds a
# connection slot open forever by dribbling bytes — uvicorn applies no body
# timeout, and this route sits outside the perimeter.
FORGE_READ_TIMEOUT = 10


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
def ci_webhook(
    body: CIEventIn,
    user: StrongUser,
    request: Request,
    subject: PolicySubjectDep,
):
    # Resolve the repository the write will actually target BEFORE policy: a
    # GitHub Actions payload carries `repository.full_name` beside the generic
    # `repo` field, and authorizing one while mutating the other lets a caller
    # pass policy under an allowed name and file against a denied one.
    mapped = None
    if body.workflow_run is not None:
        raw = ci.parse_github_actions(body.model_dump())
        if raw is None:
            return {"ignored": "not a completed pass/fail workflow_run"}
        # Re-validate through the same model: `repository` is an unschema'd
        # dict, so full_name arrives as anything — a nested dict raised
        # inside the first policy rule that called .lower() on it, and an
        # unbounded string was CPU spent before authorization.
        mapped = CIEventIn(**raw).model_dump(exclude={"workflow_run", "repository"})
    repository = mapped["repo"] if mapped is not None else body.repo
    enforce_decision(
        request.app.state.skein_registry.policy_engine.decide(
            PolicyInput(
                subject,
                "skein.integration.ci",
                PolicyResource("integration", "ci", attributes={"repository": repository}),
                "ci",
                tool="ci.webhook",
                tool_effect="write",
                tool_risk="high",
            )
        )
    )
    if mapped is not None:
        return ci.ci_event(**mapped, actor=user)
    return ci.ci_event(body.repo, body.branch, body.status, body.run_url, actor=user)


async def _read_bounded(request: Request) -> bytes:
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_FORGE_BODY:
            raise HTTPException(400, "the webhook payload is too large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/api/webhooks/forge")
async def forge_webhook(
    request: Request,
    x_gitea_event: str = Header(""),
    x_gitea_signature: str = Header(""),
    x_hub_signature_256: str = Header(""),
    x_gitea_delivery: str = Header("", max_length=200),
    x_github_delivery: str = Header("", max_length=200),
    x_github_event: str = Header(""),
) -> dict:
    """Gitea authenticates with the raw-body HMAC, not a user key."""
    # BEFORE the read, because this path sits outside the perimeter: a
    # deployment that never turned the webhook on must not buffer a byte for
    # an unsigned caller. routes/slack.py refuses the same way, first thing.
    if not config.FORGE_WEBHOOK_SECRET:
        raise forge_webhook_off()
    # by address, BEFORE the read and the HMAC: an unsigned caller has no name
    # to key on, and everything after this point costs real work
    await run_in_threadpool(ratelimit.check, "forge_addr", ratelimit.client_addr(request))
    # Content-Length is a hint a caller can lie about, so the stream is
    # counted too; the timeout bounds a caller who dribbles bytes instead.
    declared = request.headers.get("content-length") or "0"
    if not declared.isdecimal() or int(declared) > MAX_FORGE_BODY:
        raise HTTPException(400, "the webhook payload is too large")
    try:
        async with asyncio.timeout(FORGE_READ_TIMEOUT):
            body = await _read_bounded(request)
    except TimeoutError as exc:
        raise HTTPException(400, "the webhook payload did not arrive in time") from exc
    # threadpooled: an HMAC over a body up to MAX_FORGE_BODY is real CPU, this
    # route sits outside the perimeter middleware, and the forge_addr cap
    # admits 600 of these a minute — inline, a busy monorepo's push traffic
    # ran on the loop that carries every open chat stream
    # HMAC authenticates bytes, not headers. Ambiguous provider routing or
    # duplicate headers must not choose a different parser for those bytes.
    routing_headers = (
        "x-gitea-event",
        "x-gitea-signature",
        "x-gitea-delivery",
        "x-github-event",
        "x-hub-signature-256",
        "x-github-delivery",
        "x-github-hook-id",
    )
    if any(len(request.headers.getlist(name)) > 1 for name in routing_headers):
        raise HTTPException(
            400, "The webhook headers are ambiguous. Send one set of provider headers."
        )
    # Matching GitHub aliases are emitted by Gitea too. Only its native event
    # header selects processing, so native GitHub deliveries remain unsupported.
    if (
        (x_github_event and x_github_event != x_gitea_event)
        or (x_github_delivery and x_github_delivery != x_gitea_delivery)
        or request.headers.get("x-github-hook-id")
    ):
        raise HTTPException(
            400, "The webhook headers conflict. Send the original provider headers."
        )
    event = x_gitea_event
    if (
        not event
        or len(event) > 100
        or not all(c.isascii() and (c.isalnum() or c == "_") for c in event)
    ):
        raise HTTPException(
            400, "The webhook event header is not valid. Send the original event header."
        )
    # Every supplied SHA-256 signature must verify. Taking the first one
    # would accept a contradictory alias and hide a broken secret rollout.
    signatures = tuple(value for value in (x_gitea_signature, x_hub_signature_256) if value)
    for signature in signatures or ("",):
        await run_in_threadpool(verify_forge_signature, body, signature)
    # RecursionError, not just ValueError: deeply nested JSON raises it, and
    # this route hand-rolls the parse instead of taking a pydantic model, so
    # main.py's RequestValidationError handler never sees the payload
    try:
        payload = json.loads(
            body, object_pairs_hook=config._json_object, parse_constant=config._json_constant
        )
    except (ValueError, RecursionError) as exc:
        raise HTTPException(400, "the webhook payload is not valid JSON") from exc
    # a JSON array parses fine and then dies inside parse_gitea with
    # AttributeError — a caller's input must never reach a 500
    if not isinstance(payload, dict):
        raise HTTPException(400, "the webhook payload must be a JSON object")
    if event in ("push", "pull_request"):
        await run_in_threadpool(ratelimit.check, "forge", "forge")
    return await run_in_threadpool(
        forge.apply_delivery,
        request.app.state.skein_registry,
        event,
        payload,
        x_gitea_delivery,
        sha256(body).hexdigest(),
    )
