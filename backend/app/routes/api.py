"""REST API: reads for the dashboard, writes for humans (the second write path
alongside agent tools — both go through app.services)."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import config, db, ratelimit
from ..services import (
    absences,
    activity,
    admin,
    api_keys,
    blockers,
    briefing,
    capture,
    collab,
    context_pack,
    crews,
    delegation,
    digest,
    engagements,
    feedback,
    fieldguide,
    flocks,
    handoff,
    ingest,
    intake,
    memory,
    notifications,
    personas,
    playbooks,
    portfolio,
    promises,
    pulse,
    readout,
    review,
    rituals,
    schedule,
    search,
    settings,
    tuning,
    usage,
    users,
    weekly,
    work,
)
from .deps import AdminUser, CurrentUser, StrongUser

router = APIRouter(prefix="/api")


# ---- reads -----------------------------------------------------------------
#
# Every read that returns a row takes CurrentUser, including the ones whose
# handler never spends it. It buys ONE thing, and it is not access control:
# a read with no caller cannot be given a visibility filter later without
# changing its signature, and 45 of the 76 GET routes had none
# (docs/VISIBILITY.md). The walls `_resolve` applies are already applied at
# the perimeter in api-key and oidc mode, and in trusted-header mode a caller
# refused under one name reaches every read by picking another.
#
# The file-backed catalogs are the exception, enumerated with a reason each
# in tests/test_route_identity.py::OPEN_READS. Adding an open GET without a
# line there fails CI.


@router.get("/milestones")
def get_milestones(user: CurrentUser, project: str = "", status: str = ""):
    return work.list_milestones(project, status)


@router.get("/tasks")
def get_tasks(user: CurrentUser):
    return work.list_tasks_joined()


@router.get("/tasks/{task_id}/worklog")
def get_task_worklog(user: CurrentUser, task_id: int):
    try:
        return delegation.list_worklog(task_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/questions")
def get_questions(user: CurrentUser, status: str = ""):
    return collab.list_questions(status)


@router.get("/decisions")
def get_decisions(user: CurrentUser, status: str = "", category: str = ""):
    return collab.list_decisions(status=status, category=category)


@router.get("/standups")
def get_standups(user: CurrentUser):
    return collab.list_standups()


@router.get("/events")
def get_events(user: CurrentUser, from_date: str = ""):
    return schedule.list_events(from_date)


@router.get("/personas")
def get_personas():
    return personas.list_personas()


@router.get("/personas/{slug}")
def get_persona(slug: str):
    return personas.get_persona(slug)


@router.get("/flocks")
def get_flocks():
    return flocks.list_flocks()


@router.get("/flocks/traces")
def get_flock_traces(user: CurrentUser, thread: str = "", flock: str = "", limit: int = 20):
    # CurrentUser, unlike the open roster above: a trace row names a person and
    # the thread id their chat transcript is keyed by (routes/chat.py scopes
    # those per owner), so this must not answer an unidentified caller
    return flocks.list_traces(thread_id=thread, flock=flock, limit=limit)


@router.get("/notes")
def get_notes(user: CurrentUser, q: str = ""):
    return collab.search_notes(q)


class NotePatch(BaseModel):
    topic: str = Field("", max_length=200)
    content: str = Field("", max_length=20_000)


@router.patch("/notes/{note_id}")
def patch_note(note_id: int, body: NotePatch, user: CurrentUser):
    # edits scan for @mentions, so an uncapped PATCH is a notification
    # amplifier — same cap as the create routes
    ratelimit.check("write", user)
    try:
        return collab.update_note(note_id, body.topic, body.content, actor=user)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, user: CurrentUser):
    ratelimit.check("delete", user)
    try:
        return collab.delete_note(note_id, actor=user)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.delete("/events/{event_id}")
def delete_event(event_id: int, user: CurrentUser):
    ratelimit.check("delete", user)
    try:
        return schedule.cancel_event(event_id, actor=user)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/activity")
def get_activity(user: CurrentUser):
    return collab.recent_activity(user)


@router.get("/activity/feed")
def get_activity_feed(user: CurrentUser, before: int = 0, limit: int = 50):
    """The rendered feed: agent and system actions plus your own. The scope is
    enforced in the service — there is no parameter for another person."""
    fieldguide.mark(user, "activity_feed")
    return activity.feed(user, limit=limit, before=before)


@router.get("/activity/verify")
def get_activity_verify(user: CurrentUser, tail: int = 0):
    """Recompute the provenance chain. The default walks every chained row,
    because a partial answer to "is the ledger intact" is not an answer.
    tail=1 resumes from the last verified anchor for a cheap freshness check.

    Rate-capped: activity is never pruned, so the full walk is the most
    expensive read in the app and it grows for the life of the deployment. The
    daily findings rule runs it unprompted, so nobody needs it on a loop."""
    ratelimit.check("verify", user)
    return activity.verify_tail() if tail else activity.verify_chain()


@router.get("/blockers")
def get_blockers(user: CurrentUser, status: str = "", owner: str = ""):
    return blockers.list_blockers(status, owner)


@router.get("/intake")
def get_intake(user: CurrentUser, status: str = ""):
    return intake.list_requests(status)


@router.get("/review")
def get_review(user: CurrentUser, status: str = "pending"):
    return review.list_changes(status)


@router.get("/engagements")
def get_engagements(user: CurrentUser, status: str = ""):
    return engagements.list_engagements(status)


@router.get("/allocations")
def get_allocations(user: CurrentUser, engagement_id: int = 0):
    return engagements.list_allocations(engagement_id)


@router.delete("/allocations/{allocation_id}")
def delete_allocation(allocation_id: int, user: CurrentUser):
    ratelimit.check("delete", user)
    try:
        return engagements.deallocate(allocation_id, actor=user)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


class AbsenceIn(BaseModel):
    person: str = Field(max_length=64)
    starts_on: str = Field(max_length=10)
    ends_on: str = Field(max_length=10)
    kind: str = Field("pto", max_length=10)
    note: str = Field("", max_length=200)


@router.get("/absences")
def get_absences(user: CurrentUser, person: str = ""):
    return absences.list_absences(person)


@router.post("/absences")
def post_absence(body: AbsenceIn, user: CurrentUser):
    ratelimit.check("absence", user)
    try:
        return absences.add_absence(
            body.person, body.starts_on, body.ends_on, body.kind, body.note, actor=user
        )
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/absences/{absence_id}")
def delete_absence(absence_id: int, user: CurrentUser):
    ratelimit.check("delete", user)  # outside the try: a rate cap is a 400, not a 404
    try:
        return absences.delete_absence(absence_id, actor=user)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/capacity")
def get_capacity(user: CurrentUser):
    return engagements.capacity()


@router.get("/lessons")
def get_lessons(user: CurrentUser, project_class: str = ""):
    return engagements.list_lessons(project_class)


@router.get("/playbooks")
def get_playbooks():
    return playbooks.list_playbooks()


@router.get("/artifacts")
def get_artifacts(user: CurrentUser, engagement_id: int = 0):
    return handoff.list_artifacts(engagement_id)


@router.get("/users")
def get_users(user: CurrentUser, all: bool = False):
    # all=1 includes deactivated rows — the Settings roster needs them so
    # deactivation stays reversible from the UI
    return users.list_users(active_only=not all)


class UserRenameIn(BaseModel):
    new_name: str = Field(min_length=1, max_length=64)


@router.post("/users/{name}/rename")
def post_user_rename(name: str, body: UserRenameIn, user: AdminUser):
    # rename/merge moves attribution history — administrators only
    return users.rename_user(name, body.new_name, actor=user)


class UserActiveIn(BaseModel):
    active: bool


@router.post("/users/{name}/active")
def post_user_active(name: str, body: UserActiveIn, user: AdminUser):
    # roster edits are admin surface — one teammate must not be able to
    # deactivate another
    return users.set_active(name, body.active, actor=user)


# ---- crews -----------------------------------------------------------------
#
# A crew is membership only (docs/VISIBILITY.md) — it grants nothing until the
# tier columns land. Editing one is a STEWARD's job or an administrator's, and
# _crew_steward below is the only place that pair is decided.


class CrewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=crews.NAME_LEN)
    summary: str = Field("", max_length=crews.SUMMARY_LEN)


class CrewPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field("", max_length=crews.NAME_LEN)
    summary: str | None = Field(None, max_length=crews.SUMMARY_LEN)
    active: bool | None = None


class CrewMemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person: str = Field(min_length=1, max_length=64)
    role: str = Field("member", max_length=16)


class CrewMemberOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person: str = Field(min_length=1, max_length=64)


def _crew_steward(crew_id: int, user: str, request: Request) -> None:
    """A steward of THIS crew, or an administrator.

    Not AdminUser on the route: a crew whose membership only an administrator
    can edit is a crew nobody maintains. Not CurrentUser alone either — in
    trusted-header mode that is a self-asserted header, and membership is
    about to decide what a person reads. Strong identity is the same bar
    routes/private.py holds for the other surface that answers per person.
    """
    from .deps import _require_strong, is_named_admin

    _require_strong(getattr(request.state, "strong_auth", False))
    # the groups current_user stashed, not []: an administrator named only by
    # SKEIN_OIDC_ADMIN_GROUP is refused by an empty list, while the same person
    # can rename roster rows and revoke every key through AdminUser.
    groups = getattr(request.state, "auth_groups", [])
    # is_named_admin, NOT _is_admin: the scarcity fallback makes every key
    # holder an administrator in the default deployment, and that would let
    # any of them take a crew from its steward in one call.
    if crews.is_steward(crew_id, user) or is_named_admin(user, groups):
        return
    raise HTTPException(
        status_code=403,
        detail="only a steward of this crew can change it. Ask a steward to add you.",
    )


@router.get("/crews")
def get_crews(user: CurrentUser, all: bool = False):
    return crews.list_crews(active_only=not all)


@router.get("/crews/mine")
def get_my_crews(user: CurrentUser):
    """Self-scoped by construction: the only person parameter is the caller.
    There is deliberately no way to read another person's crew list."""
    return crews.crews_of(user)


@router.get("/crews/{crew_id}")
def get_crew(crew_id: int, user: CurrentUser):
    return crews.get_crew(crew_id)


@router.post("/crews")
def post_crew(body: CrewIn, user: StrongUser):
    # StrongUser: the creator becomes the first steward, so a self-asserted
    # header would mint a crew someone else appears to steward
    ratelimit.check("write", user)
    return crews.create_crew(body.name, summary=body.summary, actor=user)


@router.patch("/crews/{crew_id}")
def patch_crew(crew_id: int, body: CrewPatch, user: CurrentUser, request: Request):
    _crew_steward(crew_id, user, request)
    ratelimit.check("write", user)
    return crews.update_crew(
        crew_id, name=body.name, summary=body.summary, active=body.active, actor=user
    )


@router.post("/crews/{crew_id}/members")
def post_crew_member(crew_id: int, body: CrewMemberIn, user: CurrentUser, request: Request):
    _crew_steward(crew_id, user, request)
    ratelimit.check("write", user)
    return crews.add_member(crew_id, body.person, role=body.role, actor=user)


@router.post("/crews/{crew_id}/members/remove")
def post_crew_member_remove(crew_id: int, body: CrewMemberOut, user: CurrentUser, request: Request):
    """The person travels in the BODY, not the path.

    A roster name may contain any character (ensure_user caps length and
    nothing else), and starlette's router does not match a path segment
    holding `/` even percent-encoded — `a/b` could be added to a crew and
    then never removed by any request the client could form. Removal is the
    only way out of a crew, so it must not be shaped by what the name is.
    """
    _crew_steward(crew_id, user, request)
    ratelimit.check("delete", user)
    return crews.remove_member(crew_id, body.person, actor=user)


class GrowthIn(BaseModel):
    interests: str = Field(max_length=500)


@router.post("/users/growth-interests")
def post_growth_interests(body: GrowthIn, user: CurrentUser):
    # self-declared only: you set YOURS (future-planning data, never scored)
    return users.set_growth_interests(user, body.interests, actor=user)


@router.get("/users/growth-interests")
def get_growth_interests(user: CurrentUser):
    # write-only fields can't be reviewed or cleared — prefill needs this
    return {"interests": users.get_growth_interests(user)}


class ThemeIn(BaseModel):
    theme: str = Field(max_length=400)


@router.post("/users/theme")
def post_user_theme(body: ThemeIn, user: CurrentUser):
    try:
        return users.set_theme(user, body.theme)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/users/theme")
def get_user_theme(user: CurrentUser):
    return {"theme": users.get_theme(user), "team_default": users.get_team_default_theme()}


@router.post("/users/theme/default")
def post_team_theme(body: ThemeIn, user: AdminUser):
    try:
        return users.set_team_default_theme(body.theme, actor=user)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/search")
def get_search(q: str, user: CurrentUser):
    fieldguide.mark(user, "search")
    return search.search(q)


@router.get("/ask")
def get_ask(q: str, user: CurrentUser):
    fieldguide.mark(user, "search")
    return search.ask(q)


@router.get("/field-guide")
def get_field_guide(user: CurrentUser):
    """Self-scoped by construction: the only person parameter is the caller.
    There is deliberately no way to read another person's guide."""
    return fieldguide.guide(user)


@router.get("/field-guide/hint")
def get_field_guide_hint(user: CurrentUser):
    return fieldguide.hint(user)


class DismissKnot(BaseModel):
    knot: str = Field(min_length=1, max_length=64)


@router.post("/field-guide/dismiss")
def post_field_guide_dismiss(body: DismissKnot, user: CurrentUser):
    # the only write on this surface — cap it like every other write, or a
    # spoofed-name loop mints users + unlock rows without bound
    ratelimit.check("write", user)
    try:
        return fieldguide.dismiss(user, body.knot)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/whoami")
def get_whoami(user: CurrentUser, request: Request):
    """Who the API thinks you are and how strongly — the Settings page uses
    this to validate a pasted key without the user needing to know anything."""
    from ..services.api_keys import list_keys
    from .deps import is_named_admin

    strong = bool(getattr(request.state, "strong_auth", False))
    return {
        "user": user,
        "strong": strong,
        # Administrator by CONFIGURATION (SKEIN_ADMINS or the OIDC group), not
        # the scarcity fallback that makes every key holder one in a default
        # trusted-header deployment. This is the test _crew_steward applies, so
        # a surface that shows the crew controls on this flag shows exactly the
        # ones the server will accept. Gated on strong for the same reason
        # keys_minted is: an unproven identity is whatever the caller typed.
        "admin": strong and is_named_admin(user, getattr(request.state, "auth_groups", [])),
        # active only — after a revoke-all, Settings must show the bootstrap
        # command again, not "a key exists, paste it". Counted only for a
        # proven identity: a bare X-User names anyone, and the count is the
        # same fact GET /keys refuses to weak callers. Zero is also what
        # Settings must act on here — a caller with no proven key needs the
        # bootstrap command, whatever the roster holds.
        "keys_minted": sum(1 for k in list_keys(user) if k["active"]) if strong else 0,
    }


@router.post("/keys/request")
def post_key_request(user: CurrentUser):
    from ..services.api_keys import request_key

    # ABOVE the try, never inside it: RateLimited subclasses ValueError, so
    # the handler below caught the cap and answered 400 — wire-identical to a
    # malformed request, and stripping the Retry-After header the class exists
    # to carry. This surface is capped at 3/minute and is the one a client is
    # most likely to retry.
    ratelimit.check("keys_request", user)
    try:
        return request_key(user)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/briefing")
def get_briefing(user: CurrentUser):
    return briefing.my_day(user)


@router.get("/attention")
def get_attention(user: CurrentUser):
    return {"count": briefing.attention_count(user)}


class KeyIn(BaseModel):
    label: str = Field("", max_length=100)


# Key MUTATION requires an existing key (StrongUser): minting on X-User
# identity alone would let anyone who can reach the API become anyone and
# defeat the whole private-record boundary. First key per person:
# python -m app.bootstrap_key.


@router.post("/keys")
def post_key(body: KeyIn, user: StrongUser):
    return api_keys.create_key(user, body.label)


@router.get("/keys")
def get_keys(user: StrongUser):
    # StrongUser, like POST and DELETE below: under trusted-header a bare
    # X-User names anyone, so CurrentUser here hands out the prefix, label,
    # and last-used time of another person's credentials for the asking.
    return api_keys.list_keys(user)


@router.delete("/keys/{key_id}")
def delete_key(key_id: int, user: StrongUser):
    return api_keys.revoke_key(key_id, user)


@router.get("/admin/keys")
def get_all_keys(user: AdminUser):
    # key metadata (owners, prefixes, last use) is admin surface — one
    # teammate must not enumerate another's credentials; matches revoke-all
    # and /admin/export
    return api_keys.list_all_keys()


@router.post("/admin/keys/revoke-all")
def post_revoke_all_keys(user: AdminUser):
    return api_keys.revoke_all_keys(actor=user)


@router.get("/notifications")
def get_notifications(user: CurrentUser, unread_only: bool = True):
    return notifications.list_notifications(user, unread_only)


class MarkReadIn(BaseModel):
    notification_id: int = 0  # 0 = mark all read


@router.post("/notifications/read")
def post_notifications_read(body: MarkReadIn, user: CurrentUser):
    return notifications.mark_read(user, body.notification_id)


@router.get("/memories")
def get_memories(user: CurrentUser, q: str = ""):
    return memory.recall(q)


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: int, user: CurrentUser):
    ratelimit.check("delete", user)
    try:
        return memory.forget(memory_id, actor=user)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/pulse")
def get_pulse(user: CurrentUser):
    return pulse.pulse()


@router.get("/portfolio/health")
def get_portfolio_health(user: CurrentUser):
    return portfolio.engagement_health()


@router.get("/portfolio/conflicts")
def get_portfolio_conflicts(user: CurrentUser):
    return portfolio.allocation_conflicts()


@router.get("/portfolio/flow")
def get_portfolio_flow(user: CurrentUser):
    return portfolio.flow_metrics()


@router.get("/portfolio/forecast")
def get_portfolio_forecast(user: CurrentUser):
    return portfolio.slip_forecast()


@router.post("/portfolio/readout")
def post_portfolio_readout(user: CurrentUser):
    ratelimit.check("artifact", user)
    return readout.exec_readout(actor=user)


class WhatIfIn(BaseModel):
    people: list[Annotated[str, Field(max_length=64)]] = Field(max_length=20)
    percent: int = 50


@router.post("/intake/{request_id}/what-if")
def post_what_if(request_id: int, body: WhatIfIn, user: CurrentUser):
    return portfolio.what_if(request_id, body.people, body.percent)


@router.get("/week")
def get_week(user: CurrentUser, week: str = ""):
    return weekly.week_view(week)


@router.get("/week/draft")
def get_week_draft(user: CurrentUser, week: str = ""):
    return weekly.draft_plan(week)


class WeekPlanIn(BaseModel):
    week: str = Field("", max_length=8)
    task_ids: list[int] = Field(max_length=200)


@router.post("/week/plan")
def post_week_plan(body: WeekPlanIn, user: CurrentUser):
    return weekly.apply_plan(body.week or weekly.current_week(), body.task_ids, actor=user)


@router.get("/promises")
def get_promises(user: CurrentUser, status: str = "", audience: str = ""):
    return promises.list_promises(status, audience)


class PromiseIn(BaseModel):
    promise: str = Field(max_length=500)
    to_whom: str = Field("", max_length=120)
    due_date: str = Field("", max_length=10)
    engagement_id: int = 0
    audience: str = Field("external", max_length=20)


@router.post("/promises")
def post_promise(body: PromiseIn, user: CurrentUser):
    ratelimit.check("write", user)
    return promises.add_promise(**body.model_dump(), actor=user)


class PromiseStatusIn(BaseModel):
    status: str = Field(max_length=20)


class PromiseEditIn(BaseModel):
    promise: str = Field("", max_length=500)
    due_date: str = Field("", max_length=10)
    to_whom: str = Field("", max_length=120)


@router.patch("/promises/{promise_id}")
def patch_promise(promise_id: int, body: PromiseEditIn, user: CurrentUser):
    try:
        return promises.edit_promise(
            promise_id, body.promise, body.due_date, body.to_whom, actor=user
        )
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/promises/{promise_id}/status")
def post_promise_status(promise_id: int, body: PromiseStatusIn, user: CurrentUser):
    return promises.update_promise(promise_id, body.status, actor=user)


class SupersedeIn(BaseModel):
    title: str = Field(max_length=200)
    decision: str = Field(max_length=2000)
    context: str = Field("", max_length=4000)
    review_by: str = Field("", max_length=10)


@router.post("/decisions/{decision_id}/supersede")
def post_supersede(decision_id: int, body: SupersedeIn, user: CurrentUser):
    return collab.supersede_decision(decision_id, **body.model_dump(), decided_by=user, actor=user)


class ReconfirmIn(BaseModel):
    review_by: str = Field("", max_length=10)


@router.post("/decisions/{decision_id}/reconfirm")
def post_reconfirm(decision_id: int, body: ReconfirmIn, user: CurrentUser):
    return collab.reconfirm_decision(decision_id, body.review_by, actor=user)


@router.get("/review/stats")
def get_review_stats(user: CurrentUser):
    return review.review_stats()


class FeedbackIn(BaseModel):
    kind: str = Field(max_length=40)
    input_text: str = Field("", max_length=2000)  # a pulse vote has no input text
    output: str = Field("", max_length=4000)
    verdict: str = Field("up", max_length=10)
    correction: str = Field("", max_length=2000)


@router.post("/feedback")
def post_feedback(body: FeedbackIn, user: CurrentUser):
    # outside the try, for the reason post_key_request states
    ratelimit.check("feedback", user)
    data = body.model_dump()
    if not data["input_text"]:
        if data["kind"] != "pulse":
            raise HTTPException(422, "input_text is required for non-pulse feedback")
        data["input_text"] = "weekly pulse"
    return feedback.record_feedback(**data, actor=user)


@router.get("/feedback")
def get_feedback(user: CurrentUser, kind: str = ""):
    return feedback.list_feedback(kind)


@router.get("/eval/capture")
def get_eval_capture(user: CurrentUser):
    return feedback.eval_capture()


@router.post("/rituals/week-open")
def post_week_open(user: CurrentUser):
    ratelimit.check("ritual", user)  # each run notifies people — cap the amplifier
    return rituals.week_open(actor=user, force=True)


@router.post("/rituals/week-close")
def post_week_close(user: CurrentUser):
    ratelimit.check("ritual", user)
    return rituals.week_close(actor=user, force=True)


@router.get("/agents")
def get_agents(user: CurrentUser):
    return delegation.mission_control()


@router.get("/agents/status")
def get_agents_status(user: CurrentUser):
    """Plain-language state of the agent layer — the UI must never make mock
    mode look like a live model, or hide whether the review gate is on."""
    from .. import config

    return {
        "provider": config.MODEL_PROVIDER,
        "model": config.MODEL_ID if config.EFFECTIVE_PROVIDER != "mock" else "",
        "provider_error": config.MODEL_PROVIDER_ERROR,
        "review_gate": config.AGENT_REVIEW,
        # empty on mock: no Strands agent is built, so no strategy applies and
        # claiming one would describe machinery that is not running
        "context_strategy": (
            settings.effective_context_strategy() if config.EFFECTIVE_PROVIDER != "mock" else ""
        ),
        "context_error": config.CONTEXT_STRATEGY_ERROR,
    }


class ContextStrategyIn(BaseModel):
    # extra=forbid + no default: a mistyped field name would otherwise fall
    # through to "" — the CLEAR sentinel — silently reverting the whole team to
    # the env default and answering 200 as if it were deliberate
    model_config = ConfigDict(extra="forbid")
    strategy: str = Field(max_length=20)


@router.get("/settings/context-strategy")
def get_context_strategy(user: CurrentUser):
    """Reads for everyone (the agent strip already shows it); writing is
    operator-only. `default` is what an empty override falls back to."""
    from .. import config

    return {
        "strategy": settings.effective_context_strategy(),
        "override": settings.context_strategy_override(),
        "default": config.CONTEXT_STRATEGY,
        "choices": list(config.CONTEXT_STRATEGIES),
        "applies": config.EFFECTIVE_PROVIDER != "mock",
    }


@router.post("/settings/context-strategy")
def post_context_strategy(body: ContextStrategyIn, user: AdminUser):
    """AdminUser: this changes what every chat costs for the whole team, so
    it needs an administrator, not just any credential holder. Rate-capped
    because each call appends to the activity ledger, which is never pruned."""
    ratelimit.check("write", user)
    try:
        return settings.set_context_strategy(body.strategy, actor=user)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class TuningIn(BaseModel):
    # extra=forbid, and `value` has NO default: a mistyped field would fall
    # through to None, which is the CLEAR sentinel, silently returning a knob
    # to its default while answering 200. Same trap ContextStrategyIn names.
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=64)
    # null clears the override; the service range-checks the number
    value: int | None


@router.get("/settings/tuning")
def get_tuning(user: AdminUser):
    """AdminUser for the READ too, unlike the context strategy above. These
    numbers are the deployment's capacity limits, and listing them tells an
    ordinary caller exactly how much room is left before a cap refuses —
    which is reconnaissance, not a preference anyone needs to see."""
    return tuning.list_tunables()


@router.post("/settings/tuning")
def post_tuning(body: TuningIn, user: AdminUser):
    """Rate-capped for the reason the context strategy is: each call appends
    to the activity ledger, which is never pruned."""
    ratelimit.check("write", user)
    try:
        return tuning.set_tunable(body.name, body.value, actor=user)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/agents/trust")
def get_agents_trust(user: CurrentUser):
    return delegation.trust_scores()


@router.get("/agents/entities")
def get_agent_entities(user: CurrentUser):
    from ..services.delegation import NO_AUTHORITY

    # one set, shared with set_authority: excluding here but validating there
    # let a direct POST store a grant this picker cannot produce
    from ..services.lexicon import entity_label
    from ..services.review import _registry
    from ..tools._gate import ALWAYS_REVIEW

    return {
        # the label enumerates every capability the grant carries: a matrix
        # row is keyed on the entity, and "a blocker" hid that the same grant
        # also resolves them
        "entities": [
            {"entity": e, "label": entity_label(e)}
            for e in sorted(_registry())
            if e not in NO_AUTHORITY
        ],
        # the gate takes the review path for these before it reads the level,
        # so the card must not offer or display "acts alone" for them
        "always_review": sorted(ALWAYS_REVIEW),
    }


@router.get("/agents/authority")
def get_agents_authority(user: CurrentUser, agent: str = ""):
    return delegation.authority_matrix(agent)


class AuthorityIn(BaseModel):
    agent: str = Field(max_length=64)
    entity: str = Field(max_length=40)
    level: str = Field(max_length=20)


@router.post("/agents/authority")
def post_agents_authority(body: AuthorityIn, user: AdminUser):
    """Authority IS the kill switch — administrators only, and never a
    spoofable X-User."""
    return delegation.set_authority(body.agent, body.entity, body.level, actor=user)


@router.get("/agents/{agent}/inbox")
def get_agent_inbox(agent: str, user: CurrentUser):
    if not users.is_agent(agent):
        raise HTTPException(status_code=404, detail=f"no agent named '{agent}'")
    return delegation.agent_inbox(agent)


class DelegateIn(BaseModel):
    agent: str = Field(max_length=64)
    sponsor: str = Field("", max_length=64)


@router.post("/tasks/{task_id}/delegate")
def post_delegate(task_id: int, body: DelegateIn, user: CurrentUser):
    return delegation.delegate_task(task_id, body.agent, body.sponsor or user, actor=user)


@router.get("/context-pack")
def get_context_pack(user: CurrentUser, engagement: int = 0):
    if engagement:
        return {"engagement": engagement, "content": context_pack.build_engagement_pack(engagement)}
    return context_pack.get_pack(actor=user)


@router.post("/context-pack/publish")
def post_context_pack(user: CurrentUser):
    return context_pack.publish_pack(actor=user)


@router.get("/onboarding")
def get_onboarding(user: CurrentUser):
    from ..services import onboarding

    return onboarding.checklist(user)


@router.get("/adoption")
def get_adoption(user: CurrentUser, weeks: int = 4):
    from ..services import adoption as adoption_svc

    return adoption_svc.adoption(weeks)


@router.get("/insights")
def get_insights(user: CurrentUser):
    from ..services import insights as insights_svc

    return insights_svc.insights()


@router.get("/findings")
def get_findings(user: CurrentUser, weeks: int = 4):
    from ..services import insights as insights_svc

    return insights_svc.list_findings(weeks)


class FindingDispositionIn(BaseModel):
    disposition: str = Field(max_length=20)
    reason: str = Field("", max_length=500)
    deferred_until: str = Field("", max_length=10)


CHAIN_RULES = ("activity_chain_broken", "ledger_rows_adopted")


@router.post("/findings/{finding_id}/disposition")
def post_finding_disposition(
    finding_id: int, body: FindingDispositionIn, user: CurrentUser, request: Request
):
    from ..services import insights as insights_svc
    from .deps import _require_strong

    # Ordinary findings take CurrentUser, the way approvals do — any
    # identified human may act on team work. The ledger rules are the
    # exception: dismissing one drops it from the daily digest for good, and
    # since adoption replaced the permanent chain alarm, that digest line is
    # the only PUSH signal a smuggled row now produces. Under weak identity,
    # whoever caused the adoption could silence the report of it with a
    # chosen header.
    if insights_svc.finding_rule(finding_id) in CHAIN_RULES:
        _require_strong(getattr(request.state, "strong_auth", False))
    return insights_svc.disposition_finding(
        finding_id, body.disposition, body.reason, body.deferred_until, actor=user
    )


class ConvertIn(BaseModel):
    kind: str = Field(max_length=20)
    title: str = Field("", max_length=200)


@router.post("/findings/{finding_id}/convert")
def post_finding_convert(finding_id: int, body: ConvertIn, user: CurrentUser):
    from ..services import insights as insights_svc

    return insights_svc.convert_finding(finding_id, body.kind, body.title, actor=user)


@router.post("/findings/run")
def post_findings_run(user: CurrentUser):
    from ..services import insights as insights_svc

    return insights_svc.run_findings(actor=user)


@router.get("/usage")
def get_usage(user: CurrentUser):
    """Token and estimated-cost accounting. Costs are estimates from the
    operator's price table; unpriced_calls says how much each sum cannot see."""
    return {
        "models": usage.usage_summary(),
        "engagements": usage.engagement_costs(),
        "month": usage.month_to_date(),
        "prices_error": config.MODEL_PRICES_ERROR,
    }


# ---- writes ----------------------------------------------------------------


class MilestoneIn(BaseModel):
    title: str = Field(max_length=200)
    description: str = Field("", max_length=4000)
    project: str = Field("default", max_length=120)
    owner: str = Field("", max_length=64)
    due_date: str = Field("", max_length=10)


@router.post("/milestones")
def post_milestone(body: MilestoneIn, user: CurrentUser):
    ratelimit.check("write", user)
    return work.create_milestone(**body.model_dump(), actor=user)


class MilestonePatch(BaseModel):
    # caps match MilestoneIn — see the note on TaskPatch
    status: str = Field("", max_length=20)
    title: str = Field("", max_length=200)
    description: str = Field("", max_length=4000)
    owner: str = Field("", max_length=64)
    due_date: str = Field("", max_length=10)
    engagement_id: int = 0  # relink (-1 unlinks)


@router.patch("/milestones/{milestone_id}")
def patch_milestone(milestone_id: int, body: MilestonePatch, user: CurrentUser):
    return work.update_milestone(milestone_id, **body.model_dump(), actor=user)


class TaskIn(BaseModel):
    title: str = Field(max_length=200)
    description: str = Field("", max_length=4000)
    milestone_id: int = 0
    assignee: str = Field("", max_length=64)
    priority: str = Field("medium", max_length=10)
    due_date: str = Field("", max_length=10)
    engagement_id: int = 0


@router.post("/tasks")
def post_task(body: TaskIn, user: CurrentUser):
    ratelimit.check("write", user)
    return work.create_task(**body.model_dump(), actor=user)


class TaskPatch(BaseModel):
    # every cap here MUST match TaskIn above: a create-time bound that a PATCH
    # can step around is not a bound, and the oversized value lands in the
    # list API, the search index and the team-visible ledger just the same
    status: str = Field("", max_length=20)
    assignee: str = Field("", max_length=64)
    priority: str = Field("", max_length=10)
    due_date: str = Field("", max_length=10)
    description: str = Field("", max_length=4000)
    title: str = Field("", max_length=200)
    committed_week: str = Field("", max_length=10)
    waiting_on: str = Field("", max_length=32)  # "blocker:12" | "task:3" | "-"
    milestone_id: int = 0  # relink (-1 unlinks)
    engagement_id: int = 0  # relink (-1 unlinks)


@router.patch("/tasks/{task_id}")
def patch_task(task_id: int, body: TaskPatch, user: CurrentUser):
    # edits scan for @mentions, so an uncapped PATCH is a notification
    # amplifier — same cap as the create routes
    ratelimit.check("write", user)
    return work.update_task(task_id, **body.model_dump(), actor=user)


class QuestionIn(BaseModel):
    question: str = Field(max_length=1000)
    assigned_to: str = Field("", max_length=64)


@router.post("/questions")
def post_question(body: QuestionIn, user: CurrentUser):
    ratelimit.check("write", user)
    return collab.ask_question(
        body.question, asked_by=user, assigned_to=body.assigned_to, actor=user
    )


class QuestionPatch(BaseModel):
    assigned_to: str = Field(max_length=64)


@router.patch("/questions/{question_id}")
def patch_question(question_id: int, body: QuestionPatch, user: CurrentUser):
    return collab.assign_question(question_id, body.assigned_to, actor=user)


class AnswerIn(BaseModel):
    answer: str = Field(max_length=4000)


@router.post("/questions/{question_id}/answer")
def post_answer(question_id: int, body: AnswerIn, user: CurrentUser):
    # answers scan for @mentions — capped like every other notifying write
    ratelimit.check("write", user)
    return collab.answer_question(question_id, body.answer, answered_by=user, actor=user)


class DecisionIn(BaseModel):
    title: str = Field(max_length=200)
    decision: str = Field(max_length=2000)
    context: str = Field("", max_length=4000)
    review_by: str = Field("", max_length=10)
    category: str = Field("", max_length=40)


@router.post("/decisions")
def post_decision(body: DecisionIn, user: CurrentUser):
    ratelimit.check("write", user)
    return collab.record_decision(
        body.title,
        body.decision,
        body.context,
        decided_by=user,
        review_by=body.review_by,
        category=body.category,
        actor=user,
    )


class StandupIn(BaseModel):
    yesterday: str = Field("", max_length=2000)
    today: str = Field("", max_length=2000)
    blockers: str = Field("", max_length=2000)


@router.post("/standups")
def post_standup(body: StandupIn, user: CurrentUser):
    ratelimit.check("write", user)
    return collab.post_standup(user, body.yesterday, body.today, body.blockers, actor=user)


class NoteIn(BaseModel):
    topic: str = Field(max_length=200)
    content: str = Field(max_length=20_000)


@router.post("/notes")
def post_note(body: NoteIn, user: CurrentUser):
    ratelimit.check("write", user)
    return collab.save_note(body.topic, body.content, author=user, actor=user)


class EventIn(BaseModel):
    title: str = Field(max_length=200)
    starts_at: str = Field(max_length=25)
    ends_at: str = Field("", max_length=25)
    description: str = Field("", max_length=4000)
    attendees: str = Field("", max_length=500)


@router.post("/events")
def post_event(body: EventIn, user: CurrentUser):
    ratelimit.check("write", user)
    return schedule.schedule_event(**body.model_dump(), actor=user)


class BlockerIn(BaseModel):
    title: str = Field(max_length=200)
    detail: str = Field("", max_length=4000)
    owner: str = Field("", max_length=64)
    impact: str = Field("medium", max_length=10)
    task_id: int = 0


@router.post("/blockers")
def post_blocker(body: BlockerIn, user: CurrentUser):
    ratelimit.check("write", user)
    return blockers.raise_blocker(**body.model_dump(), actor=user)


class ResolveIn(BaseModel):
    resolution: str = Field("", max_length=2000)


class BlockerEditIn(BaseModel):
    title: str = Field("", max_length=200)
    detail: str = Field("", max_length=4000)
    owner: str = Field("", max_length=64)


@router.patch("/blockers/{blocker_id}")
def patch_blocker(blocker_id: int, body: BlockerEditIn, user: CurrentUser):
    try:
        return blockers.edit_blocker(blocker_id, body.title, body.detail, body.owner, actor=user)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/blockers/{blocker_id}/resolve")
def post_resolve_blocker(blocker_id: int, body: ResolveIn, user: CurrentUser):
    return blockers.resolve_blocker(blocker_id, body.resolution, actor=user)


class IntakeIn(BaseModel):
    title: str = Field(max_length=200)
    detail: str = Field("", max_length=4000)
    project_class: str = Field("", max_length=40)


@router.post("/intake")
def post_intake(body: IntakeIn, user: CurrentUser):
    ratelimit.check("write", user)
    return intake.submit_request(
        body.title, body.detail, requester=user, project_class=body.project_class, actor=user
    )


class ScoreIn(BaseModel):
    reach: int
    impact: int
    confidence: int
    effort: int


class IntakeEditIn(BaseModel):
    title: str = Field("", max_length=200)
    detail: str = Field("", max_length=4000)


@router.patch("/intake/{request_id}")
def patch_intake(request_id: int, body: IntakeEditIn, user: CurrentUser):
    try:
        return intake.edit_request(request_id, body.title, body.detail, actor=user)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/intake/{request_id}/score")
def post_intake_score(request_id: int, body: ScoreIn, user: CurrentUser):
    return intake.score_request(request_id, **body.model_dump(), actor=user)


class DispositionIn(BaseModel):
    disposition: str = Field(max_length=20)
    reason: str = Field(max_length=2000)
    kind: str = Field("delivery", max_length=20)
    timebox_end: str = Field("", max_length=10)
    outcome: str = Field("", max_length=2000)
    lead: str = Field("", max_length=64)
    kill_criteria: str = Field("", max_length=500)


@router.post("/intake/{request_id}/disposition")
def post_intake_disposition(request_id: int, body: DispositionIn, user: CurrentUser):
    return intake.disposition_request(
        request_id,
        body.disposition,
        body.reason,
        kind=body.kind,
        timebox_end=body.timebox_end,
        outcome=body.outcome,
        lead=body.lead,
        kill_criteria=body.kill_criteria,
        actor=user,
    )


class ReviewActionIn(BaseModel):
    note: str = Field("", max_length=1000)


@router.post("/review/{change_id}/approve")
def post_approve(change_id: int, body: ReviewActionIn, user: CurrentUser, request: Request):
    strong = bool(getattr(request.state, "strong_auth", False))
    return review.approve_change(change_id, body.note, actor=user, strong=strong)


@router.post("/review/{change_id}/reject")
def post_reject(change_id: int, body: ReviewActionIn, user: CurrentUser, request: Request):
    strong = bool(getattr(request.state, "strong_auth", False))
    return review.reject_change(change_id, body.note, actor=user, strong=strong)


class CaptureIn(BaseModel):
    text: str = Field(max_length=10_000)  # one capture, not a document dump


@router.post("/capture")
def post_capture(body: CaptureIn, user: CurrentUser, request: Request):
    ratelimit.check("capture", user)
    strong = bool(getattr(request.state, "strong_auth", False))
    return capture.capture(body.text, actor=user, strong_auth=strong)


class IngestIn(BaseModel):
    text: str = Field(max_length=70_000)  # 422 at validation, before buffering costs


@router.post("/ingest")
def post_ingest(body: IngestIn, user: CurrentUser):
    ratelimit.check("ingest", user)
    return ingest.ingest_notes(body.text, actor=user)


class BatchApproveIn(BaseModel):
    ids: list[int] = Field(max_length=200)  # matches the pending-list LIMIT


@router.get("/review/{change_id}/diff")
def get_review_diff(change_id: int, user: CurrentUser):
    return review.change_diff(change_id)


class SeenIn(BaseModel):
    ids: list[int] = Field(max_length=200)


@router.post("/review/seen")
def post_review_seen(body: SeenIn, user: CurrentUser):
    return review.mark_seen(body.ids, actor=user)


@router.post("/review/approve-batch")
def post_approve_batch(body: BatchApproveIn, user: CurrentUser, request: Request):
    strong = bool(getattr(request.state, "strong_auth", False))
    results = []
    # BatchApproveIn.max_length is the only cap. A second limit here (a
    # slice, a break) drops the tail with no result row — the caller counts
    # the answers, sees fewer than it sent, and never learns which ids were
    # skipped. Every id the model accepted gets exactly one result row.
    for cid in body.ids:
        try:
            r = review.approve_change(cid, actor=user, strong=strong)
            results.append({"id": cid, "status": r["status"]})
        except ValueError as exc:
            results.append({"id": cid, "status": "error", "detail": str(exc)})
    return {"results": results}


class EngagementIn(BaseModel):
    name: str = Field(max_length=120)
    project_class: str = Field("general", max_length=40)
    summary: str = Field("", max_length=4000)
    lead: str = Field("", max_length=64)
    kind: str = Field("delivery", max_length=20)
    timebox_end: str = Field("", max_length=10)
    kill_criteria: str = Field("", max_length=500)
    outcome: str = Field("", max_length=2000)


@router.post("/engagements")
def post_engagement(body: EngagementIn, user: CurrentUser):
    ratelimit.check("write", user)
    return engagements.create_engagement(**body.model_dump(), actor=user)


class EngagementPatch(BaseModel):
    status: str = Field("", max_length=20)
    name: str = Field("", max_length=120)  # rename propagates to milestone labels
    summary: str = Field("", max_length=4000)
    lead: str = Field("", max_length=64)
    conclusion: str = Field("", max_length=40)
    outcome: str = Field("", max_length=2000)
    timebox_end: str = Field("", max_length=10)
    kill_criteria: str = Field("", max_length=500)


@router.patch("/engagements/{engagement_id}")
def patch_engagement(engagement_id: int, body: EngagementPatch, user: CurrentUser):
    return engagements.update_engagement(engagement_id, **body.model_dump(), actor=user)


class AllocationIn(BaseModel):
    person: str = Field(max_length=64)
    percent: int = 100
    starts_on: str = Field("", max_length=10)
    ends_on: str = Field("", max_length=10)


@router.post("/engagements/{engagement_id}/allocate")
def post_allocate(engagement_id: int, body: AllocationIn, user: CurrentUser):
    ratelimit.check("write", user)
    return engagements.allocate(
        body.person, engagement_id, body.percent, body.starts_on, body.ends_on, actor=user
    )


class LessonIn(BaseModel):
    lesson: str = Field(max_length=2000)
    recommendation: str = Field("", max_length=2000)
    engagement_id: int = 0
    project_class: str = Field("general", max_length=40)


@router.post("/lessons")
def post_lesson(body: LessonIn, user: CurrentUser):
    ratelimit.check("write", user)
    return engagements.record_lesson(**body.model_dump(), actor=user)


class InstantiateIn(BaseModel):
    # caps match EngagementIn — instantiate reaches create_engagement, so an
    # uncapped name here writes past the create cap into the search index
    playbook: str = Field(max_length=40)
    engagement_name: str = Field(max_length=120)
    lead: str = Field("", max_length=64)
    start_date: str = Field("", max_length=10)


@router.post("/playbooks/instantiate")
def post_instantiate(body: InstantiateIn, user: CurrentUser):
    return playbooks.instantiate(
        body.playbook, body.engagement_name, body.lead or user, body.start_date, actor=user
    )


@router.post("/engagements/{engagement_id}/handoff")
def post_handoff(engagement_id: int, user: CurrentUser):
    ratelimit.check("artifact", user)
    return handoff.generate_handoff(engagement_id, actor=user)


@router.post("/digest")
def post_digest(user: CurrentUser):
    ratelimit.check("artifact", user)
    return digest.publish_digest(actor=user)


@router.get("/calendar.ics")
def get_calendar_ics(token: str = ""):
    """iCalendar feed of events + due dates (team-visible data only).
    Keep the feed inside the trusted network. Calendar clients can't send
    headers, so auth is a DEDICATED
    feed secret (?token=SKEIN_ICS_TOKEN) — never the API token, which
    would end up in calendar configs and access logs. Fully-open mode only
    when the whole API is open (trusted-header mode, no API_TOKEN);
    otherwise fail closed — the feed sits on the perimeter middleware's
    open-path list, so this check is its only gate."""
    import hmac

    from fastapi import HTTPException
    from fastapi.responses import Response

    from .. import config

    if config.ICS_TOKEN:
        # bytes compare: str compare_digest raises on non-ASCII input (→500)
        if not hmac.compare_digest(token.encode(), config.ICS_TOKEN.encode()):
            raise HTTPException(status_code=401, detail="token required")
    elif config.API_TOKEN or config.AUTH_MODE != "trusted-header":
        raise HTTPException(
            status_code=403,
            detail="calendar feed disabled — set SKEIN_ICS_TOKEN to enable it",
        )
    return Response(
        schedule.ics_feed(),
        media_type="text/calendar",
        headers={"Cache-Control": "private, no-store"},
    )


# ---- admin -----------------------------------------------------------------


@router.post("/admin/backup")
def post_backup(user: AdminUser):
    return admin.backup()


@router.get("/admin/export")
def get_export(user: AdminUser):
    # full-table dump — administrators only, never the X-User header
    return admin.export()
