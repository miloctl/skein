"""Author-private surfaces: 1:1 prep briefs and the private notes/feedback
journal. Everything here requires strong identity (StrongUser) — the
X-User header is never enough. No agent tool, MCP tool, or review-registry
entry may reference these records."""

from fastapi import APIRouter
from pydantic import BaseModel

from ..services import private_notes
from .deps import StrongUser

router = APIRouter(prefix="/api/private")


class NoteIn(BaseModel):
    person: str
    body: str
    kind: str = "note"


@router.get("/notes")
def get_notes(user: StrongUser, person: str = ""):
    return private_notes.list_notes(user, person)


@router.post("/notes")
def post_note(body: NoteIn, user: StrongUser):
    return private_notes.add_note(user, body.person, body.body, kind=body.kind)


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, user: StrongUser):
    return private_notes.delete_note(user, note_id)


@router.get("/audit")
def get_audit(user: StrongUser):
    return private_notes.list_audit(user)


@router.get("/brief/{person}")
def get_brief(person: str, user: StrongUser, days: int = 14):
    private_notes.audit_brief(user, person)
    brief = private_notes.one_on_one_brief(person, days=days)
    gap = private_notes.feedback_gap_days(user, person)
    brief["feedback_gap_days"] = gap
    brief["nudge"] = (
        f"no feedback note for {person} in {gap}+ days"
        if gap is not None and gap >= private_notes.FEEDBACK_GAP_DAYS
        else (f"never captured feedback for {person}" if gap is None else "")
    )
    return brief
