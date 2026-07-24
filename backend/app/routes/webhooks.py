from fastapi import APIRouter
from pydantic import BaseModel

from ..services import ci
from .deps import CurrentUser

router = APIRouter()


class CIEventIn(BaseModel):
    repo: str = ""
    branch: str = ""
    status: str = ""
    run_url: str = ""
    # raw GitHub Actions payloads are accepted too
    workflow_run: dict | None = None
    repository: dict | None = None


@router.post("/api/webhooks/ci")
def ci_webhook(body: CIEventIn, user: CurrentUser):
    if body.workflow_run is not None:
        mapped = ci.parse_github_actions(body.model_dump())
        if mapped is None:
            return {"ignored": "not a completed workflow_run"}
        return ci.ci_event(**mapped, actor=user)
    return ci.ci_event(body.repo, body.branch, body.status, body.run_url, actor=user)
