"""Author-private surfaces: 1:1 prep briefs and the private notes/feedback
journal. Everything here requires strong identity (StrongUser) — the
X-User header is never enough. No agent tool, MCP tool, or review-registry
entry may reference these records."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import ratelimit
from ..extensions.fastapi import PolicyAPIRoute
from ..services import private_notes
from .deps import StrongUser, ViewerDep

router = APIRouter(prefix="/api/private", route_class=PolicyAPIRoute)


class NoteIn(BaseModel):
    person: str = Field(max_length=64)
    body: str = Field(max_length=20_000)
    kind: str = Field("note", max_length=20)


@router.get("/notes")
def get_notes(user: StrongUser, person: str = ""):
    return private_notes.list_notes(user, person)


@router.post("/notes")
def post_note(body: NoteIn, user: StrongUser):
    # its own bucket (app/ratelimit.py): rows here are excluded from portable
    # export, FTS, and every agent surface, so a flood is invisible to every
    # other guard — and sharing the `write` budget would let a busy planning
    # session lock a person out of their own 1:1 notes
    ratelimit.check("private", user)
    return private_notes.add_note(user, body.person, body.body, kind=body.kind)


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, user: StrongUser):
    # a delete here writes a tombstone AND an audit row, so it grows the store
    ratelimit.check("private", user)
    return private_notes.delete_note(user, note_id)


@router.get("/audit")
def get_audit(user: StrongUser):
    return private_notes.list_audit(user)


@router.get("/brief/{person}")
def get_brief(person: str, user: StrongUser, viewer: ViewerDep, days: int = 14):
    private_notes.audit_brief(user, person)
    brief = private_notes.one_on_one_brief(person, days=days, viewer=viewer)
    gap = private_notes.feedback_gap_days(user, person)
    brief["feedback_gap_days"] = gap
    brief["nudge"] = (
        f"no feedback note for {person} in {gap}+ days"
        if gap is not None and gap >= private_notes.FEEDBACK_GAP_DAYS
        else (f"never captured feedback for {person}" if gap is None else "")
    )
    return brief
