from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services import ci
from .deps import CurrentUser

router = APIRouter()


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
